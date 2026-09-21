"""_test_llm_provider.py — LLM Provider 层验证（临时脚本）。

验证点：
1. 无 VD_LLM_API_KEY 时 get_llm() is None
2. 无 provider 时 enhance_script 原样返回
3. 无 provider 时 storyboard_hints 返回空 visual_hint 且不崩溃
4. 有 key 时构造 OpenAICompatibleProvider，不触发网络（import 阶段）
5. monkeypatch fake provider 注入 get_llm，generate 能返回字符串
6. storyboard_hints 解析正常 JSON 数组 + 异常容错
7. 断言不 import 即不触发网络调用（通过检查 _client 延迟初始化）
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 强制清空环境变量，保证测试从干净状态开始
for key in ("VD_LLM_API_KEY", "VD_LLM_BASE_URL", "VD_LLM_MODEL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL"):
    os.environ.pop(key, None)

from video_director import llm_provider
from video_director.llm_provider import (
    LLMProvider,
    OpenAICompatibleProvider,
    get_llm,
    reset_llm_cache,
    enhance_script,
    storyboard_hints,
    _parse_storyboard,
)

failures = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------------------
# 场景 1：无 API_KEY 的诚实降级
# ---------------------------------------------------------------------------
reset_llm_cache()
provider = get_llm()
check("无 VD_LLM_API_KEY 时 get_llm() is None", provider is None,
      f"got {type(provider).__name__ if provider else 'None'}")

# enhance_script 原样返回
script = "清晨的城市慢慢醒来。地铁穿过隧道，把灯光带向远方。"
enhanced = enhance_script(script)
check("无 provider 时 enhance_script 原样返回", enhanced == script,
      f"got: {enhanced!r}")

# storyboard_hints 返回空 visual_hint
scenes = ["清晨的城市慢慢醒来。", "地铁穿过隧道，把灯光带向远方。"]
hints = storyboard_hints(scenes)
check("无 provider 时 storyboard_hints 不崩溃", isinstance(hints, list),
      f"type={type(hints).__name__}")
check("storyboard_hints 条数对齐", len(hints) == len(scenes),
      f"{len(hints)} vs {len(scenes)}")
check("storyboard_hints text 对齐原文",
      all(h["text"] == s for h, s in zip(hints, scenes)))
check("storyboard_hints visual_hint 为空字符串",
      all(h["visual_hint"] == "" for h in hints),
      f"hints={[h['visual_hint'] for h in hints]}")
check("storyboard_hints keywords 为空列表",
      all(h["keywords"] == [] for h in hints))


# ---------------------------------------------------------------------------
# 场景 2：有 API_KEY 但不触发网络（延迟初始化）
# ---------------------------------------------------------------------------
reset_llm_cache()
os.environ["VD_LLM_API_KEY"] = "sk-test-fake-key-not-real"
os.environ["VD_LLM_BASE_URL"] = "http://localhost:0/nonexistent"  # 不合法端点
os.environ["VD_LLM_MODEL"] = "test-model"

provider = get_llm()
check("有 VD_LLM_API_KEY 时 get_llm() 非 None", provider is not None,
      f"got {type(provider).__name__ if provider else 'None'}")
check("get_llm() 返回 OpenAICompatibleProvider",
      isinstance(provider, OpenAICompatibleProvider))

# 关键：构造了 provider 但 _client 还是 None（延迟初始化）
check("Provider 构造后 _client 仍为 None（延迟初始化，不触发网络）",
      provider._client is None,
      f"_client={provider._client!r}")

# 调用 generate 会尝试连接 localhost:0（必然失败），但 client 在此时才构造
try:
    provider.generate("hello")
    check("Provider.generate 用非法端点应抛 RuntimeError", False,
          "未抛异常")
except RuntimeError as exc:
    check("Provider.generate 用非法端点抛 RuntimeError", True, str(exc)[:80])
except Exception as exc:
    # openai 可能抛自己的异常，我们包装成 RuntimeError
    check("Provider.generate 用非法端点抛 RuntimeError",
          isinstance(exc, RuntimeError), f"got {type(exc).__name__}: {exc}"[:80])

# 此时 _client 已被构造（说明延迟初始化生效）
check("generate 后 _client 已构造（延迟初始化生效）",
      provider._client is not None)

# 清理
reset_llm_cache()
for key in ("VD_LLM_API_KEY", "VD_LLM_BASE_URL", "VD_LLM_MODEL"):
    os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# 场景 3：monkeypatch fake provider 注入 get_llm，generate 返回字符串
# ---------------------------------------------------------------------------
class FakeProvider:
    """测试用 fake provider，实现 LLMProvider 协议。"""

    def __init__(self, response: str = "fake response"):
        self._response = response
        self.calls = []

    def generate(self, prompt: str, system=None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        return self._response


fake = FakeProvider("enhanced script from fake")
# 直接注入缓存
llm_provider._provider_cache = fake

provider_now = get_llm()
check("monkeypatch 后 get_llm() 返回 fake", provider_now is fake)

result = enhance_script("原文")
check("enhance_script 用 fake provider 返回 fake response",
      result == "enhanced script from fake", f"got {result!r}")

# storyboard_hints 用 fake provider（返回 JSON 数组）
fake_json = json.dumps([
    {"text": "清晨的城市慢慢醒来。", "visual_hint": "日出城市空镜", "keywords": ["日出", "城市", "空镜"]},
    {"text": "地铁穿过隧道，把灯光带向远方。", "visual_hint": "地铁隧道灯光", "keywords": ["地铁", "隧道", "灯光"]},
], ensure_ascii=False)
fake2 = FakeProvider(fake_json)
llm_provider._provider_cache = fake2

scenes2 = ["清晨的城市慢慢醒来。", "地铁穿过隧道，把灯光带向远方。"]
hints2 = storyboard_hints(scenes2)
check("storyboard_hints 用 fake provider 返回解析结果",
      len(hints2) == 2 and hints2[0]["visual_hint"] == "日出城市空镜",
      f"got: {hints2}")
check("storyboard_hints text 对齐原文（防 LLM 改写）",
      all(h["text"] == s for h, s in zip(hints2, scenes2)))
check("storyboard_hints keywords 解析为 list",
      isinstance(hints2[0]["keywords"], list) and len(hints2[0]["keywords"]) == 3)


# ---------------------------------------------------------------------------
# 场景 4：storyboard_hints 解析容错（markdown 包裹、对象包裹、异常输入）
# ---------------------------------------------------------------------------
# markdown 包裹
md_wrapped = '```json\n[{"text":"x","visual_hint":"hint","keywords":["a"]}]\n```'
parsed = _parse_storyboard(md_wrapped, ["x"])
check("_parse_storyboard 剥 markdown 包裹", parsed is not None and len(parsed) == 1,
      f"parsed={parsed}")

# 对象包裹
obj_wrapped = '{"data": [{"text":"y","visual_hint":"h","keywords":["b"]}]}'
parsed2 = _parse_storyboard(obj_wrapped, ["y"])
check("_parse_storyboard 对象包裹取 data 字段",
      parsed2 is not None and len(parsed2) == 1 and parsed2[0]["text"] == "y",
      f"parsed={parsed2}")

# 完全异常输入
parsed3 = _parse_storyboard("not json at all", ["z"])
check("_parse_storyboard 异常输入返回 None", parsed3 is None)

# 条数对不上时补空
parsed4 = _parse_storyboard(
    '[{"text":"ignore","visual_hint":"h","keywords":["a"]}]',
    ["s1", "s2"],
)
check("_parse_storyboard 条数不够时补空",
      parsed4 is not None and len(parsed4) == 2 and parsed4[1]["visual_hint"] == "",
      f"parsed={parsed4}")


# ---------------------------------------------------------------------------
# 场景 5：LLMProvider Protocol 鸭子类型校验
# ---------------------------------------------------------------------------
check("FakeProvider 满足 LLMProvider Protocol",
      isinstance(fake, LLMProvider))
check("OpenAICompatibleProvider 满足 LLMProvider Protocol",
      isinstance(OpenAICompatibleProvider("k"), LLMProvider))


# ---------------------------------------------------------------------------
# 场景 6：enhance_script LLM 调用失败时降级
# ---------------------------------------------------------------------------
class FailingProvider:
    def generate(self, prompt: str, system=None) -> str:
        raise ConnectionError("network down")


llm_provider._provider_cache = FailingProvider()
fallback = enhance_script("原文不丢")
check("enhance_script LLM 失败时原样返回", fallback == "原文不丢",
      f"got {fallback!r}")

# storyboard_hints LLM 失败时降级
hints_fail = storyboard_hints(["句1", "句2"])
check("storyboard_hints LLM 失败时降级空 visual_hint",
      len(hints_fail) == 2 and all(h["visual_hint"] == "" for h in hints_fail))


# 清理
reset_llm_cache()


# ---------------------------------------------------------------------------
# 场景 7：import 本模块不触发网络（延迟初始化验证）
# ---------------------------------------------------------------------------
# 重新 import 一次，确认不抛网络异常
reset_llm_cache()
os.environ["VD_LLM_API_KEY"] = "sk-import-test-key"
os.environ["VD_LLM_BASE_URL"] = "http://127.0.0.1:1/never-reachable"
p = get_llm()
check("import 后 get_llm() 构造不触发网络", p is not None and p._client is None)
reset_llm_cache()
for key in ("VD_LLM_API_KEY", "VD_LLM_BASE_URL", "VD_LLM_MODEL"):
    os.environ.pop(key, None)


print()
if failures:
    print(f"RESULT: {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("RESULT: 全部断言通过")
