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
| 19D-04 | Multi-select and cross-track drag form one atomic transaction; Undo/Redo restore full affected state | PASS (implementation recorded) | PASS — W2 timeline transaction + AI proposal suites passed on code SHA `471800ca2d`, run `42620911400` | Recent commits `cb366e36`, `4d866259`, `c221a0b4` adjust coverage; latest known full AI Director run before this baseline failed on `3a7ec258`. Verify exact failure and current tests |
| 19D-05 | Timeline gestures reject stale external revisions and project-switch responses | PASS (documented implementation) | UNKNOWN | Existing browser coverage is documented; re-run against exact current main |
| 19D-06 | Generation job reservation, provider identity, ambiguous submission, download recovery and Manifest/Registry idempotency | PASS | PASS — W1 hosted suite succeeded on latest code SHA `7a04012423`, run `42618988136` | Nov 9 commits changed provider identity and recovery contracts; run generation recovery, provider matrix and manifest regressions |
| 19D-07 | Manual generated-asset adoption; generation completion cannot overwrite a newer user revision | PASS (implementation recorded) | PASS — W3 adoption regression succeeded on code SHA `471800ca2d`, run `42620911400` | Re-run manual-adoption browser E2E on current main |
| 19D-08 | Project create/open/migrate/edit/reopen lifecycle uses one project and one FilmDocument revision | PASS (implementation recorded) | PASS — W3 lifecycle suite succeeded on code SHA `471800ca2d`, run `42620911400` | Re-run the existing integrated lifecycle fixture; do not create a duplicate fixture without a coverage gap |
| 19D-09 | Actual MP4 export and QC artifacts are bound to the exact FilmDocument revision | PASS (implementation recorded) | PASS — W3 render/QC suite succeeded on code SHA `471800ca2d`, run `42620911400` | Re-run actual render/QC regression on hosted Ubuntu; verify output metadata, revision, artifact digest and failure behavior |
| 19D-10 | Real model token streaming against a currently supported endpoint | MISSING | MISSING | Prior documented endpoint returned HTTP 410; mocks and historical runs do not count. Only explicit hosted opt-in with GitHub Secrets can close this |
| 19D-11 | Arbitrary HTML/WebGL deterministic rendering and exhaustive edit-operation permutations | UNKNOWN / bounded support | MISSING | Preserve explicit unsupported boundaries; do not claim universal determinism or exhaustive permutation coverage |

### 2.2 07D — Product information architecture

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 07D-01 | Safe partial model-config updates preserve omitted roles/fields and keys | PASS (safe merge path recorded in 2026-11-07 audit closure) | UNKNOWN | Re-run config contract tests on hosted runner; do not inspect or modify a developer's local config |
| 07D-02 | Existing create flow and browser/server draft recovery chain | PASS (existing lifecycle contract) | UNKNOWN | Preserve existing DOM hooks, draft key, server endpoint, upload and WorkflowRun contracts |
| 07D-03 | Project-first home hierarchy and first-class create experience | PASS — project list is default pane and top-level create action is present | IN PROGRESS — W4 browser suite running on code SHA `7a04012423` | Implement the UX over the existing project/create lifecycle; do not create a second backend/state machine |
| 07D-04 | Full-work-area create flow with safe cancel/failure/resume | PARTIAL — full-viewport workspace and existing draft/cancel lifecycle exist; required project name and project type inputs are absent | W4 browser coverage passes on `ac1fbf5558`; metadata implementation remains MISSING | Depends on 07D-02 regression protection and exact create rollback contract |
| 07D-05 | Categorized settings IA and progressive disclosure | PASS — categorized `<details>` sections and progressive disclosure exist | PASS — W5 browser + persistence tests succeeded on code SHA `471800ca2d`, run `42620911467` | Implement UI only; do not change ROLE_DEFS, provider IDs, secret semantics or settings endpoint contract |
| 07D-06 | Shared shell navigation and in-app view contract | PASS for current scope — three spaces are integrated through `switchTab`; no router migration required by this audit | UNKNOWN — no dedicated cross-space navigation E2E recorded | Coordinator owns `index.html` and `app.js`; do not introduce a router migration in this pass |
| 07D-07 | Project-scoped output defaults with backward-compatible reads | PASS — project width/height/FPS are stored in `plans/director_spec.json`; `/api/director/brief` reads them back; legacy platform-only projects derive a compatible aspect ratio | PASS — W4 browser regression verifies width/height/FPS restore, clean defaults on project switch and legacy platform fallback on code SHA `471800ca2d`, run `42620911467` | Audit existing project params/CompositionIR/FilmDocument dimensions first; add schema only with compatibility tests |
| 07D-08 | Product E2E: home → create/cancel/resume → settings expand/save/import/export | PARTIAL — W4/W5 cover separate flows; create metadata fields remain missing | W5 PASS on `471800ca2d`; W4 lifecycle PASS, but project name/type workflow not yet implemented | Add/complete browser acceptance after A/B UI and shell integration |

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
| 1 | Disable self-hosted AI Director job; establish hosted-only runner rule | W6 | No future workflow path allocates a self-hosted runner | PASS — AI Director job-level `if: false`; current main run `42637922618` skipped |
| 2 | Convert branch-gated workstream CI into hosted main-only lanes | W6 | W1/W2/W3/W4/W5 execute independently on GitHub-hosted runners from main SHA | PASS — latest completed SHA `84886d7487`, runs `42635166724` and `42635166694`; SHA `6ba62841bd` revalidation running |
| 3 | Generation/provider recovery suite | W1 | Provider identity, retry reservation and manifest invariants pass | PASS — SHA `84886d7487`, 89 passed, run `42635166724` |
| 4 | Timeline transactions, stale revisions, Undo/Redo and AI proposal contracts | W2 | Atomic transaction and revision-bound proposal behavior pass | PASS — SHA `84886d7487`, 333 passed, run `42635166724` |
| 5 | Project home/create, cancel/resume, metadata and project defaults | W4 | Existing create lifecycle and metadata fields pass without a second backend state machine | PASS — SHA `84886d7487`, 35 passed, run `42635166694` |
| 6 | Settings IA, safe save/import/export and secret preservation | W5 | Categorized UI and credential safety pass | PASS — SHA `84886d7487`, 13 passed, run `42635166694` |
| 7 | Shared shell navigation contract | W7 | Home/editor/settings switch with one active pane, stable URL and global create action | IMPLEMENTED; dedicated browser job pending on next main SHA |
| 8 | Integrated product journey across create lifecycle and settings | W7/W3 | One browser journey covers home → create/cancel/resume → settings expand/save/import/export | PARTIAL — W4 and W5 separate browser suites pass; single cross-workflow journey still missing |
| 9 | Exact-main full hosted regression | W6/W0 | Full pytest, Studio release gate, all scoped lanes and Windows native check pass on exact main SHA | PASS for non-live gates on `84886d7487`; full run `42635166809` live NVIDIA job failed before network access; SHA `6ba62841bd` rerun in progress |
| 10 | Real NVIDIA token-stream and FilmDocument immutability acceptance | W6/W0 | Real model emits token fragments before result; proposal is revision-bound; document unchanged before approval; approval single-use | BLOCKED — prior run `42635166809` failed before network access due missing `httpx`; dependency fix committed in `6ba62841bd`, live run pending |
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


### Follow-up coverage gaps discovered during source audit

- W2 currently does not include the dedicated AI proposal preview/reject/apply browser contract tests (`tests/test_film_studio_ai_proposal_browser.py` and `tests/test_film_studio_ai_stream_contract.py`). Add these to hosted acceptance before closing 19D-03.
- 07D-07 stores project-specific output settings in `plans/director_spec.json` and reads them through `/api/director/brief`; existing tests prove legacy platform fallback, but do not yet prove explicit width/height/FPS round-trip. Add a focused browser regression without changing the persisted contract.
- The existing project-home/create UI and settings IA were present in source before this audit refresh. Do not reimplement them; close their browser acceptance and only change code if a concrete failing behavior is found.


## 11. Follow-up audit correction and W2 failure analysis

- A deeper check against Product IA section 3.2 found that the current create workspace still lacks explicit project-name and project-type inputs. The creative brief is the topic field, and output profile already exists; name/type are not represented in the form or persisted project params. Do not mark W4 fully complete until these two fields are added to the existing create state machine, saved in project `params_json`, restored from project spec, and surfaced in the project index. Use a free-text project type because the source plan does not define a fixed taxonomy; do not invent one.
- W2 run `42620117002` exposed a timing failure in `test_external_agent_write_hot_reloads_and_recovers_after_broken_document`: the test observed transient preview cleanup immediately after pointer-up, before the asynchronous `/api/editor/film/edit` response had necessarily committed the authoritative revision. The run had 332 passed and 1 failed. The test now waits for the edit response before reading the authoritative revision (commit `471800ca2dfecc7dea04ed23d7dbf833d0ba7691`). This is not a relaxed assertion; it waits for the operation's actual completion.
- The preceding W2 run `42619648447` also exposed a shell continuation error after adding AI proposal tests. The missing continuation was fixed in commit `f6fbb276bca7583764c53491e389268c176e6e13`; YAML validation succeeded.
- Latest W2 verification after both fixes: run `42620911400` on code SHA `471800ca2dfecc7dea04ed23d7dbf833d0ba7691`, still running at last inspection. W1 is PASS; W3 is running. W4/W5 run `42620911467`: W5 PASS, W4 running. Workflow YAML validation run `42620911425`: SUCCESS. AI Director self-hosted workflow run `42620911500`: SKIPPED, no job allocated.


### 2026-11-09 hosted lane closure

On code SHA `471800ca2dfecc7dea04ed23d7dbf833d0ba7691`:
- W1 generation durability: PASS.
- W2 timeline transactions + AI proposal contracts: PASS after waiting for the authoritative edit response in the snap-trim browser test.
- W3 lifecycle/export/QC: PASS.
- W4 project home/create lifecycle and output profile compatibility: PASS for the currently implemented fields.
- W5 settings IA, export redaction and partial import key preservation: PASS.
- Workflow YAML validation: run `42620911425`, SUCCESS.
- AI Director legacy self-hosted workflow: run `42620911500`, SKIPPED, no job allocated.
- The complete hosted full-regression gate is not yet implemented; scoped lane PASS does not replace it.
- W4 remains PARTIAL overall because explicit project name/type inputs and persistence are still absent, as documented in section 11.


## 12. Exact-main full hosted regression and NVIDIA gate — 2026-11-09

### Latest-main trigger audit before this change

- Main was `e7aca4b1b82853c165c0fb5173ca63c3d5337891`, a documentation-only commit.
- Its push-triggered AI Director run `42621455183` concluded `SKIPPED`; job `ai-director` had no steps and the job-log endpoint returned 404 because GitHub did not create a log stream. The source-level reason is the deliberate job-level `if: false` on the former self-hosted WSL job.
- The same exact SHA's Windows native publication run `42621455035` succeeded. Its steps included checkout of the exact SHA, Python 3.11 setup, isolated dependencies, and the real Windows `MoveFileExW` write-through replace/durable-delete test; the test step succeeded.
- The same exact SHA's workflow syntax run `42621455011` succeeded; all workflow YAML files parsed.
- Because the commit changed only documentation, the path-filtered W1–W5 workflows did not trigger. Earlier W1–W5 PASS evidence on `471800ca2dfecc7dea04ed23d7dbf833d0ba7691` is historical and is not relabeled as an `e7aca4b` PASS.

### Hosted full-regression replacement

- Added `.github/workflows/full-hosted-regression.yml` with two independent GitHub-hosted Ubuntu 24.04 jobs. Both check out immutable `github.sha`; neither uses self-hosted labels, developer storage, local model config, or provider credentials.
- `full_regression` compiles application/tests and runs the complete pytest suite with JUnit and retained console logs.
- `studio_release_gate` builds React 19/Tailwind 4, installs Remotion and Chromium, then runs React integration, timeline transaction, revision conflict, project-switch, lifecycle, render/export and QC browser/CLI/MCP regressions.
- The explicit NVIDIA job is downstream of both hosted gates. It is opt-in only, requires the dedicated `NVIDIA_API_KEY` secret, pins `https://integrate.api.nvidia.com/v1` and `z-ai/glm-5.3-flash`, and invokes the real live-stream acceptance test. It does not fall back to generic provider credentials. The test itself checks at least two non-empty token chunks before the result, non-buffered timing, a revision-bound proposal, unchanged FilmDocument bytes/revision before approval, and single-use approval/replay rejection.
- Missing NVIDIA credentials produce an explicit `LIVE_STREAM_ACCEPTANCE=MISSING` failure; no mock or lifecycle-only event can be reported as PASS.
- This change establishes the hosted full-regression path; its result is PENDING until the new workflow runs on the resulting exact main SHA. The legacy self-hosted workflow remains disabled and must not be re-enabled.

### Current implementation correction

- `VIDEO_GENERATION_TIMEOUT` is already explicitly recoverable only by polling the persisted provider task. `tests/test_generation_recovery.py` contains both direct classification coverage and a full retry test asserting one provider POST. The earlier proposed follow-up to add this behavior is therefore not a missing implementation and must not be reintroduced as a duplicate change.


### W4 project metadata implementation — 2026-11-09

- Added free-text project name (80-character maximum) and project type (60-character maximum) to the existing create form; both use the existing local/server draft and conversation snapshot lifecycle.
- The unified Director service validates and forwards both values. Director Agent stores them in the existing `projects.params_json` row; no SQLite schema migration or second create state machine is introduced.
- Project index now returns `project_type`, and the project list displays it. Project prefill reads the current DB name/type (so a later rename wins over the original spec), falling back to the persisted `director_spec.json` for compatibility.
- Added service-boundary validation, Director Agent persistence, project-index, draft persistence, and browser prefill/create-payload regressions. Exact-main W4 and full-regression evidence remains PENDING until the resulting commit's hosted jobs finish.

- Retry recovery now seeds `project_name` and `project_type` from current `params_json` before historical `director_spec.json` backfill, so a post-creation rename is not reverted by retry.

- W4 follow-up: the first metadata browser run found the draft preview replaced the creative brief with the project name, and the project-prefill endpoint returned HTTP 500. The preview now retains name/type plus brief; the browser test performs a direct endpoint probe and the server logs the caught exception so the 500 root cause is visible instead of timing out silently. Revalidation is required on the new exact main SHA.

- W4 prefill HTTP 500 root cause confirmed by the diagnostic response: `NameError: config is not defined` in the pre-existing brief route. The module imports the configuration singleton as `vd_config`; the route now uses `vd_config.project_dir(pid)`. The direct browser probe remains as regression coverage.


## 13. Exact-main full regression closure and live NVIDIA trigger — 2026-11-09

### Exact main SHA before the explicit live trigger

- Main SHA: `e2d361867bcc686bc50151f3401d4a9fbe566a53`.
- Full hosted regression run `42628470638`: SUCCESS. Full pytest: 1,936 passed, 2 skipped, 8 warnings; 1,938 collected; 0 failures and 0 errors; 693.68 seconds.
- The two full-suite skips are explicit: Windows write-through persistence is Linux-inapplicable (the separate Windows-native run passed), and real NVIDIA live-stream is opt-in only (therefore MISSING, not PASS).
- Studio build/timeline/lifecycle/export/QC release gate in the same run: 344 passed, 0 failed, 0 skipped.
- Parallel workstreams run `42628470803`: W1 generation 89 passed; W2 timeline 333 passed; W3 lifecycle/export/QC 235 passed, 1 skipped (quiet lane log did not emit the skip reason).
- Project home/create run `42628470596`: W4 35 passed; W5 13 passed.
- Windows native persistence run `42628470688`: 1 passed on the Windows runner, exercising the real MoveFileExW write-through replace/delete path.
- Workflow YAML run `42628470605`: all 8 workflow files parsed successfully.
- Legacy self-hosted AI Director run `42628470660`: SKIPPED by its explicit disabled job condition; no self-hosted result is claimed. The full hosted replacement above is the authoritative regression gate.

### Studio ripple-delete browser regression correction

- A Studio-only run exposed a race in the browser test: it used the last observed `/api/editor/film/edit` response without correlating that response to the specific `ripple_delete` request. An earlier edit response could therefore be mistaken for the ripple response while the test compared a later document revision.
- The test now uses Playwright `expect_response` with a predicate that checks the request's `ops[0].op == ripple_delete`, waits for that exact response, checks the response revision, uses `cache: no-store` for the authoritative read, and verifies server/client FilmDocument paths plus immediate and post-read disk revision equality.
- Exact-main Studio gate passed 344 tests with the added checks. This was a test synchronization defect; no product timeline behavior was weakened.

### Project metadata and retry compatibility

- Create form now carries free-text project name and project type through draft, conversation snapshot, Director service, `projects.params_json`, project index and prefill. W4 browser lane passed 35 tests.
- The current-DB metadata retry test confirms a rename wins over the historical spec.
- This commit additionally fixes legacy-row fallback: when old `params_json` has no `name` or `project_type` key, the retry spec leaves the key absent so the persisted `plans/director_spec.json` can backfill it. Explicitly present empty DB values remain authoritative. A targeted regression covers both legacy fields.

### NVIDIA live-stream remains the final open gate

- The broad suite's live-stream test is SKIPPED without explicit opt-in; it is not a real-provider PASS.
- The next main commit explicitly opts in with `[live-stream-smoke] [live-provider=nvidia] [live-model=z-ai/glm-5.3-flash]`.
- The hosted workflow now requires all three markers, waits for both full pytest and Studio release gates, uses only `secrets.NVIDIA_API_KEY`, pins `https://integrate.api.nvidia.com/v1` and `z-ai/glm-5.3-flash`, and has no environment approval gate.
- The full-hosted workflow now intentionally ignores docs-only, Markdown-only and README-only pushes; source, tests and workflow files still trigger it. A live marker must therefore accompany a code/test/workflow change, not a docs-only commit.
- Final closure still requires the real response to contain model token fragments before result, a revision-bound proposal, unchanged FilmDocument bytes/revision before approval, and single-use approval/replay rejection. If the secret is absent, report MISSING and keep the final gate open.


## 15. NVIDIA endpoint timeout classification and bounded retry — 2026-11-09

- Run `42630961883` proves the explicit NVIDIA live job ran with the masked NVIDIA secret present and runtime config accepted; it does not prove token delivery.
- The actual app request to `https://integrate.api.nvidia.com/v1` / `z-ai/glm-5.3-flash` produced zero reasoning and visible fragments, then hit the SDK's configured 120-second read timeout. The previous classifier reported REJECT because it did not map a `ReadTimeout` inside the live JUnit failure to the existing `BLOCKED_ENDPOINT` category.
- This commit adds a separate minimal real NVIDIA streaming probe before the full app test, raises only this isolated live run's SDK read timeout to 210 seconds, disables SDK retries, and classifies transport timeouts from both probe diagnostics and JUnit as BLOCKED_ENDPOINT. A successful minimal probe is connectivity evidence only; the full Film AI token-stream and FilmDocument immutability test remains mandatory for PASS.
- NVIDIA's current model card says GLM-5.3-Flash supports reasoning/tool use, defaults `reasoning_effort` to `max`, and defaults `clear_thinking` to false. The official API reference documents OpenAI-compatible `/v1/chat/completions` streaming as SSE deltas. The provider already sends `reasoning_effort=low`, `clear_thinking=true`, and an 8192-token budget for this exact model.
- Community reports dated 2026-10-14 describe GLM-5.3 chat-completions timeouts with zero response bytes while `/v1/models` remained HTTP 200. This is diagnostic context, not proof of a provider outage.
- If the minimal probe or full app stream again times out, record BLOCKED_ENDPOINT and keep final acceptance OPEN. Do not substitute a mock or claim model availability from a catalog response.


## 17. Hosted regression evidence and live-probe dependency correction — 2026-11-09

- Main SHA `84886d74878178d0b606e283ee10e60e23bae9e2`: W1 89 passed, W2 333 passed, W3 235 passed / 1 skipped, W4 35 passed, W5 13 passed. Runs: `42635166724` and `42635166694`.
- The same SHA's full pytest summary was 1,943 tests, 0 failures, 0 errors, 2 skipped; Studio release gate was 344 passed. Full hosted run: `42635166809`.
- The NVIDIA live job on that SHA failed before making a network request: the probe imported `httpx`, but the hosted environment did not install it. The NVIDIA secret was present; this is a workflow dependency defect, not evidence about endpoint availability.
- Main commit `6ba62841bdf1ce1dff0d9567dd833c491acd2020` explicitly installs `httpx` in the isolated live-provider job. Its full hosted run `42637922748` completed with Studio 344 passed, but full pytest had one project-switch browser failure (1,942 passed, 2 skipped, 1 failed); W1–W5 and Windows-native checks passed. The next main SHA below re-ran the full suite successfully. The real model request must not be claimed as successful until its job and the full Film AI streaming test complete.
- The legacy AI Director self-hosted job remains disabled. No local runner is to be dispatched.

## 18. Project-switch browser stabilization and exact-main recheck — 2026-11-09

- Main SHA `6ba62841bdf1ce1dff0d9567dd833c491acd2020`, full hosted run `42637922748`: full pytest 1,942 passed, 2 skipped, 1 failed. The only failure was `test_project_switch_reconciles_request_response_store_and_disk`, which timed out waiting for `#film-studio-panel.fs-studio-expanded`. Studio release gate passed 344 tests.
- The failure did not reproduce on the next exact main SHA `e6c9c62bd33151ace78023f691f1bd4cd248aac9`: full hosted run `42638477184` passed with 1,944 passed, 2 skipped, 0 failures/errors; Studio gate 344 passed; W7 shared-shell navigation 1 passed; Windows-native persistence 1 passed; all 8 workflow YAML files parsed. The NVIDIA live job was SKIPPED because that push did not carry the explicit live markers, so no NVIDIA PASS is claimed.
- The earlier failing browser test mutated the project select and dispatched a synthetic change event, then waited for `filmStudioToggle` but did not verify the Film Studio authority wrapper itself was installed. The exact failing run did not retain page-error diagnostics, so the underlying browser exception was not proven.
- The current hardening moves the authority wrapper ahead of later dock restoration, exposes an explicit wrapper-readiness marker, makes inline project selection/open actions call `window.editorLoad()` explicitly, and changes the browser test to use Playwright `select_option`. On timeout it records wrapper, page-error, request/response and Studio-store diagnostics.
- This is a determinism and authority-routing hardening change, not a claim that the previous one-off failure's root exception was conclusively identified. The next exact-main CI must re-run the four-way project-switch test and full Studio gate before treating the hardening as validated.
- The next commit also carries the explicit NVIDIA live markers. Only the real minimal NVIDIA stream probe followed by the full Film AI token-stream/FilmDocument immutability test can close the live gate.

## 19. Hosted-workflow migration test contracts and UI handler normalization — 2026-11-09

- Main SHA `5c9f11d04e80b855f8a83712e7210b5401f7d86e`, full hosted run `42643677288`: 1,947 collected, 1,936 passed, 9 failed, 2 skipped. Studio release gate: 338 passed, 6 failed. Parallel lanes: W1 88 passed / 1 failed; W2 still in progress at review time; W3 235 passed / 1 skipped; W7 1 passed. Project home lanes: W4 35 passed; W5 13 passed. Windows-native persistence: 1 passed; workflow YAML: 8 files parsed. The next main push supersedes the in-progress W2 run under its concurrency group and starts a fresh exact-SHA W2 run; no W2 PASS is claimed for this SHA.
- Six Studio contract failures were stale references to the removed `.github/workflows/ai-director.yml` or an obsolete assertion that `paths-ignore` must not exist. The full suite additionally exposed one stale workflow reference in `tests/test_live_stream_acceptance.py`, one in `tests/test_bulk_management.py`, and one UI audit false positive for `onclick=window.editorLoad()`.
- The hosted migration now splits responsibilities across full-hosted-regression.yml, parallel-workstream-ci.yml, product-ia-regression.yml, studio-ui-build.yml, validate-workflows.yml and windows-native-publication.yml. Tests are updated to assert the actual hosted runners, exact full pytest/compileall/JS syntax coverage, W1 inclusion, Studio release gate, isolated NVIDIA secret, and the deliberate docs-only paths-ignore policy.
- The UI action audit now normalizes a leading `window.` before matching its existing UI-only handler allowlist; this recognizes the explicit global authority call without permitting unknown handlers. A targeted unit test keeps `window.unknownAction()` rejected.
- The NVIDIA live job did not run on this SHA because both full regression and Studio release gates failed. No real NVIDIA token-stream result exists from this run. The next push must re-run W1, Studio and full pytest; only if both full and Studio gates pass may the explicit NVIDIA probe and full app stream execute.
