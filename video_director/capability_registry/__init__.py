"""MPT 吸收方案 §5 Capability Registry 包。

系统能力注册中心：让 AI（Director）不用"记住系统"，而是
    system.capabilities.list()      → §63 Level-1 id 摘要
    system.capabilities.describe(id) → §63 Level-2 完整 Contract
    system.context.inspect()        → §63 Level-3 当前项目状态

与 video_director.capabilities（环境级：ffmpeg/playwright 可用性检测）
不同层：本包是**编辑能力 Manifest**（AI 可调用的操作契约）。
"""

from video_director.capability_registry.manifest import (
    capability_ids_by_category,
    describe_capability,
    list_capabilities,
    manifest,
)
from video_director.capability_registry.schemas import (
    Capability,
    FieldSpec,
    InputSchema,
)
from video_director.capability_registry.validator import validate_tool_call

# §5 五文件结构：loader + resolver 也已存在
from video_director.capability_registry import loader, resolver  # noqa: F401

__all__ = [
    "Capability",
    "FieldSpec",
    "InputSchema",
    "manifest",
    "list_capabilities",
    "describe_capability",
    "capability_ids_by_category",
    "validate_tool_call",
    "loader",
    "resolver",
]