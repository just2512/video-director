"""Agent Gateway 包：MCP（§49-§51）/ CLI（§52）统一入口（§19/§31.3）。

两个外部入口（MCP / CLI）全部经 capability_gateway.dispatch 汇合到同一
Capability Registry → Tool Executor → Command Bus，不形成第二套系统（§3.4）。
"""

from video_director.agent_gateway.capability_gateway import (  # noqa: F401
    HIGH_LEVEL_TOOLS,
    GatewayError,
    dispatch,
)

__all__ = ["HIGH_LEVEL_TOOLS", "GatewayError", "dispatch"]
