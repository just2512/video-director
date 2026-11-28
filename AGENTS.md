# Agent instructions for video-director

Before editing a film project, read [docs/FILM_AGENT_MANUAL.md](docs/FILM_AGENT_MANUAL.md).
- For MCP use, register python -m video_director.agent_gateway.mcp_server once; it exposes both Director tools and film.* timeline tools. Follow docs/agent-access.md for the revision-checked film.status → film.initialize (when needed) → film.edit → film.validate → film.look → film.render loop.

- When `film.html` exists, it is the authoritative edit timeline. Do not create a second timeline or silently write changes back to CompositionIR.
- Preserve stable clip IDs, original media files, and unrelated HTML text/comments.
- For programmable HTML scenes, implement `window.film.frame(t)` and expose required resource readiness through `window.film.ready`.
- Use only local project resources for a renderable scene. External network dependencies are blocked during deterministic render and look.
- After meaningful edits run `python -m video_director film validate --project-id PROJECT_ID`, then `python -m video_director film look --project-id PROJECT_ID`. Inspect the generated contact sheet and run `python -m video_director film render --project-id PROJECT_ID` for final delivery.
- Respect revision conflicts and locked tracks. Never bypass document validation to force an edit through.

## Mandatory local + remote parallel execution policy

**Before any code edit, test-only change, review, or task delegation, read and follow [docs/parallel-execution-protocol.md](docs/parallel-execution-protocol.md).** This is a repository-level operating constraint, not optional advice. If this file conflicts with an older dated execution note, the protocol linked above takes precedence for all new work.

Required behavior for every agent and developer:

1. Do not start implementation until the current `origin/main` SHA, task owner, exact `OWNED_FILES`, `NO_TOUCH`, dependencies, and acceptance conditions are recorded in the task card.
2. Use a short-lived `work/wN-*` branch created from the same frozen current-main SHA as the batch. Integrate only through a small PR to `main`; do not push feature work directly to `main`.
3. One file has one active implementation owner. Read-only review may be parallel; competing edits to the same file are prohibited.
4. Keep local and remote workspaces isolated. Never reset, clean, overwrite, or cherry-pick over another owner's uncommitted work. The local developer must report dirty paths before remote ownership is assigned.
5. This is a single-maintainer repository: the repository owner may coordinate and perform the final self-review. A separate read-only review by another agent/session is optional for high-risk changes and is not a merge prerequisite.
6. Bind every test result to the exact tested commit SHA and retain failures. Never delete, skip, xfail, or weaken tests to obtain a pass.
7. Linux-hosted CI does not prove Windows, FFmpeg, Chromium/Edge, actual MP4, local-storage, or production-media behavior. Required local acceptance must be recorded; otherwise the task remains MISSING/BLOCKED and the Issue stays open.
8. Do not read, upload, or commit local secrets, model configuration, user storage, or production media. Hosted CI uses GitHub-hosted runners only.
9. Never declare a task ACCEPTED or close its Issue based only on a commit, merged PR, green scoped job, or stale SHA. Follow the final-SHA and local-acceptance gates in the protocol.

For code changes, reviewers, and remote coding agents: if the required task card or ownership boundary is missing, stop implementation and return the missing fields instead of guessing.
