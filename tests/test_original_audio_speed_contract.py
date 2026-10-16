"""原声跟随画面速度：流水线创建的 original 音轨必须带上层的 speed。

背景（对抗审查 2026-10-15，已合并 PR #33-#81 区间）：
`_tool_composition` 用 `source_in/source_out/duration` 建原声轨，但**没有复制
`layer.speed`**，而编辑器提升路径（`_cmd_promote_layer_to_visual_track`）是复制的
——两条创建路径语义分叉。

后果（renderer.mix_audio 原声分支按 span/speed 算可用时长）：
- 画面 3.13× 把 8s 压成 2.56s，原声按 1× 播 → **音画整段不同步**
- span/speed < duration 的片段（实测某段 9.238s 只有 8s 源）→ 尾部静音

渲染器 `_atempo_filter` 明确支持 0.25–4× 且越界抛错，说明设计意图就是变速原声；
流水线恒产 speed=1.0 时该路径形同死代码。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from video_director import director_agent, editor as vd_editor
from video_director.models import AssetRef


def _make_video_with_audio(path: Path, seconds: float = 8.0) -> bool:
    """生成带音轨的真实视频（原声入轨要求 probe.has_audio_stream）。"""
    from video_director import prober
    ff = prober._resolve_ffmpeg()
    if not ff:
        return False
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    return proc.returncode == 0 and path.is_file()


def _composition_state(tmp_path: Path, monkeypatch, scene_duration: float):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    media = tmp_path / "clip.mp4"
    if not _make_video_with_audio(media):
        pytest.skip("ffmpeg 不可用或缺少 libx264/aac，跳过原声变速契约")
    asset = AssetRef(id="clip-with-audio", type="video", file=str(media),
                     duration=8.0, width=64, height=64)
    from video_director import style as style_mod
    return {
        "project_id": "original-audio-speed",
        "narration_scenes": [
            {"id": "S01", "text": "原声跟随画面速度", "start": 0.0, "duration": scene_duration},
        ],
        "assets": {"clip-with-audio": asset},
        "asset_order": ["clip-with-audio"],
        # 注意：流水线用的是 style.py 的 dataclass StyleContract
        #（有 transition_budget/bgm_volume），不是 visual_semantics 的同名 pydantic 模型
        "style_contract": style_mod.get_style("knowledge-explainer")
        or style_mod.StyleContract(style_id="editorial"),
        "assignments": {},
    }, media


def test_pipeline_original_audio_follows_video_speed(tmp_path, monkeypatch):
    """流水线产出的原声轨必须与画面层同速，且满足 span/speed == duration。"""
    state, _media = _composition_state(tmp_path, monkeypatch, scene_duration=2.5)
    spec = {"width": 64, "height": 64, "fps": 10}

    director_agent._tool_composition(spec, state)
    ir = vd_editor.load_composition(state["project_id"])

    video_layers = [lay for sc in ir.scenes for lay in sc.layers if lay.type.value == "video"]
    original = [t for t in ir.audio if t.type.value == "original"]
    assert video_layers, "未产出视频层"
    assert original, "未产出原声轨（素材带音轨时应入轨）"

    for track in original:
        span = float(track.source_out or 0.0) - float(track.source_in or 0.0)
        speed = float(getattr(track, "speed", 1.0) or 1.0)
        assert speed > 0, f"原声速度非法: {speed}"
        # 渲染器按 span/speed 计算可用输出时长；偏离 duration 就会静默截断/不同步
        available = span / speed
        assert abs(available - float(track.duration)) <= 0.05, (
            f"原声可用时长 {available:.3f}s 与轨道时长 {track.duration:.3f}s 不符"
            f"（speed={speed}, span={span:.3f}）——渲染器会静默截断并音画不同步"
        )

    # 变速片段必须存在（否则本测试没测到目标场景）
    speeds = {round(float(getattr(lay, "speed", 1.0) or 1.0), 3) for lay in video_layers}
    assert any(abs(s - 1.0) > 0.01 for s in speeds), (
        f"未产出变速层（speeds={speeds}），该用例失去意义"
    )
    # 原声速度集合必须与画面速度集合一致
    audio_speeds = {round(float(getattr(t, "speed", 1.0) or 1.0), 3) for t in original}
    assert audio_speeds <= speeds, f"原声速度 {audio_speeds} 未跟随画面速度 {speeds}"
