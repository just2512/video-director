"""Remotion rendering adapter for the existing CompositionIR.

The adapter builds an isolated, project-scoped public directory and input-props
manifest, invokes the pinned Remotion CLI, then records render and QC evidence.
No caller-supplied filesystem path is accepted by the HTTP API.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from video_director import asset_catalog, config, editor, prober, qc, talkcraft

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
RUNTIME_ROOT = Path(__file__).resolve().parent.parent / "remotion_runtime"


class RemotionError(Exception):
    pass


def _lock_for(project_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(project_id, threading.Lock())


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value))


def _local_asset_map(ir) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in getattr(ir, "assets", []) or []:
        aid = getattr(item, "id", None)
        if aid:
            out[str(aid)] = {
                "id": str(aid), "type": getattr(item, "type", ""),
                "file": getattr(item, "file", ""), "duration": getattr(item, "duration", None),
                "rights_status": getattr(item, "rights_status", None),
            }
    return out


def _resolve_asset(asset_id: str, known: dict[str, dict]) -> dict | None:
    if not asset_id:
        return None
    if asset_id in known and Path(str(known[asset_id].get("file") or "")).is_file():
        return known[asset_id]
    entry = asset_catalog.get_asset(asset_id)
    if not entry:
        return None
    path = entry.get("file") or entry.get("path") or ""
    if not path or not Path(path).is_file():
        return None
    return {**entry, "id": asset_id, "file": path}


def _public_asset(path: Path, public_dir: Path, cache: dict[str, str]) -> str:
    path = path.resolve()
    if not path.is_file():
        raise RemotionError(f"素材文件不存在：{path.name}")
    digest = _sha256_file(path)
    if digest in cache:
        return cache[digest]
    suffix = path.suffix.lower() or ".bin"
    relative = f"assets/{digest[:20]}{suffix}"
    target = public_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or _sha256_file(target) != digest:
        shutil.copy2(path, target)
    cache[digest] = relative
    return relative


def _extract_original_audio(source: Path, source_in: float, duration: float,
                            public_dir: Path, index: int) -> str:
    if duration <= 0:
        raise RemotionError("原声音轨时长必须大于 0。")
    target_rel = f"assets/original-{index:03d}.wav"
    target = public_dir / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        prober._resolve_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, source_in):.4f}", "-i", str(source),
        "-t", f"{duration:.4f}", "-vn", "-ac", "2", "-ar", "48000",
        "-c:a", "pcm_s16le", str(target),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemotionError(f"原声抽取执行失败：{exc}") from exc
    if result.returncode != 0 or not target.is_file() or target.stat().st_size < 44:
        detail = (result.stderr or "没有生成音频文件")[-600:]
        raise RemotionError(f"原声抽取失败：{detail}")
    return target_rel


def _prepare_props(project_id: str, quality: str) -> tuple[Path, Path, Path, dict]:
    root = config.project_dir(project_id)
    ir = editor.load_composition(project_id)
    plan_state = talkcraft.get_plan(project_id)
    if plan_state.get("status") != "READY":
        raise RemotionError("口播创作蓝图未就绪或已过期；请先在工作台刷新蓝图。")
    if quality not in ("draft", "final"):
        raise RemotionError("quality 只能是 draft 或 final。")

    timing_path = root / "alignment" / "character_timing.json"
    semantic_path = root / "plans" / "semantic_beats.json"
    shot_path = root / "plans" / "shot_plan.json"
    timing_doc = json.loads(timing_path.read_text(encoding="utf-8"))
    semantic_doc = json.loads(semantic_path.read_text(encoding="utf-8"))
    shot_doc = json.loads(shot_path.read_text(encoding="utf-8"))
    if quality == "final":
        todo = [b["scene_id"] for b in semantic_doc.get("beats", [])
                if b.get("review_status") != "REVIEWED"]
        uncertain = [s.get("scene_id", "?") for s in timing_doc.get("sentences", [])
                     if s.get("status") != "ALIGNED_FROM_WORDS"]
        if todo or uncertain:
            parts = []
            if todo:
                parts.append("待复核语义拍：" + "、".join(todo[:12]))
            if uncertain:
                parts.append("时间覆盖不足：" + "、".join(uncertain[:12]))
            raise RemotionError("正式渲染被验收门拦截。请先处理：" + "；".join(parts))

    runtime_modules = RUNTIME_ROOT / "node_modules"
    cli = runtime_modules / ".bin" / ("remotion.cmd" if os.name == "nt" else "remotion")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not runtime_modules.is_dir() or (not cli.is_file() and not npm):
        raise RemotionError(
            "Remotion 运行环境未安装。先在项目根目录执行 "
            "'cd remotion_runtime && npm install'，完成后重新打开工作台。"
        )

    render_root = root / "render" / "remotion"
    public_dir = render_root / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    props_path = render_root / "input-props.json"
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / ("talkcraft-remotion-draft.mp4" if quality == "draft"
                                else "talkcraft-remotion-final.mp4")
    (render_root / "render.log").parent.mkdir(parents=True, exist_ok=True)

    known_assets = _local_asset_map(ir)
    copied: dict[str, str] = {}
    recipe_map = {r["id"]: r for r in (plan_state.get("recipes") or []) if r.get("id")}
    beat_map = {b.get("scene_id"): b for b in semantic_doc.get("beats", [])}
    timing_map = {s.get("scene_id"): s for s in timing_doc.get("sentences", [])}
    shot_map = {s.get("scene_id"): s for s in shot_doc.get("shots", [])}

    narration_path = next((p for p in (
        root / "audio" / "narration.wav",
        root / "audio" / "narration.mp3",
        root / "audio" / "full.wav",
        root / "audio" / "full.mp3",
    ) if p.is_file()), None)
    narration_src = _public_asset(narration_path, public_dir, copied) if narration_path else None

    scenes: list[dict] = []
    for scene in ir.scenes:
        beat = beat_map.get(scene.id)
        timing = timing_map.get(scene.id) or {}
        shot = shot_map.get(scene.id) or {}
        layers: list[dict] = []
        for index, layer in enumerate(scene.layers):
            layer_type = _enum(layer.type)
            aid = getattr(layer, "asset_id", None)
            record = _resolve_asset(str(aid), known_assets) if aid else None
            asset_src = None
            if record:
                asset_src = _public_asset(Path(record["file"]), public_dir, copied)
            if layer_type in ("video", "person") and not asset_src:
                raise RemotionError(f"场景 {scene.id} 的第 {index + 1} 层缺少真实视频素材；请在素材面板补齐后再渲染。")
            if layer_type == "image" and not asset_src:
                raise RemotionError(f"场景 {scene.id} 的第 {index + 1} 层缺少真实图片素材。")
            if layer_type not in ("video", "person", "image", "graphic", "text", "title", "background", "shape", "logo"):
                raise RemotionError(f"场景 {scene.id} 的图层类型 {layer_type} 未接入 Remotion 适配器，已拒绝静默降级。")
            transform = getattr(layer, "transform", None)
            if transform is not None:
                transform = transform.model_dump() if hasattr(transform, "model_dump") else dict(transform)
                # The current Transform contract uses normalized positions; the renderer
                # consumes scale/opacity/rotation here. Do not treat normalized x/y as pixels.
                transform = {k: transform[k] for k in ("scale", "opacity", "rotation") if k in transform}
            motion_instance = getattr(layer, "motion_instance", None)
            if motion_instance and hasattr(motion_instance, "model_dump"):
                motion_instance = motion_instance.model_dump()
            layers.append({
                "index": index, "type": "video" if layer_type == "person" else layer_type,
                "src": asset_src, "asset_id": aid,
                "start": float(layer.start), "duration": float(layer.duration),
                "source_in": float(getattr(layer, "source_in", 0.0) or 0.0),
                "speed": float(getattr(layer, "speed", 1.0) or 1.0),
                "fit_mode": getattr(layer, "fit_mode", "cover") or "cover",
                "text": getattr(layer, "text", None),
                "motion_recipe_id": getattr(layer, "motion_recipe_id", None),
                "motion_instance": motion_instance,
                "transform": transform,
            })
        characters = timing.get("characters") or []
        scenes.append({
            "id": scene.id, "start": float(scene.start), "duration": float(scene.duration),
            "text": scene.text or "", "intent": scene.intent or "", "visual_intent": scene.visual_intent or "",
            "semantic_role": scene.semantic_role or "", "transition_out": _enum(scene.transition_out.type),
            "layers": layers, "beat": beat or {
                "id": f"beat-{scene.id}", "sem": [], "need": ["无"], "entities": [],
                "weight": "main", "intent": scene.intent or "",
                "visual_intent": scene.visual_intent or "", "semantic_role": scene.semantic_role or "",
            }, "characters": characters,
        })

    audio = []
    for idx, track in enumerate(ir.audio):
        if track.muted or track.volume <= 0:
            continue
        track_type = _enum(track.type)
        asset_id = getattr(track, "asset_id", None)
        source_path: Path | None = None
        if track_type == "narration" and narration_path:
            source_path = narration_path
        elif asset_id:
            record = _resolve_asset(str(asset_id), known_assets)
            if record:
                source_path = Path(record["file"])
        if track_type == "narration" and source_path is None and narration_path:
            source_path = narration_path
        if track_type == "original":
            if source_path is None:
                raise RemotionError(f"原声音轨 {idx + 1} 找不到源视频素材，无法生成正确的音频片段。")
            relative = _extract_original_audio(source_path, float(track.source_in or 0),
                                               float(track.duration), public_dir, idx + 1)
            source_in = 0.0
        else:
            if source_path is None:
                if track_type == "narration":
                    continue
                raise RemotionError(f"{track_type} 音轨 {idx + 1} 缺少可读取素材。")
            relative = _public_asset(source_path, public_dir, copied)
            source_in = float(track.source_in or 0.0)
        audio.append({
            "id": f"audio-{idx + 1}", "type": track_type, "src": relative,
            "start": float(track.start), "duration": float(track.duration),
            "source_in": source_in, "volume": float(track.volume),
            "muted": bool(track.muted), "fadeIn": float(track.fadeIn or 0),
            "fadeOut": float(track.fadeOut or 0), "ducking": bool(track.ducking),
        })

    if not any(a["type"] == "narration" for a in audio) and narration_src:
        audio.append({
            "id": "narration-fallback", "type": "narration", "src": narration_src,
            "start": 0.0, "duration": float(ir.project.duration), "source_in": 0.0,
            "volume": 1.0, "muted": False, "fadeIn": 0.0, "fadeOut": 0.0, "ducking": False,
        })

    fps = int(ir.project.fps)
    duration_frames = max(1, int(math.ceil(float(ir.project.duration) * fps)))
    props = {
        "width": int(ir.project.width), "height": int(ir.project.height), "fps": fps,
        "durationInFrames": duration_frames, "scenes": scenes, "audio": audio,
        "recipes": recipe_map, "narrationSrc": narration_src,
        "title": str(store.get_project(project_id).get("script", "") or "")[:120],
    }
    _atomic_json(props_path, props)
    manifest = {
        "version": 1, "project_id": project_id, "quality": quality,
        "composition_version": editor.composition_version(project_id),
        "semantic_revision": semantic_doc.get("revision"),
        "shot_revision": shot_doc.get("revision"),
        "character_timing_hash": _sha256_file(timing_path),
        "input_props_hash": _sha256_file(props_path),
        "public_assets": [{"path": key, "sha256": value} for value, key in copied.items()],
        "fps": fps, "width": props["width"], "height": props["height"],
        "duration_in_frames": duration_frames,
        "created_at": time.time(),
        "output_path": str(output_path),
    }
    return render_root, public_dir, props_path, {"props": props, "output": output_path, "manifest": manifest, "assets": known_assets}


def _run_job(project_id: str, quality: str, job_id: str) -> None:
    root = config.project_dir(project_id)
    status_path = root / "plans" / "remotion_render.json"
    render_root = root / "render" / "remotion"
    log_path = render_root / "render.log"

    def set_status(status: str, message: str, **extra) -> dict:
        state = {
            "version": 1, "project_id": project_id, "job_id": job_id,
            "quality": quality, "status": status, "message": message,
            "updated_at": time.time(),
        }
        state.update(extra)
        _atomic_json(status_path, state)
        return state

    try:
        set_status("PREPARING", "正在校验镜头、素材、语义标注和 Remotion 输入。")
        render_root, public_dir, props_path, built = _prepare_props(project_id, quality)
        output_path: Path = built["output"]
        manifest: dict = built["manifest"]
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            raise RemotionError("找不到 npm。安装 Node.js 18 或更高版本后重试。")
        set_status("RENDERING", "Remotion 正在逐帧渲染；页面可关闭，任务状态会保存在项目中。",
                   output_path=str(output_path), input_props_hash=manifest["input_props_hash"])
        cmd = [
            npm, "exec", "--", "remotion", "render", "src/index.ts",
            "TalkCraftVideo", str(output_path),
            "--props", str(props_path), "--public-dir", str(public_dir),
            "--codec", "h264", "--crf", "18", "--concurrency", "2",
        ]
        timeout = max(900, int(built["props"]["durationInFrames"] / built["props"]["fps"] * 120))
        try:
            proc = subprocess.run(cmd, cwd=RUNTIME_ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RemotionError(f"Remotion 渲染超过时限 {timeout}s；日志保留在 {log_path.name}。") from exc
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text((proc.stdout or "") + "\n--- STDERR ---\n" + (proc.stderr or ""),
                            encoding="utf-8")
        if proc.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 4096:
            detail = (proc.stderr or proc.stdout or "未生成可用输出文件")[-1800:]
            raise RemotionError("Remotion 渲染失败：" + detail)
        manifest["output_sha256"] = _sha256_file(output_path)
        manifest["output_bytes"] = output_path.stat().st_size
        manifest["rendered_at"] = time.time()
        probe = prober.probe_media(str(output_path))
        manifest["probe"] = {
            "duration": probe.duration, "width": probe.width, "height": probe.height,
            "fps": probe.fps, "has_video_stream": probe.has_video_stream,
            "has_audio_stream": probe.has_audio_stream,
        }
        qc_report = qc.run_qc(editor.load_composition(project_id), str(output_path),
                              built["assets"], float(built["props"]["durationInFrames"] / built["props"]["fps"]),
                              expect_subtitle=False, expect_audio=bool(built["props"]["audio"]))
        qc_report.save(str(root / "qc" / f"remotion-{quality}.json"))
        _atomic_json(root / "plans" / "remotion_render_manifest.json", manifest)
        if not qc_report.ok:
            raise RemotionError("成片文件已生成，但 QC 未通过；请查看 qc/remotion-" + quality + ".json。")
        prev = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        count = int(prev.get("successful_final_renders", 0))
        if quality == "final":
            count += 1
        set_status("PASS", "Remotion 成片渲染与机器 QC 均已通过。",
                   output_path=str(output_path), output_url="/file?path=" + __import__("urllib.parse").parse.quote(str(output_path)),
                   output_bytes=manifest["output_bytes"], probe=manifest["probe"],
                   qc=qc_report.to_dict(), manifest_path=str(root / "plans" / "remotion_render_manifest.json"),
                   successful_final_renders=count)
    except Exception as exc:
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
            set_status("REJECT", str(exc), output_path=previous.get("output_path"),
                       log_path=str(log_path))
        except Exception:
            pass


def get_status(project_id: str) -> dict:
    root = _safe_project(project_id)
    path = root / "plans" / "remotion_render.json"
    if not path.is_file():
        installed = (RUNTIME_ROOT / "node_modules").is_dir()
        return {
            "ok": True, "project_id": project_id, "status": "IDLE",
            "message": "Remotion 运行环境已安装。" if installed else
                       "Remotion 运行环境未安装；在 remotion_runtime 目录执行 npm install 后才能渲染。",
            "runtime_installed": installed,
        }
    state = json.loads(path.read_text(encoding="utf-8"))
    state["ok"] = True
    state["runtime_installed"] = (RUNTIME_ROOT / "node_modules").is_dir()
    return state


def _safe_project(project_id: str) -> Path:
    # Reuse the strict project-id check and project-store existence check.
    talkcraft._safe_root(project_id)
    return config.project_dir(project_id)


def start_render(project_id: str, quality: str = "draft", confirm_budget: bool = False) -> dict:
    root = _safe_project(project_id)
    if quality not in ("draft", "final"):
        raise RemotionError("quality 只能是 draft 或 final。")
    with _lock_for(project_id):
        status_path = root / "plans" / "remotion_render.json"
        previous = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        if previous.get("status") in ("PREPARING", "RENDERING"):
            return {"ok": True, **previous, "message": "该项目已有 Remotion 任务正在执行。"}
        successful_finals = int(previous.get("successful_final_renders", 0))
        if quality == "final" and successful_finals >= 2 and not confirm_budget:
            return {
                "ok": False, "confirmation_required": True,
                "message": f"本项目已有 {successful_finals} 次正式整片 Remotion 渲染。继续会增加渲染成本，请确认是否仍要渲染。",
                "successful_final_renders": successful_finals,
            }
        runtime = RUNTIME_ROOT / "node_modules"
        if not runtime.is_dir():
            raise RemotionError("Remotion 运行环境未安装。先在 remotion_runtime 目录执行 npm install。")
        job_id = uuid.uuid4().hex
        state = {
            "version": 1, "project_id": project_id, "job_id": job_id,
            "quality": quality, "status": "QUEUED",
            "message": "已排入 Remotion 渲染任务。", "created_at": time.time(),
            "successful_final_renders": successful_finals,
        }
        _atomic_json(status_path, state)
        thread = threading.Thread(target=_run_job, args=(project_id, quality, job_id),
                                  name=f"remotion-{project_id}", daemon=True)
        thread.start()
        return {"ok": True, **state}
