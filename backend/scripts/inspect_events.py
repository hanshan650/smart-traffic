"""查看 events 集合里现有事件的来源，用于判断哪些是"造"出来的。

用途很小但必要：`seed_map_demo.py` 是走**真实流程**
（`/public/reports` → 复核通过 → `create_event`）造的数据，
所以它们和真实事件躺在同一个集合里，只能靠 source_key / 描述特征分辨。

只读，不修改任何数据。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import mongo  # noqa: E402


def main() -> int:
    collection = mongo.get_db()['events']
    total = collection.count_documents({})
    print(f'events 集合共 {total} 条\n')

    if not total:
        print('（空）')
        return 0

    rows = list(collection.find({}))
    by_source = Counter(str(r.get('source_key') or '(空)') for r in rows)
    print('按 source_key 分组：')
    for key, n in by_source.most_common():
        print(f'  {n:3d}  {key}')

    print('\n明细：')
    for r in rows:
        print(
            f"  {str(r.get('_id'))[-6:]}  "
            f"{str(r.get('source_key') or '-'):18s}  "
            f"{str(r.get('road_name') or '-'):10s}  "
            f"{str(r.get('event_type')):14s}  "
            f"{str(r.get('title') or '')[:24]}"
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
