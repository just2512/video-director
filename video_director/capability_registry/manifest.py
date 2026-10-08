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
    caps.append(_cap("creative.candidate.select", "visual",
        "采用当前场景的创作候选方案；持久化 Scene visual_intent 与候选选择状态",
        input_schema="CreativeCandidateSelectRequest", outputs=("Composition", "CreativeCandidateSelection"),
        examples=("采用这个创作方案", "用第二个方案")))
    caps.append(_cap("stock.search", "asset",
        "搜索 Stock 素材（Pexels/Pixabay/Coverr）", input_schema="StockSearchRequest",
        outputs=("StockAsset",), side_effect="", provider_required=True,
        examples=("找两个关于咖啡的 B-roll",)))
    caps.append(_cap("visual.semantic_rerank", "visual",
        "用视觉语义模型重新排序当前场景的素材候选；不返回 similarity score",
        input_schema="VisualSemanticRerankRequest",
        outputs=("VisualSemanticCandidates",), side_effect="",
        provider_required=True,
        examples=("按画面语义重新排序这些候选素材",)))
    caps.append(_cap("visual.qa", "visual",
        "验证单个素材是否真的表达当前 Scene；返回离散 verdict 与证据",
        input_schema="VisualQARequest",
        outputs=("VisualEvidence",), side_effect="",
        provider_required=True,
        examples=("检查这个素材是否适合本场景",)))
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