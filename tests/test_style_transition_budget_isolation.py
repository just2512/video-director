"""Per-project StyleContract validation must not mutate process-wide settings."""
from __future__ import annotations

from pathlib import Path

from tests.test_studio_browser_flow import _test_composition
from video_director import config, registry, style, validator


def test_style_transition_budget_is_request_scoped(monkeypatch, tmp_path: Path):
    # Mimic independent requests selecting different styles inside one Python worker.
    monkeypatch.setattr(config, "TRANSITION_MAX_MAJOR_EFFECTS", 3)
    ir = _test_composition(tmp_path / "fixture.png")

    cinematic, _ = style.select_style("历史故事", "cinematic-story")
    style.apply_to_ir(ir, cinematic, config)
    assert ir.style_id == "cinematic-story"
    assert config.TRANSITION_MAX_MAJOR_EFFECTS == 3

    energetic, _ = style.select_style("普通旁白", "energetic-social")
    style.apply_to_ir(None, energetic, config)
    assert config.TRANSITION_MAX_MAJOR_EFFECTS == 3

    # Pin registry semantics so the test exercises budget isolation, not registry data.
    monkeypatch.setattr(registry, "is_known_transition", lambda _name: True)
    monkeypatch.setattr(registry, "is_major_effect_transition", lambda name: name != "cut")
    monkeypatch.setattr(registry, "is_implemented_transition", lambda _name: True)
    ir.scenes[0].transition_out.type = "crossfade"
    assets = {asset.id: asset for asset in ir.assets}

    strict = validator.validate_composition(
        ir, assets, ir.project.duration, transition_budget=0,
    )
    assert any(issue.code == "TRANSITION_BUDGET_EXCEEDED" for issue in strict.issues)

    allowed = validator.validate_composition(
        ir, assets, ir.project.duration, transition_budget=1,
    )
    assert not any(issue.code == "TRANSITION_BUDGET_EXCEEDED" for issue in allowed.issues)
    # Validation of another request does not change the process default either.
    assert config.TRANSITION_MAX_MAJOR_EFFECTS == 3


def test_compose_project_persists_requested_style_id_before_render(
    monkeypatch, tmp_path: Path,
):
    # Stop at validation to verify the persisted IR without needing a rendered artifact.
    import json

    from video_director import pipeline
    from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR
    from video_director.validator import Issue, ValidationResult, Verdict

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    asset = AssetRef(
        id="style-id-still", type="image", file=str(tmp_path / "fixture.png"),
        duration=2.0, width=16, height=9,
    )
    monkeypatch.setattr(
        pipeline.assets_mod, "build_manifest",
        lambda *_args, **_kwargs: ({asset.id: asset}, [asset.id]),
    )
    monkeypatch.setattr(pipeline.store, "set_stage", lambda *_args, **_kwargs: None)

    class Planner:
        def plan(self, *, scenes, assets, asset_order, width, height, fps, **_kwargs):
            return CompositionIR(
                project=ProjectSpec(width=width, height=height, fps=fps, duration=2.0),
                scenes=[SceneIR(
                    id="S01", start=0.0, duration=2.0, text="风格来源验证",
                    layers=[Layer(
                        type=LayerType.image, asset_id=asset.id, start=0.0, duration=2.0,
                    )],
                )],
                assets=[asset],
            )

    seen = {}

    def stop_before_render(ir, _assets, _duration, _subtitle_entries, transition_budget=None):
        seen["style_id"] = ir.style_id
        seen["transition_budget"] = transition_budget
        return ValidationResult(Verdict.REJECT, [
            Issue(scene_id="-", code="TEST_STOP", message="stop before render"),
        ])

    monkeypatch.setattr(pipeline.validator, "validate_composition", stop_before_render)
    try:
        pipeline.compose_project(
            script="风格来源验证",
            video_files=[],
            image_files=["mocked-input.png"],
            project_id="style-id-persist",
            planner=Planner(),
            narration_scenes=[{"id": "S01", "start": 0.0, "duration": 2.0, "text": "风格来源验证"}],
            transition_budget=2,
            style_id="knowledge-explainer",
            width=16,
            height=9,
            fps=10,
        )
    except pipeline.PipelineError as exc:
        assert "IR 校验失败" in str(exc)
    else:
        raise AssertionError("the fixture validator must stop before rendering")

    plans = tmp_path / "storage" / "style-id-persist" / "plans"
    persisted = json.loads((plans / "composition.json").read_text(encoding="utf-8"))
    assert seen == {"style_id": "knowledge-explainer", "transition_budget": 2}
    assert persisted["style_id"] == "knowledge-explainer"
