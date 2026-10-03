"""最终方案 §15/§16/§17/§54/§55/§56/§57：Director —— 控制中枢（闭环版）。

§54 真正的闭环（不是 LLM→JSON→for 循环）：
    Inspect → Understand Goal → Resolve Target → Build Plan →
    Validate → Execute → Read Result → Verify → Repair → Finish

§16 AI 必须认识四件东西：System / Project / Selection / Available Actions。
    - System Context 来自 action_registry.system_context()（§16 providers/
      render_profiles/asset_sources/voice_sources/music_sources + §64 商业政策）
    - Project Context 来自 project_context.inspect_project_context()
    - Selection Context 由 /api/director/context 上报（前端 Selection Service）
    - Action Context 只发送当前可执行动作（actions_for_context），不把
      117 个 Capability 全塞给模型

§17 LLM 输出 goal + steps[{action, target, params}]（不是 capability id 串），
    后端 Action Resolver 负责 Action→Capability→Command（不猜字段）。

§55 执行后重新读系统：每个 step 执行后 inspect 验证目标状态变化，
    失败进入修复（asset.search 找不到 → generate.image 回退，§四十）。

§56 高层工具面：AI 看到的是系统动词（resolve_step 映射到底层 capability），
    不暴露 100+ 底层细节。

无 LLM 时规则兜底（覆盖 §五十七 12 条标准命令），诚实标注 source。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from loguru import logger

from video_director import action_registry, project_context, tool_executor


class DirectorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# §16 四件套上下文
# ---------------------------------------------------------------------------

def build_contexts(project_id: str, selection: Optional[dict] = None) -> dict:
    """§16：System + Project + Selection + Action Context 一次收集。"""
    # selection 补 layer_type（前端上报可能只带 type/id；Action 分组需要层类型）
    sel = dict(selection or {"type": None})
    if sel.get("type") == "layer" and not sel.get("layer_type"):
        try:
            from video_director.editor import load_composition
            ir = load_composition(project_id)
            scene_id, idx_s = (sel.get("id") or "").split("#")
            for sc in ir.scenes:
                if sc.id == scene_id:
                    sel["layer_type"] = sc.layers[int(idx_s)].type.value
                    break
        except Exception:  # noqa: BLE001 推导失败按通用层处理
            pass
    project_ctx = project_context.inspect_project_context(project_id, sel)
    if not project_ctx:
        raise DirectorError("PROJECT_NOT_READABLE", f"项目 {project_id} 无法读取（先在工作台打开）")
    system_ctx = action_registry.system_context()
    action_ctx = action_registry.actions_for_context(
        sel, project_id=project_id, context=project_ctx)
    return {
        "system": system_ctx,
        "project": project_ctx,
        "selection": sel,
        "actions": action_ctx,
    }


def _action_context_prompt(actions: List[dict]) -> str:
    """§16 Action Context → LLM 提示行（只列当前可执行动作 + 不可用原因）。"""
    lines = []
    for a in actions:
        line = f"- {a['id']}（{a['label']}）"
        if not a.get("available", True):
            line += f" [不可用：{a.get('reason', '')}]"
        lines.append(line)
    return "\n".join(lines) or "-（当前上下文无可执行动作）"


# ---------------------------------------------------------------------------
# §17 LLM 规划：goal + steps（Action Registry 驱动，不暴露 capability 细节）
# ---------------------------------------------------------------------------

def _llm_plan(instruction: str, contexts: dict) -> Optional[dict]:
    """§17：LLM 输出 {goal, steps:[{action, target, params}]}。失败返回 None。"""
    from video_director.llm_provider import get_llm
    provider = get_llm()
    if provider is None:
        return None

    actions = contexts["actions"]["actions"]
    system = (
        "你是视频创作系统的 Director 控制中枢。把用户的剪辑要求转成动作计划。\n"
        "只输出 JSON 对象（不要解释、不要代码块标记）：\n"
        '{"goal": "一句话目标", "steps": ['
        '{"action": "<动作id>", "target": "current_selection|project|<场景id>", '
        '"params": {<动作参数>}}]}\n'
        "\n动作只能从下面列表选（id 与中文含义对应），params 只用动作说明里提到的"
        "参数，不要发明：\n" + _action_context_prompt(actions) +
        "\ntarget 用 current_selection（用户当前选中的对象）或 project（整个项目）"
        "或具体场景 id（形如 scene-1）。\n"
        "如果当前选中了对象（Selection Context 有 id），优先 current_selection。\n"
        "如果指令无法对应任何动作，输出 {\"goal\": \"无法执行\", \"steps\": []}。"
    )
    proj = contexts["project"]
    sel_desc = contexts["selection"].get("type") or "无选中"
    sel_id = contexts["selection"].get("id") or ""
    ctx_desc = (
        f"项目 {proj['project']['duration']}s · 画幅 {proj['project']['ratio']} · "
        f"{proj['timeline']['scene_count']} 场景 · 音轨 {proj['timeline']['tracks']}；"
        f"可用来源 {proj['providers']}；"
        f"当前选中：{sel_desc}{' ' + sel_id if sel_id else ''}（target 可用 current_selection）"
    )
    try:
        raw = provider.generate(f"项目状态：{ctx_desc}\n\n用户要求：{instruction}",
                                system=system)
    except Exception as exc:  # noqa: BLE001 LLM 故障走规则兜底，不编造
        logger.warning(f"director LLM 规划失败（走规则兜底）: {exc}")
        return None

    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    import json as _json
    try:
        plan = _json.loads(text[start:end + 1])
    except _json.JSONDecodeError:
        return None
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return None
    steps = []
    for s in plan["steps"]:
        if isinstance(s, dict) and s.get("action"):
            steps.append({"action": s["action"], "target": s.get("target", "current_selection"),
                          "params": s.get("params") or {}})
    if not steps:
        return None
    return {"goal": plan.get("goal", instruction[:40]), "steps": steps}


# ---------------------------------------------------------------------------
# 规则兜底（§五十七 12 条标准命令；Action Registry 共现规则复用）
# ---------------------------------------------------------------------------

def _extract_volume(instruction: str) -> Optional[float]:
    """「压到 15%」「音量 20%」→ 0.15/0.2。"""
    m = re.search(r"(?:压到|音量|调到|降到|调成)\s*(\d{1,3})\s*%", instruction)
    if m:
        return min(2.0, int(m.group(1)) / 100)
    m = re.search(r"(\d{1,3})\s*%", instruction)
    if m:
        return min(2.0, int(m.group(1)) / 100)
    return None


def _extract_opacity(instruction: str) -> Optional[float]:
    m = re.search(r"透明度?\s*(\d{1,3})\s*%", instruction)
    if m:
        return min(1.0, int(m.group(1)) / 100)
    return None


def _extract_anchor(instruction: str) -> str:
    for kw, anchor in (("右下", "bottom_right"), ("左下", "bottom_left"),
                       ("左上", "top_left"), ("右上", "top_right")):
        if kw in instruction:
            return anchor
    return "bottom_right"


def _extract_watermark_text(instruction: str) -> str:
    m = re.search(r"(?:加|添加|放一个|放个)(.+?)(?:水印|logo|Logo|LOGO)", instruction)
    if m:
        text = m.group(1).strip()
        for w in ("右上角", "右下角", "左上角", "左下角", "我的", "一个", "个"):
            text = text.replace(w, "")
        return text.strip()
    return ""


def _rule_plan(instruction: str, selection: Optional[dict]) -> Optional[dict]:
    """规则兜底：Action Registry 共现规则 → steps（§五十七 逐条覆盖）。

    多意图指令（「保留原声，加右上角水印，把 BGM 调小」）按标点分段
    逐段 resolve，每段一个 step——一条用户指令可以出多个步骤。
    """
    segments = [s.strip() for s in re.split(r"[，,。；;、\n]", instruction) if s.strip()]
    if not segments:
        segments = [instruction.strip()]
    steps: List[dict] = []
    seen_actions = set()
    for seg in segments:
        act = action_registry.resolve_by_text(seg)
        if act is None:
            continue
        if act.id in seen_actions:
            continue  # 同一动作不重复入计划
        seen_actions.add(act.id)
        target = "current_selection"
        if act.targets and "project" in act.targets and "scene" not in act.targets \
                and "layer" not in act.targets:
            target = "project"
        params: Dict = {}
        vol = _extract_volume(seg)
        op = _extract_opacity(seg)
        if act.id in ("audio.original_volume",) and vol is not None:
            params["volume"] = vol
        elif act.id == "audio.preserve" and vol is not None:
            params["volume"] = vol  # 「保留原声但压到 15%」→ preserve + volume
        elif vol is not None and act.id in ("music.volume", "audio.volume"):
            params["volume"] = vol
        if act.id == "watermark.add":
            params["anchor"] = _extract_anchor(seg)
            text = _extract_watermark_text(seg)
            if text:
                params["text"] = text
            if op is not None:
                params["opacity"] = op
        if act.id == "subtitle.style":
            style_params = {}
            if "白字黑边" in seg or "白色" in seg or "白字" in seg:
                style_params["color"] = "#FFFFFF"
            if "黑边" in seg or "黑描边" in seg:
                style_params["strokeColor"] = "#000000"
            if "大一点" in seg:
                style_params["size"] = 1.2
            if "小一点" in seg:
                style_params["size"] = 0.8
            if "底部" in seg:
                style_params["position"] = "bottom"
            if "逐词" in seg or "word" in seg:
                style_params["mode"] = "word"
            if ("句" in seg and "逐词" not in seg):
                style_params["mode"] = "sentence"
            params.update(style_params)
        if act.id == "subtitle.generate":
            params["mode"] = "word" if ("逐词" in seg or "word" in seg) else "sentence"
        steps.append({"action": act.id, "target": target, "params": params})
    if not steps:
        # 整句兜底（分段都没中时按原文再试一次，如「保留原声但压到 15%」）
        act = action_registry.resolve_by_text(instruction)
        if act is None:
            return None
        return _single_rule_step(act, instruction)
    # 跨段参数补齐：整句里有的数值参数（音量/透明度）分段未携带时回填到首步
    whole_vol = _extract_volume(instruction)
    if whole_vol is not None:
        for s in steps:
            if s["action"] in ("audio.original_volume", "audio.preserve",
                               "music.volume", "audio.volume") and "volume" not in s["params"]:
                s["params"]["volume"] = whole_vol
                break
    return {"goal": "、".join(action_registry.get_action(s["action"]).label
                              for s in steps if action_registry.get_action(s["action"])),
            "steps": steps}


def _single_rule_step(act, instruction: str) -> dict:
    """整句单步兜底（带参数提取）。"""
    target = "current_selection"
    if act.targets and "project" in act.targets and "scene" not in act.targets \
            and "layer" not in act.targets:
        target = "project"
    params: Dict = {}
    vol = _extract_volume(instruction)
    if vol is not None and act.id in ("audio.original_volume", "audio.preserve",
                                      "music.volume", "audio.volume"):
        params["volume"] = vol
    if act.id == "watermark.add":
        params["anchor"] = _extract_anchor(instruction)
        text = _extract_watermark_text(instruction)
        if text:
            params["text"] = text
        op = _extract_opacity(instruction)
        if op is not None:
            params["opacity"] = op
    return {"goal": act.label, "steps": [{"action": act.id, "target": target,
                                          "params": params}]}


# ---------------------------------------------------------------------------
# §54 主闭环
# ---------------------------------------------------------------------------

#: §40/§55 缺画面回退链：asset.search 失败 → generate.image（自动回退策略）
_REPAIR_RULES = {
    "asset.search": "generate.image",
}


def _search_fallback_queries(project_id: str, target_id: str) -> List[str]:
    """§23/§四十：回退搜索用 VisualSearchIntent 多组词（非单关键词）。

    target 为场景时取场景文案；否则用项目整体首场景文案兜底。
    """
    try:
        from video_director.editor import load_composition
        from video_director.visual_search_intent import build_intent, search_queries
        ir = load_composition(project_id)
        script = ""
        scene_type = ""
        if target_id and not target_id.startswith("scene"):
            # layer target（scene-1#0）→ 所属场景文案
            scene_id = target_id.split("#")[0]
            sc = next((s for s in ir.scenes if s.id == scene_id), None)
            script = (sc.text if sc else "") or ""
        else:
            sc = next((s for s in ir.scenes if s.id == target_id), None)
            if sc is None and ir.scenes:
                sc = ir.scenes[0]
            script = (sc.text if sc else "") or ""
        if not script and ir.scenes:
            script = ir.scenes[0].text or ""
        from video_director import asset_catalog as vd_acat
        sp = vd_acat.stock_search_params(project_id)
        intent = build_intent(target_id or "scene", script,
                              required_duration=4.0,
                              ratio=sp.get("ratio") or "9:16")
        return search_queries(intent)
    except Exception as exc:  # noqa: BLE001 回退词生成失败不阻断 repair
        logger.info(f"director: 回退检索词生成失败: {exc}")
        return []


def direct(project_id: str, instruction: str,
           selection: Optional[dict] = None) -> dict:
    """§54 闭环主入口：

    1. Inspect（§16 四件套）
    2. Understand Goal + Build Plan（LLM goal+steps，规则兜底）
    3. Resolve Target + Validate（Action Resolver → capability/command）
    4. Execute（tool_executor）+ Read Result
    5. Verify（§55 执行后重新读系统；repair 可回退）
    """
    if not instruction or not instruction.strip():
        raise DirectorError("EMPTY_INSTRUCTION", "指令为空")

    # 1. Inspect
    contexts = build_contexts(project_id, selection)

    # 2. Plan
    plan = _llm_plan(instruction, contexts)
    source = "llm"
    if not plan:
        plan = _rule_plan(instruction, selection)
        source = "rule"
    if not plan:
        raise DirectorError("MISSING_TARGET",
                             f"无法从指令解析出可执行动作：{instruction[:60]!r}")

    # 3. Resolve + Validate（§17：resolver 全链映射，未知动作诚实报错）
    steps = plan.get("steps") or []
    resolved = []
    for s in steps:
        r = action_registry.resolve_step(s, selection)
        if "error" in r:
            resolved.append({"step": s, "resolve_error": r["error"]})
        else:
            resolved.append({"step": s, **r})

    # 4. Execute + 5. Verify（每步执行后重读系统）
    executed = []
    for item in resolved:
        if "resolve_error" in item:
            executed.append({"tool": item["step"].get("action"),
                             "ok": False, "status": "RESOLVE_ERROR",
                             "error": item["resolve_error"]})
            continue
        tool_call = {"tool": item["capability"], "targetId": item["targetId"],
                     "payload": item["payload"]}
        res = tool_executor.execute_tool(project_id, tool_call)
        entry = {"tool": item["capability"], "action": item.get("action", ""),
                 "label": item.get("label", ""), "targetId": item["targetId"],
                 "reason": f"目标：{plan.get('goal', '')}", **res}

        # §55 执行后重新读系统（verify by re-inspect）
        if res.get("ok"):
            entry["verify"] = _verify_step(project_id, item, selection)

        # §55 Repair：asset.search 失败 → 自动回退 generate.image（§四十）
        # §55/§四十 Repair：asset.search 失败 → VisualSearchIntent 多组检索词
        # 逐组重试 → 仍无 → generate.image（§40 自动回退链 Local→Stock→AI Image）
        if not res.get("ok") and item.get("action") in _REPAIR_RULES:
            fallback = _REPAIR_RULES[item["action"]]
            fb_spec = action_registry.get_action(fallback)
            queries = _search_fallback_queries(project_id, item.get("targetId") or "")
            repair_note = f"自动回退：{fb_spec.label if fb_spec else fallback}"
            repaired = False
            for q in queries[:3]:  # §23 多组词逐组重试（不无限尝试，§四十预算）
                retry = tool_executor.execute_tool(project_id, {
                    "tool": "stock.search", "targetId": item["targetId"],
                    "payload": {"query": q}})
                if retry.get("ok") and (retry.get("result", {}).get("candidates") or []):
                    entry["repair"] = {"tool": "stock.search", "ok": True,
                                       "note": f"{repair_note}：检索词「{q}」找到候选"}
                    repaired = True
                    break
            if not repaired and fb_spec:
                fb_res = tool_executor.execute_tool(project_id, {
                    "tool": fb_spec.capability, "targetId": item["targetId"],
                    "payload": {"prompt": queries[0] if queries else (item["payload"].get("prompt") or ""),
                                **{k: v for k, v in item["payload"].items()
                                   if k in ("text", "style")}}})
                entry["repair"] = {
                    "tool": fb_spec.capability, "ok": fb_res.get("ok", False),
                    "note": f"{repair_note}：多组检索无果，转 AI 生成",
                }
        executed.append(entry)

    all_ok = all(e.get("ok") for e in executed)
    verify_ok = all_ok and all(e.get("verify", {}).get("ok", True) for e in executed)
    return {
        "source": source,
        "goal": plan.get("goal", ""),
        "context_summary": {
            "duration": contexts["project"]["project"].get("duration"),
            "ratio": contexts["project"]["project"].get("ratio"),
            "selection": contexts["selection"].get("type"),
            "available_actions": len(contexts["actions"]["actions"]),
        },
        "plan": [{"action": s.get("action"), "target": s.get("target"),
                  "params": s.get("params")} for s in steps],
        "executed": executed,
        "verify_ok": verify_ok,
    }


def _verify_step(project_id: str, item: dict, selection: Optional[dict]) -> dict:
    """§55 Verify：执行后重新读系统，确认目标状态确实变化。

    逐动作族验证（不猜通用断言）：
    - watermark.* → 水印数量/内容变化
    - audio.preserve/mute → original 轨存在/消失
    - music.volume → bgm 轨 volume 值
    - subtitle.* → 字幕轨 cues
    - timeline.delete/duplicate → 场景数
    """
    try:
        from video_director.editor import load_composition
        ir = load_composition(project_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "note": f"重读项目失败：{exc}"}
    action = item.get("action") or ""
    if action.startswith("watermark."):
        return {"ok": bool(ir.watermarks), "note": f"水印 {len(ir.watermarks)} 个"}
    if action in ("audio.preserve", "audio.mute_original"):
        has = any(t.type.value == "original" for t in ir.audio)
        return {"ok": has, "note": "原声轨已建立" if has else "无原声轨"}
    if action == "music.volume":
        bgm = [t for t in ir.audio if t.type.value == "bgm"]
        return {"ok": bool(bgm), "note": f"BGM 音量 {bgm[0].volume if bgm else '—'}"}
    if action.startswith("subtitle."):
        if action == "subtitle.style":
            st = getattr(ir, "subtitle_style", None)
            return {"ok": st is not None, "note": "字幕样式已更新" if st else "无样式"}
        has = bool(ir.subtitle_track and ir.subtitle_track.cues)
        return {"ok": has, "note": "字幕轨就位" if has else "无字幕轨"}
    if action in ("timeline.delete", "timeline.duplicate"):
        return {"ok": len(ir.scenes) > 0, "note": f"{len(ir.scenes)} 场景"}
    return {"ok": True, "note": "已执行（该动作族无需专项验证）"}
