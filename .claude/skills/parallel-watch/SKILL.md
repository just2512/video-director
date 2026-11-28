---
name: parallel-watch
description: Actively reconcile local Windows work with remote GitHub branches, pull requests, and Actions; advance the next unblocked task instead of waiting for another prompt.
---

# Parallel dual-line monitor

Use this skill as the active coordinator for the repository's Local + Remote execution model.

## Non-negotiable operating behavior

- Do not act as a passive status reporter. Inspect both lanes, identify the next concrete action, and execute all safe, in-scope work without waiting for another user prompt.
- The repository maintainer may own, coordinate, inspect, and merge work in this single-maintainer repository. Do not wait for a third-party reviewer.
- Treat GitHub as the shared record for branches, PRs, commits, task status, and remote CI. Treat the local checkout and Windows environment as the source of truth for dirty files, local processes, real media, browser/codec behavior, and local acceptance.
- GitHub Actions is the automatic hosted acceptance system. Never report a run as PASS until the exact head SHA and all required jobs have completed successfully.
- Keep local and remote workspaces isolated. Never reset, clean, overwrite, or cherry-pick over local dirty work.
- One active implementation owner per file. Start parallel tasks only when their file ownership does not overlap.
- Keep a single active monitor loop. Do not create scheduled reminders, cron jobs, or redundant polling loops for iterative work.

## Start-of-session reconciliation

Run these read-only checks before choosing work:

```powershell
git status --short --branch
git rev-parse HEAD
git fetch origin --prune
git branch -vv
gh issue list --state open --limit 30 --json number,title,labels,updatedAt,url
gh pr list --state open --base main --limit 30 --json number,title,headRefName,baseRefName,isDraft,updatedAt,url
gh run list --limit 30 --json databaseId,name,displayTitle,headBranch,headSha,status,conclusion,createdAt,url
```

If `gh` is unavailable or unauthenticated, use the configured GitHub connector/MCP. Mark remote state UNKNOWN if neither route can read it; do not infer a green state from old ledger entries.

Compare:
- Local branch and HEAD against its upstream and the task's recorded BASE_SHA / FINAL_CANDIDATE_SHA.
- Local dirty paths against every active task's OWNED_FILES and NO_TOUCH.
- Open task PRs against the current main SHA, recent commits, requested changes, unresolved review conversations, and required Actions runs.
- Every running or failed workflow against the exact head SHA it is testing.

## Two active lanes

### Local lane — Claude Code on the user's machine

Own the tasks requiring Windows, actual user media, FFmpeg, Chromium/Edge, local filesystem locks, real project data, or interactive desktop behavior.

- Reproduce the defect before changing production behavior.
- Run focused local tests and the required Windows/browser/media acceptance.
- Keep long-running local test/build processes observable; capture exit code, logs, and tested SHA.
- Report only sanitized evidence to GitHub. Never upload credentials, model configuration, private project data, or production media without explicit authorization.
- If a check requires the user to physically interact with the application/device, mark it MISSING/BLOCKED and continue with other unblocked work.

### Remote lane — GitHub branches and Actions

Monitor and advance isolated remote tasks.

- Check all active task branches, PRs, Actions runs, logs, and changed files.
- When a remote task is assigned, continue its implementation and focused tests on its owned branch; do not wait for the local lane to finish if ownership is disjoint.
- When Actions is running, use `gh run watch <run-id> --exit-status` for the relevant run when practical. Inspect failed logs with `gh run view <run-id> --log-failed`.
- If CI fails, identify the failing test and owning file, preserve the failure, fix within the task's scope, push the corrected commit, and let Actions rerun.
- A skipped or path-filtered job is NOT a PASS. A result from an earlier SHA does not certify a newer SHA.

## Reconciliation cycle

While any task or CI run remains active, repeat the following in one active Claude Code session:

1. Refresh local git state and remote PR/Actions state.
2. Detect state changes: new commits, new or failed runs, completed tests, PR updates, new issue information, or ownership conflicts.
3. Update the task's shared status record only when a state transition or new evidence occurs. Avoid duplicate comments and noisy status churn.
4. If one lane is waiting, advance the next unblocked action in the other lane or the next disjoint task in the queue.
5. Route a failure back to the owner of the failing paths; do not duplicate or overwrite their work.
6. Integrate only after exact-SHA CI, required local acceptance, maintainer diff inspection, and branch protection gates pass.
7. Continue until all actionable tasks are closed or every remaining task has a concrete external blocker.

For active delivery, start one local monitor loop automatically:

```
/loop 5m /parallel-watch
```

Do not start another loop if one is already active. This is an in-session monitor, not a scheduled reminder. It runs only while the local Claude Code session is active and is bounded by the CLI's loop limit. GitHub Actions continues independently when the local session is closed, but Claude cannot observe local machine state while it is offline.

## Shared task status

Keep the active PR/task record updated with these fields:

- `LOCAL_LANE_STATUS`: `NOT_STARTED`, `ACTIVE`, `BLOCKED`, `PASS`, `FAIL`, `MISSING`, or `NOT_APPLICABLE`.
- `REMOTE_LANE_STATUS`: `NOT_STARTED`, `ACTIVE`, `CI_PENDING`, `BLOCKED`, `PASS`, `FAIL`, or `NOT_APPLICABLE`.
- `FINAL_CANDIDATE_SHA`: exact current candidate commit.
- `NEXT_ACTION`: one concrete action that can be executed now, or the exact external blocker.

Update the PR/task record when a state changes; don't post a comment every polling cycle. The task record is the shared handoff between the local Claude Code session and remote execution.

## Stop conditions

Stop active polling only when:
- no task is ACTIVE and no relevant Actions run is queued or in progress; or
- all remaining work is blocked by a specific user-only decision, permission, physical acceptance, or unavailable external service.

Before stopping, record the blocker, last checked SHA, local status, remote status, and next action. Do not invent extra features or widen scope merely to keep the loop busy.