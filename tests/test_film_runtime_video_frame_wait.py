"""Offline film runtime must register a frame wait before seeking and verify the presented source timestamp."""
from pathlib import Path


def test_video_seek_registers_and_validates_a_presented_frame_before_screenshot():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    render = (root / "video_director" / "film_render.py").read_text(encoding="utf-8")

    helper = runtime[runtime.index("function waitForPresentedVideoFrame"):runtime.index("function nextFrame()", runtime.index("function waitForPresentedVideoFrame"))]
    prepare = runtime[runtime.index("async function prepareFrameEntry"):runtime.index("function renderFrame", runtime.index("async function prepareFrameEntry"))]
    assert "requestVideoFrameCallback" in helper
    assert "metadata.mediaTime" in helper
    assert "metadata.presentedFrames" in helper
    assert "function startInitialVideoFrameWait(entry)" in runtime
    assert "function cancelInitialVideoFrameWait(entry)" in runtime
    make_clip = runtime[runtime.index("function makeClip(track,clip)"):runtime.index("function pauseAll()", runtime.index("function makeClip(track,clip)"))]
    assert make_clip.index("startInitialVideoFrameWait(entry)") < make_clip.index("if(src)el.src=src")
    assert "awaitInitialVideoFrame(entry,desired)" in prepare
    assert "if(tag==='video'&&!needsSeek){" in prepare
    assert "if(!Number.isFinite(mediaTime)||Math.abs(mediaTime-desired)>tolerance){" in helper
    assert "cancelInitialVideoFrameWait(entry);" in prepare
    assert "clip.source_fps" in helper
    assert "seekThreshold=Math.max(0.001,0.5/Math.max(1,sourceFps))" in prepare
    assert helper.index("requestNext();") < helper.index("return {")
    assert prepare.index("waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames)") < prepare.index("el.currentTime=desired")
    assert "await frameWait.promise" in prepare
    assert "等待新的呈现帧超时" in helper
    assert 'clip["source_fps"] = float(probe.fps)' in render
    assert "Promise.race" not in prepare
    assert "setTimeout(resolve,12000)" not in prepare
