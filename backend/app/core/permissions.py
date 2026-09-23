"""
接口访问控制
============

**默认拒绝**：没有显式列进公开名单的路径，一律要求已登录。这样新增接口
时不会因为忘记挂依赖而裸奔 —— 漏配的后果是"访问不了"，而不是"谁都能访问"。

--------------------------------------------------------------------------
四档角色的边界
--------------------------------------------------------------------------
``admin``       全部
``dispatcher``  业务写操作（派警、复核、处置），但改不了警员档案与系统配置
``viewer``      只读
``auditor``     只看审计记录 —— 这是个**窄**角色：连大屏、地图都看不了
                （审计员核查操作留痕，不需要看业务画面；给多了反而扩大面）

--------------------------------------------------------------------------
已知缺口：``/static`` 目前是公开的
--------------------------------------------------------------------------
``backend/static/uploads/`` 下同时放着两类东西：

- **检测快照**：真实道路画面，可能带车牌与行人面部 —— 属隐私内容
- **民众上报照片**：民众端提交后要回显给自己看

`/static` 走 Vite 代理，是同源请求，cookie 会自动携带，技术上能保护。
但一旦要求登录，**民众端（本就不登录）的照片会全部裂掉** —— 那是功能性
回归，比"暂未修复"更糟。

正确做法是把上传分成两个目录：内部快照进受保护的目录，民众上报进公开目录。
但那要同时改 5 处生成路径（detection / accident / flow / surveillance /
citizen_report）与上传逻辑，属于独立的一件事，不混在本次登录改造里。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.services.auth_service import CurrentUser, Role

READ_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

# --------------------------------------------------------------------------
# 无需登录的路径前缀
# --------------------------------------------------------------------------
PUBLIC_PREFIXES: Tuple[str, ...] = (
    # 民众端全部。它按设计就是不登录可用的，后端在路由层做数据隔离
    '/api/v1/public',
    # 登录本身，以及给登录页展示的角色说明
    '/api/v1/auth/login',
    '/api/v1/auth/roles',
    # 健康检查：探活脚本不方便带会话
    '/api/v1/health',
    # 前端运行时配置（高德 JS Key、阈值、地图中心）—— 民众端地图要用。
    # 注意这里的 Key 本身就是下发到浏览器的，不属敏感信息
    '/api/v1/config',
    # 演示数据接口。仅用于"未接入真实数据源时展示行进视角"，
    # 生产应设 CITIZEN_DEMO_ENABLED=false
    '/api/v1/demo',
    # 静态资源。见文件头"已知缺口"
    '/static',
    # 接口文档。生产应关闭 DEBUG 后一并关掉
    '/docs',
    '/redoc',
    '/openapi.json',
)

# 根路径（精确匹配，不用前缀）
PUBLIC_EXACT: Tuple[str, ...] = ('/', '/favicon.ico')

# 四档角色的全集。用于"登出/改密码"这类人人可做的自助操作
ALL_ROLES = frozenset(
    {Role.ADMIN.value, Role.DISPATCHER.value, Role.VIEWER.value, Role.AUDITOR.value}
)

# --------------------------------------------------------------------------
# 写操作的角色要求
# --------------------------------------------------------------------------
# 顺序敏感：**先匹配到的生效**，所以具体的规则要排在宽泛的之前。
#
# 未列出的写操作默认只允许 admin（见 `check`）—— 宁可拦得太严，
# 也不要漏放一个"谁能调"没想清楚的写接口。
WRITE_RULES: List[Tuple[str, frozenset]] = [
    # 账号自助：登出、改自己的口令。这是"处理自己的账号"，任何登录角色
    # 都该能做。
    #
    # 漏掉这一条的后角很具体：非管理员点"退出登录"会拿到 403，退不出去 ——
    # 默认拒绝规则会把登出当成"需要特权的写操作"。
    (r'^/api/v1/auth/(logout|password)$', ALL_ROLES),
    # 警员档案：增删改都是管理员的事。值班员派警只需要**读**警员列表
    (r'^/api/v1/officers', frozenset({Role.ADMIN.value})),
    # 在线改配置（写 .env）：仅管理员
    (r'^/api/v1/runtime-config', frozenset({Role.ADMIN.value})),
    # 查车主：涉个人信息，限管理员与值班员。
    # 该接口本身有强制审计留痕，这里再收一道
    (r'^/api/v1/surveillance/owner-lookup', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    # 事件复核、民众上报复核、警情处置：值班员的本职
    (r'^/api/v1/events/.*/review', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    (r'^/api/v1/reports/.*/review', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    # 民众上报归档。**必须单独登记**：上面的 review 规则是
    # `.*/review`，匹配不到 `/archive`，漏了这条会落到"未登记 → 仅 admin"，
    # 值班员在界面上点归档就是 403
    (r'^/api/v1/reports/.*/archive', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    (r'^/api/v1/incidents', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    # 违停告警处置与巡检开关
    (r'^/api/v1/surveillance', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    # 检测/事故分析：会占用 CPU，但属日常使用，值班员可用
    (r'^/api/v1/detection', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
    (r'^/api/v1/accident', frozenset({Role.ADMIN.value, Role.DISPATCHER.value})),
]

# --------------------------------------------------------------------------
# 审计员
# --------------------------------------------------------------------------
# 窄角色：只放行这几个前缀，其余一律 403。
AUDITOR_ALLOWED_PREFIXES: Tuple[str, ...] = (
    '/api/v1/surveillance/audits',
    '/api/v1/auth',  # 至少得能登出、能看自己是谁
)


class PermissionError_(Exception):
    """权限不足。与内置 PermissionError 区分开，避免被误捕获"""

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


def is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _writers_for(path: str) -> Optional[frozenset]:
    for pattern, roles in WRITE_RULES:
        if re.match(pattern, path):
            return roles
    return None


def check(user: CurrentUser, method: str, path: str) -> None:
    """不通过就抛 `PermissionError_`。调用方（中间件）转成 403"""
    role = user.role

    # 审计员是窄角色，单独一条路径判断，不参与下面的通用规则 ——
    # 否则"只读即可"的通用规则会顺手把大屏、地图都放给它
    if role == Role.AUDITOR.value:
        if any(path.startswith(prefix) for prefix in AUDITOR_ALLOWED_PREFIXES):
            return
        raise PermissionError_(
            '审计角色只能查阅审计记录。需要查看业务页面请让管理员调整角色'
        )

    if method in READ_METHODS:
        # 只要登录了就能读（auditor 已在上面单独处理）
        return

    writers = _writers_for(path)
    if writers is None:
        # 未登记的写操作：只放行管理员。新增写接口时若忘了登记，
        # 结果是"只有管理员能用"，而不是"谁都能用"
        if role != Role.ADMIN.value:
            raise PermissionError_('该操作暂未开放给你的角色，请联系管理员')
        return

    if role not in writers:
        raise PermissionError_(
            f'当前角色（{user.role_label}）无权执行该操作'
        )


def describe_rules() -> List[dict]:
    """给前端/文档用的规则说明，便于排查"为什么我点不动这个按钮" """
    return [
        {'pattern': pattern, 'roles': sorted(roles)}
        for pattern, roles in WRITE_RULES
    ]
