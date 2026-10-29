# -*- coding: utf-8 -*-
"""§82 Action 通路统一治理契约测试（审计附带 2）。

覆盖三项治理，均只观测、不改写现有通路语义：
  1. 前端 executeCommand = 命令级 Action 通道（commands.js 通道计数埋点）
  2. 后端视角的 command 分发统计（server._bump_command_stat / _command_stats）
  3. UI Action Audit 新增 direct_command_callsites 观测字段（纯增，不动违规语义）
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: §82 已知走命令级通道的存量调用方（21 处调用点分布在 7+ 文件）
KNOWN_DIRECT_COMMAND_FILES = (
    "editor-enhanced.js", "inspector.js", "clip-clipboard.js",
    "editor-session.js", "mask-editor.js", "audio-track-asset-picker.js",
    "library.js",
)


def _rel(f: str) -> str:
    """平台无关的相对路径片段（Windows 下 Path.relative_to 产反斜杠）。"""
    return f.replace("\\", "/")


class TestAuditDirectCommandCallsites:
    def test_callsites_field_non_empty(self):
        """§82：audit_ui() 必须产出 direct_command_callsites 且非空。"""
        from video_director.ui_action_audit import audit_ui
        rep = audit_ui()
        assert "direct_command_callsites" in rep
        assert rep["direct_command_callsites"], "命令级通道调用点统计为空"

    def test_callsites_contains_known_files(self):
        """§82：存量直连调用方（editor-enhanced.js 等）必须被观测到。"""
        from video_director.ui_action_audit import audit_ui
        rep = audit_ui()
        rels = {_rel(f) for f in rep["direct_command_callsites"]}
        for known in KNOWN_DIRECT_COMMAND_FILES:
            matched = [r for r in rels if r.endswith("static/js/" + known)]
            assert matched, f"已知直连文件未被观测到: {known}"

    def test_callsites_count_matches_grep_semantics(self):
        """§82：计数与 re.finditer(r'executeCommand\\(') 语义一致（同源机械统计）。"""
        import re
        from video_director.ui_action_audit import audit_ui
        rep = audit_ui()
        for rel, n in rep["direct_command_callsites"].items():
            src = (ROOT / rel).read_text(encoding="utf-8")
            assert n == len(re.findall(r"executeCommand\(", src)), rel

    def test_additive_only_semantics_unchanged(self):
        """§82：新字段是纯增观测——直连调用点不产生任何违规条目。"""
        from video_director.ui_action_audit import audit_ui
        rep = audit_ui()
        # executeCommand 是命令级 Action 通道（ActionSpec.command 直发），不是旁路：
        # 存量调用点一律不进 orphan_button / missing_action（只观测，不判违规）。
        assert not [x for x in rep["orphan_button"] if "executeCommand" in x]
        assert not [x for x in rep["missing_action"] if "executeCommand" in x]
        # 新键存在且不挤占既有四键
        for k in ("pass", "missing_action", "missing_executor", "orphan_button",
                  "scanned_files", "direct_command_callsites"):
            assert k in rep, k

    def test_format_report_renders_callsites(self):
        """§82：format_report 输出包含通道调用点摘要行。"""
        from video_director.ui_action_audit import audit_ui, format_report
        rep = audit_ui()
        text = format_report(rep)
        assert "DIRECT_COMMAND_CALLSITES" in text
        assert "非违规" in text


class TestServerCommandChannelStats:
    """后端视角计数：直接测小函数，不起 server（端点是薄壳）。"""

    def test_bump_and_read_roundtrip(self):
        """计数前后差值（免疫同进程其它测试的累计影响）。"""
        from webui import server as srv
        cmd = "gov_roundtrip_probe"
        before = srv._command_stats()["byCommand"].get(cmd, 0)
        srv._bump_command_stat(cmd)
        srv._bump_command_stat(cmd)
        srv._bump_command_stat("gov_trim_probe")
        st = srv._command_stats()
        assert st["byCommand"][cmd] == before + 2, st
        assert st["byCommand"].get("gov_trim_probe", 0) >= 1, st

    def test_unknown_bucketing_and_no_throw(self):
        """空/非字符串 command 归入 unknown 桶；计数路径永不抛错。"""
        from webui import server as srv
        before = srv._command_stats()["byCommand"].get("unknown", 0)
        junk_values = (None, "", 0, 123, [], {})  # 6 种非字符串/空值
        for junk in junk_values:
            srv._bump_command_stat(junk)
        st = srv._command_stats()
        assert st["byCommand"]["unknown"] == before + len(junk_values), st

    def test_returned_snapshot_is_isolated_copy(self):
        """快照是副本：调用方改动不污染进程级计数。"""
        from webui import server as srv
        cmd = "gov_isolated_probe"
        srv._bump_command_stat(cmd)
        snap = srv._command_stats()
        snap["byCommand"][cmd] = 9999
        snap["total"] = -1
        assert srv._command_stats()["byCommand"][cmd] >= 1
        assert srv._command_stats()["total"] >= 1

    def test_route_registered_in_do_get(self):
        """GET /api/action_channel_stats 路由存在，且返回键与计数快照同构。"""
        src = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
        assert '"/api/action_channel_stats"' in src
        assert "_command_stats()" in src


class TestFrontendCommandChannelStats:
    """commands.js 通道计数埋点（静态契约 + node 语法）。"""

    def test_channel_stats_bootstrap_present(self):
        src = (ROOT / "webui" / "static" / "js" / "commands.js").read_text(encoding="utf-8")
        assert "window.__cmdChannelStats" in src
        assert "count: 0" in src and "byCommand: {}" in src

    def test_bump_after_projectid_guard(self):
        """埋点在 projectId 校验之后：无项目时直接抛错，不计入通道。"""
        src = (ROOT / "webui" / "static" / "js" / "commands.js").read_text(encoding="utf-8")
        guard = src.index("throw new Error('先打开项目')")
        bump = src.index("_bumpChannelStat(spec && spec.command)")
        assert guard < bump, "通道计数须在 projectId 校验之后"

    def test_commands_js_parses_under_node(self):
        """node --check：commands.js 语法合法（埋点没写坏）。"""
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            pytest.skip("node 不可用")
        proc = subprocess.run(
            [node, "--check", str(ROOT / "webui" / "static" / "js" / "commands.js")],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
