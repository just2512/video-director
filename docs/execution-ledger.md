# Execution ledger — dual-plan delivery

**Repository:** `just2512/video-director`  
**Audit starting main SHA:** `99142d0887fa771bea7c3bd3e5bafd27ee435a1b`  
**Updated:** 2026-11-10  
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

At the start of this reconciliation, `main` was `99142d0887fa771bea7c3bd3e5bafd27ee435a1b`. The exact executable/workflow tree verified here is `9d7208cc9818839ea2c9cc5965298e0998c28b68`; GitHub compare confirms the two intervening commits changed only these two documentation files. All acceptance evidence below is tied to that exact executable/workflow SHA. Subsequent edits to this ledger and the parallel-workflow document are documentation-only and do not require rerunning the product suite.

### 2.1 19D — Film Studio / OpenFilm delivery

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 19D-01 | `film.html` is the sole timeline authority once present; legacy CompositionIR writes/exports fail closed | PASS | PASS — exact tree `9d7208cc`; full hosted regression run `42821701972` (1,952 passed, 2 skipped) and Studio gate (346 passed) | `test_project_manager_film_authority.py`, `test_film_document_contract.py` and Studio release-gate coverage passed on the same executable/workflow SHA |
| 19D-02 | Revision-pinned project context, stale-response rejection, project-switch isolation | PASS | PASS — W2 355 passed and full hosted regression 1,952 passed on exact tree `9d7208cc` | External-watch, pending-revision, project-switch and project-context regressions ran in hosted lanes; preserve stale-response and project-isolation assertions |
| 19D-03 | AI proposal preview/reject/apply is revision-bound and does not write before approval | PASS | PASS — W2 355 passed; Studio gate 346 passed on exact tree `9d7208cc` | W2 and Studio release gate both include `test_film_studio_ai_proposal_browser.py` and `test_film_studio_ai_stream_contract.py`; live-model acceptance is tracked separately under 19D-10 |
| 19D-04 | Multi-select and cross-track drag form one atomic transaction; Undo/Redo restore full affected state | PASS | PASS — W2 355 passed; Studio gate 346 passed on exact tree `9d7208cc` | React 19 timeline is built in W2; transaction, history, marker, lock, ripple and proposal suites passed without weakening assertions |
| 19D-05 | Timeline gestures reject stale external revisions and project-switch responses | PASS | PASS — W2 355 passed; full hosted regression 1,952 passed on exact tree `9d7208cc` | External-watch, pending-revision and project-switch browser regressions passed; retain the existing persisted-revision checks |
| 19D-06 | Generation job reservation, provider identity, ambiguous submission, download recovery and Manifest/Registry idempotency | PASS | PASS — W1 89 passed on exact tree `9d7208cc`, run `42821701905` | Generation recovery and manifest invariants passed; video-generation provider identity remains distinct from the LLM streaming path |
| 19D-07 | Manual generated-asset adoption; generation completion cannot overwrite a newer user revision | PASS | PASS — W3 287 passed, 1 skipped on exact tree `9d7208cc`, run `42821701905` | Generated-asset adoption browser regression passed as part of lifecycle coverage; explicit adoption and stale-revision rejection remain required |
| 19D-08 | Project create/open/migrate/edit/reopen lifecycle uses one project and one FilmDocument revision | PASS | PASS — W3 287 passed, 1 skipped; full hosted regression 1,952 passed on exact tree `9d7208cc` | Existing integrated lifecycle fixtures passed; no duplicate lifecycle fixture was introduced |
| 19D-09 | Actual MP4 export and QC artifacts are bound to the exact FilmDocument revision | PASS | PASS — W3 287 passed, 1 skipped; Studio gate 346 passed on exact tree `9d7208cc` | Render/publication transaction, actual render, revision provenance and QC regressions passed in hosted acceptance |
| 19D-10 | Real model token streaming against a currently supported endpoint | PASS | PASS — explicit hosted NVIDIA manual-dispatch run `42802192086` on tested SHA `295b0acf`; completed live JUnit evidence is recorded in §37 | This closes the LLM token-stream/proposal/approval path only; it is not evidence of a real video-generation provider call. Do not repeat paid calls without explicit opt-in |
| 19D-11a | Supported project-local HTML scene deterministic frame contract (`window.film.frame(t)`) | IMPLEMENTED | PASS within the documented boundary — W3 287 passed and Studio gate 346 passed on exact tree `9d7208cc` | `test_film_scene_runtime.py` and exact-time primitive fingerprint coverage passed; no arbitrary HTML/WebGL or cross-device pixel-identity claim |
| 19D-11b | Finite edit-operation interaction matrix and state invariants (not arbitrary WebGL determinism or exhaustive permutations) | IMPLEMENTED | PASS for the finite documented matrix — W2 355 passed and Studio gate 346 passed on exact tree `9d7208cc` | Marker/clip atomic batch, rollback, track add/remove, lock, split/join, ripple, fade, volume-envelope and keyframe interactions passed; no exhaustive-permutation claim |

### 2.2 07D — Product information architecture

| ID | Deliverable | Implementation status | Current exact-main acceptance | Evidence / next action |
|---|---|---|---|---|
| 07D-01 | Safe partial model-config updates preserve omitted roles/fields and keys | PASS | PASS — W5 13 passed on exact tree `9d7208cc`, run `42821702016`; full regression also passed | Safe merge, partial import and export-secret-redaction contracts passed; no local config or credentials were read |
| 07D-02 | Existing create flow and browser/server draft recovery chain | PASS | PASS — W4 36 passed on exact tree `9d7208cc`, run `42821702016` | Real Chromium create/cancel/resume and browser/server draft persistence tests passed; existing lifecycle hooks and storage keys remain intact |
| 07D-03 | Project-first home hierarchy and first-class create experience | PASS | PASS — W4 36 passed on exact tree `9d7208cc`, run `42821702016` | Project-first home and create workspace are covered by the W4 browser suite; no second backend or state machine introduced |
| 07D-04 | Full-work-area create flow with safe cancel/failure/resume and persisted project name/type | PASS | PASS — W4 36 passed on exact tree `9d7208cc`, run `42821702016` | Name/type persist through draft, project params, project index, prefill, rename and retry; keyboard focus lifecycle is now included |
| 07D-05 | Categorized settings IA and progressive disclosure | PASS | PASS — W5 13 passed on exact tree `9d7208cc`, run `42821702016` | Settings browser and model-config persistence contracts passed, including credential preservation and export redaction |
| 07D-06 | Shared shell navigation and in-app view contract | PASS | PASS — W7 2 passed on exact tree `9d7208cc`, run `42821701905` | Keyboard navigation, active `aria-current`, inactive-pane `aria-hidden`, stable URL and one-visible-pane behavior passed |
| 07D-07 | Project-scoped output defaults with backward-compatible reads | PASS | PASS — W4 36 passed on exact tree `9d7208cc`, run `42821702016` | Width/height/FPS restore, clean project-switch defaults and legacy platform fallback passed |
| 07D-08 | Product E2E: home → create/cancel/resume → settings expand/save/import/export | PASS | PASS — W7 2 passed on exact tree `9d7208cc`, run `42821701905`; W4/W5 isolated lanes also passed | Integrated browser journey passed alongside separate W4 36 / W5 13 results; keyboard shell semantics are additionally verified |

## 3. Workstream partition — what can genuinely run in parallel

Workstreams are logical ownership lanes implemented through short-lived, task-scoped Git branches from one frozen `main` SHA. Each branch has disjoint file ownership and an independent GitHub-hosted owner CI lane; completed changes integrate path-scoped and serially to `main`. No long-lived feature branches or PR/manual-review handoff are used. `main` is the sole integration target, not the parallel development workspace.

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
| W8 Shotcraft creative browser acceptance | 19D | Shotcraft creative-workflow and visual-overlay browser acceptance | W1/W2/W3/W4/W5 isolated work | Does not own deterministic HTML-scene rendering; W3 owns scene frame/resource/export contract |

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

## 5. CI and runner policy — current verified state

| Workflow | Current configuration | Decision |
|---|---|---|
| Legacy `ai-director.yml` | Removed from `.github/workflows/` | No self-hosted workflow remains in the active workflow directory; do not recreate it |
| `parallel-workstream-ci.yml` | Main push + manual dispatch; W1/W2/W3/W7 independent jobs on `ubuntu-24.04` | PASS — exact tree `9d7208cc`, run `42821701905` |
| `product-ia-regression.yml` | Main push + manual dispatch; W4/W5 independent jobs on `ubuntu-24.04` | PASS — exact tree `9d7208cc`, run `42821702016` |
| `full-hosted-regression.yml` | Canonical exact-SHA full pytest/browser + Studio release gate; NVIDIA live test is explicit manual opt-in | PASS for non-live gates — run `42821701972`; live job correctly SKIPPED on push |
| `focused-storage-watch.yml` | GitHub-hosted Ubuntu on selected main paths | PASS — run `42821701901` |
| `studio-ui-build.yml` | GitHub-hosted Ubuntu, manual dispatch | Available for targeted manual validation |
| `windows-native-publication.yml` | GitHub-hosted `windows-latest` | PASS — 2 native tests, run `42821701904` |
| `validate-workflows.yml` | GitHub-hosted Ubuntu YAML validation | PASS — run `42821701924` |

All active workflow files use GitHub-hosted runners. W6 has upgraded checkout/setup/upload actions to the v7 majors using the Node.js 24 action runtime; the exact-SHA logs contain no Node.js 20 forced-runtime warning. Never read developer-local model configuration, secrets or storage from CI.

## 6. Current executable queue

| Order | Task | Lane | Exit condition | State |
|---:|---|---|---|---|
| 1 | Create-dialog keyboard focus containment/restoration and shared-shell keyboard/ARIA semantics | W4/W7 | Real Chromium verifies initial focus, Tab/Shift+Tab trap, focus restoration, keyboard navigation, active `aria-current` and inactive `aria-hidden` | PASS — W4 36 passed; W7 2 passed on exact tree `9d7208cc` |
| 2 | Reconcile all parallel lanes and integrated hosted release gates | W0/W1–W7 | All scoped lanes, full pytest, Studio gate, YAML and Windows-native tests pass on one executable/workflow SHA | PASS — W1 89; W2 355; W3 287/1 skipped; W4 36; W5 13; W7 2; full pytest 1,952/2 skipped; Studio 346; Windows 2; YAML PASS on `9d7208cc` |
| 3 | Maintain GitHub Actions Node.js 24 runtime baseline | W6 | Supported action versions and workflow syntax remain valid without Node.js 20 forced-runtime warnings | PASS — exact tree `9d7208cc`; YAML run `42821701924` |
| 4 | Classify and resolve remaining test diagnostics without suppressing them | W0 + owning test lane | Determine whether Pydantic serializer warnings are fixture-only or reveal a real validation/serialization gap; clean invalid-escape and Pillow deprecation warnings without changing business assertions | IMPLEMENTED — fixture root cause corrected in `cd99d98`; exact-SHA CI pending |
| 5 | Resolve root runner-policy contradiction in `CONTRACT.md` §§0–0.1 | W0 | Explicit impact analysis and controlled Contract Change align the highest-level contract with GitHub-hosted-only execution, while preserving hosted-runner tests | IMPLEMENTED — user-authorized Contract Change in `cd99d98`; exact-SHA CI pending |

**Live-provider boundary:** 19D-10 is already PASS from the separately authorized hosted NVIDIA manual dispatch in §37. The latest ordinary push correctly skipped the live job. Do not incur another provider call merely to refresh documentation; only a separately authorized manual opt-in may run it.

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
- The manual's live acceptance procedure has since been reconciled to hosted-only execution (see §48 and `docs/FILM_AGENT_MANUAL.md`). The live job remains MISSING/OPEN because ordinary exact-SHA runs intentionally skip it. Do not dispatch it or incur provider cost without separate explicit authorization. A hosted live PASS must satisfy all eight criteria in the manual, including at least two real non-empty token fragments before result, correct revision-bound proposal, byte-identical `film.html` before approval and no credential leakage. No secret availability is assumed.

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


## 31. W7 settings refresh race diagnosis and correction — 2026-11-10

- On code/workflow SHA `0f79d5b2426d34192141ab088b73f76c1f09311a`, W7 shared-shell navigation failed in `test_home_create_resume_then_settings_round_trip_in_one_browser_journey`. The assertion observed the persisted `llm.model` still equal to the original `journey-model` after the UI reported save success; expected `journey-model-updated`. W1 and W5 passed on that SHA; W2/W3 and full hosted jobs were superseded while still running, so no results are claimed for them.
- Source audit found two concurrent calls to `refreshLLMBadge()`: one unconditional call during application startup and another when `switchTab('settings')` opens the settings pane. Both asynchronously fetch model roles and re-render the settings form. The late response can overwrite an in-progress field edit before `saveModelConfig()` reads the form. The test's server log confirms the endpoint wrote six roles and returned success, but the target model value remained old.
- Corrective commit `dff1b42c05110a920250f04147799c5bf4f32c83` removes the unnecessary startup refresh. Model roles are now fetched when the settings pane is opened, avoiding the duplicate startup/route render race. The existing integrated W7 journey remains the acceptance test; no assertion was relaxed.
- Exact-SHA verification for `dff1b42c05110a920250f04147799c5bf4f32c83`: W6 syntax run `42783250937` PASS; Windows-native run `42783251068` PASS; W1 job `129718119351` PASS; W7 job `129718118539` PASS. W2 job `129718119043`, W3 job `129718118946`, W4 job `129718235040`, W5 job `129718234468`, full Python/browser job `129718237986`, and Studio release gate job `129718238142` were still IN_PROGRESS at the latest refresh. NVIDIA live acceptance remains separately opt-in and is not inferred from these jobs.


## 32. Parallel next-stage execution and exact-SHA verification — 2026-11-10

### Prior integrated baseline

- Code SHA `dff1b42c05110a920250f04147799c5bf4f32c83` has independently completed W1 generation durability, W2 timeline transactions, W3 lifecycle/export/QC, W7 shared-shell navigation, W4 project/create, W5 settings/credential safety, W6 workflow syntax, Studio release gate, full Python/browser regression and Windows-native publication. All jobs were GitHub-hosted Ubuntu/Windows and PASS.
- Run IDs: parallel `42783251038`; Product IA `42783251023`; full hosted `42783251069`; Windows native `42783251068`; workflow syntax `42783250937`.
- The NVIDIA real token-stream job `129722711516` was SKIPPED. 19D-10 remains MISSING/OPEN; no provider call is authorized by this ledger update.

### New independent lane work

- W2 added two operation-interaction regressions:
  - `test_marker_and_clip_mutations_share_one_atomic_revision_and_undo_redo_history` and `test_marker_then_invalid_clip_operation_leaves_input_document_unchanged` in `tests/test_film_timeline_markers.py`.
  - `test_track_add_then_remove_is_a_reversible_operation_pair` in `tests/test_film_document_contract.py`.
- W2 documented the finite, risk-based operation interaction contract in `docs/film-operation-interaction-matrix.md`. This is not an exhaustive 20×20 permutation claim; it identifies coupled operation pairs and their required invariants.
- W3 documented supported and rejected deterministic HTML-scene capabilities in `docs/film-scene-capability-matrix.md`. The documented boundary remains project-local HTML implementing `window.film.frame(t)`; arbitrary HTML/WebGL and cross-device pixel identity are not claimed.
- W0 corrected `docs/FILM_AGENT_MANUAL.md`: real NVIDIA acceptance is hosted-only, explicitly dispatched after the Studio and full-regression gates, and uses only the dedicated GitHub Actions Secret on `ubuntu-24.04`. Local/WSL/self-hosted execution and local model-config reads are prohibited. The workflow was not dispatched and no provider cost was incurred.

### Exact tested code SHA and live CI state

- New tested code SHA: `5f1f0b3f3924eb6233adf5ea2bca8fb98b987c2f`.
- Current `main` HEAD after documentation-only matrix commit: `72df2a04eec2c69babe2228405ffe5140940dde0`. The documentation-only commit does not alter executable code and is not a new tested code SHA.
- Workflow syntax run `42786616558`: PASS.
- Windows-native publication run `42786616614`: PASS.
- Parallel workstreams run `42786616459`: W1 job `129729436096` PASS; W2 `129729436032`, W3 `129729435949`, and W7 `129729435451` were IN_PROGRESS at the last inspection.
- Product IA run `42786616536`: W4 `129729431719` and W5 `129729431387` were IN_PROGRESS at the last inspection.
- Full hosted run `42786616833`: full Python/browser job `129729448679` and Studio release gate `129729448389` were IN_PROGRESS at the last inspection.
- Earlier runs on intermediate code SHA `c5170451d18a13f4189ef0eff9517f10a20c098e` were superseded/cancelled by the next main commit. Do not carry results from those runs forward.

### Next reconciliation

1. Refresh every W1–W7 job, full Python/browser regression, Studio release gate, W6 syntax and Windows-native result for exact code SHA `5f1f0b3f3924eb6233adf5ea2bca8fb98b987c2f`.
2. If W2 fails, inspect the new marker/clip and track add/remove assertions; preserve atomicity, history and canonical round-trip assertions.
3. If any lane fails, diagnose the failing job and fix the owning lane without weakening its acceptance conditions. Re-run affected lanes and integrated gates on the new exact SHA.
4. Keep 19D-10 live NVIDIA acceptance MISSING until separately and explicitly authorized. A green default hosted run or SKIPPED live job is not a PASS.
5. Continue W2's finite operation-interaction matrix and W3's bounded scene capability inventory as separate lanes. W7 remains the sole serial owner of shared shell files; W4/W5 remain independent module owners.


## 33. W2 asynchronous authority-test correction and W6 live-trigger hardening — 2026-11-10

### W2 failure diagnosis and correction

- On code SHA `5f1f0b3f3924eb6233adf5ea2bca8fb98b987c2f`, W2 job `129729436032` ran 354 tests successfully and failed one existing browser test: `test_legacy_director_controls_remain_available_only_without_film_document`.
- The failure was at the legacy-project transition: the test dispatched a fire-and-forget selector `change` event and waited indirectly for the Studio panel. The application uses the async `window.editorLoad` authority wrapper, so the test did not observe the transition's actual result.
- Test-only correction commit `180e9850a058ee4ee5edbc4a6c8935ccd926fa18`: wait until `window.__filmStudioEditorAuthorityWrapperInstalled` is true, invoke and await the same `window.editorLoad()` handler used by the selector, and assert the returned `mode === "migration"`. No production code, migration behavior, acceptance assertion or timeout threshold was weakened.
- The `180e` rerun's W1 and W7 passed; W4 and W5 passed; Studio release gate passed. W2/W3 scoped jobs were still in dependency installation when superseded by the W6 workflow correction below. Do not treat the incomplete parallel run as a W2/W3 PASS.

### W6 live-provider trigger hardening

- Workflow commit `18769c9b5e3d175b0af443ffe1f0e9aa653fa22b` removes the push/commit-message marker path from `full-hosted-regression.yml`.
- The NVIDIA real-model job now requires all of: full Python/browser regression PASS, Studio release gate PASS, `workflow_dispatch`, and `inputs.run_live_nvidia == true`. A normal push cannot trigger a provider call by commit-message marker.
- This matches the manual's hosted-only policy. No dispatch was performed, no provider call was made, and no cost was incurred.

### Current exact-SHA hosted verification

- Tested code/workflow SHA: `18769c9b5e3d175b0af443ffe1f0e9aa653fa22b`.
- W6 workflow syntax run `42788229646`: PASS.
- Windows-native publication run `42788229601`: PASS.
- Parallel run `42788229575`: W1 job `129734864674` PASS; W2 `129734864353`, W3 `129734864907`, and W7 `129734864838` were IN_PROGRESS at last inspection.
- Product IA run `42788229607`: W4 `129734746929` and W5 `129734746349` were IN_PROGRESS at last inspection.
- Full hosted run `42788229597`: full Python/browser job `129734875133` and Studio release gate `129734875521` were IN_PROGRESS at last inspection.
- These jobs are independent GitHub-hosted runners. The parallel run uses four separate Ubuntu jobs; Product IA uses two separate Ubuntu jobs; full regression uses separate full-suite and Studio-gate jobs. No self-hosted, WSL or developer runner was allocated.

### Required next reconciliation

- Refresh every independent job and integrated gate for exact SHA `18769c9b5e3d175b0af443ffe1f0e9aa653fa22b`. Do not carry W2/W3 evidence from the incomplete `180e` run.
- If the awaited W2 transition still fails, inspect the returned `load_result` and browser DOM/authority state; do not add forced clicks, skips or relaxed assertions.
- Confirm the NVIDIA live job is SKIPPED on this push. 19D-10 remains MISSING/OPEN until a separate, explicitly authorized `workflow_dispatch` with `run_live_nvidia=true`.


## 34. React timeline parity, W2 integrated failure and hosted revalidation — 2026-11-10

### Exact-SHA result reconciliation

- Code SHA `e75d075b4205a8c4a84de876810c3903f3dcc4b1`:
  - W1/W2/W3/W7 independent parallel jobs: PASS (run `42788985010`).
  - W4/W5 Product IA jobs: PASS (run `42788984901`).
  - Workflow syntax: PASS (run `42788984906`).
  - Windows-native publication: PASS (run `42788985219`).
  - Full Python/browser regression: PASS (job `129737436341`, full run `42788985381`).
  - Studio release gate: FAIL (job `129737436447`): 345 passed, 1 failed, 2 warnings. Failure: `tests/test_film_studio_roll_browser.py::test_film_studio_cross_track_drag_shows_react_ghost_and_commits_target_track[valid-target]`. The failure diagnostic shows the transaction journal and history contain the expected move of `clip-a` to `video-alt` at 5s, while the final `film.html` read still contains `clip-a` on `video-main`. This is a real persistence/observation inconsistency, not an assertion to relax.
  - NVIDIA live job was SKIPPED; 19D-10 remains MISSING/OPEN.
- The earlier W2 scoped job was not configured with `VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO=1` and did not build the React Studio bundle. Therefore its PASS on `e75d075` did not establish React timeline behavior. This CI parity defect explains why the scoped W2 lane did not catch the integrated React-only failure.

### Corrections committed

- Test diagnostic commit `aba3b648b60de59952ae87c32e697d29e0394532` adds an explicit wait for the cross-track `/api/editor/film/edit` response and records its status/body before checking persisted `film.html`. Existing operation, revision, source/target track, history and cleanup assertions remain intact.
- W6 workflow correction commit `cd1b909858f8857c30ae91af05d21dbf20122ee5` makes W2 build React 19 Studio (typecheck, JS bundle and Tailwind CSS) and sets `VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO=1`. W2's scoped browser tests now run against the same React timeline implementation as the integrated Studio release gate.
- Parallel-workflow contract documentation commit `4e329c8623ede507bcf88c1cab08f02284bac6ea` records this parity requirement. This is documentation-only and does not alter the tested code/workflow SHA.

### Hosted verification started

- Tested code/workflow SHA: `cd1b909858f8857c30ae91af05d21dbf20122ee5`.
- Parallel workstream run `42794600705`; Product IA run `42794600593`; full hosted run `42794600956`; workflow syntax run `42794600765`; Windows-native run `42794600862`.
- At the time of this ledger entry these runs were queued/pending. The earlier `aba3b648` run's W1 and Windows-native checks had completed successfully; remaining jobs were superseded by the W6 correction and must not be carried forward as exact-SHA acceptance.
- All workflows remain GitHub-hosted Ubuntu/Windows only. No self-hosted, local, WSL or developer runner was used. No NVIDIA provider call was dispatched.

### Required reconciliation

1. Refresh all W1/W2/W3/W7 and W4/W5 jobs plus full Python/browser, Studio release gate, YAML and Windows publication for exact SHA `cd1b909858f8857c30ae91af05d21dbf20122ee5`.
2. Inspect the new W2 React-required run's cross-track POST response body and the exact disk revision. If it exposes an interrupted FilmDocument transaction, fix the owning W2/FilmDocument persistence defect; do not weaken the persisted-source/target/history assertions.
3. If W2 scoped and integrated gate disagree again, treat that as an orchestration or environment parity failure, not a reason to accept either result without diagnosis.
4. Keep 19D-10 MISSING until separately authorized explicit hosted dispatch. No automatic or commit-marker provider invocation is permitted.


## 35. React-parity hosted acceptance and bounded W2/W3 contract closure — 2026-11-10

### Exact-SHA reconciliation

- Tested code/workflow SHA: `cd1b909858f8857c30ae91af05d21dbf20122ee5`.
- After the tested code/workflow SHA, `main` advanced only through documentation-only ledger commits `ce6a5f500c7a4ba544ff3c40c1c6291584fa6d78` and `3c41421f9f41ce4c1a4baa503cbb16b2d8ce68ca` (followed by this provenance correction). These commits do not change executable code/workflow. All workflow jobs below checked out the immutable tested SHA `cd1b909858f8857c30ae91af05d21dbf20122ee5`.
- Parallel workstream run `42794600705`: W1 job `129756527270` PASS (89 passed); W2 job `129756527669` PASS (355 passed, 1 warning); W3 job `129756527886` PASS (251 passed, 1 skipped, 1 warning); W7 job `129756528160` PASS (2 passed).
- Product IA run `42794600593`: W4 job `129756521907` PASS (35 passed); W5 job `129756521768` PASS (13 passed).
- Full hosted run `42794600956`: full Python/browser job `129756550422` PASS (1,950 passed, 2 skipped, 8 warnings; JUnit summary 1,952 tests, 0 failures, 0 errors, 2 skipped); Studio release gate job `129756550686` PASS (38 React smoke tests and 346 timeline/lifecycle/export/QC tests; 2 warnings).
- Studio gate directly confirms `test_film_studio_cross_track_drag_shows_react_ghost_and_commits_target_track` passes for valid target, locked target, overlap target and multi-select valid target. The W2 lane now builds React 19 Studio and sets `VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO=1`, so scoped and integrated tests exercise the same timeline implementation.
- W6 workflow syntax run `42794600765`: PASS. Windows-native publication run `42794600862`: PASS. All jobs used GitHub-hosted Ubuntu/Windows; no local, WSL or self-hosted runner was used.
- The explicit NVIDIA live job `129762881568` was SKIPPED. 19D-10 remains MISSING; no provider call was made and no cost incurred.

### Bounded contract status

- **19D-11a — PASS within the documented supported boundary.** W3 and the Studio release gate ran `tests/test_film_scene_runtime.py`; the exact-time primitive fingerprint test passed. The integrated gate also passed the serialized exact-frame/coalesced-preview regression. This validates only project-local HTML scenes implementing `window.film.frame(t)` and the primitives covered by the tests; it does not claim arbitrary HTML/WebGL or cross-device pixel determinism.
- **19D-11b — PASS for the finite, documented interaction matrix.** W2 ran the marker/clip atomic-batch, invalid-operation rollback, track add/remove round-trip, clip/track lock, split/join, ripple, fade, volume-envelope and keyframe interaction regressions. The new marker/clip and track add/remove tests in section 32 passed on this exact SHA. This is not an exhaustive permutation claim for all 20 operation kinds.
- **07D integrated journey — PASS.** W4 and W5 isolated suites passed, and W7's two browser tests passed, including the single-browser home → create/cancel → reload/draft recovery → settings save/import/export → navigation journey. The journey verifies model-key preservation and export redaction; isolated W4/W5 results are retained separately rather than substituted for the integrated test.

### Diagnostics retained without weakening gates

- The full suite emitted a 45-second faulthandler diagnostic while `test_chromium_loads_unified_editor_workspace` was waiting in Playwright's event loop; that same test passed approximately nine seconds later, and the complete suite finished with zero failures/errors. This is not recorded as a test failure or used to change a timeout. Keep it as a performance/startup observation for a future reproducible investigation.
- Existing deprecation warnings remain warnings; no assertion, timeout threshold, skip rule or business acceptance condition was relaxed to obtain these results.

### Next execution

1. Keep W1–W6 independently owned and independently runnable on GitHub-hosted runners; W7 remains the sole serial writer for `index.html` and `app.js`.
2. Do not add operation permutations merely to increase test count. Add a W2 case only when a concrete shared-state interaction or invariant gap is identified; retain the finite matrix as the contract.
3. Keep 19D-10 MISSING until a separately authorized `workflow_dispatch` with `run_live_nvidia=true`; ordinary pushes and green mocked SSE tests cannot close it.
4. Re-run affected scoped lanes and the integrated hosted gates on a new exact code/workflow SHA after any executable or workflow change. Documentation-only changes do not replace that evidence.


## 36. W1 resolved video-provider identity audit and W3 lane-scope reconciliation — 2026-11-10

### W1 / Issue #121 audit

- Audited the current `main` implementation of `video_director/generation.py`, `tests/test_generation_recovery.py`, `tests/test_generation_gap1.py`, and `tests/test_project_manifest.py`. Issue #121's production implementation is already present; no additional code change is justified by the current evidence.
- `generate()` resolves the effective `roles.video` configuration before computing the video fingerprint. The canonical identity contains provider ID, normalized endpoint scheme/host/path, effective model, request-contract version, and a SHA-256 identity fingerprint.
- API keys, URL userinfo, query strings and fragments are excluded. API-key rotation alone preserves identity; changing the effective model or endpoint changes identity.
- The same non-secret identity is persisted to durable video reservations and manifest rows. Manifest reuse, reusable jobs, unresolved submissions and production recovery are identity-bound. Pre-identity legacy manifest rows/jobs fail closed rather than inferring provider identity from model name.
- Timeout recovery reuses the existing provider task and preserves the one-POST invariant. Non-video fingerprint behavior remains unchanged.
- Focused regressions for these conditions are present in `tests/test_generation_recovery.py`, including endpoint normalization/secret exclusion, model/endpoint separation, legacy fail-closed behavior, and timeout recovery without a second paid POST.
- Exact-SHA evidence: W1 job `129756527270` on tested code/workflow SHA `cd1b909858f8857c30ae91af05d21dbf20122ee5` passed **89 tests**. The same SHA's full Python/browser regression passed **1,950 tests** (JUnit: 1,952 total, 0 failures, 0 errors, 2 skipped); Studio release gate passed **384 tests** (38 React smoke + 346 timeline/lifecycle/export/QC). This closes the code-level acceptance conditions in Issue #121. The issue remains open pending reconciliation of its historical live-provider acceptance note with the separately tracked 19D-10 gate; no real NVIDIA call is inferred from these mocked/provider-isolated regressions.
- No API key or provider secret was read, logged, or changed during this audit.

### W3 / parallel-lane contract correction

- The W3 hosted job runs `tests/test_film_studio_generated_asset_adoption_browser.py`, but the parallel-workstreams scope table did not list it. The test verifies pending generation cannot overwrite a concurrent user FilmDocument edit, stale-revision adoption is rejected, explicit manual adoption commits, and the result survives full-page reload.
- W3 also runs `tests/test_film_document_contract.py` as a shared lifecycle/serialization dependency check; W2 remains its primary owner for timeline operation semantics. The lane documentation now records this overlap explicitly.
- Documentation correction commit: `0c28fdda34cbef55699d11a78ed0b09ccbf56337`. Documentation-only; executable code/workflow SHA and prior exact-SHA test evidence are unchanged.

### Parallel execution / remaining gates

- Latest exact-SHA hosted evidence remains: W1/W2/W3/W7 independent jobs PASS; W4/W5 independent jobs PASS; full Python/browser and Studio release gate PASS; workflow syntax and Windows-native publication PASS.
- This conforms to the documented model: independent workstream ownership and GitHub-hosted CI jobs, integrated serially into `main`; no long-lived feature branches or PR handoff. W7 remains the sole serial owner of `index.html` and `app.js`.
- 19D-10 remains **MISSING/OPEN** until an explicitly authorized `workflow_dispatch` with `run_live_nvidia=true`. The NVIDIA live job was SKIPPED in the default full-hosted run; no provider call or cost was incurred.
- No other currently identified non-NVIDIA open issue requires a speculative code change. Continue with concrete W1–W7 acceptance gaps only; do not add tests or code solely to increase counts.


## 37. Explicit NVIDIA live-model acceptance completed — 2026-11-10

### Exact hosted run

- Manual dispatch run: [42802192086](https://github.com/just2512/video-director/actions/runs/42802192086), event `workflow_dispatch`, triggered by the repository owner.
- Tested code/workflow SHA: `295b0acf08ce2a38d384a23b69eaa55fa354de86`.
- The run completed successfully. Its independent GitHub-hosted jobs all passed:
  - Full Python/browser regression job `129782406908`: **1,950 passed, 2 skipped, 8 warnings**; JUnit summary: 1,952 tests, 0 failures, 0 errors, 2 skipped.
  - Studio build/timeline/lifecycle/export/QC job `129782407256`: **346 passed, 2 warnings**.
  - Explicit NVIDIA real token-stream job `129788232026`: **PASS**, not skipped.
- The live job first called NVIDIA's official endpoint `https://integrate.api.nvidia.com/v1` using model `z-ai/glm-5.3-flash`; provider probe returned `LIVE_PROVIDER_PROBE=PASS` with first token at 107.50 seconds.
- The application-level live test returned HTTP 200 with `text/event-stream`, 44 SSE events and 28 non-empty token chunks. The event sequence was progress → token → result; first token to final result was 1.1777 seconds.
- Dedicated test `test_configured_film_ai_stream_delivers_tokens_before_result_without_writing_film`: **1 passed** (141.43 seconds).
- The test validated the complete real-model proposal lifecycle:
  - proposal uses the exact requested base FilmDocument revision and contains exactly one move of `clip-b` to 8.5 seconds;
  - proposal generation does not change FilmDocument revision or rewrite `film.html`;
  - explicit approval applies the registered proposal, not client-supplied replacement operations;
  - replaying the same approval is rejected with HTTP 409 / `PROPOSAL_NOT_FOUND_OR_STALE`;
  - replay does not create another document write.
- The live job logs record only token-fragment lengths/timing, not token contents; API credentials are masked in Actions logs and redacted from test error details. No credential value is recorded in this ledger.
- Dedicated JUnit artifact: `nvidia-live-stream-295b0acf08ce2a38d384a23b69eaa55fa354de86` (artifact ID `13756719238`, retained until 2026-12-10). Full regression and Studio artifacts are also retained by run `42802192086`.

### Status correction and SHA provenance

- **19D-10: PASS.** The prior section 36 statement that 19D-10 remained MISSING/OPEN is superseded by this completed explicit dispatch and its live JUnit evidence.
- The current `main` head is `1312b6776b8bfc97f321ecb2648e9bddea1fbc68`. It descends from tested SHA `295b0acf08ce2a38d384a23b69eaa55fa354de86` through documentation-only commits `0c28fdda34cbef55699d11a78ed0b09ccbf56337` and `1312b6776b8bfc97f321ecb2648e9bddea1fbc68`; no executable code or workflow changed after the tested SHA.
- The previous MISSING statement was a stale-ledger error: the manually dispatched run existed and had completed. Do not repeat the MISSING status.
- This live-model acceptance proves the LLM streaming/proposal/approval path. It is not evidence that the separate video-generation provider was called; keep those provider identities and acceptance scopes distinct.


## 38. W1 issue closure and W3 Asset Library platform-lock acceptance — 2026-11-10

### W1 / Issue #121 closed on explicit acceptance conditions

- Issue [#121](https://github.com/just2512/video-director/issues/121) is now **CLOSED / completed**. Its own stated closure conditions are focused generation recovery tests plus full pytest; both have direct hosted evidence.
- W1 exact code/workflow SHA `cd1b909858f8857c30ae91af05d21dbf20122ee5`: 89 passed, job `129756527270`, run `42794600705`.
- Full Python/browser regression: 1,950 passed, 2 skipped, 0 failures/errors on SHA `295b0acf08ce2a38d384a23b69eaa55fa354de86`, job `129782406908`, run `42802192086`. The compare `cd1b909...295b0acf` confirms the intervening five commits changed only `docs/execution-ledger.md` and `docs/parallel-workstreams.md`; executable code and workflow files are identical.
- Studio release gate: 346 passed, job `129782407256`, same run.
- Issue closure comment explicitly distinguishes video-generation provider identity (Issue #121) from the NVIDIA LLM token-stream/proposal path (19D-10). The NVIDIA live test is not misrepresented as a real video-generation call.

### W3 / Asset Library lock coverage gap found and addressed

- The Asset Library uses `msvcrt.locking` on Windows and `fcntl.flock` on POSIX. Before this update, the Windows-native workflow tested `MoveFileExW` durability only; the Windows-specific catalog process-lock branch was not exercised by that workflow.
- Added `test_catalog_file_lock_serializes_independent_processes` to `tests/test_asset_catalog.py`. It starts two independent spawned Python processes against the same storage root, proves the second cannot enter the catalog transaction while the first holds the lock, then proves it enters after release.
- Added `tests/test_asset_catalog.py` to the W3 lifecycle/export hosted lane so the Asset Library catalog invariants are an explicit W3 responsibility, not only incidental coverage in the full suite.
- Extended `windows-native-publication.yml` to run the new process-lock test on `windows-latest`, alongside the existing native write-through replace/delete test. This invokes the real Windows `msvcrt.locking` implementation.
- Updated `parallel-workstreams.md` to record W3 Asset Library ownership and distinguish Linux `fcntl.flock` evidence from Windows `msvcrt.locking` evidence.

### Exact code/workflow SHA and CI

- Code/workflow SHA: `b1616671789dd7944c030625e9afd068248c3276`.
- Windows native run `42808073283`: **PASS**; log confirms both native tests ran, **2 passed in 1.40s**.
- Parallel workstream run `42808073287`: W1 and W7 completed PASS; W2 and W3 were still running at ledger update time. W3 now includes the Asset Library suite.
- Product IA run `42808073344`: W5 completed PASS; W4 still running.
- Full hosted run `42808073308`: full Python/browser and Studio release-gate jobs were still running.
- Workflow YAML validation run `42808073280`: PASS.
- Superseded intermediate runs for `6a290f2` and `8cdf1d8` were cancelled by the repository's configured per-branch concurrency after the later main commits; they are not counted as acceptance.
- Documentation-only commits after `b161667` do not alter the tested code/workflow tree.

### Next reconciliation

1. Wait for W2/W3/W4 and full Python/browser + Studio gates on `b161667...`; inspect any failure logs and repair only the owning lane.
2. Verify the new W3 Asset Library test count in the W3 job log, not only the workflow's green summary.
3. Keep the Windows-native process-lock result separate from the POSIX W3 result; both platform branches must pass.
4. After this exact-SHA reconciliation, audit the next concrete product-contract gap. Do not create speculative work or broaden the task into a wholesale React rewrite without an explicit, testable gap in the source plan.


## 39. W3 command-line continuation failure diagnosed and corrected — 2026-11-10

- The first W3 run after adding `tests/test_asset_catalog.py` on SHA `b1616671789dd7944c030625e9afd068248c3276` ran the pre-existing W3 tests (**251 passed, 1 skipped**) but then attempted to execute `tests/test_asset_catalog.py` as a shell command and failed with exit code 126 / `Permission denied`.
- Root cause: the preceding final pytest path in the YAML multiline shell command lacked a trailing continuation backslash. The new test file was therefore outside the pytest invocation. This was a workflow command-construction defect, not an Asset Library test failure.
- Corrected `.github/workflows/parallel-workstream-ci.yml` so the Asset Catalog path is part of the same `python -m pytest` invocation. No test assertion, timeout, skip, or acceptance threshold was changed.
- Corrected workflow commit: `d1e93ed86a8750b2c5b6b6cadbd40db04518a5b7`.
- Workflow YAML validation for this SHA: run `42808558317` **PASS**.
- New exact-SHA hosted verification is running:
  - Parallel W1–W3/W7: run `42808558365`.
  - Product IA W4/W5: run `42808558337`.
  - Full Python/browser + Studio release gate: run `42808558382`.
  - Windows native lock + MoveFileExW: run `42808558319`.
- The Windows-native test on preceding SHA `b161667` already directly executed both test targets and passed **2 tests in 1.40s**; the latest `d1e93ed` run must still be reconciled before recording final exact-SHA platform acceptance.


## 40. W3 Asset Library acceptance and Windows lock branch verified — 2026-11-10

- Corrected exact-SHA `d1e93ed86a8750b2c5b6b6cadbd40db04518a5b7` W3 job `129803982546` in run `42808558365`: **287 passed, 1 skipped, 1 warning**. Its log confirms `tests/test_asset_catalog.py` was included in the same pytest invocation; this covers the POSIX `fcntl.flock` process-lock branch plus catalog deduplication, semantic updates, reference-safe deletion and fail-closed catalog handling.
- Exact-SHA Windows-native job `129803866735`, run `42808558319`: **2 passed in 1.94s**. The log explicitly shows both `test_windows_write_through_replace_and_delete_round_trip` and `test_catalog_file_lock_serializes_independent_processes` were executed. This directly exercises the Windows `msvcrt.locking` branch and `MoveFileExW` write-through replacement.
- W1 job `129803982431`: PASS. W7 job `129803982323`: PASS. W5 job `129803986878`: PASS.
- At this update W2 job `129803982490`, W4 job `129803986421`, Studio release gate job `129804003079`, and full Python/browser job `129804004016` were still running. No final integrated PASS is claimed until those jobs complete.


## 41. W2 exact-SHA regression failure and same-SHA rerun — 2026-11-10

- W2 job `129803982490` in run `42808558365` on code/workflow SHA `d1e93ed86a8750b2c5b6b6cadbd40db04518a5b7` completed **354 passed, 1 failed**.
- Sole failure: `tests/test_film_studio_external_watch_browser.py::test_external_agent_write_hot_reloads_and_recovers_after_broken_document`. The failure is specifically the real React trim-start gesture: after pointerup, the test expected `clip-a.attrs.at` to advance by more than 0.1s, but the persisted/API-observed value remained `0.0`. It is not an audio-volume assertion.
- The test's transient preview geometry changed and the FilmDocument/API/Store revisions agreed, but the final persisted trim start did not satisfy the expected movement. Existing assertions were not relaxed.
- Triggered a rerun of only the failed W2 job at the same exact SHA. Rerun job ID: `129806508387`; status was in progress at this ledger update.
- W1, W3, W7 and W4/W5 are independently green on this SHA. Studio release gate is green. Full Python/browser regression is still running. Do not call integrated acceptance PASS until the W2 rerun and full regression are reconciled.


## 42. W2 same-SHA rerun passed; W4/W5 and Studio gate reconciled — 2026-11-10

- W2 initial job `129803982490` had one transient failure in the external-watch trim-start browser test; see section 41. Re-running the same W2 job on the same code/workflow SHA produced job `129806508387`: **355 passed, 1 warning in 99.59s**. No code, test, assertion, timeout, or workflow change was made between the failed attempt and this rerun.
- This is recorded as an intermittent first-attempt failure followed by a clean exact-SHA rerun, not as proof that the original failure never happened. Keep the test and its >0.1s persisted trim assertion unchanged. The full regression below is still required as the independent integrated check.
- W4 job `129803986421`: PASS. W5 job `129803986878`: PASS, run `42808558337`.
- Studio release gate job `129804003079`: **346 passed, 2 warnings**, PASS, run `42808558382`.
- Full Python/browser job `129804004016` in run `42808558382` remained in progress at this update. Final overall acceptance is pending that result.


## 43. Exact-SHA W1–W7, Windows, Studio and full regression reconciled — 2026-11-10

### Tested code/workflow SHA

`d1e93ed86a8750b2c5b6b6cadbd40db04518a5b7`.

The current `main` HEAD at ledger update is `52c8d8bcd2c265388e1d020d46bb0accc3756289`. GitHub compare confirms the four commits after the tested SHA changed **only** `docs/execution-ledger.md`; no executable code or workflow changed after testing.

### Independent parallel lanes

- W1 generation durability: **89 passed**, job `129806510077`.
- W2 timeline transactions: same-SHA rerun **355 passed, 1 warning**, job `129806508387`. The first attempt had one external-watch trim-start failure; the rerun passed without code or test changes. The full suite below also passed. Preserve this as an intermittent first-attempt observation; do not weaken the assertion.
- W3 lifecycle/export/QC + Asset Library: **287 passed, 1 skipped, 1 warning**, job `129806511566`. The corrected command executed `tests/test_asset_catalog.py` inside pytest.
- W7 shared shell navigation: **2 passed**, job `129806550703`.
- Product IA W4 project home/create: **35 passed**, job `129803986421`.
- Product IA W5 settings/credential safety: **13 passed**, job `129803986878`.

Runs:
- Parallel workstreams: [42808558365](https://github.com/just2512/video-director/actions/runs/42808558365) — **SUCCESS**.
- Product IA workstreams: [42808558337](https://github.com/just2512/video-director/actions/runs/42808558337) — **SUCCESS**.

### Integrated release gates and native platform

- Studio release gate job `129804003079`: **38 React integration tests passed** and **346 timeline/lifecycle/render/QC tests passed, 2 warnings**.
- Full Python/browser job `129804004016`: **1,951 passed, 2 skipped, 0 failures, 0 errors, 8 warnings**; JUnit summary: 1,953 tests, 0 failures, 0 errors, 2 skipped.
- Windows-native job `129803866735`: **2 passed in 1.94s**; both the `MoveFileExW` write-through replace/delete test and the actual Windows `msvcrt.locking` cross-process Asset Library lock test executed.
- Workflow syntax: run `42808558317` — PASS.
- Full hosted regression: [42808558382](https://github.com/just2512/video-director/actions/runs/42808558382) — **SUCCESS**.
- Windows native: [42808558319](https://github.com/just2512/video-director/actions/runs/42808558319) — **SUCCESS**.

### Final state

- W1–W7 independent ownership jobs are green on the tested tree; W4/W5 are separate Product IA jobs and W7 remains the sole owner of shared `index.html` / `app.js`.
- POSIX and Windows Asset Library locking now each have native implementation evidence; they are not inferred from one another.
- 19D-10 NVIDIA live-model acceptance remains PASS from manual dispatch run `42802192086`; this current run used the normal no-provider-credential full regression and did not repeat paid live calls.
- Issue #121 is closed with explicit acceptance evidence. There are currently no open GitHub issues.
- The current exact-SHA CI acceptance set is PASS. Existing two skips and eight warnings remain visible; none were suppressed or converted to PASS.


## 44. Next contract work identified: W4 modal focus and W7 shell keyboard semantics — 2026-11-10

After the current exact-SHA W1–W7 and release gates passed, a focused review of `CONTRACT.md` §§39–40 and the current browser tests found a concrete accessibility coverage gap:

- The Create Project dialog already has `role=dialog`, `aria-modal=true`, an accessible label, and an initial textarea focus. Its explicit-close/save-draft semantics are intentional. However, it has no Tab/Shift+Tab focus trap and does not restore focus to the invoking control after it is hidden or finalized. W4 owns this behavior.
- The top-level Project/Workspace/Settings controls are native buttons and can receive keyboard focus, but `switchTab()` only toggles CSS classes and pane display. It does not expose the active route with `aria-current` or mark inactive panes `aria-hidden`. W7 owns this shared-shell behavior.
- Existing W4/W7 browser tests cover mouse navigation, project creation/draft recovery, settings round trips, and integrated shell flow, but do not verify these keyboard/focus contracts with real Chromium keyboard input.
- These are bounded fixes to existing W4/W7 ownership, not a new UI framework or a change to route behavior. Do not change the Create Project backdrop/Escape behavior: both intentionally preserve the draft and require explicit “收起”.
- Acceptance: W4 verifies initial focus, Tab/Shift+Tab wrap, no keyboard escape behind the modal, and focus restoration after close/finalization; W7 verifies keyboard activation routes, active `aria-current`, inactive `aria-hidden`, and focus-visible state. Run independent W4/W7 jobs plus full hosted regression and Studio gate.


## 45. W6 GitHub Actions runtime maintenance — 2026-11-10

### Reason for change

- The exact-SHA run on `c4c9c62237787ba968627c557a3152e309bda3f8` passed, but its hosted logs emitted GitHub's warning that multiple JavaScript actions still targeted Node.js 20 and were being forced onto Node.js 24.
- This is maintenance debt rather than a product regression. The workflow action references were upgraded to the current compatible major releases: `actions/checkout@v7`, `actions/setup-python@v7`, `actions/setup-node@v7`, and `actions/upload-artifact@v7`.
- Updated all seven workflow files and the Studio build contract assertion that checks artifact retention. No application code, test assertion strength, timeout, skip policy, runner class, or provider behavior was changed.

### Exact revision and hosted verification

- Code/workflow commit: `9d7208cc9818839ea2c9cc5965298e0998c28b68`.
- Workflow syntax run `42821701924`: **PASS**.
- Windows-native run `42821701904`: **PASS**, job `129848571129`, 2 tests passed.
- Focused storage/external-edit run `42821701901`: **PASS**.
- Independent parallel lanes run `42821701905`: W1 `129848571048` PASS (89 tests), W3 `129848571925` PASS, W7 `129848571350` PASS; W2 `129848571236` was still running at this ledger entry.
- Product IA run `42821702016`: W4 `129848571674` PASS, W5 `129848571339` PASS.
- Full hosted regression run `42821701972`: full Python/browser and Studio release-gate jobs were still running at this ledger entry.
- Initial logs from the new checkout/setup actions show the Node.js 20 forced-runtime warning is no longer emitted. Final full-regression and Studio logs must still be inspected for any remaining action-runtime warnings.
- Do not call this exact-SHA integrated acceptance complete until W2, full Python/browser, and Studio release gate finish successfully. Do not rerun the paid NVIDIA live test; 19D-10 remains PASS from its separately authorized manual dispatch.

### Follow-up

- Once all pending jobs finish, record their exact test counts and inspect final logs for remaining runtime warnings.
- If the action upgrade causes a failure, diagnose the incompatible action/input or runner requirement and fix it without weakening any product gate.


## 46. Contract-level runner-policy contradiction identified — 2026-11-10 (resolved in §48)

- Read the current root `CONTRACT.md` in addition to the two required execution documents. Its §0 and §0.1 still prescribe a `[self-hosted, linux, x64]` runner for Studio build/test/publication and refer to the obsolete `.github/workflows/ai-director.yml`.
- This directly contradicts `docs/parallel-workstreams.md` Runner policy, which requires GitHub-hosted `ubuntu-24.04` / `windows-latest` only, and the current executable workflow contract tests in `tests/test_studio_build_contract.py` (which explicitly reject self-hosted runners).
- Current workflows and latest exact-SHA runs use GitHub-hosted runners. No workflow was changed to self-hosted and no local/WSL runner was used.
- **Resolved:** the user explicitly authorized the Contract Change. §48 records the controlled update to `CONTRACT.md` §§0–0.2, matching the existing GitHub-hosted workflows and test gates. The authorization removes the former decision block; it does not authorize restoring self-hosted/WSL execution or weakening CI.


## 47. Exact-SHA accessibility, CI runtime and integrated acceptance reconciliation — 2026-11-10

### Source and tested tree

- Audit starting `main` SHA: `99142d0887fa771bea7c3bd3e5bafd27ee435a1b`.
- Exact executable/workflow SHA: `9d7208cc9818839ea2c9cc5965298e0998c28b68`.
- GitHub compare shows the two commits from the tested SHA to the audit starting main changed only `docs/execution-ledger.md` and `docs/parallel-workstreams.md`. The code/workflow tree is therefore identical. This update changes only those documentation files.

### Exact-SHA hosted results

- W1 generation durability: **89 passed**, job `129848571048`.
- W2 React timeline transactions, revision invalidation and AI proposal contracts: **355 passed, 1 warning**, job `129848571236`.
- W3 lifecycle, render/QC and Asset Library: **287 passed, 1 skipped, 1 warning**, job `129848571925`.
- W4 project home/create lifecycle: **36 passed**, job `129848571674`.
- W5 settings IA and credential safety: **13 passed**, job `129848571339`.
- W7 shared-shell navigation: **2 passed**, job `129848571350`.
- Parallel W1/W2/W3/W7 run: `42821701905` **SUCCESS**.
- Product IA W4/W5 run: `42821702016` **SUCCESS**.
- Full Python/browser regression: **1,952 passed, 2 skipped, 0 failures, 0 errors**; JUnit summary 1,954 tests, run `42821701972`.
- Studio React build/timeline/lifecycle/render/QC gate: **346 passed, 2 warnings**, same run `42821701972`.
- Windows-native publication and process-lock branch: **2 passed**, run `42821701904`.
- Workflow YAML validation: **SUCCESS**, run `42821701924`.
- Focused storage/external-edit regression: **SUCCESS**, run `42821701901`.
- The NVIDIA job was **SKIPPED** on the ordinary push, as required. 19D-10 remains PASS from the separately authorized manual-dispatch run `42802192086`; do not confuse LLM streaming acceptance with video-generation provider acceptance.

### W4/W7 accessibility closure

- W4's real Chromium browser tests now verify initial dialog focus, Tab/Shift+Tab containment, no keyboard escape behind the modal, focus restoration after close/finalization, and stable invoker restoration when reopening a saved draft. The existing explicit-close/save-draft behavior remains unchanged; Escape/backdrop are not dismiss actions.
- W7's real Chromium tests verify keyboard activation of Project/Workspace/Settings, active `aria-current="page"`, inactive-pane `aria-hidden`, one visible pane, stable in-page URL and global create action.
- W4 and W7 remain separate owners and separate CI jobs; W7 alone owns `index.html` / `app.js`. No router migration or second state machine was introduced.

### Diagnostics retained

- Full pytest reports 8 warnings; Studio release gate reports 2 warnings. No warning was converted into a skip or suppressed.
- The warning set includes invalid Python escape sequences in two browser-test JS snippets (`\\d` and `\\/`), Pillow's deprecated `Image.getdata()` use in an image assertion, and Pydantic serializer warnings where `tests/test_adversarial_fixes.py` appends raw dictionaries to typed `CompositionIR` collections. The Pydantic warnings require a small audit to distinguish fixture-only construction from a production validation gap before choosing a fix.
- The action upgrade to `actions/*@v7` has no Node.js 20 forced-runtime warning in the inspected exact-SHA logs.
- No assertions, timeout thresholds, skip policies or business acceptance gates were relaxed.

### Next execution and stop boundary

1. W0 should audit the Pydantic serializer warnings first; preserve the adversarial editor behavior assertions and fix the typed-model construction or production validation only after the source of the warning is established.
2. Clean the two invalid-escape warnings and the Pillow deprecation as narrow test-maintenance changes, then rerun the owning lanes and exact-SHA integrated hosted gates for the resulting code SHA.
3. The root `CONTRACT.md` runner-policy contradiction remains BLOCKED under the contract's own change-control rule. It must not be silently rewritten or bypassed; all unrelated W1–W7 work may continue.
4. No new paid NVIDIA call is required; the live LLM acceptance is already PASS under §37.

## 48. GitHub-hosted Runner 契约统一与 warning 根因修复 — 2026-11-10

### 已授权 Contract Change

- 用户明确授权修改根目录 `CONTRACT.md`。本次修改 §0 栈适配表、§0.1 Studio runner 条款，并新增 §0.2，统一规定仓库 CI 只能使用 GitHub-hosted Actions runner：Linux `ubuntu-24.04`，Windows 原生测试 `windows-latest`。
- 明确禁止 self-hosted、WSL、开发者工作区和本机模型配置进入 CI；普通 push 回归与 NVIDIA 真实 provider 调用继续隔离，后者只允许独立 hosted job + 显式 workflow_dispatch opt-in + GitHub Actions Secret。
- 与 `docs/parallel-workstreams.md` Runner policy、现行 workflows 和测试硬闸门一致；不更改 runner class、不放宽验收，也不恢复旧 `ai-director.yml`。

### ca2da6a 的 exact-SHA CI 结果

- Tested code/workflow SHA: `ca2da6ae2eebf71956b5cdd0095fe76d177182d5`。
- W1: 89 passed；W2: 355 passed；W3: 287 passed, 1 skipped；W7: 2 passed。
- W4: 36 passed；W5: 13 passed。
- Full pytest: 1,952 passed, 2 skipped, 0 failures/errors；Studio release gate: 346 passed；Windows native publication: 2 passed；workflow syntax: success。
- W2、full pytest、Studio gate 的无效转义和 Pillow deprecation warnings 已消失。Full pytest 仍有 1 条 Pydantic serializer warning，根因已定位为 `tests/test_adversarial_fixes.py::TestP0RippleDelete::test_visual_plain_delete_leaves_gap` 在测试夹具中向类型化 `CompositionIR.assets/visual_overlays` 追加原始 dict；不属于生产数据路径。此提交将夹具改为 `AssetRef.model_validate` / `VisualOverlayClip.model_validate`，保留原行为断言。
- NVIDIA job 正常 push 时 SKIPPED；既有手动授权真实 LLM token-stream 验收仍单独记录在 §37，不代表视频生成 provider 验收。

### 当前变更及待验证

- 本节记录的 Runner Contract Change、对应测试断言和 warning fixture 修复作为一个 main commit 集成；必须等待新 SHA 的完整 hosted CI 结果，不能沿用 `ca2da6a` 作为新树验收证据。
- Contract 与测试通过后，下一阶段按 19D 继续审计 FilmDocument revision/operation history、项目切换与外部文件变更交错、生成任务与资产目录一致性、revision-pinned MP4/QC 失败恢复。每项必须先从源码和既有测试证明具体缺口，再新增行为验收；不得以增加测试数量代替交付。


## 49. CompositionIR variant fallback 边界复核 — 2026-11-10

- 对照 `video_director/film_document.py::build_film_from_composition` 与 `tests/test_film_variant_migration_api.py`，确认迁移行为：无 variants 时使用顶层 scenes/audio；单一 variant 可自动选用；多个 variants 未指定时返回 `ACTIVE_VARIANT_REQUIRED`；显式指定不存在的 ID 返回 `ACTIVE_VARIANT_NOT_FOUND`。后两种情况不得静默回退到顶层内容。
- 发现 `docs/composition-ir-migration-field-audit.md` 的顶层与递归重复条目把“无有效 ID 时回退到顶层”写得过于宽泛，可能误导多个候选版本的迁移决策。已修正文案，明确分支条件；这是文档纠错，不改变代码行为。
- 当前 HTTP 回归已验证多 variants 缺少选择时返回 `ACTIVE_VARIANT_REQUIRED`，并验证显式选择的版本只迁移所选 variant。下一步补充显式未知 variant ID 返回 `ACTIVE_VARIANT_NOT_FOUND` 且不创建 `film.html` 的 HTTP 级回归；在该断言进入精确 SHA hosted CI 前，不把未知 ID 的 HTTP 端到端拒绝路径标为已验收。


## 50. 19D-11 bounded scene capability matrix reconciled — 2026-11-10

- The bounded HTML-scene capability inventory is already present in `docs/film-scene-capability-matrix.md`; it explicitly separates supported `window.film.frame(t)` scenes, local resource handling, external-network rejection, unsupported arbitrary HTML/WebGL, uncontrolled asynchronous state and cross-device pixel identity claims.
- The W3 lane on exact code SHA `cd99d98dd0eb6649592a7086c6cc87f55e2984de`, job `129876675459` in run `42829833193`, completed **287 passed, 1 skipped**. Its regression set includes scene runtime, resource readiness, render/look revision pinning, missing-resource rejection and clip-ID-specific frame failure tests.
- This closes the documentation/inventory task; it does not broaden renderer support. Arbitrary HTML/WebGL determinism and exhaustive edit-operation permutations remain explicitly outside the contract.
- The next narrow W3 acceptance item is the explicit unknown `selected_variant_id` HTTP rejection test recorded in §49. No new renderer feature is justified until that boundary and the current integrated release rerun are closed.


## 51. Variant migration fail-closed HTTP acceptance and Studio reload harness repair — 2026-11-10

### Code changes

- Executable/workflow SHA: `36f83ed0ac0e8ac0eec677866556f7b8248b79b5` (based on `805db7b` plus the browser harness correction).
- `tests/test_film_variant_migration_api.py` now sends an explicit unknown `selected_variant_id` through the real HTTP preview route and asserts `ACTIVE_VARIANT_NOT_FOUND`, `migration_available=false`, and that `film.html` remains absent. The route must not fall back to top-level scenes/audio.
- W3's scoped hosted command now includes `tests/test_film_variant_migration_api.py`; the new HTTP contract is not left to the full-suite-only path.
- The Studio canvas history browser test now re-enters the normal project selection/change lifecycle after a full page reload. The existing project selector's change handler invokes `editorLoad`, which auto-opens Studio for a project with authoritative `film.html`; the test waits for that authority and auto-open instead of toggling the already-open panel closed.

### Exact-SHA hosted evidence

- W1 generation durability: **89 passed**.
- W2 React timeline transactions: **355 passed**.
- W3 lifecycle/export/QC: **289 passed, 1 skipped**; includes the explicit unknown-variant HTTP rejection test.
- W4 project home/create: **36 passed**.
- W5 settings/credential safety: **13 passed**.
- W7 shared-shell navigation: **2 passed**.
- Windows-native publication: **2 passed**.
- Workflow YAML validation: **PASS**.
- Studio React integration smoke: **38 passed**.
- Studio timeline/revision/project-switch/lifecycle/render/QC gate: **346 passed**.
- Full Python/browser regression run `42835448250`, job `129896459496`: **1,952 passed, 2 skipped, 0 failures, 0 errors** (1,954 collected; 704.39 s); job conclusion SUCCESS. The JUnit summary reports 1,954 tests, 0 failures, 0 errors and 2 skips. This closes the full Python/browser gate on exact SHA `36f83ed0ac0e8ac0eec677866556f7b8248b79b5`.
- NVIDIA live provider job remains skipped on ordinary push. Do not repeat the paid live call; 19D-10 remains PASS from the separately authorized run recorded in §37.

### Next source-audit item

- A read-only audit found a separate W3 boundary not covered by the current FilmDocument revision tests: the render media server reads project-local scene files from their live paths, while final publication rechecks only the `film.html` revision. The scene watcher emits a separate `scene_revision`, but `film_render.py` does not bind that revision to the MP4/QC transaction. An external edit to an HTML scene or its local resources during a multi-frame render could therefore leave a render composed from different resource states while the FilmDocument revision remains unchanged.
- This is a candidate W3 correctness gap, not yet a confirmed failing regression. Next step: add a deterministic render test that changes a project-local scene resource during export and asserts that stale/mixed output is not published; then choose between immutable render-input snapshotting and a fail-closed scene-resource revision check. Include resource scope (HTML/CSS/images/media), symlink boundary, and the cost of hashing large files in the design. Do not claim this is fixed until the test passes on an exact hosted SHA.


### Exact-SHA integrated regression completion

- Full hosted run `42835448250` on code SHA `36f83ed0ac0e8ac0eec677866556f7b8248b79b5`: full Python/browser job `129896459496` SUCCESS — 1,952 passed, 2 skipped, 0 failures/errors (1,954 collected); Studio release gate job `129896458848` SUCCESS — 346 passed. The W3 scoped job on this SHA completed 289 passed, 1 skipped; W1 89, W2 355, W4 36, W5 13, W7 2, Windows-native 2 and YAML validation PASS as separately recorded in the current lane evidence. NVIDIA live job `129908516701` was SKIPPED by default; no provider call occurred.
- The latest `main` documentation commit `8e902808f82cc828e36fed7dfa1cb4b23761ceb8` does not change the tested executable/workflow tree. Do not claim it is a new application-code test SHA.

### Next W3 export-consistency task — OPEN

- The render server currently serves project-local scene HTML/CSS/media from live project paths. The final publication transaction rechecks `film.html` revision only; it does not bind the scene-resource revision to the MP4/QC transaction. A scene resource can therefore change during multi-frame export without changing the FilmDocument revision.
- Add a deterministic regression that mutates a scene HTML/CSS/image/media resource during export and proves that a mixed/stale MP4 and QC report are not published. Inspect the existing scene watcher revision signal and its event-loss/same-size-same-mtime behavior before choosing immutable resource snapshotting or a fail-closed resource revision check. Cover project-root/symlink boundaries and quantify the streaming-hash cost for large resources.
- This is a confirmed source-level coverage gap but not yet a demonstrated failing export. Keep the implementation state UNKNOWN until a reproducible test fails before the fix and passes after it on an exact GitHub-hosted SHA. Do not weaken the existing render/QC or publication rollback assertions.


## 52. Scene watcher metadata-preserving write detection — 2026-11-10

### Confirmed source gap and correction

- video_director/film_watch.py previously tracked scenes/ files using only (device, inode, size, mtime_ns). Unlike film.html, scene files had no periodic content audit. An in-place rewrite that restored size and mtime could therefore be missed by the separate scene_revision event path.
- Code commit 21ebeb8020bfa6cb691c4a3a1fffa7a42e368330 adds a separate 10-second scene-content audit interval and streams SHA-256 over the scene tree. It verifies stable file identity before/after each read, does not follow symlink roots or nested symlink directories, and derives scene_revision from sorted path/content hashes rather than stat metadata alone.
- Test commit bcb333088ca9a3dc4e072cd602b0882b14c12c8a adds an in-place rewrite regression that preserves device, inode, size and mtime_ns but requires a scene_changed event with a content-based revision.
- The audit is streaming (bounded memory, 1 MiB chunks) but its I/O cost is O(total bytes under scenes/) per content audit. The default audit interval is 10 seconds. This is an explicit tradeoff for the rare metadata-preserving case; ordinary metadata changes still use the fast stat/debounce path.

### Exact-SHA evidence in progress

- Tested code SHA: bcb333088ca9a3dc4e072cd602b0882b14c12c8a.
- Focused storage/watch regression run 42841732836, job 129919098828: 165 passed, 1 skipped.
- W2 timeline transactions run 42841732724, job 129918482698: 356 passed.
- W1 job 129918482843, W7 job 129918482858, W5 job 129918473957, Windows-native job 129918492207 and workflow YAML job 129918448440 have completed successfully.
- W3 job 129918482338, W4 job 129918474492, full Python/browser job 129918490494 and Studio release-gate job 129918491267 were still running at this update. Do not mark integrated exact-SHA acceptance complete until all required jobs finish and their logs are inspected.

### Remaining W3 export-consistency work — OPEN

- The watcher correction covers files beneath scenes/, but it does not by itself bind the resource bytes used by Chromium to the MP4/QC publication transaction. The renderer still serves live project paths and final publication still checks only film.html revision.
- The next task remains immutable snapshotting of the HTML scene dependency closure (HTML, CSS url(), images, fonts and media), serving those pinned bytes during frame capture, and rejecting publication if any source dependency changed before commit. The dependency closure must include referenced project assets outside scenes/; hashing the entire project asset library indiscriminately is not acceptable.
- Preserve the existing offline-network, symlink/path boundary, deterministic-frame, FilmDocument revision, QC and rollback contracts. Use streaming copy/hash for large media; measure bytes and elapsed time in the regression rather than inventing a cost estimate.
- Until that end-to-end resource snapshot regression passes on an exact hosted SHA, W3 export consistency remains OPEN / NOT ACCEPTED.


## 53. Short-lived multi-branch execution restored; exact-SHA Studio failure isolated — 2026-11-10

### Branch policy correction

- Historical accepted execution design is restored as short-lived task branches plus one `main` integration target. This supersedes the earlier wording that prohibited all work branches; `main` remains the only release/integration branch and PR/manual-review handoff remains disabled.
- Frozen branch base: `e27a53ad75f3ce6df7c76287865189667a1182a5` (main at branch creation).
- Created independent branches:
  - `work/w2-roll-trim-studio-regression` — W2 Roll Trim commit/Undo/Redo history behavior.
  - `work/w3-render-resource-snapshot` — W3 immutable HTML-scene dependency snapshot and MP4/QC publication consistency.
  - `work/w6-parallel-branch-policy` — W6 branch policy and ledger synchronization.
- W2 and W3 own disjoint production/test files and may proceed concurrently. W6 is docs/workflow-only. Each branch must run scoped hosted CI against its own SHA; branch PASS is not integrated PASS. Completed branches are integrated directly and serially into main, then affected lanes and integrated gates rerun on one exact main SHA.

### Latest hosted evidence on code SHA bcb333088ca9a3dc4e072cd602b0882b14c12c8a

- Full Python/browser job `129918490494`, run `42841732897`: **1,953 passed, 2 skipped, 0 failures, 0 errors**.
- Studio release gate job `129918491267`, same run: **346 passed, 1 failed**. Failure: `tests/test_film_studio_roll_browser.py::test_film_studio_ctrl_drag_roll_trim_updates_persisted_timeline`; Playwright timed out waiting for the `POST /api/editor/film/undo` response at the Undo history assertion. Roll source-boundary persistence assertions had already completed. This remains W2 FAIL / OPEN until the missing Undo request is explained and the real browser regression passes; do not merely increase timeout or weaken the assertion.
- Independent lanes on the same code SHA: W1, W2, W3, W4, W5, W7, focused storage/watch, Windows-native and YAML all completed successfully. These do not override the integrated Studio failure.
- Explicit NVIDIA live token-stream job was correctly skipped. Existing real-provider acceptance is not repeated.

### Remaining execution order

1. W2: diagnose why Undo is not dispatched after the roll transaction despite persisted roll state; inspect history stack/store capability and browser trace. Fix product behavior or the precise test race based on evidence, then rerun the dedicated roll browser test and Studio gate on the branch SHA.
2. W3: implement immutable dependency closure snapshotting for HTML/CSS/images/fonts/media including referenced project assets outside scenes/, serve only snapshot bytes, verify source hashes before publication, and atomically reject MP4/QC when dependencies changed. Add deterministic mutation-during-render tests, symlink/path-boundary and cleanup coverage, and measured copy bytes/time.
3. W6: ensure branch-scoped GitHub-hosted CI and per-branch concurrency preserve independent branch runs; keep all runners hosted-only.
4. Integrate W2 and W3 directly to main in dependency order; rerun all affected lanes, Studio gate, full Python/browser, Windows-native and YAML against the exact resulting main SHA.
5. Keep W3 resource snapshot and W2 integrated Studio acceptance OPEN until their explicit gates pass.


## 54. W2/W3 short-lived branch acceptance and integration readiness — 2026-11-10

### Exact branch evidence

- W2 branch `work/w2-roll-trim-studio-regression`, code/test SHA `055e9f4552965d98f03384129631b45e67b5318f`:
  - W2 timeline transaction suite: **356 passed** (job `129944866978`, run `42849197117`).
  - W1, W3 and W7 branch-run jobs also completed successfully.
  - The Studio Roll Trim regression now waits for the FilmStore transaction to become idle at the exact persisted revision before calling Undo. The external-watch browser regression applies the same synchronization. This addresses the test's race between disk visibility and completion of the browser request/finally transition; it does not weaken the undo assertion or extend the response timeout.
- W3 branch `work/w3-render-resource-snapshot`, code/test SHA `9491219aabcf55ce66f55319cbe31581fb54f1c8`:
  - W3 lifecycle/export/QC suite: **292 passed, 1 skipped** (job `129949494612`, run `42850477661`).
  - Focused storage/watch suite: **165 passed, 1 skipped** (job `129949933054`, run `42850477609`).
  - W1 and W7 branch jobs completed successfully. W2 is not a W3 acceptance gate and remains owned by the W2 branch.
- W6 branch `work/w6-parallel-branch-policy`, workflow SHA `262aba62477e6a89f47e47129d33b2a7e9170ebf`: workflow syntax validation **SUCCESS** (run `42850326617`). W1–W7 and W4/W5 jobs were intentionally skipped by owner-lane filters on a W6-only branch.
- Main baseline SHA `805067a29f821d1daa3d0cb49abd555a5c613e6c`, before W2/W3 code integration:
  - Full Python/browser: **1,953 passed, 2 skipped, 0 failures, 0 errors** (job `129942383729`, run `42848498712`).
  - Studio release gate: **347 passed** (job `129942385133`, same run).
  - W1/W2/W3/W7, W4/W5, focused storage/watch, Windows-native and workflow syntax gates all succeeded on this SHA.
- Branch PASS is scoped evidence only. The W2/W3 changes remain **NOT INTEGRATED** until merged into main and the resulting exact main SHA passes the integrated gates.

### W3 resource snapshot implementation

- Added `video_director/film_resource_snapshot.py`: recursive, streaming SHA-256 snapshot of clip media plus referenced HTML/CSS dependencies; includes linked stylesheets, CSS `url()`, CSS `@import`, inline style attributes, images, fonts and nested scene HTML.
- Export Chromium media requests and FFmpeg source probes/audio reads now use pinned snapshot files. Unknown/unpinned project-local media requests fail closed.
- Before MP4/QC publication, original source dependencies are re-hashed under the project lock. Any removal/replacement/content change rejects both artifacts. This catches same-size/same-mtime rewrites.
- QC records dependency count, copied bytes, copy elapsed time and per-resource SHA-256. Snapshot construction and source-range preflight are inside the render workdir cleanup scope.
- Added regression coverage for recursive dependency discovery, immutable HTTP serving, unpinned 404, same-size/mtime-preserving mutation rejection, no MP4/QC publication, failed-preflight workdir cleanup, inline CSS and CSS imports.
- **W3 look follow-up:** implemented on `work/w3-look-resource-snapshot` at SHA `6dc2db89d0055e3c29e8b3e9ae3d16d81ea4fba5`; W3 branch suite passed 295 tests with 1 skip and focused storage/watch passed 165 with 1 skip. It is ready for direct main integration; final integrated main gates remain required.


## 55. Preview look uses the same immutable resource revision — 2026-11-10

- Branch: `work/w3-look-resource-snapshot`.
- Implementation SHA: `6dc2db89d0055e3c29e8b3e9ae3d16d81ea4fba5`.
- W3 lane: **295 passed, 1 skipped** (job `129958375331`, run `42852983184`).
- Focused storage/watch: **165 passed, 1 skipped** (job `129958376487`, run `42852983722`).
- Workflow YAML validation: SUCCESS (run `42852983011`).
- `look_project_film` now creates the same immutable transitive dependency snapshot as MP4 export, serves pinned bytes to Chromium, uses pinned source media for FPS/range validation, re-hashes dependencies under the project lock before publishing the contact sheet, and includes snapshot hashes/bytes/copy-time in the result.
- Temporary look snapshots and contact-sheet temp files are cleaned on success and failure; contact-sheet temp names are unique to concurrent look attempts.
- Added a browser regression that changes a CSS-referenced SVG during reverse-order frame sampling while preserving size and mtime; the preview must reject publication with `RESOURCE_REVISION_CONFLICT`.
- Updated the source-FPS contract test to assert the snapshot-aware resolver signature and included that test in the W3 lane.
- The previous integrated main SHA `651262424d1a404ec1b1d930097686540f0a6ede` had all W1–W7/product/focused/Windows/YAML lanes green, but its Full Python/browser gate reported **1,955 passed, 2 skipped, 1 failed**, and Studio release gate reported **349 passed, 1 failed**. Both failures were the same stale source-FPS contract assertion expecting the pre-snapshot call signature; no runtime frame-wait assertion failed. This branch updates that contract.
- Status: **INTEGRATED AND ACCEPTED on main SHA `5627243bf9cb0847cf78c9093a90732351a18aaf`**. Exact-SHA integrated gates: Full Python/browser 1,959 passed, 2 skipped, 0 failures/errors; Studio release gate 351 passed; W1 89, W2 356, W3 295 passed/1 skipped, W4 36, W5 13, W7 2, focused storage/watch 165 passed/1 skipped, Windows-native 2, YAML PASS. NVIDIA live acceptance was skipped by default.
- Remaining non-blocking engineering task after acceptance: measure snapshot copy time and disk overhead against representative large media assets; current CI fixtures validate streaming and report byte/time metrics but are not a production-scale benchmark.


## 56. W1/W3 parallel audit branches and current exact-SHA acceptance — 2026-11-11

### Frozen baseline and ownership

- Frozen main baseline: `5627243bf9cb0847cf78c9093a90732351a18aaf` (all integrated gates green; see §55).
- `work/w1-generation-recovery-race-audit` owns only `tests/test_generation_recovery.py`: independent OS-process read/modify/write test for generation_jobs.json. It starts eight processes together and requires all eight distinct job updates to survive.
- `work/w3-resource-snapshot-scale-benchmark` owns only `tests/test_film_render_scene_e2e.py`: 64 MiB synthetic media snapshot, verifies copied bytes and SHA-256, re-validates source, records copy and validation time, and asserts Python heap peak remains below 8 MiB. This is a Python-heap bound, not an RSS claim.
- `work/w6-active-branch-register` owns documentation only: synchronizes this ledger and the active branch register in `parallel-workstreams.md`.
- W1 and W3 are running independently from the same frozen main SHA with disjoint file ownership. W6 does not edit either code/test path.

### W1 scoped evidence

- Branch code/test SHA: `2f5181ceb15c1b27b5b28670a938ba573e7cb666`.
- W1 generation durability run `42918520098`, job `130187104548`: **90 passed**.
- Workflow YAML run `42918519951`: SUCCESS.
- Status: scoped PASS and test-only change directly integrated to main by merge commit `b5dd5900acd0d3ac290d24f24611685ace36d1b2`. The resulting exact-main CI run `42919019951` (full/Studio), `42919019942` (W1–W3/W7), `42919019898` (focused storage), `42919019992` (W4/W5), and `42919019914` (Windows) was still running at this ledger update.

### W3 scoped evidence

- Initial branch code/test SHA: `da44aadf77335f176036d86e49772c6a46d5e726`; W3 lane `42918465772`, job `130186897979`: **296 passed, 1 skipped**. Focused storage/watch `42918465717`, job `130186897961`: **165 passed, 1 skipped**.
- The 64 MiB bounded-memory benchmark and GitHub job-summary metric publication passed on code/test SHA `4f7e7edeed0c1b252bc3b3b4e339186697928649`: W3 job `130188785319`, run `42918981288`, **296 passed, 1 skipped**.
- Added a second native-platform regression that rewrites a pinned source with identical size and restored mtime, then requires SHA-256 source validation to reject publication while preserving pinned bytes. Latest branch SHA: `0cde3ff23fa1e22e694364867dae4848fdf9d055`; W3 run `42919753861`, job `130191312833`: **297 passed, 1 skipped**. Focused storage/watch run `42919753705`, job `130191312521`: **165 passed, 1 skipped**.
- W6 added that focused regression to `windows-native-publication.yml`; workflow syntax run `42919769519`: SUCCESS. The actual Windows-native execution requires integration to main and the resulting Windows runner job.
- Status: **SCOPED PASS** on the exact W3 branch SHA. The Windows-native workflow change is syntax-validated but the new native-platform test remains OPEN until it runs on `windows-latest` after main integration.

### Integration order

1. W1 scoped test passed (90 tests) and its test-only change is already integrated to main; await the exact-main gates on `b5dd5900acd0d3ac290d24f24611685ace36d1b2`.
2. W3 latest exact-branch lifecycle/export/QC run completed: 296 passed, 1 skipped. The 64 MiB test publishes its measured copy and heap metrics to the GitHub Actions job summary.
3. Integrate W3 test-only changes to main without dropping the already-integrated W1 test; integrate W6 documentation changes serially after code integration.
4. Run W1/W3 affected lanes, focused storage/watch, Studio release gate, full Python/browser, Windows-native and YAML on the exact resulting main SHA.
5. Update this ledger with exact final SHA and evidence. Do not treat scoped branch PASS as integrated PASS.


## 57. Three-way parallel regression closure — 2026-11-11

### Frozen baseline and ownership

- Frozen `main` baseline for this batch: `f063e5563c0c73ecc67099a50843803560d6870e`.
- W1 branch `work/w1-provider-submit-multiprocess-20261111`, SHA `ea44c2db53ab53acff4d7bc5ea1b35ef08a9f598`: owns only `tests/test_generation_recovery.py`. Adds a two-OS-process same-fingerprint provider submission test, requiring exactly one non-idempotent provider POST and one durable provider task ID. This extends the existing process-level ledger merge test, which did not exercise the full submission reservation boundary.
- W2 branch `work/w2-canvas-history-reload-20261111`, SHA `127d9d946835658c4b173158d764739f5008223c`: owns only `tests/test_film_studio_canvas_history_browser.py`. It synchronizes on the editor-authority wrapper after reload, wraps the real asynchronous `editorLoad` promise, awaits and validates its result before asserting the restored authority. This is an explicit load-completion contract, not a longer timeout or weakened assertion.
- W3 branch `work/w3-benchmark-junit-20261111`, SHA `d4130c3f63992aade3eee0fbb2c1b26dfbfb5272`: owns only `tests/test_film_render_scene_e2e.py`. Removes pytest `record_property` calls incompatible with this repository's JUnit `xunit2` format; the benchmark still publishes copy bytes, copy time, validation time and Python heap peak to the GitHub Actions job summary.
- W6 branch `work/w6-w2-canvas-ci-lane-20261111`, workflow SHA `ec72a6c8b8dd5ba2e356bc2d1c167a58e5f08d95`: owns workflow/docs only. Adds the canvas history reload browser test to W2's timeline transaction lane, not W3's lifecycle lane.
- These branches were created from the same frozen main SHA and have disjoint write ownership. No production source files were changed in this batch.

### Failure evidence and current status

- Exact main SHA `f063e5563c0c73ecc67099a50843803560d6870e`: W1–W7 owner lanes, Product IA, focused storage/watch, Windows-native and YAML completed successfully; Studio release gate succeeded.
- Full Python/browser run `42920662708`, job `130194365596`: **1,959 passed, 2 skipped, 1 failed**. Failure: `tests/test_film_studio_canvas_history_browser.py::test_studio_canvas_edit_undo_redo_and_persistence_after_reload`, line 421, waiting for Film Studio authority after a full browser reload. The same test had not been included in the dedicated W2 lane, so the main-only failure escaped the scoped gate.
- The full run also emitted a non-fatal JUnit warning because the W3 benchmark used `record_property` with `junit_family=xunit2`; W3 removes those unsupported properties while retaining job-summary metrics.
- New branch CI:
  - W1 latest corrected code/test SHA `0dc063150c3816b188f7feba0617e2e9751ac7d6`: W1 run `43004741070`, job `130469292519`: **91 passed**; YAML run `43004741039`, job `130469292412`: SUCCESS; focused storage run `43004740837`, job `130469656473`: in progress. The preceding W1 SHA `ea44c2db...` failed only because the new process worker helper was not inserted; the helper was added in `0dc06315` and the rerun passed 91 tests.
  - W2 exact branch SHA `c584dccb38957c119c617e8a84eb3fbf22aa9dd7`: run `43004343065`, job `130467973827`: **358 passed**; this run includes the Canvas reload test in W2's lane. YAML run `43004343074`, job `130467973600`: SUCCESS.
  - W3 exact branch SHA `d4130c3f63992aade3eee0fbb2c1b26dfbfb5272`: run `43003840649`, job `130466297077`: **297 passed, 1 skipped**; focused storage run `43003840734`, job `130466296123`: **166 passed, 1 skipped**.
  - W6 workflow SHA `ec72a6c8b8dd5ba2e356bc2d1c167a58e5f08d95`: syntax run `43003845806`, job `130466312747`: SUCCESS.
  - W6 workflow syntax run `43003845806`, job `130466312747`: **SUCCESS**; W6-only Product IA jobs were skipped as expected.
- W1 scoped generation durability **PASS**; its focused storage companion is still running. W2 scoped timeline plus Canvas reload **PASS** (358 tests). W3 scoped lifecycle **PASS** (297 passed, 1 skipped) and focused storage **PASS** (166 passed, 1 skipped). W6 workflow syntax **PASS**. Final integration remains OPEN until W1 focused storage finishes and all four branch changes are integrated into one exact main SHA.

### Integration and follow-on gates

1. Require W2 browser test to pass on its exact branch SHA; inspect its actual result and trace if it fails.
2. Require W3 lifecycle and focused storage lanes to pass on its exact branch SHA.
3. Require W6 YAML syntax to pass on its exact workflow SHA.
4. Integrate W2 test, W3 JUnit correction and W6 workflow change serially, preserving all intervening main changes; then rerun W2, W3, Studio release gate, full Python/browser, Windows-native and YAML on one exact resulting main SHA.
5. Reconcile the current full-suite-only canvas failure and make W2 include the canvas-history test so the scoped lane cannot omit it again.
6. After acceptance, continue independent audits of generation reservation/recovery and FilmDocument/Asset Catalog cross-process durability; do not reopen the already accepted W3 immutable resource snapshot absent new evidence.


## 58. Parallel follow-up: provider crash boundary and Asset Library cross-process merge — 2026-11-11

### Frozen baseline and branch ownership

- Frozen main baseline: `fe20b42770660db215d1f5f9210a2046d30b0416`. On this exact SHA, Full Python/browser completed with **1,963 tests, 0 failures, 0 errors, 2 skipped**; Studio release gate **353 passed**; W1 **91 passed**, W2 **358 passed**, W3 **297 passed, 1 skipped**, W4 **36 passed**, W5 **13 passed**, W7 **2 passed**, focused storage/watch **167 passed, 1 skipped**, Windows-native **3 passed**, YAML validation SUCCESS. All required jobs completed successfully. The NVIDIA live token-stream job was skipped by default.
- `work/w1-generation-crash-recovery-20261111` owns only `tests/test_generation_recovery.py`: force process termination immediately after the mocked provider accepts a non-idempotent POST but before the response/task ID is persisted; a later process must see the durable `submitting` reservation and refuse a second paid POST.
- `work/w2-canvas-history-project-isolation-20261111` owns only `tests/test_film_document_contract.py`: two projects with identical clip IDs perform independent edits/undo/redo; history operations in one project must not consume or mutate the other project's history.
- `work/w3-asset-catalog-transaction-audit-20261111` owns `tests/test_asset_catalog.py` and `tests/test_film_render_scene_e2e.py`: verify two independent OS processes updating different semantic fields of the same Asset Library item do not clobber each other; remove the unused `record_property` fixture argument from the 64 MiB snapshot benchmark, which still emits explicit GitHub job-summary metrics.
- `work/w6-next-batch-branch-register-20261111` owns workflow/docs only: run the Asset Library concurrent semantic merge test on the actual Windows `msvcrt.locking` branch and register exact branch ownership/acceptance.
- All four branches are frozen from the same main SHA and have disjoint file ownership. Branch PASS remains scoped evidence only.

### Scoped branch evidence

- W1 branch SHA `22aa3e0b63b6fe01122c886ab8b62ba0777826d7`: W1 generation durability **92 passed** (run `43013926271`, job `130500258859`); focused storage/watch **168 passed, 1 skipped** (run `43013926166`, job `130500720939`); YAML syntax SUCCESS (run `43013926171`).
- W2 branch SHA `f41fea0c06bb1603220a4205d1852d4856ad285e`: W2 timeline transaction suite **359 passed** (run `43014468734`, job `130501965838`); YAML syntax SUCCESS (run `43014468771`). This includes the new two-project history isolation regression.
- W3 branch SHA `cd863491551c91bc9c5cd31f132937db0a497aac`: W3 lifecycle/export/QC **299 passed, 1 skipped** (run `43013812285`, job `130502066852`); focused storage/watch **168 passed, 1 skipped** (run `43013812420`, job `130500271933`); YAML syntax SUCCESS (run `43013812275`). The lifecycle log no longer contains the pytest `record_property`/JUnit `xunit2` warning.
- W6 workflow SHA `2a8d4c4ca01ab21ce962b912d48107e5e4af5056`: YAML syntax SUCCESS (run `43013971093`, job `130500292783`). Windows-native execution of the newly added semantic-merge test is intentionally deferred until main integration because that workflow only triggers on main.
- W1/W2/W3 scoped Linux suites and W6 syntax are **PASS**. W6 Windows-native test and integrated exact-main gates remain **OPEN**.

### Next acceptance gates

1. W1/W2/W3 scoped Linux suites and W6 YAML syntax have passed on their recorded branch SHAs.
2. Integrate W1/W2/W3/W6 serially, preserving intervening main commits; then run W1/W2/W3, focused storage, Windows-native, Studio release gate, full Python/browser and YAML on one exact main SHA.
3. Confirm the new Asset Library semantic-merge test passes on Windows `msvcrt.locking` after integration.
6. Do not weaken the fail-closed provider reservation contract. The crash test must prove one provider POST only; it does not claim exactly-once semantics at the remote provider.
7. Keep the 64 MiB benchmark's Python heap bound distinct from process RSS; retain measured copy bytes/time and do not infer production performance from the synthetic fixture alone.

### Follow-on audit queue

- W1: provider reservation recovery and terminal-state races across independent processes, including the boundary between durable provider task ID and output finalization.
- W2: multi-context stale-revision behavior across browser tabs; the current project-scoped history test covers independent projects, not two tabs editing the same project concurrently.
- W3: Asset Library catalog mutation/read-modify-write concurrency, project-reference-safe deletion under concurrent updates, and representative large-media snapshot disk/time measurements.
- W6: keep owner-lane CI parity and native Windows coverage aligned with the tests that exercise platform-specific lock branches.


## 59. Concurrent durability and same-project stale-tab follow-up — 2026-11-11

### Frozen baseline and ownership

- Frozen baseline: `5935b3fddcaad09606ca5d014dad3623e1d3ddc0`.
- W1 `work/w1-terminal-recovery-races-20261111` owns only `tests/test_generation_recovery.py`. It adds a synchronized race between a stale provider poll (`running`) and local finalization (`done`) after the durable `downloading` state, then verifies terminal immutability.
- W2 `work/w2-multitab-stale-revision-20261111` owns only `tests/test_film_document_contract.py`. It models two browser tabs holding the same project revision; tab A commits, tab B's stale `require_base` save must return `REVISION_CONFLICT` without changing the committed bytes or clip fields.
- W3 `work/w3-asset-delete-race-20261111` owns only `tests/test_asset_catalog.py`. It adds an OS-process test proving a project-manifest writer blocks on the cross-process catalog transaction lock while deletion is in its critical section, then confirms the referenced asset cannot be deleted.
- W6 `work/w6-next-batch-register-20261111` owns only `docs/execution-ledger.md` and `docs/parallel-workstreams.md`; it records this batch's exact branch heads, CI gates and integration conditions.
- All branches were created from the same frozen main SHA and have disjoint file ownership. No production source code or protected shared contract is changed in this batch.

### Scoped CI state

- W1 branch SHA `286b4a16e8ebe8c9192686c231dce325ca7ae6e9`: W1 generation durability job **SUCCESS** (run `43021720278`); focused storage/watch **SUCCESS** (run `43021720561`); workflow syntax **SUCCESS** (run `43021720126`).
- W2 branch SHA `a1511b99203044c104784517dfed16efd815c667`: W2 timeline transaction job is **IN_PROGRESS** (run `43021887975`); workflow syntax is **IN_PROGRESS** (run `43021887980`).
- W3 branch SHA `e351c1a43921e1010457a3a9535a9ea25c087c7d`: W3 lifecycle job is **IN_PROGRESS** (run `43021786719`); focused storage/watch is **PENDING** (run `43021786693`); workflow syntax **SUCCESS** (run `43021786718`).
- W1 is scoped PASS. W2 and W3 remain **RUNNING / UNKNOWN** until their exact-SHA jobs finish. No branch is integrated based solely on an in-progress result.

### Acceptance and integration gates

1. Require W2 exact-SHA timeline tests, including the same-project stale-tab contract, to pass.
2. Require W3 exact-SHA lifecycle and focused storage/watch tests to pass; inspect the multiprocessing lock test's actual result.
3. Integrate W1, W2 and W3 serially, preserving intervening main commits; do not merge a branch whose scoped lane failed.
4. Run the affected W1/W2/W3 lanes, focused storage/watch, Studio release gate, full Python/browser regression, Windows-native and YAML validation against one exact resulting main SHA.
5. Keep the NVIDIA live provider job skipped unless a separately authorized explicit opt-in is supplied. No paid provider request is implied by this test-only batch.


### W6 Windows-native follow-up branch

- Branch `work/w6-windows-asset-reference-race-20261111` starts from the same frozen main SHA and owns only `tests/test_asset_catalog_windows_process.py` plus `.github/workflows/windows-native-publication.yml`.
- Adds a Windows `spawn`-based process test for the manifest-writer/catalog-delete race and includes it in the Windows-native publication workflow. The Linux-only W3 test uses `fork`; this separate test exercises the Windows `msvcrt.locking` path without changing W3's file ownership.
- Branch workflow syntax and exact-main Windows execution remain OPEN. The Windows job intentionally runs after integration because `windows-native-publication.yml` is main-only.


## 60. CI reconciliation after first branch attempts — 2026-11-11

- W1 latest branch SHA `286b4a16e8ebe8c9192686c231dce325ca7ae6e9`: generation lane **93 passed** (run `43021720278`), focused storage/watch **SUCCESS** (run `43021720561`), YAML **SUCCESS** (run `43021720126`).
- W3 initial SHA `e351c1a43921e1010457a3a9535a9ea25c087c7d` exposed a test-file syntax error caused by literal escaped newline characters. This was corrected, not suppressed. Latest SHA `73d06a8441b37243e8cbd107ddb1ec1da0e6a54d`: lifecycle lane **301 passed, 1 skipped** (run `43022132219`), focused storage/watch **SUCCESS** (run `43022132171`), YAML **SUCCESS** (run `43022132145`). The new cross-process reference/delete test is included in the passing W3 lifecycle lane.
- W2 first SHA `a1511b99203044c104784517dfed16efd815c667` returned **2 failures / 358 passed**: the new test incorrectly assumed an absent `volume` attribute had a default serialized value; an existing external-watch browser test also failed its asynchronous revision observation. The new assertion was corrected to verify the attribute remains absent. Latest SHA `6368f4ba18d1732171992e8fe2c444e8f14c291b` is running W2 timeline regression (run `43022505619`) and YAML validation (run `43022505569`). The external-watch failure is not waived; the rerun must establish whether it reproduces.
- W6 Windows branch `work/w6-windows-asset-reference-race-20261111`, SHA `755a7ae1483ffc761d19e196e25e00abd9539fae`: YAML validation **SUCCESS** (run `43022386946`). Actual `windows-latest` execution is intentionally deferred until main integration.
- Integration remains **BLOCKED** on W2 exact-SHA scoped PASS. W1/W3 are ready for serial integration only after W2 passes; W6 workflow changes follow the code/test branches. No main changes have been made by this batch.


## 61. Reconcile 2026-11-11 concurrent branch batch and exact-main integration

### Scoped branch evidence

- W1 work/w1-terminal-recovery-races-20261111, SHA 286b4a16e8ebe8c9192686c231dce325ca7ae6e9: generation durability 93 passed (run 43021720278); focused storage/watch SUCCESS (run 43021720561); YAML SUCCESS (run 43021720126).
- W2 work/w2-multitab-stale-revision-20261111, SHA 6368f4ba18d1732171992e8fe2c444e8f14c291b: exact-SHA React Studio timeline transaction and browser regression 360 passed (run 43022505619); YAML SUCCESS (run 43022505569). The initial external-watch observation failure did not reproduce on the corrected exact-SHA rerun; no assertion or test was waived.
- W3 work/w3-asset-delete-race-20261111, SHA 73d06a8441b37243e8cbd107ddb1ec1da0e6a54d: lifecycle/export/QC 301 passed, 1 skipped (run 43022132219); focused storage/watch SUCCESS (run 43022132171); YAML SUCCESS (run 43022132145).
- W6 work/w6-windows-asset-reference-race-20261111, SHA 755a7ae1483ffc761d19e196e25e00abd9539fae: YAML validation SUCCESS (run 43022386946). After integration, Windows-native exact-main run 43024008063, job 130533987770: 5 passed, explicitly including tests/test_asset_catalog_windows_process.py::test_windows_spawn_manifest_writer_serializes_with_library_deletion.
- W6 docs/register branch work/w6-next-batch-register-20261111, SHA 3c07f6e70589a6dba943d00794696c25249f248f: documentation-only change; no test lane required.

### Serial integration and exact-main status

- Frozen integration base: 5935b3fddcaad09606ca5d014dad3623e1d3ddc0.
- W1, W2, W3, W6 and documentation changes were integrated serially as five commits, preserving each source branch as a second parent. Final integration SHA: 12aac891b12b0ec577c318981396db398e080400.
- The main ref was advanced with an expected-SHA lease from the frozen base; no force update was used.
- Exact-main workflow evidence on 12aac891b12b0ec577c318981396db398e080400:
  - YAML validation run 43024007970: SUCCESS.
  - Windows-native run 43024008063: 5 passed, including the new Windows spawn lock regression.
  - Focused storage/external-edit run 43024008137: SUCCESS.
  - W1 generation durability job 130533988202: SUCCESS.
  - W7 shared-shell navigation job 130533988263: SUCCESS.
  - W2 timeline and W3 lifecycle jobs, Full Python/browser run 43024007958, and Product IA run 43024007986 were still running at this ledger update. Do not declare integrated acceptance until all required jobs finish on this exact SHA.
  - NVIDIA live provider/model acceptance remains explicit opt-in and is not triggered by this test-only integration.

## 62. Next independent batch: output finalization, real two-tab E2E, and media benchmark harness

### Frozen baseline and exclusive ownership

- Frozen baseline: 12aac891b12b0ec577c318981396db398e080400.
- W1 work/w1-generation-output-finalization-20261111 owns only tests/test_generation_recovery.py. It kills a separate process immediately after the MP4 atomic replace and before the durable done marker; recovery must promote the already-published bytes without downloading again.
- W2 work/w2-real-multitab-revision-20261111 owns only tests/test_film_studio_external_watch_browser.py. Two real Chromium tabs load one project at the same revision; tab B's real edit request is held, tab A commits, then tab B reaches the server with its stale base and must receive REVISION_CONFLICT without changing committed bytes.
- W3 work/w3-resource-snapshot-benchmark-harness-20261111 owns only video_director/film_resource_snapshot.py, scripts/benchmark_resource_snapshot.py and tests/test_film_render_scene_e2e.py. The CLI measures explicitly supplied existing project media: project-volume free-space delta, copied logical/allocated bytes, separate read/copy+fsync/hash-update timings, source revalidation time and copied-hash verification time. It never creates synthetic media for a production measurement.
- W6 work/w6-followup-register-20261111 owns only docs/execution-ledger.md and docs/parallel-workstreams.md. The existing W2 lane already runs tests/test_film_studio_external_watch_browser.py and the existing W3 lane already runs tests/test_film_render_scene_e2e.py; no workflow YAML change is required.
- Branches are created from the same frozen main SHA and have disjoint write ownership. Run each owner lane on the exact branch SHA. Integrate only after all scoped code lanes pass; then rerun the exact-main affected lanes and full release gates.

### Measurement boundary

- The benchmark harness's small fixture test verifies only the report contract. It is not representative media performance evidence.
- Actual snapshot performance remains MISSING until a real representative large project media file is supplied or otherwise available in the repository/project storage. Do not substitute a synthetic 64 MiB fixture or extrapolate production throughput from unit-test data.


## 63. Exact-main release closure and next-batch scoped reconciliation — 2026-11-11

### Previous batch integrated acceptance: PASS

On exact main SHA 12aac891b12b0ec577c318981396db398e080400:

- Full Python/browser regression run 43024007958, job 130533987297: 1,969 passed, 2 skipped, 0 failures, 0 errors.
- Studio build/timeline/lifecycle/export/QC release gate, same SHA, job 130533987549: 356 passed.
- Parallel workstream regression run 43024007984: W1 93 passed, W2 360 passed, W3 302 passed and 1 skipped, W7 2 passed; all owner jobs SUCCESS.
- Product IA run 43024007986: W4 project-home/create and W5 settings/credential-safety jobs SUCCESS.
- Focused storage/external-edit run 43024008137: SUCCESS.
- Windows-native run 43024008063, job 130533987770: 5 passed, including the newly integrated Windows spawn/process catalog-lock regression.
- Workflow YAML validation run 43024007970: SUCCESS.
- NVIDIA real token-stream acceptance: SKIPPED by explicit opt-in policy; no live/paid model call was made.

This closes the integrated acceptance for the previous batch on this exact main SHA. The main branch is not being modified by the next batch while its scoped branch work is running.

### Next batch scoped evidence

- W1 branch work/w1-generation-output-finalization-20261111, latest SHA 44c8a3468ddfc7d1448dcbd726d4a6f233ea0313: W1 generation durability 95 passed (run 43026770873, job 130543328949); focused storage/watch 173 passed, 1 skipped (run 43026770993, job 130543330284); YAML SUCCESS (run 43026770825). It covers process death after atomic MP4 publication and after manifest commit; both recovery paths must reuse durable bytes and avoid another provider call.
- W3 branch work/w3-resource-snapshot-benchmark-harness-20261111, latest SHA 969bd0a4e3fb26b3faaf11ad19b80eaa84c558a6: lifecycle/export/QC 304 passed, 1 skipped (run 43028662784, job 130549720506); YAML SUCCESS (run 43028662689). It adds project-volume free-space delta and separate read, copy+fsync, hash-update, source-revalidation and copied-hash timings. The legacy copy_elapsed_ms report field retains its prior total-build semantics.
- W2 branch work/w2-real-multitab-revision-20261111, latest SHA 50479f987b41fa8a7811ef1fb4f679adf43b5080: timeline transaction and browser regression 361 passed (run 43025769040, job 130540097926); YAML SUCCESS (run 43025769403). The initial response-observer failure was corrected by forwarding the held request with Playwright route.fetch/fulfill and inspecting the actual API response.
- W6 documentation branch work/w6-followup-register-20261111, latest docs SHA e5be9ad3d77865b2b364808792df7b6229da538e: docs-only update. YAML validation run 43025391185 succeeded on the same workflow content as the frozen baseline; no workflow YAML delta was needed.

### Remaining hard gate

- W2's real two-tab browser case must pass on its latest exact SHA.
- Then integrate W1 → W2 → W3 → W6 serially and run all affected scoped lanes plus full Python/browser, Studio release gate, focused storage/watch, Windows-native and YAML on one resulting main SHA.
- The real-media benchmark harness is implemented and functionally tested, but representative production performance remains MISSING until a real large project media asset is available. No synthetic fixture result is a substitute.


- W1 test-fixture reconciliation: the first manifest-crash test attempt used non-MP4 placeholder bytes, so the real Asset Catalog correctly rejected the fixture with BAD_FILE_TYPE and left registry_status pending. No production code was changed. The test was narrowed to the manifest/job-marker recovery boundary and mocks only the separate Asset Catalog registration call; the subsequent exact-SHA W1 lane passed.


## 64. Next-batch exact-SHA closure — 2026-11-11

All code owner lanes for the next batch now pass on their latest exact branch heads:

- W1 generation finalization/recovery: 44c8a3468ddfc7d1448dcbd726d4a6f233ea0313 — 95 passed; focused storage/watch 173 passed, 1 skipped; YAML SUCCESS.
- W2 real two-tab stale revision: 50479f987b41fa8a7811ef1fb4f679adf43b5080 — 361 passed; YAML SUCCESS.
- W3 resource snapshot measurement: 969bd0a4e3fb26b3faaf11ad19b80eaa84c558a6 — 304 passed, 1 skipped; YAML SUCCESS.
- W6 documentation register: work/w6-followup-register-20261111 — docs-only branch; no conflicting code ownership.

W3 test reconciliation:
- One run failed because the test expected a wording fragment that did not match the measurement-scope text; the assertion was corrected.
- The next run exposed a missed timer attribute rename in the fsync timing path. That attribute was corrected, and the final exact-SHA W3 lane passed.
- The implementation preserves the pre-existing copy_elapsed_ms report semantics and exposes the new isolated copy I/O timing in copy_io_elapsed_ms; this avoids silently changing existing consumers.

The next action is serial integration W1 → W2 → W3 → W6 onto main, followed by exact-main W1/W2/W3, focused storage/watch, Studio release gate, full Python/browser, Windows-native and YAML validation. Actual representative large-media measurements remain MISSING pending real project media; no synthetic fixture is used as production evidence.


## 65. 2026-11-12 follow-up: commercial preflight matrix and CI media-runtime ordering

### Frozen integration baseline

- Main remains frozen at `4bf9e70fee0b71e701cbc7f5ebc231356e0f3984`; no product or test code from this batch has been integrated.
- This batch continues on short-lived W1/W2/W3/W6 branches. The coordinator may propagate W6-owned workflow fixes onto code branches so each code owner lane executes against the corrected CI contract; that does not transfer workflow ownership to W1/W2/W3.

### Active scoped branches

- W1 Linux cross-process generation dedup: `work/w1-multiprocess-submit-dedup-20261111`, SHA `eb602435e63a62d45b9a76f28815cd13622daf15`; generation lane 96 passed, focused storage/watch and YAML SUCCESS. This proves local same-fingerprint submit serialization, not remote provider exactly-once.
- W1 Windows spawn generation lock: `work/w1-windows-generation-submit-lock-20261111`, SHA `86be7edef26f8ffa54c27296fd56188cfe6bb89e`; W1 owner lane and YAML SUCCESS. Actual Windows-native spawn execution remains pending integration.
- W2 external revision after full-page refresh: `work/w2-refresh-after-external-revision-20261111`, corrected workflow/test SHA `edbe599932e51263bff2ecd391f83e4851559de0`; YAML SUCCESS, focused storage/watch SUCCESS, W2 browser/timeline lane RUNNING (run 43131532044).
- W2 canvas-history startup selection race: `work/w2-canvas-history-reload-race-20261111`, corrected workflow/test SHA `4dd753d9dbdd48443a7c8043f66eea44d9cbc089`; YAML SUCCESS, focused storage/watch SUCCESS, W2 browser/timeline lane RUNNING (run 43131542189).
- W3 commercial preflight hard-gate matrix: `work/w3-commercial-preflight-matrix-20261111`, latest SHA `69dab033e6f2b594d63aebb3b72fad19e3240129`; tests cover CLEAR, UNKNOWN, BLOCKED, missing media and mixed CLEAR+UNKNOWN asset references. Latest W3 lifecycle lane RUNNING (run 43131728496); focused storage/watch SUCCESS on prior corrected SHA `36b1eeb759c60a404d2bb34db82d03ab51e11982`; latest YAML validation is RUNNING.
- W6 workflow/runtime ordering: `work/w6-commercial-preflight-lane-20261111`, SHA `fc343092829eb39fe7302f7f8fd941b30895794f`; YAML validation SUCCESS (run 43131426973), focused storage/watch SUCCESS (run 43131420050).

### CI failure diagnosis and correction

- The first W2/W3 scoped runs failed before pytest: `.github/scripts/install-ubuntu-media-deps.sh` imports `video_director.graphics.font_resolver`, which requires `loguru`, but the workflow invoked the script before installing `requirements.txt`. This is a CI setup-order defect, not a product-test failure.
- W6 corrected the order in parallel workstream, product IA, full hosted, focused storage/watch and Studio UI build workflows: install Python runtime/test dependencies first, then run the verified media/font preflight. No dependency pin was weakened and no media/font verification was bypassed.
- Corrected workflow blobs were propagated to the active W2/W3 branches while preserving each code branch's exclusive test-file ownership. W2 focused storage/watch and W3 focused storage/watch subsequently passed. The W2 browser lanes and latest W3 lifecycle lane must still finish on their exact latest SHAs before integration.

### Remaining hard gates

- Do not integrate W2 refresh, W2 canvas-history or W3 commercial preflight until their latest exact-SHA owner lanes pass.
- Then integrate W1 Linux, W1 Windows, W2 refresh, W2 canvas-history, W3 commercial preflight and W6 workflow/docs changes serially, preserving all intervening main commits.
- Run the complete affected owner lanes, focused storage/watch, Studio release gate, full Python/browser, Windows-native publication and YAML validation on one exact resulting main SHA.
- Windows spawn test for W1 must actually execute on the integrated main SHA.
- Representative large-media snapshot performance remains MISSING until real representative project media is available; no synthetic fixture is production evidence.
- NVIDIA live-model acceptance remains explicit opt-in and is not triggered by these CI-only changes.



## 66. 2026-11-12 media-runtime hardening and scoped-lane revalidation

### Exact baseline and current main

- Frozen integration baseline for this batch remains `4bf9e70fee0b71e701cbc7f5ebc231356e0f3984`.
- Current main is `f53ed86791b31e74033e0fa059b4b5a4d13853a2`; it includes the W6 dependency-order correction and the W8 creative-browser target update.
- Do not replace current main workflow files with stale branch snapshots. The W2/W3 branches were created from the frozen baseline and are behind current main; integrate only their exclusively owned test files. W1 Windows additionally owns its Windows-native workflow change. W6 supplies the final CI script/workflow and documentation deltas.

### W6 FFprobe correction

- Main full-hosted run `43132644847` failed with 9 tests: 8 AAC metadata assertions and one template-gallery browser wait for `#tg-render` to become enabled.
- Root cause for the eight media assertions: the pinned imageio-ffmpeg wheel provides FFmpeg but not FFprobe. The renderer correctly fell back to stderr probing, which cannot provide the codec/sample-rate fields required by the tests.
- First correction attempt used a dynamic FFprobe build and failed on the hosted runner because `libva.so.2` was absent. That attempt is superseded and must not be integrated.
- Final W6 correction uses the upstream static Linux x64 FFprobe asset with a pinned SHA-256. Before tests run, the script creates an AAC MP4 with the exact pinned FFmpeg binary and requires FFprobe to report `codec_name=aac` and `sample_rate=48000`. Failure is a hard stop; no silent fallback is accepted as CI readiness.
- The static FFprobe preflight is PASS on W3 exact branch run `43135695334`; log evidence includes `FFPROBE_SMOKE=PASS`.
- W6 also adds `test-results/**` to the full-regression artifact so Chromium traces and server diagnostics survive browser failures.

### Exact-SHA scoped evidence

- W1 Linux cross-process submit dedup: `eb602435e63a62d45b9a76f28815cd13622daf15`; 96 passed, focused storage/watch and YAML SUCCESS.
- W1 Windows spawn lock: `86be7edef26f8ffa54c27296fd56188cfe6bb89e`; W1 owner lane and YAML SUCCESS. Actual Windows spawn execution remains a hard gate on integrated main.
- W2 external revision after refresh: latest corrected branch SHA `67f871b9af76edea722bb3d60b8369b56190a054`; owner lane run `43135684066` SUCCESS: 362 passed, 0 failed. The run includes `FFPROBE_SMOKE=PASS`.
- W2 Canvas history startup race: first static-FFprobe run on `7787c342c772f958e17ee21db2cd2073deb0882a` reached 360 passed but failed one browser test because the test waited for current FilmDocument authority before triggering the project selector's normal `editorLoad` action. The test now explicitly reopens the project through that selector and awaits the actual `editorLoad` promise; latest SHA `a9f6f6b4c1730db53f2dd8d5c5525beb98b2261f`, owner lane run `43136145184` SUCCESS: 361 passed, 0 failed.
- W3 commercial preflight matrix: latest corrected branch SHA `bdbb62ac9f6a30ed8b0d243e09b50cf374cd55f5`; lifecycle/export/QC run `43135695334`: 353 passed, 1 skipped, 0 failures. This exact run also passed the static FFprobe AAC smoke preflight.
- W6 workflow syntax on CI workflow SHA `afd764e1ea1737015c11e4cbe433f23829745cc1`: run `43136216959` SUCCESS. The full-regression job sets `VIDEO_DIRECTOR_TEST_RESULTS=test-results` and uploads that directory alongside JUnit and pytest logs, so browser traces are retained.

### W2 Canvas-history test reconciliation

- The failed wait was at the browser-reload boundary, not during undo/redo persistence: the test had reloaded the page and shown the editor tab, then waited for `filmStudioHasCurrentFilmAuthority()` before it invoked the normal project-selector change that calls `editorLoad()`.
- The wait was therefore circular when the page did not auto-open the project. The test now triggers the real selector action first, captures and awaits the actual asynchronous `editorLoad` result, checks `film_document` and project identity, and only then waits for FilmDocument authority and the restored Inspector.
- No timeout was increased and no product assertion was weakened. The new exact-SHA owner run is required before integration.

### Remaining hard gates and integration order

- All scoped owner lanes are now PASS on their exact SHAs. Integration may proceed serially in the order below; keep the frozen baseline and current main as the first-parent integration base.
- Integrate W1 Linux → W1 Windows → W2 refresh → W2 canvas-history → W3 commercial preflight → W6 CI/docs.
- For W2/W3 branches, take only the owner test file(s); do not copy their stale workflow snapshots over current main. W6-owned script/workflow changes are integrated from the final W6 branch.
- After integration, run all affected W1/W2/W3/W7/W8 lanes, focused storage/watch, Studio release gate, full Python/browser, Windows-native publication and YAML validation on one exact main SHA.
- The template-gallery browser timeout shares the same root cause: `template_runtime_report()` sets `can_render=false` when `prober._resolve_ffprobe()` returns no executable, so `#tg-render` remains disabled after a valid upload. The static FFprobe preflight directly addresses this readiness gate. The integrated full-browser regression must verify the button becomes enabled; if it still fails, use the retained trace and server diagnostics to fix the actual readiness/asset-upload condition rather than increasing timeouts or weakening the assertion.
- NVIDIA live-model acceptance remains explicit opt-in. Representative large-media snapshot performance remains MISSING pending real project media.



## 67. 2026-11-12 integrated-main acceptance closure

### Exact integrated code revision

- Integrated code SHA: `28fc07f43cf0f01d7c39f40c89273f4395a91c10`.
- Six short-lived workstreams were integrated in the documented order with path-scoped trees and source branch heads preserved as merge parents. W2/W3 stale workflow snapshots were not copied over current main.
- All following GitHub Actions runs are on this exact SHA; no failed jobs remain.

### Integrated CI evidence

| Gate | Run | Result |
|---|---:|---|
| Full hosted regression | `43136680598` | 1,980 passed, 2 skipped, 0 failed; 647.24s |
| Studio release gate | `43136680598` | 360 passed |
| Parallel workstreams | `43136680531` | W1 96; W2 362; W3 353 passed/1 skipped; W7 2; W8 8 |
| Product IA | `43136680533` | W4 36; W5 13 passed |
| Windows native publication | `43136680515` | 6 passed |
| Focused storage/external edit | `43136680661` | 174 passed, 1 skipped |
| GitHub Actions YAML | `43136680617` | SUCCESS |

- The Windows-native run executed `test_windows_write_through_replace_and_delete_round_trip`, the Windows spawn manifest-writer/delete serialization test, and the Windows spawn generation-submit lock test. These are no longer merely Linux mocks or branch-only results.
- Full regression verified generation-job list/resolve API redaction, operator outcome persistence, ambiguous/conflicting outcome rejection, and `submission_unknown` fail-closed behavior.
- Media runtime emitted `FFPROBE_SMOKE=PASS`, with `codec_name=aac` and `sample_rate=48000`. The prior eight AAC metadata failures are closed.
- Real Chromium workspace smoke passed in 76.371s. The server log records `POST /api/templates/sample_render` 200 and MP4 download 200; the test's MP4 decode and browser-preview assertions passed. A 45s faulthandler diagnostic dump occurred while waiting for the renderer subprocess, but the test completed successfully. Keep the 300s hard render timeout; optimize actual render time rather than hiding it by loosening acceptance.
- Full-regression artifact `full-regression-28fc07f43cf0f01d7c39f40c89273f4395a91c10` retained JUnit, pytest output, and `workspace-smoke-server.log`.

### Remaining work — next independent batch

1. **W2 bounded edit-operation sequence matrix.** Extend from individually passing operations to explicit, deterministic operation combinations and state invariants. Do not claim exhaustive permutations; uncovered sequences remain UNKNOWN.
2. **W3 CompositionIR semantic round-trip matrix.** The 241-field disposition inventory classifies fields as mapped/archive-only/rejected; it is not proof that every mapped field is semantically lossless. Add round-trip tests for mapped field groups and explicit preservation/rejection assertions for archived or unsupported fields.
3. **W8 HTML scene determinism boundary.** Keep arbitrary HTML/WebGL, nondeterministic animation and unsupported external-resource behavior outside the supported claim. Add or extend fail-closed tests for unsupported capabilities; do not silently render them as deterministic.
4. **Real-media snapshot performance remains MISSING.** The benchmark harness exists, but representative large project media is required. Do not substitute synthetic media and report it as representative.
5. **NVIDIA live token-stream acceptance remains explicit opt-in.** No paid/provider live calls were made in this batch. Run only in the configured NVIDIA environment with explicit authorization and retain the acceptance artifacts.

- Do not create the next batch until this exact-main acceptance is recorded and the new baseline is frozen. New work must again use one main baseline, independent W2/W3/W8 branches, a W6 workflow/docs branch, owner CI, then path-scoped serial integration.



## 68. 2026-11-12 next parallel batch — edit-sequence and subtitle migration contracts

### Frozen baseline and ownership

- Frozen main baseline: `ed8431faac81a5567b28a82e9da972ddab6f701c`. This is a docs-only successor to fully accepted code SHA `28fc07f43cf0f01d7c39f40c89273f4395a91c10`; the code tree is unchanged.
- W2 owner branch: `work/w2-edit-operation-sequence-matrix-20261112`, latest SHA `f63d539b44b5714e41f4bc5aeab0a0db13b84f43`. Owns only `tests/test_film_document_contract.py`.
- W3 owner branch: `work/w3-compositionir-semantic-evidence-20261112`, latest SHA `1f2c20ced5301a356988919c3a716901b33e0c9f`. Owns `tests/test_film_subtitle_migration.py`; W6-owned workflow wiring is propagated only to execute its test and the existing field-audit test.
- W6 owner branch: `work/w6-next-batch-wiring-20261112`, latest workflow SHA `2e554f379d8b3f15cbd2b26fc21403f91a4e4c01`. Owns `.github/workflows/parallel-workstream-ci.yml` and the execution/parallel ledgers.
- All branches start from the same frozen baseline. Main remains integration-only.

### Scope

- W2 adds bounded multi-operation sequences: split → roll → slip with deterministic replay; set-envelope → split → trim → ripple-delete with source/timeline rebase; and a valid first operation followed by a locked-target rejection to prove no partial mutation. This is not exhaustive permutation coverage.
- W3 adds a CompositionIR subtitle migration contract: subtitle cue time/text and supported style properties must reach the generated subtitle page and FilmDocument track; legacy word-mode/source/style-registry lineage must produce explicit warnings and remain in the source archive rather than being represented as runtime behavior.
- W3 lane now includes `tests/test_composition_ir_migration_field_audit.py` and `tests/test_film_subtitle_migration.py`.
- W8 is intentionally not broadened in this batch. Arbitrary HTML/WebGL frame determinism remains outside the supported claim; do not enable or imply support without a separate capability contract and user-facing decision.

### Hard gates and external blockers

- Both owner lanes must pass on their latest exact SHAs before path-scoped integration. A failure returns only to the owning branch.
- Real-media snapshot performance remains MISSING until representative project media is available; synthetic fixtures cannot close that gate.
- NVIDIA live token-stream acceptance remains explicit opt-in; do not invoke paid/live provider calls as part of these CI tests.


## 69. 2026-11-12 owner-lane acceptance — ready for serial integration

| Owner lane | Exact branch SHA | CI evidence | Result |
|---|---|---|---|
| W2 timeline transactions | `f63d539b44b5714e41f4bc5aeab0a0db13b84f43` | Run `43139051903` | 365 passed, 0 failed |
| W3 lifecycle/export/QC + subtitle migration | `1f2c20ced5301a356988919c3a716901b33e0c9f` | Run `43140616590` | 370 passed, 1 skipped, 0 failed |
| W3 workflow syntax | same W3 branch SHA | Run `43140616559` | SUCCESS |
| W6 workflow syntax | `2e554f379d8b3f15cbd2b26fc21403f91a4e4c01` | Run `43139165110` | SUCCESS |

- W2's three sequence tests pass: split→roll→slip deterministic replay; envelope set→split→trim→ripple-delete rebasing; and late locked-target rejection without partial mutation.
- W3 subtitle migration test passes after using the real initialization transaction and lock-consistent legacy composition version. It verifies cue timing/text, supported style CSS, subtitle FilmDocument track/stacking, and source-snapshot preservation.
- The recursive field audit now links all 19 SubtitleCue/SubtitleStyle/SubtitleTrack rows to the exact migration test; the audit test asserts those evidence references exist.
- Both owner lanes are green on their latest SHAs. Proceed with W2 → W3 → W6 path-scoped serial integration; do not copy W3's propagated workflow snapshot. W6 remains the sole owner of workflow changes.


## 70. 2026-11-12 next parallel batch — semantic migration, Shotcraft UX, and execution-policy reconciliation

### Frozen baseline

- Main code/workflow SHA: `23bab67b59d968bcc2c70ef90faa52ca0939d91f`.
- The exact-SHA Parallel workstreams run `43147459576` completed SUCCESS: full Python/browser regression and Studio build/timeline/lifecycle/export/QC release gate both PASS. The explicit NVIDIA real token-stream job was SKIPPED by design.
- Product IA run `43147459508` completed SUCCESS: W4 project home/create lifecycle and W5 settings IA/credential safety both PASS.
- This SHA is the frozen base for the next independent short-lived branches. No code/workflow changes are inferred from documentation-only history.

### Independent branches and ownership

| Lane | Branch | Exclusive ownership | Acceptance |
|---|---|---|---|
| W3 | `work/w3-compositionir-mapped-roundtrip-20261112` | CompositionIR migration semantic tests and field-audit evidence; no production migration contract changes without W0 review | Mapped field groups prove semantic values survive source→FilmDocument→reopen; archived-only/rejected fields remain explicitly archived/rejected |
| W8 | `work/w8-scene-fail-closed-acceptance-20261112` | Shotcraft browser acceptance tests only; no renderer or workflow edits | Real Chromium verifies creative workspace interactions and failure states; W8 does not own scene determinism |
| W6 | `work/w6-parallel-policy-reconciliation-20261112` | `docs/execution-ledger.md`, `docs/parallel-workstreams.md` only | Remove conflicting ownership descriptions; W3 owns scene/export determinism; W8 owns Shotcraft browser UX; short-lived branch parallelism is normative |

### Integration and acceptance

- W3, W8 and W6 are separate branches created from the same frozen main SHA. Their file ownership is disjoint; W6 is documentation-only.
- W3 scene/resource fail-closed evidence remains part of W3 lifecycle/export acceptance. W8's existing CI command covers Shotcraft browser UX only.
- Integrate only after W3 and W8 owner lanes pass on their exact branch heads. Integrate path-scoped test files first, then W6 documentation; never overwrite current main workflow files with branch snapshots.
- After integration, rerun W3 lifecycle/export/QC, W8 Shotcraft browser acceptance, W1/W2/W7, W4/W5, full Python/browser regression, Studio release gate, focused storage/watch, Windows-native publication and YAML validation on one exact main SHA.
- Real representative-media performance remains MISSING pending user-supplied representative media. NVIDIA live token-stream acceptance remains explicit opt-in and was SKIPPED on the frozen baseline; no provider call is authorized by this entry.


### 2026-11-12 owner-branch execution update

- W3 semantic reopen assertion commit: `cd1e06ace5d12cec3946befc8f8a0194b339c6a9`; lifecycle/export owner run `43156659597` is IN_PROGRESS at this update. It adds post-parse assertions for subtitle track attributes, clip references, generated subtitle HTML bytes, and archived source snapshot equality.
- W8 locked-track interaction commit: `8fa3fbf521b6687e27aac2f3ffb2f3816e77e432`; Shotcraft browser owner run `43156641957` is IN_PROGRESS at this update. It verifies a real Chromium pointer drag against a locked visual track is rejected with the explicit error and dispatches no edit command.
- W6 documentation branch latest commit: `b16867c9f2904f30bb68a57882cec92fd34f3221`. It reconciles the short-lived branch protocol, adds W8 to the lane register, and assigns deterministic scene/resource/export acceptance to W3 rather than W8. This branch is documentation-only; it has no product acceptance claim.
- No branch is ready for integration until its owner lane has completed successfully on the exact recorded SHA. Do not treat IN_PROGRESS as PASS.
