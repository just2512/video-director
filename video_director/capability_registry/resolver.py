"""MPT 吸收方案 §5 Capability Registry 的 resolver：能力解析与前置条件预检。

方案 §5 目录结构里的 resolver 层：AI 给一个意图/能力 id，resolver 负责
    - 定位能力（单条 / 按 category 候选）
    - 预检 preconditions（如 target_video_exists → 项目有无视频资产）
    - provider_required 的 provider 可用性检查
返回"可执行 / 缺前置 / 缺 provider"三态之一，供 Tool Executor 决定
执行还是诚实降级（§9：不能盲执行）。

零评分：不返回 relevance/score，只返回状态。
"""

from __future__ import annotations

from typing import List, Optional

from video_director.capability_registry import loader


def resolve(cap_id: str) -> dict:
    """单能力解析：{id, found, provider_ready, missing_preconditions}。"""
    cap = loader.get_capability(cap_id)
    if cap is None:
        return {"id": cap_id, "found": False, "reason": "UNKNOWN_CAPABILITY"}
    return {
        "id": cap_id,
        "found": True,
        "category": cap.category,
        "provider_required": cap.providerRequired,
        "requires_confirmation": cap.requiresConfirmation,
        "missing_preconditions": sorted(cap.preconditions),
    }


def resolve_for_context(project_context: dict) -> dict:
    """按当前项目上下文，回填可校验的前置条件（§7 Inspect → Plan 前置）。

    当前可静态校验的前置：
      - target_video_exists / target_image_exists → 项目有无对应资产
    其余前置（如 target_scene_selected）依赖 selection，由 Tool Executor
    运行时校验。
    """
    assets = project_context.get("assets") or {}
    return {
        "has_video_asset": (assets.get("video") or 0) > 0,
        "has_image_asset": (assets.get("image") or 0) > 0,
        "has_audio_asset": (assets.get("audio") or 0) > 0,
    }


def check_preconditions(cap_id: str, project_context: dict) -> List[str]:
    """返回不满足的前置条件列表（空 = 全部满足）。"""
    cap = loader.get_capability(cap_id)
    if cap is None:
        return ["UNKNOWN_CAPABILITY"]
    env = resolve_for_context(project_context)
    unmet: List[str] = []
    for pre in cap.preconditions:
        if pre == "target_video_exists" and not env["has_video_asset"]:
            unmet.append(pre)
        elif pre == "target_image_exists" and not env["has_image_asset"]:
            unmet.append(pre)
        elif pre == "target_audio_exists" and not env["has_audio_asset"]:
            unmet.append(pre)
    return unmet