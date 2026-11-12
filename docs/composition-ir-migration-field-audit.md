# CompositionIR → FilmDocument 顶层字段迁移审计

审计对象：`video_director.models.CompositionIR` 当前持久化模型；执行路径：`build_film_from_composition()`。
本清单只声明**顶层字段的迁移处置**，不把顶层字段被读取误报为其所有嵌套语义均已无损执行。

分类定义：

- **MAPPED**：至少有明确的 FilmDocument 表达或迁移逻辑；本表“边界/证据”仍列出未覆盖的子语义。
- **ARCHIVED_ONLY**：原始值进入 `migration/source-composition.json` 或明确的 `data-source-*` provenance 属性，但没有被 Film Runtime 作为行为执行。
- **REJECTED**：迁移前或迁移时明确拒绝，不允许静默降级。
- **UNKNOWN**：尚未建立可验证的迁移处置；不得按 PASS 处理。

| CompositionIR 字段 | 分类 | 当前迁移处置与边界 |
|---|---|---|
| `version` | ARCHIVED_ONLY | 作为 IR schema/source snapshot 版本参与解析与归档；当前没有对应的 Film Runtime 播放行为或 film.html 运行时字段。历史版本语义兼容仍需独立验证。 |
| `project` | MAPPED | width/height → Film stage；fps 校验后写入；duration 用于时间线范围。非法 fps 明确拒绝。 |
| `style_id` | MAPPED | 匹配到内置 Style Registry 时只执行已声明的字幕位置/字号、旧式 zoom 和 BGM 默认音量映射；未知自定义 style 仅保存来源属性并告警。 |
| `scenes` | MAPPED | 主场景、支持的 video/image layers、选择区间及部分语义 lineage 转换；非 video/image 图层、未匹配 selection、部分层字段会告警/不迁移。 |
| `audio` | MAPPED | 音频片段按类型、asset、时间及支持的参数转换；必须结合下方 audio_tracks 说明，不能推断原始分组完全保留。 |
| `audio_tracks` | MAPPED | ID/name/order 参与轨道生成；locked 映射 Film 轨道锁定，muted 与片段 muted 合并为运行时静音；hidden 只保存折叠状态 provenance；多片段 lane 会拆成物理 Film 轨道，原分组关系不保留。 |
| `watermarks` | MAPPED | 支持的水印素材/时间/展示参数映射；不支持的类型/参数须以迁移 warning 为准。 |
| `visual_tracks` | MAPPED | 轨道顺序、名称、来源 ID 用于 overlay 排序和 provenance；locked 映射 Film 轨道锁定；hidden 是折叠状态，不是画面隐藏，原分组交互不迁移。 |
| `visual_overlays` | MAPPED | video/image overlay 映射到独立物理轨道，处理时间、素材、位置、尺寸、opacity、z-order、transform 的支持子集；其他类型明确告警。 |
| `subtitle_style` | MAPPED | 支持的字体、字号、颜色、描边、背景等转入字幕 HTML；非法值回退并告警；字体文件不随迁移打包。 |
| `subtitle_track` | MAPPED | cue 时间与文本进入 Film 字幕轨道；style registry 引用、非 sentence mode、非 tts_word_boundary 来源等 lineage 不执行或不完整保留，发出 warning。 |
| `origin_template_id` | ARCHIVED_ONLY | 写入来源 lineage 属性，不反向修改模板，也不作为渲染行为。 |
| `origin_template_version` | ARCHIVED_ONLY | 写入来源 lineage 属性，不反向修改模板，也不作为渲染行为。 |
| `active_variant_id` | MAPPED | 无 variants 时使用顶层 scenes/audio；单一 variant 可直接选用；存在多个 variants 且未提供 active_variant_id 时返回 `ACTIVE_VARIANT_REQUIRED`，显式 ID 不存在时返回 `ACTIVE_VARIANT_NOT_FOUND`，两者均禁止回退到顶层 scenes/audio。 |
| `variants` | MAPPED | 仅选中的 variant scenes/tracks/style 参与本次迁移；其他 variants 不并入最终时间线，完整原始 IR 由源快照保存。 |
| `references` | ARCHIVED_ONLY | ReferenceGraph 保存为 `data-source-reference-graph`；不会自动生成或插入未在时间线中的素材。 |
| `style_contract` | MAPPED | motion_temper、safe_zone、部分 palette/typography 仅在白名单和条件满足时应用；未识别的调色板/字体字段仍只保留来源数据并告警。 |
| `camera_tracks` | MAPPED | 支持的摄影机关键帧转换为 Film transform channels；非法/不支持的关键帧和通道不应被视为已执行。 |
| `continuity_links` | ARCHIVED_ONLY | 保存为 `data-source-continuity-links`；carry/morph 等动作尚未编译为播放行为。 |
| `assets` | MAPPED | 被选中时间线引用的素材用于资源解析；未被引用但带来源/版权/提示词/描述元数据的素材只在源快照保留，不自动插入成片。 |

## 明确的拒绝边界

- 不支持的渲染图层不得被静默转换为可播放 video/image。
- 非法 fps、无效时间/变换参数等必须按迁移校验返回错误或显式 warning；不得伪装为无损迁移。
- 源快照归档成功只证明原始数据可追溯，不证明对应字段已进入 Film Runtime。

## 已核实的嵌套字段边界（非无损声明）

以下是代码路径复核后确认的关键边界，字段未在此列出时仍不得推断为已迁移：

- `SceneIR`：`id/start/duration/layers/selections` 进入场景与时间线构建；`semantic_role/intent/visual_intent/selected_candidate_id/origin_recipe_id/origin_recipe_version` 以 `data-source-*` lineage 保存，不作为画面行为；`entrance_state/exit_state/continuity_in/continuity_out` 以来源元数据保存，状态比较与 carry/morph 未编译；`camera` 进入摄影机关键帧转换路径；`text`、`transition_out`、`human_locked`、`beats` 的语义不能整体视为 Film Runtime 已执行，其中 beats 的 layers/audio 有专门转换路径，其他 beat 语义仅保留 lineage。
- `Layer`：支持的 video/image 层进入 Film clip；`id/clip_id/track_id` 用于身份与轨道来源；`speed/source_in/source_out/fit_mode/transform/keyframes/mask` 仅在对应类型、通道和校验白名单内执行；`motion_recipe_id/motion_version/motion_instance` 保留动效 lineage；`animation` 明确告警且不执行；`text`、不支持的 LayerType 及其字段不得被推断为可渲染。
- `AudioTrack`：支持的类型、asset、时间、speed（仅 original）、volume、muted、fadeIn/fadeOut 和受支持的 keyframes 进入音频片段/混音路径；`ducking` 当前仅产生未迁移 warning，不能宣称自动压低已执行。`follows_scene_video`、`linked_visual_overlay_id` 当前未建立 Film Runtime 联动约束，迁移时告警并保留在源快照。
- `AudioLane` / `VisualTrack`：ID/name/order 用于物理轨道与排序；locked 执行轨道锁定；audio muted 执行静音；hidden 仅保留折叠 provenance，不等于隐藏画面或静音。
- `Beat`：layers/audio 进入专门迁移逻辑，并将 beat IDs/roles/semantic anchors 作为 clip provenance；Beat 的 role/anchors 本身不生成独立的播放行为。
- `SubtitleTrack`：cue 的 start/end/text 进入字幕轨道；`mode/source/style_id` 不完整执行，非默认来源/模式及 style_id 会产生 warning。
- `VisualOverlayClip`：支持的视频/图片 overlay 的素材、时间、位置尺寸、opacity、z-index、fit_mode、presentation_mode 和受支持 transform 进入独立轨道；source_audio_enabled 与 linked original audio 有互斥/匹配校验；其他字段需以专门迁移测试和 warning 为准。
- `WatermarkLayer` / `SubtitleStyle`：支持的文本/图片水印及字幕样式子集进入渲染属性；不能把所有 kind、字体资源、动画或样式字段视为无损。

嵌套模型库存（用于防止遗漏）：`SceneIR`、`Layer`、`Selection`、`TransitionOut`、`AudioTrack`、`AudioLane`、`Beat`、`SemanticAnchor`、`SubtitleTrack`、`SubtitleCue`、`SubtitleStyle`、`VisualTrack`、`VisualOverlayClip`、`WatermarkLayer`、`AssetRef`、`Variant`、`ProjectSpec`、`ReferenceGraph`。这份名单只声明审计对象，不代表这些模型的所有字段都已映射；未在上文逐项说明的字段继续按 UNKNOWN 处理。

## 审计范围限制

这是**顶层字段库存与处置审计 + 关键嵌套语义边界说明**，不是所有嵌套 Pydantic 字段、所有 LayerType、所有渲染参数的穷尽式逐字段审计。`AssetRef` 元数据、`Selection`、`TransitionOut`、`SemanticAnchor`、`ReferenceGraph`、CameraTrack/Transform/KeyframeTrack/Mask/ContinuityLink 的每个子字段仍需逐项登记和测试。任一未列明的嵌套字段仍是 UNKNOWN；不得据此宣称“CompositionIR 全量无损迁移”。

自动完整性检查：`tests/test_composition_ir_migration_field_audit.py` 会将当前 CompositionIR 顶层模型字段与本表逐项比对；新增字段未登记时测试失败。

## 嵌套字段逐字段库存（运行时模型递归）

以下库存从 `CompositionIR` 出发，递归遍历 Pydantic `BaseModel` 字段注解，包含 `models.py` 与 `visual_semantics.py` 中可达的嵌套模型。每一行对应一个真实模型字段。**当前统一标记 UNKNOWN 是有意的：它表示字段尚未完成逐项迁移证据核验，不是已迁移，也不是已归档。** 后续只有补齐迁移代码位置、FilmDocument/Runtime 落点和针对性行为测试后，才能改为 MAPPED、ARCHIVED_ONLY 或 REJECTED。

| 嵌套模型字段 | 处置 | 核验要求 |
|---|---|---|
| `AssetRef.description` | ARCHIVED_ONLY | 被引用素材写入 `data-source-asset-metadata`，仅作来源描述，不参与渲染；由资产元数据迁移测试验证。 |
| `AssetRef.duration` | ARCHIVED_ONLY | 被引用素材写入 `data-source-asset-metadata`，不覆盖 Layer/AudioTrack 的时间线时长。 |
| `AssetRef.file` | MAPPED | 当项目 manifest 未提供该 ID 的 file 时作为回退路径解析为 clip src；测试验证来源 AssetRef 可独立解析。 |
| `AssetRef.fps` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`；不改变项目 FilmDocument fps 或播放速率。 |
| `AssetRef.height` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`，不直接决定 clip 几何尺寸。 |
| `AssetRef.id` | MAPPED | 用于资产 ID 查找、clip `data-asset-id` 关联及 file 回退解析；有引用素材迁移测试。 |
| `AssetRef.keywords` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`，不作为画面内容或检索行为执行。 |
| `AssetRef.license_note` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata` 供版权追溯，不代表已执行授权校验。 |
| `AssetRef.prompt` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata` 供生成来源追溯，不会触发再次生成。 |
| `AssetRef.provenance` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`；仅保留来源结构，不作为运行时行为。 |
| `AssetRef.rights_status` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`；状态仅作追溯，迁移不会据此替代版权审核。 |
| `AssetRef.source` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`，不改变素材 URI 的信任边界或来源类型。 |
| `AssetRef.type` | MAPPED | 写入 `data-source-asset-metadata`；当 VisualOverlayClip 未声明 type/kind 时，作为叠加媒体类型推断的后备来源；主场景 Layer 类型仍由 Layer.type 决定。 |
| `AssetRef.width` | ARCHIVED_ONLY | 写入 `data-source-asset-metadata`，不直接决定 clip 宽度或 transform。 |
| `AudioLane.hidden` | ARCHIVED_ONLY | hidden 仅写入 `data-source-track-hidden`，代表来源时间线折叠状态，不隐藏画面/静音；现有迁移测试验证。 |
| `AudioLane.id` | MAPPED | 用于 `AudioTrack.track_id` 关联并写入 clip `data-source-track-id`；无匹配 lane 时走默认标签/默认顺序。 |
| `AudioLane.locked` | MAPPED | 写入 Film 轨道 `attrs.locked`；现有迁移测试验证锁定属性。 |
| `AudioLane.muted` | MAPPED | 与片段 muted 合并为 Film clip `muted`；现有迁移测试验证轨级静音生效。 |
| `AudioLane.name` | MAPPED | 用作 Film 轨道 `data-label`；现有迁移测试验证自定义 lane 名称。 |
| `AudioLane.order` | MAPPED | 决定音频片段构建顺序；新增测试验证不同 lane 的 order 先于片段 start。 |
| `AudioTrack.asset_id` | MAPPED | 通过 manifest/AssetRef 解析 `src`，写入 `data-asset-id`；资源解析失败按迁移校验处理。 |
| `AudioTrack.ducking` | ARCHIVED_ONLY | 当前迁移明确发出 ducking 未迁移 warning；完整原值留在 source-composition 快照，不执行自动压低。 |
| `AudioTrack.duration` | MAPPED | 写入时间线 clip duration 与 source_out 默认计算；模型要求时长为正值。 |
| `AudioTrack.fadeIn` | MAPPED | 与 fadeOut 合并写入 Film clip `fade`；两者之和超时长时 warning 且不应用。 |
| `AudioTrack.fadeOut` | MAPPED | 与 fadeIn 合并写入 Film clip `fade`；两者之和超时长时 warning 且不应用。 |
| `AudioTrack.follows_scene_video` | ARCHIVED_ONLY | 当前迁移对 false 明确 warning，未将结构编辑跟随关系写入 Film Runtime；原始值由 source snapshot 保留。 |
| `AudioTrack.id` | MAPPED | 生成稳定 Film clip ID，并写入 `data-source-id`；无 ID 时使用确定性序号回退。 |
| `AudioTrack.keyframes` | MAPPED | 支持的音量关键帧转换为 `volume_envelope`；不支持/非法关键帧产生 warning，不承诺完整曲线语义。 |
| `AudioTrack.linked_visual_overlay_id` | ARCHIVED_ONLY | 当前迁移明确 warning，未建立 Film 音视频联动约束；原始关联值留在 source snapshot。 |
| `AudioTrack.muted` | MAPPED | 写入 Film clip `muted`；轨级 muted 同样可触发静音。 |
| `AudioTrack.source_in` | MAPPED | 写入媒体 `src#t` 源区间起点。 |
| `AudioTrack.source_out` | MAPPED | 写入媒体 `src#t` 源区间终点；缺省时按 source_in + duration × speed 计算。 |
| `AudioTrack.speed` | MAPPED | 仅 original 类型允许非 1 倍速并写入 Film clip `speed`；其他类型由模型校验拒绝。 |
| `AudioTrack.start` | MAPPED | 写入 Film clip `at` 时间线起点。 |
| `AudioTrack.track_id` | MAPPED | 关联 AudioLane，决定 label/order/locked/muted/hidden provenance；写入 `data-source-track-id`。 |
| `AudioTrack.type` | MAPPED | 写入 `data-role`，保留 narration/dialogue/original/bgm/sfx/ambience 混音语义。 |
| `AudioTrack.volume` | MAPPED | 写入 Film clip `volume`；BGM 默认音量仅在未显式指定 volume 时可被注册 style 覆盖。 |
| `Beat.audio` | MAPPED | 作为独立音频片段进入统一音频迁移路径；按内容签名与全局音频去重，ID 冲突但内容不同时保留独立片段；Beat ID/role/anchors 写入片段 provenance。测试覆盖去重、冲突保留、时间/音量和 beat lineage；不表示 Beat 音频有独立于 AudioTrack 的时间原点。 |
| `Beat.id` | ARCHIVED_ONLY | 不作为 Film Runtime 的独立对象或行为；作为 `data-source-beat-ids` provenance，并在音频 ID 冲突时参与确定性派生 ID。完整源值保留于 source-composition 快照。 |
| `Beat.layers` | MAPPED | 合并到 Scene 图层迁移：与 scene.layers 内容签名匹配时附加 beat provenance；不匹配时作为独立图层进入后续 video/image Film clip 转换。仅支持的图层类型/字段可执行，其他字段仍按 Layer 迁移 warning/边界处理。测试覆盖独立 overlay、时间、fit_mode 和 beat lineage。 |
| `Beat.role` | ARCHIVED_ONLY | 作为 `data-source-beat-roles` provenance 写入关联 Film clip；没有对应的 Film Runtime 渲染或编辑行为。 |
| `Beat.semantic_anchors` | ARCHIVED_ONLY | 作为 `data-source-semantic-anchors` provenance 写入关联 Film clip；不编译为连续性、镜头或渲染行为。 |
| `CameraConstraint.kind` | ARCHIVED_ONLY | 当前 2D FilmRuntime 不执行摄影机约束；CameraTrack 含 constraints 时整条摄影机轨不映射并产生 warning，原字段保留在 source-composition 快照。 |
| `CameraConstraint.target` | ARCHIVED_ONLY | 当前 2D FilmRuntime 不解析约束目标；constraints 非空即阻断该摄影机轨映射，目标值只随来源快照保留。 |
| `CameraKeyframe.at` | MAPPED | 线性正交俯视摄影机关键帧时间转换为每个 Film clip 的局部关键帧时间；`test_orthographic_camera_pan_maps_to_deterministic_film_keyframes` 验证。 |
| `CameraKeyframe.easing` | MAPPED | 仅 `linear` 被接受并写入 Film `camera_x/camera_y` 关键帧；非线性缓动整条 camera 迁移拒绝并 warning，不做曲线近似。 |
| `CameraKeyframe.position` | ARCHIVED_ONLY | 用于验证三维坐标、归一 x/y 和固定俯视方向；不直接写入 Film camera channel，z/方向语义留在来源快照。 |
| `CameraKeyframe.target` | MAPPED | 仅 target.x/target.y 映射为 Film `camera_x/camera_y` 线性关键帧；target.z 不映射。测试验证两轴数值与时间。 |
| `CameraState.position` | ARCHIVED_ONLY | 作为 entrance_state/exit_state 的嵌套 JSON 写入 `data-source-entrance-state` / `data-source-exit-state`；仅作摄影机状态追溯，未转换为 Film camera channel。 |
| `CameraState.target` | ARCHIVED_ONLY | 作为 entrance_state/exit_state 的嵌套 JSON 写入 `data-source-entrance-state` / `data-source-exit-state`；仅作摄影机目标追溯，未转换为 Film camera channel。 |
| `CameraTrack.constraints` | ARCHIVED_ONLY | 非空 constraints 会阻断整条 CameraTrack 的 2D 迁移并产生 warning；FilmRuntime 不执行约束，原值只在来源快照。 |
| `CameraTrack.follow_target` | ARCHIVED_ONLY | 非空 follow_target 会阻断整条 CameraTrack 的独立 2D 迁移并产生 warning；不伪造目标跟随行为。 |
| `CameraTrack.id` | ARCHIVED_ONLY | 用于迁移诊断/warning 标识，不作为 Film track 或 clip identity；来源快照保留原 ID。 |
| `CameraTrack.keyframes` | MAPPED | 单条、正交、固定俯视方向、线性缓动的关键帧轨映射为 Film `camera_x/camera_y` channels；多轨或不支持条件整轨 warning。 |
| `CameraTrack.projection` | ARCHIVED_ONLY | 仅作为迁移能力门槛：orthographic 才继续处理，perspective 产生明确 warning；投影模型本身未进入 2D FilmRuntime。 |
| `CompositionIR.active_variant_id` | MAPPED | 无 variants 时使用顶层 scenes/audio；单一 variant 可直接选用；存在多个 variants 且未提供 active_variant_id 时返回 `ACTIVE_VARIANT_REQUIRED`，显式 ID 不存在时返回 `ACTIVE_VARIANT_NOT_FOUND`，两者均禁止回退到顶层 scenes/audio。 |
| `CompositionIR.assets` | MAPPED | 被选中时间线引用的素材用于资源解析；未被引用但带来源/版权/提示词/描述元数据的素材只在源快照保留，不自动插入成片。 |
| `CompositionIR.audio` | MAPPED | 音频片段按类型、asset、时间及支持的参数转换；必须结合下方 audio_tracks 说明，不能推断原始分组完全保留。 |
| `CompositionIR.audio_tracks` | MAPPED | ID/name/order 参与轨道生成；locked 映射 Film 轨道锁定，muted 与片段 muted 合并为运行时静音；hidden 只保存折叠状态 provenance；多片段 lane 会拆成物理 Film 轨道，原分组关系不保留。 |
| `CompositionIR.camera_tracks` | MAPPED | 支持的摄影机关键帧转换为 Film transform channels；非法/不支持的关键帧和通道不应被视为已执行。 |
| `CompositionIR.continuity_links` | ARCHIVED_ONLY | 保存为 `data-source-continuity-links`；carry/morph 等动作尚未编译为播放行为。 |
| `CompositionIR.origin_template_id` | ARCHIVED_ONLY | 写入来源 lineage 属性，不反向修改模板，也不作为渲染行为。 |
| `CompositionIR.origin_template_version` | ARCHIVED_ONLY | 写入来源 lineage 属性，不反向修改模板，也不作为渲染行为。 |
| `CompositionIR.project` | MAPPED | width/height → Film stage；fps 校验后写入；duration 用于时间线范围。非法 fps 明确拒绝。 |
| `CompositionIR.references` | ARCHIVED_ONLY | ReferenceGraph 保存为 `data-source-reference-graph`；不会自动生成或插入未在时间线中的素材。 |
| `CompositionIR.scenes` | MAPPED | 主场景、支持的 video/image layers、选择区间及部分语义 lineage 转换；非 video/image 图层、未匹配 selection、部分层字段会告警/不迁移。 |
| `CompositionIR.style_contract` | MAPPED | motion_temper、safe_zone、部分 palette/typography 仅在白名单和条件满足时应用；未识别的调色板/字体字段仍只保留来源数据并告警。 |
| `CompositionIR.style_id` | MAPPED | 匹配到内置 Style Registry 时只执行已声明的字幕位置/字号、旧式 zoom 和 BGM 默认音量映射；未知自定义 style 仅保存来源属性并告警。 |
| `CompositionIR.subtitle_style` | MAPPED | 支持的字体、字号、颜色、描边、背景等转入字幕 HTML；非法值回退并告警；字体文件不随迁移打包。 |
| `CompositionIR.subtitle_track` | MAPPED | cue 时间与文本进入 Film 字幕轨道；style registry 引用、非 sentence mode、非 tts_word_boundary 来源等 lineage 不执行或不完整保留，发出 warning。 |
| `CompositionIR.variants` | MAPPED | 仅选中的 variant scenes/tracks/style 参与本次迁移；其他 variants 不并入最终时间线，完整原始 IR 由源快照保存。 |
| `CompositionIR.version` | ARCHIVED_ONLY | 作为 IR schema/source snapshot 版本参与解析与归档；当前没有对应的 Film Runtime 播放行为或 film.html 运行时字段。历史版本语义兼容仍需独立验证。 |
| `CompositionIR.visual_overlays` | MAPPED | video/image overlay 映射到独立物理轨道，处理时间、素材、位置、尺寸、opacity、z-order、transform 的支持子集；其他类型明确告警。 |
| `CompositionIR.visual_tracks` | MAPPED | 轨道顺序、名称、来源 ID 用于 overlay 排序和 provenance；locked 映射 Film 轨道锁定；hidden 是折叠状态，不是画面隐藏，原分组交互不迁移。 |
| `CompositionIR.watermarks` | MAPPED | 支持的水印素材/时间/展示参数映射；不支持的类型/参数须以迁移 warning 为准。 |
| `ContinuityLink.camera_path` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.from_scene_id` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.id` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.morph_path` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.source_anchor` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.source_layer_id` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.target_anchor` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.target_layer_id` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.to_scene_id` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `ContinuityLink.type` | ARCHIVED_ONLY | 完整值序列化到主画面轨道的 `data-source-continuity-links`，同时保留于 `migration/source-composition.json`；当前只用于来源追溯，carry/morph 等连续性动作未编译为 Film Runtime 行为。 |
| `Keyframe.at` | MAPPED | 转换为 Film clip 局部时间，超出片段时长的点产生 warning 并跳过；重复时间按运行时语义保留最后一个。变换/关键帧迁移测试验证。 |
| `Keyframe.easing` | MAPPED | 线性、ease_in、ease_out、ease_in_out 进入 Film keyframe channel；FilmRuntime 按时间采样执行。复杂/非法缓动仅在原始输入路径上按显式 warning 处理，不宣称无损。 |
| `Keyframe.value` | MAPPED | 通过属性白名单与数值/范围校验后写入 Film channel；volume、mask_path 走专用音量包络/蒙版通道，不混入普通视觉通道。 |
| `KeyframeTrack.keyframes` | MAPPED | 普通视觉属性转为 `data-film-keyframes`；volume 与 mask_path 分流至专用迁移路径。测试覆盖变换关键帧、蒙版关键帧和音量包络。 |
| `KeyframeTrack.property` | MAPPED | 通过属性白名单决定 Film 目标通道；x/y/scale/scale_x/scale_y/rotation/opacity/blur/camera_x/camera_y/camera_scale/offset_x/offset_y 为普通通道，volume/mask_path 专用分流。 |
| `Layer.animation` | REJECTED | Film Runtime 没有对应的旧式 animation 字段执行路径；迁移诊断将其列为 unsupported，原始值保留在 migration/source-composition.json，不伪造动画效果。 |
| `Layer.asset_id` | MAPPED | 经 asset_uri() 解析为实际媒体 src，同时写入 data-asset-id；可用资产目录元数据另存 data-source-asset-metadata。 |
| `Layer.clip_id` | ARCHIVED_ONLY | 仅在 Layer.id 缺失时作为 data-source-id 的来源身份回退值；不作为 Film clip ID（Film clip ID 由迁移器独立生成）。 |
| `Layer.duration` | MAPPED | 作为 Film clip duration，并参与 video 源区间与 speed 的一致性约束。 |
| `Layer.fit_mode` | MAPPED | 支持 cover/contain/fill/none/scale-down 并写入 object-fit；非法值发出迁移提示后回退 cover。 |
| `Layer.id` | ARCHIVED_ONLY | 优先写入 data-source-id 作为来源身份；Film clip ID 由迁移器生成，不复用来源 ID。 |
| `Layer.keyframes` | MAPPED | 按属性拆分并迁移可表达的视觉、音量与 mask_path 关键帧；不支持的属性逐项产生诊断，不静默伪造。 |
| `Layer.locked` | MAPPED | 为真时写入 data-clip-locked；Film Studio 以该属性阻止剪辑编辑。 |
| `Layer.mask` | MAPPED | 将 Runtime 可表达的静态蒙版和兼容的 mask_path 关键帧迁入 clip-path/data-film-mask-keyframes；反向动态蒙版及不兼容路径显式告警并保留源快照。 |
| `Layer.motion` | MAPPED | 仅支持 slow_zoom_in/slow_zoom_out/pan_left/pan_right 等已实现预设生成 Film channels；未知预设产生迁移提示。 |
| `Layer.motion_instance` | MAPPED | 与 motion_recipe_id/version 配合解析为 Film channels；原始实例参数同时保留在 data-motion-instance，未解析 recipe 会告警。 |
| `Layer.motion_recipe_id` | MAPPED | 作为 MotionRecipe 解析入口生成受支持的 Film channels，并写入 data-motion-recipe-id 供来源追溯；未知 recipe 告警。 |
| `Layer.motion_version` | MAPPED | 与 motion_recipe_id 一起固定 recipe 版本并参与解析，同时写入 data-motion-recipe-version。 |
| `Layer.source_in` | MAPPED | video 层作为媒体源起点写入 src 时间片段；image 层不消费该字段。 |
| `Layer.source_out` | MAPPED | video 层作为媒体源终点写入 src 时间片段；image 层不消费该字段。 |
| `Layer.speed` | MAPPED | video 层写入 speed 并用于源时间区间换算；image 层固定按 1.0 处理。 |
| `Layer.start` | MAPPED | 与场景起点相加后写入 Film clip at，成为项目时间线位置。 |
| `Layer.text` | ARCHIVED_ONLY | 当前迁移路径不把 Layer.text 转成字幕或文字图层；原始值只在 migration/source-composition.json 中保留。 |
| `Layer.track_id` | ARCHIVED_ONLY | 写入 data-source-track-id 供来源轨道追溯；Film 轨道由迁移器按实际时间冲突重新分配。 |
| `Layer.transform` | MAPPED | 经 _film_transform_metadata() 归一化并写入 Film transform/style；与关键帧、CameraTrack 和蒙版坐标空间规则共同生效。 |
| `Layer.type` | MAPPED | video/image 分别迁移为 video/img clip；gap 有意跳过；其他类型不生成 Film clip 并产生不可无损迁移诊断。 |
| `Mask.animated` | ARCHIVED_ONLY | 仅作为来源标记保留在 source snapshot；Runtime 是否应用蒙版关键帧由可迁移的 mask_path 数据决定，不以该布尔值创建动画。 |
| `Mask.feather` | ARCHIVED_ONLY | CSS clip-path 不支持该羽化语义；feather 大于 0 时产生 warning，完整值保留在 source snapshot。 |
| `Mask.geometry` | MAPPED | polygon/path 的归一顶点转换为 CSS polygon；rectangle/ellipse 使用 x/y/width/height，path 的 Bezier/开放路径语义不在当前 schema。 |
| `Mask.height` | MAPPED | 作为 rectangle/ellipse 的归一高度参与 CSS clip-path 几何计算；非法值拒绝该蒙版。 |
| `Mask.id` | ARCHIVED_ONLY | 仅保留在 migration/source-composition.json；Film Runtime 不创建独立 Mask 对象或 ID。 |
| `Mask.inverted` | ARCHIVED_ONLY | 反向蒙版不能由当前 CSS clip-path 无损表达；产生 warning，不应用为正向蒙版，完整值保留在 source snapshot。 |
| `Mask.keyframes` | MAPPED | 仅支持有效 mask_path 归一多边形关键帧进入 data-film-mask-keyframes；顶点数不一致、越界或不支持属性产生 warning 并拒绝相应动画。 |
| `Mask.opacity` | ARCHIVED_ONLY | 蒙版独立 opacity 不映射为片段 opacity；小于 1 时产生 warning，原值保留在 source snapshot。 |
| `Mask.shape` | MAPPED | rectangle/ellipse/polygon 及归一顶点 path 转为 CSS clip-path；rounded_rect 因缺少圆角半径拒绝映射，未知形状产生 warning。 |
| `Mask.width` | MAPPED | 作为 rectangle/ellipse 的归一宽度参与 CSS clip-path 几何计算；非法值拒绝该蒙版。 |
| `Mask.x` | MAPPED | 作为 rectangle/ellipse 的归一水平中心参与 CSS clip-path 几何计算。 |
| `Mask.y` | MAPPED | 作为 rectangle/ellipse 的归一垂直中心参与 CSS clip-path 几何计算。 |
| `ProjectSpec.duration` | MAPPED | 作为 FilmDocument 初始时间范围；选中 Variant 时以其最晚场景/音轨结束时间覆盖，并在与声明时长不一致时 warning。 |
| `ProjectSpec.fps` | MAPPED | 经项目帧率校验后写入 FilmDocument fps；非法值拒绝迁移，不静默回退。 |
| `ProjectSpec.height` | MAPPED | 写入 FilmDocument stage 高度，参与 transform/蒙版等画幅坐标换算。 |
| `ProjectSpec.width` | MAPPED | 写入 FilmDocument stage 宽度，参与 transform/蒙版等画幅坐标换算。 |
| `Reference.id` | ARCHIVED_ONLY | 随完整 ReferenceGraph 序列化到最终轨道 data-source-reference-graph；不作为 Film clip/asset ID。 |
| `Reference.note` | ARCHIVED_ONLY | 随 ReferenceGraph 原样序列化到 data-source-reference-graph；仅供来源说明，不驱动渲染。 |
| `Reference.ref_type` | ARCHIVED_ONLY | 随 ReferenceGraph 原样序列化到 data-source-reference-graph；引用类型不会自动触发素材生成或时间线插入。 |
| `Reference.role` | ARCHIVED_ONLY | 随 ReferenceGraph 原样序列化到 data-source-reference-graph；语义角色不驱动 Film Runtime。 |
| `Reference.target_artifact_id` | ARCHIVED_ONLY | 随 ReferenceGraph 原样序列化到 data-source-reference-graph；不自动恢复/插入 Artifact。 |
| `Reference.target_asset_id` | ARCHIVED_ONLY | 随 ReferenceGraph 原样序列化到 data-source-reference-graph；不会自动生成或插入未被时间线引用的素材。 |
| `ReferenceGraph.references` | ARCHIVED_ONLY | 完整引用数组序列化到最终轨道 data-source-reference-graph；ReferenceGraph 不参与 Film Runtime 的素材生成、定位或渲染。 |
| `SceneIR.beats` | MAPPED | Beat 图层与音频进入专用迁移路径；beat ID/role/semantic anchors 写入片段 provenance；未产生独立播放行为的标签仍只作来源元数据。 |
| `SceneIR.camera` | MAPPED | 场景内摄影机关键帧在满足正交 2D、固定俯视方向、linear easing 等条件时转换为 Film camera_x/camera_y channels；不支持条件产生 warning。 |
| `SceneIR.continuity_in` | ARCHIVED_ONLY | 完整值写入 clip 的 data-source-continuity-in；carry/morph 等连续性动作尚未编译为 Film Runtime 行为。 |
| `SceneIR.continuity_out` | ARCHIVED_ONLY | 完整值写入 clip 的 data-source-continuity-out；carry/morph 等连续性动作尚未编译为 Film Runtime 行为。 |
| `SceneIR.duration` | MAPPED | 不裁剪单个 Film clip；但所选 Variant 的项目总时长以场景 start + duration 与音轨结束时间计算，超出 project.duration 时产生 warning。 |
| `SceneIR.entrance_state` | ARCHIVED_ONLY | 完整值写入 clip 的 data-source-entrance-state；可见层、锚点、摄影机和空间图不驱动 Film Runtime 状态切换。 |
| `SceneIR.exit_state` | ARCHIVED_ONLY | 完整值写入 clip 的 data-source-exit-state；可见层、锚点、摄影机和空间图不驱动 Film Runtime 状态切换。 |
| `SceneIR.human_locked` | ARCHIVED_ONLY | 当前只在 migration/source-composition.json 保留；这是 AI 规划锁，不等同于 Layer.locked 的 Film 编辑锁，迁移时显式 warning。 |
| `SceneIR.id` | MAPPED | 用于生成稳定的 Film clip ID 前缀并写入 data-source-scene-id；FilmDocument 不创建独立 SceneIR 对象。 |
| `SceneIR.intent` | ARCHIVED_ONLY | 写入 data-source-scene-intent 供创作意图追溯；不作为渲染或自动规划行为执行。 |
| `SceneIR.layers` | MAPPED | 仅支持的 video/image Layer 子集生成 Film clips；gap 有意跳过，其他类型/不支持字段必须产生诊断，不能宣称无损。 |
| `SceneIR.origin_recipe_id` | ARCHIVED_ONLY | 写入 data-source-origin-recipe-id 供镜头配方来源追溯；不自动重新实例化配方。 |
| `SceneIR.origin_recipe_version` | ARCHIVED_ONLY | 写入 data-source-origin-recipe-version 固定来源版本；不自动重新实例化配方。 |
| `SceneIR.selected_candidate_id` | ARCHIVED_ONLY | 写入 data-source-selected-candidate-id 供候选来源追溯；不恢复候选集或执行候选选择。 |
| `SceneIR.selections` | ARCHIVED_ONLY | 与可渲染 Layer 时间相交的 Selection 原样写入 data-source-selections；不转换为剪辑边界/字幕，未匹配项留在源快照并产生 warning。 |
| `SceneIR.semantic_role` | ARCHIVED_ONLY | 写入 data-source-scene-semantic-role 供语义角色追溯；不驱动 Film Runtime 渲染。 |
| `SceneIR.start` | MAPPED | 与 Layer.start 相加后形成 Film clip at，成为项目时间线位置。 |
| `SceneIR.text` | ARCHIVED_ONLY | 脚本/旁白文本不自动生成字幕或配音；迁移时显式 warning，完整原文保留在 migration/source-composition.json。 |
| `SceneIR.transition_out` | ARCHIVED_ONLY | 当前未编译为 Film 转场；非默认转场或非零时长迁移时显式 warning，完整值保留在 migration/source-composition.json。 |
| `SceneIR.visual_intent` | ARCHIVED_ONLY | 写入 data-source-scene-visual-intent 供创作意图追溯；不作为渲染行为执行。 |
| `SceneState.anchor_layer_id` | ARCHIVED_ONLY | 随 SceneIR entrance_state/exit_state 原样序列化到对应 `data-source-*` 属性；未转成 Film Runtime 的锚点选择行为。 |
| `SceneState.camera` | ARCHIVED_ONLY | 随 SceneIR entrance_state/exit_state 原样序列化到对应 `data-source-*` 属性；嵌套 CameraState 不会驱动 Film 摄影机。 |
| `SceneState.spatial_map` | ARCHIVED_ONLY | 随 SceneIR entrance_state/exit_state 原样序列化到对应 `data-source-*` 属性；空间映射未进入 Film Runtime 连续性求解。 |
| `SceneState.visible_layers` | ARCHIVED_ONLY | 随 SceneIR entrance_state/exit_state 原样序列化到对应 `data-source-*` 属性；Film Runtime 不据此切换可见图层。 |
| `Selection.end` | ARCHIVED_ONLY | 作为完整来源选择区间的结束时间写入剪辑 `data-source-selections` JSON；Film Runtime 不将其转换为可执行关键帧或剪辑边界。 |
| `Selection.id` | ARCHIVED_ONLY | 作为来源选择锚点标识写入剪辑 `data-source-selections` JSON；不作为 Film clip ID 或运行时选择状态。 |
| `Selection.start` | ARCHIVED_ONLY | 作为完整来源选择区间的开始时间写入剪辑 `data-source-selections` JSON；不改变 Film clip 的时间位置。 |
| `Selection.text` | ARCHIVED_ONLY | 作为来源语义文本写入剪辑 `data-source-selections` JSON；不自动生成字幕或文字图层。 |
| `SemanticAnchor.id` | ARCHIVED_ONLY | 作为 Beat 来源语义锚点完整序列化到视频/音频片段的 `data-source-semantic-anchors` 属性；不参与 Film Runtime 的剪辑、定位或渲染。 |
| `SemanticAnchor.kind` | ARCHIVED_ONLY | 与 `id`、`label` 一并原样保留在 `data-source-semantic-anchors` JSON 中；仅作来源元数据，不执行语义解析。 |
| `SemanticAnchor.label` | ARCHIVED_ONLY | 与 `id`、`kind` 一并原样保留在 `data-source-semantic-anchors` JSON 中；不驱动 Film Runtime 展示或行为。 |
| `SpatialMap.layer_anchors` | ARCHIVED_ONLY | 随 SceneState 的 entrance_state/exit_state 原样序列化；仅保留 layer→anchor 来源关系，不执行空间布局或连续性求解。 |
| `StyleContract.motion_temper` | MAPPED | calm/energetic/cinematic 映射为受限 image_motion_intensity 并驱动支持的图层动效；未知值不猜测执行。 |
| `StyleContract.palette` | MAPPED | 仅在无显式 subtitle_style/注册风格时，将合法 #RRGGBB 的 subtitle/text/foreground 映射为字幕颜色；其他 palette token 只保存在来源数据并 warning。 |
| `StyleContract.safe_zone` | MAPPED | 经 0.5–1.0 校验后写入字幕 safe_zone margin；越界值 warning 且不应用。 |
| `StyleContract.style_id` | MAPPED | 匹配内置 Style Registry 时执行其白名单字幕/动效/BGM 默认值；未注册 ID 只保存在 data-source-style-id 并 warning。 |
| `StyleContract.typography` | MAPPED | 仅在无显式 subtitle_style/注册风格时，将白名单 font_family/font/font_size/subtitle_font_size 映射为字幕字体与字号；其他字段不执行。 |
| `SubtitleCue.end` | MAPPED | 写入字幕 cue 结束时间；Runtime 按 start ≤ t < end 决定可见区间。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleCue.start` | MAPPED | 写入字幕 cue 开始时间；Runtime 按 start ≤ t < end 决定可见区间。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleCue.text` | MAPPED | 写入字幕 cue 文本并由字幕页按当前时间渲染；不会从 SceneIR.text 自动补造 cue。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.alignment` | MAPPED | 映射为字幕 HTML text-align，非法值由模型校验拒绝。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.animation` | MAPPED | none/fade/pop_spring 映射为字幕页帧驱动动画；其他值由模型校验拒绝。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.background` | MAPPED | 合法 #RRGGBB[@alpha] 映射为字幕背景 CSS；非法格式不生成背景并产生迁移 warning。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.background_opacity` | MAPPED | 当 background 未显式携带 @alpha 时作为 rgba alpha；超出 0–1 由渲染校验拒绝。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.color` | MAPPED | 合法 #RRGGBB 映射为字幕文字颜色；非法颜色回退为默认白色并产生 warning。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.font` | MAPPED | 白名单字体名映射为字幕 HTML font-family；字体文件不随迁移打包，目标环境可能回退。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.max_width` | MAPPED | 映射为字幕容器宽度百分比；模型限制 0.3–1.0。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.mode` | ARCHIVED_ONLY | 当前字幕页不读取该样式字段决定 cue 粒度；word 模式迁移时显式 warning，仍按 cue 自身时间/文本渲染。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.position` | MAPPED | top/center/bottom 映射为字幕 HTML 定位及 safe-zone margin。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.size` | MAPPED | 按 54px 基准换算相对字号并限制最终字号 12–160px；越界会 warning/限制。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.stroke_color` | MAPPED | 合法 #RRGGBB 映射为字幕描边颜色；非法颜色回退默认黑色并产生 warning。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleStyle.stroke_width` | MAPPED | 映射为 -webkit-text-stroke 宽度，限制 0–12px。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleTrack.cues` | MAPPED | 每条 cue 的 start/end/text 进入字幕 HTML 帧驱动渲染。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleTrack.mode` | ARCHIVED_ONLY | 非 sentence 模式产生 warning；Runtime 不执行 mode 切换，按 cue 时间与文本逐条渲染。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleTrack.source` | ARCHIVED_ONLY | 非默认 tts_word_boundary 来源产生 warning；来源 lineage 不写入 Runtime，原值保留在 source snapshot。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `SubtitleTrack.style_id` | ARCHIVED_ONLY | 当前不解析 Subtitle Registry 引用；存在时产生 warning，原值保留在 source snapshot。；回归证据：`tests/test_film_subtitle_migration.py::test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings` |
| `Transform.anchor` | MAPPED | 通过 `_FILM_ANCHORS` 白名单并写入 `data-film-transform.anchor`，供 FilmRuntime 计算变换原点；变换迁移测试验证 center。 |
| `Transform.opacity` | MAPPED | 写入 `data-film-transform.opacity` 并作为可执行变换属性；变换迁移测试验证 0.8。 |
| `Transform.rotation` | MAPPED | 写入 `data-film-transform.rotation`，由 FilmRuntime 执行旋转；变换迁移测试验证 12 度。 |
| `Transform.scale` | MAPPED | 写入 `data-film-transform.scale` 并参与实际 clip 尺寸计算；变换迁移测试验证 1.25。 |
| `Transform.scale_x` | MAPPED | 写入 `data-film-transform.scale_x` 并执行非等比水平缩放；迁移测试验证 1.5，浏览器蒙版/变换测试验证运行时尺寸变化。 |
| `Transform.scale_y` | MAPPED | 写入 `data-film-transform.scale_y` 并执行非等比垂直缩放；迁移测试验证 0.75，浏览器蒙版/变换测试验证运行时尺寸变化。 |
| `Transform.x` | MAPPED | 写入归一化 `data-film-transform.x` 并由 FilmRuntime 映射到画布位置；变换迁移及浏览器运行时测试覆盖。 |
| `Transform.y` | MAPPED | 写入归一化 `data-film-transform.y` 并由 FilmRuntime 映射到画布位置；变换迁移及浏览器运行时测试覆盖。 |
| `TransitionOut.duration` | ARCHIVED_ONLY | 当前未写入 Film clip/track 转场行为；非零时长迁移时显式 warning，完整值保留在 migration/source-composition.json。 |
| `TransitionOut.type` | ARCHIVED_ONLY | 当前未编译为 Film 转场；非默认类型迁移时显式 warning，完整值保留在 migration/source-composition.json。 |
| `Variant.id` | MAPPED | 参与 active_variant_id 显式选择，并写入 clip data-source-variant-id；FilmDocument 只执行所选单一变体。 |
| `Variant.metadata` | ARCHIVED_ONLY | 写入最终轨道 data-source-variant-metadata；明确不作为渲染指令执行。 |
| `Variant.scenes` | MAPPED | 仅所选 Variant.scenes 替代顶层 scenes 进入场景/图层迁移；其他变体不合并，完整源数据保留在 source snapshot。 |
| `Variant.style` | MAPPED | 所选变体 style 的注册风格/白名单参数可执行；完整原始对象写入 data-source-variant-style，未知参数不猜测执行。 |
| `Variant.tracks` | MAPPED | 仅所选 Variant.tracks 替代顶层 audio 进入音频迁移；不与顶层音频自动合并。 |
| `Variant.version` | MAPPED | 写入片段 data-source-variant-version 作为来源版本标识；不作为 Runtime 播放行为。 |
| `VisualOverlayClip.asset_id` | MAPPED | 解析为 Film clip src 与 data-asset-id；资源不存在/类型不支持时按迁移错误或 warning 处理。 |
| `VisualOverlayClip.duration` | MAPPED | 写入 clip 时长及视频源区间换算；超出素材时长等问题由资源/时间校验处理。 |
| `VisualOverlayClip.fit_mode` | MAPPED | cover/contain 转为 clip object-fit 或 transform fit_mode；模型拒绝其他值。 |
| `VisualOverlayClip.height` | MAPPED | picture_in_picture/sticker 模式按归一高度换算为 stage 像素；full_frame 使用完整 stage 尺寸。 |
| `VisualOverlayClip.id` | MAPPED | 用于稳定生成 Film clip/track ID，并写入 data-source-id。 |
| `VisualOverlayClip.opacity` | MAPPED | 写入 clip opacity；存在 canonical transform 时与 transform.opacity 相乘。 |
| `VisualOverlayClip.presentation_mode` | MAPPED | full_frame 使用整画幅；picture_in_picture/sticker 使用归一位置/尺寸；模型拒绝未知模式。 |
| `VisualOverlayClip.source_audio_enabled` | MAPPED | 仅 video overlay 生效：true 保留源音轨，false 写入 muted；静态图片不适用并 warning。 |
| `VisualOverlayClip.source_audio_volume` | MAPPED | 仅 video overlay 生效并写入 volume；静态图片不适用且非默认值会 warning。 |
| `VisualOverlayClip.source_in` | MAPPED | 写入媒体 src 时间区间起点；视频按 speed 换算 source_out。 |
| `VisualOverlayClip.speed` | MAPPED | video overlay 写入 speed 并按 timeline duration 换算 source_out；image overlay 固定按 1 倍处理。 |
| `VisualOverlayClip.start` | MAPPED | 写入 Film clip at，决定项目时间线位置。 |
| `VisualOverlayClip.track_id` | MAPPED | 保留 data-source-track-id/order/name 并参与叠加层排序；每个 overlay 仍拆成独立物理 Film track，原组联动编辑不迁移。 |
| `VisualOverlayClip.transform` | MAPPED | full_frame canonical transform 经 _film_transform_metadata 转为 Film transform/style/channels；模型禁止非 full_frame 组合。 |
| `VisualOverlayClip.width` | MAPPED | picture_in_picture/sticker 模式按归一宽度换算为 stage 像素；full_frame 使用完整 stage 尺寸。 |
| `VisualOverlayClip.x` | MAPPED | picture_in_picture/sticker 模式按归一水平位置换算为 stage 像素；full_frame 忽略该定位并使用全画幅。 |
| `VisualOverlayClip.y` | MAPPED | picture_in_picture/sticker 模式按归一垂直位置换算为 stage 像素；full_frame 忽略该定位并使用全画幅。 |
| `VisualOverlayClip.z_index` | MAPPED | 作为片段本地 z-index 写入 style；跨物理轨道的最终 render z-index 按 source track order 与 authored z_index 重新排序。 |
| `VisualTrack.hidden` | ARCHIVED_ONLY | 源轨 hidden 表示时间线折叠，不隐藏画面；写入 `data-source-track-hidden` 并产生 warning。`tests/test_film_visual_track_migration.py::test_visual_source_track_order_identity_and_lock_are_preserved` 验证。 |
| `VisualTrack.id` | MAPPED | 通过 `VisualOverlayClip.track_id` 关联来源视觉轨道，写入 `data-source-track-id`；缺省/未知轨道走默认轨道回退。视觉轨道迁移测试验证。 |
| `VisualTrack.locked` | MAPPED | 写入生成的 Film overlay 轨道 `attrs.locked`，运行时阻止该轨道编辑；`test_visual_source_track_order_identity_and_lock_are_preserved` 验证。 |
| `VisualTrack.name` | MAPPED | 写入 Film overlay 轨道 `attrs.data-label`；视觉轨道迁移测试验证自定义名称。 |
| `VisualTrack.order` | MAPPED | 决定 overlay 轨道生成顺序，并写入来源顺序 provenance；视觉轨道迁移测试验证顺序与 `data-source-track-order`。 |
| `WatermarkLayer.anchor` | MAPPED | 九宫格 anchor 映射为水印 HTML 定位；不支持值产生 warning 并拒绝该水印。 |
| `WatermarkLayer.asset_id` | MAPPED | image/logo/svg 水印解析为项目媒体 URL 并写入来源资产元数据；text 水印不消费该字段。 |
| `WatermarkLayer.duration` | MAPPED | 写入水印 iframe 时间范围；None 表示从 start 持续到项目片尾，超出片尾会截短并 warning。 |
| `WatermarkLayer.id` | MAPPED | 用于生成稳定水印 clip/track ID，并写入 data-source-watermark-id。 |
| `WatermarkLayer.kind` | MAPPED | text 生成文本水印页；image/logo/svg 生成媒体水印；其他类型 warning 并拒绝。 |
| `WatermarkLayer.offset_x` | MAPPED | 按 anchor 方向转换为水印 HTML 像素偏移。 |
| `WatermarkLayer.offset_y` | MAPPED | 按 anchor 方向转换为水印 HTML 像素偏移。 |
| `WatermarkLayer.opacity` | MAPPED | 映射为水印 HTML opacity；超出 0–1 由模型/渲染校验拒绝。 |
| `WatermarkLayer.safe_area` | MAPPED | 按 anchor 方向参与水印内缩像素位置计算；限制在 0–200px。 |
| `WatermarkLayer.scale` | MAPPED | 映射为水印 HTML scale；大于 10 会限制并 warning；文本水印按 24px 基准字号换算并提示视觉复核。 |
| `WatermarkLayer.start` | MAPPED | 写入水印 iframe clip at；起点超出影片时长时 warning 并拒绝该水印。 |
| `WatermarkLayer.text` | MAPPED | kind=text 时写入文本水印 HTML；图片类 kind 不消费该字段。 |
