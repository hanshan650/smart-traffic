"""持续检测 API 冒烟测试
========================

验证的是**接口与调度器真的连起来了**，而算法正确性由
``verify_surveillance.py`` 离线覆盖。两者分工不同：

  · ``verify_surveillance.py``  —— 合成轨迹，验证判定逻辑（秒级、无依赖）
  · 本脚本                      —— 真实后端，验证接线（分钟级、需 MongoDB）

真实巡检会持续抓帧 + 推理，因此本脚本跑得慢（默认观察 60 秒），
它回答的问题是："启动巡检后，轮次统计有没有真的涨上去？" ——
这类"线程没跑起来"的问题，离线测试是发现不了的。

用法（先启动后端）::

    .venv\\Scripts\\python.exe scripts\\smoke_surveillance_api.py
"""
from __future__ import annotations

import sys
import time
from typing import Any, Dict

import requests

BASE = 'http://127.0.0.1:8000/api/v1'
OBSERVE_SECONDS = 60


def show(title: str) -> None:
    print(f'\n=== {title} ===')


def main() -> int:
    failures: list[str] = []

    # ---------------------------------------------------------------- 算法说明
    show('算法说明')
    try:
        algo = requests.get(f'{BASE}/surveillance/algorithm', timeout=30).json()
    except Exception as exc:  # noqa: BLE001
        print(f'请求失败：{exc}')
        return 1

    detection = algo['detection']
    print(f"静止阈值      : {detection['config']['stallSeconds']} 秒")
    print(f"得分阈值      : {detection['config']['scoreThreshold']}")
    print(f"判据数量      : {len(detection['criterions'])}")
    print(f"巡检间隔      : {algo['scheduler']['intervalSeconds']} 秒")
    print(f"每轮帧数      : {algo['scheduler']['frameCount']}")
    print(f"场景识别通道  : {algo['sceneText']['current']}")
    print(f"车牌通道      : {algo['plate']['current']}")
    print(f"车主查询通道  : {algo['ownerLookup']['current']}")

    if detection['config']['stallSeconds'] != 30.0:
        failures.append('静止阈值不是 30 秒')
    if len(detection['criterions']) != 4:
        failures.append('判据数量不是 4')

    # ---------------------------------------------------------------- 场所判定
    show('点位名称 → 场所判定')
    # 先停掉刚才那一路，再用带名称的方式重启
    requests.post(f'{BASE}/surveillance/stop', json={}, timeout=30)
    time.sleep(2)

    # 名称里带「服务区」的点位应被判为可停车场所，否则会被当成禁停主线。
    # 这里同时也在验证**中文参数能正确送达后端** —— 用 PowerShell 的
    # Invoke-RestMethod 传中文 body 会因 ANSI 编码而变成乱码，
    # 导致关键词匹配失效，表现为「明明是服务区却判成高速主线」。
    hints = {
        '1324629868805103618': '京港澳高速 许昌服务区 入口',
        '2033453710067216385': '连霍高速 郑州收费站 广场',
        '9999999999999999999': '京港澳高速 K712+300 主线',
    }
    started = requests.post(
        f'{BASE}/surveillance/start',
        json={'cameraIds': list(hints.keys()), 'hints': hints},
        timeout=60,
    )
    if started.status_code != 200:
        print(f'启动失败：{started.text[:200]}')
        return 1

    time.sleep(3)
    state = requests.get(f'{BASE}/surveillance/status', timeout=30).json()
    for camera in state['cameras']:
        print(
            f"  {camera['cameraId']}  zone={camera['zoneType']:<13} "
            f"label={camera['zoneLabel']:<8} 可停车={camera['parkingAllowed']}"
        )

    by_id = {item['cameraId']: item for item in state['cameras']}
    service = by_id.get('1324629868805103618', {})
    toll = by_id.get('2033453710067216385', {})
    highway = by_id.get('9999999999999999999', {})

    if service.get('zoneType') != 'service_area' or not service.get('parkingAllowed'):
        failures.append(
            f"服务区点位未豁免（zone={service.get('zoneType')}）"
            " —— 检查中文参数是否在传输中被破坏"
        )
    if toll.get('zoneType') != 'toll_station' or not toll.get('parkingAllowed'):
        failures.append(f"收费站点位未豁免（zone={toll.get('zoneType')}）")
    if highway.get('zoneType') != 'highway' or highway.get('parkingAllowed'):
        failures.append(f"主线点位不应豁免（zone={highway.get('zoneType')}）")

    requests.post(f'{BASE}/surveillance/stop', json={}, timeout=30)

    # ---------------------------------------------------------------- 巡检前状态
    show('巡检启动前')
    status = requests.get(f'{BASE}/surveillance/status', timeout=30).json()
    print(f"运行中：{status['running']}")
    if status['running']:
        requests.post(f'{BASE}/surveillance/stop', json={}, timeout=30)
        time.sleep(2)

    # ---------------------------------------------------------------- 启动巡检
    show('启动巡检')
    camera = '1324629868805103618'   # 实测画面里有车的一路
    started = requests.post(
        f'{BASE}/surveillance/start', json={'cameraIds': [camera]}, timeout=60
    )
    if started.status_code != 200:
        print(f'启动失败 {started.status_code}：{started.text[:200]}')
        return 1
    payload = started.json()
    print(f"已启动：{payload['cameras']}  间隔 {payload['interval']} 秒")

    # ---------------------------------------------------------------- 观察
    show(f'观察 {OBSERVE_SECONDS} 秒（真实抓帧 + 推理，请稍候）')
    deadline = time.time() + OBSERVE_SECONDS
    last_rounds = -1
    while time.time() < deadline:
        time.sleep(10)
        try:
            status = requests.get(f'{BASE}/surveillance/status', timeout=30).json()
        except Exception:  # noqa: BLE001
            continue
        cameras = status['cameras']
        if not cameras:
            continue
        item = cameras[0]
        if item['rounds'] != last_rounds:
            print(
                f"  轮次={item['rounds']:<3} 已分析帧={item['framesAnalyzed']:<5} "
                f"轨迹={item['trackCount']:<3} 静止={item['stationaryCount']:<3} "
                f"告警={item['alertsRaised']:<3} 耗时={item['lastRoundMs']:.0f}ms"
                + (f"  错误={item['lastError'][:40]}" if item['lastError'] else '')
            )
            last_rounds = item['rounds']

    # ---------------------------------------------------------------- 结果核对
    show('巡检结果')
    status = requests.get(f'{BASE}/surveillance/status', timeout=30).json()
    item: Dict[str, Any] = status['cameras'][0]
    print(f"累计轮次      : {item['rounds']}")
    print(f"累计分析帧数  : {item['framesAnalyzed']}")
    print(f"累计告警      : {item['alertsRaised']}")
    print(f"场所判定      : {item['zoneLabel']}（{item['zoneType']}）")
    print(f"可停车        : {item['parkingAllowed']}")
    print(f"最近错误      : {item['lastError'] or '（无）'}")

    if item['rounds'] < 2:
        failures.append(f"巡检轮次过少（{item['rounds']}），调度线程可能未正常工作")
    if item['framesAnalyzed'] < 20:
        failures.append(f"分析帧数过少（{item['framesAnalyzed']}），抽帧或推理异常")

    # ---------------------------------------------------------------- 车主查询
    show('车主查询合规性')

    # 缺查询人 —— 必须 400
    bad = requests.post(
        f'{BASE}/surveillance/owner-lookup',
        json={'plate': '豫A12345', 'operator': '', 'reason': '违停核查'},
        timeout=30,
    )
    print(f"缺查询人      : HTTP {bad.status_code}  {(bad.json().get('detail') or {}).get('error', '')}")
    if bad.status_code != 400:
        failures.append('缺查询人未被拒绝')

    # 缺事由 —— 必须 400
    bad = requests.post(
        f'{BASE}/surveillance/owner-lookup',
        json={'plate': '豫A12345', 'operator': '410001', 'reason': ''},
        timeout=30,
    )
    print(f"缺查询事由    : HTTP {bad.status_code}  {(bad.json().get('detail') or {}).get('error', '')}")
    if bad.status_code != 400:
        failures.append('缺查询事由未被拒绝')

    # 默认通道（none）—— 必须如实返回不可用
    result = requests.post(
        f'{BASE}/surveillance/owner-lookup',
        json={'plate': '豫A12345', 'operator': '410001', 'reason': '违停告警核查'},
        timeout=30,
    ).json()
    owner = result['owner']
    print(f"默认通道      : found={owner['found']} source={owner['source']}")
    print(f"  说明        : {owner['message'][:70]}")
    print(f"  审计落库    : {result['audit']['persisted']}  outcome={result['audit']['outcome']}")
    if owner['found']:
        failures.append('未接入的通道不应返回车主数据')

    # 模拟通道 —— 必须返回脱敏结果
    result = requests.post(
        f'{BASE}/surveillance/owner-lookup',
        json={
            'plate': '豫A12345',
            'operator': '410001',
            'reason': '违停告警核查',
            'provider': 'mock',
        },
        timeout=30,
    ).json()
    owner = result['owner']
    print(f"模拟通道      : 姓名={owner['ownerName']} 证件={owner['ownerIdMasked']} 手机={owner['ownerPhoneMasked']}")
    print(f"  是否为模拟  : {owner['simulated']}")
    if not owner['found'] or '*' not in owner['ownerName']:
        failures.append('模拟通道未返回脱敏数据')
    if not owner['simulated']:
        failures.append('模拟数据未标记 simulated')

    # ---------------------------------------------------------------- 审计
    show('查询审计留痕')
    audits = requests.get(f'{BASE}/surveillance/audits?limit=5', timeout=30).json()
    print(f"审计总数：{audits['stats']['total']}  按结果：{audits['stats']['byOutcome']}")
    for item in audits['items'][:5]:
        print(f"  {item['at'][:19]}  {item['plate']}  {item['operator']}  "
              f"{item['outcome']}  事由={item['reason']}")
    if audits['stats']['total'] < 3:
        failures.append(f"审计记录过少（{audits['stats']['total']}），留痕可能未生效")

    # ---------------------------------------------------------------- 停止
    show('停止巡检')
    stopped = requests.post(f'{BASE}/surveillance/stop', json={}, timeout=60).json()
    print(f"停止结果：{stopped}")

    # ---------------------------------------------------------------- 汇总
    print('\n' + '=' * 70)
    if failures:
        print(f'发现 {len(failures)} 个问题：')
        for item in failures:
            print(f'  · {item}')
    else:
        print('全部检查通过')
    print('=' * 70)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
