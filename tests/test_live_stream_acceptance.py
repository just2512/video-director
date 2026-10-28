from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from video_director.live_stream_acceptance import (
    LIVE_TEST_NAME,
    REPORTS,
    classify_live_stream_acceptance,
    resolve_live_model,
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

def test_main_honors_explicit_junit_path(tmp_path, monkeypatch, capsys):
    import sys

    from video_director import live_stream_acceptance

    path = tmp_path / "explicit-live.xml"
    write_junit(path)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("LIVE_STREAM_CONFIG_OUTCOME", "success")
    monkeypatch.setenv("LIVE_STREAM_TEST_OUTCOME", "success")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(sys, "argv", ["live_stream_acceptance", "--junit", str(path)])

    assert live_stream_acceptance.main() == 0
    assert "LIVE_STREAM_ACCEPTANCE=PASS" in capsys.readouterr().out
    assert "Real-model token-stream acceptance: PASS" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "base_url",
    ["https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/"],
)
def test_retired_model_uses_glm_latest_alias_only_on_official_openrouter(base_url):
    assert resolve_live_model("z-ai/glm-5.3", base_url) == "~z-ai/glm-latest"


@pytest.mark.parametrize(
    "base_url",
    ["https://compatible-proxy.example/v1", "https://models.example/v1", ""],
)
def test_custom_gateway_model_ids_are_not_rewritten_to_openrouter_alias(base_url):
    assert resolve_live_model("z-ai/glm-5.3", base_url) == "z-ai/glm-5.3"


@pytest.mark.parametrize(
    ("model", "base_url", "expected"),
    [
        ("z-ai/glm-5.3-flash", "https://openrouter.ai/api/v1", "z-ai/glm-5.3-flash"),
        ("gpt-4o-mini", "https://openrouter.ai/api/v1", "gpt-4o-mini"),
        ("native-model-v2", "https://compatible-proxy.example/v1", "native-model-v2"),
    ],
)
def test_live_model_resolver_leaves_unrelated_models_unchanged(model, base_url, expected):
    assert resolve_live_model(model, base_url) == expected


def test_live_model_resolver_trims_model_and_endpoint_strings():
    assert resolve_live_model(" z-ai/glm-5.3 ", " https://openrouter.ai/api/v1 ") == "~z-ai/glm-latest"
