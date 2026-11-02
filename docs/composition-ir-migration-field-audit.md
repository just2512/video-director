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
| `version` | MAPPED | 参与模型版本解析与源快照；不代表所有历史版本的语义兼容均已证明。 |
| `project` | MAPPED | width/height → Film stage；fps 校验后写入；duration 用于时间线范围。非法 fps 明确拒绝。 |
| `style_id` | MAPPED | 匹配到内置 Style Registry 时只执行已声明的字幕位置/字号、旧式 zoom 和 BGM 默认音量映射；未知自定义 style 仅保存来源属性并告警。 |
| `scenes` | MAPPED | 主场景、支持的 video/image layers、选择区间及部分语义 lineage 转换；非 video/image 图层、未匹配 selection、部分层字段会告警/不迁移。 |
| `audio` | MAPPED | 音频片段按类型、asset、时间及支持的参数转换；必须结合下方 audio_tracks 说明，不能推断原始分组完全保留。 |
| `audio_tracks` | MAPPED | 轨道 ID/name/order 参与来源映射；hidden 只保存折叠状态 provenance；多片段 lane 会拆成物理 Film 轨道，原分组关系不保留。 |
| `watermarks` | MAPPED | 支持的水印素材/时间/展示参数映射；不支持的类型/参数须以迁移 warning 为准。 |
| `visual_tracks` | MAPPED | 轨道顺序、名称、来源 ID 用于 overlay 排序和 provenance；hidden 是折叠状态，不是画面隐藏，原分组交互不迁移。 |
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

## 审计范围限制

这是**顶层字段库存与处置审计**，不是所有嵌套 Pydantic 字段、所有 LayerType、所有渲染参数的穷尽式审计。嵌套字段需要按 SceneIR/Layer/AudioTrack/Overlay/CameraTrack/SubtitleTrack 分域继续扩展测试。任一未列明的嵌套字段仍是 UNKNOWN；不得据此宣称“CompositionIR 全量无损迁移”。

自动完整性检查：`tests/test_composition_ir_migration_field_audit.py` 会将当前 CompositionIR 顶层模型字段与本表逐项比对；新增字段未登记时测试失败。
