"""Numeric JSON contract regressions for editor commands.

JSON booleans are Python integers. Timeline APIs must reject them instead of
silently interpreting true as 1 second and persisting a user-visible edit.
"""
from __future__ import annotations

import pytest

from video_director import editor
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR, WatermarkLayer
from video_director.visual_semantics import Keyframe, KeyframeTrack


def _contract_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "boolean-time-contract"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    image = tmp_path / "contract-image.png"
    image.write_bytes(b"contract fixture; no render in this test")
    comp = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=10.0, text="numeric contract",
            layers=[Layer(
                type=LayerType.image, asset_id="contract-image", start=0.0, duration=10.0,
                keyframes=[KeyframeTrack(
                    property="opacity", keyframes=[Keyframe(at=1.0, value=0.5)],
                )],
            )],
        )],
        assets=[AssetRef(
            id="contract-image", type="image", file=str(image), width=40, height=40,
        )],
    )
    save_composition(project_id, comp)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plans


def _apply(project_id, command, target, payload):
    return execute_command(project_id, {
        "command": command, "targetId": target, "payload": payload,
        "expectedVersion": 1,
    })


def test_boolean_values_are_rejected_for_temporal_and_numeric_editor_contracts(tmp_path, monkeypatch):
    project_id, plans = _contract_project(tmp_path, monkeypatch)
    attempts = [
        ("add_keyframe", "S01#0", {"property": "opacity", "at": True, "value": 0.2}),
        ("remove_keyframe", "S01#0", {"property": "opacity", "at": True}),
        ("add_audio_keyframe", "bgm", {"at": True, "volume": 0.6}),
        ("add_audio_keyframe", "bgm", {"at": 1.0, "volume": True}),
        ("add_broll", "S01", {"assetId": "contract-image", "assetType": "video", "sourceOut": True}),
        ("add_broll", "S01", {"assetId": "contract-image", "assetType": "video", "sourceOut": False, "duration": 2.0}),
        ("add_broll", "S01", {"assetId": "contract-image", "assetType": "video", "duration": True}),
        ("apply_camera", "S01", {"preset": "push_in", "duration": True}),
        ("apply_morph", "S01", {"fromBlockId": "ui.button", "toBlockId": "ui.arrow", "duration": True}),
        ("set_audio_fade", "bgm", {"fadeIn": True}),
        ("set_audio_fade", "bgm", {"fadeOut": float("inf")}),
        ("audio_mix", "", {"tracks": {"bgm": {"fadeOut": True}}}),
        ("audio_mix", "", {"tracks": {"bgm": {"volume": True}}}),
        ("set_audio_volume", "bgm", {"volume": True}),
        ("set_original_audio_volume", "original", {"volume": True}),
        ("set_opacity", "S01#0", {"opacity": True}),
        ("set_transform", "S01#0", {"scale": True}),
        ("move", "S01", {"toIndex": True}),
        ("apply_camera", "S01", {"preset": "push_in", "strength": True}),
        ("configure_pip", "S01#0", {"action": "set", "source": "contract-image", "radius": True}),
        ("apply_camera", "S01", {"keyframes": [{"at": True, "position": [0.5, 0.5, 1.0], "target": [0.5, 0.5, 0.0]}]}),
        ("set_original_audio", "original", {"assetId": "contract-image", "start": True, "duration": 2.0}),
        ("set_original_audio", "original", {"assetId": "contract-image", "start": 0.0, "duration": True}),
        ("set_original_audio", "original", {"assetId": "contract-image", "start": 0.0, "duration": 2.0, "sourceIn": True}),
        ("set_original_audio", "original", {"assetId": "contract-image", "start": 0.0, "duration": 2.0, "sourceIn": 0.0, "sourceOut": True}),
        ("set_original_audio", "original", {"fadeIn": True}),
        ("set_original_audio", "original", {"volume": True}),
    ]

    for command, target, payload in attempts:
        result = _apply(project_id, command, target, payload)
        assert result.status == "REJECTED", (command, payload, result.status, result.errors)
        assert result.errors, (command, payload)
        assert result.errors[0]["code"] in {"INVALID_PAYLOAD", "INVALID_PARAMETER"}, (
            command, payload, result.errors,
        )

    unchanged = load_composition(project_id)
    assert len(unchanged.scenes) == 1
    assert unchanged.scenes[0].duration == 10.0
    assert len(unchanged.scenes[0].layers) == 1
    assert [
        (track.property, [(frame.at, frame.value) for frame in track.keyframes])
        for track in unchanged.scenes[0].layers[0].keyframes
    ] == [("opacity", [(1.0, 0.5)])]
    assert unchanged.audio == []
    assert unchanged.camera_tracks == []
    assert unchanged.continuity_links == []
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""



def test_watermark_and_transition_timing_reject_booleans_without_mutation(tmp_path, monkeypatch):
    project_id, plans = _contract_project(tmp_path, monkeypatch)
    comp = load_composition(project_id)
    comp.watermarks = [
        WatermarkLayer(
            id="wm-contract", kind="text", text="timing",
            start=1.0, duration=2.0,
        )
    ]
    before = comp.model_dump()

    attempts = [
        (editor._cmd_set_watermark, ("", {"kind": "text", "text": "new", "createNew": True, "start": True, "duration": 2.0})),
        (editor._cmd_move_watermark, ("watermark#0", {"start": True, "duration": 2.0})),
        (editor._cmd_move_watermark, ("watermark#0", {"start": 1.0, "duration": True})),
        (editor._cmd_trim_watermark, ("watermark#0", {"start": True, "duration": 1.0})),
        (editor._cmd_trim_watermark, ("watermark#0", {"start": 1.0, "duration": True})),
        (editor._cmd_set_transition_out, ("S01", {"type": "crossfade", "duration": True})),
        (editor._cmd_set_transition_out, ("S01", {"type": "fade", "duration": float("nan")})),
    ]

    for command, (target, payload) in attempts:
        with pytest.raises(editor.EditorError):
            command(comp, target, payload)

    assert comp.model_dump() == before
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""
