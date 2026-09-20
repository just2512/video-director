"""_test_script_only.py — SCRIPT_ONLY 纯文案模式端到端验证（临时，可留）。

运行方式（Windows）：
    set IMAGEIO_FFMPEG_EXE=D:\\autocut\\tools\\ffmpeg-btbn\\ffmpeg-master-latest-win64-gpl\\bin\\ffmpeg.exe
    set FFPROBE_EXE=D:\\autocut\\tools\\ffmpeg-btbn\\ffmpeg-master-latest-win64-gpl\\bin\\ffprobe.exe
    D:\\autocut\\venv\\Scripts\\python.exe _test_script_only.py

断言全部走文件/DOM（ffprobe rawvideo 像素统计），禁止截图。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault(
    "IMAGEIO_FFMPEG_EXE",
    r"D:\autocut\tools\ffmpeg-btbn\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
)
os.environ.setdefault(
    "FFPROBE_EXE",
    r"D:\autocut\tools\ffmpeg-btbn\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe",
)

from video_director import prober, script_only  # noqa: E402

failures = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        failures.append(name)


def bright_stats(video: str, t: float, w=1080, h=1920, y_limit=1500) -> tuple:
    """t 秒处抽帧（全分辨率灰度），返回 (亮像素数, 亮像素质心y, 行带范围)。

    只统计 y < y_limit 的区域（正文文字带）——底部 1500-1920 是字幕烧录区，
    混入统计会把质心拉低造成误判（首测 FAIL 的根因是本函数，非产品代码）。
    """
    import subprocess
    ff = prober._resolve_ffmpeg()
    cmd = [
        ff, "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
        "-vf", "format=gray", "-f", "rawvideo", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    data = r.stdout[: w * h]
    if len(data) < w * h:
        return (0, -1, None)
    rows = {}
    bright = 0
    for y in range(min(h, y_limit)):
        cnt = 0
        for x in range(0, w, 4):  # x 步长 4 抽样
            if data[y * w + x] > 100:
                cnt += 1
                bright += 1
        if cnt >= 2:
            rows[y] = cnt
    if not rows:
        return (0, -1, None)
    ys = sorted(rows)
    centroid = sum(y * c for y, c in rows.items()) / sum(rows.values())
    return (bright * 4, centroid, (ys[0], ys[-1]))


def main():
    script = (
        "清晨的城市慢慢醒来。"
        "地铁穿过隧道，把灯光带向远方。"
        "有人在站台上读诗，有人在早餐店排队。"
        "每一个平凡的早晨，都藏着细小的光。"
    )
    pid = f"script-only-test-{time.strftime('%H%M%S')}"
    print(f"文案（{len(script)} 字）: {script}", flush=True)

    result = script_only.compose_script_only(script, project_id=pid)

    # 1. 产物存在 + QC
    check("final.mp4 存在", Path(result.final_path).is_file(), result.final_path)
    verdict = result.qc_report.get("verdict")
    check("QC verdict == PASS", verdict == "PASS", f"verdict={verdict}")
    failed_checks = [c["check"] for c in result.qc_report.get("checks", [])
                     if c["result"] == "FAIL"]
    check("QC 无 FAIL 项", not failed_checks, f"failed={failed_checks}")

    # 2. 时长 ≈ 旁白时长（QC 已检查，双保险用 probe 直接对账）
    probe = prober.probe_media(result.final_path)
    check(
        "final 时长 ≈ 旁白时长",
        probe.duration is not None
        and abs(probe.duration - result.narration_duration) <= 0.20,
        f"final={probe.duration:.3f}s vs narration={result.narration_duration:.3f}s",
    )
    check("分辨率 1080x1920",
          probe.width == 1080 and probe.height == 1920,
          f"{probe.width}x{probe.height}@{probe.fps}")
    check("含音轨", probe.has_audio_stream, f"audio={probe.has_audio_stream}")

    # 3. 视觉层文件断言：每个场景时段抽帧有亮像素（文字），且不在画面底部
    #    （底部是字幕，正文卡文字应在垂直中部）
    scenes = result.ir["scenes"]
    check("IR 场景数 == 4", len(scenes) == 4, f"scenes={len(scenes)}")
    check("IR 全部 text 层",
          all(s["layers"][0]["type"] == "text" for s in scenes),
          "layers types=" + ",".join(s["layers"][0]["type"] for s in scenes))
    text_mid_ok = True
    for s in scenes:
        mid = s["start"] + s["duration"] / 2
        bright, cy, band = bright_stats(result.final_path, mid)
        if bright < 200 or cy < 0 or cy > 1200:
            text_mid_ok = False
            print(f"    scene {s['id']} @{mid:.2f}s bright={bright} cy={cy} band={band}",
                  flush=True)
    check("每场景中点抽帧有居中正文文字（垂直中部亮像素）", text_mid_ok,
          "文字带质心应在 y<1200（垂直中部），非底部字幕")

    # 4. 字幕层文件断言：底部区域（y>1500）在场景中点有亮像素（烧录字幕存在）
    sub_ok = True
    for s in scenes:
        mid = s["start"] + s["duration"] / 2
        import subprocess
        ff = prober._resolve_ffmpeg()
        cmd = [ff, "-ss", f"{mid:.2f}", "-i", result.final_path, "-frames:v", "1",
               "-vf", "crop=1080:420:0:1500,format=gray", "-f", "rawvideo", "-"]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        data = r.stdout[: 1080 * 420]
        bright = sum(1 for v in data[::4] if v > 100)
        if bright < 100:
            sub_ok = False
            print(f"    scene {s['id']} 底部亮像素={bright}", flush=True)
    check("每场景中点底部区域有字幕亮像素", sub_ok)

    # 5. project 目录结构（IR/SRT/scene 段落产物落盘）
    base = Path(result.project_dir)
    for rel in ("plans/composition.json", "plans/run.json",
                "render/master.srt", "render/segments/0001.mp4",
                "qc/deterministic.json"):
        check(f"产物 {rel} 存在", (base / rel).is_file())

    print()
    if failures:
        print(f"RESULT: {len(failures)} 项失败: {failures}")
        sys.exit(1)
    print("RESULT: 全部断言通过")
    print(f"  final_path: {result.final_path}")
    print(f"  narration_duration: {result.narration_duration:.3f}s (final {probe.duration:.3f}s)")
    print(f"  qc: {result.qc_report.get('verdict')}")


if __name__ == "__main__":
    main()
