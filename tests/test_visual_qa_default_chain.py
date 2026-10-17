"""差距 4：视觉审核进默认链（provider 分层，不需要 Twelve Labs key）。

- frame_vision_visual_qa 复用 vision 角色（FrameVisionProvider，Jerry 已配置）
- verify_scene_candidates：Twelve Labs 优先 → frame-vision 兜底 → UNKNOWN
- 生成补位产物 CONFLICT → 拒收 + NEEDS_HUMAN（不静默进片）
"""
from types import SimpleNamespace

from video_director import visual_qa as vqa
from video_director import director_agent as agent


def test_frame_vision_qa_uses_vision_role(monkeypatch, tmp_path):
    """frame_vision_visual_qa 走 vision 角色（可用时），输出同构 VisualEvidence。"""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    class FakeProvider:
        _model = "test-vlm"
        _base_url = "http://127.0.0.1:9"
        _api_key = "k"

        @staticmethod
        def available():
            return True

    monkeypatch.setattr("video_director.vision.FrameVisionProvider", lambda: FakeProvider())
    monkeypatch.setattr("video_director.vision.extract_frames",
                        lambda *a, **k: [str(tmp_path / "f.jpg")])
    (tmp_path / "f.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)

    captured = {}

    class FakeResp:
        def read(self):
            import json as _j
            return _j.dumps({"choices": [{"message": {"content":
                '{"verdict": "DIRECT_MATCH", "evidence": "画面出现咖啡杯", '
                '"subjects": ["咖啡杯"], "actions": [], "on_screen_text": [], '
                '"time_ranges": [[0, 3]]}'}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(vqa.urllib.request, "urlopen", fake_urlopen)

    ev = vqa.frame_vision_visual_qa("S01", "咖啡特写", "a1", "/tmp/x.mp4")
    assert ev.verdict == "DIRECT_MATCH"
    assert ev.provider == "frame-vision"
    assert ev.subjects == ("咖啡杯",)
    assert "chat/completions" in captured["url"]


def test_frame_vision_qa_honest_when_vision_unconfigured(monkeypatch, tmp_path):
    """vision 角色不可用 → PROVIDER_MISSING（诚实，不假装审核过）。"""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    class FakeProvider:
        _model = _base_url = _api_key = ""
        @staticmethod
        def available():
            return False

    monkeypatch.setattr("video_director.vision.FrameVisionProvider", lambda: FakeProvider())
    try:
        vqa.frame_vision_visual_qa("S01", "x", "a1", "/tmp/x.mp4")
    except vqa.VisualAnalysisError as exc:
        assert exc.code == "PROVIDER_MISSING"
    else:
        raise AssertionError("vision 未配置应诚实报错")


def test_verify_scene_candidates_falls_back_to_frame_vision(monkeypatch, tmp_path):
    """Twelve Labs 不可用（未配 key）→ 自动落 frame-vision，不是 UNKNOWN 摆烂。"""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    # Marengo 未配 → rerank UNKNOWN（candidates 原序）
    monkeypatch.setattr(vqa, "semantic_rerank",
                        lambda text, cands, limit=3: (list(cands), {"status": "UNKNOWN"}))

    def pegasus_fail(*a, **k):
        raise vqa.VisualAnalysisError("PROVIDER_MISSING", "未配置 visual_qa key")

    monkeypatch.setattr(vqa, "pegasus_visual_qa", pegasus_fail)

    called = {}

    def fake_frame_qa(scene_id, scene_text, asset_id, asset_file):
        called["ok"] = True
        return vqa.VisualEvidence(scene_id=scene_id, asset_id=asset_id,
                                  verdict="SUPPORTING", provider="frame-vision")

    monkeypatch.setattr(vqa, "frame_vision_visual_qa", fake_frame_qa)

    out = vqa.verify_scene_candidates(
        "S01", "咖啡", [{"asset_id": "a1", "file": "/a1.mp4"}])
    assert called.get("ok"), "未回退到 frame-vision"
    assert out["verdicts"][0]["provider"] == "frame-vision"


def test_generated_clip_conflict_rejected_and_marked(tmp_path, monkeypatch):
    """生成补位画面 CONFLICT → 从 assignments/all_assets 移除 + 候选 NEEDS_HUMAN。"""
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    monkeypatch.setattr(agent.assets_mod, "build_manifest",
                        lambda v, img, asset_metadata=None: ({}, []))
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
        return [SimpleNamespace(asset_id="any", file=str(png), type=gen_type,
                                width=100, height=100)]

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    import video_director.visual_qa as vqa_mod

    def fake_qa(scene_id, scene_text, asset_id, asset_file):
        return vqa_mod.VisualEvidence(scene_id=scene_id, asset_id=asset_id,
                                      verdict="CONFLICT", provider="frame-vision",
                                      evidence="画面与场景意思冲突")

    monkeypatch.setattr(vqa_mod, "frame_vision_visual_qa", fake_qa)
    saved = []
    monkeypatch.setattr(vqa_mod, "save_scene_evidence",
                        lambda pid, ev: saved.append(ev) or tmp_path / "r.json")

    state = {
        "project_id": "p",
        "video_files": [],
        "image_files": [],
        "constraints": {"visual_source": "auto", "allow_stock": True},
        "narration_scenes": [{"id": "S01", "text": "咖啡特写", "start": 0.0, "duration": 1.0}],
        "scene_plan": {"scenes": [{
            "id": "S01", "selected_candidate_id": "c1",
            "candidates": [{
                "id": "c1", "approach": "concept", "generation_type": "image",
                "generation_prompt": "一杯咖啡",
            }],
        }]},
    }
    agent._tool_material({"match_materials_to_script": True,
                          "width": 100, "height": 100}, state)

    assert "S01" not in state.get("assignments", {}), "CONFLICT 画面不应留在 assignments"
    cand = state["scene_plan"]["scenes"][0]["candidates"][0]
    assert cand["status"] == "NEEDS_HUMAN", f"候选应标 NEEDS_HUMAN: {cand.get('status')}"
    assert saved, "QA 证据应落盘"