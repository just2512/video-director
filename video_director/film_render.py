"""Deterministic MP4 export from the authoritative film.html document.

This renderer never writes or reads CompositionIR. The browser runtime provides
per-frame visual composition; FFmpeg encodes those frames and mixes source audio
from the same stable clip IDs / source ranges.
"""
from __future__ import annotations

import errno
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
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
from PIL import Image, ImageChops, ImageStat

from video_director import config
from video_director import film_document as fd

def _fsync_file(path: Path) -> None:
    """Flush file contents before a journal can make them authoritative."""
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform exposes directory fsync."""
    if os.name == "nt":
        # Python/Windows does not expose a portable directory fsync operation.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(Path(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


FPS_DEFAULT = 30
MAX_RENDER_SECONDS = 600
MAX_RENDER_FRAMES = 18_000
MAX_STAGE_EDGE = 3840
_MEDIA_CHUNK = 64 * 1024
_RENDER_WORKDIR_RE = re.compile(r"^\.film-render-([0-9a-f]{64})-(\d+)-(\d+)\.work$")
_QC_TEMP_RE = re.compile(r"^\.film-final-([0-9a-f]{64})\.json\.(\d+)\.(\d+)\.tmp$")


def _render_process_is_alive(pid: int) -> bool:
    """Fail closed when process liveness cannot be established."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() not in {87, 1168}
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _write_publish_journal(temp_root: Path, payload: dict) -> Path:
    journal = temp_root / "publish-transaction.json"
    temporary = temp_root / f".publish-transaction.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, journal)
        _fsync_directory(temp_root)
    finally:
        temporary.unlink(missing_ok=True)
    return journal


def _restore_render_backup(backup: Path, destination: Path, temporary: Path) -> None:
    """Restore through an atomic sibling replace; safe to retry after a crash."""
    if not backup.is_file() or backup.is_symlink():
        raise RuntimeError(f"render publication backup is missing: {backup.name}")
    shutil.copy2(backup, temporary)
    _fsync_file(temporary)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def _recover_render_publication(project_root: Path, workdir: Path, revision: str, pid: int) -> None:
    journal = workdir / "publish-transaction.json"
    if journal.is_symlink() or not journal.is_file():
        raise RuntimeError("render publication journal is missing or unsafe")
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("revision") != revision
        or not isinstance(payload.get("had_output"), bool)
        or not isinstance(payload.get("had_report"), bool)
    ):
        raise RuntimeError("render publication journal is invalid")
    phase = payload.get("phase")
    output = project_root / "render" / f"film-final-{revision}.mp4"
    report = project_root / "qc" / f"film-final-{revision}.json"
    for parent in (output.parent, report.parent):
        if parent.is_symlink() or not parent.is_dir():
            raise RuntimeError(f"render artifact directory is missing or unsafe: {parent.name}")

    if phase == "prepared":
        if payload["had_output"]:
            _restore_render_backup(
                workdir / "previous-output.mp4", output, workdir / "restore-output.tmp"
            )
        else:
            output.unlink(missing_ok=True)
        if payload["had_report"]:
            _restore_render_backup(
                workdir / "previous-qc.json", report, workdir / "restore-qc.tmp"
            )
        else:
            report.unlink(missing_ok=True)
    elif phase != "committed":
        raise RuntimeError("render publication journal has an unknown phase")

    qc_temp_name = payload.get("qc_temp_name")
    if isinstance(qc_temp_name, str) and Path(qc_temp_name).name == qc_temp_name:
        match = _QC_TEMP_RE.fullmatch(qc_temp_name)
        if match and match.group(1) == revision and int(match.group(2)) == pid:
            qc_temp = project_root / "qc" / qc_temp_name
            if qc_temp.is_symlink():
                raise RuntimeError("render QC temporary path is a symlink")
            qc_temp.unlink(missing_ok=True)


def cleanup_orphaned_render_artifacts(storage_root: Path | None = None) -> dict:
    """Recover interrupted MP4/QC publication and remove dead render workdirs.

    Run once during server startup, before accepting new work. Live owner PIDs
    are skipped. Invalid transactions remain on disk for manual inspection.
    """
    root = Path(storage_root) if storage_root is not None else config.composer_storage_root()
    summary = {"workdirs_removed": 0, "transactions_recovered": 0, "qc_temps_removed": 0, "errors": []}
    if root.is_symlink() or not root.is_dir():
        return summary

    for project_root in sorted(root.iterdir(), key=lambda item: item.name):
        if project_root.is_symlink() or not project_root.is_dir():
            continue
        try:
            fd.validate_project_id(project_root.name)
        except Exception:
            continue

        try:
            with fd.project_lock(project_root.name):
                render_dir = project_root / "render"
                if render_dir.is_dir() and not render_dir.is_symlink():
                    for workdir in sorted(render_dir.iterdir(), key=lambda item: item.name):
                        match = _RENDER_WORKDIR_RE.fullmatch(workdir.name)
                        if not match or workdir.is_symlink() or not workdir.is_dir():
                            continue
                        revision, raw_pid, _created_ns = match.groups()
                        pid = int(raw_pid)
                        if _render_process_is_alive(pid):
                            continue
                        try:
                            journal = workdir / "publish-transaction.json"
                            if journal.exists() or journal.is_symlink():
                                _recover_render_publication(project_root, workdir, revision, pid)
                                state = json.loads(journal.read_text(encoding="utf-8"))
                                if state.get("phase") == "prepared":
                                    summary["transactions_recovered"] += 1
                            shutil.rmtree(workdir)
                            summary["workdirs_removed"] += 1
                        except Exception as exc:
                            summary["errors"].append(
                                f"{project_root.name}/{workdir.name}: {type(exc).__name__}: {str(exc)[:240]}"
                            )

                qc_dir = project_root / "qc"
                if qc_dir.is_dir() and not qc_dir.is_symlink():
                    for temporary in sorted(qc_dir.iterdir(), key=lambda item: item.name):
                        match = _QC_TEMP_RE.fullmatch(temporary.name)
                        if not match or temporary.is_symlink() or not temporary.is_file():
                            continue
                        if _render_process_is_alive(int(match.group(2))):
                            continue
                        try:
                            temporary.unlink()
                            summary["qc_temps_removed"] += 1
                        except OSError as exc:
                            summary["errors"].append(
                                f"{project_root.name}/{temporary.name}: {type(exc).__name__}: {str(exc)[:240]}"
                            )

        except Exception as exc:
            summary["errors"].append(
                f"{project_root.name}: {type(exc).__name__}: {str(exc)[:240]}"
            )
    return summary


class FilmRenderError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(message)


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _install_offline_network_policy(page, base_url: str) -> list[str]:
    """Block and report every request escaping the project-scoped render server."""
    base = urlparse(base_url)
    blocked: list[str] = []

    def guard(route):
        requested = urlparse(route.request.url)
        if requested.scheme in {"data", "blob", "about"}:
            route.continue_()
            return
        if (requested.scheme == base.scheme and requested.hostname == base.hostname
                and requested.port == base.port):
            route.continue_()
            return
        blocked.append(route.request.url[:1000])
        route.abort()

    page.route("**/*", guard)
    return blocked


def _raise_for_external_scene_resources(blocked: list[str]) -> None:
    if blocked:
        examples = "\n".join("- " + url for url in blocked[:8])
        extra = "" if len(blocked) <= 8 else f"\n另有 {len(blocked) - 8} 个外部请求。"
        raise FilmRenderError(
            "EXTERNAL_SCENE_RESOURCE",
            "离线逐帧渲染拒绝访问工程以外的网络资源；请将字体、图片、脚本和样式放入工程并使用相对路径：\n"
            + examples + extra,
            422,
        )


def _install_scene_resource_failure_monitor(page, base_url: str) -> list[str]:
    """Record missing project-local media (including CSS background assets and fonts)."""
    base = urlparse(base_url)
    failures: list[str] = []
    seen: set[str] = set()

    def record(url: str, detail: str) -> None:
        if url in seen:
            return
        seen.add(url)
        failures.append((detail + ": " + url)[:1200])

    def is_local_asset_url(value: str, resource_type: str = "") -> bool:
        item = urlparse(value)
        if not (
            item.scheme == base.scheme
            and item.hostname == base.hostname
            and item.port == base.port
        ):
            return False
        if item.path == "/favicon.ico":
            return False
        if item.path == "/media":
            return True
        return resource_type in {
            "stylesheet", "font", "image", "media", "script", "fetch", "xhr",
        } or Path(item.path).suffix.lower() in {
            ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".webp", ".gif",
            ".svg", ".avif", ".bmp", ".woff", ".woff2", ".ttf", ".otf",
            ".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg", ".json",
        }

    def on_response(response) -> None:
        try:
            resource_type = response.request.resource_type
        except Exception:
            resource_type = ""
        if is_local_asset_url(response.url, resource_type) and int(response.status) >= 400:
            record(response.url, "项目资源 HTTP " + str(response.status))

    def on_request_failed(request) -> None:
        try:
            resource_type = request.resource_type
        except Exception:
            resource_type = ""
        if is_local_asset_url(request.url, resource_type):
            record(request.url, "项目资源请求失败")

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    return failures


def _raise_for_scene_resource_failures(failures: list[str]) -> None:
    if failures:
        examples = "\n".join("- " + item for item in failures[:8])
        extra = "" if len(failures) <= 8 else f"\n另有 {len(failures) - 8} 个项目资源请求失败。"
        raise FilmRenderError(
            "SCENE_RESOURCE_FAILED",
            "HTML 场景引用了缺失或无法加载的项目资源；为防止空白画面被误认为成功，已拒绝渲染：\n"
            + examples + extra,
            422,
        )


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
        # Serve frame-driven HTML scenes through the same local asset boundary as
        # Studio preview. Inject the deterministic frame(t) bridge before authored
        # scripts and resolve scene-relative resources to this project-scoped route.
        if media_path.suffix.lower() in {".html", ".htm"}:
            from video_director.film_scene_runtime import inject_scene_runtime
            try:
                source = media_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self._send_error(422, "HTML 镜头必须使用 UTF-8 编码。")
                return
            body = inject_scene_runtime(source, project_id, src, "/media").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass
            return
        if media_path.suffix.lower() == ".css":
            from video_director.film_scene_runtime import inject_scene_css
            try:
                source = media_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self._send_error(422, "CSS 镜头资源必须使用 UTF-8 编码。")
                return
            body = inject_scene_css(source, project_id, src, "/media").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass
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


def _source_video_fps(project_id: str, raw_path: str) -> float:
    """Return a verified source cadence; never silently substitute the composition FPS."""
    try:
        from video_director import prober
        probe = prober.probe_media(str(fd.media_path_for_src(project_id, raw_path)))
        fps = float(probe.fps) if probe.fps is not None else 0.0
    except Exception as exc:
        raise FilmRenderError(
            "SOURCE_FPS_UNAVAILABLE",
            f"无法确认视频素材 {raw_path!r} 的源帧率，不能保证精确 seek/逐帧导出："
            f"{type(exc).__name__}: {str(exc)[:240]}",
            422,
        ) from exc
    if not math.isfinite(fps) or fps <= 0:
        raise FilmRenderError(
            "SOURCE_FPS_UNAVAILABLE",
            f"视频素材 {raw_path!r} 未返回有效源帧率，不能安全地确认呈现帧时间。",
            422,
        )
    return fps


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
            if clip.get("tag") == "video":
                # Exact presented-frame verification requires the media's own cadence.
                clip["source_fps"] = _source_video_fps(project_id, raw_path)
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


def _volume_preset_value(x: float, preset: str) -> float:
    x = min(1.0, max(0.0, float(x)))
    if preset == "ease_in":
        return x * x
    if preset == "ease_out":
        return 1.0 - (1.0 - x) * (1.0 - x)
    if preset == "ease_in_out":
        return 2.0 * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 2) / 2.0
    return x


def _volume_easing_expression(ratio: str, easing: Any) -> str:
    """Build an FFmpeg expression equivalent to the shared easing sampler."""
    if easing == "linear" or easing is None:
        return f"({ratio})"
    if isinstance(easing, str):
        x = f"({ratio})"
        if easing == "ease_in":
            return f"(({x})*({x}))"
        if easing == "ease_out":
            return f"(1-(1-({x}))*(1-({x})))"
        if easing == "ease_in_out":
            return f"if(lt(({x}),0.5),2*({x})*({x}),1-((-2*({x})+2)*(-2*({x})+2))/2)"
        raise FilmRenderError("INVALID_VOLUME_ENVELOPE", f"未知音量缓动：{easing}", 422)
    if isinstance(easing, dict) and easing.get("type") == "window":
        preset = easing.get("preset")
        start, end = float(easing["start"]), float(easing["end"])
        curve_start, curve_end = _volume_preset_value(start, preset), _volume_preset_value(end, preset)
        curve_span = curve_end - curve_start
        if curve_span <= 1e-12:
            raise FilmRenderError("INVALID_VOLUME_ENVELOPE", "音量缓动窗口的值域无效", 422)
        x = f"({_fmt(start)}+({_fmt(end-start)})*({ratio}))"
        if preset == "ease_in":
            curve = f"(({x})*({x}))"
        elif preset == "ease_out":
            curve = f"(1-(1-({x}))*(1-({x})))"
        elif preset == "ease_in_out":
            curve = f"if(lt(({x}),0.5),2*({x})*({x}),1-((-2*({x})+2)*(-2*({x})+2))/2)"
        else:
            curve = f"({x})"
        return f"((({curve})-{_fmt(curve_start)})/{_fmt(curve_span)})"
    raise FilmRenderError("INVALID_VOLUME_ENVELOPE", "音量缓动结构无效", 422)


def _volume_envelope_filter(base_volume: float, points: list[dict]) -> str:
    """Build FFmpeg gain expressions matching Film Runtime easing."""
    base = min(2.0, max(0.0, float(base_volume)))
    if not points:
        return "volume=" + _fmt(base)
    expression = _fmt(points[-1]["v"])
    for index in range(len(points) - 2, -1, -1):
        left, right = points[index], points[index + 1]
        start, end = float(left["t"]), float(right["t"])
        delta = end - start
        if delta <= 0:
            raise FilmRenderError("INVALID_VOLUME_ENVELOPE", "音量包络时间点必须严格递增。", 422)
        ratio = f"((t-{_fmt(start)})/{_fmt(delta)})"
        eased = _volume_easing_expression(ratio, right.get("easing", "linear"))
        span = float(right["v"]) - float(left["v"])
        segment = f"({_fmt(float(left['v']))}+({_fmt(span)})*({eased}))"
        expression = f"if(lt(t,{_fmt(end)}),{segment},{expression})"
    first = points[0]
    expression = f"if(lt(t,{_fmt(float(first['t']))}),{_fmt(float(first['v']))},{expression})"
    return f"volume='{_fmt(base)}*({expression})':eval=frame"


def _audio_fade_filters(fade_in: float, fade_out: float, duration: float) -> list[str]:
    """Create timeline-relative FFmpeg fades for a clip's post-speed audio."""
    if duration <= 0:
        return []
    fi = min(max(0.0, float(fade_in)), duration)
    fo = min(max(0.0, float(fade_out)), duration)
    filters = []
    if fi > 0:
        filters.append(f"afade=t=in:st=0:d={_fmt(fi)}")
    if fo > 0:
        filters.append(f"afade=t=out:st={_fmt(max(0.0, duration - fo))}:d={_fmt(fo)}")
    return filters


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
                "volume_envelope": fd._parse_volume_envelope(attrs.get("volume_envelope")),
                "fade_in": fd._parse_fade(attrs.get("fade"))[0],
                "fade_out": fd._parse_fade(attrs.get("fade"))[1],
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
            *_audio_fade_filters(spec.get("fade_in", 0), spec.get("fade_out", 0), spec["duration"]),
            _volume_envelope_filter(spec["volume"], spec.get("volume_envelope") or []),
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


def _frame_diff_stats(reference_png: bytes, candidate_png: bytes, tolerance: int = 32) -> tuple[int, int, int, int]:
    """Return (pixels over channel tolerance, total pixels, width, height)."""
    with Image.open(BytesIO(reference_png)) as reference_image:
        reference = reference_image.convert("RGB")
    with Image.open(BytesIO(candidate_png)) as candidate_image:
        candidate = candidate_image.convert("RGB")
    if reference.size != candidate.size:
        width = max(reference.width, candidate.width)
        height = max(reference.height, candidate.height)
        return width * height, width * height, width, height
    red, green, blue = ImageChops.difference(reference, candidate).split()
    max_channel_diff = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = max_channel_diff.point(lambda value: 255 if value > tolerance else 0)
    histogram = mask.histogram()
    changed = sum(histogram[1:])
    return changed, reference.width * reference.height, reference.width, reference.height


def _assert_frame_deterministic(reference_path: Path, candidate_png: bytes, frame_number: int) -> None:
    """Fail a render when repeated same-time rendering materially changes pixels."""
    changed, total, width, height = _frame_diff_stats(reference_path.read_bytes(), candidate_png)
    allowed = max(16, int(total * 0.0005))
    if changed > allowed:
        raise FilmRenderError(
            "NON_DETERMINISTIC_FRAME",
            f"第 {frame_number} 帧重复绘制不一致：{changed}/{total} 像素超过通道差 32，"
            f"允许 {allowed}；画布 {width}×{height}。请检查时钟、随机数、媒体 seek 或动画状态。",
            422,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audio_signal_qc(ffmpeg: str, path: Path, duration: float) -> dict:
    """Measure the encoded first audio stream without changing the export."""
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
             "-map", "0:a:0", "-vn", "-af", "volumedetect", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=max(60, int(duration * 10 + 20)), check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "UNKNOWN", "mean_volume_db": None, "max_volume_db": None,
                "error": str(exc)[:500]}

    output = (completed.stderr or "") + "\n" + (completed.stdout or "")
    mean_match = re.search(r"mean_volume:\s*(-inf|[-+]?\d+(?:\.\d+)?)\s*dB", output, re.I)
    max_match = re.search(r"max_volume:\s*(-inf|[-+]?\d+(?:\.\d+)?)\s*dB", output, re.I)

    def db(match):
        if not match or match.group(1).lower() == "-inf":
            return None
        try:
            value = float(match.group(1))
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    mean_db, max_db = db(mean_match), db(max_match)
    if completed.returncode != 0 or (not mean_match and not max_match):
        return {"status": "UNKNOWN", "mean_volume_db": mean_db, "max_volume_db": max_db,
                "error": (output.strip()[-800:] or "FFmpeg volumedetect 未返回音量统计")}
    if mean_db is None and max_db is None:
        status = "SILENT"
    elif max_db is not None and max_db > -80 and (mean_db is None or mean_db > -60):
        status = "AUDIBLE"
    else:
        status = "NEAR_SILENT"
    return {"status": status, "mean_volume_db": mean_db, "max_volume_db": max_db}


def _visual_frame_qc(path: Path, frame_number: int, fps: int) -> dict:
    with Image.open(path) as source:
        image = source.convert("RGB")
    stats = ImageStat.Stat(image)
    return {
        "frame": frame_number,
        "timestamp_seconds": round(frame_number / fps, 6),
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mean_rgb": [round(value, 3) for value in stats.mean],
        "stddev_rgb": [round(value, 3) for value in stats.stddev],
    }


def _audio_signal_qc_finding(audio_specs, audio_qc):
    """Return a reportable audio-signal check without blocking intentional silence."""
    if not audio_specs:
        return None
    signal_status = str((audio_qc or {}).get("status") or "UNKNOWN").upper()
    if signal_status == "AUDIBLE":
        return {"name": "audio_signal", "status": "PASS"}
    detail = (
        "时间线包含音频，但音频信号检测为 "
        + signal_status
        + "；成片仍可发布，请人工试听并检查增益。"
    )
    return {"name": "audio_signal", "status": "WARNING", "detail": detail}


def _qc_report_status(checks):
    """Keep a technically publishable render distinct from unresolved QC warnings."""
    statuses = {str(check.get("status") or "").upper() for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    return "PASS"


def _write_qc_report(project_id: str, report: dict) -> Path:
    qc_dir = config.project_dir(project_id) / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    report_path = qc_dir / f"film-final-{report['source_revision']}.json"
    temporary = report_path.with_name(report_path.name + f".{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, report_path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return report_path


def _publish_render_artifacts(
    project_id: str,
    revision: str,
    output_temp: Path,
    output: Path,
    report: dict,
    temp_root: Path,
) -> tuple[Path, dict]:
    """Publish MP4/QC as a revision transaction with crash-recoverable rollback."""
    project_root = config.project_dir(project_id)
    qc_dir = project_root / "qc"
    qc_dir.mkdir(parents=True, exist_ok=True)
    report_path = qc_dir / f"film-final-{revision}.json"
    qc_temp = qc_dir / f".{report_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    output_backup = temp_root / "previous-output.mp4"
    report_backup = temp_root / "previous-qc.json"
    journal = temp_root / "publish-transaction.json"
    try:
        qc_temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        with fd.project_lock(project_id):
            after = fd.read_project_film(project_id)
            if after.get("rev") != revision:
                raise FilmRenderError(
                    "REVISION_CONFLICT",
                    "导出期间影片文档已变化；旧 revision 的 MP4 和 QC 报告均未发布。请重新载入最新版本后再导出。",
                    409,
                )

            had_output = output.is_file()
            had_report = report_path.is_file()
            if had_output:
                shutil.copy2(output, output_backup)
                _fsync_file(output_backup)
            if had_report:
                shutil.copy2(report_path, report_backup)
                _fsync_file(report_backup)
            # Backups must be durable before the prepared journal can authorize rollback.
            _fsync_directory(temp_root)
            transaction = {
                "schema_version": 1,
                "revision": revision,
                "phase": "prepared",
                "had_output": had_output,
                "had_report": had_report,
                "qc_temp_name": qc_temp.name,
            }
            _write_publish_journal(temp_root, transaction)
            output_published = False
            report_published = False
            try:
                os.replace(output_temp, output)
                output_published = True
                _fsync_directory(Path(output_temp).parent)
                _fsync_directory(output.parent)
                os.replace(qc_temp, report_path)
                report_published = True
                _fsync_directory(qc_temp.parent)
                _fsync_directory(report_path.parent)
                transaction["phase"] = "committed"
                _write_publish_journal(temp_root, transaction)
            except Exception:
                try:
                    if output_published:
                        if had_output:
                            _restore_render_backup(
                                output_backup, output, temp_root / "rollback-output.tmp"
                            )
                        else:
                            output.unlink(missing_ok=True)
                    if report_published:
                        if had_report:
                            _restore_render_backup(
                                report_backup, report_path, temp_root / "rollback-qc.tmp"
                            )
                        else:
                            report_path.unlink(missing_ok=True)
                    journal.unlink(missing_ok=True)
                    _fsync_directory(temp_root)
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"artifact publication failed and rollback was deferred: {rollback_error}"
                    ) from rollback_error
                raise
        return report_path, after
    finally:
        qc_temp.unlink(missing_ok=True)
        if journal.is_file() and not journal.is_symlink():
            try:
                journal_state = json.loads(journal.read_text(encoding="utf-8"))
                if journal_state.get("phase") == "committed":
                    journal.unlink(missing_ok=True)
                    _fsync_directory(temp_root)
            except Exception:
                pass
        if not journal.exists():
            output_backup.unlink(missing_ok=True)
            report_backup.unlink(missing_ok=True)

def render_project_film(project_id: str, expected_revision: str, fps: int | None = None) -> dict:
    """Render a pinned film.html revision to MP4; inherit its saved FPS unless overridden."""
    pid = fd.validate_project_id(project_id)
    state = fd.read_project_film(pid)
    if not state.get("exists"):
        raise FilmRenderError("FILM_NOT_INITIALIZED", "当前项目尚未初始化 film.html。", 409)
    revision = state["rev"]
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise FilmRenderError(
            "INVALID_REVISION",
            "导出必须指定从 film.status 获取的非空 expected_revision。",
            400,
        )
    expected_revision = expected_revision.strip()
    if revision != expected_revision:
        raise FilmRenderError(
            "REVISION_CONFLICT",
            f"影片文档已变化：提交版本 {expected_revision}，当前版本 {revision}；请重新载入后导出。",
            409,
        )

    doc = state["doc"]
    if fps is None:
        fps = int(doc.get("fps", FPS_DEFAULT))
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise FilmRenderError("INVALID_FPS", "导出帧率必须是 1–60 的整数。", 422)
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
    duration = max(float(c.get("end") or 0) for c in clips)
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_RENDER_SECONDS:
        raise FilmRenderError("DURATION_LIMIT", f"影片时长必须大于 0 且不超过 {MAX_RENDER_SECONDS} 秒。", 422)
    frame_count = int(math.ceil(duration * fps - 1e-8))
    if frame_count <= 0 or frame_count > MAX_RENDER_FRAMES:
        raise FilmRenderError("FRAME_LIMIT", f"需要渲染 {frame_count} 帧，超出单次导出上限 {MAX_RENDER_FRAMES} 帧。", 422)

    style_css = _style_blocks(doc.get("head_html", ""))
    _validate_source_ranges(pid, doc)
    # Iframe pages are checked when a frame is requested: generated subtitle
    # pages use the legacy film-time acknowledgement; authored HTML scenes must
    # implement window.film.frame(t), otherwise the renderer reports the exact clip.
    for track in doc["tracks"]:
        for clip in track["clips"]:
            if clip["tag"] == "iframe":
                src = str(clip["attrs"].get("src") or "").split("#", 1)[0].replace("\\", "/")
                if not src.lower().endswith((".html", ".htm")):
                    raise FilmRenderError(
                        "UNSUPPORTED_IFRAME_SOURCE",
                        f"iframe 片段 {clip['id']} 必须引用工程内的 .html/.htm 镜头页面。",
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
            blocked_external = _install_offline_network_policy(page, base_url)
            failed_project_resources = _install_scene_resource_failure_monitor(page, base_url)
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
                _raise_for_external_scene_resources(blocked_external)
                _raise_for_scene_resource_failures(failed_project_resources)
                if frame_error:
                    raise FilmRenderError("FRAME_RENDER_FAILED", f"第 {frame_number} 帧失败：{frame_error}", 500)
                page.locator("#runtime").screenshot(
                    path=str(frame_dir / f"{frame_number + 1:08d}.png"),
                    animations="disabled", timeout=20000,
                )
            # Revisit representative timestamps in reverse order, like an editor scrubbing.
            sample_frames = sorted(set([0, frame_count // 2, frame_count - 1]))
            for sample_frame in reversed(sample_frames):
                page.evaluate(
                    "(arg) => {window.__lastFrameReady=null;window.__frameError=null;"
                    "document.getElementById('runtime').contentWindow.postMessage("
                    "{type:'film-runtime:render-frame',time:arg.time,frame:arg.frame},location.origin);}",
                    {"time": sample_frame / fps, "frame": sample_frame},
                )
                page.wait_for_function(
                    "(frame) => window.__lastFrameReady === frame",
                    arg=sample_frame, timeout=20000,
                )
                frame_error = page.evaluate("window.__frameError")
                if frame_error:
                    raise FilmRenderError("FRAME_RENDER_FAILED",
                                          f"确定性抽样第 {sample_frame} 帧失败：{frame_error}", 500)
                repeated_png = page.locator("#runtime").screenshot(animations="disabled", timeout=20000)
                _assert_frame_deterministic(
                    frame_dir / f"{sample_frame + 1:08d}.png", repeated_png, sample_frame
                )
            determinism_samples = len(sample_frames)
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

        # QC is a hard gate before publishing the output: a non-empty MP4 is not
        # evidence that it contains a usable video stream, correct canvas or audio.
        from video_director import prober
        probe = prober.probe_media(str(output_temp))
        expected_width = width + (width % 2)
        expected_height = height + (height % 2)
        if not probe.has_video_stream:
            raise FilmRenderError("QC_VIDEO_STREAM_MISSING", "导出 QC 未检测到视频流。", 500)
        if probe.width != expected_width or probe.height != expected_height:
            raise FilmRenderError(
                "QC_DIMENSION_MISMATCH",
                f"导出画幅与工程不一致：检测到 {probe.width}×{probe.height}，"
                f"预期 {expected_width}×{expected_height}。",
                500,
            )
        if probe.duration is None or abs(float(probe.duration) - duration) > max(0.25, 2.0 / fps):
            raise FilmRenderError(
                "QC_DURATION_MISMATCH",
                f"导出时长与时间线不一致：检测到 {probe.duration} 秒，预期约 {duration:.3f} 秒。",
                500,
            )
        if audio_specs and not probe.has_audio_stream:
            raise FilmRenderError("QC_AUDIO_STREAM_MISSING", "时间线含音频片段，但导出文件没有音频流。", 500)

        ffmpeg = renderer._ffmpeg()
        audio_qc = (_audio_signal_qc(ffmpeg, output_temp, duration) if probe.has_audio_stream else {
            "status": "NOT_PRESENT", "mean_volume_db": None, "max_volume_db": None,
        })
        visual_samples = [
            _visual_frame_qc(frame_dir / f"{frame_number + 1:08d}.png", frame_number, fps)
            for frame_number in sample_frames
        ]
        # The film may be edited by Studio or an external Agent while Chromium
        # and FFmpeg are rendering. Re-read the authoritative document immediately
        # before publishing: a technically valid MP4 from an obsolete revision is
        # not an acceptable export result.
        # Serialize the final revision check and atomic publish with every
        # application-managed film.html edit/undo/redo transaction.
        current_match = True
        audio_signal_check = _audio_signal_qc_finding(audio_specs, audio_qc)
        warnings = (
            [audio_signal_check["detail"]]
            if audio_signal_check and audio_signal_check["status"] == "WARNING"
            else []
        )
        revision_status = "CURRENT" if current_match else "STALE"
        qc_checks = [
            {"name": "mp4_nonempty", "status": "PASS"},
            {"name": "video_stream", "status": "PASS"},
            {"name": "canvas_dimensions", "status": "PASS"},
            {"name": "timeline_duration", "status": "PASS"},
            {"name": "audio_stream", "status": "PASS" if (probe.has_audio_stream or not audio_specs) else "FAIL"},
        ]
        if audio_signal_check is not None:
            qc_checks.append(audio_signal_check)
        qc_checks.append({"name": "deterministic_frames", "status": "PASS"})
        qc_report = {
            "schema_version": 1,
            "status": _qc_report_status(qc_checks),
            "revision_status": revision_status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_id": pid,
            "source_revision": revision,
            "current_revision": revision,
            "current_revision_match": current_match,
            "output": {
                "mp4_path": str(output),
                "size_bytes": output_temp.stat().st_size,
                "sha256": _sha256_file(output_temp),
                "video_codec": probe.codec,
                "width": probe.width,
                "height": probe.height,
                "fps": fps,
                "duration_seconds": float(probe.duration),
            },
            "audio": {
                **audio_qc,
                "stream_present": bool(probe.has_audio_stream),
                "tracks_mixed": len(audio_specs),
            },
            "determinism": {
                "status": "PASS",
                "samples_checked": determinism_samples,
                "sample_frames": sample_frames,
            },
            "visual_samples": visual_samples,
            "checks": qc_checks,
            "warnings": warnings,
        }
        qc_path, after = _publish_render_artifacts(
            pid, revision, output_temp, output, qc_report, temp_root
        )
        return {
            "ok": True, "project_id": pid, "mp4_path": str(output),
            "rendered_revision": revision, "current_revision": after.get("rev"),
            "current": current_match, "revision_status": revision_status,
            "fps": fps, "width": width, "height": height,
            "duration": duration, "frame_count": frame_count,
            "audio_tracks_mixed": len(audio_specs),
            "determinism_status": "PASS", "determinism_samples": determinism_samples,
            "qc_status": qc_report["status"], "qc_report_path": str(qc_path),
            "audio_signal_status": audio_qc["status"], "warnings": warnings,
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
            # Keep prepared journals and backups when rollback did not complete;
            # the next server startup can recover them without guessing.
            journal = temp_root / "publish-transaction.json"
            preserve_for_recovery = journal.exists() or journal.is_symlink()
            if not preserve_for_recovery:
                shutil.rmtree(temp_root, ignore_errors=True)
            elif journal.is_file() and not journal.is_symlink():
                try:
                    state = json.loads(journal.read_text(encoding="utf-8"))
                    if state.get("phase") == "committed":
                        shutil.rmtree(temp_root, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass


def look_project_film(project_id: str, expected_revision: str | None = None,
                      times: list[float] | None = None) -> dict:
    """Render a visual contact sheet and re-check sample timestamps in reverse order.

    This is the agent-facing fast feedback loop. It renders the authoritative
    film.html through the same browser runtime as MP4 export, but does not encode
    a complete video or touch CompositionIR.
    """
    pid = fd.validate_project_id(project_id)
    state = fd.read_project_film(pid)
    if not state.get("exists"):
        raise FilmRenderError("FILM_NOT_INITIALIZED", "当前项目尚未初始化 film.html。", 409)
    revision = state["rev"]
    if expected_revision and expected_revision != revision:
        raise FilmRenderError(
            "REVISION_CONFLICT",
            f"影片文档已变化：提交版本 {expected_revision}，当前版本 {revision}；请重新载入后再 look。",
            409,
        )
    doc = state["doc"]
    width, height = int(doc["stage"]["width"]), int(doc["stage"]["height"])
    if not 16 <= width <= MAX_STAGE_EDGE or not 16 <= height <= MAX_STAGE_EDGE:
        raise FilmRenderError("INVALID_STAGE_SIZE", f"画布尺寸 {width}×{height} 不在可检查范围内。", 422)
    payload = fd.film_payload(pid, state)
    clips = [clip for track in payload["tracks"] for clip in track["clips"]]
    if not clips:
        raise FilmRenderError("EMPTY_FILM", "影片文档没有片段，无法生成 look 接触表。", 422)
    if any(clip.get("duration") is None for clip in clips):
        missing = [clip["id"] for clip in clips if clip.get("duration") is None]
        raise FilmRenderError("SOURCE_RANGE_REQUIRED", "以下片段缺少明确源区间：" + "、".join(missing[:12]), 422)
    duration = max(float(clip.get("end") or 0) for clip in clips)
    if not math.isfinite(duration) or duration <= 0 or duration > MAX_RENDER_SECONDS:
        raise FilmRenderError("DURATION_LIMIT", f"影片时长必须大于 0 且不超过 {MAX_RENDER_SECONDS} 秒。", 422)
    _validate_source_ranges(pid, doc)
    for track in doc["tracks"]:
        for clip in track["clips"]:
            if clip["tag"] == "iframe":
                src = str(clip["attrs"].get("src") or "").split("#", 1)[0].replace("\\", "/")
                if not src.lower().endswith((".html", ".htm")):
                    raise FilmRenderError(
                        "UNSUPPORTED_IFRAME_SOURCE",
                        f"iframe 片段 {clip['id']} 必须引用工程内的 .html/.htm 镜头页面。",
                        422,
                    )

    if times is None:
        candidates = [0.0, duration * 0.25, duration * 0.5, duration * 0.75, max(0.0, duration - 1 / FPS_DEFAULT)]
    else:
        candidates = [float(value) for value in times]
    samples = sorted(set(round(value, 6) for value in candidates))
    if not samples or len(samples) > 24:
        raise FilmRenderError("INVALID_LOOK_TIMES", "look 至少需要 1 个、至多 24 个检查时刻。", 422)
    if any(not math.isfinite(value) or value < 0 or value >= duration for value in samples):
        raise FilmRenderError("INVALID_LOOK_TIMES", f"look 检查时刻必须位于 [0, {duration:.3f}) 秒内。", 422)

    media_server = None
    browser = None
    try:
        media_server = _RenderHTTPServer(("127.0.0.1", 0), pid)
        server_thread = threading.Thread(target=media_server.serve_forever, name="film-look-media", daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{media_server.server_address[1]}"
        runtime_payload = _runtime_payload(pid, state, base_url)
        runtime_payload["render_css"] = _style_blocks(doc.get("head_html", ""))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            blocked_external = _install_offline_network_policy(page, base_url)
            failed_project_resources = _install_scene_resource_failure_monitor(page, base_url)
            page.goto(base_url + "/host", wait_until="load", timeout=30000)
            page.wait_for_function("window.__runtimeReady === true", timeout=30000)
            page.evaluate(
                "(arg) => document.getElementById('runtime').contentWindow.postMessage("
                "{type:'film-runtime:load',data:arg.data,rev:arg.rev},location.origin)",
                {"data": runtime_payload, "rev": revision},
            )
            page.wait_for_function("arg => window.__runtimeRev === arg", arg=revision, timeout=30000)
            frame_pngs = {}
            for frame_number, timestamp in enumerate(samples):
                page.evaluate(
                    "(arg) => {window.__lastFrameReady=null;window.__frameError=null;"
                    "document.getElementById('runtime').contentWindow.postMessage("
                    "{type:'film-runtime:render-frame',time:arg.time,frame:arg.frame},location.origin);}",
                    {"time": timestamp, "frame": frame_number},
                )
                page.wait_for_function("(frame) => window.__lastFrameReady === frame",
                                       arg=frame_number, timeout=20000)
                frame_error = page.evaluate("window.__frameError")
                _raise_for_external_scene_resources(blocked_external)
                _raise_for_scene_resource_failures(failed_project_resources)
                if frame_error:
                    raise FilmRenderError("FRAME_RENDER_FAILED", f"时刻 {timestamp:.3f}s 绘制失败：{frame_error}", 422)
                frame_pngs[timestamp] = page.locator("#runtime").screenshot(
                    animations="disabled", timeout=20000
                )

            # A contact sheet is not accepted as deterministic evidence until the
            # same timestamps are visited in a different order and compared.
            for frame_number, timestamp in enumerate(reversed(samples), start=len(samples)):
                page.evaluate(
                    "(arg) => {window.__lastFrameReady=null;window.__frameError=null;"
                    "document.getElementById('runtime').contentWindow.postMessage("
                    "{type:'film-runtime:render-frame',time:arg.time,frame:arg.frame},location.origin);}",
                    {"time": timestamp, "frame": frame_number},
                )
                page.wait_for_function("(frame) => window.__lastFrameReady === frame",
                                       arg=frame_number, timeout=20000)
                frame_error = page.evaluate("window.__frameError")
                _raise_for_external_scene_resources(blocked_external)
                _raise_for_scene_resource_failures(failed_project_resources)
                if frame_error:
                    raise FilmRenderError("FRAME_RENDER_FAILED",
                                          f"反序检查时刻 {timestamp:.3f}s 绘制失败：{frame_error}", 422)
                repeated = page.locator("#runtime").screenshot(animations="disabled", timeout=20000)
                changed, total, frame_width, frame_height = _frame_diff_stats(frame_pngs[timestamp], repeated)
                allowed = max(16, int(total * 0.0005))
                if changed > allowed:
                    raise FilmRenderError(
                        "NON_DETERMINISTIC_FRAME",
                        f"look 时刻 {timestamp:.3f}s 重复绘制不一致：{changed}/{total} 像素超过通道差 32，"
                        f"允许 {allowed}；画布 {frame_width}×{frame_height}。",
                        422,
                    )
            browser.close()
            browser = None

        from PIL import ImageDraw
        from PIL import ImageOps
        cache_dir = config.project_dir(pid) / ".film" / "cache" / "look"
        cache_dir.mkdir(parents=True, exist_ok=True)
        output = cache_dir / f"contact-{revision}.png"
        cell_width, image_height, label_height = 640, 360, 28
        # Three samples fit on one row at native preview scale; longer films
        # wrap to additional rows without making each thumbnail too small.
        columns = min(3, len(samples))
        rows = int(math.ceil(len(samples) / columns))
        sheet = Image.new("RGB", (columns * cell_width, rows * (image_height + label_height)), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for index, timestamp in enumerate(samples):
            with Image.open(BytesIO(frame_pngs[timestamp])) as source:
                thumbnail = ImageOps.contain(source.convert("RGB"), (cell_width, image_height))
            x = (index % columns) * cell_width
            y = (index // columns) * (image_height + label_height)
            sheet.paste(thumbnail, (x + (cell_width - thumbnail.width) // 2,
                                    y + (image_height - thumbnail.height) // 2))
            draw.text((x + 8, y + image_height + 6),
                      f"t={timestamp:.3f}s  revision={revision[:12]}", fill=(235, 235, 235))
        temp_output = output.with_suffix(".tmp.png")
        sheet.save(temp_output, format="PNG")
        # A look result is a preview of one immutable film revision. Do not
        # publish its contact sheet if the authoritative document changed while
        # Chromium was sampling frames.
        # Keep the revision check and contact-sheet publication in the same
        # project transaction as Studio/CLI edits.
        with fd.project_lock(pid):
            after = fd.read_project_film(pid)
            if after.get("rev") != revision:
                temp_output.unlink(missing_ok=True)
                raise FilmRenderError(
                    "REVISION_CONFLICT",
                    "look 检查期间影片文档已变化；旧 revision 的预览接触表未发布。请重新载入最新版本后再检查。",
                    409,
                )
            os.replace(temp_output, output)
        return {
            "ok": True,
            "project_id": pid,
            "revision": revision,
            "duration": duration,
            "sample_times": samples,
            "image_path": str(output),
            "determinism_status": "PASS",
            "determinism_samples": len(samples),
        }
    except FilmRenderError:
        raise
    except Exception as exc:
        raise FilmRenderError("FILM_LOOK_FAILED", f"look 检查失败：{exc}", 500) from exc
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

