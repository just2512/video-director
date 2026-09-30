"""Frontend Unification §13 ContinuityVerifier（PPT Detector）。

第一性原理：PPT 式视频的问题不是「转场太少」，而是上一镜结束、下一镜
直接换内容——没有空间/元素/镜头/形变上的连续关系。

Verifier 检查「相邻 Scene 是否有合法的连续性关系」，不是给视频打
「连续性分数」（[[no-scoring-boundary]]：零评分，只产出结构化事实）。

规则（§13 验证规则 A-F，指纹级对照）：
    A. hard_cut                  → 合法（PASS）
    B. carry 缺 source/target    → REJECT
    C. camera continuity 缺 path → REJECT
    D. morph 缺 morph path       → REJECT
    E. 静态视觉块连续替换无任何连续 → WARNING（PPT Detector 主判定）
    F. carry 但锚点突然跳变       → WARNING（除非有 camera/morph 路径）

接入链（§13）：visual.continuity.verify → tool_executor → Verifier
→ Director → Review UI。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from video_director.models import CompositionIR
from video_director.visual_semantics import (
    ContinuityLink,
    ContinuityType,
)


# ---------------------------------------------------------------------------
# ContinuityReport Contract（§13 输出）
# ---------------------------------------------------------------------------

class ContinuityIssueCode(str, Enum):
    """§13 issueCode：每个 issue 的机器可读代码。"""
    HARD_CUT_OK = "HARD_CUT_OK"                  # 规则 A
    CARRY_MISSING_SOURCE = "CARRY_MISSING_SOURCE"  # 规则 B
    CARRY_MISSING_TARGET = "CARRY_MISSING_TARGET"  # 规则 B
    CAMERA_PATH_MISSING = "CAMERA_PATH_MISSING"    # 规则 C
    MORPH_PATH_MISSING = "MORPH_PATH_MISSING"      # 规则 D
    STATIC_REPLACEMENT = "STATIC_REPLACEMENT"      # 规则 E
    ANCHOR_JUMP = "ANCHOR_JUMP"                     # 规则 F
    NO_CONTINUITY_DECLARED = "NO_CONTINUITY_DECLARED"  # 相邻场景零声明


class ContinuityIssue(BaseModel):
    """§13 issue 字段指纹：sceneId / fromSceneId / issueCode / message / recoverable。"""
    scene_id: str
    from_scene_id: Optional[str] = None
    issue_code: ContinuityIssueCode
    message: str
    recoverable: bool = True


class ContinuityReport(BaseModel):
    """§13 输出：只有 PASS / WARNING / REJECT / UNKNOWN，无分数。"""
    status: str = "PASS"  # PASS | WARNING | REJECT | UNKNOWN
    issues: List[ContinuityIssue] = Field(default_factory=list)

    def add(self, issue: ContinuityIssue) -> None:
        if issue.issue_code == ContinuityIssueCode.HARD_CUT_OK:
            pass  # 规则 A 合法，不降级
        elif issue.issue_code in (
            ContinuityIssueCode.CARRY_MISSING_SOURCE,
            ContinuityIssueCode.CARRY_MISSING_TARGET,
            ContinuityIssueCode.CAMERA_PATH_MISSING,
            ContinuityIssueCode.MORPH_PATH_MISSING,
        ):
            self.status = "REJECT" if self.status != "REJECT" else self.status
        else:
            if self.status == "PASS":
                self.status = "WARNING"
        if issue.issue_code != ContinuityIssueCode.HARD_CUT_OK:
            self.issues.append(issue)


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------

#: 相机类延续类型（规则 C：cameraPath 必填）
_CAMERA_CONTINUITY_TYPES = (ContinuityType.zoom_through, ContinuityType.camera_dive,
                            ContinuityType.pan_follow)

#: 规则 E 静态视觉块判定：graphic 类 LayerType（无运动的视觉块）
_GRAPHIC_LAYER_TYPES = {"graphic", "pip", "lower_third", "data_viz", "lottie"}


def _link_between(ir: CompositionIR, from_id: str, to_id: str) -> Optional[ContinuityLink]:
    for link in ir.continuity_links:
        if link.from_scene_id == from_id and link.to_scene_id == to_id:
            return link
    return None


def _scene_is_static_graphic(scene) -> bool:
    """规则 E：场景全部层是静态 graphic 类（无 camera/无运动层）。"""
    layers = list(scene.layers)
    if not layers:
        return False
    return all((l.type.value if hasattr(l.type, "value") else str(l.type)) in _GRAPHIC_LAYER_TYPES
               for l in layers)


def _check_pair(ir: CompositionIR, from_scene, to_scene, report: ContinuityReport) -> None:
    """§13 规则 A-F：逐对相邻场景判定。"""
    link = _link_between(ir, from_scene.id, to_scene.id)
    if link is None:
        # 未声明任何延续：静态视觉块连续替换 → 规则 E（PPT Detector 主判定）
        if _scene_is_static_graphic(from_scene) and _scene_is_static_graphic(to_scene):
            report.add(ContinuityIssue(
                scene_id=to_scene.id, from_scene_id=from_scene.id,
                issue_code=ContinuityIssueCode.STATIC_REPLACEMENT,
                message=(f"「{from_scene.id}」→「{to_scene.id}」为静态视觉块连续替换，"
                         "无任何空间/元素/镜头延续——PPT 式观感。"
                         "可加 hard_cut 明确为有意切换，或补 carry/相机延续。"),
                recoverable=True))
        elif getattr(to_scene, "continuity_in", None):
            # Scene 级声明了延续但没有 Composition 级 Link → 数据不一致
            report.add(ContinuityIssue(
                scene_id=to_scene.id, from_scene_id=from_scene.id,
                issue_code=ContinuityIssueCode.NO_CONTINUITY_DECLARED,
                message=f"场景 {to_scene.id} 声明了 continuity_in 但 Composition 无对应 Link",
                recoverable=True))
        return
    t = link.type
    # 规则 A：明确 hard cut → 合法
    if t == ContinuityType.hard_cut:
        report.add(ContinuityIssue(
            scene_id=to_scene.id, from_scene_id=from_scene.id,
            issue_code=ContinuityIssueCode.HARD_CUT_OK,
            message=f"「{from_scene.id}」→「{to_scene.id}」声明为有意硬切", recoverable=True))
        return
    # 规则 B：carry 缺 source/target → REJECT
    if t == ContinuityType.carry:
        if not link.source_layer_id:
            report.add(ContinuityIssue(
                scene_id=to_scene.id, from_scene_id=from_scene.id,
                issue_code=ContinuityIssueCode.CARRY_MISSING_SOURCE,
                message=f"carry 延续缺 sourceLayerId（{from_scene.id}→{to_scene.id}）",
                recoverable=True))
        if not link.target_layer_id:
            report.add(ContinuityIssue(
                scene_id=to_scene.id, from_scene_id=from_scene.id,
                issue_code=ContinuityIssueCode.CARRY_MISSING_TARGET,
                message=f"carry 延续缺 targetLayerId（{from_scene.id}→{to_scene.id}）",
                recoverable=True))
        # 规则 F：carry 但锚点跳变（除非有 camera/morph 路径）
        if (link.source_anchor and link.target_anchor
                and link.source_anchor != link.target_anchor
                and not link.camera_path and not link.morph_path):
            report.add(ContinuityIssue(
                scene_id=to_scene.id, from_scene_id=from_scene.id,
                issue_code=ContinuityIssueCode.ANCHOR_JUMP,
                message=(f"carry 元素锚点从 {link.source_anchor.value} 跳到 "
                         f"{link.target_anchor.value}（无相机/形变路径解释）"),
                recoverable=True))
        return
    # 规则 C：camera 类延续缺 cameraPath → REJECT
    if t in _CAMERA_CONTINUITY_TYPES and not link.camera_path:
        report.add(ContinuityIssue(
            scene_id=to_scene.id, from_scene_id=from_scene.id,
            issue_code=ContinuityIssueCode.CAMERA_PATH_MISSING,
            message=f"{t.value} 延续缺 cameraPath（{from_scene.id}→{to_scene.id}）",
            recoverable=True))
        return
    # 规则 D：morph 缺 morphPath → REJECT
    if t == ContinuityType.morph and not link.morph_path:
        report.add(ContinuityIssue(
            scene_id=to_scene.id, from_scene_id=from_scene.id,
            issue_code=ContinuityIssueCode.MORPH_PATH_MISSING,
            message=f"morph 延续缺 morphPath（{from_scene.id}→{to_scene.id}）",
            recoverable=True))
        return
    # 其余类型（materialize/gather）有合法 Link → PASS


def verify_composition_continuity(ir: CompositionIR) -> ContinuityReport:
    """§13 主入口：verify_composition_continuity(ir) → ContinuityReport。

    只检查相邻 Scene 的连续关系合法性；不评分、不判断「好不好看」。
    空场景/单场景 → PASS（无相邻对，UNKNOWN 不滥用）。
    """
    report = ContinuityReport()
    scenes = list(ir.scenes)
    if len(scenes) < 2:
        report.status = "PASS"
        return report
    for a, b in zip(scenes, scenes[1:]):
        _check_pair(ir, a, b, report)
    return report
