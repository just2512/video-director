"""多模型配置中心（方案 §8 Provider Registry：模型配置按角色分组）。

每个创作角色一组独立配置（api_key/base_url/model），互不共享：
    AI 角色：
        llm      导演/文案/解析等文本智能
        image    生图模型
        video    生视频模型
        vision   视觉理解
        hyperframes 云端动效渲染
    素材来源：
        stock_pexels
        stock_pixabay
        stock_coverr

设计：
- 与 llm_provider 兼容：get_llm() 的运行时配置由 roles["llm"] 驱动
- 持久化 storage/composer/model_config.json（api_key 明文本地文件，
  与 llm_config.json 同级；个人单机产品，不上传任何远端）
- 诚实降级：某角色未配置 → 该角色走规则/内置路径，不阻塞其它角色
- Agent 分模型：多 Agent 各自调 get_role_config(agent 的角色名)，
  同一角色共享配置；后续可扩展 per-agent 覆盖
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

#: 角色注册表（前端配置面板据此渲染；新增角色只改这里）
ROLE_DEFS = {
    "llm": {
        "label": "文本大模型（导演/文案/改稿解析）",
        "hint": "OpenAI 协议兼容：OpenAI / DeepSeek / Moonshot / 硅基流动 / Ollama",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "image": {
        "label": "生图大模型（图片生成）",
        "hint": "OpenAI Images 协议或兼容端点（如硅基流动 images）",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-image-1",
    },
    "video": {
        "label": "生视频大模型（视频生成）",
        "hint": "视频生成 API（Veo/Kling/Wan 兼容端点，按供应商文档填写）",
        "default_base_url": "",
        "default_model": "",
    },
    "vision": {
        "label": "视觉理解模型（素材画面分析）",
        "hint": "多模态视觉 API（替代 COMPOSER_VISION_API_KEY，OpenAI 协议）",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "hyperframes": {
        "label": "云端动效渲染（HyperFrames 高级动效）",
        "hint": "付费云端渲染 API。配置后成片动效走云端高质量渲染；不配置自动用内置 FFmpeg，功能不缺失",
        "default_base_url": "https://api.hyperframes.dev/v1",
        "default_model": "hyper-1",
    },
    "stock_pexels": {
        "label": "Pexels 图库",
        "hint": "官方 API Key；用于自动搜索和下载网络视频素材",
        "default_base_url": "https://api.pexels.com",
        "default_model": "videos/search",
        "key_env": "PEXELS_API_KEY",
        "kind": "stock",
    },
    "stock_pixabay": {
        "label": "Pixabay 图库",
        "hint": "官方 API Key；作为 Pexels 的补充来源",
        "default_base_url": "https://pixabay.com/api",
        "default_model": "videos",
        "key_env": "PIXABAY_API_KEY",
        "kind": "stock",
    },
    "stock_coverr": {
        "label": "Coverr 图库",
        "hint": "当前商业条款未确认，配置后仅用于手动检索，不自动进入商用成片",
        "default_base_url": "https://coverr.co",
        "default_model": "",
        "key_env": "COVERR_API_KEY",
        "kind": "stock",
    },
}

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None


def _config_path() -> Path:
    from video_director import config as cfg
    p = cfg.composer_storage_root() / "model_config.json"
    return p


def _env_fallback(role: str) -> dict:
    """角色未配置时的环境变量回退（保持向后兼容）。"""
    import os
    env_key = ROLE_DEFS.get(role, {}).get("key_env")
    if env_key and os.environ.get(env_key):
        d = ROLE_DEFS[role]
        return {
            "api_key": os.environ[env_key],
            "base_url": os.environ.get(f"{role.upper()}_BASE_URL")
                        or d.get("default_base_url", ""),
            "model": os.environ.get(f"{role.upper()}_MODEL")
                     or d.get("default_model", ""),
            "source": "env",
        }
    if role == "llm":
        if os.environ.get("VD_LLM_API_KEY"):
            return {
                "api_key": os.environ["VD_LLM_API_KEY"],
                "base_url": os.environ.get("VD_LLM_BASE_URL") or ROLE_DEFS["llm"]["default_base_url"],
                "model": os.environ.get("VD_LLM_MODEL") or ROLE_DEFS["llm"]["default_model"],
                "source": "env",
            }
    if role == "vision":
        key = (os.environ.get("COMPOSER_VISION_API_KEY")
               or os.environ.get("VD_VISION_API_KEY") or "")
        if key:
            return {
                "api_key": key,
                "base_url": os.environ.get("VD_VISION_BASE_URL")
                            or ROLE_DEFS["vision"]["default_base_url"],
                "model": os.environ.get("VD_VISION_MODEL")
                         or ROLE_DEFS["vision"]["default_model"],
                "source": "env",
            }
    return {"api_key": "", "base_url": "", "model": "", "source": "none"}


def load_all() -> Dict[str, dict]:
    """读全部角色配置（含持久化文件 + 环境变量回退）。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        raw: Dict[str, dict] = {}
        path = _config_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"model_config.json 读取失败: {exc}")
        merged: Dict[str, dict] = {}
        for role, data in raw.items():
            if role not in ROLE_DEFS or not isinstance(data, dict):
                continue
            api_key = (data.get("api_key") or "").strip()
            merged[role] = {
                "api_key": api_key,
                "base_url": (data.get("base_url") or "").strip(),
                "model": (data.get("model") or "").strip(),
                "source": "file" if api_key else "none",
            }
        _cache = merged
        return _cache


def get_role_config(role: str) -> dict:
    """单角色配置：文件配置 > 环境变量 > 未启用。"""
    all_cfg = load_all()
    entry = all_cfg.get(role)
    if entry and entry.get("api_key"):
        return entry
    return _env_fallback(role)


def is_role_enabled(role: str) -> bool:
    return bool(get_role_config(role).get("api_key"))


def save_all(configs: Dict[str, dict]) -> Dict[str, dict]:
    """保存全部角色配置（整体覆盖；空 key 角色仍写入占位保留 UI 输入）。"""
    global _cache
    clean: Dict[str, dict] = {}
    for role, data in (configs or {}).items():
        if role not in ROLE_DEFS or not isinstance(data, dict):
            continue
        clean[role] = {
            "api_key": (data.get("api_key") or "").strip(),
            "base_url": (data.get("base_url") or "").strip(),
            "model": (data.get("model") or "").strip(),
        }
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    with _lock:
        _cache = None
    logger.info(f"model_config: 保存 {len(clean)} 角色配置 → {path}")
    return clean


def sync_llm_provider() -> None:
    """把 roles['llm'] 同步进 llm_provider 的运行时配置（消费点零改动）。"""
    from video_director import llm_provider
    cfg = get_role_config("llm")
    llm_provider.set_llm_config(
        cfg.get("api_key") or "",
        cfg.get("base_url") or None,
        cfg.get("model") or None,
    )


def status_for_frontend() -> list:
    """前端配置面板数据：每角色 label/hint/是否已配置/来源（不回传 key）。"""
    out = []
    for role, d in ROLE_DEFS.items():
        cfg = get_role_config(role)
        out.append({
            "role": role,
            "kind": d.get("kind", "model"),
            "label": d["label"],
            "hint": d["hint"],
            "default_base_url": d.get("default_base_url", ""),
            "default_model": d.get("default_model", ""),
            "enabled": bool(cfg.get("api_key")),
            "source": cfg.get("source", "none"),
            "base_url": cfg.get("base_url") or d.get("default_base_url", ""),
            "model": cfg.get("model") or d.get("default_model", ""),
        })
    return out
