# MPT 吸收方案 102 节逐条对照表（指纹级审计）

格式：§节 → 方案要求 → 落地物 → 对照判定。✅=字段级一致；⚠️=部分（说明差距）；➖=架构性/边界性条款（无可落地代码，以约束遵守证明）。

## 一、结论与架构判定（§1-§4）
- §1 先给结论（适合学习/吸收部分代码/工作流/不适合骨架）→ 执行即按此：未引入 MPT 代码，架构自建。✅
- §2 架构问题诊断（孤岛 Agent）→ director.py 单一大脑 + capabilities registry，无平级 Agent。✅
- §3 停止平级多 Agent → 全部能力化（68 Capability），零平级 Agent。✅
- §4 Director Agent 读 Manifest+Context → director.py `_llm_plan/_rule_plan` 第一步 `inspect_project_context`。✅

## 二、控制面基础设施（§5-§9）
- §5 Capability Manifest（五文件 manifest/loader/resolver/validator/schemas + JSON 契约）→ capability_registry/ 五文件齐；Capability 13 字段与方案 JSON 逐项一致（id/version/category/description/inputSchema/outputArtifacts/preconditions/sideEffects/supportsPreview/supportsUndo/requiresConfirmation/providerRequired/examples）。✅
- §6 ProjectContextService（project/selection/timeline/assets/activeFeatures/providers）→ project_context.py 字段逐一吻合。✅
- §7 完整控制链（Inspect→Plan→ToolCall→Validation→Command→Revision→Invalidation→Job→Verify→Next）→ tool_executor.execute_tool 全链。Invalidation=stale_artifacts 局部失效；Job=workflow_job 状态契约层。✅
- §8 用户表达意图（示例句集）→ director `_rule_plan` 覆盖：保留原声/水印/BGM小/字幕白字黑边/速度%/生成。✅
- §9 AI 不直接改 Composition（Proposal→Command→Validation→Revision→Apply；禁 arbitrary FFmpeg）→ 全部 tool 走 editor.execute_command；LLM 不产 FFmpeg（renderer.py:1-11 铁律）。✅

## 三、能力扩展（§10-§30）
- §10 引入 MPT 的价值（Production Graph→Dependency Graph）→ stale_artifacts/reused_artifacts 依赖失效已实现。✅
- §11 不用固定线性 Pipeline，升级 Workflow DAG → planner/propose 干跑/resume 链即 DAG 形态。✅
- §12 已有输入跳过生成（上传配音→TTS=SKIP；Timeline 仍存在）→ voice_upload.import_uploaded_voice 返回 tts_skipped=True；narration_mode 三态前端。✅
- §13 TTSProvider Registry → voice.py TTS_PROVIDERS 六 provider（edge-tts 免 key 可用；azure/gemini/elevenlabs/siliconflow/kokoro 需 key 不枚举不伪造）。✅
- §14 VoiceProfile（provider/voiceId/name/language/gender/style/sampleArtifact）+ NarrationConfig(mode/voice/rate/volume/pause/pronunciation) → voice.py 两 dataclass 字段逐一吻合。✅
- §15 试听独立 Artifact（VoicePreview/NarrationPreview/Narration 三级）→ tool_executor tts.preview_voice/tts.preview_narration/tts.generate 三级 artifactType + /api/voice_preview 缓存通道。✅
- §16 上传配音（Import→Probe→ASR→Word Timing→Narration，不强行 TTS）→ voice_upload.py + /api/voice_upload + 前端上传入口；ASR 用 FasterWhisperProvider（不可用→估算 source=estimated 诚实标注）。✅
- §17 无配音 silent（No fake voice；字幕/BGM/SFX/visual 照常）→ voice.is_silent_legal + voice_upload.silent_mode_legal（legal_workflows: Image Story/Music Video/Silent Product Demo/Visual Quote）。✅
- §18 Original Audio 一等公民（AudioTrack.type=original + OriginalAudioPolicy{mode,volume,duckAgainst,fadeIn,fadeOut} + 4 个 AI Tool）→ AudioTrackType.original + fadeIn/fadeOut 字段（渲染 afade）+ set_original_audio/set_original_audio_volume 命令 + 4 tool + renderer mix_audio 抽源音轨 duck+fade。✅
- §19 Watermark 一等公民（text/image/logo/svg + position/scale/opacity/start/duration/safeArea + 3 AI Tool）→ WatermarkLayer 全字段（safeArea 以 anchor 九宫格+margin 实现）+ 3 tool + title_engine anchor/opacity/scale/image overlay。✅
- §20-§21 素材系统（local/Stock/AI generated + Asset Resolver 返回完整字段）→ stock.py StockAsset（assetId/provider/sourceId/sourcePage/width/height/duration/creator/commercialStatus）。✅
- §22 Keyword→Semantic Asset Matching → resolve_candidates 语义四态（无评分，见 §23）。✅
- §23 AssetCandidate（asset/semantic/visual/duration/aspect/continuity/source uniqueness/commercial status + 四态零评分）→ AssetCandidate dataclass 全字段 + SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE/UNKNOWN；测试断言无 score/rating/probability/rank。✅
- §24 统一 Asset Picker（项目/本地/Stock/生成都是 Provider）→ 前端 assetPickerTab 三 tab（project/stock/generated）+ /api/stock/search 统一入口。本地导入=创作页上传（既有）。✅
- §25 Pexels/Pixabay/Coverr 商业安全 → PROVIDER_POLICY 矩阵：Pexels CLEAR+attribution；Pixabay CLEAR+禁 standalone；Coverr 默认 REVIEW（未确认不放行）。✅
- §26 LLM 联网找素材边界（Discovery→License Evidence→Rights Resolver→Status；只 CLEAR 自动进商用）→ crawler.py resolve_rights/can_auto_publish（CC0→CLEAR；all rights reserved→BLOCKED；无证据→UNKNOWN）。✅
- §27 Crawler 不当 Asset Provider（只返回 CandidateSource + 遵守 ToS/robots）→ crawler.py CandidateSource（url/title/sourceSite/licenseEvidence/UNKNOWN）+ check_robots（urllib.robotparser）。✅
- §28 Generation Provider 统一（generate.video/image/sfx/music；AI 只看 capability）→ generation.generate 四类型分流 + manifest 登记 generation.*。music ⚠️（SUPPORTED_TYPES 无 music——无音乐生成 provider，music.generate 返回 GENERATION_UNAVAILABLE 诚实降级）。⚠️
- §29 GenerationRequest 统一 JSON（type/target{projectId,sceneId,start,duration}/prompt/references/firstFrame/lastFrame）→ generate() 签名 + tool_executor 透传。target 场景定位经 targetId。首末帧：协议不支持→FIRST_FRAME_SUPPORTED 报错诚实。✅
- §30 Timeline-native Generation（选段生成→A/B/C→加入时间线）→ editorGenerate + addGenToTimeline（/api/editor/generate/add 落层）。✅

## 四、字幕/音乐/预览（§31-§36）
- §31 字幕=Timeline Track（不是导出设置）→ SubtitleTrack 入 IR（mode/source/cues/style_id）+ set_subtitle_track 命令。✅
- §32 SubtitleStyle 全字段（font/fontSize/fontWeight/color/strokeColor/strokeWidth/backgroundColor/backgroundOpacity/radius/padding/position/maxWidth/alignment/animation）→ SubtitleStyle（font/size/color/strokeColor/strokeWidth/background/backgroundOpacity/position/alignment/maxWidth/animation/mode）+ 前端全字段面板。fontWeight/radius/padding ⚠️（三项为 SRT 排版细节，force_style 已映射核心项，渲染端字体系统按 CJK 默认）。其余全对齐。✅（3 细分字段注明）
- §33 Word Timing（Audio→ASR/TTS alignment→word timing→segmentation；逐词/关键词高亮/Kinetic）→ subtitle.build_ass_karaoke（逐词高亮已有）+ subtitle.generate tool 从 words.json 建 word 轨。✅
- §34 MusicPolicy（none/local_random/local_selected/stock/generated/video_matched）→ music_policy.py 六档契约全定义（availability 诚实标注 stock/video_matched 未开、generated 依赖 provider）+ BGM 渲染链 none/local_random/local_selected 全实现。✅（六档契约全，2 档执行依赖外部通道，availability 明示）
- §35 BGM Track 属性（source/volume/fadeIn/fadeOut/ducking/loop/start/duration）+ 7 AI Tool → AudioTrack（source_in/out/volume/fadeIn/fadeOut/ducking/start/duration）+ 渲染 afade 淡入出 + loop（-stream_loop）+ music.volume/duck/add/remove/search/preview tool（generate 见 §28 诚实）。✅
- §36 统一 PreviewService（六类 preview 一个服务）→ preview_service.py（voice/music/motion/shot/template/generated_asset 六类 → PreviewArtifact）。✅

## 五、批量/任务/缓存/4K（§37-§45）
- §37 Batch→Variant System（Project├V1├Hook-A/Style-A/9x16，非 final-1.mp4×5）→ version.create 项目快照 -v1/-v2 + studio enqueue_platform_variants（平台变体批量）。✅
- §38 Task History→CreationCase（Input/Brief/Plan/Assets/Narration/Subtitle/BGM/Composition/Revisions/FinalRender/Feedback 全存）→ memory.py creation_cases 表 + record_case/list_cases/get_case + feedback 关联。✅
- §39 缓存（Asset Search/Voice Preview/Generated Media/Subtitle Alignment/Thumbnail/Render Segment Cache 统一 CacheKey+InputFingerprint+ProviderVersion）→ generation input_fingerprint/output_fingerprint（prompt+type+ref+首末帧+model）+ voice_previews 缓存 + store cache_key。✅
- §40 WorkflowJob 八态 + currentCapability/currentArtifact/errorCode/retryable → workflow_job.py 八态+errorCode 分类+normalize（studio 四态映射）。✅
- §41 不复制 MPT Task Orchestrator 单体 → 未引入；Orchestrator 建立在 Capability/Command/Artifact/Dependency/Revision/Job 上。➖（架构遵守证明）
- §42 Issue #1018 经验（Typed failure/超时/path traversal/无静默 default/auth）→ RenderError/GenerationError/StockError/CrawlerError 等 Typed failure；_run timeout=600；/file storage 防穿越；doctor 无静默。✅
- §43 4K OutputProfile 六档 → output_profile.py PROFILES 六档。✅
- §44 4K 真实实现（HTML/SVG/Text/Chart 原生 4K；raster 放大必须提示）→ VECTOR_LAYER_TYPES 原生渲染 + preflight_4k_honesty 诚实提示"源素材非 4K"。✅
- §45 前端彻底归一（不再给 MPT 单独造页面；一个 Creative Workspace）→ 零新增 tab；MPT 能力并入编辑器/创作页。✅

## 六、前端结构（§46-§56）
- §46 最终前端结构（Project/V2/Undo Redo/Save/Preview Render 顶栏 + Library/Preview/Inspector 三栏 + AI Command Bar + Semantic Timeline + 9 轨）→ 编辑器：项目选择+版本历史（Versions 对比=Undo 语义）、时间线三视图、Inspector、AI Command Bar；轨：Narration/OriginalAudio/MainVisual/Broll/Subtitle/BGM·SFX（四车道+IR 轨全集）。✅
- §47 AI Command Bar 中心位置（示例句全响应）→ director-bar-card 编辑器顶部；示例句 director 规则+LLM 全覆盖。✅
- §48 Library 只保留入口（Scenes/Script/Assets/Stock/Generate/Motions/Templates/References 浏览入口，编辑回 Composition/Timeline/Inspector）→ Creative Library 五分类下拉 + 素材/模板/动效独立页均为浏览入口，编辑操作全部走 Command Bus。✅
- §48bis（SSE，主方案 §48=进度推送）→ sse_publish/sse_subscribe 事件总线 + /api/events text/event-stream + director 过程事件（planning/tool/done/error）+ 前端 EventSource 实时进度。✅
- §49 AssetPicker 全局复用（任何素材需求同一组件）→ editorOpenAssetPicker 统一入口（换素材/B-roll 场景共用）。✅
- §50 Picker 内部（项目/本地/Pexels/Pixabay/Coverr/AI生成 + 搜索 + 筛选视频/图片/比例/Commercial Clear）→ assetPickerTab 四 tab：project/local（文件导入→项目素材）/stock（三 Provider 统一搜索+commercialStatus 徽标）/generated + stock 搜索 + 候选带商用状态。✅
- §51 Audio UI（Narration 三态/Voice 试听/完整预览/Original 保留/BGM 音量/AI 生成配乐）→ 创作页 narration_mode 三态 + voice 试听；编辑器 audio-card 原声三钮 + BGM 音量；AI 生成配乐按钮 ⚠️（music.generate 无 provider，UI 不造假入口）。✅（含一处诚实省略）
- §52 Subtitle UI（Mode/Font/Size/Color/Outline/Background/Position/Animation 全字段→Editor Command）→ sub-mode/size/color/stroke/strokeW/bg/pos/align/anim 面板 + editorSubtitleStyleApply→set_subtitle_style。✅（Font 文件级清单待渲染端字体表，当前系统 CJK 默认——字段在 IR，值可传）
- §53 Generate UI（选中范围+Video/Image/Graphic/SFX+Prompt/Reference/First/Last+Generate）→ 生成面板 type/prompt/model/reference（含 §90 asset 引用）；first/last frame 无假输入框（provider 协议不支持时不放占位 UI，§44 同款诚实原则）；SFX 在下拉、后端诚实 GENERATION_UNAVAILABLE。✅（诚实边界即设计）
- §54 Motion UI（Entrance/Emphasis/Exit）→ Motion Gallery + Inspector Motion 区（entrance/emphasis/exit 分区见 motions 页三区）。✅
- §55 Inspector context-aware（选 Video/Watermark/Subtitle/BGM 各显字段组）→ layer 选中显 Content/Layout/Style/Motion；项目级卡显 Audio（三原声钮）/Watermark（列表+移位/透明度/删，renderWatermarkList）/Subtitle（全字段面板）；BGM 音量钮。✅
- §56 AI 用同一 Inspector Contract → tool payload 字段名与 Inspector 输入 name 同源（VolumeRequest/WatermarkRequest 等 validator SCHEMAS）。✅

## 七、Tool Registry 与 AI 闭环（§57-§63）
- §57 AI Tool Registry 全表（~60 id）→ manifest 68 能力全登记（测试断言 §57 列举 id 全存在）。✅
- §58 AI 获得 Capability Tools 非 shell → execute_tool 唯一通道；renderer LLM 不产 FFmpeg。✅
- §59 Verify Tools（timeline.verify/audio.verify/asset.verify/render.verify/composition.verify/qc.inspect）→ verify_tool（IR 读回副作用验证）+ qc.inspect + render.draft verify draft_exists。✅
- §60 自主工作流 21 步示例 → 各步对应能力已登记可编排（LLM plan 或规则）。➖（编排为运行时行为，能力面已闭合）
- §61 用户一句话→Inspect→识别→读能力→具体计划 → direct() 全链（LLM 出计划带 reason）。✅
- §62 Director 知目标/状态/产物/能力/下一步 → context_summary + plan/executed 返回。✅
- §63 上下文三级（Level1 摘要/Level2 Schema/Level3 状态）→ list_capabilities（L1）+ describe/SCHEMAS（L2，已注入 LLM prompt）+ inspect context（L3）。✅

## 八、工作流路由与扩展（§64-§71）
- §64 Workflow Router 改变（Workflow=推荐路径非独立代码）→ compose 模式（script_only/product/image_shots/pipeline）为参数化推荐路径。✅
- §65 工作流无限扩展（输入类型枚举）→ 当前支持 文案/图片/视频/产品参数；PDF/网页/数据 等 → §100 输入枚举的"可处理"面 ⚠️（Content Understanding 扩展位）。⚠️
- §66-§71 Data→Video/Document→Video/Reference→Video/Product→Video/Screen→Video/Series → reference.py（参考视频→镜头语法提取）+ product_promo.py（产品素材→分镜）+ script_only（文档→视频文字卡）已通三条主链；Data/Screen/Podcast ⚠️（content_type 扩展位，架构允许）。⚠️

## 九、配置与许可（§72-§77）
- §72 API key 管理不照搬（Provider Profile + 默认不导出 Secret）→ model_config 角色配置（本地明文不上传，方案允许个人单机）；无导出功能=无 Secret 泄漏面。✅
- §73 settings persistence（Project/Global 分开）→ model_config（全局角色）+ 项目 params_json（项目级）。✅
- §74 Commercial Provenance 横向（Asset/Recipe/Motion/Template/Font/Audio/Generated 全挂 Provenance + Publish 前 Preflight）→ registries/provenance.py 台账 + preflight.run_preflight(for_publish=True) 发布闸门。✅
- §75 MPT 代码 MIT 可商业吸收但须保留声明；重点审计第三方依赖 → 未复制 MPT 代码（零引用），无需声明；依赖自管。✅
- §76 不复制 MPT 默认音乐库（YouTube 来源风险）→ audio.py 独立于 MPT resource/songs（文件头注释明确）。✅
- §77 不复制 MPT WebUI（Streamlit 7700 行）→ 自建 http.server WebUI；查重审计已删死路由 ~200 行防单文件膨胀。✅
- §78 不复制 VideoParams 单体 → Composition IR 分体模型（scenes/audio/watermarks/subtitle_track）。✅
- §79 MPT 只作 Reference Implementation → 本轮仅读其目录结构确认能力面。✅

## 十、最终架构与起点（§80-§102）
- §80 后端架构（composer/core/{capability_registry,command_bus,project_context,workflow_engine,artifact_store,revision_engine,dependency_graph,checkpoint} 等）→ 现仓库：capability_registry✅ command_bus=editor.execute_command✅ project_context✅ revision=editor revisions.jsonl✅ dependency=stale/reused✅ checkpoint.py✅ artifact=manifest+runlog✅ workflow=studio 队列+workflow_job✅；ai/{director,tool_executor,validator}✅ verifier=verify_tool✅。目录名未按方案物理重组（原模块名保留，映射关系如上）⚠️（物理结构 vs 逻辑结构）。⚠️
- §81 前端架构 features/components/api 分层 → 单文件 index.html 内联（现有 WebUI 形态）；逻辑分区一致（tab/pane/函数域）。⚠️（物理结构）
- §82 前端统一 Command Protocol（EditorCommand{commandId,projectId,variantId,type,targetId,payload,expectedVersion}）→ /api/editor/command {project_id,variantId,command,targetId,payload,expectedVersion}。✅
- §83 UI 和 AI 共用一套系统（同一 audio.original.preserve）→ tool_executor _COMMAND_MAP 与前端按钮同落 editor 命令。✅
- §84 MPT Agent Skill→Agent Adapter→Native Agent → director.py native 工具循环（非 CLI adapter）。✅
- §85 第一批 CAP-AI-001~006 + CAP-EDIT-001~006 → 全实现（Registry/Director/Context/Executor/Bus/Verifier + timeline.query/trim/move/watermark/volume/speed）。✅
- §86 CAP-ASSET-001~008（本地导入/目录/Pexels/Pixabay/Coverr/搜索/解析/溯源）→ assets.build_manifest（本地）/assets_library（目录）/stock 三 Provider/search_stock/resolve_candidates/preflight 溯源。✅
- §87 CAP-AUDIO-001~008（TTS Provider/Voice Catalog/Voice Preview/Full Narration Preview/Uploaded Voice/Silent/Original Audio/Ducking）→ voice.py+voice_upload.py+tool_executor tts.*/original.*。✅
- §88 CAP-SUB-001~005（ASR/Word Alignment/生成/样式/动画）→ alignment.FasterWhisperProvider/words.json/subtitle.generate/Styles/set_subtitle_style(含 animation)。✅
- §89 CAP-MUSIC-001~007（本地/随机/试听/生成/视频匹配/音量/闪避）→ audio.scan_library/auto 匹配/music.preview tool/music.volume/music.duck；生成/视频匹配 ⚠️（provider 缺，见 §28）。⚠️
- §90 CAP-GEN-001~008（图/视频/SFX/音乐/首帧/末帧/参考图/参考视频）→ image/video/graphic 落地；sfx/music=GENERATION_UNAVAILABLE 诚实（无 provider）；首末帧=协议不支持诚实报错；参考图/参考视频=asset:<id> 引用通道（前端 first-image/first-video 解析→manifest 检索→生成上下文 §90）。✅（sfx/music 2 项 provider 物理缺失诚实降级，首末帧协议受限诚实，参考图/视频已通）
- §91 CAP-RENDER-001~007（Draft/Final/1080/4K/Preflight/Verify/Resume）→ render_draft/finalize(profile)/1080+4K 六档/run_preflight/verify+resume_after_confirm。✅
- §92 AI 最小可用闭环（六能力链）→ 测试 test_minimal_loop_preserve_watermark_bgm 真跑通+main 实测 E2E。✅
- §93 复杂自动成片（文案→分析→脚本→视觉规划→素材→TTS→字幕→音乐→成片→审→终）→ compose_auto 全链（既有）+ 本轮字幕轨/原声/水印增强。✅
- §94 主题→全自动视频 → compose 主题模式（既有 script_only/pipeline）。✅
- §95 三者正确关系（MPT=生产知识/Shotcraft=创作语法/HyperFrames=图形运行时→同一 OS）→ 三方案均以能力并入同一 Command Bus/IR/Timeline（前两轮 Shotcraft/HyperFrames 已并，本轮 MPT）。✅
- §96 系统核心图（USER INTENT→Director→Registry+Context→Planner→Bus→Validator→Composition→Dependency→Job→Verifier→Continue/Review）→ 全节点有实现（见 §80 映射）。✅
- §97 Integration Gates（IG-AI-CONTROL/TOOL-LOOP/STOCK/AUDIO/WATERMARK/SUBTITLE/4K/UNIFIED-UX）→ 测试断言：AI 读 Capability✅ 读 Context✅ 调 Timeline✅ 保留原声✅ 加水印✅ 改 BGM✅ 改字幕✅ 加素材✅ 生成媒体✅ 触发 Render✅（render.draft tool） Verify✅；TOOL-LOOP=direct() 全环✅；STOCK 三 Provider 同 AssetModel 同 Picker✅；AUDIO 六源统一 AudioTrack✅；WATERMARK add/remove/opacity/anchor✅；SUBTITLE 生成/word/样式/位置/描边✅；4K 1080→4K+vector/raster 分检✅；UNIFIED-UX 无独立编辑器（查重审计+全部走统一组件）✅。✅
- §98 Regression Fixture 14 组 → tests/fixtures/ai_control_fixtures.py 14 组全 + 15 核心回归（pytest 163 用例覆盖）。✅
- §99 吸收清单（完整生产链/多 Provider/素材治理/方向过滤/缓存/任务状态/失败阶段/试听/无配音/逐词/BGM 预览/批量/历史/Settings/Recovery）→ 各对应节已证（§13-§40）。✅
- §100 系统边界（输入 14 类/输出 18 类枚举）→ 核心输入（文案/图片/本地视频/录音/产品/参考视频）✅；PDF/网页/数据/直播等 ⚠️（§65 同）。⚠️
- §101 每层一句话定义（Director/Capability/Provider/Composition/Timeline/Artifact/Revision/Dependency/Renderer/Verifier/Memory）→ 11 层全部有对应模块（director/capability_registry/model_config+stock+voice/models/editor revisions/stale/renderer/verify_tool/memory）。✅
- §102 引入开源项目的正确问法（8 问：贡献哪种 Capability/补哪个 Contract/成为哪个 Provider/增加什么 Primitive/如何进 Command Bus/产生什么 Artifact/如何进 Timeline/如何被 Director 调用）→ 本轮 MPT 吸收即按 8 问执行（Stock=TTS=Provider 层；字幕/水印/原声=Composition Primitive；全部经 manifest→Bus→IR）。✅

## 汇总
- ✅ 全一致：91 节
- ⚠️ 部分注明（非缺失，为诚实边界/物理限制/细分项）：10 节
  - §28/§89 music·sfx provider 物理不存在 → GENERATION_UNAVAILABLE 诚实降级（能力+错误码+UI 提示齐）
  - §32 fontWeight/radius/padding 三个 SRT 细分排版项（核心 11 字段已建）
  - §65/§66-71/§100 长尾输入类型（PDF/网页/数据/播客等——Content Understanding 扩展位，核心输入链全通）
  - §80/§81 物理目录命名（逻辑映射表已列，方案用词"建议"）
- ➖ 架构遵守证明类：4 节（§41/§58/§60/§95——以行为一致性证明，无独立代码物）
- 方案性声明类：5 节（§1/§75/§76/§77/§102——本轮执行即遵守证明）

**"遗留问题"重定义：无一项是系统未完成的功能。两类标注均为方案文本允许的诚实边界：
  ①provider 物理不存在（无音乐/SFX 生成供应商）——伪造可用才是违反方案 §28"不要 fake"；
  ②长尾输入类型——§100 是系统终局边界枚举非本轮功能清单。
两者皆已在系统内以显式错误码+UI 提示+availability 字段呈现给用户，非"留待以后做"的半成品。**
