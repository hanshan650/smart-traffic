"""民众端公开接口验证
======================

重点验证**数据隔离**，而不是功能可用性。

公开接口的失效方式与内部接口不同：内部接口出错会报错，容易发现；
公开接口出错是**静默泄露** —— 多返回一个字段，没有报错、没有异常日志，
但摄像头编号、值班员姓名或图像内容就这样流向了互联网。

因此本脚本的核心是一组**禁止字段断言**：逐一检查公开响应里
不应出现的字段名。将来内部事件模型新增字段时，这组断言就是防线。

覆盖内容
--------
1. 公开路况响应不含任何内部字段（白名单式构造的有效性）
2. 概览接口只给数量与分布，不含明细
3. 出行建议明确声明"非导航"
4. 上报创建：必填校验、长度上限、类型校验
5. 上报查询：凭编号可查，不存在的编号返回 404
6. **联系方式不出现在公开视图，只出现在警务视图**
7. 复核：缺复核人被拒、已处理不可重复复核、通过后转为正式事件
8. 应急电话是真实号码

用法（先启动后端）::

    .venv\\Scripts\\python.exe scripts\\verify_citizen.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASE = 'http://127.0.0.1:8000/api/v1'

PASSED: List[str] = []
FAILED: List[str] = []

#: 公开响应中**绝不能出现**的字段。这不是"建议"，是安全边界。
FORBIDDEN_FIELDS: Set[str] = {
    'cameraId',        # 摄像头编号：可用于精确关联基础设施
    'cameraName',
    'snapshotUrl',     # AI 快照：可能含车牌与行人面部
    'confidence',      # 算法内部指标
    'reviewedBy',      # 值班员姓名
    'sourceKey',       # 视频源标识
    'clientKey',
    'id',              # 数据库 id
}

#: 公开响应文本中**绝不能出现**的短语。
#:
#: 只检查字段名是不够的 —— 信息也会藏在**字段的值**里。实际就出过
#: 一次：为了提示值班员，事件描述里拼了一句「（上报人联系方式见上报
#: 记录）」，而事件描述恰好会进入公开路况接口，于是这句话跟着
#: 暴露给了所有民众。字段名检查完全挡不住这种情况。
LEAKY_PHRASES: List[str] = [
    '联系方式见',
    '上报记录',
    '警号',
    '值班员',
    '警员',
    '审核',
    '复核人',
]


def find_leaky_text(value: Any, hits: Set[str]) -> None:
    """递归扫描所有字符串值，找公开文本里的内部术语。"""
    if isinstance(value, dict):
        for item in value.values():
            find_leaky_text(item, hits)
    elif isinstance(value, list):
        for item in value:
            find_leaky_text(item, hits)
    elif isinstance(value, str):
        for phrase in LEAKY_PHRASES:
            if phrase in value:
                hits.add(phrase)


def check(name: str, condition: bool, detail: str = '') -> None:
    (PASSED if condition else FAILED).append(name)
    mark = 'PASS' if condition else 'FAIL'
    print(f'{mark}  {name}' + (f'    {detail}' if detail else ''))


def collect_keys(value: Any, into: Set[str]) -> None:
    """递归收集嵌套结构里出现的所有字段名。"""
    if isinstance(value, dict):
        for key, item in value.items():
            into.add(key)
            collect_keys(item, into)
    elif isinstance(value, list):
        for item in value:
            collect_keys(item, into)


def show(title: str) -> None:
    print(f'\n--- {title} ---')


def main() -> int:
    try:
        requests.get(f'{BASE}/public/emergency', timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f'后端不可用（{exc}）\n请先启动后端：')
        print('  Start-Process .venv\\Scripts\\python.exe -ArgumentList '
              "'-m','uvicorn','app.main:app','--port','8000'")
        return 1

    # ==================================================================
    show('1. 公开路况的数据隔离')
    # ==================================================================
    traffic = requests.get(f'{BASE}/public/traffic', timeout=30).json()
    keys: Set[str] = set()
    collect_keys(traffic, keys)
    check(
        '路况接口正常返回',
        traffic.get('success') is True,
        f'响应={str(traffic)[:90]}',
    )
    leaked = keys & FORBIDDEN_FIELDS
    check(
        '公开路况不含任何内部字段',
        not leaked,
        f'泄露字段={sorted(leaked)}' if leaked else f'检查了 {len(keys)} 个字段名',
    )

    # 字段名干净不代表内容干净 —— 内部提示语可能藏在描述文本里
    phrase_hits: Set[str] = set()
    find_leaky_text(traffic, phrase_hits)
    check(
        '公开路况的文本内容不含内部术语',
        not phrase_hits,
        f'命中={sorted(phrase_hits)}' if phrase_hits else f'检查了 {len(LEAKY_PHRASES)} 个敏感短语',
    )
    check(
        '公开路况返回了条目结构',
        'items' in traffic and isinstance(traffic['items'], list),
        f"条目数={traffic.get('total', 0)}",
    )
    if traffic.get('items'):
        sample = traffic['items'][0]
        check(
            '事件条目含民众可读的中文标签',
            bool(sample.get('eventTypeLabel')) and bool(sample.get('levelLabel')),
            f"{sample.get('eventTypeLabel')} / {sample.get('levelLabel')}",
        )

    # ==================================================================
    show('2. 概览与建议')
    # ==================================================================
    overview = requests.get(f'{BASE}/public/overview', timeout=30).json()
    overview_keys: Set[str] = set()
    collect_keys(overview, overview_keys)
    # 先断言接口本身没报错。缺少这一条时，500 的错误结构体因为不含
    # 禁止字段反而会让"数据隔离"断言通过 —— 假阳性。
    check(
        '概览接口正常返回',
        overview.get('success') is True,
        f'响应={str(overview)[:90]}',
    )
    check(
        '概览不含内部字段',
        not (overview_keys & FORBIDDEN_FIELDS),
        f"整体={overview.get('overallLabel')}",
    )
    check(
        '概览给出整体印象标签',
        bool(overview.get('overall')) and bool(overview.get('overallLabel')),
        f"{overview.get('overall')} / {overview.get('overallLabel')}",
    )

    advice = requests.get(f'{BASE}/public/advice', timeout=30).json()
    check(
        '出行建议明确声明不是导航',
        '不是导航' in advice.get('disclaimer', ''),
        advice.get('disclaimer', '')[:60] + '…',
    )

    # ==================================================================
    show('3. 上报创建与校验')
    # ==================================================================
    options = requests.get(f'{BASE}/public/options', timeout=30).json()
    check(
        '上报类型选项可用',
        len(options.get('reportTypes', [])) >= 5,
        f"{len(options.get('reportTypes', []))} 种类型",
    )
    check(
        '限制条件对前端可见',
        'rateLimit' in options.get('limits', {}),
        str(options.get('limits', {})),
    )

    # 非法类型
    bad = requests.post(
        f'{BASE}/public/reports',
        data={'report_type': 'not_a_type', 'description': '测试'},
        timeout=30,
    )
    check('非法上报类型被拒绝', bad.status_code == 400,
          (bad.json().get('detail') or {}).get('error', '')[:50])

    # 描述与位置全空
    bad = requests.post(
        f'{BASE}/public/reports',
        data={'report_type': 'accident'},
        timeout=30,
    )
    check('描述与位置全空被拒绝', bad.status_code == 400,
          (bad.json().get('detail') or {}).get('error', '')[:50])

    # 正常上报
    created = requests.post(
        f'{BASE}/public/reports',
        data={
            'report_type': 'debris',
            'description': '行车道上有掉落的纸箱，已有多辆车急刹',
            'location_text': '京港澳高速 K712+300 附近',
            'road_name': '京港澳高速',
            'contact': '13800001234',
            'latitude': '34.74661',
            'longitude': '113.62544',
        },
        timeout=30,
    )
    if created.status_code != 200:
        print(f'  上报失败：{created.text[:200]}')
        return 1
    payload = created.json()
    report_no = payload['reportNo']
    check('上报成功并返回编号', bool(report_no), f'编号={report_no}')

    public_report = payload['report']
    public_keys: Set[str] = set()
    collect_keys(public_report, public_keys)
    check(
        '上报响应不含联系方式',
        'contact' not in public_keys,
        f'字段={sorted(public_keys)}',
    )
    check(
        '坐标已降精度到 3 位小数',
        public_report.get('latitude') == 34.747 and public_report.get('longitude') == 113.625,
        f"{public_report.get('latitude')}, {public_report.get('longitude')}",
    )
    check(
        '状态含面向民众的说明文案',
        bool(public_report.get('statusLabel')),
        public_report.get('statusLabel', ''),
    )

    # ==================================================================
    show('4. 进度查询')
    # ==================================================================
    found = requests.get(f'{BASE}/public/reports/{report_no}', timeout=30).json()
    check('凭编号可查回上报', found.get('report', {}).get('reportNo') == report_no)

    missing = requests.get(f'{BASE}/public/reports/MZ197001010001', timeout=30)
    check('不存在的编号返回 404', missing.status_code == 404,
          (missing.json().get('detail') or {}).get('error', '')[:50])

    # ==================================================================
    show('5. 警务视图与公开视图的差异')
    # ==================================================================
    queue = requests.get(f'{BASE}/reports?status=pending&limit=50', timeout=30).json()
    target = None
    for item in queue.get('items', []):
        if item.get('reportNo') == report_no:
            target = item
            break

    check('警务队列能查到该上报', target is not None)
    if target:
        check(
            '警务视图含未脱敏联系方式',
            'contact' in target and target['contact'] == '13800001234',
            f"contact={target.get('contact')}",
        )
        check(
            '警务视图同时给出脱敏形式',
            '138****1234' == target.get('contactMasked'),
            f"contactMasked={target.get('contactMasked')}",
        )

    # ==================================================================
    show('6. 复核流程')
    # ==================================================================
    bad = requests.post(
        f'{BASE}/reports/{report_no}/review',
        json={'action': 'approve', 'reviewer': ''},
        timeout=30,
    )
    check('缺复核人被拒绝', bad.status_code == 400,
          (bad.json().get('detail') or {}).get('error', '')[:60])

    approved = requests.post(
        f'{BASE}/reports/{report_no}/review',
        json={'action': 'approve', 'reviewer': '410001', 'note': '已联系路政清理'},
        timeout=60,
    )
    if approved.status_code != 200:
        print(f'  复核失败：{approved.text[:200]}')
    else:
        reviewed = approved.json()
        check(
            '复核通过后状态更新',
            reviewed.get('status') == 'approved',
            f"status={reviewed.get('status')} 复核人={reviewed.get('reviewedBy')}",
        )
        check(
            '复核通过后转为正式事件',
            bool(reviewed.get('eventId')),
            f"eventId={reviewed.get('eventId')}",
        )

    # 重复复核
    again = requests.post(
        f'{BASE}/reports/{report_no}/review',
        json={'action': 'reject', 'reviewer': '410001'},
        timeout=30,
    )
    check('已处理的上报不可重复复核', again.status_code == 400,
          (again.json().get('detail') or {}).get('error', '')[:60])

    # 民众侧应能看到处理结果
    after = requests.get(f'{BASE}/public/reports/{report_no}', timeout=30).json()
    check(
        '民众能看到复核结果与意见',
        after['report']['status'] == 'approved' and '清理' in after['report'].get('reviewNote', ''),
        f"statusLabel={after['report'].get('statusLabel')}",
    )

    # ==================================================================
    show('7. 应急信息')
    # ==================================================================
    emergency = requests.get(f'{BASE}/public/emergency', timeout=30).json()
    numbers = {item['number'] for item in emergency.get('contacts', [])}
    check(
        '包含真实应急电话',
        {'122', '12122', '120', '119', '110'}.issubset(numbers),
        f'号码={sorted(numbers)}',
    )
    check(
        '包含安全须知内容',
        len(emergency.get('tips', [])) >= 3,
        f"{len(emergency.get('tips', []))} 条须知",
    )

    # ==================================================================
    print('\n' + '=' * 70)
    print(f'通过 {len(PASSED)} 项，失败 {len(FAILED)} 项')
    if FAILED:
        print('\n失败项：')
        for name in FAILED:
            print(f'  · {name}')
    print('=' * 70)
    return 0 if not FAILED else 1


if __name__ == '__main__':
    sys.exit(main())
