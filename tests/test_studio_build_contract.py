"""Hard gates for the real React Studio build and its self-hosted runner contract."""
import json
from pathlib import Path


def test_studio_build_uses_existing_self_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    package_path = root / "studio_ui" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    remotion_package = json.loads((root / "remotion_runtime" / "package.json").read_text(encoding="utf-8"))

    assert "runs-on: [self-hosted, linux, x64]" in workflow
    assert "runs-on: ubuntu-latest" not in workflow
    assert "PY311_BIN: /home/actions/miniforge3/envs/py311/bin" in workflow
    assert "npm ci --no-audit --no-fund --prefer-offline --fetch-retries=2" in workflow
    assert "working-directory: studio_ui" in workflow
    assert "build:studio" in package["scripts"]
    assert "build:studio:css" in package["scripts"]
    assert package["scripts"]["build:studio:css"].startswith("tailwindcss -i film-studio-tailwind.css")
    tailwind_entry = root / "studio_ui" / "film-studio-tailwind.css"
    assert tailwind_entry.is_file()
    assert '@source "../remotion_runtime/src/StudioCanvasInspector.tsx";' in tailwind_entry.read_text(encoding="utf-8")
    assert not any(name.startswith("@remotion/") for name in package["dependencies"])
    assert "@remotion/renderer" in remotion_package["dependencies"]
    assert "React 19 + Tailwind 4" in contract
    assert "第二份权威影片状态" in contract


def test_studio_published_assets_are_real_build_outputs():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "webui" / "static" / "js" / "film-studio-react.js"
    css = root / "webui" / "static" / "css" / "film-studio-tailwind.css"
    lock = root / "studio_ui" / "package-lock.json"

    assert bundle.is_file(), "React Studio bundle must exist after build"
    assert css.is_file(), "Tailwind output must exist after build"
    assert lock.is_file(), "npm dependency lock must be committed with the verified build"
    assert bundle.stat().st_size > 1000, "React Studio bundle is still too small"
    assert css.stat().st_size > 1000, "Tailwind output is still too small"
    assert "Build output placeholder" not in bundle.read_text(encoding="utf-8")


def test_studio_components_share_one_react_root_and_keep_portal_slots():
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")

    assert component.count("createRoot(") == 1
    assert "createPortal" in component
    assert "const StudioReactOwner: React.FC" in component
    assert 'id="fs-react-root"' in html
    assert 'id="fs-side-workbench"' in html
    assert 'id="fs-side-divider"' in html
    assert 'id="fs-side-ai" role="region" aria-label="AI 导演"' in html
    assert 'id="fs-side-inspector" role="region" aria-label="属性检查器"' in html
    assert 'id="fs-side-ai" aria-label="AI 导演" hidden' not in html
    runtime = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "film-studio-inspector-share" in runtime
    assert "if(inspector)inspector.hidden" not in runtime
    assert "if(ai)ai.hidden" not in runtime
    assert "side-split" in runtime
    assert "applySideShare((e.clientY-d.rect.top)/sideHeight*100,true)" in runtime
    for surface in (
        "fs-assets-list", "fs-timeline", "fs-react-toolbar",
        "fs-react-timeline-toolbar", "fs-react-scrub-row",
        "fs-react-ai-director", "fs-inspector", "fs-react-canvas-inspector",
    ):
        assert f'"{surface}"' in component
