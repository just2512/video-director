"""Registry Core（REG-001）：三 Registry 共用底层契约。

契约（Task Card REG-001）：
    RegistryItem: id/version/kind/name/description/tags/commercial_status/
                  provenance_ref/enabled/created_at/updated_at
    RegistryLoader: 文件系统只读加载 builtin/*.json
    RegistryValidator: schema 校验（非法即拒，诚实暴露）
    RegistryQuery: 按 kind/tags/commercial_status 过滤
    RegistryVersion: 同 id 多版本共存，旧版本保持可寻址（禁漂移）

禁止（全局契约 §9/§12）：Registry 中出现 ffmpeg_command/shell_command/
arbitrary_js/remotion_component/absolute_asset_path —— validator 显式拒收。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# 常量（全局契约 §16：商业状态四态，禁 UNKNOWN 强转 CLEAR）
# ---------------------------------------------------------------------------
COMMERCIAL_STATUSES = ("CLEAR", "REVIEW", "BLOCKED", "UNKNOWN")

#: Registry 内容禁用的字段（全局契约 §9/§12：知识定义层不得携带执行代码）
FORBIDDEN_FIELDS = (
    "ffmpeg_command",
    "shell_command",
    "arbitrary_js",
    "remotion_component",
    "absolute_asset_path",
)

#: Registry 数据根（与代码同级，随包分发；只读加载）
REGISTRY_ROOT = Path(__file__).parent


class RegistryError(Exception):
    """Registry 加载/校验失败（INVALID_SCHEMA/DUPLICATE_ID/...）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class RegistryItem:
    """三 Registry 共用基础契约（Task Card REG-001 Contract）。"""

    id: str
    version: str
    kind: str
    name: str
    description: Optional[str] = None
    tags: tuple = ()
    commercial_status: str = "UNKNOWN"
    provenance_ref: Optional[str] = None
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


def _validate_version(version: str) -> None:
    """语义化版本 min.min.min（宽松：至少两段数字）。"""
    parts = version.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        raise RegistryError("INVALID_VERSION", f"version {version!r} 非语义化（如 1.0.0）")


def validate_item(data: dict, kind: str) -> dict:
    """校验单个 Registry Item 原始 dict；返回规范化后的 dict。

    失败抛 RegistryError（code 精确：INVALID_SCHEMA/INVALID_COMMERCIAL_STATUS/
    MISSING_PROVENANCE/DUPLICATE 见 loader）。
    """
    if not isinstance(data, dict):
        raise RegistryError("INVALID_SCHEMA", "item 必须是 JSON object")
    for key in ("id", "version", "kind", "name"):
        if not str(data.get(key) or "").strip():
            raise RegistryError("INVALID_SCHEMA", f"缺少必填字段 {key}")
    if data["kind"] != kind:
        raise RegistryError(
            "INVALID_SCHEMA", f"{data['id']}: kind {data['kind']} != 期望 {kind}"
        )
    _validate_version(str(data["version"]))
    status = data.get("commercial_status", "UNKNOWN")
    if status not in COMMERCIAL_STATUSES:
        raise RegistryError(
            "INVALID_COMMERCIAL_STATUS", f"{data['id']}: {status!r} 不在四态内"
        )
    for forbidden in FORBIDDEN_FIELDS:
        if forbidden in data:
            raise RegistryError(
                "INVALID_SCHEMA",
                f"{data['id']}: Registry 禁止携带 renderer 执行字段 {forbidden}",
            )
    if status in ("CLEAR", "REVIEW") and not data.get("provenance_ref"):
        raise RegistryError(
            "MISSING_PROVENANCE",
            f"{data['id']}: commercial_status={status} 必须有 provenance_ref",
        )
    return data


def item_from_dict(data: dict) -> RegistryItem:
    return RegistryItem(
        id=data["id"],
        version=str(data["version"]),
        kind=data["kind"],
        name=data["name"],
        description=data.get("description"),
        tags=tuple(data.get("tags") or ()),
        commercial_status=data.get("commercial_status", "UNKNOWN"),
        provenance_ref=data.get("provenance_ref"),
        enabled=bool(data.get("enabled", True)),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


class RegistryLoader:
    """文件系统只读加载器：builtin/**/*.json → 已验证 RegistryItem 列表。

    - 同 (id, version) 重复 → DUPLICATE_ID
    - 同 id 多 version 共存（合法，version pinning 的基础）
    - 目录不存在/无文件 → 空列表（Registry 可空，诚实降级）
    """

    def __init__(self, kind: str, sub_dir: str):
        self._kind = kind
        self._root = REGISTRY_ROOT / sub_dir / "builtin"
        self._items: List[RegistryItem] = []
        self._loaded = False
        self._sub_dir = sub_dir

    def load_all(self) -> List[RegistryItem]:
        self._ensure_loaded()
        return list(self._items)

    def load(self, item_id: str, version: Optional[str] = None) -> RegistryItem:
        """按 id（可选版本）取 item；不存在抛 RegistryError。

        version=None 取最高版本（语义化比较）。
        """
        self._ensure_loaded()
        matches = [i for i in self._items if i.id == item_id]
        if not matches:
            raise RegistryError("NOT_FOUND", f"{item_id} 不存在于 {self._kind} registry")
        if version is None:
            return max(matches, key=lambda i: _version_key(i.version))
        for m in matches:
            if m.version == version:
                return m
        raise RegistryError(
            "NOT_FOUND", f"{item_id}@{version} 不存在（可用: {[m.version for m in matches]}）"
        )

    def versions_of(self, item_id: str) -> List[str]:
        self._ensure_loaded()
        return sorted(
            (i.version for i in self._items if i.id == item_id), key=_version_key
        )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._root.is_dir():
            return
        seen: Dict[tuple, dict] = {}
        for path in sorted(self._root.rglob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RegistryError("REGISTRY_LOAD_ERROR", f"{path.name}: {exc}") from exc
            # 数据文件统一为数组格式（一文件多 item，多版本共存）
            items_raw = raw if isinstance(raw, list) else [raw]
            for entry in items_raw:
                data = validate_item(entry, self._kind)
                key = (data["id"], str(data["version"]))
                if key in seen:
                    raise RegistryError(
                        "DUPLICATE_ID",
                        f"{data['id']}@{data['version']} 在 {path} 与 {seen[key]} 重复定义",
                    )
                seen[key] = path
                self._items.append(item_from_dict(data))
        if self._items:
            logger.debug(
                f"registry {self._kind}: {len(self._items)} items loaded"
            )


def _version_key(version: str):
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


# ---------------------------------------------------------------------------
# §39 三层加载：System（builtin）→ User → Project override
# ---------------------------------------------------------------------------

#: User Registry 根（用户自建动效/镜头/模板；随 storage 走不随包）
def user_registry_root() -> Path:
    from video_director import config
    return config.composer_storage_root() / "registries"


def _load_dir(root: Path, kind: str, source: str,
              seen: dict, out: list, conflicts: list) -> None:
    """从一个目录加载 items（source 标记层级，供冲突报错与 lineage）。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError("REGISTRY_LOAD_ERROR", f"{path.name}: {exc}") from exc
        items_raw = raw if isinstance(raw, list) else [raw]
        for entry in items_raw:
            data = validate_item(entry, kind)
            key = (data["id"], str(data["version"]))
            if key in seen:
                # §39：同 id 同 version 跨层重复 → 显式冲突（禁静默覆盖）
                conflicts.append(
                    f"{data['id']}@{data['version']} 在 {source}:{path.name} "
                    f"与 {seen[key][0]}:{seen[key][1].name} 重复"
                )
                continue
            seen[key] = (source, path)
            data["_source_layer"] = source
            out.append(data)


class LayeredRegistryLoader(RegistryLoader):
    """三层加载器（§39 Registry 加载规则）。

    System（builtin 随包）→ User（storage/registries/<sub>）→
    Project（项目目录 registries/<sub>，可选注入）。
    同 (id, version) 跨层重复不静默覆盖 → load 时抛 REGISTRY_CONFLICT。
    同 id 不同 version 共存合法（版本演进，version pinning 各取所需）。
    """

    def __init__(self, kind: str, sub_dir: str, project_root: Optional[Path] = None):
        super().__init__(kind, sub_dir)
        self._project_root = project_root
        self._conflicts: List[str] = []
        self._item_layers: Dict[tuple, str] = {}
        self._raw_by_key: Dict[tuple, dict] = {}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        seen: Dict[tuple, tuple] = {}
        raw_items: List[dict] = []
        conflicts: List[str] = []
        _load_dir(self._root, self._kind, "system", seen, raw_items, conflicts)
        _load_dir(user_registry_root() / self._kind_safe(), self._kind, "user",
                  seen, raw_items, conflicts)
        if self._project_root is not None:
            _load_dir(self._project_root, self._kind, "project",
                      seen, raw_items, conflicts)
        if conflicts:
            # §39 registry conflict → explicit version resolution（整层拒载）
            raise RegistryError("REGISTRY_CONFLICT", "；".join(conflicts))
        self._items = [item_from_dict(d) for d in raw_items]
        self._item_layers = {
            (d["id"], str(d["version"])): d["_source_layer"] for d in raw_items
        }
        self._raw_by_key = {
            (d["id"], str(d["version"])): d for d in raw_items
        }
        if self._items:
            logger.debug(
                f"registry {self._kind}: {len(self._items)} items loaded "
                f"(layers: {sorted(set(self._item_layers.values()))})"
            )

    def raw_of(self, item_id: str, version: str) -> dict:
        """原始 dict（三层合并后的定义），供领域 registry 深重建。"""
        self._ensure_loaded()
        raw = self._raw_by_key.get((item_id, version))
        if raw is None:
            raise RegistryError("NOT_FOUND", f"{item_id}@{version} 原始定义缺失")
        return raw

    def _kind_safe(self) -> str:
        """loader sub_dir（shots/motions/templates）→ user registry 子目录同名。

        RegistryLoader.__init__ 存了 _sub_dir（构造方传入的目录名），
        LayeredRegistryLoader 用它而不用猜（shots/motions/templates 直对应）。
        """
        if getattr(self, "_sub_dir", "") in ("shots", "motions", "templates"):
            return self._sub_dir
        return self._kind

    @property
    def conflicts(self) -> List[str]:
        return list(self._conflicts)

    def layer_of(self, item_id: str, version: str) -> str:
        """item 来自哪层（system/user/project）——Gallery 可标「用户自建」。"""
        self._ensure_loaded()
        return self._item_layers.get((item_id, version), "system")


class RegistryQuery:
    """查询接口（Task Card REG-001 API）：kind 聚合查询 + 过滤。

    三个 registry 的 loader 已按 kind 分域，这里做组合过滤。
    """

    def __init__(self, loaders: Dict[str, RegistryLoader]):
        self._loaders = loaders

    def find(
        self,
        kind: Optional[str] = None,
        tags: Optional[List[str]] = None,
        commercial_status: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[RegistryItem]:
        items: List[RegistryItem] = []
        if kind:
            loader = self._loaders.get(kind)
            items = loader.load_all() if loader else []
        else:
            for loader in self._loaders.values():
                items.extend(loader.load_all())
        result = []
        for it in items:
            if enabled_only and not it.enabled:
                continue
            if tags and not any(t in it.tags for t in tags):
                continue
            if commercial_status and it.commercial_status != commercial_status:
                continue
            result.append(it)
        return result
