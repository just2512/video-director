"""Pipeline Manifest 定义（OpenMontage 思想：流水线 + 阶段产物 + 校验准则）。

零外部依赖（stdlib + 可选 yaml）。不 import 现有 pipeline.py，避免循环；
本模块是独立的数据定义层，供 pipeline 未来调用以描述自身编排。

两条内置流水线（纯 Python 定义，不依赖 YAML 文件）：
  script-media：capability_check → script → tts → alignment → subtitle →
                 asset → match → plan → render → qc  （10 stages）
  script-only ：capability_check → script → tts → alignment → subtitle →
                 style → text_scenes → render → qc                （ 9 stages）

每个 stage 三件套：
  produces         本阶段产物（供 checkpoint 记录 artifact_paths 的语义说明）
  review_focus     人审关注点（描述性，不是机器校验）
  success_criteria 机器可判定的成功条件（文本，由调用方负责实际判定）

依赖模型：线性链——stage N 依赖 stage 0..N-1。depends_on 字段在构造时自动
填入前面所有 stage 名。换风格只重算 style 下游：由 checkpoint 模块的
stage_upstream_changed 按"上游指纹"判断，不依赖此处的 depends_on。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PipelineStage:
    """单个流水线阶段定义。

    depends_on 默认在线性链构造时填入前面所有 stage 名（元组，不可变）。
    """

    name: str
    skill: str
    produces: str
    review_focus: str
    success_criteria: str
    depends_on: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineManifest:
    """一条流水线的完整定义。stages 为元组以保证不可变。"""

    name: str
    version: str
    stages: Tuple[PipelineStage, ...]
    description: str = ""

    def stage_names(self) -> List[str]:
        return [s.name for s in self.stages]

    def stage_by_name(self, name: str) -> Optional[PipelineStage]:
        for s in self.stages:
            if s.name == name:
                return s
        return None

    def upstream_of(self, stage_name: str) -> List[str]:
        """返回某 stage 显式声明的上游 stage 名列表。

        线性链中等价于"前面所有 stage"，但允许未来 DAG 扩展。
        """
        stage = self.stage_by_name(stage_name)
        if stage is None:
            return []
        return list(stage.depends_on)


# ---------------------------------------------------------------------------
# 内置流水线定义（元组列表 → 构造为 PipelineStage；顺序即依赖链）
# ---------------------------------------------------------------------------

_SCRIPT_MEDIA_STAGES = [
    (
        "capability_check",
        "doctor",
        "环境检查报告（PASS/SKIP/FAIL 三态）",
        "必需能力无 FAIL（FFmpeg / EdgeTTS / 磁盘空间）",
        "所有必需能力 PASS 或 SKIP，FAIL=0",
    ),
    (
        "script",
        "script",
        "plans/scenes.json（场景切分 + 确定性时长估算）",
        "文案切分粒度合理（无过短句、无超长句）",
        "至少 1 个场景，每场景去标点后字数 >= 8",
    ),
    (
        "tts",
        "tts",
        "audio/narration.wav（EdgeTTS 词级时间戳）",
        "TTS 发音与语速自然",
        "音频文件存在且 duration > 0",
    ),
    (
        "alignment",
        "alignment",
        "alignment/words.json（词级时间戳）",
        "时间戳覆盖完整、无大段空洞",
        "时间戳覆盖 narration 全时长",
    ),
    (
        "subtitle",
        "subtitle",
        "render/master.srt",
        "字幕与旁白对齐",
        "字幕条目数与场景数一致",
    ),
    (
        "asset",
        "vision",
        "assets/manifest.json + assets_semantic.json",
        "素材语义理解覆盖率（description/keywords 非空比例）",
        "每个 asset 有 description 或 keywords",
    ),
    (
        "match",
        "matcher",
        "assets/match_assignments.json",
        "场景-素材匹配合理性（不强行配对）",
        "每场景有 asset 或显式 MISSING 标记",
    ),
    (
        "plan",
        "planner",
        "plans/composition.json（CompositionIR）",
        "IR 规划正确性（转场预算 / 时长连续）",
        "IR 通过 validator.validate_composition",
    ),
    (
        "render",
        "renderer",
        "render/segments/*.mp4",
        "渲染无 ffmpeg 错误",
        "段文件齐全且可 ffprobe",
    ),
    (
        "qc",
        "qc",
        "qc/deterministic.json + output/final.mp4",
        "质量门通过（时长 / 分辨率 / 音频）",
        "QC verdict == PASS",
    ),
]

_SCRIPT_ONLY_STAGES = [
    (
        "capability_check",
        "doctor",
        "环境检查报告",
        "必需能力无 FAIL",
        "所有必需能力 PASS 或 SKIP",
    ),
    (
        "script",
        "script",
        "plans/scenes.json",
        "文案切分粒度",
        "至少 1 个场景",
    ),
    (
        "tts",
        "tts",
        "audio/narration.wav",
        "TTS 质量",
        "音频文件存在且 duration > 0",
    ),
    (
        "alignment",
        "alignment",
        "alignment/words.json",
        "词级时间戳覆盖",
        "时间戳覆盖 narration 全时长",
    ),
    (
        "subtitle",
        "subtitle",
        "render/master.srt",
        "字幕对齐",
        "字幕条目数 = 场景数",
    ),
    (
        "style",
        "style",
        "plans/style.json（StyleContract）",
        "风格选择合理性（与文案调性匹配）",
        "style_id 确定且 contract 字段完整",
    ),
    (
        "text_scenes",
        "text_scene_planner",
        "plans/text_scenes.json",
        "文本场景规划（无素材时的视觉表达）",
        "每场景有 text 呈现方案",
    ),
    (
        "render",
        "renderer",
        "render/segments/*.mp4",
        "渲染无错",
        "段文件齐全且可 ffprobe",
    ),
    (
        "qc",
        "qc",
        "qc/deterministic.json + output/final.mp4",
        "质量门通过",
        "QC verdict == PASS",
    ),
]


def _build_stages(raw: list) -> Tuple[PipelineStage, ...]:
    """从 (name, skill, produces, review, crit) 元组列表构造线性链 stages。

    stage N 的 depends_on = stage 0..N-1 的 name（线性链）。
    """
    stages: List[PipelineStage] = []
    for i, (name, skill, produces, review, crit) in enumerate(raw):
        depends = tuple(s.name for s in stages[:i])
        stages.append(
            PipelineStage(
                name=name,
                skill=skill,
                produces=produces,
                review_focus=review,
                success_criteria=crit,
                depends_on=depends,
            )
        )
    return tuple(stages)


_BUILTIN_PIPELINES: Dict[str, PipelineManifest] = {
    "script-media": PipelineManifest(
        name="script-media",
        version="1.0",
        stages=_build_stages(_SCRIPT_MEDIA_STAGES),
        description="完整文案→素材成片（有视频/图片素材）",
    ),
    "script-only": PipelineManifest(
        name="script-only",
        version="1.0",
        stages=_build_stages(_SCRIPT_ONLY_STAGES),
        description="纯文案成片（无素材，文本场景 + 风格）",
    ),
}


def get_pipeline(name: str) -> PipelineManifest:
    """按名取内置流水线。不存在抛 KeyError。"""
    if name not in _BUILTIN_PIPELINES:
        raise KeyError(
            f"未知流水线: {name}（可用: {list_pipelines()}）"
        )
    return _BUILTIN_PIPELINES[name]


def list_pipelines() -> List[str]:
    """内置流水线名列表。"""
    return list(_BUILTIN_PIPELINES.keys())


def _manifest_from_dict(data: Dict[str, Any]) -> PipelineManifest:
    """从 dict 构造 PipelineManifest（load_yaml 复用）。

    要求字段：name, version（可缺省 "1.0"）, stages（list of dict）。
    每个 stage dict：name 必需，skill/produces/review_focus/success_criteria
    可缺省（空串），depends_on 可缺省（按线性链推断）。
    """
    if not isinstance(data, dict):
        raise ValueError(f"流水线定义必须是 mapping，收到 {type(data).__name__}")
    if "name" not in data:
        raise ValueError("流水线定义缺少 name 字段")
    stages_raw = data.get("stages", [])
    if not isinstance(stages_raw, list):
        raise ValueError("stages 必须是列表")

    stages: List[PipelineStage] = []
    for i, s in enumerate(stages_raw):
        if not isinstance(s, dict) or "name" not in s:
            raise ValueError(f"stage[{i}] 必须是含 name 字段的 mapping")
        depends_raw = s.get("depends_on")
        if depends_raw:
            depends = tuple(str(x) for x in depends_raw)
        else:
            depends = tuple(st.name for st in stages[:i])
        stages.append(
            PipelineStage(
                name=str(s["name"]),
                skill=str(s.get("skill", "")),
                produces=str(s.get("produces", "")),
                review_focus=str(s.get("review_focus", "")),
                success_criteria=str(s.get("success_criteria", "")),
                depends_on=depends,
            )
        )
    return PipelineManifest(
        name=str(data["name"]),
        version=str(data.get("version", "1.0")),
        stages=tuple(stages),
        description=str(data.get("description", "")),
    )


def load_yaml(path) -> PipelineManifest:
    """从 YAML 文件加载 PipelineManifest（可选接口；内置流水线不依赖此函数）。

    需要 pyyaml。YAML 结构示例：

        name: script-media
        version: "1.0"
        description: 完整文案→素材成片
        stages:
          - name: capability_check
            skill: doctor
            produces: 环境检查报告
            review_focus: 必需能力无 FAIL
            success_criteria: FAIL=0
            # depends_on 可省略，按线性链推断；也可显式指定
          - name: script
            skill: script
            produces: plans/scenes.json
            review_focus: 切分粒度
            success_criteria: 至少 1 个场景
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml 未安装，无法加载 YAML 流水线定义（内置流水线不受影响）"
        ) from exc
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _manifest_from_dict(data)
