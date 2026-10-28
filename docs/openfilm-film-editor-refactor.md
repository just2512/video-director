# OpenFilm-inspired Film Document and Studio Refactor

This is the first integrated vertical slice, not a claim of complete OpenFilm or Jianying parity.

## Authority and compatibility

- Before explicit migration, legacy projects continue using CompositionIR and the existing renderer.
- Explicit migration creates film.html and a separate undo history. It refuses to overwrite an existing film.html.
- After migration, the new Studio edits film.html by stable clip IDs. It does not silently write edits back to CompositionIR.
- Existing content creation, asset catalog, generation, voice, and render workflows remain installed; they are not automatically bidirectionally synchronized with migrated documents.

## Current document and operations

The document is parsed into stage, tracks and clips. Editing supports set, move, split, insert, remove, plus locked/hidden/muted track flags. Operations are validated as a batch, atomically written, and stored for revision-checked undo/redo. A stale base is rebased on the latest valid document by stable IDs; missing target IDs fail rather than redirect an edit. Broken documents are not overwritten.

## Migration behavior

Video/image/audio, supported overlay clips, subtitle cues, static 2D transforms, supported frame-driven channels (x/y/scale/rotation/opacity/blur/offset_x/offset_y), clip-relative volume envelopes, and a constrained subset of top-down orthographic camera tracks are mapped into the FilmDocument. Composition-space polygon masks, including supported animated mask_path keyframes on transformed clips, use a stage-sized clipping wrapper so the mask is not accidentally transformed with the media element.

The mapping is deliberately capability-gated rather than pretending every field is equivalent. Perspective cameras, camera follow/constraints, changing 3D camera direction, inverted or feathered masks, path masks whose Bézier/open-path semantics are absent from the source schema, unsupported easing or transitions, some watermark/subtitle style details and unknown future schema fields remain in the exact migration/source-composition.json archive with explicit warnings. A warning is not a converted feature; users must review and explicitly accept unresolved semantics before initialization.

When a Composition has multiple variants and no valid active_variant_id, migration is blocked until the Studio selector chooses a specific version. An explicit selection is previewed through the same builder and must be supplied again when initialization runs. Other variants remain available in the archived source; they are not silently merged into one timeline. A stale/unknown variant ID is rejected.

## Legacy-source isolation

- Presence of film.html is the migration switch, even if that document is currently malformed. Once present, legacy CompositionIR command writes, direct saves and CompositionIR-based draft/final rendering are rejected with FILM_DOCUMENT_AUTHORITATIVE (HTTP 409 at the WebUI boundary).
- The new Studio remains editable against film.html; it never silently writes edits back to composition.json. The legacy renderer is deliberately blocked rather than allowed to export an old timeline.
- The legacy renderer remains blocked; final MP4 export now has a separate film.html-backed workflow described below.

## Film-backed MP4 export

- The Studio Export MP4 button submits the current film revision to a background WorkflowRun; a revision mismatch returns HTTP 409 before rendering starts.
- The renderer serves project/shared media through a loopback-only HTTP server, renders each requested timestamp through the same browser runtime, and uses FFmpeg to encode screenshots and mix available video/audio source streams. Source ranges, media duration, deterministic subtitle-page timing, video stream and duration are validated.
- Project-local `.html/.htm` iframe scenes are served through an injected bridge that executes `window.film.frame(t)`, awaits `window.film.ready`, pins the page clock/random source, advances queued animation-frame callbacks at the requested timestamp, and returns an exact `film-frame:ready` acknowledgement. A page without the contract fails the actual frame request with the clip ID; it is not silently rendered as a static screenshot.
- Relative `src`, `href`, `poster`, and CSS `url(...)` references in a scene are rewritten to the project-scoped media endpoint. External URLs remain unchanged in preview, but the offline render/look request guard blocks them and returns `EXTERNAL_SCENE_RESOURCE`; move required resources into project storage and reference them locally. UTF-8 is required for HTML/CSS scene files.
- The generated subtitle page retains its legacy `film-time:ready` contract. The bridge is a compatibility boundary, not a promise that arbitrary async frameworks, remote data, WebGL state, or third-party animations are automatically deterministic; unsupported pages must be converted to the explicit frame contract or rejected during render.
- Single export is limited to 600 seconds, 18,000 frames and stage edges up to 3840 px. This is a constrained local export adapter, not full feature parity with OpenFilm's injected deterministic runtime or every CSS/transition feature.

## Executed foundation milestone

- The repository contract distinguishes legacy CompositionIR projects from active film.html projects. A migrated project has one authoritative physical timeline; legacy writes and exports remain blocked.
- FilmDocument edits use a source-span patch path for both attribute changes and structural edits. Existing untouched clip/track nodes retain their raw markup; insertions and cross-track moves add only the necessary nodes, while deletion removes only the selected node or track range. The complete result is reparsed before committing.
- Ambiguous source spans still return to a guarded fallback path; regression tests cover split and insert while preserving unrelated clip bytes and comments.
- A regression test covers property editing while proving an untouched clip and neighboring comments remain byte-for-byte present.

## AI proposal/preview/apply milestone

- The Studio AI command endpoint now returns a validated candidate proposal and candidate preview payload without writing film.html.
- Applying a proposal requires an exact matching base revision inside the document write lock; stale proposals are rejected rather than rebased. The approved operation set lives in a short-lived, single-use server-side registry tied to project and revision; client-supplied operation arrays are ignored.
- The Studio can preview, accept, or reject a proposal. Rejection changes neither the document nor its revision. Editing manually or receiving a newer external revision invalidates the pending proposal.
- Document revision changes and AI planning phases use the repository's existing SSE event bus; file polling remains the fallback for external local-agent writes that do not pass through the server.
- Token streaming is implemented in the code path: the OpenAI-compatible provider requests stream=True, forwards delta.content fragments as server-side SSE token events, and the Studio consumes them. Automated tests use a fake upstream stream to verify fragment ordering, concatenation, and cancellation; they do not establish that a real configured model delivers incremental text through a proxy. `tests/test_film_studio_live_ai_streaming.py` now provides an opt-in end-to-end check using `VD_LLM_API_KEY`, optional `VD_LLM_BASE_URL` and `VD_LLM_MODEL`, and `VD_LLM_LIVE_STREAM_TEST=1`. It explicitly pins the test process to the `VD_LLM_*` environment override (so a saved `model_config.json` cannot redirect the test), requires at least two non-empty token events before the final result, verifies the requested `move` proposal for `clip-b`, and confirms proposal generation does not modify `film.html`. Error diagnostics redact the configured API key. The test is skipped by default, so ordinary CI reports live-model streaming as MISSING rather than inferring it from mocked SSE. An earlier isolated run [40637515525](https://github.com/just2512/video-director/actions/runs/40637515525) passed the token-stream test at that time, but that result does not establish present-day endpoint availability. The explicit current-main recheck [40689393487](https://github.com/just2512/video-director/actions/runs/40689393487) attempted a real request and was rejected: the configured `z-ai/glm-5.3` model returned HTTP 410 and was declared retired at `2026-10-28T08:00:00Z`. Its full normal pytest/Chromium suite still passed (1364 passed, 1 skipped, 0 failed), but the live gate failed and release publication was correctly withheld. Reconfigure the runner's preserved model to a currently supported endpoint, then repeat the same opt-in smoke; never count lifecycle-only events or historical live evidence as a current token-stream PASS.

## Clip-relative volume envelope

- `volume_envelope` is a JSON array in the clip's own timeline seconds: `[{"t":0,"v":1},{"t":2,"v":0.2}]`. Gain `v` is a 0–1 multiplier over the clip volume fader; it is not a decibel value.
- The Studio draws the envelope over audio/video clips. Normal mode keeps clip moving available; the dedicated “音量曲线：开” mode lets clicking insert a point, dragging adjust time/value, and Shift/right-click remove an interior point. A completed gesture commits one operation and one undo step.
- Alt-drag on a video/audio clip performs a source slip: timeline start/length remain invariant while `#t=in,out` shifts. The inspector also has 0.5×/1×/1.5×/2×/4× presets, which stage the value and still submit through the standard validated Apply Properties path.
- Browser preview evaluates the same piecewise-linear curve against clip-local time; MP4 audio uses FFmpeg's per-frame volume expression. Split, trim and ripple-delete operations slice/rebase the envelope, preserving gain continuity at edit boundaries. Invalid JSON, duplicate timestamps and out-of-range gain values are rejected before write/render.

## Global ripple-delete operation

- The unified operation engine supports explicit `ripple_delete`: it removes the selected finite timeline interval and closes the gap across all tracks.
- Clips crossing the cut retain their remaining source ranges; a crossing clip is split into source-contiguous left/right segments. The normalized operation records generated right-side clip IDs so proposal preview and confirmed apply produce the same document.
- Affected locked tracks and ambiguous unknown-duration clips reject the whole transaction. The edit remains revisioned and undoable through the same FilmDocument history.
- Roll, Slip and Slide are implemented as separate validated operations with pointer gestures. Roll is restricted to same-source continuous cuts with equal playback speeds; Slide is restricted to a same-source, equal-speed three-clip run.

## Clip fade in/out

- The clip attribute `fade="in_seconds,out_seconds"` is validated by FilmDocument, restricted to supported media elements, and rejected when total fade duration exceeds clip duration. Split, trim, insert-ripple and ripple-delete preserve only the retained outer fade; any cut inside an active fade interval is rejected to avoid changing the visible/audio curve silently.
- Studio Inspector edits both durations as one atomic field operation. Browser preview applies the same clip-relative multiplier to visual opacity and audio gain; MP4 export uses FFmpeg `afade` after source trim and speed processing.
- This remains a two-sided linear fade rather than a general opacity keyframe/Bezier transition system.

## Slip source-bound validation

- A slip changes only the source `#t=in,out` range, keeping the timeline start and duration fixed. Before the edit is written, the server probes the actual media file, verifies the expected stream and rejects an unknown or out-of-bounds source duration. This prevents a syntactically valid slip from leaving a clip that fails only at export.

## Roll trim

- Ctrl-drag a trim handle at an adjacent same-source cut to slide the edit point without changing combined duration.
- The backend rejects nonadjacent, different-source, locked-track, faded, or invalid-duration operations. Source ranges are changed together, clip-relative volume curves are cropped/extended, and the edit is one undoable operation.
- Only same-source continuous cuts are supported in this first pass; rolling between distinct source assets requires media-probe boundary checks.

## Insert edit and sync lock

- Studio's `插入编辑` mode places a selected library asset into the selected clip's track at the playhead. It inserts a gap equal to the new clip's timeline duration rather than overlapping existing clips.
- The target track always ripples. Other tracks participate only when their `sync-lock` flag is enabled; a locked participating track rejects the whole operation. Clips crossing the insertion point are split at a source-aware boundary, with clip-relative volume envelopes sliced/rebased.
- Unknown-duration clips that start before the insertion point are rejected because the editor cannot prove whether they cross it. Turning insert mode off preserves the existing behavior of creating a separate new track.

## Keyboard split shortcut

- Press `S` in the Studio to split the selected clip at the playhead. The shortcut is ignored while typing in input, textarea, select, or contenteditable controls, and key-repeat is suppressed to avoid accidental repeated cuts.
- The toolbar names the same shortcut; Playwright exercises both button-driven split and keyboard-driven split in the real Studio workflow.

## Roll/Slide/Slip browser regression

- Playwright opens a real film.html project in the Studio, uses the Split button, then Ctrl-drags the real trim handle. It verifies both the visible interaction and the persisted source in/out points plus the right segment timeline start. The fixture retains the cut's combined duration.

## Slide trim

- The dedicated slide-mode toggle (or the Alt+Shift-drag shortcut) moves a middle video/audio clip on the timeline while adjusting the immediate left and right neighbours; the left source out-point changes and the right source in-point changes, but the outside timeline bounds remain fixed.
- The first pass rejects gaps, non-identical sources, discontinuous source ranges, mixed media types, different playback speeds, locked tracks and clips with fades. This is intentionally narrower than a full NLE slide tool because media-handle availability is not yet probed for arbitrary sources.
- The gesture previews all three clips together and commits one undoable `slide` operation on pointer release. AI planning may request the same validated operation.

## Through-edit / join-cut milestone

- A new `join` operation heals a split only if the clips are adjacent on the same unlocked track, use the same source with contiguous source ranges, and agree on speed and all other clip-level attributes.
- It keeps the left clip's stable ID, extends its source out-point, removes the right node, and merges clip-relative volume-envelope curves only when gain is continuous at the cut. Fades and incompatible automation are rejected instead of silently flattened.
- The Studio exposes “愈合切口” beside split; it proposes the adjacent cut, while the backend remains the authority for source/time/attribute checks.

## Render consistency milestone

- The final browser renderer re-renders representative first/middle/last timestamps in reverse order after the frame sequence. Each repeat is compared with the captured PNG using a per-channel tolerance of 32 and a bounded changed-pixel allowance; materially different repeats fail with NON_DETERMINISTIC_FRAME.
- The scene/export end-to-end regression samples a 1-second frame-driven scene at 0.00, 0.25, 0.50 and 0.75 seconds, extracts the same four frames from the actual FFmpeg MP4, and compares both the moving/masked element centroid and a frame-driven scene pixel against the matching Chromium look tile. It separately checks the near-endpoint 0.99-second transform/mask state.
- Successful output records determinism_status and the number of sampled timestamps. These are sampled audits, not proof that every frame or arbitrary CSS feature is deterministic; the existing iframe/CSS constraints remain in force.

## Agent-facing CLI and frame-driven scene contract

- `python -m video_director film status --project-id ID` emits the current revision, stage size, duration, tracks and clip IDs as JSON.
- `python -m video_director film validate --project-id ID` runs the strict document parser and checks that every clip's local media reference resolves to an existing file. It does not claim that an HTML scene is renderable merely because the document parses; `film look` and `film render` exercise that contract in a real browser.
- `python -m video_director film diff --project-id ID [--from-revision REV]` prints a unified text diff against a retained history snapshot. Without an explicit revision it compares the last edit's before-snapshot with the current document.
- `python -m video_director film look --project-id ID --revision REVISION_FROM_STATUS [--times 0,1.5,4]` requires the exact revision returned by `film status`, renders a contact sheet from the same browser runtime as export, visits samples in reverse order, and writes the PNG under `.film/cache/look/`. A missing/blank revision is rejected before browser or cache side effects; a stale revision fails with `REVISION_CONFLICT`; a materially different repeat fails with `NON_DETERMINISTIC_FRAME`.
- `python -m video_director film render --project-id ID --expected-revision REVISION_FROM_STATUS [--fps 30]` requires the same explicit revision that was inspected. It never silently substitutes the latest version; missing/blank or stale pins are rejected before export side effects, and `rendered_revision` records the version associated with the resulting MP4.
- Real Chromium regression exercises the injected frame bridge with a sandboxed iframe and verifies exact local time, frame identity, support acknowledgement, and an actual call to the authored `frame(t)` implementation.

## Direct HTML canvas editing and Agent context

- The Studio canvas picker can select an element inside the active HTML iframe. The scene bridge reports a bounded CSS selector, local viewport rectangle, supported computed styles, and whether replacing the element's whole text is safe.
- The user can move an element or drag its edges to resize it directly in the preview. The gesture is previewed in the scene, then committed to the clip's selector-based `overrides` as one validated FilmDocument operation. The Inspector exposes text, position, dimensions, text colour, optional background fill/clear, opacity, font weight, text alignment, font size and rotation. Existing computed values are compared before emitting the newly supported style fields, so simply moving an element does not silently materialize unrelated background/opacity/weight/alignment settings.
- The server accepts `element_set` only for an unlocked iframe clip and validates the selector, text length, and restricted style properties/values. Existing override entries are merged by selector, while unrelated legacy entries are preserved. Nested elements are prevented from whole-node text replacement.
- When the user directs AI while an element is selected, the Studio includes that element's clip ID, selector and bounded context together with the exact base revision. The planner may propose `element_set` only for that selected clip and selector. A stale revision or mismatched target is rejected.
- This is selector-based editing, not a general-purpose browser DOM inspector: arbitrary script execution, stylesheet imports, external CSS URLs and unsupported style properties are not enabled by the element override contract.

## Agent authoring contract and offline resource boundary

- `AGENTS.md` routes coding agents to `docs/FILM_AGENT_MANUAL.md`; the manual documents the single-timeline authority rule, the project format, `window.film.frame(t)`, resource readiness, CLI workflow, ID/revision handling and honest failure reporting.
- `film validate` checks the strict document plus referenced local file existence; it explicitly does not certify frame determinism. `film look` uses the real browser runtime, generates a contact sheet, repeats sample times in reverse order and fails with `NON_DETERMINISTIC_FRAME` when samples materially differ.
- The actual Playwright render/look page blocks requests outside its loopback project server (while allowing `data:`, `blob:` and `about:`). Any blocked network dependency becomes `EXTERNAL_SCENE_RESOURCE` with the affected URLs rather than silently rendering incomplete content.
- The Studio's horizontal timeline dock separator supports pointer and keyboard resizing. Panel widths and timeline height are persisted locally; dock resizing is covered by a real-browser interaction test.

## Agent file-watching browser regression

- Playwright opens the real Studio, observes its SSE connection, externally changes a valid clip speed and verifies the Inspector hot reloads. It then writes a broken partial document and verifies the broken-state alert, followed by a repaired document that must clear the error and hot reload again.

## Agent file-watching milestone

- A standard-library watcher registers projects viewed by Studio, debounces file changes for at least 120 ms, validates a stable text snapshot, then emits revision_changed/document_broken events on the existing SSE bus.
- Invalid Agent writes freeze the editor; a repaired valid revision can be loaded again. The watcher discards inactive project registrations after a bounded idle period.
- Model-token streaming and simultaneous multi-user editing are not claimed by this local single-user watcher.

## Audio waveform visualization

- Audio timeline clips display a cached waveform derived from decoded media samples and sliced to the actual source in/out range.
- Decoding is limited to 8 MiB assets and 600-second audio. Unsupported or oversized media reports that the waveform is unavailable without blocking edits; editable keyframe envelopes remain separate work.

## Current boundaries

- The Studio provides a browser-rendered multi-track monitor, multi-track timeline, drag/move, source-aware in/out trimming, Alt-drag source slip, Ctrl-drag same-source roll trim, Alt+Shift-drag slide trim, split/join-cut, delete/ripple-delete, clip fades, clip-relative volume envelopes, an explicit slide-trim mode toggle, speed presets, track create/empty-track removal, lock/hide/mute flags, undo/redo, AI operation planning (set/move/split/trim/slip/roll/slide/join), and revision polling. Left/right panel widths and timeline height persist in localStorage; the horizontal timeline seam supports pointer dragging and ArrowUp/ArrowDown keyboard resizing.
- The MP4 adapter samples the shared browser runtime frame-by-frame, then lets FFmpeg encode the frames and mix audio source intervals. Generated subtitle pages use their legacy acknowledgement; local HTML scenes must implement `window.film.frame(t)` and return a successful frame acknowledgement. Unsupported scenes fail explicitly rather than exporting an unverified static frame.
- Full lossless migration of every CompositionIR field is not claimed. Migration warnings are explicit.
- The React/FilmStore path now owns timeline gesture context, envelope pointer lifecycle and preview, snap/drop decorations, waveform/envelope rendering, mode state and clip context-menu presentation. The shared adapter still owns source-aware edit math and revision-checked FilmOperation submission; no-bundle fallback retains native behavior.
- Real-browser tests now cover roll, slide and slip previews/commits, cancellation, external revision conflict during an envelope gesture, plus Bezier easing in both browser look samples and encoded FFmpeg frames. This is evidence for these specified fixtures, not a claim of all-frame determinism for arbitrary HTML/WebGL scenes.
- After migration, do not continue editing the same project in the legacy editor unless a deliberate bridge migration is added.

## React 19 / Tailwind 4 Studio integration boundary

The Studio is being moved incrementally to typed React components instead of swapping the entire working editor in one rewrite. React/FilmStore now own timeline UI state, waveform/envelope painting, context-menu surfaces, gesture-preview geometry and snap/drop indicators; the native adapter still computes source-aware trim/roll/slide/slip/move operations and routes pointerup through FilmDocument revision validation, undo/redo and atomic writes. The active clip-gesture context is a typed, ID-only FilmStore snapshot; DOM references are kept only in the no-bundle fallback closure. Other React surfaces mount behind stable bridge contracts. Tailwind 4 is scoped to the React surfaces and deliberately omits Preflight so it cannot reset the existing dock/timeline styles. This is an integration milestone, not a claim that the entire Studio shell or every timeline interaction has completed a full React rewrite.


## Previous verified mainline release (2026-10-28, superseded by later source regression)

- [AI Director `40657485729`](https://github.com/just2512/video-director/actions/runs/40657485729) completed SUCCESS on source `77f1cd4`: 1362 passed, 1 skipped, no failed tests; the curated timeline/browser/export release gate completed with 291 passed and no failures. React/TypeScript, React bundle, Tailwind CSS, Studio smoke and JavaScript syntax checks all passed.
- The workflow then published verified `webui/static/js/film-studio-react.js` and `webui/static/css/film-studio-tailwind.css` in commit [`036ea8e`](https://github.com/just2512/video-director/commit/036ea8e5bebef40be98612431d02f6aed2ab1d31). Independent [Studio browser smoke `40657485766`](https://github.com/just2512/video-director/actions/runs/40657485766) also completed SUCCESS: 327 passed, 2 warnings, 0 failed.
- The default AI run's live-provider check is MISSING because it was intentionally skipped to avoid an unrequested paid call. The explicit run above supplies separate evidence of real token fragments; it is not evidence that every default CI run rechecks the provider.


## Latest mainline acceptance (2026-10-28, source `1cd42bc`)

- [AI Director `40685008083`](https://github.com/just2512/video-director/actions/runs/40685008083) SUCCESS: 1364 passed, 1 skipped, no failures; curated timeline/browser/export gate 293 passed, 1 warning, no failures. React/TypeScript, Tailwind, integration smoke and JavaScript syntax checks passed.
- [Independent Studio `40685008091`](https://github.com/just2512/video-director/actions/runs/40685008091) SUCCESS: 329 passed, 2 warnings, 0 failures.
- Bundle publication step verified React Studio bundle, Tailwind CSS and the package lock were already current; it intentionally created no new artifact commit. The committed bundle remains the gate-verified `036ea8e`.
- The authority hard gate now also rejects duplicate `/api/editor/film/initialize` calls, old `composition_version()` reads and legacy proposal HTTP endpoints when `film.html` exists. Normal CI reports live-provider streaming as MISSING unless explicitly opted in.


## Latest mainline revision-pin acceptance (2026-10-28, `5081a943`)

- [AI Director regression `40696547029`](https://github.com/just2512/video-director/actions/runs/40696547029) completed **SUCCESS** on `5081a943`: **1375 passed, 1 skipped, 0 failed, 6 warnings** across the full pytest/Chromium suite. The curated timeline/browser/export publication gate also passed: **295 passed, 1 warning, 0 failed**. TypeScript/React, Tailwind, integration smoke and JavaScript syntax checks passed.
- [Independent Studio UI/browser smoke `40696547133`](https://github.com/just2512/video-director/actions/runs/40696547133) completed **SUCCESS**: **331 passed, 2 warnings, 0 failed**.
- The build step verified `webui/static/js/film-studio-react.js`, `webui/static/css/film-studio-tailwind.css` and the dependency lock are already current; no duplicate bundle commit was created. The validated bundle remains `036ea8e`.
- CLI/MCP `film look` and `film render` now require a non-empty revision pin. Regression tests prove missing/blank/stale pins are rejected before cache/render artifacts are written. The full-pipeline MP4 E2E now passes `--expected-revision` from the immediately preceding `film status` snapshot rather than relying on an implicit latest revision.
- This default CI intentionally reports `LIVE_STREAM_ACCEPTANCE=MISSING` because no paid provider call was opted in. The separate actual provider run `40689393487` returned HTTP 410 for the configured `z-ai/glm-5.3`; that remains a provider-configuration blocker, not a code PASS. No model key or preserved runner configuration was read out or changed.
