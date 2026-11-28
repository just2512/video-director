# Claude Code instructions for video-director

@AGENTS.md

Before any implementation, test-only modification, delegation, review, or integration:
1. Read `docs/parallel-execution-protocol.md` in full.
2. Treat it as a mandatory repository constraint for all new work.
3. Never self-assign a task without a frozen main SHA, exclusive file ownership, no-touch list, dependencies, task card, and independent reviewer.
4. Never let the same agent/session both implement and independently approve or coordinate integration of the same task.
5. Use short-lived `work/wN-*` branches and PR-only integration to `main`.
6. Preserve failing evidence and all acceptance thresholds. Do not close Windows/media-dependent tasks until required local acceptance is recorded.

When a required field or independent owner is missing, stop and report the missing prerequisite. Do not infer permission from an old branch, issue description, or earlier conversation.
