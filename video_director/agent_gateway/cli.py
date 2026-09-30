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

Gate（§31.3）：CLI execute 与 UI execute 产生同样的 Revision / Composition
——因为两者都走 capability_gateway → tool_executor → editor.execute_command。
"""

from __future__ import annotations

import argparse
import json
import sys

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

    p_caps = sub.add_parser("capabilities", help="能力清单")
    p_caps.add_argument("action", choices=["list", "describe"])
    p_caps.add_argument("name", nargs="?", default="", help="describe 用的 capability id")

    args = parser.parse_args(argv)
    if not args.verb:
        parser.print_help()
        return 2

    try:
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
