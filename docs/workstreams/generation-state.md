# Generation state workstream

Integration target: `main` (direct commits; no branch or PR)

Own the generation task lifecycle and durable persistence contract:
- Define legal monotonic state transitions and terminal-state behavior.
- Reconcile `generation_jobs.json`, Manifest, and Asset Registry after every crash point.
- Make registry/manifest retries idempotent and prove provider submission is not repeated after an ambiguous or completed submission.
- Add concurrency tests for simultaneous recovery and stale provider observations.
- Concurrent `generate()` callers sharing one paid task must converge to one output path, one deterministic Manifest asset ID, and one content-hash-deduplicated Registry entry.
- A late joiner must reuse an already committed `atomic-v2` output without polling or downgrading a terminal `done` job.
- Recover the crash window after Asset Registry commit but before the generation-job registration marker is persisted.

Primary regression gate: `tests/test_generation_recovery.py`, `tests/test_generation_gap1.py`, `tests/test_film_transaction_recovery.py`, `tests/test_project_manifest.py`, and `tests/test_bulk_management.py`.

Avoid timeline UI files. Changes to shared persistence contracts must be documented for the lifecycle workstream.

CI note: this workstream is Python-only; it skips Chromium, FFmpeg, and CJK font installation. Browser/media dependencies are reserved for timeline and lifecycle branches.
