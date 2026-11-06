# Parallel development workstreams

Baseline: `59875188399671c81e59a78afb43aa357da9e4e6` (main at workstream creation).

## Active workstreams

| Branch | Ownership | Primary regression scope |
|---|---|---|
| `work/generation-state` | generation job state machine, generation_jobs.json, Manifest, Asset Registry, crash recovery, provider idempotency | generation recovery, generation gap, transaction recovery, project manifest |
| `work/timeline-state` | multi-select, cross-track drag, undo/redo, external revision conflicts, project-switch state | editor undo/redo, timeline history, pending revision, external watch, project switch |
| `work/lifecycle-e2e` | project creation/migration/edit/export/QC lifecycle acceptance | project manager, full pipeline, migration preview, render publication and QC |

## CI policy

- Every workstream push runs its scoped regression on a GitHub-hosted Ubuntu runner with an isolated checkout.
- The full AI Director integration suite remains on the self-hosted WSL runner.
- Windows-native persistence remains on `windows-latest`; real NVIDIA live-stream remains opt-in and must only be marked PASS with a real provider response.
- A branch's scoped PASS is not an integration PASS. Before integration, compare against the latest main and resolve contract drift. After each integration commit, validate that exact main SHA.
- No pull requests or human stage gates are required. Integration is performed directly into main only after scoped tests and conflict checks.
- Keep workstream file ownership disjoint where possible. Shared contracts must be changed deliberately and covered by cross-workstream tests.

## Integration order

1. Generation persistence contract and state machine.
2. Timeline transaction/revision consistency.
3. Lifecycle E2E coverage after the first two contracts stabilize.
4. Full main CI, Windows-native validation, then real NVIDIA live-stream acceptance when credentials and the real environment are available.

## Runner isolation

Do not remove the existing self-hosted concurrency lock while AI Director and Studio UI Build share the same WSL workspace. Move Studio UI build to hosted Ubuntu before considering any concurrency-group change for the self-hosted jobs.
