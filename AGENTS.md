# Agent instructions for video-director

Before editing a film project, read [docs/FILM_AGENT_MANUAL.md](docs/FILM_AGENT_MANUAL.md).
- For MCP use, register python -m video_director.agent_gateway.mcp_server once; it exposes both Director tools and film.* timeline tools. Follow docs/agent-access.md for the revision-checked film.status → film.initialize (when needed) → film.edit → film.validate → film.look → film.render loop.

- When `film.html` exists, it is the authoritative edit timeline. Do not create a second timeline or silently write changes back to CompositionIR.
- Preserve stable clip IDs, original media files, and unrelated HTML text/comments.
- For programmable HTML scenes, implement `window.film.frame(t)` and expose required resource readiness through `window.film.ready`.
- Use only local project resources for a renderable scene. External network dependencies are blocked during deterministic render and look.
- After meaningful edits run `python -m video_director film validate --project-id PROJECT_ID`, then `python -m video_director film look --project-id PROJECT_ID`. Inspect the generated contact sheet and run `python -m video_director film render --project-id PROJECT_ID` for final delivery.
- Respect revision conflicts and locked tracks. Never bypass document validation to force an edit through.