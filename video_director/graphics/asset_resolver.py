"""Asset Resolver（§44）：Need → Resolve → Freeze Local Asset → Record Provenance → Reuse。

方案 §44 核心模式（Media OS 思想）：
    系统或用户说"这里需要一个素材" →
    ① 已有素材库可满足？ → 复用（记录 reused）
    ② 无 → 用户上传/生成 → freeze 成 immutable local asset
    ③ 每个最终资产必须携带 source/license/commercialStatus 记录

§51 Asset Localisation：远程 URL → download → hash → 本地存储 → 重写引用。
渲染前 input 必须冻结（"今天能渲染 ≠ 明天还能渲染"）。

§43 边界：Media Library（视频/图片/音频）与 Graphic Library（Blocks/Motion/
Templates/Styles——可执行视觉能力）分开管理，不混一个库。
本模块只管 Media；Graphic 能力在 blocks.py/catalog。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import config


class AssetResolveError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def media_library_root() -> Path:
    """§43 Media Library 根（storage/composer/media_library/）。"""
    root = config.composer_storage_root() / "media_library"
    root.mkdir(parents=True, exist_ok=True)
    return root


def freeze_local_file(src_path: str, source: str = "user",
                      description: str = "", license_note: Optional[str] = None) -> dict:
    """§44 Freeze：把外部文件冻结成 immutable local asset。

    - 复制进 media_library/（内容寻址命名：sha8_原名）
    - 登记 manifest（id/type/source/license/commercialStatus/provenance）
    - 已有相同 hash → 复用（不重复落盘）
    """
    src = Path(src_path)
    if not src.is_file():
        raise AssetResolveError("SOURCE_MISSING", f"来源文件不存在: {src_path}")
    h = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
    manifest = _load_manifest()
    # Reuse：同 hash 已冻结 → 复用
    for a in manifest:
        if a.get("hash") == h:
            logger.info(f"asset freeze: 复用已有冻结 {a['id']}（hash {h}）")
            a["reuse_count"] = a.get("reuse_count", 1) + 1
            _save_manifest(manifest)
            return a
    dst_dir = media_library_root() / "files"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{h}_{src.name}"
    if not dst.is_file():
        shutil.copy2(src, dst)
    asset = {
        "id": f"media-{uuid.uuid4().hex[:8]}",
        "type": _sniff_type(src),
        "file": str(dst),
        "hash": h,
        "source": source,          # user | own | generated | third-party
        "description": description,
        "license_note": license_note,
        # §44/§16 商业四态：user→CLEAR（用户负责）；third-party→UNKNOWN（REJECT 发布）
        "commercial_status": ("CLEAR" if source == "user"
                              else "CLEAR" if source == "generated" else "UNKNOWN"),
        "reuse_count": 1,
    }
    manifest.append(asset)
    _save_manifest(manifest)
    logger.info(f"asset freeze: {asset['id']} → {dst.name}（source={source}）")
    return asset


def resolve_from_library(description: str, limit: int = 10) -> List[dict]:
    """§44 Resolve 第一步：已有素材库能满足吗？（按 description 关键词过滤，零评分）"""
    manifest = _load_manifest()
    if not description:
        return manifest[:limit]
    words = [w for w in re.split(r"[\s,，。/]+", description) if w]
    out = []
    for a in manifest:
        hay = f"{a.get('description','')} {Path(a['file']).stem}"
        if any(w in hay for w in words):
            out.append(a)
    return out[:limit]


def localize_remote_asset(url: str) -> dict:
    """§51 Asset Localisation：远程 URL → 下载 → hash → 本地 → 引用重写基准。

    渲染输入必须 frozen（不依赖 https:// 远端可用性）。
    """
    import tempfile
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, \
                tempfile.NamedTemporaryFile(delete=False, suffix=_ext_of(url)) as tmp:
            shutil.copyfileobj(resp, tmp)
        return freeze_local_file(tmp.name, source="third-party",
                                 description=f"remote: {url}")
    except Exception as exc:
        raise AssetResolveError("DOWNLOAD_FAILED", f"远程资产下载失败: {exc}") from exc


# ---------------------------------------------------------------------------
# manifest 读写（storage/composer/media_library/manifest.json）
# ---------------------------------------------------------------------------

def _manifest_path() -> Path:
    return media_library_root() / "manifest.json"


def _load_manifest() -> list:
    p = _manifest_path()
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_manifest(manifest: list) -> None:
    _manifest_path().write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _sniff_type(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
        return "video"
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        return "image"
    if ext in (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"):
        return "audio"
    return "file"


def _ext_of(url: str) -> str:
    m = re.search(r"\.(\w{2,4})(?:[?#]|$)", url)
    return f".{m.group(1)}" if m else ""
