## Task identity

- TASK_ID:
- ISSUE: (number or N/A with reason)
- BASE_SHA: (full 40-character SHA used to create this task branch)
- TASK_BRANCH: (must be `work/wN-*`)
- OWNER: LOCAL / REMOTE
- INDEPENDENT_REVIEWER: (GitHub username other than PR author; assign before marking ready)

## Scope and ownership

- PROBLEM / EXPECTED_BEHAVIOR:
- OWNED_FILES: (exact paths)
- NO_TOUCH: (exact paths or protected areas)
- SHARED_CONTRACTS:
- DEPENDENCIES:

## Evidence and acceptance

- FOCUSED_TESTS:
- OWNER_LANE_CI: (workflow + Run ID + exact SHA)
- FULL_REGRESSION: (workflow + Run ID + exact SHA, or NOT_RUN with reason)
- STUDIO_RELEASE_GATE: (Run ID + exact SHA, or NOT_RUN with reason)
- LOCAL_ACCEPTANCE: PASS / FAIL / MISSING / BLOCKED / NOT_APPLICABLE
- REVIEW_RESULT:
- FINAL_CANDIDATE_SHA:
- UNRESOLVED_ITEMS:

## Required declarations

- [ ] I read and followed `docs/parallel-execution-protocol.md`.
- [ ] No other active task owns the same files.
- [ ] No uncommitted work from another workspace was overwritten.
- [ ] No tests/assertions were removed, skipped, xfailed, or weakened to obtain a pass.
- [ ] All reported test results are tied to the exact tested SHA.
- [ ] Required Windows/real-media/local acceptance is present, or the Issue remains open and acceptance is marked MISSING/BLOCKED.
- [ ] The independent reviewer is not the implementation owner or PR author.

