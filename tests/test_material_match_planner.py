"""Regression tests for semantic material matching + all-uploaded-video utilization."""

from video_director.models import AssetRef
from video_director.planner import ContentMatchPlanner


def _scenes():
    # start 从 0 起算（对齐 narration.py 生产链路的 cursor=0；原 fixture
    # range(1,6) 使首场景 start=4.0，违反 CompositionIR 校验器
    # 「IR 首场景起点必须为 0」→ CI 真实失败于 planner.py:201）。
    return [
        {"id": f"S{i:02d}", "start": float(i * 4), "duration": 4.0, "text": f"场景{i}"}
        for i in range(0, 5)
    ]


def _assets():
    return {
        f"v{i}": AssetRef(
            id=f"v{i}", type="video", file=f"/tmp/clip{i}.mp4", duration=4.0
        )
        for i in range(0, 8)
    }


def test_content_match_preferred_asset_stays_first_when_all_videos_are_used():
    assets = _assets()
    scenes = _scenes()
    preferred = {f"S{i:02d}": f"v{i}" for i in range(0, 5)}

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


def test_ghost_assignment_falls_back_without_duplicate_layers():
    """preferred 引用不存在的素材（幽灵 id）时：防御回退不得造成同场景画面重复。

    上游 ContentMatchPlanner 对幽灵 assignment 的处理是 fallback 按序补位，但
    若同一素材被 matched_order + fallback 池重复计入 video_ids，_use_cover_all
    的镜头数被抬高，兜底会把同一素材铺进同场景两次（2026-10-09 探针实录）。
    """
    assets = _assets()
    scenes = _scenes()
    # S00 引用幽灵 v99；其余场景正确匹配
    preferred = {f"S{i:02d}": f"v{i}" for i in range(0, 5)}
    preferred["S00"] = "v99"

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
        # 每个场景内部不得出现重复素材（画面重复=ContentMatchPlanner 立牌要消灭的）
        ids = [l.asset_id for l in sc.layers]
        assert len(ids) == len(set(ids)), f"{sc.id} 重复素材: {ids}"
        assert all(a in assets for a in ids), f"{sc.id} 出现幽灵素材: {ids}"
    # 其余 4 个场景的语义主素材仍须在首位
    for sc in ir.scenes:
        if sc.id != "S00":
            assert sc.layers[0].asset_id == preferred[sc.id]
    # 全部真实素材最终都进时间线
    used = {layer.asset_id for sc in ir.scenes for layer in sc.layers}
    assert used == set(assets)
