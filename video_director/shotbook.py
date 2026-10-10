"""Project-level Shotbook: editable semantic beats with honest character timing.

This module stores the editorial plan beside, not inside, composition.json.
The only operation that changes the actual timeline is the existing Editor Command API.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from video_director import config, editor

SCHEMA_VERSION = 1
SEMANTIC_ROLES = ("hook", "context", "problem", "evidence", "turn", "resolution", "cta", "neutral")
VISUAL_INTENTS = ("establish", "illustrate", "contrast", "prove", "emphasize", "transition", "breathe", "close")
SHOT_TYPES = ("footage", "document", "screenshot", "chart", "title_card", "diagram", "product", "map", "split_screen", "still")
_TEXT_LIMIT = 500
_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ShotbookError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _project_id(value: str) -> str:
    pid = str(value or "").strip()
    if not _PROJECT_ID.fullmatch(pid):
        raise ShotbookError("INVALID_PROJECT_ID", "项目编号格式无效")
    return pid


def _plan_path(project_id: str) -> Path:
    return config.project_dir(_project_id(project_id)) / "plans" / "shotbook.json"


def _timing_path(project_id: str) -> Path:
    return config.project_dir(_project_id(project_id)) / "plans" / "narration_timing.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@contextmanager
def _project_plan_lock(project_id: str) -> Iterator[None]:
    """Cross-process exclusive lock for Shotbook read/validate/write transactions."""
    path = _plan_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_plan(path: Path) -> dict | None:
    """Read the primary plan, recover from the last valid backup, never silently reset."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("shots"), list):
            raise ValueError("Shotbook JSON 结构无效")
        return value
    except (OSError, json.JSONDecodeError, ValueError) as primary_error:
        backup = path.with_suffix(".json.bak")
        try:
            recovered = json.loads(backup.read_text(encoding="utf-8"))
            if not isinstance(recovered, dict) or not isinstance(recovered.get("shots"), list):
                raise ValueError("备份结构无效")
            _atomic_json_write(path, recovered)
            return recovered
        except (OSError, json.JSONDecodeError, ValueError) as backup_error:
            raise ShotbookError(
                "CORRUPT_PLAN",
                "分镜文件已损坏，且没有可用的有效备份；原文件已保留，未自动覆盖。"
                f" 主文件：{primary_error}；备份：{backup_error}",
            ) from primary_error


def _atomic_json_write(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _normal_char(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _timed_token_chars(text: str, start: float, end: float) -> list[dict]:
    chars = [c for c in str(text) if not c.isspace()]
    if not chars or end <= start:
        return []
    span = end - start
    return [{
        "char": char,
        "start": round(start + span * index / len(chars), 3),
        "end": round(start + span * (index + 1) / len(chars), 3),
        "method": "token_interpolation",
    } for index, char in enumerate(chars)]


def expand_character_timing(
    text: str,
    start: float,
    end: float,
    word_timeline: list[dict] | None = None,
) -> list[dict]:
    """Build editable character anchors without claiming native character alignment.

    Matching token characters are expanded evenly inside their token interval.
    Unmatched characters fall back to proportional scene timing. Every item carries its
    method so the frontend can expose uncertainty instead of false precision.
    """
    visible = [(index, char) for index, char in enumerate(str(text)) if not char.isspace()]
    if not visible:
        return []
    duration = max(0.0, float(end) - float(start))
    fallback = {
        original: (round(float(start) + duration * index / len(visible), 3),
                   round(float(start) + duration * (index + 1) / len(visible), 3))
        for index, (original, _char) in enumerate(visible)
    }
    token_chars = []
    for word in word_timeline or []:
        try:
            ws = max(float(start), float(word.get("start", start)))
            we = min(float(end), float(word.get("end", end)))
        except (TypeError, ValueError):
            continue
        if we > ws:
            token_chars.extend(_timed_token_chars(word.get("text", ""), ws, we))
    result = []
    cursor = 0
    prev_start = float(start)
    for original_index, char in visible:
        match_index = None
        for candidate in range(cursor, min(len(token_chars), cursor + 9)):
            if _normal_char(token_chars[candidate]["char"]) == _normal_char(char):
                match_index = candidate
                break
        if match_index is not None:
            token = token_chars[match_index]
            a = max(float(start), min(float(end), token["start"]))
            b = max(a, min(float(end), token["end"]))
            # TalkCraft 教训（timestamps_cpu.py）：重叠/乱序 token 会产出时间倒流锚点，
            # 而保存端强制起点单调——生成端必须同样强制，否则草案自己过不了自己的校验。
            # token 起点早于前一字起点 → 该字退化为场景估算，不伪造词级精度。
            if a < prev_start - 1e-9:
                method = "scene_estimate"
                a, b = fallback[original_index]
            else:
                method = "token_interpolation"
                cursor = match_index + 1
        else:
            a, b = fallback[original_index]
            method = "scene_estimate"
        # 生成端与保存端同一单调契约：任何路径产出的起点都不得早于前一字。
        a = max(a, prev_start)
        b = max(b, a)
        result.append({"char": char, "start": round(a, 3), "end": round(b, 3), "method": method})
        prev_start = result[-1]["start"]
    return result


def _default_role(text: str, index: int, total: int, existing: str | None) -> str:
    if existing in SEMANTIC_ROLES:
        return existing
    value = str(text or "")
    if index == 0:
        return "hook"
    if index == total - 1:
        return "cta" if any(k in value for k in ("关注", "评论", "转发", "试试", "记住", "行动")) else "resolution"
    if any(k in value for k in ("证据", "数据显示", "研究", "数据", "例如", "比如", "记录", "截图", "来源")):
        return "evidence"
    if any(k in value for k in ("但是", "然而", "其实", "反过来", "问题是", "真正")):
        return "turn"
    if any(k in value for k in ("问题", "困难", "代价", "失败", "风险", "误区")):
        return "problem"
    if index == 1:
        return "context"
    return "neutral"


def _default_intent(role: str, existing: str | None) -> str:
    if existing in VISUAL_INTENTS:
        return existing
    return {
        "hook": "emphasize", "context": "establish", "problem": "contrast",
        "evidence": "prove", "turn": "contrast", "resolution": "illustrate",
        "cta": "close", "neutral": "illustrate",
    }.get(role, "illustrate")


def _scene_words(scene: Any, words: list[dict]) -> list[dict]:
    start = float(scene.start)
    end = start + float(scene.duration)
    out = []
    for word in words:
        try:
            ws, we = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if we > start and ws < end and str(word.get("text", "")).strip():
            out.append({"text": str(word["text"]), "start": ws, "end": we})
    return out


def _build_rows(ir: Any, timing: dict, previous: dict | None) -> list[dict]:
    words = timing.get("word_timeline") or []
    timing_source = str(timing.get("source") or "missing")
    old_by_scene = {
        str(item.get("scene_id")): item
        for item in (previous or {}).get("shots", [])
        if isinstance(item, dict) and item.get("scene_id")
    }
    scenes = list(getattr(ir, "scenes", []) or [])
    result = []
    for index, scene in enumerate(scenes):
        prior = old_by_scene.get(str(scene.id), {})
        start = float(scene.start)
        end = start + float(scene.duration)
        text = str(scene.text or "")
        token_words = _scene_words(scene, words) if timing_source in ("asr", "tts", "native_tts") else []
        anchors = expand_character_timing(text, start, end, token_words)
        old_anchors = prior.get("characters") or []
        prior_text_same = bool(prior) and str(prior.get("text", "")) == text
        try:
            prior_start, prior_end = float(prior.get("start", start)), float(prior.get("end", end))
        except (TypeError, ValueError):
            prior_start, prior_end = start, end
        prior_span = prior_end - prior_start
        if prior_text_same and len(old_anchors) == len(anchors) and prior_span > 0:
            current_span = end - start
            for fresh, old in zip(anchors, old_anchors):
                if old.get("char") != fresh["char"]:
                    continue
                try:
                    old_start, old_end = float(old.get("start")), float(old.get("end"))
                    if prior_start - 0.05 <= old_start <= old_end <= prior_end + 0.05:
                        rel_start = (old_start - prior_start) / prior_span
                        rel_end = (old_end - prior_start) / prior_span
                        fresh["start"] = round(max(start, min(end, start + rel_start * current_span)), 3)
                        fresh["end"] = round(max(fresh["start"], min(end, start + rel_end * current_span)), 3)
                        if old.get("method") == "manual":
                            fresh["method"] = "manual"
                except (TypeError, ValueError):
                    pass
        source_unchanged = (
            prior_text_same and abs(prior_start - start) <= 0.005
            and abs(prior_end - end) <= 0.005
        )
        role = _default_role(text, index, len(scenes),
                             getattr(scene, "semantic_role", None) or prior.get("semantic_role"))
        intent = _default_intent(role, getattr(scene, "visual_intent", None) or prior.get("visual_intent"))
        layer_recipe = next((getattr(layer, "motion_recipe_id", None)
                             for layer in (getattr(scene, "layers", []) or [])
                             if getattr(layer, "motion_recipe_id", None)), None)
        recipe_id = getattr(scene, "origin_recipe_id", None) or prior.get("shot_recipe_id")
        layers = getattr(scene, "layers", []) or []
        result.append({
            "shot_id": "shot-" + str(scene.id), "scene_id": str(scene.id),
            "start": round(start, 3), "end": round(end, 3), "text": text,
            "characters": anchors,
            "timing_label": (
                "ASR 词级时间；词内字符为插值估算" if token_words and timing_source == "asr"
                else "TTS 词边界；词内字符为插值估算" if token_words and timing_source in ("tts", "native_tts")
                else "场景区间估算；需人工核对"
            ),
            "semantic_role": role, "visual_intent": intent,
            "shot_type": prior.get("shot_type") if prior.get("shot_type") in SHOT_TYPES else "footage",
            "shot_recipe_id": str(recipe_id or ""),
            "motion_recipe_id": str(prior.get("motion_recipe_id") or layer_recipe or ""),
            "overlay_text": str(prior.get("overlay_text") or "")[:120],
            "evidence_ref": str(prior.get("evidence_ref") or "")[:300],
            "sfx_cue": str(prior.get("sfx_cue") or "")[:160],
            "notes": str(prior.get("notes") or "")[:_TEXT_LIMIT],
            # Changing narration text or scene interval invalidates previous editorial sign-off.
            "reviewed": bool(prior.get("reviewed", False)) and source_unchanged,
            "applied_motion_recipe_id": str(layer_recipe or ""),
            "editable_layers": [
                {
                    "target_id": str(scene.id) + "#" + str(layer_index),
                    "type": getattr(getattr(layer, "type", ""), "value", str(getattr(layer, "type", ""))),
                    "asset_id": str(getattr(layer, "asset_id", "") or ""),
                    "locked": bool(getattr(layer, "locked", False)),
                }
                for layer_index, layer in enumerate(layers)
            ],
            "has_visual_layer": bool(layers),
            "has_bound_asset": any(getattr(layer, "asset_id", None) for layer in layers),
        })
    return result


def _recipes() -> list[dict]:
    try:
        from video_director.registries.recipes import ShotRegistry
        rows = []
        for recipe in ShotRegistry().all():
            rows.append({
                "id": recipe.id, "version": recipe.version, "name": recipe.name,
                "category": recipe.category, "semantic_intents": list(recipe.semantic_intents),
                "compatible_content_types": list(recipe.compatible_content_types),
                "duration": {"min": recipe.duration_min, "recommended": recipe.duration_recommended,
                             "max": recipe.duration_max},
                "commercial_status": recipe.commercial_status,
                "layer_types": [part.layer_type for part in recipe.layer_plan],
            })
        return sorted(rows, key=lambda item: (item["category"], item["name"], item["id"]))
    except Exception:
        return []


def _motion_recipes() -> list[dict]:
    """Motion Registry plus conservative support for the current FFmpeg renderer."""
    try:
        from video_director.registries.recipes import MotionRegistry
        rows = []
        for motion in MotionRegistry().all():
            properties = sorted({str(channel.property) for channel in motion.channels})
            layers = {str(value) for value in motion.compatible_layers}
            preview_only = "renderer_preview_only" in motion.constraints
            supported_for = []
            # renderer.py executes image scale/opacity; video currently supports opacity only.
            if not preview_only and properties:
                if "image" in layers and set(properties) <= {"scale", "opacity"}:
                    supported_for.append("image")
                if "video" in layers and set(properties) <= {"opacity"}:
                    supported_for.append("video")
            note = ("可由当前渲染器执行" if supported_for else
                    "配方标记为仅预览" if preview_only else
                    "当前 FFmpeg 渲染器不支持此图层/属性组合")
            rows.append({
                "id": motion.id, "version": motion.version, "name": motion.name,
                "motion_type": motion.motion_type,
                "semantic_intents": list(motion.semantic_intents),
                "compatible_layers": sorted(layers), "supported_for": supported_for,
                "properties": properties, "duration_recommended": motion.duration_recommended,
                "duration_range": [motion.duration_min, motion.duration_max],
                "commercial_status": motion.commercial_status,
                "preview_only": preview_only, "execution_note": note,
            })
        return sorted(rows, key=lambda item: (item["motion_type"], item["name"], item["id"]))
    except Exception:
        return []


def _gates(shots: list[dict], recipes: list[dict], timing_source: str) -> list[dict]:
    if not shots:
        timing = {"id": "timing", "label": "时间锚点", "status": "MISSING", "detail": "项目里没有可用场景或旁白文本。"}
        semantic = {"id": "semantic", "label": "语义分镜", "status": "MISSING", "detail": "没有可检查的分镜。"}
        render = {"id": "render", "label": "镜头执行", "status": "MISSING", "detail": "没有镜头可渲染。"}
    else:
        native_chars = timing_source in ("native_character", "character")
        timing = {
            "id": "timing", "label": "时间锚点",
            "status": "PASS" if native_chars else "PARTIAL",
            "detail": "使用原生字级时间戳。" if native_chars else
                      "当前为词级或场景级映射；词内字时间仍是估算，需要在每镜详情中校正。",
        }
        reviewed = all(s.get("reviewed") and s.get("semantic_role") in SEMANTIC_ROLES
                       and s.get("visual_intent") in VISUAL_INTENTS for s in shots)
        semantic = {
            "id": "semantic", "label": "语义分镜",
            "status": "PASS" if reviewed else "REVIEW",
            "detail": "每镜均已由用户确认。" if reviewed else
                      "自动草案尚未逐镜确认；确认后才能通过分镜验收。",
        }
        known = {r["id"] for r in recipes}
        invalid = [s for s in shots if s.get("shot_recipe_id") and s["shot_recipe_id"] not in known]
        missing = [s for s in shots if not s.get("shot_recipe_id")]
        no_layers = [s for s in shots if not s.get("has_visual_layer")]
        if invalid:
            render_status, render_detail = "REJECT", "存在无法在当前镜头库解析的配方编号。"
        elif no_layers:
            render_status, render_detail = "MISSING", "部分镜头没有可用画面层。"
        elif missing:
            render_status, render_detail = "REVIEW", "时间线有画面层，但尚未给每镜绑定镜头配方；不能宣称分镜配方已完整。"
        else:
            render_status, render_detail = "PASS", "每镜均有可解析的配方；仍需检查素材槽位与实际渲染结果。"
        render = {"id": "render", "label": "镜头执行", "status": render_status, "detail": render_detail}
    evidence_required = [s for s in shots
                         if s.get("shot_type") in ("document", "screenshot", "chart", "map") and not s.get("evidence_ref")]
    evidence = {
        "id": "evidence", "label": "证据素材",
        "status": "MISSING" if evidence_required else "PASS",
        "detail": (str(len(evidence_required)) + " 个文件、截图、图表或地图镜头缺少来源/素材引用。")
                  if evidence_required else "需留证据的镜头已填写引用；请核对引用指向真实文件。",
    }
    return [timing, semantic, render, evidence]


def get_project_shotbook(project_id: str) -> dict:
    pid = _project_id(project_id)
    try:
        ir = editor.load_composition(pid)
    except Exception as exc:
        raise ShotbookError("PROJECT_NOT_FOUND", "无法读取项目时间线：" + str(exc)) from exc
    path = _plan_path(pid)
    previous = _read_plan(path)
    timing = _read_json(_timing_path(pid), {"source": "missing", "word_timeline": []})
    shots = _build_rows(ir, timing, previous)
    recipes = _recipes()
    source = str(timing.get("source") or "missing")
    return {
        "ok": True, "project_id": pid, "schema_version": SCHEMA_VERSION,
        "version": int((previous or {}).get("version") or 0),
        "saved": bool(previous), "shots": shots, "recipes": recipes, "motions": _motion_recipes(),
        "gates": _gates(shots, recipes, source), "timing_source": source,
        "timing_note": ("ASR 词时间可用，但词内字符时间由插值估算。"
                        if source == "asr" else
                        "未保存可用的原生词边界；当前字时间仅是按场景区间估算。"),
    }


def _editable_fields(incoming: dict, generated: dict) -> dict:
    role = incoming.get("semantic_role", generated["semantic_role"])
    intent = incoming.get("visual_intent", generated["visual_intent"])
    shot_type = incoming.get("shot_type", generated["shot_type"])
    if role not in SEMANTIC_ROLES:
        raise ShotbookError("INVALID_SEMANTIC_ROLE", generated["scene_id"] + " 的语义角色无效")
    if intent not in VISUAL_INTENTS:
        raise ShotbookError("INVALID_VISUAL_INTENT", generated["scene_id"] + " 的视觉意图无效")
    if shot_type not in SHOT_TYPES:
        raise ShotbookError("INVALID_SHOT_TYPE", generated["scene_id"] + " 的镜头类型无效")
    recipe_id = str(incoming.get("shot_recipe_id") or "").strip()
    if len(recipe_id) > 160:
        raise ShotbookError("INVALID_RECIPE_ID", "镜头配方编号过长")
    anchors = incoming.get("characters", generated["characters"])
    if not isinstance(anchors, list) or len(anchors) != len(generated["characters"]):
        raise ShotbookError("INVALID_CHARACTER_TIMING", generated["scene_id"] + " 的字符锚点数量不匹配")
    clean_anchors = []
    prev_start = float(generated["start"])
    for index, (anchor, fresh) in enumerate(zip(anchors, generated["characters"])):
        if not isinstance(anchor, dict) or str(anchor.get("char", "")) != fresh["char"]:
            raise ShotbookError("INVALID_CHARACTER_TIMING", generated["scene_id"] + " 第 " + str(index + 1) + " 个字符锚点被非法替换")
        try:
            start, end = float(anchor["start"]), float(anchor["end"])
        except (KeyError, TypeError, ValueError):
            raise ShotbookError("INVALID_CHARACTER_TIMING", generated["scene_id"] + " 第 " + str(index + 1) + " 个字符时间不是数字")
        if not (generated["start"] - 0.05 <= start <= end <= generated["end"] + 0.05):
            raise ShotbookError("INVALID_CHARACTER_TIMING", generated["scene_id"] + " 第 " + str(index + 1) + " 个字符时间超出镜头范围")
        if start + 0.0001 < prev_start:
            raise ShotbookError("INVALID_CHARACTER_TIMING", generated["scene_id"] + " 的字符起点必须按文本顺序递增")
        prev_start = start
        changed = abs(start - float(fresh["start"])) > 0.002 or abs(end - float(fresh["end"])) > 0.002
        clean_anchors.append({
            "char": fresh["char"], "start": round(start, 3), "end": round(end, 3),
            "method": "manual" if changed else fresh.get("method", "scene_estimate"),
        })
    def bounded_text(name: str, max_len: int) -> str:
        value = str(incoming.get(name) or "").strip()
        if len(value) > max_len:
            raise ShotbookError("FIELD_TOO_LONG", generated["scene_id"] + " 的 " + name + " 超过 " + str(max_len) + " 字")
        return value
    return {
        **generated, "characters": clean_anchors, "semantic_role": role,
        "visual_intent": intent, "shot_type": shot_type, "shot_recipe_id": recipe_id,
        "overlay_text": bounded_text("overlay_text", 120),
        "evidence_ref": bounded_text("evidence_ref", 300),
        "sfx_cue": bounded_text("sfx_cue", 160),
        "notes": bounded_text("notes", _TEXT_LIMIT),
        "motion_recipe_id": bounded_text("motion_recipe_id", 160),
        "motion_layer_target": bounded_text("motion_layer_target", 180),
        "reviewed": bool(incoming.get("reviewed", False)),
    }


def _write_plan(project_id: str, plan: dict) -> dict:
    path = _plan_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve only a parseable previous plan as the recovery point.
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("shots"), list):
                _atomic_json_write(path.with_suffix(".json.bak"), old)
        except (OSError, json.JSONDecodeError):
            # Do not overwrite the backup with a corrupt primary.
            pass
    _atomic_json_write(path, plan)
    return plan


def _generate_project_shotbook_unlocked(project_id: str, base_version: int | None = None) -> dict:
    current = get_project_shotbook(project_id)
    if base_version is not None and int(base_version) != current["version"]:
        raise ShotbookError("CONFLICT", "分镜草案已被其他操作更新，请重新载入后再生成。")
    plan = {
        "schema_version": SCHEMA_VERSION, "project_id": current["project_id"],
        "version": current["version"] + 1, "updated_at": int(time.time()),
        "shots": current["shots"],
    }
    _write_plan(current["project_id"], plan)
    return get_project_shotbook(current["project_id"])


def _save_project_shotbook_unlocked(project_id: str, payload: dict) -> dict:
    pid = _project_id(project_id)
    current = get_project_shotbook(pid)
    try:
        base_version = int(payload.get("base_version"))
    except (TypeError, ValueError):
        raise ShotbookError("INVALID_VERSION", "保存分镜前缺少版本号，请重新载入分镜。")
    if base_version != current["version"]:
        raise ShotbookError("CONFLICT", "分镜内容已被其他操作更新；没有覆盖现有修改，请重新载入后再保存。")
    incoming = payload.get("shots")
    if not isinstance(incoming, list):
        raise ShotbookError("INVALID_SHOTS", "缺少 shots 列表")
    by_scene = {}
    for item in incoming:
        if isinstance(item, dict) and item.get("scene_id"):
            scene_id = str(item["scene_id"])
            if scene_id in by_scene:
                raise ShotbookError("DUPLICATE_SCENE", "重复提交场景 " + scene_id)
            by_scene[scene_id] = item
    fresh_by_scene = {str(s["scene_id"]): s for s in current["shots"]}
    if set(by_scene) != set(fresh_by_scene):
        raise ShotbookError("STALE_SCENE_SET", "时间线场景已变化；请重新生成分镜草案，避免错写到其他镜头。")
    cleaned = [_editable_fields(by_scene[sid], fresh_by_scene[sid]) for sid in fresh_by_scene]
    allowed_ids = {r["id"] for r in current["recipes"]}
    bad = [s["shot_recipe_id"] for s in cleaned if s["shot_recipe_id"] and s["shot_recipe_id"] not in allowed_ids]
    if bad:
        raise ShotbookError("INVALID_RECIPE_ID", "镜头配方不存在于当前 Registry：" + ", ".join(bad[:3]))
    allowed_motion_ids = {m["id"] for m in current["motions"]}
    bad_motions = [s["motion_recipe_id"] for s in cleaned
                   if s["motion_recipe_id"] and allowed_motion_ids and s["motion_recipe_id"] not in allowed_motion_ids]
    if bad_motions:
        raise ShotbookError("INVALID_MOTION_RECIPE_ID",
                            "动效配方不存在于当前 Registry：" + ", ".join(bad_motions[:3]))
    for shot in cleaned:
        target = shot["motion_layer_target"]
        if target and target not in {layer["target_id"] for layer in shot["editable_layers"]}:
            raise ShotbookError("INVALID_MOTION_LAYER_TARGET",
                                shot["scene_id"] + " 的动效目标层已变化，请重新载入分镜。")
    plan = {
        "schema_version": SCHEMA_VERSION, "project_id": pid,
        "version": current["version"] + 1, "updated_at": int(time.time()),
        "shots": cleaned,
    }
    _write_plan(pid, plan)
    return get_project_shotbook(pid)



def generate_project_shotbook(project_id: str, base_version: int | None = None) -> dict:
    """Serialize generation and version validation across threads and server processes."""
    pid = _project_id(project_id)
    with _project_plan_lock(pid):
        return _generate_project_shotbook_unlocked(pid, base_version)


def save_project_shotbook(project_id: str, payload: dict) -> dict:
    """Serialize save, stale-version check, and atomic replacement as one transaction."""
    pid = _project_id(project_id)
    with _project_plan_lock(pid):
        return _save_project_shotbook_unlocked(pid, payload)
