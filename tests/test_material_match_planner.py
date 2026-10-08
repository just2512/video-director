"""Regression tests for semantic material matching + all-uploaded-video utilization."""

from video_director.models import AssetRef
from video_director.planner import ContentMatchPlanner


def _scenes():
    return [
        {"id": f"S{i:02d}", "start": float(i * 4), "duration": 4.0, "text": f"场景{i}"}
        for i in range(1, 6)
    ]


def _assets():
    return {
        f"v{i}": AssetRef(
            id=f"v{i}", type="video", file=f"/tmp/clip{i}.mp4", duration=4.0
        )
        for i in range(1, 9)
    }


def test_content_match_preferred_asset_stays_first_when_all_videos_are_used():
    assets = _assets()
    scenes = _scenes()
    preferred = {f"S{i:02d}": f"v{i}" for i in range(1, 6)}

    ir = ContentMatchPlanner(preferred).plan(
        scenes=scenes,
        assets=assets,
        asset_order=list(assets.keys()),
        width=1080,
        height=1920,
        fps=30,
    )

    assert len(ir.scenes) == 5
    for sc in ir.scenes:
        assert sc.layers, sc.id
        assert sc.layers[0].asset_id == preferred[sc.id]

    used = {layer.asset_id for sc in ir.scenes for layer in sc.layers}
    assert used == set(assets)
