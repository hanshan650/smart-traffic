"""检查生成的 Service Worker 是否包含预期的缓存规则。

PWA 的配置写对了不代表产物对 —— vite-plugin-pwa 会把 runtimeCaching
和 navigateFallbackDenylist 序列化进 sw.js，只有真去读产物才能确认
"公开接口缓存、警务接口不缓存、API 不落到 SPA fallback" 这三件事
真的生效了。
"""

from __future__ import annotations

import io
import re
from pathlib import Path

SW = Path(r'd:\University\graduation project\smart-traffic\frontend\dist\sw.js')

source = io.open(SW, encoding='utf-8').read()

# 每项：(说明, 在产物里找的字符串, 期望出现?)
#
# 注意比对的是**构建产物**里序列化后的写法，不是 vite.config.ts 里的写法：
# 正则会被转义（`/api/v1/public/` → `/\/api\/v1\/public\//`），
# 而 `navigateFallbackDenylist` 这个配置项在产物里变成
# `NavigationRoute(..., { denylist: [...] })`。按配置项名去找必然找不到。
CHECKS: list[tuple[str, str, bool]] = [
    ('预缓存路由', 'precacheAndRoute', True),
    ('公开接口运行时缓存', r'api\/v1\/public', True),
    ('NetworkFirst 策略', 'NetworkFirst', True),
    ('公开接口缓存名', 'public-api', True),
    ('缓存过期清理', 'ExpirationPlugin', True),
    ('缓存条数上限 40', 'maxEntries:40', True),
    ('缓存有效期 600s', 'maxAgeSeconds:600', True),
    ('高德走 NetworkOnly', 'NetworkOnly', True),
    ('SPA fallback 兜底页', 'createHandlerBoundToURL("/index.html")', True),
    # 关键：接口与静态文件不能落到 SPA fallback，
    # 否则接口 404 会返回一份 HTML 让前端去 JSON.parse
    ('API 排除在 fallback 外', 'denylist:[/^\\/api\\//', True),
    ('静态资源排除在 fallback 外', '/^\\/static\\//', True),
]

print('=' * 62)
failed = 0
for label, needle, want in CHECKS:
    found = needle in source
    ok = found == want
    if not ok:
        failed += 1
    print(f'  [{"OK" if ok else "!!"}] {label:24} {"命中" if found else "未命中"}')

print('-' * 62)
print(f'  预缓存条目数 : {len(re.findall(r"revision:", source))}')
print(f'  文件大小     : {SW.stat().st_size / 1024:.1f} KB')
print('=' * 62)
print('全部通过' if failed == 0 else f'{failed} 项不符合预期')
raise SystemExit(1 if failed else 0)
