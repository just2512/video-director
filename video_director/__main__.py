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

    film = sub.add_parser("film", help="读取、验证、编辑、检查或导出权威 film.html 工程")
    film_sub = film.add_subparsers(dest="film_cmd", required=True)
    for command, help_text in (
        ("status", "显示 film.html revision、画幅、轨道和片段摘要"),
        ("validate", "验证 film.html 协议、媒体引用和源区间声明"),
        ("edit", "按指定 base revision 原子提交 JSON 编辑操作并记录历史"),
        ("diff", "将指定历史 revision 与当前 film.html 比较"),
        ("look", "生成真实浏览器画面接触表并反序验证确定性"),
        ("render", "把固定 revision 的 film.html 导出为 MP4"),
    ):
        command_parser = film_sub.add_parser(command, help=help_text)
        command_parser.add_argument("--project-id", required=True)
        if command == "edit":
            command_parser.add_argument("--base-revision", required=True,
                                        help="从 film status 读取的精确 revision；过期时拒绝写入")
            command_parser.add_argument("--ops-file", required=True,
                                        help="UTF-8 JSON 文件，顶层必须是操作对象数组")
        if command == "diff":
            command_parser.add_argument("--from-revision", default=None,
                                        help="历史 revision；省略时比较最近一次编辑前的版本")
        if command == "look":
            command_parser.add_argument("--revision", default=None,
                                        help="要求当前 revision 必须精确匹配")
            command_parser.add_argument("--times", default=None,
                                        help="逗号分隔的秒数，例如 0,1.5,4")
        if command == "render":
            command_parser.add_argument("--fps", type=int, default=30)
    
    a = ap.parse_args(argv)

    if a.cmd == "film":
        from difflib import unified_diff
        from video_director import film_document as fd

        def emit(payload, exit_code=0):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            sys.exit(exit_code)

        try:
            pid = fd.validate_project_id(a.project_id)
            state = fd.read_project_film(pid)
            if a.film_cmd == "status":
                if not state.get("exists"):
                    emit({"ok": True, "exists": False, "project_id": pid,
                          "message": "尚未初始化 film.html"})
                doc = state["doc"]
                clips = [clip for track in doc["tracks"] for clip in track["clips"]]
                duration = max((fd.clip_end(clip) or 0.0 for clip in clips), default=0.0)
                emit({
                    "ok": True, "exists": True, "project_id": pid,
                    "revision": state["rev"], "stage": doc["stage"],
                    "track_count": len(doc["tracks"]), "clip_count": len(clips),
                    "duration": round(duration, 6),
                    "tracks": [{"id": track["id"], "flags": sorted(track["attrs"].keys()),
                                "clip_ids": [clip["id"] for clip in track["clips"]]}
                               for track in doc["tracks"]],
                })

            if a.film_cmd == "edit":
                if not state.get("exists"):
                    emit({"ok": False, "code": "FILM_NOT_INITIALIZED",
                          "error": "当前项目尚未初始化 film.html。"}, 2)
                try:
                    operations = json.loads(Path(a.ops_file).read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    emit({"ok": False, "code": "INVALID_OPERATIONS_FILE",
                          "error": "无法读取编辑操作 JSON；请提供 UTF-8、合法 JSON 文件。",
                          "detail": str(exc)[:500]}, 2)
                if (not isinstance(operations, list) or not operations
                        or len(operations) > fd.MAX_OPERATIONS
                        or any(not isinstance(operation, dict) for operation in operations)):
                    emit({"ok": False, "code": "INVALID_OPERATIONS",
                          "error": f"编辑操作必须是 1–{fd.MAX_OPERATIONS} 个 JSON 对象组成的数组。"}, 2)
                # External agents go through exactly the same document validator,
                # locking, operation history and revision guard as the Studio.
                # Never use the UI's best-effort rebase behavior for an agent write.
                result = fd.edit_project_film(
                    pid, a.base_revision, operations, require_base=True
                )
                emit({
                    "ok": True, "project_id": pid,
                    "base_revision": a.base_revision,
                    "revision": result["rev"],
                    "operation_count": len(result["ops"]),
                    "operations": result["ops"],
                    "rebased": False,
                })

            if a.film_cmd == "validate":
                if not state.get("exists"):
                    emit({"ok": False, "code": "FILM_NOT_INITIALIZED",
                          "error": "当前项目尚未初始化 film.html。"}, 2)
                # read_project_film has already run the strict document parser. This
                # second parse keeps the command's contract explicit and fail-closed.
                doc = fd.parse_film(state["text"])
                issues = []
                checked_assets = 0
                for track in doc["tracks"]:
                    for clip in track["clips"]:
                        src = str((clip.get("attrs") or {}).get("src") or "")
                        try:
                            asset_path = fd.media_path_for_src(pid, src)
                            checked_assets += 1
                            if not asset_path.is_file():
                                issues.append({"code": "MISSING_ASSET", "clip": clip["id"],
                                               "src": src, "message": "素材文件不存在"})
                        except Exception as exc:
                            issues.append({"code": getattr(exc, "code", "INVALID_MEDIA_PATH"),
                                           "clip": clip["id"], "src": src, "message": str(exc)})
                clips = [clip for track in doc["tracks"] for clip in track["clips"]]
                duration = max((fd.clip_end(clip) or 0.0 for clip in clips), default=0.0)
                payload = {
                    "ok": not issues, "project_id": pid, "revision": state["rev"],
                    "stage": doc["stage"], "track_count": len(doc["tracks"]),
                    "clip_count": len(clips), "duration": round(duration, 6),
                    "assets_checked": checked_assets, "issues": issues,
                    "note": "frame(t) 页面能力与实际逐帧确定性由 film look / film render 的真实浏览器验收。",
                }
                emit(payload, 0 if not issues else 1)

            if a.film_cmd == "diff":
                import json as _json
                from pathlib import Path as _Path
                history_path = fd.config.project_dir(pid) / fd.HISTORY_NAME
                if not state.get("exists"):
                    emit({"ok": False, "code": "FILM_NOT_INITIALIZED",
                          "error": "当前项目尚未初始化 film.html。"}, 2)
                if history_path.is_file():
                    history = _json.loads(history_path.read_text(encoding="utf-8"))
                else:
                    history = {"past": [], "future": []}
                entries = list(history.get("past", [])) + list(history.get("future", []))
                requested = getattr(a, "from_revision", None)
                candidate = None
                if requested is None and history.get("past"):
                    candidate = (history["past"][-1].get("before_rev"),
                                 history["past"][-1].get("before"))
                else:
                    for entry in entries:
                        if entry.get("before_rev") == requested:
                            candidate = (entry.get("before_rev"), entry.get("before"))
                            break
                        if entry.get("after_rev") == requested:
                            candidate = (entry.get("after_rev"), entry.get("after"))
                            break
                if not candidate or not isinstance(candidate[1], str):
                    emit({"ok": False, "code": "REVISION_NOT_IN_HISTORY",
                          "error": "找不到指定 revision 的历史快照；历史窗口最多保留最近 200 次编辑。",
                          "current_revision": state["rev"]}, 2)
                old_rev, old_text = candidate
                diff = "".join(unified_diff(
                    old_text.splitlines(keepends=True), state["text"].splitlines(keepends=True),
                    fromfile="film.html@" + str(old_rev),
                    tofile="film.html@" + str(state["rev"]),
                ))
                print(diff if diff else "无差异。")
                sys.exit(0)

            if a.film_cmd == "look":
                from video_director import film_render as film_render_mod
                requested_times = None
                if a.times:
                    try:
                        requested_times = [float(part.strip()) for part in a.times.split(",") if part.strip()]
                    except ValueError:
                        emit({"ok": False, "code": "INVALID_LOOK_TIMES",
                              "error": "--times 必须是逗号分隔的秒数。"}, 2)
                result = film_render_mod.look_project_film(
                    pid, expected_revision=a.revision, times=requested_times
                )
                emit(result)

            if a.film_cmd == "render":
                from video_director import film_render as film_render_mod
                if not state.get("exists"):
                    emit({"ok": False, "code": "FILM_NOT_INITIALIZED",
                          "error": "当前项目尚未初始化 film.html。"}, 2)
                result = film_render_mod.render_project_film(pid, state["rev"], fps=a.fps)
                emit(result)

        except SystemExit:
            raise
        except Exception as exc:
            emit({"ok": False, "code": getattr(exc, "code", "FILM_COMMAND_FAILED"),
                  "error": str(exc)}, 1)

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
