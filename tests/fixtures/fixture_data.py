# -*- coding: utf-8 -*-
"""§43 Regression Fixture Pack 数据源（Python dict，测试加载后落盘为项目）。

方案 §43 要求 fixtures/{recipe,motion,template}-project.json + sample-assets/。
JSON 内容以此处 DICT 为单一事实源（test 里 dump 落盘），避免二进制/转义歧义。
"""

RECIPE_PROJECT = {
    "project_id": "fixture-recipe",
    "composition": {
        "project": {"duration": 10, "fps": 30, "height": 1920, "width": 1080},
        "style_id": "knowledge-explainer",
        "scenes": [
            {
                "id": "S01", "start": 0, "duration": 5, "text": "很多人以为AI只能聊天",
                "origin_recipe_id": "shot.title-card-minimal",
                "layers": [{"asset_id": "img1", "start": 0, "duration": 5, "type": "image"}],
            },
            {
                "id": "S02", "start": 5, "duration": 5, "text": "其实AI改变的是工作流程",
                "origin_recipe_id": "shot.editorial-photo-story",
                "layers": [{"asset_id": "img2", "start": 0, "duration": 5, "type": "image"}],
            },
        ],
    },
    "manifest": [
        {"id": "img1", "type": "image", "file": "sample-assets/frame.jpg",
         "duration": 5, "source": "user"},
        {"id": "img2", "type": "image", "file": "sample-assets/frame2.jpg",
         "duration": 5, "source": "user"},
        {"id": "vid1", "type": "video", "file": "sample-assets/clip.mp4",
         "duration": 8, "source": "user"},
    ],
}

MOTION_PROJECT = {
    "project_id": "fixture-motion",
    "composition": {
        "project": {"duration": 10, "fps": 30, "height": 1920, "width": 1080},
        "style_id": "knowledge-explainer",
        "scenes": [
            {
                "id": "S01", "start": 0, "duration": 5, "text": "很多人以为AI只能聊天",
                "origin_recipe_id": "shot.title-card-minimal",
                "layers": [{
                    "asset_id": "img1", "start": 0, "duration": 5, "type": "image",
                    "motion_recipe_id": "motion.fade-in", "motion_version": "1.0.0",
                    "motion_instance": {
                        "recipe_id": "motion.fade-in", "recipe_version": "1.0.0",
                        "start_offset": 0, "duration": 0.5, "parameters": {},
                    },
                }],
            },
            {
                "id": "S02", "start": 5, "duration": 5, "text": "其实AI改变的是工作流程",
                "layers": [{"asset_id": "img2", "start": 0, "duration": 5, "type": "image"}],
            },
        ],
    },
    "manifest": RECIPE_PROJECT["manifest"],
}

TEMPLATE_PROJECT = {
    "project_id": "fixture-template",
    "composition": {
        "project": {"duration": 10, "fps": 30, "height": 1920, "width": 1080},
        "style_id": "editorial",
        "origin_template_id": "tpl.ink-press-editorial",
        "origin_template_version": "1.0.0",
        "scenes": [
            {
                "id": "S01", "start": 0, "duration": 5, "text": "纸墨标题",
                "layers": [{"asset_id": "img1", "start": 0, "duration": 5, "type": "image"}],
            },
            {
                "id": "S02", "start": 5, "duration": 5, "text": "墨韵内容",
                "layers": [{"asset_id": "img1", "start": 0, "duration": 5, "type": "image"}],
            },
        ],
    },
    "manifest": RECIPE_PROJECT["manifest"],
}

#: sample-assets 二进制最小合法文件魔数（jpg/mp4 头，够存在性检查用）
SAMPLE_BINARY_MAGIC = {
    "frame.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 32,
    "frame2.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 32,
    "clip.mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 24,
}
