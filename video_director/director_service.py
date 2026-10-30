"""Unified Director service facade.

The WebUI and local Agent tools call this module rather than maintaining
separate request-to-Director translations. Durable workflow state remains
owned by workflow_run; this module validates inputs and delegates.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from video_director import config


class DirectorServiceError(ValueError):
    """A user-actionable Director service error with an HTTP-compatible status."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _text(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DirectorServiceError("INVALID_INPUT", f"{name} 必须是字符串")
    return value.strip()


def _project_id(value: Any) -> str:
    """Accept a single path-safe identifier, never a path or traversal token."""
    value = _text(value, "project_id")
    if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise DirectorServiceError(
            "INVALID_PROJECT_ID",
            "project_id 只能包含字母、数字、点、下划线和连字符，且必须以字母或数字开头",
        )
    return value


def create_director_run(payload: dict) -> dict:
    """Validate and start the same Director workflow used by the WebUI.

    The payload mirrors POST /api/director/create. director_inputs is the
    canonical mixed-source bundle; legacy top-level fields remain compatible.
    """
    if not isinstance(payload, dict):
        raise DirectorServiceError("INVALID_INPUT", "请求体必须是 JSON 对象")

    inputs = payload.get("director_inputs") or {}
    if not isinstance(inputs, dict):
        raise DirectorServiceError("INVALID_INPUT", "director_inputs 必须是对象")

    nested_script = inputs.get("script")
    if isinstance(nested_script, dict):
        nested_script = nested_script.get("text")
    brief = _text(inputs.get("brief") or payload.get("brief")
                  or payload.get("creative_brief"), "brief")
    script = _text(nested_script or payload.get("script"), "script")
    assets = inputs.get("assets") or {}
    if not isinstance(assets, dict):
        raise DirectorServiceError("INVALID_INPUT", "director_inputs.assets 必须是对象")

    has_sources = bool(
        script or assets.get("asset_refs") or inputs.get("references")
        or inputs.get("articles") or inputs.get("software_project")
    )
    if not brief and not has_sources:
        raise DirectorServiceError(
            "MISSING_SOURCE",
            "请告诉 AI 想做什么，并可同时附加文案、素材、参考视频或软件项目",
        )

    from video_director import director_inputs as di
    from video_director import workflow_run as wf

    pid = _project_id(payload.get("project_id") or inputs.get("project_id"))
    if not pid:
        pid = f"web-{uuid.uuid4().hex[:8]}"

    merged = dict(payload)
    merged["project_id"] = pid
    merged["brief"] = brief
    merged["script"] = script
    merged_inputs = dict(inputs)
    merged_inputs["brief"] = brief
    merged_inputs["script"] = {
        "text": script,
        "source": "user" if script else "director",
        "authority": "authoritative" if script else "generated",
        "editable": True,
    }
    merged_inputs["platform"] = inputs.get("platform") or payload.get("platform") or ""
    merged_inputs["duration"] = inputs.get("duration") or payload.get("duration") or ""
    merged_inputs["style"] = (
        inputs.get("style") or payload.get("style") or payload.get("style_intent") or ""
    )
    merged["director_inputs"] = merged_inputs

    bundle = di.build_bundle(merged)

    # Resolve language intent before choosing dimensions or rebuilding the final input bundle.
    # The current WebUI sends select defaults too; _explicit_fields distinguishes defaults
    # from settings the user deliberately changed.
    from video_director import director_intent
    # 2026-10-27 用户裁决：create HTTP 线程只跑规则层（毫秒级秒回），LLM 泛化层
    # 由后台 run 的 understand 节点 merge_llm_intents 补齐——同步 resolve_director_intents
    # 在网关挂死时把 create 拖到 15s+（前端 TypeError: Failed to fetch，任务提交不出）。
    intent = director_intent.resolve_rule_intents(brief)
    raw_explicit_fields = payload.get("_explicit_fields")
    explicit_fields_known = isinstance(raw_explicit_fields, list)
    explicit_fields = {
        str(item) for item in raw_explicit_fields
        if isinstance(item, str) and item.strip()
    } if explicit_fields_known else set()

    def choose_setting(field: str, raw_value: Any, intent_field: str | None = None,
                       default: Any = None) -> Any:
        key = intent_field or field
        if explicit_fields_known:
            if field in explicit_fields:
                return raw_value if raw_value is not None else default
            parsed = intent.get(key)
            if parsed is not None and parsed != "":
                return parsed
            return raw_value if raw_value is not None and raw_value != "" else default
        # Compatibility for existing MCP/CLI/API clients which do not send field provenance.
        if raw_value is not None and raw_value != "":
            return raw_value
        parsed = intent.get(key)
        if parsed is not None and parsed != "":
            return parsed
        return default

    raw_resolution = str(payload.get("resolution") or "").strip()
    raw_platform = str(payload.get("platform") or "").strip()
    if explicit_fields_known:
        if "resolution" in explicit_fields:
            resolution = raw_resolution
        elif "platform" in explicit_fields:
            # The platform selection updates the resolution control programmatically;
            # that derived value must not be mistaken for a separate resolution choice.
            resolution = ""
        else:
            resolution = str(intent.get("resolution") or "").strip()
        platform = raw_platform if "platform" in explicit_fields else ""
    else:
        resolution = raw_resolution or str(intent.get("resolution") or "").strip()
        platform = raw_platform
    platform_dims = {
        "douyin": (1080, 1920),
        "video_channel": (1080, 1920),
        "bilibili": (1920, 1080),
        "xhs": (1080, 1440),
        "square": (1080, 1080),
    }
    if resolution:
        if not re.fullmatch(r"\d{2,5}x\d{2,5}", resolution):
            raise DirectorServiceError(
                "INVALID_RESOLUTION", "resolution 格式应为宽x高，例如 1080x1920"
            )
        rw, rh = (int(part) for part in resolution.split("x", 1))
        if rw <= 0 or rh <= 0:
            raise DirectorServiceError("INVALID_RESOLUTION", "resolution 的宽高必须大于 0")
    else:
        resolved_dims = platform_dims.get(platform)
        rw = rh = None
        if resolved_dims:
            rw, rh = resolved_dims
        # 2026-10-18：无显式分辨率时优先匹配用户第一个视频素材的宽高（16:9 素材
        # 不该被默认成 9:16）。只在用户确实指定了平台画幅时用平台默认；否则
        # probe 源素材，均失败才回 config.DEFAULT_RESOLUTION。
        if rw is None:
            for ref in (bundle.get("assets") or {}).get("asset_refs") or []:
                f = (ref or {}).get("file")
                if not f or str(ref.get("type") or "").lower() != "video":
                    continue
                try:
                    from video_director import prober
                    pr = prober.probe_media(str(f))
                    if pr and pr.width and pr.height:
                        rw, rh = int(pr.width), int(pr.height)
                        break
                except Exception:
                    continue
        if rw is None:
            rw, rh = config.DEFAULT_RESOLUTION

    confirm_mode = str(payload.get("confirm_mode") or "preview").strip()
    if confirm_mode not in {"preview", "direct"}:
        raise DirectorServiceError(
            "INVALID_CONFIRM_MODE", "confirm_mode 仅支持 preview 或 direct"
        )
    raw_fps = payload.get("fps")
    raw_bgm_volume = choose_setting("bgm_volume", payload.get("bgm_volume"), default=0.3)
    raw_narration_volume = choose_setting(
        "narration_volume", payload.get("narration_volume"), default=1.0
    )
    try:
        fps = int(30 if raw_fps is None or raw_fps == "" else raw_fps)
        bgm_volume = float(0.3 if raw_bgm_volume is None or raw_bgm_volume == "" else raw_bgm_volume)
        narration_volume = float(1.0 if raw_narration_volume is None or raw_narration_volume == "" else raw_narration_volume)
    except (TypeError, ValueError):
        raise DirectorServiceError("INVALID_SETTINGS", "fps 或音量参数格式不正确") from None
    if fps < 1 or fps > 120:
        raise DirectorServiceError("INVALID_SETTINGS", "fps 必须在 1 到 120 之间")
    if not 0 <= bgm_volume <= 2 or not 0 <= narration_volume <= 2:
        raise DirectorServiceError("INVALID_SETTINGS", "音量参数必须在 0 到 2 之间")

    constraints = inputs.get("constraints") or {}
    visual_source = (
        payload.get("visual_source")
        or (constraints.get("visual_source") if isinstance(constraints, dict) else None)
        or "auto"
    )
    if visual_source not in {"auto", "mixed", "online", "local"}:
        raise DirectorServiceError(
            "INVALID_VISUAL_SOURCE", "visual_source 仅支持 auto、mixed、online、local"
        )

    # Rebuild the input bundle and plan from the same normalized settings the executor receives.
    effective_duration = choose_setting(
        "duration", inputs.get("duration") or payload.get("duration") or "", default=""
    )
    effective_style = choose_setting(
        "style",
        inputs.get("style") or payload.get("style") or payload.get("style_intent") or "",
        intent_field="style",
        default="",
    )
    effective_platform = platform
    merged["platform"] = effective_platform
    merged["duration"] = effective_duration
    merged["style"] = effective_style
    merged_inputs["platform"] = effective_platform
    merged_inputs["duration"] = effective_duration
    merged_inputs["style"] = effective_style
    merged["director_inputs"] = merged_inputs
    bundle = di.build_bundle(merged)
    plan = di.build_plan(bundle, brief or script or "AI Director 创作")

    raw_sfx = payload.get("sfx")
    if explicit_fields_known and "sfx" in explicit_fields:
        sfx_value = raw_sfx
    else:
        sfx_value = raw_sfx or payload.get("sfx_choice")
    clip_duration_value = choose_setting(
        "clip_duration", payload.get("clip_duration"), default=None
    )
    clip_speed_value = choose_setting("clip_speed", payload.get("clip_speed"), default=None)
    try:
        clip_duration_value = (
            float(clip_duration_value) if clip_duration_value not in (None, "") else None
        )
        clip_speed_value = float(clip_speed_value) if clip_speed_value not in (None, "") else None
    except (TypeError, ValueError):
        raise DirectorServiceError(
            "INVALID_SETTINGS", "单镜头时长和画面速度必须是数字"
        ) from None
    if clip_duration_value is not None and clip_duration_value <= 0:
        raise DirectorServiceError("INVALID_SETTINGS", "单镜头时长必须大于 0")
    if clip_speed_value is not None and clip_speed_value <= 0:
        raise DirectorServiceError("INVALID_SETTINGS", "画面速度必须大于 0")
    spec = {
        "project_id": pid, "script": script, "creative_brief": brief, "brief": brief,
        "conversation_id": str(payload.get("conversation_id") or ""),
        "_explicit_fields": sorted(explicit_fields),
        "mode": payload.get("mode") or "auto",
        "voice": choose_setting("voice", payload.get("voice"), default="zh-CN-XiaoxiaoNeural"),
        "voice_mode": choose_setting("voice_mode", payload.get("voice_mode"), default="auto"),
        "voice_rate": choose_setting("voice_rate", payload.get("voice_rate"), default="1.0"),
        "style_intent": effective_style,
        "platform": effective_platform,
        "width": rw, "height": rh, "fps": fps,
        "bgm": choose_setting("bgm", payload.get("bgm"), default="auto"),
        "bgm_volume": bgm_volume,
        "narration_volume": narration_volume,
        "sfx_plan": payload.get("sfx_plan"),
        # 音效自动搜索（Openverse 免 Key：freesound 源 CC0，本地未命中自动搜）
        "auto_sfx_search": bool(payload.get("auto_sfx_search", False)),
        # 具体音效选择（前端 cf-sfx；'openverse-auto' 由 workflow_run 归一）
        "sfx": choose_setting("sfx", sfx_value, default="auto"),
        "subtitle_mode": choose_setting("subtitle_mode", payload.get("subtitle_mode"), default="plain"),
        "fit_mode": choose_setting("fit_mode", payload.get("fit_mode"), default="cover"),
        "clip_duration": clip_duration_value,
        "clip_speed": clip_speed_value,
        "concat_mode": choose_setting("concat_mode", payload.get("concat_mode"), default="sequential"),
        "transition_mode": choose_setting("transition_mode", payload.get("transition_mode"), default="cut"),
        "match_materials_to_script": bool(payload.get("match_materials_to_script", True)),
        "stop_at": str(payload.get("stop_at") or "video"),
        "watermark": choose_setting("watermark", payload.get("watermark"), default=""),
        "title": choose_setting("title", payload.get("title"), default=""),
        "lower_third_name": choose_setting("lower_third_name", payload.get("lower_third_name"), default=""),
        "lower_third_role": choose_setting("lower_third_role", payload.get("lower_third_role"), default=""),
        "confirm_mode": confirm_mode, "auto_confirm": confirm_mode == "direct",
        "visual_source": visual_source,
        "duration": effective_duration,
        "video_paths": payload.get("video_paths") or [],
        "image_paths": payload.get("image_paths") or [],
        "narration_upload": payload.get("narration_upload") or "",
        "director_inputs": bundle,
        # 意图解析出的目标时长（秒）——脚本生成时注入 constraints 控制文案长度。
        # 挂在 spec 上而非 dirinputs（dirinputs 的 creative_settings.duration 语义是
        # 「总时长」字符串，意图值是秒的规范化结果，二者不要混同）。
        "_intent_duration_s": str(effective_duration or ""),
        "_unresolved": intent.get("_unresolved") or "",
    }
    run_id = wf.run_director_create_async(spec, plan=plan)
    return {
        "ok": True, "run_id": run_id, "project_id": pid, "script": script,
        "director_plan": plan, "director_inputs": bundle,
    }


def analyze_reference_video(video_path: str, use_llm: bool = True) -> dict:
    """Analyze an explicit local reference video and return a creation-ready reference object."""
    from pathlib import Path

    path_text = _text(video_path, "video_path")
    if not path_text:
        raise DirectorServiceError("MISSING_REFERENCE_VIDEO", "缺少参考视频本地路径")
    path = Path(path_text).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise DirectorServiceError("REFERENCE_NOT_FOUND", "参考视频文件不存在或不可读取", 404) from None
    if not path.is_file():
        raise DirectorServiceError("REFERENCE_NOT_FOUND", "参考视频路径不是文件", 404)
    allowed_extensions = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg"}
    if path.suffix.lower() not in allowed_extensions:
        raise DirectorServiceError(
            "UNSUPPORTED_REFERENCE_TYPE",
            "参考素材必须是常见视频文件（mp4/mov/m4v/mkv/webm/avi/mpeg）",
            415,
        )

    scratch_id = f"reference-{uuid.uuid4().hex[:12]}"
    output_dir = config.project_dir(scratch_id)
    try:
        from video_director import reference as reference_module
        report = reference_module.reference_analyzer(
            str(path), str(output_dir), use_llm=bool(use_llm)
        )
    except Exception as exc:
        raise DirectorServiceError(
            "REFERENCE_ANALYSIS_FAILED", f"参考视频分析失败：{str(exc)[:240]}", 422
        ) from exc

    reference = {
        "id": f"ref-{scratch_id}",
        "project_id": scratch_id,
        "video": report.get("video") or str(path),
        "report_path": report.get("report_path") or "",
        "report": report,
        "apply": ["pace", "density", "motion", "transition", "shot_grammar"],
    }
    return {
        "ok": True,
        "reference": reference,
        "report": report,
        "hint": "将返回的 reference 对象放入 director_inputs.references，以便创建任务复用同一份分析结果。",
    }


def get_director_status(run_id: str) -> dict:
    """Return a durable WorkflowRun snapshot by run_id."""
    run_id = _text(run_id, "run_id")
    if not run_id:
        raise DirectorServiceError("MISSING_RUN_ID", "缺少 run_id")
    from video_director import workflow_run as wf
    run = wf.get_run(run_id)
    if run is None:
        raise DirectorServiceError("RUN_NOT_FOUND", "任务不存在或已清理", 404)
    return {"ok": True, "run": run}


def retry_director_project(project_id: str) -> dict:
    """Retry a failed Director project using its persisted input bundle."""
    project_id = _project_id(project_id)
    if not project_id:
        raise DirectorServiceError("MISSING_PROJECT", "缺少 project_id")
    from video_director import store, workflow_run as wf
    from video_director import director_inputs as di
    if store.get_project(project_id) is None:
        raise DirectorServiceError("PROJECT_NOT_FOUND", "项目不存在", 404)
    if not di.load_bundle(project_id):
        raise DirectorServiceError(
            "DIRECTOR_INPUT_MISSING",
            "该项目没有 AI 导演输入记录，可能来自旧流程，无法重新执行",
            409,
        )
    run_id = wf.run_director_retry_async(project_id)
    if not run_id:
        raise DirectorServiceError(
            "RETRY_REJECTED", "项目有正在执行的任务，或当前状态不允许重新执行", 409
        )
    return {"ok": True, "run_id": run_id, "project_id": project_id}


def continue_director_project(project_id: str, target: str = "preview") -> dict:
    """Continue an existing Director run at a supported persisted checkpoint."""
    project_id = _project_id(project_id)
    target = _text(target or "preview", "target").lower()
    if not project_id:
        raise DirectorServiceError("MISSING_PROJECT", "缺少 project_id")
    if target not in {"preview", "video"}:
        raise DirectorServiceError("INVALID_TARGET", "target 仅支持 preview 或 video")
    from video_director import workflow_run as wf
    run_id = wf.run_director_continue_async(project_id, target=target)
    if not run_id:
        raise DirectorServiceError(
            "CONTINUE_REJECTED", "当前项目没有可继续的 AI Director 检查点", 409
        )
    return {"ok": True, "run_id": run_id, "project_id": project_id, "target": target}
