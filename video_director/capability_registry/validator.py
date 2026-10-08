        "candidateId": FieldSpec("str", required=True),
        "visualIntent": FieldSpec("str", required=True),
        "approach": FieldSpec("str"),
        "sourcePreference": FieldSpec("str"),
    }),
    "StockSearchRequest": InputSchema("StockSearchRequest", {
        "query": FieldSpec("str", required=True),
        "provider": FieldSpec("enum:str", enum=frozenset({"pexels", "pixabay", "coverr"})),
        "orientation": FieldSpec("enum:str", enum=frozenset({"landscape", "portrait", "square"})),
    }),
    "VisualSemanticRerankRequest": InputSchema("VisualSemanticRerankRequest", {
        "sceneId": FieldSpec("str", required=True),
        "sceneText": FieldSpec("str", required=True),
        "candidates": FieldSpec("list", required=True),
        "limit": FieldSpec("int", minimum=1, maximum=10),
    }),
    "VisualQARequest": InputSchema("VisualQARequest", {
        "sceneId": FieldSpec("str", required=True),
        "sceneText": FieldSpec("str", required=True),
        "assetId": FieldSpec("str", required=True),
        "assetFile": FieldSpec("str", required=True),
    }),
    "VoicePreviewRequest": InputSchema("VoicePreviewRequest", {
        "voiceId": FieldSpec("str"),
        "text": FieldSpec("str"),
    }),
    "MusicAddRequest": InputSchema("MusicAddRequest", {
        "assetId": FieldSpec("str"),
        "volume": FieldSpec("float", minimum=0.0, maximum=2.0),
    }),
    "GenerationRequest": InputSchema("GenerationRequest", {
        "type": FieldSpec("enum:str", enum=frozenset({"video", "image", "graphic", "sfx", "music"})),
        "prompt": FieldSpec("str", required=True),
        "targetId": FieldSpec("str"),
        "target": FieldSpec("dict"),  # §29 target{projectId,variantId,sceneId,start,duration}
        "references": FieldSpec("str"),
        "firstFrame": FieldSpec("str"),
        "lastFrame": FieldSpec("str"),
    }),
    "MotionRequest": InputSchema("MotionRequest", {
        "grammar": FieldSpec("str", required=True),
        "prompt": FieldSpec("str"),
        "script": FieldSpec("str"),