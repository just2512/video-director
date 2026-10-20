# AI Video Director

一句话文案 + 素材（可选）→ AI 自动导演、配音、配乐、成片。

**这不是又一个 MoneyPrinterTurbo。** MPT 是"文案→图库素材→模板拼合"的自动剪辑器；
本系统是**语义视频导演**：AI 先理解文案在讲什么，再为每个句子挑选语义匹配的画面，
把视频当成一次"导演"而不是一次"合成"。

## 核心差异化（我们独有的概念，不在任何参考项目里）

| 概念 | 含义 | 为什么独特 |
|---|---|---|
| **Composition IR** | AI 规划的唯一产物，结构化 JSON | AI 只产出时间轴契约，禁止直接生成 FFmpeg 命令——AI 做决策，确定性引擎做执行 |
| **Narration 主轴** | 旁白是唯一主时间轴 | 画面、转场、字幕全部服从旁白节奏，不是反过来 |
| **语义匹配** | 场景↔画面内容匹配 | 打"焚烧"配焚烧画面，"出征"配军队画面；不是随机选段（SVF 就纯随机） |
| **Style Contract** | 风格是结构化契约不是名字 | 四风格各绑定字幕/动效/转场/音频参数；AUTO 按文案特征选，绝不评分 |
| **概念桥接** | 文案用语↔画面概念的词典 | 解决"付之一炬"匹配不到"焚烧"的词面盲区，零 API 零本地模型 |
| **六级降级链** | 素材理解 P1→P5 逐级降级 | 无 key、无标注也能跑（文件名语义），诚实 MISSING 不编造 |
| **确定性 QC + 修复** | 七项检查 + ≤3 次修复循环 | "生成成功"不等于"作品完成" |

## 三个硬原则

1. **零成本起步**：核心全链路不需要任何 API key、不需要本地模型。TTS 用 EdgeTTS(免费)、
   内容匹配用文件名语义+概念桥接、画面用 FFmpeg。Vision API 只是可选增强。
2. **AI 决策，引擎执行**：AI（或有朝一日的 LLM）只产出 Composition IR；渲染、校验、QC
   全部是确定性 Python，可复现可审计。
3. **诚实降级**：素材不足报 MISSING、音效没有报 NO_SFX、风格不认识回退 AUTO 并提示。
   绝不编造、绝不假装成功。

## 快速开始

```bash
# 零配置启动（自动找有依赖的 Python，自动开浏览器）
python run_webui.py

# CLI
python -m video_director auto --script 文案.txt --videos 素材目录/

# film.html 智能体工作流（项目已初始化影片文档后）
python -m video_director film status --project-id <project_id>
python -m video_director film validate --project-id <project_id>
python -m video_director film look --project-id <project_id>
python -m video_director film diff --project-id <project_id>
python -m video_director film render --project-id <project_id>
```

外部编程智能体可以直接读取和修改项目内的 `film.html`、`scenes/*.html` 与 `assets/*`；Studio 通过文件监视实时刷新。HTML 镜头如需参与逐帧导出，应实现 `window.film.frame(t)`，并可通过 `window.film.ready` 暴露异步资源就绪状态。执行 `film look` 会生成真实浏览器接触表并反序检查重复时刻的一致性。

编程智能体的完整文件协议、`frame(t)` 约定、冲突规则、离线资源边界与验收步骤见 [Film Agent Manual](docs/FILM_AGENT_MANUAL.md)。仓库根目录的 [AGENTS.md](AGENTS.md) 要求 Claude Code、Codex 等 Agent 在修改影片前读取同一份手册。

## 三个关键能力现在就能用（无需 key）

- **文件名语义匹配**：素材命名 `战场出征.mp4`、`送葬队伍.mp4` → 自动对应文案相关场景
- **风格 AUTO**：文案含"历史/悬疑"自动选克制的电影感，含"快节奏"选强动效
- **完整成片**：文案→TTS→字幕→画面匹配→变速率→BGM/SFX→QC→final.mp4

## 项目结构

```
video-director/
├── video_director/        # 语义导演引擎
│   ├── models.py           #   Composition IR 契约（核心差异化）
│   ├── matcher.py          #   语义匹配（文件名语义主路 + 概念桥接）
│   ├── style.py            #   Style Contract
│   ├── narration.py        #   旁白合成（EdgeTTS 词级时间戳，零 key）
│   ├── pipeline.py         #   导演编排（compose_auto 全自动入口）
│   ├── renderer.py         #   FFmpeg 确定性执行
│   ├── doctor.py           #   环境自检（生成前能力闸门）
│   └── ...                 #   validator/qc/store/runlog/audio/prober
├── webui/App.py           # WebUI（零外部项目依赖）
├── run_webui.py           # 一键启动器（自动定位依赖、自动开浏览器）
├── tests/                 # pytest
└── docs/                  # 验收文档
```

## 参考项目的定位（零代码耦合）

只借**设计思想**，不复制代码、不作为运行时依赖：

- Short Video Factory：本地 FFmpeg / SQLite / BGM 工程经验（AGPL，只读）
- OpenMontage：Pipeline/Artifact/Checkpoint 生产操作系统思想（AGPL，只读）
- Hypit / video-use / VideoLingo：语义锚点 / EDL / 词级对齐思想
- MoneyPrinterTurbo：TTS provider 组织方式（仅参考）

## 环境变量（全部可选）

```
IMAGEIO_FFMPEG_EXE / FFPROBE_EXE    ffmpeg/ffprobe 二进制路径（不设则自动探测）
COMPOSER_VISION_API_KEY ...         视觉 LLM 增强（不设不影响使用）
HF_HOME                             模型缓存目录
```