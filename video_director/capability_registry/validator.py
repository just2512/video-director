"""MPT 吸收方案 §5 Capability Registry 的 validator：命名 inputSchema 契约校验。

Tool Call 在进入 Command Bus 之前，先按 capability.inputSchema 指向的
命名 schema 校验 payload（§9：AI 不能盲改 Composition，必须 Server
Validation 通过后才形成 Command）。

命名 schema 表（与 manifest 中的 inputSchema 引用一一对应）：
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from video_director.capability_registry.schemas import FieldSpec, InputSchema

#: 命名 schema 表。字段约束对齐各 capability 的 payload 边界
#: （volume ∈ [0,2] 对齐 models.AudioTrack；opacity ∈ [0,1] 对齐 WatermarkLayer）。
SCHEMAS: Dict[str, InputSchema] = {
    "DescribeRequest": InputSchema("DescribeRequest", {
        "id": FieldSpec("str", required=True),
    }),
    "TargetRequest": InputSchema("TargetRequest", {
        "targetId": FieldSpec("str", required=True),
    }),
    "TrimRequest": InputSchema("TrimRequest", {
        "targetId": FieldSpec("str", required=True),
        "sourceIn": FieldSpec("float", minimum=0.0),
        "sourceOut": FieldSpec("float", minimum=0.0),
    }),
    "MoveRequest": InputSchema("MoveRequest", {
        "targetId": FieldSpec("str", required=True),
        "toIndex": FieldSpec("int", minimum=0),
    }),
    "SplitRequest": InputSchema("SplitRequest", {
        "targetId": FieldSpec("str", required=True),
        "ratio": FieldSpec("float", minimum=0.0, maximum=1.0, required=True),
    }),
    "RecipeReplaceRequest": InputSchema("RecipeReplaceRequest", {
        "targetId": FieldSpec("str", required=True),
        "recipeId": FieldSpec("str", required=True),
    }),
    "LayerAddRequest": InputSchema("LayerAddRequest", {
        "targetId": FieldSpec("str", required=True),
        "assetId": FieldSpec("str", required=True),
        "assetType": FieldSpec("enum:str", enum=frozenset({"video", "image"})),
        "sourceOut": FieldSpec("float", minimum=0.0),
    }),
    "LayerUpdateRequest": InputSchema("LayerUpdateRequest", {
        "targetId": FieldSpec("str", required=True),
        "speed": FieldSpec("float", minimum=0.1),
        "opacity": FieldSpec("float", minimum=0.0, maximum=1.0),
        "scale": FieldSpec("float", minimum=0.1, maximum=3.0),
    }),
    "OriginalAudioPolicy": InputSchema("OriginalAudioPolicy", {
        "mode": FieldSpec("enum:str", enum=frozenset({"preserve", "mute"}), required=True),
        "volume": FieldSpec("float", minimum=0.0, maximum=2.0),
        "duckAgainst": FieldSpec("str"),
    }),
    "VolumeRequest": InputSchema("VolumeRequest", {
        "targetId": FieldSpec("str"),
        "type": FieldSpec("enum:str", enum=frozenset({"bgm", "sfx", "ambient", "original", "narration"})),
        "volume": FieldSpec("float", minimum=0.0, maximum=2.0, required=True),
    }),
    "WatermarkRequest": InputSchema("WatermarkRequest", {
        "kind": FieldSpec("enum:str", enum=frozenset({"text", "image", "logo", "svg"})),
        "text": FieldSpec("str"),
        "assetId": FieldSpec("str"),
        "anchor": FieldSpec("enum:str", enum=frozenset({
            "top_left", "top_right", "bottom_left", "bottom_right", "center"})),
        "opacity": FieldSpec("float", minimum=0.0, maximum=1.0),
        "scale": FieldSpec("float", minimum=0.1, maximum=5.0),
        "start": FieldSpec("float", minimum=0.0),
        "duration": FieldSpec("float", minimum=0.0),
    }),
    "SubtitleGenerateRequest": InputSchema("SubtitleGenerateRequest", {
        "mode": FieldSpec("enum:str", enum=frozenset({"sentence", "word"})),
    }),
    "SubtitleStyleRequest": InputSchema("SubtitleStyleRequest", {
        "font": FieldSpec("str"),
        "size": FieldSpec("float", minimum=0.0),
        "color": FieldSpec("str"),
        "strokeColor": FieldSpec("str"),
        "strokeWidth": FieldSpec("int", minimum=0, maximum=12),
        "background": FieldSpec("str"),
        "position": FieldSpec("enum:str", enum=frozenset({"bottom", "center", "top"})),
        "animation": FieldSpec("enum:str", enum=frozenset({"none", "pop_spring", "fade"})),
    }),
    "SubtitleTimingRequest": InputSchema("SubtitleTimingRequest", {
        "targetId": FieldSpec("str"),
        "timing": FieldSpec("str"),
    }),
    "AssetImportRequest": InputSchema("AssetImportRequest", {
        "path": FieldSpec("str", required=True),
    }),
    "SearchRequest": InputSchema("SearchRequest", {
        "query": FieldSpec("str", required=True),
    }),
    "AssetResolveRequest": InputSchema("AssetResolveRequest", {
        "sceneId": FieldSpec("str"),
        "query": FieldSpec("str"),
    }),
    "AssetTimelineRequest": InputSchema("AssetTimelineRequest", {
        "targetId": FieldSpec("str", required=True),
        "assetId": FieldSpec("str", required=True),
        "assetType": FieldSpec("enum:str", enum=frozenset({"video", "image"})),
    }),
    "AssetReplaceRequest": InputSchema("AssetReplaceRequest", {
        "targetId": FieldSpec("str", required=True),
        "assetId": FieldSpec("str", required=True),
    }),
    "StockSearchRequest": InputSchema("StockSearchRequest", {
        "query": FieldSpec("str", required=True),
        "provider": FieldSpec("enum:str", enum=frozenset({"pexels", "pixabay", "coverr"})),
        "orientation": FieldSpec("enum:str", enum=frozenset({"landscape", "portrait", "square"})),
    }),
    "VoicePreviewRequest": InputSchema("VoicePreviewRequest", {
        "voiceId": FieldSpec("str"),
        "text": FieldSpec("str"),
    }),
    "MusicAddRequest": InputSchema("MusicAddRequest", {
        "assetId": FieldSpec("str"),
        "volume": FieldSpec("float", minimum=0.0, maximum=2.0),
    }),
    "GenerationRequest": InputSchema("GenerationRequest", {
        "type": FieldSpec("enum:str", enum=frozenset({"video", "image", "graphic", "sfx", "music"})),
        "prompt": FieldSpec("str", required=True),
        "targetId": FieldSpec("str"),
        "references": FieldSpec("str"),
        "firstFrame": FieldSpec("str"),
        "lastFrame": FieldSpec("str"),
    }),
    "MotionApplyRequest": InputSchema("MotionApplyRequest", {
        "targetId": FieldSpec("str", required=True),
        "motionRecipeId": FieldSpec("str", required=True),
    }),
    "StyleRequest": InputSchema("StyleRequest", {
        "styleId": FieldSpec("str", required=True),
    }),
    "TemplateRequest": InputSchema("TemplateRequest", {
        "templateId": FieldSpec("str", required=True),
    }),
    "ShotResolveRequest": InputSchema("ShotResolveRequest", {
        "sceneId": FieldSpec("str"),
        "query": FieldSpec("str"),
    }),
    "ShotApplyRequest": InputSchema("ShotApplyRequest", {
        "targetId": FieldSpec("str", required=True),
        "recipeId": FieldSpec("str", required=True),
    }),
    "RenderRequest": InputSchema("RenderRequest", {
        "profile": FieldSpec("enum:str", enum=frozenset({
            "1080p-landscape", "1080p-portrait", "1080p-square",
            "4k-landscape", "4k-portrait", "4k-square"})),
    }),
    "RevisionProposeRequest": InputSchema("RevisionProposeRequest", {
        "instruction": FieldSpec("str", required=True),
    }),
    "RevisionIdRequest": InputSchema("RevisionIdRequest", {
        "proposalId": FieldSpec("str", required=True),
    }),
    "ProjectCreateRequest": InputSchema("ProjectCreateRequest", {
        "ratio": FieldSpec("enum:str", enum=frozenset({"16:9", "9:16", "1:1"})),
    }),
    "ProjectReopenRequest": InputSchema("ProjectReopenRequest", {
        "projectId": FieldSpec("str", required=True),
    }),
    "ReviewRequest": InputSchema("ReviewRequest", {
        "projectId": FieldSpec("str", required=True),
        "issue": FieldSpec("str", required=True),
    }),
}


def validate_tool_call(cap_id: str, payload: dict) -> Tuple[bool, List[str]]:
    """按 capability.inputSchema 校验 payload。

    返回 (ok, errors)。capability 无 inputSchema 或未登记命名 schema
    （如 system.capabilities.list）→ 恒通过（只读能力不需要 payload 约束）。
    """
    from video_director.capability_registry.manifest import manifest as _mf
    cap = _mf().get(cap_id)
    if cap is None:
        return False, [f"未知能力 {cap_id!r}"]
    schema_name = cap.inputSchema
    if not schema_name:
        return True, []
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        # 命名 schema 未登记 → 不静默放行，报契约缺口（诚实失败）
        return False, [f"能力 {cap_id} 的 inputSchema {schema_name!r} 未登记契约"]
    errors = schema.validate(payload or {})
    return (len(errors) == 0), errors