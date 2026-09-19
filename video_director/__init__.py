"""AI Video Director（独立项目）。

核心引擎 = Composition IR 流水线：
    用户意图 → 风格 → 素材理解（VisionProvider 六级链）→ 语义匹配 →
    Composition IR → Validator → Renderer → QC → final.mp4

Narration Timeline 是唯一主时间轴；LLM 只产出结构化 JSON，禁止直接生成
FFmpeg/Shell 命令；素材不足诚实 MISSING，绝不编造。
MoneyPrinterTurbo / Short Video Factory / Hypit / HyperFrames / video-use /
VideoLingo 仅作参考实现，本系统与其零代码耦合。
"""

COMPOSER_VERSION = "0.1.0"
TIMELINE_SCHEMA_VERSION = 1
