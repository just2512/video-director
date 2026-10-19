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

## Current boundaries

- The Studio provides a browser-rendered multi-track monitor, multi-track timeline, drag/move, source-aware in/out trimming, split/delete, track create/empty-track removal, lock/hide/mute flags, undo/redo, AI operation planning (set/move/split/trim) and revision polling. Dock separators persist panel widths.
- The monitor remains a clip-level monitor rather than the final full-composition renderer. Generated subtitle pages accept time messages, but arbitrary iframe pages do not yet share an injected deterministic clock. Multi-track compositing/export convergence remains work to complete.
- Full lossless migration of every CompositionIR field is not claimed. Migration warnings are explicit.
- After migration, do not continue editing the same project in the legacy editor unless a deliberate bridge migration is added.
