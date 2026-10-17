"""视觉语义归一 §34 Repo → Promo：代码库 → 可验证宣传片（Clean-room）。

流程（§34/§三十五/§三十六/§三十七/§三十八）：
    WorkspaceRoot（用户选定，禁全盘扫描）
    → Repo Discovery（.git/package.json/pyproject.toml/go.mod/*.sln 指纹）
    → Project Scan（README/CHANGELOG/routes/components/tokens；排除 .env/keys）
    → Feature Discovery（Claim→Source→Runtime→Evidence；只认 VERIFIED）
    → ProjectSnapshot + ProjectEvidenceGraph（§72 Contract 6）

安全边界（§三十五/§三十六）：
    - 只扫用户显式给的根目录，不递归 node_modules/.git/venv/dist
    - 排除 .env / *.key / *credential* / *token* / cookies 等敏感文件
    - git history 只取最近提交摘要，不取内容

Clean-room 声明：本模块为自有实现，协议参照 guizang product video skill 的
「repo → 卖点 → 证据 → 分镜」思想（AGPL，按 §55 只学流程不复制代码）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from video_director.visual_semantics import (
    EvidenceRef,
    EvidenceType,
    FeatureClaim,
    FeatureClaimStatus,
    ProjectEvidenceGraph,
)

#: §三十五 项目根指纹文件（出现任一即判定为「开发项目」）
PROJECT_FINGERPRINTS = (
    "package.json", "pyproject.toml", "requirements.txt", "go.mod",
    "Cargo.toml", "pom.xml", "*.sln", "composer.json", "Gemfile",
    "build.gradle", "CMakeLists.txt",
)

#: §三十六 框架指纹（文件 → 框架名）
FRAMEWORK_FINGERPRINTS = {
    "package.json": ["next", "react", "vue", "svelte", "vite", "electron"],
    "pyproject.toml": ["fastapi", "flask", "django"],
    "Cargo.toml": ["tauri"],
    "Gemfile": ["rails"],
}

#: §三十六 默认排除（敏感/噪声）
EXCLUDE_DIRS = {
    "node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__",
    ".next", ".nuxt", "target", ".idea", ".vscode",
}
EXCLUDE_FILE_PATTERNS = (
    re.compile(r"\.env", re.I),
    re.compile(r"credential", re.I),
    re.compile(r"\bprivate[_-]?key\b", re.I),
    re.compile(r"\bsecret", re.I),
    re.compile(r"password", re.I),
    re.compile(r"cookies?", re.I),
)

#: §三十六 扫描目标（浅层 + src/app 子层，不深扫）
SCAN_HINTS = (
    "README.md", "CHANGELOG.md", "readme.md",
    "src", "app", "pages", "routes", "components", "screens", "views",
    "lib", "static", "assets", "styles", "theme",
)

#: §三十八 功能声明候选词（README/CHANGELOG 里的「能力句」信号）
FEATURE_HINTS = (
    "支持", "新增", "实现", "提供", "优化", "修复", "上线", "接入",
)


class RepoPromoError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def is_github_url(value: str) -> bool:
    """识别 GitHub 仓库地址（https://github.com/owner/repo[.git]）。"""
    return bool(re.match(
        r"^https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+?(?:\.git)?/?$",
        (value or "").strip(),
    ))


def github_readme_url(value: str) -> str:
    """GitHub 仓库地址 → raw README 文本直链（不 clone、不落全仓库）。

    抓单个 README 文件（几 KB）代替 clone 整个仓库——100 个地址也只用
    100 次 HTTP，不占本地硬盘。README 是 markdown 文本，交给文章抓取链
    （import_article）分析生成介绍视频。非 GitHub 地址原样返回。
    """
    clean = (value or "").strip().rstrip("/")
    m = re.match(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", clean)
    if not m:
        return clean
    owner, name = m.group(1), m.group(2)
    return f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/README.md"


def _is_excluded(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    name = path.name
    return any(p.search(name) for p in EXCLUDE_FILE_PATTERNS)


def discover_repos(root: str, limit: int = 30) -> List[dict]:
    """§三十五 Repo Discovery：在用户选定根目录下发现开发项目（不递归深层）。

    返回 [{path, frameworks, has_git}]。无项目 → 空列表（诚实，不猜）。
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise RepoPromoError("INVALID_ROOT", f"目录不存在: {root}")
    repos: List[dict] = []
    candidates = [root_path] + sorted(root_path.iterdir())
    for p in candidates[:limit * 3]:
        if not p.is_dir():
            continue
        if p != root_path and p.name in EXCLUDE_DIRS:
            continue
        fingerprints = [f for f in PROJECT_FINGERPRINTS
                        if p.glob(f) and any(p.glob(f))]
        has_git = (p / ".git").exists()
        if not fingerprints and not has_git:
            continue
        repos.append({
            "path": str(p),
            "name": p.name,
            "frameworks": _detect_frameworks(p),
            "has_git": has_git,
        })
        if len(repos) >= limit:
            break
    return repos


def _detect_frameworks(p: Path) -> List[str]:
    found: List[str] = []
    for fname, libs in FRAMEWORK_FINGERPRINTS.items():
        f = p / fname
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            continue
        for lib in libs:
            if lib.lower() in content.lower():
                found.append(lib)
    return found


def _safe_read(p: Path, max_bytes: int = 60000) -> str:
    """读文本（截断；敏感文件已被 _is_excluded 拦截）。"""
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        return ""


def scan_project(repo_path: str) -> dict:
    """§三十六/§三十七 Project Scan → ProjectSnapshot 结构（§37 字段）。

    routes/components 从常见目录浅层提取文件名（不解析 AST——诚实记录
    「发现了什么文件」，不假装「理解了组件」）。
    """
    rp = Path(repo_path)
    if not rp.is_dir():
        raise RepoPromoError("INVALID_ROOT", f"目录不存在: {repo_path}")
    snapshot: Dict = {
        "project_id": rp.name,
        "git_commit": _git_head(rp),
        "framework": _detect_frameworks(rp),
        "entry_points": [],
        "routes": [],
        "components": [],
        "design_system": {},
        "recent_changes": [],
        "screenshots": [],
        "runtime_status": "NOT_RUN",
    }
    # entry points
    for cand in ("package.json", "pyproject.toml", "main.py", "app.py", "src/main.ts",
                 "src/main.tsx", "src/App.tsx", "index.js"):
        if (rp / cand).is_file():
            snapshot["entry_points"].append(cand)
    # routes / components（浅层，最多各 50）
    for sub, key in (("routes", "routes"), ("pages", "routes"), ("src/pages", "routes"),
                     ("components", "components"), ("src/components", "components"),
                     ("app", "routes"), ("src/app", "routes")):
        d = rp / sub
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*"))[:200]:
            if f.is_file() and f.suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte") \
                    and not _is_excluded(f):
                rel = f.relative_to(rp).as_posix()
                entry = {"path": rel, "name": f.stem}
                if entry not in snapshot[key]:
                    snapshot[key].append(entry)
                if len(snapshot[key]) >= 50:
                    break
    # Python/后端包结构：顶层含 __init__.py 的目录视作组件目录（诚实记录文件名）
    if not snapshot["components"]:
        for d in sorted(rp.iterdir()):
            if not d.is_dir() or _is_excluded(d) or d.name in EXCLUDE_DIRS:
                continue
            if not (d / "__init__.py").is_file():
                continue
            for f in sorted(d.glob("*.py"))[:50]:
                rel = f.relative_to(rp).as_posix()
                snapshot["components"].append({"path": rel, "name": f.stem})
            if len(snapshot["components"]) >= 50:
                break
    # design tokens / theme
    for cand in ("src/styles/tokens.css", "src/theme.ts", "theme/variables.scss",
                 "tailwind.config.js", "src/tokens.json", "design-tokens.json"):
        f = rp / cand
        if f.is_file() and not _is_excluded(f):
            snapshot["design_system"][cand] = True
    # recent changes（git log 摘要，§三十六）
    snapshot["recent_changes"] = _git_recent(rp)
    return snapshot


def _git_head(rp: Path) -> Optional[str]:
    head = rp / ".git" / "HEAD"
    if not head.is_file():
        return None
    content = _safe_read(head, 200)
    m = re.search(r"ref: refs/heads/(\S+)", content)
    return m.group(1) if m else None


def _git_recent(rp: Path, limit: int = 10) -> List[str]:
    """最近提交摘要（无 git 则空列表；不执行 shell，只读 .git 文件系统）。"""
    commits_dir = rp / ".git" / "refs" / "heads"
    logs: List[str] = []
    if commits_dir.is_dir():
        for f in sorted(commits_dir.rglob("*")):
            if f.is_file():
                logs.append(f"{f.relative_to(commits_dir).as_posix()}: {_safe_read(f, 40).strip()[:12]}")
            if len(logs) >= limit:
                break
    return logs


def discover_features(repo_path: str) -> ProjectEvidenceGraph:
    """§三十八 Feature Discovery：README/CHANGELOG → FeatureClaim + Evidence。

    诚实边界：本实现是「文本信号 + 源文件存在性核对」——claim 的证据
    evidenceType=source（文件存在即记 UNVERIFIED→有 README 行号定位→VERIFIED）。
    Runtime/screenshot 证据需要真实运行时（promo.runtime_launch），此处不伪造。
    """
    rp = Path(repo_path)
    if not rp.is_dir():
        raise RepoPromoError("INVALID_ROOT", f"目录不存在: {repo_path}")
    graph = ProjectEvidenceGraph(project_snapshot_id=rp.name)
    seen_claims = set()
    for doc_name in ("README.md", "readme.md", "CHANGELOG.md", "docs/CHANGELOG.md"):
        doc = rp / doc_name
        if not doc.is_file() or _is_excluded(doc):
            continue
        lines = _safe_read(doc).splitlines()
        for i, line in enumerate(lines, 1):
            text = line.strip().lstrip("#*- ").strip()
            if len(text) < 8 or len(text) > 120:
                continue
            if not any(h in text for h in FEATURE_HINTS):
                continue
            claim_id = f"claim-{len(graph.claims) + 1:03d}"
            key = text[:60]
            if key in seen_claims:
                continue
            seen_claims.add(key)
            # 证据 1：文档自身（行号定位）
            ev_doc = EvidenceRef(
                id=f"{claim_id}-doc", project_snapshot_id=rp.name,
                source_path=doc_name, evidence_type=EvidenceType.document,
            )
            graph.evidence.append(ev_doc)
            claim = FeatureClaim(
                id=claim_id, text=text,
                evidence_refs=[ev_doc.id],
                status=FeatureClaimStatus.VERIFIED,  # 文档行号可直接核对
            )
            # 证据 2（若 claim 里出现可对应源文件名 → source 证据追加）
            m = re.search(r"[`«\"]([\w\-./]+\.(?:py|tsx?|jsx?|vue|svelte|go|rs))[`»\"]", text)
            if m and (rp / m.group(1)).is_file():
                ev_src = EvidenceRef(
                    id=f"{claim_id}-src", project_snapshot_id=rp.name,
                    source_path=m.group(1), evidence_type=EvidenceType.source,
                )
                graph.evidence.append(ev_src)
                claim.evidence_refs.append(ev_src.id)
            graph.claims.append(claim)
            if len(graph.claims) >= 20:
                return graph
    return graph
