"""AG-002 CLI Gateway：creative-os <verb> 对等入口（§52）。

用法（§19 CLI 建议）：
    creative-os inspect [--project <id>]
    creative-os plan --project <id> --instruction "……"
    creative-os execute --project <id> --command watermark.add --target <id> [--json '{...}']
    creative-os preview --project <id>
    creative-os render --project <id>
    creative-os verify --project <id>
    creative-os watch [--project <id>]
    creative-os capabilities list
    creative-os capabilities describe watermark.add
    creative-os actions --project <id> [--selection '{...}']
    creative-os action --project <id> --action asset.search --json '{...}'
    creative-os director-create --input director-request.json
    creative-os director-status --run-id <run-id>
    creative-os director-reference --video ./reference.mp4 [--no-llm]
    creative-os director-retry --project <project-id>
    creative-os director-continue --project <project-id> --target preview|video

Gate（§31.3）：CLI execute 与 UI execute 产生同样的 Revision / Composition
——因为两者都走 capability_gateway → tool_executor → editor.execute_command。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from video_director.agent_gateway import GatewayError, dispatch
from video_director.capability_registry import describe_capability, list_capabilities


def _print(result: dict) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="creative-os",
        description="AI Video Creative OS 命令行入口（与 UI/MCP 同一内部执行链）")
    sub = parser.add_subparsers(dest="verb")

    p_inspect = sub.add_parser("inspect", help="查看项目/系统状态")
    p_inspect.add_argument("--project", default="", help="项目 id（空=系统摘要）")

    p_plan = sub.add_parser("plan", help="AI 规划（出提案，不直接落 Composition）")
    p_plan.add_argument("--project", required=True)
    p_plan.add_argument("--instruction", required=True, help="自然语言意图")

    p_exec = sub.add_parser("execute", help="执行单条能力（与 UI 同链）")
    p_exec.add_argument("--project", required=True)
    p_exec.add_argument("--command", required=True, help="capability id（如 watermark.add）")
    p_exec.add_argument("--target", default=None, help="目标场景/层 id")
    p_exec.add_argument("--json", dest="payload_json", default="{}",
                        help="payload JSON 字符串")

    p_prev = sub.add_parser("preview", help="生成草稿预览")
    p_prev.add_argument("--project", required=True)
    p_prev.add_argument("--scale", type=float, default=0.5)

    p_rend = sub.add_parser("render", help="终渲成片")
    p_rend.add_argument("--project", required=True)

    p_ver = sub.add_parser("verify", help="组合校验（composition + continuity）")
    p_ver.add_argument("--project", required=True)

    p_watch = sub.add_parser("watch", help="项目状态快照（长连接走 webui SSE）")
    p_watch.add_argument("--project", default="")

    p_actions = sub.add_parser("actions", help="列出项目可用的 Action Registry 动作")
    p_actions.add_argument("--project", required=True)
    p_actions.add_argument("--selection", default=None, help="可选目标 selection JSON 对象")

    p_action = sub.add_parser("action", help="执行 Action Registry 中的用户语义动作")
    p_action.add_argument("--project", required=True)
    p_action.add_argument("--action", dest="action_id", required=True, help="例如 creative.select_candidate、asset.search、generate.image")
    p_action.add_argument("--target", default="current_selection")
    p_action.add_argument("--json", dest="params_json", default="{}", help="动作参数 JSON 对象")
    p_action.add_argument("--selection", default=None, help="目标 selection JSON 对象")

    p_dreference = sub.add_parser("director-reference", help="分析本地参考视频并返回可复用的参考输入")
    p_dreference.add_argument("--video", dest="video_path", required=True)
    p_dreference.add_argument("--no-llm", action="store_true", help="仅使用确定性分析，不调用已配置的 LLM")

    p_dcreate = sub.add_parser("director-create", help="通过共享 Director 服务创建混合输入任务")
    create_input = p_dcreate.add_mutually_exclusive_group(required=True)
    create_input.add_argument("--input", dest="input_path", help="JSON 请求文件路径")
    create_input.add_argument("--json", dest="payload_json", help="JSON 请求对象字符串")

    p_dstatus = sub.add_parser("director-status", help="读取持久化 Director 任务状态")
    p_dstatus.add_argument("--run-id", required=True)

    p_dretry = sub.add_parser("director-retry", help="重试失败的 Director 项目")
    p_dretry.add_argument("--project", required=True)

    p_dcontinue = sub.add_parser("director-continue", help="从保存的 Director 检查点继续")
    p_dcontinue.add_argument("--project", required=True)
    p_dcontinue.add_argument("--target", choices=["preview", "video"], default="preview")

    p_caps = sub.add_parser("capabilities", help="能力清单")
    p_caps.add_argument("action", choices=["list", "describe"])
    p_caps.add_argument("name", nargs="?", default="", help="describe 用的 capability id")

    args = parser.parse_args(argv)
    if not args.verb:
        parser.print_help()
        return 2

    try:
        if args.verb == "actions":
            try:
                selection = json.loads(args.selection) if args.selection else None
                if selection is not None and not isinstance(selection, dict):
                    raise ValueError("--selection 必须是 JSON 对象")
            except json.JSONDecodeError as exc:
                _print({"ok": False, "code": "INVALID_JSON", "error": str(exc)})
                return 2
            except ValueError as exc:
                _print({"ok": False, "code": "INVALID_INPUT", "error": str(exc)})
                return 2
            result = dispatch("creative.actions.list", project_id=args.project,
                              selection=selection)
            _print(result)
            return 0 if result.get("ok") else 1

        if args.verb == "action":
            try:
                params = json.loads(args.params_json or "{}")
                selection = json.loads(args.selection) if args.selection else None
                if not isinstance(params, dict) or (selection is not None and not isinstance(selection, dict)):
                    raise ValueError("--json 和 --selection 必须是 JSON 对象")
            except json.JSONDecodeError as exc:
                _print({"ok": False, "code": "INVALID_JSON", "error": str(exc)})
                return 2
            except ValueError as exc:
                _print({"ok": False, "code": "INVALID_INPUT", "error": str(exc)})
                return 2
            result = dispatch("creative.action", project_id=args.project,
                              action=args.action_id, target=args.target,
                              params=params, selection=selection)
            _print(result)
            return 0 if result.get("ok") else 1

        if args.verb in {"director-create", "director-reference", "director-status", "director-retry", "director-continue"}:
            from video_director import director_service as service
            from video_director.director_service import DirectorServiceError
            if args.verb == "director-create":
                try:
                    raw = (Path(args.input_path).read_text(encoding="utf-8")
                           if args.input_path else args.payload_json)
                    payload = json.loads(raw or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("Director 创建 JSON 顶层必须是对象")
                except (json.JSONDecodeError, OSError, ValueError) as exc:
                    _print({"ok": False, "code": "INVALID_INPUT", "error": str(exc)})
                    return 2
            try:
                if args.verb == "director-create":
                    result = service.create_director_run(payload)
                elif args.verb == "director-status":
                    result = service.get_director_status(args.run_id)
                elif args.verb == "director-retry":
                    result = service.retry_director_project(args.project)
                elif args.verb == "director-reference":
                    result = service.analyze_reference_video(
                        args.video_path, use_llm=not args.no_llm
                    )
                else:
                    result = service.continue_director_project(args.project, target=args.target)
            except DirectorServiceError as exc:
                _print({"ok": False, "code": exc.code, "error": str(exc)})
                return 1
            _print(result)
            return 0 if result.get("ok") else 1

        if args.verb == "capabilities":
            if args.action == "list":
                caps = list_capabilities()
                names = sorted(caps.keys() if isinstance(caps, dict) else
                               [c["name"] if isinstance(c, dict) else str(c) for c in caps])
                _print({"capabilities": names, "count": len(names)})
            else:
                _print({"capability": describe_capability(args.name)})
            return 0
        if args.verb == "inspect":
            _print(dispatch("creative.inspect", project_id=args.project))
            return 0
        if args.verb == "plan":
            _print(dispatch("creative.plan", project_id=args.project,
                            instruction=args.instruction))
            return 0
        if args.verb == "execute":
            try:
                payload = json.loads(args.payload_json or "{}")
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"payload JSON 非法: {exc}\n")
                return 2
            _print(dispatch("creative.execute", project_id=args.project,
                            tool=args.command, payload=payload,
                            target_id=args.target))
            return 0
        if args.verb == "preview":
            _print(dispatch("creative.preview", project_id=args.project, scale=args.scale))
            return 0
        if args.verb == "render":
            _print(dispatch("creative.render", project_id=args.project))
            return 0
        if args.verb == "verify":
            _print(dispatch("creative.verify", project_id=args.project))
            return 0
        if args.verb == "watch":
            _print(dispatch("creative.watch", project_id=args.project))
            return 0
    except GatewayError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
