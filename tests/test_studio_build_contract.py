"""Hard gates for the real React Studio build and its self-hosted runner contract."""
from pathlib import Path


def test_studio_build_uses_existing_self_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    package = (root / "remotion_runtime" / "package.json").read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, linux, x64]" in workflow
    assert "runs-on: ubuntu-latest" not in workflow
    assert "PY311_BIN: /home/actions/miniforge3/envs/py311/bin" in workflow
    assert "npm ci --no-audit --no-fund --prefer-offline --fetch-retries=2" in workflow
    assert '"build:studio"' in package
    assert '"build:studio:css"' in package
    assert "React 19 + Tailwind 4" in contract
    assert "第二份权威影片状态" in contract


def test_studio_published_assets_are_real_build_outputs():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "webui" / "static" / "js" / "film-studio-react.js"
    css = root / "webui" / "static" / "css" / "film-studio-tailwind.css"
    lock = root / "remotion_runtime" / "package-lock.json"

    assert bundle.is_file(), "React Studio bundle must exist after build"
    assert css.is_file(), "Tailwind output must exist after build"
    assert lock.is_file(), "npm dependency lock must be committed with the verified build"
    assert bundle.stat().st_size > 1000, "React Studio bundle is still too small"
    assert css.stat().st_size > 1000, "Tailwind output is still too small"
    assert "Build output placeholder" not in bundle.read_text(encoding="utf-8")
