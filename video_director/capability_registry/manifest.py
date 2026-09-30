"""MPT 吸收方案 §5 Capability Manifest：系统能力的只读事实源。

登记范围（指纹级对齐方案原文）：
  - §57 AI Tool Registry 全表（system.* / project.* / timeline.* / layer.* /
    asset.* / stock.* / audio.original.* / tts.* / subtitle.* / music.* /
    watermark.* / generation.* / motion.* / style.* / template.* / shot.* /
    render.* / revision.* / version.* / qc.* / review.*）
  - §85-§94 能力组编号（CAP-AI / CAP-EDIT / CAP-ASSET / CAP-AUDIO /
    CAP-SUB / CAP-MUSIC / CAP-GEN / CAP-RENDER）

AI 通过 system.capabilities.list() 得到 Level-1 摘要（id 列表），
通过 system.capabilities.describe(id) 得到 Level-2 完整 Contract ——
不把整个系统塞给 LLM（§63 上下文分层）。
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List

from video_director.capability_registry.schemas import Capability

#: CAP-AI / CAP-EDIT / CAP-ASSET / ... 分组根目录（§85-§94）
CATEGORY = {
    "ai": "AI 控制中枢能力（读系统/规划/验证）",
    "edit": "编辑域能力（时间线/轨道/层/wfp 编辑）",
    "asset": "素材域能力（本地导入/目录/Stock/解析/溯源）",
    "audio": "音频域能力（配音/试听/原声/闪避）",
    "subtitle": "字幕域能力（生成/样式/逐词/动画）",
    "music": "音乐域能力（本地/试听/生成/匹配）",
    "generation": "生成域能力（图/视频/SFX/音乐/首尾帧/参考）",
    "render": "渲染域能力（草稿/终稿/1080/4K/预检/校验/恢复）",
    "revision": "修改域能力（提案/预览/应用/拒绝）",
    "qc": "质检域能力",
    # ---- 视觉语义归一新增域（§71 能力族）----
    "visual": "视觉语义能力（概念/分镜/连续性/摄影机/关键帧/蒙版/抠像/PiP/形变）",
    "graphics": "程序化图形能力（HTML/SVG/Canvas/Lottie/Chart/DataViz/3D/WebGPU）",
    "promo": "项目宣传片能力（Repo→Promo：发现/扫描/证据/分镜/渲染/验证）",
    "agent": "Agent 网关能力（MCP/CLI/上下文/Job流/工具发现）",
}


def _cap(
    cap_id: str,
    category: str,
    description: str,
    input_schema: str = None,
    outputs=(), preconditions=(),
    side_effect="composition", supports_preview=False,
    requires_confirmation=False, provider_required=False, examples=(),
    domain: str = "", kind: str = "",
) -> Capability:
    # §40：domain 缺省继承 category（域分类）；kind 缺省据 side_effect 推断——
    # 无副作用=只读查询(query)，有副作用=可执行动作(command)。
    dom = domain or category
    knd = kind or ("query" if side_effect == "" else "command")
    return Capability(
        id=cap_id, version="1.0.0", category=category, description=description,
        domain=dom, kind=knd,
        inputSchema=input_schema,
        outputArtifacts=frozenset(outputs),
        preconditions=frozenset(preconditions),
        sideEffects=frozenset({side_effect} if side_effect else ()),
        supportsPreview=supports_preview, supportsUndo=True,
        requiresConfirmation=requires_confirmation, providerRequired=provider_required,
        examples=frozenset(examples),
    )


def _build_manifest() -> List[Capability]:
    caps: List[Capability] = []

    # ---- CAP-AI（§85：第一批必须补齐的控制中枢能力）----
    caps.append(_cap("system.capabilities.list", "ai",
        "列出系统当前全部能力（Level-1 摘要：id 列表）",
        outputs=("CapabilityList",), side_effect="", supports_preview=False,
        examples=("系统有哪些能力", "能做什么")))
    caps.append(_cap("system.capabilities.describe", "ai",
        "读取单条能力的完整 Contract（Level-2 schema）", input_schema="DescribeRequest",
        outputs=("Capability",), side_effect="", examples=("描述保留原声能力",)))
    caps.append(_cap("system.context.inspect", "ai",
        "读取当前项目上下文快照（§6 ProjectContextService）", outputs=("ProjectContext",),
        side_effect="", examples=("看一下当前项目状态", "现在有什么轨")))
    caps.append(_cap("project.inspect", "ai",
        "读取项目详情（时长/画幅/分镜/产物）", outputs=("ProjectDetail",),
        side_effect="", examples=("这个项目多长", "当前画幅")))
    caps.append(_cap("project.create", "ai",
        "新建项目", input_schema="ProjectCreateRequest", outputs=("Project",),
        side_effect="composition", requires_confirmation=True,
        examples=("新建一个竖屏项目",)))
    caps.append(_cap("project.save", "ai",
        "保存项目（project.save 落盘）", outputs=("Project",), side_effect="composition",
        examples=("保存项目",)))

    # ---- CAP-EDIT（§85/§18/§19/§34：AI 最基本的"手"）----
    caps.append(_cap("timeline.query", "edit",
        "读取时间线结构（场景/层/长度/顺序，只读）", outputs=("TimelineSummary",),
        side_effect="", examples=("看看时间线", "现在有几个镜头")))
    caps.append(_cap("timeline.select", "edit",
        "选中场景/层（前端选择态，无 revision）", input_schema="TargetRequest",
        outputs=("Selection",), side_effect="", examples=("选中第一段",)))
    caps.append(_cap("timeline.trim", "edit",
        "调整视频层时长（源区间裁剪）", input_schema="TrimRequest",
        outputs=("Layer",), examples=("把这段剪到5秒", "剪短一点")))
    caps.append(_cap("timeline.move", "edit",
        "重排场景顺序", input_schema="MoveRequest", outputs=("Composition",),
        examples=("把第三段移到最前",)))
    caps.append(_cap("timeline.split", "edit",
        "在比例点分裂场景为两场景", input_schema="SplitRequest", outputs=("Composition",),
        examples=("在这里切成两段",)))
    caps.append(_cap("timeline.delete", "edit",
        "删除场景/层", input_schema="TargetRequest", outputs=("Composition",),
        requires_confirmation=True, examples=("删掉这段",)))
    caps.append(_cap("timeline.duplicate", "edit",
        "复制场景", input_schema="TargetRequest", outputs=("Composition",),
        examples=("复制这一段",)))
    caps.append(_cap("timeline.replace", "edit",
        "替换镜头配方（Shot Picker）", input_schema="RecipeReplaceRequest",
        outputs=("Composition",), examples=("换成产品近景",)))
    caps.append(_cap("layer.add", "edit",
        "新增层（B-roll/图片/生成层）", input_schema="LayerAddRequest", outputs=("Layer",),
        examples=("加一个 B-roll",)))
    caps.append(_cap("layer.update", "edit",
        "更新层（变换/时长/透明度/速度）", input_schema="LayerUpdateRequest",
        outputs=("Layer",), examples=("这段慢20%", "透明度调低")))
    caps.append(_cap("layer.remove", "edit",
        "删除层", input_schema="TargetRequest", outputs=("Composition",),
        requires_confirmation=True, examples=("删掉这个 B-roll",)))
    # §18 Original Audio 一等公民
    caps.append(_cap("audio.original.preserve", "audio",
        "保留视频素材原声（作为 OriginalAudio 轨）", input_schema="OriginalAudioPolicy",
        outputs=("AudioTrack",), provider_required=False,
        examples=("保留原声", "不要去掉现场声音", "留着原始声音")))
    caps.append(_cap("audio.original.mute", "audio",
        "静音素材原声", input_schema="OriginalAudioPolicy", outputs=("AudioTrack",),
        examples=("去掉原声", "不要现场声音")))
    caps.append(_cap("audio.original.volume", "audio",
        "调整素材原声音量", input_schema="VolumeRequest", outputs=("AudioTrack",),
        examples=("原声小一点", "现场声音拉高")))
    caps.append(_cap("audio.original.duck", "audio",
        "原声在旁白下自动闪避", input_schema="OriginalAudioPolicy", outputs=("AudioTrack",),
        examples=("原声给旁白让路",)))
    # §19 Watermark 一等公民
    caps.append(_cap("watermark.add", "edit",
        "添加水印（文本/图片/Logo）", input_schema="WatermarkRequest",
        outputs=("WatermarkLayer",), supports_preview=True,
        examples=("加一个右上角水印", "放一个 Logo")))
    caps.append(_cap("watermark.update", "edit",
        "更新水印（位置/缩放/透明度/时长）", input_schema="WatermarkRequest",
        outputs=("WatermarkLayer",), supports_preview=True,
        examples=("水印移到底部", "水印淡一点")))
    caps.append(_cap("watermark.remove", "edit",
        "移除水印", input_schema="TargetRequest", outputs=("Composition",),
        examples=("去掉水印",)))
    # §34/§35 Music volume/duck
    caps.append(_cap("music.volume", "music",
        "调整 BGM 音量", input_schema="VolumeRequest", outputs=("AudioTrack",),
        examples=("BGM 小一点", "背景音乐拉高")))
    caps.append(_cap("music.duck", "music",
        "BGM 在旁白下自动闪避", input_schema="VolumeRequest", outputs=("AudioTrack",),
        examples=("BGM 不要盖住原声",)))

    # ---- CAP-SUB（§31/§32/§88）----
    caps.append(_cap("subtitle.generate", "subtitle",
        "生成字幕（句级/逐词，从 TTS/ASR 对齐）", input_schema="SubtitleGenerateRequest",
        outputs=("SubtitleTrack",), provider_required=False,
        examples=("加字幕", "生成字幕")))
    caps.append(_cap("subtitle.update_style", "subtitle",
        "更新字幕样式（字/色/描边/底色/位置/动画）", input_schema="SubtitleStyleRequest",
        outputs=("SubtitleTrack",), supports_preview=True,
        examples=("字幕白字黑边", "字幕小一点放到中下方")))
    caps.append(_cap("subtitle.update_timing", "subtitle",
        "更新字幕逐词时间轴", input_schema="SubtitleTimingRequest",
        outputs=("SubtitleTrack",), examples=("字幕跟说话对齐",)))

    # ---- CAP-ASSET（§20-§27/§86）----
    caps.append(_cap("asset.import_local", "asset",
        "导入本地素材（视频/图片/音频）", input_schema="AssetImportRequest",
        outputs=("Asset",), examples=("导入这个视频",)))
    caps.append(_cap("asset.search", "asset",
        "按语义搜索项目/系统素材", input_schema="SearchRequest",
        outputs=("AssetList",), side_effect="", examples=("找咖啡的素材",)))
    caps.append(_cap("asset.resolve", "asset",
        "解析素材为候选（§21 Asset Resolver，四态零评分）", input_schema="AssetResolveRequest",
        outputs=("AssetCandidate",), side_effect="",
        examples=("这个镜头能用什么素材",)))
    caps.append(_cap("asset.add_to_timeline", "asset",
        "把素材加入时间线（§30 Timeline-native）", input_schema="AssetTimelineRequest",
        outputs=("Layer",), examples=("把这个放进去",)))
    caps.append(_cap("asset.replace", "asset",
        "替换层素材", input_schema="AssetReplaceRequest", outputs=("Layer",),
        examples=("把这个镜头换成那张图",)))
    caps.append(_cap("stock.search", "asset",
        "搜索 Stock 素材（Pexels/Pixabay/Coverr）", input_schema="StockSearchRequest",
        outputs=("StockAsset",), side_effect="", provider_required=True,
        examples=("找两个关于咖啡的 B-roll",)))
    caps.append(_cap("stock.preview", "asset",
        "预览 Stock 素材", input_schema="TargetRequest", outputs=("Preview",),
        side_effect="", provider_required=True, examples=("预览这个素材",)))
    caps.append(_cap("stock.import", "asset",
        "导入 Stock 素材到项目", input_schema="AssetImportRequest", outputs=("Asset",),
        provider_required=True, examples=("把这个素材导进来",)))

    # ---- CAP-AUDIO（§13-§18/§87）----
    caps.append(_cap("tts.list_voices", "audio",
        "列出可用配音音色", outputs=("VoiceList",), side_effect="",
        examples=("有哪些配音音色", "换个声音")))
    caps.append(_cap("tts.preview_voice", "audio",
        "试听单个音色（VoicePreviewArtifact）", input_schema="VoicePreviewRequest",
        outputs=("VoicePreviewArtifact",), supports_preview=True, provider_required=True,
        examples=("试听晓晓这个声音",)))
    caps.append(_cap("tts.preview_narration", "audio",
        "完整旁白试听（NarrationPreviewArtifact）", input_schema="VoicePreviewRequest",
        outputs=("NarrationPreviewArtifact",), supports_preview=True, provider_required=True,
        examples=("完整试听一下",)))
    caps.append(_cap("tts.generate", "audio",
        "生成正式旁白（NarrationArtifact，入 Composition）", input_schema="VoicePreviewRequest",
        outputs=("NarrationArtifact",), provider_required=True,
        examples=("生成配音",)))

    # ---- CAP-MUSIC（§34/§35/§89）----
    caps.append(_cap("music.search", "music",
        "搜索本地/Stock 音乐", input_schema="SearchRequest", outputs=("MusicList",),
        side_effect="", examples=("找一首轻快 BGM",)))
    caps.append(_cap("music.preview", "music",
        "试听音乐（MusicPreviewArtifact）", input_schema="TargetRequest",
        outputs=("MusicPreviewArtifact",), supports_preview=True, side_effect="",
        examples=("试听这首 BGM",)))
    caps.append(_cap("music.add", "music",
        "添加 BGM 轨", input_schema="MusicAddRequest", outputs=("AudioTrack",),
        examples=("加一首 BGM",)))
    caps.append(_cap("music.remove", "music",
        "移除 BGM", input_schema="TargetRequest", outputs=("Composition",),
        examples=("去掉 BGM",)))
    caps.append(_cap("music.generate", "music",
        "AI 生成配乐（§28/§29 Generation Provider 统一）", input_schema="GenerationRequest",
        outputs=("MusicArtifact",), provider_required=True,
        examples=("给我生成一段配乐",)))

    # ---- CAP-GEN（§28-§30/§90）----
    caps.append(_cap("generation.image", "generation",
        "生成图片素材", input_schema="GenerationRequest", outputs=("Asset",),
        supports_preview=True, provider_required=True, examples=("生成一张配图",)))
    caps.append(_cap("generation.video", "generation",
        "生成视频素材（Timeline-native §30）", input_schema="GenerationRequest",
        outputs=("Asset",), supports_preview=True, provider_required=True,
        examples=("把这一段生成 AI 视频",)))
    caps.append(_cap("generation.sfx", "generation",
        "生成音效", input_schema="GenerationRequest", outputs=("Asset",),
        supports_preview=True, provider_required=True, examples=("生成一个 whoosh 音效",)))
    caps.append(_cap("generation.music", "generation",
        "生成音乐", input_schema="GenerationRequest", outputs=("Asset",),
        supports_preview=True, provider_required=True, examples=("生成配乐",)))

    # ---- motion / style / template / shot（§54/§57）----
    caps.append(_cap("motion.list", "edit",
        "列出动效配方", outputs=("MotionList",), side_effect="",
        examples=("有哪些动效",)))
    caps.append(_cap("motion.preview", "edit",
        "预览动效", input_schema="TargetRequest", outputs=("Preview",),
        side_effect="", examples=("预览这个动效",)))
    caps.append(_cap("motion.apply", "edit",
        "应用动效到层", input_schema="MotionApplyRequest", outputs=("Layer",),
        examples=("给这段加淡入",)))
    caps.append(_cap("style.apply", "edit",
        "应用项目风格", input_schema="StyleRequest", outputs=("Composition",),
        examples=("换一个复古风格",)))
    caps.append(_cap("template.instantiate", "edit",
        "实例化模板", input_schema="TemplateRequest", outputs=("Composition",),
        requires_confirmation=True, examples=("用产品宣传模板",)))
    caps.append(_cap("shot.resolve", "edit",
        "解析镜头配方候选", input_schema="ShotResolveRequest", outputs=("ShotCandidate",),
        side_effect="", examples=("这个场景适合什么镜头",)))
    caps.append(_cap("shot.apply", "edit",
        "应用镜头配方", input_schema="ShotApplyRequest", outputs=("Composition",),
        examples=("套用这个镜头",)))

    # ---- CAP-RENDER（§43/§44/§91）----
    caps.append(_cap("render.preflight", "render",
        "渲染前预检（商用/源分辨率/能力）", outputs=("PreflightReport",),
        side_effect="", examples=("检查能不能导出",)))
    caps.append(_cap("render.draft", "render",
        "渲染草稿（低分辨率快速审片）", input_schema="RenderRequest",
        outputs=("RenderResult",), side_effect="render", supports_preview=True,
        examples=("生成草稿", "先渲染一版看看")))
    caps.append(_cap("render.final", "render",
        "渲染终稿", input_schema="RenderRequest", outputs=("RenderResult",),
        side_effect="render", requires_confirmation=True, examples=("导出成品",)))

    # ---- revision（§9/§49：AI 只提案不直接改 Composition）----
    caps.append(_cap("revision.propose", "revision",
        "AI 生成结构化修改提案（不落盘）", input_schema="RevisionProposeRequest",
        outputs=("Proposal",), side_effect="", examples=("把第三段换成产品近景",)))
    caps.append(_cap("revision.preview", "revision",
        "预览提案效果（干跑，不落盘）", input_schema="RevisionIdRequest",
        outputs=("Preview",), side_effect=""))
    caps.append(_cap("revision.apply", "revision",
        "应用提案（走 Command Bus 正式 Revision）", input_schema="RevisionIdRequest",
        outputs=("Composition",), side_effect="composition"))
    caps.append(_cap("revision.reject", "revision",
        "拒绝提案（零变更）", input_schema="RevisionIdRequest", outputs=("Status",),
        side_effect=""))

    # ---- version / qc / review（§38/§59）----
    caps.append(_cap("version.create", "ai",
        "创建项目版本（Variant §37）", outputs=("Variant",), side_effect="composition",
        examples=("保存为版本V2",)))
    caps.append(_cap("project.reopen", "ai",
        "重开项目/版本", input_schema="ProjectReopenRequest", outputs=("Project",),
        side_effect="", examples=("恢复上次的版本",)))
    caps.append(_cap("qc.inspect", "qc",
        "质检检查（确定性/音频/视觉）", input_schema="TargetRequest",
        outputs=("QCReport",), side_effect="", examples=("检查一下这条视频",)))
    caps.append(_cap("review.create", "qc",
        "创建审核意见/Issue（§39 Review Issues）", input_schema="ReviewRequest",
        outputs=("ReviewIssue",), side_effect="", examples=("这一段字幕太小",)))

    # ---- CAP-VERIFY（§59 Verify Tools 硬清单）----
    caps.append(_cap("timeline.verify", "verify",
        "验证时间线副作用（IR 读回断言）", input_schema="TargetRequest",
        outputs=("VerifyReport",), side_effect="", examples=("检查时间线改对了吗",)))
    caps.append(_cap("audio.verify", "verify",
        "验证音轨副作用（原声/BGM/字幕轨 IR 读回）", input_schema="TargetRequest",
        outputs=("VerifyReport",), side_effect="", examples=("原声真的保留了吗",)))
    caps.append(_cap("asset.verify", "verify",
        "验证素材替换/导入副作用", input_schema="TargetRequest",
        outputs=("VerifyReport",), side_effect="", examples=("素材换上了吗",)))
    caps.append(_cap("render.verify", "verify",
        "验证渲染产物存在与规格", input_schema="TargetRequest",
        outputs=("VerifyReport",), side_effect="", examples=("draft 渲染成功了吗",)))
    caps.append(_cap("composition.verify", "verify",
        "验证 Composition IR 整体状态", input_schema="TargetRequest",
        outputs=("VerifyReport",), side_effect="", examples=("整体检查一遍",)))

    # ---- CAP-MUSIC 补齐（§35 七 AI Tool 全表）----
    caps.append(_cap("music.replace", "music",
        "替换 BGM（§35）", input_schema="MusicAddRequest", outputs=("AudioTrack",),
        examples=("换一首 BGM",)))
    caps.append(_cap("music.disable", "music",
        "关闭 BGM（§35 music.disable；volume=0 非删轨）", input_schema="TargetRequest",
        outputs=("AudioTrack",), examples=("不要背景音乐了")))
    caps.append(_cap("music.match_video", "music",
        "按参考视频风格匹配 BGM（§35/§68；匹配通道未开时诚实不可用）",
        input_schema="TargetRequest", outputs=("AudioTrack",), provider_required=True,
        examples=("配乐风格参考这条视频",)))

    # ---- 视觉语义归一 CAP-VIS-001~009（§71：Concept/Storyboard/Continuity/
    #      Camera/Keyframe/Mask/Matting/PiP/Morph）----
    caps.append(_cap("visual.concept", "visual",
        "提出多个视觉概念供选择（Concept-first §八：一个元素贯穿/一镜到底/前后对比...）",
        outputs=("ConceptList",), side_effect="", examples=("给我3个视觉概念",)))
    caps.append(_cap("visual.storyboard", "visual",
        "分镜编排（含 Before/Action/Carry/After 前后状态 §三十）",
        outputs=("Storyboard",), examples=("做一份分镜",)))
    caps.append(_cap("visual.continuity.plan", "visual",
        "连续镜头编排（Continuity Planner §29：Carry/相机过渡/空间关系/Morph 候选）",
        outputs=("ContinuityPlan",), examples=("让镜头之间有延续",)))
    caps.append(_cap("visual.continuity.verify", "visual",
        "连续性检查（PPT Detector §三十三：无 Carry/无相机延续 → CONTINUITY_WARNING）",
        outputs=("ContinuityReport",), side_effect="", examples=("检查是不是PPT式",)))
    caps.append(_cap("camera.apply", "visual",
        "应用摄影机运动（推/拉/横移/俯视/环绕/穿越/跟拍 §十一）",
        input_schema="CameraApplyRequest", outputs=("CameraTrack",), examples=("镜头推进",)))
    caps.append(_cap("keyframe.add", "visual",
        "在当前时间创建关键帧（§24 Inspector 小圆点）",
        input_schema="KeyframeAddRequest", outputs=("KeyframeTrack",), examples=("在这里打个关键帧",)))
    caps.append(_cap("keyframe.remove", "visual",
        "删除关键帧", input_schema="KeyframeTargetRequest", outputs=("KeyframeTrack",),
        examples=("去掉这个关键帧",)))
    caps.append(_cap("mask.apply", "visual",
        "应用几何蒙版（rect/rounded/ellipse/polygon/path + 羽化/反转/关键帧 §63）",
        input_schema="MaskApplyRequest", outputs=("Mask",), examples=("给这个层加个圆形蒙版",)))
    caps.append(_cap("matting.generate", "visual",
        "抠像生成 Matte（segmentation/chroma provider → MaskArtifact §六十四；无 provider 诚实 UNAVAILABLE）",
        input_schema="MattingRequest", outputs=("MaskArtifact",), provider_required=True,
        examples=("把人物抠出来",)))
    caps.append(_cap("pip.configure", "visual",
        "配置画中画层（video/image/screen/product/camera + 圆角/阴影/蒙版/裁剪 §二十二）",
        input_schema="PipConfigRequest", outputs=("Layer",), examples=("加一个小窗录屏",)))
    caps.append(_cap("visual.morph", "visual",
        "UI→Graphic Morph（按钮→箭头→流程图节点，程序化形变 §四十二）",
        input_schema="MorphRequest", outputs=("MorphPlan",), examples=("把这个按钮变成流程图节点",)))

    # ---- 视觉语义归一 CAP-SUB-006~011（§26 字幕能力族补全）----
    caps.append(_cap("subtitle.word_token", "subtitle",
        "逐词时间轴（Word Token；模式 word 的逐词高亮基础）", input_schema="TargetRequest",
        outputs=("SubtitleTrack",), examples=("逐词高亮",)))
    caps.append(_cap("subtitle.highlight", "subtitle",
        "关键词高亮/变色/放大（§26 Highlight）", input_schema="HighlightRequest",
        outputs=("SubtitleTrack",), examples=("关键词变黄色",)))
    caps.append(_cap("subtitle.karaoke", "subtitle",
        "卡拉OK式逐词填充（§26 Karaoke）", input_schema="TargetRequest",
        outputs=("SubtitleTrack",), examples=("做卡拉OK字幕",)))
    caps.append(_cap("subtitle.kinetic", "subtitle",
        "动力学字幕（逐字入场/放大/重排 §26 Animation）", input_schema="KineticRequest",
        outputs=("SubtitleTrack",), examples=("字幕逐字蹦出来",)))
    caps.append(_cap("subtitle.layout", "subtitle",
        "字幕布局（断句/换行/避让/位置跟踪 §26 Layout）", input_schema="LayoutRequest",
        outputs=("SubtitleTrack",), examples=("字幕不要挡住人脸",)))
    caps.append(_cap("subtitle.safe_zone", "subtitle",
        "字幕安全区（平台安全边距 §26 Safe Zone）", input_schema="SafeZoneRequest",
        outputs=("SubtitleTrack",), examples=("按抖音安全区摆放",)))

    # ---- 视觉语义归一 CAP-GFX-001~008（§45：GFX 按能力拆分注册，不再一揽子）----
    caps.append(_cap("gfx.html", "graphics",
        "HTML 图形（DOM 组件，Graphics Runtime 渲染）", input_schema="GfxRequest",
        outputs=("Graphic",), examples=("做一个HTML卡片",)))
    caps.append(_cap("gfx.svg", "graphics",
        "SVG 矢量图形", input_schema="GfxRequest", outputs=("Graphic",),
        examples=("画个SVG图标",)))
    caps.append(_cap("gfx.canvas", "graphics",
        "Canvas 位图动画图形", input_schema="GfxRequest", outputs=("Graphic",),
        examples=("用canvas画粒子",)))
    caps.append(_cap("gfx.lottie", "graphics",
        "Lottie 动画（可控动画资源 §四十五；无 renderer 诚实 UNAVAILABLE）",
        input_schema="GfxRequest", outputs=("Graphic",), provider_required=True,
        examples=("放一个Lottie动画",)))
    caps.append(_cap("gfx.chart", "graphics",
        "图表图形（柱/线/饼，结构化数据→视觉）", input_schema="GfxRequest",
        outputs=("Graphic",), examples=("画个增长曲线图",)))
    caps.append(_cap("gfx.data_viz", "graphics",
        "数据可视化（Workflow/Comparison/Hierarchy/Timeline 等扩展族 §四十四）",
        input_schema="GfxRequest", outputs=("Graphic",), examples=("做个流程图",)))
    caps.append(_cap("gfx.three", "graphics",
        "Three.js 3D 图形（§45 3D Graphics Provider；未接通诚实 UNAVAILABLE）",
        input_schema="GfxRequest", outputs=("Graphic",), provider_required=True,
        examples=("加个3D元素",)))
    caps.append(_cap("gfx.webgpu", "graphics",
        "WebGPU 实验图形（§45 Experimental Provider；未接通诚实 UNAVAILABLE）",
        input_schema="GfxRequest", outputs=("Graphic",), provider_required=True,
        examples=("试试WebGPU效果",)))

    # ---- 视觉语义归一 CAP-PROMO-001~011（§34：Repo → Promo 全链）----
    caps.append(_cap("promo.repo_discovery", "promo",
        "发现开发项目（WorkspaceRoot 下 .git/package.json 等指纹 §三十五；不扫全盘）",
        input_schema="PathRequest", outputs=("RepoList",), side_effect="",
        examples=("找到我电脑里的项目",)))
    caps.append(_cap("promo.project_scan", "promo",
        "项目扫描（README/CHANGELOG/routes/components/design tokens/git history §三十六；排除 .env/keys）",
        input_schema="PathRequest", outputs=("ProjectSnapshot",), side_effect="",
        examples=("扫描这个项目",)))
    caps.append(_cap("promo.feature_discovery", "promo",
        "功能发现（Claim→Source→Runtime→Evidence，只认 VERIFIED §三十八）",
        input_schema="PathRequest", outputs=("FeatureClaimList",), side_effect="",
        examples=("这个软件有哪些功能值得宣传",)))
    caps.append(_cap("promo.runtime_launch", "promo",
        "启动项目运行时（Playwright 打开真实 UI §五）", input_schema="RuntimeRequest",
        outputs=("RuntimeStatus",), side_effect="runtime", requires_confirmation=True,
        examples=("把项目跑起来",)))
    caps.append(_cap("promo.component_trace", "promo",
        "组件溯源（功能→真实组件/route/state §2 Evidence Graph）",
        input_schema="TraceRequest", outputs=("EvidenceRefList",), side_effect="",
        examples=("这个功能对应哪个组件",)))
    caps.append(_cap("promo.evidence_graph", "promo",
        "证据图（镜头↔代码/运行时/截图证据追溯 §3 ProjectEvidenceGraph）",
        input_schema="PathRequest", outputs=("ProjectEvidenceGraph",), side_effect="",
        examples=("给宣传片建证据链",)))
    caps.append(_cap("promo.product_brief", "promo",
        "产品简报（从 Evidence 提炼卖点，禁编造 §六）", input_schema="TargetRequest",
        outputs=("ProductBrief",), side_effect="", examples=("写产品简报",)))
    caps.append(_cap("promo.concept", "promo",
        "宣传创意（3 个 Concept 供选择 §六十六）", input_schema="TargetRequest",
        outputs=("ConceptList",), side_effect="", examples=("给我3个宣传创意",)))
    caps.append(_cap("promo.storyboard", "promo",
        "宣传片分镜（连续镜头：Carry/Camera 延续 §六十七）", input_schema="TargetRequest",
        outputs=("Storyboard",), examples=("生成宣传片分镜",)))
    caps.append(_cap("promo.render", "promo",
        "渲染宣传片（代码驱动：真实组件+GSAP+FFmpeg，不用 AI Video §四十）",
        input_schema="TargetRequest", outputs=("RenderResult",), side_effect="render",
        requires_confirmation=True, examples=("渲染宣传片",)))
    caps.append(_cap("promo.verify", "promo",
        "宣传片验证（Evidence 核对 + Continuity QC §74 E2E）", input_schema="TargetRequest",
        outputs=("PromoVerifyReport",), side_effect="", examples=("验证宣传片内容属实",)))

    # ---- 视觉语义归一 CAP-AGENT-001~005（§四十九：Agent Gateway）----
    caps.append(_cap("agent.mcp", "agent",
        "MCP 网关（creative.inspect/plan/execute/preview/render/verify/watch 七工具；"
        "已接通 agent_gateway.mcp_server §51/AG-001）",
        outputs=("AgentGateway",), side_effect="", examples=("接 MCP",)))
    caps.append(_cap("agent.cli", "agent",
        "CLI 网关（creative-os inspect/plan/execute/preview/render/verify/watch；"
        "已接通 agent_gateway.cli §52/AG-002）",
        outputs=("AgentGateway",), side_effect="", examples=("用命令行操作",)))
    caps.append(_cap("agent.context", "agent",
        "Agent 上下文（Project Context 快照给 Agent §53；agent_gateway.context）",
        outputs=("ProjectContext",), side_effect="", examples=("给Agent看项目上下文",)))
    caps.append(_cap("agent.job_watch", "agent",
        "Job 事件流（SSE job.watch §四十八；未订阅诚实 UNKNOWN）",
        outputs=("JobEvent",), side_effect="", examples=("盯这个任务",)))
    caps.append(_cap("agent.tool_discovery", "agent",
        "工具发现（Capability Contract → MCP tools/list 派生 §五十）",
        outputs=("ToolList",), side_effect="", examples=("Agent能调什么工具",)))

    return caps


#: Manifest 惰性单例（只读事实源；-1 不会在导入期构建，避免循环导入）
_MANIFEST: Dict[str, Capability] | None = None


def manifest() -> Dict[str, Capability]:
    """返回 id → Capability 的完整清单（Level-1/Level-2 共用）。"""
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = {c.id: c for c in _build_manifest()}
    return _MANIFEST


def list_capabilities() -> List[dict]:
    """§63 Level-1 摘要：id 列表（供 AI 上下文，避免塞满 schema）。"""
    return sorted(manifest().keys())


def describe_capability(cap_id: str) -> dict | None:
    """§63 Level-2：单条完整 Contract。"""
    c = manifest().get(cap_id)
    return c.to_dict() if c else None


def capability_ids_by_category(category: str) -> List[str]:
    """按能力组（CAP-AI/CAP-EDIT/...）返回 id 列表。"""
    return sorted(c.id for c in manifest().values() if c.category == category)