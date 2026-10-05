"""Composer 配置常量（V2 契约）。

所有阈值集中在此定义，禁止散落硬编码。
变速采用双层策略（V2 §12）：preferred 区间优先，absolute 为硬边界（validator 强制）。
"""

from pathlib import Path
import os

# ---------------------------------------------------------------------------
# 变速策略（V2 §12，取代 V1 §15 的 0.85-1.60 单层）
# ---------------------------------------------------------------------------
SPEED_PREFERRED_MIN = 0.90
SPEED_PREFERRED_MAX = 1.50
SPEED_ABSOLUTE_MIN = 0.50   # 慢放下限（短视频素材进长镜头需慢放，0.5x 是标准慢动作）
SPEED_ABSOLUTE_MAX = 1.70
# 多镜头快切上限（素材全利用「不裁剪可加快」场景允许比普通路径更高的加速）
SPEED_FAST_FORWARD_MAX = 6.0

# ---------------------------------------------------------------------------
# 时间轴容差（V1 §31，V2 §45 保留）
# ---------------------------------------------------------------------------
DURATION_TOLERANCE = 0.10          # IR/validator 层：总时长 vs 旁白
ITEM_DURATION_TOLERANCE = 0.05    # 单场景覆盖

# 渲染层时长公差：帧量化导致（25fps 一帧=0.04s），8~20 段累计的平均漂移
# ~半帧/段，取 0.20s 作为确定性能通过的工程上界（spec 0.10s 是 IR 层目标，
# 渲染产物受帧粒度约束天然略宽）。
RENDER_DURATION_TOLERANCE = 0.20

# ---------------------------------------------------------------------------
# 资产约束（V1 §25/§17）
# ---------------------------------------------------------------------------
IMAGE_MIN_DURATION = 1.0
IMAGE_MOTION_MIN_DURATION = 2.0  # V2 §14：>2s 图片默认轻微 motion
IMAGE_MAX_DURATION = 30.0

# ---------------------------------------------------------------------------
# 渲染参数（V1 §20/§21）
# ---------------------------------------------------------------------------
DEFAULT_FPS = 30
DEFAULT_RESOLUTION = (1080, 1920)  # 9:16
DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_PIXEL_FORMAT = "yuv420p"
DEFAULT_CRF = 20

# ---------------------------------------------------------------------------
# 转场策略（V2 §26）
# ---------------------------------------------------------------------------
TRANSITION_DEFAULT = "cut"
TRANSITION_MAX_MAJOR_EFFECTS = 3   # 全片效果转场上限（cut 不算效果）
MOTION_MAX_EFFECTS_PER_SCENE = 2

# ---------------------------------------------------------------------------
# 音频策略（V2 §31/§32/§34）
# ---------------------------------------------------------------------------
BGM_DEFAULT_VOLUME = 0.16
DUCKING_DB = -10.0                 # 旁白出现时 BGM 压低量（固定 duck 第一版，区间 -8~-14）
SFX_DEFAULT_VOLUME = 0.5
SFX_DENSITY_DEFAULT = "sparse"     # sparse | medium | dense

# ---------------------------------------------------------------------------
# 确定性文案→时长估算（Gate 1 无 TTS 时的旁白时长来源）
# ---------------------------------------------------------------------------
# 中文旁白播报语速约 4.3 字/秒（240 字/分钟），保守取 4.5
SCRIPT_CPS = 4.5
SCENE_MIN_DURATION = 2.0
SCENE_GAP_PADDING = 0.35           # 场景间呼吸停顿

# ---------------------------------------------------------------------------
# 修复闭环（V1 §33，V2 §49）
# ---------------------------------------------------------------------------
MAX_REPAIR_ROUNDS = 3

# ---------------------------------------------------------------------------
# 项目目录布局（V2 §75）
# ---------------------------------------------------------------------------


def composer_storage_root() -> Path:
    """项目产物根目录；支持 VIDEO_DIRECTOR_STORAGE_ROOT 隔离测试/运行数据。"""
    configured = os.environ.get("VIDEO_DIRECTOR_STORAGE_ROOT", "").strip()
    root = (Path(configured).expanduser() if configured
            else Path(__file__).resolve().parent.parent / "storage" / "composer")
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(project_id: str) -> Path:
    """单个 composer 项目目录 storage/composer/<project_id>/。"""
    d = composer_storage_root() / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


PROJECT_SUBDIRS = (
    "input",
    "assets",
    "plans",
    "audio",
    "audio/sfx",
    "alignment",
    "render",
    "render/segments",
    "qc",
    "output",
)


def init_project_dirs(base: Path) -> dict:
    """按 V2 §75 布局创建项目子目录，返回 {name: Path}。"""
    dirs = {}
    for sub in PROJECT_SUBDIRS:
        d = base / sub
        d.mkdir(parents=True, exist_ok=True)
        dirs[sub] = d
    return dirs
