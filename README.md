# 智能交通监测与事故预警平台

> 基于 YOLOv8 + FastAPI + Vue 3 + MongoDB + Redis 的城市道路监控与交通事件预警系统
>
> **实时视频源：河南高速云视频** ｜ **路网与地图：上海市**（两者分区域展示，互不影响）

---

## 一、项目结构

```
smart-traffic/
├── backend/                        # FastAPI 后端
│   ├── .venv/                      # Python 3.12 虚拟环境
│   ├── requirements.txt
│   ├── .env.example                # 环境变量样例
│   └── app/
│       ├── main.py                 # 应用入口（lifespan / CORS / 静态文件）
│       ├── core/
│       │   └── config.py           # pydantic-settings 配置
│       ├── db/
│       │   ├── mongo.py            # MongoDB 连接与索引
│       │   └── redis_client.py     # Redis 缓存 / 队列 / 限流
│       ├── models/
│       │   └── schemas.py          # Pydantic 数据契约（自动 camelCase）
│       ├── services/
│       │   ├── video_source.py     # ★ 视频源适配层（河南 / 上海）
│       │   ├── detector.py         # YOLO 检测（优雅降级）
│       │   ├── road_network.py     # ★ 上海路网（与视频源解耦）
│       │   └── event_service.py    # 检测记录 / 事件 / 统计
│       ├── realtime/
│       │   └── ws_manager.py       # WebSocket 连接管理
│       └── api/v1/
│           ├── system.py           # 健康 / 统计 / 配置 / WS
│           ├── video.py            # 视频源接口
│           ├── detection.py        # 检测接口
│           ├── events.py           # 事件与复核
│           └── roads.py            # 上海路网 + 高德代理
│
├── frontend/                       # Vue 3 前端
│   ├── vite.config.ts              # 含 API / WS 代理
│   └── src/
│       ├── api/                    # axios 封装 + 端点
│       ├── stores/                 # Pinia（system / video / event）
│       ├── composables/            # useWebSocket / useAmap
│       ├── components/             # StatCard / VideoTile / EventCard
│       ├── layouts/MainLayout.vue  # 后台布局 + 告警弹窗
│       └── views/
│           ├── ScreenView.vue      # 指挥大屏（1920×1080 等比缩放）
│           ├── DashboardView.vue   # 运行概览
│           ├── VideoWallView.vue   # 实时监控（视频墙）
│           ├── MapView.vue         # 地图态势
│           ├── EventsView.vue      # 告警中心
│           ├── RoadsView.vue       # 上海路网
│           └── SystemView.vue      # 系统状态
│
└── docs/
    ├── 开题报告.md
    └── 视频源说明.md
```

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

访问：<http://localhost:5173>
指挥大屏：<http://localhost:5173/screen>

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

详见 [`docs/视频源说明.md`](../docs/视频源说明.md)。

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

### 3.3 人机协同（AI 建议 + 一键确认）

```
AI 检出 → 事件落库(PENDING) → 弹窗置顶 → 值班员处置
                                        ├─ 确认 → CONFIRMED + 广播
                                        └─ 误报 → REJECTED + 回写负样本池
```

- 大屏与后台均支持 **空格键确认 / Esc 忽略**
- 误报自动标记 `hard_negative`，为后续主动学习提供样本
- 同一 `(事件类型, 摄像头)` 组合 60 秒内限流，避免告警风暴

### 3.4 优雅降级

| 依赖缺失 | 影响 | 处理 |
|----------|------|------|
| MongoDB 未启动 | 检测记录、事件不可用 | 检测结果仍正常返回，健康检查标 `degraded` |
| Redis 未启动 | 缓存与限流失效 | 缓存静默跳过，限流**放行**（不吞真实告警） |
| ultralytics 未安装 | 检测与实时分析不可用 | 返回 503 + 安装指引，其余功能正常 |
| 缺少 `yolov8n.pt` 权重 | 同上 | 提示下载地址；**权重需放在 `backend/` 目录** |
| ffmpeg 未安装 | — | 三级降级：ffmpeg → OpenCV 直连 → HLS 分片离线解码 |
| 高德 Key 未配置 | 地图空白 | 提示配置位置，其余功能正常 |

---

## 四、接口一览

| 分组 | 路径 | 说明 |
|------|------|------|
| 系统 | `GET /api/v1/health` | 健康检查 |
| | `GET /api/v1/stats` | 统计概览 |
| | `GET /api/v1/config` | 前端运行时配置 |
| | `WS /api/v1/ws` | 实时推送 |
| 视频源 | `GET /api/v1/video/sources` | 视频源列表 |
| | `GET /api/v1/video/cameras` | 摄像头检索 |
| | `GET /api/v1/video/stream-url` | 直播流地址 |
| 检测 | `POST /api/v1/detection/image` | 上传图片检测 |
| | `POST /api/v1/detection/snapshot` | 直播抓帧检测 |
| | `GET /api/v1/detection/model` | 模型状态 |
| | `GET /api/v1/detection/history` | 检测历史 |
| 事件 | `GET /api/v1/events` | 事件列表 |
| | `POST /api/v1/events/{id}/review` | 一键确认 / 误报 |
| | `GET /api/v1/events/rank` | 拥堵排行 |
| 上海路网 | `GET /api/v1/roads/network` | 路网拓扑 |
| | `GET /api/v1/roads/summary` | 路网摘要 |
| | `GET /api/v1/roads/amap/route` | 高德路线规划 |

---

## 五、WebSocket 频道

| 频道 | 负载 | 触发时机 |
|------|------|----------|
| `event:new` | 完整事件对象 | AI 检出事件 |
| `event:update` | 完整事件对象 | 人工复核后 |
| `snapshot` | 检测快照 | 每次检测完成 |
| `health` | 连接状态 | 建立连接时 |
| `pong` | — | 心跳应答 |

---

## 六、待办（下一轮）

- [ ] **AI 事故识别算法模块** —— 三级级联（感知 / 规则 / 时序确认）
- [ ] 移动端（Vue 3 + Vant + PWA）
- [ ] 警情上报适配器（Mock 交警接口）
- [ ] 智能派警算法（ETA + 多因子打分）
- [ ] 检测记录的可视化统计页（ECharts）

---

## 七、合规声明

视频数据来源严格限定于上游平台**主动公开**的接口，或自购设备与公开授权数据集。

**严禁**扫描、破解、爬取任何未授权摄像头。依据《中华人民共和国刑法》第 285/286 条、
《公共安全视频图像信息系统管理条例》（2025-04-01 施行）、《个人信息保护法》《数据安全法》。

上游接口的服务条款与调用频率限制由使用方自行确认并遵守。
