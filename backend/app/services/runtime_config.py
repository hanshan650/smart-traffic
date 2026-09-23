"""
运行时配置编辑
==============

把 ``.env`` 里的一小组键做成可在网页上修改的，省掉"改配置 → 重启服务"。

--------------------------------------------------------------------------
三个刻意的限制
--------------------------------------------------------------------------
1. **默认关闭**。开关在 ``.env`` 里（``RUNTIME_CONFIG_ENABLED``），且必须
   同时设置口令（``RUNTIME_CONFIG_TOKEN``）—— "开关开着但没设口令"同样
   按关闭处理，否则等于把写 ``.env`` 的能力公开。
2. **只放开白名单内的键**。名单外的直接拒绝，否则这就是一个改写任意配置
   的后门（``MONGO_URI``、``API_PREFIX`` 都能被指向别处）。
3. **只替换既有行，不重写整个文件**。``.env`` 里有大量注释，重写会把它们
   丢光；用户手写的其他键也不能因为在本页面改了高德 Key 就消失。

写回的值会**同时热更新内存里的 ``settings``** —— 否则页面显示"已保存"
但本次进程仍用旧值，用户会以为没生效。因为 ``settings`` 是模块级单例、
其他模块都是直接 import 这个对象，所以 ``setattr`` 后全局可见。
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Tuple

from app.core.config import BASE_DIR, settings

ENV_PATH = BASE_DIR / '.env'

# 回显打码：保留头尾各 4 位。高德 Key 通常 32 位，够人核对是哪一把
_MASK_HEAD = 4
_MASK_TAIL = 4
_MASK_STARS = 6


def describe() -> Dict[str, Any]:
    """当前状态 + 可编辑的键（值已打码）。

    **不返回口令本身**，只说是否已设置。
    """
    return {
        'enabled': is_enabled(),
        'reason': _disabled_reason(),
        'envPath': str(ENV_PATH),
        'envExists': ENV_PATH.exists(),
        'items': [_item(key) for key in settings.runtime_config_key_list],
    }


def is_enabled() -> bool:
    return bool(settings.runtime_config_enabled and settings.runtime_config_token)


def _disabled_reason() -> str:
    """页面要能直接显示"为什么不能改"，而不是只给一个灰按钮"""
    if not settings.runtime_config_enabled:
        return (
            '此功能默认关闭（写入服务器配置属于敏感操作）。'
            '如需在网页上修改，请在 backend/.env 设置 '
            'RUNTIME_CONFIG_ENABLED=true，并同时设置 RUNTIME_CONFIG_TOKEN'
        )
    if not settings.runtime_config_token:
        return (
            '已开启开关，但未设置 RUNTIME_CONFIG_TOKEN。'
            '没有口令时任何人都能改写配置，因此仍按关闭处理'
        )
    return ''


def _masked(value: str) -> str:
    """打码。短值整体打码 —— 头尾各留 4 位的规则对短值等于没打"""
    if not value:
        return ''
    if len(value) <= _MASK_HEAD + _MASK_TAIL:
        return '*' * len(value)
    return f'{value[:_MASK_HEAD]}{"*" * _MASK_STARS}{value[-_MASK_TAIL:]}'


def _item(key: str) -> Dict[str, Any]:
    value = str(getattr(settings, key.lower(), '') or '')
    return {
        'key': key,
        'value': _masked(value),
        'configured': bool(value),
    }


def check_token(token: str) -> bool:
    """恒定时间比较，避免按响应时间逐字符猜口令"""
    expected = settings.runtime_config_token
    if not expected:
        return False
    return secrets.compare_digest(token or '', expected)


def apply(updates: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """写回 ``.env`` 并热更新内存配置。

    返回 ``(已改动的键, 被拒绝的原因)``。值与原值相同的键不会被写入 ——
    避免无谓地改动文件、也避免用户误以为"改了什么"。
    """
    allowed = set(settings.runtime_config_key_list)
    accepted: Dict[str, str] = {}
    rejected: List[str] = []

    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip().upper()
        if not key:
            continue
        if key not in allowed:
            rejected.append(f'{key}：不在允许修改的名单内')
            continue
        value = str(raw_value if raw_value is not None else '').strip()
        # 换行会破坏 .env 结构（写进去等于凭空多出一个键）
        if '\n' in value or '\r' in value:
            rejected.append(f'{key}：值不能包含换行')
            continue
        accepted[key] = value

    if not accepted:
        return [], rejected

    changed = _write_env(accepted)
    for key, value in changed.items():
        setattr(settings, key.lower(), value)

    return sorted(changed.keys()), rejected


def _write_env(updates: Dict[str, str]) -> Dict[str, str]:
    """只替换既有行、保留注释与顺序；文件里没有的键追加到末尾"""
    lines: List[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding='utf-8').splitlines()

    pending = dict(updates)
    changed: Dict[str, str] = {}
    out: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            raw_key = stripped.split('=', 1)[0].strip()
            upper = raw_key.upper()
            if upper in pending:
                old_value = stripped.split('=', 1)[1].strip()
                new_value = pending.pop(upper)
                if old_value != new_value:
                    changed[upper] = new_value
                # 保留原有的键名大小写，只换值
                out.append(f'{raw_key}={new_value}')
                continue
        out.append(line)

    if pending:
        if out and out[-1].strip():
            out.append('')
        for key, value in pending.items():
            out.append(f'{key}={value}')
            changed[key] = value

    if not changed:
        return {}

    text = '\n'.join(out).rstrip('\n') + '\n'
    # 原子写：先落临时文件再替换，中途失败不会留下半个 .env
    tmp = ENV_PATH.with_name('.env.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(ENV_PATH)
    return changed
