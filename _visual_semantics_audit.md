# 视觉语义归一.md 逐条审计（指纹级，2026-09-30）

方案：`D:\openworkplace\content-wiki\视觉语义归一.md`（77 节）。
格式：§节 → 方案指纹 → 当前落地 → 判定。✅=字段/枚举/结构级一致；⚠️=部分落地；➖=方法论/参考类。

## 六 Contract 落地（§72，本轮核心）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §72.1 Transform | position/scale/rotation/opacity 结构化 + CoordinateMapper | `visual_semantics.py Transform(x/y归一/scale/rotation/opacity/anchor九宫格)` + Layer.transform 挂载 + editor 命令 `set_visual_transform`（增量更新 fail-closed）+ Inspector UI（§24 五输入框） | ✅ |
| §72.2 Keyframe | property 枚举 + Keyframe{at,value,easing} + KeyframeTrack | `KeyframeTrack(property 11枚举)` + `Keyframe(at/value/easing)` + Layer.keyframes + 命令 `add_keyframe/remove_keyframe`（同 at 覆盖/时间排序 fail-closed）+ Inspector 打帧 UI + 时间线轨 11 帧点 | ✅ |
| §72.3 Mask | shape/geometry/feather/invert/opacity/keyframes；Mask ≠ Matte | `Mask(id/shape5/xy/wh/feather/inverted/opacity/animated/keyframes/geometry)` + Layer.mask + 命令 `apply_mask/remove_mask` + Inspector 蒙板块（§63 形状/羽化/反转） | ✅ |
| §72.4 Camera | CameraKey{at,position,target} + Track{keyframes,projection,followTarget,constraints} | `CameraKeyframe(at/position[x,y,z]/target/easing)` + `CameraTrack(id/keyframes/projection2/follow_target/constraints)` + Scene.camera + CompositionIR.camera_tracks | ✅ schema 落地；渲染侧 perspective 诚实 UNAVAILABLE（§45 不 fake success） |
| §72.5 Continuity | ContinuityLink(from/to/type8/sourceLayer/targetLayer/anchor/cameraPath/morphPath) + SceneState | `ContinuityLink(8类型枚举)` + `SceneState(visibleLayers/anchorLayerId/camera/spatialMap)` + Scene.entrance_state/exit_state/continuity_in/continuity_out + CompositionIR.continuity_links + 分镜卡「从上一镜延续」选择（§61 UI） | ✅ schema+UI；ContinuityVerifier（§33 PPT Detector 自动检测）未做 |
| §72.6 Evidence | EvidenceRef(5型) + FeatureClaim(3态) + ProjectEvidenceGraph | `EvidenceRef(id/sourcePath/symbol/route/state/artifactId/evidenceType5/reuseMode3)` + `FeatureClaim(VERIFIED/UNVERIFIED/REJECTED)` + `verified_claims() 只放行 VERIFIED` + repo_promo.py 真实发现链 + §65 前端证据链显示 | ✅ |

## IR 结构升级（§十六/§十七/§三十一/§七十⑤）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §十六 Composition 顶层 | tracks/references/styleContract/cameraTracks/continuityLinks/assets | references(§17已有) + style_contract + camera_tracks + continuity_links + assets 全部 Optional 挂载；tracks 不新造平行模型（audio 六型 + Layer.type 26 值已覆盖 16 轨语义，避免两套表达） | ✅（tracks 用现有 audio+Layer 表达，注释说明防割裂） |
| §十七 Track 16 种 | Narration…Ambience 结构化对象 | AudioTrackType 6 型 + LayerType 26 值（含 pip/lower_third/graphic/lottie/data_viz）+ renderPhysicalTimeline 十一轨渲染 | ✅ |
| §十八 Reference Graph | firstFrame/lastFrame/visual/generation/continuity/mask/source/style | ReferenceType 7 值（系统能力归一轮已落）+ 本轮 mask_reference 消费点就位 | ✅（前轮落地） |
| §十九 7 基本元素 | Media/Graphic/Text/Motion/Camera/Mask/Audio | LayerType 覆盖 Media+Graphic+Text；Motion=registries/recipes；Camera=CameraTrack；Mask=Mask+ChromaKey Matte；Audio=AudioTrack | ✅ |
| §三十一 Scene Contract | intent/visualIntent/entranceState/exitState/continuityIn/Out/camera | SceneIR 全字段落地（beats/semantic_role 前轮 + 本轮 6 字段） | ✅ |
| §七十⑤ CompositionIR 顶层升级 + legacy loader | 新旧 JSON 兼容 | 全 Optional；`test_legacy_ir_json_still_loads` 显式回归 | ✅ |

## 能力族（§71）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §71 Visual | CAP-VIS-001~009（Concept/Storyboard/Continuity/Camera/Keyframe/Mask/Matting/PiP/Morph） | manifest 登记 11 条（visual.concept/storyboard/continuity.plan/continuity.verify/camera.apply/keyframe.add/keyframe.remove/mask.apply/matting.generate/pip.configure/visual.morph）；keyframe/mask/matting 三条已可执行（editor 命令 + /api/editor/matting） | ✅ 登记；camera.apply/pip.configure/morph 可执行命令待接线（诚实 gap） |
| §71 Subtitle | CAP-SUB-006~011（WordToken/Highlight/Karaoke/Kinetic/Layout/SafeZone） | subtitle.word_token/highlight/karaoke/kinetic/layout/safe_zone 全登记 | ✅ 登记；word_token 有 SubtitleTrack mode=word 实现，highlight/karaoke 有样式动画参数，kinetic/layout/safe_zone 为 schema 登记待渲染实现 |
| §71 GFX | CAP-GFX-001~008（HTML/SVG/Canvas/Lottie/Chart/DataViz/3D/WebGPU） | gfx.html/svg/canvas/lottie/chart/data_viz/three/webgpu 全登记；lottie/three/webgpu provider_required=True（无 renderer 诚实 UNAVAILABLE §45） | ✅ 登记；GFX-001~030 一揽子已按 §七十③ 拆分 |
| §71 Promo | CAP-PROMO-001~011 | promo.repo_discovery/project_scan/feature_discovery/runtime_launch/component_trace/evidence_graph/product_brief/concept/storyboard/render/verify 全登记；repo_discovery/project_scan/feature_discovery 三条已可执行（repo_promo.py + /api/promo/*） | ✅ 登记；runtime_launch/render/verify 命令待接线 |
| §71 Agent | CAP-AGENT-001~005（MCP/CLI/Context/JobWatch/ToolDiscovery） | agent.mcp/cli/context/job_watch/tool_discovery 全登记；mcp/cli provider_required=True 诚实 UNAVAILABLE；job_watch 有 SSE /api/events 地基 | ✅ 登记；MCP/CLI 网关实体未接（§49-§53 全量实现属下一阶段） |

## 前端融合（§59/§60/§61/§62/§63/§64/§65）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §59 核心页面 | Create / Workspace / Review 三页 | tab-row 15 → 4：🎬 创作 / 🎛 工作台 / ✅ 审阅 / ⚙ 设置；旧 15 pane 全部融合为页内子面板（create+projects+series+capture+memory→创作页；templates+motions+assets+reference→工作台；publish+automation→审阅页）；旧 switchTab 调用 TAB_ALIAS 兼容映射 | ✅ |
| §60 Storyboard UI | 场景卡含 Narration/VisualIntent/VisualForm/Carry/Motion/Camera/Evidence | gfx 分镜卡有 旁白/视觉意图/视觉形态/动效/布局 + 本轮加「从上一镜延续+过渡方式」下拉（8 类型） | ⚠️ Camera/Evidence 行未显示（分镜 schema 无对应字段，渲染层未消费） |
| §61 Continuity UI | 两 Scene 间显示延续 + Continuity Inspector | 分镜卡延续行（carry 来源+类型下拉） | ✅ 卡内形态；独立 Continuity Inspector 面板未建（信息已在卡内） |
| §62 Motion UI | Presets + Keyframe editor 并存 | 动效库 Presets（motions tab→工作台库）+ 本轮 Keyframe editor（Inspector 打帧+时间线帧点） | ✅ |
| §63 Mask UI | Shape/Feather/Invert/Keyframe + Path Editor | Inspector 蒙板块：形状/羽化/反转/应用/移除；Path Editor（画面拖顶点）未做 | ⚠️ 参数面板落地；Path Editor 画布级编辑未做 |
| §64 Matting UI | Provider/生成/预览/应用 | Inspector 抠像块：绿幕/蓝幕 + /api/editor/matting（ChromaKey 真实 ffmpeg 抠像 + 产物入素材库 + replace_source 替换层） | ✅ |
| §65 Repo Promo UI | 选择目录→Project Found→Scan→Detected Features→Source/Runtime/Evidence | 创作页「从软件项目开始」入口 + promo-panel：发现项目→扫描（框架/路由/组件/设计令牌）→功能证据（只显示 VERIFIED + 证据路径） | ✅（Runtime 真实打开属 promo.runtime_launch，未接，诚实标注） |

## 遗留问题（§七十②/§33/§49-§53）

| § | 遗留 | 原因 | 归属 |
|---|---|---|---|
| §七十② | 图片生成 503 时前端仍显示「生成」按钮无 UNAVAILABLE 态 | 需要 Capability Registry status 消费链（/api/capabilities 有状态，前端生成面板未读） | 下一轮（前端读 /api/capabilities 显式禁用态） |
| §33 | ContinuityVerifier（PPT Detector）自动检测未实现 | 需要「渲染帧测量」管线（onetake probe 思路：boundary 测 carried/anchored/bare），非声明式 schema | 下一轮（独立 QC 模块） |
| §49-§53 | MCP/CLI Agent Gateway 实体未接 | 需新进程级网关服务（creative.inspect/plan/execute 七工具），非本轮 Contract 范围 | 下一阶段 |
| §63 | Mask Path Editor（画布拖顶点）未做 | 需 Canvas 交互层（CoordinateMapper 前端实现） | 下一轮 |

## 商业安全（§55）

- ffmpeg-skill：MIT，仅学习其 Contract Derived Tooling 模式（argparse→schema 派生思想），未复制代码。
- Jianying/Guizang/Onetake：Clean-room——只学协议（keyframe fail-closed、camera key 结构、boundary 测量思想、evidence 文件分离），零代码复制。
- Remotion Skills：学习 Interactivity/Captions 的结构思想（editableProperties、Caption JSON），未引入 Runtime。

## 验证

- pytest 234 passed（173 旧全绿 + 61 新：41 Contract 指纹/fail-closed + 20 命令端到端含 undo/lock/revision 链）
- 前端结构自检：4 tab / 4 pane / div 配平 / 13 loader 函数全在 / 旧 switchTab 全 alias 覆盖
- 前端实测：见会话验证记录（8501 起服务 + browser 检查 js_errors）
