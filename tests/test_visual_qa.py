"""Semantic Rerank / Visual QA tests.

所有测试不访问 Twelve Labs 网络；只验证 provider gate、payload、hard verdict
以及「不暴露 similarity score」这条产品契约。
"""

from __future__ import annotations

import json

from video_director import provider_matrix, visual_qa


def test_twelvelabs_roles_are_provider_ready_only_when_configured():
    cfg = {
        "api_key": "k",
        "base_url": "https://api.twelvelabs.io",
        "model": "marengo3.5",
    }
    st = provider_matrix.state(
        "semantic_rerank", "visual.semantic_retrieve", cfg
    )
    assert st.status == provider_matrix.READY

    cfg2 = {
        "api_key": "k",
        "base_url": "https://api.twelvelabs.io",
        "model": "pegasus1.5",
    }
    st2 = provider_matrix.state("visual_qa", "visual.qa", cfg2)
    assert st2.status == provider_matrix.READY


def test_twelvelabs_roles_missing_key_are_blocked():
    assert provider_matrix.state(
        "semantic_rerank",
        "visual.semantic_retrieve",
        {"api_key": "", "base_url": "https://api.twelvelabs.io", "model": "marengo3.5"},
    ).status == provider_matrix.MISSING
    assert provider_matrix.state(
        "visual_qa",
        "visual.qa",
        {"api_key": "", "base_url": "https://api.twelvelabs.io", "model": "pegasus1.5"},
    ).status == provider_matrix.MISSING


def test_pegasus_payload_uses_base64_string(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")

    class Probe:
        duration = 5.0

    monkeypatch.setattr(
        "video_director.prober.probe_media", lambda _: Probe()
    )
    monkeypatch.setattr(
        visual_qa,
        "_role",
        lambda role: {
            "api_key": "k",
            "base_url": "https://api.twelvelabs.io",
            "model": "pegasus1.5",
        },
    )
    seen = {}

    def fake_post(url, key, payload, timeout=120):
        seen["url"] = url
        seen["key"] = key
        seen["payload"] = payload
        return {
            "data": json.dumps({
                "verdict": "DIRECT_MATCH",
                "evidence": "画面中实际出现办公室人员开会。",
                "subjects": ["多人"],
                "actions": ["开会"],
                "on_screen_text": [],
                "time_ranges": [[0, 4]],
            }, ensure_ascii=False)
        }

    monkeypatch.setattr(visual_qa, "_post_json", fake_post)
    result = visual_qa.pegasus_visual_qa(
        "S01", "团队正在开会", "v1", str(video)
    )
    assert result.verdict == "DIRECT_MATCH"
    assert seen["url"].endswith("/v1.3/analyze")
    assert seen["payload"]["video"]["type"] == "base64_string"
    assert seen["payload"]["video"]["base_64_string"]
    assert "score" not in result.to_dict()
    assert "similarity" not in result.to_dict()


def test_semantic_rerank_never_persists_similarity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        visual_qa,
        "_role",
        lambda role: {
            "api_key": "k",
            "base_url": "https://api.twelvelabs.io",
            "model": "marengo3.5",
        },
    )
    monkeypatch.setattr(visual_qa, "marengo_embed_text", lambda _: [1.0, 0.0])

    def fake_video(path, asset_cache_key=""):
        return [1.0, 0.0] if str(path).endswith("a.mp4") else [0.0, 1.0]

    monkeypatch.setattr(visual_qa, "marengo_embed_video", fake_video)
    candidates = [
        {"asset_id": "b", "file": str(tmp_path / "b.mp4")},
        {"asset_id": "a", "file": str(tmp_path / "a.mp4")},
    ]
    ranked, meta = visual_qa.semantic_rerank("团队开会", candidates, limit=2)
    assert [x["asset_id"] for x in ranked] == ["a", "b"]
    assert meta["status"] == visual_qa.RERANK_READY
    assert all("score" not in x and "similarity" not in x for x in ranked)


def test_visual_qa_uses_hard_verdicts_not_numeric_order(monkeypatch, tmp_path):
    candidates = [
        {"asset_id": "a", "file": str(tmp_path / "a.mp4")},
        {"asset_id": "b", "file": str(tmp_path / "b.mp4")},
    ]
    monkeypatch.setattr(
        visual_qa,
        "semantic_rerank",
        lambda *args, **kwargs: (candidates, {"status": "READY", "provider": "twelve"}),
    )
    qa = iter([
        visual_qa.VisualEvidence(
            scene_id="S01", asset_id="a", verdict="SUPPORTING",
            provider="twelve", evidence="只是办公室远景。",
        ),
        visual_qa.VisualEvidence(
            scene_id="S01", asset_id="b", verdict="DIRECT_MATCH",
            provider="twelve", evidence="实际出现团队开会。",
        ),
    ])
    monkeypatch.setattr(visual_qa, "pegasus_visual_qa", lambda *args: next(qa))
    out = visual_qa.verify_scene_candidates(
        "S01", "团队正在开会", candidates, max_qa=2
    )
    assert out["selected_asset_id"] == "b"
    assert [v["verdict"] for v in out["verdicts"]] == [
        "DIRECT_MATCH", "SUPPORTING"
    ]
    assert all(
        "score" not in v and "similarity" not in v
        for v in out["verdicts"]
    )


def test_visual_qa_provider_failure_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(
        visual_qa,
        "_role",
        lambda role: {"api_key": "", "base_url": "https://api.twelvelabs.io", "model": "pegasus1.5"},
    )
    try:
        visual_qa.pegasus_visual_qa(
            "S01", "场景", "a", str(tmp_path / "missing.mp4")
        )
    except visual_qa.VisualAnalysisError as exc:
        assert exc.code == "PROVIDER_MISSING"
    else:
        raise AssertionError("无 provider 必须明确失败，不得伪造 UNKNOWN 成功")
