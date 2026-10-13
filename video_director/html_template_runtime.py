"""Sandboxed import and real MP4 sample rendering for html-video-style packages.

Imported HTML is a renderable sample package, not yet an editable CompositionIR.
The module therefore exposes sample rendering but never makes these packages
instantiable in the native timeline editor.
"""
from __future__ import annotations

import hashlib
import html
import http.server
import json
import os
import re
import shutil
import socketserver
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml
from playwright.sync_api import sync_playwright

from video_director import config, prober

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 120 * 1024 * 1024
MAX_FILES = 512
MAX_SINGLE_FILE_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][a-zA-Z0-9.-]+)?$")
KNOWN_PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD"}


class HtmlTemplateError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _root() -> Path:
    return config.composer_storage_root() / "html-template-packages"


def _registry_root() -> Path:
    return config.composer_storage_root() / "registries" / "templates" / "html-video-imports"


def _safe_rel(value: Any, field: str = "path") -> str:
    raw = str(value or "").strip()
    if not raw or "\\" in raw or "\x00" in raw:
        raise HtmlTemplateError("INVALID_PACKAGE_PATH", f"{field} 不是有效的相对路径")
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        raise HtmlTemplateError("INVALID_PACKAGE_PATH", f"{field} 不得使用绝对路径或目录穿越")
    if re.match(r"^[A-Za-z]:", raw):
        raise HtmlTemplateError("INVALID_PACKAGE_PATH", f"{field} 不得使用 Windows 盘符路径")
    return parsed.as_posix()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _ResourceParser(HTMLParser):
    """Find resources the browser must load; ordinary hyperlink destinations are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(k).lower(): str(v or "").strip() for k, v in attrs if k}
        for key in ("src", "poster", "data-composition-src", "data-src"):
            if values.get(key):
                self.refs.append(values[key])
        if tag.lower() == "link":
            rel = set((values.get("rel") or "").lower().split())
            if rel.intersection({"stylesheet", "preload", "modulepreload", "icon", "preload"}):
                if values.get("href"):
                    self.refs.append(values["href"])
        style = values.get("style", "")
        self.refs.extend(re.findall(r"url\(\s*['\"]?([^'\")]+)", style, flags=re.I))


def _resource_path(base_file: Path, package_dir: Path, ref: str) -> tuple[Path | None, bool]:
    """Return (local path, external); data/fragments don't need a file."""
    ref = html.unescape(str(ref or "").strip())
    if not ref or ref.startswith("#") or ref.startswith("data:") or ref.startswith("blob:"):
        return None, False
    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc:
        return None, True
    decoded = unquote(parsed.path)
    if not decoded:
        return None, False
    decoded = decoded.replace("\\", "/")
    if decoded.startswith("/"):
        rel = _safe_rel(decoded.lstrip("/"), "resource URL")
        target = package_dir.joinpath(*PurePosixPath(rel).parts)
    else:
        rel = _safe_rel(decoded, "resource URL")
        target = base_file.parent.joinpath(*PurePosixPath(rel).parts)
    root = package_dir.resolve()
    try:
        target = target.resolve(strict=False)
        target.relative_to(root)
    except (OSError, ValueError):
        raise HtmlTemplateError("RESOURCE_PATH_ESCAPE", f"本地资源超出模板包目录：{ref}")
    return target, False


def _scan_package_resources(package_dir: Path, entry: str) -> tuple[list[str], list[str]]:
    """Validate referenced local resources and report non-bundled external requests."""
    external: list[str] = []
    missing: list[str] = []
    visited: set[Path] = set()
    queue = [package_dir / entry]
    css_url = re.compile(r"url\(\s*['\"]?([^'\")]+)", re.I)
    css_import = re.compile(r"@import\s+(?:url\()?['\"]([^'\"]+)['\"]", re.I)
    js_import = re.compile(r"(?:from\s*|import\s*\()\s*['\"]([^'\"]+)['\"]", re.I)

    while queue:
        source = queue.pop()
        try:
            source = source.resolve(strict=True)
            source.relative_to(package_dir.resolve())
        except (OSError, ValueError):
            missing.append(str(source))
            continue
        if source in visited:
            continue
        visited.add(source)
        try:
            if source.stat().st_size > MAX_SINGLE_FILE_BYTES:
                raise HtmlTemplateError("PACKAGE_FILE_TOO_LARGE", f"模板资源超过大小限制：{source.name}")
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            missing.append(source.relative_to(package_dir).as_posix())
            continue

        refs: list[str] = []
        suffix = source.suffix.lower()
        if suffix in {".html", ".htm"}:
            parser = _ResourceParser()
            parser.feed(text)
            refs.extend(parser.refs)
        if suffix == ".css":
            refs.extend(css_url.findall(text))
            refs.extend(css_import.findall(text))
        if suffix in {".js", ".mjs"}:
            refs.extend(js_import.findall(text))
        for ref in refs:
            raw = str(ref or "").strip()
            # Bare JS package imports have no URL scheme but cannot be resolved
            # from an isolated package unless the local file is explicitly present.
            if raw.startswith(("https://", "http://", "//")):
                external.append(raw)
                continue
            if raw.startswith(("data:", "blob:", "#", "mailto:", "tel:")):
                continue
            target, is_external = _resource_path(source, package_dir, raw)
            if is_external:
                external.append(raw)
            elif target is not None:
                if not target.is_file():
                    missing.append(raw)
                elif target.suffix.lower() in {".css", ".js", ".mjs", ".html", ".htm"}:
                    queue.append(target)
    return sorted(set(external)), sorted(set(missing))


def _license_meta(manifest: dict) -> tuple[str, str, dict]:
    lic = manifest.get("license") or {}
    if isinstance(lic, str):
        spdx, lic_obj = lic, {"spdx": lic}
    else:
        lic_obj = lic if isinstance(lic, dict) else {}
        spdx = str(lic_obj.get("spdx") or lic_obj.get("id") or "").strip()
    provenance = manifest.get("provenance") or {}
    provenance_ref = ""
    if isinstance(provenance, dict):
        provenance_ref = str(
            provenance.get("reference") or provenance.get("url")
            or (provenance.get("via_skill") or {}).get("url")
            or (provenance.get("origin") or {}).get("reference") or ""
        ).strip()
    if spdx in KNOWN_PERMISSIVE and provenance_ref:
        return "REVIEW", provenance_ref, {"spdx": spdx, **lic_obj, "review_required": True}
    return "UNKNOWN", provenance_ref, {"spdx": spdx or "UNKNOWN", **lic_obj, "review_required": True}


def _duration_settings(manifest: dict) -> tuple[float, int, int, int]:
    output = manifest.get("output") or {}
    duration = output.get("duration") or {}
    if isinstance(duration, dict):
        duration_sec = duration.get("default_sec", duration.get("recommended_sec"))
        if duration_sec is None:
            duration_sec = duration.get("min_sec", 5)
    else:
        duration_sec = 5
    try:
        duration_sec = float(duration_sec)
    except (TypeError, ValueError):
        duration_sec = 5.0
    if not 0.5 <= duration_sec <= 20:
        raise HtmlTemplateError("INVALID_DURATION", "样片时长必须在 0.5–20 秒之间")
    resolution = output.get("resolution") or {}
    if not isinstance(resolution, dict):
        resolution = {}
    default_resolution = resolution.get("default") or {}
    try:
        width = int(default_resolution.get("width", 1280))
        height = int(default_resolution.get("height", 720))
        fps = int((output.get("fps") or {}).get("default", 30))
    except (TypeError, ValueError, AttributeError):
        raise HtmlTemplateError("INVALID_OUTPUT_PROFILE", "模板输出分辨率或帧率格式无效")
    if not (160 <= width <= 3840 and 90 <= height <= 2160 and 10 <= fps <= 60):
        raise HtmlTemplateError("INVALID_OUTPUT_PROFILE", "样片输出范围为宽 160–3840、高 90–2160、帧率 10–60")
    # Bound the browser sample workload while preserving declared aspect ratio.
    scale = min(1.0, 1280 / width, 720 / height)
    width, height = int(width * scale), int(height * scale)
    width -= width % 2
    height -= height % 2
    return duration_sec, width, height, fps


def _find_manifest_root(zf: zipfile.ZipFile) -> tuple[str, dict[str, zipfile.ZipInfo]]:
    files: dict[str, zipfile.ZipInfo] = {}
    total = 0
    infos = [item for item in zf.infolist() if not item.is_dir()]
    if len(infos) == 0 or len(infos) > MAX_FILES:
        raise HtmlTemplateError("INVALID_PACKAGE_FILE_COUNT", f"包内文件数必须为 1–{MAX_FILES}")
    for info in infos:
        name = info.filename
        if "\\" in name or "\x00" in name:
            raise HtmlTemplateError("INVALID_ARCHIVE_PATH", "ZIP 含有反斜杠或 NUL 路径")
        rel = _safe_rel(name, "ZIP member")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise HtmlTemplateError("SYMLINK_NOT_ALLOWED", f"模板包不允许符号链接：{rel}")
        if info.file_size > MAX_SINGLE_FILE_BYTES:
            raise HtmlTemplateError("PACKAGE_FILE_TOO_LARGE", f"单个模板文件超过 32 MiB：{rel}")
        if info.file_size and info.compress_size == 0:
            raise HtmlTemplateError("INVALID_COMPRESSION_RATIO", f"压缩信息异常：{rel}")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise HtmlTemplateError("INVALID_COMPRESSION_RATIO", f"模板文件压缩比超限：{rel}")
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise HtmlTemplateError("PACKAGE_EXPANDED_TOO_LARGE", "模板包解压后超过 120 MiB")
        if rel in files:
            raise HtmlTemplateError("DUPLICATE_ARCHIVE_PATH", f"ZIP 存在重复路径：{rel}")
        files[rel] = info

    manifests = [p for p in files if PurePosixPath(p).name == "template.html-video.yaml"]
    if len(manifests) != 1:
        raise HtmlTemplateError("MANIFEST_COUNT_INVALID", "ZIP 必须恰好包含一个 template.html-video.yaml")
    manifest_path = manifests[0]
    prefix = str(PurePosixPath(manifest_path).parent)
    if prefix == ".":
        prefix = ""
    normalized: dict[str, zipfile.ZipInfo] = {}
    for path, info in files.items():
        if prefix:
            if not path.startswith(prefix + "/"):
                raise HtmlTemplateError("MULTIPLE_PACKAGE_ROOTS", "模板 ZIP 在清单目录之外还包含文件")
            stripped = path[len(prefix) + 1:]
        else:
            stripped = path
        stripped = _safe_rel(stripped, "ZIP member")
        if stripped in normalized:
            raise HtmlTemplateError("DUPLICATE_ARCHIVE_PATH", f"ZIP 路径归一化后重复：{stripped}")
        normalized[stripped] = info
    return prefix, normalized


def import_template_package(data: bytes, filename: str = "template.zip") -> dict:
    """Validate, atomically install, and register one html-video package."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HtmlTemplateError("PACKAGE_UPLOAD_SIZE_INVALID", "ZIP 上传必须为 1 byte–50 MiB")
    if not zipfile.is_zipfile(BytesIO(data)):
        raise HtmlTemplateError("NOT_A_ZIP", "文件不是有效 ZIP；请压缩模板包目录而不是上传单个 HTML。")
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            prefix, members = _find_manifest_root(zf)
            manifest_name = "template.html-video.yaml"
            manifest_info = members[manifest_name]
            # Read through the original ZIP path; prefix is already normalized.
            raw_manifest = yaml.safe_load(zf.read(manifest_info).decode("utf-8"))
            if not isinstance(raw_manifest, dict):
                raise HtmlTemplateError("INVALID_MANIFEST", "模板 manifest 必须是 YAML 对象")
            template_id = str(raw_manifest.get("id") or "").strip()
            version = str(raw_manifest.get("version") or "").strip()
            if not SLUG_RE.fullmatch(template_id):
                raise HtmlTemplateError("INVALID_TEMPLATE_ID", "manifest.id 必须为小写字母、数字、点、下划线或连字符")
            if not VERSION_RE.fullmatch(version):
                raise HtmlTemplateError("INVALID_TEMPLATE_VERSION", "manifest.version 必须使用显式版本号")
            if str(raw_manifest.get("engine") or "").strip().lower() != "hyperframes":
                raise HtmlTemplateError("ENGINE_NOT_SUPPORTED", "当前 HTML 包适配器只接受 engine: hyperframes")
            source_entry = _safe_rel(raw_manifest.get("source_entry"), "source_entry")
            if source_entry not in members:
                raise HtmlTemplateError("SOURCE_ENTRY_MISSING", f"manifest.source_entry 不在 ZIP 中：{source_entry}")
            duration, width, height, fps = _duration_settings(raw_manifest)
            spdx = (raw_manifest.get("license") or {})
            if isinstance(spdx, dict):
                spdx = spdx.get("spdx") or spdx.get("id") or ""
            # Refuse to register code under a registry identity with conflicting contents.
            package_sha = _sha256(data)
            package_id = f"{template_id}@{version}"
            package_dir = _root() / template_id / version
            registry_id = "htmlvideo-" + template_id
            registry_file = _registry_root() / f"{template_id}-{version.replace('.', '_')}.json"
            if package_dir.exists() or registry_file.exists():
                raise HtmlTemplateError("PACKAGE_VERSION_EXISTS", f"{package_id} 已导入；请升级版本，不会覆盖现有包")

            _root().mkdir(parents=True, exist_ok=True)
            package_dir.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".html-package-", dir=str(_root())))
            try:
                for name, info in members.items():
                    target = staging.joinpath(*PurePosixPath(name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                source = staging.joinpath(*PurePosixPath(source_entry).parts)
                if not source.is_file():
                    raise HtmlTemplateError("SOURCE_ENTRY_MISSING", f"HTML 入口不存在：{source_entry}")
                external, missing = _scan_package_resources(staging, source_entry)
                if not external and not missing:
                    scan_status = "PASS"
                else:
                    scan_status = "BLOCKED"
                manifest_bytes = (staging / manifest_name).read_bytes()
                manifest_sha = _sha256(manifest_bytes)
                license_status, provenance_ref, license_metadata = _license_meta(raw_manifest)
                name = str(raw_manifest.get("name") or template_id).strip()[:160]
                category = str(raw_manifest.get("category") or "html-video").strip()[:80]
                ratios = ((raw_manifest.get("output") or {}).get("resolution") or {}).get("supported_aspects") or ["16:9"]
                supported_ratios = [str(value) for value in ratios if str(value) in {"16:9", "9:16", "1:1", "3:4", "4:5"}]
                if not supported_ratios:
                    supported_ratios = ["16:9"]
                description = str(raw_manifest.get("description") or "").strip()[:1600]
                if license_status == "UNKNOWN":
                    provenance_ref = None
                registry_item = {
                    "id": registry_id,
                    "version": version,
                    "kind": "template",
                    "name": name,
                    "description": description,
                    "tags": list(raw_manifest.get("tags") or [])[:40],
                    "commercial_status": license_status,
                    "provenance_ref": provenance_ref,
                    "enabled": True,
                    "category": category,
                    "supported_ratios": supported_ratios,
                    "duration": {"min": duration, "recommended": duration, "max": duration},
                    "style_contract": {},
                    "slots": [],
                    "scenes": [{
                        "id": "html-frame",
                        "duration": duration,
                        "semantic_role": "body",
                        "layers": [{"id": "html-source", "type": "html"}],
                    }],
                    "required_capabilities": [],
                    "recipe_bindings": [],
                    "runtime": {
                        "renderer": "hyperframes",
                        "html_video_package": True,
                        "package_id": template_id,
                        "package_version": version,
                        "source_entry": source_entry,
                        "package_sha256": package_sha,
                        "manifest_sha256": manifest_sha,
                        "license": license_metadata,
                        "fps": fps,
                        "width": width,
                        "height": height,
                        "duration_sec": duration,
                        "dependency_scan": {
                            "status": scan_status,
                            "external_resources": external[:100],
                            "missing_resources": missing[:100],
                        },
                    },
                }
                _registry_root().mkdir(parents=True, exist_ok=True)
                package_dir.parent.mkdir(parents=True, exist_ok=True)
                package_dir = package_dir.resolve()
                staging.replace(package_dir)
                registry_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    temp_registry = registry_file.with_suffix(".json.tmp")
                    temp_registry.write_text(json.dumps(registry_item, ensure_ascii=False, indent=2), encoding="utf-8")
                    temp_registry.replace(registry_file)
                except Exception:
                    shutil.rmtree(package_dir, ignore_errors=True)
                    raise
                return {
                    "ok": True,
                    "template_id": registry_id,
                    "package_id": package_id,
                    "template_version": version,
                    "name": name,
                    "commercial_status": license_status,
                    "license": license_metadata,
                    "package_sha256": package_sha,
                    "manifest_sha256": manifest_sha,
                    "runtime": html_template_runtime_report_from_record(registry_item, package_dir),
                    "message": "HTML 视频包已导入模板库。它目前可运行隔离样片，但尚不能直接实例化为多轨可编辑项目。",
                }
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
    except HtmlTemplateError:
        raise
    except (zipfile.BadZipFile, UnicodeDecodeError, yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        raise HtmlTemplateError("INVALID_TEMPLATE_PACKAGE", f"模板包解析失败：{exc}") from exc


def _record_for(template: Any) -> dict:
    runtime = getattr(template, "runtime", {}) or {}
    if runtime.get("renderer") != "hyperframes" or not runtime.get("html_video_package"):
        raise HtmlTemplateError("NOT_HTML_TEMPLATE_PACKAGE", "该模板不是 HTML 视频包")
    return runtime


def _manifest_path(runtime: dict) -> Path:
    package_id = str(runtime.get("package_id") or "")
    version = str(runtime.get("package_version") or "")
    if not SLUG_RE.fullmatch(package_id) or not VERSION_RE.fullmatch(version):
        raise HtmlTemplateError("INVALID_PACKAGE_IDENTITY", "模板注册中的包标识无效")
    root = _root().resolve()
    package_dir = (root / package_id / version).resolve()
    try:
        package_dir.relative_to(root)
    except ValueError as exc:
        raise HtmlTemplateError("PACKAGE_PATH_ESCAPE", "模板包目录超出受信任根目录") from exc
    if not package_dir.is_dir():
        raise HtmlTemplateError("PACKAGE_NOT_FOUND", "模板包文件目录不存在；请重新导入模板 ZIP")
    entry = _safe_rel(runtime.get("source_entry"), "source_entry")
    source = (package_dir / entry).resolve()
    try:
        source.relative_to(package_dir)
    except ValueError as exc:
        raise HtmlTemplateError("SOURCE_ENTRY_ESCAPE", "HTML 入口路径超出模板包目录") from exc
    if not source.is_file():
        raise HtmlTemplateError("SOURCE_ENTRY_MISSING", f"HTML 入口不存在：{entry}")
    manifest_file = package_dir / "template.html-video.yaml"
    if not manifest_file.is_file() or _sha256_file(manifest_file) != runtime.get("manifest_sha256"):
        raise HtmlTemplateError("PACKAGE_FINGERPRINT_STALE", "模板 manifest 已变化；运行前门禁拒绝使用未验证版本")
    return source


def html_template_runtime_report_from_record(record: dict, package_dir: Path) -> dict:
    runtime = record.get("runtime") or {}
    blockers: list[dict] = []
    warnings: list[dict] = []
    installed: dict[str, Any] = {}
    try:
        source = _manifest_path_for_record(runtime, package_dir)
        installed["source_entry"] = True
        external, missing = _scan_package_resources(package_dir, runtime.get("source_entry") or "")
        if external:
            blockers.append({
                "code": "EXTERNAL_DEPENDENCY_NOT_BUNDLED",
                "message": "模板引用了未打包的远程资源；为避免联网依赖与渲染差异，已阻止执行。",
                "resources": external[:20],
            })
        if missing:
            blockers.append({
                "code": "PACKAGE_RESOURCE_MISSING",
                "message": "模板引用的本地资源缺失：" + "、".join(missing[:12]),
                "resources": missing[:30],
            })
    except HtmlTemplateError as exc:
        blockers.append({"code": exc.code, "message": str(exc)})
        installed["source_entry"] = False

    ffmpeg = prober._resolve_ffmpeg()
    ffprobe = prober._resolve_ffprobe()
    installed["ffmpeg"] = bool(ffmpeg)
    installed["ffprobe"] = bool(ffprobe)
    if not ffmpeg:
        blockers.append({"code": "FFMPEG_NOT_INSTALLED", "message": "找不到可执行的 FFmpeg。"})
    if not ffprobe:
        blockers.append({"code": "FFPROBE_NOT_INSTALLED", "message": "找不到可执行的 ffprobe，无法核验输出 MP4。"})
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        installed["chromium"] = True
    except Exception as exc:
        installed["chromium"] = False
        blockers.append({"code": "CHROMIUM_NOT_INSTALLED", "message": f"Playwright Chromium 不可运行：{str(exc)[:200]}"})

    dep = runtime.get("dependency_scan") or {}
    if dep.get("status") == "BLOCKED":
        # Keep import visibly blocked immediately; report the exact package issue too.
        if dep.get("external_resources"):
            blockers.append({
                "code": "EXTERNAL_DEPENDENCY_NOT_BUNDLED",
                "message": "导入时发现外部资源，模板包尚未自包含；不能把它标为可执行。",
                "resources": dep["external_resources"][:20],
            })
        if dep.get("missing_resources"):
            blockers.append({
                "code": "PACKAGE_RESOURCE_MISSING",
                "message": "导入时发现缺失本地资源。",
                "resources": dep["missing_resources"][:20],
            })
    sample = _validated_sample_receipt(record)
    status = "BLOCKED" if blockers else "READY"
    return {
        "template_id": record.get("id", ""),
        "template_version": record.get("version", ""),
        "engine": "hyperframes",
        "adapter": "html-video-python-playwright",
        "status": status,
        "can_instantiate": False,
        "can_render": not blockers,
        "sample_render": "PASS" if sample else "NOT_RUN",
        "sample_mp4": sample,
        "installed": installed,
        "package_id": runtime.get("package_id"),
        "source_entry": runtime.get("source_entry"),
        "package_sha256": runtime.get("package_sha256"),
        "license": runtime.get("license") or {"spdx": "UNKNOWN", "review_required": True},
        "warnings": warnings + [{
            "code": "HTML_SAMPLE_NOT_EDITABLE_COMPOSITION",
            "message": "该包可验证独立 HTML 样片，但尚未转换为 CompositionIR；“创建可编辑项目”保持禁用。",
        }],
        "blockers": blockers,
        "contract": "dependency preflight + isolated browser render + ffprobe + full FFmpeg decode; no automatic CompositionIR conversion",
    }


def _manifest_path_for_record(runtime: dict, package_dir: Path) -> Path:
    source = (package_dir / _safe_rel(runtime.get("source_entry"), "source_entry")).resolve()
    try:
        source.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise HtmlTemplateError("SOURCE_ENTRY_ESCAPE", "HTML 入口越出包目录") from exc
    if not source.is_file():
        raise HtmlTemplateError("SOURCE_ENTRY_MISSING", f"HTML 入口不存在：{source.name}")
    manifest = package_dir / "template.html-video.yaml"
    if not manifest.is_file() or _sha256_file(manifest) != runtime.get("manifest_sha256"):
        raise HtmlTemplateError("PACKAGE_FINGERPRINT_STALE", "manifest SHA-256 不匹配")
    return source


def _validated_sample_receipt(record: dict) -> dict | None:
    runtime = record.get("runtime") or {}
    receipt_dir = config.composer_storage_root() / "html-template-samples" / str(runtime.get("package_id") or "")
    receipt = receipt_dir / (str(runtime.get("package_version") or "") + ".json")
    if not receipt.is_file():
        return None
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        mp4 = Path(str(data.get("path") or "")).resolve()
        expected_root = (config.composer_storage_root() / "html-template-samples").resolve()
        mp4.relative_to(expected_root)
        if data.get("package_sha256") != runtime.get("package_sha256") or not mp4.is_file():
            return None
        if _sha256_file(mp4) != data.get("mp4_sha256"):
            return None
        return {k: v for k, v in data.items() if k != "path"}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def template_runtime_report(template: Any) -> dict:
    """Public dispatcher used by the existing Template Gallery and runtime endpoint."""
    runtime = getattr(template, "runtime", {}) or {}
    if runtime.get("renderer") == "hyperframes" and runtime.get("html_video_package"):
        try:
            package_dir = _root() / str(runtime.get("package_id")) / str(runtime.get("package_version"))
            record = {
                "id": getattr(template, "id", ""),
                "version": getattr(template, "version", ""),
                "runtime": runtime,
            }
            return html_template_runtime_report_from_record(record, package_dir)
        except Exception as exc:
            return {
                "template_id": getattr(template, "id", ""),
                "template_version": getattr(template, "version", ""),
                "engine": "hyperframes",
                "status": "BLOCKED",
                "can_instantiate": False,
                "can_render": False,
                "sample_render": "NOT_RUN",
                "installed": {},
                "warnings": [],
                "blockers": [{"code": "HTML_PACKAGE_PREFLIGHT_FAILED", "message": str(exc)[:320]}],
                "contract": "failed closed",
            }
    # Import lazily so the HTML adapter can be tested without altering native templates.
    from video_director.registries.runtime import template_runtime_report as native_report
    return native_report(template)


def _write_receipt(record: dict, mp4: Path, probe: Any, package_sha: str) -> dict:
    root = (config.composer_storage_root() / "html-template-samples").resolve()
    mp4 = mp4.resolve()
    mp4.relative_to(root)
    receipt = {
        "status": "PASS",
        "package_id": record["runtime"]["package_id"],
        "package_version": record["runtime"]["package_version"],
        "package_sha256": package_sha,
        "mp4_sha256": _sha256_file(mp4),
        "duration_seconds": round(float(probe.duration), 3),
        "width": int(probe.width),
        "height": int(probe.height),
        "fps": float(probe.fps or record["runtime"].get("fps") or 30),
        "video_stream": "PASS",
        "full_decode": "PASS",
        "bytes": mp4.stat().st_size,
        "rendered_at_epoch": int(time.time()),
        "path": str(mp4),
    }
    receipt_dir = root / record["runtime"]["package_id"]
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / (record["runtime"]["package_version"] + ".json")
    temp = receipt_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(receipt_path)
    return {k: v for k, v in receipt.items() if k != "path"}


def render_html_template_sample(template: Any, output_root: Path | None = None) -> dict:
    """Render the imported entry page in an isolated local-only Chromium context, then verify MP4."""
    import shutil as _shutil

    runtime = _record_for(template)
    package_dir = (_root() / str(runtime.get("package_id")) / str(runtime.get("package_version"))).resolve()
    source_entry = _manifest_path(runtime)
    package_root = _root().resolve()
    package_dir.relative_to(package_root)
    external, missing = _scan_package_resources(package_dir, runtime["source_entry"])
    if external:
        raise HtmlTemplateError("EXTERNAL_DEPENDENCY_NOT_BUNDLED", "发现未打包的外部资源：" + "、".join(external[:12]))
    if missing:
        raise HtmlTemplateError("PACKAGE_RESOURCE_MISSING", "发现缺失的本地资源：" + "、".join(missing[:12]))

    ffmpeg = prober._resolve_ffmpeg()
    ffprobe = prober._resolve_ffprobe()
    if not ffmpeg or not ffprobe:
        raise HtmlTemplateError("MEDIA_TOOLS_MISSING", "必须同时具备 ffmpeg 与 ffprobe 才能渲染/验证 MP4")
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as exc:
        raise HtmlTemplateError("CHROMIUM_ADAPTER_MISSING", f"Playwright 不可用：{exc}") from exc

    record = {
        "id": getattr(template, "id", ""),
        "version": getattr(template, "version", ""),
        "runtime": runtime,
    }
    # Build receipt/samples under a dedicated root so they cannot be confused with
    # native Composition template samples.
    sample_root = (output_root or (config.composer_storage_root() / "html-template-samples")).resolve()
    sample_root.mkdir(parents=True, exist_ok=True)
    sample_id = uuid.uuid4().hex
    work_root = Path(tempfile.mkdtemp(prefix="html-sample-", dir=str(sample_root)))
    record_dir = work_root / "record"
    record_dir.mkdir()
    mp4_out = sample_root / f"{sample_id}.mp4"
    duration = float(runtime.get("duration_sec") or 5)
    width, height, fps = int(runtime.get("width") or 1280), int(runtime.get("height") or 720), int(runtime.get("fps") or 30)

    class _NoLogHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

    handler = lambda *args, **kwargs: _NoLogHandler(*args, directory=str(package_dir), **kwargs)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}/"
    recording_started = time.monotonic()
    animation_started = None
    webm_path = None
    try:
        entry_url = base_url + "/".join(__import__("urllib.parse", fromlist=["quote"]).quote(part) for part in PurePosixPath(runtime["source_entry"]).parts)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    record_video={"dir": str(record_dir), "size": {"width": width, "height": height}},
                )
                page = context.new_page()
                page.route("**/*", lambda route: route.continue_() if (
                    urlsplit(route.request.url).scheme == "http"
                    and urlsplit(route.request.url).hostname == "127.0.0.1"
                    and urlsplit(route.request.url).port == server.server_port
                ) else route.abort())
                page.add_init_script("""
                    (() => {
                      const install = () => {
                        const style = document.createElement('style');
                        style.id = '__vd_html_freeze';
                        style.textContent = '*,*::before,*::after{animation-play-state:paused!important;-webkit-animation-play-state:paused!important;transition:none!important}';
                        (document.head || document.documentElement).appendChild(style);
                        window.__vdHtmlUnfreeze = () => {
                          document.getElementById('__vd_html_freeze')?.remove();
                          document.querySelectorAll('svg').forEach(svg => {
                            try { svg.unpauseAnimations?.(); } catch (_) {}
                          });
                        };
                        window.__vdHtmlFreezeSvg = () => document.querySelectorAll('svg').forEach(svg => {
                          try { svg.pauseAnimations?.(); } catch (_) {}
                        });
                      };
                      if (document.documentElement) install();
                      else document.addEventListener('DOMContentLoaded', install, {once:true});
                    })();
                """)
                page.goto(entry_url, wait_until="domcontentloaded", timeout=15000)
                page.evaluate("""async () => {
                  await Promise.race([
                    (document.fonts && document.fonts.ready) || Promise.resolve(),
                    new Promise(resolve => setTimeout(resolve, 5000))
                  ]);
                  window.__vdHtmlFreezeSvg?.();
                }""")
                animation_started = time.monotonic()
                page.evaluate("window.__vdHtmlUnfreeze?.()")
                page.wait_for_timeout(int(duration * 1000))
                webm_path = Path(page.video.path())
                context.close()
            finally:
                browser.close()
        if not webm_path or not webm_path.is_file() or webm_path.stat().st_size < 1024:
            raise HtmlTemplateError("BROWSER_CAPTURE_FAILED", "Chromium 没有产生有效的 WebM 录制")
        lead_in = max(0.0, float(animation_started - recording_started)) if animation_started else 0.0
        encoded = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{lead_in:.3f}", "-i", str(webm_path),
             "-t", f"{duration:.3f}", "-r", str(fps), "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4_out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
        )
        if encoded.returncode != 0 or not mp4_out.is_file() or mp4_out.stat().st_size < 1024:
            raise HtmlTemplateError("MP4_ENCODE_FAILED", "FFmpeg 编码失败：" + (encoded.stderr or "")[-1000:])
        probe = prober.probe_media(str(mp4_out))
        if not probe.has_video_stream or not probe.duration or probe.duration < min(duration * 0.6, duration - 0.4):
            raise HtmlTemplateError("MP4_PROBE_FAILED", "MP4 视频流或输出时长未满足要求")
        decoded = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(mp4_out), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
        )
        if decoded.returncode != 0:
            raise HtmlTemplateError("MP4_FULL_DECODE_FAILED", "MP4 全文件解码失败：" + (decoded.stderr or "")[-1000:])

        published = sample_root / (sample_id + ".mp4")
        if published != mp4_out:
            _shutil.copy2(mp4_out, published)
        public_record = _write_receipt(record, published, probe, str(runtime["package_sha256"]))
        return {
            "ok": True,
            "sample_id": sample_id,
            "template_id": getattr(template, "id", ""),
            "template_version": getattr(template, "version", ""),
            "download_url": "/api/template_sample/download?id=" + sample_id,
            "runtime": {
                **template_runtime_report(template),
                "sample_render": "PASS",
                "sample_mp4": public_record,
            },
            "message": "Chromium 实际录制、FFmpeg 编码、ffprobe 检查和 MP4 全文件解码全部通过；该 HTML 包仍未转换为可编辑多轨项目。",
        }
    finally:
        try:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        finally:
            _shutil.rmtree(work_root, ignore_errors=True)
            try:
                if mp4_out.exists():
                    mp4_out.unlink()
            except OSError:
                pass
