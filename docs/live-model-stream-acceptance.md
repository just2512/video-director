# NVIDIA 真实模型流式与 AI 提案审批验收

## 目的与边界

这项验收只验证真实 Provider 的 token 流、提案生成、显式审批和单次消费边界。它不是视频/图片生成验收，也不是开放式模型创作质量测试。

普通 CI 不调用真实模型。默认执行的 mock / fake provider 测试只能验证协议和应用逻辑，不能证明 NVIDIA 上游确实逐 token 流式返回。真实验收必须由操作者显式设置 `VD_LLM_LIVE_STREAM_TEST=1` 后在本机或自托管 Runner 上执行。

## 固定测试配置

- Provider：NVIDIA 官方 OpenAI-compatible endpoint
- Base URL：`https://integrate.api.nvidia.com/v1`
- Model：`z-ai/glm-5.3-flash`
- Reasoning effort：默认 `low`；也可显式设为 `high` 或 `max`
- API key：NVIDIA API key；仅通过环境变量注入，不得写入命令文本、测试输出或仓库文件

该模型在 `video_director/llm_provider.py` 中有针对性请求参数：`reasoning_effort`、`max_tokens=8192`、`clear_thinking=true`。该逻辑只匹配 NVIDIA 官方 endpoint 与精确模型 ID，不改变其他 Provider。

## 本机执行（PowerShell）

在仓库根目录执行，并先激活与项目一致的 Python 3.11 环境。下面优先读取当前进程中的 `NVIDIA_API_KEY`；若不存在，则从应用已保存的 `llm` 角色配置读取 key。读取结果只赋值给进程环境变量，不打印 key。

```powershell
$env:VD_LLM_API_KEY = $env:NVIDIA_API_KEY
if ([string]::IsNullOrWhiteSpace($env:VD_LLM_API_KEY)) {
  $env:VD_LLM_API_KEY = (& python -c "from video_director.model_config import get_role_config; print(get_role_config('llm').get('api_key', ''))").Trim()
}
if ([string]::IsNullOrWhiteSpace($env:VD_LLM_API_KEY)) {
  throw "缺少 NVIDIA API key；先在本机凭据/模型配置中配置，不要把 key 写入脚本或仓库。"
}

$env:VD_LLM_BASE_URL = "https://integrate.api.nvidia.com/v1"
$env:VD_LLM_MODEL = "z-ai/glm-5.3-flash"
$env:VD_LLM_REASONING_EFFORT = "low"
$env:VD_LLM_LIVE_STREAM_TEST = "1"

python -m pytest -vv -s tests/test_film_studio_live_ai_streaming.py --junitxml=live-stream-junit.xml
$pytestExit = $LASTEXITCODE
$env:LIVE_STREAM_CONFIG_OUTCOME = "success"
$env:LIVE_STREAM_TEST_OUTCOME = if ($pytestExit -eq 0) { "success" } else { "failure" }
python -m video_director.live_stream_acceptance --junit live-stream-junit.xml
if ($LASTEXITCODE -ne 0) {
  throw "真实模型流式验收未达到 PASS；保留失败类别和 JUnit 证据，勿将其标记为通过。"
}
```

不要用 `Write-Host $env:VD_LLM_API_KEY`、`echo` 或日志输出密钥。测试报告只保留结果，不应上传包含凭据的文件。

## 必须通过的行为

测试入口：`tests/test_film_studio_live_ai_streaming.py`。

1. **真实上游流式**：请求返回非空 token fragments；至少两个非空 token 事件必须先于最终 result。首个 token 到最终 result 的间隔必须不少于 0.1 秒，以排除代理把全部内容缓冲到最后一次性返回。
2. **结构化提案**：最终 result 必须 `ok=true`，返回提案 ID，`base_revision` 与请求起始 revision 完全一致；本用例要求恰好一个 `move clip-b at=8.5` 操作。
3. **生成不写正式文档**：提案生成阶段，FilmDocument revision 与 `film.html` 字节必须保持不变。
4. **明确审批才写入**：通过 `/api/editor/film/apply-proposal` 明确提交 proposal ID 和 base revision 后，服务端返回 HTTP 200；`source=ai_proposal`，`applied_proposal` 与原提案 ID 一致，FilmDocument revision 前进，clip-b 移至 8.5 秒。
5. **操作权威性**：客户端即使附带伪造 `ops`，也不能替换服务端登记的提案操作。
6. **拒绝重复审批**：同一 proposal 再次提交必须 HTTP 409，错误码 `PROPOSAL_NOT_FOUND_OR_STALE`；第二次请求不得产生新 revision，也不得再次写入 `film.html`。

测试请求超时上限为 240 秒。一个显式模型调用失败后，应先依据证据处理 Provider / 模型 / 代理问题，不要通过重复调用掩盖第一次失败。

## 状态含义

`video_director.live_stream_acceptance` 只允许以下结果：

| 状态 | 含义 |
|---|---|
| PASS | 唯一匹配的真实模型 JUnit 用例成功，完整 token / proposal / apply / replay assertions 通过 |
| MISSING | 本次未显式 opt-in；不是通过，也不是代码失败 |
| REJECT | 显式测试失败，或没有 token、提案不符合契约、审批链失败 |
| BLOCKED_PROVIDER | Provider 余额不足等账户侧阻断；不是 PASS |
| BLOCKED_MODEL | 模型已退役或返回 HTTP 410；不是 PASS |
| UNKNOWN | JUnit 缺失、损坏或证据不唯一 |

显式请求真实模型后，只有 `PASS` 才能认定真实模型验收通过。Provider / Model blocker 需要修复配置后重新验收；不得将 `MISSING`、`UNKNOWN` 或 mock 测试改写为 PASS。

## 当前 CI 策略

普通 push 不调用真实模型，不自动消耗 NVIDIA 配额。需要在已配置 NVIDIA 凭据的自托管 Runner 上执行真实验收时，使用一次性提交标记触发：

```text
[live-stream-smoke] [live-provider=nvidia] [live-model=z-ai/glm-5.3-flash]
```

标记必须出现在触发 push 的 commit message 中；仅写入文档正文不会触发。验收完成后按 JUnit 与 workflow 输出记录 PASS/REJECT，不允许仅凭 endpoint probe 通过。该标记只影响本次 workflow：优先使用显式提供的 `NVIDIA_API_KEY` / GitHub Secrets / Runner 环境变量；只有这些来源均未配置时，才回退到 Runner 已保存的 `model_config.json`（不打印、不写回），避免过期本机 key 覆盖显式配置。Provider 固定到 NVIDIA 官方 endpoint、模型固定到 `z-ai/glm-5.3-flash`，先执行最小 token 探测，再执行上面的完整真实模型 pytest。完整测试只在探测通过后启动；Provider 探测或真实测试失败时 CI 不得报告 PASS。密钥只传给隔离子进程，真实模型 JUnit 只保留脱敏后的测试结果。

workflow 的真实模型验收入口仍然是 opt-in；未带标记的普通 push 应报告 MISSING，而不是把 mock 测试当作真实模型通过。

### CI 执行顺序与发布门禁

带有上述一次性 commit message 标记的 push，仍必须先完成同一提交 SHA 的全量 pytest、React Studio 构建/集成检查和 Timeline / migration / export 浏览器回归。只有全量测试与 Timeline / Studio 发布门禁均为 success，workflow 才允许配置 NVIDIA 凭据并启动真实模型探测与 token-stream pytest；任一门禁失败时，真实模型步骤不得运行。真实模型验收的 PASS 还要求唯一匹配的 JUnit 用例完整通过，不能用最小探测成功代替。

