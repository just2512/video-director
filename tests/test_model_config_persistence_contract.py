"""Regression tests for partial model-role updates and local credential retention."""

import json
import importlib
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from video_director import config, model_config


@pytest.fixture
def isolated_model_config(tmp_path, monkeypatch):
    path = tmp_path / "model_config.json"
    monkeypatch.setattr(model_config, "_config_path", lambda: path)
    monkeypatch.setattr(model_config, "_cache", None)
    yield path
    model_config._cache = None


def test_partial_role_update_preserves_other_saved_api_keys(isolated_model_config):
    path = isolated_model_config
    model_config.save_all({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm-old"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image-old"},
        "video": {"api_key": "local-video-secret", "base_url": "https://video.example/v1", "model": "video-old"},
    })
    merged = model_config.merge_role_updates({
        "llm": {"api_key": "__KEEP__", "base_url": "https://llm.example/v2", "model": "llm-new"},
    })
    model_config.save_all(merged)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "local-llm-secret"
    assert saved["llm"]["model"] == "llm-new"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["video"]["api_key"] == "local-video-secret"


def test_partial_import_preserves_roles_not_present_in_import(isolated_model_config):
    path = isolated_model_config
    model_config.save_all({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
        "tts_azure": {"api_key": "local-azure-secret", "base_url": "", "model": "eastasia"},
    })
    imported = model_config.merge_role_updates({
        "image": {"api_key": "__KEEP__", "base_url": "https://imported-image.example/v1", "model": "image-imported"},
    })
    model_config.save_all(imported)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved) == {"llm", "image", "tts_azure"}
    assert saved["llm"]["api_key"] == "local-llm-secret"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["tts_azure"]["api_key"] == "local-azure-secret"
    assert saved["image"]["base_url"] == "https://imported-image.example/v1"


def test_explicit_empty_key_clears_only_the_selected_role(isolated_model_config):
    path = isolated_model_config
    model_config.save_all({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
    })
    merged = model_config.merge_role_updates({
        "llm": {"api_key": "", "base_url": "https://llm.example/v2", "model": "llm"},
    })
    model_config.save_all(merged)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == ""
    assert saved["image"]["api_key"] == "local-image-secret"


def test_merge_rejects_non_object_payload(isolated_model_config):
    with pytest.raises(TypeError, match="configs"):
        model_config.merge_role_updates([])


def test_frontend_status_never_contains_saved_api_key(isolated_model_config):
    model_config.save_all({
        "llm": {"api_key": "must-never-leak-this-secret", "base_url": "https://llm.example/v1", "model": "llm"},
    })
    status = model_config.status_for_frontend()
    serialized = json.dumps(status, ensure_ascii=False)
    assert "must-never-leak-this-secret" not in serialized
    assert all("api_key" not in role for role in status)



def test_malformed_existing_config_is_not_overwritten(isolated_model_config):
    path = isolated_model_config
    path.write_text('{"llm": ', encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises(ValueError, match="拒绝覆盖"):
        model_config.save_role_updates({
            "image": {"api_key": "new-image-secret", "base_url": "https://image.example/v1", "model": "image"},
        })

    assert path.read_bytes() == original


def test_concurrent_partial_updates_keep_both_roles(isolated_model_config):
    from concurrent.futures import ThreadPoolExecutor

    path = isolated_model_config
    model_config.save_all({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
    })
    updates = [
        {"llm": {"api_key": "__KEEP__", "base_url": "https://llm.example/v2", "model": "llm-v2"}},
        {"image": {"api_key": "__KEEP__", "base_url": "https://image.example/v2", "model": "image-v2"}},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(model_config.save_role_updates, updates))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "local-llm-secret"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["llm"]["model"] == "llm-v2"
    assert saved["image"]["model"] == "image-v2"



def _post_settings_json(storage, monkeypatch, path, payload):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    model_config._cache = None
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None


def test_model_config_endpoint_partial_save_preserves_unsubmitted_roles(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    path = storage / "model_config.json"
    path.write_text(json.dumps({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
        "video": {"api_key": "local-video-secret", "base_url": "https://video.example/v1", "model": "video"},
    }), encoding="utf-8")

    result = _post_settings_json(storage, monkeypatch, "/api/model_config", {
        "configs": {
            "llm": {"api_key": "__KEEP__", "base_url": "https://llm.example/v2", "model": "llm-v2"},
        },
    })

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert saved["llm"]["api_key"] == "local-llm-secret"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["video"]["api_key"] == "local-video-secret"
    assert "local-llm-secret" not in json.dumps(result)


def test_settings_import_endpoint_preserves_unlisted_local_roles(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    path = storage / "model_config.json"
    path.write_text(json.dumps({
        "llm": {"api_key": "local-llm-secret", "base_url": "https://llm.example/v1", "model": "llm"},
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
        "tts_azure": {"api_key": "local-azure-secret", "base_url": "", "model": "eastasia"},
    }), encoding="utf-8")

    result = _post_settings_json(storage, monkeypatch, "/api/settings/import", {
        "models": [
            {"role": "image", "base_url": "https://imported-image.example/v1", "model": "image-imported"},
        ],
    })

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["imported_roles"] == ["image"]
    assert saved["llm"]["api_key"] == "local-llm-secret"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["tts_azure"]["api_key"] == "local-azure-secret"
    assert saved["image"]["base_url"] == "https://imported-image.example/v1"
    assert "local-llm-secret" not in json.dumps(result)



def test_legacy_llm_config_migration_preserves_other_local_roles(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    config_path = storage / "model_config.json"
    config_path.write_text(json.dumps({
        "image": {"api_key": "local-image-secret", "base_url": "https://image.example/v1", "model": "image"},
        "tts_azure": {"api_key": "local-azure-secret", "base_url": "", "model": "eastasia"},
    }), encoding="utf-8")
    legacy_path = tmp_path / "llm_config.json"
    legacy_path.write_text(json.dumps({
        "api_key": "legacy-llm-secret",
        "base_url": "https://legacy-llm.example/v1",
        "model": "legacy-llm",
    }), encoding="utf-8")
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    model_config._cache = None

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "LLM_CONFIG_FILE", legacy_path)
    web_server._load_llm_config()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "legacy-llm-secret"
    assert saved["image"]["api_key"] == "local-image-secret"
    assert saved["tts_azure"]["api_key"] == "local-azure-secret"
    assert model_config.get_role_config("llm")["api_key"] == "legacy-llm-secret"
    model_config._cache = None
