"""Deterministic MP4 export from the authoritative film.html document.

This renderer never writes or reads CompositionIR. The browser runtime provides
per-frame visual composition; FFmpeg encodes those frames and mixes source audio
from the same stable clip IDs / source ranges.
"""
from __future__ import annotations

import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from video_director import config
from video_director import film_document as fd

FPS_DEFAULT = 30
MAX_RENDER_SECONDS = 600
MAX_RENDER_FRAMES = 18_000
MAX_STAGE_EDGE = 3840
_MEDIA_CHUNK = 64 * 1024


class FilmRenderError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(message)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _style_blocks(head_html: str) -> str:
    """Keep only local, passive CSS from the film document head.

    External fetches, imports and CSS URL functions are denied for offline,
    repeatable exports. Runtime media elements keep their stable clip IDs.
    """
    styles = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", head_html or "", re.I | re.S)
    result = "\n".join(styles)
    if re.search(r"@import\b|url\s*\(|https?://|file:|javascript:|expression\s*\(|-moz-binding", result, re.I):
        raise FilmRenderError(
            "UNSAFE_FILM_STYLE",
            "film.html 的 head CSS 含外部资源或可执行 CSS；为避免联网/非确定性渲染，已拒绝导出。",
            422,
        )
    return result


def _host_html() -> bytes:
    return b"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}#runtime{position:absolute;inset:0;width:100%;height:100%;border:0;display:block}</style>
</head><body><iframe id="runtime" title="film runtime" src="/static/film-runtime.html"></iframe>
<script>
window.__runtimeReady=false;window.__runtimeRev='';window.__lastFrameReady=null;window.__frameError=null;
window.addEventListener('message',function(event){
  var frame=document.getElementById('runtime');
  if(!frame||event.source!==frame.contentWindow||event.origin!==window.location.origin)return;
  var m=event.data||{};
  if(m.type==='film-runtime:ready'){window.__runtimeReady=true;if(m.rev)window.__runtimeRev=m.rev;}
  if(m.type==='film-runtime:loaded'){window.__runtimeRev=m.rev||'';}
  if(m.type==='film-runtime:frame-ready'){window.__lastFrameReady=m.frame;window.__frameError=m.error||null;}
});
</script></body></html>"""


class _RenderHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, project_id: str):
        self.project_id = fd.validate_project_id(project_id)
        super().__init__(server_address, _RenderHTTPHandler)


class _RenderHTTPHandler(BaseHTTPRequestHandler):
    server: _RenderHTTPServer

    def log_message(self, _format, *_args):
        return

    def _send_error(self, status: int, message: str):
        body = message.encode("utf-8", errors="replace")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/host":
            body = _host_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path in {"/static/film-runtime.html", "/static/js/film-runtime.js"}:
            rel = parsed.path.removeprefix("/static/")
            target = (Path(__file__).resolve().parent.parent / "webui" / "static" / rel).resolve()
            static_root = (Path(__file__).resolve().parent.parent / "webui" / "static").resolve()
            try:
                target.relative_to(static_root)
            except ValueError:
                self._send_error(403, "static path denied")
                return
            if not target.is_file():
                self._send_error(404, "runtime resource missing")
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path != "/media":
            self._send_error(404, "not found")
            return

        qs = parse_qs(parsed.query)
        project_id = (qs.get("project_id", [""])[0] or "").strip()
        if project_id != self.server.project_id:
            self._send_error(403, "project mismatch")
            return
        src = qs.get("src", [""])[0]
        try:
            media_path = fd.media_path_for_src(project_id, src)
        except fd.FilmError as exc:
            self._send_error(exc.status, str(exc))
            return
        except Exception:
            self._send_error(400, "invalid media source")
            return
        try:
            total = media_path.stat().st_size
            start, end, status = 0, max(0, total - 1), 200
            range_header = self.headers.get("Range", "")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match or (not match.group(1) and not match.group(2)):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                if not match.group(1):
                    count = int(match.group(2))
                    start = max(0, total - count)
                else:
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else total - 1
                if start >= total or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                end, status = min(end, total - 1), 206

            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(str(media_path))[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.end_headers()
            with media_path.open("rb") as stream:
                stream.seek(start)
                remaining = max(0, end - start + 1)
                while remaining:
                    chunk = stream.read(min(_MEDIA_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _runtime_payload(project_id: str, state: dict, base_url: str) -> dict:
    payload = fd.film_payload(project_id, state)
    payload["head_html"] = state["doc"].get("head_html", "")
    for track in payload.get("tracks", []):
        for clip in track.get("clips", []):
            source = str((clip.get("attrs") or {}).get("src") or "")
            raw_path, marker, fragment = source.partition("#")
            clip["media_url"] = (
                base_url + "/media?project_id=" + quote(project_id, safe="")
                + "&src=" + quote(raw_path, safe="")
                + (("#" + fragment) if marker else "")
            )
    payload["render_css"] = _style_blocks(payload.get("head_html", ""))
    return payload


def _atempo_filters(speed: float) -> list[str]:
    """Split playback speed into FFmpeg atempo values accepted by broad FFmpeg versions."""
    values = []
    remaining = float(speed)
    while remaining > 2.0 + 1e-9:
        values.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        values.append("atempo=0.5")
        remaining /= 0.5
    values.append("atempo=" + _fmt(remaining))
    return values if abs(speed - 1.0) > 1e-9 else []


def _source_file(project_id: str, clip: dict) -> tuple[Path, float, float, float, float]:
    attrs = clip["attrs"]
    src = str(attrs.get("src") or "")
    path = fd.media_path_for_src(project_id, src)
    src_in, src_out = fd._source_range(src)
    if src_out is None:
        raise FilmRenderError(
            "SOURCE_RANGE_REQUIRED",
            f"片段 {clip['id']} 缺少 #t=in,out 源区间；导出需要明确裁剪区间。",
            422,
        )
    speed = float(attrs.get("speed", 1) or 1) if clip["tag"] in {"video", "audio"} else 1.0
    start = float(attrs.get("at", 0) or 0)
    duration = (src_out - src_in) / speed
    if not all(math.isfinite(v) for v in (src_in, src_out, speed, start, duration)) or duration <= 0 or speed <= 0:
        raise FilmRenderError("INVALID_CLIP_TIMING", f"片段 {clip['id']} 的时间参数无效。", 422)
    return path, src_in, src_out, speed, start


def _validate_source_ranges(project_id: str, doc: dict) -> None:
    from video_director import prober

    for track in doc.get("tracks", []):
        for clip in track.get("clips", []):
            if clip["tag"] not in {"video", "audio"}:
                continue
            path, src_in, src_out, _speed, _start = _source_file(project_id, clip)
            probe = prober.probe_media(str(path))
            if clip["tag"] == "video" and not probe.has_video_stream:
                raise FilmRenderError("VIDEO_STREAM_MISSING", f"视频片段 {clip['id']} 指向的素材没有视频流：{path.name}", 422)
            if clip["tag"] == "audio" and not probe.has_audio_stream:
                raise FilmRenderError("AUDIO_STREAM_MISSING", f"音频片段 {clip['id']} 指向的素材没有音频流：{path.name}", 422)
            if probe.duration is not None and src_out > float(probe.duration) + 0.05:
                raise FilmRenderError(
                    "SOURCE_RANGE_OUT_OF_BOUNDS",
                    f"片段 {clip['id']} 的源出点 {src_out:.3f}s 超出素材时长 {float(probe.duration):.3f}s。",
                    422,
                )


def _audio_specs(project_id: str, doc: dict, duration: float) -> list[dict]:
    from video_director import prober

    specs = []
    for track in doc.get("tracks", []):
        tattrs = track.get("attrs") or {}
        if "hidden" in tattrs or "muted" in tattrs:
            continue
        for clip in track.get("clips", []):
            if clip["tag"] not in {"video", "audio"}:
                continue
            attrs = clip["attrs"]
            if "muted" in attrs:
                continue
            volume = float(attrs.get("volume", 1) or 0)
            if volume <= 0:
                continue
            path, src_in, src_out, speed, start = _source_file(project_id, clip)
            probe = prober.probe_media(str(path))
            if not probe.has_audio_stream:
                if clip["tag"] == "audio":
                    raise FilmRenderError(
                        "AUDIO_STREAM_MISSING",
                        f"音频片段 {clip['id']} 指向的文件没有音频流：{path.name}",
                        422,
                    )
                continue
            if probe.duration is not None and src_out > float(probe.duration) + 0.05:
                raise FilmRenderError(
                    "SOURCE_RANGE_OUT_OF_BOUNDS",
                    f"片段 {clip['id']} 的源出点 {src_out:.3f}s 超出素材时长 {float(probe.duration):.3f}s。",
                    422,
                )
            if start >= duration:
                continue
            specs.append({
                "id": clip["id"], "path": str(path), "src_in": src_in,
                "src_out": src_out, "speed": speed, "start": start,
                "duration": min((src_out-src_in)/speed, duration-start),
                "volume": min(2.0, max(0.0, volume)),
            })
    return specs


def _run_ffmpeg(command: list[str], timeout: int):
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "FFmpeg exited unsuccessfully").strip()
        raise FilmRenderError("FFMPEG_FAILED", detail[-2400:], 500)


def _mux_audio(ffmpeg: str, silent_video: Path, output: Path, specs: list[dict], duration: float,
               work_dir: Path):
    if not specs:
        shutil.copy2(silent_video, output)
        return
    command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(silent_video)]
    for spec in specs:
        command.extend(["-i", spec["path"]])
    filters = []
    labels = []
    for idx, spec in enumerate(specs, start=1):
        label = f"aud{idx}"
        speed_filters = _atempo_filters(spec["speed"])
        chain = [
            f"atrim=start={_fmt(spec['src_in'])}:end={_fmt(spec['src_out'])}",
            "asetpts=PTS-STARTPTS",
            *speed_filters,
            f"volume={_fmt(spec['volume'])}",
        ]
        delay_ms = max(0, int(round(spec["start"] * 1000)))
        if delay_ms:
            chain.append(f"adelay={delay_ms}:all=1")
        chain.extend([f"apad=whole_dur={_fmt(duration)}", f"atrim=duration={_fmt(duration)}"])
        filters.append(f"[{idx}:a:0]{','.join(chain)}[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        filters.append(f"{labels[0]}atrim=duration={_fmt(duration)}[aout]")
    else:
        filters.append(
            "".join(labels) +
            f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0,"
            f"alimiter=limit=0.97,atrim=duration={_fmt(duration)}[aout]"
        )
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", _fmt(duration), "-movflags", "+faststart", str(output),
    ])
    _run_ffmpeg(command, timeout=max(180, int(duration * 10 + 120)))


def render_project_film(project_id: str, expected_revision: str, fps: int = FPS_DEFAULT) -> dict:
    """Render a pinned film.html revision to MP4; never falls back to CompositionIR."""
    pid = fd.validate_project_id(project_id)
    state = fd.read_project_film(pid)
    if not state.get("exists"):
        raise FilmRenderError("FILM_NOT_INITIALIZED", "当前项目尚未初始化 film.html。", 409)
    revision = state["rev"]
    if expected_revision and revision != expected_revision:
        raise FilmRenderError(
            "REVISION_CONFLICT",
            f"影片文档已变化：提交版本 {expected_revision}，当前版本 {revision}；请重新载入后导出。",
            409,
        )
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise FilmRenderError("INVALID_FPS", "导出帧率必须是 1–60 的整数。", 422)

    doc = state["doc"]
    width, height = int(doc["stage"]["width"]), int(doc["stage"]["height"])
    if not 16 <= width <= MAX_STAGE_EDGE or not 16 <= height <= MAX_STAGE_EDGE:
        raise FilmRenderError(
            "INVALID_STAGE_SIZE",
            f"画布尺寸 {width}×{height} 不在可导出范围内（16–{MAX_STAGE_EDGE}px）。",
            422,
        )
    payload = fd.film_payload(pid, state)
    clips = [c for track in payload["tracks"] for c in track["clips"]]
    if not clips:
        raise FilmRenderError("EMPTY_FILM", "影片文档没有任何片段，无法导出。", 422)
    if any(c.get("duration") is None for c in clips):
        ids = [c["id"] for c in clips if c.get("duration") is None]
        raise FilmRenderError("SOURCE_RANGE_REQUIRED", "以下片段缺少明确源区间：" + "、".join(ids[:12]), 422)
    project_duration = (doc.get("project_metadata") or {}).get("project", {}).get("duration", 0)
    try:
        project_duration = float(project_duration or 0)
    except (TypeError, ValueError):
        raise FilmRenderError("INVALID_PROJECT_DURATION", "film.html 中的项目时长不是有效数字。", 422) from None
    if not math.isfinite(project_duration) or project_duration < 0:
        raise FilmRenderError("INVALID_PROJECT_DURATION", "film.html 中的项目时长不是有限非负数字。", 422)
    duration = max(max(float(c.get("end") or 0) for c in clips), project_duration)
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_RENDER_SECONDS:
        raise FilmRenderError("DURATION_LIMIT", f"影片时长必须大于 0 且不超过 {MAX_RENDER_SECONDS} 秒。", 422)
    frame_count = int(math.ceil(duration * fps - 1e-8))
    if frame_count <= 0 or frame_count > MAX_RENDER_FRAMES:
        raise FilmRenderError("FRAME_LIMIT", f"需要渲染 {frame_count} 帧，超出单次导出上限 {MAX_RENDER_FRAMES} 帧。", 422)

    style_css = _style_blocks(doc.get("head_html", ""))
    _validate_source_ranges(pid, doc)
    # Arbitrary external iframe HTML does not promise time seeking. The generated
    # subtitle page is frame-driven and participates in the per-frame acknowledgement.
    for track in doc["tracks"]:
        for clip in track["clips"]:
            if clip["tag"] == "iframe":
                src = str(clip["attrs"].get("src") or "").split("#", 1)[0].replace("\\", "/")
                if not src.endswith("scenes/subtitles.html"):
                    raise FilmRenderError(
                        "UNSUPPORTED_DETERMINISTIC_IFRAME",
                        f"iframe 片段 {clip['id']} 不是系统生成的逐帧字幕页。请把动态内容转成视频，或添加 film-time 帧驱动协议后再导出。",
                        422,
                    )

    fps_dir = config.project_dir(pid) / "render"
    fps_dir.mkdir(parents=True, exist_ok=True)
    output = fps_dir / f"film-final-{revision}.mp4"
    temp_root = fps_dir / f".film-render-{revision}-{os.getpid()}-{time.time_ns()}.work"
    temp_root.mkdir(parents=True, exist_ok=False)
    frame_dir = temp_root / "frames"
    frame_dir.mkdir()
    silent_video = temp_root / "silent.mp4"
    output_temp = temp_root / "final.mp4"
    media_server = None
    browser = None

    try:
        media_server = _RenderHTTPServer(("127.0.0.1", 0), pid)
        server_thread = threading.Thread(target=media_server.serve_forever, name="film-render-media", daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{media_server.server_address[1]}"
        payload = _runtime_payload(pid, state, base_url)
        payload["render_css"] = style_css
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            page.goto(base_url + "/host", wait_until="load", timeout=30000)
            page.wait_for_function("window.__runtimeReady === true", timeout=30000)
            page.evaluate(
                "(arg) => document.getElementById('runtime').contentWindow.postMessage("
                "{type:'film-runtime:load',data:arg.data,rev:arg.rev},location.origin)",
                {"data": payload, "rev": revision},
            )
            page.wait_for_function("arg => window.__runtimeRev === arg", arg=revision, timeout=30000)
            for frame_number in range(frame_count):
                timestamp = frame_number / fps
                page.evaluate(
                    "(arg) => {window.__lastFrameReady=null;window.__frameError=null;"
                    "document.getElementById('runtime').contentWindow.postMessage("
                    "{type:'film-runtime:render-frame',time:arg.time,frame:arg.frame},location.origin);}",
                    {"time": timestamp, "frame": frame_number},
                )
                page.wait_for_function(
                    "(frame) => window.__lastFrameReady === frame",
                    arg=frame_number, timeout=20000,
                )
                frame_error = page.evaluate("window.__frameError")
                if frame_error:
                    raise FilmRenderError("FRAME_RENDER_FAILED", f"第 {frame_number} 帧失败：{frame_error}", 500)
                page.locator("#runtime").screenshot(
                    path=str(frame_dir / f"{frame_number + 1:08d}.png"),
                    animations="disabled", timeout=20000,
                )
            browser.close()
            browser = None

        from video_director import renderer
        ffmpeg = renderer._ffmpeg()
        _run_ffmpeg([
            ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps),
            "-start_number", "1", "-i", str(frame_dir / "%08d.png"),
            "-frames:v", str(frame_count), "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-an", "-r", str(fps), "-c:v", config.DEFAULT_VIDEO_CODEC,
            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(silent_video),
        ], timeout=max(240, int(duration * 15 + 180))
        )
        audio_specs = _audio_specs(pid, doc, duration)
        _mux_audio(ffmpeg, silent_video, output_temp, audio_specs, duration, temp_root)
        if not output_temp.is_file() or output_temp.stat().st_size <= 0:
            raise FilmRenderError("OUTPUT_MISSING", "渲染结束后没有生成 MP4。", 500)
        os.replace(output_temp, output)
        after = fd.read_project_film(pid)
        return {
            "ok": True, "project_id": pid, "mp4_path": str(output),
            "rendered_revision": revision, "current_revision": after.get("rev"),
            "current": after.get("rev") == revision, "fps": fps,
            "width": width, "height": height, "duration": duration,
            "frame_count": frame_count, "audio_tracks_mixed": len(audio_specs),
            "warnings": [] if after.get("rev") == revision else [
                "导出期间影片文档已变化；成片对应 rendered_revision，不对应当前最新 revision。"
            ],
        }
    except FilmRenderError:
        raise
    except Exception as exc:
        raise FilmRenderError("FILM_RENDER_FAILED", f"影片渲染失败：{exc}", 500) from exc
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if media_server is not None:
            try:
                media_server.shutdown()
                media_server.server_close()
            except Exception:
                pass
        try:
            shutil.rmtree(temp_root, ignore_errors=True)
        except Exception:
            pass
