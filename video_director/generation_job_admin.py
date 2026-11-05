"""Operator controls for ambiguous asynchronous video submissions.

These operations never contact a provider and never submit a generation.
The operator must reconcile the job against the provider dashboard first.
"""
from __future__ import annotations

import time
from typing import Optional

from video_director import generation


def list_video_jobs(project_id: str, status: Optional[str] = None) -> list[dict]:
    jobs = list(generation._load_jobs(project_id).values())
    if status:
        jobs = [job for job in jobs if job.get("status") == status]
    return sorted(jobs, key=lambda job: (job.get("updated_at", 0), job.get("job_id", "")))


def resolve_video_submission(
    project_id: str,
    job_id: str,
    *,
    provider_task_id: Optional[str] = None,
    confirm_no_provider_task: bool = False,
) -> dict:
    """Resolve one submitting/submission_unknown reservation after human review.

    Supply exactly one outcome:
    - provider_task_id: provider dashboard confirms the task exists; resume polling.
    - confirm_no_provider_task: provider dashboard confirms no task was created;
      explicitly release the reservation so a later user request may submit again.
    """
    if bool(provider_task_id) == bool(confirm_no_provider_task):
        raise generation.GenerationError(
            "INVALID_SUBMISSION_RESOLUTION",
            "必须且只能提供 --provider-task-id 或 --confirm-no-provider-task 其中一项",
        )
    jobs = generation._load_jobs(project_id)
    job = jobs.get(job_id)
    if not job:
        raise generation.GenerationError("VIDEO_JOB_NOT_FOUND", f"找不到视频任务 {job_id}")
    if job.get("status") not in ("submitting", "submission_unknown"):
        raise generation.GenerationError(
            "VIDEO_JOB_NOT_UNRESOLVED",
            f"任务当前状态为 {job.get('status')!r}，只允许处理 submitting/submission_unknown",
        )

    now = time.time()
    if provider_task_id:
        provider_task_id = provider_task_id.strip()
        if not provider_task_id:
            raise generation.GenerationError(
                "INVALID_PROVIDER_TASK_ID", "供应商任务 ID 不能为空",
            )
        duplicate = next((
            other_id for other_id, other in jobs.items()
            if other_id != job_id and other.get("provider_task_id") == provider_task_id
        ), None)
        if duplicate:
            raise generation.GenerationError(
                "PROVIDER_TASK_ALREADY_LINKED",
                f"供应商任务 ID 已关联到本项目任务 {duplicate}，拒绝重复绑定",
            )
        fields = {
            "provider_task_id": provider_task_id,
            "status": "running",
            "submission_resolution": {
                "decision": "provider_task_found",
                "provider_task_id": provider_task_id,
                "resolved_at": now,
                "source": "operator_cli",
            },
        }
    else:
        fields = {
            "status": "submission_rejected",
            "submission_resolution": {
                "decision": "provider_task_not_found_confirmed",
                "resolved_at": now,
                "source": "operator_cli",
            },
        }
    return generation._update_job(project_id, job_id, fields)
