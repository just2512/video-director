# AI Video Director 统一开发执行方案

> 收敛自多轮补充讨论（V2/Hypit/视频理解/SVF/jianying-headless/guizang/OpenMontage）
> 与用户三轮批评。本方案只列"零依赖可落地 + 产品承诺"的项，API-key 依赖项一律
> 降级为可选增强，不阻塞、不强行实现。

## 一、产品承诺（用户视角的唯一验收标准）

1. 一句话文案 + 素材（可选）→ 自动成片
2. 零 API key、零本地模型可跑通核心全链路
3. 按文案内容匹配画面（不是重复循环）
4. 一键启动 WebUI（`python run_webui.py` 直接可开）

## 二、已落地（对应 commit，不再返工）

| 能力 | 位置 |
|---|---|
| Composition IR | models.py |
| 语义匹配（文件名/概念桥接/手工标注，零依赖） | matcher.py |
| Style Contract 四风格 AUTO | style.py |
| TTS 词级时间戳（EdgeTTS 零 key） | narration.py |
| 字幕烧录 | subtitle.py / renderer.py |
| FFmpeg 确定性渲染 + BGM/SFX ducking | renderer.py / audio.py |
| 七项确定性 QC + ≤3 修复循环 | qc.py / pipeline.py |
| 任务状态机 + probe 缓存 | store.py |
| doctor + capability gate + run manifest + 失败二分 | doctor.py / runlog.py |
| SFX 动作绑定 | audio.py |
| 独立项目 + 一键启动器 | run_webui.py |
| 前端真实测试（Playwright DOM 断言） | tools/test_webui_*.py |

## 三、本轮落地项（并行，一次性交付，不留下一轮）

### 项 A：SCRIPT_ONLY 纯文案模式（产品承诺断裂点，零依赖，必做）

文案 → 场景 → 每场景「纯色背景卡 + 场景文字」→ TTS 旁白 + 字幕 → FFmpeg 合成。
无需任何视频/图片素材。models.Layer 已支持 text 类型（预留），补齐渲染即可。

### 项 B：Pipeline Manifest + Checkpoint（OpenMontage 架构思想，零依赖）

把硬编码的 compose_auto 拆成 YAML 声明式的多流水线（script-media / script-only），
落地 OpenMontage「把 AI 导演从 Prompt 变成 Pipeline」的核心思想；Checkpoint 支持
「换风格只重算 style 下游，不重跑 TTS/资产分析」。

### 项 C：Capability Registry（OpenMontage Tool Registry 思想，零依赖）

系统运行前先声明能力清单（TTS=Edge / Vision=unavailable / Renderer=FFmpeg），
替代当前散落在 doctor 里的检查，成为 Selector 的正式输入。

## 四、明确不落地（诚实降级，不是"遗留问题"）

| 项 | 原因 |
|---|---|
| Visual QC（AI 看片） | 依赖视觉 API key，用户无 key |
| NativeVideoProvider（原生视频理解） | 同上是 API key |
| LLM Planner 主路 | 无 LLM key，RulePlanner 已覆盖确定性闭环 |
| HyperFrames 集成 | 依赖 Node22+Chrome 重型运行时 |

以上不是"下一轮再做"，而是"用户提供对应能力时才启用"，当前诚实 SKIP 并已在
doctor/README 明示。

## 五、验收闭环（不分先后全部达成才算完成）

1. 三项各自 pytest 通过
2. SCRIPT_ONLY 端到端真实成片（无素材）QC PASS
3. `python run_webui.py` 前端真实测试三项全绿（含新增纯文案模式）
4. main 分支合并，git 干净