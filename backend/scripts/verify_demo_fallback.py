"""验证"接入真实数据后演示数据自动失效"

这是演示机制最关键的一条性质，必须真的走一遍：
造一条**真实流程**产生的事件（民众上报 → 复核通过 → create_event），
然后确认 /public/* 立刻改用真实数据、并把 demo 标记去掉。

跑完会清理这条测试数据，不留痕迹。

用法::

    .venv\\Scripts\\python.exe scripts\\verify_demo_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASE = 'http://127.0.0.1:8000/api/v1'

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = '') -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f'  [OK] {label}')
    else:
        failed += 1
        print(f'  [!!] {label}')
        if detail:
            print(f'       {detail}')


def get(path: str, **params):
    return requests.get(f'{BASE}{path}', params=params, timeout=20).json()


def main() -> int:
    print('=' * 66)
    print('一、注入演示数据后，公开接口应交付演示数据')
    print('=' * 66)

    requests.post(f'{BASE}/demo/seed', timeout=20)

    status = get('/demo/status')
    check('演示数据已注入', status.get('seeded', 0) > 0, str(status))
    check('没有真实事件', status.get('realEvents') == 0,
          f"realEvents = {status.get('realEvents')}")
    check('serving 为 true', status.get('serving') is True, status.get('reason', ''))

    traffic = get('/public/traffic', limit=20)
    check('公开接口标记 demo = true', traffic.get('demo') is True, str(traffic.get('demo')))
    check('返回的是演示事件', traffic.get('total', 0) > 0, f"total = {traffic.get('total')}")
    sample_roads = {item['roadName'] for item in traffic.get('items', [])}
    check('路段来自上海', any(r in sample_roads for r in ('延安西路', '虹桥路')),
          f'实际路段: {sample_roads}')

    overview = get('/public/overview')
    check('概览也标记 demo = true', overview.get('demo') is True)

    print()
    print('=' * 66)
    print('二、接入一条真实事件')
    print('=' * 66)

    report = requests.post(
        f'{BASE}/public/reports',
        data={
            'report_type': 'accident',
            'description': '【自动验证用】真实数据接入测试',
            'location_text': '测试路段 K0',
            'road_name': '测试路段',
            'contact': '',
            'latitude': '31.2300',
            'longitude': '121.4700',
        },
        timeout=30,
    )
    if report.status_code != 200:
        print(f'  上报失败: {report.status_code} {report.text[:200]}')
        return 1
    report_no = report.json()['reportNo']
    check('民众上报已提交', bool(report_no), report_no)

    review = requests.post(
        f'{BASE}/reports/{report_no}/review',
        json={'action': 'approve', 'reviewer': 'verify-bot', 'note': '自动验证'},
        timeout=30,
    )
    check('复核通过并生成真实事件', review.status_code == 200,
          f'{review.status_code} {review.text[:200]}')

    print()
    print('=' * 66)
    print('三、演示数据应自动失效')
    print('=' * 66)

    status = get('/demo/status')
    check('realEvents 变为 1', status.get('realEvents') == 1,
          f"realEvents = {status.get('realEvents')}")
    check('serving 变为 false', status.get('serving') is False,
          f"serving = {status.get('serving')}")
    check('reason 说明已接入真实数据', '已接入真实数据' in (status.get('reason') or ''),
          status.get('reason', ''))

    traffic = get('/public/traffic', limit=20)
    check('公开接口 demo 变为 false', traffic.get('demo') is False,
          f"demo = {traffic.get('demo')}")
    roads = [item['roadName'] for item in traffic.get('items', [])]
    check('不再返回演示数据', '延安西路' not in roads, f'实际路段: {roads}')
    check('返回的是真实事件', '测试路段' in roads, f'实际路段: {roads}')

    overview = get('/public/overview')
    check('概览 demo 变为 false', overview.get('demo') is False)

    print()
    print('=' * 66)
    print('四、清理')
    print('=' * 66)
    print('  测试写入的民众上报与真实事件需要手动清理：')
    print('    .venv\\Scripts\\python.exe scripts\\clear_seeded_events.py --yes')
    print('  演示数据本身仍保留，待真实事件清空后会自动重新生效。')

    print()
    print('=' * 66)
    print(f'  通过 {passed} 项，失败 {failed} 项')
    print('=' * 66)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
