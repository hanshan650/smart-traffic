"""
初始化警员登录凭据
==================

给 `officers` 集合里的警员设置口令与角色，让警务端能登录。

    python scripts/seed_credentials.py              # 只处理还没有口令的警员
    python scripts/seed_credentials.py --reset      # 全部重置为默认口令
    python scripts/seed_credentials.py --admin 410005
    python scripts/seed_credentials.py --role 410011 viewer

--------------------------------------------------------------------------
为什么角色要按演示分配
--------------------------------------------------------------------------
四档角色（admin / dispatcher / viewer / auditor）必须都有人，否则
"权限边界"这件事没法验证 —— 只有一个管理员账号，等于没做 RBAC。

所以首次初始化会按固定顺序铺开：第一个警员给 `admin`，其余多数给
`dispatcher`，最后两个分别给 `viewer` 与 `auditor`。**这是演示分配，
不是真实组织的权限模型** —— 实际部署应按岗位指定，用 `--role` 调整。

口令一律取 `AUTH_DEFAULT_PASSWORD`（默认 `Traffic@2026`）。
生产环境应改为首次登录强制改密，这里没做那一步。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services import auth_service, officer_service  # noqa: E402
from app.services.auth_service import Role  # noqa: E402

# 演示用的角色铺开方案。写死这几个警号是为了让四档角色各有账号 ——
# 否则"权限边界"没法验证（只有一个管理员等于没做 RBAC）
_ADMIN_IDS = ('410001',)
_VIEWER_IDS = ('410011',)
_AUDITOR_IDS = ('410012',)
_DEFAULT_ROLE = Role.DISPATCHER.value


def _role_for(officer_id: str) -> str:
    if officer_id in _ADMIN_IDS:
        return Role.ADMIN.value
    if officer_id in _VIEWER_IDS:
        return Role.VIEWER.value
    if officer_id in _AUDITOR_IDS:
        return Role.AUDITOR.value
    return _DEFAULT_ROLE


def main() -> int:
    parser = argparse.ArgumentParser(description='初始化警员登录凭据')
    parser.add_argument('--reset', action='store_true', help='已有口令的也重置为默认口令')
    parser.add_argument('--admin', metavar='警号', help='把该警号设为管理员')
    parser.add_argument('--role', nargs=2, metavar=('警号', '角色'),
                        help=f'指定角色，可选：{", ".join(r.value for r in Role)}')
    args = parser.parse_args()

    password = settings.auth_default_password
    if len(password) < auth_service.MIN_PASSWORD_LENGTH:
        print(f'[错误] AUTH_DEFAULT_PASSWORD 不足 {auth_service.MIN_PASSWORD_LENGTH} 位，无法用于初始化')
        return 1

    try:
        result = officer_service.list_officers(limit=500)
    except Exception as exc:
        print(f'[错误] 读取警员失败：{exc}')
        return 1

    officers = result.items
    if not officers:
        print('[提示] 警员列表为空。先导入示例警员：')
        print('       python scripts/seed_officers.py')
        print('       或在「警员管理」页点「导入示例警员」')
        return 1

    # ---------- 单独指定角色 ----------
    if args.role or args.admin:
        if args.admin:
            target, role = args.admin, Role.ADMIN.value
        else:
            target, role = args.role[0], args.role[1]
        if role not in {item.value for item in Role}:
            print(f'[错误] 未知角色：{role}')
            return 1
        try:
            ok = officer_service.set_role(target, role)
        except Exception as exc:
            print(f'[错误] 设置角色失败：{exc}')
            return 1
        print(f'{"[OK]" if ok else "[跳过]"} {target} → {auth_service.ROLE_LABELS[role]}')
        return 0 if ok else 1

    # ---------- 批量初始化 ----------
    created, skipped, rows = 0, 0, []
    try:
        for officer in officers:
            credential = officer_service.get_credential(officer.officer_id) or {}
            has_password = bool(credential.get('password_hash'))

            if has_password and not args.reset:
                skipped += 1
                rows.append((officer.officer_id, officer.name,
                             credential.get('role') or _DEFAULT_ROLE, '（保持不变）'))
                continue

            role = credential.get('role') or _role_for(officer.officer_id)
            officer_service.set_credential(
                officer.officer_id, auth_service.hash_password(password), role
            )
            created += 1
            rows.append((officer.officer_id, officer.name, role, password))
    except Exception as exc:
        print(f'[错误] 写入凭据失败：{exc}')
        return 1

    width = max(len(row[0]) for row in rows) if rows else 6
    print()
    print(f'{"警号":<{width}}  {"姓名":<6} {"角色":<8} 口令')
    print('-' * 64)
    for officer_id, name, role, shown in rows:
        label = auth_service.ROLE_LABELS.get(role, role)
        print(f'{officer_id:<{width}}  {name:<6} {label:<8} {shown}')
    print('-' * 64)
    print(f'新设置 {created} 个，跳过 {skipped} 个（已有口令）')
    if created:
        print(f'默认口令：{password}   ← 请尽快修改')
    print()
    print('角色边界：')
    for item in auth_service.describe_roles():
        print(f'  {item["label"]:<6} {item["hint"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
