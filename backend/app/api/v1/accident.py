"""
事故识别算法接口
================
+----------------------+-----------------------------------------------+
| ``GET  /algorithm``  | 算法参数、判据与权重说明                       |
| ``GET  /scenarios``  | 内置仿真场景列表                               |
| ``POST /simulate``   | 运行合成轨迹仿真（无需真实事故视频）           |
| ``POST /analyze``    | 抓取实时流片段并分析                           |
+----------------------+-----------------------------------------------+

可解释性
--------
``/simulate`` 与 ``/analyze`` 的响应都携带**逐条判据的证据链**
（原始值 / 阈值 / 归一化得分 / 权重贡献），前端可直接渲染
「为什么判定为事故」。这是交警采信与答辩演示的关键 ——
黑盒模型无法提供这一能力。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import accident_service
from app.services.accident_detector import AccidentConfig, AccidentDetector
from app.services.detector import DetectorUnavailable
from app.services.video_source import VideoSourceError

router = APIRouter(prefix='/accident', tags=['事故识别'])


@router.get('/algorithm', summary='算法说明')
def algorithm_description() -> Dict[str, Any]:
    """返回三级级联框架说明、判据权重与当前参数。

    前端可据此渲染算法说明卡片与参数表，也便于论文撰写时对照。
    """
    detector = AccidentDetector()

    return {
        'success': True,
        'framework': [
            {
                'layer': 'L1',
                'name': '感知层',
                'method': 'YOLOv8 检测 + ByteTrack/IoU 关联跟踪',
                'output': '目标轨迹序列（Track）',
                'purpose': '保证实时性',
            },
            {
                'layer': 'L2',
                'name': '规则层',
                'method': '轨迹物理量多判据融合',
                'output': '事故候选 + 证据链',
                'purpose': '保证可解释性（每条判据可追溯）',
            },
            {
                'layer': 'L3',
                'name': '时序确认层',
                'method': '滑动窗口投票（可替换为时序模型）',
                'output': '最终确认事件',
                'purpose': '抑制单帧抖动导致的误报',
            },
        ],
        'criterions': [
            {
                'name': 'contact',
                'label': '目标接触',
                'formula': 'IoU(b_i, b_j) > τ 或 归一化中心距 < δ',
                'rationale': '碰撞必然伴随空间接近；路侧俯视下 bbox 常因遮挡不重叠，故辅以中心距',
            },
            {
                'name': 'speed_drop',
                'label': '速度骤降',
                'formula': '(v_before − v_after) / v_before > τ_v',
                'rationale': '核心判据：真实碰撞必然导致速度骤降，正常跟车与变道不会',
            },
            {
                'name': 'ttc',
                'label': '碰撞时间',
                'formula': 'TTC = d / (−ḋ)，仅在两目标接近时成立',
                'rationale': '刻画「即将碰撞」的紧迫程度，对追尾场景尤为敏感',
            },
            {
                'name': 'heading_change',
                'label': '轨迹畸变',
                'formula': '|θ_after − θ_before| / Δt > τ_θ',
                'rationale': '碰撞会令目标非预期转向；但变道同样转向，故权重最低',
            },
        ],
        'suppressions': [
            {'name': 'vulnerable_penalty', 'label': '行人/非机动车折减', 'factor': detector.config.vulnerable_penalty},
            {'name': 'synchronized_slowdown', 'label': '多车同步减速（红灯）', 'threshold': detector.config.synchronized_count},
            {'name': 'stationary_pair', 'label': '双方均近静止（违停）', 'threshold': detector.config.stationary_speed},
            {'name': 'min_track_age', 'label': '轨迹最短观测帧数', 'threshold': detector.config.min_track_age},
        ],
        'algorithm': detector.describe(),
    }


@router.get('/scenarios', summary='仿真场景列表')
def list_scenarios() -> Dict[str, Any]:
    """内置的合成轨迹场景，用于在无真实事故视频时验证算法。"""
    return {'success': True, 'items': accident_service.list_scenarios()}


@router.post('/simulate', summary='合成轨迹仿真')
def simulate(
    scenario: str = Query('collision', description='场景 key'),
) -> Dict[str, Any]:
    """运行合成轨迹仿真，返回判定结果与证据链。

    这是**算法验证与答辩演示的主要入口**：四种场景分别覆盖
    正样本与两类典型误报陷阱。
    """
    try:
        result = accident_service.simulate_scenario(scenario)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                'success': False,
                'error': str(exc),
                'hint': '可选场景：' + '、'.join(item['key'] for item in accident_service.list_scenarios()),
            },
        ) from exc

    return {'success': True, **result}


@router.post('/analyze', summary='实时片段事故分析')
def analyze(
    camera_num: str = Query(..., alias='cameraNum', description='摄像头编号'),
    source: Optional[str] = Query(None, description='视频源 key'),
    frame_count: int = Query(
        accident_service.DEFAULT_FRAME_COUNT,
        ge=5,
        le=120,
        alias='frameCount',
        description='分析帧数',
    ),
    road_id: str = Query('R001', alias='roadId'),
) -> Dict[str, Any]:
    """抓取实时流的一段（默认 30 帧 ≈ 3 秒）并执行事故分析。

    注意：本接口会**同步阻塞**若干秒（抽帧 + 逐帧推理），
    同步端点由 FastAPI 放入线程池执行，不阻塞事件循环。
    """
    try:
        result = accident_service.analyze_camera(
            camera_num=camera_num,
            source_key=source,
            frame_count=frame_count,
            road_id=road_id,
        )
    except VideoSourceError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc
    except DetectorUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                'success': False,
                'error': str(exc),
                'hint': '模型未就绪，事故分析不可用；可先用 /accident/simulate 验证算法逻辑',
            },
        ) from exc

    return {'success': True, **result}


@router.get('/config', summary='算法参数')
def get_config() -> Dict[str, Any]:
    """当前的算法阈值与权重，便于前端调参面板使用。"""
    config = AccidentConfig()
    return {'success': True, 'config': config.to_dict()}
