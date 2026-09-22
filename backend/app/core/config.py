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
    # 上传
    # ------------------------------------------------------------------
    upload_dir: str = 'static/uploads'
    max_upload_mb: int = 100

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
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
