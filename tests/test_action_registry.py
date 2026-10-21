"""最终方案 §13/§14/§57/§62/§63/§64 Action Registry 契约测试。

覆盖：
- §13 结构：id/label/aliases/targets/payload/preconditions/undo/preview
- §五十七 12 条标准命令全部可 resolve（自然语言 → Action）
- §63 Availability：precondition 不满足 → available=false + reason + next_actions
- §62 actions_for_context：Selection 上下文 → 动作组（layer.video 14 项等）
- §17 resolve_step：goal+step → capability+command+payload（不发明字段）
- §64 commercial_context：provider 政策由系统声明
"""

import pytest

from video_director import action_registry as ar


class TestRegistryStructure:
    def test_actions_have_plan_fingerprint(self):
        """§13 结构指纹：每条 Action 必有 id/label/aliases/targets/capability/command。"""
        for a in ar.registry().values():
            assert a.id and a.label and a.targets
            assert a.capability and a.command, f"{a.id} 缺 capability/command"
            assert isinstance(a.aliases, list)
            assert isinstance(a.undo, bool) and isinstance(a.preview, bool)

    def test_unique_ids(self):
        ids = [a.id for a in ar.registry().values()]
        assert len(ids) == len(set(ids))

    def test_capability_ids_exist_in_manifest(self):
        """§14 Action → Capability 链：每个 capability 必须真的注册在 Capability Registry。"""
        from video_director.capability_registry.manifest import manifest
        mf = manifest()
        missing = [a.id for a in ar.registry().values()
                   if a.capability not in ("noop", "preview_voice") and a.capability not in mf]
        assert not missing, f"Action 引用了不存在的 capability: {missing}"


class TestResolveStandardCommands:
    """§五十七「AI 应该能直接完成的命令」逐条。"""

    @pytest.mark.parametrize("text,expect", [
        ("把所有素材的原声都关掉。", "audio.mute_original"),
        ("只保留第 2、4、7 段原声，音量 20%。", "audio.preserve"),
        ("右下角加我的 Logo，透明度 70%。", "watermark.add"),
        ("把 BGM 调小，旁白出来时自动压低。", "music.duck"),
        ("第二段重新找素材，优先本地，没有就免费图库。", "asset.search"),
        ("第三段没有合适视频就生成图片。", "generate.image"),
        ("字幕改成白字黑边、底部、稍微大一点。", "subtitle.style"),
        ("把第 3、4 镜做成连续推进。", "relation.set"),
        ("保留这个视频的现场声音，但压到 15%。", "audio.preserve"),
    ])
    def test_command_resolves(self, text, expect):
        a = ar.resolve_by_text(text)
        assert a is not None, f"无法解析: {text}"
        assert a.id == expect, f"{text} 解析为 {a.id}，期望 {expect}"


class TestResolveStep:
    """§17 LLM goal+steps → resolver。"""

    def test_step_to_capability_command(self):
        r = ar.resolve_step(
            {"action": "watermark.add", "target": "project",
             "params": {"asset": "my_logo", "anchor": "bottom_right", "opacity": 0.7}},
            {"type": "project"})
        assert r["capability"] == "watermark.add"
        assert r["command"] == "set_watermark"
        assert r["targetId"] == ""  # project target → 空 targetId（项目级命令）
        assert r["payload"]["anchor"] == "bottom_right"
        assert r["payload"]["opacity"] == 0.7

    def test_step_current_selection(self):
        r = ar.resolve_step(
            {"action": "audio.preserve", "target": "current_selection", "params": {"volume": 0.2}},
            {"type": "layer", "id": "scene_03#0"})
        assert r["targetId"] == "scene_03#0"
        assert r["payload"]["mode"] == "preserve"
        assert r["payload"]["volume"] == 0.2

    def test_step_unknown_action_honest(self):
        r = ar.resolve_step({"action": "让视频更火", "params": {}})
        assert "error" in r, "未知动作必须诚实报错，不猜"


class TestAvailability:
    """§63 不可用时给原因 + 下一步。"""

    def test_watermark_update_requires_watermark(self, tmp_path, monkeypatch):
        from video_director import config
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
        pid = "act-avail-01"
        base = config.project_dir(pid)
        config.init_project_dirs(base)
        (base / "plans" / "composition.json").write_text(_comp_json(), encoding="utf-8")
        r = ar.action_availability("watermark.update", pid)
        assert r["available"] is False
        assert "水印" in r["reason"] or "Logo" in r["reason"]
        assert r["next_actions"], "§63 不可用必须给 next_actions"

    def test_available_when_preconditions_met(self, tmp_path, monkeypatch):
        from video_director import config
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
        pid = "act-avail-02"
        base = config.project_dir(pid)
        config.init_project_dirs(base)
        comp = _comp_json(with_watermark=True)
        (base / "plans" / "composition.json").write_text(comp, encoding="utf-8")
        r = ar.action_availability("watermark.update", pid)
        assert r["available"] is True

    def test_selection_required(self):
        r = ar.action_availability("asset.replace", "any", {"selection": {"type": None}})
        assert r["available"] is False
        assert "选中" in r["reason"]


class TestActionsForContext:
    """§62 上下文动作组。"""

    def test_layer_video_group(self):
        r = ar.actions_for_context({"type": "layer", "id": "s#0", "layer_type": "video"})
        assert r["context"] == "layer.video"
        ids = [a["id"] for a in r["actions"]]
        # §16 选中视频层可执行面：换素材/裁剪/速度/变换/镜头运动/画中画/蒙版/关键帧/原声/音量/删除/复制
        for must in ("asset.replace", "timeline.trim", "visual.transform", "visual.pip",
                     "visual.mask", "audio.preserve", "audio.mute_original",
                     "timeline.delete"):
            assert must in ids, f"layer.video 组缺 {must}"

    def test_scene_pair_group_only_relation(self):
        r = ar.actions_for_context({"type": "scene_pair", "id": "a->b"})
        ids = [a["id"] for a in r["actions"]]
        assert "relation.set" in ids

    def test_manifest_groups_cover_plan(self):
        """§62 分组键：project/scene/layer.video/layer.subtitle/layer.audio/scene_pair。"""
        groups = ar.manifest_for_project(None)
        for must in ("project", "scene", "layer.video", "layer.subtitle", "scene_pair"):
            assert must in groups, f"缺上下文组 {must}"

    def test_actions_carry_availability_fields(self):
        r = ar.actions_for_context({"type": "project"}, project_id=None)
        for a in r["actions"]:
            assert "available" in a and "reason" in a and "next_actions" in a

    def test_layer_video_exposes_visual_qa(self):
        r = ar.actions_for_context({"type": "layer", "id": "s#0", "layer_type": "video"})
        ids = [a["id"] for a in r["actions"]]
        assert "visual.verify_asset" in ids

    def test_scene_exposes_visual_semantic_rerank(self):
        r = ar.actions_for_context({"type": "scene", "id": "s"})
        ids = [a["id"] for a in r["actions"]]
        assert "visual.semantic_rerank" in ids


class TestSystemContext:
    def test_system_context_fingerprint(self):
        """§16 System Context：actions/providers/render_profiles/asset_sources/voice_sources/music_sources。"""
        s = ar.system_context()
        for key in ("build_id", "actions", "providers", "render_profiles",
                    "asset_sources", "voice_sources", "music_sources", "commercial"):
            assert key in s, f"System Context 缺 {key}"
        assert s["build_id"], "§42 build_id 必须非空"
        assert "local" in s["asset_sources"] and "generated" in s["asset_sources"]

    def test_system_context_hides_unwired_actions(self):
        """三层注册但前端/服务端零入口的能力不得向 Agent 声明——
        否则 Agent 读 system_context 会派发全部打空的能力。
        已接线的 generate.image 必须保留（防止过度过滤）。"""
        actions = ar.system_context()["actions"]
        for unwired in ("generate.sfx", "music.auto_match",
                        "delivery.generate_metadata"):
            assert unwired not in actions, f"{unwired} 零接线，不应暴露给 Agent"
        assert "generate.image" in actions, "已接线能力不得被误过滤"

    def test_unwired_actions_still_registered_for_frontend(self):
        """反向锁：过滤只发生在 system_context，_ACTIONS 注册表本身不动——
        前端动作面板按 target 索引，三个 action 仍可被查（接线后一处改动即恢复）。"""
        ids = {a.id for a in ar._ACTIONS}
        assert {"generate.sfx", "music.auto_match",
                "delivery.generate_metadata"} <= ids

    def test_commercial_context_declares_policies(self):
        """§64 三 Provider 商业政策由系统声明。"""
        cs = ar.commercial_context()
        names = {c["provider"] for c in cs}
        assert {"pexels", "pixabay", "coverr"} <= names
        for c in cs:
            assert "commercial_policy" in c and "api_limit" in c
            assert c["bulk_query"] is False


def _comp_json(with_watermark: bool = False) -> str:
    import json
    wms = [{"kind": "text", "text": "logo", "anchor": "bottom_right",
            "opacity": 0.7}] if with_watermark else []
    return json.dumps({
        "project": {"title": "t", "width": 1080, "height": 1920, "fps": 30, "duration": 6.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 3.0, "text": "一",
             "layers": [{"type": "video", "start": 0.0, "duration": 3.0,
                         "asset_id": "a.mp4", "source_in": 0.0, "source_out": 3.0,
                         "speed": 1.0}]},
            {"id": "scene-2", "start": 3.0, "duration": 3.0, "text": "二",
             "layers": [{"type": "image", "start": 0.0, "duration": 3.0,
                         "asset_id": "b.jpg"}]},
        ],
        "audio": [], "watermarks": wms,
    }, ensure_ascii=False)
