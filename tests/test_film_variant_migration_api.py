"""HTTP-level regression for previewing and initializing an explicit legacy Composition variant."""
from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from video_director import config, film_document as fd


def test_film_migration_api_requires_and_persists_explicit_variant_selection(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "film-migration-variant-http"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"test asset not decoded during migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    source = {
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 3},
        "assets": [{"id": "asset-still", "file": "assets/still.png", "type": "image"}],
        "scenes": [{
            "id": "legacy-scene", "start": 0, "duration": 3, "text": "fallback",
            "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 3}],
        }],
        "variants": [
            {"id": "cut-a", "version": 1, "metadata": {"name": "Cut A"}, "scenes": [{
                "id": "scene-a", "start": 0, "duration": 1, "text": "A",
                "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 1}],
            }]},
            {"id": "cut-b", "version": 2, "metadata": {"name": "Cut B"}, "scenes": [{
                "id": "scene-b", "start": 0, "duration": 2, "text": "B",
                "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 2}],
            }]},
        ],
    }
    source_path = root / "plans" / "composition.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        preview_url = base_url + "/api/editor/film?" + urlencode({"project_id": project_id})
        with urlopen(preview_url, timeout=10) as response:
            unselected = json.loads(response.read().decode("utf-8"))
        assert unselected["ok"] is True
        assert unselected["migration_available"] is False
        assert unselected["migration_error_code"] == "ACTIVE_VARIANT_REQUIRED"
        assert {item["id"] for item in unselected["variants"]} == {"cut-a", "cut-b"}

        selected_url = base_url + "/api/editor/film?" + urlencode({
            "project_id": project_id, "selected_variant_id": "cut-b",
        })
        with urlopen(selected_url, timeout=10) as response:
            selected = json.loads(response.read().decode("utf-8"))
        assert selected["migration_available"] is True
        assert selected["migration_preview"]["selected_variant_id"] == "cut-b"
        assert selected["migration_preview"]["duration"] == 2

        payload = {
            "project_id": project_id,
            "expected_composition_version": selected["source_composition_version"],
            "selected_variant_id": "cut-b",
            "accept_warnings": True,
        }
        request = Request(
            base_url + "/api/editor/film/initialize",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            created = json.loads(response.read().decode("utf-8"))
            assert response.status == 201
        assert created["migration"]["selected_variant_id"] == "cut-b"
        state = fd.read_project_film(project_id)
        track = next(item for item in state["doc"]["tracks"] if item["id"] == "track-main")
        clip = track["clips"][0]
        assert clip["attrs"]["data-source-variant-id"] == "cut-b"
        archive = json.loads((root / "migration" / "source-composition.json").read_text(encoding="utf-8"))
        assert {variant["id"] for variant in archive["variants"]} == {"cut-a", "cut-b"}
        assert archive.get("active_variant_id") is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_variant_migration_ui_requires_an_explicit_choice_when_no_active_variant_exists():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "if(!selectedVariantId)variantPicker+='<option value=\"\" disabled selected>请选择一个版本</option>';" in script
    assert "data.migration_error_code==='ACTIVE_VARIANT_REQUIRED'||data.migration_error_code==='ACTIVE_VARIANT_NOT_FOUND'" in script
    assert "S.migration=next;renderMigration();" in script
