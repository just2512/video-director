# Film scene capability and rejection matrix

**Scope:** deterministic offline rendering of project-local HTML scenes used by Film Studio.  
**Contract owner:** W3 lifecycle/export acceptance.  
**Status vocabulary:** supported means covered by the current bounded contract; rejected means the renderer deliberately fails closed; unknown means no compatibility guarantee is made.

This document narrows the claim. It does not promise deterministic rendering for arbitrary HTML, arbitrary browser applications, or arbitrary WebGL content.

## Supported scene contract

A project-local HTML scene is eligible for deterministic frame rendering only when it implements the frame protocol:

- `window.film.frame(t)` draws the scene at the requested local time `t`.
- Optional `window.film.ready` may be a promise; the bridge waits for it before acknowledging a frame.
- The parent requests exact frames through the versioned `film-frame` message protocol. Exact frame requests are serialized; preview seeks may be coalesced.
- A scene frame failure or timeout is an export failure associated with the stable timeline clip ID. It is not silently replaced with a blank frame.

The bridge is injected before authored scripts. It controls the primitives below while a frame request is active:

| Runtime surface | Contract |
|---|---|
| `Date()`, `new Date()`, `Date.now()) | Fixed epoch plus requested scene time |
| `performance.now()` | Requested scene time in milliseconds |
| `Math.random()` | Deterministic sequence seeded from requested time |
| CSS Web Animations | Existing animations are paused and assigned the requested current time |
| `requestAnimationFrame` | Callbacks are queued and flushed with a bounded iteration limit; unbounded loops fail |
| Frame ordering | Exact requests are serialized; preview requests may be coalesced |

## Resource contract

| Resource class | Supported behavior | Fail-closed condition |
|---|---|---|
| HTML scene file | Project-local scene loaded by the Film runtime | Missing scene, invalid frame protocol, frame exception or readiness timeout |
| Relative HTML media attributes | Project-relative paths are rewritten to the project media route | Path escapes the project boundary or the resulting project resource cannot load |
| Relative CSS `url(...)` | Paths in scene-local style blocks are rewritten relative to the scene stylesheet path | Missing referenced project resource |
| Fonts and images | Project-local resources; readiness is awaited before frame acknowledgement | Load/decode failure or bounded readiness timeout |
| Data/blob/about URLs | Allowed by the offline browser request guard where applicable | Not a substitute for a project-local resource that cannot be resolved |
| Remote HTTP(S), file and other external requests | Not supported by offline render | Request is blocked and reported as `EXTERNAL_SCENE_RESOURCE` |
| Film-document head CSS | Passive local CSS only | `@import`, `url()`, remote/file/javascript references, CSS `expression()` and `-moz-binding` are rejected |

The media rewrite and offline renderer have separate responsibilities: URL rewriting resolves supported project-local references; the renderer's network policy blocks external requests and its resource monitor rejects missing local resources.

## Explicitly unsupported / no guarantee

The following must not be represented as deterministic support:

- Arbitrary HTML that does not implement `window.film.frame(t)`.
- Arbitrary WebGL, GPU shaders, canvas workloads, or pixel identity across GPU/driver/browser versions.
- Uncontrolled timers, asynchronous state not awaited by `film.ready`, or authored code that mutates outside the requested frame protocol.
- Remote resource contents, remote APIs, external fonts/scripts/stylesheets, and network-dependent scene behavior.
- Every possible codec, media decoder, browser API, or third-party animation library.
- Exhaustive permutations of all FilmDocument edit operations.
- Pixel-identical output across different operating systems, browsers, GPU devices, or hardware encoders.

A passing same-time fixture proves only the primitives and scene behavior exercised by that fixture. It is not a universal determinism certificate.

## Required regression evidence

W3 must retain exact-SHA hosted evidence for:

1. The bridge is injected before authored scripts and the requested `frame(t)` is acknowledged.
2. Controlled clock/random/animation primitives repeat for the same requested time.
3. Exact frame requests serialize; preview seeks coalesce; unbounded RAF loops fail.
4. Project-local CSS/media/font readiness is enforced; missing resources fail closed.
5. External network requests are blocked.
6. A frame exception identifies the stable clip ID.
7. Look/render/QC artifacts carry the pinned FilmDocument revision; a revision change during sampling or export prevents publication.

Relevant existing regression files include `tests/test_film_scene_runtime.py`, `tests/test_film_render_scene_e2e.py`, `tests/test_film_runtime_video_frame_wait.py`, and the W3 lifecycle/export suite. Existing PASS evidence must be tied to the exact tested SHA; this inventory itself is not a test result.
