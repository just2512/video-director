# Product IA Audit & Implementation Plan

**Repository:** `just2512/video-director`  
**Audited ref:** `main`  
**Audited commit:** `c0d6fa2f313b0037a40590e9afd44c26d5140b37`  
**Date:** 2026-11-07  
**Scope:** Product navigation, project entry/create flow, settings information architecture, local configuration persistence, routing and project context.  
**Status:** Audit baseline recorded; implementation not started by this document-only change.

## 1. Product decision

The product is a project-centred video creation workbench, not a model/API administration console.

Target hierarchy:

1. **Projects and creation** — create, resume, organize and inspect video projects.
2. **Creation workbench** — director planning, assets, timeline editing, AI assistance, review/QC and export.
3. **Production capabilities** — text, image/video generation, vision, semantic retrieval, visual QA, voice, stock media and rendering.
4. **System settings** — provider credentials, model endpoints, defaults, import/export.
5. **System maintenance** — environment checks, diagnostics and developer-only details.

Settings remains accessible, but it must not visually or structurally outrank the project workflow.

## 2. Main-branch audit findings

### 2.1 Navigation and page structure

Observed in `webui/static/index.html`:

- The application already has three top-level spaces: **项目 / 工作台 / 设置**.
- The top bar already exposes **+ 新建项目** as a primary action.
- The default `pane-home` is the project page; settings is a separate `pane-settings`.
- The project page already includes project search, lifecycle filters, bulk actions, task drawer, project detail and series management.
- The settings pane is currently a long vertical stack: model roles, voice, stock sources, output defaults, config import/export, environment check and developer mode.
- New-project entry is still an `openCreate()` action rather than a dedicated first-class route/page. The exact create-flow markup and persistence boundaries need a focused follow-up trace before implementation.

**Implication:** Do not rebuild the application shell from scratch. The core project-first navigation exists. The immediate UX defect is the relative prominence and density of the settings pane, plus the new-project flow's modal treatment and the separation between project setup and system defaults.

### 2.2 Routing

Observed in `webui/static/js/app.js`:

- `switchTab(tab)` assigns `window.UI.route` and toggles the three pane elements.
- Switching to home reloads projects, series and inbox.
- Switching to editor initializes the editor and build metadata.
- Switching to settings loads settings, developer build ID, stock status and model badge.
- The inspected route implementation is an in-page tab switch; no evidence in the inspected code establishes a URL-addressable route or browser-history restoration contract.

**Implication:** First implement a stable in-app subview/navigation contract without introducing a router migration. Decide separately whether deep links/history are required. Do not mix a routing-library migration into the settings redesign.

### 2.3 Local model/API configuration — critical preservation boundary

Observed in `video_director/model_config.py`:

- Role definitions are centrally registered in `ROLE_DEFS`.
- Local file persistence is `composer_storage_root()/model_config.json`, normally under `storage/composer/`.
- Environment variables take precedence over file values.
- Legacy environment fallbacks are explicitly supported, including `VD_LLM_API_KEY`, `COMPOSER_VISION_API_KEY`, `VD_VISION_API_KEY` and generic `VD_<ROLE>_API_KEY`.
- `load_all()` reads persisted role records and caches them.
- `get_role_config()` resolves environment first, then local file.
- `save_all()` currently writes the supplied role dictionary as a whole-file replacement.
- `status_for_frontend()` deliberately returns role metadata and capability states without returning API key values.

Observed in `webui/server.py` and `webui/static/js/app.js`:

- `POST /api/model_config` recognizes the `__KEEP__` sentinel and substitutes the existing persisted key for roles represented by that sentinel, then calls `save_all()`.
- The current settings UI displays a placeholder for file-backed saved keys; a blank field is treated as clearing that role, while the explicit placeholder means keep.
- `POST /api/settings/import` imports role/base URL/model structure while retaining existing local keys via `__KEEP__`.
- Export/import and model settings are server-backed; they are not ordinary browser `localStorage` settings.

**Hard constraints for all implementation branches:**

1. Never delete, rename, relocate, reset, re-encrypt, or silently migrate `model_config.json`.
2. Never change `ROLE_DEFS` role IDs, provider identifiers, capability IDs, environment-variable precedence or legacy fallback semantics as part of a UI redesign.
3. Never return secret values to the browser. Keep the existing write-only secret input pattern and explicit keep/clear semantics.
4. Never call `save_all()` with a partial role set. Its current semantics are whole-file replacement. Any future partial-save API must be additive and separately tested; do not silently alter this contract during layout work.
5. Import must continue to preserve local keys. Export must continue to exclude keys.
6. A UI-only refactor must not write configuration merely by opening the settings page, switching categories, or rendering forms.
7. Before and after implementation, compare the local config file byte-for-byte where no explicit user edit was requested; in tests, seed sentinel keys and verify they survive navigation, refresh, settings expansion/collapse, import and unrelated-role saves.
8. Do not run destructive migration or configuration-reset scripts against the user's local storage.

### Required config regression cases

- Existing file-backed keys survive opening settings and switching tabs.
- Existing file-backed keys survive a save that changes only another role.
- Environment-backed keys remain environment-backed and are never copied into browser responses.
- Import updates only permitted non-secret structure and preserves local keys.
- Export contains no API key, secret, credential, or equivalent value.
- Malformed config is reported without silently replacing the original file.
- Missing optional provider configuration does not erase other roles.
- Existing role/provider/capability identifiers remain stable.

### Important implementation issue to resolve before changing save semantics

The present `save_all()` is whole-file replacement, while the HTTP handler reconstructs keys for submitted roles only. A future UI split into independently saved settings categories must not submit only the visible category to this endpoint. Either keep one complete-role save payload for the existing contract, or add a separately tested atomic per-role/per-category patch endpoint that preserves all omitted roles. This is a backend contract change and should not be hidden inside a front-end refactor.

### Scope clarification

The user-facing statement “API settings are saved locally” is supported by the inspected model-config persistence path. This audit does not inspect the user's running machine or read their actual local secret file. Therefore, the actual contents and current local keys are **not inspected**; preservation is a mandatory invariant, not a claim that a backup has been made.

### Other persisted state

- Director draft snapshots use browser local storage and a server draft endpoint in `webui/static/js/create.js`; the before-unload path has explicit protections against recreating a cleared active draft.
- Project records and task state are stored in SQLite at `composer_storage_root()/composer.db` via `video_director/store.py`.
- Project files live under `composer_storage_root()/`.
- Project metadata is also held in each SQLite row's `params_json`; `project_manager.py` deliberately keeps name/archive metadata there to avoid schema migration.
- Asset manifests are project-local JSON with locking and atomic replacement in `video_director/project_manifest.py`.

Do not conflate browser draft state, local model credentials, SQLite project records, project files, or project manifests. They are separate persistence domains and need separate regression tests.

### 2.4 Project context and timeline authority

Observed in `video_director/project_context.py` and `webui/static/js/workspace.js`:

- Project context is revision-pinned and reports authority, project dimensions/ratio, selection, timeline, assets, active features and provider availability.
- `film.html` is authoritative when present. The project-context resolver checks FilmDocument before CompositionIR.
- If FilmDocument authority cannot be established, the system reports unknown authority rather than silently falling back to stale CompositionIR.
- `workspaceOpen()` is the central project-opening path and guards against stale project-selection responses.
- When `film.html` exists, the Film Studio is used; the legacy CompositionIR path is only used when FilmDocument is explicitly absent or when an explicit compatibility mode is requested.
- Project refresh/reload must retain the existing revision and authority safeguards.

**Implication:** Project creation and project navigation must feed the existing project/workspace lifecycle. Do not create a parallel project context, duplicate editor selection state, or introduce a second timeline authority.

### 2.5 Existing project persistence

Observed in `video_director/store.py`, `video_director/project_manager.py` and `video_director/project_manifest.py`:

- SQLite stores project rows, serialized params, task status and history.
- Project directories and files remain under the configured composer storage root.
- Project manager centralizes lifecycle actions and avoids schema migration for name/archive fields.
- Asset manifest writes use project locks and atomic replacement.

**Implication:** Project-level output settings should be stored with the project using an explicit, backward-compatible project contract. They must not be written into global model configuration. Existing projects without the new fields must continue to open with documented defaults without rewriting their records on read.

## 3. Target information architecture

### Top-level spaces

1. **项目** — home/dashboard, recent projects, search, status, resume, create.
2. **工作台** — one authoritative project editing environment; Director, assets, timeline, Inspector, AI assistant, QC and export.
3. **素材资源** — optional dedicated space only if the current library workflows cannot be made discoverable inside the workbench; do not add a top-level tab solely for symmetry.
4. **设置** — secondary system configuration.
5. **系统维护** — nested under settings and developer mode, not a top-level destination.

Keep the existing three-space shell initially. Improve the project landing and creation experience before considering a fourth top-level space.

### Settings categories

- **AI 模型**: text generation; image generation; video generation; frame vision; semantic retrieval; visual QA.
- **语音与配音**: Edge TTS default plus optional Azure Speech and ElevenLabs.
- **素材来源**: Pexels, Pixabay and Coverr, with commercial-use restrictions visible at point of use.
- **渲染与输出服务**: HyperFrames as an optional cloud renderer; FFmpeg remains the built-in fallback.
- **创作默认值**: default aspect ratio, resolution and FPS. These are defaults only; project-specific values belong to the project.
- **配置管理**: key-safe export/import and backup/restore guidance.
- **系统维护**: environment self-check, diagnostics, logs and developer mode.

Default view should show concise service summaries and state, not every API field. Expand a role/provider to edit its key, base URL, model/region and test capability. Do not show secret values after save.

### Project-first home and create flow

- Keep project list as the default home.
- Make **新建项目** a visually dominant primary action within the project page and top bar.
- Replace the modal with a dedicated full-work-area create flow only after auditing the full existing `openCreate()`/form/draft lifecycle and browser back/close semantics.
- Creation should ask only for project-specific inputs: name/topic/type and output profile (ratio, resolution, FPS). Provider/model choice should inherit current system configuration unless the user explicitly overrides it for this project.
- Advanced creation options remain collapsed and optional.
- Project settings are stored with the project and do not mutate global API/provider settings.
- A failed create or abandoned wizard must not create partial project rows, overwrite the active Director draft, or mutate model configuration.

## 4. Efficient parallel execution strategy

Parallelize by **stable file ownership and contract boundaries**, not by broad feature labels. Do not let multiple branches edit `webui/static/index.html`, `webui/static/js/app.js`, `model_config.py` or the settings HTTP handlers concurrently.

### Phase 0 — contract and baseline (single coordinator)

- Freeze current `main` SHA and capture current CI baseline.
- Map exact create-flow DOM, `openCreate()`, draft snapshot/restore, API endpoints and project initialization.
- Add/confirm config preservation regression tests before any UI work.
- Define the route/view contract and project-defaults schema in this document or a small dedicated contract document.
- Establish branch file ownership and integration order.

### Phase 1 — parallel workstreams

| Workstream | Branch | Exclusive ownership | Deliverables | Must not change |
|---|---|---|---|---|
| A. Project home and create experience | `work/product-project-entry` | Project-home and create-flow JS/CSS; shared index markup only in a single reserved integration window | Project-first hierarchy, create wizard/page, project resume path | model config, provider registry, settings API, FilmDocument authority |
| B. Settings information architecture | `work/product-settings-ia` | Settings-specific JS/CSS and settings markup in a separate reserved integration window | Categorized settings, concise role summaries, progressive disclosure | model_config persistence, settings endpoint semantics, role/provider IDs |
| C. Persistence and lifecycle regression | `work/product-persistence-guards` | Tests and test fixtures; backend contract only when an explicit failing test proves necessary | Config retention, project defaults compatibility, draft isolation, create/refresh recovery tests | visual markup and styles |
| D. Shared shell integration | coordinator-owned, not an independent competing branch | `index.html`, `app.js`, route/view shell and final integration | Merge A/B navigation slots and preserve one route state | no secret/storage migration |

**Concurrency rule:** A and B may develop their isolated JS/CSS modules in parallel, but because the current HTML shell is a shared file, their markup changes must be applied in separate short integration windows or after extracting stable component/partial boundaries. C can run in parallel against the frozen contracts. Avoid long-lived branches that repeatedly merge each other's unfinished UI work.

### Phase 2 — integration order

1. Merge persistence/lifecycle guard tests first; resolve any existing baseline failures without weakening assertions.
2. Integrate the project home/create experience.
3. Integrate settings IA without changing its backend contracts.
4. Integrate shared navigation and route state.
5. Add project-defaults persistence only after the storage schema and backward-compatibility tests are agreed.
6. Run scoped tests per workstream, then full main CI and browser E2E on the exact integrated SHA.
7. Verify local config preservation on a seeded fixture and, on the user's machine, perform a non-destructive backup/check before any release that touches configuration code.

### Why this is more efficient than many branches

- The project and settings UI can progress independently once the shared shell contract is frozen.
- Persistence tests protect the highest-risk boundary without waiting for visual implementation.
- One coordinator owns the small number of shared files, preventing repeated merge conflicts.
- Each branch has measurable acceptance evidence and an explicit no-touch list.
- CI is run at the workstream boundary and again on the exact integrated main SHA, not redundantly after every cosmetic commit.

## 5. Required acceptance gates

### Local API/configuration safety — BLOCKING

- [ ] Existing local `model_config.json` is not deleted, renamed, moved or rewritten by navigation/render-only operations.
- [ ] Every pre-existing file-backed role key survives unrelated role edits.
- [ ] Environment-backed configuration retains precedence and is not copied into local files or browser payloads unexpectedly.
- [ ] Saved key values are never returned to the browser.
- [ ] Import preserves existing local keys; export contains no keys.
- [ ] Legacy environment fallbacks and role/provider/capability IDs remain compatible.
- [ ] Any intentional config write is user-triggered, explicit, and limited to the selected role/fields.

### Project lifecycle and context — BLOCKING

- [ ] Existing projects open without migration or data rewrite.
- [ ] Projects lacking new defaults fields receive defaults in memory without destructive backfill.
- [ ] Create/cancel/retry cannot leak or overwrite the active draft.
- [ ] Project switch and refresh discard stale async responses.
- [ ] `film.html` remains the sole authority once present; no stale CompositionIR fallback.
- [ ] Project settings never mutate global model/API configuration.
- [ ] Project data, manifest, SQLite records and generated assets remain intact.

### UX and navigation

- [ ] Project list is the default landing view.
- [ ] New project is a first-class primary workflow, not a cramped settings-adjacent modal.
- [ ] Settings is secondary and categorized; advanced provider fields are progressively disclosed.
- [ ] HyperFrames is grouped under rendering, not AI model roles.
- [ ] TTS and stock sources are configured in settings but used in the workbench/library.
- [ ] Project output profile is visible and editable at project scope.
- [ ] Keyboard, browser refresh, back/close and failure recovery behavior is deterministic.

### CI

- [ ] Workstream scoped tests pass.
- [ ] TypeScript/build checks pass where touched.
- [ ] Browser E2E covers project home, create/cancel/resume, settings expand/save/import/export.
- [ ] Full main CI passes on the exact integrated SHA.
- [ ] No test is weakened or skipped to conceal a regression.

## 6. Audit gaps / items to trace before implementation

The following are not yet fully established by this pass and must be traced before modifying them:

- Full `openCreate()` modal DOM, all create options, validation, cancellation and project-creation API call chain.
- Complete settings load/save/export endpoints and their tests, including cache invalidation and multi-process behavior.
- Current `window.UI` initialization and whether route state survives reload or is intentionally session-only.
- Full browser draft persistence contract and interaction between create flow and active draft.
- Project creation's exact storage write sequence and rollback behavior on partial failure.
- Existing test coverage for `model_config.save_all()` whole-file replacement and `__KEEP__` behavior.
- Whether project defaults already exist in project params or CompositionIR and how those values are reconciled with `film.html` dimensions/FPS.

No assumptions about these gaps should be converted into code changes until verified.

## 7. Recommended next actions

1. Complete the create-flow and settings-endpoint trace listed above.
2. Add config preservation and create/draft isolation tests before visual changes.
3. Create the three scoped work branches from the same audited main SHA.
4. Implement the project-entry and settings UI in parallel with strict file ownership.
5. Integrate serially through the coordinator-owned shell and run full CI.

**Non-negotiable:** this is an information-architecture and interaction redesign, not a credential-storage migration. Existing local API configuration must remain usable without re-entry.


## 8. Call-chain audit closure (2026-11-07)

### 8.1 New-project / Director submission chain

Confirmed call chain:

1. Top-bar `+ 新建项目` calls `openCreate()` in `webui/static/js/create.js`.
2. `openCreate()` mounts the create form, awaits `createLoadDefaults()`, installs lifecycle handlers and calls `restoreDraft()`.
3. If opening an existing project for a new creation, the project-prefill path calls `POST /api/director/brief`, captures a prefill baseline and guards stale responses / restores the previous draft on failure.
4. `createProject()` validates the brief and source bundle, uploads selected assets to `/api/assets/upload` (and optional narration through the asset staging path), then creates a conversation through `POST /api/director/conversations`.
5. It submits the normalized DirectorInputBundle and project-specific request options to `POST /api/director/create`.
6. The server delegates to `video_director.director_service.create_director_run()`; the service validates source and render-engine availability, allocates a `web-<id>` project ID when one was not supplied, normalizes the output profile, then calls `workflow_run.run_director_create_async()`.
7. The returned run is linked to the conversation. Only after successful task submission does the client clear the active draft, set the current project ID and switch the create surface to progress mode. The actual project/task lifecycle is owned by the workflow service, not by the visual dialog.

**Design consequence:** the create surface can be promoted to a full-workspace experience without introducing a second project-creation backend or a duplicate project state machine. Preserve the existing DirectorInputBundle, conversation, WorkflowRun, asset-upload and prefill contracts.

### 8.2 Draft persistence and recovery chain

Confirmed behavior in `webui/static/js/create.js` and `video_director/director_history.py`:

- Browser-local snapshot key: `video-director:create-draft:v1`.
- Server snapshot endpoint: `/api/director/drafts/active`.
- Server persistence: SQLite `director_history.db`, table `director_drafts`; conversation and messages use separate tables.
- Snapshot includes form fields, explicit user-changed fields, asset/reference/article/software-project context, project ID, prefill baseline and disclosure state.
- Changes are locally snapshotted immediately and server sync is debounced by 450 ms. Server writes are serialized through a promise chain.
- Restore compares local saved time with server `updated_at`, chooses the newer snapshot, merges dirty fields and independent context sources, then syncs a local-newer snapshot to the server.
- While a run is active, persistence is blocked for the hidden form; a successful submission clears the draft only after a run ID and conversation link exist.
- Close/cancel, project switching, history restoration and failed project prefill have explicit guards and rollback behavior.

**Design consequence:** the full-workspace conversion must retain the same form host IDs and lifecycle hooks until the tests prove a safe component migration. A page transition must not clear the local snapshot or replace the active context object while uploads are in flight.

### 8.3 Settings save / export / import chain

Confirmed behavior:

- `GET /api/model_config` returns `status_for_frontend()`; this omits API key values and exposes role/provider/capability status.
- `GET /api/settings/export` exports the role metadata/model/base URL structure; no secret value is returned.
- `POST /api/settings/import` accepts a `models` list and uses `__KEEP__` for credentials of explicitly imported roles.
- `POST /api/model_config` accepts a `configs` object. The front end uses `__KEEP__` for an unchanged saved key; an empty key is an explicit clear.
- `model_config.save_all()` writes the whole file at `composer_storage_root()/model_config.json`.

**Defect found and corrected in workstream C:** both POST routes previously passed only the submitted roles to the whole-file `save_all()` writer. A partial category save or partial import could therefore erase roles omitted from the request. The persistence branch introduces `save_role_updates()`, which merges the partial role/field update with freshly read persisted values under the config lock, retains omitted roles and fields, preserves `__KEEP__`, honors an explicit empty-key clear, and rejects malformed existing JSON rather than replacing it. The HTTP save/import handlers and the legacy `llm_config.json` startup migration now use this safe update path.

Regression coverage includes:
- partial role update preserves other saved role keys;
- partial import preserves unlisted roles;
- explicit empty key clears only the selected role;
- malformed existing config is not overwritten;
- concurrent partial role updates do not erase each other;
- legacy LLM config migration does not erase other local roles;
- omitted fields in a partial role update retain their persisted values;
- frontend status omits key values;
- HTTP save/import routes preserve unsubmitted local credentials.

### 8.4 Confirmed audit disposition

| Audit area | Result | Implementation implication |
|---|---|---|
| Project home | Existing default project pane and top-level action confirmed | Improve prominence; no shell rewrite |
| Create flow | Existing Director workflow confirmed | Promote existing form; do not fork workflow backend |
| Draft recovery | Browser + server durable snapshots confirmed | Preserve IDs, keys and transactional guards |
| Settings save | Whole-file overwrite hazard confirmed | Fix backend update contract before category split |
| Export/import | Secret redaction and import path confirmed | Preserve key-free export and local key retention |
| Project context | Revision-pinned FilmDocument authority confirmed | Reuse current workspace context; no parallel timeline state |
| URL routing | Only in-page `switchTab` confirmed in inspected shell | Do not add router dependency as part of the first UX pass |

The local API configuration on the user's physical machine remains **not inspected**. All credential-preservation tests use isolated temporary storage with synthetic keys. The code change is designed to preserve existing values; it is not a claim that a machine-level backup or local-file byte comparison has been performed.
