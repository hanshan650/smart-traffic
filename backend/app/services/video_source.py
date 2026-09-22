"""
视频源适配层
============
统一抽象不同来源的道路监控视频接入，使 API 层与前端不感知具体供应商。

职责边界
--------
只负责「视频流的发现与获取」：

  · 视频源注册与切换
  · 摄像头列表检索
  · 直播流地址获取
  · 抽帧（供 YOLO 检测）

**不负责**：

  · 上海路网 / 地图 / 地理编码 —— 见 ``app/services/road_network.py``
    （这些能力与视频源完全解耦，切换视频源不受影响）
  · 检测业务编排 —— 见 ``app/services/detector.py``

当前视频源
----------
+--------------+--------------------------------------+----------------+
| key          | 说明                                 | 状态           |
+==============+======================================+================+
| ``henan``    | 河南高速云视频 weixin.hngscloud.com  | 可用（默认）   |
| ``shanghai`` | 上海城市道路监控                     | 仅保留接入位   |
+--------------+--------------------------------------+----------------+

扩展方式
--------
继承 :class:`VideoSourceProvider` 实现 ``is_available`` / ``list_cameras`` /
``get_stream_url``，再注册进 ``_PROVIDER_REGISTRY`` 与 ``_SETTINGS`` 即可，
API 层与前端无需改动。
"""
from __future__ import annotations

import glob
import os
import random
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests

from app.core.config import settings
from app.models.schemas import CameraInfo, VideoSourceInfo

# ==========================================================================
# 异常与结果模型
# ==========================================================================


class VideoSourceError(Exception):
    """视频源统一异常。

    携带 ``hint``（面向用户的排障提示）与 ``payload``（附带的结构化数据，
    如已取到的 m3u8 地址），使 API 层无需解析错误文本即可返回友好响应。
    """

    def __init__(
        self,
        message: str,
        hint: str = '',
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.payload = payload or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'success': False,
            'error': self.message,
            'hint': self.hint,
            **self.payload,
        }


@dataclass
class CameraQueryResult:
    cameras: List[CameraInfo] = field(default_factory=list)
    total_available: int = 0
    total_all: int = 0


# ==========================================================================
# 抽帧工具（所有 Provider 共用）
# ==========================================================================

_FFMPEG_CACHE: Optional[str] = None


def find_ffmpeg() -> Optional[str]:
    """定位 ffmpeg 可执行文件，结果进程内缓存。

    查找顺序：``FFMPEG_PATH`` 配置 → 系统 PATH → 常见安装路径 →
    项目及同级目录（**不递归扫描整个磁盘**，避免启动时长时间阻塞）。
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is not None:
        return _FFMPEG_CACHE or None

    candidates: List[str] = []

    if settings.ffmpeg_path:
        candidates.append(settings.ffmpeg_path)

    on_path = shutil.which('ffmpeg')
    if on_path:
        candidates.append(on_path)

    candidates.extend([
        os.path.expandvars(r'%ProgramFiles%\ffmpeg\bin\ffmpeg.exe'),
        os.path.expandvars(r'%USERPROFILE%\ffmpeg\bin\ffmpeg.exe'),
        r'C:\ffmpeg\bin\ffmpeg.exe',
        r'D:\ffmpeg\bin\ffmpeg.exe',
    ])

    # 仅扫描项目目录及其父目录
    from app.core.config import BASE_DIR
    for root in (BASE_DIR, BASE_DIR.parent):
        for pattern in ('ffmpeg*/**/ffmpeg.exe', '**/ffmpeg.exe'):
            try:
                candidates.extend(glob.glob(str(root / pattern), recursive=True))
            except OSError:
                pass

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            _FFMPEG_CACHE = candidate
            return candidate

    _FFMPEG_CACHE = ''
    return None


def _is_valid_image(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= settings.frame_min_bytes
    except OSError:
        return False


def _grab_frame_ffmpeg(ffmpeg: str, stream_url: str, output_path: str) -> bool:
    """用 ffmpeg 从直播流抓取一帧。

    ``-rw_timeout`` 是 **输入协议选项**，必须位于 ``-i`` 之前，
    否则会被 ffmpeg 当作输出选项而静默失效。
    """
    timeout_sec = settings.frame_capture_timeout
    cmd = [
        ffmpeg, '-y',
        '-rw_timeout', str(timeout_sec * 1_000_000),   # 微秒
        '-probesize', '2000000',
        '-analyzeduration', '2000000',
        '-i', stream_url,
        '-frames:v', '1',
        '-q:v', '2',
        '-f', 'image2',
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout_sec + 5, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return _is_valid_image(output_path)


def _grab_frame_cv2(stream_url: str, output_path: str) -> bool:
    """回退方案：OpenCV 内置 ffmpeg 后端通常也能读取 HLS。"""
    try:
        import cv2
    except ImportError:
        return False

    capture = None
    try:
        capture = cv2.VideoCapture(stream_url)
        if not capture.isOpened():
            return False
        ok, frame = capture.read()
        if not ok or frame is None:
            return False
        return bool(cv2.imwrite(output_path, frame))
    except Exception:
        return False
    finally:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass


# ==========================================================================
# 抓帧路径三：HLS 分片解码（不依赖 ffmpeg）
# --------------------------------------------------------------------------
# 动机：ffmpeg 不是任何环境都具备的（安装包约 100 MB，且需要单独部署）。
# 而 HLS 的**媒体分片本身就是完整的 ts 文件**，可以直接下载后离线解码，
# 由此得到两个好处：
#   · 无需安装 ffmpeg，降低部署门槛
#   · 下载时可携带 Referer 等自定义头，绕过上游防盗链
#     （OpenCV 的 VideoCapture 无法自定义请求头）
# 代价是只能对已下载的分片解码，可用时长受分片长度限制（通常 2~10 秒）。
# ==========================================================================


_HLS_PLAYLIST_SUFFIX = '.m3u8'

# 主播放列表变体的码率/分辨率属性
_HLS_RESOLUTION = re.compile(r'RESOLUTION=(\d+)x(\d+)')
_HLS_BANDWIDTH = re.compile(r'BANDWIDTH=(\d+)')


def _fetch_text(url: str, headers: Dict[str, str], timeout: int = 10) -> Optional[str]:
    """拉取文本资源（播放列表），失败返回 ``None``。"""
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return None


def _playlist_uris(text: str, base_url: str) -> List[str]:
    """提取播放列表中的资源 URI（已转为绝对地址）。

    HLS 播放列表中，非注释行即为 URI：可能是 ts 分片，
    也可能是指向子播放列表的 m3u8（主播放列表场景）。
    """
    uris: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        uris.append(urljoin(base_url, line))
    return uris


def _iter_variants(text: str, base_url: str) -> List[Tuple[str, int, int]]:
    """解析主播放列表中的变体流，返回 ``[(uri, 像素数, 带宽)]``。

    HLS 主播放列表用 ``#EXT-X-STREAM-INF`` 描述每个变体，
    其后的第一行非注释内容即为该变体的地址。
    """
    variants: List[Tuple[str, int, int]] = []
    pixels = 0
    bandwidth = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith('#EXT-X-STREAM-INF'):
            pixels = 0
            bandwidth = 0
            match = _HLS_RESOLUTION.search(line)
            if match:
                pixels = int(match.group(1)) * int(match.group(2))
            match = _HLS_BANDWIDTH.search(line)
            if match:
                bandwidth = int(match.group(1))
            continue

        if line.startswith('#'):
            continue

        if pixels or bandwidth:
            variants.append((urljoin(base_url, line), pixels, bandwidth))
            pixels = 0
            bandwidth = 0

    return variants


def _resolve_media_playlist(
    stream_url: str,
    headers: Dict[str, str],
) -> Optional[Tuple[str, List[str]]]:
    """向下钻取到真正的**媒体播放列表**，返回 ``(地址, 分片列表)``。

    直播流常见两级结构：主播放列表（仅有 ``#EXT-X-STREAM-INF``，
    指向不同码率的子播放列表）→ 媒体播放列表（列出 ts 分片）。

    在有多个变体时**优先选择分辨率最高的流**。这一点对检测任务
    至关重要：道路监控中远处车辆往往只占十几个像素，
    低码率流会让检测率断崖式下降（实测 352x288 下全部漏检）。
    """
    text = _fetch_text(stream_url, headers)
    if not text:
        return None

    if '#EXT-X-STREAM-INF' in text:
        variants = _iter_variants(text, stream_url)
        # 先比像素数，相同则比带宽
        ordered = sorted(variants, key=lambda item: (item[1], item[2]), reverse=True)
        # 末位的低码率变体作为兜底（可能仅有它是可用的）
        candidates = [item[0] for item in ordered] + [
            uri for uri in _playlist_uris(text, stream_url) if uri.endswith(_HLS_PLAYLIST_SUFFIX)
        ]

        for candidate in candidates:
            child_text = _fetch_text(candidate, headers)
            if not child_text:
                continue
            child_uris = _playlist_uris(child_text, candidate)
            if child_uris:
                return candidate, child_uris

        return None

    uris = _playlist_uris(text, stream_url)
    return (stream_url, uris) if uris else None


def _download(url: str, headers: Dict[str, str], target: str, timeout: int = 15) -> bool:
    """流式下载资源到本地文件。"""
    try:
        with requests.get(url, headers=headers, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            with open(target, 'wb') as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    handle.write(chunk)
    except (requests.RequestException, OSError):
        return False
    return os.path.isfile(target) and os.path.getsize(target) > 0


def _decode_segment(
    segment_path: str,
    output_dir: str,
    limit: int,
    offset: int = 0,
) -> List[str]:
    """用 OpenCV 离线解码本地 ts 分片，导出至多 ``limit`` 帧。

    分片是完整文件而非流，因此解码稳定，不存在直播流的缓冲与丢帧问题。
    """
    try:
        import cv2
    except ImportError:
        return []

    capture = cv2.VideoCapture(segment_path)
    if not capture.isOpened():
        return []

    paths: List[str] = []
    try:
        index = 0
        while index < limit:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            path = os.path.join(output_dir, f'h_{offset + index:04d}.jpg')
            if cv2.imwrite(path, frame):
                paths.append(path)
            index += 1
    finally:
        capture.release()

    return paths


def grab_frames_from_hls(
    stream_url: str,
    headers: Optional[Dict[str, str]],
    count: int,
    output_dir: str,
) -> List[str]:
    """从 HLS 直播流抽取 ``count`` 帧，**无需 ffmpeg**。

    实现要点：先定位媒体播放列表，再从**最新分片向前**依次下载解码，
    直到攒够所需的帧数。向前回溯是因为直播列表中的末尾分片可能尚未
    写满（部分播放器会返回不完整分片）。

    返回本地帧路径（按时间升序）；无法取流时返回空列表。
    """
    if count <= 0:
        return []

    os.makedirs(output_dir, exist_ok=True)

    resolved = _resolve_media_playlist(stream_url, headers or {})
    if resolved is None:
        return []
    _, segments = resolved
    if not segments:
        return []

    paths: List[str] = []
    segment_path = os.path.join(output_dir, '_segment.ts')

    # 从倒数第二个分片开始：末尾分片在直播中常常仍在写入
    for segment in reversed(segments[:-1] or segments):
        if len(paths) >= count:
            break
        if not _download(segment, headers or {}, segment_path):
            continue
        paths.extend(_decode_segment(segment_path, output_dir, count - len(paths)))
        try:
            os.remove(segment_path)
        except OSError:
            pass

    return paths


def probe_frame_rate(
    stream_url: str,
    headers: Optional[Dict[str, str]],
) -> Optional[float]:
    """探测 HLS 流的真实帧率。

    时基直接决定速度与 TTC 的量纲：把 25 fps 的流按 10 fps 换算，
    速度会被**低估 2.5 倍**、TTC 被**高估 2.5 倍**。比值型判据
    （速度骤降比等，分子分母同比例缩放）不受影响，但所有绝对
    阈值都依赖真实时基，因此分析前必须先探测。

    实现上取媒体分片读取 ``CAP_PROP_FPS``：分片是完整文件，
    容器头带精确帧率，比在直播流上估算可靠得多。
    """
    try:
        import cv2
    except ImportError:
        return None

    resolved = _resolve_media_playlist(stream_url, headers or {})
    if resolved is None:
        return None
    _, segments = resolved
    if not segments:
        return None

    fd, probe_path = tempfile.mkstemp(suffix='.ts')
    os.close(fd)

    try:
        for segment in reversed(segments):
            if not _download(segment, headers or {}, probe_path):
                continue

            capture = cv2.VideoCapture(probe_path)
            try:
                if not capture.isOpened():
                    continue
                fps = float(capture.get(cv2.CAP_PROP_FPS))
            finally:
                capture.release()

            # 容错：部分容器头会返回 0 或异常值
            if 1.0 <= fps <= 120.0:
                return fps

        return None
    finally:
        try:
            os.remove(probe_path)
        except OSError:
            pass


def grab_frame(
    stream_url: str,
    output_path: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """从直播流抓取一帧并保存为 JPEG，返回本地文件路径。

    按代价从低到高依次尝试三条路径：

    1. **ffmpeg** —— 最快，且可通过 ``-headers`` 携带 Referer
    2. **OpenCV 直连** —— 适用于无需鉴权的流
    3. **HLS 分片解码** —— 无 ffmpeg 环境下的兜底（见 :func:`grab_frames_from_hls`）

    三条路径均失败时抛出 :class:`VideoSourceError`。
    """
    if not output_path:
        stamp = datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
        output_path = str(settings.upload_path / f'frame_{stamp}.jpg')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ffmpeg = find_ffmpeg()
    if ffmpeg and _grab_frame_ffmpeg(ffmpeg, stream_url, output_path):
        return output_path

    if _grab_frame_cv2(stream_url, output_path):
        return output_path

    # 兜底：上游校验 Referer 时直连必然被拒，改从媒体分片离线取帧
    work_dir = os.path.join(os.path.dirname(output_path), '_hls_frames')
    frames = grab_frames_from_hls(stream_url, headers, 1, work_dir)
    if frames:
        shutil.move(frames[0], output_path)
        shutil.rmtree(work_dir, ignore_errors=True)
        return output_path

    raise VideoSourceError(
        '直播流抽帧失败',
        hint=(
            '请确认直播流地址未过期；若仍有问题，可安装 ffmpeg 并设置 '
            'FFMPEG_PATH 环境变量以提高抽帧成功率'
        ),
        payload={'streamUrl': stream_url, 'ffmpeg': ffmpeg or '未找到'},
    )


# ==========================================================================
# Provider 抽象
# ==========================================================================


class VideoSourceProvider(ABC):
    """视频源提供者抽象基类。"""

    key: str = ''
    display_name: str = ''
    region_label: str = ''

    def __init__(self, options: Optional[Dict[str, Any]] = None) -> None:
        self.options: Dict[str, Any] = options or {}

    # ---------------------------------------------------------------- 能力
    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """返回 ``(是否可用, 不可用原因)``。"""

    @abstractmethod
    def list_cameras(
        self,
        count: int = 8,
        exclude: Optional[Sequence[str]] = None,
    ) -> CameraQueryResult:
        """检索摄像头列表。"""

    @abstractmethod
    def get_stream_url(self, camera_num: str) -> str:
        """获取指定摄像头的直播流地址。"""

    # ---------------------------------------------------------------- 通用
    @property
    def request_headers(self) -> Dict[str, str]:
        """访问上游资源（m3u8 播放列表、ts 分片等）所需的 HTTP 头。

        上游普遍校验 ``Referer``，浏览器直接拉流会因缺少该头而被拒，
        因此需要由后端代为访问。子类按需覆盖。
        """
        return {}

    def capture_frame(self, camera_num: str) -> Tuple[str, str]:
        """抓取一帧，返回 ``(本地图片路径, 直播流地址)``。"""
        stream_url = self.get_stream_url(camera_num)
        return grab_frame(stream_url, headers=self.request_headers), stream_url

    @property
    def default_camera(self) -> str:
        return str(self.options.get('default_camera', ''))

    def describe(self) -> VideoSourceInfo:
        available, reason = self.is_available()
        return VideoSourceInfo(
            key=self.key,
            display_name=self.display_name,
            region=self.region_label,
            available=available,
            reason=reason,
            default_camera=self.default_camera,
        )


# ==========================================================================
# Provider：河南高速
# ==========================================================================


class HenanVideoProvider(VideoSourceProvider):
    """河南高速云视频（weixin.hngscloud.com）。

    上游接口
    --------
    ``GET /camera/search``   按视野范围检索摄像头列表
    ``GET /camera/playUrl``  获取带时效 token 的 m3u8 地址（约数小时有效）

    两者均需携带 ``Referer``，否则服务端拒绝。

    注意：``playUrl`` 的参数名上游拼写为 ``cameraNUm``（大写 N、小写 u），
    属上游历史遗留，不可"纠正"。
    """

    key = 'henan'
    display_name = '河南高速云视频'
    region_label = '河南'

    # -------------------------------------------------------------- 请求头
    @property
    def request_headers(self) -> Dict[str, str]:
        return {
            'Referer': str(self.options.get('referer', settings.hngs_referer)),
            'User-Agent': str(self.options.get('user_agent', settings.hngs_user_agent)),
        }

    # -------------------------------------------------------------- 内部
    def _request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        base = str(self.options.get('api_base', settings.hngs_api_base)).rstrip('/')
        try:
            response = requests.get(
                base + path,
                params=params,
                headers=self.request_headers,
                timeout=int(self.options.get('timeout', settings.hngs_request_timeout)),
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise VideoSourceError(
                '河南高速云平台请求失败',
                hint=f'请检查网络连通性，或确认 {base} 是否可访问',
                payload={'endpoint': path},
            ) from exc
        except ValueError as exc:
            raise VideoSourceError(
                '河南高速云平台返回非 JSON 数据',
                hint='接口可能已变更或触发了风控，请稍后重试',
                payload={'endpoint': path},
            ) from exc

    # -------------------------------------------------------------- 能力
    def is_available(self) -> Tuple[bool, str]:
        # 配置就绪即视为可调度；真实连通性由各接口调用时体现，
        # 避免应用启动阶段因外网阻塞而拖慢初始化。
        return True, ''

    # ---------------------------------------------------------- 摄像头列表
    def list_cameras(
        self,
        count: int = 8,
        exclude: Optional[Sequence[str]] = None,
    ) -> CameraQueryResult:
        payload = self._request('/camera/search', {
            'zoomLevel': self.options.get('viewport_zoom', settings.hngs_viewport_zoom),
            'sbMapLevel': self.options.get('viewport_zoom', settings.hngs_viewport_zoom),
            'northEast': self.options.get('viewport_ne', settings.hngs_viewport_ne),
            'southWest': self.options.get('viewport_sw', settings.hngs_viewport_sw),
        })

        raw_cameras = payload.get('data')
        if not isinstance(raw_cameras, list):
            raise VideoSourceError(
                f"摄像头列表格式不符合预期（msg={payload.get('msg', '无')}）",
                hint='上游接口可能已变更',
            )

        excluded = set(exclude or [])
        available = [
            item for item in raw_cameras
            if str(item.get('cameraNum', '')) not in excluded
        ]

        picked = random.sample(available, min(count, len(available))) if available else []

        cameras: List[CameraInfo] = []
        for item in picked:
            try:
                cameras.append(CameraInfo(
                    camera_num=str(item.get('cameraNum', '')),
                    camera_name=item.get('cameraName', '') or '',
                    road=item.get('road', '') or '',
                    pile_num=item.get('pileNum', '') or '',
                    region_name=item.get('regionName', '') or '',
                    latitude=float(item.get('latitude') or 0),
                    longitude=float(item.get('longitude') or 0),
                    online=int(item.get('online') or 0),
                ))
            except (TypeError, ValueError):
                # 个别脏数据不应导致整批失败
                continue

        return CameraQueryResult(
            cameras=cameras,
            total_available=len(available),
            total_all=len(raw_cameras),
        )

    # ---------------------------------------------------------- 直播流地址
    def get_stream_url(self, camera_num: str) -> str:
        camera_num = camera_num or self.default_camera
        if not camera_num:
            raise VideoSourceError('未指定摄像头编号，且视频源未配置默认摄像头')

        payload = self._request('/camera/playUrl', {
            'cameraNUm': camera_num,   # 上游参数名即为如此拼写
            'videoType': self.options.get('video_type', settings.hngs_video_type),
            'videoRate': self.options.get('video_rate', settings.hngs_video_rate),
        })

        stream_url = (payload.get('data') or {}).get('playUrl')
        if payload.get('code') != 200 or not stream_url:
            raise VideoSourceError(
                f"获取直播流地址失败：{payload.get('msg', '上游未返回 playUrl')}",
                hint='摄像头可能已离线，或该编号不存在',
                payload={'cameraNum': camera_num},
            )
        return str(stream_url)


# ==========================================================================
# Provider：上海（保留接入位）
# ==========================================================================


class ShanghaiVideoProvider(VideoSourceProvider):
    """上海城市道路监控 —— 接口占位。

    上海交警与市交通委均未开放公开的监控视频接口，因此本 Provider 保留
    完整的调用契约，但所有取流操作都会抛出明确的 :class:`VideoSourceError`。

    未来获得合法接入渠道时，只需实现 ``list_cameras`` 与 ``get_stream_url``，
    API 层与前端无需任何改动。

    注意：上海**路网 / 地图 / 地理编码**能力不属于本模块，
    见 ``app/services/road_network.py`` 与 ``app/api/v1/roads.py``。
    """

    key = 'shanghai'
    display_name = '上海城市道路监控'
    region_label = '上海'

    _REASON = (
        '上海交警 / 市交通委未开放公开监控视频接口，当前仅保留接入位。'
        '实时视频已切换至河南高速云视频源。'
    )

    def is_available(self) -> Tuple[bool, str]:
        return False, self._REASON

    def list_cameras(
        self,
        count: int = 8,
        exclude: Optional[Sequence[str]] = None,
    ) -> CameraQueryResult:
        raise VideoSourceError(
            '上海视频源尚未接入',
            hint='可将 VIDEO_SOURCE 设为 henan 使用河南高速云视频；上海路网与地图接口不受影响',
        )

    def get_stream_url(self, camera_num: str) -> str:
        raise VideoSourceError(
            '上海视频源尚未接入',
            hint='上海暂无公开监控视频接口，详见 docs/视频源说明.md',
            payload={'cameraNum': camera_num},
        )


# ==========================================================================
# 注册表与工厂
# ==========================================================================

_PROVIDER_REGISTRY: Dict[str, type] = {
    HenanVideoProvider.key: HenanVideoProvider,
    ShanghaiVideoProvider.key: ShanghaiVideoProvider,
}

_PROVIDER_OPTIONS: Dict[str, Dict[str, Any]] = {
    HenanVideoProvider.key: {
        'api_base': settings.hngs_api_base,
        'referer': settings.hngs_referer,
        'user_agent': settings.hngs_user_agent,
        'timeout': settings.hngs_request_timeout,
        'default_camera': settings.hngs_default_camera,
        'video_type': settings.hngs_video_type,
        'video_rate': settings.hngs_video_rate,
        'viewport_ne': settings.hngs_viewport_ne,
        'viewport_sw': settings.hngs_viewport_sw,
        'viewport_zoom': settings.hngs_viewport_zoom,
    },
    ShanghaiVideoProvider.key: {
        'default_camera': '',
    },
}

_INSTANCES: Dict[str, VideoSourceProvider] = {}


def get_provider(key: Optional[str] = None) -> VideoSourceProvider:
    """按 key 获取 Provider；``key`` 为空时使用 ``settings.video_source``。"""
    resolved = (key or settings.video_source or HenanVideoProvider.key).strip().lower()

    if resolved not in _PROVIDER_REGISTRY:
        raise VideoSourceError(
            f'未知的视频源：{resolved}',
            hint='可用视频源：' + '、'.join(sorted(_PROVIDER_REGISTRY)),
        )

    if resolved not in _INSTANCES:
        _INSTANCES[resolved] = _PROVIDER_REGISTRY[resolved](_PROVIDER_OPTIONS.get(resolved, {}))

    return _INSTANCES[resolved]


def list_sources() -> List[VideoSourceInfo]:
    """列出全部已注册视频源及其可用状态。"""
    return [get_provider(key).describe() for key in _PROVIDER_REGISTRY]


def current_source() -> VideoSourceInfo:
    """当前生效的视频源描述。"""
    return get_provider().describe()
