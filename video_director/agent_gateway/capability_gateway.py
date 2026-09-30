"""Frontend Unification §19/AG-001：Agent Gateway——MCP 与 CLI 的统一内部执行层。

结构（§19 最终结构图）：
    MCP（Claude/Codex）  CLI（OpenCode/Shell）
            ↓                   ↓
        capability_gateway（本文件——唯一汇合点）
            ↓
    Capability Registry → Tool Executor → Command Bus → Composition IR

设计纪律（§3.4/§19）：
    - Gateway 不是第二套控制中枢：不自己实现任何业务，
      只做「高层动词 → 既有 capability/tool 的编排」。
    - 全部副作用经 tool_executor.execute_tool（与 UI 同一条链）。
    - 高层七工具：inspect/plan/execute/preview/render/verify/watch。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from video_director import config, editor, store, tool_executor
from video_director import project_context


class GatewayError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# 高层工具实现（§19 MCP 高层工具 / §52 CLI 对等入口）
# ---------------------------------------------------------------------------

def creative_inspect(project_id: str = "", selection: Optional[Dict] = None) -> dict:
    """inspect：项目/系统状态。无 project_id → 系统级摘要（项目列表+能力数）。"""
    if not project_id:
        projects = store.list_projects(limit=30)
        from video_director.capability_registry import list_capabilities
        return {"projects": projects, "capabilities": len(list_capabilities())}
    if selection is not None:
        return tool_executor.execute_tool(project_id, {
            "tool": "system.context.inspect", "payload": {"selection": selection}})
    return tool_executor.execute_tool(project_id, {"tool": "project.inspect", "payload": {}})


def creative_plan(project_id: str, instruction: str) -> dict:
    """plan：AI 规划（Director）。只出计划（proposal），不落 Composition。"""
    from video_director import director
    return director.direct(project_id, instruction)


def creative_execute(project_id: str, tool: str, payload: Optional[Dict] = None,
                     target_id: Optional[str] = None) -> dict:
    """execute：执行单条能力（granular capability，经 Tool Executor 全链）。

    与 UI 同一条链：validate → editor.execute_command → revision → verify。
    """
    return tool_executor.execute_tool(project_id, {
        "tool": tool, "payload": payload or {}, "targetId": target_id,
    })


def creative_preview(project_id: str, scale: float = 0.5) -> dict:
    """preview：生成草稿预览（低清快渲染）。"""
    from video_director import confirm
    try:
        out = confirm.render_draft(project_id, scale=scale)
        return {"ok": True, "draft_path": out, "preview": out}
    except Exception as exc:  # noqa: BLE001 网关层诚实返回错误，不吞
        return {"ok": False, "tool": "creative.preview", "status": "ERROR",
                "result": {}, "error": str(exc)[:200], "verify": {}}


def creative_render(project_id: str) -> dict:
    """render：终渲（finalize）。经 render.final 渲染域能力。"""
    return tool_executor.execute_tool(project_id, {"tool": "render.final", "payload": {}})


def creative_verify(project_id: str, aspect: str = "all") -> dict:
    """verify：组合校验（composition/continuity 全量读回断言）。

    composition.verify 的 TargetRequest schema 要求 targetId（该能力
    原设计为逐目标断言）；网关层以「断言整个项目 Composition」语义传
    project_id 作 target（全量校验），不改变 verify 能力本身契约。
    """
    checks: Dict[str, dict] = {}
    r1 = tool_executor.execute_tool(project_id, {
        "tool": "composition.verify",
        "payload": {"targetId": project_id}, "targetId": project_id})
    checks["composition.verify"] = {"ok": r1.get("ok"), "status": r1.get("status"),
                                    "result": r1.get("result")}
    r2 = tool_executor.execute_tool(project_id, {"tool": "visual.continuity.verify", "payload": {}})
    checks["visual.continuity.verify"] = {"ok": r2.get("ok"), "status": r2.get("status"),
                                          "result": r2.get("result")}
    ok = all(c.get("ok") for c in checks.values())
    return {"ok": ok, "aspect": aspect, "checks": checks,
            "tool": "creative.verify", "status": "OK" if ok else "FAIL",
            "result": {"checks": {k: v for k, v in checks.items()}}, "error": None}


def creative_watch(project_id: str = "") -> dict:
    """watch：项目/任务状态流（SSE /api/events 的轮询式读取；网关层给快照）。"""
    if not project_id:
        return {"ok": True, "jobs": [], "note": "watch 长连接走 SSE /api/events；此处为快照"}
    try:
        root = config.project_dir(project_id)
        run_json = root / "plans" / "run.json"
        stage = store.get_stage(project_id)
        run = {}
        if run_json.is_file():
            import json as _json
            try:
                run = _json.loads(run_json.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                run = {}
        return {"ok": True, "project_id": project_id, "stage": stage,
                "providers": run.get("providers", {})}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


#: §19 高层工具注册表（MCP tools / CLI 子命令共用同一实现）
HIGH_LEVEL_TOOLS = {
    "creative.inspect": creative_inspect,
    "creative.plan": creative_plan,
    "creative.execute": creative_execute,
    "creative.preview": creative_preview,
    "creative.render": creative_render,
    "creative.verify": creative_verify,
    "creative.watch": creative_watch,
}


def dispatch(name: str, project_id: str = "", **kwargs) -> dict:
    """统一入口：MCP tool call 与 CLI 子命令都走这里（同一内部能力层 §19）。"""
    fn = HIGH_LEVEL_TOOLS.get(name)
    if fn is None:
        raise GatewayError("UNKNOWN_TOOL", f"未知高层工具 {name!r}，应为 {sorted(HIGH_LEVEL_TOOLS)}")
    if name != "creative.inspect" and name != "creative.watch" and not project_id:
        raise GatewayError("MISSING_PROJECT", f"{name} 需 project_id")
    try:
        return fn(project_id, **kwargs) if project_id else fn(**kwargs)
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001 网关层不吞业务异常，结构化返回
        return {"ok": False, "tool": name, "status": "EXECUTION_ERROR",
                "result": {}, "error": str(exc)[:200], "verify": {}}
