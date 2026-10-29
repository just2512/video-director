"""上轮审计三条遗留差距的回归锁。

差距 7（审批点 A）：stop_at=script 的创建入口——审计判断「只差入口」，实测
f88b507（10-08）已有前端下拉 cf-stop-at（含「先完成文案」），42b5c68（10-16）
已有后端 stop_at 透传（director_service spec），director_agent._STOP_NODE_ALIASES
映射 script→script，continue 链（project.js → /api/director/continue → verify）
在 script 之后可达。本文件锁住全链关键点，防回归。
差距 2a：generate.sfx / music.auto_match 真入口接线后的 system_context 声明恢复；
delivery.generate_metadata 保持摘除（真无实现）。
差距 5：镜头级 fallback_routes 契约（候选自带降级链，旧数据回落全局表）。
"""
import json
from pathlib import Path
from types import SimpleNamespace

from video_director import creative_candidates
from video_director.director_agent import _STOP_NODE_ALIASES, _resolve_stop_node

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 差距 7：stop_at=script 全链
# ---------------------------------------------------------------------------

class TestStopAtScriptChain:
    def test_script_alias_maps_to_script_node(self):
        """stop_at=script → Director script 节点（不是默认 render）。"""
        assert _STOP_NODE_ALIASES.get("script") == "script"
        assert _resolve_stop_node({"stop_at": "script"}) == "script"
        # 其他 alias 不回归
        assert _resolve_stop_node({"stop_at": "materials"}) == "material_match"
        assert _resolve_stop_node({}) == "render"

    def test_frontend_create_panel_has_stop_at_control(self):
        """创建面板「自动做到」下拉存在且含 script 选项（用户可先审文案）。"""
        src = (ROOT / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
        assert 'id="cf-stop-at"' in src
        assert '<option value="script">先完成文案</option>' in src
        # payload 透传
        assert "stop_at: (document.getElementById('cf-stop-at')" in src

    def test_backend_service_passthrough_stop_at(self):
        """director_service 把 payload.stop_at 透传进 spec（不做白名单拦截）。"""
        src = (ROOT / "video_director" / "director_service.py").read_text(encoding="utf-8")
        assert '"stop_at": str(payload.get("stop_at") or "video")' in src

    def test_continue_target_reachable_from_script_checkpoint(self):
        """script 停住后 continue target=preview（verify 节点）在 checkpoint 之后。"""
        from video_director.director_inputs import NODE_DEFS
        ids = [n[0] for n in NODE_DEFS]
        assert ids.index("verify") > ids.index("script"), \
            "verify 必须在 script 之后，否则 continue 到 preview 不可达"

    def test_continue_button_wired_to_continue_endpoint(self):
        """项目列表「继续」按钮 → /api/director/continue（现有入口，不新增）。"""
        src = (ROOT / "webui" / "static" / "js" / "project.js").read_text(encoding="utf-8")
        assert "apiPost('/api/director/continue'" in src
        # AWAITING_CONFIRM 状态给 continue_ 动作（前端不猜，服务端状态矩阵）
        pm = (ROOT / "video_director" / "project_manager.py").read_text(encoding="utf-8")
        assert 'a["continue_"] = True' in pm

    def test_server_continue_route_targets_service_not_workflow_directly(self):
        """/api/director/continue 走 shared service（契约测试同源）。"""
        src = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
        assert "continue_director_project(" in src


# ---------------------------------------------------------------------------
# 差距 2a：假能力恢复声明（接线后）
# ---------------------------------------------------------------------------

class TestSystemContextRestoredDeclarations:
    def test_wired_actions_are_declared(self):
        from video_director import action_registry as ar
        actions = ar.system_context()["actions"]
        assert "generate.sfx" in actions
        assert "music.auto_match" in actions

    def test_delivery_generate_metadata_declared_after_real_impl(self):
        """2026-10-23 翻转：真实现落地（tool_executor LLM 发布文案）→ 声明恢复。

        此前的「保持摘除」锁已完成使命——占位期防假能力暴露给 Agent；
        现在 capability 改指 delivery.generate_metadata 真能力，
        断言随之翻转：声明必须在、capability 必须是真能力。
        """
        from video_director import action_registry as ar
        spec = ar.get_action("delivery.generate_metadata")
        assert spec is not None
        assert spec.capability == "delivery.generate_metadata", spec.capability
        assert "delivery.generate_metadata" in ar.system_context()["actions"]

    def test_sfx_frontend_entry_is_honest_search(self):
        """前端文案写「搜索音效」，不伪造 AI 生成。"""
        gen_src = (ROOT / "webui" / "static" / "js" / "generation.js").read_text(encoding="utf-8")
        insp_src = (ROOT / "webui" / "static" / "js" / "inspector.js").read_text(encoding="utf-8")
        assert 'value="sfx"' in insp_src
        assert "搜索音效" in gen_src
        assert "AI 生成音效" not in gen_src

    def test_sfx_backend_goes_through_openverse_not_generation_generate(self):
        """generation.sfx 在 tool_executor 特判走 Openverse 链（generation.py
        对 sfx 诚实不可用，绝不落入 gen.generate 的 GENERATION_UNAVAILABLE）。"""
        src = (ROOT / "video_director" / "tool_executor.py").read_text(encoding="utf-8")
        assert 'if tool == "generation.sfx":' in src
        assert "_execute_sfx_search" in src

    def test_auto_match_frontend_entry_exists(self):
        """BGM 面板有「自动配乐」按钮且走 executeAction('music.auto_match')。"""
        src = (ROOT / "webui" / "static" / "js" / "library.js").read_text(encoding="utf-8")
        assert "edLibAutoMatchBgm" in src
        assert "music.auto_match" in src


# ---------------------------------------------------------------------------
# 差距 5：镜头级 fallback_routes 契约
# ---------------------------------------------------------------------------

class TestCandidateFallbackRoutes:
    def test_candidate_carries_default_routes_by_generation_type(self):
        cand = creative_candidates._clean_candidate(
            "S01", 0,
            {"source_preference": "generated", "generation_type": "video"},
            "x")
        assert cand["fallback_routes"] == ["video", "image", "graphic"]

    def test_candidate_honors_llm_specified_routes(self):
        cand = creative_candidates._clean_candidate(
            "S01", 0,
            {"source_preference": "generated", "generation_type": "video",
             "fallback_routes": ["image"]},
            "x")
        assert cand["fallback_routes"] == ["image"], "LLM 指定的镜头级链必须生效"

    def test_candidate_filters_unknown_route_values(self):
        cand = creative_candidates._clean_candidate(
            "S01", 0,
            {"source_preference": "generated", "generation_type": "image",
             "fallback_routes": ["sfx", "bogus", "graphic"]},
            "x")
        assert cand["fallback_routes"] == ["graphic"]

    def test_non_generated_candidate_has_empty_routes(self):
        cand = creative_candidates._clean_candidate("S01", 0, {}, "x")
        assert cand["fallback_routes"] == []

    def test_tool_material_uses_candidate_routes_first(self, tmp_path, monkeypatch):
        """候选带 fallback_routes=["image"] 时生成补位只尝试 image 路线。"""
        from video_director import director_agent as agent
        from video_director.models import AssetRef

        png = tmp_path / "gen.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        monkeypatch.setattr(
            agent.assets_mod, "build_manifest",
            lambda v, img, asset_metadata=None: ({"genid": AssetRef(
                id="genid", type="image", file=str(img[0]), width=100, height=100)},
                ["genid"]) if img else ({}, []))
        monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

        tried = []

        def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
            tried.append(gen_type)
            if gen_type == "video":
                raise RuntimeError("video API 挂")
            return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                    width=100, height=100)]

        monkeypatch.setattr("video_director.generation.generate", fake_gen)

        state = {
            "project_id": "p",
            "video_files": [], "image_files": [],
            "constraints": {"visual_source": "auto", "allow_stock": True},
            "narration_scenes": [{"id": "S01", "text": "咖啡特写", "start": 0.0, "duration": 1.0}],
            "scene_plan": {"scenes": [{
                "id": "S01", "selected_candidate_id": "c1",
                "candidates": [{
                    "id": "c1", "approach": "concept", "visual_intent": "x",
                    "search_terms": ["coffee"], "source_preference": "generated",
                    "generation_type": "video", "generation_prompt": "一杯咖啡",
                    "fallback_routes": ["image"],
                }],
            }]},
        }
        out = agent._tool_material({"match_materials_to_script": True,
                                    "width": 100, "height": 100}, state)
        assert out.get("ok") is True
        assert tried == ["image"], f"候选指定只走 image，实际尝试: {tried}"
        assert state["assignments"]["S01"] == "gen-s01-image"

    def test_tool_material_falls_back_to_global_table_for_legacy(self, tmp_path, monkeypatch):
        """旧数据（候选无 fallback_routes 字段）回落全局默认表，行为不变。"""
        from video_director import director_agent as agent
        from video_director.models import AssetRef

        png = tmp_path / "gen.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        monkeypatch.setattr(
            agent.assets_mod, "build_manifest",
            lambda v, img, asset_metadata=None: ({"genid": AssetRef(
                id="genid", type="image", file=str(img[0]), width=100, height=100)},
                ["genid"]) if img else ({}, []))
        monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

        tried = []

        def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
            tried.append(gen_type)
            if gen_type == "video":
                raise RuntimeError("video API 挂")
            return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                    width=100, height=100)]

        monkeypatch.setattr("video_director.generation.generate", fake_gen)

        state = {
            "project_id": "p",
            "video_files": [], "image_files": [],
            "constraints": {"visual_source": "auto", "allow_stock": True},
            "narration_scenes": [{"id": "S01", "text": "咖啡特写", "start": 0.0, "duration": 1.0}],
            "scene_plan": {"scenes": [{
                "id": "S01", "selected_candidate_id": "c1",
                "candidates": [{
                    "id": "c1", "approach": "concept", "visual_intent": "x",
                    "search_terms": ["coffee"], "source_preference": "generated",
                    "generation_type": "video", "generation_prompt": "一杯咖啡",
                    # 无 fallback_routes 字段（旧数据）
                }],
            }]},
        }
        out = agent._tool_material({"match_materials_to_script": True,
                                    "width": 100, "height": 100}, state)
        assert out.get("ok") is True
        # 全局表 video → (video, image, graphic)：video 挂了降级 image 成功
        assert tried == ["video", "image"], f"旧数据应回落默认链，实际: {tried}"
        assert state["assignments"]["S01"] == "gen-s01-image"
