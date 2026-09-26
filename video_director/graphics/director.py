"""Text → Graphic Director（GFX-019/020/021）：纯文案 → 知识/产品动效视频。

方案 §21-23：用户输入纯文案，系统自动产出 Graphic Plan（文案分句 → 语义角色 →
block 绑定 → motion 绑定），编译成 GraphicProgram。不做"AI → arbitrary HTML"；
每一步是规则化的结构化映射（§14 HTML 是执行格式不是 AI 知识格式）。

第一版规则化 Director（零 LLM 也可跑，对齐 Jerry 零成本起步原则）：
    - 文案按句号/换行切分（复用 pipeline.split_script_scenes 语义）
    - 角色推断：首句 hook、末句 conclusion/cta、数字句 statistic、否则 body
    - block 绑定由角色 + content_type 决定；motion 按节奏确定性选择
    - LLM 可用时为增强（后续），规则为兜底基线
"""

from __future__ import annotations

import re
import uuid
from typing import List, Optional

from video_director.graphics.program import GraphicProgram, GraphicScene


class DirectorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


#: block 绑定表（角色 + 内容类型 → block_id + motion）
_ROLE_BINDING = {
    # knowledge（§27 知识动效视觉原语）
    ("knowledge", "hook"): ("kb.title", "graphics.fade-up"),
    ("knowledge", "statistic"): ("kb.statistic", "graphics.scale-punch"),
    ("knowledge", "comparison"): ("kb.comparison", "graphics.rise-soft"),
    ("knowledge", "body"): ("kb.definition", "graphics.fade-up"),
    ("knowledge", "conclusion"): ("kb.conclusion", "graphics.word-relay"),
    # product（§28 产品介绍视觉原语）
    ("product", "hook"): ("pd.hero", "graphics.scale-punch"),
    ("product", "body"): ("pd.feature", "graphics.fade-up"),
    ("product", "conclusion"): ("pd.cta", "graphics.fade-up"),
}


def _split_sentences(script: str) -> List[str]:
    """文案 → 分句（句号/问号/感叹号/换行/分号）。"""
    raw = re.split(r"[。！？!?；;\n]+", script.strip())
    out = [s.strip() for s in raw if s.strip()]
    if not out:
        raise DirectorError("EMPTY_SCRIPT", "文案为空，无法生成图形方案")
    return out


def _classify_role(text: str, idx: int, total: int) -> str:
    """句子 → 语义角色（规则基线）。"""
    if idx == 0:
        return "hook"
    if idx == total - 1:
        return "conclusion"
    # 数字冲击：含百分比/数量/金额 → statistic
    if re.search(r"\d+%|百分之|\d+万|\d+亿|仅\s*\d|\d+\s*个", text):
        return "statistic"
    # 对比：含"不是/而是/但/却/相比/不是...是"
    if re.search(r"不是|而是|但|却|相比|反而|其实", text):
        return "comparison"
    return "body"


def _content_category(content_type: str) -> str:
    """content_type → knowledge | product。"""
    if content_type in ("product_video", "product", "advertisement"):
        return "product"
    return "knowledge"


def build_graphic_plan(
    script: str,
    content_type: str = "knowledge",
    style_id: str = "editorial",
    scene_duration: float = 3.0,
    width: int = 1080,
    height: int = 1920,
    fps: float = 30.0,
) -> dict:
    """纯文案 → GraphicPlan（结构化分镜，供前端展示 + 后续编译复用）。

    返回 {scene_count, total_duration, scenes:[{id,role,text,duration,block_id,motion_id}]}
    这是"可编辑方案"（方案 §34 Creative Plan Review），不是最终产物。
    """
    sentences = _split_sentences(script)
    cat = _content_category(content_type)
    plan_scenes = []
    for i, s in enumerate(sentences):
        role = _classify_role(s, i, len(sentences))
        block_id, motion_id = _ROLE_BINDING.get((cat, role)) or _ROLE_BINDING[(cat, "body")]
        # 长句 → 更长停留（每 20 字约 +0.5s，上限 6s）
        dur = max(2.0, min(6.0, scene_duration + max(0, (len(s) - 20) // 20 * 0.5)))
        plan_scenes.append({
            "id": f"scene-{i + 1:02d}",
            "role": role,
            "text": s,
            "duration": round(dur, 2),
            "block_id": block_id,
            "motion_id": motion_id,
        })
    total = round(sum(s["duration"] for s in plan_scenes), 2)
    return {
        "scene_count": len(plan_scenes),
        "total_duration": total,
        "content_type": content_type,
        "category": cat,
        "style_id": style_id,
        "width": width,
        "height": height,
        "fps": fps,
        "scenes": plan_scenes,
    }


def instantiate_graphic_program(plan: dict, program_id: Optional[str] = None) -> GraphicProgram:
    """GraphicPlan → GraphicProgram（GFX-002，可被 compiler/render 消费）。"""
    scenes: List[GraphicScene] = []
    cursor = 0.0
    for ps in plan["scenes"]:
        # 槽位值：kb/pd block 统一用 title/body（数字块额外 value）
        slot_values = {"title": ps["text"]}
        if ps["block_id"] == "kb.statistic":
            # 抽取首数字作为视觉冲击值，标题/正文保留原文
            m = re.search(r"(\d+(?:\.\d+)?%?)", ps["text"])
            slot_values = {
                "value": m.group(1) if m else ps["text"],
                "title": ps["text"],
                "body": "",
            }
        elif ps["block_id"] == "kb.comparison":
            # 简单二分：以"不是/而是"等切左右
            parts = re.split(r"不是|而是|但|却|相比", ps["text"], maxsplit=1)
            slot_values = {
                "title": ps["text"][:16],
                "left": parts[0].strip(),
                "right": (parts[1].strip() if len(parts) > 1 else "…"),
            }
        else:
            slot_values = {"title": ps["text"], "body": ""}
        scenes.append(GraphicScene(
            id=ps["id"], role=ps["role"], start=cursor,
            duration=ps["duration"], block_id=ps["block_id"],
            slot_values=slot_values, motion_id=ps["motion_id"],
        ))
        cursor += ps["duration"]

    return GraphicProgram(
        id=program_id or f"gfx-{uuid.uuid4().hex[:8]}",
        width=int(plan.get("width", 1080)),
        height=int(plan.get("height", 1920)),
        fps=float(plan.get("fps", 30.0)),
        duration=cursor,
        scenes=scenes,
        style_id=plan.get("style_id", "editorial"),
        variables={},
        provenance_ref="src-own",
    )


def plan_to_html(plan: dict) -> str:
    """GraphicPlan → 完整 HTML（前端 preview 直接 srcdoc 渲染）。"""
    from video_director.graphics.compiler import compile_to_html

    program = instantiate_graphic_program(plan)
    return compile_to_html(program)