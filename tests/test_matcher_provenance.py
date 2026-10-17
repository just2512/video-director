"""匹配来源诚实化：顺序兜底必须标记为 sequential，不能被写成语义匹配。

背景（2026-10-16 实测 web-dfc44070）：assignments 实际是纯序号分配
（S01→素材[0]…S05→素材[4]），但 selection_reasons 统一写死「场景语义匹配」
→ 用户误以为语义已生效。这里锁死机制级来源。
"""
from pathlib import Path

from video_director import matcher
from video_director.models import AssetRef


def _scenes():
    return [{"id": "S01", "text": "咖啡特写"}, {"id": "S02", "text": "数据图表"}]


def _assets(descriptions):
    out = {}
    for i, d in enumerate(descriptions):
        out[f"a{i}"] = AssetRef(
            id=f"a{i}", type="video", file=f"/a{i}.mp4",
            description=d, keywords=[])
    return out


def test_provenance_records_llm():
    prov = {}

    def llm(_prompt):
        return ('{"assignments": [{"scene_id": "S01", "asset_id": "a0"},'
                ' {"scene_id": "S02", "asset_id": "a1"}]}')

    matcher.match_assets(_scenes(), _assets(["咖啡特写", "数据图表"]), llm,
                         provenance_out=prov)
    assert prov["S01"] == "llm"
    assert prov["S02"] == "llm"


def test_provenance_records_sequential_when_no_semantics():
    prov = {}
    matcher.match_assets(_scenes(), _assets(["", ""]), None, provenance_out=prov)
    assert set(prov.values()) == {"sequential"}, prov


def test_director_agent_no_longer_claims_every_match_is_semantic():
    src = (Path(__file__).resolve().parents[1] / "video_director"
           / "director_agent.py").read_text(encoding="utf-8")
    assert "场景语义匹配：结合场景文案、素材语义与素材可用性选择" not in src
    assert "match_provenance" in src
    assert "顺序兜底" in src