"""Agent Gateway watch：SSE 事件订阅助手（webui /api/events 的客户端读取）。

creative.watch 高层工具给的是状态快照（capability_gateway.creative_watch）；
本模块提供真正的长连接读取器（供 CLI/外部脚本把 SSE 流转成结构化事件）。
MCP 的 creative.watch 同样走快照语义——长连接交给客户端自己接 SSE。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Iterator, Optional


def iter_events(base_url: str = "http://127.0.0.1:8501",
                timeout: int = 30) -> Iterator[dict]:
    """读取 SSE /api/events 流，逐事件 yield 解析后的 dict。

    阻塞式生成器：连接保持直到服务端断开或异常。事件行格式
    `data: {json}`（hello 事件为 `event: hello\\ndata: {...}`）。
    """
    req = urllib.request.Request(base_url.rstrip("/") + "/api/events")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "{\"connected\": true}":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


def watch_snapshot(project_id: str = "") -> dict:
    """快照语义（与 capability_gateway.creative_watch 一致）。"""
    from video_director.agent_gateway.capability_gateway import creative_watch
    return creative_watch(project_id)
