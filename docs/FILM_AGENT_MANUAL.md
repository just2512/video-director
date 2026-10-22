# Film Agent Manual

This document is the shared working contract for Claude Code, Codex, and any coding agent that creates or edits a video-director film project.

## Authority rules

- Once a project has a valid `film.html`, it is the single authoritative edit timeline for that project.
- Do not edit `plans/composition.json` or use the legacy CompositionIR editor to change an active `film.html` project. Do not maintain a second competing timeline.
- `CompositionIR` remains a director/planning representation. It is not an automatically synchronized mirror of the final film.
- The Studio and agents share project files. Manual and AI edits must be visible in those files, not stored only in chat state.
- Never overwrite a valid project document with an incomplete or unparsable rewrite. Write a sibling temporary file, validate it, then replace the destination atomically where the environment supports it.
- Keep existing IDs stable. If an operation refers to a clip ID that no longer exists, stop and inspect the current film rather than applying the change to a different clip.

## Project shape

A typical film project contains:

- `film.html`: stage, ordered tracks, and timeline clip instances.
- `scenes/*.html`: programmable HTML scene pages.
- `assets/*`: project-local images, videos, audio, fonts and other local resources.
- `.film/cache/`: generated look sheets and other disposable runtime caches.
- FilmDocument history metadata managed by video-director.

Keep source footage immutable. Trimming and splitting should change clip source ranges and timeline attributes, not create replacement source files unless an explicit export/bake operation is required.

## Minimal film.html example

Use stage dimensions that match the target export. `at` is timeline time in seconds. A media `#t=in,out` fragment is its source range in seconds.

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=1920,height=1080">
  <style>body { margin: 0; }</style>
</head>
<body>
  <section id="v1" data-label="主画面">
    <iframe id="opening" src="scenes/opening.html#t=0,5" at="0"
      style="left:0px;top:0px;width:1920px;height:1080px"></iframe>
    <video id="footage" src="assets/footage.mp4#t=12,18" at="5"
      style="left:0px;top:0px;width:1920px;height:1080px"></video>
  </section>
  <section id="a1" data-label="旁白">
    <audio id="narration" src="assets/narration.wav#t=0,11" at="0"></audio>
  </section>
</body>
</html>
```

This snippet documents the format; a new project must replace example paths with files that actually exist inside the validated project storage boundary. Use the asset catalog or `film validate` to verify local references.

## Programmable scene contract

A local scene intended for deterministic preview and export must provide `window.film.frame(t)`. `t` is a floating-point number in seconds measured relative to the scene's own source start. It must redraw the complete intended scene for that time and must not depend on which frame was requested before it.

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
    #title { position: fixed; left: 120px; top: 140px; font: 700 72px sans-serif; color: white; }
  </style>
</head>
<body>
  <h1 id="title">Opening</h1>
  <script>
    window.film = {
      duration: 5,
      width: 1920,
      height: 1080,
      ready: document.fonts.ready,
      frame(t) {
        const title = document.querySelector('#title');
        title.textContent = t < 1 ? 'Opening' : 'A new chapter';
        title.style.opacity = String(Math.max(0, Math.min(1, t / 0.5)));
      }
    };
  </script>
</body>
</html>
```

Runtime guarantees and limits:

- The host sends an exact local time and waits for the frame acknowledgement before capture.
- It controls `Date`, `performance.now()`, `Math.random()`, queued `requestAnimationFrame` callbacks, and CSS/Web Animations time to make supported frame code repeatable.
- `ready` should resolve only after fonts, images, video textures, and other required scene resources are initialized.
- Relative scene resources should use project-local paths. `look` and `render` block external network requests; move external fonts, scripts, and images into the project and reference them locally.
- Arbitrary framework state, timers, remote data, WebGL internals, and hidden asynchronous state are not automatically made deterministic. If a scene cannot honor `frame(t)`, validation must report the problem rather than pretending the scene is render-safe.

## Agent workflow

Run these from the video-director repository's Python environment, replacing `PROJECT_ID` with the actual project ID:

```bash
python -m video_director film status --project-id PROJECT_ID
python -m video_director film validate --project-id PROJECT_ID
python -m video_director film look --project-id PROJECT_ID
python -m video_director film diff --project-id PROJECT_ID
python -m video_director film render --project-id PROJECT_ID
```

For timeline edits, external agents must use the revision-checked operation entry point instead of overwriting `film.html` directly:

```json
[
  {"op": "set", "clip": "footage", "field": "at", "value": 6.0},
  {"op": "set", "clip": "footage", "field": "speed", "value": 1.25}
]
```

Save that array as a UTF-8 JSON file (for example, `film-ops.json`), copy the exact `revision` from the latest `film status` result, then run:

```bash
python -m video_director film edit --project-id PROJECT_ID --base-revision REVISION --ops-file film-ops.json
```

The command applies the whole operation batch under the project lock, validates it with the same FilmDocument contract as Studio, and appends it to undo/diff history. A stale revision is rejected with `REVISION_CONFLICT` and does not write anything. The command does not silently rebase an external agent's intent. Re-read `status`, inspect `diff`, and produce a new operation batch after a conflict. Edit scene source files under `scenes/` directly when necessary; for structural direct edits to `film.html`, prefer a validated temporary file and atomic replacement rather than an in-place partial write.

Studio watches active projects for direct external `film.html` writes. After a stable snapshot passes the strict parser, the watcher records its before/after document text and revisions in the same bounded history file used by CLI and Studio, so the change is visible in `film diff` and can be undone/redone. These entries are marked `external_file`; they do not invent a granular operation list for a raw HTML rewrite. Malformed or invalid-UTF-8 writes emit a broken-document event and pause Studio editing until a valid revision returns. The watcher debounces writes (at least 120 ms), so several writes inside one settling window may collapse into the final stable snapshot; it is not a per-keystroke or every-write audit log. An external file writer must still avoid partial writes and preserve the current document if validation fails.

- `status` reports the current revision, stage, tracks, clips and duration.
- `validate` checks the strict film document and verifies that referenced local files exist. It does not prove that the scene's frame contract works.
- `look` renders a contact sheet through the real browser runtime and repeats sample times in reverse order. Read the returned `image_path`; fix each explicit frame/resource/determinism failure before full export.
- `diff` shows the current document against a retained revision snapshot. The built-in history window is finite, so create a deliberate project snapshot or Git commit before a large rewrite.
- Composition migration resolves version-pinned `motion_recipe_id` and `motion_instance` through MotionRegistry. Supported opacity/scale/rotation/blur and pixel-offset channels become deterministic `data-film-keyframes` consumed by `frame(t)`; the exact recipe ID/version/instance parameters are preserved in clip attributes. Unsupported properties or parameters are identified in migration warnings.
- `film.html` stores its project frame rate in the `film-fps` meta tag. Existing documents without this meta default to 30 FPS. `render` inherits the saved rate unless the caller explicitly supplies `--fps`; it exports the exact revision and returns `rendered_revision`. It also writes `qc/film-final-<revision>.json` with output SHA-256/size, probed stream/dimensions/duration, audio signal measurements when available, visual sample hashes/metrics and determinism checks. Studio exposes the QC record next to the MP4 link. If the project changes during export, the record includes `current_revision_match: false`; do not describe the result as the latest revision. A technical PASS does not replace human review of visual composition or listening to the audio.

Studio timeline editing also supports `Ctrl/⌘+C` and `Ctrl/⌘+V` for selected clips. The in-memory clipboard is intentionally scoped to the current project: paste preserves each clip's source range, speed, effects/overrides and relative timing, searches the original tracks for the next interval that can be proven not to overlap, and commits the group as one undoable FilmDocument transaction. It rejects missing/locked source tracks, unknown-duration clips and cross-project paste rather than guessing. Clipboard state is not persisted across a page reload.

Timeline snapping can be toggled from the Studio toolbar. It targets the film start, playhead, and other clips' start/end boundaries within an eight-screen-pixel threshold; drag previews display a snap guide and the commit path recalculates the same target. Right-click a clip for copy, duplicate, paste, split, delete, ripple-delete, and Inspector actions.

The visual keyframe editor stores the validated `data-film-keyframes` schema on the corresponding clip. Each channel is `{property, keyframes:[{at,value,easing}]}`; `at` is relative to the clip and must be within its known duration, and keyframe times must be strictly increasing. Supported easing values are `linear`, `ease_in`, `ease_out`, and `ease_in_out`. The React Inspector provides a curve editor, editable points, easing controls and frame-step snapping. The unbundled fallback uses the same JSON field and FilmDocument transaction. Use only the properties and ranges defined by the current FilmDocument validator. For clips split or trimmed through an eased segment, the validator also accepts a bounded `{type:"window",preset,start,end}` easing record produced by FilmDocument so the remaining section preserves the original curve. This form is generated by editing operations; it should not be hand-invented. The curve UI preserves and displays that segment as “裁剪保真的曲线分段”.


For editing:

1. Read `film.html`, the referenced scene files and only the required assets.
2. Identify exact clip IDs and timeline/source times before modifying the cut.
3. Make the smallest text diff possible. Keep unrelated comments, attributes, clip IDs and resources unchanged.
4. Run `film validate`, then `film look`; inspect the contact sheet instead of trusting code inspection alone.
5. Render the final MP4 and report its output path, exported revision and any warnings.
6. If Studio or another agent has changed the document, reload the latest revision and re-plan instead of applying stale coordinates.

## Canvas element editing

The Studio canvas picker returns an exact scene clip ID plus a CSS selector. The AI can propose an `element_set` operation only for that selected element; the server validates the clip ID, selector, text length and CSS property/value allowlist. Do not use a guessed selector to modify arbitrary elements.

The selector-based `overrides` subset supports text and bounded style properties validated by FilmDocument: `color`, `backgroundColor`, `fontSize`, `fontWeight`, `textAlign`, `lineHeight`, `letterSpacing`, `left`, `top`, `width`, `height`, `position`, `opacity`, `transform`, `borderRadius`, and `rotate`. The Studio Inspector exposes text, position, dimensions, text/background colour, opacity, font weight, text alignment, font size and rotation. A transparent background is not materialized unless the user enables a fill; unchecking an existing fill explicitly restores transparency. Legacy/non-selector override records are preserved as-is by an element edit, but the current runtime only applies the selector-based subset it understands. For an element with nested children, do not replace its whole `textContent`; edit an explicit leaf element instead.

## Failure policy

Return a concrete `MISSING`, `UNSUPPORTED`, or error explanation when a resource or rendering behavior cannot be verified. Do not silently skip broken clips, convert unknown durations to guessed values, bypass revision conflicts, or label an unrendered candidate as a completed video.