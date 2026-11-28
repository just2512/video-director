# Parallel Local + Remote Execution Protocol

**Repository:** `just2512/video-director`  
**Status:** Active policy for all new work after adoption  
**Prepared against observed main SHA:** `d8de748219be28b81c1ac68bbb5766f9160bbdd8` (2026-11-27)  
**Policy branch:** `work/w0-parallel-local-remote-governance-20261128`

This document is the authoritative operating protocol for new local/remote parallel work. It supersedes conflicting procedural instructions in older dated sections of `execution-ledger.md` and `parallel-workstreams.md`. Historical run records remain historical evidence and are not silently rewritten.

## 1. Operating model

Use two independent execution lanes and one controlled integration lane:

1. **Local lane:** reproduce and characterize issues that depend on the user's Windows environment, actual media, installed codecs, browser behavior, local storage, credentials, or production project data. The local owner supplies evidence and performs acceptance that hosted Linux cannot establish.
2. **Remote lane:** an independent cloud workspace/agent owns only the assigned files and contracts, implements isolated fixes, adds or updates focused tests, and runs the relevant GitHub-hosted checks.
3. **Integration lane:** one coordinator freezes the baseline, assigns exclusive file ownership, checks dependencies, obtains independent review, controls the PR merge, and ensures final-SHA acceptance.

GitHub Actions is the automated test and evidence lane, not a substitute for local Windows or production-media acceptance.

## 2. Roles and separation of duties

| Role | Responsibilities | Must not |
|---|---|---|
| Local issue owner | Reproduce real-environment behavior; collect sanitized logs, request/response headers, media profile and exact local commit; run required Windows/browser/media acceptance | Push uncommitted local changes into another owner's branch; upload production media or secrets without explicit authorization |
| Remote implementation owner | Work in an isolated cloud clone/workspace; implement only assigned paths; add focused regressions; report exact commit and CI runs | Touch files owned by the local lane or another remote task; change acceptance thresholds; merge its own PR |
| Integration coordinator | Freeze the current main SHA; maintain task register and file locks; resolve dependencies; check diffs and acceptance; manage PR integration | Act as the sole independent reviewer of code it authored; merge without required gates |
| Independent reviewer | Review the actual diff, business contract, failure mode, and test adequacy; must be different from the implementation owner | Approve based only on the author's summary or green CI badge |
| GitHub Actions | Run hosted tests against an exact commit SHA and retain test logs/artifacts | Read local files, local storage, local model configuration, or developer credentials |

The remote implementation owner and integration coordinator must be different people or agent sessions; one AI agent must not both implement a remote task and coordinate/integrate that same task. The implementation owner and independent reviewer must also be different people or agents. The PR author cannot approve or self-certify their own patch. The coordinator may review operational metadata, but code approval requires an independent reviewer. If no independent reviewer is available, the PR remains unmerged.

## 3. Baseline and task creation

Before each batch:

1. Fetch the latest `origin/main` and record the full 40-character SHA. Do not reuse a baseline from an earlier chat, task, or ledger entry.
2. The local owner records `git status --short`, current branch, local HEAD, staged paths, unstaged paths, and untracked paths. Do not expose secrets or private media.
3. The coordinator compares the local changed-path list with the proposed remote ownership list.
4. Create every task branch from the same frozen current-main SHA. Record that SHA in the task card.
5. Do not start a remote implementation task until its owned paths and no-touch paths are explicit and non-overlapping.

A later advance of main does not change the original task baseline silently. If a task branch must be updated, record the new base/merge commit, inspect the complete diff again, and rerun all affected checks against the new exact head SHA. Previous PASS evidence does not carry forward.

The observed baseline in the header is the main SHA used to prepare this protocol branch. It is not a permanent baseline and must not be reused for future tasks after this policy PR is merged.

## 4. Branch and PR policy

**One integration channel: short-lived task branch + lightweight PR to `main`.**

- `main` is the only integration/release branch.
- Branch names must preserve the existing CI lane prefixes:
  - `work/w1-<issue-or-topic>` — generation durability
  - `work/w2-<issue-or-topic>` — timeline transactions and browser interactions
  - `work/w3-<issue-or-topic>` — lifecycle, media, rendering and QC
  - `work/w4-<issue-or-topic>` — project home/create UX
  - `work/w5-<issue-or-topic>` — settings and credential safety
  - `work/w6-<issue-or-topic>` — GitHub Actions and execution governance
  - `work/w7-<issue-or-topic>` — shared shell/navigation
  - `work/w8-<issue-or-topic>` — Shotcraft browser UX
- Open a Draft PR while implementation is active. Mark it ready for review only when the task owner has supplied its exact commit SHA, focused test results, and remaining limitations.
- Every task PR targets `main`, describes its exclusive file scope, and links its Issue/task card.
- Merge only after independent review, required owner-lane CI, and required integration gates pass on the exact PR head SHA. Use a small, task-scoped PR; do not combine unrelated fixes.
- Do not directly push completed feature branches into `main`, create ad-hoc path-scoped synthetic main trees, or use a second integration mechanism.
- Delete a short-lived branch only after the PR is merged and its final evidence is recorded.

### Historical PRs and stale branches

PR #122 and PR #127 are historical reference material only for new work. Their branch histories were observed to be 85 commits behind current main when this protocol was prepared. Do not check them out as a new task base, rebase them wholesale, merge them, or assume their historical CI results apply to current main. Extract only a specific issue description, diagnostic finding, or patch concept, then recreate any needed implementation as a new task branch from the latest main. This rule does not close or otherwise alter those PRs.

Before opening work for an Issue that already has another open PR or active branch, inspect that work first. Do not create duplicate implementation lanes for the same acceptance target.

## 5. File ownership and collision control

Each active task card must contain:

- `OWNED_FILES`: exact paths the owner may modify.
- `NO_TOUCH`: exact protected paths and all other files by default.
- `SHARED_CONTRACTS`: any cross-module API, schema, revision, persistence, provider, lock, or project-authority contract affected.
- `DEPENDENCIES`: tasks/contracts that must be reviewed first.
- `ACCEPTANCE`: business behavior and tests required to close the task.

Rules:

1. One file has at most one active implementation owner at a time. Multiple agents may review it read-only.
2. Tests are owned explicitly too. A test-only task must not change production code unless a separately reviewed scope change is approved.
3. Shared contracts have one named writer at a time. The coordinator must approve any ownership transfer before a second task edits the same contract.
4. If a defect spans multiple files, assign one owner per file or create one cohesive owner task. Do not let parallel agents independently modify the same cross-file behavior.
5. Generated bundles must be rebuilt from their authoritative source and included only when the task owns that generated output. Never hand-edit generated output to evade source ownership.
6. The default no-touch set includes local user data, model configuration, API credentials, real production media, and unrelated dirty working-tree files.

## 6. Task lifecycle and status vocabulary

Use these operational states in the task register:

| State | Meaning | Exit condition |
|---|---|---|
| INTAKE | Issue captured; evidence or scope incomplete | Problem and expected behavior are written |
| READY | Baseline, owner, file locks, dependencies and acceptance are explicit | Task branch created from recorded SHA |
| ACTIVE_LOCAL | Local reproduction or fix is in progress | Local evidence and changed paths recorded |
| ACTIVE_REMOTE | Remote implementation or focused tests are in progress | Commit pushed and focused CI started |
| REVIEW | Implementation is complete; independent review pending | Review decision and requested corrections recorded |
| CI_PENDING | Required hosted jobs are queued/running | Exact-head results available |
| INTEGRATION_READY | Review, owner CI and local requirements are satisfied | Coordinator approves merge |
| BLOCKED | A named external input, product decision, or evidence is missing | Blocker is resolved and documented |
| ACCEPTED | Required business behavior and all applicable gates pass on final integrated SHA | Evidence ledger updated |
| CLOSED | Issue and PR are closed after acceptance | No unresolved required acceptance remains |

Use evidence labels without inventing confidence scores:

- `PASS`: directly demonstrated on the stated SHA and environment.
- `FAIL`: reproducible contract violation.
- `MISSING`: required implementation or evidence absent.
- `UNKNOWN`: current evidence cannot decide.
- `BLOCKED`: a named dependency prevents safe progress.
- `NOT_APPLICABLE`: the acceptance item does not apply, with reason.

Do not treat INTAKE, READY, a green scoped job, or a merged PR as equivalent to ACCEPTED.

## 7. Acceptance gates

### 7.1 All tasks

- State the user-visible business behavior, not just the code change.
- Preserve existing tests and assertions unless a demonstrated test defect is independently reviewed and corrected. Never delete, skip, xfail, or weaken a test to obtain green CI.
- Run the task's focused tests first; then the owning lane workflow; then the required full regression/release gates on the same final candidate SHA.
- Record full SHA, workflow name, Run ID, result, and artifact/log reference.
- Inspect the complete diff and ensure no unrelated files, generated secrets, user data, or local configuration are included.
- A retry is a new piece of evidence; preserve the earlier failure rather than replacing it with the later success.
- Do not run cost-bearing live-provider tests unless separately and explicitly authorized. Ordinary hosted CI must keep those tests disabled.

### 7.2 Windows / media / browser dependent tasks

For defects involving Windows file locks, path/case/line-ending behavior, FFmpeg, Chromium/Edge media decoding, actual MP4 playback, local proxy/network behavior, or production storage:

- GitHub-hosted Linux PASS is necessary only where the code contract has a hosted test; it is not proof of Windows or real-media acceptance.
- Require a local Windows result on the exact candidate commit, or mark the local acceptance `MISSING` / `BLOCKED`.
- Do not close the Issue or describe the whole fix as accepted while required local acceptance is missing.
- Record the environment and media characteristics without uploading private media or credentials.
- If the user cannot run local acceptance yet, keep the issue open in `INTEGRATION_READY` only when the hosted scope is complete; do not set overall `ACCEPTED`.

### 7.3 Required GitHub Actions interpretation

- Push to a correctly named `work/wN-*` branch to run its lane-specific workflow where configured.
- PRs containing application/test paths trigger the canonical full hosted regression and Studio release gate according to the workflow's current path filters.
- A path-filtered `SKIPPED` job is not a PASS. A docs-only PR is not expected to run product tests; its review should verify documentation consistency and links.
- For final acceptance, the owner-lane job, full Python/browser regression, Studio release gate, and applicable Windows-native tests must refer to the exact final candidate SHA. Re-run affected gates after any code change.
- Hosted CI must use GitHub-hosted Ubuntu/Windows runners only. No self-hosted, WSL, developer workstation, local config, or local storage in repository CI.

## 8. Issue-specific task guidance

### 8.1 Issue #131 — Roll/Trim persistence assertion

Suggested branch: `work/w2-issue131-roll-assertion`

Initial scope is test-only: `tests/test_film_studio_roll_browser.py`. The owner must first establish whether the failure is caused by float serialization, gesture-to-time conversion, or test observation timing. Do not assume the Issue's current float-format hypothesis is proven.

Acceptance must:
- Parse the persisted clip URL fragment / source range into numeric start and end values.
- Compare numeric values using a tolerance justified by the timeline frame precision and test contract; do not choose an arbitrary epsilon that masks a real frame error.
- Confirm the authoritative persisted timeline state after the specific Roll/Trim request has completed, and assert the expected revision/state.
- Preserve the existing semantic checks for clip timing, source range, and adjacent clip continuity.
- Keep the production code untouched unless a separate reproducible product defect is demonstrated and separately authorized.
- Run the focused Roll/Trim browser test and the W2 lane. For an intermittent failure, use independent test processes and retain every run's outcome; a 20-run repeat may be used as a stability batch, but does not replace the full W2 gate.

### 8.2 Issue #124 — H.264 MP4 Range response investigation

Suggested branch after duplicate-work check: `work/w3-issue124-media-range`

Phase 1 is read-only code audit plus isolated tests. Do not change production Range behavior until the local controlled A/B evidence is available and matches the endpoint under investigation.

The audit must distinguish at least:
- Studio media endpoint: `Handler._film_document_api()` in `webui/server.py`.
- Render media endpoint: `_RenderHTTPHandler.do_GET()` in `video_director/film_render.py`.
- Generic whole-file routes, which are not interchangeable with those media endpoints.

Test the applicable Range contract, including:
- `bytes=0-`, `bytes=N-`, suffix ranges, single-range parsing, malformed/multi-range policy, unsatisfiable ranges, empty files and boundaries.
- Valid ranges return `206`; unsatisfiable ranges return `416` with `Content-Range: bytes */<size>`.
- `Content-Range` uses the actual inclusive end byte (`size - 1` at EOF); `Content-Length` equals the number of bytes actually sent.
- `Accept-Ranges: bytes`, GET/HEAD consistency, correct seek/file-pointer behavior, early client disconnect handling, and streaming behavior for large files.
- Browser requests that fetch tail ranges (for example MP4 metadata at the end of a file) are not assumed to be equivalent to a whole-file 200 response.

Local A/B must use the same media, project, endpoint and browser channel, changing only the diagnostic Range behavior. Retain sanitized request/response headers, status, requested byte interval, actual payload length, browser network failure and console details. A successful whole-file route does not prove the product media endpoint is correct.

Only after the A/B evidence identifies a reproducible handler defect may a production fix be authorized. Final acceptance requires the relevant endpoint's regression, the isolated real-media project flow, full hosted regression, Studio gate, and local Windows/browser verification. Keep the Issue open while required evidence is missing.

## 9. Task card template

Copy this for every local or remote task:

```text
TASK_ID:
ISSUE:
TITLE:

BASE_SHA:                         # full 40-character main SHA
LOCAL_HEAD:
LOCAL_BRANCH:
LOCAL_STATUS_PATHS:
TASK_BRANCH:
OWNER: LOCAL / REMOTE
INDEPENDENT_REVIEWER:

PROBLEM:
REPRODUCTION_EVIDENCE:
EXPECTED_BEHAVIOR:

OWNED_FILES:
NO_TOUCH:
SHARED_CONTRACTS:
DEPENDENCIES:

FOCUSED_TESTS:
OWNER_LANE_CI:
FULL_REGRESSION:
STUDIO_RELEASE_GATE:
LOCAL_ACCEPTANCE: PASS / FAIL / MISSING / BLOCKED / NOT_APPLICABLE

IMPLEMENTATION_SHA:
FINAL_CANDIDATE_SHA:
CI_RUN_IDS:
REVIEW_RESULT:
UNRESOLVED_ITEMS:

STATUS:
DELIVERABLES:
- Root-cause report
- Changed-file list
- Commit SHA
- Focused test output
- Hosted CI Run IDs and artifact links
- Local environment evidence, where required
- Independent review result
- Known limitations and remaining blockers
```

## 10. Integration and closure

1. The task owner opens or updates its task PR against `main`.
2. The independent reviewer reviews the complete final diff and evidence.
3. The coordinator confirms all file ownership, dependencies, and acceptance gates.
4. If main advanced after the task's base SHA, update the task branch deliberately; inspect the new full diff and rerun every affected gate on the new exact SHA.
5. Merge the small PR to main only after required gates pass. Do not enable automatic merge for a task whose required local acceptance is still missing.
6. After merge, record the merge SHA and rerun/confirm the main-branch gates required by the workflow policy. Update `execution-ledger.md` with the final SHA, Run IDs, PASS/FAIL/MISSING state and local acceptance status.
7. Close the Issue only when every required acceptance item is PASS. Otherwise leave it OPEN and record the exact missing evidence or blocker.

**No false closure:** a code commit, a PR merge, a green Linux job, or a test-only change alone does not prove the user-visible defect is fixed in the user's Windows/production environment.

## 11. Repository-level enforcement and agent entry points

This protocol is wired into the repository's normal agent and PR entry points:

| Entry point | Consumer | Required behavior |
|---|---|---|
| `AGENTS.md` | Coding agents that support repository agent instructions, including OpenCode/Codex-style agents | Mandatory read of this protocol before code changes, tests, reviews or delegation |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md` and repeats the mandatory protocol gate |
| `.github/copilot-instructions.md` | GitHub Copilot coding/review agents | Requires this protocol and the same ownership/acceptance rules |
| `.github/pull_request_template.md` | Every new PR | Captures baseline SHA, owner, independent reviewer, owned/no-touch paths, tests and local acceptance |
| `.github/workflows/parallel-governance.yml` | Pull requests targeting `main` | Checks lane branch naming, target branch, task-card fields, full baseline SHA, task-branch identity, and reviewer separation before a draft can become merge-ready |

These files are intentionally concise entry points; the full contract remains this document. Any coding agent that does not automatically load the repository instructions must be given an explicit instruction to read this document before acting. Local developers must update their checkout after this policy PR is merged so their local agents see the same `AGENTS.md` and `CLAUDE.md` instructions as remote agents.

### Required `main` branch rules

A workflow status check is only advisory until repository rules require it. The repository owner must verify/configure the `main` branch protection or repository ruleset with at least:

1. Require a pull request before merging; prohibit direct pushes to `main`.
2. Require the `Parallel governance` status check.
3. Require at least one approval from someone other than the PR author; dismiss stale approvals after new commits.
4. Require all review conversations to be resolved.
5. Block force pushes and branch deletion.
6. Do not permit routine administrator bypass of the above rules.

The `Parallel governance` check must first complete on a PR so GitHub can offer it as a required status check. Before enabling the rules, confirm the exact check name shown by GitHub Actions.

**Configuration evidence must be recorded.** Do not claim branch protection is active until the repository Settings page or an authorized GitHub API read confirms the saved rule. During preparation of this protocol, the rulesets read returned an empty list, while the branch-protection endpoint was not accessible to the connected integration (403). Therefore the actual `main` protection state is `UNKNOWN`, not PASS or FAIL. This is an explicit repository-owner activation item; do not confuse agent instructions and CI validation with GitHub server-side merge enforcement.

### Limits of enforcement

- Agent instruction files guide compliant local and remote agents; they cannot technically prevent a person or an agent that ignores repository instructions from editing files.
- The governance workflow validates PR metadata and reviewer separation; it does not replace code review, product tests, local Windows acceptance, or the required GitHub branch rule.
- If branch protection is unavailable or disabled, keep the governance check visible and treat any merge that bypasses the stated review/gates as a policy violation.
