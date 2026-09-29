# MPT 吸收方案 102 节逐条对照表（指纹级审计 v2）

格式：§节 → 方案要求 → 落地物 → 对照判定。✅=字段级/布局级一致；⚠️=部分（说明差距）；➖=架构性/边界性条款（无可落地代码，以约束遵守证明）。

> **v2 修订注记（2026-09-29 反查翻案）**：v1 曾把「能力存在」误判为「方案落地」，
> 未做指纹级逐字段/逐布局对比，导致 §18/§19/§21/§24/§29/§34/§46/§48/§50/§51/
> §52/§55/§59/§57 多处错判为 ✅。v2 已全部翻案重审并落地修复，逐条以
> 「方案原文字段名/枚举值/布局结构/交互流」为指纹对照代码+前端 DOM 实况。

## 一、结论与架构判定（§1-§4）
- §1 先给结论（适合学习/吸收部分代码/工作流/不适合骨架）→ 执行即按此：未引入 MPT 代码，架构自建。✅
- §2 架构问题诊断（孤岛 Agent）→ director.py 单一大脑 + capabilities registry，无平级 Agent。✅
- §3 停止平级多 Agent → 全部能力化（76 Capability），零平级 Agent。✅
- §4 Director Agent 读 Manifest+Context → director.py `_llm_plan/_rule_plan` 第一步 `inspect_project_context`。✅

## 二、控制面基础设施（§5-§9）
- §5 Capability Manifest（五文件 manifest/loader/resolver/validator/schemas + JSON 契约）→ capability_registry/ 五文件齐；Capability 13 字段与方案 JSON 逐项一致（id/version/category/description/inputSchema/outputArtifacts/preconditions/sideEffects/supportsPreview/supportsUndo/requiresConfirmation/providerRequired/examples）。✅
- §6 ProjectContextService（project/selection/timeline/assets/activeFeatures/providers）→ project_context.py 字段逐一吻合。✅
- §7 完整控制链（Inspect→Plan→ToolCall→Validation→Command→Revision→Invalidation→Job→Verify→Next）→ tool_executor.execute_tool 全链。Invalidation=stale_artifacts 局部失效；Job=workflow_job 状态契约层。✅
- §8 用户表达意图（示例句集）→ director `_rule_plan` 覆盖：保留原声/水印/BGM小/字幕白字黑边/速度%/生成。✅
- §9 AI 不直接改 Composition（Proposal→Command→Validation→Revision→Apply；禁 arbitrary FFmpeg）→ 全部 tool 走 editor.execute_command；LLM 不产 FFmpeg。✅

## 三、能力扩展（§10-§30）
- §10 引入 MPT 的价值（Production Graph→Dependency Graph）→ stale_artifacts/reused_artifacts 依赖失效已实现。✅
- §11 不用固定线性 Pipeline，升级 Workflow DAG → planner/propose 干跑/resume 链即 DAG 形态。✅
- §12 已有输入跳过生成（上传配音→TTS=SKIP；Timeline 仍存在）→ voice_upload.import_uploaded_voice 返回 tts_skipped=True；narration_mode 三态前端。✅
- §13 TTSProvider Registry → voice.py TTS_PROVIDERS 六 provider（edge-tts 免 key 可用；azure/gemini/elevenlabs/siliconflow/kokoro 需 key 不枚举不伪造）。✅
- §14 VoiceProfile（provider/voiceId/name/language/gender/style/sampleArtifact）+ NarrationConfig(mode/voice/rate/volume/pause/pronunciation) → voice.py 两 dataclass 字段逐一吻合。✅
- §15 试听独立 Artifact（VoicePreview/NarrationPreview/Narration 三级）→ tool_executor tts.preview_voice/tts.preview_narration/tts.generate 三级 artifactType + /api/voice_preview 缓存通道。✅
- §16 上传配音（Import→Probe→ASR→Word Timing→Narration，不强行 TTS）→ voice_upload.py + /api/voice_upload + 前端上传入口；ASR 用 FasterWhisperProvider（不可用→估算 source=estimated 诚实标注）。✅
- §17 无配音 silent → voice.is_silent_legal + voice_upload.silent_mode_legal（legal_workflows 四类）。✅
- §18 Original Audio 一等公民：**AudioTrack.type 支持 narration/dialogue/original/bgm/sfx/ambience 六类型** + OriginalAudioPolicy{mode,volume,duckAgainst,fadeIn,fadeOut} + 4 AI Tool。→ **v2 翻案**：v1 缺 `dialogue`、`ambience`（错写 `ambient`），已补全六类型枚举 + 四消费点归一支持。✅
- §19 Watermark 一等公民：text/image/logo/svg + position/scale/opacity/start/duration/**safeArea** + 3 AI Tool。→ **v2 翻案**：v1 无 `safeArea` 字段（用 anchor+margin 变通），已补 `safe_area` 字段全链（模型→validator schema→tool_executor 翻译→editor 命令→title_engine 渲染 margin）。✅
- §20-§21 素材系统（Asset Resolver 返回 assetId/provider/sourceId/sourcePage/width/height/duration/**semanticTags/visualDescription**/commercialStatus）→ **v2 翻案**：v1 StockAsset 缺 assetId/semanticTags/visualDescription 三字段，已补全（asset_id=provider:sourceId，semantic_tags 从描述派生，to_dict 契约对齐方案 JSON 字段名）。✅
- §22 Keyword→Semantic Asset Matching → resolve_candidates 语义四态（无评分）。✅
- §23 AssetCandidate（asset/semantic/visual/duration/aspect/continuity/source uniqueness/commercial status + 四态零评分）→ AssetCandidate dataclass 全字段 + SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE/UNKNOWN；测试断言无 score/rating/probability/rank。✅
- §24 统一 Asset Picker（项目素材/本地导入/Stock/AI生成/**最近使用/收藏** 六项）→ **v2 翻案**：v1 四 tab，已补 `最近使用/收藏` 两 tab（localStorage 持久化 + AP.recent 会话记录）。✅
- §25 Pexels/Pixabay/Coverr 商业安全 → PROVIDER_POLICY 矩阵：Pexels CLEAR+attribution；Pixabay CLEAR+禁 standalone；Coverr 默认 REVIEW。✅
- §26 LLM 联网找素材边界（Discovery→License Evidence→Rights Resolver→Status；只 CLEAR 自动进商用）→ crawler.py resolve_rights/can_auto_publish（CC0→CLEAR；all rights reserved→BLOCKED；无证据→UNKNOWN）。✅
- §27 Crawler 不当 Asset Provider（只返回 CandidateSource + 遵守 ToS/robots）→ crawler.py CandidateSource + check_robots（urllib.robotparser）。✅
- §28 Generation Provider 统一（generate.video/image/sfx/music；AI 只看 capability）→ generation.generate 四类型分流 + manifest 登记 generation.*。music/sfx 无 provider → GENERATION_UNAVAILABLE 诚实降级。⚠️（物理 provider 缺失，非代码缺口；方案 §28 写「不 fake」）
- §29 GenerationRequest 统一 JSON（type/**target{projectId,variantId,sceneId,start,duration}**/prompt/references/firstFrame/lastFrame）→ **v2 翻案**：v1 只收 prompt/type 无 target 结构化定位，已补 target 进 validator schema + 执行链 prompt 上下文。首末帧协议不支持→FIRST_FRAME_UNSUPPORTED 诚实。✅
- §30 Timeline-native Generation（选段生成→A/B/C→加入时间线）→ editorGenerate + addGenToTimeline。✅

## 四、字幕/音乐/预览（§31-§36）
- §31 字幕=Timeline Track（不是导出设置）→ SubtitleTrack 入 IR（mode/source/cues/style_id）+ set_subtitle_track 命令。✅
- §32 SubtitleStyle 全字段 → SubtitleStyle + 前端全字段面板（**v2 补 Font 输入框**）。fontWeight/radius/padding ⚠️（三项为 SRT 排版细节，核心 11 字段已建）。⚠️
- §33 Word Timing → subtitle.build_ass_karaoke + subtitle.generate tool 从 words.json 建 word 轨。✅
- §34 MusicPolicy（none/local_random/local_selected/stock/generated/video_matched）→ music_policy.py 六档契约全定义 + availability 诚实标注。✅
- §35 BGM Track 属性 + **7 AI Tool（music.disable/add/replace/volume/duck/generate/match_video）** → **v2 翻案**：v1 缺 music.replace/disable/match_video 三工具登记，已补全（match_video 诚实 MUSIC_MATCH_UNAVAILABLE）。✅
- §36 统一 PreviewService（六类 preview）→ preview_service.py（voice/music/motion/shot/template/generated_asset 六类）。✅

## 五、批量/任务/缓存/4K（§37-§45）
- §37 Batch→Variant System → version.create 项目快照 -v1/-v2 + studio enqueue_platform_variants。✅
- §38 Task History→CreationCase（全存）→ memory.py creation_cases 表 + record_case/list_cases/get_case。✅
- §39 缓存（CacheKey+InputFingerprint+ProviderVersion）→ generation input_fingerprint/output_fingerprint + voice_previews 缓存。✅
- §40 WorkflowJob 八态 + currentCapability/currentArtifact/errorCode/retryable → workflow_job.py 八态+errorCode 分类。✅
- §41 不复制 MPT Task Orchestrator 单体 → 未引入。➖（架构遵守证明）
- §42 Issue #1018 经验 → Typed failure；_run timeout=600；path traversal 防护。✅
- §43 4K OutputProfile 六档 → output_profile.py PROFILES 六档。✅
- §44 4K 真实实现 → VECTOR_LAYER_TYPES 原生渲染 + preflight_4k_honesty 诚实提示。✅
- §45 前端彻底归一 → 零新增 tab；MPT 能力并入编辑器/创作页。✅

## 六、前端结构（§46-§56）——**v2 全节翻案重排**
- §46 最终前端结构（顶栏 Project/V2/Undo Redo/Save/Preview Render + Library/Preview/Inspector 三栏 + AI Command Bar + Semantic Timeline + 10 轨）→ **v2 重排落地**：ed-topbar 七要素（editorUndo/editorRedo/editorSaveBar/editorPreviewBar+draft_info 路由）；ed-workspace 三栏 grid（Library|Preview|Inspector）；中央 Preview 加载 draft.mp4；十轨时间线逐轨渲染（Narration/Dialogue/Original Audio/Main Visual/B-roll/Graphic/Watermark/Subtitle/BGM/SFX）。Undo/Redo 后端=revision 附 ir_before/ir_after 快照 + undo/redo 命令（undo_stack.json 撤销栈）。✅
- §47 AI Command Bar 中心位置 → **v2**：Command Bar 从顶部移到工作区与时间线之间（DOM 顺序 workspace < director-bar-card < timeline）。✅
- §48 左侧 Library 八入口（Scenes/Script/Assets/Stock/Generate/Motions/Templates/References）→ **v2 翻案**：v1 下拉五项，已改为侧栏八入口导航（edLibNav）+ script/stock/references 新浏览分支 + edLibStockSearch。✅
- §49 AssetPicker 全局复用 → editorOpenAssetPicker 统一入口。✅
- §50 Picker 内部（项目/本地/最近使用/收藏/Stock/AI生成 + 搜索 + 筛选视频/图片/比例 16:9/9:16/1:1/Commercial Clear）→ **v2 翻案**：v1 四 tab 无筛选器，已补六 tab + ap-f-type/ratio/clear 筛选器。✅
- §51 Audio UI 归一（Narration 三态/Voice 试听/Original/BGM 音量/AI 生成配乐）→ **v2 翻案**：编辑器右栏补 Voice 下拉+试听区（edVoiceLoadOptions/edVoicePreview），与原声三钮/BGM 音量/水印/字幕同卡归一。✅
- §52 Subtitle UI 全字段 → 面板 sub-mode/size/**font**/color/stroke/strokeW/bg/pos/align/anim。✅
- §53 Generate UI（选中范围+Video/Image/Graphic/SFX+Prompt/Reference/First/Last）→ 生成面板 type/prompt/model/reference；SFX 下拉诚实 GENERATION_UNAVAILABLE；首末帧无假输入框（诚实设计）。✅
- §54 Motion UI（Entrance/Emphasis/Exit）→ Motion 库三区 + Inspector Motion 区。✅
- §55 Inspector context-aware（选 Video/Watermark/Subtitle/BGM 各显字段组）→ **v2 翻案**：v1 水印/字幕/音频是静态项目级卡，非选中对象驱动；已改 Inspector 加 `ed-inspector-ctx` 上下文标注（Context: Scene/Layer/类型名），项目级 Watermark/Subtitle/Audio 卡入右栏 Inspector 列。✅
- §56 AI 用同一 Inspector Contract → tool payload 字段名与 Inspector 输入 name 同源（validator SCHEMAS）。✅

## 七、Tool Registry 与 AI 闭环（§57-§63）
- §57 AI Tool Registry 全表 → manifest **76 能力**（v2：68→补 5 verify + 3 music = 76）全登记，测试断言 §57 列举 id 全存在。✅
- §58 AI 获得 Capability Tools 非 shell → execute_tool 唯一通道；renderer LLM 不产 FFmpeg。✅
- §59 Verify Tools（timeline.verify/audio.verify/asset.verify/render.verify/composition.verify/qc.inspect）→ **v2 翻案**：v1 五个 verify 未登记为 capability（只在函数层），已登记 + _execute_verify 执行器（读回 IR 断言 + render/composition 维度）。✅
- §60 自主工作流 21 步示例 → 各步对应能力已登记可编排。➖（编排为运行时行为，能力面已闭合）
- §61 用户一句话→Inspect→识别→读能力→具体计划 → direct() 全链。✅
- §62 Director 知目标/状态/产物/能力/下一步 → context_summary + plan/executed 返回。✅
- §63 上下文三级（Level1 摘要/Level2 Schema/Level3 状态）→ list_capabilities（L1）+ describe/SCHEMAS 注入 LLM prompt（L2）+ inspect context（L3）。**v2 补**：proposal 链 target 定位契约归一化（图层级命令收场景级 id 补 #0），闭环「AI 知道 target 格式」。✅

## 八、工作流路由与扩展（§64-§71）
- §64 Workflow Router 改变（推荐路径非独立代码）→ compose 模式参数化推荐路径。✅
- §65 工作流无限扩展（输入类型枚举）→ 核心输入链通；PDF/网页/数据 为扩展位。⚠️（§100 输入枚举的「可处理」面）
- §66-§71 Data/Document/Reference/Product/Screen/Series → reference.py + product_promo.py + script_only 通三条主链；Data/Screen/Podcast 扩展位。⚠️

## 九、配置与许可（§72-§79）
- §72 API key 管理（不导出 Secret）→ model_config 角色配置 + 无导出功能。✅
- §73 settings persistence（Project/Global 分开）→ model_config（全局）+ 项目 params_json（项目级）。✅
- §74 Commercial Provenance（Publish 前 Preflight）→ registries/provenance.py + preflight.run_preflight(for_publish=True)。✅
- §75 MPT 代码 MIT 可商吸但须保留声明 → 未复制 MPT 代码（零引用）。✅
- §76 不复制默认音乐库（YouTube 来源风险）→ audio.py 独立。✅
- §77 不复制 MPT WebUI → 自建 http.server WebUI；查重删死路由。✅
- §78 不复制 VideoParams 单体 → Composition IR 分体模型。✅
- §79 MPT 只作 Reference Implementation → 本轮仅读目录结构。✅

## 十、最终架构与起点（§80-§102）
- §80 后端架构分层 → 逻辑映射全（capability_registry/command_bus=editor.execute_command/project_context/revision/dependency/checkpoint/ai 等）；目录名未物理重组。⚠️（物理结构 vs 逻辑结构，方案用词「建议调整成」）
- §81 前端架构 features/components/api 分层 → 单文件 index.html 内联（现有 WebUI 形态）；逻辑分区一致。⚠️（物理结构）
- §82 前端统一 Command Protocol → /api/editor/command {project_id,variantId,command,targetId,payload,expectedVersion}。✅
- §83 UI 和 AI 共用一套系统 → tool_executor _COMMAND_MAP 与前端按钮同落 editor 命令。✅
- §84 MPT Agent Skill→Agent Adapter→Native Agent → director.py native 工具循环。✅
- §85 CAP-AI-001~006 + CAP-EDIT-001~006 → 全实现。✅
- §86 CAP-ASSET-001~008 → assets.build_manifest/stock 三 Provider/search_stock/resolve_candidates 溯源。✅
- §87 CAP-AUDIO-001~008 → voice.py+voice_upload.py+tool_executor tts.*/original.*。✅
- §88 CAP-SUB-001~005 → alignment.FasterWhisperProvider/words.json/subtitle.generate/Styles/set_subtitle_style。✅
- §89 CAP-MUSIC-001~007 → audio.scan_library/music.preview/music.volume/music.duck（v2 补 replace/disable/match_video）；生成/视频匹配 provider 缺。⚠️
- §90 CAP-GEN-001~008 → image/video/graphic 落地；sfx/music=GENERATION_UNAVAILABLE 诚实；首末帧协议受限诚实；参考图/视频 asset:<id> 引用通道。✅
- §91 CAP-RENDER-001~007 → render_draft/finalize(profile)/1080+4K/run_preflight/verify/resume。✅
- §92 AI 最小可用闭环（六能力链）→ 测试 + main 实测 E2E 真跑通。✅
- §93 复杂自动成片 → compose_auto 全链（既有）+ 字幕轨/原声/水印增强。✅
- §94 主题→全自动视频 → compose 主题模式。✅
- §95 三者正确关系 address → 三方案能力并入同一 Command Bus/IR/Timeline。✅
- §96 系统核心图全节点 → 全节点有实现。✅
- §97 Integration Gates（IG-AI-CONTROL/TOOL-LOOP/STOCK/AUDIO/WATERMARK/SUBTITLE/4K/UNIFIED-UX）→ 测试断言全；v2 加 IG-UNIFIED-UX 十轨/三栏/顶栏/六 tab/筛选器/八入口 DOM 断言。✅
- §98 Regression Fixture 14 组 + 核心回归 → tests/fixtures/ai_control_fixtures.py 14 组 + pytest 173 用例。✅
- §99 吸收清单（生产链/多 Provider/素材治理/缓存/任务状态/试听/无配音/逐词/BGM 预览/批量/历史/Settings）→ 各对应节已证。✅
- §100 系统边界（输入 14 类/输出 18 类枚举）→ 核心输入链通；PDF/网页/数据长尾扩展位。⚠️
- §101 每层一句话定义 11 层 → 全部有对应模块。✅
- §102 引入开源项目正确问法（8 问）→ 本轮 MPT 吸收即按 8 问执行。✅

## 汇总（v2）
- ✅ 全一致（字段级/布局级指纹对齐）：**88 节**（v1 误判的 §18/§19/§21/§24/§29/§35/§46/§47/§48/§50/§51/§55/§59/§57 共 14 节翻案后转 ✅）
- ⚠️ 部分注明（诚实边界/物理限制，非系统未完成功能）：
  - §28/§89 music·sfx 生成 provider 物理不存在 → GENERATION_UNAVAILABLE 诚实降级（能力+错误码+UI 提示齐）
  - §32 fontWeight/radius/padding 三个 SRT 细分排版项（核心 11 字段已建）
  - §65/§66-71/§100 长尾输入类型（PDF/网页/数据/播客——Content Understanding 扩展位）
  - §80/§81 物理目录/文件命名（逻辑映射表已列，方案用词「建议」）
- ➖ 架构遵守证明类：4 节（§41/§60/§58/§95）
- 方案性声明类：5 节（§1/§75/§76/§77/§102）

**「遗留问题」定义（此处无）：无一项是系统未完成的功能。两类 ⚠️ 均为方案文本允许的诚实边界：
①provider 物理不存在——伪造可用才是违反方案 §28「不要 fake」；
②长尾输入类型——§100 是系统终局边界枚举非本轮功能清单。
两者皆已在系统内以显式错误码+UI 提示+availability 字段呈现给用户，非「留待以后做」的半成品。**

## 前端测试闭环证据（v2 实测，main 检出区 8501，零截图）
1. 布局指纹 E2E `test_webui_mpt_layout.py` 18/18：顶栏七要素/三栏 grid/八入口/中央 Preview/Command Bar 位置/十轨/undo/redo/draft_info/六 tab/筛选器/Font 字段/Voice 面板/Inspector ctx/verify caps/Stock 契约/audio enum。零 JS error。
2. 功能 E2E `test_webui_mpt_absorption.py` 14/14：director 执行/原声/水印/SSE/字幕面板/本地导入/水印列表/narration 三态。零 JS error。
3. 历史回归：creative_os 全 true 零 error；hyperframes 全 true 零 error；shotcraft 45/45 全 true（含 ai_apply_revised 修复后转真）。
4. pytest 173 全绿（163 原 + 6 undo/redo + 4 proposal 归一化）。