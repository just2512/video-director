# AI Video Director

一句话文案 + 素材（可选）→ AI 自动导演、配音、配乐、成片。

```
用户意图 → 风格(AUTO/描述) → 素材理解 → 语义匹配 → Composition IR
       → Validator → Renderer(FFmpeg) → QC → final.mp4
```

## 独立定位（与所有参考项目的关系）

本系统是**独立的 AI 导演系统**，不是任何开源项目的分支或补丁：

| 我们独有 | 参考项目没有的 |
|---|---|
| Composition IR（AI 规划唯一契约） | — |
| 语义素材匹配（场景↔画面内容，Concept Bridge） | SVF 纯随机选段 |
| Style Contract 四风格结构化（AUTO 选择，无评分） | — |
| 叙事时间轴（Narration 为主轴，变速 0.8-1.7 双层策略） | — |
| VisionProvider 六级降级链（P1 语义→P5 手工→诚实 MISSING） | — |
| 确定性 QC 七项 + 修复循环（≤3 次） | — |
| 任务状态机（SQLite 跨会话历史，9 态可解释） | — |

参考项目（MoneyPrinterTurbo/Short Video Factory/Hypit/HyperFrames/
video-use/VideoLingo）只借设计思想，零代码耦合。许可证纪律：
SVF=AGPL-3.0 / Hypit=修改版 Apache-2.0，均只读不复制。

## 快速开始

```bash
# 环境（Python 3.11+，依赖见 requirements.txt）
pip install -r requirements.txt

# 方式一：WebUI（浏览器打开 http://localhost:8501）
python run_webui.py

# 方式二：CLI
python -m video_director auto \
    --script 文案.txt --videos 素材目录/ \
    [--semantic assets_semantic.json] [--style "历史悬疑感"] [--bgm bgm-ambient-001]
```

## 项目结构

```
video-director/
├── video_director/        # 核心引擎（20 模块）
│   ├── models.py           #   Composition IR 契约
│   ├── pipeline.py         #   编排（compose_auto 全自动入口）
│   ├── planner.py          #   RulePlanner + ContentMatchPlanner + LLMPlanner
│   ├── matcher.py          #   语义匹配（LLM 主路 + 概念桥接 fallback）
│   ├── vision.py           #   VisionProvider 六级链
│   ├── style.py            #   Style Contract 四风格
│   ├── narration.py        #   EdgeTTS 逐场景合成（词级时间戳）
│   ├── renderer.py         #   FFmpeg 渲染（逐层→concat→混音→字幕烧录）
│   ├── store.py            #   SQLite 任务状态机
│   └── ...                 #   validator/qc/audio/subtitle/prober/registry/assets
├── webui/                  # 独立 WebUI（不依赖任何外部项目页面）
├── tests/                  # pytest 测试
├── docs/                   # 验收文档
└── storage/                # 项目产物（composer.db + 各项目目录）
```

## 环境变量（可选）

```
IMAGEIO_FFMPEG_EXE / FFPROBE_EXE   ffmpeg/ffprobe 二进制路径
COMPOSER_VISION_API_KEY/BASE_URL/MODEL   视觉 LLM（激活自动素材理解）
HF_HOME                             模型缓存目录
```
