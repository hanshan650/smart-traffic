"""清理由脚本造出来的演示事件（events 集合）

为什么需要它
------------
`seed_map_demo.py` 走的是**真实流程**（`/public/reports` → 复核 → `create_event`），
所以它造的数据落在 `events` 集合里，和真实事件混在一起。
而 `demo_data_service` 判定"是否已接入真实数据"看的正是
`events` 集合是否为空 —— 只要这 6 条还在，新的演示数据就不会生效。

筛选条件（两个都满足才删，避免误伤真实数据）：

  · `source_key == 'citizen'`  —— 民众上报转来的事件
  · `title` 以「民众上报：」开头

**默认只预览，不会删除。** 确认无误后加 `--yes` 执行。

用法::

    .venv\\Scripts\\python.exe scripts\\clear_seeded_events.py
    .venv\\Scripts\\python.exe scripts\\clear_seeded_events.py --yes
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import mongo  # noqa: E402

#: 与 seed_map_demo.py 造出的数据特征一致
QUERY = {'source_key': 'citizen', 'title': {'$regex': '^民众上报：'}}


def main() -> int:
    confirmed = '--yes' in sys.argv
    collection = mongo.get_db()['events']

    rows = list(collection.find(QUERY))
    total = collection.count_documents({})

    print(f'events 集合共 {total} 条，其中匹配"脚本造的" {len(rows)} 条\n')

    if not rows:
        print('没有匹配项，无需清理。')
        return 0

    for row in rows:
        print(
            f"  {str(row.get('_id'))[-6:]}  "
            f"{str(row.get('road_name') or '-'):10s}  "
            f"{str(row.get('title') or '')}"
        )

    survivors = total - len(rows)
    print(f'\n删除后剩余 {survivors} 条（应为 0 才能让演示数据生效）')

    if not confirmed:
        print('\n这是预览。确认无误后加 --yes 真正执行：')
        print('  .venv\\Scripts\\python.exe scripts\\clear_seeded_events.py --yes')
        return 0

    result = collection.delete_many(QUERY)
    print(f'\n已删除 {result.deleted_count} 条')
    print(f'events 集合剩余 {collection.count_documents({})} 条')

    # 关联的民众上报记录一并清掉，否则它们在警务端复核列表里仍会显示，
    # 而对应的事件已经没了 —— 那种半截状态更让人困惑
    reports = mongo.get_db()['citizen_reports']
    report_result = reports.delete_many({'event_id': {'$ne': ''}})
    print(f'同时清掉 {report_result.deleted_count} 条已转事件的民众上报记录')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
