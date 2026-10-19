# OpenFilm-inspired Film Document and Studio Refactor

This is the first integrated vertical slice, not a claim of complete OpenFilm or Jianying parity.

## Authority and compatibility

- Before explicit migration, legacy projects continue using CompositionIR and the existing renderer.
- Explicit migration creates film.html and a separate undo history. It refuses to overwrite an existing film.html.
- After migration, the new Studio edits film.html by stable clip IDs. It does not silently write edits back to CompositionIR.
- Existing content creation, asset catalog, generation, voice, and render workflows remain installed; they are not automatically bidirectionally synchronized with migrated documents.

## Current document and operations

The document is parsed into stage, tracks and clips. Editing supports set, move, split, insert, remove, ripple_remove (same-track downstream shift), plus locked/hidden/muted track flags. Operations are validated as a batch, atomically written, and stored for revision-checked undo/redo. Every edit requires a base revision. A stale request may rebase only when the touched fields/objects are unchanged in the current document; same-field or structural conflicts return 409 without writing. Missing target IDs fail rather than redirect an edit. Broken documents are not overwritten.

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

## Current boundaries

- The Studio provides a browser-rendered multi-track monitor, multi-track timeline, drag/move, source-aware in/out trimming, split/delete/ripple-delete, track create/empty-track removal, lock/hide/mute flags, undo/redo, AI operation planning (set/move/split/trim/remove/ripple_remove) and lightweight revision polling with a full-document fetch only after the revision changes. Ctrl/Command+wheel zooms around the pointer; clip dragging uses requestAnimationFrame edge auto-scroll. The Inspector edits visual box geometry (left/top/width/height/rotation/opacity) in stage pixels while clearing legacy normalized geometry attributes in the same undoable batch. Dock separators persist panel widths.
- The MP4 adapter samples the same browser runtime frame-by-frame, then lets FFmpeg encode the frames and mix audio source intervals. Generated subtitle pages acknowledge their requested time; arbitrary iframe pages are rejected until they implement the same contract.
- Full lossless migration of every CompositionIR field is not claimed. Migration warnings are explicit.
- After migration, do not continue editing the same project in the legacy editor unless a deliberate bridge migration is added.
