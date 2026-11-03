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
| `active_variant_id` | MAPPED | 只选择当前有效 variant；无有效 ID 时按顶层 scenes/audio 迁移并告警，不合并候选变体。 |
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
- `AudioTrack`：支持的类型、asset、时间、speed（仅 original）、volume、muted、fadeIn/fadeOut、ducking 和 keyframes 进入音频片段/混音路径；`follows_scene_video`、`linked_visual_overlay_id` 仅在对应结构编辑/关联约束中有意义，不能泛化为所有音频的跟随行为。
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
| `AssetRef.description` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.file` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.fps` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.height` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.keywords` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.license_note` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.prompt` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.provenance` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.rights_status` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.source` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AssetRef.width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.hidden` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.locked` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.muted` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.name` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioLane.order` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.asset_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.ducking` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.fadeIn` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.fadeOut` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.follows_scene_video` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.keyframes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.linked_visual_overlay_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.muted` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.source_in` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.source_out` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.speed` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.track_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `AudioTrack.volume` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Beat.audio` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Beat.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Beat.layers` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Beat.role` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Beat.semantic_anchors` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraConstraint.kind` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraConstraint.target` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraKeyframe.at` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraKeyframe.easing` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraKeyframe.position` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraKeyframe.target` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraState.position` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraState.target` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraTrack.constraints` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraTrack.follow_target` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraTrack.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraTrack.keyframes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CameraTrack.projection` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.active_variant_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.assets` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.audio` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.audio_tracks` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.camera_tracks` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.continuity_links` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.origin_template_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.origin_template_version` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.project` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.references` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.scenes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.style_contract` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.style_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.subtitle_style` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.subtitle_track` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.variants` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.version` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.visual_overlays` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.visual_tracks` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `CompositionIR.watermarks` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.camera_path` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.from_scene_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.morph_path` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.source_anchor` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.source_layer_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.target_anchor` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.target_layer_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.to_scene_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ContinuityLink.type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Keyframe.at` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Keyframe.easing` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Keyframe.value` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `KeyframeTrack.keyframes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `KeyframeTrack.property` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.animation` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.asset_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.clip_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.fit_mode` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.keyframes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.locked` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.mask` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.motion` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.motion_instance` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.motion_recipe_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.motion_version` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.source_in` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.source_out` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.speed` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.text` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.track_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.transform` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Layer.type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.animated` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.feather` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.geometry` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.height` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.inverted` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.keyframes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.opacity` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.shape` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.x` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Mask.y` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ProjectSpec.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ProjectSpec.fps` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ProjectSpec.height` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ProjectSpec.width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.note` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.ref_type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.role` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.target_artifact_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Reference.target_asset_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `ReferenceGraph.references` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.beats` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.camera` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.continuity_in` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.continuity_out` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.entrance_state` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.exit_state` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.human_locked` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.intent` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.layers` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.origin_recipe_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.origin_recipe_version` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.selected_candidate_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.selections` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.semantic_role` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.text` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.transition_out` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneIR.visual_intent` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneState.anchor_layer_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneState.camera` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneState.spatial_map` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SceneState.visible_layers` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Selection.end` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Selection.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Selection.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Selection.text` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SemanticAnchor.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SemanticAnchor.kind` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SemanticAnchor.label` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SpatialMap.layer_anchors` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `StyleContract.motion_temper` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `StyleContract.palette` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `StyleContract.safe_zone` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `StyleContract.style_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `StyleContract.typography` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleCue.end` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleCue.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleCue.text` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.alignment` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.animation` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.background` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.background_opacity` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.color` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.font` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.max_width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.mode` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.position` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.size` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.stroke_color` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleStyle.stroke_width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleTrack.cues` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleTrack.mode` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleTrack.source` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `SubtitleTrack.style_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.anchor` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.opacity` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.rotation` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.scale` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.scale_x` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.scale_y` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.x` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Transform.y` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `TransitionOut.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `TransitionOut.type` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.metadata` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.scenes` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.style` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.tracks` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `Variant.version` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.asset_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.fit_mode` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.height` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.opacity` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.presentation_mode` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.source_audio_enabled` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.source_audio_volume` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.source_in` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.speed` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.track_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.transform` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.width` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.x` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.y` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualOverlayClip.z_index` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualTrack.hidden` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualTrack.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualTrack.locked` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualTrack.name` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `VisualTrack.order` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.anchor` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.asset_id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.duration` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.id` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.kind` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.offset_x` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.offset_y` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.opacity` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.safe_area` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.scale` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.start` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
| `WatermarkLayer.text` | UNKNOWN | 待逐字段核验迁移实现、运行时行为及回归测试。 |
