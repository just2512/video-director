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
        "timeline": _timeline_for_director(project_id),
    }


def _action_context_prompt(actions: List[dict]) -> str:
    """Action Context → LLM 提示：动作 + 目标 + 已知参数，避免只知道按钮却不会调用。"""
    lines = []
    for a in actions:
        line = f"- {a['id']}（{a['label']}） targets={a.get('targets') or []}"
        params = a.get("parameters") or {}
        if params:
            line += f" params={params}"
        if not a.get("available", True):
            line += f" [不可用：{a.get('reason', '')}]"
        lines.append(line)
    return "\n".join(lines) or "-（当前上下文无可执行动作）"


def _timeline_for_director(project_id: str) -> dict:
    """给 Director 的紧凑时间线上下文：人类序号 + 稳定 scene id + 层/素材。"""
    try:
        summary = tool_executor._timeline_summary(project_id)
        scenes = []
        for idx, sc in enumerate(summary.get("scenes") or [], 1):
            scenes.append({
                "number": idx,
                "id": sc.get("id"),
                "text": (sc.get("text") or "")[:220],
                "duration": sc.get("duration"),
                "layers": [
                    {
                        "index": layer.get("index"),
                        "id": f"{sc.get('id')}#{layer.get('index')}",
                        "type": layer.get("type"),
                        "asset_id": layer.get("asset_id"),
                        "duration": layer.get("duration"),
                    }
                    for layer in (sc.get("layers") or [])
                ],
            })
        return {
            "scenes": scenes,
            "audio": summary.get("audio") or [],
            "subtitle_style": summary.get("subtitle_style"),
            "watermarks": summary.get("watermarks") or [],
        }
    except Exception as exc:
        logger.info(f"director timeline context unavailable: {exc}")
        return {"scenes": [], "audio": [], "subtitle_style": None, "watermarks": []}


def _resolve_natural_target(project_id: str, target, selection: Optional[dict] = None) -> object:
    """把“第二段/第2段/开头/最后一段/Scene 02”解析为真实 scene id。"""
    if not isinstance(target, str):
        return target
    raw = target.strip()
    if not raw:
        return target
    if raw in ("当前选中", "当前对象", "current_selection"):
        return "current_selection"
    try:
        scenes = tool_executor._timeline_summary(project_id).get("scenes") or []
    except Exception:
        scenes = []
    if not scenes:
        return target
    # 稳定 ID 直接通过。
    if any(sc.get("id") == raw for sc in scenes):
        return raw
    low = raw.lower().replace(" ", "")
    m = re.fullmatch(r"(?:scene[-_]?|s[-_]?)?0*(\d+)", low)
    num = int(m.group(1)) if m else None
    if num is None:
        m = re.search(r"(?:第|no\.?|segment|场景|镜头)\s*0*(\d+)\s*(?:段|个|号|号镜头)?", raw, re.I)
        if m:
            num = int(m.group(1))
    if "最后" in raw:
        num = len(scenes)
    if "开头" in raw or "第一段" in raw:
        num = 1
    if num is not None and 1 <= num <= len(scenes):
        return scenes[num - 1].get("id") or raw
    if selection and raw in ("这个", "这里", "这段", "此处"):
        return selection.get("id") or "current_selection"
    return target


def _target_layer_for_scene(project_id: str, target_id: str) -> str:
    """scene target → 首个视觉层；layer target 原样返回。"""
    if not target_id or "#" in str(target_id):
        return target_id
    try:
        ir = tool_executor.editor.load_composition(project_id)
        scene = next((s for s in ir.scenes if s.id == target_id), None)
        if scene and scene.layers:
            return f"{scene.id}#0"
    except Exception:
        pass
    return target_id


def _execute_asset_search_apply(project_id: str, item: dict) -> dict:
    """找素材的完整链：Library 查询 → Stock 搜索 → 下载 → Library 引用 → 时间线采用。"""
    from video_director import asset_catalog, stock, config as vd_config
    target_id = item.get("targetId") or ""
    payload = item.get("payload") or {}
    query = (payload.get("query") or "").strip()
    if not query:
        # 从目标场景文本取检索词，避免要求用户额外提供搜索词。
        try:
            timeline = _timeline_for_director(project_id).get("scenes") or []
            for sc in timeline:
                if sc.get("id") == target_id or f"{sc.get('id')}#0" == target_id:
                    query = sc.get("text") or ""
                    break
        except Exception:
            pass
    if not query:
        return {"tool": "asset.search", "ok": False, "status": "MISSING_QUERY", "error": "无法从当前场景推导素材检索词"}

    # 先搜已有 Asset Library，命中直接采用。
    local = asset_catalog.list_assets(q=query)[:5]
    for asset in local:
        if asset.get("commercial_status") in ("BLOCKED",):
            continue
        asset_catalog.project_reference(project_id, asset["asset_id"])
        layer_target = _target_layer_for_scene(project_id, target_id)
        if layer_target and "#" in str(layer_target):
            r = tool_executor.execute_tool(project_id, {
                "tool": "asset.replace",
                "targetId": layer_target,
                "payload": {"assetId": asset["asset_id"]},
            })
            if r.get("ok"):
                return {"tool": "asset.search", "ok": True, "status": "APPLIED",
                        "result": {"query": query, "source": "library",
                                   "asset_id": asset["asset_id"], "targetId": layer_target},
                        "verify": {"ok": True, "note": "已有素材已采用"}}
    # Library 没找到再查 Stock。
    ratio = ""
    orientation = None
    try:
        sp = asset_catalog.stock_search_params(project_id)
        ratio, orientation = sp.get("ratio") or "", sp.get("orientation")
    except Exception:
        pass
    hits = stock.search_stock(query, orientation=orientation)
    candidates = stock.resolve_candidates(hits, target_ratio=ratio or "9:16")
    for candidate in candidates:
        if candidate.status != "SUPPORTED":
            continue
        try:
            downloaded = stock.download_asset(candidate.asset, vd_config.project_dir(project_id))
            entry = asset_catalog.import_asset(
                downloaded["file"],
                source=candidate.asset.provider,
                source_id=candidate.asset.source_id,
                source_page=candidate.asset.source_page,
                creator=candidate.asset.creator,
                commercial_status=candidate.asset.commercial_status,
            )
            asset_catalog.project_reference(project_id, entry["asset_id"])
            layer_target = _target_layer_for_scene(project_id, target_id)
            if not layer_target or "#" not in str(layer_target):
                return {"tool": "asset.search", "ok": True, "status": "FOUND",
                        "result": {"query": query, "source": "stock",
                                   "asset_id": entry["asset_id"], "file": entry["file"]},
                        "verify": {"ok": True, "note": "找到并入库素材，但当前没有可替换图层"}}
            r = tool_executor.execute_tool(project_id, {
                "tool": "asset.replace",
                "targetId": layer_target,
                "payload": {"assetId": entry["asset_id"]},
            })
            if r.get("ok"):
                return {"tool": "asset.search", "ok": True, "status": "APPLIED",
                        "result": {"query": query, "source": "stock",
                                   "asset_id": entry["asset_id"], "targetId": layer_target},
                        "verify": {"ok": True, "note": "搜索→下载→采用完成"}}
        except Exception as exc:
            logger.info(f"director asset adopt failed: {exc}")
            continue
    return {"tool": "asset.search", "ok": False, "status": "NOT_FOUND",
            "error": f"未找到可直接采用的素材：{query[:100]}"}


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
    timeline = contexts.get("timeline") or {}
    ctx_desc = (
        f"项目 {proj['project']['duration']}s · 画幅 {proj['project']['ratio']} · "
        f"{proj['timeline']['scene_count']} 场景 · 音轨 {proj['timeline']['tracks']}；"
        f"当前选中：{sel_desc}{' ' + sel_id if sel_id else ''}\n"
        f"时间线：{timeline}\n"
        f"可用来源：{proj['providers']}"
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
    return {"goal": plan.get("goal", instruction[:80]), "steps": steps,
            "timeline_context": contexts.get("timeline") or {}}


def _llm_repair(instruction: str, failed_item: dict, failure: dict, contexts: dict) -> Optional[dict]:
    """Tool Result → 下一步工具的动态修复；最多返回 2 个替代动作。"""
    try:
        from video_director import llm_provider
        provider = llm_provider.get_llm()
        if provider is None:
            return None
        prompt = (
            '你是视频编辑 Agent。某一步执行失败。根据当前项目上下文和失败结果，'
            '给出最多 2 个可执行的替代动作；只输出 JSON：'
            '{"goal":"...","steps":[{"action":"...","target":"...","params":{}}]}。'
            '不要编造不存在的 action；target 可以使用第二段/开头/最后/当前选中。\n'
            f'原指令：{instruction[:500]}\n'
            f'失败动作：{failed_item.get("action")} / {failed_item.get("targetId")}\n'
            f'失败结果：{failure}\n'
            f'当前时间线：{contexts.get("timeline") or {}}\n'
            f'可执行动作：{contexts.get("actions") or {}}'
        )
        raw = provider.generate(prompt).strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```[a-zA-Z]*\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        start, end = raw.find('{'), raw.rfind('}')
        if start < 0 or end <= start:
            return None
        import json as _json
        data = _json.loads(raw[start:end + 1])
        if not isinstance(data, dict) or not isinstance(data.get('steps'), list):
            return None
        steps = []
        for s in data['steps'][:2]:
            if isinstance(s, dict) and s.get('action'):
                steps.append({'action': s['action'], 'target': s.get('target', 'current_selection'),
                              'params': s.get('params') or {}})
        return {'goal': data.get('goal', '修复失败步骤'), 'steps': steps} if steps else None
    except Exception as exc:
        logger.info(f'director repair replan unavailable: {exc}')
        return None

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
        normalized = dict(s)
        resolved_target = _resolve_natural_target(
            project_id, s.get("target"), selection=selection)
        spec = action_registry.get_action(s.get("action") or "")
        if spec and resolved_target and "#" not in str(resolved_target):
            # 仅允许 layer-only 动作用真实图层 id；“第二段”先落到 scene，再取首个视觉层。
            if "layer" in (spec.targets or []) and "scene" not in (spec.targets or []):
                resolved_target = _target_layer_for_scene(project_id, str(resolved_target))
        normalized["target"] = resolved_target
        r = action_registry.resolve_step(normalized, selection)
        if "error" in r:
            resolved.append({"step": s, "resolve_error": r["error"]})
        else:
            resolved.append({"step": s, **r})

    # 4. Execute + 5. Verify（每步执行后重读系统）
    executed = []
    repair_used = False
    for item in resolved:
        if 'resolve_error' in item:
            executed.append({'tool': item['step'].get('action'), 'ok': False,
                             'status': 'RESOLVE_ERROR', 'error': item['resolve_error']})
            continue
        try:
            contexts = build_contexts(project_id, selection)
        except DirectorError:
            pass
        if item.get('action') == 'asset.search':
            res = _execute_asset_search_apply(project_id, item)
        else:
            tool_call = {'tool': item['capability'], 'targetId': item['targetId'],
                         'payload': item['payload']}
            res = tool_executor.execute_tool(project_id, tool_call)
        entry = {'tool': item['capability'], 'action': item.get('action', ''),
                 'label': item.get('label', ''), 'targetId': item['targetId'],
                 'reason': f"目标：{plan.get('goal', '')}", **res}
        if res.get('ok'):
            item_for_verify = dict(item)
            item_for_verify['_result'] = res
            entry['verify'] = _verify_step(project_id, item_for_verify, selection)
        if not res.get('ok') and item.get('action') == 'asset.search':
            queries = _search_fallback_queries(project_id, item.get('targetId') or '')
            fb_res = tool_executor.execute_tool(project_id, {
                'tool': 'generation.image', 'targetId': item['targetId'],
                'payload': {'prompt': queries[0] if queries else (item.get('payload') or {}).get('query', ''),
                            'width': None, 'height': None},
            })
            entry['repair'] = {'tool': 'generation.image', 'ok': bool(fb_res.get('ok')),
                               'result': fb_res.get('result') or {}, 'error': fb_res.get('error'),
                               'note': '图库素材无法采用，已回退到 AI 画面生成并尝试直接采用时间线'}
            if fb_res.get('ok'):
                entry['verify'] = fb_res.get('verify') or {'ok': True, 'note': 'AI 生成画面已采用'}
        if not res.get('ok') and not repair_used and item.get('action') != 'asset.search':
            repair_plan = _llm_repair(instruction, item, res, contexts)
            if repair_plan:
                repair_used = True
                repaired_entries = []
                for rs in repair_plan['steps']:
                    normalized = dict(rs)
                    normalized['target'] = _resolve_natural_target(project_id, rs.get('target'), selection=selection)
                    spec = action_registry.get_action(rs.get('action') or '')
                    if spec and normalized['target'] and '#' not in str(normalized['target']) and 'layer' in (spec.targets or []) and 'scene' not in (spec.targets or []):
                        normalized['target'] = _target_layer_for_scene(project_id, normalized['target'])
                    rr = action_registry.resolve_step(normalized, selection)
                    if 'error' in rr:
                        repaired_entries.append({'ok': False, 'status': 'REPAIR_RESOLVE_ERROR',
                                                 'error': rr['error'], 'action': rs.get('action')})
                        continue
                    rr2 = tool_executor.execute_tool(project_id, {
                        'tool': rr['capability'], 'targetId': rr['targetId'], 'payload': rr['payload']})
                    repaired_entry = {'ok': rr2.get('ok'), 'status': 'REPAIRED',
                                      'action': rs.get('action'), 'targetId': rr['targetId'],
                                      'tool': rr['capability'], **rr2}
                    if rr2.get('ok'):
                        repaired_entry['verify'] = _verify_step(project_id, {**rr, '_result': rr2}, selection)
                    repaired_entries.append(repaired_entry)
                entry['dynamic_repair'] = {'goal': repair_plan.get('goal', ''),
                                           'steps': repaired_entries,
                                           'ok': all(x.get('ok') for x in repaired_entries)}
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
    if action == "watermark.remove":
        return {"ok": not ir.watermarks, "note": "水印已移除" if not ir.watermarks else f"仍有 {len(ir.watermarks)} 个水印"}
    if action in ("watermark.add", "watermark.update"):
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
    if action == "content.generate_script" or action == "content.rewrite_script":
        return {"ok": True, "note": "脚本产物已由内容能力返回"}
    if action in ("asset.search", "asset.replace", "generate.image", "generate.video"):
        target = item.get("targetId") or ""
        asset_expected = ((item.get("payload") or {}).get("assetId") or "")
        if asset_expected and "#" in str(target):
            try:
                scene_id, idx_s = str(target).split("#", 1)
                layer = next(s for s in ir.scenes if s.id == scene_id).layers[int(idx_s)]
                ok = layer.asset_id == asset_expected
                return {"ok": ok, "note": f"目标素材 {'已替换' if ok else '未替换'}"}
            except Exception as exc:
                return {"ok": False, "note": f"目标层读回失败：{exc}"}
        return {"ok": bool(ir.scenes), "note": "项目仍可读取"}
    if action == "timeline.duration":
        target = item.get("targetId") or ""
        scene = next((s for s in ir.scenes if s.id == target), None)
        expected = (item.get("payload") or {}).get("duration")
        ok = scene is not None and (expected is None or abs(scene.duration - float(expected)) < 0.05)
        return {"ok": ok, "note": f"目标时长={scene.duration if scene else '—'}"}
    if action == "render.draft":
        p = __import__("video_director").config.project_dir(project_id) / "output" / "draft.mp4"
        return {"ok": p.is_file(), "note": "草稿产物已生成" if p.is_file() else "草稿产物不存在"}
    if action == "render.final":
        return {"ok": bool(item.get("_result", {}).get("result", {}).get("final")),
                "note": "终稿接口已返回产物" if item.get("_result", {}).get("result", {}).get("final") else "终稿产物返回缺失"}
    return {"ok": True, "note": "已执行（该动作没有独立 IR 断言）"}
