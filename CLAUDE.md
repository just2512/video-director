# Claude Code instructions for video-director

@AGENTS.md

Before any implementation, test-only modification, delegation, review, or integration:
1. Read `docs/parallel-execution-protocol.md` in full.
2. Treat it as a mandatory repository constraint for all new work.
3. Never self-assign a task without a frozen main SHA, exclusive file ownership, no-touch list, dependencies, and task card.
4. The repository owner may coordinate and self-review a task; optional second-agent read-only review is not a required approval gate.
5. Use short-lived `work/wN-*` branches and PR-only integration to `main`.
6. Preserve failing evidence and all acceptance thresholds. Do not close Windows/media-dependent tasks until required local acceptance is recorded.

When a required task field or ownership boundary is missing, stop and report the missing prerequisite. Do not infer permission from an old branch, issue description, or earlier conversation.
