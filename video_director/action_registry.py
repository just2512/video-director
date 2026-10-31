"""最终方案 §13/§14/§62/§63/§64：Action Registry —— 用户行为的唯一注册表。

三层关系（§14）：
    User / AI → Action → Action Resolver → Capability → Command → CompositionIR

Action 是「用户理解的事情」（保留原声/加 Logo/换素材），Capability 是系统
能力（audio.original.preserve），Command 才修改 Composition（set_original_audio）。
UI 和 AI 必须走完全相同的 Action Resolver（§14 结论）。

§13 Action 结构：id/label/aliases/targets/allowed_layer_types/payload/
    preconditions/effects/undo/preview。
§63 Availability：所有 Action 必须返回 available/reason/next_actions——
    用户看到的不是灰色按钮，而是「加 Logo——当前没有 Logo [上传]」。
§64 商业安全进 Action Context：provider 的 commercial_policy/api_limit/bulk_query
    由系统告诉 Director，Director 不自行判断法律问题。
§62 /api/actions 按上下文返回 {project:[...], scene:[...], layer.video:[...]}。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger


class ActionSpec:
    """§13 单条 Action 契约。

    ui 字段（方案 §58/§56）：非空 = UI 交互型动作（打开 Picker/Modal、
    试听播放、切换视图）——前端 dispatcher 在本地执行 UI 行为，不进后端
    /api/action/execute。这类动作的业务效果（若产生）仍由打开的 UI 组件
    内的 data-action 按钮走后端执行链。ui 为空 = 业务动作，唯一通路
    /api/action/execute → Resolver → Capability → Command。
    """

    __slots__ = ("id", "label", "aliases", "targets", "allowed_layer_types",
                 "capability", "command", "payload_defaults", "preconditions",
                 "effects", "undo", "preview", "ui")

    def __init__(self, id: str, label: str, capability: str, command: str,
                 targets: List[str], aliases: Optional[List[str]] = None,
                 allowed_layer_types: Optional[List[str]] = None,
                 payload_defaults: Optional[dict] = None,
                 preconditions: Optional[List[str]] = None,
                 effects: Optional[List[str]] = None,
                 undo: bool = True, preview: bool = False,
                 ui: str = ""):
        self.id = id
        self.label = label
        self.aliases = aliases or []
        self.targets = targets
        self.allowed_layer_types = allowed_layer_types
        self.capability = capability
        self.command = command
        self.payload_defaults = payload_defaults or {}
        self.preconditions = preconditions or []
        self.effects = effects or []
        self.undo = undo
        self.preview = preview
        self.ui = ui

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "aliases": self.aliases,
            "targets": self.targets, "allowed_layer_types": self.allowed_layer_types,
            "capability": self.capability, "command": self.command,
            "payload_defaults": self.payload_defaults, "preconditions": self.preconditions,
            "effects": self.effects, "undo": self.undo, "preview": self.preview,
            "ui": self.ui,
        }


#: §13 Action 表（id ↔ §五十七 用户命令逐一对应；capability ↔ manifest 现有 id）
_ACTIONS: List[ActionSpec] = [
    # ---- 声音（§五「保留原声」族 / §37 Audio Bus）----
    ActionSpec("audio.preserve", "保留原声", "audio.original.preserve", "set_original_audio",
               targets=["project", "scene", "layer", "multi_layer"],
               allowed_layer_types=["video"],
               aliases=["保留原声", "保留现场声音", "不要静音", "留住现场声音"],
               payload_defaults={"mode": "preserve"},
               preconditions=["target_has_audio"], effects=["original_audio_track"]),
    ActionSpec("audio.mute_original", "静音原声", "audio.original.mute", "set_original_audio",
               targets=["project", "scene", "layer", "multi_layer"],
               allowed_layer_types=["video"],
               aliases=["去掉原声", "静音原声", "关掉现场声音"],
               payload_defaults={"mode": "mute"},
               preconditions=["target_has_audio"], effects=["original_audio_track"]),
    ActionSpec("audio.original_volume", "调原声音量", "audio.original.volume",
               "set_original_audio_volume",
               targets=["project", "scene", "layer", "multi_layer"],
               allowed_layer_types=["video"],
               aliases=["原声音量", "压低原声", "原声小一点"],
               payload_defaults={"volume": 0.2},
               preconditions=["target_has_audio"], effects=["original_audio_track"]),
    ActionSpec("audio.duck_original", "旁白时压原声", "audio.original.duck",
               "set_original_audio",
               targets=["project", "scene", "layer", "multi_layer"],
               allowed_layer_types=["video"],
               aliases=["闪避", "旁白时压低原声"],
               payload_defaults={"mode": "preserve", "duckAgainst": "narration"}),
    ActionSpec("audio.volume", "调音轨音量", "music.volume", "set_audio_volume",
               targets=["project", "layer"],
               aliases=["音量", "音效音量", "对白音量"],
               payload_defaults={"volume": 0.3}),
    ActionSpec("audio.mute_track", "静音音轨", "music.volume", "set_audio_mute",
               targets=["project"],
               aliases=["静音 BGM", "音轨静音", "BGM 静音"],
               payload_defaults={"track_type": "bgm", "muted": True}),
    ActionSpec("audio.fade", "轨道淡入淡出", "music.volume", "set_audio_fade",
               targets=["project"],
               aliases=["淡入", "淡出", "音乐淡入"],
               payload_defaults={}),
    ActionSpec("audio.mix", "整体调音（Audio Bus）", "music.volume", "audio_mix",
               targets=["project"],
               aliases=["混音", "整体声音调整", "原声 20% BGM 10%"],
               payload_defaults={},
               effects=["audio_bus"]),
    # capability 必须是 tts.set_voice（写 params_json.voice）——曾误指只读的
    # tts.list_voices，导致「应用音色」返回 ok=True 却什么都没落盘（假成功）。
    ActionSpec("voice.change", "换配音", "tts.set_voice", "set_narration_voice",
               targets=["project"],
               aliases=["换音色", "换个声音", "配音换成男声", "重新配音", "改配音"]),
    ActionSpec("voice.preview", "试听音色", "tts.preview_voice", "preview_voice",
               targets=["project"], undo=False, preview=True,
               aliases=["试听"]),
    ActionSpec("edit_voice", "调配音参数", "subtitle.update_timing", "set_audio_volume",
               targets=["project"],
               aliases=["语速快一点", "语速慢一点", "配音音量"],
               payload_defaults={"track_type": "narration", "volume": 1.0}),
    ActionSpec("edit_subtitles", "改字幕（统称）", "subtitle.update_style", "set_subtitle_style",
               targets=["project"],
               aliases=["字幕调整", "改字幕", "字幕设置"],
               payload_defaults={}),
    # ---- 字幕（§七）----
    ActionSpec("subtitle.generate", "生成字幕", "subtitle.generate", "set_subtitle_track",
               targets=["project"],
               aliases=["加字幕", "出字幕", "生成字幕", "自动字幕"],
               payload_defaults={"mode": "word"}),
    ActionSpec("subtitle.style", "改字幕样式", "subtitle.update_style", "set_subtitle_style",
               targets=["project"],
               aliases=["字幕白字黑边", "字幕大一点", "字幕小一点", "字幕改颜色", "逐词字幕"],
               payload_defaults={}),
    # ---- 音乐（§36）----
    ActionSpec("music.add", "加背景音乐", "music.add", "set_audio_track",
               targets=["project"],
               aliases=["加 BGM", "配乐", "来点音乐", "背景音乐"],
               payload_defaults={"type": "bgm"}),
    ActionSpec("music.remove", "去背景音乐", "music.remove", "remove_audio_track",
               targets=["project"],
               aliases=["去掉 BGM", "不要背景音乐", "关掉音乐"],
               payload_defaults={"type": "bgm"}),
    ActionSpec("music.volume", "调音乐音量", "music.volume", "set_audio_volume",
               targets=["project"],
               aliases=["BGM 小一点", "音乐调小", "背景音乐小一点", "声音小一点"],
               payload_defaults={"type": "bgm", "volume": 0.3}),
    ActionSpec("music.duck", "旁白时自动压低音乐", "music.duck", "set_audio_ducking",
               targets=["project"],
               aliases=["别盖住旁白", "音乐闪避"],
               payload_defaults={"type": "bgm", "ducking": True}),
    # ---- 水印 / 品牌叠加（§六 Project Overlay）----
    ActionSpec("watermark.add", "加 Logo/水印", "watermark.add", "set_watermark",
               targets=["project"],
               aliases=["加水印", "右下角加我的 Logo", "加品牌名", "放个 logo"],
               payload_defaults={"kind": "text", "anchor": "bottom_right", "opacity": 0.75}),
    ActionSpec("watermark.update", "改水印", "watermark.update", "set_watermark",
               targets=["project"],
               aliases=["Logo 透明一点", "水印再低一点", "换个位置"],
               preconditions=["has_watermark"]),
    ActionSpec("watermark.remove", "去水印", "watermark.remove", "remove_watermark",
               targets=["project"],
               aliases=["去掉水印", "移除 Logo"],
               preconditions=["has_watermark"]),
    # ---- 素材（§八/§九 Asset Picker 统一）----
    ActionSpec("creative.select_candidate", "采用创作方案", "creative.candidate.select", "set_scene_intent",
               targets=["scene"],
               aliases=["采用这个方案", "用这个创作方案", "采用候选方案"],
               preconditions=["has_selection"]),
    # ---- 镜头锁定（差距6：人工介入不被自动重算覆盖）----
    ActionSpec("scene.lock", "锁定镜头", "scene.lock", "set_scene_locked",
               targets=["scene"],
               aliases=["锁定这个镜头", "锁定镜头", "不要动这个镜头"],
               preconditions=["has_selection"]),
    ActionSpec("asset.replace", "换素材", "asset.replace", "replace_source",
               targets=["scene", "layer"],
               aliases=["换画面", "换个素材", "换镜头画面"],
               preconditions=["has_selection"]),
    ActionSpec("asset.search", "自动找素材", "asset.search", "search_and_adopt",
               targets=["scene", "layer", "project"],
               aliases=["自动匹配素材", "找素材", "帮我找画面"],
               undo=False),
    ActionSpec("asset.add_broll", "加素材层", "asset.add_to_timeline", "add_broll",
               targets=["scene"],
               aliases=["加 B-roll", "叠加画面", "加个画中画素材"]),
    # ---- 生成（§八 生成入 Scene→画面）----
    ActionSpec("generate.image", "AI 生成图片", "generation.image", "generate_media",
               targets=["scene", "layer"],
               aliases=["生成图片", "AI 画一张", "生成画面"]),
    ActionSpec("generate.video", "AI 生成视频", "generation.video", "generate_media",
               targets=["scene", "layer"],
               aliases=["生成视频", "AI 生成动态画面"]),
    ActionSpec("generate.sfx", "AI 生成音效", "generation.sfx", "generate_media",
               targets=["project"],
               aliases=["生成音效"]),
    # ---- 视觉编辑（§五 Layer 控制）----
    ActionSpec("visual.transform", "调位置/缩放/透明度", "layer.update", "set_visual_transform",
               targets=["layer"],
               aliases=["挪一下", "放大", "缩小", "透明一点", "调整位置"],
               preconditions=["has_selection"]),
    ActionSpec("visual.motion", "换动效", "motion.apply", "replace_motion",
               targets=["layer"],
               aliases=["加动效", "换个动效", "让它动起来"],
               preconditions=["has_selection"]),
    ActionSpec("visual.camera", "镜头运动", "camera.apply", "apply_camera",
               targets=["scene"],
               aliases=["镜头推进", "推镜", "拉远", "缓慢推进"],
               payload_defaults={"strength": 0.6}),
    ActionSpec("visual.pip", "设为画中画", "pip.configure", "configure_pip",
               targets=["layer"], allowed_layer_types=["video"],
               aliases=["画中画", "小窗", "放个小窗口"]),
    ActionSpec("visual.mask", "加蒙版", "mask.apply", "apply_mask",
               targets=["layer"],
               aliases=["蒙版", "遮罩", "只留中间"]),
    ActionSpec("visual.mask.remove", "移除蒙版", "mask.apply", "remove_mask",
               targets=["layer"],
               aliases=["去掉蒙版"],
               preconditions=["has_mask"]),
    ActionSpec("visual.keyframe_add", "打关键帧", "keyframe.add", "add_keyframe",
               targets=["layer"],
               aliases=["加关键帧", "打关键帧"]),
    ActionSpec("visual.lock", "锁定图层", "layer.update", "lock",
               targets=["layer"],
               aliases=["锁定", "批量锁定"]),
    ActionSpec("visual.keyframe_remove", "删关键帧", "keyframe.add", "remove_keyframe",
               targets=["layer"],
               aliases=["删除关键帧", "删帧"]),
    # ---- 时间线编辑 ----
    ActionSpec("timeline.trim", "裁剪", "timeline.trim", "trim",
               targets=["layer"], allowed_layer_types=["video"],
               aliases=["裁一下", "掐头去尾"],
               preconditions=["has_selection"]),
    ActionSpec("timeline.split", "拆分场景", "timeline.split", "split",
               targets=["scene"],
               aliases=["拆开", "切成两半"]),
    ActionSpec("ui.split_layer", "切分片段", "system.capabilities.list", "noop",
               targets=["project", "scene", "layer", "visual_overlay"], undo=False, ui="split_layer"),
    ActionSpec("ui.join_through_edits", "愈合切口", "system.capabilities.list", "noop",
               targets=["project", "scene", "layer", "visual_overlay"], undo=False, ui="join_through_edits"),
    ActionSpec("ui.lift_layer_to_playhead", "主画面片段移到播放头", "system.capabilities.list", "noop",
               targets=["project", "scene", "layer"], undo=False, ui="lift_layer_to_playhead"),
    ActionSpec("ui.add_edit_all_tracks", "添加编辑（全轨）", "system.capabilities.list", "noop",
               targets=["project", "scene", "layer"], undo=False, ui="add_edit_all_tracks"),
    ActionSpec("ui.edit_visual_overlay", "编辑画面片段", "system.capabilities.list", "noop",
               targets=["project", "visual_overlay"], undo=False, ui="edit_visual_overlay"),
    ActionSpec("timeline.delete", "删除", "timeline.delete", "delete",
               targets=["scene", "layer"],
               aliases=["删除场景", "移除图层", "删掉"],
               preconditions=["has_selection"]),
    ActionSpec("timeline.duplicate", "复制", "timeline.duplicate", "duplicate",
               targets=["scene", "layer"],
               aliases=["复制场景", "再要一个"],
               preconditions=["has_selection"]),
    ActionSpec("timeline.move", "移动位置", "timeline.move", "move",
               targets=["scene"],
               aliases=["挪到第三位", "换个顺序"],
               preconditions=["has_selection"]),
    ActionSpec("timeline.duration", "调时长", "layer.update", "set_duration",
               targets=["scene"],
               aliases=["长一点", "短一点", "时长改 5 秒"]),
    # ---- 多轨时间线与剪贴板（差距分析 L 项：Agent 与鼠标同命令集）----
    # 注册前前端已可用的编辑命令到 Action Registry——MCP/CLI 经 /api/action/execute
    # 走同一 execute_command，不再出现「鼠标能剪、Agent 报命令不支持」。
    ActionSpec("track.add_visual", "加画面轨", "timeline.track", "add_visual_track",
               targets=["project"], aliases=["新增画面轨", "加一条画面轨", "画中画轨"]),
    ActionSpec("track.add_audio", "加音轨", "timeline.track", "add_audio_track",
               targets=["project"], aliases=["新增音轨", "加一条音轨", "加音乐轨"]),
    ActionSpec("track.rename_visual", "重命名画面轨", "timeline.track", "rename_visual_track",
               targets=["project"], aliases=["画面轨改名"]),
    ActionSpec("track.rename_audio", "重命名音轨", "timeline.track", "rename_audio_track",
               targets=["project"], aliases=["音轨改名"]),
    ActionSpec("track.remove_visual", "删画面轨", "timeline.track", "remove_visual_track",
               targets=["project"], aliases=["删除画面轨"]),
    ActionSpec("track.remove_audio", "删音轨", "timeline.track", "remove_audio_track",
               targets=["project"], aliases=["删除音轨"]),
    ActionSpec("track.reorder_visual", "画面轨排序", "timeline.track", "reorder_visual_track",
               targets=["project"], aliases=["画面轨上移", "画面轨下移"]),
    ActionSpec("track.lock", "锁定/解锁轨道", "timeline.track", "set_track_locked",
               targets=["project"], aliases=["锁定轨道", "解锁轨道", "轨道上锁"]),
    ActionSpec("track.mute", "轨道静音", "timeline.track", "set_track_muted",
               targets=["project"], aliases=["静音整轨", "轨道静音", "取消轨道静音"]),
    ActionSpec("track.hidden", "隐藏/显示轨道", "timeline.track", "set_track_hidden",
               targets=["project"], aliases=["隐藏轨道", "显示轨道", "折叠轨道"]),
    ActionSpec("clip.move_visual", "移动画面片段", "timeline.clip", "move_visual_overlay",
               targets=["visual_overlay"], aliases=["画面片段移动", "挪动叠加片段"],
               preconditions=["has_selection"]),
    ActionSpec("clip.trim_visual", "裁剪画面片段", "timeline.clip", "trim_visual_overlay",
               targets=["visual_overlay"], aliases=["叠加片段裁剪", "画面片段掐头去尾"],
               preconditions=["has_selection"]),
    ActionSpec("clip.split_visual", "切分画面片段", "timeline.clip", "split_visual_overlay",
               targets=["visual_overlay"], aliases=["叠加片段切开"],
               preconditions=["has_selection"]),
    ActionSpec("clip.remove_visual", "删除画面片段", "timeline.clip", "remove_visual_overlay",
               targets=["visual_overlay"], aliases=["删除叠加片段", "移除画中画"],
               preconditions=["has_selection"]),
    ActionSpec("clip.set_visual", "编辑画面片段属性", "timeline.clip", "set_visual_overlay",
               targets=["visual_overlay"], aliases=["画面片段属性", "画中画参数"],
               preconditions=["has_selection"]),
    ActionSpec("clip.move_audio", "移动音频片段", "timeline.clip", "move_audio_clip",
               targets=["layer"], aliases=["音频片段移动", "挪动音效"],
               preconditions=["has_selection"]),
    ActionSpec("clip.trim_audio", "裁剪音频片段", "timeline.clip", "trim_audio_clip",
               targets=["layer"], aliases=["音频裁剪", "音乐掐头去尾"],
               preconditions=["has_selection"]),
    ActionSpec("clip.split_audio", "切分音频片段", "timeline.clip", "split_audio_clip",
               targets=["layer"], aliases=["音效切开", "音乐切分"],
               preconditions=["has_selection"]),
    ActionSpec("clip.remove_audio", "删除音频片段", "timeline.clip", "remove_audio_clip",
               targets=["layer"], aliases=["删除音效", "移除音频片段"],
               preconditions=["has_selection"]),
    ActionSpec("clip.audio_properties", "音频片段混音属性", "timeline.clip", "set_audio_clip_properties",
               targets=["layer"], aliases=["音量淡入淡出", "音频片段属性"],
               preconditions=["has_selection"]),
    ActionSpec("clip.add_audio_asset", "添加音频素材到音轨", "timeline.clip", "add_audio_asset_clip",
               targets=["project"], aliases=["加音乐", "加音效", "放一段配乐"]),
    ActionSpec("track.audio_volume", "轨级音量", "timeline.track", "set_audio_volume",
               targets=["project"], aliases=["背景音乐声音小点", "音乐音量"]),
    ActionSpec("track.audio_mute", "轨级静音", "timeline.track", "set_audio_mute",
               targets=["project"], aliases=["静音", "闭麦"]),
    ActionSpec("track.audio_fade", "轨级淡入淡出", "timeline.track", "set_audio_fade",
               targets=["project"], aliases=["淡入", "淡出"]),
    # ---- 镜头关系（§二 Scene Pair）----
    ActionSpec("relation.set", "设镜头关系", "visual.continuity.plan", "set_scene_continuity",
               targets=["scene_pair"],
               aliases=["连续推进", "元素延续", "形变", "硬切", "平移跟随"],
               preconditions=["has_selection"]),
    # ---- 交付 ----
    ActionSpec("render.draft", "渲染草稿", "render.draft", "render_draft",
               targets=["project"], undo=False,
               aliases=["出个草稿", "快速预览渲染"]),
    ActionSpec("render.final", "渲染成片", "render.final", "render_final",
               targets=["project"], undo=False,
               aliases=["渲染", "出成片", "导出"]),
    ActionSpec("verify.run", "检查", "composition.verify", "noop",
               targets=["project"], undo=False,
               aliases=["检查一下", "预检", "验证"]),
    # ---- §66 MPT 级 Action：内容 ----
    ActionSpec("content.generate_script", "主题生成脚本", "content.generate_script",
               "generate_script", targets=["project"], undo=False,
               aliases=["按主题写脚本", "生成文案"]),
    ActionSpec("content.rewrite_script", "改写脚本", "content.rewrite_script",
               "rewrite_script", targets=["project"], undo=False,
               aliases=["润色文案", "改写"]),
    # ---- §66 素材扩展 ----
    ActionSpec("asset.import_local", "导入本地素材", "asset.import_local", "noop",
               targets=["project", "scene"], undo=False,
               aliases=["上传素材", "导入素材"], ui="open_local_import"),
    ActionSpec("asset.search_stock", "搜图库", "stock.search", "noop",
               targets=["scene", "layer", "project"], undo=False,
               aliases=["搜 Pexels", "搜图库素材"], ui="open_stock_search"),
    ActionSpec("asset.download", "下载素材", "asset.resolve", "noop",
               targets=["scene", "layer"], undo=False,
               aliases=["下载采用"]),
    ActionSpec("visual.verify_asset", "视觉验证素材", "visual.qa", "noop",
               targets=["layer"], undo=False,
               preconditions=["has_selection"],
               aliases=["视觉验证", "检查画面", "验证素材"]),
    ActionSpec("visual.semantic_rerank", "按画面语义重排素材", "visual.semantic_rerank", "noop",
               targets=["scene"], undo=False, preconditions=["has_selection"],
               aliases=["按画面语义找素材", "语义重排素材"]),
    # ---- §66 配音扩展（§37 试听=UI 播放型）----
    ActionSpec("voice.preview_full", "完整旁白试听", "tts.preview_narration",
               "noop", targets=["project"], undo=False, preview=True,
               aliases=["完整试听"], ui="play_full_preview"),
    ActionSpec("voice.upload", "上传配音", "system.capabilities.list", "noop",
               targets=["project"], undo=False,
               aliases=["上传配音文件"], ui="open_voice_upload"),
    # ---- §66 字幕位置 ----
    ActionSpec("subtitle.position", "字幕位置", "subtitle.update_style",
               "set_subtitle_style",
               targets=["project"],
               aliases=["字幕放底部", "字幕放顶部", "字幕居中"],
               payload_defaults={}),
    # ---- §66 音乐扩展 ----
    ActionSpec("music.auto_match", "自动配乐", "music.match_video", "auto_match",
               targets=["project"], undo=False,
               aliases=["自动匹配 BGM", "配点音乐"]),
    ActionSpec("music.preview", "试听音乐", "music.preview", "noop",
               targets=["project"], undo=False, preview=True,
               aliases=["听一下音乐"], ui="play_music_preview"),
    # ---- §66 SFX ----
    ActionSpec("sfx.add", "加音效", "music.add", "set_audio_volume",
               targets=["scene"],
               aliases=["加音效", "来点音效"],
               payload_defaults={"track_type": "sfx", "volume": 0.6}),
    # ---- §66 工作流控制 ----
    ActionSpec("workflow.start", "开始创作", "system.capabilities.list", "noop",
               targets=["project"], undo=False,
               aliases=["开始生成", "开始创作"]),
    ActionSpec("workflow.resume", "继续流程", "system.capabilities.list", "noop",
               targets=["project"], undo=False,
               aliases=["继续渲染", "确认分镜"]),
    ActionSpec("workflow.retry", "重试流程", "system.capabilities.list", "noop",
               targets=["project"], undo=False,
               aliases=["重新执行", "再来一次"]),
    # ---- §66 批量 ----
    ActionSpec("batch.create", "批量创作", "system.capabilities.list", "noop",
               targets=["project"], undo=False,
               aliases=["多条视频", "批量生成"]),
    # ---- §66 交付元数据 ----
    # 2026-10-22 真实现：tool_executor._execute_delivery_metadata 读项目文案
    # → LLM 生成 title/description/tags → params_json.delivery_metadata。
    # 此前 capability 误指 system.capabilities.list（Agent 调它只拿到能力
    # 列表、假成功）——现改指同名真能力。
    ActionSpec("delivery.generate_metadata", "生成发布文案", "delivery.generate_metadata",
               "noop", targets=["project"], undo=False,
               aliases=["写标题", "生成描述", "生成标签"]),
    # ---- UI 交互型（§58：本地 UI 行为，无后端执行）----
    ActionSpec("ui.open_asset_picker", "换素材（打开选择器）", "asset.replace", "noop",
               targets=["scene", "layer"], undo=False,
               aliases=["换素材"], ui="open_asset_picker"),
    ActionSpec("ui.open_shot_picker", "换镜头（打开选择器）", "camera.apply", "noop",
               targets=["scene"], undo=False, ui="open_shot_picker"),
    ActionSpec("ui.open_motion_picker", "换动效（打开选择器）", "motion.apply", "noop",
               targets=["layer"], undo=False, ui="open_motion_picker"),
    ActionSpec("ui.open_voice_settings", "配音设置", "tts.list_voices", "noop",
               targets=["project"], undo=False, ui="open_voice_settings"),
    ActionSpec("ui.open_audio_bus", "整体调音", "music.volume", "noop",
               targets=["project"], undo=False, ui="open_audio_bus"),
    ActionSpec("ui.open_subtitle_style", "字幕样式", "subtitle.update_style", "noop",
               targets=["project"], undo=False, ui="open_subtitle_style"),
    ActionSpec("ui.generate_scene_visual", "AI 生成画面", "generation.image", "noop",
               targets=["scene"], undo=False, ui="generate_scene_visual"),
    ActionSpec("ui.edit_mask", "编辑蒙版", "mask.apply", "noop",
               targets=["layer"], undo=False, ui="edit_mask"),
    ActionSpec("ui.preview_draft", "预览草稿", "render.draft", "noop",
               targets=["project"], undo=False, ui="preview_draft"),
    ActionSpec("ui.render_draft", "渲染草稿任务", "render.draft", "noop",
               targets=["project"], undo=False, ui="render_draft"),
    ActionSpec("ui.undo", "撤销", "system.capabilities.list", "noop",
               targets=["project"], undo=False, ui="undo"),
    ActionSpec("ui.redo", "重做", "system.capabilities.list", "noop",
               targets=["project"], undo=False, ui="redo"),
    ActionSpec("ui.save", "保存", "system.capabilities.list", "noop",
               targets=["project"], undo=False, ui="save"),
    ActionSpec("ui.editor_session_snap", "时间线吸附", "system.capabilities.list", "noop",
               targets=["project"], undo=False, ui="editor_session_snap"),
    ActionSpec("ui.editor_session_box", "时间线框选", "system.capabilities.list", "noop",
               targets=["project"], undo=False, ui="editor_session_box"),
    ActionSpec("ui.move_prompt", "移动场景（输入位置）", "timeline.move", "noop",
               targets=["scene"], undo=False, ui="move_prompt"),
    ActionSpec("ui.add_broll_prompt", "加素材层（选择素材）", "asset.add_to_timeline",
               "noop", targets=["scene"], undo=False, ui="add_broll_prompt"),
    # ---- TalkCraft-style creative plan panel: pure UI entry actions ----
    ActionSpec("ui.creative_plan_toggle", "打开语义拍与分镜", "system.capabilities.list",
               "noop", targets=["project"], undo=False, ui="creative_plan_toggle"),
    ActionSpec("ui.creative_plan_reload", "刷新语义拍与分镜", "system.capabilities.list",
               "noop", targets=["project"], undo=False, ui="creative_plan_reload"),
    ActionSpec("ui.creative_plan_initialize", "生成创作计划草案", "system.capabilities.list",
               "noop", targets=["project"], undo=False, ui="creative_plan_initialize"),
    ActionSpec("ui.creative_plan_save", "保存创作计划", "system.capabilities.list",
               "noop", targets=["project"], undo=False, ui="creative_plan_save"),
    ActionSpec("ui.creative_plan_validate", "检查创作计划", "system.capabilities.list",
               "noop", targets=["project"], undo=False, ui="creative_plan_validate"),
]

_BY_ID = {a.id: a for a in _ACTIONS}


def registry() -> Dict[str, ActionSpec]:
    return dict(_BY_ID)


def get_action(action_id: str) -> Optional[ActionSpec]:
    return _BY_ID.get(action_id)


#: 词序无关的共现规则（§五十七 标准命令的变体："原声都关掉"/"保留第 2 段原声"）。
#: (全组词命中 → action id)，组内词全部出现才命中；按组词数降优先。
_CO_OCCUR_RULES = [
    (("原声", "关"), "audio.mute_original"),
    (("原声", "静音"), "audio.mute_original"),
    (("原声", "不要"), "audio.mute_original"),
    (("原声", "去掉"), "audio.mute_original"),
    (("原声", "保留"), "audio.preserve"),
    (("现场", "保留"), "audio.preserve"),
    (("原声", "留着"), "audio.preserve"),
    (("原声", "音量"), "audio.original_volume"),
    (("原声", "压"), "audio.original_volume"),
    (("现场", "压"), "audio.original_volume"),
    (("BGM", "音量"), "music.volume"),
    (("音乐", "音量"), "music.volume"),
    (("音乐", "小"), "music.volume"),
    (("旁白", "压"), "music.duck"),
    (("别", "盖住"), "music.duck"),
    (("字幕", "样式"), "subtitle.style"),
    (("字幕", "改"), "subtitle.style"),
    (("字幕", "白字"), "subtitle.style"),
    (("水印", "透明"), "watermark.update"),
    (("Logo", "低"), "watermark.update"),
    (("加", "水印"), "watermark.add"),
    (("加", "Logo"), "watermark.add"),
    (("加", "logo"), "watermark.add"),
    (("放", "Logo"), "watermark.add"),
    (("水印", "加"), "watermark.add"),
    (("BGM", "小"), "music.volume"),
    (("BGM", "调小"), "music.volume"),
    (("BGM", "小一点"), "music.volume"),
    (("音乐", "调小"), "music.volume"),
    (("BGM", "大"), "music.volume"),
    (("音乐", "响"), "music.volume"),
]


def resolve_by_text(text: str) -> Optional[ActionSpec]:
    """§13 aliases：自然语言 → Action。

    两级匹配：
    1. 共现规则（词序无关——"原声都关掉"/"保留第 2 段原声"这类变体）
    2. 精确别名（最长优先——"右下角加我的 Logo"）
    """
    if not text:
        return None
    # 1. 共现规则：组词越多越优先
    best_rule: Optional[ActionSpec] = None
    best_rule_n = 0
    for words, aid in _CO_OCCUR_RULES:
        if all(w in text for w in words) and len(words) > best_rule_n:
            cand = _BY_ID.get(aid)
            if cand:
                best_rule, best_rule_n = cand, len(words)
    if best_rule is not None:
        return best_rule
    # 2. 精确别名（最长优先）
    best: Optional[ActionSpec] = None
    best_len = 0
    for a in _ACTIONS:
        for alias in [a.label, *a.aliases]:
            if alias and alias in text and len(alias) > best_len:
                best, best_len = a, len(alias)
    return best


# ---------------------------------------------------------------------------
# §63 Availability：action × project → available/reason/next_actions
# ---------------------------------------------------------------------------

def _check_precondition(project_id: str, cond: str, context: Optional[dict]) -> Optional[str]:
    """返回不满足原因；None = 通过。context 为 project_context.inspect_project_context 结果。"""
    ctx = context or {}
    if cond == "has_mask":
        ir = _load_ir(project_id)
        if ir is None:
            return "项目读取失败"
        sel = ((ctx or {}).get("selection") or {})
        if sel.get("type") == "layer":
            lay = _layer_of_ir(ir, sel.get("id", ""))
            if lay is None or not getattr(lay, "mask", None):
                return "当前图层没有蒙版可移除"
            return None
        return "先选中一个带蒙版的图层"
    if cond == "has_watermark":
        ir = _load_ir(project_id)
        if ir is None:
            return "项目读取失败"
        if not (ir.watermarks or []):
            return "项目中还没有水印/Logo"
        return None
    if cond == "has_selection":
        sel = ((ctx or {}).get("selection") or {})
        if not sel.get("type") or sel.get("type") in ("none", None):
            return "先在时间线选中对象（场景或图层）"
        return None
    if cond == "target_has_audio":
        # §63 语义：目标是视频层或项目有原声轨
        ir = _load_ir(project_id)
        if ir is None:
            return "项目读取失败"
        sel = ((ctx or {}).get("selection") or {})
        if sel.get("type") == "layer":
            lay = _layer_of_ir(ir, sel.get("id", ""))
            if lay is None or lay.type.value not in ("video",):
                return "选中的图层不是视频（图片没有声音）"
            return None
        if any(t.type.value == "original" for t in ir.audio):
            return None
        # 项目级：有任何视频层即可（preserve 会建立 original 轨）
        if any(l.type.value == "video" for sc in ir.scenes for l in sc.layers):
            return None
        return "项目里没有含声音的视频素材"
    return None


def _load_ir(project_id: str):
    try:
        from video_director.editor import load_composition
        return load_composition(project_id)
    except Exception as exc:  # noqa: BLE001 读失败诚实返回，不编造
        logger.warning(f"action_registry: 载入 {project_id} 失败: {exc}")
        return None


def _layer_of_ir(ir, layer_id: str):
    if not layer_id or "#" not in layer_id:
        return None
    scene_id, idx_s = layer_id.split("#")
    for sc in ir.scenes:
        if sc.id == scene_id:
            try:
                return sc.layers[int(idx_s)]
            except (IndexError, ValueError):
                return None
    return None


def action_availability(action_id: str, project_id: str,
                        context: Optional[dict] = None) -> dict:
    """§63 单条 Action 可用性：{action, available, reason, next_actions}。"""
    a = _BY_ID.get(action_id)
    if a is None:
        return {"action": action_id, "available": False,
                "reason": f"未知动作 {action_id}", "next_actions": []}
    if context is None and project_id:
        try:
            from video_director import project_context as _project_context
            context = _project_context.inspect_project_context(project_id, {})
        except Exception as exc:
            return {
                "action": a.id, "available": False,
                "reason": f"无法确认项目权威状态，拒绝暴露旧 Action：{exc}",
                "next_actions": [],
            }
    if context and context.get("authority") == "film.html":
        return {
            "action": a.id, "available": False,
            "reason": "当前项目由 film.html 独占时间线权威；旧 Action Registry 操作已停用，请使用 revision-bound film.* 工具。",
            "next_actions": [],
        }
    if not a.preconditions:
        return {"action": a.id, "available": True, "reason": "", "next_actions": []}
    reason = None
    for cond in a.preconditions:
        r = _check_precondition(project_id, cond, context)
        if r:
            reason = r
            break
    if reason is None:
        return {"action": a.id, "available": True, "reason": "", "next_actions": []}
    next_actions: List[str] = []
    if a.id.startswith("watermark."):
        next_actions = ["asset.import", "watermark.add_text"]
    elif a.id == "asset.replace":
        next_actions = ["asset.search", "generate.image"]
    elif a.id == "audio.preserve":
        next_actions = ["asset.import"]
    return {"action": a.id, "available": False, "reason": reason,
            "next_actions": next_actions}


#: §62 上下文键（Selection type+layer_type → 动作组）
_CONTEXT_KEYS = {
    ("project", None): [
        "audio.preserve", "audio.mute_original", "audio.original_volume",
        "subtitle.generate", "subtitle.style",
        "music.add", "music.remove", "music.volume", "music.duck",
        "watermark.add", "watermark.update", "watermark.remove",
        "voice.change", "voice.preview",
        # §58 UI 交互动作（dispatcher 本地执行；group 暴露 ui 字段）
        "ui.undo", "ui.redo", "ui.save", "ui.preview_draft", "ui.render_draft",
        "ui.split_layer", "ui.join_through_edits", "ui.add_edit_all_tracks", "ui.lift_layer_to_playhead", "ui.edit_visual_overlay",
        "ui.editor_session_snap", "ui.editor_session_box",
        "ui.creative_plan_toggle", "ui.creative_plan_reload",
        "ui.creative_plan_initialize", "ui.creative_plan_save", "ui.creative_plan_validate",
        "ui.open_voice_settings", "ui.open_audio_bus", "ui.open_subtitle_style",
        "voice.preview_full", "voice.upload", "content.generate_script",
        "content.rewrite_script", "music.auto_match", "music.preview",
        "asset.search", "generate.image", "generate.video", "generate.sfx",
        "render.draft", "render.final", "verify.run",
    ],
    ("scene", None): [
        "creative.select_candidate",
        "timeline.split", "timeline.duplicate", "timeline.delete", "timeline.move",
        "ui.split_layer", "ui.join_through_edits", "ui.add_edit_all_tracks", "ui.lift_layer_to_playhead",
        "timeline.duration", "visual.camera",
        "asset.replace", "asset.search", "asset.add_broll",
        "generate.image", "generate.video",
        "audio.preserve", "audio.mute_original",
        "ui.open_asset_picker", "ui.open_shot_picker", "ui.generate_scene_visual",
        "ui.move_prompt", "ui.add_broll_prompt", "sfx.add",
        "asset.import_local", "asset.search_stock",
        "visual.semantic_rerank",
    ],
    ("layer", "video"): [
        "asset.replace", "asset.search",
        "timeline.trim", "timeline.delete", "timeline.duplicate", "ui.split_layer", "ui.join_through_edits", "ui.lift_layer_to_playhead",
        "visual.transform", "visual.motion", "visual.pip", "visual.mask",
        "audio.preserve", "audio.mute_original", "audio.original_volume",
        "generate.image", "generate.video",
        "ui.open_asset_picker", "ui.open_motion_picker", "ui.edit_mask",
        "visual.keyframe_add", "visual.keyframe_remove", "visual.mask.remove",
        "visual.verify_asset",
    ],
    ("layer", "image"): [
        "asset.replace", "asset.search",
        "timeline.delete", "timeline.duplicate", "ui.split_layer", "ui.join_through_edits", "ui.lift_layer_to_playhead",
        "visual.transform", "visual.motion", "visual.mask",
        "generate.image",
        "ui.open_asset_picker", "ui.open_motion_picker", "ui.edit_mask",
        "visual.keyframe_add", "visual.keyframe_remove", "visual.mask.remove",
    ],
    ("layer", "subtitle"): [
        "subtitle.style", "timeline.delete",
    ],
    ("layer", None): [
        "asset.replace", "timeline.delete", "timeline.duplicate",
        "visual.transform", "visual.motion",
    ],
    ("visual_overlay", None): [
        "ui.split_layer", "ui.join_through_edits", "ui.edit_visual_overlay",
    ],
    ("scene_pair", None): [
        "relation.set",
    ],
    ("multi_layer", None): [
        "audio.preserve", "audio.mute_original", "audio.original_volume",
        "timeline.delete", "timeline.duplicate",
    ],
}


def actions_for_context(selection: Optional[dict], project_id: Optional[str] = None,
                        context: Optional[dict] = None) -> dict:
    """§62 /api/actions：按当前 Selection 上下文返回可用动作（含 Availability）。

    selection = {type: 'project'|'scene'|'layer'|'scene_pair'|'multi_layer'|none,
                 id, layer_type}
    返回 {context: 'layer.video', actions: [{id,label,available,reason,next_actions}]}
    """
    sel_type = (selection or {}).get("type") or "none"
    layer_type = (selection or {}).get("layer_type")
    if sel_type == "layer" and layer_type:
        key = ("layer", layer_type)
    else:
        key = (sel_type, None)
    if project_id and context is None:
        try:
            from video_director import project_context as _project_context
            context = _project_context.inspect_project_context(project_id, selection or {})
        except Exception as exc:
            context = {"authority": "unknown", "authority_error": str(exc)}
    ids = _CONTEXT_KEYS.get(key)
    if ids is None:
        # 回退：layer 未知类型 → 通用组
        ids = _CONTEXT_KEYS.get((sel_type if sel_type == "layer" else sel_type, None)) \
            or _CONTEXT_KEYS.get(("layer", None), []) if sel_type == "layer" \
            else _CONTEXT_KEYS.get((sel_type, None), [])
    ctx_name = f"layer.{layer_type}" if sel_type == "layer" and layer_type else sel_type
    out = []
    for aid in (ids or []):
        a = _BY_ID.get(aid)
        if not a:
            continue
        avail = {"action": a.id, "available": True, "reason": "", "next_actions": []}
        if project_id:
            avail = action_availability(a.id, project_id, context)
        out.append({
            "id": a.id, "label": a.label, "aliases": a.aliases,
            "targets": a.targets, "undo": a.undo, "preview": a.preview,
            "ui": a.ui, "parameters": dict(a.payload_defaults),
            **{k: avail[k] for k in ("available", "reason", "next_actions")},
        })
    return {"context": ctx_name, "actions": out}


def manifest_for_project(project_id: str, selection: Optional[dict] = None) -> dict:
    """§62 全上下文 manifest（GET /api/actions?project_id= 无 selection 时用）：
    project/scene/layer.video/layer.image/layer.subtitle/layer.audio/scene_pair 各组。"""
    groups = {}
    context = None
    if project_id:
        try:
            from video_director import project_context as _project_context
            context = _project_context.inspect_project_context(project_id, selection or {})
        except Exception as exc:
            context = {"authority": "unknown", "authority_error": str(exc)}
    for (sel_type, ltype), _ in _CONTEXT_KEYS.items():
        name = f"layer.{ltype}" if sel_type == "layer" and ltype else sel_type
        if name in groups:
            continue
        r = actions_for_context({"type": sel_type, "layer_type": ltype},
                                project_id=project_id, context=context)
        groups[name] = r["actions"]
    return groups


# ---------------------------------------------------------------------------
# §17 Action Resolver：goal+steps（LLM 输出）→ capability → command
# ---------------------------------------------------------------------------

def resolve_step(step: dict, selection: Optional[dict] = None) -> dict:
    """§17 单步解析：action id/目标/参数 → capability+command+完整 payload。

    step = {action, target, params}
    target = 'current_selection' | 'project' | 具体 id
    返回 {action, capability, command, targetId, payload}
    解析失败返回 {error}——不猜（不发明字段名/取值）。
    """
    action_id = step.get("action") or ""
    a = _BY_ID.get(action_id)
    if a is None:
        # 文本兜底：直接给中文动词也尝试解析（§13 aliases）
        a = resolve_by_text(action_id)
    if a is None:
        return {"error": f"未知动作 {action_id!r}"}

    target = step.get("target") or "current_selection"
    sel = selection or {}
    if target == "current_selection":
        target_id = sel.get("id") or ""
        if sel.get("type") == "scene_pair":
            target_id = sel.get("id") or ""  # from->to 形态
        elif sel.get("type") == "project" or not target_id:
            target_id = ""
    else:
        target_id = target if target != "project" else ""

    payload = dict(a.payload_defaults)
    params = step.get("params") or {}
    for k, v in params.items():
        payload[k] = v

    # target 语义映射：数字（第几段）→ 场景 id
    if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
        return {"error": "数字目标暂不支持，请先用 scene id"}

    # capability 与 command 不同名时的参数适配
    if a.id == "music.volume" and "type" not in payload:
        payload["type"] = "bgm"
    if a.id == "music.duck":
        payload.setdefault("type", "bgm")
        payload["ducking"] = True
    if a.id == "voice.change":
        # voice_id 进 payload
        if params.get("voice") and "voice_id" not in payload:
            payload["voice_id"] = params.get("voice")
    if a.id == "timeline.trim":
        # trim Command 契约：sourceIn/sourceOut
        if params.get("source_in"):
            payload["sourceIn"] = params["source_in"]
        if params.get("source_out"):
            payload["sourceOut"] = params["source_out"]
    if a.id in ("visual.keyframe_add", "visual.keyframe_remove"):
        # keyframe Command 契约：property/at/value（params 字段名一致直传）
        for k in ("property", "at", "value"):
            if params.get(k) is not None:
                payload[k] = params[k]
    if a.id == "audio.mix":
        # §37 Audio Bus：tracks 映射直接透传（{original:{volume:0.2}, bgm:{volume:0.1}}）
        if params.get("tracks"):
            payload["tracks"] = params["tracks"]

    return {
        "action": a.id, "capability": a.capability, "command": a.command,
        "targetId": target_id, "payload": payload,
        "label": a.label, "undo": a.undo,
    }


# ---------------------------------------------------------------------------
# §64 商业安全 Action Context：provider 政策由系统声明
# ---------------------------------------------------------------------------

def commercial_context() -> List[dict]:
    """§64 Stock provider 商业政策（系统声明，Director 只选择不判断）。"""
    import os
    out = []
    try:
        from video_director import model_config as _mc
        configured = {
            name: _mc.is_role_enabled(f"stock_{name}")
            for name in ("pexels", "pixabay", "coverr")
        }
    except Exception:
        configured = {
            "pexels": bool(os.environ.get("PEXELS_API_KEY")),
            "pixabay": bool(os.environ.get("PIXABAY_API_KEY")),
            "coverr": bool(os.environ.get("COVERR_API_KEY")),
        }
    policies = {
        "pexels": {
            "available": configured["pexels"],
            "commercial_policy": "allowed_with_attribution",
            "api_limit": "200/hour · 20000/month（官方默认）",
            "bulk_query": False,
            "note": "禁止用 API 复制图库平台功能/建数据集；需正确归属",
        },
        "pixabay": {
            "available": configured["pixabay"],
            "commercial_policy": "allowed",
            "api_limit": "100/60s（官方默认）",
            "bulk_query": False,
            "note": "响应须缓存 24h；禁止系统性批量下载；图片先落服务器再使用",
        },
        "coverr": {
            "available": configured["coverr"],
            "commercial_policy": "allowed_with_attribution",
            "api_limit": "50/hour 免费层 · 2000/hour 生产层",
            "bulk_query": False,
            "note": "视频需 Coverr attribution；禁止建竞争服务/AI 训练",
        },
    }
    for name, p in policies.items():
        out.append({"provider": name, **p})
    return out


#: §42 源码契约与运行态一致性：build_id 显式暴露（前端 Developer Mode 显示）
BUILD_ID = "vd-actions-v1"


# ---------------------------------------------------------------------------
# §67 Workflow Registry：高层工作流（AI/用户的"真正大动作"，映射到 WorkflowRun kind）
# ---------------------------------------------------------------------------

#: §47/§67 WorkflowRun 通用步骤（与 workflow_run.DEFAULT_STEPS 对齐）
DEFAULT_RUN_STEPS = ["script", "scene_plan", "narration", "material_match",
                     "subtitle", "music", "watermark", "composition",
                     "preview", "verify"]

#: WorkflowSpec：高层工作流 → WorkflowRun kind + 步骤序（§47 任务中心事实源）
WORKFLOW_SPECS = {
    "create_video": {
        "label": "创作视频", "run_kind": "compose",
        "steps": DEFAULT_RUN_STEPS,
        "desc": "主题/文案/素材 → 脚本 → 镜头 → 配音 → 素材 → 字幕 → 音乐 → 成片",
    },
    "create_video_from_topic": {
        "label": "主题成片", "run_kind": "compose",
        "steps": DEFAULT_RUN_STEPS,
        "desc": "一句话主题 → 全链自动成片（LLM 生成脚本后同 create_video）",
    },
    "create_video_from_script": {
        "label": "文案成片", "run_kind": "compose",
        "steps": DEFAULT_RUN_STEPS,
        "desc": "完整文案 → 全链自动成片",
    },
    "create_video_from_assets": {
        "label": "素材成片", "run_kind": "compose",
        "steps": DEFAULT_RUN_STEPS,
        "desc": "上传素材 → 按素材生成文案/镜头 → 成片",
    },
    "replace_scene_visual": {
        "label": "换场景画面", "run_kind": "compose",
        "steps": ["material_match", "composition", "preview"],
        "desc": "单场景重找/重生成画面（asset.search → replace → verify 回退链）",
    },
    "rebuild_audio": {
        "label": "重建声音", "run_kind": "render_draft",
        "steps": ["narration", "render", "verify"],
        "desc": "换音色/换配音后重生成旁白与草稿",
    },
    "rebuild_subtitles": {
        "label": "重建字幕", "run_kind": "render_draft",
        "steps": ["subtitle", "render", "verify"],
        "desc": "字幕模式/样式变更后重出字幕轨",
    },
    "batch_create": {
        "label": "批量成片", "run_kind": "series_episode",
        "steps": ["script", "narration", "material_match", "composition", "verify", "render"],
        "desc": "多份文案共享设置，逐集成片（MPT 批量）",
    },
    "render_draft": {
        "label": "渲染草稿", "run_kind": "render_draft",
        "steps": ["render", "verify"],
        "desc": "低清快速草稿",
    },
    "render_final": {
        "label": "渲染成片", "run_kind": "render_final",
        "steps": ["render", "verify"],
        "desc": "商用预检通过后正式成片",
    },
}

def workflow_specs() -> dict:
    """§67 /api/actions.system.workflows（AI 高层工具面 + 任务中心分类）。"""
    return {k: dict(v) for k, v in WORKFLOW_SPECS.items()}


#: §68 AI 最终工具面：16 动词 → Action id（高层语义，AI 只见动词不见底层）
AI_TOOL_SURFACE = {
    # 创作由 /api/director/create 直接进入 Director Workflow，不再伪装成 noop Action。
    "create_video": None,
    "edit_script": "content.rewrite_script",
    "find_assets": "asset.search",
    "replace_asset": "asset.replace",
    "generate_visual": "generate.image",
    "set_voice": "voice.change",
    "set_subtitles": "subtitle.generate",
    "set_music": "music.add",
    "edit_scene": "timeline.duration",
    "edit_layer": "visual.transform",
    "edit_audio": "audio.mix",
    "set_brand": "watermark.add",
    "set_scene_relation": "relation.set",
    "preview": "render.draft",
    "verify": "verify.run",
    "render": "render.final",
}


def ai_tool_surface() -> dict:
    """§68 /api/actions.system.ai_tools：动词 → Action → （内部）Capability。"""
    out = {}
    for verb, aid in AI_TOOL_SURFACE.items():
        if aid is None:
            out[verb] = {
                "workflow": "director_create",
                "label": "AI 导演创作",
                "requires_project": False,
                "description": "自然语言简报 → 脚本 → 分镜 → 配音 → 素材 → 字幕 → 音乐 → 预览 → 预检 → 成片",
            }
            continue
        a = _BY_ID.get(aid)
        if a:
            out[verb] = {"action": a.id, "label": a.label}
    return out


def system_context() -> dict:
    """§16 System Context：actions/providers/render_profiles/asset_sources/voice_sources/music_sources。"""
    from video_director.capability_registry.manifest import manifest as _mf
    # 已三层注册（_ACTIONS/capability manifest/workflow）但前端动作面板与服务端
    # action-dispatcher 均零入口的能力——Agent 读 system_context 会当成可调能力
    # 派发，实际全部打空。从这里摘掉，等真实接线后再移除本集合。
    # 注意：_ACTIONS 本身不动（前端面板按 target 索引，过滤只影响 Agent 面）。
    # 2026-10-22 三能力全部真接线完成，本集合清空（generate.sfx → Openverse
    # CC0 搜索；music.auto_match → BGM 面板按钮；delivery.generate_metadata →
    # tool_executor LLM 发布文案 + 成片完成框按钮）。保留过滤机制本身，
    # 未来新占位能力仍走这里摘除。
    _FRONTEND_UNWIRED: set = set()
    caps = sorted(_mf().keys())
    return {
        "build_id": BUILD_ID,
        "actions": [a.id for a in _ACTIONS if a.id not in _FRONTEND_UNWIRED],
        "capabilities_count": len(caps),
        "workflows": workflow_specs(),       # §67 Workflow Registry
        "ai_tools": ai_tool_surface(),       # §68 AI 高层工具面
        "providers": _provider_summary(),
        "render_profiles": [
            {"id": "draft", "label": "草稿（低清快速）", "scale": 0.5},
            {"id": "final", "label": "成片（全分辨率）", "scale": 1.0},
        ],
        "asset_sources": ["local", "project", "pexels", "pixabay", "coverr", "generated"],
        "voice_sources": ["edge-tts", "uploaded"],
        "music_sources": ["auto_match", "local", "generated", "none"],
        "commercial": commercial_context(),
    }


def _provider_summary() -> dict:
    try:
        from video_director.project_context import _provider_availability
        return _provider_availability()
    except Exception:  # noqa: BLE001
        return {}
