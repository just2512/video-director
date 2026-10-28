from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from video_director.live_stream_acceptance import (
    LIVE_TEST_NAME,
    REPORTS,
    classify_live_stream_acceptance,
)


def write_junit(path, result: str = "", test_name: str = LIVE_TEST_NAME):
    result_xml = result
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuite tests="1" failures="' + ("1" if result else "0") + '" errors="0" skipped="0">'
        '<testcase classname="tests.test_live" name="' + test_name + '">'
        + result_xml
        + "</testcase></testsuite>",
        encoding="utf-8",
    )


def test_default_ci_marks_live_provider_check_missing_without_attempt(tmp_path):
    assert classify_live_stream_acceptance("skipped", "skipped", tmp_path / "missing.xml") == "MISSING"


def test_clean_isolated_live_stream_junit_is_the_only_pass(tmp_path):
    path = tmp_path / "live.xml"
    write_junit(path)
    assert classify_live_stream_acceptance("success", "success", path) == "PASS"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "HTTP 410 Gone: the model has reached its end of life and is no longer available",
            "BLOCKED_MODEL",
        ),
        ("HTTP 429: insufficient balance on provider account", "BLOCKED_PROVIDER"),
        ("AssertionError: only lifecycle events were delivered; no model token fragments", "REJECT"),
    ],
)
def test_provider_and_acceptance_failures_have_distinct_statuses(tmp_path, message, expected):
    path = tmp_path / "live.xml"
    write_junit(path, '<failure message="' + message.replace("&", "&amp;").replace('"', "&quot;") + '">' + message + "</failure>")
    assert classify_live_stream_acceptance("success", "failure", path) == expected
    assert REPORTS[expected]


def test_missing_or_ambiguous_junit_evidence_is_unknown(tmp_path):
    path = tmp_path / "live.xml"
    write_junit(path, test_name="some_other_test")
    assert classify_live_stream_acceptance("success", "success", path) == "UNKNOWN"


def test_explicit_failed_test_without_junit_is_rejected(tmp_path):
    assert classify_live_stream_acceptance("success", "failure", tmp_path / "missing.xml") == "REJECT"


def test_malformed_junit_does_not_become_pass(tmp_path):
    path = tmp_path / "live.xml"
    path.write_text("<testsuite>", encoding="utf-8")
    assert classify_live_stream_acceptance("success", "success", path) == "UNKNOWN"
