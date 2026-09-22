"""时间线一致性验证
==================

回归目标
--------
每次状态流转（上报 / 派警 / 接警 / 到场 / 办结）的**接口响应**里，
必须已经包含刚写入的那一条时间线记录。

历史缺陷：``_append_timeline`` 在 ``find_one_and_update`` 之后执行 ``$push``，
但服务层返回的仍是推送前的文档快照，导致响应永远少最新一条 —— 前端表现为
「刚点了接警，时间线上却没有接警记录」，让人误以为操作没生效。

运行前需先启动后端：
    python scripts/verify_timeline.py
"""
from __future__ import annotations

import sys

import requests

BASE = 'http://127.0.0.1:8000/api/v1'


def _check(step: str, payload: dict, expected: tuple[str, ...]) -> bool:
    timeline = payload.get('timeline') or []
    last = timeline[-1]['action'] if timeline else '(空)'
    ok = last in expected
    print(
        f"{'PASS' if ok else 'FAIL'}  {step:<10} "
        f"status={payload.get('status'):<11} 时间线={len(timeline)}条 末条={last}"
    )
    return ok


def main() -> int:
    created = requests.post(
        f'{BASE}/incidents',
        json={
            'type': 'collision',
            'level': 'critical',
            'address': '时间线一致性验证',
            'latitude': 34.7466,
            'longitude': 113.6254,
            'score': 0.9,
            'autoReport': True,
        },
        timeout=30,
    )
    created.raise_for_status()
    incident = created.json()
    incident_id = incident['id']

    results = [_check('create', incident, ('create', 'report'))]

    dispatched = requests.post(
        f'{BASE}/incidents/{incident_id}/dispatch',
        json={'strategy': 'nearest', 'topN': 5},
        timeout=60,
    )
    dispatched.raise_for_status()
    results.append(_check('dispatch', dispatched.json(), ('dispatch',)))

    for action in ('accept', 'arrive', 'close'):
        response = requests.post(
            f'{BASE}/incidents/{incident_id}/status',
            params={'action': action},
            timeout=30,
        )
        response.raise_for_status()
        results.append(_check(action, response.json(), (action,)))

    # 重新读取一次，确认落库结果与响应一致
    final = requests.get(f'{BASE}/incidents/{incident_id}', timeout=30).json()
    stored = [entry['action'] for entry in final['timeline']]
    expected = ['create', 'report', 'dispatch', 'accept', 'arrive', 'close']
    consistent = stored == expected
    print(f"\n落库时间线: {' → '.join(stored)}")
    print(f"{'PASS' if consistent else 'FAIL'}  与预期序列一致")

    passed = all(results) and consistent
    print('\n结果:', '全部通过' if passed else '存在失败项')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
