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
    assert helper.index("throw new Error('视频片段 '+clip.id+' 无法建立先前的呈现帧计数基线") < helper.index("var promise=new Promise")
    assert "if(!Number.isFinite(previousPresentedFrames))" in helper
    assert "var frameAdvanced=Number.isFinite(presentedFrames)&&presentedFrames>previousPresentedFrames;" in helper
    assert "var baseline=await awaitInitialVideoFrame(entry,baselineTime);" in prepare
    assert "缺少有效初始呈现帧计数" in prepare
    assert prepare.index("awaitInitialVideoFrame(entry,baselineTime)") < prepare.index("waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames)")
    assert "function startInitialVideoFrameWait(entry)" in runtime
    assert "function cancelInitialVideoFrameWait(entry)" in runtime
    make_clip = runtime[runtime.index("function makeClip(track,clip)"):runtime.index("function pauseAll()", runtime.index("function makeClip(track,clip)"))]
    assert make_clip.index("startInitialVideoFrameWait(entry)") < make_clip.index("if(src)el.src=src")
    assert "awaitInitialVideoFrame(entry,desired)" in prepare
    assert "if(tag==='video'&&!needsSeek&&!Number.isFinite(entry.lastPresentedMediaTime)){" in prepare
    assert "var confirmedMediaTime=Number(entry.lastPresentedMediaTime);" in prepare
    assert "entry.lastPresentedMediaTime=presented.mediaTime;" in prepare
    assert "timeMatches" in helper
    assert "metadata.mediaTime" in helper
    assert "cancelInitialVideoFrameWait(entry);" in prepare
    assert "clip.source_fps" in helper
    assert "var timeTolerance=0.5/Math.max(1,sourceFps)+0.002;" in helper
    assert "var tolerance=0.5/Math.max(1,sourceFps)+0.002;" in runtime
    assert "var confirmedTolerance=0.5/Math.max(1,sourceFps)+0.002;" in prepare
    assert "seekThreshold=Math.max(0.001,0.5/Math.max(1,sourceFps))" in prepare
    assert helper.index("requestNext();") < helper.index("return {")
    assert prepare.index("waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames)") < prepare.index("el.currentTime=desired")
    assert "await frameWait.promise" in prepare
    assert "等待新的呈现帧超时" in helper
    assert 'clip["source_fps"] = float(probe.fps)' in render
    assert "Promise.race" not in prepare
    assert "setTimeout(resolve,12000)" not in prepare



def test_source_video_fps_fails_closed_instead_of_using_composition_fps(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from video_director import film_document as fd
    from video_director.film_render import FilmRenderError, _source_video_fps
    from video_director import prober

    monkeypatch.setattr(fd, "media_path_for_src", lambda _pid, path: "/tmp/" + path)
    monkeypatch.setattr(prober, "probe_media", lambda path: SimpleNamespace(fps=25.0))
    assert _source_video_fps("film-project", "assets/source.mp4") == 25.0

    monkeypatch.setattr(prober, "probe_media", lambda _path: SimpleNamespace(fps=None))
    with pytest.raises(FilmRenderError) as missing:
        _source_video_fps("film-project", "assets/source.mp4")
    assert missing.value.code == "SOURCE_FPS_UNAVAILABLE"

    def fail(_path):
        raise RuntimeError("ffprobe could not parse stream")
    monkeypatch.setattr(prober, "probe_media", fail)
    with pytest.raises(FilmRenderError, match="ffprobe could not parse stream"):
        _source_video_fps("film-project", "assets/source.mp4")
