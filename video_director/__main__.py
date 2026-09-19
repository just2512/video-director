"""Composer CLI（V2 §68：composer create/render/validate 同一引擎多入口）。

用法：
    python -m video_director compose --script script.txt --videos dir/ --images dir/ \
        [--bgm bgm-ambient-001] [--sfx 13.4:whoosh] [--out project_id]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _collect_files(d: str, exts: tuple) -> list:
    p = Path(d)
    if p.is_file():
        return [str(p)]
    return sorted(str(x) for x in p.iterdir() if x.suffix.lower() in exts)


def main(argv=None):
    ap = argparse.ArgumentParser("video_director", description="Script-driven Video Composer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compose", help="文案+素材 → final.mp4")
    c.add_argument("--script", required=True, help="文案 txt 文件")
    c.add_argument("--videos", default=None, help="视频目录/文件")
    c.add_argument("--images", default=None, help="图片目录/文件")
    c.add_argument("--project-id", default=None)
    c.add_argument("--width", type=int, default=None)
    c.add_argument("--height", type=int, default=None)
    c.add_argument("--fps", type=int, default=None)
    c.add_argument("--narration", default=None, help="旁白音频（跳过 TTS 时提供）")
    c.add_argument("--bgm", default=None, help="BGM asset_id（音频库）")
    c.add_argument("--sfx", action="append", default=[],
                   help="SFX 事件 start:asset_id 或 start:tag1,tag2")
    c.add_argument("--init-test-audio", action="store_true",
                   help="先生成自产测试音频库")
    c.add_argument("--json", action="store_true", help="stdout 只输出结果 JSON")

    auto = sub.add_parser("auto", help="全自动成片：文案+TTS+字幕+成片")
    auto.add_argument("--script", required=True, help="文案 txt/md 文件")
    auto.add_argument("--videos", default=None, help="视频目录/文件")
    auto.add_argument("--images", default=None, help="图片目录/文件")
    auto.add_argument("--project-id", default="auto-run")
    auto.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    auto.add_argument("--voice-rate", default="+0%")
    auto.add_argument("--semantic", default=None,
                      help="素材语义标注 assets_semantic.json（按内容匹配画面）")
    auto.add_argument("--style", default=None,
                      help="风格描述（留空 AUTO 决定）")
    auto.add_argument("--width", type=int, default=None)
    auto.add_argument("--height", type=int, default=None)
    auto.add_argument("--fps", type=int, default=None)
    auto.add_argument("--bgm", default=None)
    auto.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)

    from video_director import pipeline

    if getattr(a, "init_test_audio", False):
        from video_director import audio
        audio.generate_test_audio_library()

    script = Path(a.script).read_text(encoding="utf-8")
    videos = _collect_files(a.videos, (".mp4", ".mov", ".mkv", ".webm")) if a.videos else []
    images = _collect_files(a.images, (".jpg", ".jpeg", ".png", ".webp")) if a.images else []

    if a.cmd == "auto":
        result = pipeline.compose_auto(
            script=script,
            video_files=videos,
            image_files=images,
            project_id=getattr(a, "project_id", "auto-run"),
            voice=a.voice,
            voice_rate=a.voice_rate,
            manual_manifest=a.semantic if getattr(a, "semantic", None) else None,
            style_intent=a.style if getattr(a, "style", None) else None,
            bgm_asset_id=a.bgm if getattr(a, "bgm", None) else None,
            width=a.width,
            height=a.height,
            fps=a.fps,
        )
    else:
        sfx_plan = []
        for item in a.sfx:
            start_str, _, spec = item.partition(":")
            ev = {"start": float(start_str)}
            if "," in spec or spec in ("whoosh", "impact", "click"):
                ev["tags"] = spec.split(",")
            else:
                ev["asset_id"] = spec
            sfx_plan.append(ev)

        result = pipeline.compose_project(
            script=script,
            video_files=videos,
            image_files=images,
            project_id=a.project_id or "cli-run",
            narration_file=a.narration,
            bgm_asset_id=a.bgm,
            sfx_plan=sfx_plan or None,
            width=a.width,
            height=a.height,
            fps=a.fps,
        )
    payload = {
        "final": result.final_path,
        "project_dir": result.project_dir,
        "narration_duration": result.narration_duration,
        "qc": result.qc_report,
        "warnings": result.warnings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2 if not a.json else None))


if __name__ == "__main__":
    main()
