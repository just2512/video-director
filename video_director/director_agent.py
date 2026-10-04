"""AI Director creation Agent loop.

Every creation node is an independent execution unit with Tool Result and Verify.
Creation never calls pipeline.compose_auto as one opaque operation.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger

from video_director import audio, assets as assets_mod, config, matcher, narration
from video_director import planner as planner_mod
from video_director import renderer, style as style_mod, subtitle as subtitle_mod
from video_director import validator, qc as qc_mod


def _json_file(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _file_ok(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def _strip_code_fence(text: str) -> str:
    fence = chr(96) * 3
    raw = (text or "").strip()
    if raw.startswith(fence):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit(fence, 1)[0]
    return raw.strip()


def _scene_texts(script: str) -> List[str]:
    import re
    parts = [x.strip() for x in re.split(r"[。！？!?
]+", script or "") if x.strip()]
    return [x + ("。" if not x.endswith(("。", "！", "？", "!", "?")) else "") for x in parts]


def _ready_nodes(plan: dict) -> List[str]:
    by_id = {n.get("id"): n for n in plan.get("nodes") or []}
    out = []
    for n in plan.get("nodes") or []:
        if n.get("status") != "PENDING":
            continue
        if all(by_id.get(d, {}).get("status") in ("DONE", "SKIPPED")
               for d in n.get("dependencies") or []):
            out.append(n["id"])
    return out


def _choose_next(plan: dict, current: str, result: dict, retries: int) -> Tuple[str, str]:
    ready = _ready_nodes(plan)
    try:
        from video_director import llm_provider
        provider = llm_provider.get_llm()
    except Exception:
        provider = None
    if provider is not None:
        prompt = (
            "你是视频 AI Director 调度器。节点执行完成后，根据真实 Tool Result 选择下一节点。"
            '只能从 ready 中选择，或者 retry，或者 STOP。只输出 JSON：'
            '{"next":"节点ID|retry|STOP","reason":"一句话"}\n'
            + "current=" + current + "\n"
            + "result=" + json.dumps(result, ensure_ascii=False, default=str)[:7000] + "\n"
            + "ready=" + json.dumps(ready, ensure_ascii=False) + "\n"
            + "retries=" + str(retries)
        )
        try:
            raw = _strip_code_fence(provider.generate(
                prompt, system="只做节点调度，不修改项目，不虚构结果。"))
            a, b = raw.find("{"), raw.rfind("}")
            if a >= 0 and b > a:
                data = json.loads(raw[a:b + 1])
                choice = str(data.get("next") or "").strip()
                reason = str(data.get("reason") or "").strip()
                if choice in ready:
                    return choice, reason or "根据 Tool Result 继续"
                if choice == "retry" and retries < 2:
                    return "retry", reason or "重试当前节点"
                if choice == "STOP":
                    return "STOP", reason or "Director 停止"
        except Exception as exc:
            logger.warning("Director next-node decision failed: %s", exc)
    if not result.get("ok", True):
        return ("retry", "Tool Result 未通过，重试当前节点") if retries < 2 else ("STOP", "节点连续失败")
    return (ready[0], "按依赖图继续") if ready else ("STOP", "没有可继续节点")
