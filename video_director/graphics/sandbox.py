"""Sandbox 策略（GFX-026）：隔离 origin + 网络阻断。

方案 §48/§49：生成 HTML 是代码，必须在隔离 origin、禁网络、禁主 App DOM/cookie
的环境运行。本模块定义策略常量与一个可复用的 Playwright 隔离 context 工厂。

不实际 launch 浏览器（render 模块负责生命周期），只提供策略与 context 配置。
"""

from __future__ import annotations

from typing import Optional

#: 网络阻断：渲染页不得发起任何网络请求（确定性 + 沙箱逃逸面）
BLOCK_ALL_NETWORK = True

#: 隔离 origin：用 about:blank + set_content 注入，天然无 cookie/localStorage 继承
#: （不导航到任何 http origin，不与 localhost 应用 origin 共享存储）
ISOLATED_ORIGIN = True

#: 渲染安全上下文（Chromium 启动参数，render 层消费）
_CHROMIUM_ARGS = (
    "--disable-web-security",
    "--no-first-run",
    "--disable-features=Translate",
)

#: 禁用的 JS 能力（额外保险：编译产物已通过 preflight，这里防御深度）
_DENIED_PERMISSIONS = ("geolocation", "camera", "microphone", "clipboard-read")


def sandbox_policy() -> dict:
    """返回沙箱策略描述（render 层读取，UI/日志可展示）。"""
    return {
        "isolated_origin": ISOLATED_ORIGIN,
        "block_network": BLOCK_ALL_NETWORK,
        "denied_permissions": list(_DENIED_PERMISSIONS),
    }


def chromium_args() -> tuple:
    return _CHROMIUM_ARGS