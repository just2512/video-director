"""TalkCraft 风格的创作管线基础设施：时间戳、语义拍、镜头计划与硬门禁。"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from video_director import config
from video_director.creative_contracts import (
    CharacterTiming, CreativePlan, SemanticBeat, SentenceTiming, ShotLayerPlan, ShotPlan,
)

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PLAN_WRITE_LOCK = threading.RLock()


class CreativePlanError(ValueError):
    pass


def validate_project_id(project_id: str) -> str:
    value = str(project_id or "").strip()
    if value in {".", ".."} or not PROJECT_ID_RE.fullmatch(value):
        raise CreativePlanError("project_id 格式非法")
    return value


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_fingerprints(project_id: str, ir) -> dict[str, str]:
    """稿件与配音分别指纹化；没有可验证音频时 audio_hash 留空。"""
    pid = validate_project_id(project_id)
    script = "\n".join(str(getattr(scene, "text", "") or "") for scene in ir.scenes)
    base = config.project_dir(pid)
    candidates = (
        base / "audio" / "narration.mp3",
        base / "audio" / "narration.wav",
        base / "audio" / "full.wav",
        base / "input" / "audio" / "narration.mp3",
    )
    audio_file = next((path for path in candidates if path.is_file()), None)
    return {
        "script_hash": _hash_bytes(script.encode("utf-8")),
        "audio_hash": _hash_file(audio_file) if audio_file else "",
    }


def _norm_char(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return value if len(value) == 1 and value.isalnum() else ""


def _safe_ms(seconds: Any) -> int:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(value * 1000)))


def _word_units(words: Iterable[dict], source: str) -> list[tuple[str, int, int, str, bool]]:
    """展开词边界。多字符词内均分必须留下 interpolation 指纹。"""
    units = []
    native_source = "tts_native" if source == "tts_word_boundary" else "asr_native"
    for word in words:
        if not isinstance(word, dict):
            continue
        raw = str(word.get("text", "") or "")
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start:
            continue
        chars = [(ch, _norm_char(ch)) for ch in raw]
        chars = [(ch, key) for ch, key in chars if key]
        if not chars:
            continue
        start_ms, end_ms = _safe_ms(start), _safe_ms(end)
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        duration = end_ms - start_ms
        for index, (char, key) in enumerate(chars):
            left = start_ms + int(round(duration * index / len(chars)))
            right = start_ms + int(round(duration * (index + 1) / len(chars)))
            if right <= left:
                right = left + 1
            inferred = len(chars) > 1
            units.append((key, left, right,
                          "word_interpolation" if inferred else native_source,
                          inferred))
    return units


def build_sentence_timing(
    sentence_id: str, text: str, start_ms: int, end_ms: int,
    words: list[dict], source: str = "tts_word_boundary",
) -> SentenceTiming:
    """把配音词边界映射到原稿字符；未对齐文本不冒充真实字级声学时间。"""
    if not text.strip():
        raise CreativePlanError(f"{sentence_id}: 空白句子不能生成时间轴")
    if end_ms <= start_ms:
        raise CreativePlanError(f"{sentence_id}: 句子时间窗非法")
    source_key = source if source in {"tts_word_boundary", "asr", "manual"} else "missing"
    target = [(index, char, _norm_char(char)) for index, char in enumerate(text)]
    target = [(index, char, key) for index, char, key in target if key]
    timed = _word_units(words, source_key)
    if not target or not timed:
        return SentenceTiming(
            sentence_id=sentence_id, text=text, start_ms=start_ms, end_ms=end_ms,
            source=source_key if timed else "missing", status="MISSING", characters=[],
            review_reasons=["没有可用的逐字语音锚点；不能从句子时长伪造字级精度"],
        )

    matcher = difflib.SequenceMatcher(
        a=[entry[2] for entry in target], b=[entry[0] for entry in timed], autojunk=False,
    )
    assigned: dict[int, tuple[int, int, str, bool]] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            _, start, end, timing_source, review = timed[block.b + offset]
            assigned[block.a + offset] = (start, end, timing_source, review)
    if not assigned:
        return SentenceTiming(
            sentence_id=sentence_id, text=text, start_ms=start_ms, end_ms=end_ms,
            source=source_key, status="MISSING", characters=[],
            review_reasons=["稿件文本与时间戳文本没有可验证的匹配锚点"],
        )

    missing = [index for index in range(len(target)) if index not in assigned]
    review_reasons = []
    cursor = 0
    while cursor < len(missing):
        first = missing[cursor]
        last_cursor = cursor
        while last_cursor + 1 < len(missing) and missing[last_cursor + 1] == missing[last_cursor] + 1:
            last_cursor += 1
        group = missing[cursor:last_cursor + 1]
        left_anchor = first - 1
        while left_anchor >= 0 and left_anchor not in assigned:
            left_anchor -= 1
        right_anchor = group[-1] + 1
        while right_anchor < len(target) and right_anchor not in assigned:
            right_anchor += 1
        left_ms = assigned[left_anchor][1] if left_anchor >= 0 else start_ms
        right_ms = assigned[right_anchor][0] if right_anchor < len(target) else end_ms
        # 插值永远限制在句子时间窗内。若相邻锚点重叠/没有空间，允许字符时间窗重叠，
        # 但必须留 review_required；不能为了制造单调间隔把时间推到下一句或音频外。
        sentence_last_ms = max(start_ms + 1, end_ms)
        left_ms = min(sentence_last_ms - 1, max(start_ms, left_ms))
        right_ms = min(sentence_last_ms, max(start_ms + 1, right_ms))
        if right_ms <= left_ms:
            left_ms = start_ms
            right_ms = sentence_last_ms
            review_reasons.append("相邻锚点没有可分配间隔，回退到句内插值并需复核")
        span = max(1, right_ms - left_ms)
        for offset, target_index in enumerate(group):
            left = left_ms + int(round(span * offset / len(group)))
            right = left_ms + int(round(span * (offset + 1) / len(group)))
            left = min(sentence_last_ms - 1, max(start_ms, left))
            right = min(sentence_last_ms, max(left + 1, right))
            assigned[target_index] = (left, right, "text_interpolation", True)
        cursor = last_cursor + 1
        review_reasons.append("部分字符通过相邻锚点插值，需抽听复核")

    ordered = []
    previous_start = start_ms
    sentence_end = max(start_ms + 1, end_ms)
    for target_index, (original_index, char, _key) in enumerate(target):
        start, end, timing_source, review = assigned[target_index]
        original_start, original_end = start, end

        # Hard bound every character to its sentence. ASR/TTS can return words
        # slightly outside an IR scene; retain the fact as a review issue rather
        # than allowing impossible timestamps into the persisted contract.
        start = min(sentence_end - 1, max(start_ms, start))
        end = min(sentence_end, max(start + 1, end))
        if original_start != start or original_end != end:
            review = True
            timing_source = "text_interpolation"
            review_reasons.append("字符锚点越出句子时间窗，已裁回句内并需抽听复核")

        # Character order is an output invariant. Do not let a reversed ASR
        # anchor break the contract: monotonize the start while preserving a
        # valid positive interval, explicitly mark the resulting estimate.
        if start < previous_start:
            start = min(sentence_end - 1, previous_start)
            end = min(sentence_end, max(start + 1, end))
            review = True
            timing_source = "text_interpolation"
            review_reasons.append("检测到时间锚点倒序，已归一为单调时间并需抽听复核")

        if end <= start:
            start = min(start, sentence_end - 1)
            end = min(sentence_end, start + 1)
            review = True
            timing_source = "text_interpolation"
            review_reasons.append("字符时间窗无有效长度，已按句内最小时间窗修正并需复核")

        ordered.append(CharacterTiming(
            index=original_index, text=char, start_ms=start,
            end_ms=end, source=timing_source, review_required=review,
        ))
        previous_start = start

    status = "ALIGNED" if all(
        not char.review_required and char.source in {"tts_native", "asr_native"}
        for char in ordered
    ) else "PARTIAL"
    return SentenceTiming(
        sentence_id=sentence_id, text=text, start_ms=start_ms, end_ms=end_ms,
        source=source_key, status=status, characters=ordered,
        review_reasons=list(dict.fromkeys(review_reasons)),
    )


def load_word_timeline(project_id: str) -> tuple[list[dict], str]:
    """只读取项目已有的对齐产物，不触发隐式网络请求或模型下载。"""
    base = config.project_dir(validate_project_id(project_id))
    candidates = (
        (base / "alignment" / "words.json", "tts_word_boundary"),
        (base / "audio" / "words.json", "tts_word_boundary"),
        (base / "alignment" / "timestamps.json", "asr"),
        (base / "audio" / "timestamps.json", "asr"),
    )
    for path, default_source in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        declared_source = data.get("source", default_source) if isinstance(data, dict) else default_source
        source = "asr" if str(declared_source).lower() in {"asr", "whisper", "firered"} else default_source
        if isinstance(data, list):
            words = data
        elif isinstance(data, dict) and isinstance(data.get("words"), list):
            words = data["words"]
        elif isinstance(data, dict) and isinstance(data.get("sentences"), list):
            words = []
            for sentence in data["sentences"]:
                if not isinstance(sentence, dict):
                    continue
                for word in sentence.get("words") or []:
                    if not isinstance(word, dict):
                        continue
                    item = dict(word)
                    try:
                        offset = float(sentence.get("start", 0) or 0)
                        word_end = float(item.get("end", 0) or 0)
                        sentence_end = float(sentence.get("end", offset) or offset)
                    except (TypeError, ValueError):
                        continue
                    if offset > 0 and word_end <= sentence_end - offset + 0.05:
                        item["start"] = float(item.get("start", 0) or 0) + offset
                        item["end"] = word_end + offset
                    words.append(item)
        else:
            continue
        valid = [word for word in words if isinstance(word, dict) and word.get("text") is not None]
        if valid:
            return valid, source
    return [], "missing"


def build_initial_plan(
    project_id: str, ir, words: list[dict], source: str,
    audio_hash: str, version: int = 1,
) -> CreativePlan:
    pid = validate_project_id(project_id)
    fingerprints = project_fingerprints(pid, ir)
    source = source if source in {"tts_word_boundary", "asr"} else "missing"
    timings, beats, shots = [], [], []
    for scene in ir.scenes:
        text = str(getattr(scene, "text", "") or "").strip()
        start_s = float(getattr(scene, "start", 0) or 0)
        end_s = start_s + float(getattr(scene, "duration", 0) or 0)
        start_ms, end_ms = _safe_ms(start_s), _safe_ms(end_s)
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        beat_ids = []
        unresolved = []
        if text:
            scene_words = []
            for word in words:
                try:
                    word_start, word_end = float(word.get("start", 0)), float(word.get("end", 0))
                except (TypeError, ValueError):
                    continue
                if word_end > start_s and word_start < end_s:
                    scene_words.append(word)
            timing = build_sentence_timing(
                sentence_id=f"sentence:{scene.id}", text=text,
                start_ms=start_ms, end_ms=end_ms, words=scene_words, source=source,
            )
            timings.append(timing)
            beat_id = f"beat:{scene.id}"
            beat_ids.append(beat_id)
            beats.append(SemanticBeat(
                id=beat_id, sentence_id=timing.sentence_id, text=text,
                start_ms=timing.characters[0].start_ms if timing.characters else start_ms,
                end_ms=timing.characters[-1].end_ms if timing.characters else end_ms,
                intent=[], entities=[], visual_need=[], weight="main",
                shot_id=str(scene.id), timing_status=timing.status,
            ))
            unresolved.extend(["未确认镜头意图", "未配置画面素材或动效配方"])
        else:
            unresolved.append("镜头没有旁白文本；需要人工说明视觉目的")
        transition = getattr(getattr(scene, "transition_out", None), "type", None)
        transition = getattr(transition, "value", transition)
        shots.append(ShotPlan(
            shot_id=str(scene.id), start_ms=start_ms, end_ms=end_ms, intent="",
            beat_ids=beat_ids, layers=[], transition_id=str(transition) if transition else None,
            unresolved_items=unresolved,
        ))
    return CreativePlan(
        project_id=pid, version=version, script_hash=fingerprints["script_hash"],
        audio_hash=audio_hash or fingerprints["audio_hash"], timing_source=source,
        character_timings=timings, semantic_beats=beats, shots=shots,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _shot_registry() -> dict[str, Any]:
    from video_director.registries.recipes import ShotRegistry
    return {str(item.id): item for item in ShotRegistry().all()}


def _motion_registry() -> dict[str, Any]:
    from video_director.registries.recipes import MotionRegistry
    return {str(item.id): item for item in MotionRegistry().all()}


def _known_project_assets(project_id: str) -> dict[str, dict]:
    """只接受资产库/项目 manifest 中登记过的 ID；自由输入路径不直接交给浏览器渲染。"""
    pid = validate_project_id(project_id)
    found: dict[str, dict] = {}
    try:
        from video_director import asset_catalog
        for item in asset_catalog.list_assets():
            aid = str(item.get("asset_id") or item.get("id") or "")
            path = str(item.get("file") or "")
            if aid and path:
                found[aid] = {**item, "asset_id": aid, "file": path}
    except Exception:
        pass

    manifest = config.project_dir(pid) / "assets" / "manifest.json"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                entries = payload.get("assets", list(payload.values()))
            elif isinstance(payload, list):
                entries = payload
            else:
                entries = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                aid = str(item.get("asset_id") or item.get("id") or "")
                path = str(item.get("file") or "")
                if aid and path:
                    found[aid] = {**found.get(aid, {}), **item, "asset_id": aid, "file": path}
        except (OSError, json.JSONDecodeError):
            pass
    return found


def _asset_type_matches(slot_type: str, asset_type: str) -> bool:
    slot_type = str(slot_type or "").lower()
    asset_type = str(asset_type or "").lower()
    compatibility = {
        "video": {"video"},
        "image": {"image"},
        "product": {"image"},
        "screen": {"video", "image"},
        "person": {"video", "image"},
        "graphic": {"image"},
        "logo": {"image"},
        "text": set(),
    }
    return asset_type in compatibility.get(slot_type, set())


def validate_creative_plan(plan: CreativePlan, ir, project_id: str) -> dict:
    """硬门禁输出 PASS/REJECT/MISSING/NEEDS_REVIEW 与具体原因；不输出评分。"""
    pid = validate_project_id(project_id)
    issues: list[dict] = []

    def add(code: str, status: str, message: str, shot_id: str = "", beat_id: str = ""):
        issues.append({"code": code, "status": status, "message": message,
                       "shot_id": shot_id, "beat_id": beat_id})

    if plan.project_id != pid:
        add("PROJECT_MISMATCH", "REJECT", "创作计划属于其他项目")
    fingerprints = project_fingerprints(pid, ir)
    if plan.script_hash != fingerprints["script_hash"]:
        add("SCRIPT_STALE", "REJECT", "稿件已变化，原语义拍与时间戳失效；请重新生成草案")
    if plan.audio_hash and fingerprints["audio_hash"] and plan.audio_hash != fingerprints["audio_hash"]:
        add("AUDIO_STALE", "REJECT", "配音已变化，原时间戳失效；请重新对齐")
    if not plan.audio_hash or not fingerprints["audio_hash"]:
        add("AUDIO_HASH_MISSING", "MISSING", "当前项目没有可校验的配音文件指纹")

    scenes = {str(scene.id): scene for scene in ir.scenes}
    shots = {shot.shot_id: shot for shot in plan.shots}
    beats = {beat.id: beat for beat in plan.semantic_beats}
    timings = {timing.sentence_id: timing for timing in plan.character_timings}
    for scene_id, scene in scenes.items():
        shot = shots.get(scene_id)
        if shot is None:
            add("SHOT_MISSING", "MISSING", f"场景 {scene_id} 没有对应 ShotPlan", scene_id)
            continue
        expected_start = _safe_ms(scene.start)
        expected_end = _safe_ms(float(scene.start) + float(scene.duration))
        fps = max(1, int(getattr(ir.project, "fps", 30) or 30))
        tolerance_ms = int(round(1000 / fps))
        if abs(shot.start_ms - expected_start) > tolerance_ms or abs(shot.end_ms - expected_end) > tolerance_ms:
            add("SHOT_TIME_MISMATCH", "REJECT", "ShotPlan 镜头边界与当前 CompositionIR 不一致", scene_id)
        if not shot.intent.strip():
            add("SHOT_INTENT_MISSING", "MISSING", "未填写镜头叙事意图", scene_id)
        if not shot.layers:
            add("SHOT_LAYER_MISSING", "MISSING", "镜头没有配置素材层、证据层或动效配方", scene_id)
        for layer in shot.layers:
            if not layer.recipe_id and not layer.asset_ids and not layer.evidence_refs:
                add("LAYER_TARGET_MISSING", "MISSING", f"图层 {layer.role} 没有真实配方或素材引用", scene_id)
            if layer.recipe_id:
                try:
                    recipes = _shot_registry()
                    motions = _motion_registry()
                except Exception as exc:
                    add("RECIPE_REGISTRY_UNAVAILABLE", "REJECT",
                        f"无法加载 Shot/Motion Registry：{exc}", scene_id)
                    recipes, motions = {}, {}
                recipe = recipes.get(layer.recipe_id)
                if recipe is None:
                    add("SHOT_RECIPE_UNAVAILABLE", "REJECT",
                        f"镜头配方 {layer.recipe_id} 不在当前 Shot Registry 中", scene_id)
                else:
                    if recipe.id in {"shot.soft-transition-breathe"} and len(layer.asset_ids) + len(layer.evidence_refs) < 2:
                        add("SHOT_RECIPE_INPUT_MISSING", "MISSING",
                            f"{recipe.name} 需要两个视频/图片资产，当前只声明了 {len(layer.asset_ids) + len(layer.evidence_refs)} 个",
                            scene_id)
                    for slot in recipe.slots:
                        if slot.type == "text":
                            if slot.required and not any(
                                beat.text.strip() for beat in plan.semantic_beats
                                if beat.id in shot.beat_ids
                            ):
                                add("SHOT_SLOT_TEXT_MISSING", "MISSING",
                                    f"镜头配方 {recipe.name} 需要文本输入 {slot.id}", scene_id)
                            continue
                        candidates = []
                        assets = _known_project_assets(pid)
                        for aid in list(layer.asset_ids) + list(layer.evidence_refs):
                            asset = assets.get(aid)
                            if asset and _asset_type_matches(slot.type, str(asset.get("type") or "")):
                                candidates.append(asset)
                        needed = len(candidates) if slot.cardinality == "many" else min(1, len(candidates))
                        if slot.required and needed == 0:
                            add("SHOT_SLOT_ASSET_MISSING", "MISSING",
                                f"镜头配方 {recipe.name} 的必需素材槽 {slot.id}（{slot.type}）没有可用资产",
                                scene_id)
                        for asset in candidates:
                            asset_path = Path(str(asset.get("file") or "")).expanduser()
                            if not asset_path.is_file():
                                add("ASSET_FILE_MISSING", "REJECT",
                                    f"资产 {asset.get('asset_id')} 的文件不存在：{asset_path}", scene_id)
                    for item in recipe.layer_plan:
                        motion_id = item.motion
                        if not motion_id:
                            continue
                        motion = motions.get(motion_id)
                        if motion is None:
                            add("MOTION_RECIPE_UNAVAILABLE", "REJECT",
                                f"镜头配方 {recipe.id} 引用的动效 {motion_id} 不在 Motion Registry",
                                scene_id)
                            continue
                        supported_properties = {"opacity", "x", "y", "scale", "scaleX", "scaleY", "rotation", "blur"}
                        unsupported = sorted({
                            channel.property for channel in motion.channels
                            if channel.property not in supported_properties
                        })
                        if unsupported:
                            add("MOTION_CHANNEL_UNSUPPORTED", "REJECT",
                                f"动效 {motion_id} 含 Remotion 适配器未实现的通道：{', '.join(unsupported)}",
                                scene_id)

    valid_scene_ids, valid_beat_ids = set(scenes), set(beats)
    for shot in plan.shots:
        if shot.shot_id not in valid_scene_ids:
            add("SHOT_UNKNOWN", "REJECT", f"ShotPlan 指向不存在的场景 {shot.shot_id}", shot.shot_id)
        for beat_id in shot.beat_ids:
            if beat_id not in valid_beat_ids:
                add("SHOT_BEAT_UNKNOWN", "REJECT", f"镜头引用了不存在的语义拍 {beat_id}", shot.shot_id, beat_id)
    for beat in plan.semantic_beats:
        if beat.shot_id not in valid_scene_ids:
            add("BEAT_SHOT_UNKNOWN", "REJECT", "语义拍没有归属到当前存在的镜头", beat.shot_id or "", beat.id)
        elif beat.shot_id in shots and beat.id not in shots[beat.shot_id].beat_ids:
            add("BEAT_SHOT_LINK_MISMATCH", "REJECT", "语义拍与镜头的双向引用不一致", beat.shot_id, beat.id)
        scene = scenes.get(beat.shot_id or "")
        if scene and beat.text.strip() != str(getattr(scene, "text", "") or "").strip():
            add("BEAT_TEXT_STALE", "REJECT", "语义拍文本与当前旁白文本不一致", beat.shot_id or "", beat.id)
        if not beat.intent:
            add("BEAT_INTENT_MISSING", "MISSING", "未标注这句话的语义意图", beat.shot_id or "", beat.id)
        if not beat.visual_need:
            add("BEAT_VISUAL_NEED_MISSING", "MISSING", "未声明这句话需要什么画面表达", beat.shot_id or "", beat.id)
        timing = timings.get(beat.sentence_id)
        if timing is None:
            add("SENTENCE_TIMING_MISSING", "MISSING", "语义拍没有对应的句级时间记录", beat.shot_id or "", beat.id)
        elif timing.status == "MISSING":
            add("BEAT_TIMING_MISSING", "MISSING", "没有可验证的字符时间锚点", beat.shot_id or "", beat.id)
            add("CHARACTER_TIMING_MISSING", "MISSING", "句级没有可用字符锚点", beat.shot_id or "", beat.id)
        elif timing.status == "PARTIAL" and not timing.reviewed:
            add("BEAT_TIMING_REVIEW", "NEEDS_REVIEW",
                "部分字符时间来自插值或异常锚点；完成试听复核并勾选确认后才能放行",
                beat.shot_id or "", beat.id)
            add("CHARACTER_TIMING_REVIEW", "NEEDS_REVIEW",
                "字级时间轴含插值或异常位置且尚未人工确认", beat.shot_id or "", beat.id)

    statuses = {issue["status"] for issue in issues}
    verdict = (
        "REJECT" if "REJECT" in statuses else
        "MISSING" if "MISSING" in statuses else
        "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS"
    )
    return {
        "verdict": verdict, "issues": issues,
        "counts": {status: sum(1 for issue in issues if issue["status"] == status)
                   for status in ("REJECT", "MISSING", "NEEDS_REVIEW")},
    }


def plan_file(project_id: str) -> Path:
    return config.project_dir(validate_project_id(project_id)) / "plans" / "creative_plan.json"


def load_plan(project_id: str) -> CreativePlan | None:
    path = plan_file(project_id)
    if not path.is_file():
        return None
    try:
        return CreativePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CreativePlanError(f"创作计划文件损坏或契约不兼容: {exc}") from exc


def save_plan(plan: CreativePlan, expected_version: int | None = None) -> CreativePlan:
    """原子写入 + 版本检查，避免并发页面互相覆盖。"""
    pid = validate_project_id(plan.project_id)
    with _PLAN_WRITE_LOCK:
        path = plan_file(pid)
        current = load_plan(pid)
        if expected_version is not None:
            actual = current.version if current else 0
            if actual != expected_version:
                raise CreativePlanError(
                    f"创作计划版本冲突：页面版本 {expected_version}，磁盘版本 {actual}；请刷新后重试"
                )
        next_version = (current.version + 1) if current else max(1, plan.version)
        saved = plan.model_copy(update={
            "version": next_version, "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return saved
