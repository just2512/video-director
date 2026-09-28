"""MPT 吸收方案 §40：WorkflowJob 统一作业状态机（八态）+ 失败阶段记录。

方案 §40 要求 Job 系统统一为 WorkflowJob，状态：
    QUEUED / RUNNING / WAITING_INPUT / WAITING_CONFIRMATION /
    RETRYABLE_FAILURE / FAILED / COMPLETED / CANCELLED
并记录 currentCapability / currentArtifact / errorCode / retryable。

与 studio.py 现有四态（waiting/running/done/failed）的关系：
studio 是生产队列的持久化层，本模块是 WorkflowJob 的**状态契约 + 迁移映射层**
（§40 统一语义，不推翻 studio 的 SQL——通过 job_status_legacy 映射兼容）。
"""

from __future__ import annotations

from enum import Enum


class WorkflowJobStatus(str, Enum):
    """§40：八态作业状态机。"""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"              # 需要人工输入（选素材/确认参数）
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"  # 需要人工确认（Gate B 审片）
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"      # 可重试失败（如网络/供应商超时）
    FAILED = "FAILED"                            # 不可重试失败
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


#: 终态集合（不再迁移）
TERMINAL = {WorkflowJobStatus.COMPLETED, WorkflowJobStatus.FAILED,
            WorkflowJobStatus.CANCELLED}

#: studio 四态 → WorkflowJob 八态映射（§40 统一语义层）
LEGACY_TO_WORKFLOW = {
    "waiting": WorkflowJobStatus.QUEUED,
    "running": WorkflowJobStatus.RUNNING,
    "done": WorkflowJobStatus.COMPLETED,
    "failed": WorkflowJobStatus.FAILED,
}


def from_legacy(legacy_status: str, error: str = "", retryable: bool = False) -> str:
    """studio 四态 → §40 八态（failed + retryable → RETRYABLE_FAILURE）。"""
    if legacy_status == "failed" and retryable:
        return WorkflowJobStatus.RETRYABLE_FAILURE.value
    mapped = LEGACY_TO_WORKFLOW.get(legacy_status)
    return mapped.value if mapped else WorkflowJobStatus.QUEUED.value


def normalize_job(job: dict) -> dict:
    """把 studio job 记录归一为 §40 WorkflowJob 结构。

    补 currentCapability/currentArtifact/errorCode/retryable 占位字段
    （当前四态持久层未存这些，诚实置空，不编造）。
    """
    status = job.get("status", "waiting")
    spec = job.get("spec") or {}
    return {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "status": from_legacy(status),
        "status_legacy": status,
        "currentCapability": spec.get("current_capability"),
        "currentArtifact": spec.get("current_artifact"),
        "errorCode": _error_code(job.get("error")),
        "retryable": bool(spec.get("retryable", False)),
        "error": job.get("error"),
        "result": job.get("result"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def _error_code(error: str) -> str:
    """从错误文本粗分类 errorCode（§40 要结构化而非散落字符串）。

    第一版：按已知错误类型关键词映射；无匹配 → GENERIC_ERROR。
    """
    if not error:
        return ""
    low = error.lower()
    if "timeout" in low or "超时" in error:
        return "CAPABILITY_TIMEOUT"
    if "network" in low or "网络" in error or "connect" in low:
        return "NETWORK_ERROR"
    if "unavailable" in low or "不可用" in error:
        return "PROVIDER_UNAVAILABLE"
    if "conflict" in low or "冲突" in error:
        return "CONFLICT"
    if "invalid" in low or "非法" in error or "越界" in error:
        return "INVALID_INPUT"
    return "GENERIC_ERROR"


def is_terminal(status: str) -> bool:
    return status in {s.value for s in TERMINAL}