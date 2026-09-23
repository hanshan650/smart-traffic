"""持续检测（违停）离线验证
============================

全部用**合成轨迹**验证，不依赖网络、数据库与 YOLO。这样做的价值是
可以在几秒内反复跑，把算法的边界条件钉死；真实流的端到端验证另有一处
（``scripts/probe_plate_pixels.py`` 与前端巡检页）。

覆盖的关键点
------------
1. **静止时长判定** —— 40 秒报警、20 秒不报警（阈值 30 秒）
2. **跨轮次时间基准** —— 本模块最核心的回归项。巡检分批抓帧，
   若用「帧号 ÷ 帧率」推算时间，轮次之间几十秒的空档会被抹掉，
   30 秒阈值永远达不到。这里构造「两轮之间真实间隔 60 秒」的轨迹，
   断言它**必须**被判为静止。
3. **观测新鲜度** —— 车辆驶出画面后轨迹会因跟踪器 max_age 残留，
   若不检查观测新鲜度，会报出「已静止 5 分钟」这种荒谬告警。
4. **场所豁免** —— 收费站 / 服务区停车属正常行为，不得报警。
5. **拥堵抑制** —— 整片同步静止是排队，不是违停。
6. **车牌可读性预检** —— 小框必须返回「不可读」并给出估算依据，
   而不是调用引擎后返回空结果。
7. **车主查询合规** —— 缺查询人或事由必须被拒绝；输出必须脱敏。
8. **证据链自洽** —— 各判据贡献之和必须等于最终得分。

用法::

    .venv\\Scripts\\python.exe scripts\\verify_surveillance.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.schemas import BBox  # noqa: E402
from app.services import owner_lookup, plate_recognizer, stall_detector  # noqa: E402
from app.services.stall_detector import StallConfig  # noqa: E402
from app.services.tracking import Track, TrackPoint  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = '') -> None:
    (PASSED if condition else FAILED).append(name)
    mark = 'PASS' if condition else 'FAIL'
    print(f'{mark}  {name}' + (f'    {detail}' if detail else ''))


# ==========================================================================
# 合成轨迹
# ==========================================================================


def make_track(
    track_id: int,
    center: tuple[float, float],
    timestamps: list[float],
    *,
    class_name: str = 'car',
    jitter: float = 0.0,
    size: float = 0.05,
) -> Track:
    """构造一条轨迹。

    :param jitter: 检测框抖动幅度（归一化）。用于验证"抖动不会被误判为移动"。
    """
    track = Track(track_id=track_id, class_name=class_name)
    x, y = center
    for index, timestamp in enumerate(timestamps):
        # 抖动按帧序号做确定性偏移，避免引入随机性导致结果不可复现
        wobble = jitter * math.sin(index * 1.7) if jitter else 0.0
        track.append(
            TrackPoint(
                frame_index=index,
                timestamp=timestamp,
                bbox=BBox(x=x + wobble, y=y + wobble, w=size, h=size * 0.8),
                confidence=0.8,
                class_name=class_name,
            )
        )
    return track


def continuous_timestamps(fps: float, seconds: float) -> list[float]:
    return [round(i / fps, 4) for i in range(int(fps * seconds))]


def batched_timestamps(
    fps: float,
    frames_per_round: int,
    rounds: int,
    gap_seconds: float,
    round_seconds: float,
) -> list[float]:
    """构造**分批抓帧**的时间戳：轮次内连续，轮次之间有真实空档。

    这正是巡检器实际产生的时间序列形状。
    """
    stamps: list[float] = []
    cursor = 0.0
    for _ in range(rounds):
        for index in range(frames_per_round):
            stamps.append(round(cursor + index / fps, 4))
        cursor += round_seconds + gap_seconds
    return stamps


# ==========================================================================
# 1. 静止时长
# ==========================================================================


def test_stationary_duration() -> None:
    config = StallConfig(stall_seconds=30.0)

    # 静止 40 秒（含抖动）—— 应报警
    long_track = make_track(1, (0.5, 0.5), continuous_timestamps(10, 40), jitter=0.002)
    outcome = stall_detector.detect_stalls(
        [long_track], config=config, now=long_track.last.timestamp
    )
    check(
        '静止 40 秒触发告警',
        len(outcome.alerts) == 1,
        f'告警数={len(outcome.alerts)} 静止时长={outcome.verdicts[0].stationary_seconds if outcome.verdicts else 0:.1f}s',
    )

    # 静止 20 秒 —— 不应报警（未达 30 秒硬前提）
    short_track = make_track(2, (0.5, 0.5), continuous_timestamps(10, 20), jitter=0.002)
    outcome = stall_detector.detect_stalls(
        [short_track], config=config, now=short_track.last.timestamp
    )
    check(
        '静止 20 秒不告警（未达阈值）',
        len(outcome.alerts) == 0,
        f'静止时长={outcome.verdicts[0].stationary_seconds:.1f}s' if outcome.verdicts else '',
    )

    # 抖动不应被当成移动：整段 jitter=0.002，位移阈值 0.01
    check(
        '检测框抖动不破坏静止判定',
        outcome.verdicts and outcome.verdicts[0].displacement <= config.displacement_threshold,
        f'最大偏离={outcome.verdicts[0].displacement:.4f}' if outcome.verdicts else '',
    )


# ==========================================================================
# 2. 跨轮次时间基准（核心回归项）
# ==========================================================================


def test_batch_timing() -> None:
    """两轮抓帧之间隔 60 秒，车辆全程静止 —— 必须判定为静止。

    这正是「用帧号推算时间」会失败的场景：
      40 帧 ÷ 10 fps = 4 秒 → 判不出 30 秒
    而真实时间是 61.9 秒 → 必须判出。
    """
    config = StallConfig(stall_seconds=30.0)
    stamps = batched_timestamps(
        fps=10.0, frames_per_round=20, rounds=2, gap_seconds=60.0, round_seconds=2.0
    )
    track = make_track(10, (0.5, 0.5), stamps, jitter=0.002)

    naive_seconds = len(stamps) / 10.0
    real_seconds = stamps[-1] - stamps[0]

    outcome = stall_detector.detect_stalls(
        [track], config=config, now=track.last.timestamp
    )
    verdict = outcome.verdicts[0] if outcome.verdicts else None

    check(
        '跨轮次使用真实时间（而非帧号推算）',
        verdict is not None and verdict.alert and verdict.stationary_seconds >= 60.0,
        f'帧号推算={naive_seconds:.1f}s 真实时间={real_seconds:.1f}s '
        f'判定={verdict.stationary_seconds:.1f}s' if verdict else '无判定结果',
    )


# ==========================================================================
# 3. 观测新鲜度
# ==========================================================================


def test_observation_freshness() -> None:
    """车辆早已驶出画面，轨迹因 max_age 残留 —— 不得报警。"""
    config = StallConfig(stall_seconds=30.0, max_observation_gap=45.0)

    # 轨迹最后观测在 t=60，而当前时间已是 t=300（4 分钟前就离开了）
    stamps = continuous_timestamps(10, 60)
    stale = make_track(20, (0.5, 0.5), stamps, jitter=0.002)

    with_gap = stall_detector.detect_stalls(
        [stale], config=config, now=300.0
    )
    without_gap = stall_detector.detect_stalls(
        [stale], config=config, now=stale.last.timestamp
    )

    check(
        '观测新鲜度检查剔除已驶离的轨迹',
        len(with_gap.alerts) == 0 and len(without_gap.alerts) == 1,
        f'带新鲜度检查={len(with_gap.alerts)} 条，不检查={len(without_gap.alerts)} 条',
    )


# ==========================================================================
# 4. 场所豁免
# ==========================================================================


def test_zone_exempt() -> None:
    config = StallConfig(stall_seconds=30.0)
    track = make_track(30, (0.5, 0.5), continuous_timestamps(10, 45), jitter=0.002)

    highway = stall_detector.detect_stalls(
        [track], config=config, zone_type='highway', now=track.last.timestamp
    )
    service = stall_detector.detect_stalls(
        [track],
        config=config,
        zone_type='service_area',
        parking_allowed=True,
        zone_message='点位名称含「服务区」',
        now=track.last.timestamp,
    )
    toll = stall_detector.detect_stalls(
        [track],
        config=config,
        zone_type='toll_station',
        parking_allowed=True,
        now=track.last.timestamp,
    )

    check(
        '高速主线停车告警',
        len(highway.alerts) == 1,
        f'告警数={len(highway.alerts)}',
    )
    check(
        '服务区停车豁免',
        len(service.alerts) == 0 and service.zone_exempt,
        service.verdicts[0].suppress_reason if service.verdicts else '',
    )
    check(
        '收费站停车豁免',
        len(toll.alerts) == 0 and toll.zone_exempt,
        toll.verdicts[0].suppress_reason if toll.verdicts else '',
    )


# ==========================================================================
# 5. 拥堵抑制
# ==========================================================================


def test_crowd_suppression() -> None:
    """画面中 5 辆车全部静止 —— 是排队，不是违停。"""
    config = StallConfig(stall_seconds=30.0, crowd_ratio=0.6, crowd_min_vehicles=3)
    stamps = continuous_timestamps(10, 40)
    tracks = [
        make_track(100 + index, (0.2 + index * 0.1, 0.5), stamps, jitter=0.002)
        for index in range(5)
    ]

    outcome = stall_detector.detect_stalls(
        [*tracks], config=config, now=stamps[-1]
    )
    check(
        '整片同步静止判为拥堵并抑制',
        outcome.crowded and len(outcome.alerts) == 0,
        f'占比={outcome.crowd_ratio:.0%} 拥堵={outcome.crowded} 告警={len(outcome.alerts)}',
    )

    # 对照组：只有 1 辆静止、其余在移动 —— 应报警
    moving = [
        make_track(
            200 + index,
            (0.2 + index * 0.1, 0.1),
            [index * 0.1 + step * 0.5 for step in range(40)],
            jitter=0.0,
        )
        for index in range(4)
    ]
    single = make_track(300, (0.5, 0.8), stamps, jitter=0.002)
    outcome = stall_detector.detect_stalls(
        [*moving, single], config=config, now=stamps[-1]
    )
    check(
        '单车静止仍然告警（不误伤）',
        len(outcome.alerts) == 1,
        f'占比={outcome.crowd_ratio:.0%} 告警={len(outcome.alerts)}',
    )


# ==========================================================================
# 6. 证据链自洽
# ==========================================================================


def test_evidence_chain() -> None:
    config = StallConfig(stall_seconds=30.0)
    track = make_track(400, (0.5, 0.5), continuous_timestamps(10, 45), jitter=0.002)
    outcome = stall_detector.detect_stalls(
        [track], config=config, now=track.last.timestamp
    )
    verdict = outcome.verdicts[0]

    total = verdict.contribution_sum
    check(
        '证据链自洽：各判据贡献之和等于总分',
        abs(total - verdict.score) < 1e-6,
        f'Σ贡献={total:.6f} 得分={verdict.score:.6f}',
    )
    check(
        '四条件判据齐全',
        len(verdict.evidences) == 4,
        '、'.join(item.label for item in verdict.evidences),
    )


# ==========================================================================
# 7. 车牌可读性预检
# ==========================================================================


def test_plate_precheck() -> None:
    recognizer = plate_recognizer.get_recognizer('none')

    # 实测到的真实尺寸：车辆框 14.8 x 16.1 px（352x288 画面）
    tiny = BBox(x=0.3, y=0.4, w=14.8 / 352, h=16.1 / 288)
    result = recognizer.recognize(
        image_path='', vehicle_bbox=tiny, frame_width=352, frame_height=288
    )
    check(
        '小框（<1 px 字符高）返回不可读 + 估算依据',
        not result.ok and result.readability == 'unreadable' and result.estimated_char_height < 2.0,
        f'估算字符高={result.estimated_char_height:.2f} px  等级={result.readability}',
    )
    check(
        '不可读时给出可解释的原因',
        '需接入高分辨率卡口相机' in result.message,
        result.message[:60] + '…',
    )

    # 720p 下车辆占画面宽 1/6 —— 字符高约 11 px，落在**临界区**
    # （这个数字有实际意义：它说明"接入 720p 相机"并不自动等于"能读牌"，
    #   还要车辆在画面中足够大）
    marginal = BBox(x=0.4, y=0.4, w=1 / 6, h=1 / 6)
    result = recognizer.recognize(
        image_path='', vehicle_bbox=marginal, frame_width=1280, frame_height=720
    )
    check(
        '720p 下车辆占 1/6 宽 → 判定为临界区而非可读',
        result.readability == 'marginal',
        f'估算字符高={result.estimated_char_height:.1f} px  等级={result.readability}',
    )

    # 车辆占画面宽 1/3 —— 字符高约 23 px，达到可读水平
    big = BBox(x=0.3, y=0.3, w=1 / 3, h=1 / 3)
    result = recognizer.recognize(
        image_path='', vehicle_bbox=big, frame_width=1280, frame_height=720
    )
    check(
        '720p 下车辆占 1/3 宽 → 通过可读性预检',
        result.readability == 'readable',
        f'估算字符高={result.estimated_char_height:.1f} px',
    )

    # 反推部署要求：车辆需占画面宽度多少才能读牌。
    # 逐步推导（全部是几何换算，不涉模型）：
    #   可读字符高 16 px
    #     → 牌高 = 16 / 0.7 = 22.9 px
    #     → 牌宽 = 22.9 * 3.14 = 71.8 px
    #     → 车宽 = 71.8 / 0.24 = 299 px
    #     → 占 1280 宽画面 = 23.4%
    required_box_px = (
        plate_recognizer.MIN_READABLE_CHAR_HEIGHT
        / plate_recognizer.PLATE_CHAR_HEIGHT_RATIO
        * plate_recognizer.PLATE_ASPECT
        / plate_recognizer.PLATE_TO_VEHICLE_WIDTH
    )
    required_ratio = required_box_px / 1280
    check(
        '反推出 720p 下可读车牌需车辆占画面宽约 23%',
        abs(required_ratio - 0.234) < 0.01,
        f'车辆框需 ≥{required_box_px:.0f} px 宽，占 1280 画面的 {required_ratio:.1%}',
    )

    # 模拟通道应能产出车牌，但必须带 simulated 标记
    mock = plate_recognizer.get_recognizer('mock')
    result = mock.recognize(
        image_path='', vehicle_bbox=big, frame_width=1280, frame_height=720, hint='cam-1'
    )
    check(
        '模拟通道输出带 simulated 标记',
        result.ok and result.simulated and result.plate,
        f'车牌={result.plate} simulated={result.simulated}',
    )


# ==========================================================================
# 8. 车主查询合规
# ==========================================================================


def test_owner_lookup_compliance() -> None:
    provider = 'mock'

    # 缺查询人
    try:
        owner_lookup.query_owner('豫A12345', operator='', reason='违停核查', provider_key=provider)
        check('缺查询人被拒绝', False, '未抛出异常')
    except owner_lookup.OwnerLookupError as exc:
        check('缺查询人被拒绝', True, str(exc))

    # 缺事由
    try:
        owner_lookup.query_owner('豫A12345', operator='410001', reason='', provider_key=provider)
        check('缺查询事由被拒绝', False, '未抛出异常')
    except owner_lookup.OwnerLookupError as exc:
        check('缺查询事由被拒绝', True, str(exc))

    # 正常查询 —— 结果必须脱敏
    record, audit = owner_lookup.query_owner(
        '豫A12345', operator='410001', reason='违停告警核查', provider_key=provider
    )
    check(
        '正常查询返回脱敏结果',
        record.found and '*' in record.owner_name and '*' in record.owner_id_masked,
        f'姓名={record.owner_name} 证件={record.owner_id_masked} 手机={record.owner_phone_masked}',
    )
    check(
        '审计记录包含查询人与事由',
        audit.operator == '410001' and audit.reason == '违停告警核查',
        f'operator={audit.operator} reason={audit.reason} outcome={audit.outcome}',
    )

    # 未接入通道必须如实报告不可用，而不是编造数据
    record, audit = owner_lookup.query_owner(
        '豫A12345', operator='410001', reason='违停核查', provider_key='official'
    )
    check(
        '未接入通道返回不可用而非编造数据',
        not record.found and audit.outcome == 'unavailable' and not record.owner_name,
        audit.message[:50] + '…',
    )

    # 脱敏函数本身
    check(
        '脱敏函数不泄露完整信息',
        owner_lookup.mask_id_number('410105199001011234') == '4101**********1234'
        and owner_lookup.mask_phone('13812345678') == '138****5678'
        and owner_lookup.mask_name('张伟') == '张*',
        f'{owner_lookup.mask_id_number("410105199001011234")} '
        f'{owner_lookup.mask_phone("13812345678")} {owner_lookup.mask_name("张伟")}',
    )


# ==========================================================================
# 9. 场所识别通道
# ==========================================================================


def test_scene_text() -> None:
    from app.services import scene_text

    recognizer = scene_text.get_recognizer('keyword')
    cases = [
        ('京港澳高速 许昌服务区 入口', 'service_area', True),
        ('连霍高速 郑州收费站 广场', 'toll_station', True),
        ('京港澳高速 K712+300 主线', 'highway', False),
        ('大广高速 省界公安检查站', 'checkpoint', True),
    ]
    all_ok = True
    detail = []
    for text, expected_zone, expected_allowed in cases:
        result = recognizer.recognize(hint_text=text)
        ok = result.zone_type == expected_zone and result.parking_allowed == expected_allowed
        all_ok = all_ok and ok
        detail.append(f'{expected_zone}={"✓" if ok else "✗"}')
    check('点位名称关键词识别场所类型', all_ok, ' '.join(detail))

    # OCR 通道不可用时必须退回元数据，而不是放弃判定
    ocr = scene_text.get_recognizer('ocr')
    result = ocr.recognize(hint_text='京港澳高速 许昌服务区 入口')
    check(
        'OCR 不可用时退回元数据判定',
        result.zone_type == 'service_area' and '退回' in result.message,
        result.message[:70] + '…',
    )


# ==========================================================================
# 主流程
# ==========================================================================


def main() -> int:
    print('=' * 74)
    print('持续检测（违停）离线验证')
    print('=' * 74)

    sections = [
        ('1. 静止时长判定', test_stationary_duration),
        ('2. 跨轮次时间基准（核心）', test_batch_timing),
        ('3. 观测新鲜度', test_observation_freshness),
        ('4. 场所豁免', test_zone_exempt),
        ('5. 拥堵抑制', test_crowd_suppression),
        ('6. 证据链自洽', test_evidence_chain),
        ('7. 车牌可读性预检', test_plate_precheck),
        ('8. 车主查询合规', test_owner_lookup_compliance),
        ('9. 场所识别通道', test_scene_text),
    ]

    for title, func in sections:
        print(f'\n--- {title} ---')
        func()

    print('\n' + '=' * 74)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('\n失败项：')
        for name in FAILED:
            print(f'  · {name}')
    print('=' * 74)
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
