# Parallel delivery workstreams

**Integration target:** `main` only. No long-lived work branches and no PR-based handoff.  
**Operational status:** see [execution-ledger.md](execution-ledger.md) for the live task/dependency ledger and evidence.

## Operating model

Parallelize independent ownership lanes and GitHub-hosted CI jobs, not competing writes to shared files. Each workstream must have:
- an explicit file/contract boundary;
- a business-level acceptance condition;
- scoped tests and exact-SHA evidence;
- a no-touch list;
- a named dependency before integration.

Source changes are integrated directly into `main`, serially, after review against the current main SHA. Where isolated workspaces are used for preparation, they must not become long-lived branches or PRs. The single coordinator owns shared contracts and the final integration.

## Workstream lanes

| Lane | Scope | Primary regression |
|---|---|---|
| W1 Generation durability (19D) | Generation reservation/state machine, provider submission identity, crash recovery, Manifest/Registry idempotency | `tests/test_generation_recovery.py`, `tests/test_generation_gap1.py`, `tests/test_film_transaction_recovery.py`, `tests/test_project_manifest.py`, `tests/test_bulk_management.py` |
| W2 Timeline transaction state (19D) | Multi-select, cross-track drag, Undo/Redo, stale revisions, project-switch state, marker transactions and clip-lock enforcement | `tests/test_editor_undo_redo.py`, `tests/test_film_studio_timeline_history_browser.py`, `tests/test_film_studio_pending_revision.py`, `tests/test_film_studio_external_watch_browser.py`, `tests/test_editor_command_project_switch_browser.py`, `tests/test_visual_overlay_timeline.py`, `tests/test_audio_clip_timeline.py`, `tests/test_film_document_contract.py`, `tests/test_film_timeline_markers.py`, `tests/test_film_clip_lock.py`, `tests/test_film_external_watch_history.py`, `tests/test_film_transaction_recovery.py`, `tests/test_film_studio_ai_proposal_browser.py`, `tests/test_film_studio_ai_stream_contract.py`, `tests/test_film_studio_roll_browser.py` |
| W3 Lifecycle/export acceptance (19D) | Create, migrate, edit, reopen, render, QC, deterministic supported HTML-scene frame contract and artifact provenance | `tests/test_project_manager.py`, `tests/test_project_manager_film_authority.py`, `tests/test_film_full_pipeline_e2e.py`, `tests/test_film_studio_migration_preview_browser.py`, `tests/test_film_render_publication_transaction.py`, `tests/test_film_render_scene_e2e.py`, `tests/test_film_scene_runtime.py` |
| W4 Project home/create UX (07D) | Project-first hierarchy and existing create-flow promotion | `tests/test_project_creation_workspace_browser.py`, `tests/test_create_dialog_cancel_browser.py`, `tests/test_create_dialog_persistence_browser.py` (including project width/height/FPS restore and legacy platform fallback) |
| W5 Settings IA (07D) | Categorized settings, progressive disclosure, safe save/import/export UX | `tests/test_settings_information_architecture_browser.py` (including export secret redaction and partial-import key preservation), `tests/test_model_config_persistence_contract.py` |
| W6 Hosted CI orchestration | GitHub Actions workflows, scoped lanes, artifact retention, exact-SHA provenance | Workflow syntax validation plus each lane's scoped suite |
| W7 Shared shell integration | `index.html`, `app.js`, navigation and view-state integration | Project home/settings navigation and full browser E2E |

## Parallelism and integration

- W1 and W2 can work concurrently: generation persistence and timeline interaction have separate primary file ownership.
- W3 can prepare lifecycle fixtures and acceptance assertions concurrently, but final integrated lifecycle execution waits for W1 and W2 contracts.
- W4 and W5 can develop isolated JS/CSS modules concurrently. They must not edit `index.html` or `app.js`; W7 integrates those shared files in a reserved serial window.
- W6 can migrate and validate hosted CI concurrently with read-only audits and isolated test preparation.
- W7 is serial: one writer owns shared shell files and integrates only after W4/W5 contracts are stable. Its browser suite includes the integrated home → create/cancel/resume → settings save/import/export journey, not just isolated route checks.
- Shared contracts (FilmDocument revision, project context, create lifecycle, model-config persistence and provider identity) are never modified by two lanes simultaneously.

## Runner policy

**GitHub-hosted runners only.** Use `ubuntu-24.04` for Linux/browser/media suites and `windows-latest` for Windows-native publication tests. No workflow may target `self-hosted`, local WSL, developer workspaces, or local model configuration. The obsolete self-hosted AI Director workflow has been removed; `full-hosted-regression.yml` is the canonical release gate. Live NVIDIA acceptance is a separately gated, explicit opt-in using only the dedicated GitHub Actions secret on a hosted runner.

Documentation-only commits do not rerun the expensive full test suite. Every code/workflow change does; the ledger records the exact tested code SHA. Per-branch concurrency cancels superseded main runs so CI capacity is spent on the latest source revision.

## Acceptance rule

A workstream PASS is not an integrated PASS. After each integration commit, rerun affected hosted scoped tests. Final acceptance requires all required workstream tests, browser E2E and full hosted regression to pass on the exact same `main` SHA. Never weaken or skip a gate to make the workflow green.


## Acceptance coverage refinements (2026-11-09)

- W2 includes the revision-bound AI proposal preview/reject/apply and stream contract browser tests; these are part of the 19D AI editing contract, not a separate timeline authority.
- W4 verifies project-scoped output profile restoration (width, height, FPS), clean defaults when switching to a project without stored settings, and legacy platform-only aspect-ratio fallback.
- W5 verifies export redacts API keys and partial import preserves local keys for both submitted and omitted roles.
- These refinements add regression coverage only; they do not change FilmDocument, project lifecycle, model-config, or provider contracts.

- W7 now has a dedicated browser contract test: `tests/test_product_shell_navigation_browser.py` verifies home → editor → settings → home, exactly one visible pane and active tab, stable in-page URL, and the global create action in every space. Its CI job is isolated on GitHub-hosted Ubuntu.
