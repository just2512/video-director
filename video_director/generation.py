"""Timeline-native Generation（EDT-008 / §十六 / §31 / §五十一 Capability G）。

选时间范围 → Generate → Provider → 结果变体 → 加入时间线 → 素材落地。

能力边界（诚实，按 provider 配置动态分流）：
    image   = AI 生图（roles["image"]，OpenAI Images 协议 images.generate；
              未配置 image key → 兜底 Graphics Runtime 程序化视觉卡，仍可用）
    graphic = 程序化视觉卡（自有 Graphics Runtime，确定性 PNG，无外部依赖）
    video   = AI 生视频（roles["video"]，OpenAI 兼容 video/generations 异步任务；
              未配置 video key（或供应商协议不符）→ 诚实报错，禁 fake success）
    sfx     = 无合成音 provider → GENERATION_UNAVAILABLE（禁 fake）

结果变体 3 个（§31「结果 A/B/C」）。

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


# ---------------------------------------------------------------------------
# AI 生图 / 生视频 provider（§五十一 Capability G：Image/Video 正式接线）
# ---------------------------------------------------------------------------

def _role_cfg(role: str) -> dict:
    from video_director import model_config as vd_mc
    return vd_mc.get_role_config(role)


def _image_size(width: int, height: int) -> str:
    """画幅 → OpenAI Images 协议 size（默认 1024 级，按比例映射）。"""
    if abs(width - height) <= max(width, height) * 0.02:
        return "1024x1024"
    if height > width:
        return "1024x1792"  # 竖屏 9:16
    return "1792x1024"      # 横屏 16:9


def generate_image_variants(
    prompt: str,
    width: int,
    height: int,
    count: int = 3,
    variant: Optional[str] = None,
) -> List[dict]:
    """AI 生图（§五十一 Capability G Image）。返回 [{variant, data(bytes), meta}]。

    roles["image"] 有 key → OpenAI Images 协议 images.generate，逐变体呼叫。
    无 key → 抛 GenerationError("IMAGE_PROVIDER_UNAVAILABLE")，由调用方兜底。
    供应商拒绝（size/协议不符）→ 抛 GenerationError("IMAGE_GENERATION_FAILED")，诚实。
    """
    cfg = _role_cfg("image")
    if not (cfg or {}).get("api_key"):
        raise GenerationError(
            "IMAGE_PROVIDER_UNAVAILABLE",
            "生图模型未配置（roles.image.api_key 为空）；将走程序化视觉卡兜底",
        )
    model = cfg.get("model") or "gpt-image-1"
    base_url = cfg.get("base_url") or "https://api.openai.com/v1"
    size = _image_size(width, height)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=base_url)
    except Exception as exc:
        raise GenerationError("IMAGE_PROVIDER_UNAVAILABLE", f"openai 库不可用: {exc}") from exc

    n = count if variant is None else 1
    out: List[dict] = []
    try:
        resp = client.images.generate(model=model, prompt=prompt, size=size, n=n)
    except Exception as exc:
        # 供应商协议不符/失败 → 诚实暴露，不 fake
        raise GenerationError(
            "IMAGE_GENERATION_FAILED",
            f"生图调用失败（model={model}, size={size}）：{exc}",
        ) from exc
    for i, item in enumerate(resp.data or []):
        raw: bytes = b""
        if getattr(item, "b64_json", None):
            import base64
            raw = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            raw = _download_bytes(item.url)
        if not raw:
            continue
        out.append({
            "variant": (variant or VARIANTS[i % len(VARIANTS)]),
            "data": raw,
            "model": model,
        })
    if not out:
        # 探测任务型异步响应（ModelScope Qwen-Image 等：返回 task_id+task_status，data=None）
        # 标准 OpenAI/硅基流动走 data[].b64_json|url；异步型需轮询结果，诚实暴露协议差异。
        ts = getattr(resp, "task_status", None)
        tid = getattr(resp, "task_id", None)
        if tid:
            raise GenerationError(
                "IMAGE_GENERATION_FAILED",
                f"生图返回异步任务（task_id={tid[:8]}… task_status={ts}）"
                f"且 data 为空：{model} 通过该端点走任务型协议，"
                f"本渲染器已接标准 OpenAI Images 协议（data[].b64_json/url），"
                f"请换用 OpenAI 兼容同步端点（如 OpenAI/硅基流动 images），或补充该供应商的任务轮询端点",
            )
        raise GenerationError(
            "IMAGE_GENERATION_FAILED",
            f"生图返回空结果（model={model}）；供应商未返回 b64_json/url",
        )
    return out


def _download_bytes(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


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
    provider 分流（§五十一 Capability G）：
        image   → roles["image"] AI 生图；无 key 兜底 Graphics Runtime 程序化
        graphic → Graphics Runtime 程序化（自有 renderer，确定性）
        video   → roles["video"] AI 生视频（异步任务）；无 key 诚实报错（不 fake）
        sfx     → GENERATION_UNAVAILABLE
    """
    if not prompt or not str(prompt).strip():
        raise GenerationError("INVALID_PROMPT", "生成提示词不能为空")
    if gen_type not in SUPPORTED_TYPES:
        raise GenerationError(
            "GENERATION_UNAVAILABLE",
            f"类型 {gen_type!r} 无可用生成 provider（可用: image/graphic/video）",
        )
    prompt = str(prompt).strip()
    variants = [variant] if variant else list(VARIANTS)
    base = config.project_dir(project_id)
    assets_dir = base / "assets" / "generated"
    assets_dir.mkdir(parents=True, exist_ok=True)

    input_fp = hashlib.sha256((prompt + "|" + gen_type).encode("utf-8")).hexdigest()[:16]
    results: List[GenerationResult] = []
    manifest = _load_manifest(project_id)

    if gen_type == "image":
        results = _generate_image_or_fallback(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest)
    elif gen_type == "video":
        results = _generate_video(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest)
    else:  # graphic → 程序化视觉卡（自有 renderer，确定性）
        results = _generate_graphic(
            prompt, width, height, variants, assets_dir, input_fp, manifest)

    _save_manifest(project_id, manifest)
    logger.info(
        f"generation: {gen_type} x{len(results)} prompt={prompt[:30]!r} → {project_id}"
    )
    return results


def _register_result(
    manifest, results, asset_id, asset_type, out_file, width, height, dur,
    model, provider_id, prompt, input_fp, variant,
) -> None:
    out_fp = _fingerprint(Path(out_file).read_bytes())
    results.append(GenerationResult(
        asset_id=asset_id, variant=variant, type=asset_type, file=str(out_file),
        width=width, height=height,
        input_fingerprint=input_fp, output_fingerprint=out_fp,
        provider_id=provider_id,
    ))
    license_note = (
        f"generated by {provider_id}" + (f" (model={model})" if model else ""))
    manifest.append({
        "id": asset_id, "type": asset_type, "file": str(out_file),
        "duration": dur, "width": width, "height": height, "fps": 30.0,
        "source": "generated", "origin": "generated",
        "license_note": license_note, "prompt": prompt,
        "generation_request_id": f"genreq-{input_fp}",
        "provider_id": provider_id, "model": model,
        "input_fingerprint": input_fp, "output_fingerprint": out_fp,
        "variant": variant, "created_at": time.time(),
    })


def _generate_graphic(prompt, width, height, variants, assets_dir, input_fp, manifest):
    """graphic → Graphics Runtime 程序化视觉卡（自有 renderer，确定性）。"""
    from video_director.graphics import compiler as vd_compiler
    from video_director.graphics import render as vd_gfx_render

    results: List[GenerationResult] = []
    for v in variants:
        program = _build_single_scene_program(prompt, v, width, height)
        html = vd_compiler.compile_to_html(program)
        asset_id = f"gen-{uuid.uuid4().hex[:8]}"
        out_file = assets_dir / f"{asset_id}.png"
        vd_gfx_render.render_still(html, str(out_file), width, height, frame=0)
        _register_result(manifest, results, asset_id, "image", out_file, width, height,
                         None, None, "graphics-runtime", prompt, input_fp, v)
    return results


def _generate_image_or_fallback(project_id, prompt, width, height, variants,
                                assets_dir, input_fp, manifest):
    """image → AI 生图（roles["image"]）；无 key/失败 → 兜底程序化视觉卡。"""
    results: List[GenerationResult] = []
    try:
        imgs = generate_image_variants(
            prompt, width, height, count=len(variants),
            variant=variants[0] if len(variants) == 1 else None)
        for i, item in enumerate(imgs):
            asset_id = f"gen-{uuid.uuid4().hex[:8]}"
            out_file = assets_dir / f"{asset_id}.png"
            out_file.write_bytes(item["data"])
            _register_result(manifest, results, asset_id, "image", out_file, width, height,
                             None, item["model"], "image-llm", prompt, input_fp,
                             item["variant"])
        if results:
            return results
        return _generate_graphic(prompt, width, height, variants, assets_dir,
                                 input_fp, manifest)
    except GenerationError as exc:
        if exc.code == "IMAGE_PROVIDER_UNAVAILABLE":
            logger.info(f"生图 provider 未配置，兜底程序化视觉卡: {exc}")
            return _generate_graphic(prompt, width, height, variants, assets_dir,
                                     input_fp, manifest)
        # IMAGE_GENERATION_FAILED：AI 生图失败 → 诚实兜底程序化，但记警告
        logger.warning(f"AI 生图失败，兜底程序化视觉卡: {exc}")
        return _generate_graphic(prompt, width, height, variants, assets_dir,
                                 input_fp, manifest)


def _generate_video(project_id, prompt, width, height, variants, assets_dir,
                    input_fp, manifest):
    """video → AI 生视频（roles["video"]，异步任务）；无 key 诚实报错（不 fake）。"""
    cfg = _role_cfg("video")
    if not (cfg or {}).get("api_key"):
        raise GenerationError(
            "VIDEO_PROVIDER_UNAVAILABLE",
            "生视频模型未配置（roles.video.api_key 为空）；请按供应商文档填写模型/端点",
        )
    model = cfg.get("model") or ""
    base_url = cfg.get("base_url") or ""
    if not model or not base_url:
        raise GenerationError(
            "VIDEO_PROVIDER_UNAVAILABLE",
            "生视频 model/base_url 未配置完整（供应商特定：Veo/Kling/Wan 兼容端点）",
        )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=base_url)
    except Exception as exc:
        raise GenerationError("VIDEO_PROVIDER_UNAVAILABLE", f"openai 库不可用: {exc}") from exc

    results: List[GenerationResult] = []
    for v in variants:
        asset_id = f"gen-{uuid.uuid4().hex[:8]}"
        out_file = assets_dir / f"{asset_id}.mp4"
        try:
            url = _create_and_poll_video(client, model, prompt, width, height)
        except Exception as exc:
            raise GenerationError(
                "VIDEO_GENERATION_FAILED",
                f"生视频调用失败（model={model}）：{exc}",
            ) from exc
        out_file.write_bytes(_download_bytes(url))
        _register_result(manifest, results, asset_id, "video", out_file, width, height,
                         5.0, model, "video-llm", prompt, input_fp, v)
    return results


def _create_and_poll_video(client, model, prompt, width, height, timeout: float = 600.0):
    """OpenAI 兼容视频生成：POST video/generations → 轮询 → 返回 mp4 url。

    供应商协议可能不同（任务 id 字段/状态字段/轮询端点），失败向上抛诚实错误。
    """
    import time as _time
    size = _image_size(width, height)
    try:
        create = client.post(
            "/video/generations",
            json={"model": model, "prompt": prompt, "size": size},
        )
    except Exception:
        # 部分供应商用 /videos 单例端点
        create = client.post(
            "/videos",
            json={"model": model, "prompt": prompt, "size": size},
        )
    data = create.json() if hasattr(create, "json") else create
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    job_id = None
    if isinstance(data, dict):
        job_id = data.get("id")
    elif isinstance(data, list) and data:
        job_id = data[0].get("id")
    if not job_id:
        raise GenerationError(
            "VIDEO_GENERATION_FAILED", f"生视频任务无 id：{str(data)[:200]}")

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        job = client.get(f"/video/generations/{job_id}")
        js = job.json() if hasattr(job, "json") else job
        if isinstance(js, dict) and "data" in js:
            js = js["data"]
        if isinstance(js, list) and js:
            js = js[0]
        status = (js.get("status") if isinstance(js, dict) else None)
        if status in ("completed", "succeeded", "done"):
            url = None
            if isinstance(js, dict):
                url = js.get("url") or js.get("output") or js.get("video_url")
            if isinstance(url, dict):
                url = url.get("url")
            if not url:
                raise GenerationError(
                    "VIDEO_GENERATION_FAILED", f"生视频任务完成但无 url：{str(js)[:200]}")
            return url
        if status in ("failed", "error", "cancelled"):
            raise GenerationError("VIDEO_GENERATION_FAILED", f"生视频任务失败：{str(js)[:200]}")
        _time.sleep(5.0)
    raise GenerationError("VIDEO_GENERATION_FAILED",
                          f"生视频任务超时（>{int(timeout)}s）")


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