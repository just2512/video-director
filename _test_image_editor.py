"""_test_image_editor.py — IMAGE_EDITOR 图片智能编辑成动画验证脚本（临时，可留）。

运行方式（Windows）：
    set IMAGEIO_FFMPEG_EXE=D:\\autocut\\tools\\ffmpeg-btbn\\ffmpeg-master-latest-win64-gpl\\bin\\ffmpeg.exe
    set FFPROBE_EXE=D:\\autocut\\tools\\ffmpeg-btbn\\ffmpeg-master-latest-win64-gpl\\bin\\ffprobe.exe
    D:\\autocut\\venv\\Scripts\\python.exe _test_image_editor.py

断言全部走文件/probe（ffprobe rawvideo 像素统计），禁止截图。
"""

import os
import sys
import tempfile
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

from video_director import image_editor, prober  # noqa: E402

failures = []


def check(name, cond, detail=""):
    mark = "OK  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        failures.append(name)


def make_test_image(color: str, path: str, w: int = 640, h: int = 480):
    """用 ffmpeg lavfi 生成纯色测试图。"""
    ff = prober._resolve_ffmpeg()
    import subprocess
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d=0.1",
        "-frames:v", "1",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"生成测试图失败: {r.stderr.decode(errors='replace')}")
    return path


def make_gradient_image(path: str, w: int = 640, h: int = 480):
    """用 ffmpeg lavfi 生成渐变测试图。"""
    ff = prober._resolve_ffmpeg()
    import subprocess
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"gradients=s={w}x{h}:c0=0x0000FF:c1=0xFF0000",
        "-frames:v", "1",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"生成渐变图失败: {r.stderr.decode(errors='replace')}")
    return path


def test_analyze_image(tmpdir: str):
    """测试 analyze_image 返回主色/尺寸/brightness/saliency。"""
    print("\n[1] analyze_image", flush=True)
    img_red = make_test_image("red", str(Path(tmpdir) / "red.png"))
    img_green = make_test_image("green", str(Path(tmpdir) / "green.png"))
    img_gradient = make_gradient_image(str(Path(tmpdir) / "gradient.png"))

    for label, img, expect_brightness_low, expect_brightness_high in [
        ("red", img_red, 50, 100),
        ("green", img_green, 50, 100),
        ("gradient", img_gradient, 0, 256),
    ]:
        analysis = image_editor.analyze_image(img)
        check(f"{label}: width 非空", analysis["width"] is not None and analysis["width"] > 0,
              f"width={analysis['width']}")
        check(f"{label}: height 非空", analysis["height"] is not None and analysis["height"] > 0,
              f"height={analysis['height']}")
        check(f"{label}: mean_color 3元素",
              isinstance(analysis["mean_color"], list) and len(analysis["mean_color"]) == 3,
              f"mean_color={analysis['mean_color']}")
        check(f"{label}: brightness 在合理范围",
              expect_brightness_low <= analysis["brightness"] <= expect_brightness_high,
              f"brightness={analysis['brightness']}")
        check(f"{label}: dominant_colors 非空",
              isinstance(analysis["dominant_colors"], list) and len(analysis["dominant_colors"]) > 0,
              f"dominant_colors={analysis['dominant_colors']}")
        check(f"{label}: saliency_region 有四元组",
              all(k in analysis["saliency_region"] for k in ("x", "y", "w", "h")),
              f"saliency_region={analysis['saliency_region']}")

    # 验证主色：纯红图的 mean_color R 应远大于 G 和 B
    red_analysis = image_editor.analyze_image(img_red)
    r, g, b = red_analysis["mean_color"]
    check("纯红图 mean_color R > G+50", r > g + 50, f"R={r}, G={g}")
    check("纯红图 mean_color R > B+50", r > b + 50, f"R={r}, B={b}")

    return [img_red, img_green, img_gradient]


def test_crop_region(tmpdir: str, img_paths: list):
    """测试 crop_region 裁剪关键元素。"""
    print("\n[2] crop_region", flush=True)
    img = img_paths[0]  # red 640x480
    analysis = image_editor.analyze_image(img)
    region = analysis["saliency_region"]
    out_path = str(Path(tmpdir) / "cropped.png")
    result = image_editor.crop_region(img, region, out_path)
    check("裁剪产物存在", os.path.isfile(result), result)
    # probe 裁剪后的尺寸
    probe = prober.probe_image(result)
    check("裁剪后尺寸 > 0",
          probe.width is not None and probe.width > 0 and probe.height is not None and probe.height > 0,
          f"cropped size={probe.width}x{probe.height}")
    # 裁剪后的尺寸应 <= 原图
    check("裁剪后宽 <= 原图宽",
          probe.width <= analysis["width"],
          f"cropped_w={probe.width} vs src_w={analysis['width']}")
    check("裁剪后高 <= 原图高",
          probe.height <= analysis["height"],
          f"cropped_h={probe.height} vs src_h={analysis['height']}")


def test_render_image_animation(tmpdir: str, img_paths: list):
    """测试 render_image_animation 单张图渲染出段。"""
    print("\n[3] render_image_animation", flush=True)
    img = img_paths[2]  # gradient 图（有视觉变化，动画更明显）
    out_path = str(Path(tmpdir) / "anim_seg.mp4")
    target_duration = 3.0
    result = image_editor.render_image_animation(
        img=img,
        text="这是测试文案",
        duration=target_duration,
        out_path=out_path,
        w=1080, h=1920, fps=30,
        motion="kenburns_zoom_in",
        text_position="bottom",
        font_size=56,
    )
    check("动画 segment 存在", os.path.isfile(result), result)
    probe = prober.probe_media(result)
    check("segment 有视频流", probe.has_video_stream, f"codec={probe.codec}")
    check("segment 时长 ≈ 目标",
          probe.duration is not None and abs(probe.duration - target_duration) <= 0.15,
          f"duration={probe.duration:.3f}s vs target={target_duration:.3f}s")
    check("segment 分辨率 1080x1920",
          probe.width == 1080 and probe.height == 1920,
          f"{probe.width}x{probe.height}")
    check("segment 无音轨（纯视觉段）",
          not probe.has_audio_stream,
          f"has_audio={probe.has_audio_stream}")

    # 测试所有 5 种动效都能渲染
    for motion in image_editor._ALL_MOTIONS:
        seg_path = str(Path(tmpdir) / f"anim_{motion}.mp4")
        try:
            image_editor.render_image_animation(
                img=img, text="测试", duration=2.0, out_path=seg_path,
                w=1080, h=1920, fps=30, motion=motion,
            )
            check(f"动效 {motion} 渲染成功", os.path.isfile(seg_path), seg_path)
        except Exception as exc:
            check(f"动效 {motion} 渲染成功", False, f"error: {exc}")


def test_compose_images_video(tmpdir: str, img_paths: list):
    """测试 compose_images_video 3 张图 + 短文案 → final.mp4，QC PASS。"""
    print("\n[4] compose_images_video", flush=True)
    script = (
        "清晨的阳光洒满房间。"
        "窗外的绿叶随风摇曳。"
        "天空从蓝变红，黄昏降临。"
    )
    pid = f"img-edit-test-{time.strftime('%H%M%S')}"
    print(f"文案（{len(script)} 字）: {script}", flush=True)
    print(f"图片: {[Path(p).name for p in img_paths]}", flush=True)

    result = image_editor.compose_images_video(
        script=script,
        image_paths=img_paths,
        project_id=pid,
    )

    # 1. 产物存在 + QC
    check("final.mp4 存在", Path(result.final_path).is_file(), result.final_path)
    verdict = result.qc_report.get("verdict")
    check("QC verdict == PASS", verdict == "PASS", f"verdict={verdict}")
    failed_checks = [c["check"] for c in result.qc_report.get("checks", [])
                     if c["result"] == "FAIL"]
    check("QC 无 FAIL 项", not failed_checks, f"failed={failed_checks}")

    # 2. 时长 ≈ 旁白时长
    probe = prober.probe_media(result.final_path)
    check("final 时长 ≈ 旁白时长",
          probe.duration is not None
          and abs(probe.duration - result.narration_duration) <= 0.20,
          f"final={probe.duration:.3f}s vs narration={result.narration_duration:.3f}s")
    check("分辨率 1080x1920",
          probe.width == 1080 and probe.height == 1920,
          f"{probe.width}x{probe.height}@{probe.fps}")
    check("含音轨", probe.has_audio_stream, f"audio={probe.has_audio_stream}")

    # 3. 分析结果字段非空
    check("analysis 非空（3 张图）",
          isinstance(result.analysis, list) and len(result.analysis) == 3,
          f"analysis len={len(result.analysis) if isinstance(result.analysis, list) else 'N/A'}")
    for i, a in enumerate(result.analysis):
        check(f"analysis[{i}] mean_color 3元素",
              isinstance(a.get("mean_color"), list) and len(a["mean_color"]) == 3,
              f"mean_color={a.get('mean_color')}")
        check(f"analysis[{i}] saliency_region 有四元组",
              all(k in a.get("saliency_region", {}) for k in ("x", "y", "w", "h")),
              f"saliency={a.get('saliency_region')}")

    # 4. 项目目录结构
    base = Path(result.project_dir)
    for rel in ("plans/scenes.json", "plans/run.json",
                "plans/image_assignments.json",
                "assets/image_analysis.json",
                "render/segments/0001.mp4",
                "render/master.srt",
                "qc/deterministic.json"):
        check(f"产物 {rel} 存在", (base / rel).is_file())

    # 5. 每场景段存在 + 动效轮换（3 场景对应 3 种不同动效）
    assignments_path = base / "plans" / "image_assignments.json"
    if assignments_path.is_file():
        import json
        assignments = json.loads(assignments_path.read_text(encoding="utf-8"))
        motions_used = [a["motion"] for a in assignments]
        check("3 场景动效轮换（不全相同）",
              len(set(motions_used)) > 1,
              f"motions={motions_used}")

    print(f"\n  final_path: {result.final_path}", flush=True)
    print(f"  narration_duration: {result.narration_duration:.3f}s (final {probe.duration:.3f}s)", flush=True)
    print(f"  qc: {result.qc_report.get('verdict')}", flush=True)
    if result.warnings:
        print(f"  warnings: {result.warnings}", flush=True)


def main():
    print("=" * 60, flush=True)
    print("IMAGE_EDITOR 图片智能编辑成动画 验证", flush=True)
    print("=" * 60, flush=True)

    tmpdir = tempfile.mkdtemp(prefix="img_edit_test_")
    print(f"临时目录: {tmpdir}", flush=True)

    # 1. analyze_image
    img_paths = test_analyze_image(tmpdir)

    # 2. crop_region
    test_crop_region(tmpdir, img_paths)

    # 3. render_image_animation
    test_render_image_animation(tmpdir, img_paths)

    # 4. compose_images_video（端到端）
    test_compose_images_video(tmpdir, img_paths)

    print("\n" + "=" * 60, flush=True)
    if failures:
        print(f"RESULT: {len(failures)} 项失败: {failures}", flush=True)
        sys.exit(1)
    print("RESULT: 全部断言通过", flush=True)


if __name__ == "__main__":
    main()
