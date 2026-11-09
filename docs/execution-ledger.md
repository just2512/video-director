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
| 19D-04 | Multi-select and cross-track drag form one atomic transaction; Undo/Redo restore full affected state | UNKNOWN | UNKNOWN | Recent commits `cb366e36`, `4d866259`, `c221a0b4` adjust coverage; latest known full AI Director run before this baseline failed on `3a7ec258`. Verify exact failure and current tests |
| 19D-05 | Timeline gestures reject stale external revisions and project-switch responses | PASS (documented implementation) | UNKNOWN | Existing browser coverage is documented; re-run against exact current main |
| 19D-06 | Generation job reservation, provider identity, ambiguous submission, download recovery and Manifest/Registry idempotency | PASS (implementation changes recorded; current behavior needs revalidation) | UNKNOWN | Nov 9 commits changed provider identity and recovery contracts; run generation recovery, provider matrix and manifest regressions |
| 19D-07 | Manual generated-asset adoption; generation completion cannot overwrite a newer user revision | PASS (historical browser evidence) | UNKNOWN | Re-run manual-adoption browser E2E on current main |
| 19D-08 | Project create/open/migrate/edit/reopen lifecycle uses one project and one FilmDocument revision | PASS (historical lifecycle E2E evidence) | UNKNOWN | Re-run the existing integrated lifecycle fixture; do not create a duplicate fixture without a coverage gap |
| 19D-09 | Actual MP4 export and QC artifacts are bound to the exact FilmDocument revision | PASS (historical real FFmpeg evidence) | UNKNOWN | Re-run actual render/QC regression on hosted Ubuntu; verify output metadata, revision, artifact digest and failure behavior |
| 19D-10 | Real model token streaming against a currently supported endpoint | MISSING | MISSING | Prior documented endpoint returned HTTP 410; mocks and historical runs do not count. Only explicit hosted opt-in with GitHub Secrets can close this |
| 19D-11 | Arbitrary HTML/WebGL deterministic rendering and exhaustive edit-operation permutations | UNKNOWN / bounded support | MISSING | Preserve explicit unsupported boundaries; do not claim universal determinism or exhaustive permutation coverage |

### 2.2 07D — Product information architecture

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 07D-01 | Safe partial model-config updates preserve omitted roles/fields and keys | PASS (safe merge path recorded in 2026-11-07 audit closure) | UNKNOWN | Re-run config contract tests on hosted runner; do not inspect or modify a developer's local config |
| 07D-02 | Existing create flow and browser/server draft recovery chain | PASS (existing lifecycle contract) | UNKNOWN | Preserve existing DOM hooks, draft key, server endpoint, upload and WorkflowRun contracts |
| 07D-03 | Project-first home hierarchy and first-class create experience | MISSING | MISSING | Implement the UX over the existing project/create lifecycle; do not create a second backend/state machine |
| 07D-04 | Full-work-area create flow with safe cancel/failure/resume | MISSING | MISSING | Depends on 07D-02 regression protection and exact create rollback contract |
| 07D-05 | Categorized settings IA and progressive disclosure | MISSING | MISSING | Implement UI only; do not change ROLE_DEFS, provider IDs, secret semantics or settings endpoint contract |
| 07D-06 | Shared shell navigation and in-app view contract | PARTIAL (three spaces exist; inspected route is in-page switch) | UNKNOWN | Coordinator owns `index.html` and `app.js`; do not introduce a router migration in this pass |
| 07D-07 | Project-scoped output defaults with backward-compatible reads | UNKNOWN | MISSING | Audit existing project params/CompositionIR/FilmDocument dimensions first; add schema only with compatibility tests |
| 07D-08 | Product E2E: home → create/cancel/resume → settings expand/save/import/export | MISSING | MISSING | Add/complete browser acceptance after A/B UI and shell integration |

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
| 1 | Disable self-hosted AI Director job; establish hosted-only runner rule | W6 | No future workflow path allocates a self-hosted runner | IN PROGRESS |
| 2 | Convert current branch-gated workstream CI into hosted main-only lanes | W6 | Generation, timeline, lifecycle and Product IA scoped jobs can run from main SHA | BLOCKED on workflow migration |
| 3 | Re-run focused generation/provider recovery suite on hosted runner | W1 | Exact-main tests pass; provider submission and manifest invariants verified | BLOCKED on 2 |
| 4 | Re-run timeline multi-select/cross-track/Undo/Redo browser suite | W2 | Atomic transaction and full undo/redo behavior pass on exact SHA | BLOCKED on 2 |
| 5 | Implement project-home/create UX over existing create lifecycle | W4 | Create/cancel/failure/resume browser scenarios pass | MISSING |
| 6 | Implement categorized settings IA | W5 | Expand/save/import/export and secret preservation browser scenarios pass | MISSING |
| 7 | Integrate shared shell serially | W7 | One route/view state; no duplicate project or settings state | BLOCKED on 5, 6 |
| 8 | Run lifecycle/export/QC E2E and project defaults compatibility | W3 | One project/revision across create→edit→reopen→export/QC | BLOCKED on 3, 4, 7 |
| 9 | Run final hosted full regression on exact main SHA | W6/W0 | All required hosted jobs pass; no local runner used | BLOCKED on 2–8 |

## 7. Stop conditions

Stop and request a decision only for architecture/contract conflict, data-loss risk, security boundary change, irreversible migration, production-impacting change, or a product decision not specified by either source plan. Ordinary test failures, lint issues and CI configuration defects are to be diagnosed and fixed without changing acceptance thresholds.
