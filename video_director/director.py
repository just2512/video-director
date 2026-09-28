"""MPT 吸收方案 §4/§7/§61/§62/§92：Director Agent —— 系统真正的 AI 大脑。

Director 收到用户剪辑要求，第一件事不是回答，而是：
    1. 读 Capability Manifest（§5）          system.capabilities.list
    2. 读 Project Context（§6）              system.context.inspect
    3. 规划 Tool Call 序列（§7）
    4. 逐条执行 + Server Validation + verify（§59）
    5. 继续下一能力，直到完成（§92 最小可用闭环）

与 proposal.py（§49 改稿四态）分工：proposal 是"自然语言→编辑补丁"的
改稿通道；Director 是更上层的"目标→能力编排"控制中枢，可把
revision.propose/apply 当作其工具之一。

规划：LLM 可用→LLM 出 tool 序列；不可用→规则兜底（覆盖 §92 最小闭环
与 §60 的常见指令），诚实标注 source。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from video_director import project_context, tool_executor


class DirectorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def _llm_plan(instruction: str, context: dict) -> Optional[List[dict]]:
    """LLM 出 tool 序列（§61：不只说"可以帮你"，而是提出具体计划）。"""
    from video_director.llm_provider import get_llm
    provider = get_llm()
    if provider is None:
        return None

    caps = "\n".join(_edit_tool_prompt_lines())
    system = (
        "你是视频创意系统的 Director。把用户的自然语言剪辑要求转成能力调用序列。"
        "只输出 JSON 数组，不要解释。每个元素："
        '{"tool": "<capability id>", "targetId": "<场景id 或 空>", '
        '"payload": {...}, "reason": "一句话理由"}'
        "\n可用能力（只能从下面选，payload 字段必须严格按每条能力列的 payload 字段/取值写，"
        "不要发明字段名或取值）：\n" + caps +
        "\n如果指令无法定位到可执行能力，返回 [{\"tool\": \"MISSING_TARGET\"}]。"
        "不要发明不存在的 capability id。"
    )
    ctx_desc = (
        f"项目时长 {context['project']['duration']}s，画幅 {context['project']['ratio']}，"
        f"{context['timeline']['scene_count']} 个场景；"
        f"当前音轨 {context['timeline']['tracks']}；"
        f"activeFeatures {context['activeFeatures']}；"
        f"可用 provider {context['providers']}"
    )
    try:
        raw = provider.generate(f"项目状态：{ctx_desc}\n\n用户要求：{instruction}", system=system)
    except Exception as exc:
        logger.warning(f"director LLM 规划失败: {exc}")
        return None

    import re
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    import json as _json
    try:
        plan = _json.loads(text[start:end + 1])
    except _json.JSONDecodeError:
        return None
    if not isinstance(plan, list):
        return None
    out = []
    for p in plan:
        if not isinstance(p, dict) or "tool" not in p:
            continue
        if p["tool"] == "MISSING_TARGET":
            return [{"tool": "MISSING_TARGET", "payload": {}, "reason": "无法定位"}]
        out.append({"tool": p["tool"], "targetId": p.get("targetId"),
                    "payload": p.get("payload") or {}, "reason": p.get("reason", "")})
    return out or None


#: 规则兜底可用的编辑能力子集（无 LLM 时的诚实降级，覆盖 §92 最小闭环）
_GEN_EDIT_TOOLS = {
    "system.capabilities.list", "system.capabilities.describe", "system.context.inspect",
    "project.inspect", "timeline.query", "timeline.select", "timeline.trim",
    "timeline.move", "timeline.split", "timeline.delete", "timeline.duplicate",
    "timeline.replace", "layer.add", "layer.update", "layer.remove",
    "asset.search", "asset.add_to_timeline", "asset.replace",
    "audio.original.preserve", "audio.original.mute", "audio.original.volume",
    "audio.original.duck", "tts.list_voices", "subtitle.update_style",
    "music.volume", "music.duck", "music.add", "music.remove",
    "watermark.add", "watermark.update", "watermark.remove",
    "generation.image", "generation.video", "generation.sfx",
    "motion.list", "motion.apply", "style.apply",
    "render.preflight", "render.draft", "render.final",
    "revision.propose", "revision.preview", "revision.apply", "revision.reject",
    "qc.inspect",
}


def _schema_prompt_line(cap_id: str) -> str:
    """§63 Level-2：单条能力的 payload 字段提示（给 LLM 的 schema 描述）。

    从 validator 的命名 schema 生成人类可读的字段/取值约束，让 LLM 按
    契约写 payload，避免瞎编字段名（如 watermark 写 position 而非 anchor）。
    """
    from video_director.capability_registry.manifest import manifest as _mf
    from video_director.capability_registry.validator import SCHEMAS
    cap = _mf().get(cap_id)
    if cap is None or not cap.inputSchema:
        return f"- {cap_id}（无 payload）"
    schema = SCHEMAS.get(cap.inputSchema)
    if schema is None:
        return f"- {cap_id}"
    field_descs = []
    for fname, spec in schema.fields.items():
        hint = spec.type
        if spec.enum:
            hint = "{" + "|".join(sorted(spec.enum)) + "}"
        req = "必填" if spec.required else "可选"
        field_descs.append(f"{fname}:{hint}({req})")
    desc = cap.description or ""
    return f"- {cap_id} [{desc[:20]}] payload: " + ", ".join(field_descs)


def _edit_tool_prompt_lines() -> list:
    """§63：Level-1 id 摘要 → Level-2 schema 描述（仅编辑域能力展开）。"""
    return [_schema_prompt_line(t) for t in sorted(_GEN_EDIT_TOOLS)]


def _rule_plan(instruction: str, context: dict) -> Optional[List[dict]]:
    """规则兜底规划：覆盖 §92 最小闭环 + §8 的常见剪辑意图。

    不覆盖就返回 None（Director 报 MISSING_TARGET，不编造）。
    """
    plan: List[dict] = []

    # 原声（§18）：保留/静音/音量
    if any(k in instruction for k in ("保留原声", "保留现场", "留着原声", "保留原始声音")):
        plan.append({"tool": "audio.original.preserve", "payload": {"mode": "preserve"},
                     "reason": "保留素材原声"})
    if any(k in instruction for k in ("去掉原声", "静音原声", "不要现场声音", "关掉原声")):
        plan.append({"tool": "audio.original.mute", "payload": {"mode": "mute"},
                     "reason": "静音原声"})

    # 水印（§19）：加/去
    if "水印" in instruction:
        if any(k in instruction for k in ("去掉", "移除", "删除")):
            plan.append({"tool": "watermark.remove", "payload": {},
                         "reason": "移除水印"})
        else:
            anchor = "top_right"
            if "右下" in instruction:
                anchor = "bottom_right"
            elif "左下" in instruction:
                anchor = "bottom_left"
            elif "左上" in instruction:
                anchor = "top_left"
            text = _extract_watermark_text(instruction) or "水印"
            plan.append({"tool": "watermark.add",
                         "payload": {"kind": "text", "text": text, "anchor": anchor,
                                     "opacity": 0.75},
                         "reason": f"添加{anchor}水印"})

    # BGM（§34）：音量/关闭/添加
    if any(k in instruction for k in ("BGM", "背景音乐", "配乐", "音乐")):
        if any(k in instruction for k in ("去掉", "关掉", "移除", "不要", "关闭")):
            plan.append({"tool": "music.remove", "payload": {}, "reason": "移除 BGM"})
        elif any(k in instruction for k in ("小", "低", "降", "轻")):
            plan.append({"tool": "music.volume", "payload": {"type": "bgm", "volume": 0.3},
                         "reason": "BGM 调小"})

    # 字幕样式（§三十二）
    if "字幕" in instruction:
        payload = {}
        if any(k in instruction for k in ("白字黑边", "白色字幕", "白字")):
            payload["color"] = "#FFFFFF"
            payload["strokeColor"] = "#000000"
        if "小" in instruction and "小一点" in instruction:
            payload["size"] = 0.8
        if any(k in instruction for k in ("中下", "中下方", "底部", "下方")):
            payload["position"] = "bottom"
        if payload:
            plan.append({"tool": "subtitle.update_style", "payload": payload,
                         "reason": "更新字幕样式"})

    # 速度（§85 CAP-EDIT-006）
    import re as _re
    m = _re.search(r"(第[一二三四五六七八九十\d]+段|这段|这一段).{0,6}(慢|快)\s*(\d+)\s*%", instruction)
    if m:
        pct = int(m.group(3))
        speed = (100 - pct) / 100 if m.group(2) == "慢" else (100 + pct) / 100
        plan.append({"tool": "layer.update", "payload": {"speed": speed, "opacity": None},
                     "reason": f"速度调为 {speed}"})

    # 生成（§30）：把某段生成 AI 视频
    if any(k in instruction for k in ("生成", "AI 视频", "AI视频")):
        m2 = _re.search(r"这一段|这段", instruction)
        if m2 and "视频" in instruction:
            plan.append({"tool": "generation.video",
                         "payload": {"type": "video", "prompt": instruction},
                         "reason": "生成 AI 视频"})

    # 渲染 draft（§92 最小闭环的收尾）
    if any(k in instruction for k in ("草稿", "draft", "Draft", "先渲染", "生成草稿")):
        plan.append({"tool": "render.draft", "payload": {}, "reason": "渲染草稿"})

    return plan or None


def _extract_watermark_text(instruction: str) -> str:
    """从指令提取水印文本（「加 XX 水印」→ XX）。"""
    import re
    m = re.search(r"(?:加|添加|放一个|放个)(.+?)(?:水印|logo|Logo|LOGO)", instruction)
    if m:
        text = m.group(1).strip()
        # 过滤位置词
        for w in ("右上角", "右下角", "左上角", "左下角", "底部", "顶部", "一个", "个"):
            text = text.replace(w, "")
        return text.strip() or ""
    return ""


def direct(project_id: str, instruction: str) -> dict:
    """Director 主入口（§92 最小可用闭环：inspect → plan → execute → verify）。

    返回 {plan, executed, verify_summary, source}。不抛——把每一步结果
    结构化返回，让前端展示"AI 做了什么、验证结果是什么"。
    """
    if not instruction or not instruction.strip():
        raise DirectorError("EMPTY_INSTRUCTION", "指令为空")

    # 1. Inspect project context（§6）
    context = project_context.inspect_project_context(project_id)

    # 2. Plan（LLM 优先，规则兜底）
    plan = _llm_plan(instruction, context)
    source = "llm"
    if not plan:
        plan = _rule_plan(instruction, context)
        source = "rule"
    if not plan:
        raise DirectorError(
            "MISSING_TARGET",
            f"无法从指令解析出可执行能力：{instruction[:60]!r}")

    if plan and plan[0].get("tool") == "MISSING_TARGET":
        raise DirectorError("MISSING_TARGET", "指令目标不明确，请更具体地描述要改哪里")

    # 3. 逐条执行 + verify
    executed = []
    for step in plan:
        res = tool_executor.execute_tool(project_id, step)
        executed.append({"tool": step["tool"], "reason": step.get("reason", ""),
                         **res})

    # 4. 汇总 verify
    all_ok = all(e["ok"] for e in executed)
    return {
        "source": source,
        "context_summary": {
            "duration": context.get("project", {}).get("duration"),
            "ratio": context.get("project", {}).get("ratio"),
            "tracks": context.get("timeline", {}).get("tracks"),
            "active_features": context.get("activeFeatures"),
        },
        "plan": [{"tool": s["tool"], "targetId": s.get("targetId"),
                  "payload": s["payload"], "reason": s.get("reason", "")} for s in plan],
        "executed": executed,
        "verify_ok": all_ok,
    }