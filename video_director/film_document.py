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
SETTABLE_FIELDS = {"at", "src", "speed", "volume", "style", "class", "overrides", "muted", "title", "alt", "lang", "dir", "size"}
AI_SETTABLE_FIELDS = {"at", "speed", "volume", "style", "class", "muted", "title"}
TRACK_FLAGS = {"locked", "hidden", "muted"}
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


def _ensure_unlocked(track: dict):
    if "locked" in (track.get("attrs") or {}):
        raise FilmError("TRACK_LOCKED", f"轨道 {track['id']} 已锁定，不能编辑", 409)


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


def apply_ops(doc: dict, ops: list[dict]) -> tuple[dict, list[dict]]:
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPERATIONS:
        raise FilmError("INVALID_OPERATIONS", f"ops 必须是 1–{MAX_OPERATIONS} 项的数组")
    out = copy.deepcopy(doc)
    normalized = []
    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise FilmError("INVALID_OPERATION", f"第 {index + 1} 项操作不是对象")
        kind = op.get("op")
        if kind == "set":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track)
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
            else:
                attrs[field] = value
            normalized.append({"op": "set", "clip": clip["id"], "field": field, "value": value})
        elif kind == "move":
            old_track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(old_track)
            track_id = str(op.get("track_id") or old_track["id"])
            new_track = _find_track(out, track_id)
            _ensure_unlocked(new_track)
            at = _number(op.get("at"), "at", 0)
            old_track["clips"].remove(clip)
            clip["attrs"]["at"] = _fmt(at)
            new_track["clips"].append(clip)
            normalized.append({"op": "move", "clip": clip["id"], "track_id": track_id, "at": _fmt(at)})
        elif kind == "remove":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track)
            track["clips"].remove(clip)
            normalized.append({"op": "remove", "clip": clip["id"]})
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
            _ensure_unlocked(track)
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
            clip["attrs"]["src"] = _with_source_range(attrs["src"], src_in, mid)
            right = copy.deepcopy(clip)
            right["id"], right["attrs"]["id"] = right_id, right_id
            right["attrs"]["src"] = _with_source_range(attrs["src"], mid, src_out)
            right["attrs"]["at"] = _fmt(at)
            track["clips"].insert(track["clips"].index(clip) + 1, right)
            normalized.append({"op": "split", "clip": original_id, "at": _fmt(at), "new_id": right_id})
        elif kind == "trim":
            track, clip = _find_clip(out, str(op.get("clip") or ""))
            _ensure_unlocked(track)
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
                if clip["tag"] in {"img", "iframe"}:
                    new_src_in, new_src_out = src_in, src_out - delta
                else:
                    new_src_in, new_src_out = src_in + delta * speed, src_out
                if new_src_out is None or new_src_in >= new_src_out - 0.001:
                    raise FilmError("TRIM_EMPTY_CLIP", "修剪后片段时长不能为零")
                attrs["src"] = _with_source_range(attrs["src"], new_src_in, new_src_out)
                attrs["at"] = _fmt(new_start)
                normalized.append({"op": "trim", "clip": clip["id"], "edge": "start", "at": _fmt(new_start)})
            elif edge == "end":
                new_end = _number(op.get("end"), "trim end", start + 0.001, old_end - 0.001)
                new_src_out = src_in + (new_end - start) * speed
                if new_src_out <= src_in + 0.001:
                    raise FilmError("TRIM_EMPTY_CLIP", "修剪后片段时长不能为零")
                attrs["src"] = _with_source_range(attrs["src"], src_in, min(src_out, new_src_out))
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
                raise FilmError("INVALID_TRACK_FLAG", "轨道属性只允许 locked/hidden/muted 布尔值")
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


def edit_project_film(project_id: str, base: str | None, ops: list[dict]) -> dict:
    pid = validate_project_id(project_id)
    with project_lock(pid):
        current = read_project_film(pid)
        if not current["exists"]:
            raise FilmError("FILM_NOT_INITIALIZED", "当前项目还没有 film.html，请先初始化影片文档", 409)
        doc, normalized = apply_ops(current["doc"], ops)
        updated = serialize_film(doc)
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
    font_size = int(max(12, min(160, _number(opts.get("font_size", opts.get("fontSize", 54)), "subtitle font size", 12, 160))))
    bottom = int(max(0, min(height - 10, height * 0.1)))
    color = str(opts.get("color") or "#ffffff")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#ffffff"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width={width},height={height}"><style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}}#stage{{position:relative;width:{width}px;height:{height}px;pointer-events:none}}
#caption{{position:absolute;left:5%;right:5%;bottom:{bottom}px;text-align:center;font-family:Arial,"Microsoft YaHei",sans-serif;font-size:{font_size}px;font-weight:700;color:{color};text-shadow:0 2px 4px #000,2px 0 2px #000,-2px 0 2px #000;white-space:pre-wrap;line-height:1.25}}
</style></head><body><div id="stage"><div id="caption"></div></div><script>
const cues={data};const caption=document.getElementById('caption');
window.film={{width:{width},height:{height},duration:{_fmt(duration)},frame(t){{const active=cues.filter(c=>Number(c.start)<=t&&t<Number(c.end));caption.textContent=active.map(c=>String(c.text||'')).join('\\n');caption.style.display=active.length?'block':'none';}}}};
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


def build_film_from_composition(project_id: str, ir) -> dict:
    """Convert supported video/image/audio/subtitle content; report non-lossless fields."""
    pid = validate_project_id(project_id)
    raw_ir = json.loads(ir.model_dump_json())
    root = _project_root(pid)
    stage = {"width": int(raw_ir.get("project", {}).get("width", 1080)),
             "height": int(raw_ir.get("project", {}).get("height", 1920))}
    duration = _number(raw_ir.get("project", {}).get("duration", 1), "project duration", 0.001)
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
    for scene in raw_ir.get("scenes") or []:
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
            attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(scene_start + _number(layer.get("start", 0), "layer start", 0)), "data-asset-id": str(layer.get("asset_id") or "")}
            if kind == "video" and abs(speed - 1) > 1e-6:
                attrs["speed"] = _fmt(speed)
            if layer.get("transform"):
                warnings.append(f"片段 {cid} 的 transform 尚未映射到标准 CSS box")
            if layer.get("keyframes"):
                warnings.append(f"片段 {cid} 的关键帧映射尚未完成")
            clips.append({"id": cid, "tag": "video" if kind == "video" else "img", "attrs": attrs})

    # Put each overlapping overlay/audio item on its own physical section; OpenFilm tracks may not overlap internally.
    def one_clip_track(track_id, label, clip):
        return {"id": track_id, "attrs": {"id": track_id, "data-label": label}, "clips": [clip]}

    for idx, overlay in enumerate(raw_ir.get("visual_overlays") or []):
        kind = str(overlay.get("type") or overlay.get("kind") or "video")
        if kind not in {"video", "image"}:
            warnings.append(f"叠加层 {overlay.get('id')} 类型 {kind} 尚不能无损迁移")
            continue
        uri = asset_uri(overlay.get("asset_id"))
        at, d = _number(overlay.get("start", 0), "overlay start", 0), _number(overlay.get("duration"), "overlay duration", 0.001)
        speed = _number(overlay.get("speed", 1), "overlay speed", 0.25, 4)
        src_in = _number(overlay.get("source_in", 0), "overlay source_in", 0)
        src_out = src_in + d * (speed if kind == "video" else 1)
        cid = _safe_id(f"overlay-{overlay.get('id') or idx + 1}", "clip")
        x = _number(overlay.get("x", 0), "overlay x", 0, 1) * stage["width"]
        y = _number(overlay.get("y", 0), "overlay y", 0, 1) * stage["height"]
        w = _number(overlay.get("width", 1), "overlay width", 0.001, 1) * stage["width"]
        h = _number(overlay.get("height", 1), "overlay height", 0.001, 1) * stage["height"]
        fit = "contain" if overlay.get("fit_mode") == "contain" else "cover"
        style = f"left:{_fmt(x)}px;top:{_fmt(y)}px;width:{_fmt(w)}px;height:{_fmt(h)}px;opacity:{_fmt(_number(overlay.get('opacity',1),'opacity',0,1))};object-fit:{fit}"
        attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(at), "style": style, "data-asset-id": str(overlay.get("asset_id") or "")}
        if kind == "video" and abs(speed - 1) > 1e-6:
            attrs["speed"] = _fmt(speed)
        tid = _safe_id("track-" + cid, "track")
        tracks.append(one_clip_track(tid, "叠加画面", {"id": cid, "tag": kind, "attrs": attrs}))

    subtitle = raw_ir.get("subtitle_track") or {}
    cues = subtitle.get("cues") or []
    if cues:
        pages["scenes/subtitles.html"] = _subtitle_page(stage["width"], stage["height"], duration, cues, raw_ir.get("subtitle_style"))
        cid = "subtitle-page"
        tracks.append(one_clip_track("track-subtitles", "字幕", {"id": cid, "tag": "iframe",
            "attrs": {"id": cid, "src": f"scenes/subtitles.html#t=0,{_fmt(duration)}", "at": "0",
                      "style": f"left:0px;top:0px;width:{stage['width']}px;height:{stage['height']}px"}}))

    for idx, audio in enumerate(raw_ir.get("audio") or []):
        uri = asset_uri(audio.get("asset_id"))
        start, d = _number(audio.get("start", 0), "audio start", 0), _number(audio.get("duration"), "audio duration", 0.001)
        src_in = _number(audio.get("source_in", 0), "audio source_in", 0)
        speed = _number(audio.get("speed", 1), "audio speed", 0.25, 4)
        src_out = _number(audio.get("source_out") if audio.get("source_out") is not None else src_in + d * speed, "audio source_out", src_in + 0.001)
        cid = _safe_id(f"audio-{audio.get('id') or idx + 1}", "clip")
        attrs = {"id": cid, "src": f"{uri}#t={_fmt(src_in)},{_fmt(src_out)}", "at": _fmt(start),
                 "volume": _fmt(_number(audio.get("volume", 1), "audio volume", 0, 2)), "data-role": str(audio.get("type") or "audio"), "data-asset-id": str(audio.get("asset_id") or "")}
        if abs(speed - 1) > 1e-6:
            attrs["speed"] = _fmt(speed)
        if audio.get("muted"):
            attrs["muted"] = ""
        tid = _safe_id("track-" + cid, "track")
        tracks.append(one_clip_track(tid, str(audio.get("type") or "音频"), {"id": cid, "tag": "audio", "attrs": attrs}))

    watermarks = raw_ir.get("watermarks") or []
    if watermarks:
        warnings.append("水印尚未完整迁移；初始化后请检查旧项目的水印")
    for scene in raw_ir.get("scenes") or []:
        transition = (scene.get("transition_out") or {}).get("type", "cut")
        if transition not in {"cut", "none", ""}:
            warnings.append(f"场景 {scene.get('id')} 转场 {transition} 尚未完整映射")
    if clips:
        tracks.append({"id": "track-main", "attrs": {"id": "track-main", "data-label": "主画面"}, "clips": clips})
    if not tracks:
        raise FilmError("NO_SUPPORTED_CLIPS", "当前 Composition 没有可迁移的视频、图片、字幕或音频片段", 422)
    warnings = list(dict.fromkeys(warnings))
    head = f'<head><meta charset="utf-8"><meta name="viewport" content="width={stage["width"]},height={stage["height"]}"><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#000}}section{{position:absolute;inset:0}}</style></head>'
    document = {"stage": stage, "head_html": head, "tracks": tracks}
    _validate_doc(document)
    return {"document": document, "pages": pages, "warnings": warnings, "duration": duration,
            "track_count": len(tracks), "clip_count": sum(len(t["clips"]) for t in tracks)}


def initialize_project_film(project_id: str, document: dict, pages: dict[str, str], source_version: int, warnings: list[str]) -> dict:
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
        migration = {"format": "video-director-film-migration/1", "source_composition_version": int(source_version),
                     "warnings": list(warnings), "document_revision": revision_of(text)}
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


def plan_film_command(llm, state: dict, message: str, selected_clip_id: str = "", summary_out: dict | None = None) -> list[dict]:
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
                "muted": "muted" in attrs, "style": attrs.get("style", ""),
                "title": attrs.get("title", ""),
            })
    prompt = (
        "你是视频时间线编辑规划器。只输出 JSON：{"
        '"operations":[...],"summary":"..."}。'
        "可用 set 操作: {op:'set',clip:准确片段ID,field:'at'|'speed'|'volume'|'muted'|'style'|'class'|'title',value:...}; "
        "可用 move 操作: {op:'move',clip:准确ID,track_id:现有轨道ID,at:非负秒}; "
        "可用 split 操作: {op:'split',clip:准确ID,at:片段内部时间秒}; "
        "可用 trim 操作: {op:'trim',clip:准确ID,edge:'start',at:新的时间线入点} 或 {op:'trim',clip:准确ID,edge:'end',end:新的时间线出点}; "
        "可用 remove 操作: {op:'remove',clip:明确要删除的片段ID}。删除可撤销，只在用户明确要求删除时使用。"
        "visual_index 是按时间排序的视觉片段序号且不包含音频；track_index 是影片文档轨道顺序；clip_index 是轨道内按时间排序的片段序号。"
        "“第 N 段/第 N 个镜头”优先按 visual_index 定位；轨道内描述按 track_index/clip_index 定位；“这个/选中片段”使用当前选择 ID。"
        "不要编造 ID；修剪只能缩短当前源区间并保留正时长。目标不明确时返回空 operations，并在 summary 提出简短澄清问题。"
        "不允许素材插入、付费动作及源码修改；新增素材必须由 Studio 素材池选择。"
        f"\n当前选择片段 ID：{selected_clip_id or '无'}"
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
        if not isinstance(op, dict) or op.get("op") not in {"set", "move", "split", "trim", "remove"}:
            raise FilmError("LLM_INVALID_PLAN", "AI 计划包含不允许的操作", 422)
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
