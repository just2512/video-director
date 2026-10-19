# OpenFilm-inspired Film Document and Studio Refactor

This is the first integrated vertical slice, not a claim of complete OpenFilm or Jianying parity.

## Authority and compatibility

- Before explicit migration, legacy projects continue using CompositionIR and the existing renderer.
- Explicit migration creates film.html and a separate undo history. It refuses to overwrite an existing film.html.
- After migration, the new Studio edits film.html by stable clip IDs. It does not silently write edits back to CompositionIR.
- Existing content creation, asset catalog, generation, voice, and render workflows remain installed; they are not automatically bidirectionally synchronized with migrated documents.

## Current document and operations

The document is parsed into stage, tracks and clips. Editing supports set, move, split, insert, remove, ripple_remove (same-track downstream shift), plus locked/hidden/muted track flags. Operations are validated as a batch, atomically written, and stored for revision-checked undo/redo. Every edit requires a base revision. A stale request may rebase only when the touched fields/objects are unchanged in the current document; same-field or structural conflicts return 409 without writing. Missing target IDs fail rather than redirect an edit. Broken documents are not overwritten.

## Migration behavior and field contract

Mapping is loss-aware: fields are either executed by the film runtime, retained as provenance metadata, or rejected as blockers. Metadata retention alone is not treated as equivalent rendering behavior.

| CompositionIR field | film.html representation | Current contract |
|---|---|---|
| `project.width/height` | Native stage dimensions | Mapped and validated |
| `project.fps`, `style_id`, template lineage, variants, references and project metadata | `video-director-project-meta` in `<head>` | Round-tripped as metadata; export FPS remains an explicit render parameter |
| Scene identity/order/start/duration | Scene inventory plus stable clip bindings, `at`, and source range | Supported for bound clips; duration grows if a clip extends a scene |
| Scene semantic role/intent | `data-vd-scene-meta` plus scene inventory | Preserved as metadata; camera/beat/selection timing/entrance/exit/continuity execution remains blocked |
| Main video/image identity, asset ID, relative source path, time range, speed, fit mode | `data-vd-*`, `src#t=in,out`, `at`, `speed`, CSS `object-fit` | Supported; path/type changes are rejected |
| Main layer position, scale, anchor, rotation, opacity | CSS geometry, `transform`, `transform-origin`, `opacity` | Supported for nine anchors and uniform scale; constrained CSS is executed by the runtime |
| Layer lock | Bound scene-layer track `locked` flag | Supported |
| Keyframes, mask, motion, animation, motion recipe, text/gap/custom layer types | No equivalent execution mapping | Hard migration blocker |
| Visual overlay identity/order, source path/type, timing, speed, fit, rectangle and opacity | Bound overlay tracks + CSS geometry; source-array index retained while physical order is reversed for z-stacking | Static overlays supported; transform, nonzero `z_index`, or embedded source audio blocks migration |
| Audio clip/lane identity, source interval, timeline timing, speed, volume/mute, lane state | Audio clip attributes and lane metadata | Independent clips supported; follow-source, linked overlay, fades, ducking and keyframes block migration |
| Subtitle cues/style | Editable cue/style attributes bound to the generated subtitle iframe | Cue/style values reverse-map; edits regenerate the page and undo/redo restore it; independent page edits are rejected; animation blocks migration |
| Watermarks, camera tracks, style contract, non-cut transitions | Original values can be retained in metadata | Runtime equivalence is not implemented; hard migration blocker |
| Referenced assets | Sanitized metadata plus root-relative `file_uri` | Recovered from CompositionIR or project manifest; missing metadata/path is a hard blocker |

Reverse conversion is exposed read-only as `GET /api/editor/film/composition?project_id=...` and returns a model-validated CompositionIR payload. Unbound clips, source mismatches, unknown CSS, duplicate bindings, conflicting lane states, unsupported physical-track reorder or deletion of the last layer in a scene fail explicitly. It does not overwrite `plans/composition.json`; migrated `film.html` remains authoritative.

Migration rejects any unsupported field. `accept_warnings` cannot bypass the gate.

## Legacy-source isolation

- Presence of film.html is the migration switch, even if that document is currently malformed. Once present, legacy CompositionIR command writes, direct saves and CompositionIR-based draft/final rendering are rejected with FILM_DOCUMENT_AUTHORITATIVE (HTTP 409 at the WebUI boundary).
- The new Studio remains editable against film.html; it never silently writes edits back to composition.json. The legacy renderer is deliberately blocked rather than allowed to export an old timeline.
- The legacy renderer remains blocked; final MP4 export now has a separate film.html-backed workflow described below.

## Film-backed MP4 export

- The Studio Export MP4 button submits the current film revision to a background WorkflowRun; a revision mismatch returns HTTP 409 before rendering starts.
- The renderer serves project/shared media through a loopback-only HTTP server, renders each requested timestamp through the same browser runtime, and uses FFmpeg to encode screenshots and mix available video/audio source streams. Source ranges, media duration, deterministic subtitle-page timing, video stream and duration are validated.
- Dynamic arbitrary iframe HTML is rejected unless it participates in the generated subtitle page's frame-time contract. CSS imports, external URLs, URL functions, animations and transitions are rejected for repeatable offline frame export.
- Single export is limited to 600 seconds, 18,000 frames and stage edges up to 3840 px. This is a constrained local export adapter, not full feature parity with OpenFilm's injected deterministic runtime or every CSS/transition feature.

## Current boundaries

- The Studio provides a browser-rendered multi-track monitor, multi-track timeline, drag/move, source-aware in/out trimming, split/delete/ripple-delete, track create/empty-track removal, lock/hide/mute flags, undo/redo, AI operation planning (set/move/split/trim/remove/ripple_remove) and lightweight revision polling with a full-document fetch only after the revision changes. Ctrl/Command+wheel zooms around the pointer; clip dragging uses requestAnimationFrame edge auto-scroll. The Inspector edits visual box geometry (left/top/width/height/rotation/opacity) in stage pixels while clearing legacy normalized geometry attributes in the same undoable batch. The preview overlays a selected visible visual clip with a transform box; dragging moves it, the corner handle resizes it, preview-only styles are sent to the isolated runtime, and pointer release commits one revision. Dock separators persist panel widths.
- The MP4 adapter samples the same browser runtime frame-by-frame, then lets FFmpeg encode the frames and mix audio source intervals. Generated subtitle pages acknowledge their requested time; arbitrary iframe pages are rejected until they implement the same contract.
- Full lossless migration of every CompositionIR field is not claimed. Migration warnings are explicit.
- After migration, do not continue editing the same project in the legacy editor unless a deliberate bridge migration is added.
