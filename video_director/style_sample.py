"""Render a real, isolated MP4 preview for a supported native StyleContract.

External style registry records remain metadata-only until a bounded execution package
and its dependencies are registered. This module never evaluates arbitrary imported code.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from video_director import asset_catalog, audio as vd_audio, config as vd_config
from video_director import editor as vd_editor, prober as vd_prober, renderer as vd_renderer
from video_director.models import AssetRef, CompositionIR, SubtitleStyle
from video_director import style as vd_style


class StyleSampleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _srt_time(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds_i, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_i:02d},{ms:03d}"


def _write_subtitles(ir: CompositionIR, output: Path, font_size: int) -> tuple[str | None, bool]:
    """Use persisted cue timing when present; otherwise build the existing sentence ASS."""
    track = getattr(ir, "subtitle_track", None)
    cues = list(getattr(track, "cues", []) or [])
    if cues:
        entries = []
        duration = float(ir.project.duration)
        for cue in cues:
            start = max(0.0, float(cue.start))
            end = min(duration, float(cue.end))
            text = str(cue.text or "").replace("\r", " ").replace("\n", " ").strip()
            if end <= start or not text:
                continue
            entries.extend([str(len(entries) // 4 + 1), f"{_srt_time(start)} --> {_srt_time(end)}", text, ""])
        if entries:
            srt_output = output.with_suffix(".srt")
            srt_output.write_text("\n".join(entries), encoding="utf-8")
            return str(srt_output), True

    scene_rows = [
        {"start": float(scene.start), "duration": float(scene.duration), "text": str(scene.text or "")}
        for scene in ir.scenes if str(scene.text or "").strip()
    ]
    if not scene_rows:
        return None, False
    from video_director.subtitle import build_ass_plain

    ass_output = output.with_suffix(".ass")
    ass_output.write_text(
        build_ass_plain(scene_rows, width=ir.project.width, height=ir.project.height, font_size=font_size),
        encoding="utf-8",
    )
    return str(ass_output), True


def _safe_file(value: Any, trusted_roots: list[Path], project_dir: Path) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved.is_file():
        return None
    for root in trusted_roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except (OSError, ValueError):
            continue
    return None


def _required_asset_ids(ir: CompositionIR) -> set[str]:
    ids: set[str] = set()
    for scene in ir.scenes:
        for layer in scene.layers:
            if getattr(layer, "asset_id", None):
                ids.add(str(layer.asset_id))
    for clip in getattr(ir, "visual_overlays", []) or []:
        if getattr(clip, "asset_id", None):
            ids.add(str(clip.asset_id))
    for track in ir.audio or []:
        if not getattr(track, "muted", False) and getattr(track, "asset_id", None):
            ids.add(str(track.asset_id))
    for watermark in getattr(ir, "watermarks", []) or []:
        if getattr(watermark, "kind", "text") != "text" and getattr(watermark, "asset_id", None):
            ids.add(str(watermark.asset_id))
    return ids


def _load_assets(project_id: str, ir: CompositionIR) -> tuple[dict[str, AssetRef], list[str]]:
    project_dir = vd_config.project_dir(project_id).resolve()
    storage_root = vd_config.composer_storage_root().resolve()
    library_root = asset_catalog.library_root().resolve()
    trusted_roots = [storage_root, library_root]
    manifest_by_id: dict[str, dict] = {}
    manifest = project_dir / "assets" / "manifest.json"
    if manifest.is_file():
        try:
            rows = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                manifest_by_id = {
                    str(row.get("id")): row for row in rows
                    if isinstance(row, dict) and row.get("id")
                }
        except (OSError, json.JSONDecodeError):
            raise StyleSampleError("CORRUPT_ASSET_MANIFEST", "项目素材清单损坏，无法安全渲染样片。")

    audio_library = vd_audio.scan_library()
    ir_assets = {
        str(asset.id): asset.model_dump()
        for asset in (getattr(ir, "assets", []) or [])
        if getattr(asset, "id", None)
    }
    needed = _required_asset_ids(ir)
    assets: dict[str, AssetRef] = {}
    missing: list[str] = []
    for asset_id in sorted(needed):
        entry = manifest_by_id.get(asset_id) or ir_assets.get(asset_id) or asset_catalog.get_asset(asset_id)
        if entry:
            path = _safe_file(entry.get("file"), trusted_roots, project_dir)
            if path is None:
                missing.append(asset_id)
                continue
            try:
                entry_type = entry.get("type") or "video"
                entry_type = getattr(entry_type, "value", entry_type)
                entry_source = entry.get("source") or "uploaded"
                entry_source = getattr(entry_source, "value", entry_source)
                assets[asset_id] = AssetRef(
                    id=asset_id,
                    type=str(entry_type),
                    file=str(path),
                    duration=float(entry["duration"]) if entry.get("duration") is not None else None,
                    width=int(entry["width"]) if entry.get("width") is not None else None,
                    height=int(entry["height"]) if entry.get("height") is not None else None,
                    fps=float(entry["fps"]) if entry.get("fps") is not None else None,
                    source=str(entry_source),
                )
                continue
            except (TypeError, ValueError):
                missing.append(asset_id)
                continue
        library_asset = audio_library.get(asset_id)
        if library_asset is not None:
            path = _safe_file(library_asset.file, trusted_roots, project_dir)
            if path is not None:
                assets[asset_id] = library_asset.model_copy(update={"file": str(path)})
                continue
        missing.append(asset_id)
    return assets, missing


def render_style_sample(project_id: str, style_id: str, sample_root: Path) -> dict:
    """Apply the supported part of a native style on a copy of the current CompositionIR."""
    project_id = str(project_id or "").strip()
    style_id = str(style_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id):
        raise StyleSampleError("INVALID_PROJECT_ID", "项目 ID 格式无效。")

    slug = style_id.removeprefix("style.")
    available = set(vd_style.available_styles())
    if slug not in available:
        raise StyleSampleError(
            "METADATA_ONLY",
            "该风格没有受控的内置执行器；当前只能查看元数据，不能声称可运行或生成其风格样片。",
        )

    try:
        original = vd_editor.load_composition(project_id)
    except Exception as exc:
        raise StyleSampleError("COMPOSITION_NOT_FOUND", f"无法读取项目时间线：{exc}") from exc
    ir = original.model_copy(deep=True)
    contract, warnings = vd_style.select_style("", slug)
    sample_root.mkdir(parents=True, exist_ok=True)

    # Keep generated samples bounded; old sample MP4s expire after 72 hours.
    expiry = time.time() - 72 * 60 * 60
    for pattern in ("*.mp4", "*.work"):
        for stale in sample_root.glob(pattern):
            try:
                if stale.stat().st_mtime < expiry:
                    if stale.is_dir():
                        shutil.rmtree(stale, ignore_errors=True)
                    else:
                        stale.unlink()
            except OSError:
                pass

    sample_id = uuid.uuid4().hex
    work_dir = sample_root / (sample_id + ".work")
    work_dir.mkdir(parents=True, exist_ok=False)
    published = sample_root / (sample_id + ".mp4")
    applied: list[str] = []
    not_applied: list[str] = []
    subtitle_path: str | None = None
    expected_audio = False

    try:
        assets, missing = _load_assets(project_id, ir)
        if missing:
            raise StyleSampleError(
                "MISSING_ASSETS",
                "时间线引用的素材无法解析，未生成样片：" + "、".join(missing[:12]),
            )
        if not assets:
            raise StyleSampleError("NO_RENDERABLE_ASSETS", "当前项目没有可解析的图片/视频/音频素材。")

        ir.style_id = slug
        # These are parameters the current FFmpeg renderer can truly consume today.
        style_values = ir.subtitle_style.model_dump() if ir.subtitle_style else {}
        style_values["position"] = contract.subtitle_position
        # renderer.subtitle_burn_in maps size to force_style FontSize=18*size.
        # Use that actual renderer base, not the ASS generator's separate 56px default.
        style_values["size"] = contract.subtitle_font_size / 18.0
        ir.subtitle_style = SubtitleStyle.model_validate(style_values)
        applied.extend(["字幕位置", "字幕字号"])
        if any(getattr(track.type, "value", track.type) == "bgm" and not track.muted for track in ir.audio):
            ir.audio = [
                track.model_copy(update={"volume": contract.bgm_volume})
                if getattr(track.type, "value", track.type) == "bgm" and not track.muted
                else track
                for track in ir.audio
            ]
            applied.append("BGM 音量")

        # These fields are retained as explicit gaps, not silently claimed as applied.
        not_applied.extend([
            "图片运动倍率：现有逐镜 IR 暂无该风格参数的连续强度接口",
            "转场预算：不覆盖用户已经确认的镜头剪辑",
            "音效密度：不自动增删用户时间线中的 SFX",
            "调色板/字体族：当前 FFmpeg media renderer 尚未把完整 StyleContract 消费进所有图层",
        ])

        subtitle_path, subtitles_present = _write_subtitles(
            ir, work_dir / "style-sample-subtitles.srt", contract.subtitle_font_size
        )
        if not subtitles_present:
            not_applied.append("字幕样式：当前项目没有可渲染的字幕文本/时间锚点")
            subtitle_path = None

        project_dir = vd_config.project_dir(project_id).resolve()
        narration_candidates = [
            project_dir / "audio" / "narration.mp3",
            project_dir / "audio" / "narration_uploaded.mp3",
        ]
        narration_candidates.extend(sorted((project_dir / "audio").glob("narration_uploaded.*")))
        trusted_roots = [vd_config.composer_storage_root().resolve(), asset_catalog.library_root().resolve()]
        narration_path = next(
            (_safe_file(p, trusted_roots, project_dir) for p in narration_candidates if p.is_file()),
            None,
        )
        narration_file = str(narration_path) if narration_path else None
        expected_audio = bool(narration_file) or any(
            not getattr(track, "muted", False) and getattr(track, "asset_id", None)
            for track in (ir.audio or [])
        )

        result = vd_renderer.render_composition(
            ir, assets, work_dir / "render",
            narration_file=narration_file,
            subtitle_file=subtitle_path,
        )
        final_mp4 = Path(result.final_path).resolve()
        if not final_mp4.is_file() or final_mp4.stat().st_size < 1024:
            raise StyleSampleError("INVALID_MP4", "渲染器未生成有效 MP4；样片未发布。")
        probe = vd_prober.probe_media(str(final_mp4))
        if not probe.has_video_stream or not probe.duration or probe.duration <= 0:
            raise StyleSampleError("INVALID_MP4", "MP4 缺少有效视频流或时长；样片未发布。")
        if expected_audio and not getattr(probe, "has_audio_stream", False):
            raise StyleSampleError("AUDIO_MISSING", "项目含非静音音频素材，但输出 MP4 没有音轨。")
        ffmpeg = vd_prober._resolve_ffmpeg()
        if not ffmpeg:
            raise StyleSampleError("FFMPEG_MISSING", "无法解析 FFmpeg，不能通过完整解码验收。")
        decoded = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(final_mp4), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        if decoded.returncode != 0:
            raise StyleSampleError("MP4_DECODE_FAILED", "MP4 完整解码失败：" + (decoded.stderr or "")[-900:])

        shutil.copy2(final_mp4, published)
        report = {
            "style_id": slug,
            "status": "PARTIAL" if not_applied else "APPLIED",
            "sample_render": "PASS",
            "mp4_decode": "PASS",
            "applied_parameters": applied,
            "not_applied_parameters": not_applied,
            "warnings": list(warnings),
            "sample_mp4": {
                "decode": "PASS",
                "duration_seconds": round(float(probe.duration), 3),
                "width": probe.width,
                "height": probe.height,
                "has_audio": bool(getattr(probe, "has_audio_stream", False)),
                "bytes": published.stat().st_size,
            },
        }
        return {
            "ok": True,
            "sample_id": sample_id,
            "style_id": slug,
            "project_id": project_id,
            "download_url": "/api/style_sample/download?id=" + sample_id,
            "runtime": report,
            "message": "当前时间线的样片已真实渲染并完成 MP4 全解码；未实现的风格参数已逐项列出。",
        }
    except StyleSampleError:
        published.unlink(missing_ok=True)
        raise
    except Exception as exc:
        published.unlink(missing_ok=True)
        raise StyleSampleError("SAMPLE_RENDER_FAILED", f"样片渲染失败：{exc}") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
