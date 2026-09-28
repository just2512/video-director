"""MPT 吸收方案 §36：统一 PreviewService。

不各模块自己实现 play()——Voice/Music/Motion/Shot/Template/Generated
六类预览统一走本服务，返回 PreviewArtifact（kind + 引用 + 可播 URL/摘要）。

现有各预览端点（/api/voice_preview、/api/audio_preview、Motion CSS 预览）
保持兼容；PreviewService 是统一契约层，WebUI/ToolExecutor 都经由它取
预览产物（§56 UI 与 AI 共用同一 Inspector Contract 的预览面）。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger


class PreviewError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


#: 六类预览（§36 全枚举）
KINDS = ("voice", "music", "motion", "shot", "template", "generated_asset")


def preview(kind: str, ref: str, project_id: Optional[str] = None) -> dict:
    """统一预览入口。返回 PreviewArtifact（可播 URL / CSS 摘要 / HTML）。

    kind:
        voice          → /api/voice_preview?voice=<id>（TTS 试听，独立 Artifact §15）
        music          → /api/audio_preview?asset_id=<id>（BGM/SFX 试听）
        motion         → 前端 CSS 循环预览（返回 motion id，前端按既有通道渲染）
        shot           → 镜头配方卡摘要（registry describe，只读）
        template       → 模板卡摘要（registry describe，只读）
        generated_asset → /file?path=<项目内路径>（生成素材回放）
    """
    if kind not in KINDS:
        raise PreviewError("UNKNOWN_KIND", f"预览类型 {kind!r} 非法（{KINDS}）")
    if not ref:
        raise PreviewError("INVALID_REF", "预览引用为空")

    if kind == "voice":
        return {"kind": "voice", "ref": ref,
                "play_url": f"/api/voice_preview?voice={ref}",
                "artifactType": "VoicePreviewArtifact"}
    if kind == "music":
        return {"kind": "music", "ref": ref,
                "play_url": f"/api/audio_preview?asset_id={ref}",
                "artifactType": "MusicPreviewArtifact"}
    if kind == "motion":
        # Motion 预览在前端 CSS 循环通道（同 MotionRecipe，Preview/Final parity）
        return {"kind": "motion", "ref": ref,
                "render_hint": "css_loop", "artifactType": "MotionPreview"}
    if kind == "shot":
        from video_director.capability_registry import describe_capability
        cap = describe_capability("shot.resolve")
        return {"kind": "shot", "ref": ref,
                "resolve_via": "/api/registry/shots/resolve",
                "artifactType": "ShotCardPreview"}
    if kind == "template":
        return {"kind": "template", "ref": ref,
                "instantiate_via": "/api/templates/instantiate",
                "artifactType": "TemplateCardPreview"}
    # generated_asset：项目内文件回放（限 storage 内，/file 已做防穿越）
    if project_id is None:
        raise PreviewError("MISSING_PROJECT", "生成素材预览需 project_id")
    return {"kind": "generated_asset", "ref": ref,
            "play_url": f"/file?path={ref}",
            "artifactType": "GeneratedAssetPreview", "project_id": project_id}


def preview_tool_call(project_id: str, payload: dict) -> dict:
    """ToolExecutor 入口：tool=motion.preview / music.preview / stock.preview
    之外的统一 preview 能力（§36 与 §57 的 preview 系列对齐）。"""
    kind = payload.get("kind") or "motion"
    ref = payload.get("ref") or payload.get("targetId") or ""
    try:
        out = preview(kind, ref, project_id=project_id)
        return {"ok": True, "preview": out}
    except PreviewError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}