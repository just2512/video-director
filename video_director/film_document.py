"""OpenFilm-inspired film.html document protocol and ID-addressed editor operations."""
from __future__ import annotations

import copy
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import quote, unquote

from video_director import config
from video_director.project_lock import project_lock, validate_project_id

FILM_NAME = "film.html"
HISTORY_NAME = ".film-history.json"
MIGRATION_NAME = "film-migration.json"
CLIP_TAGS = {"iframe", "video", "img", "audio"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SETTABLE_FIELDS = {"at", "src", "speed", "volume", "volume_envelope", "fade", "style", "class", "overrides", "muted", "title", "alt", "lang", "dir", "size"}
AI_SETTABLE_FIELDS = {"at", "speed", "volume", "volume_envelope", "fade", "style", "class", "muted", "title"}
TRACK_FLAGS = {"locked", "hidden", "muted", "sync-lock"}
MAX_OPERATIONS = 200


class FilmError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(message)


def _number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise FilmError("INVALID_NUMBER", f"{label} 必须是有限数字")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise FilmError("INVALID_NUMBER", f"{label} 必须是有限数字") from None
    if not math.isfinite(result):
        raise FilmError("INVALID_NUMBER", f"{label} 必须是有限数字")
    if minimum is not None and result < minimum:
        raise FilmError("INVALID_NUMBER", f"{label} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise FilmError("INVALID_NUMBER", f"{label} 不能大于 {maximum}")
    return result


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _safe_id(value: str, prefix: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-_")[:72] or prefix


def _source_range(src: str) -> tuple[float, float | None]:
    match = re.search(r"#t=([0-9]+(?:\.[0-9]+)?)(?:,([0-9]+(?:\.[0-9]+)?))?$", src)
    if not match:
        return 0.0, None
    start = float(match.group(1))
    end = float(match.group(2)) if match.group(2) is not None else None
    if end is not None and end <= start:
        raise FilmError("INVALID_SOURCE_RANGE", f"素材源区间无效：{src}")
    return start, end


def _with_source_range(src: str, start: float, end: float) -> str:
    return re.sub(r"#t=[^#]*$", "", src) + f"#t={_fmt(start)},{_fmt(end)}"


def _validate_src_syntax(src: Any) -> str:
    """Only accept local relative asset paths; encoded absolute paths are rejected too."""
    if not isinstance(src, str) or not src.strip() or len(src) > 4096:
        raise FilmError("INVALID_SRC", "src 必须是非空相对路径字符串")
    value = src.strip()
    path = unquote(value.split("#", 1)[0]).strip()
    if (not path or path.startswith(("/", "\\")) or "\\" in path
            or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", path)
            or path.startswith("//")):
        raise FilmError("INVALID_SRC", "影片素材 src 只能使用系统存储范围内的相对路径")
    _source_range(value)
    return value


def clip_duration(clip: dict) -> float | None:
    attrs = clip.get("attrs") or {}
    src_in, src_out = _source_range(str(attrs.get("src") or ""))
    if src_out is None:
        return None
    speed = _number(attrs.get("speed", 1), "speed", 0.25, 4) if clip.get("tag") in {"video", "audio"} else 1.0
    return (src_out - src_in) / speed


def clip_end(clip: dict) -> float | None:
    duration = clip_duration(clip)
    return None if duration is None else _number(clip["attrs"].get("at", 0), "at", 0) + duration


def _parse_fade(value: Any) -> tuple[float, float]:
    """Parse the optional clip-relative visual/audio fade durations (in,out) in seconds."""
    if value is None or value == "":
        return 0.0, 0.0
    if isinstance(value, (list, tuple)) and len(value) == 2:
        parts = value
    elif isinstance(value, str):
        parts = value.split(",")
    else:
        raise FilmError("INVALID_FADE", "fade 必须是淡入秒数,淡出秒数", 422)
    if len(parts) != 2:
        raise FilmError("INVALID_FADE", "fade 必须是淡入秒数,淡出秒数", 422)
    fade_in = _number(parts[0], "淡入时长", 0, 60)
    fade_out = _number(parts[1], "淡出时长", 0, 60)
    return fade_in, fade_out


def _store_fade(attrs: dict, value: str | None) -> None:
    if value is None:
        attrs.pop("fade", None)
    else:
        attrs["fade"] = value


def _split_fade(value: Any, duration: float, offset: float) -> tuple[str | None, str | None]:
    """Partition one linear fade pair across a cut without changing the retained outer fades."""
    fade_in, fade_out = _parse_fade(value)
    if fade_in <= 0 and fade_out <= 0:
        return None, None
    if not math.isfinite(duration) or not math.isfinite(offset) or offset <= 0 or offset >= duration:
        raise FilmError("FADE_EDIT_BOUNDARY", "淡化切点必须位于片段内部", 422)
    # A single in/out pair cannot represent a partial fade inherited at a new
    # segment start. Only cut in the fully visible middle interval.
    if offset < fade_in - 0.001 or offset > duration - fade_out + 0.001:
        raise FilmError(
            "FADE_EDIT_BOUNDARY",
            "切点落在淡入或淡出区间；为避免预览和导出效果变化，请先移除淡化或调整切点",
            422,
        )
    left_value = f"{_fmt(fade_in)},0" if fade_in > 0 else None
    right_value = f"0,{_fmt(fade_out)}" if fade_out > 0 else None
    return left_value, right_value


def _trim_fade(value: Any, duration: float, offset: float, edge: str) -> str | None:
    """Keep the original fade only when a trim can represent it exactly."""
    fade_in, fade_out = _parse_fade(value)
    if fade_in <= 0 and fade_out <= 0:
        return None
    if not math.isfinite(duration) or not math.isfinite(offset) or offset <= 0 or offset >= duration:
        raise FilmError("FADE_EDIT_BOUNDARY", "淡化修剪点必须位于片段内部", 422)
    if edge == "start":
        if offset < fade_in - 0.001 or offset > duration - fade_out + 0.001:
            raise FilmError(
                "FADE_EDIT_BOUNDARY",
                "起点修剪切入了淡入或淡出区间；请先移除淡化或调整切点",
                422,
            )
        return f"0,{_fmt(fade_out)}" if fade_out > 0 else None
    if edge == "end":
        if offset < fade_in - 0.001:
            raise FilmError(
                "FADE_EDIT_BOUNDARY",
                "终点修剪截断了淡入区间；请先移除淡化或调整切点",
                422,
            )
        if offset < duration - 0.001 and offset > duration - fade_out + 0.001:
            raise FilmError(
                "FADE_EDIT_BOUNDARY",
                "终点修剪切入了淡出区间；请先移除淡化或调整切点",
                422,
            )
        return f"{_fmt(fade_in)},0" if fade_in > 0 else None
    raise FilmError("INVALID_TRIM_EDGE", "trim edge 只能是 start 或 end", 422)


def _normalize_fade(value: Any, clip: dict) -> str | None:
    fade_in, fade_out = _parse_fade(value)
    duration = clip_duration(clip)
    if duration is not None and fade_in + fade_out > duration + 0.001:
        raise FilmError(
            "INVALID_FADE",
            f"淡入和淡出合计 {fade_in + fade_out:.3f}s，超过片段时长 {duration:.3f}s",
            422,
        )
    if fade_in <= 0 and fade_out <= 0:
        return None
    return _fmt(fade_in) + "," + _fmt(fade_out)


def _parse_volume_envelope(value: Any) -> list[dict[str, float]]:
    """Parse an optional clip-relative gain envelope: [{t: seconds, v: 0..1}, ...]."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FilmError("INVALID_VOLUME_ENVELOPE", f"音量包络不是有效 JSON：{exc.msg}", 422) from None
    if not isinstance(value, list) or not 2 <= len(value) <= 128:
        raise FilmError("INVALID_VOLUME_ENVELOPE", "音量包络必须是 2–128 个时间点的数组", 422)
    points: list[dict[str, float]] = []
    previous_time = -1.0
    for index, point in enumerate(value):
        if not isinstance(point, dict) or set(point) != {"t", "v"}:
            raise FilmError("INVALID_VOLUME_ENVELOPE", f"音量包络第 {index + 1} 点必须只包含 t、v", 422)
        timestamp = _number(point.get("t"), f"音量包络时间点 {index + 1}", 0, 600)
        gain = _number(point.get("v"), f"音量包络值 {index + 1}", 0, 1)
        if timestamp <= previous_time:
            raise FilmError("INVALID_VOLUME_ENVELOPE", "音量包络时间点必须严格递增且不能重复", 422)
        points.append({"t": timestamp, "v": gain})
        previous_time = timestamp
    return points


def _normalize_volume_envelope(value: Any) -> str:
    points = _parse_volume_envelope(value)
    if not points:
        raise FilmError("INVALID_VOLUME_ENVELOPE", "音量包络不能为空；清除时请将字段设为 null", 422)
    canonical = [{"t": float(_fmt(point["t"])), "v": float(_fmt(point["v"]))} for point in points]
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))

def _volume_envelope_value(points: list[dict[str, float]], timestamp: float) -> float:
    if not points:
        return 1.0
    if timestamp <= points[0]["t"]:
        return points[0]["v"]
    for left, right in zip(points, points[1:]):
        if timestamp <= right["t"]:
            amount = (timestamp - left["t"]) / (right["t"] - left["t"])
            return left["v"] + (right["v"] - left["v"]) * min(1.0, max(0.0, amount))
    return points[-1]["v"]


def _slice_volume_envelope(attrs: dict, old_start: float, old_end: float) -> None:
    """Rebase an envelope to a retained subrange of the old clip-local timeline."""
    if "volume_envelope" not in attrs:
        return
    if not math.isfinite(old_start) or not math.isfinite(old_end) or old_start < 0 or old_end <= old_start:
        raise FilmError("INVALID_VOLUME_ENVELOPE", "不能将音量包络切片到无效的片段区间", 422)
    points = _parse_volume_envelope(attrs["volume_envelope"])
    if not points:
        attrs.pop("volume_envelope", None)
        return
    rebased = [{"t": 0.0, "v": _volume_envelope_value(points, old_start)}]
    for point in points:
        if old_start + 1e-7 < point["t"] < old_end - 1e-7:
            rebased.append({"t": point["t"] - old_start, "v": point["v"]})
    rebased.append({"t": old_end - old_start, "v": _volume_envelope_value(points, old_end)})
    attrs["volume_envelope"] = _normalize_volume_envelope(rebased)


def revision_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class _FilmParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.in_body = False
        self.body_seen = False
        self.body_closed = False
        self.tracks = []
        self.current_track = None
        self.problems = []
        self.clip_stack = []

    def handle_starttag(self, tag, attrs):
        attr_map = {}
        for key, value in attrs:
            if key in attr_map:
                self.problems.append(f"重复属性：{key}")
            attr_map[key] = "" if value is None else value
        parent = self.stack[-1] if self.stack else ""
        if tag == "body":
            if self.body_seen:
                self.problems.append("存在多个 body 元素")
            self.body_seen, self.in_body = True, True
            self.stack.append(tag)
            return
        if tag == "section" and self.in_body:
            if parent != "body":
                self.problems.append("section 必须是 body 的直接子元素")
            if self.current_track is not None:
                self.problems.append("轨道不能嵌套")
            tid = attr_map.get("id") or f"track-{len(self.tracks) + 1:03d}"
            attr_map["id"] = tid
            self.current_track = {"id": tid, "attrs": attr_map, "clips": []}
            self.tracks.append(self.current_track)
            self.stack.append(tag)
            return
        if tag in CLIP_TAGS and self.in_body:
            if parent != "section" or self.current_track is None:
                self.problems.append(f"{tag} 必须是 section 的直接子元素")
                self.stack.append(tag)
                return
            if self.clip_stack:
                self.problems.append(f"片段元素不得嵌套：{tag}")
            src = str(attr_map.get("src") or "").strip()
            if not src:
                self.problems.append(f"{tag} 缺少 src")
            else:
                try:
                    _validate_src_syntax(src)
                except FilmError as exc:
                    self.problems.append(str(exc))
            if tag in {"img", "iframe"} and src:
                src_in, src_out = _source_range(src)
                if src_out is None or abs(src_in) > 0.001:
                    self.problems.append(f"{tag} 必须使用 #t=0,length 指定片段时长")
            at = attr_map.get("at", "0")
            try:
                _number(at, "at", 0)
            except FilmError as exc:
                self.problems.append(str(exc))
            cid = attr_map.get("id")
            if not cid:
                key = f"{self.current_track['id']}|{src}|{at}|{tag}"
                cid = "clip-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
                attr_map["id"] = cid
            self.current_track["clips"].append({"id": cid, "tag": tag, "attrs": attr_map})
            if tag not in VOID_TAGS:
                self.clip_stack.append({"tag": tag, "text": ""})
                self.stack.append(tag)
            return
        if self.in_body and tag != "html":
            self.problems.append(f"body 中不允许 {tag}；只接受 section 与媒体片段")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body, self.body_closed = False, True
        if tag in CLIP_TAGS and self.clip_stack:
            if self.clip_stack[-1]["tag"] != tag:
                self.problems.append(f"片段结束标签不匹配：{tag}")
            else:
                if self.clip_stack[-1]["text"].strip():
                    self.problems.append(f"{tag} 必须为空元素")
                self.clip_stack.pop()
        if tag == "section":
            self.current_track = None
        if tag in self.stack:
            idx = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[idx:]

    def handle_data(self, data):
        if self.clip_stack:
            self.clip_stack[-1]["text"] += data
            if data.strip():
                self.problems.append(f"{self.clip_stack[-1]['tag']} 必须为空元素")


def parse_film(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise FilmError("INVALID_FILM", "film.html 为空", 422)
    head_match = re.search(r"<head\b[^>]*>.*?</head\s*>", text, re.I | re.S)
    if not head_match:
        raise FilmError("INVALID_FILM", "film.html 缺少完整 head 元素", 422)
    head = head_match.group(0)
    if re.search(r"<script\b", head, re.I):
        raise FilmError("INVALID_FILM", "film.html 的 head 禁止 script；动态画面应放在 scenes/*.html", 422)
    parser = _FilmParser()
    parser.feed(text)
    parser.close()
    problems = list(dict.fromkeys(parser.problems))
    if not parser.body_seen or not parser.body_closed:
        problems.append("body 标签不完整")
    track_ids = [t["id"] for t in parser.tracks]
    clip_ids = [c["id"] for t in parser.tracks for c in t["clips"]]
    if len(track_ids) != len(set(track_ids)):
        problems.append("轨道 id 必须唯一")
    if len(clip_ids) != len(set(clip_ids)):
        problems.append("片段 id 必须全片唯一")
    if not parser.tracks:
        problems.append("film.html 至少需要一个 section 轨道")
    for track in parser.tracks:
        ordered = sorted(track["clips"], key=lambda c: _number(c["attrs"].get("at", 0), "at", 0))
        last_end = None
        last_id = ""
        for clip in ordered:
            if "volume_envelope" in clip["attrs"]:
                try:
                    _parse_volume_envelope(clip["attrs"]["volume_envelope"])
                except FilmError as exc:
                    problems.append(f"片段 {clip['id']} 的音量包络无效：{exc}")
            if "fade" in clip["attrs"]:
                try:
                    fade_in, fade_out = _parse_fade(clip["attrs"]["fade"])
                    fade_duration = clip_duration(clip)
                    if fade_duration is not None and fade_in + fade_out > fade_duration + 0.001:
                        problems.append(
                            f"片段 {clip['id']} 的淡入淡出合计超过片段时长"
                        )
                except FilmError as exc:
                    problems.append(f"片段 {clip['id']} 的 fade 无效：{exc}")
            end = clip_end(clip)
            at = _number(clip["attrs"].get("at", 0), "at", 0)
            if end is not None and last_end is not None and at < last_end - 0.001:
                problems.append(f"轨道 {track['id']} 片段重叠：{last_id} 与 {clip['id']}")
            last_end = (max(last_end or 0, end) if end is not None else None)
            last_id = clip["id"]
    if problems:
        raise FilmError("INVALID_FILM", "film.html 校验失败：\n- " + "\n- ".join(problems), 422)
    viewport = re.search(r'<meta\b[^>]*\bname\s*=\s*["\']?viewport["\']?[^>]*\bcontent\s*=\s*["\']width\s*=\s*(\d+)\s*,?\s*height\s*=\s*(\d+)', head, re.I)
    stage = {"width": int(viewport.group(1)), "height": int(viewport.group(2))} if viewport else {"width": 1920, "height": 1080}
    return {"stage": stage, "head_html": head, "tracks": parser.tracks}


def _serialize_attrs(attrs: dict[str, Any]) -> str:
    parts = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        parts.append(f' {key}=""' if value is True else f' {key}="{html.escape(str(value), quote=True)}"')
    return "".join(parts)


def serialize_film(doc: dict) -> str:
    stage = doc.get("stage") or {"width": 1920, "height": 1080}
    head = doc.get("head_html") or '<head><meta charset="utf-8"></head>'
    viewport = f'<meta name="viewport" content="width={int(stage["width"])},height={int(stage["height"])}">'
    if re.search(r"<meta\b[^>]*\bname\s*=\s*['\"]?viewport", head, re.I):
        head = re.sub(r"<meta\b[^>]*\bname\s*=\s*['\"]?viewport['\"]?[^>]*>", viewport, head, count=1, flags=re.I)
    else:
        head = head.replace("</head>", viewport + "</head>", 1)
    rows = []
    for track in doc.get("tracks") or []:
        attrs = dict(track.get("attrs") or {})
        attrs["id"] = track["id"]
        row = [f"<section{_serialize_attrs(attrs)}>"]
        for clip in sorted(track.get("clips") or [], key=lambda c: _number(c["attrs"].get("at", 0), "at", 0)):
            attrs = dict(clip.get("attrs") or {})
            attrs["id"] = clip["id"]
            row.append(f"<img{_serialize_attrs(attrs)}>" if clip["tag"] == "img" else f"<{clip['tag']}{_serialize_attrs(attrs)}></{clip['tag']}>")
        row.append("</section>")
        rows.append("\n".join(row))
    return "<!doctype html>\n<html>\n" + head + "\n<body>\n" + "\n".join(rows) + "\n</body>\n</html>\n"


def _find_clip(doc: dict, clip_id: str) -> tuple[dict, dict]:
    if not clip_id:
        raise FilmError("CLIP_ID_REQUIRED", "操作缺少 clip id")
    for track in doc["tracks"]:
        for clip in track["clips"]:
            if clip["id"] == clip_id:
                return track, clip
    raise FilmError("CLIP_NOT_FOUND", f"片段不存在或已被删除：{clip_id}", 409)


def _find_track(doc: dict, track_id: str) -> dict:
    for track in doc["tracks"]:
        if track["id"] == track_id:
            return track
    raise FilmError("TRACK_NOT_FOUND", f"轨道不存在：{track_id}", 409)


def _ensure_unlocked(track: dict, clip: dict | None = None):
    if "locked" in (track.get("attrs") or {}):
        raise FilmError("TRACK_LOCKED", f"轨道 {track['id']} 已锁定，不能编辑", 409)
    if clip is not None and "data-clip-locked" in (clip.get("attrs") or {}):
        raise FilmError("CLIP_LOCKED", f"片段 {clip['id']} 已锁定，不能编辑", 409)


def _validate_doc(doc: dict):
    track_ids = [t["id"] for t in doc["tracks"]]
    clip_ids = [c["id"] for t in doc["tracks"] for c in t["clips"]]
    if len(track_ids) != len(set(track_ids)) or len(clip_ids) != len(set(clip_ids)):
        raise FilmError("DUPLICATE_ID", "轨道 ID 与片段 ID 必须唯一")
    if not doc["tracks"]:
        raise FilmError("NO_TRACKS", "影片至少需要一条轨道")
    for track in doc["tracks"]:
        end_seen = None
        for clip in sorted(track["clips"], key=lambda c: _number(c["attrs"].get("at", 0), "at", 0)):
            attrs = clip["attrs"]
            _number(attrs.get("at", 0), "at", 0)
            if clip["tag"] in {"video", "audio"}:
                if "speed" in attrs:
                    _number(attrs["speed"], "speed", 0.25, 4)
                if "volume" in attrs:
                    _number(attrs["volume"], "volume", 0, 2)
            end = clip_end(clip)
            at = _number(attrs.get("at", 0), "at", 0)
            if end is not None and end_seen is not None and at < end_seen - 0.001:
                raise FilmError("TRACK_OVERLAP", f"轨道 {track['id']} 片段 {clip['id']} 与前一片段重叠")
            end_seen = end if end is not None else None


def _apply_ripple_delete(doc: dict, clip_id: str, requested_split_ids: Any = None) -> dict:
    """Delete the selected clip's timeline interval and ripple every unlocked track."""
    target_track, target_clip = _find_clip(doc, clip_id)
    _ensure_unlocked(target_track, target_clip)
    cut_start = _number(target_clip.get("attrs", {}).get("at", 0), "at", 0)
    cut_end = clip_end(target_clip)
    if cut_end is None or not math.isfinite(cut_end) or cut_end <= cut_start:
        raise FilmError("RIPPLE_DURATION_UNKNOWN", "波纹删除要求目标片段有明确且为正的时长", 422)
    duration = cut_end - cut_start
    eps = 1e-7

    if requested_split_ids is None:
        requested_split_ids = {}
    if not isinstance(requested_split_ids, dict):
        raise FilmError("INVALID_SPLIT_IDS", "split_ids 必须是对象映射", 422)
    requested_split_ids = {str(key): str(value) for key, value in requested_split_ids.items()}
    known_ids = {clip["id"] for track in doc["tracks"] for clip in track.get("clips", [])}
    used_ids = set(known_ids)
    split_ids: dict[str, str] = {}
    removed_ids: list[str] = []
    shifted_ids: list[str] = []

    # Reject the whole transaction before mutation when ripple would touch any
    # locked track or locked clip, including clips that would be shifted/split.
    for track in doc["tracks"]:
        track_locked = "locked" in (track.get("attrs") or {})
        for clip in track.get("clips") or []:
            attrs = clip.get("attrs") or {}
            at = _number(attrs.get("at", 0), "at", 0)
            end = clip_end(clip)
            affected = (
                clip["id"] == clip_id
                or (end is None and at >= cut_start - eps)
                or (end is not None and (at >= cut_end - eps or end > cut_start + eps))
            )
            if affected and (track_locked or "data-clip-locked" in attrs):
                code = "TRACK_LOCKED" if track_locked else "CLIP_LOCKED"
                target = track["id"] if track_locked else clip["id"]
                if track_locked:
                    message = f"锁定轨道 {target} 将受到波纹删除影响；整笔操作已拒绝 [TRACK_LOCKED]"
                else:
                    message = f"波纹删除会影响已锁定对象 {target}；整笔操作已拒绝 [CLIP_LOCKED]"
                raise FilmError(code, message, 409)

    for track in doc["tracks"]:
        rewritten: list[dict] = []
        for clip in track.get("clips") or []:
            cid = clip["id"]
            attrs = clip.get("attrs") or {}
            at = _number(attrs.get("at", 0), "at", 0)
            end = clip_end(clip)

            if cid == clip_id:
                removed_ids.append(cid)
                continue

            if end is None:
                if at >= cut_end - eps:
                    clip["attrs"]["at"] = _fmt(max(0.0, at - duration))
                    shifted_ids.append(cid)
                    rewritten.append(clip)
                    continue
                raise FilmError(
                    "RIPPLE_DURATION_UNKNOWN",
                    f"片段 {cid} 的时长未知，无法判断它与波纹删除区间的关系",
                    422,
                )

            if end <= cut_start + eps:
                rewritten.append(clip)
                continue

            if at >= cut_end - eps:
                clip["attrs"]["at"] = _fmt(max(0.0, at - duration))
                shifted_ids.append(cid)
                rewritten.append(clip)
                continue

            source_in, source_out = _source_range(str(attrs.get("src") or ""))
            if source_out is None:
                raise FilmError("RIPPLE_DURATION_UNKNOWN", f"片段 {cid} 缺少明确的源出点", 422)
            speed = (
                _number(attrs.get("speed", 1), "speed", 0.25, 4)
                if clip["tag"] in {"video", "audio"} else 1.0
            )
            left_duration = max(0.0, min(end, cut_start) - at)
            right_duration = max(0.0, end - max(at, cut_end))

            if left_duration <= eps and right_duration <= eps:
                removed_ids.append(cid)
                continue

            original_duration = end - at
            left_fade = None
            right_fade = None
            if left_duration > eps:
                left_fade, _ = _split_fade(attrs.get("fade"), original_duration, cut_start - at)
            if right_duration > eps:
                _, right_fade = _split_fade(attrs.get("fade"), original_duration, cut_end - at)

            if left_duration > eps:
                left = copy.deepcopy(clip)
                left_source_out = min(source_out, source_in + left_duration * speed)
                if left_source_out <= source_in + eps:
                    raise FilmError("RIPPLE_RANGE_INVALID", f"片段 {cid} 的左侧源区间无效", 422)
                left["attrs"]["src"] = _with_source_range(str(left["attrs"]["src"]), source_in, left_source_out)
                _slice_volume_envelope(left["attrs"], 0.0, left_duration)
                _store_fade(left["attrs"], left_fade)
                rewritten.append(left)

            if right_duration > eps:
                right = copy.deepcopy(clip)
                right_source_in = max(source_in, min(source_out, source_in + (cut_end - at) * speed))
                if right_source_in >= source_out - eps:
                    raise FilmError("RIPPLE_RANGE_INVALID", f"片段 {cid} 的右侧源区间无效", 422)
                right["attrs"]["src"] = _with_source_range(str(right["attrs"]["src"]), right_source_in, source_out)
                right["attrs"]["at"] = _fmt(cut_start)
                _slice_volume_envelope(right["attrs"], cut_end - at, clip_duration(clip))
                _store_fade(right["attrs"], right_fade)
                if left_duration > eps:
                    new_id = requested_split_ids.get(cid, "")
                    if not new_id:
                        token = hashlib.sha1(
                            f"{cid}|{cut_start:.9f}|{cut_end:.9f}|{time.time_ns()}".encode("utf-8")
                        ).hexdigest()[:10]
                        new_id = _safe_id(f"{cid}-ripple-{token}", "clip")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,72}", new_id) or new_id in used_ids:
                        raise FilmError("DUPLICATE_CLIP_ID", f"波纹删除生成了无效或重复 ID：{new_id}", 409)
                    used_ids.add(new_id)
                    split_ids[cid] = new_id
                    right["id"] = new_id
                    right["attrs"]["id"] = new_id
                else:
                    # If only the right part survives, preserve its stable original ID.
                    right["id"] = cid
                    right["attrs"]["id"] = cid
                rewritten.append(right)

        track["clips"] = rewritten

    if set(requested_split_ids) - set(split_ids):
        raise FilmError("INVALID_SPLIT_IDS", "split_ids 包含本次操作没有拆分的片段", 422)
    return {
        "split_ids": split_ids,
        "removed_clip_ids": removed_ids,
        "shifted_clip_ids": shifted_ids,
        "start": _fmt(cut_start),
        "end": _fmt(cut_end),
    }


def _slide_trim(doc: dict, clip_id: str, delta: Any) -> dict:
    """Move one clip within a same-source three-clip run while keeping outer timeline bounds fixed."""
    track, middle = _find_clip(doc, clip_id)
    _ensure_unlocked(track, middle)
    if middle["tag"] not in {"video", "audio"}:
        raise FilmError("SLIDE_MEDIA_UNSUPPORTED", "滑移式修剪只支持视频/音频片段", 422)

    ordered = sorted(track["clips"], key=lambda item: _number(item["attrs"].get("at", 0), "at", 0))
    index = next((i for i, item in enumerate(ordered) if item["id"] == clip_id), -1)
    if index <= 0 or index >= len(ordered) - 1:
        raise FilmError("SLIDE_NEIGHBOURS_REQUIRED", "滑移式修剪要求所选片段左右都有相邻片段", 422)
    left, right = ordered[index - 1], ordered[index + 1]
    _ensure_unlocked(track, left)
    _ensure_unlocked(track, right)
    if left["tag"] != middle["tag"] or right["tag"] != middle["tag"]:
        raise FilmError("SLIDE_MEDIA_MISMATCH", "滑移式修剪要求三段使用相同的视频/音频类型", 422)
    left_duration, middle_duration, right_duration = map(clip_duration, (left, middle, right))
    left_end, middle_end = clip_end(left), clip_end(middle)
    middle_at = _number(middle["attrs"].get("at", 0), "at", 0)
    right_at = _number(right["attrs"].get("at", 0), "at", 0)
    if any(value is None for value in (left_duration, middle_duration, right_duration, left_end, middle_end)):
        raise FilmError("SOURCE_RANGE_REQUIRED", "滑移式修剪要求三段均有明确源时间区间", 422)
    if abs(left_end - middle_at) > 0.001 or abs(middle_end - right_at) > 0.001:
        raise FilmError("SLIDE_NOT_ADJACENT", "滑移式修剪要求三段在时间线上连续相邻", 422)

    attrs = [item["attrs"] for item in (left, middle, right)]
    if any(item.get("fade") for item in attrs):
        raise FilmError("SLIDE_FADE_BOUNDARY", "含淡入淡出属性的片段暂不支持滑移式修剪；请先移除淡化", 422)
    srcs = [str(item.get("src") or "") for item in attrs]
    ranges = [_source_range(src) for src in srcs]
    if any(out is None for _, out in ranges):
        raise FilmError("SOURCE_RANGE_REQUIRED", "滑移式修剪要求三段均有明确源入点和源出点", 422)
    bases = [re.sub(r"#t=[^#]*$", "", src) for src in srcs]
    if len(set(bases)) != 1 or abs(ranges[0][1] - ranges[1][0]) > 0.001 or abs(ranges[1][1] - ranges[2][0]) > 0.001:
        raise FilmError("SLIDE_SOURCE_DISCONTINUITY", "为避免素材越界，滑移式修剪当前只支持同源连续的三段素材", 422)

    speeds = [
        _number(item.get("speed", 1), "speed", 0.25, 4) if clip["tag"] in {"video", "audio"} else 1.0
        for item, clip in zip(attrs, (left, middle, right))
    ]
    if max(speeds) - min(speeds) > 1e-7:
        raise FilmError("SLIDE_SPEED_MISMATCH", "滑移式修剪要求三段播放速度一致", 422)
    speed = speeds[0]
    amount = _number(delta, "slide delta")
    if abs(amount) < 0.001:
        raise FilmError("INVALID_SLIDE_DELTA", "滑移式修剪位移必须至少为 0.001 秒", 422)
    negative_limit = min(left_duration, middle_duration)
    positive_limit = min(middle_duration, right_duration)
    if amount <= -negative_limit + 0.001 or amount >= positive_limit - 0.001:
        raise FilmError("SLIDE_EMPTY_CLIP", "滑移式修剪会令相邻片段剩余时长不足 0.001 秒", 422)

    left_in, left_out = ranges[0]
    middle_in, middle_out = ranges[1]
    right_in, right_out = ranges[2]
    new_left_out = left_out + amount * speed
    new_right_in = right_in + amount * speed
    if new_left_out <= left_in + 0.001 or new_left_out >= middle_out - 0.001:
        raise FilmError("SLIDE_LEFT_SOURCE_RANGE", "滑移后左邻片段源区间无效或超出中间片段可用素材", 422)
    if new_right_in < middle_in or new_right_in >= right_out - 0.001:
        raise FilmError("SLIDE_RIGHT_SOURCE_RANGE", "滑移后右邻片段源区间无效或超出中间片段可用素材", 422)

    attrs[0]["src"] = _with_source_range(srcs[0], left_in, new_left_out)
    attrs[1]["at"] = _fmt(middle_at + amount)
    attrs[2]["src"] = _with_source_range(srcs[2], new_right_in, right_out)
    attrs[2]["at"] = _fmt(right_at + amount)

    if attrs[0].get("volume_envelope"):
        _slice_volume_envelope(attrs[0], 0.0, left_duration + amount)
    if attrs[2].get("volume_envelope"):
        if amount > 0:
            _slice_volume_envelope(attrs[2], amount, right_duration)
        else:
            extension = -amount
            right_copy = {"volume_envelope": attrs[2]["volume_envelope"]}
            _slice_volume_envelope(right_copy, 0.0, right_duration)
            points = _parse_volume_envelope(right_copy["volume_envelope"])
            extended = [{"t": 0.0, "v": points[0]["v"]}]
            extended.extend({"t": point["t"] + extension, "v": point["v"]} for point in points)
            attrs[2]["volume_envelope"] = _normalize_volume_envelope(extended)
    return {"clip": clip_id, "left_clip": left["id"], "right_clip": right["id"], "delta": _fmt(amount)}


def _roll_trim(doc: dict, left_id: str, right_id: str, delta: Any) -> dict:
    """Move an existing split cut without changing the two clips' combined duration."""
    left_track, left = _find_clip(doc, left_id)
    right_track, right = _find_clip(doc, right_id)
    if left_track["id"] != right_track["id"]:
        raise FilmError("ROLL_TRACK_MISMATCH", "滚动修剪要求两个片段在同一轨道", 422)
    _ensure_unlocked(left_track, left)
    _ensure_unlocked(right_track, right)
    if left_id == right_id or left["tag"] != right["tag"]:
        raise FilmError("ROLL_INCOMPATIBLE_CLIPS", "滚动修剪要求两个不同且类型一致的片段", 422)
    left_duration, right_duration = clip_duration(left), clip_duration(right)
    left_end = clip_end(left)
    right_at = _number(right.get("attrs", {}).get("at", 0), "at", 0)
    if left_duration is None or right_duration is None or left_end is None:
        raise FilmError("SOURCE_RANGE_REQUIRED", "滚动修剪要求两个片段都有明确源区间", 422)
    if abs(left_end - right_at) > 0.001:
        raise FilmError("ROLL_NOT_ADJACENT", "滚动修剪要求两个片段在时间线上相邻", 422)

    left_attrs, right_attrs = left.get("attrs") or {}, right.get("attrs") or {}
    if left_attrs.get("fade") or right_attrs.get("fade"):
        raise FilmError("ROLL_FADE_BOUNDARY", "含淡入淡出属性的切口暂不支持滚动修剪；请先移除淡化", 422)
    left_src, right_src = str(left_attrs.get("src") or ""), str(right_attrs.get("src") or "")
    left_in, left_out = _source_range(left_src)
    right_in, right_out = _source_range(right_src)
    if left_out is None or right_out is None:
        raise FilmError("SOURCE_RANGE_REQUIRED", "滚动修剪要求左右片段具有明确源区间", 422)
    left_base = re.sub(r"#t=[^#]*$", "", left_src)
    right_base = re.sub(r"#t=[^#]*$", "", right_src)
    if left_base != right_base or abs(left_out - right_in) > 0.001:
        raise FilmError("ROLL_SOURCE_DISCONTINUITY", "为避免素材出界，滚动修剪当前只支持同源连续切口", 422)

    amount = _number(delta, "roll delta")
    if amount <= -left_duration + 0.001 or amount >= right_duration - 0.001:
        raise FilmError("ROLL_EMPTY_CLIP", "滚动修剪会令左段或右段时长不足 0.001 秒", 422)
    left_speed = _number(left_attrs.get("speed", 1), "left speed", 0.25, 4) if left["tag"] in {"video", "audio"} else 1.0
    right_speed = _number(right_attrs.get("speed", 1), "right speed", 0.25, 4) if right["tag"] in {"video", "audio"} else 1.0
    if abs(left_speed - right_speed) > 1e-7:
        raise FilmError("ROLL_SPEED_MISMATCH", "为保持同源连续，滚动修剪要求左右片段速度一致", 422)
    new_left_out = left_in + (left_duration + amount) * left_speed
    new_right_in = right_in + amount * right_speed
    if new_left_out <= left_in + 0.001 or new_right_in < 0 or new_right_in >= right_out - 0.001:
        raise FilmError("ROLL_SOURCE_RANGE_INVALID", "滚动修剪后的源区间无效", 422)
    left_attrs["src"] = _with_source_range(left_src, left_in, new_left_out)
    right_attrs["src"] = _with_source_range(right_src, new_right_in, right_out)
    right_attrs["at"] = _fmt(right_at + amount)

    if left_attrs.get("volume_envelope"):
        _slice_volume_envelope(left_attrs, 0.0, left_duration + amount)
    if right_attrs.get("volume_envelope"):
        if amount > 0:
            _slice_volume_envelope(right_attrs, amount, right_duration)
        elif amount < 0:
            extension = -amount
            right_copy = {"volume_envelope": right_attrs["volume_envelope"]}
            _slice_volume_envelope(right_copy, 0.0, right_duration)
            points = _parse_volume_envelope(right_copy["volume_envelope"])
            extended = [{"t": 0.0, "v": points[0]["v"]}]
            extended.extend({"t": point["t"] + extension, "v": point["v"]} for point in points)
            right_attrs["volume_envelope"] = _normalize_volume_envelope(extended)
    return {"clip": left_id, "other_clip": right_id, "delta": _fmt(amount)}


def _insert_ripple(doc: dict, track_id: str, raw_clip: Any, at_value: Any) -> dict:
    """Insert a clip and ripple the target track plus explicitly sync-locked tracks."""
    target = _find_track(doc, track_id)
    _ensure_unlocked(target)
    if not isinstance(raw_clip, dict) or raw_clip.get("tag") not in CLIP_TAGS or not isinstance(raw_clip.get("attrs"), dict):
        raise FilmError("INVALID_INSERT", "insert_ripple 需要带 tag、id 和 attrs 的 clip 对象", 422)
    clip_id = str(raw_clip.get("id") or "").strip()
    if not clip_id or any(clip_id == clip["id"] for track in doc["tracks"] for clip in track["clips"]):
        raise FilmError("DUPLICATE_CLIP_ID", "插入素材必须使用影片中未占用的片段 ID", 422)
    attrs = copy.deepcopy(raw_clip["attrs"])
    attrs["id"] = clip_id
    attrs["src"] = _validate_src_syntax(attrs.get("src"))
    at = _number(at_value, "insert at", 0)
    attrs["at"] = _fmt(at)
    if raw_clip["tag"] in {"img", "iframe"}:
        source_in, source_out = _source_range(attrs["src"])
        if source_out is None or abs(source_in) > 0.001:
            raise FilmError("INVALID_SOURCE_RANGE", f"{raw_clip['tag']} 必须使用 #t=0,length 指定插入片段时长", 422)
    if "volume_envelope" in attrs:
        attrs["volume_envelope"] = _normalize_volume_envelope(attrs["volume_envelope"])
    new_clip = {"id": clip_id, "tag": raw_clip["tag"], "attrs": attrs}
    duration = clip_duration(new_clip)
    if duration is None or not math.isfinite(duration) or duration <= 0.001:
        raise FilmError("INSERT_DURATION_UNKNOWN", "插入编辑要求新素材具有明确且大于 0.001 秒的时间线时长", 422)
    if "fade" in attrs:
        attrs["fade"] = _normalize_fade(attrs["fade"], new_clip)

    affected = [track for track in doc["tracks"]
                if track["id"] == target["id"] or "sync-lock" in (track.get("attrs") or {})]
    # Reject the whole transaction before touching any of the candidate tracks.
    for track in affected:
        _ensure_unlocked(track)
        for existing in track.get("clips") or []:
            if "data-clip-locked" not in (existing.get("attrs") or {}):
                continue
            existing_at = _number(existing.get("attrs", {}).get("at", 0), "at", 0)
            existing_end = clip_end(existing)
            will_change = (
                existing_at >= at - eps if existing_end is None
                else existing_end > at + eps
            )
            if will_change:
                raise FilmError(
                    "CLIP_LOCKED",
                    f"插入编辑会移动或切分已锁定片段 {existing['id']}；整笔操作已拒绝",
                    409,
                )

    used_ids = {clip["id"] for track in doc["tracks"] for clip in track["clips"]}
    used_ids.add(clip_id)
    split_ids: dict[str, str] = {}
    shifted_ids: list[str] = []
    eps = 1e-7
    for track in affected:
        rewritten = []
        for clip in list(track.get("clips") or []):
            clip_attrs = clip.get("attrs") or {}
            start = _number(clip_attrs.get("at", 0), "at", 0)
            end = clip_end(clip)
            if end is None:
                if start >= at - eps:
                    clip_attrs["at"] = _fmt(start + duration)
                    shifted_ids.append(clip["id"])
                    rewritten.append(clip)
                    continue
                raise FilmError("INSERT_DURATION_UNKNOWN",
                                f"轨道 {track['id']} 的片段 {clip['id']} 时长未知，不能判断是否穿过插入点", 422)
            if end <= at + eps:
                rewritten.append(clip)
                continue
            if start >= at - eps:
                clip_attrs["at"] = _fmt(start + duration)
                shifted_ids.append(clip["id"])
                rewritten.append(clip)
                continue

            offset, old_duration = at - start, end - start
            if offset <= 0.001 or old_duration - offset <= 0.001:
                raise FilmError("INSERT_SPLIT_TOO_CLOSE", f"插入点过于接近片段 {clip['id']} 的边缘，无法安全切分", 422)
            left_fade, right_fade = _split_fade(clip_attrs.get("fade"), old_duration, offset)
            original_src = str(clip_attrs.get("src") or "")
            source_in, source_out = _source_range(original_src)
            if source_out is None:
                raise FilmError("SOURCE_RANGE_REQUIRED", f"片段 {clip['id']} 缺少明确源区间，不能在插入点切分", 422)
            speed = _number(clip_attrs.get("speed", 1), "speed", 0.25, 4) if clip["tag"] in {"video", "audio"} else 1.0
            mid = source_in + offset * speed
            if mid <= source_in + 0.001 or mid >= source_out - 0.001:
                raise FilmError("INSERT_SOURCE_SPLIT_INVALID", f"片段 {clip['id']} 的源区间无法在插入点安全切分", 422)

            right = copy.deepcopy(clip)
            right_id = _safe_id(f"{clip['id']}-insert-{revision_of(f'{at:.6f}:{clip_id}')[:6]}", "clip")
            base_right_id, suffix = right_id, 2
            while right_id in used_ids:
                right_id = _safe_id(f"{base_right_id}-{suffix}", "clip")
                suffix += 1
            used_ids.add(right_id)
            right["id"] = right_id
            right_attrs = right["attrs"]
            right_attrs["id"] = right_id
            right_attrs["src"] = _with_source_range(original_src, mid, source_out)
            right_attrs["at"] = _fmt(at + duration)
            clip_attrs["src"] = _with_source_range(original_src, source_in, mid)
            if "volume_envelope" in clip_attrs:
                _slice_volume_envelope(clip_attrs, 0.0, offset)
                _slice_volume_envelope(right_attrs, offset, old_duration)
            _store_fade(clip_attrs, left_fade)
            _store_fade(right_attrs, right_fade)
            split_ids[clip["id"]] = right_id
            rewritten.extend((clip, right))
            shifted_ids.append(right_id)
        track["clips"] = rewritten
    target["clips"].append(new_clip)
    return {"track_id": target["id"], "at": _fmt(at), "duration": _fmt(duration), "clip": new_clip,
            "split_ids": split_ids, "shifted_ids": shifted_ids, "affected_tracks": [t["id"] for t in affected]}


def _join_through_edit(doc: dict, left_id: str, right_id: str) -> dict:
    """Heal a split cut only when source, timing, and clip-level semantics are compatible."""
    left_track, left = _find_clip(doc, left_id)
    right_track, right = _find_clip(doc, right_id)
    if left_track["id"] != right_track["id"]:
        raise FilmError("JOIN_TRACK_MISMATCH", "愈合切口要求两个片段位于同一轨道", 422)
    _ensure_unlocked(left_track, left)
    _ensure_unlocked(right_track, right)
    if left_id == right_id or left["tag"] != right["tag"]:
        raise FilmError("JOIN_INCOMPATIBLE_CLIPS", "愈合切口要求两个不同且类型相同的片段", 422)
    left_duration, right_duration = clip_duration(left), clip_duration(right)
    left_end = clip_end(left)
    right_at = _number(right.get("attrs", {}).get("at", 0), "at", 0)
    if left_duration is None or right_duration is None or left_end is None:
        raise FilmError("SOURCE_RANGE_REQUIRED", "愈合切口要求两个片段都有明确源区间", 422)
    if abs(left_end - right_at) > 0.001:
        raise FilmError("JOIN_NOT_ADJACENT", "两个片段在时间线上不相邻，不能愈合切口", 422)
    left_attrs, right_attrs = left.get("attrs") or {}, right.get("attrs") or {}
    left_src, right_src = str(left_attrs.get("src") or ""), str(right_attrs.get("src") or "")
    if re.sub(r"#t=[^#]*$", "", left_src) != re.sub(r"#t=[^#]*$", "", right_src):
        raise FilmError("JOIN_SOURCE_MISMATCH", "两个片段引用不同素材，不能愈合切口", 422)
    left_in, left_out = _source_range(left_src)
    right_in, right_out = _source_range(right_src)
    if left_out is None or right_out is None or abs(left_out - right_in) > 0.001:
        raise FilmError("JOIN_SOURCE_DISCONTINUITY", "两个片段的源时间区间不连续，不能愈合切口", 422)
    if left.get("tag") in {"video", "audio"}:
        left_speed = _number(left_attrs.get("speed", 1), "speed", 0.25, 4)
        right_speed = _number(right_attrs.get("speed", 1), "speed", 0.25, 4)
        if abs(left_speed - right_speed) > 1e-7:
            raise FilmError("JOIN_SPEED_MISMATCH", "两个片段速度不同，不能无损愈合", 422)
    if left_attrs.get("fade") or right_attrs.get("fade"):
        raise FilmError("JOIN_FADE_BOUNDARY", "含淡入淡出边界的片段不能自动愈合；请先移除淡化再操作", 422)

    # Every non-temporal attribute must match, except the clip-relative volume
    # envelope, which is merged explicitly below.
    ignored = {"id", "src", "at", "volume_envelope"}
    for key in (set(left_attrs) | set(right_attrs)) - ignored:
        if left_attrs.get(key) != right_attrs.get(key):
            raise FilmError("JOIN_ATTRIBUTE_MISMATCH", f"片段属性 {key} 不一致，不能无损愈合", 422)

    left_envelope = _parse_volume_envelope(left_attrs["volume_envelope"]) if left_attrs.get("volume_envelope") else []
    right_envelope = _parse_volume_envelope(right_attrs["volume_envelope"]) if right_attrs.get("volume_envelope") else []
    if left_envelope or right_envelope:
        if not left_envelope:
            left_envelope = [{"t": 0.0, "v": 1.0}, {"t": left_duration, "v": 1.0}]
        else:
            left_copy = {"volume_envelope": json.dumps(left_envelope, separators=(",", ":"))}
            _slice_volume_envelope(left_copy, 0.0, left_duration)
            left_envelope = _parse_volume_envelope(left_copy["volume_envelope"])
        if not right_envelope:
            right_envelope = [{"t": 0.0, "v": 1.0}, {"t": right_duration, "v": 1.0}]
        else:
            right_copy = {"volume_envelope": json.dumps(right_envelope, separators=(",", ":"))}
            _slice_volume_envelope(right_copy, 0.0, right_duration)
            right_envelope = _parse_volume_envelope(right_copy["volume_envelope"])
        if abs(left_envelope[-1]["v"] - right_envelope[0]["v"]) > 0.005:
            raise FilmError("JOIN_ENVELOPE_DISCONTINUITY", "两个片段音量包络在切口处不连续，不能无损愈合", 422)
        merged = list(left_envelope)
        merged.extend({"t": point["t"] + left_duration, "v": point["v"]} for point in right_envelope[1:])
        left_attrs["volume_envelope"] = _normalize_volume_envelope(merged)

    left_attrs["src"] = _with_source_range(left_src, left_in, right_out)
    left_track["clips"].remove(right)
    return {"clip": left_id, "joined_clip": right_id, "duration": _fmt(left_duration + right_duration)}


_OVERRIDE_STYLE_MAP = {
    "color": "color", "backgroundColor": "background-color", "fontSize": "font-size",
    "fontWeight": "font-weight", "textAlign": "text-align", "lineHeight": "line-height",
    "letterSpacing": "letter-spacing", "left": "left", "top": "top", "width": "width",
    "height": "height", "position": "position", "opacity": "opacity", "transform": "transform",
    "borderRadius": "border-radius", "rotate": "rotate",
}


def _normalize_element_override(selector: Any, text_value: Any = None,
                                text_supplied: bool = False,
                                style_value: Any = None) -> dict:
    if not isinstance(selector, str) or not selector.strip() or len(selector) > 500:
        raise FilmError("INVALID_ELEMENT_SELECTOR", "画布元素必须提供 1–500 字符的 CSS selector", 422)
    selector = selector.strip()
    if any(ord(char) < 32 for char in selector):
        raise FilmError("INVALID_ELEMENT_SELECTOR", "CSS selector 不能包含控制字符", 422)
    if text_supplied and (not isinstance(text_value, str) or len(text_value) > 4000):
        raise FilmError("INVALID_ELEMENT_TEXT", "画布元素文本必须是字符串且不超过 4000 字符", 422)
    if style_value is None:
        style_value = {}
    if not isinstance(style_value, dict) or len(style_value) > len(_OVERRIDE_STYLE_MAP):
        raise FilmError("INVALID_ELEMENT_STYLE", "画布元素 style 必须是受支持属性组成的对象", 422)
    style = {}
    for key, raw_value in style_value.items():
        if key not in _OVERRIDE_STYLE_MAP:
            raise FilmError("ELEMENT_STYLE_NOT_ALLOWED", f"不允许修改画布 CSS 属性：{key}", 422)
        if not isinstance(raw_value, str) or len(raw_value) > 120:
            raise FilmError("INVALID_ELEMENT_STYLE", f"画布 CSS 属性 {key} 必须是字符串且不超过 120 字符", 422)
        value = raw_value.strip()
        if not value or re.search(r"[{};]", value) or re.search(r"url\s*\(|expression\s*\(|javascript:|@import", value, re.I):
            raise FilmError("UNSAFE_ELEMENT_STYLE", f"画布 CSS 属性 {key} 包含不允许的值", 422)
        if key in {"left", "top", "width", "height", "fontSize", "letterSpacing", "lineHeight", "borderRadius"}:
            length_match = re.fullmatch(
                r"(-?(?:\d+(?:\.\d+)?|\.\d+))(px|%|em|rem|vh|vw)?", value
            )
            if not length_match:
                raise FilmError("INVALID_ELEMENT_STYLE", f"画布 CSS 属性 {key} 需要有效长度值", 422)
            magnitude = float(length_match.group(1))
            if not math.isfinite(magnitude):
                raise FilmError("INVALID_ELEMENT_STYLE", f"画布 CSS 属性 {key} 必须是有限数字", 422)
            if key in {"width", "height"} and not 0 < magnitude <= 10000:
                raise FilmError("INVALID_ELEMENT_STYLE", f"画布 CSS 属性 {key} 必须在 0 到 10000 之间", 422)
            if key == "fontSize" and not 0 < magnitude <= 1000:
                raise FilmError("INVALID_ELEMENT_STYLE", "画布字号必须在 0 到 1000 之间", 422)
            if key in {"left", "top"} and abs(magnitude) > 100000:
                raise FilmError("INVALID_ELEMENT_STYLE", f"画布 CSS 属性 {key} 超出可编辑范围", 422)
            if key == "letterSpacing" and abs(magnitude) > 1000:
                raise FilmError("INVALID_ELEMENT_STYLE", "字间距超出可编辑范围", 422)
            if key == "lineHeight" and magnitude <= 0:
                raise FilmError("INVALID_ELEMENT_STYLE", "行高必须大于 0", 422)
        elif key == "opacity":
            number = _number(value, "opacity", 0, 1)
            value = _fmt(number)
        elif key == "position" and value not in {"static", "relative", "absolute", "fixed", "sticky"}:
            raise FilmError("INVALID_ELEMENT_STYLE", "position 不是受支持的定位值", 422)
        elif key == "fontWeight" and not re.fullmatch(r"(?:normal|bold|bolder|lighter|[1-9]00)", value):
            raise FilmError("INVALID_ELEMENT_STYLE", "fontWeight 不是受支持的字重值", 422)
        elif key == "textAlign" and value not in {"left", "right", "center", "justify", "start", "end"}:
            raise FilmError("INVALID_ELEMENT_STYLE", "textAlign 不是受支持的对齐值", 422)
        elif key == "rotate" and not re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)(?:deg|rad|turn)", value):
            raise FilmError("INVALID_ELEMENT_STYLE", "rotate 必须使用 deg、rad 或 turn 单位", 422)
        elif key == "transform" and not re.fullmatch(
                r"(?:none|translate(?:X|Y)?\([-+\d.%a-zA-Z, ]+\)|scale(?:X|Y)?\([\d., -]+\)|rotate\([-+\d.]+deg\))",
                value):
            raise FilmError("INVALID_ELEMENT_STYLE", "transform 不是受支持的安全变换值", 422)
        elif key in {"color", "backgroundColor"} and not re.fullmatch(
                r"(?:#[0-9a-fA-F]{3,8}|rgba?\([\d.,%\s]+\)|hsla?\([\d.,%\s]+\)|[a-zA-Z]{1,24})",
                value):
            raise FilmError("INVALID_ELEMENT_STYLE", f"{key} 不是受支持的颜色值", 422)
        style[key] = value
    if not text_supplied and not style:
        raise FilmError("EMPTY_ELEMENT_EDIT", "画布元素修改必须包含文本或至少一个样式属性", 422)
    result = {"selector": selector}
    if text_supplied:
        result["text"] = text_value
    if style:
        result["style"] = style
    return result


def _apply_element_set(doc: dict, op: dict) -> dict:
    track, clip = _find_clip(doc, str(op.get("clip") or ""))
    _ensure_unlocked(track, clip)
    if clip.get("tag") != "iframe":
        raise FilmError("ELEMENT_EDIT_REQUIRES_HTML", "画布元素修改只支持 HTML 镜头 iframe 片段", 422)
    text_supplied = "text" in op
    override = _normalize_element_override(
        op.get("selector"), op.get("text"), text_supplied, op.get("style")
    )
    attrs = clip["attrs"]
    raw = attrs.get("overrides")
    try:
        current = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except json.JSONDecodeError as exc:
        raise FilmError("INVALID_OVERRIDES", "当前 overrides 不是有效 JSON；未写入画布修改", 422) from exc
    if current is None or current == "":
        current = []
    if not isinstance(current, list):
        raise FilmError("INVALID_OVERRIDES", "当前 overrides 必须是数组；未写入画布修改", 422)
    entries = [dict(item) if isinstance(item, dict) else item for item in current]
    match = next((item for item in entries
                  if isinstance(item, dict) and item.get("selector") == override["selector"]), None)
    if match is None:
        if len(current) >= 500:
            raise FilmError("OVERRIDE_LIMIT", "单个镜头最多保存 500 项 overrides", 422)
        entries.append(override)
    else:
        if "text" in override:
            match["text"] = override["text"]
        match["style"] = {**(match.get("style") if isinstance(match.get("style"), dict) else {}),
                          **override.get("style", {})}
    # Keep other legacy override records intact. The browser runtime only applies
    # the selector-based subset it understands, so this edit cannot flatten them.
    attrs["overrides"] = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    normalized = {"op": "element_set", "clip": clip["id"], **override}
    return normalized


def apply_ops(doc: dict, ops: list[dict]) -> tuple[dict, list[dict]]:
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPERATIONS:
        raise FilmError("INVALID_OPERATIONS", f"ops 必须是 1–{MAX_OPERATIONS} 项的数组")
    out = copy.deepcopy(doc)
    normalized = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise FilmError("INVALID_OPERATION", f"第 {index + 1} 项操作不是对象")
        kind = op.get("op")
        if kind == "clip_lock":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track)
            locked = op.get("locked")
            if not isinstance(locked, bool):
                raise FilmError("INVALID_CLIP_LOCK", "clip_lock.locked 必须是布尔值", 422)
            if locked:
                clip["attrs"]["data-clip-locked"] = ""
            else:
                clip["attrs"].pop("data-clip-locked", None)
            normalized.append({"op": "clip_lock", "clip": clip["id"], "locked": locked})
        elif kind == "element_set":
            normalized.append(_apply_element_set(out, op))
        elif kind == "set":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track, clip)
            field = str(op.get("field") or "")
            if field not in SETTABLE_FIELDS:
                raise FilmError("FIELD_NOT_ALLOWED", f"Studio 不允许修改字段：{field}")
            value, attrs = op.get("value"), clip["attrs"]
            if field == "at":
                value = _fmt(_number(value, "at", 0))
            elif field == "src":
                value = _validate_src_syntax(value)
                if clip["tag"] in {"img", "iframe"}:
                    src_in, src_out = _source_range(value)
                    if src_out is None or abs(src_in) > 0.001:
                        raise FilmError("INVALID_SOURCE_RANGE", f"{clip['tag']} 必须使用 #t=0,length 指定片段时长")
            elif field == "speed":
                if clip["tag"] not in {"video", "audio"}:
                    raise FilmError("FIELD_NOT_SUPPORTED", f"{clip['tag']} 不支持 speed")
                value = _fmt(_number(value, "speed", 0.25, 4))
            elif field == "volume":
                if clip["tag"] not in {"video", "audio"}:
                    raise FilmError("FIELD_NOT_SUPPORTED", f"{clip['tag']} 不支持 volume")
                value = _fmt(_number(value, "volume", 0, 2))
            elif field == "volume_envelope":
                if clip["tag"] not in {"video", "audio"}:
                    raise FilmError("FIELD_NOT_SUPPORTED", f"{clip['tag']} 不支持音量包络")
                if value is not None:
                    value = _normalize_volume_envelope(value)
            elif field == "fade":
                if clip["tag"] not in {"video", "audio", "img", "iframe"}:
                    raise FilmError("FIELD_NOT_SUPPORTED", f"{clip['tag']} 不支持淡入淡出")
                if value is not None:
                    value = _normalize_fade(value, clip)
            elif field == "muted":
                if clip["tag"] not in {"video", "audio"} or not isinstance(value, bool):
                    raise FilmError("INVALID_BOOLEAN", "muted 只接受视频/音频片段的布尔值")
                value = "" if value else None
            elif field in {"class", "title", "alt", "lang", "dir", "size", "style", "overrides"}:
                if value is not None and not isinstance(value, str):
                    if field == "overrides" and isinstance(value, (list, dict)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    else:
                        raise FilmError("INVALID_FIELD", f"{field} 必须是字符串或 null")
                if isinstance(value, str) and len(value) > 20000:
                    raise FilmError("FIELD_TOO_LARGE", f"{field} 太长")
            if value is None or value is False:
                attrs.pop(field, None)
            elif field == "fade" and value == "":
                attrs.pop(field, None)
            else:
                attrs[field] = value
            normalized.append({"op": "set", "clip": clip["id"], "field": field, "value": value})
        elif kind == "move":
            old_track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(old_track, clip)
            track_id = str(op.get("track_id") or old_track["id"])
            new_track = _find_track(out, track_id)
            _ensure_unlocked(new_track)
            at = _number(op.get("at"), "at", 0)
            old_track["clips"].remove(clip)
            clip["attrs"]["at"] = _fmt(at)
            new_track["clips"].append(clip)
            normalized.append({"op": "move", "clip": clip["id"], "track_id": track_id, "at": _fmt(at)})
        elif kind == "slide":
            result = _slide_trim(out, str(op.get("clip") or ""), op.get("delta"))
            normalized.append({"op": "slide", "clip": result["clip"], "delta": result["delta"]})
        elif kind == "slip":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track, clip)
            if clip["tag"] not in {"video", "audio"}:
                raise FilmError("FIELD_NOT_SUPPORTED", f"{clip['tag']} 不支持源区间滑移", 422)
            source_in, source_out = _source_range(str(clip["attrs"].get("src") or ""))
            if source_out is None:
                raise FilmError("SOURCE_RANGE_REQUIRED", f"片段 {clip['id']} 缺少明确源区间，不能滑移", 422)
            delta_source = _number(op.get("delta_source"), "源区间滑移量")
            next_in, next_out = source_in + delta_source, source_out + delta_source
            if next_in < 0 or next_out <= next_in:
                raise FilmError("INVALID_SLIP_RANGE", "滑移后源入点不能小于零，且源区间必须保持为正", 422)
            clip["attrs"]["src"] = _with_source_range(str(clip["attrs"]["src"]), next_in, next_out)
            normalized.append({"op": "slip", "clip": clip["id"], "delta_source": _fmt(delta_source)})
        elif kind == "join":
            left_id = str(op.get("clip") or "")
            right_id = str(op.get("other_clip") or "")
            result = _join_through_edit(out, left_id, right_id)
            normalized.append({"op": "join", "clip": result["clip"], "other_clip": result["joined_clip"]})
        elif kind == "roll":
            result = _roll_trim(out, str(op.get("clip") or ""), str(op.get("other_clip") or ""), op.get("delta"))
            normalized.append({"op": "roll", "clip": result["clip"], "other_clip": result["other_clip"], "delta": result["delta"]})
        elif kind == "ripple_delete":
            if op.get("scope", "all") != "all":
                raise FilmError("INVALID_RIPPLE_SCOPE", "当前只支持明确的全轨道波纹删除 scope='all'", 422)
            clip_id = str(op.get("clip") or "")
            result = _apply_ripple_delete(out, clip_id, op.get("split_ids"))
            normalized.append({
                "op": "ripple_delete", "clip": clip_id, "scope": "all",
                "split_ids": result["split_ids"],
            })
        elif kind == "remove":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track, clip)
            track["clips"].remove(clip)
            normalized.append({"op": "remove", "clip": clip["id"]})
        elif kind == "insert_ripple":
            result = _insert_ripple(out, str(op.get("track_id") or ""), op.get("clip"), op.get("at"))
            normalized.append({"op": "insert_ripple", "track_id": result["track_id"], "at": result["at"],
                               "clip": result["clip"], "split_ids": result["split_ids"],
                               "shifted_ids": result["shifted_ids"], "affected_tracks": result["affected_tracks"]})
        elif kind == "insert":
            track = _find_track(out, str(op.get("track_id") or ""))
            _ensure_unlocked(track)
            src = op.get("clip")
            if not isinstance(src, dict) or src.get("tag") not in CLIP_TAGS or not isinstance(src.get("attrs"), dict):
                raise FilmError("INVALID_INSERT", "insert 需要带 tag、id 和 attrs 的 clip 对象")
            clip = {"id": str(src.get("id") or ""), "tag": src["tag"], "attrs": copy.deepcopy(src["attrs"])}
            if not clip["id"] or any(clip["id"] == c["id"] for t in out["tracks"] for c in t["clips"]):
                raise FilmError("DUPLICATE_CLIP_ID", "新片段必须有未使用的稳定 ID")
            clip["attrs"]["id"] = clip["id"]
            clip["attrs"]["src"] = _validate_src_syntax(clip["attrs"].get("src"))
            if clip["tag"] in {"img", "iframe"}:
                src_in, src_out = _source_range(clip["attrs"]["src"])
                if src_out is None or abs(src_in) > 0.001:
                    raise FilmError("INVALID_SOURCE_RANGE", f"{clip['tag']} 必须使用 #t=0,length 指定片段时长")
            _number(clip["attrs"].get("at", 0), "at", 0)
            track["clips"].append(clip)
            normalized.append({"op": "insert", "track_id": track["id"], "clip": clip})
        elif kind == "split":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track, clip)
            attrs = clip["attrs"]
            at = _number(op.get("at"), "split at", 0)
            start, duration = _number(attrs.get("at", 0), "at", 0), clip_duration(clip)
            if duration is None:
                raise FilmError("SOURCE_RANGE_REQUIRED", f"片段 {clip['id']} 必须有 #t=in,out 源区间才能切分")
            if at <= start + 0.001 or at >= start + duration - 0.001:
                raise FilmError("SPLIT_OUTSIDE_CLIP", f"切点 {at:.3f}s 不在片段内部")
            src_in, src_out = _source_range(attrs["src"])
            speed = _number(attrs.get("speed", 1), "speed", 0.25, 4) if clip["tag"] in {"video", "audio"} else 1.0
            mid = src_in + (at - start) * speed
            right_id = _safe_id(str(op.get("new_id") or f"{clip['id']}-split-{revision_of(str(at))[:6]}"), "clip-right")
            if any(right_id == c["id"] for t in out["tracks"] for c in t["clips"]):
                raise FilmError("DUPLICATE_CLIP_ID", f"切分后 ID 已存在：{right_id}")
            original_id = clip["id"]
            original_envelope = attrs.get("volume_envelope")
            original_fade = attrs.get("fade")
            split_offset = at - start
            left_fade, right_fade = _split_fade(original_fade, duration, split_offset)
            clip["attrs"]["src"] = _with_source_range(attrs["src"], src_in, mid)
            if original_envelope is not None:
                _slice_volume_envelope(clip["attrs"], 0.0, split_offset)
            _store_fade(clip["attrs"], left_fade)
            right = copy.deepcopy(clip)
            right["id"], right["attrs"]["id"] = right_id, right_id
            right["attrs"]["src"] = _with_source_range(attrs["src"], mid, src_out)
            right["attrs"]["at"] = _fmt(at)
            if original_envelope is not None:
                right["attrs"]["volume_envelope"] = original_envelope
                _slice_volume_envelope(right["attrs"], split_offset, duration)
            _store_fade(right["attrs"], right_fade)
            track["clips"].insert(track["clips"].index(clip) + 1, right)
            normalized.append({"op": "split", "clip": original_id, "at": _fmt(at), "new_id": right_id})
        elif kind == "trim":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track, clip)
            attrs = clip["attrs"]
            edge = str(op.get("edge") or "")
            start = _number(attrs.get("at", 0), "at", 0)
            duration = clip_duration(clip)
            if duration is None:
                raise FilmError("SOURCE_RANGE_REQUIRED", f"片段 {clip['id']} 必须有 #t=in,out 源区间才能修剪")
            old_end = start + duration
            src_in, src_out = _source_range(attrs["src"])
            speed = _number(attrs.get("speed", 1), "speed", 0.25, 4) if clip["tag"] in {"video", "audio"} else 1.0
            if edge == "start":
                new_start = _number(op.get("at"), "trim start", start + 0.001, old_end - 0.001)
                delta = new_start - start
                trimmed_fade = _trim_fade(attrs.get("fade"), duration, delta, "start")
                if clip["tag"] in {"img", "iframe"}:
                    new_src_in, new_src_out = src_in, src_out - delta
                else:
                    new_src_in, new_src_out = src_in + delta * speed, src_out
                if new_src_out is None or new_src_in >= new_src_out - 0.001:
                    raise FilmError("TRIM_EMPTY_CLIP", "修剪后片段时长不能为零")
                attrs["src"] = _with_source_range(attrs["src"], new_src_in, new_src_out)
                attrs["at"] = _fmt(new_start)
                _slice_volume_envelope(attrs, delta, duration)
                _store_fade(attrs, trimmed_fade)
                normalized.append({"op": "trim", "clip": clip["id"], "edge": "start", "at": _fmt(new_start)})
            elif edge == "end":
                new_end = _number(op.get("end"), "trim end", start + 0.001, old_end - 0.001)
                trimmed_fade = _trim_fade(attrs.get("fade"), duration, new_end - start, "end")
                new_src_out = src_in + (new_end - start) * speed
                if new_src_out <= src_in + 0.001:
                    raise FilmError("TRIM_EMPTY_CLIP", "修剪后片段时长不能为零")
                attrs["src"] = _with_source_range(attrs["src"], src_in, min(src_out, new_src_out))
                _slice_volume_envelope(attrs, 0.0, new_end - start)
                _store_fade(attrs, trimmed_fade)
                normalized.append({"op": "trim", "clip": clip["id"], "edge": "end", "end": _fmt(new_end)})
            else:
                raise FilmError("INVALID_TRIM_EDGE", "trim edge 只能是 start 或 end")
        elif kind == "track_remove":
            track = _find_track(out, str(op.get("track_id") or ""))
            _ensure_unlocked(track)
            if track["clips"]:
                raise FilmError("TRACK_NOT_EMPTY", "只能删除空轨道；请先移动或删除其中的片段")
            if len(out["tracks"]) <= 1:
                raise FilmError("LAST_TRACK", "影片必须保留至少一条轨道")
            out["tracks"].remove(track)
            normalized.append({"op": "track_remove", "track_id": track["id"]})
        elif kind == "track_add":
            track_id = str(op.get("track_id") or "").strip()
            label = str(op.get("label") or "新轨道").strip()[:120]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", track_id):
                raise FilmError("INVALID_TRACK_ID", "新轨道 ID 只允许 ASCII 字母、数字、下划线和连字符")
            if any(track_id == t["id"] for t in out["tracks"]):
                raise FilmError("DUPLICATE_TRACK_ID", "新轨道必须有未使用的 ID")
            raw_index = op.get("index", 0)
            index = int(_number(raw_index, "track index", 0, len(out["tracks"])))
            if index != raw_index:
                raise FilmError("INVALID_TRACK_INDEX", "轨道位置必须是整数")
            attrs = {"id": track_id, "data-label": label or "新轨道"}
            if str(op.get("kind") or ""):
                attrs["data-kind"] = str(op.get("kind"))[:24]
            out["tracks"].insert(index, {"id": track_id, "attrs": attrs, "clips": []})
            normalized.append({"op": "track_add", "track_id": track_id, "label": attrs["data-label"], "index": index})
        elif kind == "track":
            track = _find_track(out, str(op.get("track_id") or ""))
            field, value = str(op.get("field") or ""), op.get("value")
            if field not in TRACK_FLAGS or not isinstance(value, bool):
                raise FilmError("INVALID_TRACK_FLAG", "轨道属性只允许 locked/hidden/muted/sync-lock 布尔值")
            if value:
                track["attrs"][field] = ""
            else:
                track["attrs"].pop(field, None)
            normalized.append({"op": "track", "track_id": track["id"], "field": field, "value": value})
        else:
            raise FilmError("UNKNOWN_OPERATION", f"不支持的操作：{kind}")
    _validate_doc(out)
    return out, normalized


def _project_root(project_id: str) -> Path:
    pid = validate_project_id(project_id)
    composer_root = config.composer_storage_root().resolve()
    root = (composer_root / pid).resolve()
    try:
        root.relative_to(composer_root)
    except ValueError:
        raise FilmError("PROJECT_PATH_OUTSIDE_STORAGE", "项目目录解析后超出 composer 存储根目录", 403) from None
    return root


def _allowed_media_roots(project_id: str) -> list[Path]:
    """Read access is restricted to this project and the two shared media libraries."""
    composer_root = config.composer_storage_root().resolve()
    project_root = _project_root(project_id)
    return [
        project_root,
        (composer_root / "assets").resolve(),
        (composer_root / "audio_library").resolve(),
    ]


def _assert_media_path_allowed(project_id: str, media_path: Path) -> Path:
    resolved = media_path.resolve()
    for root in _allowed_media_roots(project_id):
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise FilmError(
        "MEDIA_PATH_OUTSIDE_ALLOWED_ROOTS",
        "素材路径必须位于当前项目、共享素材库或音频库中，不能跨项目读取",
        403,
    )


def _film_path(project_id: str) -> Path:
    return _project_root(project_id) / FILM_NAME


def assert_legacy_composition_access(project_id: str, operation: str = "编辑或渲染") -> None:
    """Fail closed when film.html owns the project timeline.

    Existence is the switch, not parse success: even a damaged film.html must
    never cause old CompositionIR writes or exports to resume silently.
    """
    pid = validate_project_id(project_id)
    if _film_path(pid).exists():
        verb = str(operation or "编辑或渲染").strip() or "编辑或渲染"
        raise FilmError(
            "FILM_DOCUMENT_AUTHORITATIVE",
            f"项目 {pid} 已以 film.html 作为影片事实源；旧 CompositionIR 的{verb}已拒绝，"
            "请从 OpenFilm 影片工作台操作。旧编辑链尚未实现与 film.html 的双向同步。",
            409,
        )


def _history_path(project_id: str) -> Path:
    return _project_root(project_id) / HISTORY_NAME


def _write_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        last_error = None
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                if os.name != "nt" or attempt == 7:
                    raise
                time.sleep(0.02 * (2 ** attempt))
        if last_error is not None:
            raise last_error
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_project_film(project_id: str) -> dict:
    path = _film_path(project_id)
    if not path.is_file():
        return {"exists": False, "project_id": project_id, "text": "", "rev": revision_of(""), "doc": None}
    text = path.read_text(encoding="utf-8")
    return {"exists": True, "project_id": project_id, "text": text, "rev": revision_of(text), "doc": parse_film(text)}


def _history_read(project_id: str) -> dict:
    path = _history_path(project_id)
    if not path.is_file():
        return {"past": [], "future": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("past"), list) and isinstance(data.get("future"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    raise FilmError("HISTORY_CORRUPT", "影片编辑历史文件损坏；为防止覆盖，不继续写入", 500)


def _history_write(project_id: str, state: dict):
    _write_atomic(_history_path(project_id), json.dumps(state, ensure_ascii=False, separators=(",", ":")))




class _FilmSourceLocator(HTMLParser):
    """Record exact source spans for tracks and empty media elements."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_offsets = [0]
        for match in re.finditer("\n", source):
            self.line_offsets.append(match.end())
        self.in_body = False
        self.current_track = -1
        self.tracks: list[dict] = []
        self.clips_by_track: list[list[dict]] = []
        self.open_clips: list[dict] = []
        self.body_close_start: int | None = None
        self.body_close_end: int | None = None
        self.feed(source)
        self.close()

    def _position(self) -> int:
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def _tag_span(self) -> tuple[int, int, str]:
        start = self._position()
        token = self.get_starttag_text() or ""
        return start, start + len(token), token

    def _closing_span(self) -> tuple[int, int]:
        start = self._position()
        end = self.source.find(">", start)
        if end < 0:
            raise FilmError("SOURCE_SPAN_INVALID", "无法定位 HTML 结束标签", 422)
        return start, end + 1

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_map = {key.lower(): ("" if value is None else value) for key, value in attrs}
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        start, open_end, token = self._tag_span()
        item = {"tag": tag, "start": start, "open_end": open_end, "end": open_end,
                "open": token, "attrs": attr_map}
        if tag == "section":
            self.current_track = len(self.tracks)
            item["close_start"] = None
            item["close_end"] = None
            self.tracks.append(item)
            self.clips_by_track.append([])
        elif tag in CLIP_TAGS and self.current_track >= 0:
            item["track_index"] = self.current_track
            self.clips_by_track[self.current_track].append(item)
            if tag not in VOID_TAGS and not token.rstrip().endswith("/>"):
                self.open_clips.append(item)

    def handle_startendtag(self, tag, attrs):
        # HTMLParser calls handle_startendtag as one event rather than invoking
        # handle_starttag + handle_endtag by default.
        tag = tag.lower()
        attr_map = {key.lower(): ("" if value is None else value) for key, value in attrs}
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        start, open_end, token = self._tag_span()
        item = {"tag": tag, "start": start, "open_end": open_end, "end": open_end,
                "open": token, "attrs": attr_map}
        if tag == "section":
            self.current_track = len(self.tracks)
            item["close_start"] = None
            item["close_end"] = None
            self.tracks.append(item)
            self.clips_by_track.append([])
        elif tag in CLIP_TAGS and self.current_track >= 0:
            item["track_index"] = self.current_track
            self.clips_by_track[self.current_track].append(item)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "body":
            self.body_close_start, self.body_close_end = self._closing_span()
            self.in_body = False
            return
        if not self.in_body:
            return
        if tag in CLIP_TAGS:
            for index in range(len(self.open_clips) - 1, -1, -1):
                item = self.open_clips[index]
                if item["tag"] == tag:
                    _, close_end = self._closing_span()
                    item["end"] = close_end
                    self.open_clips.pop(index)
                    break
        elif tag == "section" and self.current_track >= 0:
            close_start, close_end = self._closing_span()
            item = self.tracks[self.current_track]
            item["close_start"], item["close_end"], item["end"] = close_start, close_end, close_end
            self.current_track = -1


def _attribute_token_span(open_tag: str, name: str) -> tuple[int, int] | None:
    """Lex attributes without matching text inside quoted attribute values."""
    if not open_tag.startswith("<"):
        return None
    i = 1
    length = len(open_tag)
    while i < length and not open_tag[i].isspace() and open_tag[i] not in "/>":
        i += 1
    while i < length:
        while i < length and (open_tag[i].isspace() or open_tag[i] == "/"):
            i += 1
        if i >= length or open_tag[i] == ">":
            return None
        token_start = i
        while i < length and not open_tag[i].isspace() and open_tag[i] not in "=/>":
            i += 1
        if i == token_start:
            i += 1
            continue
        attr_name = open_tag[token_start:i]
        while i < length and open_tag[i].isspace():
            i += 1
        if i < length and open_tag[i] == "=":
            i += 1
            while i < length and open_tag[i].isspace():
                i += 1
            if i < length and open_tag[i] in {"'", '"'}:
                quote_char = open_tag[i]
                i += 1
                while i < length and open_tag[i] != quote_char:
                    i += 1
                if i < length:
                    i += 1
            else:
                while i < length and not open_tag[i].isspace() and open_tag[i] not in ">":
                    i += 1
        if attr_name.lower() == name.lower():
            return token_start, i
    return None


def _replace_html_attribute(open_tag: str, name: str, value: Any) -> str:
    """Update one HTML attribute while preserving all other raw attribute bytes."""
    if not re.fullmatch(r"[A-Za-z_:][A-Za-z0-9_.:-]*", name):
        raise FilmError("INVALID_ATTRIBUTE_NAME", f"非法 HTML 属性名：{name}", 422)
    span = _attribute_token_span(open_tag, name)
    if value is None or value is False:
        if span:
            return open_tag[:span[0]] + open_tag[span[1]:]
        return open_tag
    serialized = f'{name}="{html.escape(str(value), quote=True)}"'
    if span:
        return open_tag[:span[0]] + serialized + open_tag[span[1]:]
    close_at = open_tag.rfind(">")
    if close_at < 0:
        raise FilmError("SOURCE_SPAN_INVALID", "无法定位 HTML 开始标签结尾", 422)
    insert_at = close_at - 1 if close_at > 0 and open_tag[close_at - 1] == "/" else close_at
    return open_tag[:insert_at] + " " + serialized + open_tag[insert_at:]


def _expanded_line_range(source: str, start: int, end: int) -> tuple[int, int]:
    """Include a node's surrounding line only when it contains no other content."""
    line_start = source.rfind("\n", 0, start) + 1
    prefix = source[line_start:start]
    next_newline = source.find("\n", end)
    line_end = len(source) if next_newline < 0 else next_newline
    suffix = source[end:line_end]
    if not prefix.strip() and not suffix.strip():
        return line_start, len(source) if next_newline < 0 else next_newline + 1
    return start, end


def _serialize_clip_node(clip: dict) -> str:
    attrs = dict(clip.get("attrs") or {})
    attrs["id"] = clip["id"]
    tag = clip["tag"]
    opening = f"<{tag}{_serialize_attrs(attrs)}>"
    return opening if tag == "img" else opening + f"</{tag}>"


def _serialize_track_node(track: dict, child_indent: str) -> str:
    attrs = dict(track.get("attrs") or {})
    attrs["id"] = track["id"]
    pieces = [f"<section{_serialize_attrs(attrs)}>"]
    for clip in sorted(track.get("clips") or [], key=lambda c: _number(c["attrs"].get("at", 0), "at", 0)):
        pieces.append(child_indent + _serialize_clip_node(clip))
    pieces.append("</section>")
    return "\n".join(pieces)


def _line_insertion_point(source: str, tag_start: int) -> tuple[int, str]:
    line_start = source.rfind("\n", 0, tag_start) + 1
    prefix = source[line_start:tag_start]
    if not prefix.strip():
        return line_start, prefix
    return tag_start, ""


def _indent_at(source: str, tag_start: int) -> str:
    line_start = source.rfind("\n", 0, tag_start) + 1
    prefix = source[line_start:tag_start]
    return prefix if not prefix.strip() else ""


def _preserve_film_text(original_text: str, old_doc: dict, new_doc: dict) -> str | None:
    """Patch source spans for both attribute and structural edits.

    Existing untouched track/clip nodes are retained byte-for-byte. Deleted nodes
    lose only their own source range; added/moved clips and tracks are inserted as
    new nodes. Any ambiguous/unlocatable span returns None so the caller can fail
    closed or use the serializer fallback.
    """
    old_tracks = old_doc.get("tracks") or []
    new_tracks = new_doc.get("tracks") or []
    locator = _FilmSourceLocator(original_text)
    if (len(locator.tracks) != len(old_tracks)
            or any(len(locator.clips_by_track[i]) != len(track.get("clips") or [])
                   for i, track in enumerate(old_tracks))
            or locator.body_close_start is None):
        return None
    if any(track.get("close_start") is None or track.get("end") is None for track in locator.tracks):
        return None
    if any(item.get("end") is None for lane in locator.clips_by_track for item in lane):
        return None

    old_track_by_id = {track["id"]: track for track in old_tracks}
    new_track_by_id = {track["id"]: track for track in new_tracks}
    old_clip_by_id, old_owner = {}, {}
    new_clip_by_id, new_owner = {}, {}
    source_track_item = {}
    source_clip_item = {}
    for index, track in enumerate(old_tracks):
        source_track_item[track["id"]] = locator.tracks[index]
        for clip, item in zip(track.get("clips") or [], locator.clips_by_track[index]):
            old_clip_by_id[clip["id"]] = clip
            old_owner[clip["id"]] = track["id"]
            source_clip_item[clip["id"]] = item
    for track in new_tracks:
        for clip in track.get("clips") or []:
            new_clip_by_id[clip["id"]] = clip
            new_owner[clip["id"]] = track["id"]

    patches: dict[tuple[int, int], str] = {}
    insertions: dict[int, list[tuple[int, int, str]]] = {}
    removed_track_ids = set(old_track_by_id) - set(new_track_by_id)
    added_track_ids = set(new_track_by_id) - set(old_track_by_id)

    def add_patch(start: int, end: int, replacement_text: str) -> None:
        key = (start, end)
        if key in patches and patches[key] != replacement_text:
            raise FilmError("SOURCE_PATCH_CONFLICT", "多个编辑操作覆盖了同一处 HTML 文本", 422)
        patches[key] = replacement_text

    # Remove complete tracks first. Children inside a deleted parent must not emit
    # additional overlapping patches.
    for track_id in removed_track_ids:
        item = source_track_item[track_id]
        start, end = _expanded_line_range(original_text, item["start"], item["end"])
        add_patch(start, end, "")

    # Existing track headers can be updated without reserializing their contents.
    for track_id in set(old_track_by_id) & set(new_track_by_id):
        if track_id in removed_track_ids:
            continue
        old_track, new_track = old_track_by_id[track_id], new_track_by_id[track_id]
        item = source_track_item[track_id]
        old_attrs, new_attrs = old_track.get("attrs") or {}, new_track.get("attrs") or {}
        changed = [key for key in (set(old_attrs) | set(new_attrs)) if old_attrs.get(key) != new_attrs.get(key)]
        needs_id = bool(changed) and "id" not in item["attrs"]
        if changed or needs_id:
            token = item["open"]
            for key in sorted(changed):
                token = _replace_html_attribute(token, key, new_attrs.get(key))
            if needs_id:
                token = _replace_html_attribute(token, "id", track_id)
            add_patch(item["start"], item["open_end"], token)

    # Existing clip nodes: patch their attributes in-place, or remove nodes that
    # were deleted/moved. If their parent track is deleted, the parent removal wins.
    for clip_id, old_clip in old_clip_by_id.items():
        owner = old_owner[clip_id]
        if owner in removed_track_ids:
            continue
        item = source_clip_item[clip_id]
        if clip_id not in new_clip_by_id or new_owner[clip_id] != owner:
            start, end = _expanded_line_range(original_text, item["start"], item["end"])
            add_patch(start, end, "")
            continue
        new_clip = new_clip_by_id[clip_id]
        if old_clip["tag"] != new_clip["tag"]:
            return None
        old_attrs, new_attrs = old_clip.get("attrs") or {}, new_clip.get("attrs") or {}
        changed = [key for key in (set(old_attrs) | set(new_attrs)) if old_attrs.get(key) != new_attrs.get(key)]
        needs_id = bool(changed) and "id" not in item["attrs"]
        if changed or needs_id:
            token = item["open"]
            for key in sorted(changed):
                token = _replace_html_attribute(token, key, new_attrs.get(key))
            if needs_id:
                token = _replace_html_attribute(token, "id", clip_id)
            add_patch(item["start"], item["open_end"], token)

    def add_insertion(point: int, at: float, html_node: str, indent: str) -> None:
        insertions.setdefault(point, []).append((at, len(insertions.get(point, [])), indent + html_node))

    # Added tracks are emitted as a self-contained section, at their requested
    # document order, without touching the bytes of existing sections.
    new_track_positions = {track["id"]: i for i, track in enumerate(new_tracks)}
    for track_id in added_track_ids:
        track = new_track_by_id[track_id]
        index = new_track_positions[track_id]
        following = next((new_tracks[j]["id"] for j in range(index + 1, len(new_tracks))
                          if new_tracks[j]["id"] in source_track_item
                          and new_tracks[j]["id"] not in removed_track_ids), None)
        if following:
            anchor = source_track_item[following]["start"]
        else:
            anchor = locator.body_close_start
        point, indent = _line_insertion_point(original_text, anchor)
        existing_clip_indent = next(
            (_indent_at(original_text, source_clip_item[c["id"]]["start"])
             for c in track.get("clips") or []
             if c["id"] in source_clip_item and old_owner.get(c["id"]) == track_id),
            indent,
        )
        block = _serialize_track_node(track, existing_clip_indent or indent)
        add_insertion(point, float(index), block, indent)

    # Insert new or cross-track moved clips into an existing destination track.
    for clip_id, clip in new_clip_by_id.items():
        owner = new_owner[clip_id]
        if owner in added_track_ids:
            continue
        if clip_id in old_clip_by_id and old_owner[clip_id] == owner:
            continue
        if owner not in source_track_item or owner in removed_track_ids:
            return None
        new_track = new_track_by_id[owner]
        existing_candidates = []
        for candidate in new_track.get("clips") or []:
            candidate_id = candidate["id"]
            if candidate_id == clip_id or candidate_id not in source_clip_item:
                continue
            if old_owner.get(candidate_id) != owner or candidate_id not in new_clip_by_id:
                continue
            item = source_clip_item[candidate_id]
            if candidate.get("tag") != old_clip_by_id[candidate_id].get("tag"):
                return None
            candidate_at = _number(candidate.get("attrs", {}).get("at", 0), "at", 0)
            if candidate_at >= _number(clip.get("attrs", {}).get("at", 0), "at", 0):
                existing_candidates.append((candidate_at, item["start"], item))
        if existing_candidates:
            _, anchor, anchor_item = min(existing_candidates, key=lambda x: (x[0], x[1]))
            point, indent = _line_insertion_point(original_text, anchor)
        else:
            anchor_item = source_track_item[owner]
            point, indent = _line_insertion_point(original_text, anchor_item["close_start"])
        child_indent = next(
            (_indent_at(original_text, source_clip_item[c["id"]]["start"])
             for c in new_track.get("clips") or []
             if c["id"] in source_clip_item and old_owner.get(c["id"]) == owner),
            indent,
        )
        add_insertion(point, _number(clip.get("attrs", {}).get("at", 0), "at", 0),
                      _serialize_clip_node(clip), child_indent)

    # If a track is entirely new, all of its clips are already part of its block.
    # Group inserts at identical offsets, preserve temporal order, then splice the
    # original file right-to-left so earlier spans never shift.
    for point, entries in insertions.items():
        ordered = sorted(entries, key=lambda entry: (entry[0], entry[1]))
        block = "\n".join(entry[2] for entry in ordered) + "\n"
        if point > 0 and original_text[point - 1] != "\n":
            block = "\n" + block
        add_patch(point, point, block)

    result = original_text
    for (start, end), replacement_text in sorted(patches.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True):
        result = result[:start] + replacement_text + result[end:]
    return result




def _validate_slip_source_bounds(project_id: str, doc: dict, normalized_ops: list[dict]) -> None:
    """Reject slip edits that move a source range beyond the actual media duration."""
    slip_ids = {
        str(op.get("clip") or "")
        for op in normalized_ops
        if isinstance(op, dict) and op.get("op") == "slip"
    }
    if not slip_ids:
        return

    from video_director import prober

    clips = {
        clip["id"]: clip
        for track in doc.get("tracks", [])
        for clip in track.get("clips", [])
    }
    for clip_id in sorted(slip_ids):
        clip = clips.get(clip_id)
        if clip is None:
            # A compound edit may subsequently remove the slipped clip; no invalid
            # source range remains in the committed document in that case.
            continue
        if clip["tag"] not in {"video", "audio"}:
            raise FilmError("SLIP_MEDIA_UNSUPPORTED", f"片段 {clip_id} 不支持源区间滑移", 422)
        src = str((clip.get("attrs") or {}).get("src") or "")
        source_in, source_out = _source_range(src)
        if source_out is None or source_in < 0:
            raise FilmError("SOURCE_RANGE_REQUIRED", f"滑移后的片段 {clip_id} 缺少合法源时间区间", 422)
        path = media_path_for_src(project_id, src)
        try:
            probe = prober.probe_media(str(path))
        except prober.ProbeError as exc:
            raise FilmError("SOURCE_PROBE_FAILED", f"无法验证片段 {clip_id} 的素材时长：{exc}", 422) from None
        if clip["tag"] == "video" and not probe.has_video_stream:
            raise FilmError("VIDEO_STREAM_MISSING", f"视频片段 {clip_id} 指向的素材没有视频流", 422)
        if clip["tag"] == "audio" and not probe.has_audio_stream:
            raise FilmError("AUDIO_STREAM_MISSING", f"音频片段 {clip_id} 指向的素材没有音频流", 422)
        if probe.duration is None or not math.isfinite(float(probe.duration)) or float(probe.duration) <= 0:
            raise FilmError("SOURCE_DURATION_UNKNOWN", f"无法确认片段 {clip_id} 的真实素材时长，已拒绝滑移", 422)
        if source_out > float(probe.duration) + 0.05:
            raise FilmError(
                "SOURCE_RANGE_OUT_OF_BOUNDS",
                f"滑移后片段 {clip_id} 的源出点 {source_out:.3f}s 超出素材时长 {float(probe.duration):.3f}s",
                422,
            )


def edit_project_film(project_id: str, base: str | None, ops: list[dict], *, require_base: bool = False) -> dict:
    pid = validate_project_id(project_id)
    with project_lock(pid):
        current = read_project_film(pid)
        if not current["exists"]:
            raise FilmError("FILM_NOT_INITIALIZED", "当前项目还没有 film.html，请先初始化影片文档", 409)
        if require_base and base != current["rev"]:
            raise FilmError("REVISION_CONFLICT", "影片文档已变化；该 AI 提案已过期，请重新生成提案。", 409)
        original_doc = current["doc"]
        doc, normalized = apply_ops(original_doc, ops)
        _validate_slip_source_bounds(pid, doc, normalized)
        updated = _preserve_film_text(current["text"], original_doc, doc)
        if updated is None:
            updated = serialize_film(doc)
        # Fail closed: every write must still satisfy the full document contract.
        parse_film(updated)
        history = _history_read(pid)
        history["past"].append({"before": current["text"], "after": updated,
                                "before_rev": current["rev"], "after_rev": revision_of(updated),
                                "ops": normalized})
        history["past"] = history["past"][-200:]
        history["future"] = []
        _write_atomic(_film_path(pid), updated)
        _history_write(pid, history)
        return {"ok": True, "project_id": pid, "rev": revision_of(updated), "base": base,
                "rebased": bool(base and base != current["rev"]), "document": doc, "ops": normalized}


def undo_redo_project_film(project_id: str, base: str | None, action: str) -> dict:
    pid = validate_project_id(project_id)
    if action not in {"undo", "redo"}:
        raise FilmError("INVALID_HISTORY_ACTION", "action 必须是 undo 或 redo")
    with project_lock(pid):
        current = read_project_film(pid)
        if not current["exists"]:
            raise FilmError("FILM_NOT_INITIALIZED", "当前项目还没有 film.html", 409)
        history = _history_read(pid)
        source, destination = (history["past"], history["future"]) if action == "undo" else (history["future"], history["past"])
        if not source:
            raise FilmError("NOTHING_TO_UNDO" if action == "undo" else "NOTHING_TO_REDO",
                            "没有可撤销的编辑" if action == "undo" else "没有可重做的编辑", 409)
        entry = source[-1]
        expected = entry["after_rev"] if action == "undo" else entry["before_rev"]
        if current["rev"] != expected:
            raise FilmError("HISTORY_CONFLICT", "影片已被外部修改；请重新载入以避免覆盖外部编辑", 409)
        text = entry["before"] if action == "undo" else entry["after"]
        parse_film(text)
        source.pop()
        destination.append(entry)
        _write_atomic(_film_path(pid), text)
        _history_write(pid, history)
        return {"ok": True, "project_id": pid, "rev": revision_of(text), "document": parse_film(text), "action": action}


def media_path_for_src(project_id: str, src: str) -> Path:
    pid = validate_project_id(project_id)
    raw = unquote(str(src or "")).strip().split("#", 1)[0]
    if (not raw or raw.startswith(("/", "\\")) or "\\" in raw
            or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) or raw.startswith("//")):
        raise FilmError("INVALID_MEDIA_PATH", "影片素材必须是存储范围内的相对文件路径", 403)
    candidate = _assert_media_path_allowed(pid, _project_root(pid) / raw)
    if not candidate.is_file():
        raise FilmError("MEDIA_NOT_FOUND", f"影片素材文件不存在：{raw}", 404)
    return candidate


def _safe_storage_uri(project_id: str, source_file: str | Path) -> str:
    root = _project_root(project_id)
    composer_root = config.composer_storage_root().resolve()
    p = Path(source_file)
    if not p.is_absolute():
        candidates = [root / p, composer_root / p, composer_root.parent / p]
        p = next((candidate.resolve() for candidate in candidates if candidate.is_file()), candidates[0].resolve())
    else:
        p = p.resolve()
    p = _assert_media_path_allowed(project_id, p)
    if not p.is_file():
        raise FilmError("ASSET_NOT_FOUND", f"素材文件不存在：{p.name}", 422)
    return quote(os.path.relpath(p, root).replace("\\", "/"), safe="/-._~")


def library_asset_clip(project_id: str, asset_id: str, at: float, still_duration: float = 5.0) -> dict:
    """Resolve an approved library/project asset into a safe film clip descriptor."""
    pid = validate_project_id(project_id)
    aid = str(asset_id or "").strip()
    if not aid or len(aid) > 160:
        raise FilmError("ASSET_ID_REQUIRED", "缺少有效的素材 ID", 400)
    entry = None
    try:
        from video_director import asset_catalog as vd_assets
        entry = vd_assets.get_asset(aid)
    except Exception:
        entry = None
    if entry is None:
        root = config.composer_storage_root()
        if root.is_dir():
            for folder in root.iterdir():
                manifest_path = folder / "assets" / "manifest.json"
                if not folder.is_dir() or not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(manifest, list):
                    continue
                for item in manifest:
                    if isinstance(item, dict) and str(item.get("id") or item.get("asset_id") or "") == aid:
                        entry = dict(item)
                        entry["asset_id"] = aid
                        break
                if entry is not None:
                    break
    if not isinstance(entry, dict):
        raise FilmError("ASSET_NOT_FOUND", f"素材库中不存在素材：{aid}", 404)
    kind = str(entry.get("type") or "").lower()
    if kind not in {"video", "image", "audio"}:
        raise FilmError("ASSET_TYPE_UNSUPPORTED", f"素材类型 {kind or 'unknown'} 暂不支持直接加入时间线", 422)
    file_value = entry.get("file") or entry.get("path") or ""
    if not file_value:
        raise FilmError("ASSET_FILE_MISSING", f"素材 {aid} 没有可用文件路径", 422)
    src_uri = _safe_storage_uri(pid, file_value)
    at_value = _fmt(_number(at, "at", 0))
    if kind == "image":
        duration = _number(still_duration, "图片片段时长", 0.25, 600)
        tag = "img"
    else:
        duration = _number(entry.get("duration"), "素材时长", 0.001)
        tag = "audio" if kind == "audio" else "video"
    clip_id = "clip-" + hashlib.sha1(f"{aid}|{time.time_ns()}".encode("utf-8")).hexdigest()[:16]
    attrs = {"id": clip_id, "src": f"{src_uri}#t=0,{_fmt(duration)}", "at": at_value,
             "title": str(entry.get("description") or Path(str(file_value)).name)[:200], "data-asset-id": aid}
    if tag in {"video", "audio"}:
        attrs["volume"] = "1"
    if tag == "audio":
        attrs["data-role"] = str(entry.get("type") or "audio")
    return {"id": clip_id, "tag": tag, "attrs": attrs, "asset_id": aid, "duration": duration}


def _subtitle_page(width: int, height: int, duration: float, cues: list[dict], style: dict | None) -> str:
    data = json.dumps(cues, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    opts = style or {}
    absolute_size = opts.get("font_size", opts.get("fontSize"))
    if absolute_size is None:
        relative_size = _number(opts.get("size", 1.0), "subtitle style size", 0.01, 20)
        requested_size = 54 * relative_size
    else:
        requested_size = _number(absolute_size, "subtitle font size", 1, 1000)
    font_size = int(max(12, min(160, requested_size)))

    color = str(opts.get("color") or "#FFFFFF")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#FFFFFF"
    stroke_color = str(opts.get("stroke_color") or "#000000")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", stroke_color):
        stroke_color = "#000000"
    stroke_width = int(max(0, min(12, _number(opts.get("stroke_width", 2), "subtitle stroke width", 0, 12))))

    background_css = ""
    background = str(opts.get("background") or "")
    background_match = re.fullmatch(r"#([0-9a-fA-F]{6})(?:@([0-9]*\.?[0-9]+))?", background)
    if background_match:
        raw_hex = background_match.group(1)
        alpha = _number(
            background_match.group(2) if background_match.group(2) is not None else opts.get("background_opacity", 0.5),
            "subtitle background opacity", 0, 1,
        )
        red, green, blue = (int(raw_hex[i:i + 2], 16) for i in (0, 2, 4))
        background_css = f"background-color:rgba({red},{green},{blue},{_fmt(alpha)});padding:0.12em 0.24em;border-radius:0.12em;"

    max_width = _number(opts.get("max_width", 0.9), "subtitle max_width", 0.3, 1.0) * 100
    alignment = str(opts.get("alignment") or "center")
    if alignment not in {"left", "center", "right"}:
        alignment = "center"
    position = str(opts.get("position") or "bottom")
    margin = int(max(0, min(height - 10, height * 0.1)))
    if position == "top":
        position_css, base_transform = f"top:{margin}px;bottom:auto;", "translateX(-50%)"
    elif position == "center":
        position_css, base_transform = "top:50%;bottom:auto;", "translate(-50%,-50%)"
    else:
        position_css, base_transform = f"bottom:{margin}px;top:auto;", "translateX(-50%)"

    font_name = str(opts.get("font") or "")
    if re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", font_name):
        font_family = f'"{font_name}","WenQuanYi Micro Hei",sans-serif'
    else:
        font_family = 'Arial,"WenQuanYi Micro Hei",sans-serif'
    animation = str(opts.get("animation") or "none")
    if animation not in {"none", "fade", "pop_spring"}:
        animation = "none"

    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width={width},height={height}"><style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}}#stage{{position:relative;width:{width}px;height:{height}px;pointer-events:none}}
#caption{{position:absolute;left:50%;width:{_fmt(max_width)}%;max-width:{_fmt(max_width)}%;box-sizing:border-box;{position_css}transform:{base_transform};text-align:{alignment};font-family:{font_family};font-size:{font_size}px;font-weight:700;color:{color};-webkit-text-stroke:{stroke_width}px {stroke_color};text-shadow:0 2px 4px rgba(0,0,0,.9);{background_css}white-space:pre-wrap;line-height:1.25}}
</style></head><body><div id="stage"><div id="caption"></div></div><script>
const cues={data};const caption=document.getElementById('caption');
const baseTransform="{base_transform}";const animation="{animation}";
window.film={{width:{width},height:{height},duration:{_fmt(duration)},frame(t){{
  const active=cues.filter(c=>Number(c.start)<=t&&t<Number(c.end));
  caption.textContent=active.map(c=>String(c.text||'')).join('\\\\n');
  caption.style.display=active.length?'block':'none';
  let alpha=1;let transform=baseTransform;
  if(active.length){{
    const start=Math.min(...active.map(c=>Number(c.start)||0));
    if(animation==='fade'){{
      const trackStart=Math.min(...cues.map(c=>Number(c.start)||0));
      const trackEnd=Math.max(...cues.map(c=>Number(c.end)||0));
      const edge=Math.min(0.2,Math.max(0.04,(trackEnd-trackStart)/2));
      alpha=Math.min(1,Math.max(0,(t-trackStart)/edge),Math.max(0,(trackEnd-t)/edge));
    }}else if(animation==='pop_spring'){{
      const p=Math.max(0,Math.min(1,(t-start)/0.28));const eased=1-Math.pow(1-p,3);
      transform=baseTransform+' scale('+(0.82+0.18*eased)+')';
    }}
  }}
  caption.style.opacity=String(alpha);caption.style.transform=transform;
}}}};
window.film.frame(0);
window.addEventListener('message',function(event){{
  if(event.source!==parent||!event.data||event.data.type!=='film-time')return;
  const t=Number(event.data.time);
  if(!Number.isFinite(t))return;
  const current=Math.max(0,Math.min(window.film.duration,t));
  window.film.frame(current);
  requestAnimationFrame(function(){{requestAnimationFrame(function(){{
    parent.postMessage({{type:'film-time:ready',time:current}},'*');
  }});}});
}});
</script></body></html>'''




def _watermark_page(width: int, height: int, duration: float, watermark: dict,
                    image_src: str | None = None) -> str:
    anchor = str(watermark.get("anchor") or "bottom_right")
    if anchor not in _FILM_ANCHORS:
        raise FilmError("INVALID_WATERMARK_ANCHOR", f"水印 anchor 不支持：{anchor}", 422)
    anchor_x, anchor_y = _FILM_ANCHORS[anchor]
    safe_area = _number(watermark.get("safe_area", 32), "watermark safe_area", 0, 200)
    offset_x = _number(watermark.get("offset_x", 0), "watermark offset_x")
    offset_y = _number(watermark.get("offset_y", 0), "watermark offset_y")
    opacity = _number(watermark.get("opacity", 0.75), "watermark opacity", 0, 1)
    scale = _number(watermark.get("scale", 1), "watermark scale", 0.000001)
    if anchor_x == 0:
        horizontal = f"left:{_fmt(safe_area + offset_x)}px;right:auto;"
    elif anchor_x == 1:
        horizontal = f"right:{_fmt(safe_area - offset_x)}px;left:auto;"
    else:
        horizontal = "left:50%;right:auto;"
    if anchor_y == 0:
        vertical = f"top:{_fmt(safe_area + offset_y)}px;bottom:auto;"
    elif anchor_y == 1:
        vertical = f"bottom:{_fmt(safe_area - offset_y)}px;top:auto;"
    else:
        vertical = "top:50%;bottom:auto;"
    translates = []
    if anchor_x == 0.5:
        translates.append("translateX(-50%)")
    if anchor_y == 0.5:
        translates.append("translateY(-50%)")
    transforms = translates + [f"scale({_fmt(scale)})"]
    transform = " ".join(transforms)
    transform_origin = f"{int(anchor_x * 100)}% {int(anchor_y * 100)}%"
    if image_src:
        body = f'<img id="watermark" alt="" src="{html.escape(image_src, quote=True)}">'
        content_css = (
            f"width:auto;height:auto;max-width:none;max-height:none;object-fit:contain;"
            f"transform:{transform};transform-origin:{transform_origin};"
        )
    else:
        text = html.escape(str(watermark.get("text") or ""), quote=False)
        # CompositionIR has a scale multiplier, but no absolute text size; 24px is
        # the explicit migration baseline and is surfaced as a warning by the caller.
        font_size = _number(scale * 24, "watermark text font size", 0.1, 240)
        body = f'<div id="watermark">{text}</div>'
        content_css = (
            f"font-family:Arial,'WenQuanYi Micro Hei',sans-serif;font-size:{_fmt(font_size)}px;"
            f"font-weight:700;color:#ffffff;text-shadow:0 1px 3px rgba(0,0,0,.9);"
            f"white-space:pre-wrap;transform:{transform};transform-origin:{transform_origin};"
        )
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width={width},height={height}"><style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}}#stage{{position:relative;width:{width}px;height:{height}px;pointer-events:none}}
#watermark{{position:absolute;{horizontal}{vertical}opacity:{_fmt(opacity)};{content_css}}}
</style></head><body><div id="stage">{body}</div><script>
window.film={{width:{width},height:{height},duration:{_fmt(duration)},frame:function(_t){{}}}};
</script></body></html>'''





_FILM_ANCHORS = {
    "top_left": (0.0, 0.0), "top_center": (0.5, 0.0), "top_right": (1.0, 0.0),
    "center_left": (0.0, 0.5), "center": (0.5, 0.5), "center_right": (1.0, 0.5),
    "bottom_left": (0.0, 1.0), "bottom_center": (0.5, 1.0), "bottom_right": (1.0, 1.0),
}
_FILM_KEYFRAME_PROPERTIES = {"x", "y", "scale", "rotation", "opacity", "blur", "camera_x", "camera_y", "camera_scale", "offset_x", "offset_y"}


def _film_transform_metadata(transform: dict | None, stage_width: int, stage_height: int,
                             base_width: float, base_height: float) -> dict:
    """Normalize Transform into the deterministic 2D contract consumed by FilmRuntime."""
    raw = transform or {}
    anchor = str(raw.get("anchor") or "center")
    if anchor not in _FILM_ANCHORS:
        raise FilmError("INVALID_TRANSFORM_ANCHOR", f"不支持的 Transform anchor：{anchor}", 422)
    return {
        "x": _number(raw.get("x", 0.5), "transform.x", 0, 1),
        "y": _number(raw.get("y", 0.5), "transform.y", 0, 1),
        "scale": _number(raw.get("scale", 1), "transform.scale", 0.1, 5),
        "rotation": _number(raw.get("rotation", 0), "transform.rotation", -3600, 3600),
        "opacity": _number(raw.get("opacity", 1), "transform.opacity", 0, 1),
        "anchor": anchor,
        "stage_width": int(stage_width),
        "stage_height": int(stage_height),
        "base_width": _number(base_width, "transform base width", 1),
        "base_height": _number(base_height, "transform base height", 1),
    }


def _film_transform_css(transform: dict, fit_mode: str | None = None) -> str:
    anchor_x, anchor_y = _FILM_ANCHORS[transform["anchor"]]
    scale = float(transform["scale"])
    width = float(transform["base_width"]) * scale
    height = float(transform["base_height"]) * scale
    left = float(transform["x"]) * int(transform["stage_width"]) - anchor_x * width
    top = float(transform["y"]) * int(transform["stage_height"]) - anchor_y * height
    origin = f"{int(anchor_x * 100)}% {int(anchor_y * 100)}%"
    parts = [
        f"left:{_fmt(left)}px", f"top:{_fmt(top)}px",
        f"width:{_fmt(width)}px", f"height:{_fmt(height)}px",
        f"opacity:{_fmt(float(transform['opacity']))}",
        f"rotate:{_fmt(float(transform['rotation']))}deg",
        f"transform-origin:{origin}",
    ]
    if fit_mode in {"cover", "contain", "fill", "none", "scale-down"}:
        parts.append(f"object-fit:{fit_mode}")
    return ";".join(parts)


def _film_keyframes(raw_tracks: list, duration: float) -> tuple[list[dict], list[str]]:
    """Convert the supported 2D properties and return explicit warnings for others."""
    channels, unsupported = [], []
    if not isinstance(raw_tracks, list):
        return [], ["keyframes 格式无效"]
    for track in raw_tracks:
        if not isinstance(track, dict):
            unsupported.append("keyframes 项格式无效")
            continue
        prop = str(track.get("property") or "")
        points = track.get("keyframes") or []
        if prop not in _FILM_KEYFRAME_PROPERTIES:
            unsupported.append(prop or "unknown")
            continue
        mapped, invalid = [], False
        for point in points:
            try:
                at = _number(point.get("at"), f"keyframe {prop}.at", 0)
                value = _number(point.get("value"), f"keyframe {prop}.value")
            except (AttributeError, FilmError):
                invalid = True
                break
            if at > duration + 0.001:
                unsupported.append(f"{prop}@{_fmt(at)}s 超出片段时长")
                continue
            limits = {
                "x": (0.0, 1.0), "y": (0.0, 1.0), "scale": (0.1, 5.0),
                "rotation": (-3600.0, 3600.0), "opacity": (0.0, 1.0), "blur": (0.0, 100.0),
                # Camera keyframes are 2D Composition-space view controls. FilmRuntime
                # applies them around the frame center to each clip in the active scene.
                "camera_x": (0.0, 1.0), "camera_y": (0.0, 1.0), "camera_scale": (0.1, 5.0),
                "offset_x": (-10000.0, 10000.0), "offset_y": (-10000.0, 10000.0),
            }[prop]
            if not limits[0] <= value <= limits[1]:
                invalid = True
                break
            easing = str(point.get("easing") or "linear")
            if easing not in {"linear", "ease_in", "ease_out", "ease_in_out"}:
                unsupported.append(f"{prop} 缓动 {easing}")
                easing = "linear"
            mapped.append({"at": at, "value": value, "easing": easing})
        if invalid:
            unsupported.append(f"{prop} 含非数值或越界关键帧")
            continue
        if mapped:
            mapped.sort(key=lambda item: item["at"])
            channels.append({"property": prop, "keyframes": mapped})
    return channels, list(dict.fromkeys(unsupported))




def _film_motion_recipe_keyframes(layer: dict, clip_duration: float) -> tuple[list[dict], list[str]]:
    """Resolve a version-pinned MotionRecipe into deterministic frame(t) keyframes.

    Recipe x/y channels are pixel offsets, while Layer.keyframes x/y are normalized
    composition coordinates. They intentionally use separate FilmRuntime properties.
    """
    recipe_id = str(layer.get("motion_recipe_id") or "")
    if not recipe_id:
        return [], []
    version = str(layer.get("motion_version") or "") or None
    warnings: list[str] = []
    instance = layer.get("motion_instance") or {}
    if not isinstance(instance, dict):
        return [], [f"motion_instance for {recipe_id} 必须是对象"]

    try:
        from video_director.registries.recipes import MotionRegistry
        recipe = MotionRegistry().get(recipe_id, version)
    except Exception as exc:
        return [], [
            f"动效配方 {recipe_id}@{version or 'latest'} 无法解析，关键帧未迁移："
            f"{type(exc).__name__}: {str(exc)[:160]}"
        ]

    recommended = float(recipe.duration_recommended)
    raw_duration = instance.get("duration", recommended)
    try:
        motion_duration = _number(
            raw_duration, f"{recipe_id}.duration",
            float(recipe.duration_min), float(recipe.duration_max)
        )
    except FilmError as exc:
        motion_duration = recommended
        warnings.append(
            f"动效 {recipe_id} 实例时长 {raw_duration!r} 不在 "
            f"{recipe.duration_min:g}–{recipe.duration_max:g}s 范围，"
            f"按推荐时长 {recommended:g}s 迁移：{exc}"
        )
    try:
        start_offset = _number(
            instance.get("start_offset", 0), f"{recipe_id}.start_offset", 0, 600
        )
    except FilmError as exc:
        return [], warnings + [f"动效 {recipe_id} 的 start_offset 无效，关键帧未迁移：{exc}"]

    raw_parameters = instance.get("parameters") or {}
    if not isinstance(raw_parameters, dict):
        warnings.append(f"动效 {recipe_id} 的 parameters 不是对象；按配方默认参数处理")
        raw_parameters = {}
    parameter_defs = {
        str(item.get("name")): item
        for item in recipe.parameters
        if isinstance(item, dict) and item.get("name")
    }
    parameters: dict[str, float] = {}
    for name, spec in parameter_defs.items():
        default = spec.get("default", 0)
        try:
            parameters[name] = _number(
                raw_parameters.get(name, default),
                f"{recipe_id}.parameters.{name}",
                float(spec.get("min", -1e9)),
                float(spec.get("max", 1e9))
            )
        except FilmError as exc:
            parameters[name] = float(default)
            warnings.append(
                f"动效 {recipe_id} 参数 {name}={raw_parameters.get(name)!r} 无效，"
                f"按默认值 {default!r} 迁移：{exc}"
            )

    property_map = {
        "opacity": "opacity",
        "scale": "scale",
        "rotation": "rotation",
        "blur": "blur",
        "x": "offset_x",
        "y": "offset_y",
    }
    mapped_channels: list[dict] = []
    for source_channel in recipe.channels:
        source_property = str(source_channel.property)
        target_property = property_map.get(source_property)
        if target_property is None:
            warnings.append(
                f"动效 {recipe_id} 的 channel {source_property} 当前 FilmRuntime 无对应属性，未迁移"
            )
            continue

        points: list[dict] = []
        bad_channel = False
        for keyframe in source_channel.keyframes:
            try:
                value = _number(keyframe.value, f"{recipe_id}.{source_property}.value")
            except FilmError:
                warnings.append(
                    f"动效 {recipe_id} 的 {source_property} 含非数值关键帧，channel 未迁移"
                )
                bad_channel = True
                break

            if source_property in {"x", "y"} and "offsetPx" in parameters:
                default_offset = float(parameter_defs["offsetPx"].get("default", 0) or 0)
                if abs(default_offset) > 1e-9:
                    value *= parameters["offsetPx"] / default_offset
            if source_property == "scale" and len(points) == 0 and "fromScale" in parameters:
                value = parameters["fromScale"]

            easing = keyframe.easing or {"type": "linear", "mode": "in"}
            if isinstance(easing, dict):
                easing = dict(easing)
                easing_type = str(easing.get("type") or "linear")
                easing_mode = str(easing.get("mode") or "in").lower().replace("_", "")
                if easing_type not in {
                    "linear", "quad", "cubic", "quart", "quint", "sine",
                    "expo", "circ", "back", "elastic"
                } or easing_mode not in {"in", "out", "inout"}:
                    warnings.append(
                        f"动效 {recipe_id} 的缓动 {easing_type}/{easing_mode} 不支持，改用线性"
                    )
                    easing = "linear"
                elif easing_type == "back" and "overshoot" in parameters:
                    easing["parameters"] = dict(easing.get("parameters") or {})
                    easing["parameters"]["overshoot"] = parameters["overshoot"]
            elif easing not in {"linear", "ease_in", "ease_out", "ease_in_out"}:
                warnings.append(f"动效 {recipe_id} 的 easing 格式无效，改用线性")
                easing = "linear"

            at = start_offset + float(keyframe.at) * motion_duration
            points.append({"at": round(at, 6), "value": value, "easing": easing})

        if bad_channel or not points:
            continue
        points.sort(key=lambda item: item["at"])
        if start_offset > 0:
            # Hold the initial value before a delayed entrance starts.
            initial = dict(points[0])
            initial["at"] = 0.0
            points.insert(0, initial)
        mapped_channels.append({"property": target_property, "keyframes": points})

    if start_offset + motion_duration > clip_duration + 0.001:
        warnings.append(
            f"动效 {recipe_id} 结束于 {start_offset + motion_duration:.3f}s，"
            f"超过片段时长 {clip_duration:.3f}s；超出部分不会显示"
        )
    ignored_parameters = {
        name for name, value in parameters.items()
        if abs(value - float(parameter_defs[name].get("default", value))) > 1e-9
        and name not in {"offsetPx", "fromScale", "overshoot"}
    }
    if ignored_parameters:
        warnings.append(
            f"动效 {recipe_id} 的参数 {sorted(ignored_parameters)} 未影响已映射关键帧；"
            "原始参数仍保存在 data-motion-instance"
        )
    if recipe.motion_type == "kinetic_text":
        warnings.append(
            f"动效 {recipe_id} 属于 kinetic_text；逐词/级联语义不能作用于单个媒体片段，"
            "本次只迁移可表示的整体属性关键帧"
        )
    return mapped_channels, list(dict.fromkeys(warnings))


def _film_volume_envelope(raw_tracks: list, duration: float) -> tuple[list[dict], list[str]]:
    """Convert volume keyframes into the bounded envelope consumed by Film Runtime."""
    if not isinstance(raw_tracks, list):
        return [], ["volume keyframes 格式无效"]
    mapped: list[dict] = []
    warnings: list[str] = []
    found = False
    for track in raw_tracks:
        if not isinstance(track, dict):
            warnings.append("volume keyframes 项格式无效")
            continue
        prop = str(track.get("property") or "")
        if prop != "volume":
            warnings.append(f"非 volume 关键帧属性 {prop or 'unknown'} 不适用于音量包络")
            continue
        found = True
        points = track.get("keyframes") or []
        if not isinstance(points, list):
            warnings.append("volume keyframes 列表格式无效")
            continue
        for point in points:
            try:
                at = _number(point.get("at"), "volume keyframe.at", 0)
                value = _number(point.get("value"), "volume keyframe.value", 0, 1)
            except (AttributeError, FilmError):
                warnings.append("volume 关键帧含非数值或越界值（允许范围 0–1）")
                continue
            easing = str(point.get("easing") or "linear")
            if easing != "linear":
                warnings.append(
                    f"volume@{_fmt(at)}s 的缓动 {easing} 尚未进入统一包络协议；本次按线性插值迁移"
                )
            if at > duration + 0.001:
                warnings.append(f"volume@{_fmt(at)}s 超出片段时长")
                continue
            if at > 600:
                warnings.append(f"volume@{_fmt(at)}s 超出 Runtime 音量包络 600s 的限制")
                continue
            mapped.append({"t": at, "v": value})
    if not found:
        return [], list(dict.fromkeys(warnings))
    mapped.sort(key=lambda point: point["t"])
    normalized: list[dict] = []
    for point in mapped:
        if normalized and abs(normalized[-1]["t"] - point["t"]) <= 1e-9:
            normalized[-1] = point
            warnings.append(f"volume 在 {_fmt(point['t'])}s 有重复关键帧；迁移保留最后一个")
        else:
            normalized.append(point)
    if len(normalized) > 128:
        warnings.append(f"volume 关键帧数量 {len(normalized)} 超出 Runtime 的 128 个限制；音量包络未写入")
        return [], list(dict.fromkeys(warnings))
    if not normalized:
        warnings.append("volume 关键帧没有可迁移的有效点")
        return [], list(dict.fromkeys(warnings))
    if len(normalized) == 1:
        # A lone gain point has no slope; preserve it as a constant envelope.
        gain = normalized[0]["v"]
        end = min(max(0.001, float(duration)), 600.0)
        normalized = [{"t": 0.0, "v": gain}, {"t": end, "v": gain}]
        warnings.append("volume 只有一个有效关键帧；按恒定音量补齐 0s 与片段末端")
    return normalized, list(dict.fromkeys(warnings))



def _film_mask_keyframes(raw_tracks: list, duration: float) -> tuple[list[dict], list[str]]:
    """Convert mask_path keyframes containing normalized polygon vertices."""
    if not isinstance(raw_tracks, list):
        return [], ["mask_path keyframes 格式无效"]
    mapped: list[dict] = []
    warnings: list[str] = []
    found = False
    for track in raw_tracks:
        if not isinstance(track, dict):
            warnings.append("mask_path keyframe track 格式无效")
            continue
        prop = str(track.get("property") or "")
        if prop != "mask_path":
            warnings.append(f"蒙版关键帧属性 {prop or 'unknown'} 尚未迁移")
            continue
        found = True
        points = track.get("keyframes") or []
        if not isinstance(points, list):
            warnings.append("mask_path keyframes 必须是数组")
            continue
        track_points: list[dict] = []
        for point in points:
            try:
                at = _number(point.get("at"), "mask_path keyframe.at", 0)
            except (AttributeError, FilmError) as exc:
                warnings.append(f"mask_path keyframe 时间无效：{exc}")
                continue
            value = point.get("value") if isinstance(point, dict) else None
            if isinstance(value, dict):
                value = value.get("geometry") or value.get("points")
            if not isinstance(value, list) or not 3 <= len(value) <= 64:
                warnings.append(f"mask_path@{_fmt(at)}s 顶点数必须在 3–64 之间")
                continue
            vertices: list[list[float]] = []
            valid = True
            for vertex in value:
                try:
                    if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
                        raise FilmError("INVALID_MASK_KEYFRAME", "顶点必须是 [x,y]")
                    vx = _number(vertex[0], "mask_path.x", 0, 1)
                    vy = _number(vertex[1], "mask_path.y", 0, 1)
                except FilmError as exc:
                    warnings.append(f"mask_path@{_fmt(at)}s 顶点无效：{exc}")
                    valid = False
                    break
                vertices.append([vx, vy])
            if not valid:
                continue
            if at > duration + 0.001:
                warnings.append(f"mask_path@{_fmt(at)}s 超出片段时长")
                continue
            if at > 600:
                warnings.append(f"mask_path@{_fmt(at)}s 超出 Runtime 600s 限制")
                continue
            easing = str(point.get("easing") or "linear")
            if easing not in {"linear", "ease_in", "ease_out", "ease_in_out"}:
                warnings.append(f"mask_path@{_fmt(at)}s 缓动 {easing} 不受支持，改用线性插值")
                easing = "linear"
            track_points.append({"at": at, "value": vertices, "easing": easing})
        if track_points:
            mapped.extend(track_points)

    if not found:
        return [], list(dict.fromkeys(warnings))
    mapped.sort(key=lambda point: point["at"])
    normalized: list[dict] = []
    for point in mapped:
        if normalized and abs(normalized[-1]["at"] - point["at"]) <= 1e-9:
            normalized[-1] = point
            warnings.append(f"mask_path 在 {_fmt(point['at'])}s 有重复关键帧；保留最后一个")
        else:
            normalized.append(point)
    if normalized:
        vertex_count = len(normalized[0]["value"])
        if any(len(point["value"]) != vertex_count for point in normalized):
            warnings.append("mask_path 各关键帧顶点数量不同，不能逐点插值；动态蒙版未写入")
            return [], list(dict.fromkeys(warnings))
    if len(normalized) > 128:
        warnings.append("mask_path 关键帧数量超出 Runtime 的 128 个限制；动态蒙版未写入")
        return [], list(dict.fromkeys(warnings))
    if not normalized:
        warnings.append("mask_path 没有可迁移的有效关键帧")
    return normalized, list(dict.fromkeys(warnings))



def _motion_keyframes(motion: str | None, duration: float, transform: dict,
                      channels: list[dict]) -> tuple[list[dict], str | None]:
    """Map supported legacy zoom/pan presets into the frame(t) keyframe contract."""
    if not motion:
        return channels, None
    existing = {channel.get("property") for channel in channels}
    x, scale = float(transform["x"]), float(transform["scale"])
    if motion == "slow_zoom_in":
        prop = "scale"
        points = [{"at": 0.0, "value": scale, "easing": "linear"},
                  {"at": duration, "value": min(5.0, scale * 1.08), "easing": "ease_in_out"}]
    elif motion == "slow_zoom_out":
        prop = "scale"
        points = [{"at": 0.0, "value": min(5.0, scale * 1.08), "easing": "ease_in_out"},
                  {"at": duration, "value": scale, "easing": "linear"}]
    elif motion == "pan_left":
        prop = "x"
        points = [{"at": 0.0, "value": min(1.0, x + 0.02), "easing": "linear"},
                  {"at": duration, "value": max(0.0, x - 0.02), "easing": "ease_in_out"}]
    elif motion == "pan_right":
        prop = "x"
        points = [{"at": 0.0, "value": max(0.0, x - 0.02), "easing": "linear"},
                  {"at": duration, "value": min(1.0, x + 0.02), "easing": "ease_in_out"}]
    else:
        return channels, f"motion {motion} 尚不支持 2D 映射"
    if prop in existing:
        return channels, f"motion {motion} 与显式 {prop} 关键帧冲突；保留显式关键帧"
    channels.append({"property": prop, "keyframes": points})
    return channels, None


def _apply_film_transform(attrs: dict, transform: dict, channels: list[dict],
                          fit_mode: str | None = None) -> None:
    attrs["data-film-transform"] = json.dumps(transform, ensure_ascii=False, separators=(",", ":"))
    attrs["style"] = _film_transform_css(transform, fit_mode)
    if channels:
        attrs["data-film-keyframes"] = json.dumps(channels, ensure_ascii=False, separators=(",", ":"))




def _film_mask_clip_path(raw_mask: dict | None, allow_empty_path: bool = False) -> tuple[str | None, list[str]]:
    """Convert only CSS-representable mask geometry; report alpha/feather/inversion loss."""
    if not isinstance(raw_mask, dict):
        return None, ["蒙版格式无效"]
    warnings: list[str] = []
    shape = str(raw_mask.get("shape") or "rectangle")
    try:
        x = _number(raw_mask.get("x", 0.5), "mask.x", 0, 1)
        y = _number(raw_mask.get("y", 0.5), "mask.y", 0, 1)
        width = _number(raw_mask.get("width", 0.5), "mask.width", 0, 1)
        height = _number(raw_mask.get("height", 0.5), "mask.height", 0, 1)
        feather = _number(raw_mask.get("feather", 0), "mask.feather", 0)
        opacity = _number(raw_mask.get("opacity", 1), "mask.opacity", 0, 1)
    except FilmError as exc:
        return None, [f"蒙版几何参数无效：{exc}"]

    if raw_mask.get("inverted"):
        warnings.append("inverted 反向蒙版不能用 CSS clip-path 无损表达")
        return None, warnings
    if feather > 0:
        warnings.append("feather 羽化未映射（CSS clip-path 不支持该羽化语义）")
    if opacity < 1:
        warnings.append("蒙版独立 opacity 未映射；片段本身的 opacity 不变")
    if shape == "rectangle":
        top = (y - height / 2) * 100
        right = (1 - x - width / 2) * 100
        bottom = (1 - y - height / 2) * 100
        left = (x - width / 2) * 100
        return f"inset({_fmt(top)}% {_fmt(right)}% {_fmt(bottom)}% {_fmt(left)}%)", warnings
    if shape == "ellipse":
        return (
            f"ellipse({_fmt(width * 50)}% {_fmt(height * 50)}% at {_fmt(x * 100)}% {_fmt(y * 100)}%)",
            warnings,
        )
    if shape in {"polygon", "path"}:
        # CompositionIR's Mask.geometry stores normalized [x,y] vertices for
        # both polygon and path; it does not carry Bezier commands or open-path
        # stroke data. Preserve the polygonal path; flag that semantic boundary.
        points = raw_mask.get("geometry") or []
        if not isinstance(points, list) or len(points) < 3:
            if allow_empty_path and shape in {"polygon", "path"} and not points:
                return None, warnings
            return None, warnings + [f"{shape} geometry 少于三个顶点，不能迁移"]
        mapped_points = []
        try:
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise FilmError("INVALID_MASK_GEOMETRY", f"{shape} 顶点必须是 [x,y]")
                px = _number(point[0], "mask.geometry.x", 0, 1)
                py = _number(point[1], "mask.geometry.y", 0, 1)
                mapped_points.append(f"{_fmt(px * 100)}% {_fmt(py * 100)}%")
        except FilmError as exc:
            return None, warnings + [f"{shape} 顶点无效：{exc}"]
        if shape == "path":
            warnings.append(
                "path 蒙版按 IR 中保存的归一顶点转换为闭合多边形；Bezier/开放路径语义未进入当前 schema"
            )
        return "polygon(" + ", ".join(mapped_points) + ")", warnings
    if shape == "rounded_rect":
        return None, warnings + ["rounded_rect 未提供明确圆角半径，不能无损映射"]
    if shape == "path":
        return None, warnings + ["path 蒙版的路径数据不是 CSS polygon，可编辑路径尚未迁移"]
    return None, warnings + [f"蒙版形状 {shape} 尚未映射"]






def _film_camera_channels(raw_tracks: list, clip_start: float, clip_duration: float) -> tuple[list[dict], list[str]]:
    """Map only an explicitly supported, top-down orthographic CameraTrack to frame-driven 2D channels.

    Perspective projection, follow targets, constraints, non-linear easing and camera rotation
    require semantics that the current 2D Runtime cannot reproduce. They remain in the source
    snapshot and produce a warning instead of being silently approximated.
    """
    if not raw_tracks:
        return [], []
    if not isinstance(raw_tracks, list):
        return [], ["camera_tracks 格式无效"]
    if len(raw_tracks) != 1:
        return [], [f"camera_tracks 有 {len(raw_tracks)} 条 CameraTrack，未提供活动轨道标识，无法安全合并"]
    track = raw_tracks[0]
    if not isinstance(track, dict):
        return [], ["camera_tracks 中存在无效 CameraTrack"]
    track_id = str(track.get("id") or "unknown")
    projection = str(track.get("projection") or "perspective").lower()
    if projection != "orthographic":
        return [], [f"camera_tracks {track_id} 的 projection={projection}；透视投影不能由 2D FilmRuntime 无损表达"]
    if track.get("follow_target") or track.get("constraints"):
        return [], [f"camera_tracks {track_id} 含 follow_target/constraints，不能转换为独立 2D 摄影机运动"]

    raw_keyframes = track.get("keyframes") or []
    if not isinstance(raw_keyframes, list) or not raw_keyframes:
        return [], [f"camera_tracks {track_id} 没有可迁移的摄影机关键帧"]

    keyframes, fixed_direction = [], None
    for frame in raw_keyframes:
        if not isinstance(frame, dict):
            return [], [f"camera_tracks {track_id} 含无效关键帧"]
        try:
            at = _number(frame.get("at"), f"camera_tracks {track_id}.at", 0)
            position, target = frame.get("position"), frame.get("target")
            if not isinstance(position, list) or not isinstance(target, list) or len(position) != 3 or len(target) != 3:
                raise FilmError("INVALID_CAMERA_KEYFRAME", "position/target 必须是三个坐标")
            position = [float(value) for value in position]
            target = [float(value) for value in target]
            if not all(math.isfinite(value) for value in position + target):
                raise FilmError("INVALID_CAMERA_KEYFRAME", "position/target 必须是有限数值")
        except (TypeError, ValueError, FilmError) as exc:
            return [], [f"camera_tracks {track_id} 含无效 position/target：{exc}"]

        if not all(0.0 <= value <= 1.0 for value in position[:2] + target[:2]):
            return [], [f"camera_tracks {track_id} 的 x/y 不是 [0,1] 归一坐标，未猜测坐标单位"]
        direction = tuple(round(position[index] - target[index], 8) for index in range(3))
        # Only fixed top-down direction can be represented as a 2D pan without faking rotation.
        if abs(direction[0]) > 1e-6 or abs(direction[1]) > 1e-6 or abs(direction[2]) <= 1e-6:
            return [], [f"camera_tracks {track_id} 不是固定俯视方向；摄像机旋转/3D 语义尚不能无损转换"]
        if fixed_direction is None:
            fixed_direction = direction
        elif any(abs(direction[i] - fixed_direction[i]) > 1e-6 for i in range(3)):
            return [], [f"camera_tracks {track_id} 的 position-target 方向变化，需要 3D 摄影机求解"]

        easing = frame.get("easing", "linear")
        easing = str(getattr(easing, "value", easing))
        if easing != "linear":
            return [], [f"camera_tracks {track_id} 使用 {easing} 缓动；当前摄像机迁移仅支持 linear"]

        keyframes.append({"at": at, "x": target[0], "y": target[1]})
    keyframes.sort(key=lambda item: item["at"])
    for left, right in zip(keyframes, keyframes[1:]):
        if right["at"] <= left["at"]:
            return [], [f"camera_tracks {track_id} 含重复或逆序关键帧时间"]

    clip_start = _number(clip_start, "camera clip start", 0)
    clip_duration = _number(clip_duration, "camera clip duration", 0.001)
    clip_end = clip_start + clip_duration
    if clip_end < keyframes[0]["at"] - 1e-6:
        return [], []

    def sample(global_time: float, axis: str) -> float:
        if global_time < keyframes[0]["at"]:
            return 0.5
        if global_time >= keyframes[-1]["at"]:
            return float(keyframes[-1][axis])
        for left, right in zip(keyframes, keyframes[1:]):
            if left["at"] <= global_time <= right["at"]:
                span = right["at"] - left["at"]
                ratio = 1.0 if span <= 0 else (global_time - left["at"]) / span
                return float(left[axis]) + (float(right[axis]) - float(left[axis])) * ratio
        return float(keyframes[-1][axis])

    camera_x, camera_y = [], []
    if clip_start >= keyframes[0]["at"] - 1e-6:
        camera_x.append({"at": 0.0, "value": sample(clip_start, "x"), "easing": "linear"})
        camera_y.append({"at": 0.0, "value": sample(clip_start, "y"), "easing": "linear"})

    for frame in keyframes:
        if frame["at"] < clip_start - 1e-6 or frame["at"] > clip_end + 1e-6:
            continue
        local_at = max(0.0, frame["at"] - clip_start)
        point_x = {"at": local_at, "value": frame["x"], "easing": "linear"}
        point_y = {"at": local_at, "value": frame["y"], "easing": "linear"}
        if not camera_x or abs(camera_x[-1]["at"] - local_at) > 1e-6:
            camera_x.append(point_x)
        if not camera_y or abs(camera_y[-1]["at"] - local_at) > 1e-6:
            camera_y.append(point_y)

    # Preserve the original linear curve when a clip starts/ends between camera keys.
    if clip_end >= keyframes[0]["at"] - 1e-6 and clip_end < keyframes[-1]["at"] - 1e-6:
        if not camera_x or abs(camera_x[-1]["at"] - clip_duration) > 1e-6:
            camera_x.append({"at": clip_duration, "value": sample(clip_end, "x"), "easing": "linear"})
        if not camera_y or abs(camera_y[-1]["at"] - clip_duration) > 1e-6:
            camera_y.append({"at": clip_duration, "value": sample(clip_end, "y"), "easing": "linear"})

    if not camera_x and clip_start > keyframes[-1]["at"]:
        camera_x = [{"at": 0.0, "value": keyframes[-1]["x"], "easing": "linear"}]
        camera_y = [{"at": 0.0, "value": keyframes[-1]["y"], "easing": "linear"}]

    channels = []
    if camera_x:
        channels.append({"property": "camera_x", "keyframes": camera_x})
    if camera_y:
        channels.append({"property": "camera_y", "keyframes": camera_y})
    return channels, []


def _collect_unmodeled_source_fields(source: Any, model: Any, prefix: str = "CompositionIR") -> list[str]:
    """Locate future/unknown keys before Pydantic's default extra-field filtering hides them."""
    if not isinstance(source, dict):
        return []
    fields = getattr(type(model), "model_fields", None)
    if not isinstance(fields, dict):
        return []
    unknown = [f"{prefix}.{key}" for key in sorted(source) if key not in fields]
    for key, raw_value in source.items():
        if key not in fields:
            continue
        child_model = getattr(model, key, None)
        child_fields = getattr(type(child_model), "model_fields", None)
        child_prefix = f"{prefix}.{key}"
        if isinstance(raw_value, dict) and isinstance(child_fields, dict):
            unknown.extend(_collect_unmodeled_source_fields(raw_value, child_model, child_prefix))
        elif isinstance(raw_value, list) and isinstance(child_model, list):
            for index, (raw_child, model_child) in enumerate(zip(raw_value, child_model)):
                if isinstance(raw_child, dict) and isinstance(getattr(type(model_child), "model_fields", None), dict):
                    unknown.extend(_collect_unmodeled_source_fields(raw_child, model_child, f"{child_prefix}[{index}]"))
    return unknown


def _source_composition_archive(project_id: str, ir: Any, typed_ir: dict) -> tuple[dict, list[str]]:
    """Archive the exact persisted JSON while proving its typed fields match the converted IR."""
    source_path = config.project_dir(project_id) / "plans" / "composition.json"
    if not source_path.is_file():
        return typed_ir, []
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        normalized = type(ir).model_validate(source).model_dump(mode="json")
        current = ir.model_dump(mode="json")
    except Exception as exc:
        raise FilmError(
            "COMPOSITION_SOURCE_CHANGED",
            f"无法确认磁盘 composition.json 与当前迁移对象一致；请重新载入迁移预览：{exc}",
            409,
        ) from exc
    if normalized != current:
        raise FilmError(
            "COMPOSITION_SOURCE_CHANGED",
            "磁盘 composition.json 已与当前迁移对象不同；为避免归档和成片来自不同版本，请重新载入。",
            409,
        )
    unmodeled = _collect_unmodeled_source_fields(source, ir)
    diagnostics = []
    if unmodeled:
        shown = ", ".join(unmodeled[:20])
        suffix = f" 等另外 {len(unmodeled) - 20} 项" if len(unmodeled) > 20 else ""
        diagnostics.append(
            f"旧 composition.json 含 {len(unmodeled)} 个当前 schema 未建模字段（{shown}{suffix}）；"
            "完整原始值已归档，但这些字段不会被猜测为可执行的 film.html 语义。"
        )
    return source, diagnostics

def build_film_from_composition(project_id: str, ir) -> dict:
    """Convert supported video/image/audio/subtitle content; report non-lossless fields."""
    pid = validate_project_id(project_id)
    raw_ir = json.loads(ir.model_dump_json())
    source_archive_ir, source_archive_warnings = _source_composition_archive(pid, ir, raw_ir)
    root = _project_root(pid)
    project_spec = raw_ir.get("project") or {}
    stage = {"width": int(project_spec.get("width", 1080)),
             "height": int(project_spec.get("height", 1920))}
    declared_duration = _number(project_spec.get("duration", 1), "project duration", 0.001)
    duration = declared_duration
    variants = raw_ir.get("variants") or []
    selected_variant = None
    requested_variant = raw_ir.get("active_variant_id")
    if requested_variant:
        selected_variant = next((variant for variant in variants
                                 if str(variant.get("id")) == str(requested_variant)), None)
        if selected_variant is None:
            raise FilmError("ACTIVE_VARIANT_NOT_FOUND",
                            f"active_variant_id {requested_variant!r} 未匹配到 Composition 变体，拒绝静默回退", 422)
    elif len(variants) == 1:
        selected_variant = variants[0]
    source_scenes = list((selected_variant or {}).get("scenes") or raw_ir.get("scenes") or [])
    source_audio = list((selected_variant or {}).get("tracks") or raw_ir.get("audio") or [])
    variant_scene_end = max(
        (float(scene.get("start", 0)) + float(scene.get("duration", 0)) for scene in source_scenes),
        default=0.0,
    )
    variant_audio_end = max(
        (float(audio.get("start", 0)) + float(audio.get("duration", 0))
         for audio in source_audio if isinstance(audio, dict)),
        default=0.0,
    )
    variant_duration = max(variant_scene_end, variant_audio_end)
    variant_warnings = []
    if selected_variant and variant_duration > 0:
        if abs(variant_duration - declared_duration) > 0.05:
            variant_warnings.append(
                f"所选变体 {selected_variant.get('id')} 最晚画面/音轨结束于 {variant_duration:.3f}s，"
                f"与旧 project.duration {declared_duration:.3f}s 不同；迁移按画面与音轨的较晚结束时间定时长"
            )
        duration = variant_duration
    manifest_path = root / "assets" / "manifest.json"
    manifest = []
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise FilmError("MANIFEST_CORRUPT", "项目 assets/manifest.json 损坏，无法安全迁移", 422)
    by_id = {str(a.get("id")): a for a in manifest if isinstance(a, dict) and a.get("id")}
    for asset in raw_ir.get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("id"):
            by_id.setdefault(str(asset["id"]), asset)

    def asset_uri(asset_id):
        if not asset_id:
            raise FilmError("ASSET_ID_MISSING", "片段缺少 asset_id，无法生成素材引用", 422)
        item = by_id.get(str(asset_id), {})
        file_value = item.get("file") or ""
        if not file_value:
            try:
                from video_director import asset_catalog
                file_value = (asset_catalog.get_asset(str(asset_id)) or {}).get("file") or ""
            except Exception:
                pass
        if not file_value:
            try:
                from video_director import audio as vd_audio
                obj = vd_audio.scan_library().get(str(asset_id))
                file_value = getattr(obj, "file", "") if obj else ""
            except Exception:
                pass
        return _safe_storage_uri(pid, file_value)

    clips, tracks, pages, warnings = [], [], {}, []
    warnings.extend(variant_warnings)
    warnings.extend(source_archive_warnings)
    if variants:
        if selected_variant:
            alternatives = [str(item.get("id")) for item in variants
                           if str(item.get("id")) != str(selected_variant.get("id"))]
            warnings.append(
                f"本次迁移选择变体 {selected_variant.get('id')}；film.html 是单条最终时间线，"
                f"其它 variants {alternatives or '无'} 不会被并入成片"
            )
            if selected_variant.get("style"):
                warnings.append(f"变体 {selected_variant.get('id')} 的 variant.style 未映射到 film.html")
            if selected_variant.get("metadata"):
                warnings.append(
                    f"变体 {selected_variant.get('id')} 的 metadata 字段未映射到 film.html；"
                    "完整内容保留于 migration/source-composition.json"
                )
            if not selected_variant.get("scenes"):
                warnings.append(f"变体 {selected_variant.get('id')} 没有独立 scenes，当前使用顶层 scenes")
            if not selected_variant.get("tracks"):
                warnings.append(f"变体 {selected_variant.get('id')} 没有独立 tracks，当前使用顶层 audio")
        else:
            warnings.append(
                f"Composition 有 {len(variants)} 个 variants 但没有有效 active_variant_id；"
                "按顶层 scenes/audio 迁移，未隐式合并候选变体"
            )
    if int(project_spec.get("fps", config.DEFAULT_FPS)) != config.DEFAULT_FPS:
        warnings.append(
            f"旧项目帧率为 {project_spec.get('fps')} FPS；film.html 未保存此设置，"
            f"导出默认帧率为 {config.DEFAULT_FPS} FPS，需显式选择目标帧率"
        )
    if raw_ir.get("style_id"):
        warnings.append("旧项目 style_id 未映射到 film.html 的可执行样式；需检查成片样式")
    if raw_ir.get("style_contract"):
        warnings.append("旧项目 style_contract 尚未转换为 film.html 样式，语义样式信息会丢失")
    if raw_ir.get("origin_template_id") or raw_ir.get("origin_template_version"):
        warnings.append("旧项目模板来源 lineage 未写入 film.html")
    if raw_ir.get("continuity_links"):
        warnings.append("旧项目 continuity_links 仅属创作 lineage，未映射到 film.html")
    references = raw_ir.get("references") or {}
    if references.get("references"):
        warnings.append("旧项目参考图/首尾帧/遮罩等 references lineage 未写入 film.html")
    assets_with_provenance = [
        asset for asset in (raw_ir.get("assets") or [])
        if isinstance(asset, dict) and (
            asset.get("prompt") or asset.get("description") or asset.get("keywords")
            or asset.get("license_note") or asset.get("provenance") or asset.get("rights_status")
            or asset.get("source") not in (None, "", "uploaded", "local")
        )
    ]
    if assets_with_provenance:
        warnings.append(
            f"{len(assets_with_provenance)} 个素材带有来源/版权/提示词/描述元数据；"
            "film.html 保留素材 ID 与路径，但不会保存这些资产目录元数据"
        )

    audio_lanes_by_id = {
        str(lane.get("id")): lane for lane in (raw_ir.get("audio_tracks") or [])
        if isinstance(lane, dict) and lane.get("id")
    }
    audio_lane_usage = {}
    for audio_item in source_audio:
        lane_id = str(audio_item.get("track_id") or "")
        if lane_id:
            audio_lane_usage[lane_id] = audio_lane_usage.get(lane_id, 0) + 1
    for lane_id, lane in audio_lanes_by_id.items():
        if lane.get("hidden"):
            warnings.append(
                f"旧音轨 {lane.get('name') or lane_id} hidden 表示时间线折叠；"
                "来源状态保存在 data-source-track-hidden，原分组折叠交互尚未迁移"
            )
        if audio_lane_usage.get(lane_id, 0) > 1:
            warnings.append(
                f"旧音轨 {lane.get('name') or lane_id} 含多个片段；片段会拆成独立 Film 轨道，"
                "只保留轨道标签，不保留原始分组关系"
            )

    subtitle_style = raw_ir.get("subtitle_style") or {}
    unsupported_subtitle_style = []
    if subtitle_style.get("font"):
        unsupported_subtitle_style.append("font")
    rendered_size = float(subtitle_style.get("size", 1.0) or 1.0) * 54
    if rendered_size < 12 or rendered_size > 160:
        unsupported_subtitle_style.append("size（换算后的字号超出 12–160px，将被限制）")
    for field_name in ("color", "stroke_color"):
        field_value = str(subtitle_style.get(field_name) or ("#FFFFFF" if field_name == "color" else "#000000"))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", field_value):
            unsupported_subtitle_style.append(field_name + "（不是 #RRGGBB 颜色，已回退为默认色）")
    background = str(subtitle_style.get("background") or "")
    if background and not re.fullmatch(r"#[0-9a-fA-F]{6}(?:@[0-9]*\.?[0-9]+)?", background):
        unsupported_subtitle_style.append("background（格式无效，已回退为透明）")
    if unsupported_subtitle_style:
        warnings.append(
            "旧字幕样式字段 " + ", ".join(unsupported_subtitle_style)
            + " 含未映射或尚未验证的字段"
        )
    subtitle_track = raw_ir.get("subtitle_track") or {}
    if subtitle_track.get("style_id"):
        warnings.append("旧 subtitle_track.style_id 样式注册表引用未解析")
    if subtitle_track.get("source") not in (None, "", "tts_word_boundary"):
        warnings.append("旧字幕对齐来源元数据未写入 film.html")
    if subtitle_track.get("mode") not in (None, "", "sentence"):
        warnings.append("旧字幕 mode 元数据未写入 film.html；仅保留 cue 时间与文本")

    visual_tracks = raw_ir.get("visual_tracks") or []
    visual_lanes_by_id = {
        str(lane.get("id")): lane for lane in visual_tracks
        if isinstance(lane, dict) and lane.get("id")
    }
    if any(lane.get("hidden") for lane in visual_tracks if isinstance(lane, dict)):
        warnings.append(
            "旧视觉轨道 hidden 表示时间线折叠而不是画面隐藏；来源状态保存在 data-source-track-hidden，"
            "原分组折叠交互尚未迁移"
        )
    overlay_lane_counts: dict[str, int] = {}
    for overlay in raw_ir.get("visual_overlays") or []:
        if not isinstance(overlay, dict):
            continue
        lane_id = str(overlay.get("track_id") or "overlay-main")
        overlay_lane_counts[lane_id] = overlay_lane_counts.get(lane_id, 0) + 1
    grouped_lanes = [
        lane_id for lane_id, count in overlay_lane_counts.items() if count > 1
    ]
    if grouped_lanes:
        warnings.append(
            "旧视觉轨道包含多片段分组 " + ", ".join(sorted(grouped_lanes))
            + "；FilmDocument 为允许不同片段重叠而拆成独立物理轨道，"
              "来源分组 ID/顺序保存在 data-source-track-*，组联动编辑尚未迁移"
        )

    for scene in source_scenes:
        scene_id = str(scene.get("id") or "unknown")
        if scene.get("selections"):
            warnings.append(f"场景 {scene_id} 的 selections 语义锚点未映射到 film.html")
        if scene.get("beats"):
            warnings.append(f"场景 {scene_id} 的 beats 分层内容未迁移；仅处理 scene.layers")
        scene_metadata = [
            name for name in (
                "semantic_role", "intent", "visual_intent", "selected_candidate_id",
                "origin_recipe_id", "origin_recipe_version",
            ) if scene.get(name)
        ]
        if scene_metadata:
            warnings.append(f"场景 {scene_id} 的创作 lineage 字段未映射：" + ", ".join(scene_metadata))
        if any(scene.get(name) for name in (
            "entrance_state", "exit_state", "continuity_in", "continuity_out", "camera"
        )):
            warnings.append(f"场景 {scene_id} 的 camera/continuity/state 信息未迁移")
        scene_start = _number(scene.get("start", 0), "scene start", 0)
        for idx, layer in enumerate(scene.get("layers") or []):
            kind = str(layer.get("type") or "")
            if kind == "gap":
                continue
            if kind not in {"video", "image"}:
                warnings.append(f"场景 {scene.get('id')} 图层 {idx + 1} 类型 {kind} 尚不能无损迁移")
                continue
            uri = asset_uri(layer.get("asset_id"))
            d = _number(layer.get("duration"), "layer duration", 0.001)
            speed = _number(layer.get("speed", 1), "layer speed", 0.25, 4) if kind == "video" else 1.0
            src_in = _number(layer.get("source_in", 0), "source_in", 0) if kind == "video" else 0
            src_out = _number(layer.get("source_out") if kind == "video" and layer.get("source_out") is not None else src_in + d * speed, "source_out", src_in + 0.001)
            cid = _safe_id(f"main-{scene.get('id','scene')}-{idx + 1}", "clip")
            layer_timeline_start = scene_start + _number(layer.get("start", 0), "layer start", 0)
            attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(layer_timeline_start), "data-asset-id": str(layer.get("asset_id") or ""), "data-source-id": str(layer.get("id") or layer.get("clip_id") or ""), "data-source-scene-id": str(scene.get("id") or ""), "data-source-track-id": str(layer.get("track_id") or "")}
            if selected_variant:
                attrs["data-source-variant-id"] = str(selected_variant.get("id") or "")
                attrs["data-source-variant-version"] = str(selected_variant.get("version", 1))
            if kind == "video" and abs(speed - 1) > 1e-6:
                attrs["speed"] = _fmt(speed)
            unsupported_layer_fields = []
            raw_transform = layer.get("transform") or {}
            base_transform = _film_transform_metadata(
                raw_transform, stage["width"], stage["height"], stage["width"], stage["height"]
            )
            raw_layer_keyframes = layer.get("keyframes") or []
            volume_tracks = [
                track for track in raw_layer_keyframes
                if isinstance(track, dict) and str(track.get("property") or "") == "volume"
            ] if isinstance(raw_layer_keyframes, list) else []
            mask_layer_tracks = [
                track for track in raw_layer_keyframes
                if isinstance(track, dict) and str(track.get("property") or "") == "mask_path"
            ] if isinstance(raw_layer_keyframes, list) else []
            visual_keyframes = [
                track for track in raw_layer_keyframes
                if not isinstance(track, dict) or str(track.get("property") or "") not in {"volume", "mask_path"}
            ] if isinstance(raw_layer_keyframes, list) else raw_layer_keyframes
            channels, unsupported_keyframes = _film_keyframes(visual_keyframes, d)
            unsupported_layer_fields.extend(f"keyframes:{field}" for field in unsupported_keyframes)
            if volume_tracks:
                if kind == "video":
                    volume_envelope, volume_warnings = _film_volume_envelope(volume_tracks, d)
                    if volume_envelope:
                        attrs["volume_envelope"] = json.dumps(
                            volume_envelope, ensure_ascii=False, separators=(",", ":")
                        )
                    unsupported_layer_fields.extend(f"音量包络迁移提示：{warning}" for warning in volume_warnings)
                else:
                    unsupported_layer_fields.append("图片层的 volume 关键帧无对应音轨，未迁移")
            explicit_channel_properties = {
                str(channel.get("property") or "") for channel in channels
                if isinstance(channel, dict)
            }
            channels, motion_warning = _motion_keyframes(layer.get("motion"), d, base_transform, channels)
            if motion_warning:
                unsupported_layer_fields.append(motion_warning)
            recipe_channels, recipe_warnings = _film_motion_recipe_keyframes(layer, d)
            current_channel_properties = {
                str(channel.get("property") or "") for channel in channels
                if isinstance(channel, dict)
            }
            for recipe_channel in recipe_channels:
                prop = str(recipe_channel.get("property") or "")
                if prop in explicit_channel_properties:
                    unsupported_layer_fields.append(
                        f"motion_recipe {layer.get('motion_recipe_id')} 的 {prop} 与显式关键帧冲突；保留显式关键帧"
                    )
                    continue
                # A pinned MotionRecipe takes precedence over the deprecated
                # shorthand motion preset when both write the same channel.
                if prop in current_channel_properties:
                    channels = [
                        channel for channel in channels
                        if channel.get("property") != prop
                    ]
                channels.append(recipe_channel)
                current_channel_properties.add(prop)
            unsupported_layer_fields.extend(
                f"motion recipe: {warning}" for warning in recipe_warnings
            )
            if layer.get("motion_recipe_id"):
                attrs["data-motion-recipe-id"] = str(layer.get("motion_recipe_id"))
                attrs["data-motion-recipe-version"] = str(layer.get("motion_version") or "")
                attrs["data-motion-instance"] = json.dumps(
                    layer.get("motion_instance") or {},
                    ensure_ascii=False, separators=(",", ":")
                )
            camera_channels, camera_warnings = _film_camera_channels(
                raw_ir.get("camera_tracks") or [], layer_timeline_start, d
            )
            unsupported_layer_fields.extend(camera_warnings)
            for camera_channel in camera_channels:
                camera_property = str(camera_channel.get("property") or "")
                if camera_property in current_channel_properties:
                    unsupported_layer_fields.append(
                        f"camera_tracks {camera_property} 与图层显式关键帧冲突；保留图层关键帧"
                    )
                    continue
                channels.append(camera_channel)
                current_channel_properties.add(camera_property)
            fit_mode = str(layer.get("fit_mode") or "cover")
            if fit_mode not in {"cover", "contain", "fill", "none", "scale-down"}:
                unsupported_layer_fields.append(f"fit_mode:{fit_mode}")
                fit_mode = "cover"
            known_motion = layer.get("motion") in {"slow_zoom_in", "slow_zoom_out", "pan_left", "pan_right"}
            if raw_transform or channels or known_motion:
                _apply_film_transform(attrs, base_transform, channels, fit_mode)
            else:
                attrs["style"] = f"left:0px;top:0px;width:{stage['width']}px;height:{stage['height']}px;object-fit:{fit_mode}"
            if layer.get("animation"):
                unsupported_layer_fields.append("animation")
            raw_mask = layer.get("mask")
            mask_tracks = list(mask_layer_tracks)
            if isinstance(raw_mask, dict) and isinstance(raw_mask.get("keyframes"), list):
                mask_tracks.extend(raw_mask.get("keyframes") or [])
            mask_keyframes, mask_keyframe_warnings = _film_mask_keyframes(mask_tracks, d)
            unsupported_layer_fields.extend(f"mask:{warning}" for warning in mask_keyframe_warnings)
            if isinstance(raw_mask, dict) and raw_mask.get("inverted") and mask_keyframes:
                # The Runtime currently accepts only a positive polygon clip-path.
                # Applying these frames would turn an inverted mask into its opposite,
                # which is worse than explicitly leaving the source mask unapplied.
                unsupported_layer_fields.append(
                    "inverted 动态蒙版无法由当前 Runtime 无损表达；未应用正向关键帧，原始值保留于源副本"
                )
                mask_keyframes = []
            mask_clip_path = None
            if raw_mask:
                mask_clip_path, mask_warnings = _film_mask_clip_path(
                    raw_mask, allow_empty_path=bool(mask_keyframes)
                )
                unsupported_layer_fields.extend(f"mask:{warning}" for warning in mask_warnings)
                if raw_mask.get("animated") and not mask_keyframes:
                    unsupported_layer_fields.append("蒙版标记为 animated，但没有可迁移的 mask_path 关键帧")
            channels_affect_geometry = any(
                channel.get("property") in {
                    "x", "y", "scale", "rotation", "camera_x", "camera_y", "camera_scale",
                }
                for channel in channels
            )
            transform_affects_geometry = bool(
                raw_transform
                and (
                    abs(float(base_transform["x"]) - 0.5) > 1e-6
                    or abs(float(base_transform["y"]) - 0.5) > 1e-6
                    or abs(float(base_transform["scale"]) - 1.0) > 1e-6
                    or abs(float(base_transform["rotation"])) > 1e-6
                )
            )
            mask_geometry_conflict = channels_affect_geometry or transform_affects_geometry or known_motion
            if mask_geometry_conflict and mask_keyframes:
                # A polygon expressed in stage coordinates cannot be attached to a
                # transformed local clip without changing its meaning. Do not emit a
                # plausible-looking but semantically wrong animation; the complete mask
                # remains in migration/source-composition.json for a future faithful mapper.
                unsupported_layer_fields.append(
                    "动态蒙版与图层 Transform/位置动效坐标系冲突；未写入近似关键帧，原始值保留于源副本"
                )
                mask_keyframes = []
            if mask_keyframes:
                attrs["data-film-mask-keyframes"] = json.dumps(
                    mask_keyframes, ensure_ascii=False, separators=(",", ":")
                )
                if mask_clip_path is None:
                    first_polygon = mask_keyframes[0]["value"]
                    mask_clip_path = "polygon(" + ", ".join(
                        f"{_fmt(x * 100)}% {_fmt(y * 100)}%" for x, y in first_polygon
                    ) + ")"
                if mask_geometry_conflict:
                    unsupported_layer_fields.append(
                        "动态蒙版画布坐标与图层 Transform/位置动效的组合尚未无损迁移"
                    )
            if mask_clip_path:
                if mask_geometry_conflict:
                    if not mask_keyframes:
                        unsupported_layer_fields.append(
                            "蒙版画布坐标与图层 Transform/位置动效的组合尚未无损迁移"
                        )
                else:
                    attrs["style"] += ";clip-path:" + mask_clip_path
            if layer.get("locked"):
                attrs["data-clip-locked"] = ""
            clip_record = {"id": cid, "tag": "video" if kind == "video" else "img", "attrs": attrs}
            clip_at = _number(attrs.get("at", 0), "clip at", 0)
            clip_end_at = clip_end(clip_record)
            can_share_main_track = not clips or (
                idx == 0 and clip_end_at is not None and all(
                    clip_end(existing) is not None
                    and (
                        clip_end(existing) <= clip_at + 0.001
                        or _number(existing["attrs"].get("at", 0), "existing clip at", 0) >= clip_end_at - 0.001
                    )
                    for existing in clips
                )
            )
            if can_share_main_track:
                clips.append(clip_record)
            else:
                layer_track_id = _safe_id("track-layer-" + cid, "track")
                layer_track = {
                    "id": layer_track_id,
                    "attrs": {
                        "id": layer_track_id,
                        "data-label": f"图层 {scene_id}/{idx + 1}",
                        "data-render-z-index": str(max(0, idx)),
                        "style": f"z-index:{max(0, idx)}",
                    },
                    "clips": [clip_record],
                }
                tracks.append(layer_track)
            if kind == "image" and (
                abs(_number(layer.get("speed", 1), "image speed", 0.001) - 1) > 1e-6
                or _number(layer.get("source_in", 0), "image source_in", 0) > 0
                or layer.get("source_out") is not None
            ):
                unsupported_layer_fields.append("image source timing/speed")
            if unsupported_layer_fields:
                warnings.append(
                    f"片段 {cid} 字段 " + ", ".join(unsupported_layer_fields) + " 尚未无损迁移"
                )
    # Put each overlapping overlay/audio item on its own physical section; OpenFilm tracks may not overlap internally.
    def one_clip_track(track_id, label, clip):
        return {"id": track_id, "attrs": {"id": track_id, "data-label": label}, "clips": [clip]}

    visual_overlay_items = list(raw_ir.get("visual_overlays") or [])
    visual_overlay_items.sort(key=lambda overlay: (
        int(visual_lanes_by_id.get(str(overlay.get("track_id") or ""), {}).get("order", 999)),
        _number(overlay.get("start", 0), "overlay start", 0),
        str(overlay.get("id") or ""),
    ))
    # Each overlay is lifted to its own physical Film track, so CSS z-index on
    # the clip itself cannot order it against another track. Reassign a compact
    # render z-index by (source track order, authored z_index), keeping every
    # overlay inside the overlay band below subtitles/watermarks.
    stack_order = sorted(
        enumerate(visual_overlay_items),
        key=lambda entry: (
            int(visual_lanes_by_id.get(str(entry[1].get("track_id") or ""), {}).get("order", 999)),
            int(_number(entry[1].get("z_index", 0), "overlay z_index", -100000, 100000)),
            str(entry[1].get("id") or ""),
            entry[0],
        ),
    )
    overlay_render_z_by_object = {
        id(overlay): 100001 + rank
        for rank, (_original_index, overlay) in enumerate(stack_order)
    }
    for idx, overlay in enumerate(visual_overlay_items):
        asset_item = by_id.get(str(overlay.get("asset_id") or ""), {})
        asset_file = str(asset_item.get("file") or "")
        suffix = Path(asset_file).suffix.lower()
        inferred_kind = "image" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"} else "video"
        kind = str(overlay.get("type") or overlay.get("kind") or asset_item.get("type") or inferred_kind).lower()
        if kind not in {"video", "image"}:
            warnings.append(f"叠加层 {overlay.get('id')} 类型 {kind} 尚不能无损迁移")
            continue
        uri = asset_uri(overlay.get("asset_id"))
        at, d = _number(overlay.get("start", 0), "overlay start", 0), _number(overlay.get("duration"), "overlay duration", 0.001)
        camera_channels, camera_warnings = _film_camera_channels(raw_ir.get("camera_tracks") or [], at, d)
        warnings.extend(camera_warnings)
        speed = _number(overlay.get("speed", 1), "overlay speed", 0.25, 4)
        src_in = _number(overlay.get("source_in", 0), "overlay source_in", 0)
        src_out = src_in + d * (speed if kind == "video" else 1)
        cid = _safe_id(f"overlay-{overlay.get('id') or idx + 1}", "clip")
        presentation = str(overlay.get("presentation_mode") or "picture_in_picture")
        opacity = _number(overlay.get("opacity", 1), f"overlay {cid} opacity", 0, 1)
        z_index = int(_number(overlay.get("z_index", 0), f"overlay {cid} z_index", -100000, 100000))
        if presentation == "full_frame":
            x, y, w, h = 0.0, 0.0, float(stage["width"]), float(stage["height"])
        else:
            x = _number(overlay.get("x", 0.65), "overlay x", 0, 1) * stage["width"]
            y = _number(overlay.get("y", 0.1), "overlay y", 0, 1) * stage["height"]
            w = _number(overlay.get("width", 0.3), "overlay width", 0.001, 1) * stage["width"]
            h = _number(overlay.get("height", 0.3), "overlay height", 0.001, 1) * stage["height"]
        fit = "contain" if overlay.get("fit_mode") == "contain" else "cover"
        attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(at),
                 "data-asset-id": str(overlay.get("asset_id") or ""), "data-source-id": str(overlay.get("id") or ""), "opacity": _fmt(opacity)}
        if selected_variant:
            attrs["data-source-variant-id"] = str(selected_variant.get("id") or "")
            attrs["data-source-variant-version"] = str(selected_variant.get("version", 1))
        source_lane_id = str(overlay.get("track_id") or "")
        source_lane = visual_lanes_by_id.get(source_lane_id, {})
        if source_lane_id:
            attrs["data-source-track-id"] = source_lane_id
            attrs["data-source-track-order"] = str(int(source_lane.get("order", 0)))
            attrs["data-source-track-name"] = str(source_lane.get("name") or source_lane_id)
        if source_lane.get("hidden"):
            attrs["data-source-track-hidden"] = "true"
        raw_overlay_transform = overlay.get("transform")
        if raw_overlay_transform:
            overlay_transform = _film_transform_metadata(
                raw_overlay_transform, stage["width"], stage["height"], w, h
            )
            overlay_transform["opacity"] = float(overlay_transform["opacity"]) * opacity
            _apply_film_transform(attrs, overlay_transform, camera_channels, fit)
        elif camera_channels:
            overlay_transform = _film_transform_metadata({
                "x": (x + w / 2) / stage["width"],
                "y": (y + h / 2) / stage["height"],
                "scale": 1, "rotation": 0, "opacity": opacity, "anchor": "center",
            }, stage["width"], stage["height"], w, h)
            _apply_film_transform(attrs, overlay_transform, camera_channels, fit)
        else:
            style = f"left:{_fmt(x)}px;top:{_fmt(y)}px;width:{_fmt(w)}px;height:{_fmt(h)}px;opacity:{_fmt(opacity)};object-fit:{fit}"
            attrs["style"] = style
        attrs["style"] += f";z-index:{z_index}"
        if kind == "video" and abs(speed - 1) > 1e-6:
            attrs["speed"] = _fmt(speed)
        source_audio_enabled = bool(overlay.get("source_audio_enabled"))
        source_audio_volume = _number(overlay.get("source_audio_volume", 1), "overlay source audio volume", 0, 2)
        if kind == "video":
            if source_audio_enabled:
                attrs["volume"] = _fmt(source_audio_volume)
            else:
                attrs["muted"] = ""
        elif source_audio_enabled or abs(source_audio_volume - 1) > 1e-6:
            warnings.append(f"叠加层 {cid} 是静态图片，source_audio_enabled/source_audio_volume 无适用音轨")
        tid = _safe_id("track-" + cid, "track")
        source_label = str(source_lane.get("name") or "叠加画面")
        overlay_track = one_clip_track(tid, source_label, {"id": cid, "tag": kind, "attrs": attrs})
        # Track-level stacking decides overlay order across independent CSS contexts.
        # The clip's own z-index remains authored metadata within that track.
        render_z_index = overlay_render_z_by_object[id(overlay)]
        overlay_track["attrs"]["data-render-z-index"] = str(render_z_index)
        overlay_track["attrs"]["style"] = f"z-index:{render_z_index}"
        if source_lane.get("locked"):
            overlay_track["attrs"]["locked"] = ""
        if source_lane.get("hidden"):
            # Source "hidden" folds a timeline lane; FilmDocument "hidden" suppresses rendering.
            # Preserve it as metadata instead of muting the picture.
            overlay_track["attrs"]["data-source-track-hidden"] = "true"
        if source_lane_id:
            overlay_track["attrs"]["data-source-track-id"] = source_lane_id
            overlay_track["attrs"]["data-source-track-order"] = str(int(source_lane.get("order", 0)))
        tracks.append(overlay_track)

    subtitle = subtitle_track
    cues = subtitle.get("cues") or []
    if cues:
        pages["scenes/subtitles.html"] = _subtitle_page(stage["width"], stage["height"], duration, cues, raw_ir.get("subtitle_style"))
        cid = "subtitle-page"
        subtitle_track_film = one_clip_track("track-subtitles", "字幕", {"id": cid, "tag": "iframe",
            "attrs": {"id": cid, "src": f"scenes/subtitles.html#t=0,{_fmt(duration)}", "at": "0",
                      "data-source-track-id": str(subtitle_track.get("id") or ""),
                      "style": f"left:0px;top:0px;width:{stage['width']}px;height:{stage['height']}px"}})
        subtitle_track_film["attrs"]["data-render-z-index"] = "200002"
        subtitle_track_film["attrs"]["style"] = "z-index:200002"
        tracks.append(subtitle_track_film)

    audio_items = list(source_audio)
    audio_items.sort(key=lambda item: (
        int(audio_lanes_by_id.get(str(item.get("track_id") or ""), {}).get("order", 999)),
        _number(item.get("start", 0), "audio start", 0),
        str(item.get("id") or ""),
    ))
    for idx, audio in enumerate(audio_items):
        uri = asset_uri(audio.get("asset_id"))
        start, d = _number(audio.get("start", 0), "audio start", 0), _number(audio.get("duration"), "audio duration", 0.001)
        src_in = _number(audio.get("source_in", 0), "audio source_in", 0)
        speed = _number(audio.get("speed", 1), "audio speed", 0.25, 4)
        src_out = _number(audio.get("source_out") if audio.get("source_out") is not None else src_in + d * speed, "audio source_out", src_in + 0.001)
        cid = _safe_id(f"audio-{audio.get('id') or idx + 1}", "clip")
        lane = audio_lanes_by_id.get(str(audio.get("track_id") or ""), {})
        attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(start),
                 "volume": _fmt(_number(audio.get("volume", 1), "audio volume", 0, 2)), "data-role": str(audio.get("type") or "audio"), "data-asset-id": str(audio.get("asset_id") or ""), "data-source-id": str(audio.get("id") or ""), "data-source-track-id": str(audio.get("track_id") or "")}
        if selected_variant:
            attrs["data-source-variant-id"] = str(selected_variant.get("id") or "")
            attrs["data-source-variant-version"] = str(selected_variant.get("version", 1))
        if abs(speed - 1) > 1e-6:
            attrs["speed"] = _fmt(speed)
        if audio.get("muted") or lane.get("muted"):
            attrs["muted"] = ""
        fade_in = _number(audio.get("fadeIn", 0), "audio fadeIn", 0, 60)
        fade_out = _number(audio.get("fadeOut", 0), "audio fadeOut", 0, 60)
        if fade_in + fade_out <= d + 0.001:
            if fade_in > 0 or fade_out > 0:
                attrs["fade"] = f"{_fmt(fade_in)},{_fmt(fade_out)}"
        elif fade_in > 0 or fade_out > 0:
            warnings.append(f"音频片段 {cid} 的 fadeIn/fadeOut 之和超过片段时长，淡入淡出未迁移")
        if audio.get("ducking"):
            warnings.append(f"音频片段 {cid} 的 ducking 自动压低未迁移")
        if audio.get("keyframes"):
            mapped_volume, volume_warnings = _film_volume_envelope(audio.get("keyframes") or [], d)
            if mapped_volume:
                attrs["volume_envelope"] = json.dumps(
                    mapped_volume, ensure_ascii=False, separators=(",", ":")
                )
            if volume_warnings:
                warnings.append(f"音频片段 {cid} 的 keyframes 未完全映射：" + ", ".join(volume_warnings))
        if audio.get("linked_visual_overlay_id") or audio.get("follows_scene_video") is False:
            warnings.append(f"音频片段 {cid} 的原声关联/跟随视频关系未迁移")
        tid = _safe_id("track-" + cid, "track")
        label = str(lane.get("name") or audio.get("type") or "音频")
        audio_timeline_track = one_clip_track(tid, label, {"id": cid, "tag": "audio", "attrs": attrs})
        if lane.get("locked"):
            audio_timeline_track["attrs"]["locked"] = ""
        if lane.get("hidden"):
            audio_timeline_track["attrs"]["data-source-track-hidden"] = "true"
        tracks.append(audio_timeline_track)

    watermarks = raw_ir.get("watermarks") or []
    for idx, watermark in enumerate(watermarks):
        watermark_id = str(watermark.get("id") or f"wm-{idx + 1}")
        watermark_clip_id = _safe_id(f"watermark-{watermark_id}-{idx + 1}", "clip")
        anchor = str(watermark.get("anchor") or "bottom_right")
        if anchor not in _FILM_ANCHORS:
            warnings.append(f"水印 {watermark_id} 的 anchor {anchor} 不支持，水印未迁移")
            continue
        start = _number(watermark.get("start", 0), f"水印 {watermark_id} start", 0)
        if start >= duration:
            warnings.append(f"水印 {watermark_id} 的 start 超出影片时长，水印未迁移")
            continue
        raw_duration = watermark.get("duration")
        requested_duration = duration - start if raw_duration is None else _number(
            raw_duration, f"水印 {watermark_id} duration", 0.001
        )
        watermark_duration = min(requested_duration, duration - start)
        if requested_duration > watermark_duration + 0.001:
            warnings.append(f"水印 {watermark_id} 的显示区间超出影片时长，已截短到片尾")
        scale = _number(watermark.get("scale", 1), f"水印 {watermark_id} scale", 0.000001)
        if scale > 10:
            warnings.append(f"水印 {watermark_id} 的 scale 超出可稳定显示范围，已限制为 10")
            watermark["scale"] = 10
        kind = str(watermark.get("kind") or "text")
        media_src = None
        if kind in {"image", "logo", "svg"}:
            if not watermark.get("asset_id"):
                warnings.append(f"图片水印 {watermark_id} 缺少 asset_id，水印未迁移")
                continue
            source_uri = asset_uri(watermark.get("asset_id"))
            media_src = (
                "/api/editor/film/media?project_id=" + quote(pid, safe="")
                + "&src=" + quote(unquote(source_uri), safe="")
            )
        elif kind != "text":
            warnings.append(f"水印 {watermark_id} 的类型 {kind} 暂不支持，水印未迁移")
            continue
        page_name = f"scenes/{watermark_clip_id}.html"
        pages[page_name] = _watermark_page(stage["width"], stage["height"], watermark_duration, watermark, media_src)
        watermark_attrs = {
            "id": watermark_clip_id,
            "src": f"{page_name}#t=0,{_fmt(watermark_duration)}",
            "at": _fmt(start),
            "style": f"left:0px;top:0px;width:{stage['width']}px;height:{stage['height']}px;pointer-events:none",
        }
        watermark_track = {
            "id": _safe_id("track-" + watermark_clip_id, "track"),
            "attrs": {"id": _safe_id("track-" + watermark_clip_id, "track"),
                      "data-label": "水印 " + watermark_id,
                      "style": "z-index:100000;pointer-events:none"},
            "clips": [{"id": watermark_clip_id, "tag": "iframe", "attrs": watermark_attrs}],
        }
        tracks.append(watermark_track)
        if kind == "text":
            warnings.append(
                f"文本水印 {watermark_id} 的 scale 采用 24px 基准字号迁移；旧字段未提供绝对字号，需复核视觉尺寸"
            )
    declared_duration = duration
    mapped_end = max(
        [float(clip["attrs"].get("at", 0)) + (clip_duration(clip) or 0.0)
         for track in tracks + ([{"clips": clips}] if clips else []) for clip in track.get("clips", [])]
        or [0.0]
    )
    if declared_duration - mapped_end > 0.05:
        warnings.append(
            f"旧项目总时长为 {declared_duration:.3f}s，但可迁移片段仅到 {mapped_end:.3f}s；"
            "尾部空白/gap 未变成显式片段，film.html 导出时长会缩短"
        )
    for scene in source_scenes:
        transition_spec = scene.get("transition_out") or {}
        transition = str(transition_spec.get("type") or "cut")
        transition_duration = _number(
            transition_spec.get("duration", 0), f"scene {scene.get('id')} transition duration", 0
        )
        if transition not in {"cut", "none", ""} or transition_duration > 0.001:
            warnings.append(
                f"场景 {scene.get('id')} 转场 type={transition}, duration={transition_duration:.3f}s "
                "尚未完整映射；原始设置保留于迁移源副本"
            )
    if clips:
        tracks.append({"id": "track-main",
                       "attrs": {"id": "track-main", "data-label": "主画面",
                                 "data-render-z-index": "0", "style": "z-index:0"},
                       "clips": clips})
    # Keep a stable semantic ordering in the serialized document: overlay sections
    # remain before the base picture (for callers that enumerate tracks), the main
    # picture comes next, extra/locked visual layers follow it, and audio/utility
    # tracks stay after the visual stack. Compositing remains governed by z-index.
    def _migration_track_order(track: dict) -> tuple[int, str]:
        track_id = str(track.get("id") or "")
        if track_id.startswith("track-overlay-"):
            group = 0
        elif track_id == "track-main":
            group = 1
        elif track_id.startswith("track-layer-"):
            group = 2
        elif track_id.startswith("track-audio-"):
            group = 4
        else:
            group = 3
        return group, track_id

    tracks.sort(key=_migration_track_order)
    if not tracks:
        raise FilmError("NO_SUPPORTED_CLIPS", "当前 Composition 没有可迁移的视频、图片、字幕或音频片段", 422)
    warnings = list(dict.fromkeys(warnings))
    # Keep the complete validated source IR next to the migration record. This
    # preserves non-rendered variants/lineage/references for later recovery and audit.
    # It is an archive, not a claim that unsupported semantics were rendered.
    source_snapshot_path = "migration/source-composition.json"
    pages[source_snapshot_path] = json.dumps(
        source_archive_ir, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    source_snapshot_sha256 = hashlib.sha256(
        pages[source_snapshot_path].encode("utf-8")
    ).hexdigest()
    head = f'<head><meta charset="utf-8"><meta name="viewport" content="width={stage["width"]},height={stage["height"]}"><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#000}}section{{position:absolute;inset:0}}</style></head>'
    document = {"stage": stage, "head_html": head, "tracks": tracks}
    _validate_doc(document)
    return {"document": document, "pages": pages, "warnings": warnings, "duration": duration,
            "track_count": len(tracks), "clip_count": sum(len(t["clips"]) for t in tracks),
            "selected_variant_id": str(selected_variant.get("id")) if selected_variant else None}


def initialize_project_film(project_id: str, document: dict, pages: dict[str, str], source_version: int, warnings: list[str], selected_variant_id: str | None = None) -> dict:
    pid = validate_project_id(project_id)
    text = serialize_film(document)
    parse_film(text)
    with project_lock(pid):
        path = _film_path(pid)
        if path.exists():
            raise FilmError("FILM_ALREADY_INITIALIZED", "film.html 已存在，不会覆盖已有影片文档", 409)
        from video_director.editor import composition_version
        if composition_version(pid) != int(source_version):
            raise FilmError("COMPOSITION_CHANGED", "初始化期间旧 Composition 已变化，请重新载入迁移预览", 409)
        root = _project_root(pid)
        for relative, page in pages.items():
            dest = (root / relative).resolve()
            try:
                dest.relative_to(root)
            except ValueError:
                raise FilmError("INVALID_PAGE_PATH", f"生成页面路径非法：{relative}", 422) from None
            _write_atomic(dest, page)
        source_snapshot = pages.get("migration/source-composition.json")
        migration = {"format": "video-director-film-migration/1", "source_composition_version": int(source_version),
                     "selected_variant_id": selected_variant_id,
                     "warnings": list(warnings), "document_revision": revision_of(text)}
        if isinstance(source_snapshot, str):
            migration["source_snapshot_path"] = "migration/source-composition.json"
            migration["source_snapshot_sha256"] = hashlib.sha256(source_snapshot.encode("utf-8")).hexdigest()
        _write_atomic(root / MIGRATION_NAME, json.dumps(migration, ensure_ascii=False, indent=2))
        _write_atomic(path, text)
        _history_write(pid, {"past": [], "future": []})
    return {"ok": True, "project_id": pid, "rev": revision_of(text), "document": parse_film(text), "warnings": warnings, "migration": migration}


def film_payload(project_id: str, state: dict) -> dict:
    if not state.get("exists"):
        return {"ok": True, "exists": False, "project_id": project_id, "rev": state.get("rev")}
    doc = copy.deepcopy(state["doc"])
    tracks = []
    for track in doc["tracks"]:
        clips = []
        for clip in track["clips"]:
            attrs, src = clip["attrs"], str(clip["attrs"].get("src") or "")
            path = src.split("#", 1)[0]
            media_url = "/api/editor/film/media?project_id=" + quote(project_id, safe="") + "&src=" + quote(path, safe="")
            if "#" in src:
                media_url += "#" + src.split("#", 1)[1]
            clips.append({"id": clip["id"], "tag": clip["tag"], "attrs": attrs, "at": float(attrs.get("at", 0) or 0),
                          "duration": clip_duration(clip), "end": clip_end(clip), "src_in": _source_range(src)[0],
                          "src_out": _source_range(src)[1], "media_url": media_url})
        tracks.append({"id": track["id"], "attrs": track["attrs"], "clips": clips})
    metadata_path = _project_root(project_id) / MIGRATION_NAME
    migration = None
    if metadata_path.is_file():
        try:
            migration = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            migration = {"warnings": ["film-migration.json 无法读取"]}
    head_html = doc.get("head_html", "")
    style_blocks = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", head_html, re.I | re.S)
    render_css = "\n".join(style_blocks)
    if re.search(r"@import\b|@keyframes\b|\banimation(?:-name)?\s*:|\btransition\s*:|url\s*\(|https?://|file:|javascript:|expression\s*\(|-moz-binding", render_css, re.I):
        raise FilmError("UNSAFE_FILM_STYLE", "film.html head CSS 含外部资源、动画或可执行 CSS；请先移除后再载入。", 422)
    return {"ok": True, "exists": True, "project_id": project_id, "rev": state["rev"], "stage": doc["stage"],
            "head_html": head_html, "render_css": render_css, "tracks": tracks,
            "duration": max([c.get("end") or 0 for t in tracks for c in t["clips"]] or [0]), "migration": migration}


def plan_film_command(llm, state: dict, message: str, selected_clip_id: str = "",
                      summary_out: dict | None = None, selected_element: dict | None = None) -> list[dict]:
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise FilmError("INVALID_MESSAGE", "指令不能为空且不能超过 4000 个字符")
    if not state.get("exists") or not state.get("doc"):
        raise FilmError("FILM_NOT_INITIALIZED", "影片文档尚未初始化", 409)
    doc = state["doc"]
    track_order = {track["id"]: index for index, track in enumerate(doc["tracks"])}
    timeline_clips = [(track, clip) for track in doc["tracks"] for clip in track["clips"]]
    visual_clips = sorted(
        [(track, clip) for track, clip in timeline_clips if clip["tag"] != "audio"],
        key=lambda item: (_number(item[1]["attrs"].get("at", 0), "at", 0),
                          track_order[item[0]["id"]], item[1]["id"]),
    )
    visual_index = {clip["id"]: index + 1 for index, (_track, clip) in enumerate(visual_clips)}
    clips = []
    for track in doc["tracks"]:
        label = str((track.get("attrs") or {}).get("data-label") or track["id"])
        ordered = sorted(track["clips"], key=lambda clip: _number(clip["attrs"].get("at", 0), "at", 0))
        for clip_index, clip in enumerate(ordered, 1):
            attrs = clip["attrs"]
            source_in, source_out = _source_range(str(attrs.get("src") or ""))
            clips.append({
                "id": clip["id"], "tag": clip["tag"], "track_id": track["id"],
                "track_label": label, "track_index": track_order[track["id"]] + 1,
                "clip_index": clip_index, "visual_index": visual_index.get(clip["id"]),
                "at": _number(attrs.get("at", 0), "at", 0), "end": clip_end(clip),
                "duration": clip_duration(clip), "src": attrs.get("src", ""),
                "source_in": source_in, "source_out": source_out,
                "speed": attrs.get("speed", "1"), "volume": attrs.get("volume", "1"),
                "muted": "muted" in attrs, "volume_envelope": attrs.get("volume_envelope", ""), "fade": attrs.get("fade", "0,0"), "style": attrs.get("style", ""),
                "title": attrs.get("title", ""),
            })
    prompt = (
        "你是视频时间线编辑规划器。只输出 JSON：{"
        '"operations":[...],"summary":"..."}。'
        "可用 set 操作: {op:'set',clip:准确片段ID,field:'at'|'speed'|'volume'|'volume_envelope'|'fade'|'muted'|'style'|'class'|'title',value:...}; "
        "可用 move 操作: {op:'move',clip:准确ID,track_id:现有轨道ID,at:非负秒}; "
        "可用 slip 操作: {op:'slip',clip:准确视频/音频片段ID,delta_source:素材源时间偏移秒}。滑移只改变 #t 入出点，不改变时间线位置和长度；仅在用户明确要求保持时间线位置但更换素材源区间时使用。 " "可用 slide 操作: {op:'slide',clip:中间视频/音频片段ID,delta:时间线秒}。滑移式修剪会移动中间片段，同时修剪左右相邻片段以保持三段总时长与外侧边界不变；当前仅支持同源连续、同类型、等速且无 fade 的三段。只在用户明确要求 slide trim 时使用。 "
        "可用 split 操作: {op:'split',clip:准确ID,at:片段内部时间秒}; " "可用 join 操作: {op:'join',clip:左片段ID,other_clip:右片段ID}。只在用户明确要求愈合切口/合并同源切开片段时使用；需要同轨相邻、源区间连续、属性一致。" "可用 roll 操作: {op:'roll',clip:左片段ID,other_clip:右片段ID,delta:秒}。只在用户明确要求滚动修剪切口时使用；仅允许同源连续切口；正数延长左段并缩短右段，负数相反，总时长不变。"
        "可用 trim 操作: {op:'trim',clip:准确ID,edge:'start',at:新的时间线入点} 或 {op:'trim',clip:准确ID,edge:'end',end:新的时间线出点}; "
        "可用 remove 操作: {op:'remove',clip:明确要删除的片段ID}。删除可撤销，只在用户明确要求删除时使用。"
        "可用 ripple_delete 操作: {op:'ripple_delete',clip:准确片段ID,scope:'all'}。仅当用户明确要求波纹删除或删除并收拢时间线时使用；会影响全部轨道，并切分跨越删除区间的片段。"
        "可用 element_set 操作: {op:'element_set',clip:当前选中 HTML 镜头 ID,selector:当前选中元素的精确 selector,text?:字符串,style?:{color:'色值',backgroundColor:'色值',fontSize:'32px',left:'20px',top:'20px',width:'300px',height:'100px',rotate:'10deg',...}}。只允许修改系统提供的当前选中元素；不能猜测其他 selector；仅当用户明确要求修改该画面元素时使用。style 仅允许 color/backgroundColor/fontSize/fontWeight/textAlign/lineHeight/letterSpacing/left/top/width/height/position/opacity/transform/borderRadius/rotate，CSS 值会经过服务端安全校验。不要通过 set 直接修改 overrides。"
        "visual_index 是按时间排序的视觉片段序号且不包含音频；track_index 是影片文档轨道顺序；clip_index 是轨道内按时间排序的片段序号。"
        "“第 N 段/第 N 个镜头”优先按 visual_index 定位；轨道内描述按 track_index/clip_index 定位；“这个/选中片段”使用当前选择 ID。"
        "不要编造 ID；修剪只能缩短当前源区间并保留正时长。目标不明确时返回空 operations，并在 summary 提出简短澄清问题。"
        "volume_envelope 是剪辑相对时间的音量倍率关键帧 JSON 数组，形如 [{t:0,v:1},{t:2,v:0.2}]；t 为秒，v 在 0–1；只有用户明确要求自动化音量时才修改。fade 是以秒表示的淡入/淡出对，形如 '0.3,0.5'；只在用户明确要求淡化时修改，合计不能超过片段时长。不允许素材插入、付费动作及源码修改；新增素材必须由 Studio 素材池选择。"
        f"\n当前选择片段 ID：{selected_clip_id or '无'}"
        f"\n当前选中画布元素（只可操作这一项）：{json.dumps(selected_element, ensure_ascii=False, separators=(',', ':')) if selected_element else '无；不得使用 element_set'}"
        f"\n当前片段：{json.dumps(clips, ensure_ascii=False, separators=(',', ':'))}"
        f"\n用户指令：{message}"
    )
    try:
        output = str(llm.generate(prompt) or "").strip()
    except Exception as exc:
        raise FilmError("LLM_PLAN_FAILED", f"AI 规划失败：{exc}", 502) from exc
    fence = chr(96) * 3
    if output.startswith(fence):
        output = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", output, flags=re.I)
        output = re.sub(r"\s*" + re.escape(fence) + r"$", "", output)
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise FilmError("LLM_INVALID_PLAN", f"AI 没有返回有效 JSON（{exc.msg}）", 502) from exc
    if not isinstance(result, dict) or not isinstance(result.get("operations", []), list):
        raise FilmError("LLM_INVALID_PLAN", "AI 返回的计划缺少 operations 数组", 502)
    assistant_summary = str(result.get("summary") or "").strip()
    if summary_out is not None:
        summary_out["summary"] = assistant_summary[:500]
    operations = result.get("operations") or []
    if len(operations) > 40:
        raise FilmError("LLM_INVALID_PLAN", "AI 计划超过 40 项操作上限", 422)
    valid_ids = {c["id"] for t in doc["tracks"] for c in t["clips"]}
    valid_tracks = {t["id"] for t in doc["tracks"]}
    for op in operations:
        if not isinstance(op, dict) or op.get("op") not in {"set", "move", "split", "trim", "remove", "ripple_delete", "slip", "join", "roll", "slide", "element_set"}:
            raise FilmError("LLM_INVALID_PLAN", "AI 计划包含不允许的操作", 422)
        if op["op"] == "element_set":
            target = next((clip for track in doc["tracks"] for clip in track["clips"]
                           if clip["id"] == op.get("clip")), None)
            if (not selected_element or not isinstance(selected_element, dict)
                    or op.get("clip") != selected_element.get("clip_id")
                    or op.get("clip") != selected_clip_id
                    or op.get("selector") != selected_element.get("selector")
                    or target is None or target.get("tag") != "iframe"):
                raise FilmError("LLM_INVALID_ELEMENT_TARGET",
                                "AI 只能修改当前选中的 HTML 镜头内、且 selector 精确匹配的元素", 422)
            # Reuse the same server-side style/text validator used by edit execution.
            if "text" in op and selected_element.get("textEditable") is False:
                raise FilmError("ELEMENT_TEXT_NOT_EDITABLE",
                                "当前选中元素包含子元素；为避免破坏内部结构，不允许整体替换其文本", 422)
            _normalize_element_override(op.get("selector"), op.get("text"),
                                        "text" in op, op.get("style"))
        if op["op"] == "join" and (op.get("clip") not in valid_ids or op.get("other_clip") not in valid_ids):
            raise FilmError("LLM_INVALID_PLAN", "AI 愈合切口操作必须指定两个已存在的片段 ID", 422)
        if op["op"] == "roll":
            if (op.get("clip") not in valid_ids or op.get("other_clip") not in valid_ids
                    or isinstance(op.get("delta"), bool) or not isinstance(op.get("delta"), (int, float))):
                raise FilmError("LLM_INVALID_PLAN", "AI 滚动修剪操作必须指定左右片段 ID 和数字 delta", 422)
        if op["op"] == "slide":
            if (op.get("clip") not in valid_ids
                    or isinstance(op.get("delta"), bool) or not isinstance(op.get("delta"), (int, float))):
                raise FilmError("LLM_INVALID_PLAN", "AI 滑移式修剪操作必须指定中间片段 ID 和数字 delta", 422)
            target_clip = next(clip for track in doc["tracks"] for clip in track["clips"] if clip["id"] == op["clip"])
            if target_clip["tag"] not in {"video", "audio"}:
                raise FilmError("LLM_INVALID_PLAN", "AI 滑移式修剪只支持视频/音频片段", 422)
        if op["op"] == "slip":
            if op.get("clip") not in valid_ids or not isinstance(op.get("delta_source"), (int, float)):
                raise FilmError("LLM_INVALID_PLAN", "AI 滑移操作必须包含有效片段 ID 和数字 delta_source", 422)
            target_clip = next(clip for track in doc["tracks"] for clip in track["clips"] if clip["id"] == op["clip"])
            if target_clip["tag"] not in {"video", "audio"}:
                raise FilmError("LLM_INVALID_PLAN", "AI 只能对视频/音频片段执行源区间滑移", 422)
        if op["op"] == "trim" and (op.get("edge") not in {"start", "end"} or
                                   (op.get("edge") == "start" and "at" not in op) or
                                   (op.get("edge") == "end" and "end" not in op)):
            raise FilmError("LLM_INVALID_PLAN", "AI 修剪操作缺少有效 edge/时间值", 422)
        if op["op"] == "set" and op.get("field") not in AI_SETTABLE_FIELDS:
            raise FilmError("LLM_INVALID_PLAN", f"AI 使用了不允许的片段字段：{op.get('field')}", 422)
        if op.get("clip") not in valid_ids:
            raise FilmError("LLM_INVALID_PLAN", f"AI 使用了未知片段 ID：{op.get('clip')}", 422)
        if op["op"] == "move" and op.get("track_id") not in valid_tracks:
            raise FilmError("LLM_INVALID_PLAN", f"AI 使用了未知轨道 ID：{op.get('track_id')}", 422)
    return operations
