"""§63 target 定位契约归一化：LLM 提案图层级命令收场景级 id → 补 #0 首层。

背景（真实事故）：LLM 给 replace_motion 生成场景级 targetId（无 #idx），
apply 走 editor._find_layer 抛 INVALID_TARGET，导致 shotcraft E2E ai_apply_revised
失败。根因=提案链未闭环 §63 的 target 定位契约。修复=propose 后统一归一化。
"""

from video_director.proposal import _normalize_layer_targets


def test_layer_level_command_gets_hash_zero():
    patches = [{"command": "replace_motion", "targetId": "scene-hooka",
                "payload": {"motionRecipeId": "motion.scale-punch"}}]
    out = _normalize_layer_targets(patches, {})
    assert out[0]["targetId"] == "scene-hooka#0"


def test_scene_level_command_untouched():
    patches = [{"command": "set_duration", "targetId": "scene-hooka",
                "payload": {"duration": 3.0}}]
    out = _normalize_layer_targets(patches, {})
    assert out[0]["targetId"] == "scene-hooka"  # 场景级命令不加 #0


def test_already_has_index_untouched():
    patches = [{"command": "trim", "targetId": "scene-hooka#2",
                "payload": {"sourceIn": 0.5, "sourceOut": 2.0}}]
    out = _normalize_layer_targets(patches, {})
    assert out[0]["targetId"] == "scene-hooka#2"


def test_empty_target_untouched():
    patches = [{"command": "replace_source", "targetId": "", "payload": {}}]
    out = _normalize_layer_targets(patches, {})
    assert out[0]["targetId"] == ""