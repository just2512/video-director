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
