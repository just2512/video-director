"""LLM Provider 层（V1 §配置契约：常规大模型能力标配）。

设计原则：
- **诚实降级**：无 VD_LLM_API_KEY 时 get_llm() 返回 None，消费点走规则路径
- **延迟连接**：OpenAI client 在 generate() 首次调用时构造，import 本模块不触发网络
- **OpenAI 协议兼容**：base_url/api_key/model 全部环境变量注入，兼容 moonshot/deepseek/
  硅基流动/dashscope 兼容模式/gemini openai 端点 等任何 OpenAI 协议端点
- **零破坏**：planner.py/matcher.py 的 llm_call 回调签名不变，本模块只提供 helper
  供上层调用方注入或直接使用

环境变量：
    VD_LLM_API_KEY    必填，有则启用真实 provider
    VD_LLM_BASE_URL   可选，OpenAI 兼容端点（默认 https://api.openai.com/v1）
    VD_LLM_MODEL      可选，模型名（默认 gpt-4o-mini）

消费点（三个 helper，无 provider 时诚实降级）：
    enhance_script(script)              文案口语化与节奏优化
    storyboard_hints(scene_texts)       每句视觉提示
    # 扩展位：复刻拆解 prompt（后续产品线消费，签名 storyboard_deconstruct）
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Callable, List, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from loguru import logger

# ---------------------------------------------------------------------------
# 环境变量键名（集中定义，禁止散落）
# ---------------------------------------------------------------------------
ENV_API_KEY = "VD_LLM_API_KEY"
ENV_BASE_URL = "VD_LLM_BASE_URL"
ENV_MODEL = "VD_LLM_MODEL"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

# 2026-10-20 用户裁决：LLM 调用失败给 10 次重试机会。
# 2026-10-25 用户裁决修正：递增等待（15/30/45/60s）在网关静默挂死场景下
# 把 10 次重试拖成 8-15 分钟假卡死；改固定 10s 间隔。
# 2026-10-27 用户裁决：重试次数 10 → 3（10 次太久）。
LLM_MAX_RETRIES = 3
LLM_RETRY_WAIT_S = 10.0       # 固定重试间隔（用户裁决 2026-10-25）
LLM_ATTEMPT_TIMEOUT_S = 120.0  # 单次尝试读超时（秒）：正常长生成 60~150s，流式=相邻块间隔
LLM_CONNECT_TIMEOUT_S = 10.0  # 连接超时：网关挂死时 connect 10s 内快速失败暴露，不再拖 120s
# 可选增强（素材匹配/规划等「有则更好」步骤）的短耐心上限：网关挂死或模型极慢时
# 到点即放弃并降级确定性兜底，不把 3×120s 主链重试拖进可选步骤。
OPTIONAL_LLM_TIMEOUT_S = 45.0
# 空流兜底（非流式重发）的短耐心上限：网关偶发返回 0 chunk 的完全空流，非流式
# 兜底在大 prompt 下会跑满 120s 读超时，把单次调用打成「流式 + 非流式 120s」。
# 兜底不给比流式更长的预算，到点即进正常重试循环。
EMPTY_STREAM_FALLBACK_TIMEOUT_S = 45.0

# 进程级 LLM 重试状态（诊断）：最近一次 generate 的重试进度——健康检查/
# 状态查询读它区分「长时间生成」与「LLM 正在重试」。
_last_retry_state: dict = {"attempt": 0, "max": LLM_MAX_RETRIES, "last_error": "", "ts": 0.0}

# 进程级取消钩子（2026-10-26 用户裁决「停止就停止」）：由 workflow_run 在
# worker 线程启动前注入。generate 的重试循环每次尝试前、以及每秒等待间隙
# 都会调用它——返回 True 则立即抛 GenerationCancelled，让正在执行的 LLM 节点
# 也能被停止打断（不再等整个节点跑完）。
_cancel_check: Optional[Callable[[], bool]] = None


def set_cancel_check(fn: Optional[Callable[[], bool]]) -> None:
    """注入/清除 LLM 取消检测回调（workflow_run worker 调用，进程级单值）。"""
    global _cancel_check
    _cancel_check = fn


class GenerationCancelled(Exception):
    """LLM 调用因用户停止创作被中断（重试循环内检测到取消标志）。"""


class OptionalLLMUnavailable(Exception):
    """可选增强的 LLM 短耐心调用不可用（超时/异常/空内容）——调用方降级兜底。"""


class _EmptyGeneration(Exception):
    """LLM 返回空内容（可重试的瞬时故障信号）。"""


def retry_state() -> dict:
    """最近一次 generate 的重试进度快照（只读诊断）。"""
    return dict(_last_retry_state)


# ---------------------------------------------------------------------------
# Provider 抽象（Protocol：鸭子类型，便于测试 monkeypatch）
# ---------------------------------------------------------------------------
@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 抽象。实现者必须提供 generate(prompt, system) -> str。"""

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """生成文本。system 为 None 表示不挂系统提示词。"""
        ...


# ---------------------------------------------------------------------------
# OpenAI 兼容实现
# ---------------------------------------------------------------------------
def _openai_timeout(connect: float, read: float):
    """构造 openai.Timeout（=httpx.Timeout）拆 connect/read；不可用返回 None。

    openai 2.x 与 3.x 都 re-export `openai.Timeout` 且都接受对象。dict 在 3.x
    会炸 `'dict' object cannot be interpreted as an integer`（生产实录 2026-11-18，
    分镜规划三连败）；标量则无法拆分 connect/read，网关黑洞时 connect 要等满
    read 预算才报 Connection error（生产实录 2026-11-18，重试间隔被拖到 165s）。
    openai 不可用（CI 无 openai / fake 缺 Timeout 属性）时返回 None。
    """
    import sys
    mod = sys.modules.get("openai")
    if mod is None:
        try:
            from importlib import import_module
            mod = import_module("openai")
        except Exception:
            return None
    TO = getattr(mod, "Timeout", None)
    if TO is None:
        return None
    try:
        return TO(connect=float(connect), read=float(read), write=float(read), pool=float(connect))
    except Exception:
        return None


def _request_timeout_kwargs(connect: float, read: float) -> dict:
    """给 chat.completions.create 的 per-call timeout 参数。

    优先用 openai.Timeout 对象拆 connect/read（connect 短 10s，网关黑洞快速失败，
    不再等满 read 预算）；openai 不可用时退回标量 read（慢一点，但不炸）。
    """
    if read is None:
        read = LLM_ATTEMPT_TIMEOUT_S
    read = float(read)
    if not math.isfinite(read) or read <= 0:
        read = LLM_ATTEMPT_TIMEOUT_S
    t = _openai_timeout(connect, read)
    if t is not None:
        return {"timeout": t}
    return {"timeout": read}


class OpenAICompatibleProvider:
    """OpenAI 协议兼容端点的统一实现。

    延迟初始化：__init__ 只存配置，openai.OpenAI 客户端在首次 generate() 时构造。
    这样 import 本模块 / 构造 provider 不会触发任何网络或证书加载，单元测试可在
    无网络环境下安全导入。

    兼容端点示例：
        - moonshot:        base_url=https://api.moonshot.cn/v1
        - deepseek:        base_url=https://api.deepseek.com/v1
        - 硅基流动:         base_url=https://api.siliconflow.cn/v1
        - dashscope 兼容:   base_url=https://dashscope.aliyuncs.com/compatible-mode/v1
        - gemini openai:   base_url=https://generativelanguage.googleapis.com/v1beta/openai
        - 本地 ollama:      base_url=http://localhost:11434/v1, api_key="ollama"
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        if not api_key:
            raise ValueError("OpenAICompatibleProvider: api_key 不可为空")
        # 2026-10-13 报障教训：openai 库缺失曾延迟到首次 generate() 才炸，
        # 导致 scene_plan 节点「不可恢复」。构造期真实 import——缺包在这里
        # 抛 ValueError（带安装指引），get_llm() 捕获后诚实降级 None，
        # 消费点走规则路径，绝不让缺包炸死整个 run。
        try:
            from openai import OpenAI  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - 环境问题不可达路径
            raise ValueError(
                "openai 库未安装，无法启用 LLM Provider。"
                " 请运行: pip install -r requirements.txt"
            ) from exc
        self._api_key = api_key
        self._base_url = base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        self._model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        # 延迟初始化：client 在首次 generate 时构造
        self._client = None  # type: ignore[assignment]
        # __new__ 构造的测试实例没有 _ensure_client，用默认值保持安全。
        self._read_timeout_s = LLM_ATTEMPT_TIMEOUT_S

    def _ensure_client(self):
        """首次调用时构造 openai.OpenAI 客户端。"""
        if self._client is not None:
            return
        # __init__ 已验证 openai 可 import；这里再取一次（sys.modules 缓存，零成本）
        from openai import OpenAI  # type: ignore[import-untyped]
        client_options = {}
        timeout_raw = os.environ.get("VD_LLM_REQUEST_TIMEOUT_SECONDS", "").strip()
        read_timeout = LLM_ATTEMPT_TIMEOUT_S
        if timeout_raw:
            try:
                read_timeout = float(timeout_raw)
            except ValueError as exc:
                raise ValueError("VD_LLM_REQUEST_TIMEOUT_SECONDS must be a positive number") from exc
            if not math.isfinite(read_timeout) or read_timeout <= 0:
                raise ValueError("VD_LLM_REQUEST_TIMEOUT_SECONDS must be a positive number")
        # connect 分离：网关挂死时 TCP connect 只等 LLM_CONNECT_TIMEOUT_S 就快速失败
        # 暴露，不再被 read 超时拖成「Connection error 数分钟后才进重试」。client
        # 层用标量（新旧版 SDK 都安全），connect/read 拆分通过 per-call openai.Timeout
        # 对象实现（见 _request_timeout_kwargs）。
        client_options["timeout"] = float(read_timeout)
        self._read_timeout_s = float(read_timeout)

        retries_raw = os.environ.get("VD_LLM_MAX_RETRIES", "").strip()
        if retries_raw:
            try:
                max_retries = int(retries_raw)
            except ValueError as exc:
                raise ValueError("VD_LLM_MAX_RETRIES must be an integer from 0 to 5") from exc
            if not 0 <= max_retries <= 5:
                raise ValueError("VD_LLM_MAX_RETRIES must be an integer from 0 to 5")
            client_options["max_retries"] = max_retries

        # 默认值：SDK 内部重试必须点掉（max_retries=0，重试纪律由本模块显式循环独占）。
        client_options.setdefault("max_retries", 0)
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            **client_options,
        )
        # 换供应商前清理旧 client：worker 可能在旧 client 克隆后仍持有引用，
        # 这里记录创建时间戳便于诊断「重试还在打旧供应商还是新供应商」。
        self._created_at = time.time()

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    def _model_request_options(self) -> dict:
        """Use NVIDIA's documented low-latency settings for GLM-5.3-Flash.

        NVIDIA documents reasoning_effort=max and clear_thinking=false as
        defaults for this model. Long hidden reasoning can delay user-facing
        content deltas, so the Flash model uses low effort by default. The
        setting is narrowly scoped to NVIDIA's official endpoint and this exact
        model; other OpenAI-compatible providers are not changed.
        """
        # Some callers/tests construct the provider via __new__ to isolate the
        # streaming loop. Missing endpoint state must preserve generic OpenAI
        # compatibility rather than turning a stream/cancel path into AttributeError.
        endpoint = str(getattr(self, "_base_url", "") or "").strip()
        model = str(getattr(self, "_model", "") or "").strip().lower()
        if not endpoint or not model:
            return {}
        if "://" not in endpoint:
            endpoint = "https://" + endpoint
        host = (urlparse(endpoint).hostname or "").lower().rstrip(".")
        if host != "integrate.api.nvidia.com" or model != "z-ai/glm-5.3-flash":
            return {}

        effort = os.environ.get("VD_LLM_REASONING_EFFORT", "low").strip().lower() or "low"
        if effort not in {"low", "high", "max"}:
            raise ValueError("VD_LLM_REASONING_EFFORT must be low, high, or max")
        return {
            # NVIDIA documents that GLM-5.3-Flash reasoning tokens consume the
            # same max_tokens budget as the visible answer. Its endpoint defaults
            # this budget to 1024; set the documented 8192 floor so the reasoning
            # phase cannot consume the entire response budget before JSON begins.
            "reasoning_effort": effort,
            "max_tokens": 8192,
            "extra_body": {"chat_template_kwargs": {"clear_thinking": True}},
        }

    def _refresh_runtime_config(self) -> bool:
        """重试前刷新当前生效配置（前端/环境变量变更）；有变化返回 True。

        2026-10-26 用户报障「换供应商救不了正在跑的节点」——provider 实例
        构造时固化 base_url/model/api_key，用户设置页改配置后，实例内字段仍是
        旧的。重试时读一次运行时配置，变了就更新字段。
        """
        try:
            cfg = get_llm_config()
        except Exception:  # noqa: BLE001 刷新失败保持原配置重试
            return False
        if not cfg.get("enabled"):
            return False
        new_base = cfg.get("base_url") or self._base_url
        new_model = cfg.get("model") or self._model
        changed = (new_base != self._base_url) or (new_model != self._model)
        if changed:
            self._base_url = new_base
            self._model = new_model
        return changed

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """调用 chat.completions 生成文本（流式接收）。

        2026-10-27 用户裁决：重试 3 次（10 次太久），固定 10s 间隔。
        每次单次超时 LLM_ATTEMPT_TIMEOUT_S（流式语义=相邻数据块间隔）。
        重试覆盖网络抖动/超时/5xx/429（限流等待后重试）；4xx 参数类错误
        不重试（重试也无用，快速失败）。全部失败抛 RuntimeError（调用方
        降级到规则路径）。
        """
        self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last_exc: Optional[Exception] = None
        _last_retry_state.update({"attempt": 0, "last_error": "", "ts": time.time()})
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            # 2026-10-26 用户裁决「停止就停止」：每次尝试前检测取消标志，
            # 让停止在 LLM 节点内部立即生效（不等节点跑完整个重试循环）。
            if _cancel_check is not None and _cancel_check():
                raise GenerationCancelled("创作已停止，文本模型调用被中断")
            try:
                # 2026-10-27 根修（用户实证「换供应商仍超时」对照矩阵）：
                # 大 prompt 长生成（scene_plan 完整 JSON）非流式请求下，
                # 模型全部生成完才返回首字节——httpx 120s 读超时在最后一
                # 块数据到达前先打穿（11KB 无 max_tokens 实测 121.5s 超时；
                # 同请求 stream=True 58.5s 成功）。改流式接收：超时语义变成
                # 「两块之间 ≤120s」而非「总时长 ≤120s」，长生成不再误判
                # 超时；取消检测也能在流式间隙生效。消费点签名零改动。
                stream = None
                try:
                    stream = self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        stream=True,
                        **_request_timeout_kwargs(
                            LLM_CONNECT_TIMEOUT_S,
                            getattr(self, "_read_timeout_s", None)),
                        **self._model_request_options(),
                    )
                    pieces: list = []
                    reasoning_chunks = 0
                    for chunk in stream:
                        if _cancel_check is not None and _cancel_check():
                            # 流式间隙同样响应停止（不等整段生成完）
                            try:
                                stream.close()
                            except Exception:
                                pass
                            raise GenerationCancelled("创作已停止，文本模型调用被中断")
                        try:
                            delta_obj = chunk.choices[0].delta
                        except (AttributeError, IndexError):
                            delta_obj = None
                        delta = None
                        reasoning = None
                        if delta_obj is not None:
                            delta = getattr(delta_obj, "content", None)
                            reasoning = getattr(delta_obj, "reasoning_content", None)
                        if delta:
                            pieces.append(delta)
                        elif reasoning:
                            # reasoning_content 是私有思考，绝不进可见输出（不进 on_delta、
                            # 不写日志）。仅计数用于诊断「模型确实产出了、只是内容为空」，
                            # 不作为返回值——调用方（分镜规划/素材匹配）都期望 JSON，
                            # 自然语言推理文本会让它们报「没有返回合法 JSON」。
                            reasoning_chunks += 1
                    content = "".join(pieces)
                except GenerationCancelled:
                    raise
                except (TypeError, AttributeError, StopIteration) as stream_exc:
                    # 流式响应结构异常（网关不支持 stream / chunk 结构不符）。
                    # 2026-11-18：不再原地降级重打非流式——实测同一慢网关上非流式
                    # 更慢且半数失败。置空后统一按可重试失败进重试循环（重试用新
                    # 连接重发同样的流式请求）。网络/HTTP 错误不在此列，直接上抛。
                    content = ""
                    logger.debug(f"LLM 流式响应结构异常，按可重试失败处理: {stream_exc}")
                if not content:
                    # 2026-10-27：空流兜底——部分 OpenAI 兼容网关对 stream 支持
                    # 不完整（流式返回非流式响应对象，或 0 chunk）。降级同请求非流式
                    # 一次；仍空则进正常重试循环。
                    #
                    # 2026-11-18 生产实录：step-5 在大 prompt 上流式单发就要
                    # 80~272s，网关还会偶发返回完全空的流（0 chunk）。若兜底沿用
                    # 120s 读超时，单次调用被打成「流式 + 非流式 120s」，再叠加
                    # 3 次重试 = 十几分钟卡死（前端「Request timed out」与
                    # 「Connection error」交替）。兜底改为短耐心：非流式同样受该
                    # 慢网关影响，不给它比流式更长的预算，到点即进重试循环（重试
                    # 用新连接重发同样的流式请求——那才是对网关抖动有效的恢复）。
                    resp = self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        **_request_timeout_kwargs(
                            LLM_CONNECT_TIMEOUT_S,
                            min(getattr(self, "_read_timeout_s", None)
                                or LLM_ATTEMPT_TIMEOUT_S,
                                EMPTY_STREAM_FALLBACK_TIMEOUT_S)),
                        **self._model_request_options(),
                    )
                    try:
                        content = resp.choices[0].message.content or ""
                    except (AttributeError, IndexError) as exc:
                        raise RuntimeError(f"LLM 返回结构异常: {exc}") from exc
                    if content:
                        return content
                    # 空内容走可重试路径（瞬时故障可能下轮恢复）
                    raise _EmptyGeneration(
                        f"LLM 流式返回空 content（reasoning_chunks={reasoning_chunks}）")
                return content
            except RuntimeError:
                raise  # 结构异常类，不重试
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None)
                # 4xx（除 429 限流）是参数/鉴权类错误，重试无意义
                if status is not None and 400 <= status < 500 and status != 429:
                    logger.warning(
                        f"LLM generate 客户端错误 {status}，不重试: {str(exc)[:160]}")
                    raise RuntimeError(f"LLM generate 失败: {exc}") from exc
                if attempt < LLM_MAX_RETRIES:
                    # 2026-10-26 用户报障「已经更换了供应商还在超时」：正在跑的
                    # 节点持有启动时的 provider 实例（旧 base_url/model），用户
                    # 在设置页换供应商救不了它。重试时刷新当前生效配置——变了就
                    # 换用新供应商的端点/key/model，下一次尝试立即生效。
                    if self._refresh_runtime_config():
                        logger.info(
                            f"LLM 重试前检测到配置变更，改用 base_url={self._base_url} "
                            f"model={self._model}")
                    # 2026-10-25 生产事故：nvidia 网关上游挂死时 TCP 连接被
                    # 静默挂起（models 200 但 chat 无响应）。重试必须丢弃旧
                    # client——httpx 连接池里的死连接会让后续重试全挂在同一条
                    # 连接上，10 次重试全部 Connection error（生产 7 连败实录）。
                    self._client = None
                    self._ensure_client()
                    _last_retry_state.update({
                        "attempt": attempt, "last_error": str(exc)[:160],
                        "ts": time.time(),
                    })
                    logger.warning(
                        f"LLM generate 第 {attempt}/{LLM_MAX_RETRIES} 次失败"
                        f"（{LLM_RETRY_WAIT_S:.0f}s 后重试）: {str(exc)[:160]}")
                    # 2026-10-26 用户裁决「停止就停止」：等待间隙按 1s 粒度
                    # 轮询取消标志——停止请求在重试等待期间也能立即生效，
                    # 不再被固定 time.sleep(10) 阻塞满 10s。
                    for _ in range(int(LLM_RETRY_WAIT_S)):
                        if _cancel_check is not None and _cancel_check():
                            raise GenerationCancelled("创作已停止，文本模型调用被中断")
                        time.sleep(1.0)
        raise RuntimeError(
            f"LLM generate 失败（重试 {LLM_MAX_RETRIES} 次后）: {last_exc}") from last_exc

    def generate_optional(self, prompt: str, system: Optional[str] = None,
                          timeout_s: float = OPTIONAL_LLM_TIMEOUT_S) -> str:
        """可选增强专用：短耐心调用，超时/异常一律抛 OptionalLLMUnavailable。

        素材语义匹配、BGM 语义选曲等「有则更好、无则降级」的增强点，绝不能
        把主链的 3×120s 重试纪律拖进可选步骤——生产实录：画面匹配读超时
        卡 6 分钟才降级，用户看到创作流程停摆。这里用守护线程 join(timeout)
        丢弃法：超时即放弃，调用方立刻走确定性/顺序兜底（与意图层同一模式）。
        """
        import threading
        box: dict = {}

        def _worker():
            try:
                box["raw"] = self.generate(prompt, system=system)
            except Exception as exc:
                box["exc"] = exc

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            logger.warning(f"可选 LLM 增强 {timeout_s:.0f}s 超时，调用方降级兜底")
            raise OptionalLLMUnavailable(f"可选 LLM 增强 {timeout_s:.0f}s 超时")
        if "exc" in box:
            raise OptionalLLMUnavailable(str(box["exc"])) from box["exc"]
        raw = box.get("raw") or ""
        if not raw:
            raise OptionalLLMUnavailable("可选 LLM 增强返回空内容")
        return raw

    supports_stream_cancellation = True

    def generate_stream(self, prompt: str, system: Optional[str] = None,
                        on_delta=None, on_stream_open=None, cancel_event=None) -> str:
        """Stream deltas, expose the active upstream stream, and close it on cancellation."""
        self._ensure_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        parts: list[str] = []
        reasoning_chunks = 0
        reasoning_chars = 0
        visible_chunks = 0
        stream = None
        try:
            stream = self._client.chat.completions.create(
                model=self._model, messages=messages, stream=True,
                **_request_timeout_kwargs(
                    LLM_CONNECT_TIMEOUT_S,
                    getattr(self, "_read_timeout_s", LLM_ATTEMPT_TIMEOUT_S)),
                **self._model_request_options(),
            )
            if callable(on_stream_open):
                on_stream_open(stream)
            for event in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("LLM stream 已取消")
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                reasoning = getattr(delta, "reasoning_content", None)
                if isinstance(reasoning, str) and reasoning:
                    reasoning_chunks += 1
                    reasoning_chars += len(reasoning)
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    parts.append(content)
                    visible_chunks += 1
                    if on_delta is not None:
                        on_delta(content)
                elif isinstance(content, list):
                    for item in content:
                        fragment = getattr(item, "text", None)
                        if isinstance(fragment, str) and fragment:
                            parts.append(fragment)
                            visible_chunks += 1
                            if on_delta is not None:
                                on_delta(fragment)
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("LLM stream 已取消")
        except Exception as exc:
            cancelled = cancel_event is not None and cancel_event.is_set()
            reason = "已取消" if cancelled else "失败"
            # Keep transport diagnostics useful without logging request bodies,
            # reasoning text, credentials, or raw exception messages.
            chain = []
            current = exc
            seen = set()
            while current is not None and id(current) not in seen and len(chain) < 5:
                seen.add(id(current))
                chain.append(type(current).__name__)
                current = current.__cause__ or current.__context__
            exception_chain = "->".join(chain) or type(exc).__name__
            metrics = (
                f"exception_chain={exception_chain}, reasoning_chunks={reasoning_chunks}, "
                f"reasoning_chars={reasoning_chars}, visible_chunks={visible_chunks}"
            )
            logger.warning(f"LLM stream {reason} (model={self._model}, {metrics})")
            raise RuntimeError(f"LLM stream {reason} ({metrics})") from exc
        finally:
            closer = getattr(stream, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        return "".join(parts)


# ---------------------------------------------------------------------------
# 工厂：有 key 返真实 provider，无 key 返 None（诚实）
# ---------------------------------------------------------------------------
# 运行时配置：前端 /api/llm_config 写入，进程内覆盖环境变量；空 key 清除回退 env
_runtime_config: Optional[dict] = None
_provider_cache: Optional[Optional[LLMProvider]] = None


def set_llm_config(
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """运行时设置 LLM 配置（前端调用），覆盖环境变量并重置单例缓存。"""
    global _runtime_config, _provider_cache
    if not api_key or not api_key.strip():
        _runtime_config = None
    else:
        _runtime_config = {
            "api_key": api_key.strip(),
            "base_url": (base_url or "").strip() or None,
            "model": (model or "").strip() or None,
        }
    _provider_cache = None


def get_llm_config() -> dict:
    """返回当前生效的 LLM 配置（前端状态展示用）。

    {"enabled", "api_key_set", "base_url", "model", "source"}
    source: runtime（前端设置）| env（环境变量）| none（未启用）
    """
    if _runtime_config is not None:
        return {
            "enabled": True,
            "api_key_set": True,
            "base_url": _runtime_config.get("base_url") or DEFAULT_BASE_URL,
            "model": _runtime_config.get("model") or DEFAULT_MODEL,
            "source": "runtime",
        }
    env_key = os.environ.get(ENV_API_KEY, "").strip()
    if env_key:
        return {
            "enabled": True,
            "api_key_set": True,
            "base_url": os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL,
            "model": os.environ.get(ENV_MODEL) or DEFAULT_MODEL,
            "source": "env",
        }
    return {
        "enabled": False,
        "api_key_set": False,
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "source": "none",
    }


def get_llm() -> Optional[LLMProvider]:
    """获取 LLM Provider 单例。

    配置优先级：model_config.json 的 llm 角色（前端五角色配置中心）>
    运行时 set_llm_config > 环境变量 VD_LLM_API_KEY。
    均无 → 返回 None（诚实降级，调用方走规则路径）。

    结果缓存（进程内单例），避免每次调用重建 client。
    """
    global _provider_cache
    if _provider_cache is not None:
        return _provider_cache
    if _runtime_config is not None:
        api_key = _runtime_config["api_key"]
        base_url = _runtime_config.get("base_url")
        model = _runtime_config.get("model")
    else:
        # 前端「大模型配置」中心（model_config.json 的 llm 角色）——用户
        # 在页面保存的 key 优先于环境变量（否则配置了 key 仍走 env 空→None）
        try:
            from video_director import model_config
            mc = model_config.get_role_config("llm")
            if mc.get("api_key"):
                api_key = mc["api_key"]
                base_url = mc.get("base_url") or None
                model = mc.get("model") or None
                logger.info(f"LLM 已启用（大模型配置中心 llm 角色，model={model}）")
            else:
                raise KeyError
        except Exception:
            api_key = os.environ.get(ENV_API_KEY, "").strip()
            base_url = None
            model = None
    if not api_key:
        logger.info(
            f"LLM 未启用（{ENV_API_KEY} 未配置），消费点将走规则路径兜底"
        )
        _provider_cache = None
        return None
    try:
        provider = OpenAICompatibleProvider(
            api_key=api_key, base_url=base_url, model=model
        )
    except Exception as exc:
        logger.warning(f"LLM Provider 构造失败，降级 None: {exc}")
        _provider_cache = None
        return None
    _provider_cache = provider
    return _provider_cache


def reset_llm_cache() -> None:
    """重置 provider 缓存（测试用，生产代码不应调用）。"""
    global _provider_cache
    _provider_cache = None


# ---------------------------------------------------------------------------
# 消费点 1：文案优化
# ---------------------------------------------------------------------------
ENHANCE_SYSTEM = """你是短视频文案润色师。规则：
1. 保持原意不变，不改任何事实/数字/专有名词
2. 优化口语化与节奏，让朗读更流畅
3. 按句号分句（每句独立完整，不要合并句子）
4. 不要新增内容，不要删除原文要点
5. 只输出润色后的文案，不要任何解释或前后缀"""

ENHANCE_USER_TEMPLATE = """原文：
{script}

请按规则润色。"""


def enhance_script(script: str) -> str:
    """文案优化：保持原意，优化口语化与节奏，按句号分句。

    无 provider 时原样返回 script（诚实降级）。
    """
    if not script or not script.strip():
        return script
    provider = get_llm()
    if provider is None:
        logger.debug("enhance_script: 无 LLM provider，原样返回")
        return script
    prompt = ENHANCE_USER_TEMPLATE.format(script=script)
    try:
        enhanced = provider.generate(prompt, system=ENHANCE_SYSTEM)
    except GenerationCancelled:
        raise  # 用户停止创作：穿透，不降级原样返回
    except Exception as exc:
        logger.warning(f"enhance_script LLM 调用失败，原样返回: {exc}")
        return script
    enhanced = enhanced.strip()
    if not enhanced:
        logger.warning("enhance_script LLM 返回空，原样返回")
        return script
    return enhanced


# ---------------------------------------------------------------------------
# 消费点 1.5：主题 → 脚本
# ---------------------------------------------------------------------------
SCRIPT_GENERATE_SYSTEM = """你是短视频编导。根据用户主题写一份可直接配音的中文短视频文案。
规则：
1. 保持事实克制，不编造具体数据、人物经历或新闻事实。
2. 开头直接进入主题，句子适合口语配音。
3. 默认 30-60 秒；按用户指定时长调整。
4. 不要标题、不要分镜说明、不要镜头编号，只输出正文。
5. 每段尽量短，便于自动切成场景。"""

def generate_script(topic: str, platform: str = "", duration: str = "", style: str = "") -> str:
    """主题 → 可直接进入 compose_auto 的脚本文案；无 LLM 时抛明确错误。"""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("主题不能为空")
    provider = get_llm()
    if provider is None:
        raise RuntimeError("未配置文本大模型，请先到「设置 → 模型」配置文本模型")
    context = []
    if platform:
        context.append("目标平台：" + platform)
    if duration:
        context.append("目标时长：" + duration + " 秒")
    if style:
        context.append("风格：" + style)
    prompt = "主题：" + topic + ("\n" + "\n".join(context) if context else "")
    result = provider.generate(prompt, system=SCRIPT_GENERATE_SYSTEM).strip()
    if not result:
        raise RuntimeError("大模型返回空文案")
    return result

# ---------------------------------------------------------------------------
# 消费点 2：分镜视觉提示
# ---------------------------------------------------------------------------
STORYBOARD_SYSTEM = """你是视频分镜师。给定若干文案句子，为每句生成视觉提示。

规则：
1. visual_hint：一句话描述适合搭配的画面（视觉化、具体，不超过 30 字）
2. keywords：3-5 个画面关键词，用于素材匹配（名词为主）
3. 不要改写原句，text 字段必须是原句
4. 只输出 JSON 数组，不要任何解释或 markdown 代码块

输出格式：
[
  {"text": "原句1", "visual_hint": "画面描述", "keywords": ["词1", "词2", "词3"]},
  ...
]"""

STORYBOARD_USER_TEMPLATE = """文案句子：
{lines}

为每句生成视觉提示，只输出 JSON 数组。"""


def storyboard_hints(scene_texts: List[str]) -> List[dict]:
    """为每句生成视觉提示。

    返回 List[{"text": str, "visual_hint": str, "keywords": List[str]}]，
    顺序与 scene_texts 一致。

    无 provider 时返回每句的空 visual_hint（规则路径兜底）：
        [{"text": s, "visual_hint": "", "keywords": []}, ...]
    """
    if not scene_texts:
        return []
    provider = get_llm()
    if provider is None:
        logger.debug("storyboard_hints: 无 LLM provider，返回空 visual_hint")
        return [
            {"text": t, "visual_hint": "", "keywords": []}
            for t in scene_texts
        ]
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(scene_texts))
    prompt = STORYBOARD_USER_TEMPLATE.format(lines=lines)
    try:
        raw = provider.generate(prompt, system=STORYBOARD_SYSTEM)
    except GenerationCancelled:
        raise  # 用户停止创作：穿透，不降级空兜底
    except Exception as exc:
        logger.warning(f"storyboard_hints LLM 调用失败，走空兜底: {exc}")
        return [
            {"text": t, "visual_hint": "", "keywords": []}
            for t in scene_texts
        ]
    parsed = _parse_storyboard(raw, scene_texts)
    if parsed is None:
        logger.warning("storyboard_hints LLM 输出解析失败，走空兜底")
        return [
            {"text": t, "visual_hint": "", "keywords": []}
            for t in scene_texts
        ]
    return parsed


def _parse_storyboard(raw: str, scene_texts: List[str]) -> Optional[List[dict]]:
    """解析 LLM 返回的 JSON 数组，对齐到 scene_texts 顺序与长度。

    宽容策略：
    - markdown 代码块包裹 → 剥掉
    - 返回 JSON 对象而非数组 → 取 data 字段
    - 条数对不上 → 按顺序尽量匹配，缺的补空 hint
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 找第一个 [ 到最后一个 ]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        # 可能是 {"data": [...]} 形式
        start_obj, end_obj = text.find("{"), text.rfind("}")
        if start_obj >= 0 and end_obj > start_obj:
            try:
                obj = json.loads(text[start_obj : end_obj + 1])
            except json.JSONDecodeError:
                return None
            if isinstance(obj, dict) and isinstance(obj.get("data"), list):
                items = obj["data"]
            else:
                return None
        else:
            return None
    else:
        try:
            items = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(items, list):
        return None
    result: List[dict] = []
    for i, original in enumerate(scene_texts):
        entry = items[i] if i < len(items) and isinstance(items[i], dict) else {}
        hint = entry.get("visual_hint", "") or ""
        kws = entry.get("keywords", []) or []
        if not isinstance(kws, list):
            kws = []
        # 强制 text 对齐原文（防止 LLM 改写）
        result.append({
            "text": original,
            "visual_hint": str(hint).strip(),
            "keywords": [str(k) for k in kws if k],
        })
    return result


# ---------------------------------------------------------------------------
# 扩展位：复刻拆解 prompt（后续产品线消费，暂不接线）
# ---------------------------------------------------------------------------
# storyboard_deconstruct(reference_video_desc: str) -> dict
#   用于把参考视频拆解为可复用的镜头序列。签名与 prompt 待产品线接入时定义，
#   此处保留扩展位注释，不提前实现（YAGNI）。
