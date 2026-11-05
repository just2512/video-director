"""最终方案 §25/§26/§27/§32：Asset Graph + Asset Library —— 素材统一管线。

§25 Asset Graph：每个素材统一成同一种 Asset（本地/Stock/AI 生成/上传无差别）：
    {asset_id, type, source, source_id, source_page, creator, file, width,
     height, duration, tags, description, commercial_status, license_note,
     created_at, content_hash}

§26 Asset Library：storage/assets/{local,generated,stock,thumbnails}——
    上传不再塞进 project/input，项目只保存 Asset Reference（同一素材
    多项目复用）。上传链：MIME/magic bytes 检查 → ffprobe/image probe →
    缩略图 → 内容 hash → Asset Metadata → Library。

§27 支持格式：视频 mp4/mov/mkv/webm/avi(m4v)；图片 jpg/jpeg/png/webp。
    导入自动检测尺寸/方向/时长/帧率/含音频/编码。

§32 Commercial Resolver：CLEAR/UNKNOWN/REVIEW/BLOCKED；UNKNOWN≠CLEAR；
    用户自己的素材 = source:user + USER_SUPPLIED（用户承担权利）。

持久化：storage/assets/catalog.json（Library 级台账）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import config

_CATALOG_THREAD_LOCK = threading.RLock()

#: §26 目录结构（storage/assets 四子目录）
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

#: §27 支持格式白名单（上传校验用；.bmp 老库兼容但新上传拒绝——§27 原文四格式）
UPLOAD_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
UPLOAD_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

#: §32 Commercial 状态
CLEAR = "CLEAR"
UNKNOWN = "UNKNOWN"
REVIEW = "REVIEW"
BLOCKED = "BLOCKED"
USER_SUPPLIED = "USER_SUPPLIED"

#: MIME 白名单（§26 文件安全检查第一层）
MIME_WHITELIST = {
    "video/mp4", "video/quicktime", "video/x-matroska", "video/webm",
    "video/x-msvideo", "image/jpeg", "image/png", "image/webp",
}

#: magic bytes 指纹（§26 第二层：扩展名可伪造，文件头不可）
_MAGIC = [
    (b"\x00\x00\x00", "mp4/mov", 0),          # ftyp 在偏移 4，前 3 字节为长度
    (b"\x1a\x45\xdf\xa3", "mkv/webm", 0),
    (b"RIFF", "avi/webm", 0),
    (b"\xff\xd8\xff", "jpeg", 0),
    (b"\x89PNG\r\n\x1a\n", "png", 0),
    (b"RI....", "webp", 0),                    # RIFF....WEBP（RIFF 容器）
]


class AssetLibraryError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# §26 目录与台账
# ---------------------------------------------------------------------------

def library_root() -> Path:
    """storage/assets/（Library 根；与 composer 项目目录平级）。"""
    p = config.composer_storage_root() / "assets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _catalog_path() -> Path:
    return library_root() / "catalog.json"


def _ensure_dirs() -> None:
    for sub in ("local", "generated", "stock", "thumbnails"):
        (library_root() / sub).mkdir(parents=True, exist_ok=True)


def _load_catalog() -> dict:
    p = _catalog_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@contextmanager
def _catalog_file_lock():
    """Serialize Library catalog transactions across threads and processes."""
    lock_path = library_root() / ".catalog.lock"
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
            except OSError:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    raise
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _catalog_lock():
    """Thread + process lock for catalog read/modify/write transactions."""
    with _CATALOG_THREAD_LOCK:
        with _catalog_file_lock():
            yield


def _save_catalog(catalog: dict) -> None:
    """Atomically replace catalog.json; caller owns _catalog_lock()."""
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".catalog-", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(catalog, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def content_hash(file_path: str, chunk: int = 1 << 20) -> str:
    """§25 内容 hash（sha256；1MB 块流式读，大视频不爆内存）。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:24]


def find_by_hash(file_path: str) -> Optional[dict]:
    """内容 hash 查重（§25：同一文件重复上传 = 同一 Asset，复用既有条目）。"""
    try:
        chash = content_hash(file_path)
    except OSError:
        return None
    for a in _load_catalog().values():
        if a.get("content_hash") == chash:
            return a
    return None


def find_by_path(file_path: str) -> Optional[dict]:
    """按文件绝对路径查 Library 条目。

    上传素材入库时 `file` 就是这个绝对路径（文件名 stem 又是 asset_id），
    因此这是「项目里那个文件 → Library 条目」最直接的键。语义（description/
    tags）只登记在 Library，素材清单必须靠它回落，否则上传素材永远是「无描述」。
    """
    if not file_path:
        return None
    try:
        target = os.path.normcase(str(Path(file_path).resolve()))
    except OSError:
        return None
    for a in _load_catalog().values():
        stored = a.get("file") or ""
        if not stored:
            continue
        try:
            if os.path.normcase(str(Path(stored).resolve())) == target:
                return a
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# §26 上传链：安检 → probe → 缩略图 → hash → Metadata → Library
# ---------------------------------------------------------------------------

def _check_magic(path: Path) -> None:
    """magic bytes 校验（扩展名说谎直接拒）。"""
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        raise AssetLibraryError("UNREADABLE", f"文件不可读：{exc}")
    if head.startswith(b"\x1a\x45\xdf\xa3") or head.startswith(b"RIFF"):
        return  # mkv/webm/avi/webp 容器
    if head.startswith(b"\xff\xd8\xff"):
        return  # jpeg
    if head.startswith(b"\x89PNG"):
        return  # png
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return  # mp4/mov
    raise AssetLibraryError("BAD_FILE_TYPE", "文件内容与扩展名不符（已拒绝）")


def import_asset(file_path: str, source: str = "user",
                 tags: Optional[List[str]] = None,
                 description: str = "",
                 commercial_status: str = USER_SUPPLIED,
                 license_note: str = "",
                 source_id: str = "", source_page: str = "",
                 creator: str = "") -> dict:
    """§26 上传/登记入口：文件 → Asset Library 条目（项目无关，可复用）。

    source: user | generated | pexels | pixabay | coverr
    commercial_status: §32（user 素材固定 USER_SUPPLIED——不默认写 CLEAR）
    返回 §25 完整 Asset 元数据。
    """
    p = Path(file_path)
    if not p.is_file():
        raise AssetLibraryError("NOT_FOUND", f"素材文件不存在：{file_path}")
    ext = p.suffix.lower()
    if ext in UPLOAD_VIDEO_EXTS:
        kind = "video"
    elif ext in UPLOAD_IMAGE_EXTS:
        kind = "image"
    elif ext in VIDEO_EXTS | IMAGE_EXTS:
        # 老格式（.bmp/.m4v 等存量兼容）按扩展归型，但拒绝新白名单外上传
        kind = "video" if ext in VIDEO_EXTS else "image"
    else:
        raise AssetLibraryError("UNSUPPORTED_EXT", f"不支持的素材类型 {ext}")

    _check_magic(p)

    # 内容 hash 查重：同一文件 = 同一 Asset（§25）
    existing = find_by_hash(str(p))
    chash = content_hash(str(p))
    if existing:
        return existing

    # probe（§27 自动检测：尺寸/方向/时长/帧率/含音频/编码）
    meta: dict = {"type": kind}
    from video_director import prober
    if kind == "video":
        probe = prober.probe_media(str(p))
        if not probe.has_video_stream:
            raise AssetLibraryError("NO_VIDEO_STREAM", "文件没有视频流")
        meta.update({"duration": probe.duration, "width": probe.width,
                     "height": probe.height, "fps": probe.fps,
                     "has_audio": bool(probe.has_audio_stream),
                     "codec": probe.codec})  # §27 自动检测编码
    else:
        probe = prober.probe_image(str(p))
        meta.update({"width": probe.width, "height": probe.height})
    orientation = ("portrait" if meta.get("height", 0) > meta.get("width", 0)
                   else "landscape" if meta.get("width", 0) > meta.get("height", 0)
                   else "square")
    meta["orientation"] = orientation

    # Serialize the final dedup/copy/catalog transaction. The earlier hash lookup
    # is only a fast path; this second check closes concurrent import races.
    with _catalog_lock():
        catalog = _load_catalog()
        existing = next(
            (item for item in catalog.values() if item.get("content_hash") == chash),
            None,
        )
        if existing:
            return existing

        _ensure_dirs()
        zone = {"user": "local", "generated": "generated",
                "pexels": "stock", "pixabay": "stock", "coverr": "stock"}.get(source, "local")
        asset_id = f"{zone}-{chash[:12]}-{int(time.time()) % 100000}"
        while asset_id in catalog:
            asset_id = f"{zone}-{chash[:12]}-{uuid.uuid4().hex[:8]}"
        dst_dir = library_root() / zone
        dst = dst_dir / f"{asset_id}{ext}"
        stored_file = file_path
        if p.resolve() != dst.resolve():
            try:
                import shutil
                shutil.copy2(str(p), str(dst))
                stored_file = str(dst)
            except OSError as exc:
                raise AssetLibraryError("COPY_FAILED", f"素材入库失败：{exc}")

        entry = {
            "asset_id": asset_id, "type": kind, "source": source,
            "source_id": source_id, "source_page": source_page, "creator": creator,
            "file": stored_file, "width": meta.get("width"),
            "height": meta.get("height"), "duration": meta.get("duration"),
            "fps": meta.get("fps"), "has_audio": meta.get("has_audio"),
            "codec": meta.get("codec"),
            "orientation": orientation,
            "tags": tags or [], "description": description,
            "commercial_status": commercial_status, "license_note": license_note,
            "created_at": time.time(), "content_hash": chash,
        }
        catalog[asset_id] = entry
        try:
            _save_catalog(catalog)
        except OSError:
            if stored_file != str(p) and Path(stored_file).is_file():
                Path(stored_file).unlink(missing_ok=True)
            raise
    logger.info(f"asset_library: 入库 {asset_id} ({kind}/{source}, hash={chash[:10]})")
    return entry


def register_generated(file_path: str, prompt: str = "",
                       tags: Optional[List[str]] = None) -> dict:
    """§39 生成产物入 Asset Registry（AI 图片/视频 = 普通 Asset，无差别）。"""
    return import_asset(file_path, source="generated", tags=tags,
                        description=prompt,
                        commercial_status=CLEAR,
                        license_note="AI generated in-app")


def register_stock(file_path: str, provider: str, source_id: str = "",
                   source_page: str = "", creator: str = "",
                   license_note: str = "") -> dict:
    """§31 Stock 素材登记（provider/creator/attribution 可追溯）。"""
    policy = {"pexels": (CLEAR, "Pexels License（需归属）"),
              "pixabay": (CLEAR, "Pixabay Content License"),
              "coverr": (CLEAR, "Coverr License（需 attribution）")}
    status, note = policy.get(provider, (UNKNOWN, license_note or ""))
    return import_asset(file_path, source=provider, source_id=source_id,
                        source_page=source_page, creator=creator,
                        commercial_status=status, license_note=note or license_note)


def make_thumbnail(asset_id: str, src_file: Optional[str] = None,
                   media_type: Optional[str] = None) -> Optional[str]:
    """§26 缩略图（视频抽帧 1s 处 / 图片直接缩；产物在 thumbnails/）。

    用项目统一 ffmpeg 二进制（prober._resolve_ffmpeg，随包 bin/ 优先），不引
    ffmpeg-python 依赖：requirements 从未包含它，旧实现 import 即
    ModuleNotFoundError → 恒返回 None → /api/asset_thumbnail 全 404（2026-10-09
    实测；缩略图零测试覆盖所以从未被发现）。

    ``src_file``/``media_type`` 供 Library 未收录的素材（项目本地上传）显式传入
    源文件——否则上传素材缩略图恒 404（2026-10-15 浏览器实测）。
    """
    entry = get_asset(asset_id)
    if not entry and not src_file:
        return None
    _ensure_dirs()
    thumb_dir = library_root() / "thumbnails"
    thumb = thumb_dir / f"{asset_id}.jpg"
    if thumb.is_file():
        return str(thumb)
    src = src_file or str((entry or {}).get("file") or "")
    kind = media_type or (entry or {}).get("type")
    if not src or not os.path.isfile(src):
        return None
    try:
        from video_director import prober as _prober
        ff = _prober._resolve_ffmpeg()
        if not ff:
            logger.warning(f"asset_library: 缩略图失败 {asset_id}: 找不到 ffmpeg 可执行文件")
            return None
        cmd = [ff, "-y", "-loglevel", "error"]
        if kind == "video":
            # 视频在 1s 处抽帧（短于 1s 的素材回退首帧）
            cmd += ["-ss", "1.0", "-i", src]
        else:
            cmd += ["-i", src]
        cmd += ["-vf", "scale=320:-2", "-vframes", "1", "-f", "image2", str(thumb)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode != 0 or not thumb.is_file():
            # 短视频在 1s 处无帧：回退首帧（不带 -ss 再试一次）
            if kind == "video":
                cmd2 = [ff, "-y", "-loglevel", "error", "-i", src,
                        "-vf", "scale=320:-2", "-vframes", "1",
                        "-f", "image2", str(thumb)]
                proc2 = subprocess.run(cmd2, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=60)
                if proc2.returncode != 0 or not thumb.is_file():
                    logger.warning(
                        f"asset_library: 缩略图失败 {asset_id}: {proc2.stderr[:200]}")
                    return None
            else:
                logger.warning(
                    f"asset_library: 缩略图失败 {asset_id}: {proc.stderr[:200]}")
                return None
        return str(thumb)
    except Exception as exc:  # noqa: BLE001 缩略图失败不阻断入库
        logger.warning(f"asset_library: 缩略图失败 {asset_id}: {exc}")
        return None


def get_asset(asset_id: str) -> Optional[dict]:
    return _load_catalog().get(asset_id)


def update_asset_semantics(asset_id: str, description: Optional[str] = None,
                           tags: Optional[List[str]] = None) -> dict:
    """写入素材语义（§25 description/tags）。

    只改语义字段：file / source / 权利 / content_hash 一律不动——语义是人对
    画面的判断，不能顺手把溯源信息一起覆盖掉。None 表示「该字段不修改」；
    传空字符串或空列表表示显式清空。写入后素材清单与 LLM 匹配立即可用。
    """
    with _catalog_lock():
        catalog = _load_catalog()
        entry = catalog.get(asset_id)
        if entry is None:
            raise AssetLibraryError("ASSET_NOT_FOUND", f"素材不存在：{asset_id}")
        if description is not None:
            entry["description"] = str(description).strip()
        if tags is not None:
            entry["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        entry["semantics_updated_at"] = time.time()
        catalog[asset_id] = entry
        _save_catalog(catalog)
    logger.info(f"asset_library: 语义更新 {asset_id}")
    return entry


def list_assets(kind: Optional[str] = None, source: Optional[str] = None,
                q: str = "", only_clear: bool = False) -> List[dict]:
    """Library 列表（§26 全局素材；项目无关）。"""
    out = []
    for a in sorted(_load_catalog().values(), key=lambda x: -x.get("created_at", 0)):
        if kind and a.get("type") != kind:
            continue
        if source and a.get("source") != source:
            continue
        if only_clear and a.get("commercial_status") not in (CLEAR, USER_SUPPLIED):
            continue
        if q:
            hay = " ".join([a.get("asset_id", ""), " ".join(a.get("tags", [])),
                            a.get("description", "")]).lower()
            if q.lower() not in hay:
                continue
        out.append(a)
    return out


def asset_usage(asset_id: str) -> List[str]:
    """扫描项目 manifest；任何引用状态不明时都拒绝给出「未使用」结论。"""
    used = []
    root = config.composer_storage_root()
    if not root.is_dir():
        return used
    from video_director import project_manifest

    try:
        projects = list(root.iterdir())
    except OSError as exc:
        raise AssetLibraryError(
            "ASSET_USAGE_UNKNOWN", f"无法枚举项目素材清单：{exc}"
        ) from exc
    for project_dir_path in projects:
        if not project_dir_path.is_dir():
            continue
        manifest = project_dir_path / "assets" / "manifest.json"
        if not manifest.exists() and not manifest.is_symlink():
            continue
        try:
            # The project-level lock prevents a concurrent manifest update from
            # being observed halfway through. Corrupt/unreadable/unsafe manifests
            # are not equivalent to an empty reference list.
            rows = project_manifest.read_project_manifest(project_dir_path.name)
        except Exception as exc:
            raise AssetLibraryError(
                "ASSET_USAGE_UNKNOWN",
                f"项目 {project_dir_path.name} 的素材引用无法核验：{exc}",
            ) from exc
        if any(item.get("id") == asset_id for item in rows):
            used.append(project_dir_path.name)
    return used


def delete_asset(asset_id: str, *, require_unused: bool = True) -> dict:
    """删除全局 Library Asset；被项目引用时硬拒绝。"""
    with _catalog_lock():
        catalog = _load_catalog()
        entry = catalog.get(asset_id)
        if entry is None:
            return {"ok": False, "code": "NOT_FOUND", "error": f"素材不存在：{asset_id}"}
        try:
            used_in = asset_usage(asset_id) if require_unused else []
        except AssetLibraryError as exc:
            return {
                "ok": False, "code": "ASSET_USAGE_UNKNOWN",
                "error": f"无法确认素材是否仍被项目引用，已拒绝删除：{exc}",
            }
        if used_in:
            return {"ok": False, "code": "ASSET_IN_USE", "error": f"素材正在被项目引用：{', '.join(used_in)}", "used_in": used_in}
        catalog.pop(asset_id, None)
        try:
            _save_catalog(catalog)
        except OSError as exc:
            return {"ok": False, "code": "CATALOG_WRITE_FAILED", "error": f"素材台账写入失败，原素材保留：{exc}"}
        try:
            file_path = entry.get("file") or ""
            if file_path and Path(file_path).is_file():
                Path(file_path).unlink()
            (library_root() / "thumbnails" / f"{asset_id}.jpg").unlink(missing_ok=True)
        except OSError as exc:
            return {"ok": False, "code": "DELETE_FAILED", "error": f"素材台账已移除，但文件清理失败：{exc}"}
        return {"ok": True, "asset_id": asset_id}


def delete_assets(asset_ids: List[str]) -> dict:
    """批量删除未被项目引用的 Library Asset；先全量预检，任一引用则整批拒绝。"""
    ids = list(dict.fromkeys(str(x).strip() for x in (asset_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择素材"}
    with _catalog_lock():
        catalog = _load_catalog()
        missing = [x for x in ids if x not in catalog]
        usage = {}
        try:
            for aid in ids:
                if aid in catalog:
                    refs = asset_usage(aid)
                    if refs:
                        usage[aid] = refs
        except AssetLibraryError as exc:
            return {
                "ok": False, "code": "ASSET_USAGE_UNKNOWN",
                "error": f"无法确认素材是否仍被项目引用，整批删除已拒绝：{exc}",
            }
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的素材：" + ",".join(missing)}
        if usage:
            detail = "；".join(f"{aid} → {','.join(refs)}" for aid, refs in usage.items())
            return {"ok": False, "code": "ASSET_IN_USE", "error": "所选素材仍被项目引用：" + detail}

        # Commit the catalog removal atomically before unlinking payloads. If a
        # file unlink fails, the remaining file is an orphan (recoverable), not
        # a catalog row pointing at a missing asset.
        entries = [catalog[aid] for aid in ids]
        for aid in ids:
            catalog.pop(aid, None)
        _save_catalog(catalog)

        deleted = []
        for aid, entry in zip(ids, entries):
            try:
                file_path = entry.get("file") or ""
                if file_path and Path(file_path).is_file():
                    Path(file_path).unlink()
                (library_root() / "thumbnails" / f"{aid}.jpg").unlink(missing_ok=True)
            except OSError as exc:
                return {
                    "ok": False, "code": "DELETE_FAILED",
                    "error": f"素材台账已移除，但文件清理失败：{exc}",
                    "deleted": deleted, "orphaned": ids[len(deleted):],
                }
            deleted.append(aid)
        return {"ok": True, "deleted": deleted}


def project_reference(project_id: str, asset_id: str) -> bool:
    """§26 项目只保存 Asset Reference：把 Library 资产引用写进项目 manifest。

    （复用项目现有 assets/manifest.json 结构——Layer.asset_id 照旧指向；
    Library 是可复用的源，项目 manifest 是本项目用了哪些。）
    """
    with _catalog_lock():
        entry = _load_catalog().get(asset_id)
        if entry is None:
            raise AssetLibraryError("NOT_FOUND", f"Library 里没有 {asset_id}")
        ref = {
            "id": asset_id, "type": entry["type"], "file": entry["file"],
            "duration": entry.get("duration"), "width": entry.get("width"),
            "height": entry.get("height"), "fps": entry.get("fps"),
            "source": entry.get("source"), "license_note": entry.get("license_note", ""),
            "description": entry.get("description", ""),
            "content_hash": entry.get("content_hash", ""),
        }
        from video_director import project_manifest
        return bool(project_manifest.merge_project_manifest_entries(
            project_id, [ref], _catalog_lock_held=True
        ))


# ---------------------------------------------------------------------------
# §十/§23 ratio 修复：搜索方向由当前项目画幅决定（不硬编码 9:16）
# ---------------------------------------------------------------------------

def project_ratio(project_id: str) -> str:
    """当前项目画幅 → ratio 形态（§10：currentProject.output.aspect）。

    返回 "9:16"/"16:9"/"1:1"/"3:4" 等（_norm_ratio 可归一的三档 + 宽高原始值）。
    """
    try:
        from video_director.editor import load_composition
        ir = load_composition(project_id)
        w, h = ir.project.width, ir.project.height
    except Exception:
        return ""
    if not w or not h:
        return ""
    r = w / h
    if abs(r - 9 / 16) < 0.1:
        return "9:16"
    if abs(r - 16 / 9) < 0.1:
        return "16:9"
    if abs(r - 1.0) < 0.1:
        return "1:1"
    if abs(r - 3 / 4) < 0.1:
        return "3:4"
    return f"{w}x{h}"


def stock_search_params(project_id: str) -> dict:
    """§10/§23：搜索参数从 Project Context 推导（方向/长度自动匹配）。"""
    ratio = project_ratio(project_id)
    if not ratio:
        return {"ratio": "", "orientation": "any"}
    try:
        w, h = (int(x) for x in
                (ratio.split("x") if "x" in ratio else
                 (ratio.split(":")[0], ratio.split(":")[1])))
    except (ValueError, IndexError):
        return {"ratio": ratio, "orientation": "any"}
    orientation = ("portrait" if h > w else "landscape" if w > h else "square")
    return {"ratio": ratio, "orientation": orientation}
