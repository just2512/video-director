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
| 19D-11a | Supported project-local HTML scene deterministic frame contract (`window.film.frame(t)`) | IMPLEMENTED — controlled clock/random seed, animation synchronization, serialized exact frames, resource readiness and bounded RAF flushing | IN_PROGRESS — exact-SHA W3/full hosted verification pending for code/workflow SHA `17ed00978adf6d9d6224710e040cd85aa2ba9efb` | Browser regression now includes repeated same-time frame fingerprints; only the documented local HTML contract is in scope. No universal arbitrary-DOM guarantee |
| 19D-11b | Arbitrary HTML/WebGL determinism and exhaustive edit-operation permutations | UNKNOWN / outside current deterministic contract | MISSING | Do not claim arbitrary WebGL/GPU determinism or exhaustive permutations. Define supported-content exclusions and a finite operation-interaction matrix with state invariants; keep this separate from 19D-11a |

### 2.2 07D — Product information architecture

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 07D-01 | Safe partial model-config updates preserve omitted roles/fields and keys | PASS (safe merge path recorded in 2026-11-07 audit closure) | UNKNOWN | Re-run config contract tests on hosted runner; do not inspect or modify a developer's local config |
| 07D-02 | Existing create flow and browser/server draft recovery chain | PASS (existing lifecycle contract) | UNKNOWN | Preserve existing DOM hooks, draft key, server endpoint, upload and WorkflowRun contracts |
| 07D-03 | Project-first home hierarchy and first-class create experience | PASS — project list is default pane and top-level create action is present | IN PROGRESS — W4 browser suite running on code SHA `7a04012423` | Implement the UX over the existing project/create lifecycle; do not create a second backend/state machine |
| 07D-04 | Full-work-area create flow with safe cancel/failure/resume | PASS — full-work-area flow includes project name and project type inputs; `create.js` restores them from project metadata and submits them through `requestOptions` | PASS on code SHA `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`, Product IA run `42744407811` (W4 job success) | Continue checking rename/retry/legacy-row compatibility in the W4 lifecycle suite; do not report metadata fields as missing |
| 07D-05 | Categorized settings IA and progressive disclosure | PASS — categorized `<details>` sections and progressive disclosure exist | PASS — W5 browser + persistence tests succeeded on code SHA `471800ca2d`, run `42620911467` | Implement UI only; do not change ROLE_DEFS, provider IDs, secret semantics or settings endpoint contract |
| 07D-06 | Shared shell navigation and in-app view contract | PASS for current scope — three spaces are integrated through `switchTab`; no router migration required by this audit | UNKNOWN — no dedicated cross-space navigation E2E recorded | Coordinator owns `index.html` and `app.js`; do not introduce a router migration in this pass |
| 07D-07 | Project-scoped output defaults with backward-compatible reads | PASS — project width/height/FPS are stored in `plans/director_spec.json`; `/api/director/brief` reads them back; legacy platform-only projects derive a compatible aspect ratio | PASS — W4 browser regression verifies width/height/FPS restore, clean defaults on project switch and legacy platform fallback on code SHA `471800ca2d`, run `42620911467` | Audit existing project params/CompositionIR/FilmDocument dimensions first; add schema only with compatibility tests |
| 07D-08 | Product E2E: home → create/cancel/resume → settings expand/save/import/export | PARTIAL — W4 and W5 are independently covered; integrated shell journey remains a separate W7 acceptance concern | W4/W5 PASS on code SHA `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`, Product IA run `42744407811`; W7 PASS on same SHA, Parallel run `42744407839` | Keep isolated W4/W5 results distinct from the integrated W7 journey; verify metadata through create, reopen and rename |

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

## 20. Exact-main audit and hosted regression repair — 2026-11-10

### Code SHA and scoped acceptance

- Latest code/test commit at the start of this audit: `02459402aa10d2a2314adba5e65b2ddb10389ec9`.
- On that exact SHA, W1 generation durability, W2 timeline transactions, W3 lifecycle/export/QC, W7 shared-shell navigation, W4 project-home/create, W5 settings IA, Windows-native persistence and the Studio release gate all completed successfully:
  - Parallel workstreams: run `42646601266` — W1/W2/W3/W7 all SUCCESS.
  - Product IA: run `42646601406` — W4/W5 both SUCCESS.
  - Windows native: run `42646601264` — SUCCESS.
  - Studio release gate within full hosted run `42646601093` — SUCCESS.
- The integrated W7 browser journey covers home navigation, create-form project name/type and brief, server draft persistence, close/reload/resume, settings view/save, secret-redacted export, partial import preserving existing keys, and return navigation. This is test evidence on SHA `02459402aa10d2a2314adba5e65b2ddb10389ec9`, not yet an exact-`c8cca904` acceptance result.
- The W4 form and Director service now carry free-text project name/type through request and project metadata; W4 scoped browser suite passed on the above SHA.

### Full regression failure and correction

- Full hosted run `42646601093` on SHA `02459402aa10d2a2314adba5e65b2ddb10389ec9` failed only at `tests/test_live_stream_acceptance.py::test_nvidia_live_workflow_uses_dedicated_secret_and_official_endpoint`.
- Exact failure: `NameError: name 'Path' is not defined`; suite summary: 1,945 passed, 1 failed, 2 skipped. Compile, Studio build, Studio release gate and JavaScript syntax steps succeeded.
- Corrective commit on `main`: `c8cca904e0dada7a6ecca7beffd5fb94af12049e`; added the missing `from pathlib import Path` import. No test assertion, threshold, product code or runner policy was changed.
- The changed file is confirmed on main with blob SHA `c0b4034b3c188792a97a489a9d3519750d949925`.
- Exact-main hosted revalidation for `c8cca904` must still be observed and its full regression logs checked before this correction is marked PASS.

### Remaining gates

- NVIDIA real token-stream acceptance remains MISSING/OPEN. Run `42646601093` skipped the live job because the full regression prerequisite failed; the earlier real request timeout is not a PASS. The follow-up must explicitly opt in and prove real token fragments, revision-bound proposal, no FilmDocument mutation before approval, and single-use approval/replay rejection.
- The correction commit `c8cca904` did not carry the three NVIDIA opt-in markers. Do not infer that its live job will run; a later explicit opt-in is required after the hosted prerequisites pass.
- The user's physical machine's local model-config file has not been inspected. Existing preservation evidence is from isolated temporary fixtures only; do not claim machine-level backup or byte-preservation acceptance.
- No self-hosted/local runner was used for the above runs. Keep all future CI on GitHub-hosted Ubuntu/Windows runners.


## 21. Exact-main cross-track release-gate failure — 2026-11-10

### Exact SHA and hosted CI

- Code/test SHA: `c8cca904e0dada7a6ecca7beffd5fb94af12049e`.
- Parallel workstreams run `42735621845`: W1 generation durability, W2 timeline transactions, W3 lifecycle/export/QC, and W7 shared-shell navigation all SUCCESS.
- Product IA run `42735621936`: W4 project home/create and W5 settings IA both SUCCESS.
- Windows-native publication run `42735621814`: SUCCESS.
- Full hosted run `42735621934`:
  - Full Python/browser regression: SUCCESS; JUnit reports 1,948 collected, 1,946 passed, 0 failed, 2 skipped.
  - Studio build/timeline/lifecycle/export/QC release gate: FAILURE; JUnit reports 344 collected, 343 passed, 1 failed.
  - Explicit NVIDIA real token-stream job: SKIPPED because this SHA does not carry the explicit three-marker opt-in.
- Retained artifacts: full regression artifact `13728460009`; Studio release-gate artifact `13728175683`.

### Reproducible failure

- Failing test: `tests/test_film_studio_roll_browser.py::test_film_studio_cross_track_drag_shows_react_ghost_and_commits_target_track[valid-target]`.
- Failure assertion: line 1218 expects `clip-a` to be absent from source track `video-main` after the API read has already observed the clip on destination track `video-alt`.
- Preview, target row, drop validity, single `move` operation, and the API-visible destination move all pass before the final direct persisted read assertion.
- This is not a missing `Path` import, not a pytest suite failure, and not a reason to weaken the acceptance test. The discrepancy is between the API-observed post-commit document and the subsequent direct `read_project_film()` observation. The underlying cause is not yet proven.
- Next action: instrument the failing browser regression to capture the API response revision/document, direct-read revision/document, resolved `film.html` path and bytes, and transaction-journal state at the mismatch; then fix the proven cause and rerun the exact Studio gate plus all affected hosted lanes.

### Remaining gates

- Integrated 19D release gate: FAIL on `c8cca904`; do not mark cross-track drag/Studio release as PASS.
- Full Python suite and W1-W5/W7/Windows scoped workflows: PASS on `c8cca904`, but this does not override the Studio failure.
- NVIDIA real token-stream acceptance: MISSING/OPEN; live job was skipped.
- User physical-machine local model-config preservation: UNKNOWN; not inspected and must not be accessed by CI.
- Runner policy remains GitHub-hosted Ubuntu/Windows only; no self-hosted or local runner was used.


## 22. Exact-main recheck and W2 coverage/diagnostic hardening — 2026-11-10

### Exact SHA `fc6d1fc72093abdd2b6a344d2d6b76240d7065c3`

- Full hosted run `42739424896`: PASS. Full pytest: 1,948 tests, 0 failures, 0 errors, 2 skipped.
- Studio build/timeline/lifecycle/export/QC release gate in the same run: PASS; JUnit: 344 passed, 0 failed, 0 skipped. This includes all four cross-track drag scenarios from `tests/test_film_studio_roll_browser.py`; the diagnostic instrumentation is now green on this SHA.
- Windows-native publication run `42739424860`: PASS.
- Product IA run `42739424668`: PASS.
- Parallel workstream run `42739424671`: FAIL only in W2. W1, W3 and W7 passed. W2: 333 passed, 1 failed in `test_external_agent_write_hot_reloads_and_recovers_after_broken_document`.
- W2 failure occurs after a browser/API-visible clip trim: the API/FilmStore observes a changed revision and clip position, but the subsequent direct `read_project_film()` assertion sees the old `at=0`. The assertion is not to be relaxed. This is a second API-versus-disk observation mismatch; root cause remains UNKNOWN pending captured API revision, Store revision, disk revision, resolved path, transaction journal and exact disk HTML.
- The W2 workflow command currently omitted `tests/test_film_studio_roll_browser.py`, despite W2's documented cross-track ownership. Add it to the W2 hosted lane and align the W2 scope table.
- The cross-track diagnostic test passed on this SHA; it proves the observed behavior passed this run, not that the earlier intermittent mismatch has a proven root cause.
- NVIDIA real token-stream acceptance remains MISSING/OPEN: the explicit job was skipped because this SHA lacked all three opt-in markers. User-machine model-config preservation remains UNKNOWN and is not accessible to CI.
- Runner policy remains GitHub-hosted Ubuntu/Windows only.


## 23. Exact-main parallel-lane recheck and W2 test harness repair — 2026-11-10

### SHA `2f9aee27f63dc2d1547ed203b6347b9fd6591589`

- Parallel workstream run `42743155339`: W1 generation durability PASS; W3 lifecycle/export/QC PASS; W7 shared-shell navigation PASS; W2 FAIL.
- Product IA run `42743155486`: W4 project home/create PASS; W5 settings IA and credential safety PASS.
- Windows-native run `42743155333`: PASS. Workflow YAML validation run `42743155327`: PASS.
- Studio release gate in full-hosted run `42743155410`: FAIL, 344 collected, 341 passed, 3 failed. Four cross-track drag scenarios in `tests/test_film_studio_roll_browser.py` all passed.
- W2 run `42743155339`: 338 collected, 336 passed, 2 failed:
  1. `test_external_agent_write_hot_reloads_and_recovers_after_broken_document` stopped at the new diagnostic request with `NameError: base_url is not defined`. Therefore the API/Store/disk diagnostic was not executed and the underlying persistence mismatch remains UNKNOWN.
  2. `test_legacy_director_controls_remain_available_only_without_film_document` timed out waiting for the migration-preview marker. The test inserted the project into the selector but did not dispatch the selector's change event before opening Studio.
- The Studio release-gate third failure was `test_project_switch_reconciles_request_response_store_and_disk`: `select_option()` could not find the injected fixture option because the asynchronous project-index refresh could replace the selector options after fixture insertion.
- Full Python/browser job `129588897142` was cancelled when later main commits superseded this SHA. The dependent NVIDIA job was also cancelled; no full-regression or live-model PASS is claimed.

### Test-harness corrections integrated directly into main

- `c983ad251357abe23736f69a49fe29642475010c`: diagnostic API URL now derives from the test server's actual port; the legacy planning test dispatches the real selector change event before opening Studio.
- `e07ca32bc08fe9a0607842431acd2be616b4f0ca`: initial attempt to wait for selector population before injecting project-switch fixtures.
- `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`: strengthened the project-switch test to wait for the authoritative `/api/projects?filter=all&limit=500` response, not merely a non-empty selector that may contain only a placeholder.
- These are test synchronization/diagnostic corrections only. No production persistence behavior was changed, and no assertion or acceptance threshold was weakened.

### Current exact-main state

- Latest code/test SHA before this ledger-only update: `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`. Ledger-only follow-up: `2a7033cf5ce29d0395f0cd06592187e289336fc1`.
- W1/W3/W4/W5/W7 have PASS evidence on `2f9aee27`; because the subsequent commits only changed W2 test code, project-switch test synchronization and this ledger, these results remain useful lane evidence but are not a substitute for a final exact-SHA integrated acceptance.
- W2 remains OPEN pending new hosted evidence on the latest SHA. The original external-write/trim API-versus-disk discrepancy remains UNKNOWN until the corrected diagnostic reaches its assertions.
- Full Python/browser regression, Studio release gate, Windows native, W1-W7, workflow validation and Product IA must all be checked again on the current exact main SHA.
- NVIDIA real token-stream acceptance remains MISSING/OPEN; no real-model test completed in this cycle. User-machine model-config preservation remains UNKNOWN and must not be accessed by CI.
- Runner policy remains GitHub-hosted Ubuntu/Windows only.


## 24. Exact-main parallel-lane rerun and unresolved W2 transaction failure — 2026-11-10

### Code/test SHA `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`

- Main currently points to `29b8656bc07d3c672b13424a507b46c4451ab037`, a ledger-only follow-up; the tested code SHA remains `68ab8bd9db7b432d36ee5c6fd960187eac5acfd2`.
- Parallel run `42744407839`, attempt 2: W1 SUCCESS, W3 SUCCESS, W7 SUCCESS; W2 FAILURE (337 passed, 1 failed). W2 failure: `test_legacy_director_controls_remain_available_only_without_film_document`, Playwright cannot click `#film-studio-toggle` because the element is not visible after selecting the legacy project. The roll/cross-track test file is included in the W2 command.
- Product IA run `42744407811`: W4 project home/create SUCCESS; W5 settings IA/credential safety SUCCESS.
- Windows-native run `42744407857`: SUCCESS.
- Full hosted run `42744407982`, attempt 2: Studio release gate FAILURE (344 collected, 342 passed, 2 failed). Failures are the same legacy Studio launcher visibility issue and `test_film_studio_cross_track_drag_shows_react_ghost_and_commits_target_track[multi-select-valid-target]`.
- The multi-select cross-track diagnostic shows `api_snapshot=None`, disk revision still `2c99b2c47c22c326`, `.film-transaction.json` still present, source track still contains `clip-a`, and target track still contains `clip-c`. This proves the transaction did not reach the expected durable document state; it does not yet prove why `_commit_film_transaction` stopped. Preserve the assertion and journal; root cause remains UNKNOWN.
- Full Python/browser regression collected 1,948 tests but the process exited with code 139 before JUnit summary generation. One legacy launcher test had already failed. This is a process-level failure; do not convert it into a complete pytest count or treat the job as a normal one-test failure.
- NVIDIA live acceptance was SKIPPED because both full regression and Studio release gate failed. Real model token-stream acceptance remains MISSING.
- W4 metadata correction: `webui/static/js/create.js` contains `cf-project-name` and `cf-project-type`, restores `spec.project_name`/`spec.project_type`, and carries them into `requestOptions`. W4's independent hosted job passed on this SHA. Rows 07D-04 and 07D-08 above are corrected; this does not imply every create/reopen/rename lifecycle edge is closed.
- W6 workflow YAML validation last passed on SHA `2f9aee27f63dc2d1547ed203b6347b9fd6591589` (run `42743155327`). No workflow file changed between that validation and this tested code SHA; exact-SHA W6 validation is still not recorded.
- Runner policy remains GitHub-hosted Ubuntu/Windows only. No self-hosted/local runner was used.

### Next lane-owned actions

- W2: capture the failed request's HTTP status/body, exception and journal payload at the point of failure; compare API response revision, disk revision and journal before changing production transaction code. Diagnose the legacy launcher visibility from computed style and ancestor visibility; preserve the behavior assertion.
- W1/W3/W7: no speculative code edits; retain their same-SHA PASS evidence and re-run after any shared-contract change.
- W4/W5: keep independent lane ownership; W4 metadata is implemented, but continue full lifecycle edge coverage.
- W6: run hosted workflow syntax validation after the next workflow/code integration and retain YAML/JUnit/artifact provenance.
- W7: shared-shell changes remain serial and only follow W4/W5 module contracts; do not treat main integration as a substitute for the lane gates.
- Final acceptance remains blocked until W2, Studio release gate and full hosted regression pass on one exact code SHA; only then consider the explicitly opted-in NVIDIA real-model gate.

## 25. W2 evidence capture and current-main hosted revalidation — 2026-11-10

### Diagnostic-only commits

- aad2e728311beeb4e4c22f478e6b681ced821439: the legacy Studio launcher browser regression now captures the target and ancestor DOM chain when the real click fails, including computed display/visibility/opacity, bounding boxes, hidden flags, active tab and editor-pane state. The original Playwright click and business assertions remain unchanged.
- 83c585e37bd42efb1115fa40f649c20e3360a396: the cross-track mismatch diagnostic now includes the full transaction journal payload and history state alongside API snapshot, disk revision, resolved film.html path, source/target clip IDs and disk HTML.
- Both commits modify tests only. No production behavior, transaction implementation, acceptance assertion, runner policy or test threshold was changed.
- Current tested code SHA: 83c585e37bd42efb1115fa40f649c20e3360a396. The current main head may advance through documentation-only commits; always distinguish the tested code SHA from the ledger head.

### Hosted runs on the diagnostic commits

- SHA aad2e728: runs 42756944624 (parallel), 42756944568 (Product IA), and 42756944598 (full hosted) were superseded by the follow-up W2 diagnostic commit. Some jobs were cancelled; the in-progress W3 and full Python jobs are useful only for the exact SHA shown on those runs and do not constitute final acceptance.
- SHA 83c585e: runs 42756949648 (parallel), 42756949368 (Product IA), 42756949263 (full hosted), and 42756949301 (Windows native) were created. At ledger update time, the first three were queued/pending with no job records yet; Windows native completed successfully.
- Previous W2 evidence remains FAIL until the new diagnostic reaches the failing assertions and exposes the actual DOM visibility chain and transaction journal contents. No production fix is authorized by the currently available evidence.

### Lane ownership remains unchanged

- W1, W3, W4 and W5 continue to run in their independent hosted jobs; W7 remains its own navigation job and does not own W2 behavior.
- W2 owns both timeline transaction diagnostics and the legacy launcher regression because both are in its declared regression suite. The cross-track journal failure must be fixed from the captured payload and exception path, not by relaxing the persisted-state assertion.
- W6 still needs exact-current-workflow validation evidence; the latest workflow syntax PASS remains run 42743155327 on SHA 2f9aee27. The workflow files have not been edited by these two test-only commits.
- W4/W5 isolated PASS records on earlier SHAs remain historical evidence only. Current-SHA Product IA jobs must complete before those lanes are carried forward.
- W3 lifecycle/export/QC and W1 generation durability are not considered complete on the diagnostic SHA until their current-SHA scoped jobs complete.
- NVIDIA real token-stream acceptance remains MISSING/OPEN and must not run until the Studio and full hosted gates pass; only the explicit hosted opt-in with the dedicated GitHub Actions secret is permitted.


## 26. W2 legacy-project transition synchronization and parallel rerun — 2026-11-10

### Exact tested code SHA `67c17a2929b852b9adb2bce278ff0f2a022808f0`

- On preceding code SHA `83c585e37bd42efb1115fa40f649c20e3360a396`, the independent parallel workflow completed with W1 PASS, W3 PASS, W7 PASS and W2 FAIL (337 passed, 1 failed). Product IA completed with W4 PASS and W5 PASS. Windows-native publication PASS.
- W2's only failure was `test_legacy_director_controls_remain_available_only_without_film_document`. The diagnostic showed `html.fs-studio-open` still present and the launcher parent still `display:none` immediately after the test dispatched the project selector change. The selector change is routed through the asynchronous `editorLoad` authority wrapper; the test attempted the launcher click before that transition had completed.
- The same W2 run passed the remaining 337 tests, including the cross-track multi-select drag/commit scenario. The Studio release gate reported 343 passed and the same one launcher failure. No transaction implementation change is justified by this run.
- Commit `67c17a2929b852b9adb2bce278ff0f2a022808f0` changes only the failing browser test: after selecting the legacy project it waits for the Studio-open document class to clear and the launcher to become visible before performing the original real click. The migration-preview and legacy-director behavior assertions remain intact. No force click, skipped assertion, production change or threshold reduction.
- Current-SHA reruns created: parallel `42759478338`, full hosted `42759478347`, Product IA `42759478532`, Windows native `42759478331`. At this ledger update, all four workflows were in progress; their scoped jobs are separately enumerated in GitHub Actions. Do not carry forward PASS until each current-SHA job completes.
- W6: workflow tree comparison between syntax-validation SHA `2f9aee27f63dc2d1547ed203b6347b9fd6591589` (run `42743155327`, PASS) and current tested code SHA `67c17a2929b852b9adb2bce278ff0f2a022808f0` found all seven `.github/workflows/` files byte-identical (same Git blob SHAs). No workflow dispatch action is available in the connected GitHub operations. The prior syntax PASS therefore still validates the unchanged workflow content; a new exact-SHA syntax run is not available without a workflow change/dispatch capability.
- NVIDIA live token-stream acceptance remains SKIPPED/MISSING until the current-SHA Studio and full hosted gates pass and the dedicated explicit opt-in/secret conditions are satisfied.
- Runner policy remains GitHub-hosted Ubuntu/Windows only; no self-hosted/local runner was used.



## 27. W2 legacy-project migration contract correction and exact-main rerun — 2026-11-10

### Corrected root cause

- The previous section 26's diagnosis that the test should wait for Studio to close and the launcher to become visible was incorrect.
- The implementation in `webui/static/js/film-studio.js` explicitly routes a legacy project (confirmed `exists:false`) into the unified Film Studio's migration/planning mode. `editorLoad()` calls `filmStudioToggle(state)` when Studio is not open; `filmStudioToggle()` adds `fs-studio-open`, hides `.film-studio-launch`, and loads the migration preview.
- Therefore the failing test's expectation that `fs-studio-open` disappear and the launcher become visible contradicted the actual product contract. The subsequent attempt to click the hidden launcher was also invalid for this flow.
- Corrective commit `7865a15840090c71aa6808b2f70818618a1eaccf` changes only `tests/test_film_studio_external_watch_browser.py`: after selecting the legacy project, it waits for the expanded Studio, `fs-studio-open`, the `迁移预览` marker and the hidden launcher. It then continues to exercise the legacy director-planning controls within Studio. No production code, business assertion, threshold or runner policy was changed.

### Exact-SHA parallel acceptance in progress

Code/test SHA: `7865a15840090c71aa6808b2f70818618a1eaccf`.

- Parallel workstreams run `42775637350`: W1 generation durability PASS; W7 shared shell navigation PASS; W2 timeline transactions and W3 lifecycle/export/QC were still running at the last inspection.
- Product IA run `42775637428`: W4 project home/create and W5 settings IA/credential safety were still running at the last inspection.
- Windows-native publication run `42775637347`: PASS.
- Full hosted run `42775637705`: Studio release gate and full Python/browser regression were still running at the last inspection.
- These are separate hosted jobs for W1/W2/W3/W7, W4/W5, Windows-native and full release acceptance. Do not infer a lane PASS from another lane or from the mainline commit.
- W7's browser test file includes both the route/pane contract and the integrated home → create → close/reload/resume draft → settings save/export/partial-import → navigation journey. The W7 job runs the complete file; its PASS therefore covers that integrated journey on this SHA.

### W6 workflow syntax evidence

- The latest syntax-validation run still predates this code SHA. The seven workflow files were unchanged since the prior successful YAML parse, so their content is unchanged; however, exact-SHA W6 syntax validation remains MISSING until a current workflow-validation run is available.
- The current `validate-workflows.yml` push trigger only includes `.github/workflows/**`; it does not run on ordinary code/test commits. The next W6 improvement is to make the inexpensive YAML parse run on main code/test changes as well, excluding docs-only changes, so exact-SHA syntax evidence is generated alongside the independent lanes.

### Remaining gates

- W2 and the Studio release gate must complete on this exact SHA before the timeline/Studio release is considered closed.
- Full Python/browser regression, W1–W3/W7, W4/W5 and Windows-native evidence must be reconciled by exact SHA; no historical PASS is substituted.
- NVIDIA real token-stream acceptance remains MISSING/OPEN. It is not to run until full Python and Studio gates pass and the commit carries the explicit opt-in markers with the dedicated hosted secret.
- No self-hosted/local runner was used.



## 28. Exact-SHA parallel acceptance closure and W6 trigger repair — 2026-11-10

### Code/test SHA `7865a15840090c71aa6808b2f70818618a1eaccf`

The exact-SHA jobs have now all completed:

- Parallel workstreams run `42775637350`: W1 generation durability PASS; W2 timeline transactions PASS; W3 lifecycle/export/QC PASS; W7 shared shell navigation PASS.
- Product IA run `42775637428`: W4 project home/create lifecycle PASS; W5 settings IA/credential safety PASS.
- Full hosted run `42775637705`: Studio build/timeline/lifecycle/export/QC release gate PASS; full Python/browser regression PASS. Explicit NVIDIA real token-stream acceptance was SKIPPED, not passed.
- Windows-native publication run `42775637347`: PASS.
- All these runs target the same code/test SHA `7865a15840090c71aa6808b2f70818618a1eaccf`. This closes the previously pending W1–W5/W7, Studio, full-regression and Windows gates for that SHA. It does not close NVIDIA live acceptance.

### W6 trigger repair

- Commit `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a` updates only `.github/workflows/validate-workflows.yml`.
- The workflow now runs on main pushes except documentation-only changes, using `paths-ignore` for `docs/**`, `**/*.md` and `**/*.mdx`. Workflow/source/test changes therefore trigger the YAML parser, while docs-only ledger updates do not launch the expensive CI suite.
- Exact-SHA W6 validation and the affected regression workflows were triggered for `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a`: syntax run `42778257402`, full hosted `42778257129`, Product IA `42778257445`, Windows native `42778257207`. At the time of this ledger entry these were queued/in progress; no result is inferred. Refresh each workflow and each independent job before marking it PASS.
- Because the trigger change itself is a workflow-file change, it correctly triggers the YAML validation workflow. Docs-only ledger updates are excluded.
- Runner policy remains GitHub-hosted Ubuntu/Windows only. NVIDIA live acceptance remains separately gated and must not be inferred from ordinary full regression.

### Next required reconciliation

- On SHA `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a`, verify W6 YAML syntax, W1/W2/W3/W7, W4/W5, Studio release gate, full Python/browser regression and Windows-native publication independently.
- If any job fails, inspect its logs and preserve the lane's acceptance assertions; do not lower thresholds or substitute a different lane's result.
- Only after Studio and full Python/browser gates pass may the separately opted-in NVIDIA real token-stream acceptance be considered, using the dedicated hosted secret and no local model configuration.


## 29. Exact-SHA reconciliation of W1–W7 and hosted release gates — 2026-11-10

### Source and evidence identity

- Tested code/workflow SHA: `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a`.
- Current `main` HEAD at reconciliation: `0dd55580036a42a1aa12935e2e093a0adb8c5287`; this is a documentation-only ledger commit whose parent is the tested SHA. Do not describe the ledger HEAD as a separately tested code SHA.
- All runs below report the same tested `head_sha` above.

### Independent workstream outcomes

- Parallel workstreams run `42778257417`: W1 Generation durability job `129701791198` PASS; W2 Timeline transactions job `129701791115` PASS; W3 Lifecycle/export/QC job `129701791131` PASS; W7 Shared shell navigation job `129701790824` PASS.
- Product IA run `42778257445`: W4 Project home/create lifecycle job `129701791674` PASS; W5 Settings IA/credential safety job `129701791741` PASS.
- W6 Hosted workflow syntax validation run `42778257402` PASS.
- Windows-native publication run `42778257207` PASS.
- Full hosted run `42778257129`: Full Python/browser regression job `129701786903` PASS; Studio build/timeline/lifecycle/export/QC release gate job `129701787115` PASS.
- The NVIDIA real token-stream job `129709063478` was SKIPPED. This is not a PASS and leaves live-provider acceptance OPEN/MISSING.

### Reconciliation and next gate

- W1–W7's required independent lane jobs, workflow syntax validation, Studio release gate, full Python/browser regression and Windows-native publication are all PASS on the same exact code/workflow SHA `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a`.
- This closes the currently defined non-live hosted acceptance gates for that SHA. It does not prove real NVIDIA connectivity or token delivery.
- The live NVIDIA job is guarded by successful full-regression and Studio jobs plus either explicit `workflow_dispatch` input `run_live_nvidia=true` or the required live-stream/provider/model commit markers. It consumes the dedicated `NVIDIA_API_KEY` GitHub Actions secret and may incur provider cost. Do not dispatch or add cost-bearing opt-in markers without explicit user authorization and the secret's availability.
- Runner policy remains GitHub-hosted Ubuntu/Windows only. No self-hosted, local WSL, developer workspace or local model configuration was used.
- No test assertion, threshold, or lane boundary was weakened to obtain these results.



## 30. Post-acceptance remaining scope audit — 2026-11-10

### Exact-SHA acceptance remains closed for non-live gates

- Latest verified code/workflow SHA remains `1c89eb5105e1512c2cb4fcd50cd8f5ac2f59321a`; all W1–W7 scoped jobs, W6 YAML validation, Studio release gate, full Python/browser regression and Windows-native publication passed on that exact SHA (Section 29).
- Current main HEAD before this ledger-only update: `f2f318e31c0a2cc6226f1eaba928a2ca0f191258`. It only adds the Section 29 evidence reconciliation; it does not change executable code. Do not claim a new code test SHA.

### Remaining 19D boundaries — source audit

- Reviewed `docs/openfilm-film-editor-refactor.md` current boundaries, `docs/openfilm-gap-and-target-architecture.md`, `docs/FILM_AGENT_MANUAL.md`, `tests/test_film_scene_runtime.py`, `tests/test_film_render_scene_e2e.py`, `tests/test_film_runtime_video_frame_wait.py`, `video_director/film_scene_runtime.py` and `video_director/film_render.py`.
- Bounded deterministic scene support has direct coverage for the `frame(t)` protocol, ready/resource waits, controlled RAF limits, exact-frame serialization, missing local resources, external-network rejection, reverse-order sample comparison, revision pinning and clip-ID-specific frame failure reporting.
- This evidence does not establish deterministic rendering for arbitrary HTML, arbitrary WebGL, hidden asynchronous state, every codec/media combination or exhaustive edit-operation permutations. Those remain UNKNOWN / explicitly unsupported where the source contract says so; do not expand the claim or weaken fail-closed behavior.
- No production or test change is justified by this read-only audit. The next 19D-11 task is a bounded capability inventory: enumerate supported scene/resource/runtime classes and their explicit reject conditions before adding any new rendering capability. It must remain separate from the already-passing W1/W2/W3/W7 lanes and must not silently widen the renderer contract.

### Live NVIDIA acceptance — gated, not executed

- Current hosted workflow supports an explicit `workflow_dispatch` input `run_live_nvidia=true` after full regression and Studio gates pass; it uses only the dedicated `NVIDIA_API_KEY` GitHub Actions secret and can incur provider cost.
- The current manual `docs/FILM_AGENT_MANUAL.md` still describes the complete live test as local-only, while `parallel-workstreams.md` and this ledger require hosted-runner-only provider acceptance. This is a documentation/policy inconsistency; no local credentials, local model config or local runner may be used to resolve it.
- The live job remains MISSING/OPEN because the exact-SHA run skipped it. Do not dispatch it, add live commit markers or incur provider cost without explicit user authorization. A hosted live PASS must satisfy all eight criteria in the manual, including at least two real non-empty token fragments before result, correct revision-bound proposal, byte-identical `film.html` before approval and no credential leakage.
- Next action: reconcile the manual's local-only wording with the hosted-only runner policy, then wait for explicit authorization before any cost-bearing live call. No secret availability is assumed.

### Lane discipline for the next iteration

- W1/W2/W3/W4/W5/W7 remain independently owned and independently evidenced; no shared-shell edits are authorized by this audit.
- W6 owns any workflow/policy correction. W7 remains serial owner of `index.html` and `app.js`; W4/W5 retain their module boundaries.
- Any executable code/workflow change creates a new tested SHA and must rerun the affected independent lanes plus integrated gates. Documentation-only reconciliation does not substitute for or invalidate the existing code SHA evidence.



## 30. Bounded deterministic HTML-scene contract and W2/W3 lane coverage — 2026-11-10

### Audit finding

- The scene runtime is not a universal HTML/WebGL renderer. It injects a versioned `window.film.frame(t)` bridge for project-local HTML scenes and rewrites project-local media references. A scene must implement the frame contract to be accepted for deterministic offline rendering.
- The bridge controls `Date.now()` / zero-argument `Date`, `performance.now()`, a time-seeded `Math.random()`, CSS animation current time, and queued `requestAnimationFrame` callbacks. Exact frame requests are serialized; preview seeks are coalesced; unbounded RAF flushing is rejected. It waits for declared visual resources before acknowledging a frame.
- This does not normalize WebGL/GPU driver behavior, arbitrary browser APIs, uncontrolled timers, remote resource contents, or arbitrary DOM scripts that do not implement the frame contract. Those cases must not be represented as deterministically supported.
- `film_document.apply_ops` currently exposes 20 operation kinds: `marker_add`, `marker_set`, `marker_remove`, `clip_lock`, `element_set`, `set`, `move`, `slide`, `slip`, `join`, `roll`, `ripple_delete`, `remove`, `insert_ripple`, `insert`, `split`, `trim`, `track_remove`, `track_add`, and `track`. The existing contract suite contains broad atomicity, round-trip, revision, stale-write and edit behavior coverage, but “all mathematical permutations” is not a finite acceptance criterion.

### Bounded regression and lane ownership changes

- Test commit `44afcba7ecb1bfb6e2d8fa1ca022596fd49dfeef` adds a real Chromium test that requests the same exact local HTML frame twice and compares `Date`, `performance.now()` and seeded random fingerprints.
- Workflow commit `17ed00978adf6d9d6224710e040cd85aa2ba9efb` assigns `tests/test_film_scene_runtime.py` to W3's lifecycle/render/QC job.
- Workflow commit `0f79d5b2426d34192141ab088b73f76c1f09311a` also assigns `tests/test_film_timeline_markers.py` and `tests/test_film_clip_lock.py` to W2. Static audit found these operation contracts were covered by dedicated tests but omitted from W2's scoped regression list; they are now included rather than duplicated or rewritten.
- Current exact code/workflow SHA for the combined W2/W3 lane run: `0f79d5b2426d34192141ab088b73f76c1f09311a`. Current `main` HEAD is a later documentation-only commit; it is not a separately tested code SHA.
- On this exact SHA, W6 syntax validation run `42782667632` PASS and Windows-native publication run `42782668018` PASS. Parallel workstream run `42782667643`, Product IA run `42782667495`, and full hosted run `42782667995` were still in progress at ledger update time. No Lane outcome is inferred from those pending runs.

### Remaining bounded work

- 19D-11a supported local HTML-scene determinism remains pending exact-SHA W3 and full-hosted results. A successful same-time fingerprint test proves only the controlled primitives exercised by that fixture; it does not prove pixel-identical output across browsers, GPUs or arbitrary scene code.
- 19D-11b remains MISSING. Replace the unbounded “exhaustive permutations” wording with a finite operation-interaction matrix and explicit invariants: revision monotonicity, canonical serialization, undo/redo restoration, stale-revision rejection and transaction atomicity. Existing tests provide broad scenario coverage; identify concrete uncovered operation-order pairs before adding tests.
- 19D-10 real NVIDIA token-stream acceptance remains MISSING/OPEN and requires explicit user opt-in to a cost-bearing hosted run with the dedicated `NVIDIA_API_KEY` secret.
