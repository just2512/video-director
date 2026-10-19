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

Supported video/image/audio and overlay clips are mapped into tracks. Subtitle cues can be exported to a page-backed track. Fields that are not yet losslessly mapped, including some transforms, keyframes, transitions and watermark types, are reported as migration warnings. The API requires explicit confirmation if warnings exist. The migration manifest stores the source Composition revision and warning list.

## Legacy-source isolation

- Presence of film.html is the migration switch, even if that document is currently malformed. Once present, legacy CompositionIR command writes, direct saves and CompositionIR-based draft/final rendering are rejected with FILM_DOCUMENT_AUTHORITATIVE (HTTP 409 at the WebUI boundary).
- The new Studio remains editable against film.html; it never silently writes edits back to composition.json. The legacy renderer is deliberately blocked rather than allowed to export an old timeline.
- The legacy renderer remains blocked; final MP4 export now has a separate film.html-backed workflow described below.

## Film-backed MP4 export

- The Studio Export MP4 button submits the current film revision to a background WorkflowRun; a revision mismatch returns HTTP 409 before rendering starts.
- The renderer serves project/shared media through a loopback-only HTTP server, renders each requested timestamp through the same browser runtime, and uses FFmpeg to encode screenshots and mix available video/audio source streams. Source ranges, media duration, deterministic subtitle-page timing, video stream and duration are validated.
- Dynamic arbitrary iframe HTML is rejected unless it participates in the generated subtitle page's frame-time contract. CSS imports, external URLs, URL functions, animations and transitions are rejected for repeatable offline frame export.
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
- Model-token streaming is not claimed: the current LLM abstraction exposes a blocking generate call, so this milestone streams lifecycle events, not partial model text.

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

## Roll trim

- Ctrl-drag a trim handle at an adjacent same-source cut to slide the edit point without changing combined duration.
- The backend rejects nonadjacent, different-source, locked-track, faded, or invalid-duration operations. Source ranges are changed together, clip-relative volume curves are cropped/extended, and the edit is one undoable operation.
- Only same-source continuous cuts are supported in this first pass; rolling between distinct source assets requires media-probe boundary checks.

## Insert edit and sync lock

- Studio's `插入编辑` mode places a selected library asset into the selected clip's track at the playhead. It inserts a gap equal to the new clip's timeline duration rather than overlapping existing clips.
- The target track always ripples. Other tracks participate only when their `sync-lock` flag is enabled; a locked participating track rejects the whole operation. Clips crossing the insertion point are split at a source-aware boundary, with clip-relative volume envelopes sliced/rebased.
- Unknown-duration clips that start before the insertion point are rejected because the editor cannot prove whether they cross it. Turning insert mode off preserves the existing behavior of creating a separate new track.

## Roll-trim browser regression

- Playwright opens a real film.html project in the Studio, uses the Split button, then Ctrl-drags the real trim handle. It verifies both the visible interaction and the persisted source in/out points plus the right segment timeline start. The fixture retains the cut's combined duration.

## Slide trim

- Alt+Shift-drag on a middle video/audio clip moves it on the timeline while adjusting the immediate left and right neighbours; the left source out-point changes and the right source in-point changes, but the outside timeline bounds remain fixed.
- The first pass rejects gaps, non-identical sources, discontinuous source ranges, mixed media types, different playback speeds, locked tracks and clips with fades. This is intentionally narrower than a full NLE slide tool because media-handle availability is not yet probed for arbitrary sources.
- The gesture previews all three clips together and commits one undoable `slide` operation on pointer release. AI planning may request the same validated operation.

## Through-edit / join-cut milestone

- A new `join` operation heals a split only if the clips are adjacent on the same unlocked track, use the same source with contiguous source ranges, and agree on speed and all other clip-level attributes.
- It keeps the left clip's stable ID, extends its source out-point, removes the right node, and merges clip-relative volume-envelope curves only when gain is continuous at the cut. Fades and incompatible automation are rejected instead of silently flattened.
- The Studio exposes “愈合切口” beside split; it proposes the adjacent cut, while the backend remains the authority for source/time/attribute checks.

## Render consistency milestone

- The final browser renderer re-renders representative first/middle/last timestamps in reverse order after the frame sequence. Each repeat is compared with the captured PNG using a per-channel tolerance of 32 and a bounded changed-pixel allowance.
- A materially different repeated frame fails the render with NON_DETERMINISTIC_FRAME instead of silently exporting. Successful output records determinism_status and the number of sampled timestamps.
- This is a sampled deterministic audit, not proof that every frame or arbitrary CSS feature is deterministic; the existing iframe/CSS constraints remain in force.

## Agent file-watching milestone

- A standard-library watcher registers projects viewed by Studio, debounces file changes for at least 120 ms, validates a stable text snapshot, then emits revision_changed/document_broken events on the existing SSE bus.
- Invalid Agent writes freeze the editor; a repaired valid revision can be loaded again. The watcher discards inactive project registrations after a bounded idle period.
- Model-token streaming and simultaneous multi-user editing are not claimed by this local single-user watcher.

## Audio waveform visualization

- Audio timeline clips display a cached waveform derived from decoded media samples and sliced to the actual source in/out range.
- Decoding is limited to 8 MiB assets and 600-second audio. Unsupported or oversized media reports that the waveform is unavailable without blocking edits; editable keyframe envelopes remain separate work.

## Current boundaries

- The Studio provides a browser-rendered multi-track monitor, multi-track timeline, drag/move, source-aware in/out trimming, Alt-drag source slip, Ctrl-drag same-source roll trim, split/join-cut, delete/ripple-delete, clip fades, clip-relative volume envelopes, speed presets, track create/empty-track removal, lock/hide/mute flags, undo/redo, AI operation planning (set/move/split/trim/slip/roll/join) and revision polling. Dock separators persist panel widths.
- The MP4 adapter samples the same browser runtime frame-by-frame, then lets FFmpeg encode the frames and mix audio source intervals. Generated subtitle pages acknowledge their requested time; arbitrary iframe pages are rejected until they implement the same contract.
- Full lossless migration of every CompositionIR field is not claimed. Migration warnings are explicit.
- After migration, do not continue editing the same project in the legacy editor unless a deliberate bridge migration is added.
