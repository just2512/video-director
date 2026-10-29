"""freeze_last_frame（视频定格出口）测试。

策略：
1) 纯参数校验不依赖 ffmpeg（freeze_seconds<=0 → 原路返回 / 输入不存在 → RenderError）。
2) ffmpeg 可用时合成 1s 测试视频 → freeze 0.5s → 断言时长落在 1.4–1.7s。
   tpad 有 1 帧取整误差，窗口比 1.5s 单点断言宽松（fps=25 下 ±0.04s）。
"""
from __future__ import annotations

import subprocess

import pytest

from video_director import prober, renderer
from video_director.renderer import RenderError


def _ffmpeg_or_none() -> str | None:
    """同 renderer._ffmpeg() 的解析链（随包 bin/ > PATH）：没有即 None，测试跳过。"""
    try:
        ff = renderer._ffmpeg()
        return ff if ff else None
    except Exception:
        return None


@pytest.mark.skipif(_ffmpeg_or_none() is None, reason="无可用 ffmpeg，跳过真实渲染")
def test_freeze_last_frame_extends_duration(tmp_path):
    ff = renderer._ffmpeg()
    src = tmp_path / "src.mp4"
    out = tmp_path / "frozen.mp4"

    synth = subprocess.run(
        [
            ff, "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(src),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert synth.returncode == 0, synth.stderr
    assert src.is_file()

    result = renderer.freeze_last_frame(str(src), str(out), 0.5)
    assert result == str(out) and out.is_file()

    duration = prober.probe_media(str(out)).duration
    assert duration is not None
    # 1s 源 + 0.5s 定格 = 1.5s，容帧取整误差后落 1.4–1.7s
    assert 1.4 <= duration <= 1.7, f"定格输出时长异常: {duration}"


def test_freeze_last_frame_rejects_non_positive_seconds():
    with pytest.raises(RenderError):
        renderer.freeze_last_frame("nonexistent.mp4", "out.mp4", 0.5)


def test_freeze_last_frame_zero_seconds_returns_input(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    # <=0 视为无操作：直接返回输入路径（不校验文件存在性）
    assert renderer.freeze_last_frame(str(src), str(tmp_path / "out.mp4"), 0) == str(src)
    assert renderer.freeze_last_frame(str(src), str(tmp_path / "out.mp4"), -1.0) == str(src)
