"""临时验证脚本：capabilities.py 落地验证（可留作回归）。"""

import sys

sys.path.insert(0, r"D:\autocut\video-director\.claude\worktrees\vd-full-parallel")

from video_director.capabilities import (  # noqa: E402
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    STATUS_MISSING,
    capability_report,
    degradation_explanation,
    is_capable,
    preflight,
    registry,
)

failures = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main():
    caps = registry()

    # 1. 状态断言
    check("tts AVAILABLE", caps["tts"].status == STATUS_AVAILABLE, caps["tts"].status)
    check(
        "alignment AVAILABLE",
        caps["alignment"].status == STATUS_AVAILABLE,
        caps["alignment"].status,
    )
    check(
        "renderer AVAILABLE",
        caps["renderer"].status == STATUS_AVAILABLE,
        caps["renderer"].status,
    )
    check("vision DEGRADED", caps["vision"].status == STATUS_DEGRADED, caps["vision"].status)
    check("matting MISSING", caps["matting"].status == STATUS_MISSING, caps["matting"].status)

    # 2. preflight 断言
    ok, blocked, degraded = preflight(["tts", "renderer"])
    check("preflight tts+renderer ok", ok is True and blocked == [], f"blocked={blocked}")
    check("preflight tts+renderer 无 degraded", degraded == [], f"degraded={degraded}")

    ok2, blocked2, degraded2 = preflight(["matting"])
    check(
        "preflight matting blocked",
        ok2 is False and "matting" in blocked2,
        f"blocked={blocked2}",
    )

    # 3. vision 降级不阻塞
    ok3, blocked3, degraded3 = preflight(["tts", "renderer", "vision"])
    check(
        "preflight vision degraded 不阻塞",
        ok3 is True and "vision" in degraded3 and blocked3 == [],
        f"blocked={blocked3} degraded={degraded3}",
    )

    # 4. is_capable
    check("is_capable(tts)", is_capable("tts") is True)
    check("is_capable(matting) False", is_capable("matting") is False)
    check("is_capable(vision) True（DEGRADED 算可用）", is_capable("vision") is True)

    # 5. degradation_explanation
    exp_vision = degradation_explanation("vision")
    check(
        "degradation_explanation(vision) 含文件名语义",
        "文件名语义" in exp_vision,
        exp_vision[:60],
    )
    exp_matting = degradation_explanation("matting")
    check(
        "degradation_explanation(matting) 含 BLOCKED",
        "BLOCKED" in exp_matting,
        exp_matting[:60],
    )

    # 6. capability_report 打印不报错
    report = capability_report()
    check("capability_report 非空", len(report) > 100, f"{len(report)} chars")
    check("report 含 MISSING", "MISSING" in report)
    check("report 含 DEGRADED", "DEGRADED" in report)
    print("\n" + report)

    print("\n" + "=" * 50)
    if failures:
        print(f"共 {len(failures)} 项失败: {failures}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
