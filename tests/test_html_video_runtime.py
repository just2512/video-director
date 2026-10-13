"""Adversarial import and real-render tests for html-video-style packages."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from video_director.html_template_runtime import (
    HtmlTemplateError,
    import_template_package,
    render_html_template_sample,
    template_runtime_report,
)
from video_director.registries.templates import TemplateRegistry


def _manifest(package_id: str = "sample-html-motion") -> str:
    return f"""spec_version: 1
id: {package_id}
name: Sample HTML Motion
description: Self-contained local HTML sample for runtime verification.
engine: hyperframes
source_entry: source/index.html
category: presentation
tags: [sample, motion]
output:
  formats: [mp4]
  default_format: mp4
  resolution:
    default: {{width: 320, height: 180}}
    supported_aspects: ["16:9"]
  fps:
    default: 10
    supported: [10, 30]
  duration:
    type: variable
    min_sec: 1.2
    max_sec: 1.2
license:
  spdx: MIT
  attribution_required: true
  redistribution_allowed: true
  commercial_use: true
provenance:
  origin:
    name: Test Fixture
    reference: https://example.invalid/source
version: 1.0.0
"""


def _zip_package(html: str, package_id: str = "sample-html-motion", extra: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("template.html-video.yaml", _manifest(package_id))
        zf.writestr("source/index.html", html)
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return output.getvalue()


_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Local test</title>
<style>
html,body{margin:0;width:100%;height:100%;background:#102030;color:#fff;font-family:sans-serif}
main{width:100%;height:100%;display:grid;place-items:center}
.card{width:70%;height:40%;background:#1a9b80;display:grid;place-items:center;font-size:24px}
</style>
</head><body><main><div class="card">REAL HTML SAMPLE</div></main></body></html>"""


def test_html_package_import_registers_reviewed_metadata_but_never_enables_instantiation(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    result = import_template_package(_zip_package(_HTML))
    assert result["ok"] is True
    assert result["template_id"] == "htmlvideo-sample-html-motion"
    assert result["commercial_status"] == "REVIEW"
    assert result["runtime"]["can_render"] is True
    assert result["runtime"]["can_instantiate"] is False
    assert result["runtime"]["sample_render"] == "NOT_RUN"

    template = TemplateRegistry().get("htmlvideo-sample-html-motion", "1.0.0")
    assert template.runtime["renderer"] == "hyperframes"
    assert template.runtime["html_video_package"] is True
    assert template.scenes[0].layers[0].type == "html"


def test_html_package_import_rejects_zip_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("template.html-video.yaml", _manifest())
        zf.writestr("source/index.html", _HTML)
        zf.writestr("../outside.txt", "not allowed")
    with pytest.raises(HtmlTemplateError, match="目录穿越|相对路径"):
        import_template_package(archive.getvalue())
    assert not (tmp_path / "storage" / "outside.txt").exists()


def test_html_package_is_blocked_when_external_assets_are_not_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    html = _HTML.replace("</head>", '<link rel="stylesheet" href="https://cdn.example.invalid/theme.css"></head>')
    result = import_template_package(_zip_package(html, "external-html-motion"))
    report = result["runtime"]
    assert report["status"] == "BLOCKED"
    assert report["can_render"] is False
    assert any(item["code"] == "EXTERNAL_DEPENDENCY_NOT_BUNDLED" for item in report["blockers"])


def test_html_package_files_are_fingerprinted_and_tampering_blocks_render(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    import_template_package(_zip_package(_HTML, "fingerprinted-motion"))
    template = TemplateRegistry().get("htmlvideo-fingerprinted-motion", "1.0.0")
    entry = storage / "html-template-packages" / "fingerprinted-motion" / "1.0.0" / "source" / "index.html"
    entry.write_text(_HTML + "\n<!-- altered after import -->", encoding="utf-8")
    report = template_runtime_report(template)
    assert report["status"] == "BLOCKED"
    assert report["can_render"] is False
    assert any(item["code"] == "PACKAGE_FINGERPRINT_STALE" for item in report["blockers"])


def test_html_package_produces_real_mp4_and_fingerprint_receipt(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    import_template_package(_zip_package(_HTML, "renderable-html-motion"))
    template = TemplateRegistry().get("htmlvideo-renderable-html-motion", "1.0.0")

    result = render_html_template_sample(template)
    assert result["ok"] is True
    assert result["runtime"]["sample_render"] == "PASS"
    receipt = result["runtime"]["sample_mp4"]
    assert receipt["status"] == "PASS"
    assert receipt["full_decode"] == "PASS"
    assert receipt["video_stream"] == "PASS"
    assert receipt["width"] == 320
    assert receipt["height"] == 180
    assert receipt["duration_seconds"] >= 0.8
    assert len(receipt["package_sha256"]) == 64
    assert len(receipt["mp4_sha256"]) == 64

    mp4 = storage / "html-template-samples" / (result["sample_id"] + ".mp4")
    assert mp4.is_file() and mp4.stat().st_size > 1024
    verified = template_runtime_report(template)
    assert verified["sample_render"] == "PASS"
    assert verified["sample_mp4"]["mp4_sha256"] == receipt["mp4_sha256"]
