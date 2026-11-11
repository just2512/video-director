# Parallel delivery workstreams

**Integration target:** `main` only. Use short-lived, task-scoped work branches from one frozen main SHA; integrate directly to `main` without PR-based handoff. Delete branches after exact-SHA acceptance and integration.  
**Operational status:** see [execution-ledger.md](execution-ledger.md) for the live task/dependency ledger and evidence.

## Operating model

Parallelize independent ownership lanes and GitHub-hosted CI jobs, not competing writes to shared files. Each workstream must have:
- an explicit file/contract boundary;
- a business-level acceptance condition;
- scoped tests and exact-SHA evidence;
- a no-touch list;
- a named dependency before integration.

Create independent short-lived branches from one frozen `main` SHA for workstreams with disjoint file ownership. Each branch runs scoped GitHub-hosted CI against its own exact SHA. No PR/manual-review handoff is required: the coordinator integrates completed branches directly into `main` in dependency order, preserving intervening main fixes and resolving conflicts explicitly. Branches are temporary execution units, not long-lived release lines. The single coordinator owns shared contracts and final integration. Never allow two branches to write the same protected/shared file concurrently.

## Workstream lanes

| Lane | Scope | Primary regression |
|---|---|---|
| W1 Generation durability (19D) | Generation reservation/state machine, provider submission identity, crash recovery, Manifest/Registry idempotency | `tests/test_generation_recovery.py`, `tests/test_generation_gap1.py`, `tests/test_film_transaction_recovery.py`, `tests/test_project_manifest.py`, `tests/test_bulk_management.py` |
| W2 Timeline transaction state (19D) | Multi-select, cross-track drag, Undo/Redo, stale revisions, project-switch state, marker transactions and clip-lock enforcement | `tests/test_editor_undo_redo.py`, `tests/test_film_studio_timeline_history_browser.py`, `tests/test_film_studio_pending_revision.py`, `tests/test_film_studio_external_watch_browser.py`, `tests/test_editor_command_project_switch_browser.py`, `tests/test_visual_overlay_timeline.py`, `tests/test_audio_clip_timeline.py`, `tests/test_film_document_contract.py`, `tests/test_film_timeline_markers.py`, `tests/test_film_clip_lock.py`, `tests/test_film_external_watch_history.py`, `tests/test_film_transaction_recovery.py`, `tests/test_film_studio_ai_proposal_browser.py`, `tests/test_film_studio_ai_stream_contract.py`, `tests/test_film_studio_roll_browser.py` |
| W3 Lifecycle/export acceptance (19D) | Create, migrate, edit, reopen, render, QC, deterministic supported HTML-scene frame contract, generated-asset manual adoption and artifact provenance | `tests/test_project_manager.py`, `tests/test_project_manager_film_authority.py`, `tests/test_film_full_pipeline_e2e.py`, `tests/test_film_studio_migration_preview_browser.py`, `tests/test_film_render_publication_transaction.py`, `tests/test_film_render_scene_e2e.py`, `tests/test_film_scene_runtime.py`, `tests/test_film_studio_generated_asset_adoption_browser.py`, `tests/test_asset_catalog.py` (Asset Library catalog persistence, reference-safe deletion, and cross-process transaction lock), and `tests/test_film_variant_migration_api.py` (explicit variant selection and unknown-ID fail-closed HTTP contract); also runs `tests/test_film_document_contract.py` as a shared FilmDocument lifecycle/serialization contract |
| W4 Project home/create UX (07D) | Project-first hierarchy and existing create-flow promotion | `tests/test_project_creation_workspace_browser.py`, `tests/test_create_dialog_cancel_browser.py`, `tests/test_create_dialog_persistence_browser.py` (including project width/height/FPS restore and legacy platform fallback) |
| W5 Settings IA (07D) | Categorized settings, progressive disclosure, safe save/import/export UX | `tests/test_settings_information_architecture_browser.py` (including export secret redaction and partial-import key preservation), `tests/test_model_config_persistence_contract.py` |
| W6 Hosted CI orchestration | GitHub Actions workflows, scoped lanes, artifact retention, exact-SHA provenance | Workflow syntax validation plus each lane's scoped suite |
| W7 Shared shell integration | `index.html`, `app.js`, navigation and view-state integration | Project home/settings navigation and full browser E2E |

## Parallelism and integration

- W1 and W2 can work concurrently on separate short-lived branches: generation persistence and timeline interaction have separate primary file ownership.
- W3 can implement resource snapshot/export consistency and prepare lifecycle fixtures concurrently with W1/W2, but final integrated lifecycle execution waits for W1 and W2 contracts.
- W4 and W5 can develop isolated JS/CSS modules concurrently. They must not edit `index.html` or `app.js`; W7 integrates those shared files in a reserved serial window.
- W6 can migrate and validate hosted CI concurrently with read-only audits and isolated test preparation.
- W7 is serial: one writer owns shared shell files and integrates only after W4/W5 contracts are stable. Its browser suite includes the integrated home → create/cancel/resume → settings save/import/export journey, not just isolated route checks.
- Shared contracts (FilmDocument revision, project context, create lifecycle, model-config persistence and provider identity) are never modified by two lanes simultaneously.

## Runner policy

**GitHub-hosted runners only.** Use `ubuntu-24.04` for Linux/browser/media suites and `windows-latest` for Windows-native publication tests. No workflow may target `self-hosted`, local WSL, developer workspaces, or local model configuration. The obsolete self-hosted AI Director workflow has been removed; `full-hosted-regression.yml` is the canonical release gate. Live NVIDIA acceptance is a separately gated, explicit opt-in using only the dedicated GitHub Actions secret on a hosted runner.

Documentation-only commits do not rerun the expensive full test suite. Every code/workflow change does; the ledger records the exact tested code SHA. Each short-lived branch has independent concurrency and exact-SHA scoped CI; superseded runs on that branch may be cancelled, but runs on other branches must not cancel one another. After direct integration, rerun affected lanes and the integrated full gates on the resulting exact `main` SHA.

## Acceptance rule

A workstream PASS is not an integrated PASS. After each integration commit, rerun affected hosted scoped tests. Final acceptance requires all required workstream tests, browser E2E and full hosted regression to pass on the exact same `main` SHA. Never weaken or skip a gate to make the workflow green.


## React Studio lane parity correction (2026-11-10)

- W2's hosted lane must exercise the same React 19 timeline surface as the Studio release gate. It now builds `studio_ui` (typecheck, React bundle and Tailwind CSS) and sets `VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO=1` before running timeline browser regressions.
- A W2 PASS from the earlier fallback-only lane is not evidence for React timeline behavior. Exact-SHA W2 and integrated Studio release-gate results remain separate acceptance records.
- W7 remains the only serial owner of shared shell files. This W2 CI correction is owned by W6 and does not change W2 production ownership or shared-shell boundaries.

## Acceptance coverage refinements (2026-11-09)

- W2 includes the revision-bound AI proposal preview/reject/apply and stream contract browser tests; these are part of the 19D AI editing contract, not a separate timeline authority.
- W4 verifies project-scoped output profile restoration (width, height, FPS), clean defaults when switching to a project without stored settings, and legacy platform-only aspect-ratio fallback.
- W5 verifies export redacts API keys and partial import preserves local keys for both submitted and omitted roles.
- These refinements add regression coverage only; they do not change FilmDocument, project lifecycle, model-config, or provider contracts.

- W7 now has a dedicated browser contract test: `tests/test_product_shell_navigation_browser.py` verifies home → editor → settings → home, exactly one visible pane and active tab, stable in-page URL, and the global create action in every space. Its CI job is isolated on GitHub-hosted Ubuntu.


## W3 lifecycle scope reconciliation (2026-11-10)

- The W3 hosted job also runs `tests/test_film_studio_generated_asset_adoption_browser.py`. This is a lifecycle acceptance test: a generated asset must remain a reusable library item until explicit user adoption; a generation request overlapping a user edit must not overwrite the newer FilmDocument revision; stale-revision adoption must fail; explicit adoption must survive a full-page reload.
- W3 also runs `tests/test_film_document_contract.py` as a shared FilmDocument serialization/lifecycle integration contract. W2 remains the primary owner of timeline operation semantics in that file; the W3 invocation is an additional lifecycle dependency check, not a transfer of W2 ownership.
- W1–W7 are parallel ownership and independent hosted-CI lanes on the single `main` integration target. They are not seven long-lived branches. W7 remains the only serial writer for `index.html` and `app.js`.


## Asset Library lifecycle lane reconciliation (2026-11-10)

- W3 owns the Asset Library's persistent catalog lifecycle alongside project assets: content-hash deduplication, cross-process read/modify/write serialization, semantic updates, project-reference-safe deletion, and corrupt/symlinked catalog fail-closed behavior. The scoped W3 job now runs `tests/test_asset_catalog.py` (in addition to the full regression suite).
- The Windows-native publication workflow now separately runs `test_catalog_file_lock_serializes_independent_processes` against the actual Windows `msvcrt.locking` branch. The existing `MoveFileExW` durability test alone did not exercise this distinct lock implementation.
- W3's Linux lane exercises the POSIX `fcntl.flock` branch; the Windows-native lane exercises `msvcrt.locking`. These are separate platform-specific acceptance results, not interchangeable evidence.


## Frontend accessibility acceptance extension (2026-11-10)

- W4 owns keyboard focus containment/restoration for the full-screen Create Project dialog. The dialog must receive initial focus, keep Tab/Shift+Tab inside its modal boundary, preserve the existing explicit-close/save-draft behavior, and return focus to the invoking control after close/finalization. Escape and backdrop must remain non-dismiss actions as already specified by the create workflow.
- W7 owns top-level project/workspace/settings navigation semantics in the shared shell. Native keyboard activation must route correctly, the active navigation control must expose `aria-current="page"`, and inactive panes must be hidden from assistive technology as well as visually hidden.
- W4 and W7 tests must use real Chromium keyboard input and DOM/focus assertions. Static presence of ARIA attributes alone is not sufficient.
- Ownership boundaries remain unchanged: W4 owns create-dialog behavior; W7 alone edits shared `index.html` / `app.js`. W4/W7 retain their independent Product IA and Parallel workstream CI jobs respectively.


## Hosted action runtime baseline (2026-11-10)

- W6 maintains supported GitHub-hosted action majors: `actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-node@v7`, and `actions/upload-artifact@v7`.
- These releases use the Node.js 24 action runtime, avoiding GitHub's Node.js 20 forced-runtime deprecation warning.
- Action-version changes are W6-owned. They do not change W1–W7 product ownership or the requirement to rerun each independent lane and integrated release gates on the exact same code/workflow SHA.


## W4/W7 accessibility exact-SHA closure (2026-11-10)

- W4 Create Project dialog focus lifecycle and W7 shared-shell keyboard/ARIA semantics are implemented and verified with real Chromium input on executable/workflow SHA `9d7208cc9818839ea2c9cc5965298e0998c28b68`.
- W4 project-home/create lane: **36 passed** (run `42821702016`). Coverage includes initial focus, Tab/Shift+Tab containment, background keyboard isolation, focus restoration after close/finalization, and stable invoker restoration after reopening a saved draft. Existing explicit-close/save-draft semantics remain unchanged; Escape and backdrop remain non-dismiss actions.
- W7 shell-navigation lane: **2 passed** (run `42821701905`). Real Chromium keyboard activation verifies Project/Workspace/Settings navigation, active `aria-current="page"`, inactive-pane `aria-hidden`, exactly one visible pane, stable in-page URL and global create action.
- Integrated full pytest: **1,952 passed, 2 skipped, 0 failures/errors**; Studio release gate: **346 passed**; both on the same SHA (run `42821701972`). W1/W2/W3/W5 and Windows-native evidence is recorded in `execution-ledger.md` §47.
- W4 and W7 ownership boundaries are unchanged. W7 remains the only writer of `webui/static/index.html` and `webui/static/js/app.js`; no router migration or second project state machine was introduced.
- These results close the accessibility acceptance extension for the tested tree. Any later code/workflow change requires the affected scoped lanes and integrated hosted gates to run against its exact SHA.

## Short-lived branch execution protocol (restored 2026-11-10)

- Freeze one base `main` SHA before creating task branches.
- Frozen baseline for the current batch: `fe20b42770660db215d1f5f9210a2046d30b0416`.
- Active short-lived branches, with disjoint write ownership:
  - `work/w1-generation-crash-recovery-20261111`: W1 owns only `tests/test_generation_recovery.py`; process-crash boundary after provider acceptance.
  - `work/w2-canvas-history-project-isolation-20261111`: W2 owns only `tests/test_film_document_contract.py`; independent per-project undo/redo histories.
  - `work/w3-asset-catalog-transaction-audit-20261111`: W3 owns `tests/test_asset_catalog.py` and `tests/test_film_render_scene_e2e.py`; cross-process semantic merge and JUnit warning cleanup.
  - `work/w6-next-batch-branch-register-20261111`: W6 owns workflow/docs; adds the Asset Library cross-process merge test to Windows-native CI and updates the live ledger.
- W1/W2/W3 scoped Linux CI and W6 workflow syntax are PASS on the exact SHAs recorded in ledger §58. W6's added Windows-native semantic-merge test remains OPEN until main integration.
- W1/W2/W3/W6 were developed concurrently from the same frozen main SHA with disjoint write ownership. Integrate completed branches serially after scoped PASS, then rerun affected lanes and integrated gates on one exact main SHA.
- Branch-scoped workflow filters execute only the owning lane on `work/w1-*` through `work/w7-*`; the `main` integration target still executes all applicable lanes. This prevents every task branch from paying for unrelated W1/W2/W3/W7 suites.
- Integrate completed W1/W2/W3 test-only branches and W6 workflow changes serially to `main` after scoped acceptance, then run W1/W2/W3 affected lanes, Studio release gate, full Python/browser, Windows-native and YAML validation on the same final main SHA.
- No PR/manual reviewer handoff; the coordinator performs direct integration and conflict resolution. Delete each short-lived branch only after its commits are integrated and exact-main acceptance is recorded.


## Active branch register — 2026-11-11 follow-up

All code branches below start from frozen `main` SHA `5935b3fddcaad09606ca5d014dad3623e1d3ddc0`. Their file ownership is disjoint; the coordinator integrates only after each scoped lane passes.

| Lane | Branch | Exclusive write ownership | Acceptance |
|---|---|---|---|
| W1 | `work/w1-terminal-recovery-races-20261111` | `tests/test_generation_recovery.py` | Generation durability workflow; stale poll cannot downgrade `downloading` / terminal state |
| W2 | `work/w2-multitab-stale-revision-20261111` | `tests/test_film_document_contract.py` | Timeline transaction workflow; second tab's stale base revision is rejected without file mutation |
| W3 | `work/w3-asset-delete-race-20261111` | `tests/test_asset_catalog.py` | Lifecycle and focused storage/watch workflows; cross-process manifest writer serializes with catalog deletion |
| W6 | `work/w6-next-batch-register-20261111` | `docs/execution-ledger.md`, `docs/parallel-workstreams.md` only | Documentation register; no production/test ownership |

### Integration discipline for this batch

- W1, W2 and W3 are independent test-only branches and may execute concurrently on GitHub-hosted runners.
- W6 is documentation-only and does not own workflow YAML or code/test files.
- Do not cherry-pick or merge while a scoped job is running. A failed scoped lane returns to its owning branch for correction and rerun.
- After all scoped lanes pass, integrate W1 → W2 → W3 → W6 serially, preserving intervening main commits. Then run the exact-main integrated gates listed in the execution ledger.
- These are short-lived branches, not new long-lived release branches; no PR/manual-review handoff is required.


### Additional W6 platform-specific branch

| Lane | Branch | Exclusive write ownership | Acceptance |
|---|---|---|---|
| W6 | `work/w6-windows-asset-reference-race-20261111` | `tests/test_asset_catalog_windows_process.py`, `.github/workflows/windows-native-publication.yml` | YAML validation on branch; after main integration, Windows-hosted `spawn` test proves manifest reference writes serialize with catalog deletion under `msvcrt.locking` |

This W6 branch is independent of the W3 Linux `fork` regression and does not modify W3-owned files.


## Active branch register — 2026-11-11 next batch

All branches below are frozen from main SHA 12aac891b12b0ec577c318981396db398e080400. File ownership is exclusive and disjoint.

| Lane | Branch | Exclusive write ownership | Acceptance |
|---|---|---|---|
| W1 | work/w1-generation-output-finalization-20261111 | tests/test_generation_recovery.py | Process death after MP4 atomic replace but before terminal job marker; recovery reuses bytes without a second download |
| W2 | work/w2-real-multitab-revision-20261111 | tests/test_film_studio_external_watch_browser.py | Two real Chromium tabs, held stale edit request, server-side REVISION_CONFLICT, no overwrite of committed bytes |
| W3 | work/w3-resource-snapshot-benchmark-harness-20261111 | video_director/film_resource_snapshot.py, scripts/benchmark_resource_snapshot.py, tests/test_film_render_scene_e2e.py | Opt-in real-media snapshot measurements with separate read/copy+fsync/hash timings; fixture tests validate only the harness contract |
| W6 | work/w6-followup-register-20261111 | docs/execution-ledger.md, docs/parallel-workstreams.md | Documentation reconciliation; existing W2/W3 lane commands already include their test modules, so no workflow edit is required |

- W1, W2 and W3 may run concurrently on GitHub-hosted Ubuntu from this same base. W6 maintains the CI lane wiring and records evidence; it does not modify W1/W2/W3-owned files.
- W2 reuses the already-owned external-watch browser test module so the new real two-tab regression is executed by the existing W2 lane without a duplicate test entry or a second workflow owner.
- W3's production measurement requires real representative media. A small test fixture is permitted only for testing report shape, path validation and hash consistency; it must never be cited as a production benchmark.
- Integrate W1 → W2 → W3 → W6 only after exact-SHA scoped PASS. Then run W1/W2/W3, focused storage/watch, Studio release gate, full Python/browser, Windows-native and YAML on one exact main SHA.


## Next-batch CI reconciliation — 2026-11-11

- Integrated main SHA 12aac891b12b0ec577c318981396db398e080400 passed the full hosted acceptance: 1,969 passed, 2 skipped, 0 failures/errors; Studio release gate 356 passed; W1/W2/W3/W7, Product IA, focused storage, Windows-native (5 passed) and YAML all SUCCESS. NVIDIA live acceptance remained explicitly skipped.
- W1 next-batch exact SHA 44c8a3468ddfc7d1448dcbd726d4a6f233ea0313: 95 passed; focused storage 173 passed, 1 skipped; YAML SUCCESS.
- W3 latest exact SHA 969bd0a4e3fb26b3faaf11ad19b80eaa84c558a6: 304 passed, 1 skipped; YAML SUCCESS. The harness reports project-volume free-space delta, separate read/copy+fsync/hash-update timings, source revalidation and copied-hash verification. Existing copy_elapsed_ms semantics remain unchanged.
- W2 next-batch exact SHA 50479f987b41fa8a7811ef1fb4f679adf43b5080: 361 passed, including the two-real-tab stale-revision regression; YAML SUCCESS. The first attempt exposed only a response-observation defect in the test; route.fetch/fulfill now forwards the held request to the real API and verifies the actual conflict response.
- W6 workflow syntax SHA db427b5bd94201b2765c9002d67e5510fc4124f9: SUCCESS. Do not integrate until W2 exact-SHA lane passes.
- Actual large-media performance measurements remain MISSING until representative project media is supplied; fixture tests are harness-contract checks only.


- Final next-batch owner-lane closure: W1 44c8a3468ddfc7d1448dcbd726d4a6f233ea0313 PASS (95 tests; focused storage 173 passed, 1 skipped); W2 50479f987b41fa8a7811ef1fb4f679adf43b5080 PASS (361 tests); W3 969bd0a4e3fb26b3faaf11ad19b80eaa84c558a6 PASS (304 passed, 1 skipped). W6 documentation register is ready. Serial integration is now unblocked.
