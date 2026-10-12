"""Real MP4 render/decode matrix for every supported native style contract."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_studio_browser_flow import _test_composition

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lYkAAAAASUVORK5CYII="
)

NATIVE_STYLES = [
    "style.documentary-calm",
    "style.knowledge-explainer",
    "style.energetic-social",
    "style.cinematic-story",
]


@pytest.mark.parametrize("style_id", NATIVE_STYLES)
def test_every_native_style_renders_decodable_mp4_without_mutating_project(
    style_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Each case gets a fully isolated project root and sample-output directory.
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    from video_director import asset_catalog, config, editor, store
    from video_director.style_sample import render_style_sample

    slug = style_id.removeprefix("style.")
    project_id = "style-matrix-" + slug
    asset_root = asset_catalog.library_root() / "local"
    asset_root.mkdir(parents=True, exist_ok=True)
    still = asset_root / (project_id + ".png")
    still.write_bytes(_PNG)

    store.create_project(project_id, "Style matrix " + slug, {"name": slug})
    editor.save_composition(project_id, _test_composition(still))
    project_dir = config.project_dir(project_id)
    manifest = project_dir / "assets" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps([{
            "id": "matrix-still",
            "type": "image",
            "file": str(still.resolve()),
            "width": 1,
            "height": 1,
            "source": "uploaded",
        }]),
        encoding="utf-8",
    )

    before = hashlib.sha256(
        editor.load_composition(project_id).model_dump_json().encode("utf-8")
    ).hexdigest()
    output_root = tmp_path / "style-samples"
    result = render_style_sample(project_id, style_id, output_root)

    assert result["ok"] is True, result
    assert result["style_id"] == slug
    assert result["runtime"]["sample_render"] == "PASS", result
    assert result["runtime"]["mp4_decode"] == "PASS", result
    assert result["runtime"]["status"] == "PARTIAL", result
    assert "字幕位置" in result["runtime"]["applied_parameters"], result
    assert "字幕字号" in result["runtime"]["applied_parameters"], result

    sample_id = result["sample_id"]
    assert sample_id and sample_id.isalnum()
    mp4 = output_root / (sample_id + ".mp4")
    assert mp4.is_file() and mp4.stat().st_size > 1024
    assert result["runtime"]["sample_mp4"]["decode"] == "PASS", result
    assert result["runtime"]["sample_mp4"]["duration_seconds"] > 0

    after = hashlib.sha256(
        editor.load_composition(project_id).model_dump_json().encode("utf-8")
    ).hexdigest()
    assert after == before, "sample rendering mutated the persisted editable CompositionIR"
