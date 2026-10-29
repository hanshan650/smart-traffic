"""违停告警分页、组合筛选与时间边界离线验证。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402

from app.api.v1.surveillance import list_alerts as alerts_endpoint  # noqa: E402
from app.services import surveillance_service  # noqa: E402


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)

    def sort(self, key: str, direction: int):
        self.documents.sort(key=lambda item: item[key], reverse=direction < 0)
        return self

    def skip(self, count: int):
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeCollection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for key, expected in query.items():
            value = document.get(key)
            if isinstance(expected, dict):
                if '$gte' in expected and value < expected['$gte']:
                    return False
                if '$lte' in expected and value > expected['$lte']:
                    return False
            elif value != expected:
                return False
        return True

    def find(self, query: dict) -> FakeCursor:
        return FakeCursor([item for item in self.documents if self._matches(item, query)])

    def count_documents(self, query: dict) -> int:
        return sum(self._matches(item, query) for item in self.documents)


class FakeDB:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def __getitem__(self, _name: str) -> FakeCollection:
        return self.collection


def main() -> int:
    base = datetime(2026, 1, 1)
    cameras = ['cam-a', 'cam-a', 'cam-b', 'cam-a', 'cam-b']
    statuses = ['pending', 'verified', 'pending', 'pending', 'handled']
    documents = [
        {
            '_id': index,
            'camera_id': cameras[index],
            'track_id': index,
            'class_name': 'car',
            'status': statuses[index],
            'created_at': base + timedelta(minutes=index),
        }
        for index in range(5)
    ]
    database = FakeDB(FakeCollection(documents))

    with patch.object(surveillance_service, 'get_db', return_value=database):
        first = surveillance_service.query_alerts(page=1, page_size=2)
        second = surveillance_service.query_alerts(page=2, page_size=2)
        assert first['total'] == second['total'] == 5
        assert [item['id'] for item in first['items']] == ['4', '3']
        assert [item['id'] for item in second['items']] == ['2', '1']
        print('PASS  跨页排序稳定且 total 不随页大小变化')

        filtered = surveillance_service.query_alerts(
            page=1, page_size=10, camera_id='cam-a', status='pending'
        )
        assert filtered['total'] == 2
        assert [item['id'] for item in filtered['items']] == ['3', '0']
        print('PASS  摄像头与状态组合筛选使用同一条件计数')

        bounded = surveillance_service.query_alerts(
            page=1,
            page_size=10,
            start_time=base + timedelta(minutes=1),
            end_time=base + timedelta(minutes=3),
        )
        assert bounded['total'] == 3
        assert [item['id'] for item in bounded['items']] == ['3', '2', '1']
        print('PASS  起止时间边界均包含')

        aware = surveillance_service.query_alerts(
            page=1,
            page_size=10,
            start_time=(base + timedelta(minutes=4)).replace(tzinfo=timezone.utc),
        )
        assert aware['total'] == 1 and aware['items'][0]['id'] == '4'
        print('PASS  带时区时间统一转换为 UTC 查询')

        legacy = surveillance_service.list_alerts(limit=2)
        assert isinstance(legacy, list) and len(legacy) == 2
        print('PASS  原 list_alerts 列表契约保持兼容')

        empty = surveillance_service.query_alerts(page=1, page_size=10, camera_id='missing')
        assert empty == {'items': [], 'total': 0}
        print('PASS  空结果与总记录数正确区分')

        response = alerts_endpoint(
            page=2,
            page_size=2,
            limit=None,
            camera_id='',
            status='',
            start_time=None,
            end_time=None,
        )
        assert response['total'] == 5 and response['totalPages'] == 3
        assert response['page'] == 2 and response['pageSize'] == 2
        assert len(response['items']) == 2
        print('PASS  API 返回完整分页元数据')

        legacy_response = alerts_endpoint(
            page=1,
            page_size=20,
            limit=3,
            camera_id='',
            status='',
            start_time=None,
            end_time=None,
        )
        assert legacy_response['pageSize'] == 3 and len(legacy_response['items']) == 3
        print('PASS  旧 limit 参数继续作为页大小生效')

        try:
            alerts_endpoint(
                page=1,
                page_size=20,
                limit=None,
                camera_id='',
                status='',
                start_time=base + timedelta(days=1),
                end_time=base,
            )
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError('未拒绝颠倒的时间范围')
        print('PASS  颠倒的时间范围返回 400')

    print('\n告警查询离线验证全部通过')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'FAIL  {type(exc).__name__}: {exc}')
        raise SystemExit(1) from exc
