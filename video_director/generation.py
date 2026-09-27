"""Timeline-native Generation（EDT-008 / §十六 / §31）。

选时间范围 → Generate → Provider → 结果变体 → 加入时间线 → 素材落地。

能力边界（诚实）：
    image / graphic = 程序化视觉卡（Graphics Runtime 确定性渲染 PNG）——
        自有 Renderer，无外部 API 依赖，source="generated" CLEAR。
    video           = 程序化动态图形（Graphics Runtime 确定渲染短 MP4）。
    sfx             = 无生图/生视频/合成音 provider → GENERATION_UNAVAILABLE（禁 fake）。

结果变体 3 个（§31「结果 A/B/C」）：按 style/强调色确定性变化。

Lineage（§23/§31）：每个结果写 generation_request_id / provider id /
input_fingerprint（prompt 哈希）/ output_fingerprint（文件哈希）进 manifest。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import config


class GenerationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


#: 本系统真实可用的生成类型（sfx 无 provider，诚实不可用）
SUPPORTED_TYPES = ("image", "graphic", "video")

#: 3 个确定性变体名（§31 结果 A/B/C）
VARIANTS = ("A", "B", "C")


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


@dataclass(frozen=True)
class GenerationResult:
    asset_id: str
    variant: str
    type: str
    file: str
    width: int
    height: int
    input_fingerprint: str
    output_fingerprint: str
    provider_id: str


def _build_single_scene_program(prompt: str, variant: str, width: int, height: int):
    """prompt → 单场景 GraphicProgram（确定性，变化只在 style/强调色）。"""
    from video_director.graphics import director as vd_director

    style_map = {"A": "editorial", "B": "minimal", "C": "bold"}
    plan = vd_director.build_graphic_plan(
        script=prompt,
        content_type="knowledge",
        style_id=style_map.get(variant, "editorial"),
        scene_duration=3.0,
        width=width,
        height=height,
        fps=30.0,
    )
    # 单场景版本：只取首场景（生成素材是一个视觉卡，非完整叙事）
    plan["scenes"] = plan["scenes"][:1]
    if plan["scenes"]:
        plan["scenes"][0]["duration"] = min(plan["scenes"][0]["duration"], 3.0)
    return vd_director.instantiate_graphic_program(plan)


def _manifest_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "assets" / "manifest.json"


def _load_manifest(project_id: str) -> List[dict]:
    p = _manifest_path(project_id)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_manifest(project_id: str, entries: List[dict]) -> None:
    p = _manifest_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(
    project_id: str,
    prompt: str,
    gen_type: str = "image",
    width: int = 1080,
    height: int = 1920,
    variant: Optional[str] = None,
) -> List[GenerationResult]:
    """生成 1 或 3 个素材变体，落地到项目 assets 并登记 lineage。

    variant=None → 生成 A/B/C 三变体（§31）；指定则只生成该变体。
    gen_type 不在 SUPPORTED_TYPES → GENERATION_UNAVAILABLE（sfx 等）。
    """
    if not prompt or not str(prompt).strip():
        raise GenerationError("INVALID_PROMPT", "生成提示词不能为空")
    if gen_type not in SUPPORTED_TYPES:
        raise GenerationError(
            "GENERATION_UNAVAILABLE",
            f"类型 {gen_type!r} 无可用生成 provider（可用: image/graphic/video 程序化）",
        )
    prompt = str(prompt).strip()
    variants = [variant] if variant else list(VARIANTS)
    base = config.project_dir(project_id)
    assets_dir = base / "assets" / "generated"
    assets_dir.mkdir(parents=True, exist_ok=True)

    from video_director.graphics import compiler as vd_compiler
    from video_director.graphics import render as vd_gfx_render

    input_fp = hashlib.sha256((prompt + "|" + gen_type).encode("utf-8")).hexdigest()[:16]
    results: List[GenerationResult] = []
    manifest = _load_manifest(project_id)

    for v in variants:
        program = _build_single_scene_program(prompt, v, width, height)
        html = vd_compiler.compile_to_html(program)
        asset_id = f"gen-{uuid.uuid4().hex[:8]}"
        if gen_type == "video":
            out_file = assets_dir / f"{asset_id}.mp4"
            vd_gfx_render.render_graphic_program(program, str(out_file), quality="draft")
            asset_type = "video"
            dur = float(program.duration)
        else:
            out_file = assets_dir / f"{asset_id}.png"
            vd_gfx_render.render_still(html, str(out_file), width, height, frame=0)
            asset_type = "image"
            dur = None
        out_fp = _fingerprint(out_file.read_bytes())
        results.append(GenerationResult(
            asset_id=asset_id, variant=v, type=asset_type, file=str(out_file),
            width=width, height=height,
            input_fingerprint=input_fp, output_fingerprint=out_fp,
            provider_id="graphics-runtime",
        ))
        # 登记 lineage + provenance（§23/§31：生成素材 origin=generated CLEAR）
        manifest.append({
            "id": asset_id, "type": asset_type, "file": str(out_file),
            "duration": dur, "width": width, "height": height,
            "fps": 30.0,
            "source": "generated",
            "origin": "generated",
            "license_note": "generated by graphics-runtime (own renderer)",
            "prompt": prompt,
            "generation_request_id": f"genreq-{input_fp}",
            "provider_id": "graphics-runtime",
            "input_fingerprint": input_fp,
            "output_fingerprint": out_fp,
            "variant": v,
            "created_at": time.time(),
        })
    _save_manifest(project_id, manifest)
    logger.info(
        f"generation: {gen_type} x{len(results)} prompt={prompt[:30]!r} → {project_id}"
    )
    return results


def add_to_timeline(project_id: str, scene_id: str, asset_id: str) -> dict:
    """生成结果接入时间线（EDT-008 Add to Timeline）：走 editor 命令 generate_media。

    由 API 层决定 asset 类型；这里只做 asset 存在性 + 类型解析，
    实际落层复用 editor._cmd_generate_media（revision 版本化）。
    """
    manifest = _load_manifest(project_id)
    entry = next((a for a in manifest if a.get("id") == asset_id), None)
    if entry is None:
        raise GenerationError("ASSET_NOT_FOUND", f"生成素材 {asset_id} 未登记")
    from video_director import editor as vd_editor
    result = vd_editor.execute_command(project_id, {
        "command": "generate_media",
        "targetId": scene_id,
        "payload": {"assetId": asset_id, "assetType": entry.get("type", "image")},
        "author": "generation",
    })
    return {
        "status": result.status,
        "revision_id": result.revision_id,
        "composition_version": result.composition_version,
        "asset_type": entry.get("type"),
        "errors": result.errors,
    }