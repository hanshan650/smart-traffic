# 智能交通监测与事故预警平台

> 基于 YOLOv8 + FastAPI + Vue 3 + MongoDB + Redis 的交通监控与事件预警系统
>
> **实时视频源：河南高速云视频** ｜ **路网与地图：上海市**（两者分区域展示，互不影响）
>
> 一套后端、两套前端：**警务端**（监控室大屏，长时值守）与**民众端**（手机，看一眼就走）

---

## 一、项目结构

```
smart-traffic/
├── backend/                          # FastAPI 后端
│   ├── .venv/                        # Python 3.12 虚拟环境（不入库）
│   ├── requirements.txt
│   ├── .env.example                  # 环境变量样例（脱敏模板）
│   ├── yolov8n.pt / yolov8s.pt       # 模型权重（不入库，见 README 指引）
│   ├── static/uploads/               # 抓帧产物（不入库，含隐私画面）
│   ├── scripts/                      # 标定与验证脚本（见下表）
│   └── app/
│       ├── main.py                   # 应用入口（lifespan / CORS / 静态文件）
│       ├── core/config.py            # pydantic-settings 配置
│       ├── db/
│       │   ├── mongo.py              # 连接与索引
│       │   └── redis_client.py       # 缓存 / 队列 / 限流（含离线冷却）
│       ├── models/schemas.py         # Pydantic 契约（自动 camelCase）
│       ├── realtime/ws_manager.py    # WebSocket 连接管理与广播
│       ├── services/
│       │   ├── video_source.py       # ★ 视频源适配层 + 抽帧三级降级
│       │   ├── detector.py           # YOLO 检测（优雅降级 + 类别修正）
│       │   ├── flow_service.py       # 多帧时序聚合
│       │   ├── geometry.py           # ★ 几何计算（IoU / 中心距 / 航向角）
│       │   ├── tracking.py           # ★ 轨迹管理 + IoU 跟踪
│       │   ├── accident_detector.py  # ★ 事故判定 L2 规则层 + L3 时序确认层
│       │   ├── accident_service.py   # ★ 事故分析编排 + 合成场景
│       │   ├── road_network.py       # ★ 上海路网（与视频源解耦）
│       │   ├── event_service.py      # 检测记录 / 事件 / 统计
│       │   ├── officer_service.py    # 警员档案
│       │   ├── dispatch_service.py   # ★ 智能派警（四判据三策略）
│       │   ├── incident_service.py   # 警情状态机与时间线
│       │   ├── incident_reporter.py  # 警情上报适配器（mock / official 占位）
│       │   ├── scene_text.py         # ★ 场景文字识别（场所判定）
│       │   ├── stall_detector.py     # ★ 违停判定（静止时长 + 证据链）
│       │   ├── plate_recognizer.py   # 车牌可读性预检与识别通道
│       │   ├── owner_lookup.py       # 车主查询（脱敏 + 全量审计）
│       │   ├── surveillance_service.py # 巡检调度（跨轮次的时间基准）
│       │   ├── citizen_report_service.py # 民众上报状态机 + 防滥用
│       │   └── demo_data_service.py  # 演示数据（接入真实数据后自动失效）
│       └── api/v1/                   # 见「四、接口一览」
│
├── frontend/                         # Vue 3 前端
│   ├── vite.config.ts                # API / WS 代理 + PWA 配置
│   ├── public/icons/                 # PWA 图标（由脚本生成，改配色重跑）
│   ├── scripts/                      # 图标生成与离线验证脚本
│   └── src/
│       ├── api/                      # axios 封装（client / api / citizen）
│       ├── stores/                   # Pinia（system / video / event）
│       ├── composables/              # useWebSocket / useAmap / useHls / useGeolocation
│       ├── utils/
│       │   ├── camera.ts             # 摄像头标签生成（长站名收敛）
│       │   ├── geo.ts                # ★ 距离 / 方位角 / WGS84→GCJ-02 转换
│       │   ├── route.ts              # ★ 行进路段判定
│       │   └── time.ts               # 服务端 UTC 时间解析（补 Z）
│       ├── layouts/
│       │   ├── MainLayout.vue        # 警务端：深色 + 左侧导航（窄屏转抽屉）
│       │   └── CitizenLayout.vue     # 民众端：亮色 + 底部 Tab + 字号阶梯
│       ├── components/
│       │   ├── VideoTile.vue / VideoLightbox.vue
│       │   ├── EventCard.vue / EvidenceBar.vue / StatCard.vue
│       │   ├── PwaPrompt.vue         # 安装引导 / 更新 / 离线提示
│       │   └── citizen/RouteMap.vue  # ★ 行进视角地图
│       └── views/
│           ├── 警务端
│           │   ├── ScreenView.vue        # 指挥大屏（1920×1080 等比缩放）
│           │   ├── DashboardView.vue     # 运行概览
│           │   ├── VideoWallView.vue     # 实时监控（视频墙）
│           │   ├── MapView.vue           # 地图态势
│           │   ├── AccidentView.vue      # ★ 事故识别演示台（证据链可视化）
│           │   ├── EventsView.vue        # 告警中心
│           │   ├── DispatchView.vue      # 警情调度
│           │   ├── OfficersView.vue      # 警员管理
│           │   ├── SurveillanceView.vue  # 违停监控
│           │   ├── ReportsView.vue       # 民众上报复核
│           │   ├── RoadsView.vue         # 上海路网
│           │   └── SystemView.vue        # 系统状态
│           └── citizen/                  # 民众端
│               ├── CitizenHomeView.vue   # ★ 行进视角（地图为主）
│               ├── CitizenReportView.vue # 众包上报
│               ├── CitizenTrackView.vue  # 上报进度查询
│               ├── CitizenAdviceView.vue # 出行建议（非导航）
│               └── CitizenHelpView.vue   # 应急电话与安全须知
│
├── docs/
│   ├── 开题报告.md
│   ├── 视频源说明.md                  # 各地监控源可用性调研
│   ├── 模型选型实验.md                # ★ 检测参数标定与模型选型实测报告
│   └── 车牌识别可行性.md              # ★ 为什么当前分辨率下车牌读不出来
│
├── start-backend.bat
└── start-frontend.bat
```

### 验证脚本

关键模块都有**可离线复现**的验证脚本，改动后应重跑：

| 脚本 | 覆盖 |
|------|------|
| `verify_algorithm.py` | 事故算法 4 个合成场景 |
| `verify_temporal.py` | 时序聚合收益 |
| `verify_dispatch.py` | 派警 4 场景 + 断言 Σ贡献 == 总分 |
| `verify_timeline.py` | 警情时间线一致性 |
| `verify_surveillance.py` | 违停判定 26 项 |
| `verify_citizen.py` | 民众端数据隔离 31 项 |
| `verify_flow.py` | 多帧流量 |
| `verify_demo_fallback.py` | 演示数据失效机制 16 项 |
| `probe_redis_timing.py` | Redis 各原语耗时（确认无阻塞） |
| `probe_resolution.py` / `probe_plate_pixels.py` | 上游分辨率与车牌可读性实测 |
| `compare_detectors.py` / `compare_yolo_detr.py` | 模型与推理尺寸对比 |

前端：`npm run verify:route`（地理与路段判定 21 项）、`npm run verify:pwa`（构建产物的缓存规则）。

---

## 二、快速开始

### 前置依赖

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12 | 虚拟环境已创建于 `backend/.venv` |
| Node.js | 18+ | 前端构建 |
| MongoDB | 4.0+ | 必需，检测记录与事件持久化 |
| Redis | 6.0+ | 可选，未启动时自动降级 |
| ffmpeg | 任意 | 可选；未安装时自动降级为 HLS 分片离线解码 |
| adb | 任意 | 可选；真机调试用（见 3.11） |

### 1. 启动后端

```powershell
cd smart-traffic\backend
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env       # 首次运行
python -m uvicorn app.main:app --reload --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>

### 2. 启动前端

```powershell
cd smart-traffic\frontend
npm install                        # 首次运行
npm run dev
```

| 入口 | 地址 |
|------|------|
| 警务端 | <http://localhost:5173/dashboard> |
| 民众端 | <http://localhost:5173/citizen> |
| 指挥大屏 | <http://localhost:5173/screen> |

---

## 三、核心设计

### 3.1 视频源适配层

视频源通过 Provider 模式抽象，业务层与前端不感知具体供应商：

| key | 名称 | 状态 |
|-----|------|------|
| `henan` | 河南高速云视频 | ✅ 可用（默认） |
| `shanghai` | 上海城市道路监控 | ❌ 仅保留接入位 |

切换方式：修改 `backend/.env` 的 `VIDEO_SOURCE`。

**上海视频源为保留接入位**：上海交警与市交通委未开放公开监控视频接口，
调用其取流接口会返回 HTTP 503 + 明确的 `hint`，前端据此展示提示而非崩溃。

详见 [`docs/视频源说明.md`](docs/视频源说明.md)。

### 3.2 上海路网与视频源解耦

```
┌──────────────────────┐        ┌──────────────────────┐
│  视频源层（可切换）    │        │  地理信息层（上海）    │
│  henan   ✅          │        │  12 路口 / 13 路段    │
│  shanghai ❌          │        │  73 km 高快速路       │
└──────────────────────┘        └──────────────────────┘
            └────────────┬───────────────┘
                         ▼
              检测 / 预警 / 大屏展示
```

切换视频源**不会**影响任何上海路网、地图、地理编码或路线规划能力。

### 3.3 事故识别算法（三级级联）

```
L1 轨迹层     tracking.py        多帧关联，输出带速度/航向的轨迹
     ↓
L2 规则层     accident_detector.py 加权判据，得分过阈值才进 L3
     ↓
L3 时序层     accident_detector.py 连续多帧一致才确认，抑制单帧抖动
```

L2 判据权重：**接触 0.35 / 速度骤降 0.35 / TTC 0.20 / 轨迹畸变 0.10**，
`score_threshold = 0.55`。

**硬性前提收紧为「速度骤降必须 > 0」**：真实高速流上路侧视角下，
前后车 bbox 在图像上经常重叠（透视压缩），检测框也会因遮挡抖动 ——
仅凭"接触"会大量误报。收紧后红灯场景 L2 候选从 111 降到 60，整体无误报。

**误报抑制**：多车同步减速（红灯）、双方近静止（违停）、轨迹年龄不足、
行人折减 ×0.6。被抑制的候选**保留**在结果里并标 `suppressed=True`，
供演示台解释"为什么没报"。

`AccidentView.vue` 是算法演示台：四个合成场景可直接跑，并把每个判据的
**原始值 / 归一化得分 / 权重 / 贡献**逐条列出 —— 结论可解释，不是黑箱。

### 3.4 人机协同（AI 建议 + 一键确认）

```
AI 检出 → 事件落库(PENDING) → 弹窗置顶 → 值班员处置
                                        ├─ 确认 → CONFIRMED + 广播
                                        └─ 误报 → REJECTED + 回写负样本池
```

- 大屏与后台均支持 **空格键确认 / Esc 忽略**
- 误报自动标记 `hard_negative`，为后续主动学习提供样本
- 同一 `(事件类型, 摄像头)` 组合 60 秒内限流，避免告警风暴

### 3.5 优雅降级

| 依赖缺失 | 影响 | 处理 |
|----------|------|------|
| MongoDB 未启动 | 检测记录、事件不可用 | 检测结果仍正常返回，健康检查标 `degraded` |
| Redis 未启动 | 缓存与限流失效 | **预检后立即跳过**，不再阻塞（见下） |
| ultralytics 未安装 | 检测与实时分析不可用 | 返回 503 + 安装指引，其余功能正常 |
| 缺少 `yolov8n.pt` 权重 | 同上 | 提示下载地址；**权重需放在 `backend/` 目录** |
| ffmpeg 未安装 | — | 三级降级：ffmpeg → OpenCV 直连 → HLS 分片离线解码 |
| 高德 Key 未配置 | 地图空白 | 提示配置位置，其余功能正常 |
| 浏览器不支持定位 | 无法判断"我在哪" | 引导手动选择路段，路况照常可看 |
| 车牌引擎缺失 | 无法识别车牌 | 返回几何估算依据，不伪造数据 |

**Redis 未启动时不能让它拖慢接口**。实测发现 redis-py 对 `localhost`
会先试 IPv6 再回退 IPv4，叠加内部连接尝试，`socket_connect_timeout`
兜不住 —— 单次 `set_json` / `push_queue` 阻塞 **36~37 秒**，
导致复核等接口客户端超时（数据其实已写库）。

修法是把预检下沉到 `get_redis()` 本身，并加 30 秒离线冷却：
不可用时抛 `RedisUnavailable`（`RedisError` 子类，调用方已有的
`except RedisError` 降级写法无需改动）；冷却期内直接返回，
不再逐次付探活代价。修复后第一次 2 秒，之后**全部 0 ms**。

### 3.6 检测参数与模型选型

检测链路的参数经**实测标定**而非沿用默认值，并且排除了几个常见的优化方向。

| 参数 | 取值 | 依据 |
|------|------|------|
| 推理尺寸 | `imgsz=960` | 上游画面仅 352×288，默认 640 会大量漏检。同一帧同一模型：640 检出 1 个目标，960 检出 7 个 |
| 模型 | `yolov8s` | `yolov8n` 与 `s` 在 960 下检出完全相同，取 `s` 保留精度余量 |
| 置信度 | `0.25` | 再低会引入 `bench`/`boat` 等无关类别误检 |
| 类别修正 | `train → truck` | 实测发现高速货车被系统性误判为火车，直接丢弃会使车辆数偏低 |
| 时基 | 运行时探测帧率 | 上游为 25 fps；若按固定 10 fps 换算，速度会低估 2.5 倍 |

**经实测排除的方向**（避免重复投入）：

- **切片推理**（SAHI 类）—— 为 4000×3000 级大图设计，把 352×288 切块后
  目标像素数不增加，无收益
- **继续加大 `imgsz`** —— 1280 相比 960 检出完全相同，仅耗时翻倍
- **继续放大模型** —— `n`≈`s`；且更大的 RT-DETR（r50vd）反而比 r18vd 少检出一半
- **YOLO 换 RT-DETR** —— 同批样本检出打平（8 vs 8），且两者的小目标检出
  都是 0，说明瓶颈在输入分辨率而非检测器架构
- **超分辨率预处理** —— 无法恢复编码器已丢弃的细节

**有效方向**：时序聚合（实测召回 +33%）、更换更高分辨率视频源、微调模型。

> 注意：画面里的「1920×1080-交投许昌」是**摄像头 OSD 水印**，不是真实分辨率。
> 实测上游下发分片就是 **352×288**，平台侧做了转码降质，改代码无法提升。

完整实验设计、原始数据与复现命令见
**[`docs/模型选型实验.md`](docs/模型选型实验.md)**。

### 3.7 警务侧：警员 / 警情 / 智能派警

**警号 `officer_id` 是业务主键**，数据库 `_id` 仅存储用；API 路径里的 id 即警号。

**不建地理索引**：警员仅百人量级，坐标存独立 lat/lng 字段，应用层用
haversine 算距离，比维护 2dsphere 更直观。

派警按**四判据**加权（`rank_candidates` 是**纯函数**，不访问数据库，
所以离线脚本能直接喂合成警员）：

| 判据 | 说明 |
|------|------|
| 距离 | 线性衰减 `1 - d/80km` |
| 技能 | 与事件类型所需技能的命中比 |
| 负载 | `1 / (1 + 在办数)` |
| 勤务状态 | 在岗 1.0 / 出警中 0.5 |

三种策略（`NEAREST` / `BALANCED` / `SKILL`）只是改权重组合。
被排除的候选（休假、离岗）**仍出现在结果里**并附 `exclude_reason` ——
"为什么没派他"必须能回答。

**上报失败不阻断派警**：外部平台不可用只把 `report_status` 置 FAILED，
警情仍可派警，接口返回 200（警情已创建）。无警力可派返回 **409**
（业务状态，不是服务器错误）。

### 3.8 持续检测与违停识别

对多路摄像头**轮次巡检**，判断"车辆在非可停车地点静止超过 30 秒"。

**时间基准是本模块的头号坑**：巡检分批抓帧，若用「帧号 ÷ 帧率」推算时间，
轮次之间的十几秒空档会被完全抹掉（40 帧算出 4 秒而非 64 秒），
30 秒阈值永远达不到。必须用 `time.monotonic()` 的真实时间。

**场所豁免建立在确凿证据上**：默认按**禁停**处理，只有点位名称命中
「服务区 / 收费站 / 检查站 / 停车场」等关键词才豁免。OCR 通道是兜底，
不可用时退回关键词而非放弃判定。

**拥堵抑制**：统计"同步静止车辆数"时**必须加 ≥2 秒的下界** ——
位移判定只要两帧位移小于阈值就返回正时长（约几十毫秒），
不加下界会把所有慢速行驶的车算成静止，使拥堵抑制误触发、真违停被压掉。

**车牌识别当前不可行**：352×288 下车牌字符高 0.8 px（近车 6.1 px），
OCR 可读下限约 16 px。因此实现了**几何预检**，读不出时返回估算依据
而不调引擎。推导过程与部署要求（车辆需占画面宽 ≥23.4%）见
[`docs/车牌识别可行性.md`](docs/车牌识别可行性.md)。

**车主查询把合规要求写进代码结构**：强制要求 `operator` 与 `reason`、
全量审计留痕、输出脱敏、不伪造数据。

### 3.9 民众端与隐私边界

**数据隔离做在路由层**（`/public/*`），不依赖前端"记得别显示"。
公开事件用**白名单式构造** —— 逐个挑出可公开字段，
内部模型新增字段时**默认不会被带出去**。

对外**不返回**：

| 字段 | 理由 |
|------|------|
| `camera_id` | 可精确关联到具体摄像头，属基础设施信息 |
| `snapshot_url` | AI 快照含真实道路画面，可能带车牌与行人面部 |
| `confidence` | 算法内部指标，容易被误读为"可信度" |
| `reviewed_by` | 值班员姓名，属内部人员信息 |
| 数据库 `id` | 避免接口变成可遍历的入口 |

坐标统一降精度到 3 位小数（约 100 m）—— 够表达"附近哪里堵了"，
又不足以定位到具体车道。

**民众上报不直接生成告警**，进待复核队列，警务端确认后才转正式事件。
上报类型**刻意不复用内部 `EventType`**：一个按算法可检测性划分，
一个要民众看得懂，混用会让内部数据混入无法参与算法的事件。

> 教训：**字段名干净不代表值干净**。曾在事件描述里拼给值班员的提示语
> 「（联系方式见上报记录）」，而事件描述会进公开接口 —— 跟着泄露了。
> 验证脚本因此同时检查**禁止字段名**与**禁止短语**。

### 3.10 行进视角地图

民众端首页以地图为主体，回答「**我在哪、前面有什么**」。

**坐标系必须转换**。`navigator.geolocation` 返回 **WGS84**，高德用
**GCJ-02**，实测许昌一带相差 **568 米**（北京 555 / 上海 481 / 广州 621）——
不转换会把"我在的主路"判到隔壁街区。转换算法内置在 `utils/geo.ts`，
不依赖高德的 `convertFrom`（异步回调，在持续定位下不好用）。

**同一条高速 ≠ 同一段路**。京港澳高速许昌段与信阳段相距 **211 公里**，
路名相同。若不设上限，用户会被告知"前方 2 个事件"，而其中一个在
200 公里外 —— 这在出行决策上是误导。因此设 `AHEAD_MAX_KM = 50`，
超出者单列"同路段更远处"。

**距离只能写"约"**。没有路网几何就算不出沿路里程，界面上不出现
"前方 300 米"这种像导航里程的措辞。

**没有定位是常态**（用户不授权、非 HTTPS、室内无信号）。这条路径必须
仍能看全部事件，且**不出现任何距离文案**。

| 场景 | 行为 |
|------|------|
| 紧邻某路段 | 自动判定当前路段，按距离列出前方事件 |
| 距所有路段 >30 km | **不猜**，引导手动选择路段 |
| 无定位 | 显示全部事件，隐藏距离 |
| 静止（`speed ≈ 0`） | 忽略 `heading`，两侧事件都算前方 |

> 静止时要忽略 `heading`：很多手机静止时报 0（磁力计朝向）而非 null，
> 会把正南方的事件判成"已在身后"，前方事件凭空少一半。

### 3.11 移动端与 PWA

**民众端可安装到桌面**，弱网时能看最近一次路况。Service Worker 的
**缓存边界是核心取舍**：

- **只有 `/api/v1/public/*` 做运行时缓存**（NetworkFirst，10 分钟过期）
- **警务端接口一律不缓存** —— 警情、警员、车主信息属敏感数据，
  且过期的派警状态比"加载失败"更危险（值班员会以为警情还没派出去）
- `navigateFallbackDenylist` 排除 `/api/` 与 `/static/`，否则接口 404
  会返回一份 HTML 让前端去 `JSON.parse`

**手机端是 App 式布局**：外壳锁定一屏高、整页不滚动，纵向空间全给地图
（地图占视口 **87%**）。顶部品牌条与页脚在窄屏隐藏 —— 底部 Tab 已承担
导航，而页脚被 Tab 完全盖住却仍在占位。

**尺寸随视口连续缩放**（`clamp()` + `vw`），不用断点跳档：跨过边界时
整页会突然跳一截，而中间尺寸（平板竖屏、分屏窗口）只能二选一。
只保留两类离散断点：布局模式切换、`prefers-reduced-motion`。

**iOS 输入框字号不得低于 16px** —— 否则聚焦时会自动放大整个页面且
不会缩回。用 `--fs-input` 单独管控，不跟着正文阶梯走。

#### 真机调试

手机通过局域网 HTTP 访问时**定位不可用**（Geolocation 只在安全上下文
开放）。要让真机跑完整效果，用 **USB 端口转发**：

```powershell
# 手机开「开发者选项 → USB 调试」（ColorOS/vivo 系还需开「USB 调试（安全设置）」）
adb reverse tcp:5173 tcp:5173
```

然后手机浏览器打开 `http://127.0.0.1:5173/citizen` —— 走 `localhost`
即为安全上下文，定位与 PWA 安装都可用。

> **方向极易搞反**：`adb forward` 是「电脑 → 手机」，`adb reverse` 才是
> 「手机 → 电脑」。写成 `forward` 时手机报 `ERR_CONNECTION_REFUSED`，
> 而 `adb forward --list` 仍显示规则存在。
>
> 拔线后转发失效，需重新执行。

---

## 四、接口一览

共 **73 个接口**，分 12 组。完整定义见 `/docs`（Swagger UI）。

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/stats` | 统计概览 |
| GET | `/api/v1/config` | 前端运行时配置 |
| WS | `/api/v1/ws` | 实时推送（见下节） |

### 视频源

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/video/sources` | 列出视频源 |
| GET | `/api/v1/video/cameras` | 检索摄像头 |
| GET | `/api/v1/video/stream-url` | 获取直播流地址 |
| GET | `/api/v1/video/hls/{cam}/playlist.m3u8` | HLS 入口播放列表 |
| GET | `/api/v1/video/hls/{cam}/proxy` | HLS 分片代理（带 Referer） |

### 检测

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/detection/model` | 模型状态 |
| POST | `/api/v1/detection/image` | 上传图片检测 |
| POST | `/api/v1/detection/snapshot` | 直播抓帧检测 |
| POST | `/api/v1/detection/flow` | 多帧流量分析（时序聚合） |
| GET | `/api/v1/detection/history` | 检测历史 |
| GET | `/api/v1/detection/thresholds` | 拥堵判定阈值 |

### 事故识别

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/accident/algorithm` | 算法说明 |
| GET | `/api/v1/accident/scenarios` | 仿真场景列表 |
| POST | `/api/v1/accident/simulate` | 合成轨迹仿真 |
| POST | `/api/v1/accident/analyze` | 实时片段事故分析 |
| GET | `/api/v1/accident/config` | 算法参数 |

### 事件

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/events` | 事件列表 |
| GET | `/api/v1/events/stats` | 事件统计 |
| GET | `/api/v1/events/rank` | 拥堵排行 |
| GET | `/api/v1/events/{id}` | 事件详情 |
| POST | `/api/v1/events/{id}/review` | 事件复核 |

### 上海路网

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/roads/network` | 路网拓扑 |
| GET | `/api/v1/roads/summary` | 路网摘要 |
| GET | `/api/v1/roads/segments/{id}` | 路段详情 |
| GET | `/api/v1/roads/amap/geocode` | 高德地理编码 |
| GET | `/api/v1/roads/amap/route` | 高德驾车路线规划 |

### 警员

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/officers` | 警员列表 |
| POST | `/api/v1/officers` | 新增警员 |
| GET | `/api/v1/officers/stats` | 勤务概览 |
| POST | `/api/v1/officers/seed` | 导入示例警员 |
| GET | `/api/v1/officers/{id}` | 警员详情 |
| PUT | `/api/v1/officers/{id}` | 更新警员 |
| DELETE | `/api/v1/officers/{id}` | 删除警员 |

### 警情调度

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/incidents` | 警情列表 |
| POST | `/api/v1/incidents` | 创建警情 |
| GET | `/api/v1/incidents/stats` | 警情统计 |
| GET | `/api/v1/incidents/algorithm` | 派警算法与适配器说明 |
| GET | `/api/v1/incidents/{id}` | 警情详情 |
| POST | `/api/v1/incidents/{id}/dispatch` | 智能派警 |
| POST | `/api/v1/incidents/{id}/status` | 处置状态流转 |
| POST | `/api/v1/incidents/{id}/report` | 上报外部平台 |
| POST | `/api/v1/incidents/{id}/retry` | 重试上报 |

### 持续检测（违停监控）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/surveillance/status` | 巡检状态 |
| POST | `/api/v1/surveillance/start` | 开始巡检 |
| POST | `/api/v1/surveillance/stop` | 停止巡检 |
| POST | `/api/v1/surveillance/cameras/{id}/toggle` | 暂停/恢复单路 |
| GET | `/api/v1/surveillance/alerts` | 告警列表 |
| GET | `/api/v1/surveillance/alerts/stats` | 告警概览 |
| GET | `/api/v1/surveillance/alerts/{id}` | 告警详情 |
| POST | `/api/v1/surveillance/alerts/{id}/status` | 告警处置 |
| GET | `/api/v1/surveillance/algorithm` | 算法与通道状态 |
| POST | `/api/v1/surveillance/plate/estimate` | 车牌可读性估算 |
| POST | `/api/v1/surveillance/owner-lookup` | 车主信息查询 |
| GET | `/api/v1/surveillance/audits` | 查询审计记录 |

### 民众上报复核（警务端）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/reports` | 复核队列 |
| GET | `/api/v1/reports/stats` | 上报概览 |
| GET | `/api/v1/reports/{no}` | 上报详情 |
| POST | `/api/v1/reports/{no}/review` | 复核上报 |

### 民众端（公开）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/public/traffic` | 实时路况 |
| GET | `/api/v1/public/traffic/rank` | 拥堵路段排行 |
| GET | `/api/v1/public/overview` | 路况概览 |
| GET | `/api/v1/public/advice` | 出行建议（**非导航**） |
| GET | `/api/v1/public/options` | 上报选项（自描述） |
| POST | `/api/v1/public/reports` | 提交上报 |
| GET | `/api/v1/public/reports/{no}` | 查询上报进度 |
| GET | `/api/v1/public/emergency` | 应急电话与安全须知 |

### 演示数据（测试接口）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/demo/status` | 演示数据状态与原因 |
| GET | `/api/v1/demo/preview` | 预览内容（不写库） |
| POST | `/api/v1/demo/seed` | 注入演示数据（幂等） |
| DELETE | `/api/v1/demo/clear` | 清除演示数据 |

---

## 五、WebSocket 频道

统一信封：`{ channel, ts, payload }`，地址 `/api/v1/ws`。

| channel | 说明 |
|---------|------|
| `event:new` | 新事件产生（触发前端告警弹窗） |
| `event:update` | 事件状态变更（确认 / 误报） |
| `snapshot` | 检测快照更新 |
| `health` | 系统健康状态 |
| `pong` | 心跳应答 |

---

## 六、演示数据

真实数据源未接入时，"行进视角"的核心内容（当前路段、前方多少公里）
看不到效果。为此提供一批演示数据，但**绝不能混进真实数据流**。

- 演示数据放**独立集合** `demo_citizen_events`，**不写 `events`**
- 只有当 `events` 集合**一条真实事件都没有**时，`/public/*` 才把它
  合并进来。判据是"有没有真实数据"这个**客观事实**，而不是某个需要
  人去关的开关 —— 开关会忘
- 响应体带 `demo` 字段，前端据此显示橙色通栏「演示数据 · 非真实路况」。
  编的坐标不能和真实路况长得一样
- **不随启动自动注入**，必须显式调 `/demo/seed`

坐标取自上海长宁区延安西路 / 虹桥路一带的真实道路，演示时位置与路名
对得上；时间按"距当前 N 分钟"生成，避免写死日期导致一眼假。

```powershell
# 查看状态（会说明为什么生效或为什么失效）
curl http://127.0.0.1:8000/api/v1/demo/status

# 注入
curl -X POST http://127.0.0.1:8000/api/v1/demo/seed
```

配置项 `CITIZEN_DEMO_ENABLED`（默认 `true`，生产部署应设 `false`）。

早期为地图演示造的数据走的是真实流程（上报 → 复核），落在 `events` 里，
会让演示数据失效。清理：

```powershell
# 默认只预览，加 --yes 才真删
.venv\Scripts\python.exe scripts\clear_seeded_events.py
```

---

## 七、待办

- [ ] 接入更高分辨率视频源，或对摄像头做变焦 / 近景部署（车牌识别的**前置条件**）
- [ ] 训练针对高速场景的检测模型（当前 COCO 预训练模型有系统性误判）
- [ ] 上海视频源接入位等待官方接口开放
- [ ] 违停巡检的端到端验证需要上游可用（当前受 IP 级限流影响）
- [ ] 同路段状态线（按路名连线）需真实拥堵快照数据才能验证

---

## 八、合规声明

- 本平台为**毕业设计作品**，不用于生产环境
- 视频源来自公开的交通监控平台，**不存储原始视频**，仅在本地缓存抓帧结果
- 抓帧产物可能包含车牌与行人面部，仅用于算法验证，**不对外提供**
- 车主信息查询功能**仅保留接口**，需通过政务专网接入，且强制留痕审计
- 应急电话为真实号码，安全须知参考公开的交管资料
- 民众端展示的路况信息**仅供参考**，出行请以交管部门发布为准
