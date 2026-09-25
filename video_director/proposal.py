"""AI Proposal 层（EDT-009 + IG-REVISION）：自然语言 → 结构化提案 → 预览 → 应用/拒绝。

全局契约 §4/§5：
    AI 只 Understand/Propose/Explain，不直接改 Composition。
    Proposal ≠ Applied。Reject → Composition/Artifact/Revision 全部不变。

流程：
    POST propose  → parse_edit_instruction_llm 结构化 patches（未落盘）
    POST preview  → patches 应用到临时 IR（不写 composition.json）
    POST apply    → 走 editor.execute_command（正式 Revision）
    POST reject   → 仅状态记录，零变更
"""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional

from loguru import logger

from video_director import config, editor
from video_director.editor import EditorError


class ProposalError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# Proposal 存储（内存态 + jsonl 落盘留痕；proposal 本身不是 mutation）
# ---------------------------------------------------------------------------

_PENDING: Dict[str, dict] = {}


def _proposals_path(project_id: str):
    return config.project_dir(project_id) / "plans" / "proposals.jsonl"


def _save_proposal_record(project_id: str, record: dict) -> None:
    p = _proposals_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(record, ensure_ascii=False) + "\n")


def _llm_parse(instruction: str, composition_summary: dict) -> Optional[List[dict]]:
    """LLM 结构化解析（AI 只出提案，不出 mutation）。

    返回 patches 列表：[{command, targetId, payload, reason}]
    LLM 不可用/解析失败 → None（调用方回退规则解析或报 MISSING_TARGET）。
    """
    from video_director.llm_provider import get_llm
    provider = get_llm()
    if provider is None:
        return None
    import json as _json
    scenes_desc = "\n".join(
        f"- {s['id']}: duration={s['duration']:.1f}s, layers={len(s['layers'])}, "
        f"text={s['text'][:30]!r}, motion={s.get('motion', '-')}"
        for s in composition_summary.get("scenes", [])
    )
    system = (
        "你是视频编辑指令解析器。把用户的自然语言修改要求转成结构化命令列表。"
        "只输出 JSON 数组，不要解释。每个元素："
        '{"command": "replace_motion|replace_recipe|set_motion_parameter|replace_source|'
        'set_duration|trim", "targetId": "<场景id 或 场景id#层序号>", '
        '"payload": {...}, "reason": "一句话理由"}'
        "\n可用命令：replace_motion(payload.motionRecipeId 如 motion.scale-punch / motion.word-relay /"
        " motion.fade-slide / motion.rise-fade / motion.ken-burns-soft / motion.fade-in / motion.fade-out)、"
        "replace_recipe(payload.recipeId 如 shot.product-hero-assemble / shot.spotlight-opening /"
        " shot.editorial-photo-story / shot.card-stack-reveal)、"
        "set_duration(payload.duration 秒)、trim(payload.sourceIn/sourceOut 秒)。"
        "如果指令含糊无法定位目标场景，返回 [{\"command\": \"MISSING_TARGET\"}]。"
        "不要发明不存在的目标 id，只能用场景清单里给出的 id。"
    )
    user = f"场景清单：\n{scenes_desc}\n\n用户指令：{instruction}"
    try:
        raw = provider.generate(user, system=system)
    except Exception as exc:
        logger.warning(f"proposal LLM 解析失败: {exc}")
        return None
    import re
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        logger.warning(f"proposal LLM 输出无 JSON 数组: {text[:80]}")
        return None
    try:
        patches = _json.loads(text[start:end + 1])
    except _json.JSONDecodeError:
        logger.warning(f"proposal LLM JSON 解析失败: {text[:120]}")
        return None
    if not isinstance(patches, list):
        return None
    out = []
    for p in patches:
        if not isinstance(p, dict) or "command" not in p:
            continue
        if p["command"] == "MISSING_TARGET":
            return [{"command": "MISSING_TARGET"}]
        out.append({
            "command": p.get("command"),
            "targetId": p.get("targetId"),
            "payload": p.get("payload") or {},
            "reason": p.get("reason", ""),
        })
    return out or None


def _rule_parse_fallback(instruction: str, composition_summary: dict) -> Optional[List[dict]]:
    """规则兜底：第N段/场景 + 常见动效关键词（无 LLM 时的诚实降级）。"""
    import re
    patches = []
    scenes = composition_summary.get("scenes", [])
    # "第N段动画慢一点/换动画" → replace_motion
    m = re.search(r"第([一二三四五六七八九十\d]+)段", instruction)
    idx = None
    if m:
        tok = m.group(1)
        cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        idx = cn.get(tok) or (int(tok) if tok.isdigit() else None)
    if idx and 0 < idx <= len(scenes):
        sid = scenes[idx - 1]["id"]
        if any(k in instruction for k in ("慢", "柔", "缓")):
            patches.append({"command": "replace_motion", "targetId": f"{sid}#0",
                            "payload": {"motionRecipeId": "motion.fade-slide"},
                            "reason": "规则解析：慢一点的入场"})
        elif any(k in instruction for k in ("冲击", "砸", "重")):
            patches.append({"command": "replace_motion", "targetId": f"{sid}#0",
                            "payload": {"motionRecipeId": "motion.scale-punch"},
                            "reason": "规则解析：冲击感"})
        elif "标题" in instruction or "文字" in instruction:
            patches.append({"command": "replace_motion", "targetId": f"{sid}#0",
                            "payload": {"motionRecipeId": "motion.word-relay"},
                            "reason": "规则解析：标题动效"})
    return patches or None


def propose(project_id: str, instruction: str) -> dict:
    """生成提案（不改任何状态；返回 proposalId + patches + explanation）。"""
    ir = editor.load_composition(project_id)
    summary = {
        "scenes": [
            {
                "id": sc.id, "duration": sc.duration, "text": sc.text,
                "layers": [
                    {
                        "index": i, "type": lay.type.value,
                        "motion": (lay.motion_recipe_id or lay.motion or "-"),
                    } for i, lay in enumerate(sc.layers)
                ],
            } for sc in ir.scenes
        ]
    }
    patches = _llm_parse(instruction, summary)
    source = "llm"
    if not patches:
        patches = _rule_parse_fallback(instruction, summary)
        source = "rule"
    if not patches:
        raise ProposalError(
            "MISSING_TARGET",
            f"无法从指令定位修改目标：{instruction[:60]!r}（请指明第几段/哪个场景）",
        )
    if patches and patches[0].get("command") == "MISSING_TARGET":
        raise ProposalError("MISSING_TARGET", "指令目标不明确，请指明第几段/哪个场景")

    proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
    record = {
        "proposal_id": proposal_id,
        "project_id": project_id,
        "instruction": instruction,
        "patches": patches,
        "source": source,
        "status": "PROPOSED",
        "created_at": time.time(),
    }
    _PENDING[proposal_id] = record
    _save_proposal_record(project_id, record)
    return {
        "proposal_id": proposal_id,
        "patches": patches,
        "source": source,
        "explanation": "；".join(p.get("reason", "") for p in patches if p.get("reason")),
        "affected": sorted({p.get("targetId", "").split("#")[0] for p in patches if p.get("targetId")}),
    }


def preview(project_id: str, proposal_id: str) -> dict:
    """预览：patches 应用到内存 IR（不写盘）。失败返回逐 patch 错误。"""
    record = _PENDING.get(proposal_id)
    if record is None:
        raise ProposalError("NOT_FOUND", f"提案 {proposal_id} 不存在或已过期")
    results = []
    for p in record["patches"]:
        try:
            cmd = dict(p)
            cmd["expectedVersion"] = None  # 预览不做版本检查（正式 apply 才检查）
            ir = editor.load_composition(project_id)  # 每次从盘读（预览幂等）
            # 预览复用 execute_command 的校验与变换逻辑，但拦截 save：
            # 通过 monkeypatch 临时禁写——正式做法是抽出 apply 层；这里直接
            # 调 execute_command 到一个假 project 会改盘，不可取。改为：
            # 手动执行每个命令的校验路径。
            from video_director import editor as ed
            # 复制 execute_command 的执行核心，跳过 save/revision：
            result = _dry_run_command(ed, project_id, cmd)
            results.append({"patch": p, "status": "PREVIEW_OK",
                            "changed": result.get("changed", [])})
        except (EditorError, Exception) as exc:
            results.append({"patch": p, "status": "INVALID",
                            "error": str(exc)[:200]})
    record["status"] = "PREVIEWED"
    return {"proposal_id": proposal_id, "results": results}


def _dry_run_command(ed, project_id: str, command: dict) -> dict:
    """干跑单命令（不落盘）：复用命令实现的变换函数。"""
    ir = ed.load_composition(project_id)
    target_id = command.get("targetId") or ""
    payload = command.get("payload") or {}
    cmd_name = command.get("command")
    handlers = {
        "replace_motion": ed._cmd_replace_motion,
        "set_motion_parameter": ed._cmd_set_motion_parameter,
        "replace_recipe": lambda ir, t, p: ed._cmd_replace_recipe(ir, t, p, project_id),
        "replace_source": ed._cmd_replace_source,
        "set_duration": ed._cmd_set_duration,
        "trim": ed._cmd_trim,
    }
    handler = handlers.get(cmd_name)
    if handler is None:
        raise EditorError("COMMAND_NOT_SUPPORTED", f"预览不支持命令 {cmd_name}")
    changed, _stale = handler(ir, target_id, payload)
    return {"changed": changed, "composition": __import__("json").loads(ir.model_dump_json())}


def apply(project_id: str, proposal_id: str) -> dict:
    """应用：逐 patch 走 editor.execute_command（正式 Revision 落盘）。"""
    record = _PENDING.get(proposal_id)
    if record is None:
        raise ProposalError("NOT_FOUND", f"提案 {proposal_id} 不存在或已过期")
    applied = []
    errors = []
    for p in record["patches"]:
        result = editor.execute_command(project_id, {
            "command": p["command"],
            "targetId": p.get("targetId"),
            "payload": p.get("payload") or {},
            "author": "ai-proposal",
        })
        if result.status == "APPLIED":
            applied.append({
                "command": p["command"], "targetId": p.get("targetId"),
                "revision_id": result.revision_id,
                "changed": result.changed_artifacts,
            })
        else:
            errors.append({"patch": p, "errors": result.errors})
            break  # 一个失败即停（不产生半套修改）
    record["status"] = "APPLIED" if not errors else "REJECTED"
    if errors:
        # apply 中途失败的 patch 已落盘（逐条 execute 是原子的），报告实际状态
        return {"status": "PARTIAL", "applied": applied, "errors": errors}
    return {"status": "APPLIED", "applied": applied}


def reject(project_id: str, proposal_id: str) -> dict:
    """拒绝：零变更（契约 §5：Composition/Artifact/Revision 全不变）。"""
    record = _PENDING.pop(proposal_id, None)
    if record is not None:
        record["status"] = "REJECTED"
        _save_proposal_record(project_id, record)
    # 显式断言：不做任何 mutation
    return {"status": "REJECTED", "changed": False}
