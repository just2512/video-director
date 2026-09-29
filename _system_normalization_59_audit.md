# 系统能力归一.md 59 节逐条审计（指纹级，2026-09-29）

方案：`D:\openworkplace\content-wiki\系统能力归一.md`（59 节）。格式：§节→方案指纹→当前落地→判定。
✅=字段/枚举/结构级一致；⚠️=有指纹差异（注明是"形式差异/超集"还是"未落地的架构归一"）；➖=方法/参考类。

## 控制面与模型（§1-§10）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §1 项目重定义 | AI Video Creative OS 分层（Director→Capability→Context→Bus→IR） | director.py + capability_registry + project_context + editor.execute_command + CompositionIR | ✅ |
| §2 开源项目归宿 | 16 项目降维为 Provider/Adapter/Registry | MPT/HyperFrames/Shotcraft/VideoLingo 集成；未引 MPT UI/VideoParams | ✅ |
| §3 压缩成六层 | Intelligence/Capability/Domain/Execution/Artifact/UI | 逻辑分层对应；物理目录未重组（同 §37） | ⚠️ 逻辑一致，物理未重组 |
| §4 平台核心 13 项 | Registry/Director/Context/Bus/IR/SemanticTimeline/Revision/Dependency/Lineage/UnifiedAsset/Audio/Layer/Workspace/Verifier | 全部有对应模块 | ✅ |
| §5 三抽象 | Capability≠Provider；Provider(谁完成)；Artifact(产出什么) | capability_registry(能力) / capabilities.py+voice/stock(Provider) / preview_service(Artifact) | ✅ |
| §6 Composition IR | `Project{Variants,activeVariantId}`→`Variant{scenes,tracks}`→`Scene{semanticRole,beats}`→`Beat{layers,audio}` | CompositionIR=Project(version/scenes/audio/watermarks) **无 Variant 层、无 Beat 层、Scene 无 semanticRole** | ⚠️ 未落地 Variant/Beat/semanticRole（结构差异） |
| §7 Layer 20 类型 | video/image/person/text/title/subtitle/lowerThird/shape/svg/icon/chart/diagram/graphic/background/logo/lottie/pip/html/canvas/webgl/three_scene | **本轮补 graphic/title/lower_third/pip**；subtitle 用 SubtitleTrack 轨非 Layer | ✅（补4）⚠️ subtitle 异架构 |
| §8 Audio 6 类型 | narration/dialogue/original/bgm/sfx/ambience + OriginalAudioPolicy | AudioTrackType 六类型齐 + ambient 别名归一 | ✅ |
| §9 Watermark 标准 Layer | `{id,source:AssetRef,position,scale,opacity,start,duration,safeArea:boolean}` | WatermarkLayer{kind,text,asset_id,anchor,offset_x/y,**safe_area:int=32**,...} | ⚠️ 三处指纹差异（见下） |
| §10 Asset 统一 | 7 来源 local/uploaded/stock/generated/extracted/graphic/hybrid + Provenance/RightsStatus/Provider/SourcePage/Creator/OriginalAssetId/GenerationPromt/Fingerprint | StockAsset 有 provider/source_page/creator/semantic_tags/commercial_status；AssetRef.source 是松散 "user"；字段分片 | ⚠️ 未定义 AssetSource 枚举，字段分片 |

## 控制流与集成（§11-§24）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §11 Recipe/Motion/Template 统一 Registry | Registry→Resolver→Composition | registries/(recipes/templates/resolver/provenance) | ✅ |
| §12 Capability Registry | 动态发现 system.capabilities.list/describe | capability_registry 76 能力 + /api/director/capabilities | ✅ |
| §13 Project Context | project/selection/tracks/assets/providers/version | project_context.inspect_project_context 字段全 | ✅ |
| §14 Director Agent | 停平级 Agent，Director→Resolver/Worker/Provider | director.py 单一 + 76 能力 | ✅ |
| §15 执行循环 | Intent→Inspect→Plan→Validate→Execute→Verify | direct() 全链 | ✅ |
| §16 Command Bus | UI/AI/API 同命令 | editor.execute_command + /api/editor/command | ✅ |
| §17 AI 禁直改文件 | Proposal→Command→Validation→Composition | proposal.py 四态流 | ✅ |
| §18 Revision 统一 | Command→Revision{patches,affected,stale} | editor revisions(ir_before/ir_after) | ✅ |
| §19 Dependency Graph | 改 Motion 只 invalidate Scene03 | stale_artifacts/reused_artifacts | ✅ |
| §20 Artifact Lineage | inputFingerprint/outputFingerprint/createdBy | generation input/output_fingerprint | ✅ |
| §21 Preview/Final 统一 | IR→Renderer→Draft/Final 同语义 | preview_service + renderer draft/final | ✅ |
| §22 HyperFrames 集成 | Composition→GraphicPlan→Adapter→Runtime | graphics/(compiler/provider/render) | ✅ |
| §23 Shotcraft 集成 | Director→shot.resolve→Registry | registries/resolver.resolve_shots | ✅ |
| §24 MPT 集成 | tts.generate/stock.search/subtitle.generate/music.add→Provider | voice/stock/subtitle/audio 各 Provider | ✅ |

## 前端与生成（§25-§36）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §25 统一 Workspace | 顶栏+Library九入口+Preview+Inspector+AI Command Bar+十轨 | editor 三栏+顶栏七要素+八入口+十轨+AI 指令栏 | ✅ |
| §26 Inspector context-aware | 选 Video/Watermark/Subtitle/Graphic 各显字段组 | edShowInspector 按选中类型显字段 | ✅ |
| §27 Asset Picker 统一 | Project/Local/Pexels/Pixabay/Coverr/Generated/Recent/Favorite | editorOpenAssetPicker 六 tab+筛选 | ✅ |
| §28 Generation 统一 | Image/Video/SFX/Music→Request→Job→Result→Artifact→Asset→Timeline | generation.generate 四类型+add_to_timeline | ✅ |
| §29 Timeline-native Generation | 选段→Generate→A/B/C→Add to Timeline | editorGenerate+addGenToTimeline | ✅ |
| §30 Reference Video | 镜头检测→Style DNA | reference.py reference_analyzer | ⚠️ Style DNA 抽取部分 |
| §31 Product Video | Product Understanding→Evidence→Composition | product_promo.py | ✅ |
| §32 Screen Demo | 录屏+放大+高亮+光标 | shot.screen-demo-flow 配方 | ⚠️ 部分/扩展位 |
| §33 Data Video | CSV→Chart Resolver（方案"未来"） | data_viz LayerType 预留 | ⚠️ 扩展位（方案自标未来） |
| §34 纯文案视频 | Text→Narrative→Beat→Resolver→Composition | script_only.py + graphics/graph plan | ✅ |
| §35 两生成体系 | Generative Media + Generative Graphics | generation + graphics | ✅ |
| §36 创作单位 Visual Element→Layer | 视频/图片/HTML/SVG/Chart 统一 Layer | LayerType 枚举（本轮补 4） | ✅ |

## 工程与收口（§37-§59）

| § | 方案指纹 | 当前落地 | 判定 |
|---|---|---|---|
| §37 Directory 收口 composer/ | core/composition/capabilities/.../adapters/ | video_director/ 扁平（capability_registry/graphics/registries 三子包） | ⚠️ 物理目录未重组（逻辑分层一致） |
| §38 Adapter 防污染 | adapters/mpt/hyperframes/shotcraft 隔离 | graphics/ 有 adapter 语义；无 adapters/ 目录 | ⚠️ 逻辑隔离，无物理 adapters/ |
| §39 开发单位 | Capability→Contract→Task→Gate→Regression | 逻辑遵循（tests/ INTEGRATION-GATES.md） | ✅ |
| §40 核心能力地图 | CAP-AI/COMP/EDIT/ASSET/AUDIO/SUB/MUSIC/GEN/CREATIVE/RENDER | manifest 76 能力归 CAP-AI/EDIT/ASSET/AUDIO/SUB/MUSIC/GEN/RENDER 组 | ⚠️ CAP-COMP-001~008/CAP-CREATIVE-001~006 未登记（能力已实现） |
| §41-43 前三 E2E | 原声+水印+BGM+字幕+Draft / 换场景 / 改字幕 | 测试+main 实测 | ✅ |
| §44 第四 E2E | 参考视频风格→新产品视频 | reference Style DNA→product 链 | ⚠️ 部分 |
| §45 Integration Gates | 25 IG 状态 PASS/REJECT/MISSING/UNKNOWN | INTEGRATION-GATES.md + tests | ✅ |
| §46-48 AI Control/Unified UX/Persistence Gate | AI 可控/UI=AI=API/重启不丢 | director 闭环/Command Bus/存储落盘 | ✅ |
| §49-50 Version/Commercial Gate | @1.0.0 版本锁定/UNKNOWN BLOCKED 拦截 | registry version/preflight | ✅ |
| §51-54 并行/第一批/闭环/职责 | 能力流并行/核心 Contract/最小闭环/职责固定 | 逻辑一致 | ✅ |
| §55-59 5问/归宿表/收敛图/目标/阶段 | 方法/参考 | ➖ | ➖ |

## 汇总

- ✅ 全一致：**43 节**（§7 本轮补 4 类型后转 ✅）
- ⚠️ 指纹差异：**11 节**，分两类：
  - **形式差异/实现超集**（方案意图已达成，字段形式不同）：§9 safeArea int=32 是 `safeArea:boolean` 的超集（0↔false/>0↔true 且带距离）；§9 anchor+offset 是 `position` 的九宫格具体化
  - **未落地的架构归一**（需专门重构）：§3/§37 物理目录未重组为 composer/；§6 IRR 缺 Variant/Beat/semanticRole；§10 AssetSource 7 枚举未定义；§38 无物理 adapters/ 目录；§40 CAP-COMP/CAP-CREATIVE 未登记；§7 subtitle 用 SubtitleTrack 轨非 Layer
- ➖ 方法/参考类：5 节（§55-59）

## §9 三处指纹差异明细（诚实说明，非假装一致）

1. `safeArea:boolean` vs `safe_area:int=32`：int 是 boolean 超集（safe_area=0↔safeArea=false，>0↔true 且距离 N px）。字段名经 to_dict/validator 已对齐 camelCase。
2. `source:AssetRef` vs `kind+text+asset_id`：当前支持「文字水印(text)」和「素材水印(image/logo/svg, asset_id)」两种，方案要求统一 `source:AssetRef`。文字水印无 AssetRef 是合理扩展。
3. `position:Position` vs `anchor+offset_x/offset_y`：anchor 九宫格（top_left~center）+像素偏移是 position 的具体实现超集。