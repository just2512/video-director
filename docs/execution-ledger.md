# Execution ledger — dual-plan delivery

**Repository:** `just2512/video-director`  
**Baseline main SHA:** `c221a0b4fe4eb8e572e85fc20bb7ec4afc2ab4f1`  
**Updated:** 2026-11-09  
**Purpose:** operational status and dependency ledger only. This file does not replace, merge, or rewrite either source plan.

## 0. Non-negotiable execution rules

1. The 2026-10-19 OpenFilm/Film Studio work and the 2026-11-07 Product IA work remain separate delivery lines.
2. `main` is the only integration branch. No long-lived work branches or PR workflow is introduced.
3. Parallelism means independent work ownership and independent GitHub-hosted CI jobs. Shared production files are changed serially by the coordinator.
4. **No self-hosted/local runner, WSL runner, developer checkout, or local model-config file may be used by repository CI.** GitHub Actions jobs must use GitHub-hosted runners (Ubuntu or Windows).
5. Real-provider acceptance may only run as an explicit opt-in on a GitHub-hosted runner with GitHub Actions Secrets. No local runner credentials or local storage are read.
6. A test added is not a feature delivered. A green scoped job is not an integrated PASS. Every PASS must identify the code SHA, business behavior, and test evidence.
7. Missing evidence remains `UNKNOWN` or `MISSING`; it is never inferred from an older commit or documentation prose.

### Status vocabulary

- `PASS`: implementation and the stated behavior have direct evidence.
- `FAIL`: a reproducible behavior violates the contract.
- `MISSING`: implementation or required evidence does not exist.
- `UNKNOWN`: current evidence is insufficient to decide.
- `BLOCKED`: work cannot safely proceed until a named dependency is resolved.

## 1. Source plans and scope boundaries

| ID | Source of truth | Scope |
|---|---|---|
| 19D | `docs/openfilm-film-editor-refactor.md`, `docs/openfilm-gap-and-target-architecture.md`, `docs/FILM_AGENT_MANUAL.md`, `CONTRACT.md`, `INTEGRATION-GATES.md` | FilmDocument authority, unified Studio, timeline transactions, AI proposal/apply, project refresh/recovery, generation lifecycle, export and QC |
| 07D | `docs/product-information-architecture-audit.md` | Project-first home/create flow, draft lifecycle, settings IA, credential preservation, navigation and project defaults |

A constraint may be shared (for example, one FilmDocument authority), but a task may not be moved from one plan to the other or counted as delivery for both without separate acceptance evidence.

## 2. Current delivery ledger

The baseline is `c221a0b4fe4eb8e572e85fc20bb7ec4afc2ab4f1`. Historical CI evidence is not automatically evidence for this SHA. The active AI Director run recorded during this audit is listed separately in section 5.

### 2.1 19D — Film Studio / OpenFilm delivery

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 19D-01 | `film.html` is the sole timeline authority once present; legacy CompositionIR writes/exports fail closed | PASS (documented implementation) | UNKNOWN | Verify current main code paths and authority tests; do not reintroduce a second timeline |
| 19D-02 | Revision-pinned project context, stale-response rejection, project-switch isolation | PASS (documented implementation) | UNKNOWN | Re-run project-context, external-watch and project-switch tests on a hosted runner |
| 19D-03 | AI proposal preview/reject/apply is revision-bound and does not write before approval | PASS (documented implementation) | UNKNOWN | Re-run proposal browser tests on current main |
| 19D-04 | Multi-select and cross-track drag form one atomic transaction; Undo/Redo restore full affected state | UNKNOWN | IN PROGRESS — W2 hosted browser suite running on latest code SHA `7a04012423` | Recent commits `cb366e36`, `4d866259`, `c221a0b4` adjust coverage; latest known full AI Director run before this baseline failed on `3a7ec258`. Verify exact failure and current tests |
| 19D-05 | Timeline gestures reject stale external revisions and project-switch responses | PASS (documented implementation) | UNKNOWN | Existing browser coverage is documented; re-run against exact current main |
| 19D-06 | Generation job reservation, provider identity, ambiguous submission, download recovery and Manifest/Registry idempotency | PASS | PASS — W1 hosted suite succeeded on latest code SHA `7a04012423`, run `42618988136` | Nov 9 commits changed provider identity and recovery contracts; run generation recovery, provider matrix and manifest regressions |
| 19D-07 | Manual generated-asset adoption; generation completion cannot overwrite a newer user revision | PASS (implementation recorded) | IN PROGRESS — adoption regression is part of W3 run `42618988136` | Re-run manual-adoption browser E2E on current main |
| 19D-08 | Project create/open/migrate/edit/reopen lifecycle uses one project and one FilmDocument revision | PASS (implementation recorded) | IN PROGRESS — W3 lifecycle suite running on code SHA `7a04012423` | Re-run the existing integrated lifecycle fixture; do not create a duplicate fixture without a coverage gap |
| 19D-09 | Actual MP4 export and QC artifacts are bound to the exact FilmDocument revision | PASS (implementation recorded) | IN PROGRESS — W3 render/QC suite running on code SHA `7a04012423` | Re-run actual render/QC regression on hosted Ubuntu; verify output metadata, revision, artifact digest and failure behavior |
| 19D-10 | Real model token streaming against a currently supported endpoint | MISSING | MISSING | Prior documented endpoint returned HTTP 410; mocks and historical runs do not count. Only explicit hosted opt-in with GitHub Secrets can close this |
| 19D-11 | Arbitrary HTML/WebGL deterministic rendering and exhaustive edit-operation permutations | UNKNOWN / bounded support | MISSING | Preserve explicit unsupported boundaries; do not claim universal determinism or exhaustive permutation coverage |

### 2.2 07D — Product information architecture

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 07D-01 | Safe partial model-config updates preserve omitted roles/fields and keys | PASS (safe merge path recorded in 2026-11-07 audit closure) | UNKNOWN | Re-run config contract tests on hosted runner; do not inspect or modify a developer's local config |
| 07D-02 | Existing create flow and browser/server draft recovery chain | PASS (existing lifecycle contract) | UNKNOWN | Preserve existing DOM hooks, draft key, server endpoint, upload and WorkflowRun contracts |
| 07D-03 | Project-first home hierarchy and first-class create experience | PASS — project list is default pane and top-level create action is present | IN PROGRESS — W4 browser suite running on code SHA `7a04012423` | Implement the UX over the existing project/create lifecycle; do not create a second backend/state machine |
| 07D-04 | Full-work-area create flow with safe cancel/failure/resume | PASS — `openCreate()` creates a full-viewport workspace over existing lifecycle | IN PROGRESS — W4 create/cancel/draft browser suite running on code SHA `7a04012423` | Depends on 07D-02 regression protection and exact create rollback contract |
| 07D-05 | Categorized settings IA and progressive disclosure | PASS — categorized `<details>` sections and progressive disclosure exist | PASS — W5 browser + persistence tests succeeded on code SHA `7a04012423`, run `42618988066` | Implement UI only; do not change ROLE_DEFS, provider IDs, secret semantics or settings endpoint contract |
| 07D-06 | Shared shell navigation and in-app view contract | PASS for current scope — three spaces are integrated through `switchTab`; no router migration required by this audit | UNKNOWN — no dedicated cross-space navigation E2E recorded | Coordinator owns `index.html` and `app.js`; do not introduce a router migration in this pass |
| 07D-07 | Project-scoped output defaults with backward-compatible reads | UNKNOWN | MISSING | Audit existing project params/CompositionIR/FilmDocument dimensions first; add schema only with compatibility tests |
| 07D-08 | Product E2E: home → create/cancel/resume → settings expand/save/import/export | PARTIAL — W4/W5 cover the flows in separate isolated browser suites | IN PROGRESS — W4 pending; W5 including export redaction and partial import preservation PASS on `7a04012423` | Add/complete browser acceptance after A/B UI and shell integration |

## 3. Workstream partition — what can genuinely run in parallel

Workstreams are logical ownership lanes, not new Git branches. Source writes integrate directly to `main`; CI jobs run on isolated GitHub-hosted runners.

| Lane | Plan | Owns | Can run concurrently with | Must wait for / must not touch |
|---|---|---|---|---|
| W0 Coordinator / contracts | Both | Baseline SHA, dependency ledger, shared contracts, integration order | Read-only audits and independent test design | Serial owner of shared files and main integration; no parallel edits to shared shell |
| W1 Generation durability | 19D | Generation reservation/state machine, job persistence, provider submission identity, Manifest/Registry recovery and focused tests | W2, W3, W4 test preparation, W5 CI migration | Must coordinate before changing shared project-manifest/storage contracts; avoid timeline UI and settings |
| W2 Timeline transaction state | 19D | Multi-select, cross-track drag, Undo/Redo, gesture/revision invalidation and timeline-specific tests | W1, W3, W4 test preparation, W5 CI migration | FilmDocument/revision contract changes require W0 review; avoid generation persistence |
| W3 Project lifecycle / export acceptance | 19D | Existing lifecycle fixtures, migration preview, reopen, actual render/QC assertions; tests first | W1/W2 test authoring and W5 CI migration | Final integrated run waits for W1 and W2 contracts; do not duplicate existing full lifecycle fixture |
| W4 Product home/create UX | 07D | Project-home and create-flow JS/CSS modules; create lifecycle browser tests | W1/W2/W3 test preparation and W5 | Does not own `index.html`, `app.js`, backend create service, FilmDocument authority or model config |
| W5 Settings IA | 07D | Settings-specific JS/CSS modules and settings browser tests | W1/W2/W3/W4 isolated module work | Does not own `index.html`, `app.js`, model_config persistence, API semantics or role/provider IDs |
| W6 Hosted CI and test orchestration | Both | GitHub Actions workflows, hosted runner migration, test result artifacts and SHA provenance | All read-only audits and isolated test preparation | Must not invoke self-hosted/local runner; cannot mark business work PASS |
| W7 Shared shell integration | Both | `webui/static/index.html`, `webui/static/js/app.js`, navigation slots and final integration | Only read-only review | Serial after W4/W5 module contracts are stable; no competing writer |

### File ownership and collision controls

- `webui/static/index.html` and `webui/static/js/app.js`: W0/W7 only; no concurrent A/B edits.
- `webui/static/js/create.js` and project-home-specific styles/modules: W4; preserve existing create form IDs and lifecycle hooks.
- Settings-specific modules/styles: W5; settings markup is integrated by W7 in one reserved edit.
- Generation job/persistence files and generation tests: W1.
- FilmStore/React timeline interaction files and timeline tests: W2.
- Lifecycle and render/QC tests/fixtures: W3; production contract changes go through W0 review.
- `.github/workflows/**`: W6 only.
- `video_director/model_config.py`, settings handlers, provider/role registries, FilmDocument authority and shared project context: protected shared contracts; one writer at a time, explicit tests required.

## 4. Dependency graph and execution order

```text
W6 hosted-runner safety ──┐
W0 baseline + contracts ─┼──> W1 generation ─────┐
                         ├──> W2 timeline ───────┼──> W3 lifecycle/export integrated E2E
                         ├──> W4 project/create ─┤
                         └──> W5 settings IA ────┘
                                      │
                           W7 shared-shell integration
                                      │
                    hosted scoped tests + full hosted regression
                                      │
                         exact-main-SHA business acceptance
```

Execution order:

1. **W6 first:** prevent any new self-hosted/local runner execution and establish hosted-only CI. Do not use local WSL checkout, local secrets or local model config.
2. **W0:** freeze the current main SHA and map exact source/test ownership. Keep 19D and 07D ledgers separate.
3. **Parallel preparation:** W1 and W2 may implement in disjoint files; W3 prepares lifecycle tests; W4 and W5 may implement isolated JS/CSS modules; W6 builds hosted test lanes.
4. **Contract checkpoints:** shared persistence, FilmDocument revision, project context and create lifecycle changes require W0 review before integration.
5. **Serial shell integration:** W7 integrates project and settings UI into `index.html` / `app.js`.
6. **Integrated lifecycle:** W3 runs create/open/migrate/edit/reopen/export/QC after W1/W2 and shell contracts stabilize.
7. **Final acceptance:** all relevant hosted jobs and browser E2E run against the exact same main SHA. Historical evidence is not carried forward after code changes.

## 5. CI and runner policy — current state

| Workflow | Current configuration | Decision |
|---|---|---|
| `ai-director.yml` | Previously targeted `[self-hosted, linux, x64]` and accessed local WSL storage/model config | Disabled at the job level pending a full GitHub-hosted migration; do not dispatch or rerun |
| `parallel-workstream-ci.yml` | GitHub-hosted Ubuntu, but branch-name gated to `work/**` and PRs | Needs conversion to main-only logical lanes before it can serve this main-only plan |
| `product-ia-regression.yml` | GitHub-hosted Ubuntu, but only `work/product-*` branches | Reuse its test commands in hosted main lanes; do not create branches just to trigger it |
| `focused-storage-watch.yml` | GitHub-hosted Ubuntu on selected main paths | Safe to use |
| `studio-ui-build.yml` | GitHub-hosted Ubuntu, manual dispatch | Safe to use when explicitly needed |
| `windows-native-publication.yml` | GitHub-hosted Windows | Safe to use for Windows-native tests |

**Important:** the active run `42616478439` was already in progress on the self-hosted runner when this audit began. It was not started by this change. The available GitHub controls in this session did not expose a cancel action, so this ledger does not claim it was cancelled. No new self-hosted run is to be started.

## 6. Next executable queue

| Order | Task | Lane | Exit condition | State |
|---:|---|---|---|---|
| 1 | Disable self-hosted AI Director job; establish hosted-only runner rule | W6 | No future workflow path allocates a self-hosted runner | PASS — job-level `if: false` verified; run `42617062648` allocated no job |
| 2 | Convert branch-gated workstream CI into hosted main-only lanes | W6 | W1/W2/W3 and W4/W5 run as independent GitHub-hosted jobs from main SHA | IMPLEMENTED; latest exact-main run in progress |
| 3 | Re-run focused generation/provider recovery suite on hosted runner | W1 | Exact-main tests pass; provider submission and manifest invariants verified | PASS — code SHA `7a04012423`, run `42618988136` |
| 4 | Re-run timeline multi-select/cross-track/Undo/Redo browser suite | W2 | Atomic transaction and full undo/redo behavior pass on exact SHA | IN PROGRESS — code SHA `7a04012423`, run `42618988136` |
| 5 | Implement project-home/create UX over existing create lifecycle | W4 | Create/cancel/failure/resume browser scenarios pass | IMPLEMENTATION PASS; hosted W4 browser suite pending on code SHA `7a04012423` |
| 6 | Implement categorized settings IA | W5 | Expand/save/import/export and secret preservation browser scenarios pass | PASS — code SHA `7a04012423`, run `42618988066`; includes new browser export-redaction/partial-import test |
| 7 | Integrate shared shell serially | W7 | One route/view state; no duplicate project or settings state | BLOCKED on 5, 6 |
| 8 | Run lifecycle/export/QC E2E and project defaults compatibility | W3 | One project/revision across create→edit→reopen→export/QC | BLOCKED on 3, 4, 7 |
| 9 | Run final hosted full regression on exact main SHA | W6/W0 | All required hosted jobs pass; no local runner used | BLOCKED on business lanes 1–8 |

## 7. Stop conditions

Stop and request a decision only for architecture/contract conflict, data-loss risk, security boundary change, irreversible migration, production-impacting change, or a product decision not specified by either source plan. Ordinary test failures, lint issues and CI configuration defects are to be diagnosed and fixed without changing acceptance thresholds.


## 8. Execution update after ledger commit

- Ledger/workstream policy commit: `2c5997b94ec54ac9ab7b014fb814882fb050c0ad`.
- GitHub Actions YAML validation for that exact SHA: run `42617062288`, SUCCESS.
- The AI Director workflow was guarded with job-level `if: false`; run `42617062648` has no jobs, so this commit did not allocate a self-hosted runner.
- The earlier run `42616478439` had already started on the self-hosted WSL runner before the guard commit. It remained in progress at the time of verification. The connected GitHub operations available in this session do not expose workflow cancellation; this work does not claim it was cancelled.
- Windows-native publication run `42617062343` is on GitHub-hosted `windows-latest` and was in progress at verification time.
- Full hosted AI Director regression is still BLOCKED: the existing main integration workflow is disabled, while the branch-gated parallel/Product IA workflows do not yet provide main-only parallel lanes. This is the next W6 task, not a PASS.


## 9. Hosted parallel CI rollout — 2026-11-09

### Changes committed

- `a482e48b38a08eca9646a01dc04f97c17c27d773`: converted `parallel-workstream-ci.yml` to main push + manual dispatch and three independent GitHub-hosted jobs:
  - W1 generation durability;
  - W2 timeline transactions;
  - W3 lifecycle/export/QC.
- `cfbc893fa23bfa4f40a1dac00ed4511d03adbdb2`: converted `product-ia-regression.yml` to main push + manual dispatch and two independent GitHub-hosted jobs:
  - W4 project home/create lifecycle;
  - W5 settings IA/credential safety.
- `7f2c4dfe0c00ad199210352c51c070a5e9f893c3` and `ab0454b568bd09eff19124b3592f5db25485bb14`: added documentation-only path exclusions so updating this ledger does not launch another full test matrix.
- All five lane jobs use `ubuntu-24.04`, checkout `github.sha`, and have independent hosted runners. No self-hosted runner labels or local workspaces are used by these five jobs.
- The AI Director workflow remains guarded by job-level `if: false`; run `42618516049` and run `42618666941` were skipped with no job allocation.
- Workflow YAML validation on latest SHA `ab0454b568bd09eff19124b3592f5db25485bb14`: run `42618666928`, SUCCESS.
- Windows native publication on prior SHA `a482e48b38`: run `42618491298`, SUCCESS. Latest SHA run `42618666974` was pending at last inspection.

### Current hosted lane evidence

Latest main SHA at this update: `ab0454b568bd09eff19124b3592f5db25485bb14`.

| Lane | Latest run | State at last inspection | Evidence interpretation |
|---|---:|---|---|
| W1 generation | `42618666944` | IN_PROGRESS | No latest-SHA PASS yet; earlier `42618516043` completed W1 successfully on `cfbc893fa2` |
| W2 timeline | `42618666944` | IN_PROGRESS | No PASS/FAIL conclusion yet |
| W3 lifecycle/export/QC | `42618666944` | IN_PROGRESS | No PASS/FAIL conclusion yet |
| W4 project/create | `42618667030` | IN_PROGRESS | No latest-SHA PASS yet |
| W5 settings IA | `42618667030` | IN_PROGRESS | Earlier `42618516045` W5 job succeeded on `cfbc893fa2`; latest-SHA run pending |
| YAML validation | `42618666928` | SUCCESS | Workflow syntax valid on latest SHA |

Additional duplicate runs were created because the two workflow files were committed sequentially while both listened to every main push. These runs are all GitHub-hosted; the available GitHub operations do not expose cancellation. Documentation-only commits are now excluded from the two lane workflows to prevent further unnecessary matrix runs.

### Self-hosted runner incident update

The historical self-hosted AI Director run `42616478439` is no longer active; it completed with FAILURE. Its failing steps were the full pytest run and timeline browser regression gate. This is historical failure evidence on the pre-guard SHA `c221a0b4fe`, not a failure of the hosted main-lane jobs. No later run allocated that self-hosted job; AI Director runs after the guard are SKIPPED with no jobs.

### Next action

1. Monitor W1–W5 on exact main SHA `ab0454b568bd09eff19124b3592f5db25485bb14` until all jobs complete; diagnose each failure from its step log.
2. Do not count earlier-SHA PASS as current-main PASS.
3. Once the hosted test matrix is stable, proceed with W1/W2 actual implementation gaps and W4/W5 UI implementation; tests alone do not close those deliverables.
4. W7 shared shell integration remains serial and W3 integrated lifecycle acceptance remains downstream.


## 10. Latest code acceptance update

**Latest code/test SHA:** `7a0401242396cb68e86ac8f58e7762b602288860`.  
A later ledger-only commit does not change application or test code.

| Lane | Run | Latest result |
|---|---:|---|
| W1 generation | `42618988136` | PASS |
| W2 timeline | `42618988136` | IN PROGRESS at last inspection |
| W3 lifecycle/export/QC | `42618988136` | IN PROGRESS at last inspection |
| W4 project/create | `42618988066` | IN PROGRESS at last inspection |
| W5 settings IA/credentials | `42618988066` | PASS, including the new export-redaction and partial-import-preservation browser test |
| Workflow YAML validation | `42618988105` | SUCCESS |

W5's new browser regression uses synthetic fixture keys in an isolated temporary storage root. It verifies that export contains no API keys and that importing only the image role changes that role's model/base URL while preserving both local LLM and image keys.

W4/W5 implementation was re-audited against current source rather than assumed missing: the project list is already the default home, the top-level create action exists, `openCreate()` opens a full-viewport create workspace, and settings already contain the six planned categories with progressive disclosure. The remaining question for W4 is current-SHA browser acceptance, not whether these UI structures exist.
