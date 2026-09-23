"""
持续检测调度（违停监控）
========================

从"抓一段分析一次"到"一直盯着"
------------------------------
项目原有的检测都是**一次性**的：调一次接口，抓若干帧，出一次结论。
违停检测用不了这个模式 —— "停了多久"这个量必须**跨时间观测**才能得到，
一次抓帧最多看到 3 秒，永远够不到 30 秒的阈值。

所以这里引入一个后台巡检器：按轮次持续抓帧、持续跟踪、持续判定。

时间基准（最容易出错的地方）
----------------------------
判定静止 30 秒，就必须知道"真的过了 30 秒"。而巡检是分批抓帧的：

    轮次 1:  [帧 1..20]              ← 一轮内帧间隔 1/25 秒
             ↓ 间隔 15 秒（下一轮还没开始）
    轮次 2:  [帧 21..40]

如果按「帧号 ÷ 帧率」推算时间，那 15 秒的空档会被完全抹掉 —— 两轮
40 帧只算出 1.6 秒，30 秒阈值永远达不到。**这是这套机制里最容易踩的坑。**

正确做法是用**真实墙钟时间**：
  · 轮次内的帧按「轮次起始时刻 + 帧序号/帧率」插值（帧已抓完，无法逐个取时）
  · 轮次起始时刻取自同一个单调时钟，因此轮次之间的空档自然体现为时间差

使用 ``time.monotonic()`` 而非 ``time.time()``：后者会被系统对时调整而
跳变，可能让静止时长瞬间倒退或暴增。

跨轮次轨迹保持
--------------
跟踪器实例在摄像头级的 ``CameraWatch`` 中长期持有，不随轮次重建，
因此 ``track_id`` 能跨轮次延续。代价是 ``max_age`` 必须设得较大，
车辆驶出画面后轨迹不会立即消失 —— 这由静止检测里的**观测新鲜度**
检查（``StallConfig.max_observation_gap``）兜住，两道防线缺一不可。

线程模型
--------
巡检跑在**独立守护线程**里，不是 asyncio 任务。原因：推理是同步阻塞的
（单帧约 200ms），放进事件循环会卡死所有 HTTP 请求与 WebSocket 推送。
线程通过 ``threading.Event`` 停止，所有共享状态由一把可重入锁保护。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.db.mongo import get_db
from app.models.schemas import BBox, StallAlert
from app.realtime.ws_manager import broadcast_threadsafe
from app.services import (
    detector as yolo_detector,
    plate_recognizer,
    scene_text,
    stall_detector,
    video_source,
)
from app.services.stall_detector import StallConfig, StallOutcome
from app.services.tracking import IoUTracker
from app.services.video_source import VideoSourceError

#: 轮次内帧间插值用的兜底帧率（探测失败时）
FALLBACK_FPS = 10.0

#: 跟踪器的最大存活帧数。
#:
#: 注意这个值**不需要**覆盖轮次之间的时间间隔 —— ``max_age`` 统计的是
#: 「连续多少帧未匹配」，而帧计数器只在调用 ``update()`` 时递增，
#: 轮次之间没有分析的帧根本不参与计数。所以它只需要容忍一轮内的
#: 少量漏检，这是个很小的数字。
TRACKER_MAX_AGE = 5

#: 跟踪器确认轨迹所需的最少观测帧数
TRACKER_MIN_HITS = 2

#: 轨迹关联所需的 IoU。低分辨率下检测框抖动大，取值不宜高
TRACKER_IOU = 0.3

#: 告警落库集合
ALERT_COLLECTION = 'stall_alerts'

#: 需要做车牌识别的目标类别（行人等不适用）
PLATE_CLASSES = frozenset({'car', 'truck', 'bus', 'motorcycle', 'train'})


# ==========================================================================
# 摄像头巡检状态
# ==========================================================================


@dataclass
class CameraWatch:
    """单个摄像头的持续巡检状态。

    生命周期与巡检器等长：跟踪器、帧计数、时间基准都挂在这里，
    这样跨轮次才能延续。**不要每轮重建这个对象。**
    """

    camera_id: str
    camera_name: str = ''
    hint_text: str = ''
    road_id: str = 'R001'
    enabled: bool = True

    # ---- 内部状态（跨轮次保持）----
    tracker: Any = None
    frame_counter: int = 0
    rounds: int = 0
    frames_analyzed: int = 0
    alerts_raised: int = 0
    started_at: Optional[float] = None
    last_round_at: Optional[float] = None
    last_round_ms: float = 0.0
    last_error: str = ''
    last_snapshot: str = ''
    last_outcome: Optional[StallOutcome] = None
    #: track_id -> 上次告警时刻，用于冷却去重
    alerted_at: Dict[int, float] = field(default_factory=dict)
    #: 场所判定缓存（点位属性不会频繁变化，没必要每轮重算）
    zone_type: str = 'highway'
    zone_label: str = ''
    zone_message: str = ''
    parking_allowed: bool = False
    zone_resolved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        outcome = self.last_outcome
        return {
            'cameraId': self.camera_id,
            'cameraName': self.camera_name or self.camera_id,
            'hintText': self.hint_text,
            'roadId': self.road_id,
            'enabled': self.enabled,
            'rounds': self.rounds,
            'framesAnalyzed': self.frames_analyzed,
            'alertsRaised': self.alerts_raised,
            'lastRoundAt': (
                datetime.utcfromtimestamp(self.last_round_at).isoformat()
                if self.last_round_at
                else None
            ),
            'lastRoundMs': round(self.last_round_ms, 1),
            'lastError': self.last_error,
            'lastSnapshot': self.last_snapshot,
            'zoneType': self.zone_type,
            'zoneLabel': self.zone_label,
            'zoneMessage': self.zone_message,
            'parkingAllowed': self.parking_allowed,
            'trackCount': outcome.track_count if outcome else 0,
            'stationaryCount': outcome.stationary_count if outcome else 0,
            'crowded': outcome.crowded if outcome else False,
            'crowdRatio': round(outcome.crowd_ratio, 4) if outcome else 0.0,
            'activeTracks': len(getattr(self.tracker, 'active_tracks', lambda: [])()),
            # 最近一轮的逐目标判定。带上它才能在界面上回答
            # 「画面里有车在动/在停、各自多久了、为什么没报警」——
            # 只给一个静止数计数，使用者无法判断系统是否正常。
            'verdicts': [
                {
                    'trackId': item.track_id,
                    'className': item.class_name,
                    'stationarySeconds': round(item.stationary_seconds, 2),
                    'score': round(item.score, 4),
                    'alert': item.alert,
                    'suppressed': item.suppressed,
                    'suppressReason': item.suppress_reason,
                }
                for item in (outcome.verdicts[:10] if outcome else [])
            ],
        }


# ==========================================================================
# 巡检器
# ==========================================================================


class SurveillanceService:
    """持续检测调度器（进程内单例）。

    对外只暴露 :meth:`start` / :meth:`stop` / :meth:`status` 三件事，
    其余都是内部实现 —— 调度细节（线程、时钟、去重）不应泄漏给 API 层。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._watches: Dict[str, CameraWatch] = {}
        self._epoch: float = time.monotonic()
        self._started_at: Optional[float] = None
        self._last_error: str = ''

    # ------------------------------------------------------------------ 配置

    def stall_config(self) -> StallConfig:
        """从全局配置构造静止检测参数。

        每次读取而非缓存，这样调参后重新启动巡检即生效，无需重启进程。
        """
        return StallConfig(
            stall_seconds=settings.stall_seconds,
            displacement_threshold=settings.stall_displacement,
            crowd_ratio=settings.stall_crowd_ratio,
            count_min_stationary=settings.stall_count_min_stationary,
            score_threshold=settings.stall_score_threshold,
            max_observation_gap=settings.stall_observation_gap,
        )

    # -------------------------------------------------------------- 生命周期

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        camera_ids: Optional[List[str]] = None,
        hints: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """开始巡检。

        :param camera_ids: 要巡检的摄像头；省略则取配置里的列表，
            配置也为空时退回当前视频源的默认摄像头。
        :param hints: 点位名称映射（摄像头编号 -> 名称），用于判定该地点
            是否属于可停车场所。名称里含"服务区""收费站"等关键词即豁免。
        """
        with self._lock:
            if self.is_running():
                return {'started': False, 'message': '巡检已在运行中'}

            hints = hints or {}
            resolved = camera_ids or settings.surveillance_camera_list
            if not resolved:
                try:
                    resolved = [video_source.get_provider().default_camera()]
                except Exception as exc:  # noqa: BLE001
                    return {'started': False, 'message': f'无法确定巡检摄像头：{exc}'}

            self._watches = {}
            for camera_id in resolved:
                hint = hints.get(camera_id, '')
                watch = CameraWatch(
                    camera_id=camera_id,
                    # 名称参与场所判定，所以从启动第一刻起就要带上 ——
                    # 若等到第一轮取流成功后再解析，取流失败的通道会一直
                    # 显示空的场所标签，看不到「这个点位到底禁不禁停」
                    hint_text=hint or camera_id,
                    # 跟踪器在摄像头级长期持有，跨轮次延续 track_id。
                    # 每轮重建会让所有目标换号，静止时长也就无法累积。
                    tracker=IoUTracker(
                        iou_threshold=TRACKER_IOU,
                        max_age=TRACKER_MAX_AGE,
                        min_hits=TRACKER_MIN_HITS,
                    ),
                )
                # 启动时就解析场所属性，不等第一轮取流
                self._resolve_zone(watch)
                self._watches[camera_id] = watch

            self._stop_event = threading.Event()
            self._epoch = time.monotonic()
            self._started_at = time.time()
            self._last_error = ''

            self._thread = threading.Thread(
                target=self._loop,
                name='surveillance',
                daemon=True,          # 守护线程：主进程退出时不必等待它
            )
            self._thread.start()

            return {
                'started': True,
                'cameras': resolved,
                'interval': settings.surveillance_interval,
                'message': f'已开始巡检 {len(resolved)} 路摄像头',
            }

    def stop(self, timeout: float = 5.0) -> Dict[str, Any]:
        """停止巡检。等待线程退出，但不等超过 ``timeout`` 秒。"""
        with self._lock:
            if not self.is_running():
                return {'stopped': False, 'message': '巡检未在运行'}
            self._stop_event.set()
            thread = self._thread

        # 在锁外 join：线程收尾时也要拿锁，锁内 join 会死锁
        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
            self._thread = None
            return {'stopped': True, 'message': '巡检已停止'}

    # ------------------------------------------------------------------ 状态

    def status(self) -> Dict[str, Any]:
        with self._lock:
            watches = [item.to_dict() for item in self._watches.values()]
            running = self.is_running()
            return {
                'running': running,
                'startedAt': (
                    datetime.utcfromtimestamp(self._started_at).isoformat()
                    if self._started_at
                    else None
                ),
                'uptimeSeconds': (
                    round(time.monotonic() - self._epoch, 1) if running else 0.0
                ),
                'interval': settings.surveillance_interval,
                'frameCount': settings.surveillance_frame_count,
                'lastError': self._last_error,
                'cameras': watches,
                'totals': {
                    'cameras': len(watches),
                    'rounds': sum(item['rounds'] for item in watches),
                    'framesAnalyzed': sum(item['framesAnalyzed'] for item in watches),
                    'alertsRaised': sum(item['alertsRaised'] for item in watches),
                },
            }

    def set_enabled(self, camera_id: str, enabled: bool) -> Dict[str, Any]:
        """启用 / 暂停某一路巡检，不影响其他路。"""
        with self._lock:
            watch = self._watches.get(camera_id)
            if watch is None:
                raise KeyError(camera_id)
            watch.enabled = enabled
            return watch.to_dict()

    def watch(self, camera_id: str) -> Optional[CameraWatch]:
        with self._lock:
            return self._watches.get(camera_id)

    # -------------------------------------------------------------- 巡检主循环

    def _loop(self) -> None:
        """巡检主循环。

        用「固定间隔」而非「固定频率」：每轮结束后 sleep 固定秒数。
        不追求严格等间隔 —— 单轮耗时本来就会随画面内目标数波动，
        强行对齐只会增加复杂度而不会让判定更准（判定用的是真实时间戳）。
        """
        interval = max(settings.surveillance_interval, 1)
        while not self._stop_event.is_set():
            round_started = time.monotonic()

            with self._lock:
                targets = [item for item in self._watches.values() if item.enabled]

            for watch in targets:
                if self._stop_event.is_set():
                    break
                try:
                    self._run_round(watch)
                except VideoSourceError as exc:
                    self._mark_error(watch, f'取流失败：{exc}')
                except yolo_detector.DetectorUnavailable as exc:
                    self._mark_error(watch, f'推理模型不可用：{exc}')
                except Exception as exc:  # noqa: BLE001 - 单路异常不能拖垮整个巡检
                    self._mark_error(watch, f'巡检异常：{exc}')

            # 扣除本轮耗时，避免"轮次耗时 + 间隔"导致实际周期越来越长
            elapsed = time.monotonic() - round_started
            self._stop_event.wait(max(interval - elapsed, 1.0))

    def _mark_error(self, watch: CameraWatch, message: str) -> None:
        with self._lock:
            watch.last_error = message
        self._last_error = message

    def _run_round(self, watch: CameraWatch) -> None:
        """执行一轮巡检：取流 → 抓帧 → 逐帧检测跟踪 → 静止判定 → 告警。"""
        round_begin = time.monotonic()
        provider = video_source.get_provider()
        stream_url = provider.get_stream_url(watch.camera_id)
        headers = provider.request_headers

        # 时基：帧间插值需要真实帧率。探测失败时用兜底值，
        # 此时绝对时间有偏差但**轮次间的时间差仍然准确**（那才是 30 秒判定的关键）。
        fps = video_source.probe_frame_rate(stream_url, headers) or FALLBACK_FPS

        frames = video_source.extract_frames(
            stream_url, headers=headers, count=settings.surveillance_frame_count
        )
        if not frames:
            raise VideoSourceError('抽帧失败')

        # 轮次起始时刻（相对巡检启动的秒数）—— 轮次间的空档由此体现
        round_offset = time.monotonic() - self._epoch

        tracks: List[Any] = []
        best_snapshot = frames[0]

        with self._lock:
            if watch.started_at is None:
                watch.started_at = round_offset
            self._resolve_zone(watch)

        for index, frame_path in enumerate(frames):
            if self._stop_event.is_set():
                break
            outcome = yolo_detector.detect_image(frame_path)

            with self._lock:
                frame_index = watch.frame_counter
                watch.frame_counter += 1

            # 帧时间戳 = 轮次起始 + 帧内偏移。轮次之间的真实空档不在这个
            # 循环里体现，而是体现在下一次 _run_round 的 round_offset 上。
            timestamp = round_offset + index / max(fps, 1e-6)
            tracks = watch.tracker.update(outcome.objects, frame_index, timestamp)

            if outcome.total_vehicles >= 0:
                best_snapshot = frame_path

        now = time.monotonic() - self._epoch
        result = stall_detector.detect_stalls(
            tracks,
            config=self.stall_config(),
            zone_type=watch.zone_type,
            zone_message=watch.zone_message,
            parking_allowed=watch.parking_allowed,
            now=now,
        )

        snapshot_url = '/static/uploads/' + best_snapshot.replace('\\', '/').split('/')[-1]
        frame_width, frame_height = _frame_size(best_snapshot)

        with self._lock:
            watch.rounds += 1
            watch.frames_analyzed += len(frames)
            watch.last_round_at = time.time()
            watch.last_round_ms = (time.monotonic() - round_begin) * 1000
            watch.last_error = ''
            watch.last_snapshot = snapshot_url
            watch.last_outcome = result

        self._raise_alerts(watch, result, snapshot_url, now, frame_width, frame_height)

    def _resolve_zone(self, watch: CameraWatch) -> None:
        """解析点位场所属性（每路只算一次）。

        场所属性来自摄像头名称等元数据，属于**慢变量**，没必要每轮重算；
        但首次解析必须发生在第一轮判定之前 —— 否则第一轮会按默认的
        "高速主线（禁停）"处理，在服务区点位上产生一条假告警。
        """
        if watch.zone_resolved:
            return

        if not watch.hint_text:
            # 元数据为空时退回用摄像头编号占位，仍走同一个判定通道
            watch.hint_text = watch.camera_name or watch.camera_id

        recognizer = scene_text.get_recognizer(settings.surveillance_text)
        result = recognizer.recognize(hint_text=watch.hint_text)

        watch.zone_type = result.zone_type
        watch.zone_label = result.zone_label
        watch.zone_message = f'{result.source}：{result.message}'
        watch.parking_allowed = result.parking_allowed
        watch.zone_resolved = True

    # ------------------------------------------------------------------ 告警

    def _raise_alerts(
        self,
        watch: CameraWatch,
        result: StallOutcome,
        snapshot_url: str,
        now: float,
        frame_width: int,
        frame_height: int,
    ) -> None:
        """把新增的告警落库并广播。

        去重策略：同一 ``track_id`` 在冷却期内只报一次。没有这层，
        一个静止目标会在每轮巡检里重复触发，几分钟就是几十条重复记录。
        """
        cooldown = settings.stall_alert_cooldown
        for verdict in result.alerts:
            with self._lock:
                last = watch.alerted_at.get(verdict.track_id)
                if last is not None and (now - last) < cooldown:
                    continue
                watch.alerted_at[verdict.track_id] = now
                watch.alerts_raised += 1

            record = {
                'camera_id': watch.camera_id,
                'camera_name': watch.camera_name or watch.camera_id,
                'road_id': watch.road_id,
                'track_id': verdict.track_id,
                'class_name': verdict.class_name,
                'stationary_seconds': round(verdict.stationary_seconds, 2),
                'score': round(verdict.score, 4),
                'displacement': round(verdict.displacement, 5),
                'zone_type': verdict.zone_type,
                'zone_label': verdict.zone_label,
                'snapshot_url': snapshot_url,
                # evidences 用 camelCase 落库（StallEvidence.to_dict 的输出），
                # StallAlert 模型能直接解析，不必再转换一次
                'evidences': [item.to_dict() for item in verdict.evidences],
                'plate': '',
                'plate_simulated': False,
                'plate_message': '',
                'plate_char_height': 0.0,
                'plate_readability': 'unreadable',
                'created_at': datetime.utcnow(),
                'status': 'pending',
                'note': '',
            }

            # ---- 车牌识别：走预检 + 适配器，读不出就如实记录原因 ----
            plate_result = plate_recognizer.get_recognizer(
                settings.surveillance_plate_engine
            ).recognize(
                image_path=snapshot_url,
                vehicle_bbox=verdict.bbox,
                frame_width=frame_width,
                frame_height=frame_height,
                hint=f'{watch.camera_id}-{verdict.track_id}',
            )
            record['plate_char_height'] = round(plate_result.estimated_char_height, 2)
            record['plate_readability'] = plate_result.readability
            record['plate_message'] = plate_result.message
            if plate_result.ok:
                record['plate'] = plate_result.plate
                record['plate_simulated'] = plate_result.simulated

            alert_id = self._persist_alert(record)

            # 广播与查询接口共用同一个规范化函数，避免两处格式跑偏 ——
            # 前端同一份代码要能同时解析实时推送与历史查询的结果
            payload = dict(record)
            payload['id'] = alert_id
            broadcast_threadsafe('stall:alert', _to_alert(payload))

    @staticmethod
    def _persist_alert(record: Dict[str, Any]) -> str:
        try:
            inserted = get_db()[ALERT_COLLECTION].insert_one(dict(record))
            return str(inserted.inserted_id)
        except Exception:  # noqa: BLE001 - 落库失败不影响实时推送
            return ''


def _jsonable(value: Any) -> Any:
    """把 datetime / BBox 等不可直接序列化的值转成基础类型。"""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BBox):
        return value.model_dump()
    return value


def _frame_size(image_path: str) -> tuple[int, int]:
    """读取画面尺寸，用于车牌可读性预检。

    尺寸从**实际帧**读取而非写进配置：上游分辨率可能变化，
    若用配置里的固定值，可读性估算会与实际不符，预检也就失去意义。
    """
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            return (image.width, image.height)
    except Exception:  # noqa: BLE001 - 读不到就退回常见值，不中断巡检
        return (352, 288)


# ==========================================================================
# 查询辅助
# ==========================================================================


def _to_alert(document: Dict[str, Any]) -> Dict[str, Any]:
    """把 MongoDB 文档规范化为 camelCase 响应。

    直接返回原始文档会让前端拿到 ``camera_id`` 这类 snake_case 字段，
    而项目其余接口一律输出 camelCase —— 前端按统一约定解析就会静默拿到空值。
    走一遍 Pydantic 模型即可对齐格式，同时也顺带做了类型校验。
    """
    payload = dict(document)
    payload['id'] = str(payload.pop('_id', ''))
    created = payload.get('created_at')
    if isinstance(created, datetime):
        payload['created_at'] = created.isoformat()
    # evidences 落库时已是 camelCase（由 StallEvidence.to_dict 生成），
    # 而 CamelModel 的 alias 机制能正确解析，无需在此手动转换
    return StallAlert.model_validate(payload).model_dump(by_alias=True)


def list_alerts(limit: int = 50, camera_id: str = '', status: str = '') -> List[Dict[str, Any]]:
    """查询历史违停告警。"""
    try:
        query: Dict[str, Any] = {}
        if camera_id:
            query['camera_id'] = camera_id
        if status:
            query['status'] = status
        cursor = get_db()[ALERT_COLLECTION].find(query).sort('created_at', -1).limit(limit)
        return [_to_alert(document) for document in cursor]
    except Exception:  # noqa: BLE001
        return []


def alert_stats() -> Dict[str, Any]:
    """违停告警概览。"""
    try:
        collection = get_db()[ALERT_COLLECTION]
        total = collection.count_documents({})
        by_status: Dict[str, int] = {}
        for item in collection.aggregate([{'$group': {'_id': '$status', 'count': {'$sum': 1}}}]):
            by_status[str(item['_id'])] = item['count']

        with_plate = collection.count_documents({'plate': {'$ne': ''}})
        return {
            'total': total,
            'byStatus': by_status,
            'withPlate': with_plate,
        }
    except Exception:  # noqa: BLE001
        return {'total': 0, 'byStatus': {}, 'withPlate': 0}


def get_alert(alert_id: str) -> Optional[Dict[str, Any]]:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        document = get_db()[ALERT_COLLECTION].find_one({'_id': ObjectId(alert_id)})
    except (InvalidId, TypeError):
        return None
    except Exception:  # noqa: BLE001
        return None
    if document is None:
        return None
    return _to_alert(document)


def set_alert_status(alert_id: str, status: str, note: str = '') -> Optional[Dict[str, Any]]:
    """更新告警处置状态（核验 / 误报 / 已处置）。"""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        get_db()[ALERT_COLLECTION].update_one(
            {'_id': ObjectId(alert_id)},
            {'$set': {'status': status, 'note': note, 'updated_at': datetime.utcnow()}},
        )
    except (InvalidId, TypeError):
        return None
    except Exception:  # noqa: BLE001
        return None
    return get_alert(alert_id)


#: 进程内单例。FastAPI 的依赖注入不适用于跨请求的后台线程状态，
#: 因此这里用模块级单例，与 detector / ws_manager 的处理方式一致。
surveillance = SurveillanceService()
