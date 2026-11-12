# Local Acceptance Constraints

**Status date:** 2026-11-12  
**Repository:** just2512/video-director  
**Purpose:** Close only the external acceptance items that hosted CI cannot legitimately prove. This document does not reopen product implementation or closed feature/test workstreams.

## 1. Acceptance boundary

Cloud-side implementation and the documented default hosted CI gates are closed with PASS evidence in execution-ledger.md and parallel-workstreams.md.

| Item | Current state | Evidence needed to close |
|---|---|---|
| User-machine model configuration preservation | UNKNOWN | Local before/after comparison showing existing provider/model configuration is unchanged |
| NVIDIA live Film AI acceptance | BLOCKED_ENDPOINT | Successful real provider response followed by actual Film AI streaming and safety integration tests |
| Representative user-production-media performance | MISSING | Measurement using representative project media with reproducible environment and source details |

The public sample media PASS is not a substitute for a user-production-media benchmark. A hosted provider timeout is neither a product-code failure nor a model PASS.

## 2. Non-negotiable safety constraints

1. **Credential secrecy:** Never paste, upload, commit, log, screenshot, or include API keys, authorization headers, .env contents, tokens, cookies, or credential-bearing URLs in issues, CI artifacts, chat, terminal transcripts, or benchmark reports.
2. **CI isolation:** Hosted workflows must not read local credentials, local config files, user directories, or machine-specific secrets to claim local preservation.
3. **Configuration immutability:** Do not regenerate .env, replace provider settings, reset model selection, or change endpoint/model values merely to make a test pass. If a test requires a temporary setting, use a separate temporary process/session and restore the original state.
4. **No weakened gates:** Do not disable approval checks, safety checks, streaming assertions, timeouts, export/QC checks, or tests to manufacture PASS.
5. **No partial-success claims:** A health check or endpoint probe alone does not prove Film AI integration. A test that was skipped, never started, or timed out is NOT TESTED / BLOCKED, not PASS.
6. **No blind retries:** The hosted NVIDIA endpoint already timed out twice at approximately 210 seconds. Do not repeat the same call until endpoint availability is independently confirmed and the user explicitly authorizes another live call.
7. **Production data protection:** Use local copies or approved fixtures. Do not upload private project media to public services or CI without explicit authorization.
8. **No feature expansion:** Do not start another W1/W2/W3/W4/W5/W7/W8 feature or test batch for these evidence items. Reopen implementation only for a reproducible violation of a documented product contract.

## 3. Local check A — preserve existing model configuration

This check is read-only. Its purpose is to prove that the acceptance procedure does not alter the existing local provider/model configuration.

### Procedure

1. Close the application and any process that may rewrite its configuration.
2. Identify the configuration file(s) actually used by the current local installation by following the repository's existing setup documentation. Do not assume a path or create a new configuration file.
3. Record the file path, last-modified timestamp, and SHA-256 before testing. Do not print file contents.
4. Run the local application or the already documented local smoke procedure without changing provider/model settings.
5. Close the application cleanly, then record the same metadata and SHA-256 again.
6. Compare the before/after records.

PowerShell example for a known config file (replace the path locally; do not paste its contents):

    $config = (Resolve-Path -LiteralPath "PATH_TO_EXISTING_CONFIG").Path
    Get-Item -LiteralPath $config | Select-Object FullName, Length, LastWriteTimeUtc
    Get-FileHash -LiteralPath $config -Algorithm SHA256

Run the same commands before and after. Keep the resulting metadata and hashes locally. Share only redacted path information, timestamps, file size, and hashes if external review is needed.

### Result criteria

- **PASS:** the relevant existing configuration remains byte-for-byte unchanged, or any expected application-managed non-secret metadata change is separately explained and verified not to alter provider/model/credential values.
- **FAIL:** provider, model, endpoint, credential reference, or another protected setting changes unexpectedly.
- **UNKNOWN:** the active configuration file cannot be identified, or before/after evidence is missing.

Do not copy the configuration into the repository as evidence.

## 4. Local check B — NVIDIA live Film AI acceptance

This check is conditional. Do not run it merely to clear a status label.

### Preconditions

- The NVIDIA service and configured model endpoint are independently confirmed responsive for the same model and account.
- The user explicitly authorizes a new live provider call.
- The existing local provider configuration is protected using the read-only procedure in section 3.
- The local environment uses the repository's documented dependencies and test entry points. Do not invent a new test command or bypass the established harness.

### Procedure

1. First perform a minimal authenticated streaming probe using the existing configured endpoint/model. Keep credentials in the local secret store/environment; never echo them.
2. Confirm that the probe receives at least one non-empty content token and terminates according to the provider protocol.
3. Only after the probe succeeds, run the repository's existing Film AI live-provider integration test(s) through the documented test entry point.
4. Preserve the unmodified test output and exit status locally. Redact secrets before sharing any excerpt.
5. Verify the actual application-level behaviors covered by existing tests: stream token ordering/completion, approval and safety boundaries, and Film AI integration. A provider probe alone is insufficient.
6. Recheck the configuration hash from section 3.

### Result criteria

- **PASS:** the probe succeeds and the actual Film AI integration tests execute and pass, including existing streaming and approval/safety assertions.
- **BLOCKED_ENDPOINT:** the endpoint times out, refuses the request, or returns no non-empty token before the probe can hand off to the application test.
- **FAIL:** the provider probe succeeds but an application-level assertion fails reproducibly.
- **NOT TESTED:** the actual integration tests do not start, are skipped, or no usable test evidence is produced.
- **UNKNOWN:** the test result or environment cannot be established.

Do not report BLOCKED_ENDPOINT or NOT TESTED as a product-code failure. Do not report a successful probe as full Film AI acceptance.

## 5. Local check C — representative production-media performance

The hosted Big Buck Bunny public-media test proves a real-media end-to-end path. It does not establish performance for user production projects.

### Required input

Use one representative project or a user-approved local copy. Record:

- project identifier using a non-sensitive alias;
- media file count and total bytes;
- video/audio formats, codecs, resolution, frame rate and durations;
- machine CPU, RAM, GPU and operating system;
- application commit SHA and relevant runtime/tool versions;
- whether source media is on local SSD, external disk, or network storage.

Do not include private filenames, client names, faces, voices, or other identifying content in a shared report unless explicitly approved.

### Procedure

1. Keep the source project and media unchanged; run on a copy if the workflow may modify project state.
2. Use the existing application workflow and documented measurement points. Do not substitute synthetic fixtures or the public sample for the representative project.
3. Record cold and warm runs separately if both are relevant; do not mix them into one timing.
4. Record stage timings and outcomes for media probing/ingest, preview readiness, render, export, and QC where those stages apply.
5. Record output dimensions, frame rate, duration, audio/video stream presence, and QC result. Retain logs locally and redact sensitive paths before sharing.
6. Repeat only when a run is invalidated by a documented environmental interruption; do not discard slow but valid results.

### Result criteria

- **PASS:** representative project metadata, environment, commit, stage timings, output validation and QC evidence are all recorded and reproducible.
- **FAIL:** a documented product contract or output/QC requirement fails reproducibly.
- **MISSING:** no representative user production project/media has been supplied or measured.
- **UNKNOWN:** measurements exist but project/environment/commit or output evidence is insufficient to interpret them.

This is an evidence-gathering benchmark, not permission to add performance optimizations or broaden product scope.

## 6. Evidence record template

Keep this record locally. Remove secrets and sensitive project identifiers before sharing.

| Field | Value |
|---|---|
| Local date/time and timezone | |
| Repository commit SHA | |
| OS / machine details | |
| Check performed (A / B / C) | |
| Configuration hash before/after (A/B only) | |
| Provider/model (B only; never include key) | |
| Probe result and elapsed time (B only) | |
| Actual integration test result (B only) | |
| Project/media alias and metadata (C only) | |
| Stage timings and output/QC evidence (C only) | |
| Final disposition (PASS / FAIL / BLOCKED / NOT TESTED / MISSING / UNKNOWN) | |
| Redacted log/artifact location | |

## 7. Stop and escalation rules

- If check A passes, mark local model-configuration preservation PASS; do not repeat it after every ordinary application launch.
- If check B is BLOCKED_ENDPOINT, stop. Resume only after independent endpoint recovery confirmation and fresh explicit authorization.
- If check C is MISSING, leave it MISSING until representative media is available. Do not manufacture substitute evidence.
- Escalate only a reproducible application-level failure with commit SHA, exact steps, redacted logs and expected-versus-actual behavior.
- These checks do not authorize changes to product code, CI secrets, workflows, approval policy, or the closed workstream plan.

## 8. Final acceptance statement

The cloud-side implementation and default hosted CI delivery remain CLOSED / PASS on their recorded exact SHAs. Overall external acceptance remains OPEN only for the three evidence items in section 1. Close each item independently; do not collapse them into a single ambiguous "all passed" statement.
