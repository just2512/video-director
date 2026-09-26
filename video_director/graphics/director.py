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
#: 场景角色 → Block 绑定（§22 知识六场景 / §23 产品七场景）。
#: role 取方案原文：problem_title / wrong_belief / cause / contrast /
#: process / conclusion（知识）；problem / product_hero / input /
#: processing / output / benefit / cta（产品）。
_ROLE_BINDING = {
    # ---- §22 knowledge：问题标题→错误认知→因果图→对比图→过程动画→结论卡 ----
    ("knowledge", "problem_title"): "kb.title",
    ("knowledge", "wrong_belief"): "kb.quote",
    ("knowledge", "cause"): "kb.cause-effect",
    ("knowledge", "contrast"): "kb.comparison",
    ("knowledge", "process"): "kb.process",
    ("knowledge", "concept"): "kb.concept-card",
    ("knowledge", "statistic"): "kb.statistic",
    ("knowledge", "definition"): "kb.definition",
    ("knowledge", "timeline"): "kb.timeline",
    ("knowledge", "formula"): "kb.formula",
    ("knowledge", "code"): "kb.code",
    ("knowledge", "conclusion"): "kb.conclusion",
    # ---- §23 product：Problem→Product Hero→Input→Processing→Output→Benefit→CTA ----
    ("product", "problem"): "pd.problem",
    ("product", "product_hero"): "pd.hero",
    ("product", "input"): "pd.ui-demo",
    ("product", "processing"): "pd.workflow",
    ("product", "output"): "pd.feature-stack",
    ("product", "benefit"): "pd.metric",
    ("product", "cta"): "pd.cta",
    ("product", "feature"): "pd.feature",
    ("product", "proof"): "pd.proof",
    ("product", "pricing"): "pd.pricing",
}

#: 知识六场景固定结构（§22：整段文案不足六句时的诚实降级序列）
_KNOWLEDGE_ARC = ("problem_title", "wrong_belief", "cause", "contrast", "process", "conclusion")
#: 产品七场景固定结构（§23）
_PRODUCT_ARC = ("problem", "product_hero", "input", "processing", "output", "benefit", "cta")


def _split_sentences(script: str) -> List[str]:
    """文案 → 分句（句号/问号/感叹号/换行/分号）。"""
    raw = re.split(r"[。！？!?；;\n]+", script.strip())
    out = [s.strip() for s in raw if s.strip()]
    if not out:
        raise DirectorError("EMPTY_SCRIPT", "文案为空，无法生成图形方案")
    return out


def _classify_role(text: str, idx: int, total: int, arc: Tuple[str, ...]) -> str:
    """句子 → 场景角色（规则基线，§22/§23 的 arc 语义）。"""
    # 首句/末句永远锚定 arc 两端
    if idx == 0:
        return arc[0]
    if idx == total - 1:
        return arc[-1]
    # 数字句 → statistic/ketric（数字冲击）
    if re.search(r"\d+%|百分之|\d+万|\d+亿|仅\s*\d|\d+\s*个", text):
        return "statistic"
    # 对比语义
    if re.search(r"不是|而是|但|却|相比|反而|其实", text):
        return "contrast" if "contrast" in arc else "feature"
    # 过程语义（先/然后/接着/步骤）
    if re.search(r"首先|先|然后|接着|其次|最后|步骤", text):
        return "process" if "process" in arc else "processing"
    # 因果语义（因为/所以/导致/原因）
    if re.search(r"因为|所以|导致|原因|于是", text):
        return "cause" if "cause" in arc else "input"
    # 数字+场景很多时按 arc 中段轮转（保证结构分布）
    return arc[1 + (idx % max(1, len(arc) - 2))]


def _content_category(content_type: str) -> str:
    """content_type → knowledge | product。

    §33 四入口：知识分享/教程/自由创作 → knowledge 视觉原语；
    产品介绍 → product 视觉原语（§22/§23）。
    """
    if content_type in ("product_video", "product", "advertisement"):
        return "product"
    return "knowledge"


def _llm_plan(script: str, content_type: str, cat: str) -> Optional[List[dict]]:
    """§13：LLM 输出 Graphic Plan（结构化 JSON），失败/未配置返回 None（规则兜底）。

    LLM 只产出 [{text, role}] 结构（知识/角色语义分句），块绑定仍由确定性
    _ROLE_BINDING 完成——AI 出意图，系统执行（禁 AI 直接出 HTML/块名）。
    """
    try:
        from video_director import llm_provider
        llm = llm_provider.get_llm()
        if llm is None:
            return None
        arc_roles = list(_PRODUCT_ARC) if cat == "product" else list(_KNOWLEDGE_ARC)
        extra = ("statistic,contrast,cause,process,concept,definition,timeline,formula,code"
                 if cat == "knowledge"
                 else "problem,product_hero,input,processing,output,benefit,cta,feature,proof,pricing")
        prompt = (
            "你是分镜规划器。把下面的文案切成若干场景，每场景输出一个 JSON 对象："
            '{"text": "该场景展示的原文句", "role": "场景角色"}。'
            f"角色只能从这些里选：{','.join(arc_roles)},{extra}。"
            "第一场景必须用叙事开场的角色，最后一个必须是收尾角色。"
            "只输出 JSON 数组，不要任何其它文字。\n\n文案：\n" + script
        )
        raw = llm.generate(prompt)
        if not raw:
            return None
        # 提取 JSON 数组（容忍 markdown 代码围栏）
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return None
        import json as _json
        arr = _json.loads(m.group(0))
        out = []
        for it in arr:
            if isinstance(it, dict) and it.get("text"):
                out.append({"text": str(it["text"]).strip(),
                            "role": str(it.get("role") or "").strip()})
        return out or None
    except Exception:
        return None


def build_graphic_plan(
    script: str,
    content_type: str = "knowledge",
    style_id: str = "editorial",
    scene_duration: float = 3.0,
    target_duration: Optional[float] = None,
    width: int = 1080,
    height: int = 1920,
    fps: float = 30.0,
) -> dict:
    """纯文案 → GraphicPlan（结构化分镜，供前端展示 + 后续编译复用）。

    §13：LLM 可用时产出语义分句+角色（Graphic Plan 的 intent 层），
    LLM 不可用/失败 → 规则分句兜底（零成本起步）。块绑定始终由确定性
    _ROLE_BINDING 完成（AI 出意图，系统执行）。

    返回 {scene_count, total_duration, scenes:[{id,role,text,duration,block_id}]}
    这是"可编辑方案"（方案 §34 Creative Plan Review），不是最终产物。
    """
    cat = _content_category(content_type)
    arc = _PRODUCT_ARC if cat == "product" else _KNOWLEDGE_ARC
    llm_scenes = _llm_plan(script, content_type, cat)
    if llm_scenes:
        sentences_roles = [(s["text"], s.get("role")) for s in llm_scenes]
    else:
        sentences_roles = [(s, None) for s in _split_sentences(script)]
    n = len(sentences_roles)
    if n == 0:
        raise DirectorError("EMPTY_SCRIPT", "文案为空，无法生成图形方案")
    # 目标总时长（§33「时长 30s」）：按句数均分；上限取 catalog 块的
    # 绝对最大可停留时长（硬约束优先于时长意图——块越界编译会诚实失败）
    from video_director.graphics import blocks as _blocks_mod
    max_stay = max(b.duration_max for b in _blocks_mod.all_blocks())
    if target_duration and target_duration > 0:
        per = max(2.0, min(float(max_stay), float(target_duration) / n))
    else:
        per = scene_duration
    plan_scenes = []
    for i, (s, role_in) in enumerate(sentences_roles):
        role = role_in if role_in and (cat, role_in) in _ROLE_BINDING else _classify_role(s, i, n, arc)
        block_id = _ROLE_BINDING.get((cat, role))
        if block_id is None:
            # LLM 给的角色在本域无绑定 → 回退 arc 语义（诚实映射）
            fallback_role = arc[1 + (i % max(1, len(arc) - 2))] if len(arc) > 2 else arc[0]
            block_id = _ROLE_BINDING[(cat, fallback_role)]
            role = fallback_role
        # 长句 → 更长停留（每 20 字约 +0.5s），夹在该块自身上限内
        blk = _blocks_mod.get_block(block_id)
        dur = max(2.0, min(blk.duration_max, per + max(0, (len(s) - 20) // 20 * 0.5)))
        plan_scenes.append({
            "id": f"scene-{i + 1:02d}",
            "role": role,
            "text": s,
            "duration": round(dur, 2),
            "block_id": block_id,
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


def _slot_values_for(block_id: str, text: str) -> dict:
    """句子 → 该 block 的真实槽位值（按 §27/§28 各块 slots 定义）。"""
    if block_id == "kb.statistic":
        m = re.search(r"(\d+(?:\.\d+)?%?)", text)
        return {"value": m.group(1) if m else text, "title": text, "body": ""}
    if block_id == "kb.formula":
        m = re.search(r"([A-Za-zα-ωΑ-Ω0-9_=+\-*/^().]{2,})", text)
        return {"value": m.group(1) if m else text, "term": text}
    if block_id in ("kb.comparison", "kb.before-after"):
        parts = re.split(r"不是|而是|但|却|相比", text, maxsplit=1)
        return {"title": text[:16],
                "left": parts[0].strip(),
                "right": (parts[1].strip() if len(parts) > 1 else "…")}
    if block_id == "kb.cause-effect":
        parts = re.split(r"因为|所以|导致|于是|原因", text, maxsplit=1)
        return {"cause": parts[0].strip(), "effect": (parts[1].strip() if len(parts) > 1 else text)}
    if block_id == "kb.quote":
        return {"quote": text, "source": ""}
    if block_id == "kb.code":
        return {"code": text}
    if block_id == "pd.metric" or block_id == "pd.proof":
        # 「1,250,000|用户」式；无 | 时数字|原文
        m = re.search(r"(\d[\d,.]*\s*(?:%|万|亿|个|人|次|倍)?)", text)
        return {"items": (m.group(1) + "|" + text[:10]) if m else text}
    if block_id in ("kb.process", "kb.timeline", "kb.hierarchy", "kb.matrix",
                    "kb.ranking", "kb.checklist", "kb.flowchart",
                    "kb.relationship-map", "pd.feature-stack", "pd.workflow",
                    "pd.screenshot-tour", "pd.integration"):
        return {"items": text}
    # 双栏类（pd.feature-comparison / pd.pricing / pd.before-after）
    if block_id in ("pd.feature-comparison", "pd.pricing"):
        parts = re.split(r"对比|vs|VS|相比|比", text, maxsplit=1)
        return {"title": text[:12], "left": parts[0].strip(),
                "right": (parts[1].strip() if len(parts) > 1 else "…")}
    # 默认 title/body 型
    return {"title": text, "body": ""}


def instantiate_graphic_program(plan: dict, program_id: Optional[str] = None) -> GraphicProgram:
    """GraphicPlan → GraphicProgram（GFX-002，可被 compiler/render 消费）。

    motion 从 Block 定义读取（§16 motion binding 在块上，非 plan 字段）。
    """
    from video_director.graphics import blocks as blocks_mod

    scenes: List[GraphicScene] = []
    cursor = 0.0
    for ps in plan["scenes"]:
        block = blocks_mod.get_block(ps["block_id"])  # 校验块存在（compiler 前的诚实失败点）
        slot_values = _slot_values_for(ps["block_id"], ps["text"])
        # §34 用户覆盖优先：motion_id（null=跟随块默认）/ layout / source
        motion = ps.get("motion_id") or block.motion
        layout = ps.get("layout")
        if layout in ("center", "left", "split"):
            slot_values["layout"] = layout  # blocks 渲染基件消费（排版取向）
        source = ps.get("source") or "Generated Graphic"
        slot_values["source"] = source
        scenes.append(GraphicScene(
            id=ps["id"], role=ps["role"], start=cursor,
            duration=ps["duration"], block_id=ps["block_id"],
            slot_values=slot_values, motion_id=motion,
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