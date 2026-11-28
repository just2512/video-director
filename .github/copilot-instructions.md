# GitHub Copilot instructions for video-director

All repository changes must follow [AGENTS.md](../AGENTS.md) and the authoritative [Local + Remote Parallel Execution Protocol](../docs/parallel-execution-protocol.md).

Mandatory constraints:
- Read the protocol before editing, generating tests, reviewing, or delegating.
- Require a frozen current-main SHA, task card, named owner, independent reviewer, explicit `OWNED_FILES` and `NO_TOUCH`, dependencies, and acceptance gates before implementation.
- Use a short-lived `work/wN-*` branch and a PR to `main`; never push feature work directly to `main`.
- Do not let two agents edit the same file concurrently. Do not overwrite another workspace's uncommitted changes.
- The implementation owner cannot be the independent reviewer or integration coordinator for that task.
- Test evidence must identify the exact commit SHA. Preserve failures and do not weaken or remove tests.
- Linux CI does not replace required Windows/real-media/local acceptance. Keep the Issue open while required local evidence is missing.
- Never access or commit local credentials, model configuration, private storage, or production media.
