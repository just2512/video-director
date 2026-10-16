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
    ap = argparse.ArgumentParser("video_director", description="AI Video Director")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境自检：必需能力缺失则在生成前阻塞")
    d.add_argument("--json", action="store_true", help="输出 JSON（供 WebUI/Skill 消费）")

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
    auto_mode = auto.add_mutually_exclusive_group()
    auto_mode.add_argument("--confirm", action="store_true",
                           help="先预览分镜再渲染（规划完成暂停，输出分镜书 + 项目ID）")
    auto_mode.add_argument("--auto", action="store_true",
                           help="不确认，一键到底")
    auto.add_argument("--json", action="store_true")

    txt = sub.add_parser("text", help="纯文案模式：文案 → 文字卡+旁白+字幕 → 成片（无需素材）")
    txt.add_argument("--script", required=True, help="文案 txt/md 文件")
    txt.add_argument("--project-id", default="text-run")
    txt.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    txt.add_argument("--voice-rate", default="+0%")
    txt.add_argument("--style", default=None, help="风格描述（留空 AUTO）")
    txt.add_argument("--width", type=int, default=None)
    txt.add_argument("--height", type=int, default=None)
    txt.add_argument("--fps", type=int, default=None)
    txt.add_argument("--json", action="store_true")

    resume = sub.add_parser(
        "resume",
        help="人工确认后从 RENDERING 继续渲染（配合 auto --confirm 使用）",
    )
    resume.add_argument("--project-id", required=True,
                        help="auto --confirm 生成的 project_id")
    resume.add_argument("--edits", default=None,
                        help="edits JSON 文件路径（改素材/删场景/调顺序），"
                             "省略则直接用原分镜书确认渲染")
    resume.add_argument("--json", action="store_true")

    caps = sub.add_parser("capabilities", help="能力清单与 preflight 报告")

    a = ap.parse_args(argv)

    if a.cmd == "capabilities":
        from video_director import capabilities as caps_mod
        print(caps_mod.capability_report())
        sys.exit(0)

    if a.cmd == "doctor":
        from video_director import doctor
        results = doctor.run_doctor()
        if getattr(a, "json", False):
            import json as _json
            print(_json.dumps(
                [{"name": r.name, "status": r.status, "detail": r.detail} for r in results],
                ensure_ascii=False, indent=2,
            ))
        else:
            print(doctor.format_doctor_report(results))
        sys.exit(1 if doctor.has_blocking_failures(results) else 0)

    from video_director import pipeline

    if getattr(a, "init_test_audio", False):
        from video_director import audio
        audio.generate_test_audio_library()

    script = Path(a.script).read_text(encoding="utf-8")
    videos = _collect_files(a.videos, (".mp4", ".mov", ".mkv", ".webm")) if a.videos else []
    images = _collect_files(a.images, (".jpg", ".jpeg", ".png", ".webp")) if a.images else []

    if a.cmd == "text":
        # 纯文案模式：先做能力级 preflight（script-only 需要 tts+renderer）
        from video_director import capabilities as caps_mod
        ok, blocked, _degraded = caps_mod.preflight(["tts", "renderer"])
        if not ok:
            print(f"[X] 能力缺失，无法进入纯文案模式: {', '.join(blocked)}", file=sys.stderr)
            sys.exit(1)
        from video_director import script_only
        result = script_only.compose_script_only(
            script=script,
            project_id=getattr(a, "project_id", "text-run"),
            voice=a.voice,
            style_intent=a.style if getattr(a, "style", None) else None,
            width=a.width,
            height=a.height,
            fps=a.fps,
        )
    elif a.cmd == "auto":
        result = pipeline.compose_auto(
            script=script,
            video_files=videos,
            image_files=images,
            project_id=getattr(a, "project_id", "auto-run"),
            voice=a.voice,
            voice_rate=a.voice_rate,
            auto_confirm=not a.confirm,
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
