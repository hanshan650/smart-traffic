"""生成地图演示数据
==================

往库里写入几条**带真实坐标**的民众上报并复核通过，用于在地图上看到
多个点位的效果。

为什么需要它：地图上的点位来自事件自带的坐标，而事件要么由检测产生
（需要上游可用），要么由民众上报产生。上游会限流，跑一次检测也只有一个
点位 —— 想看多点的分布，直接造数据最快。

坐标取河南高速沿线的几个真实位置，便于判断地图视野是否正确。

用法::

    .venv\\Scripts\\python.exe scripts\\seed_map_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASE = 'http://127.0.0.1:8000/api/v1'

#: (类型, 描述, 位置, 路段, 纬度, 经度)
SAMPLES = [
    ('accident', '两车追尾，占用行车道', '京港澳高速 K712 许昌段', '京港澳高速', 34.0358, 113.8521),
    ('debris', '路面有掉落物，已有多辆车急刹', '连霍高速 K590 郑州段', '连霍高速', 34.7466, 113.6254),
    ('construction', '施工占道，仅剩一条车道通行', '大广高速 K1856 开封段', '大广高速', 34.7972, 114.3074),
    ('weather', '路面积水，车辆普遍减速', '沪陕高速 K1080 南阳段', '沪陕高速', 32.9908, 112.5286),
    ('congestion', '车流量大，通行缓慢', '京港澳高速 K980 信阳段', '京港澳高速', 32.1470, 114.0919),
    ('accident', '货车侧翻，占用应急车道', '晋新高速 K140 新乡段', '晋新高速', 35.3230, 113.9468),
]


def main() -> int:
    created = 0
    approved = 0

    for report_type, description, location, road, lat, lng in SAMPLES:
        response = requests.post(
            f'{BASE}/public/reports',
            data={
                'report_type': report_type,
                'description': description,
                'location_text': location,
                'road_name': road,
                'contact': '',
                'latitude': str(lat),
                'longitude': str(lng),
            },
            timeout=30,
        )
        if response.status_code != 200:
            print(f'  跳过（{response.status_code}）：{description[:20]} '
                  f'{(response.json().get("detail") or {}).get("error", "")}')
            continue

        report_no = response.json()['reportNo']
        created += 1

        reviewed = requests.post(
            f'{BASE}/reports/{report_no}/review',
            json={
                'action': 'approve',
                'reviewer': '410001',
                'note': '已核实并转入处置',
                'level': 'critical' if report_type == 'accident' else 'warning',
            },
            timeout=60,
        )
        if reviewed.status_code == 200:
            approved += 1
            print(f'  ✓ {location}  ({lat}, {lng})')
        else:
            print(f'  ✗ 复核失败 {report_no}: {reviewed.text[:100]}')

    print(f'\n创建上报 {created} 条，复核通过 {approved} 条')
    print('打开 http://localhost:5173/citizen 并切到「地图」查看')
    return 0


if __name__ == '__main__':
    sys.exit(main())
