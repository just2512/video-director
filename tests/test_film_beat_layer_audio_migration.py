"""Composition Beat layers and audio must become real FilmDocument clips."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_beat_layers_and_audio_are_migrated_and_tagged_without_duplicate_global_audio(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "beat-layer-audio-migration"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "base.png").write_bytes(b"not-decoded-by-migration")
    (assets / "overlay.png").write_bytes(b"not-decoded-by-migration")
    (assets / "effect.wav").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-base", "file": "assets/base.png", "type": "image"},
        {"id": "asset-overlay", "file": "assets/overlay.png", "type": "image"},
        {"id": "asset-sfx", "file": "assets/effect.wav", "type": "audio"},
    ]), encoding="utf-8")

    global_sfx = {
        "id": "sfx-shared", "type": "sfx", "asset_id": "asset-sfx",
        "start": 0.2, "duration": 0.3, "volume": 0.6, "source_in": 0,
    }
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "scene-beats", "start": 0, "duration": 1, "text": "beat test",
            "layers": [{
                "id": "base-layer", "type": "image", "asset_id": "asset-base",
                "start": 0, "duration": 1,
            }],
            "beats": [{
                "id": "beat-hook", "role": "hook",
                "semantic_anchors": [{"id": "anchor-hook", "kind": "moment", "label": "开场强调"}],
                "layers": [{
                    "id": "beat-overlay", "type": "image", "asset_id": "asset-overlay",
                    "start": 0.25, "duration": 0.5, "fit_mode": "contain",
                }],
                "audio": [
                    global_sfx,
                    {**global_sfx, "volume": 0.2},
                ],
            }],
        }],
        "audio": [global_sfx],
    })

    result = fd.build_film_from_composition(project_id, ir)
    clips = [
        (track, clip)
        for track in result["document"]["tracks"]
        for clip in track["clips"]
    ]
    beat_layer_matches = [
        (track, clip) for track, clip in clips
        if clip["attrs"].get("data-source-id") == "beat-overlay"
    ]
    assert len(beat_layer_matches) == 1
    overlay_track, overlay_clip = beat_layer_matches[0]
    assert overlay_clip["attrs"]["data-source-beat-ids"] == '["beat-hook"]'
    assert overlay_clip["attrs"]["data-source-beat-roles"] == '["hook"]'
    assert json.loads(overlay_clip["attrs"]["data-source-semantic-anchors"])[0]["id"] == "anchor-hook"
    assert overlay_clip["attrs"]["data-source-beat-layer"] == "true"
    assert float(overlay_clip["attrs"]["at"]) == 0.25
    assert "object-fit:contain" in overlay_clip["attrs"]["style"]

    audio_matches = [
        clip for _, clip in clips
        if clip["attrs"].get("data-role") == "sfx" and (
            clip["attrs"].get("data-source-id") == "sfx-shared"
            or clip["attrs"].get("id", "").startswith("audio-beat-beat-hook-sfx-shared")
        )
    ]
    # Equal ID/content merges despite the normalized top-level track_id. A
    # same-ID clip with different volume is preserved as a second real clip.
    assert len(audio_matches) == 2
    original = next(clip for clip in audio_matches if clip["attrs"].get("data-source-id") == "sfx-shared")
    distinct = next(clip for clip in audio_matches if clip is not original)
    assert original["attrs"]["data-source-beat-ids"] == '["beat-hook"]'
    assert float(original["attrs"]["at"]) == 0.2
    assert float(original["attrs"]["volume"]) == 0.6
    assert float(distinct["attrs"]["volume"]) == 0.2
    assert any("ID 冲突但内容不同" in warning for warning in result["warnings"])
    assert not any("beats 分层内容未迁移" in warning for warning in result["warnings"])
