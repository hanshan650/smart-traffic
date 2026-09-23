"""
应用配置
========
基于 ``pydantic-settings``，优先级：环境变量 > ``.env`` 文件 > 代码默认值。

``.env`` 使用绝对路径定位，因此无论从哪个工作目录启动服务都能正确加载。
"""
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（app/core/config.py → parents[2]）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全部可通过环境变量或 .env 覆盖，变量名大小写不敏感。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / '.env'),
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    # ------------------------------------------------------------------
    # 应用
    # ------------------------------------------------------------------
    app_name: str = 'smart-traffic'
    app_title: str = '智能交通监测与事故预警平台'
    debug: bool = True
    api_prefix: str = '/api/v1'

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------
    mongo_uri: str = 'mongodb://localhost:27017'
    mongo_db_name: str = 'smart_traffic'
    mongo_timeout_ms: int = 3000

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_host: str = 'localhost'
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    redis_key_prefix: str = 'traffic:'
    # 交通快照缓存 TTL（秒）
    cache_ttl_snapshot: int = 5
    cache_ttl_road_heatmap: int = 60
    # 同一路段告警限流窗口（秒）
    alert_throttle_seconds: int = 60

    # ------------------------------------------------------------------
    # 视频源
    # ------------------------------------------------------------------
    # henan = 河南高速云视频（可用）；shanghai = 上海（仅保留接入位）
    video_source: str = 'henan'

    hngs_api_base: str = 'https://weixin.hngscloud.com'
    hngs_referer: str = 'https://weixin.hngscloud.com/'
    hngs_user_agent: str = 'Mozilla/5.0'
    hngs_default_camera: str = '1324629868805103618'
    hngs_request_timeout: int = 10
    hngs_video_type: str = '2'
    hngs_video_rate: str = '0'
    # 摄像头检索视野（河南全省）
    hngs_viewport_ne: str = '116.5,36.5'
    hngs_viewport_sw: str = '110.5,31.0'
    hngs_viewport_zoom: str = '8'

    # ffmpeg 抽帧
    ffmpeg_path: str = ''
    frame_capture_timeout: int = 20
    frame_min_bytes: int = 1024

    # ------------------------------------------------------------------
    # YOLO
    # ------------------------------------------------------------------
    yolo_model_path: str = 'yolov8n.pt'
    yolo_confidence: float = 0.3
    # 推理输入边长。这是低分辨率视频源的**关键参数**：
    # 上游监控画面常见 352x288，而远处车辆在其中只占几十个像素，
    # 直接用默认的 640 推理会大量漏检 —— LetterBox 放大倍率不足，
    # 目标在特征图上的尺寸仍远小于模型的训练分布。
    # 实测同一帧同一模型：640 检出 1 个目标，960 检出 7 个。
    # 代价是单帧耗时约 130ms -> 200ms，对秒级检测完全可接受。
    yolo_image_size: int = 960
    # 模型文件搜索目录（相对 backend/，用于定位 yolov8n.pt）
    yolo_model_dirs: str = '.,../traffic_monitor'

    # ------------------------------------------------------------------
    # 拥堵阈值（单帧车辆数）
    # ------------------------------------------------------------------
    traffic_light_threshold: int = 15
    traffic_moderate_threshold: int = 30
    traffic_heavy_threshold: int = 50

    # ------------------------------------------------------------------
    # 跨域
    # ------------------------------------------------------------------
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'

    # ------------------------------------------------------------------
    # 上海路网 / 高德
    # ------------------------------------------------------------------
    amap_js_key: str = ''
    amap_web_key: str = ''
    # JS API 2.0 强制要求的安全密钥。未配置时地图会加载失败
    # 并报 INVALID_USER_SCODE（即使 JS Key 本身正确）。
    # 在高德控制台「应用管理 → 我的应用」中与 Key 一同生成。
    amap_js_security_code: str = ''
    shanghai_center_lng: float = 121.4737
    shanghai_center_lat: float = 31.2304

    # ------------------------------------------------------------------
    # 警情上报
    # ------------------------------------------------------------------
    # 上报适配器：mock = 模拟交警平台（默认，可跑通全流程）
    #              shanghai = 上海交警平台（仅保留接入位，无公开接口）
    report_reporter: str = 'mock'
    # 模拟适配器的人为失败率，用于验证失败重试与提示。0 = 不失败
    report_mock_failure_rate: float = 0.0

    # ------------------------------------------------------------------
    # 上传
    # ------------------------------------------------------------------
    upload_dir: str = 'static/uploads'
    max_upload_mb: int = 100

    # ------------------------------------------------------------------
    # 持续检测（违停监控）
    # ------------------------------------------------------------------
    # 服务启动时是否自动开始巡检。默认关闭 —— 巡检会持续占用 CPU，
    # 应当由使用者显式开启，而不是启动服务就默默跑起来。
    surveillance_enabled: bool = False
    # 巡检摄像头列表（逗号分隔）。留空则使用当前视频源的默认摄像头。
    surveillance_cameras: str = ''
    # 轮次间隔（秒）。一轮 = 连续抓 N 帧分析 + 分析。
    # 不宜过小：单帧推理约 200ms，间隔太小会让 CPU 持续饱和。
    surveillance_interval: int = 15
    # 每轮抓取的帧数。帧数越多跟踪越稳，但一轮耗时越长。
    surveillance_frame_count: int = 20
    # 单轮超时（秒），超时后跳过本轮而不阻塞后续轮次
    surveillance_round_timeout: int = 120

    # 静止判定阈值（秒）。需求给定：非可停车地点静止超过 30 秒即告警
    stall_seconds: float = 30.0
    # 静止判定的位移阈值（归一化坐标）。约 3.5 像素，略高于检测框抖动
    stall_displacement: float = 0.01
    # 同步静止占比达到该值即判为拥堵排队（抑制违停告警）
    stall_crowd_ratio: float = 0.6
    # 判定为异常所需的最低综合得分
    stall_score_threshold: float = 0.60
    # 统计"同步静止车辆数"时的最短静止时长（秒）。
    # 不能设为 0：只要两帧间位移小于阈值就会得到 >0 的时长，
    # 设 0 会把所有慢速行驶的车都算成静止，使拥堵抑制误触发。
    stall_count_min_stationary: float = 2.0
    # 同一目标重复告警的冷却时间（秒）
    stall_alert_cooldown: int = 300
    # 观测新鲜度上限（秒）。超过该时长无新观测的轨迹不再参与判定
    stall_observation_gap: float = 45.0

    # 场景文字（可停车场所）识别通道：keyword（默认，匹配点位名称）| ocr
    surveillance_text: str = 'keyword'
    # 车牌识别通道：none（默认，未接入）| mock（演示）| official（正式）
    surveillance_plate_engine: str = 'none'
    # 车主信息查询通道：none（默认，未接入）| mock（演示）| official（正式）
    surveillance_owner_provider: str = 'none'

    # ------------------------------------------------------------------
    # 民众端（公开接口）
    # ------------------------------------------------------------------
    # 民众上报的频率上限（每条 client_key）。开放入口必然会被滥用，
    # 但限制也不能太紧 —— 真实事故发生时民众可能多人同时上报，
    # 卡死上报通道比收到几条重复信息更糟。
    citizen_report_rate_limit: int = 10
    # 频率统计的滑动窗口（小时）
    citizen_report_window_hours: int = 1
    # 描述与联系方式的长度上限
    citizen_report_max_description: int = 500
    citizen_report_max_contact: int = 100
    # 民众上报转为正式事件时的置信度。
    # 低于 AI 识别（通常 0.8+）—— 因为它经过人工核实但缺少算法证据链，
    # 保留这个差异可以让下游区分事件来源。
    citizen_report_confidence: float = 0.7
    # 路况公开接口一次最多返回的事件数
    public_traffic_limit: int = 100

    # 演示数据开关。
    #
    # 为一件事服务：**没接上真实数据源时也能看到"行进视角"的完整效果**。
    # 数据存在独立集合（demo_citizen_events），不写进 events。
    #
    # 注意它只是"允许提供"，不等于"一定提供" —— 真正的判据是
    # "events 集合里有没有真实事件"（见 demo_data_service.should_serve）。
    # 有了真实数据就自动失效，不需要谁记得回来关这个开关。
    #
    # 默认开启是为了方便答辩演示；生产部署应设为 false。
    citizen_demo_enabled: bool = True

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @property
    def surveillance_camera_list(self) -> List[str]:
        return [item.strip() for item in self.surveillance_cameras.split(',') if item.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return [item.strip() for item in self.cors_origins.split(',') if item.strip()]

    @property
    def yolo_search_dirs(self) -> List[Path]:
        return [
            (BASE_DIR / item.strip()).resolve()
            for item in self.yolo_model_dirs.split(',')
            if item.strip()
        ]

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
