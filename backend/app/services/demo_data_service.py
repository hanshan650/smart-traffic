"""民众端演示数据
================

给"行进视角"造一批**真实坐标**的事件，让没接上真实数据源时也能看到
完整效果（当前路段判定、编号标记、前方多少公里）。

为什么必须这样设计
------------------

**演示数据绝不能写进 `events` 集合。** 一旦混进去，它在界面、
统计、复核队列里与真实事件完全无法区分 —— 而这份数据的坐标是编的，
把它当成真实路况发布出去，是比"看不见效果"严重得多的问题。

因此这里用**独立集合** `demo_citizen_events`，并且：

  真实数据一旦出现，演示数据**自动停止提供**（见 :func:`should_serve`）。
  判断依据是"库里有没有真实事件"，而不是某个需要人去关的开关 ——
  开关会忘，而"有真实数据了"是客观事实。

失效条件（任一满足即不再对外提供）：

  1. 配置 `CITIZEN_DEMO_ENABLED=false`
  2. 尚未注入过演示数据
  3. **`events` 集合里存在任意一条真实事件** ← 这就是"接入真实接口即失效"

注入与清除都是显式接口（`/demo/*`），不会在启动时自动执行 ——
否则部署到新环境会莫名其妙地看到一批上海的数据。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import PyMongoError

from app.core.config import settings
from app.db import mongo
from app.models.schemas import (
    CongestionLevel,
    EventLevel,
    EventStatus,
    EventType,
    TrafficEvent,
)
from app.services.event_service import serialize_event, to_event

#: 演示数据独立存放，**不写进 events**
DEMO_COLLECTION = 'demo_citizen_events'

#: 演示数据的来源标识，仅用于排查"这些数据哪来的"
DEMO_SOURCE_KEY = 'demo:citizen'


# ---------------------------------------------------------------------------
# 演示事件内容
# ---------------------------------------------------------------------------
#
# 坐标取自上海长宁区延安西路 / 虹桥路一带，是真实存在的道路 ——
# 演示时地图上的位置与路名对得上，不会出现"标在荒地里的交通事故"。
# 精度到小数点后 4 位（约 10 m）已足够，反正是编的，不必更细。
#
# (事件类型, 级别, 路段, 标题, 描述, 纬度, 经度, 拥堵等级, 距当前分钟数)
_DEMO_ROWS: List[Tuple[Any, ...]] = [
    (
        EventType.DEBRIS, EventLevel.WARNING, '虹桥路',
        '路面有散落物',
        '虹桥路东向西近凯旋路，路面有纸箱，请提前避让',
        31.1960, 121.4195, CongestionLevel.LIGHT, 8,
    ),
    (
        EventType.COLLISION, EventLevel.CRITICAL, '延安西路',
        '两车追尾',
        '延安西路西向东，两车追尾占用一条车道，通行缓慢',
        31.2005, 121.4185, CongestionLevel.MODERATE, 14,
    ),
    (
        EventType.CONGESTION, EventLevel.WARNING, '延安西路',
        '车流量大',
        '延安西路东向西车辆排队，建议绕行天山路',
        31.1945, 121.4105, CongestionLevel.MODERATE, 21,
    ),
    (
        EventType.ABNORMAL_STOP, EventLevel.WARNING, '虹桥路',
        '车辆异常停车',
        '虹桥路西向东有车辆停在最右车道，未见人员下车',
        31.1920, 121.4230, CongestionLevel.LIGHT, 27,
    ),
    (
        EventType.CONGESTION, EventLevel.INFO, '天山路',
        '车流量大',
        '天山路近遵义路，路面积水导致车辆普遍减速',
        31.2115, 121.4085, CongestionLevel.LIGHT, 35,
    ),
    (
        EventType.CONGESTION, EventLevel.INFO, '中山北路',
        '车流量大',
        '中山北路高架下匝道排队，预计持续 20 分钟',
        31.2290, 121.4120, CongestionLevel.LIGHT, 42,
    ),
]


def _collection():
    return mongo.get_db()[DEMO_COLLECTION]


def build_events() -> List[TrafficEvent]:
    """按模板生成事件对象（**不写库**）。

    公开导出是因为 `/demo/preview` 需要在不落库的前提下展示将要写入的内容 ——
    先看再写，避免注入了才发现内容不合适。

    时间用"距当前 N 分钟"而不是写死日期：演示数据可能过很久才被查看，
    写死日期会让所有事件显示成"某某天前"，一眼假。
    """
    now = datetime.utcnow()
    events: List[TrafficEvent] = []
    for (
        event_type, level, road_name, title, description,
        latitude, longitude, congestion, minutes_ago,
    ) in _DEMO_ROWS:
        events.append(
            TrafficEvent(
                # id 最终由 MongoDB 生成：写入时走 serialize_event，
                # 它会丢掉这个字段（与真实事件的写入路径保持一致）。
                # 这里给一个占位值只为满足模型必填，不参与存储。
                id='demo',
                event_type=event_type,
                level=level,
                # 置为已确认：民众端只展示 confirmed / pending，
                # 用 pending 会让它出现在警务端的待复核队列里 ——
                # 那正是我们要避免的"混进真实流程"
                status=EventStatus.CONFIRMED,
                confidence=0.9,
                title=title,
                description=description,
                road_name=road_name,
                source_key=DEMO_SOURCE_KEY,
                congestion_level=congestion,
                latitude=latitude,
                longitude=longitude,
                created_at=now - timedelta(minutes=minutes_ago),
            )
        )
    return events


# ---------------------------------------------------------------------------
# 注入 / 清除
# ---------------------------------------------------------------------------


def seed() -> Dict[str, Any]:
    """注入演示数据。**幂等** —— 重复调用只保留一份。"""
    events = build_events()
    documents = [serialize_event(event) for event in events]
    try:
        collection = _collection()
        collection.delete_many({})
        if documents:
            # 写入时带上可读时间戳，方便直接在数据库里看出是演示数据
            for document in documents:
                document['_demo'] = True
            collection.insert_many(documents)
    except PyMongoError as exc:
        return {'success': False, 'error': f'注入失败：{exc}'}

    return {'success': True, 'seeded': len(documents), 'roads': _roads(documents)}


def clear() -> Dict[str, Any]:
    """清除演示数据。"""
    try:
        result = _collection().delete_many({})
    except PyMongoError as exc:
        return {'success': False, 'error': f'清除失败：{exc}'}
    return {'success': True, 'removed': result.deleted_count}


def _roads(documents: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for document in documents:
        road = document.get('road_name') or ''
        if road and road not in seen:
            seen.append(road)
    return seen


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def count() -> int:
    """已注入的演示数据条数。数据库不可用时返回 0。"""
    try:
        return int(_collection().count_documents({}))
    except PyMongoError:
        return 0


def real_event_count() -> int:
    """真实事件条数。**这是判定"是否已接入真实数据"的依据。"""
    try:
        return int(mongo.get_db()['events'].count_documents({}))
    except PyMongoError:
        # 数据库不可用时保守返回 1，即"当作已有真实数据" ——
        # 宁可演示数据不出现，也不要在数据库故障时把编的数据当成路况发出去
        return 1


def should_serve() -> Tuple[bool, str]:
    """是否应该把演示数据提供给公开接口。返回 (是否, 原因)。

    原因字符串会直接展示在 `/demo/status` 里，所以写成能读懂的句子 ——
    排查"为什么看不到演示数据"时不用再翻代码。
    """
    if not settings.citizen_demo_enabled:
        return False, '配置已关闭演示数据（CITIZEN_DEMO_ENABLED=false）'

    seeded = count()
    if seeded == 0:
        return False, '尚未注入演示数据，可调用 POST /api/v1/demo/seed'

    real = real_event_count()
    if real > 0:
        return (
            False,
            f'已接入真实数据（events 集合有 {real} 条），演示数据自动失效。'
            f'如需重新演示，请先清空真实事件',
        )

    return True, f'演示数据生效中（{seeded} 条），坐标与路名取自上海长宁区'


def demo_events(limit: Optional[int] = None) -> List[TrafficEvent]:
    """读取演示事件。

    **不在这里判断是否该提供** —— 调用方（公开接口）负责调用
    :func:`should_serve`。把两件事分开，是为了让"是否提供服务"这个决策
    只有一个地方做，而不是散在每个读数据的地方各判一次。
    """
    try:
        cursor = _collection().find({}).sort('created_at', -1)
        if limit:
            cursor = cursor.limit(limit)
        events = [to_event(document) for document in cursor]
    except PyMongoError:
        return []
    # _to_event 需要文档里有 _id，而演示文档是我们自己写的，一定有
    events.sort(key=lambda item: item.created_at, reverse=True)
    return events


def roads() -> List[str]:
    """演示数据涉及的路段名，去重后按首次出现顺序返回。"""
    try:
        values = _collection().distinct('road_name')
    except PyMongoError:
        return []
    return [str(value) for value in values if value]


def status() -> Dict[str, Any]:
    """演示数据的整体状态，供 `/demo/status` 与排查使用。"""
    serving, reason = should_serve()
    seeded = count()
    return {
        'configured': settings.citizen_demo_enabled,
        'seeded': seeded,
        'serving': serving,
        'reason': reason,
        'realEvents': real_event_count(),
        'collection': DEMO_COLLECTION,
        'roads': roads() if seeded else [],
    }
