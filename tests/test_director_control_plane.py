"""MPT 吸收方案 §98 Regression Fixture + IG-AI-CONTROL 契约测试。

覆盖：
    - CAP-AI-001 Capability Registry（manifest 只读事实源 + validator 契约校验）
    - CAP-AI-003 Project Context（§6 字段：project/selection/timeline/assets/activeFeatures/providers）
    - CAP-AI-004 Tool Executor（六能力经 Command Bus → 真实落到 Composition）
    - CAP-AI-006 Verifier（执行后 verify 副作用真实存在）
    - CAP-AI-002 Director（§92 最小闭环：一句话 → 能力序列 → 执行 → verify）
    - §18 original / §19 watermark / §32 subtitle_style / §43 4K profile

不依赖真实 ffmpeg / LLM（渲染链由 E2E 覆盖；这里测契约 + 执行副作用）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_director import config, editor
from video_director.capability_registry import (
    describe_capability,
    list_capabilities,
    validate_tool_call,
)
from video_director.models import CompositionIR


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    """§98 fixture：一个可编辑的 video 项目（含一个视频层能建 original 轨）。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    from tests.fixtures.fixture_data import RECIPE_PROJECT
    data = RECIPE_PROJECT
    pid = data["project_id"]
    base = tmp_path / pid
    (base / "plans").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    ir = CompositionIR(**data["composition"])
    (base / "plans" / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
    # 素材清单：让第一个视频层的 asset 有个可寻址的 manifest 条目
    (base / "assets" / "manifest.json").write_text(
        json.dumps(data.get("manifest", []), ensure_ascii=False), encoding="utf-8")
    return pid


class TestCapabilityRegistry:
    def test_manifest_has_all_required_tools(self):
        """§57 Tool Registry 全表必须登记（指纹级：至少覆盖方案列出的技能）。"""
        required = {
            "system.capabilities.list", "system.capabilities.describe", "system.context.inspect",
            "project.inspect", "timeline.query", "timeline.trim", "timeline.move",
            "timeline.split", "timeline.delete", "timeline.duplicate", "timeline.replace",
            "layer.add", "layer.update", "layer.remove",
            "audio.original.preserve", "audio.original.mute", "audio.original.volume",
            "audio.original.duck", "watermark.add", "watermark.update", "watermark.remove",
            "subtitle.generate", "subtitle.update_style", "subtitle.update_timing",
            "music.volume", "music.duck", "music.add", "music.remove", "music.generate",
            "tts.list_voices", "tts.preview_voice", "tts.generate",
            "stock.search", "stock.preview", "stock.import",
            "generation.image", "generation.video", "generation.sfx", "generation.music",
            "motion.list", "motion.preview", "motion.apply",
            "style.apply", "template.instantiate", "shot.resolve", "shot.apply",
            "render.preflight", "render.draft", "render.final",
            "revision.propose", "revision.preview", "revision.apply", "revision.reject",
            "version.create", "project.reopen", "qc.inspect", "review.create",
        }
        assert required <= set(list_capabilities())

    def test_manifest_fields_align_scheme_5(self):
        """§5 Capability 契约字段逐一对齐（id/version/category/inputSchema/examples…）。"""
        c = describe_capability("audio.original.preserve")
        assert c["id"] == "audio.original.preserve"
        assert c["version"] == "1.0.0"
        assert c["category"] == "audio"
        assert c["inputSchema"] == "OriginalAudioPolicy"
        assert "AudioTrack" in c["outputArtifacts"]
        assert c["supportsPreview"] is False
        assert c["requiresConfirmation"] is False
        assert "保留原声" in c["examples"]

    def test_validator_contract(self):
        ok, e = validate_tool_call("music.volume", {"type": "bgm", "volume": 0.3})
        assert ok, e
        ok2, e2 = validate_tool_call("music.volume", {"type": "bgm", "volume": 3.0})
        assert not ok2 and "volume" in "".join(e2)

    def test_unknown_capability_rejected(self):
        ok, e = validate_tool_call("no.such.tool", {})
        assert not ok and "未知能力" in "".join(e)


class TestProjectContext:
    def test_context_structure_align_scheme_6(self, pid):
        from video_director.project_context import inspect_project_context
        ctx = inspect_project_context(pid)
        assert "project" in ctx and "id" in ctx["project"]
        assert ctx["project"]["id"] == pid
        assert "ratio" in ctx["project"]
        assert set(("timeline", "assets", "activeFeatures", "providers")) <= set(ctx)
        assert {"video", "image", "audio"} <= set(ctx["assets"])
        assert {"tts", "stock", "video_generation", "image"} <= set(ctx["providers"])


class TestEditorCommandsNew:
    def test_set_original_audio_preserve(self, pid):
        r = editor.execute_command(pid, {
            "command": "set_original_audio", "targetId": "",
            "payload": {"mode": "preserve", "volume": 0.8, "duckAgainst": "narration"},
        })
        assert r.status == "APPLIED", r.errors
        ir = editor.load_composition(pid)
        orig = [t for t in ir.audio if t.type.value == "original"]
        assert orig and abs(orig[0].volume - 0.8) < 1e-6
        assert orig[0].ducking is True

    def test_set_original_audio_idempotent(self, pid):
        editor.execute_command(pid, {"command": "set_original_audio", "payload": {"mode": "preserve"}})
        editor.execute_command(pid, {"command": "set_original_audio", "payload": {"mode": "mute"}})
        ir = editor.load_composition(pid)
        orig = [t for t in ir.audio if t.type.value == "original"]
        assert len(orig) == 1  # 幂等：更新而非追加

    def test_set_watermark_and_remove(self, pid):
        r = editor.execute_command(pid, {
            "command": "set_watermark", "payload": {"kind": "text", "text": "品牌",
                                                    "anchor": "top_right", "opacity": 0.7},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        assert ir.watermarks and ir.watermarks[0].anchor == "top_right"
        r2 = editor.execute_command(pid, {"command": "remove_watermark", "payload": {}})
        assert r2.status == "APPLIED"
        assert not editor.load_composition(pid).watermarks

    def test_set_subtitle_style(self, pid):
        r = editor.execute_command(pid, {
            "command": "set_subtitle_style",
            "payload": {"color": "#FFFFFF", "strokeColor": "#000000", "position": "bottom"},
        })
        assert r.status == "APPLIED"
        assert editor.load_composition(pid).subtitle_style.position == "bottom"

    def test_watermark_requires_text_for_text_kind(self, pid):
        r = editor.execute_command(pid, {"command": "set_watermark", "payload": {"kind": "text"}})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"


class TestDirectorToolExecutor:
    def _plan(self, pid, instruction, monkeypatch):
        """Director 走规则兜底（LLM 不可用时不阻塞真实性）。"""
        from video_director import director
        monkeypatch.setattr(director, "_llm_plan", lambda i, c: None)
        return director.direct(pid, instruction)

    def test_minimal_loop_preserve_watermark_bgm(self, pid, monkeypatch):
        """§92 最小闭环：保留原声 + 右上角水印 + BGM 小一点。"""
        out = self._plan(pid, "保留原声，加右上角水印，把 BGM 调小。", monkeypatch)
        assert out["verify_ok"] is True
        tools = [e["tool"] for e in out["executed"]]
        assert "audio.original.preserve" in tools
        assert "watermark.add" in tools
        assert "music.volume" in tools
        # 副作用真实落地
        ir = editor.load_composition(pid)
        assert any(t.type.value == "original" for t in ir.audio)
        assert ir.watermarks and ir.watermarks[0].anchor == "top_right"
        assert any(t.type.value == "bgm" for t in ir.audio)

    def test_subtitle_white_on_black(self, pid, monkeypatch):
        out = self._plan(pid, "字幕改成白字黑边", monkeypatch)
        assert out["verify_ok"] is True
        st = editor.load_composition(pid).subtitle_style
        assert st and st.color == "#FFFFFF" and st.stroke_color == "#000000"

    def test_readonly_timeline_query(self, pid):
        from video_director.tool_executor import execute_tool
        res = execute_tool(pid, {"tool": "timeline.query", "payload": {}})
        assert res["ok"] and res["status"] == "OK"
        assert "scenes" in res["result"]["timeline"]

    def test_system_capabilities_list(self, pid):
        from video_director.tool_executor import execute_tool
        res = execute_tool(pid, {"tool": "system.capabilities.list", "payload": {}})
        assert res["ok"]
        assert "audio.original.preserve" in res["result"]["capabilities"]

    def test_unknown_tool_rejected(self, pid):
        from video_director.tool_executor import execute_tool
        res = execute_tool(pid, {"tool": "bogus.tool", "payload": {}})
        assert not res["ok"] and res["status"] == "UNKNOWN_CAPABILITY"


class TestOutputProfile:
    def test_profiles_scheme_43(self):
        from video_director.output_profile import PROFILES
        assert set(PROFILES) == {
            "1080p-landscape", "1080p-portrait", "1080p-square",
            "4k-landscape", "4k-portrait", "4k-square",
        }
        assert PROFILES["4k-landscape"]["width"] == 3840
        assert PROFILES["4k-landscape"]["is_4k"]

    def test_preflight_4k_honesty(self):
        from video_director.output_profile import preflight_4k_honesty
        class A:
            width = 1080
            height = 1920
        class L:
            type = type("T", (), {"value": "video"})()
            asset_id = "a1"
        class S:
            layers = [L()]
        class IR:
            scenes = [S()]
        warnings = preflight_4k_honesty(IR(), {"a1": A()})
        assert warnings  # 1080p 源 → 诚实提示"非 4K"