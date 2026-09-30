"""Agent Gateway context：MCP/CLI 共用的项目上下文快照（§49 Context 能力）。

给外部 Agent（Claude/Codex/OpenCode）一致的「当前项目是什么状态、
能干什么」视图——数据全部来自既有只读能力，不另建状态。
"""

from __future__ import annotations

from typing import Dict, Optional

from video_director import editor, store
from video_director.capability_registry import list_capabilities


def project_context_snapshot(project_id: str,
                             selection: Optional[Dict] = None) -> dict:
    """项目上下文：阶段/场景数/版本 + 全部可用能力 id。

    供 creative.inspect 底层与外部 Agent 决策「下一步用什么能力」。
    """
    try:
        stage = store.get_stage(project_id)
    except Exception:  # noqa: BLE001 未登记项目（纯编辑器项目）诚实空值
        stage = None
    scenes = 0
    version = 0
    try:
        ir = editor.load_composition(project_id)
        scenes = len(ir.scenes)
        version = len(editor.load_revisions(project_id)) + 1
    except Exception:  # noqa: BLE001 无 composition.json → 空值非伪造
        pass
    caps = list_capabilities()
    names = sorted(caps.keys() if isinstance(caps, dict) else
                   [c["name"] if isinstance(c, dict) else str(c) for c in caps])
    return {
        "project_id": project_id,
        "stage": stage,
        "scene_count": scenes,
        "composition_version": version,
        "selection": selection,
        "capabilities": names,
    }
