"""Provenance Registry（REG-002）：来源/许可证/商业状态追溯。

全局契约 §14：Recipe/Motion/Template/Asset/Font/Audio/Generated 全部必须有
provenance。商业边界（§15/§41）：Apache-2.0 Recipe ≠ 第三方原片授权 ≠
第三方音频授权 ≠ Remotion 授权 —— 四者独立记录，互不传染。

数据落盘：registries/provenance/*.json（sources/recipes/motions/templates/assets）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director.registries.core import (
    COMMERCIAL_STATUSES,
    REGISTRY_ROOT,
    RegistryError,
    validate_item,
)

#: 来源分类（全局契约 §14 origin）
ORIGINS = ("own", "video-shotcraft", "third-party", "user", "generated", "unknown")

#: provenance 数据目录
PROVENANCE_DIR = REGISTRY_ROOT / "provenance"


@dataclass(frozen=True)
class ProvenanceRecord:
    """来源记录（Task Card REG-002 Contract，字段对齐全局契约 §14）。"""

    id: str
    origin: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    license: Optional[str] = None
    copyright_notice: Optional[str] = None
    attribution_required: bool = False
    modification_required: bool = False
    commercial_status: str = "UNKNOWN"
    modifications: tuple = ()
    verified_at: Optional[str] = None
    notes: Optional[str] = None


#: registry item 校验复用 core.validate_item 需要 kind；provenance 文件本身用轻校验
def _validate_provenance(data: dict) -> dict:
    if not isinstance(data, dict):
        raise RegistryError("INVALID_SCHEMA", "provenance 必须是 JSON object")
    if not str(data.get("id") or "").strip():
        raise RegistryError("INVALID_SCHEMA", "provenance 缺 id")
    if data.get("origin") not in ORIGINS:
        raise RegistryError(
            "INVALID_ORIGIN", f"{data['id']}: origin {data.get('origin')!r} 非法"
        )
    if data.get("commercial_status") not in COMMERCIAL_STATUSES:
        raise RegistryError(
            "INVALID_COMMERCIAL_STATUS", f"{data['id']}: 四态之外的值被拒"
        )
    if data.get("commercial_status") == "CLEAR" and not data.get("license"):
        # user origin：授权由用户自行负责，系统只记来源（无 license 字段合法）
        if data.get("origin") != "user":
            raise RegistryError(
                "LICENSE_MISSING", f"{data['id']}: CLEAR 必须记录 license"
            )
    return data


def load_provenance_all() -> Dict[str, ProvenanceRecord]:
    """加载 provenance/*.json 全部记录（id → record）。"""
    records: Dict[str, ProvenanceRecord] = {}
    if not PROVENANCE_DIR.is_dir():
        return records
    for path in sorted(PROVENANCE_DIR.glob("*.json")):
        try:
            raw_list = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(
                "REGISTRY_LOAD_ERROR", f"provenance/{path.name}: {exc}"
            ) from exc
        if not isinstance(raw_list, list):
            raise RegistryError("INVALID_SCHEMA", f"provenance/{path.name} 必须是数组")
        for data in raw_list:
            _validate_provenance(data)
            rec = ProvenanceRecord(
                id=data["id"],
                origin=data["origin"],
                source_url=data.get("source_url"),
                source_name=data.get("source_name"),
                license=data.get("license"),
                copyright_notice=data.get("copyright_notice"),
                attribution_required=bool(data.get("attribution_required", False)),
                modification_required=bool(data.get("modification_required", False)),
                commercial_status=data["commercial_status"],
                modifications=tuple(data.get("modifications") or ()),
                verified_at=data.get("verified_at"),
                notes=data.get("notes"),
            )
            if rec.id in records:
                raise RegistryError("DUPLICATE_ID", f"provenance {rec.id} 重复定义")
            records[rec.id] = rec
    if records:
        logger.debug(f"provenance: {len(records)} records loaded")
    return records


def get_provenance(provenance_id: str) -> ProvenanceRecord:
    """按 id 取记录；不存在抛 RegistryError（诚实，禁止静默 UNKNOWN）。"""
    records = load_provenance_all()
    if provenance_id not in records:
        raise RegistryError("PROVENANCE_NOT_FOUND", f"{provenance_id} 未登记")
    return records[provenance_id]
