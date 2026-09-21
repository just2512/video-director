"""_test_product_promo.py — 产品广告片能力验证脚本。

用 D:/autocut/venv/Scripts/python.exe 执行：
    set IMAGEIO_FFMPEG_EXE=D:/autocut/tools/ffmpeg-btbn/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
    set FFPROBE_EXE=D:/autocut/tools/ffmpeg-btbn/ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe
    D:/autocut/venv/Scripts/python.exe _test_product_promo.py

验证三件：
    1. get_shot_cards() >= 10 张卡，categories 覆盖 opening/product/feature/transition/closing
    2. derive_visual_spec 用 ffmpeg 生成 2 张纯色测试图，返回主色/背景/文字色非空
    3. compose_product_promo 用 2 张测试图 + 短文案 → final.mp4，QC PASS，shots_used 场景>1 且动效有轮换
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# 设置 ffmpeg/ffprobe 环境变量（测试脚本负责，不依赖用户预置）
os.environ.setdefault(
    "IMAGEIO_FFMPEG_EXE",
    "D:/autocut/tools/ffmpeg-btbn/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe",
)
os.environ.setdefault(
    "FFPROBE_EXE",
    "D:/autocut/tools/ffmpeg-btbn/ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe",
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_director import shot_cards, product_promo, prober

PASS_COUNT = 0
FAIL_COUNT = 0


def check(cond: bool, label: str) -> None:
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  PASS  {label}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {label}")


def _make_test_image(color_rgb: tuple, path: str) -> str:
    """用 ffmpeg lavfi color 生成纯色测试图。"""
    r, g, b = color_rgb
    color_str = f"0x{r:02x}{g:02x}{b:02x}"
    ff = prober._resolve_ffmpeg()
    import subprocess
    subprocess.run(
        [ff, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c={color_str}:s=640x640:d=0.1",
         "-frames:v", "1",
         path],
        capture_output=True, timeout=30,
    )
    return path


def test_shot_cards() -> None:
    print("[1] shot_cards 注册表")
    cards = shot_cards.get_shot_cards()
    check(len(cards) >= 10, f"get_shot_cards() 返回 >=10 张卡（实际 {len(cards)}）")

    categories = shot_cards.cards_by_category()
    expected = {"opening", "product", "feature", "transition", "closing"}
    check(set(categories.keys()) == expected,
          f"categories 覆盖 {expected}（实际 {set(categories.keys())}）")

    # 每张卡字段非空
    for c in cards:
        ok = all([c.name, c.category, c.one_liner, c.energy,
                  c.duration_hint_s > 0, c.motion])
        check(ok, f"卡 {c.name} 字段齐全（motion={c.motion}, energy={c.energy}）")

    # get_card 存在性
    first = cards[0]
    check(shot_cards.get_card(first.name).name == first.name,
          f"get_card({first.name}) 返回正确")

    # get_card 未知抛 KeyError
    try:
        shot_cards.get_card("no-such-card")
        check(False, "get_card(未知) 应抛 KeyError")
    except KeyError:
        check(True, "get_card(未知) 抛 KeyError")

    # assign_shots 默认分配
    names = shot_cards.assign_shots(5)
    check(len(names) == 5, f"assign_shots(5) 返回 5 个（实际 {len(names)}）")
    check(len(set(names)) == 5, f"assign_shots(5) 5 张卡全不同（实际 {set(names)}）")

    # assign_shots 显式指定
    explicit = shot_cards.assign_shots(3, ["spotlight_focus", "hero_zoom_in", "color_pop"])
    check(explicit == ["spotlight_focus", "hero_zoom_in", "color_pop"],
          f"assign_shots 显式指定返回正确")

    # assign_shots 长度不匹配抛 ValueError
    try:
        shot_cards.assign_shots(3, ["spotlight_focus"])
        check(False, "assign_shots(3, [1]) 应抛 ValueError")
    except ValueError:
        check(True, "assign_shots(3, [1]) 抛 ValueError")

    # 11 张卡名清单
    print("  10+ 张卡名清单:")
    for c in cards:
        print(f"    - {c.name} [{c.category}] motion={c.motion} energy={c.energy}")


def test_derive_visual_spec() -> None:
    print("[2] derive_visual_spec 产品主色提取")
    tmp = Path(tempfile.mkdtemp(prefix="vd_promo_"))

    # 图1：深色（深红 80,20,20）
    img1 = _make_test_image((80, 20, 20), str(tmp / "dark_product.png"))
    spec1 = product_promo.derive_visual_spec([img1])
    check(bool(spec1.get("bg_color")), f"深色图 bg_color 非空: {spec1['bg_color']}")
    check(bool(spec1.get("accent_color")), f"深色图 accent_color 非空: {spec1['accent_color']}")
    check(bool(spec1.get("text_color")), f"深色图 text_color 非空: {spec1['text_color']}")
    check(bool(spec1.get("style_note")), f"深色图 style_note 非空")
    print(f"    深色图 spec: {spec1}")

    # 图2：浅色（浅蓝 200,220,255）
    img2 = _make_test_image((200, 220, 255), str(tmp / "light_product.png"))
    spec2 = product_promo.derive_visual_spec([img2])
    check(bool(spec2.get("bg_color")), f"浅色图 bg_color 非空: {spec2['bg_color']}")
    check(bool(spec2.get("accent_color")), f"浅色图 accent_color 非空: {spec2['accent_color']}")
    check(bool(spec2.get("text_color")), f"浅色图 text_color 非空: {spec2['text_color']}")
    print(f"    浅色图 spec: {spec2}")

    # 深色图与浅色图 bg_color 应不同（不同主色推不同背景）
    check(spec1["bg_color"] != spec2["bg_color"],
          f"深/浅图 bg_color 不同（{spec1['bg_color']} vs {spec2['bg_color']}）")

    # 无图兜底
    spec3 = product_promo.derive_visual_spec([])
    check(bool(spec3.get("bg_color")), f"无图兜底 bg_color 非空: {spec3['bg_color']}")


def test_compose_product_promo() -> None:
    print("[3] compose_product_promo 端到端")
    tmp = Path(tempfile.mkdtemp(prefix="vd_promo_full_"))

    # 2 张测试图（一深一浅，保证主色不同）
    img1 = _make_test_image((60, 120, 180), str(tmp / "product_blue.png"))
    img2 = _make_test_image((200, 100, 80), str(tmp / "product_red.png"))

    # 短文案（3 场景，每场景一个完整句）
    script = (
        "全新智能水杯，让你的生活更健康。\n"
        "精准水温控制，每一口都是最佳温度。\n"
        "立刻下单，享受限时优惠。"
    )

    pid = f"promo-test-{int(time.time())}"
    try:
        result = product_promo.compose_product_promo(
            script=script,
            product_image_paths=[img1, img2],
            project_id=pid,
            voice="zh-CN-XiaoxiaoNeural",
            style_intent="快节奏",
        )
    except Exception as exc:
        check(False, f"compose_product_promo 未抛异常但失败: {exc}")
        import traceback
        traceback.print_exc()
        return

    # final.mp4 存在
    check(Path(result.final_path).is_file(),
          f"final.mp4 存在: {result.final_path}")

    # QC PASS
    qc = result.qc_report
    check(qc.get("verdict") == "PASS",
          f"QC verdict == PASS（实际 {qc.get('verdict')}）")

    # shots_used 场景 > 1
    check(len(result.shots_used) > 1,
          f"shots_used 场景 >1（实际 {len(result.shots_used)}）")

    # 动效有轮换（至少 2 种不同 motion）
    cards_used = [shot_cards.get_card(n) for n in result.shots_used]
    motions = {c.motion for c in cards_used}
    check(len(motions) >= 2,
          f"动效有轮换（{len(motions)} 种: {motions}）")

    # 旁白时长 > 0
    check(result.narration_duration > 0,
          f"narration_duration > 0（实际 {result.narration_duration:.2f}s）")

    # visual_spec 非空
    check(bool(result.visual_spec.get("bg_color")),
          f"visual_spec.bg_color 非空: {result.visual_spec.get('bg_color')}")

    # probe final.mp4：分辨率 + 时长 + 视频流
    probe = prober.probe_media(result.final_path)
    check(probe.has_video_stream, f"final.mp4 有视频流 (codec={probe.codec})")
    check(probe.width == 1080 and probe.height == 1920,
          f"final.mp4 分辨率 1080x1920（实际 {probe.width}x{probe.height}）")
    check(probe.duration is not None and probe.duration > 2.0,
          f"final.mp4 时长 > 2s（实际 {probe.duration:.2f}s）")
    check(probe.has_audio_stream, f"final.mp4 有音轨")

    print(f"    shots_used: {result.shots_used}")
    print(f"    motions: {[c.motion for c in cards_used]}")
    print(f"    narration_duration: {result.narration_duration:.2f}s")
    print(f"    final duration: {probe.duration:.2f}s")
    print(f"    visual_spec: {result.visual_spec}")
    if result.warnings:
        print(f"    warnings: {result.warnings}")


if __name__ == "__main__":
    print("=" * 60)
    print("AI Video Director — 产品广告片能力验证")
    print(f"ffmpeg: {prober._resolve_ffmpeg()}")
    print(f"ffprobe: {prober._resolve_ffprobe()}")
    print("=" * 60)
    test_shot_cards()
    print()
    test_derive_visual_spec()
    print()
    test_compose_product_promo()
    print()
    print("=" * 60)
    print(f"总计: {PASS_COUNT} PASS / {FAIL_COUNT} FAIL")
    print("=" * 60)
    sys.exit(0 if FAIL_COUNT == 0 else 1)
