"""Classify the explicit real-provider token-stream acceptance result.

This module is shared by the CI gate and unit tests so provider/model retirement
is reported separately from a code-level acceptance failure. It never reads or
prints provider credentials.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

LIVE_TEST_NAME = "test_configured_film_ai_stream_delivers_tokens_before_result_without_writing_film"

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
) -> str:
    """Return PASS/MISSING/REJECT/BLOCKED_PROVIDER/BLOCKED_MODEL/UNKNOWN."""
    config_outcome = str(config_outcome or "unknown")
    test_outcome = str(test_outcome or "unknown")
    if config_outcome == "skipped":
        return "MISSING"
    if config_outcome != "success":
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


def main() -> int:
    config_outcome = os.environ.get("LIVE_STREAM_CONFIG_OUTCOME", "unknown")
    test_outcome = os.environ.get("LIVE_STREAM_TEST_OUTCOME", "unknown")
    status = classify_live_stream_acceptance(
        config_outcome,
        test_outcome,
        os.environ.get("LIVE_STREAM_JUNIT_PATH", "live-stream-junit.xml"),
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
