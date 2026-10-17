"""差距 7（仓库审计）：版权放行闸门。

BLOCKED 硬阻止；UNKNOWN/REVIEW 警告放行（Jerry 裁决，2026-10-17）。
授权状态从 Library commercial_status 归一流入 AssetRef.rights_status。
"""
from video_director import asset_catalog, assets as assets_mod, config, confirm, editor
from video_director.models import CompositionIR


def _seed_ir(tmp_path, monkeypatch, rights_by_id):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    pid = "com-gate"
    base = config.project_dir(pid)
    (base / "plans").mkdir(parents=True, exist_ok=True)
    ir = CompositionIR.model_validate({
        "project": {"duration": 10, "fps": 30, "width": 1080, "height": 1920},
        "scenes": [{"id": "S01", "start": 0, "duration": 10, "text": "x",
                    "layers": [{"type": "image", "asset_id": "a1",
                                "start": 0, "duration": 10}]}],
        "assets": [
            {"id": aid, "type": "image", "file": f"/{aid}.png",
             "rights_status": rs} for aid, rs in rights_by_id.items()
        ],
        "audio": [],
    })
    editor.save_composition(pid, ir)
    (base / "plans" / "revisions.jsonl").write_text("", encoding="utf-8")
    return pid


def test_commercial_release_gate_classifies(tmp_path, monkeypatch):
    pid = _seed_ir(tmp_path, monkeypatch, {
        "a1": "BLOCKED", "a2": "UNKNOWN", "a3": "REVIEW", "a4": "CLEAR",
    })
    g = confirm.commercial_release_gate(pid)
    assert g["blocked"] == ["a1"]
    assert set(g["unknown"]) == {"a2", "a3"}


def test_library_semantics_maps_user_supplied_to_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00" * 16)
    catalog = asset_catalog._load_catalog()
    catalog["local-abc"] = {
        "asset_id": "local-abc", "type": "video", "source": "user",
        "file": str(media), "commercial_status": "USER_SUPPLIED",
        "description": "咖啡", "tags": ["咖啡"],
        "content_hash": "abc",
    }
    asset_catalog._save_catalog(catalog)

    sem = assets_mod._library_semantics(str(media))
    assert sem["rights_status"] == "CLEAR"  # USER_SUPPLIED 视为可商用
    assert sem["description"] == "咖啡"


def test_library_semantics_passthrough_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00" * 16)
    catalog = asset_catalog._load_catalog()
    catalog["local-abc"] = {
        "asset_id": "local-abc", "type": "video", "source": "stock",
        "file": str(media), "commercial_status": "BLOCKED",
        "description": "", "tags": [], "content_hash": "abc",
    }
    asset_catalog._save_catalog(catalog)
    assert assets_mod._library_semantics(str(media))["rights_status"] == "BLOCKED"