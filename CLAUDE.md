# Claude Code instructions for video-director

@AGENTS.md

Before any implementation, test-only modification, delegation, review, or integration:
1. Read `docs/parallel-execution-protocol.md` in full.
2. Treat it as a mandatory repository constraint for all new work.
3. Never self-assign a task without a frozen main SHA, exclusive file ownership, no-touch list, dependencies, and task card.
4. The repository owner may coordinate and self-review a task; optional second-agent read-only review is not a required approval gate.
5. Use short-lived `work/wN-*` branches and PR-only integration to `main`.
6. Preserve failing evidence and all acceptance thresholds. Do not close Windows/media-dependent tasks until required local acceptance is recorded.

## Active dual-line execution

This repository is maintained by one owner. Do not wait for the owner to repeatedly ask for status or the next step.

- At session start, inspect the local worktree and reconcile open GitHub Issues, PRs, branches, and Actions runs.
- When any task or relevant CI run is active, automatically start one `/loop 5m /parallel-watch` using the `parallel-watch` skill. Do not ask the owner to start it, and do not create duplicate loops.
- Own local Windows/real-media reproduction and acceptance while monitoring remote GitHub work and Actions status in parallel.
- When one lane is waiting, take the next unblocked task with disjoint file ownership. Continue through implementation, focused tests, exact-SHA hosted CI, and integration; don't stop after a progress summary.
- Keep the PR task record's LOCAL_LANE_STATUS, REMOTE_LANE_STATUS, FINAL_CANDIDATE_SHA, and NEXT_ACTION current at state transitions.
- Stop only when no actionable task or relevant run remains, or all remaining work has a concrete external/user-only blocker. Never invent scope to keep working.
- Local monitoring runs only while this Claude Code session is alive. GitHub Actions continues independently; resume local monitoring when the session returns.

The detailed operating procedure is `.claude/skills/parallel-watch/SKILL.md`.

When a required task field or ownership boundary is missing, stop and report the missing prerequisite. Do not infer permission from an old branch, issue description, or earlier conversation.
