"""Classify the explicit real-provider token-stream acceptance result.

This module is shared by the CI gate and unit tests so provider/model retirement
is reported separately from a code-level acceptance failure. It never reads or
prints provider credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

LIVE_TEST_NAME = "test_configured_film_ai_stream_delivers_tokens_before_result_without_writing_film"


def resolve_live_model_override(explicit_override: str, commit_message: str) -> str:
    """Accept a one-run model override from workflow_dispatch or a tagged push.

    The commit marker is ``[live-model=provider/model-id]``. Only a simple
    model identifier is accepted; the commit text is never passed to a shell.
    This affects the isolated smoke config only, never the saved runner config.
    """
    candidate = str(explicit_override or "").strip()
    if not candidate:
        match = re.search(
            r"\[live-model=([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})\]",
            str(commit_message or ""),
        )
        candidate = match.group(1) if match else ""
    if candidate and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", candidate):
        raise ValueError("model override must be a simple provider/model identifier")
    return candidate


def resolve_live_provider_override(explicit_override: str, commit_message: str) -> str:
    """Allow only the explicitly supported per-run provider override.

    ``[live-provider=nvidia]`` selects NVIDIA's official OpenAI-compatible endpoint.
    This is isolated to the live smoke config; stored runner settings are unchanged.
    """
    candidate = str(explicit_override or "").strip().lower()
    if not candidate:
        match = re.search(r"\[live-provider=([a-z0-9-]{1,32})\]", str(commit_message or ""))
        candidate = match.group(1) if match else ""
    if candidate and candidate != "nvidia":
        raise ValueError("currently only the nvidia live-provider override is supported")
    return candidate




def resolve_live_api_key(
    provider_override: str,
    saved_api_key: str,
    saved_base_url: str,
    nvidia_api_key_secret: str,
    nvidia_api_key: str,
    generic_api_key: str,
) -> tuple[str, str]:
    """Select a credential without sending another provider's key to NVIDIA.

    An explicit NVIDIA run prefers a named NVIDIA credential unless the saved
    runner credential is already pinned to NVIDIA's official endpoint. For all
    other runs, preserve the existing saved-config-first precedence.
    """
    saved_key = str(saved_api_key or "").strip()
    endpoint = str(saved_base_url or "").strip()
    if endpoint and "://" not in endpoint:
        endpoint = "https://" + endpoint
    saved_host = (urlparse(endpoint).hostname or "").lower().rstrip(".")
    provider = str(provider_override or "").strip().lower()

    if provider == "nvidia":
        if saved_key and saved_host == "integrate.api.nvidia.com":
            return "runner_saved_config", saved_key
        for source, value in (
            ("NVIDIA_API_KEY_SECRET", nvidia_api_key_secret),
            ("NVIDIA_API_KEY", nvidia_api_key),
        ):
            candidate = str(value or "").strip()
            if candidate:
                return source, candidate
        raise ValueError(
            "NVIDIA live override requires NVIDIA_API_KEY or a saved credential "
            "already pinned to integrate.api.nvidia.com; refusing to send an "
            "unrelated provider key."
        )

    for source, value in (
        ("runner_saved_config", saved_key),
        ("NVIDIA_API_KEY_SECRET", nvidia_api_key_secret),
        ("NVIDIA_API_KEY", nvidia_api_key),
        ("VD_LLM_API_KEY_SECRET", generic_api_key),
    ):
        candidate = str(value or "").strip()
        if candidate:
            return source, candidate
    return "missing", ""

def resolve_live_model(model: str, base_url: str) -> str:
    """Resolve a retired OpenRouter model only for the official OpenRouter endpoint.

    The configured endpoint may be an OpenAI-compatible gateway that does not
    understand OpenRouter's ``~`` routing alias. Never rewrite a model ID for a
    different hostname; operators can supply a one-run native model override.
    The saved runner configuration is never changed.
    """
    model_id = str(model or "").strip()
    endpoint = str(base_url or "").strip()
    if "://" not in endpoint:
        endpoint = "https://" + endpoint
    host = (urlparse(endpoint).hostname or "").lower().rstrip(".")
    if model_id == "z-ai/glm-5.3" and host in {"openrouter.ai", "www.openrouter.ai"}:
        return "~z-ai/glm-latest"
    return model_id

REPORTS = {
    "PASS": (
        "The isolated real-provider request streamed model token fragments before "
        "its final result and left FilmDocument unchanged."
    ),
    "MISSING": (
        "This run did not opt in to real-provider streaming; mocked SSE tests do "
        "not prove incremental provider output."
    ),
    "REJECT": (
        "The explicitly requested real-provider stream failed its acceptance checks. "
        "Token-stream acceptance remains blocked."
    ),
    "BLOCKED_PROVIDER": (
        "The configured provider rejected the request because its account has "
        "insufficient balance. No token stream was produced; use a funded endpoint."
    ),
    "BLOCKED_MODEL": (
        "The configured model has been retired or is no longer available (HTTP 410). "
        "No token stream was produced; select a currently supported model and rerun."
    ),
    "BLOCKED_ENDPOINT": (
        "The official provider endpoint did not complete the minimal streaming probe "
        "within its connection/read timeout. The model-token acceptance test was not run; "
        "inspect runner egress, provider availability, and first-token latency before rerunning."
    ),
    "UNKNOWN": (
        "The isolated real-provider JUnit evidence was missing or could not be interpreted."
    ),
}


def _case_evidence(case: ET.Element) -> str:
    parts: list[str] = []
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is None:
            continue
        parts.append(node.attrib.get("message", ""))
        parts.append(node.text or "")
    return " ".join(parts).lower()


def classify_live_stream_acceptance(
    config_outcome: str,
    test_outcome: str,
    junit_path: str | Path = "live-stream-junit.xml",
    probe_outcome: str = "skipped",
    probe_error_type: str = "",
    probe_cause_type: str = "",
) -> str:
    """Return PASS/MISSING/REJECT/provider blockers/UNKNOWN."""
    config_outcome = str(config_outcome or "unknown")
    test_outcome = str(test_outcome or "unknown")
    probe_outcome = str(probe_outcome or "skipped")
    if config_outcome in {"skipped", "missing"}:
        return "MISSING"
    if config_outcome != "success":
        return "REJECT"
    if probe_outcome not in {"success", "skipped"}:
        timeout_types = {
            "APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout",
            "PoolTimeout", "WriteTimeout", "APIConnectionError",
        }
        if str(probe_error_type or "") in timeout_types or str(probe_cause_type or "") in timeout_types:
            return "BLOCKED_ENDPOINT"
        return "REJECT"

    path = Path(junit_path)
    if not path.is_file():
        return "REJECT" if test_outcome != "success" else "UNKNOWN"

    try:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
        cases = [
            case
            for suite in suites
            for case in suite.findall(".//testcase")
            if LIVE_TEST_NAME in case.attrib.get("name", "")
        ]
    except (ET.ParseError, OSError):
        return "UNKNOWN"

    if len(cases) != 1:
        return "UNKNOWN"

    case = cases[0]
    failure = case.find("failure")
    error = case.find("error")
    skipped = case.find("skipped") is not None
    if failure is None and error is None and not skipped and test_outcome == "success":
        return "PASS"
    if failure is None and error is None and skipped:
        return "UNKNOWN"

    evidence = _case_evidence(case)
    if "insufficient balance" in evidence or ("429" in evidence and "balance" in evidence):
        return "BLOCKED_PROVIDER"
    transport_timeout_markers = (
        "apitimeouterror", "readtimeout", "connecttimeout",
        "pooltimeout", "writetimeout", "timed out",
    )
    if any(marker in evidence for marker in transport_timeout_markers):
        return "BLOCKED_ENDPOINT"
    model_retirement_markers = (
        "has reached its end of life",
        "no longer available",
        "model has been retired",
        "model is retired",
    )
    if (
        any(marker in evidence for marker in model_retirement_markers)
        or "http 410" in evidence
        or "status 410" in evidence
        or ("410" in evidence and ("gone" in evidence or "retired" in evidence))
    ):
        return "BLOCKED_MODEL"
    if failure is not None or error is not None or test_outcome != "success":
        return "REJECT"
    return "UNKNOWN"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report the live model token-stream acceptance gate.")
    parser.add_argument(
        "--junit",
        default=os.environ.get("LIVE_STREAM_JUNIT_PATH", "live-stream-junit.xml"),
        help="JUnit XML written by the isolated real-provider smoke test.",
    )
    args = parser.parse_args(argv)
    config_outcome = os.environ.get("LIVE_STREAM_CONFIG_OUTCOME", "unknown")
    test_outcome = os.environ.get("LIVE_STREAM_TEST_OUTCOME", "unknown")
    probe_outcome = os.environ.get("LIVE_PROVIDER_PROBE_OUTCOME", "skipped")
    probe_error_type = os.environ.get("LIVE_PROVIDER_PROBE_ERROR_TYPE", "")
    probe_cause_type = os.environ.get("LIVE_PROVIDER_PROBE_CAUSE_TYPE", "")
    status = classify_live_stream_acceptance(
        config_outcome, test_outcome, args.junit,
        probe_outcome, probe_error_type, probe_cause_type,
    )
    print(f"LIVE_STREAM_ACCEPTANCE={status}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(f"## Real-model token-stream acceptance: {status}\n\n")
            summary.write(REPORTS[status] + "\n\n")
    # MISSING is allowed only when this run did not opt in. An explicit request
    # must pass; REJECT, UNKNOWN and all provider/model blockers fail the gate.
    if config_outcome != "skipped" and status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
