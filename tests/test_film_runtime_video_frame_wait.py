"""Offline film runtime must register a frame wait before seeking and verify the presented source timestamp."""
from pathlib import Path


def test_video_seek_registers_and_validates_a_presented_frame_before_screenshot():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    render = (root / "video_director" / "film_render.py").read_text(encoding="utf-8")

    sync_media = runtime[runtime.index("function syncMedia"):runtime.index("function renderTime", runtime.index("function syncMedia"))]
    assert "if(!state.renderMode&&(forceSeek||!entry.active||drift>(state.playing?0.45:0.04)))" in sync_media
    helper = runtime[runtime.index("function waitForPresentedVideoFrame"):runtime.index("function nextFrame()", runtime.index("function waitForPresentedVideoFrame"))]
    prepare = runtime[runtime.index("async function prepareFrameEntry"):runtime.index("function renderFrame", runtime.index("async function prepareFrameEntry"))]
    assert "frameWait=waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames,entry.lastPresentedMediaTime,frameNumber,t);" in prepare
    assert "el.currentTime=desired;" in prepare
    assert "requestVideoFrameCallback" in helper
    assert "metadata.mediaTime" in helper
    assert "metadata.presentedFrames" in helper
    assert helper.index("throw new Error('视频片段 '+clip.id+' 无法建立先前的呈现帧计数基线") < helper.index("var promise=new Promise")
    assert "if(!Number.isFinite(previousPresentedFrames))" in helper
    assert "var frameAdvanced=Number.isFinite(presentedFrames)&&presentedFrames>previousPresentedFrames;" in helper
    assert "var baseline=await awaitInitialVideoFrame(entry,baselineTime);" in prepare
    assert "缺少有效初始呈现帧计数" in prepare
    assert prepare.index("awaitInitialVideoFrame(entry,baselineTime)") < prepare.index("waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames,entry.lastPresentedMediaTime,frameNumber,t)")
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
    assert prepare.index("waitForPresentedVideoFrame(el,clip,desired,entry.lastPresentedFrames,entry.lastPresentedMediaTime,frameNumber,t)") < prepare.index("el.currentTime=desired")
    assert "await frameWait.promise" in prepare
    assert "等待新的呈现帧超时" in helper
    event_helpers = runtime[runtime.index("function mediaFailureDescription"):runtime.index("function startInitialVideoFrameWait")]
    event_wait = event_helpers[event_helpers.index("function waitForEvent"):]
    assert "function mediaFailureDescription(element)" in event_helpers
    assert "function eventAlreadySatisfied(element,eventName)" in event_helpers
    assert "element.addEventListener('error',onMediaError" in event_wait
    assert "readinessSignals=['loadedmetadata','loadeddata','canplay','progress','durationchange']" in event_wait
    assert "element.addEventListener(name,onReadinessSignal)" in event_wait
    assert "networkState=" in event_helpers and "readyState=" in event_helpers
    assert "MEDIA_ERR_DECODE" in event_helpers
    listener_index = event_wait.index("element.addEventListener(eventName,finish")
    post_listener_check = event_wait.index("if(eventAlreadySatisfied(element,eventName))", listener_index)
    timer_index = event_wait.index("timer=setTimeout(timeout", listener_index)
    assert listener_index < post_listener_check < timer_index
    assert "function timeout(){" in event_wait
    timeout_body = event_wait[event_wait.index("function timeout(){"):]
    assert timeout_body.index("if(eventAlreadySatisfied(element,eventName))") < timeout_body.index("fail('等待媒体事件 '+eventName+' 超时：');")
    assert 'clip["source_fps"] = _source_video_fps(project_id, raw_path, resource_snapshot)' in render
    assert "def _source_video_fps(project_id: str, raw_path: str, resource_snapshot=None)" in render
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


def test_video_frame_timeout_contains_bounded_seek_and_presentation_telemetry():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    diagnostic = runtime[runtime.index("function mediaFrameRangeSnapshot"):runtime.index("function nextFrame()", runtime.index("function mediaFrameRangeSnapshot"))]
    helper = runtime[runtime.index("function waitForPresentedVideoFrame"):runtime.index("function nextFrame()", runtime.index("function waitForPresentedVideoFrame"))]
    prepare = runtime[runtime.index("async function prepareFrameEntry"):runtime.index("function renderFrame", runtime.index("async function prepareFrameEntry"))]

    # Failure-only telemetry is bounded and captures both media state and RVFC/event history.
    assert "MEDIA_FRAME_TIMEOUT_TELEMETRY=" in helper
    assert "callbacks:callbackTrace.slice(-12)" in diagnostic
    assert "events:eventTrace.slice(-24)" in diagnostic
    assert "markers:markers.slice(-12)" in diagnostic
    for field in ("currentTime", "seeking", "readyState", "networkState", "videoWidth", "videoHeight",
                  "mediaError", "buffered", "seekable", "playbackQuality", "desiredMediaTime",
                  "baselinePresentedFrames", "baselineMediaTime", "presentedFrames", "mediaTime",
                  "expectedDisplayTime", "processingDuration"):
        assert field in diagnostic
    assert "frameWait.mark('seek-assignment-start')" in prepare
    assert "frameWait.mark('seek-assignment-returned')" in prepare
    assert "frameWait.mark('seeked-resolved')" in prepare
    # Preserve the existing wait deadline and success predicate; telemetry must not weaken acceptance.
    assert "if(frameAdvanced&&timeMatches)" in helper
    assert "},12000);" in helper
    assert "var playback=el.play();" in helper
    assert "frame-play-requested" in helper and "frame-play-started" in helper
    assert "try{el.pause();}catch(_){}" in helper
    assert "frameWait.startPlayback();" in prepare
    assert prepare.index("frameWait.mark('seeked-resolved')") < prepare.index("frameWait.startPlayback();") < prepare.index("await frameWait.promise")
    assert "'play','playing','pause'" in helper
    render_frame = runtime[runtime.index("async function renderFrame"):runtime.index("function pause()", runtime.index("async function renderFrame"))]
    # Preserve the complete bounded telemetry envelope across the iframe bridge;
    # ordinary render errors retain their existing compact 1,800-character cap.
    assert "rawDetail.indexOf('MEDIA_FRAME_TIMEOUT_TELEMETRY=')>=0?16*1024:1800" in render_frame
    assert "var detail=rawDetail.slice(0,detailLimit);" in render_frame


def test_offline_frame_preparation_uses_half_open_clip_ranges_at_cut_boundaries():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")

    prepare = runtime[runtime.index("async function prepareFrameEntry"):runtime.index(
        "function renderFrame", runtime.index("async function prepareFrameEntry")
    )]
    # The outgoing clip owns [at, end), not the exact cut timestamp.
    # At t == end it must not seek or wait for a frame that belongs to no
    # part of that clip; the incoming clip starts at the same timestamp.
    assert "if(!(at<=t&&(end==null||t<end)))return;" in prepare
    assert "Math.abs(t-end)<0.0001" not in prepare

    # Preview may retain its near-end tolerance while offline capture is strict.
    render_time = runtime[runtime.index("function renderTime"):runtime.index("function frame()", runtime.index("function renderTime"))]
    assert "(!state.renderMode&&Math.abs(state.time-end)<0.0001)" in render_time
