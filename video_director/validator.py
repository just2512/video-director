"""Composition IR Validator（V1 §30/§31 + V2 §45/§53）。

职责分层：
- 结构合法性（schema/类型/区间）→ pydantic model_validator（models.py 内）承担
- 业务规则（资产存在/速度双层边界/转场预算/图层可渲染性/音频覆盖）→ 本模块

三态：PASS / REJECT / MISSING（V2 §91：技术正确由程序判断，不伪装成评分）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from video_director import config, registry
from video_director.models import (
    AssetRef,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    SceneIR,
)


class Verdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    MISSING = "MISSING"


@dataclass
class Issue:
    scene_id: str
    code: str
    message: str


@dataclass
class ValidationResult:
    verdict: Verdict
    issues: List[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == Verdict.PASS

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "issues": [
                {"scene_id": i.scene_id, "code": i.code, "message": i.message}
                for i in self.issues
            ],
        }


def validate_composition(
    ir: CompositionIR,
    assets: Dict[str, AssetRef],
    narration_duration: float,
    subtitle_entries: Optional[List[dict]] = None,
) -> ValidationResult:
    issues: List[Issue] = []

    def add(scene_id: str, code: str, message: str):
        issues.append(Issue(scene_id=scene_id, code=code, message=message))

    if not ir.scenes:
        return ValidationResult(Verdict.REJECT, [Issue("-", "EMPTY_TIMELINE", "IR 无场景")])

    # 1. 总时长 vs 旁白主时间轴
    if abs(ir.project.duration - narration_duration) > config.DURATION_TOLERANCE:
        add("-", "TOTAL_DURATION_MISMATCH",
            f"IR 总长 {ir.project.duration:.3f}s 与旁白 {narration_duration:.3f}s "
            f"差值超容差 {config.DURATION_TOLERANCE}s")

    # 2. 可渲染层检查（诚实降级：schema 允许 text/logo 等，但第一阶段渲染不了）
    for layer in ir.unrenderable_layers():
        add("-", "LAYER_NOT_RENDERABLE",
            f"层类型 {layer.type.value} 超出第一阶段渲染能力，需 HyperFrames Renderer")

    # 3. 转场预算与注册表
    major_count = 0
    for scene in ir.scenes:
        t = scene.transition_out.type
        if not registry.is_known_transition(t):
            add(scene.id, "TRANSITION_NOT_IN_REGISTRY", f"转场 {t} 不在注册表")
        elif registry.is_major_effect_transition(t):
            major_count += 1
            if not registry.is_implemented_transition(t):
                add(scene.id, "TRANSITION_NOT_IMPLEMENTED",
                    f"转场 {t} 尚未实现，第一阶段只支持 cut")
    if major_count > config.TRANSITION_MAX_MAJOR_EFFECTS:
        add("-", "TRANSITION_BUDGET_EXCEEDED",
            f"效果转场 {major_count} 个超上限 {config.TRANSITION_MAX_MAJOR_EFFECTS}")

    # 4. 逐场景资产/速度/源区间
    for scene in ir.scenes:
        for layer in scene.layers:
            # Explicit editor gaps have no source asset by design; the renderer
            # materializes them as black segments at the exact persisted duration.
            if layer.type == LayerType.gap:
                continue
            asset = assets.get(layer.asset_id or "")
            if asset is None:
                add(scene.id, "ASSET_NOT_FOUND", f"素材 {layer.asset_id} 不在资产清单")
                continue
            if asset.type != layer.type.value and layer.type not in (
                LayerType.video, LayerType.image,
            ):
                add(scene.id, "ASSET_TYPE_MISMATCH",
                    f"素材 {asset.id} 是 {asset.type}，层声明 {layer.type.value}")
            if layer.type == LayerType.video:
                if not (config.SPEED_ABSOLUTE_MIN <= layer.speed <= config.SPEED_FAST_FORWARD_MAX):
                    add(scene.id, "SPEED_OUT_OF_RANGE",
                        f"{layer.asset_id}: speed {layer.speed} 超出绝对边界 "
                        f"[{config.SPEED_ABSOLUTE_MIN}, {config.SPEED_FAST_FORWARD_MAX}]")
                if asset.duration is not None:
                    if layer.source_out > asset.duration + 0.05:
                        add(scene.id, "SOURCE_OUT_OF_RANGE",
                            f"{layer.asset_id}: source_out {layer.source_out} "
                            f"超素材时长 {asset.duration}")
                    if layer.source_in < -0.001:
                        add(scene.id, "SOURCE_IN_NEGATIVE",
                            f"{layer.asset_id}: source_in {layer.source_in} 为负")
            elif layer.type == LayerType.image:
                if layer.motion is not None and not registry.is_known_motion(layer.motion):
                    add(scene.id, "MOTION_NOT_IN_REGISTRY", f"motion {layer.motion} 不在注册表")
                if layer.motion is not None and not registry.is_implemented_motion(layer.motion):
                    add(scene.id, "MOTION_NOT_IMPLEMENTED",
                        f"motion {layer.motion} 尚未实现（第一阶段只支持 slow_zoom_in）")
    # 视频区间重叠：仅同场景内检查（跨场景同素材区间交叠是 §16"重复素材"
    # 档的合法循环复用；spec §30 检查清单只要求不越界，无跨场景重叠规则）。
    by_scene_asset: Dict[tuple, List[Layer]] = {}
    for scene in ir.scenes:
        for layer in scene.layers:
            if layer.type == LayerType.video and layer.asset_id:
                by_scene_asset.setdefault((scene.id, layer.asset_id), []).append(layer)
    for (scene_id, aid), layers in by_scene_asset.items():
        intervals = sorted((l.source_in, l.source_out) for l in layers)
        for i in range(1, len(intervals)):
            prev_end, cur_start = intervals[i - 1][1], intervals[i][0]
            if cur_start < prev_end - 0.05:
                add(scene_id, "SOURCE_RANGE_OVERLAP",
                    f"{aid}: 同场景 source 区间重叠 {cur_start:.3f} < 前段结束 {prev_end:.3f}")

    # 5. 音频规则
    narration_tracks = [t for t in ir.audio if t.type == AudioTrackType.narration]
    if narration_tracks:  # 仅当 IR 声明了旁白轨才校验覆盖（Gate 1 纯视觉无旁白轨）
        narration_total = sum(t.duration for t in narration_tracks)
        if abs(narration_total - narration_duration) > config.DURATION_TOLERANCE:
            add("-", "NARRATION_COVERAGE_MISMATCH",
                f"narration 轨总长 {narration_total:.3f}s 与旁白 {narration_duration:.3f}s 不符")
    for track in ir.audio:
        if track.type in (AudioTrackType.bgm, AudioTrackType.sfx, AudioTrackType.ambient, AudioTrackType.ambience):
            if track.asset_id and track.asset_id not in assets:
                add("-", "AUDIO_ASSET_NOT_FOUND", f"音频素材 {track.asset_id} 不在资产清单")

    # 6. 字幕边界（最终输出时间轴）
    if subtitle_entries:
        for entry in subtitle_entries:
            end = entry.get("end", 0)
            if end > ir.project.duration + config.DURATION_TOLERANCE:
                add("-", "SUBTITLE_OUT_OF_RANGE",
                    f"字幕结束 {end:.3f}s 超出时间轴 {ir.project.duration:.3f}s")

    if issues:
        missing_only = all(i.code == "ASSET_NOT_FOUND" for i in issues)
        return ValidationResult(Verdict.MISSING if missing_only else Verdict.REJECT, issues)
    return ValidationResult(Verdict.PASS)


def scene_id_of(layer: Layer, ir: CompositionIR) -> str:
    for scene in ir.scenes:
        if layer in scene.layers:
            return scene.id
    return "-"


# ---------------------------------------------------------------------------
# 词级时间轴 → 场景对齐（V1 §11，Gate 2 使用）
# ---------------------------------------------------------------------------

def build_narration_alignment(
    scene_texts: List[str],
    word_timeline: List[dict],
) -> List[dict]:
    """从词级时间轴恢复每个场景句子的起止时间。

    word_timeline: [{text, start, end}, ...] 按时间排序
    返回 [{id, text, start, duration}]（duration = end - start）
    匹配失败抛 ValueError（上层回退 estimate 模式）。
    """
    def norm(s: str) -> str:
        return re.sub(r"[\s，。！？；：、,.!?;:　“”\"'（）()【】\[\]]", "", s)

    idx = 0
    results = []
    for si, text in enumerate(scene_texts):
        target = norm(text)
        if not target:
            raise ValueError(f"scene {si}: 空句子")
        matched = None
        for start_idx in range(idx, len(word_timeline)):
            acc = ""
            j = start_idx
            while j < len(word_timeline) and len(acc) < len(target):
                acc += norm(word_timeline[j]["text"])
                j += 1
            if acc == target:
                matched = (start_idx, j)
                break
            if len(acc) >= len(target):
                break
        if matched is None:
            raise ValueError(f"scene {si}: 句子未能在词时间轴中匹配: {text[:30]}")
        s, e = matched
        start_t = word_timeline[s]["start"]
        end_t = word_timeline[e - 1]["end"]
        results.append({
            "id": f"S{si + 1:02d}",
            "text": text,
            "start": round(start_t, 3),
            "duration": round(end_t - start_t, 3),
        })
        idx = e
    return results

