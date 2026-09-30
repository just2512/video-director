"""MPT 吸收方案 §5 Capability Manifest 的核心 schema。

方案 §5 定义每个系统能力是一条不含"评分"的契约（零评分铁律延续自
[[no-scoring-boundary]]），字段完全对齐方案原文：

    id / version / category / description / inputSchema /
    outputArtifacts / preconditions / sideEffects / supportsPreview /
    supportsUndo / requiresConfirmation / providerRequired / examples

inputSchema 是指向一个**命名 schema** 的引用（如 "OriginalAudioPolicy"），
validator 据此对 Tool Call 的 payload 做契约校验，而不是盲执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional


@dataclass(frozen=True)
class Capability:
    """一条系统能力契约（§5）。frozen：Manifest 是只读事实源。

    §40 归一（系统能力归一方案）：domain = 能力域（composition/creative/edit/
    asset/audio/subtitle/music/generation/render/revision/qc/ai，即 category 的
    规范语义）；kind = command（可执行动作）/ query（只读查询）。
    只注册「可执行操作」，不把 Composition IR 等纯数据模型注册成 AI Tool。
    """

    id: str
    version: str
    category: str
    description: str
    domain: str = ""
    kind: str = "command"
    inputSchema: Optional[str] = None
    outputArtifacts: FrozenSet[str] = frozenset()
    preconditions: FrozenSet[str] = frozenset()
    sideEffects: FrozenSet[str] = frozenset()
    supportsPreview: bool = False
    supportsUndo: bool = True
    requiresConfirmation: bool = False
    providerRequired: bool = False
    examples: FrozenSet[str] = frozenset()

    def to_dict(self) -> dict:
        """序列化为 §5 示例同构的 JSON（outputArtifacts 等转 list）。"""
        return {
            "id": self.id,
            "version": self.version,
            "category": self.category,
            "domain": self.domain or self.category,
            "kind": self.kind,
            "description": self.description,
            "inputSchema": self.inputSchema,
            "outputArtifacts": sorted(self.outputArtifacts),
            "preconditions": sorted(self.preconditions),
            "sideEffects": sorted(self.sideEffects),
            "supportsPreview": self.supportsPreview,
            "supportsUndo": self.supportsUndo,
            "requiresConfirmation": self.requiresConfirmation,
            "providerRequired": self.providerRequired,
            "examples": sorted(self.examples),
        }


@dataclass(frozen=True)
class FieldSpec:
    """命名 schema 内的单字段约束（validator 用）。"""

    type: str  # str | int | float | bool | enum:int | enum:str | list | dict
    required: bool = False
    enum: FrozenSet[str] = frozenset()
    minimum: Optional[float] = None
    maximum: Optional[float] = None


@dataclass(frozen=True)
class InputSchema:
    """命名 inputSchema：字段名 → FieldSpec 约束表。"""

    name: str
    fields: Dict[str, FieldSpec] = field(default_factory=dict)

    def validate(self, payload: dict) -> List[str]:
        """校验 payload 是否符合本 schema，返回错误信息列表（空=通过）。"""
        errors: List[str] = []
        for fname, spec in self.fields.items():
            if fname not in payload or payload[fname] is None:
                if spec.required:
                    errors.append(f"缺必填字段 {fname}")
                continue
            val = payload[fname]
            if spec.type == "str" and not isinstance(val, str):
                errors.append(f"{fname} 应为字符串，收到 {type(val).__name__}")
            elif spec.type in ("int", "float") and not isinstance(val, (int, float)):
                errors.append(f"{fname} 应为数值，收到 {type(val).__name__}")
            elif spec.type == "bool" and not isinstance(val, bool):
                errors.append(f"{fname} 应为布尔，收到 {type(val).__name__}")
            elif spec.type == "enum:str" and val not in spec.enum:
                errors.append(f"{fname} 取值 {val!r} 非法，应为 {sorted(spec.enum)}")
            elif spec.type == "enum:int" and val not in {int(x) for x in spec.enum}:
                errors.append(f"{fname} 取值 {val!r} 非法，应为 {sorted(spec.enum)}")
            elif spec.type == "list" and not isinstance(val, list):
                errors.append(f"{fname} 应为列表，收到 {type(val).__name__}")
            elif spec.type == "dict" and not isinstance(val, dict):
                errors.append(f"{fname} 应为对象，收到 {type(val).__name__}")
            if spec.minimum is not None and isinstance(val, (int, float)) and val < spec.minimum:
                errors.append(f"{fname} 值 {val} 低于下限 {spec.minimum}")
            if spec.maximum is not None and isinstance(val, (int, float)) and val > spec.maximum:
                errors.append(f"{fname} 值 {val} 高于上限 {spec.maximum}")
        return errors