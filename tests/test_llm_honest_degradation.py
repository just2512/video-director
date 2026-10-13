"""LLM 诚实降级 + 错误透传契约（2026-10-13 scene_plan 报障根修引入）。

背景：openai 包缺失 + model_config.json 配置存在 → get_llm() 曾返回
"看似可用"的 Provider，直到 scene_plan 首次 generate() 才炸，且三层吞
错误（loguru %s / 顶层新建 plan 错标 understand / DirectorAgentError
只报节点名）把根因全部藏掉。

锁四件事：
1. Provider 构造期真实 import openai——缺包抛 ValueError 带 pip install 指引
2. get_llm() 对构造失败诚实降级 None（消费点走规则路径，不炸 run）
3. 节点不可恢复失败时，异常必须带内层根因
4. 全 repo 的 loguru 调用不允许 printf 风格 %s（loguru 用 {}，%s 会
   原样打印吞掉真实异常——本次报障日志 "%s" 原样出现就是实锤）
"""
import ast
import inspect
from pathlib import Path

import pytest

import video_director.llm_provider as lp
from video_director.llm_provider import OpenAICompatibleProvider


def test_provider_missing_openai_raises_with_hint(monkeypatch):
    """缺 openai 包：构造期（不是首次 generate）就抛 ValueError + 安装指引。"""
    import sys
    monkeypatch.setitem(sys.modules, "openai", None)  # import 即 ImportError
    with pytest.raises(ValueError) as ei:
        OpenAICompatibleProvider(api_key="k", base_url="https://x/v1", model="m")
    assert "pip install" in str(ei.value), "缺包错误必须带安装指引"


def test_get_llm_honest_none_when_construct_fails(monkeypatch):
    """key 已配置但构造失败 → get_llm() 必须返 None（规则路径兜底）。

    这是核心契约：配置存在不等于能力可用；构造失败绝不能返回
    一个首次调用才炸的 Provider。
    """
    lp.reset_llm_cache()
    monkeypatch.setattr(
        "video_director.model_config.get_role_config",
        lambda role: {"api_key": "k", "base_url": "https://x/v1", "model": "m"},
        raising=False,
    )
    # 强制构造失败路径
    def boom(*a, **k):
        raise ValueError("openai 库未安装，请运行: pip install -r requirements.txt")
    monkeypatch.setattr(lp, "OpenAICompatibleProvider", boom)
    assert lp.get_llm() is None, "构造失败必须诚实降级 None，不许炸/不许返回残废 Provider"


def test_unrecoverable_node_error_carries_root_cause():
    """不可恢复失败必须带内层根因（2026-10-13 报障：只报节点名查障全靠猜）。"""
    import video_director.director_agent as da
    src = inspect.getsource(da)
    marker = "节点执行失败且无法恢复"
    assert marker in src
    # 修复形态：raise 前 300 字符内从 tool_result 取 error，raise 的 f-string
    # 里拼接 inner。两个特征同时在场才算数。
    idx = src.index(marker)
    before = src[max(0, idx - 300): idx]
    after = src[idx: idx + 600]
    assert 'tool_result.get("error")' in before + after, (
        "raise 前必须从 tool_result 提取内层 error"
    )
    assert "inner" in after, "异常消息必须拼接 inner 根因"


def test_failed_run_keeps_real_failed_node(monkeypatch, tmp_path):
    """run 失败时 plan 回读持久化版本——不许新建 plan 把失败错标 understand。"""
    import video_director.workflow_run as wr
    src = inspect.getsource(wr._compose_worker)
    assert "load_plan" in src, "_compose_worker 失败收口必须优先回读持久化 plan"


def test_repo_loguru_calls_no_percent_format():
    """全 repo loguru 调用禁止 printf %s 风格（loguru 用 {}）。"""
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for py in root.glob("video_director/*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("info", "warning", "error", "debug", "exception")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                    and len(node.args) >= 2):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if "%" in first.value:
                        offenders.append(f"{py.name}:{node.lineno} {first.value[:60]}")
    assert not offenders, "loguru printf 风格残留（吞异常实锤）: " + "; ".join(offenders)
