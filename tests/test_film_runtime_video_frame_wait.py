"""Fail closed when offline video or image media never reaches the requested frame."""
from __future__ import annotations

from pathlib import Path


def test_media_event_timeout_cannot_be_mistaken_for_a_successful_frame():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    wait_start = runtime.index("function waitForEvent(element,eventName,timeoutMs){")
    wait_end = runtime.index("function nextFrame()", wait_start)
    wait_event = runtime[wait_start:wait_end]
    assert "new Promise(function(resolve,reject)" in wait_event
    assert "reject(new Error('等待媒体事件 '" in wait_event
    assert "setTimeout(finish" not in wait_event

    video_start = runtime.index("if(tag==='video'){", runtime.index("async function prepareFrameEntry"))
    video_end = runtime.index("}else if(tag==='img'){", video_start)
    video_wait = runtime[video_start:video_end]
    assert "requestVideoFrameCallback" in video_wait
    assert "cancelVideoFrameCallback" in video_wait
    assert "reject(new Error('视频片段 '" in video_wait
    assert "Promise.race" not in video_wait
    assert "setTimeout(resolve,12000)" not in video_wait


def test_failed_or_undecodable_still_image_blocks_offline_render():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    image_start = runtime.index("}else if(tag==='img'){", runtime.index("async function prepareFrameEntry"))
    image_end = runtime.index("}else if(tag==='iframe'){", image_start)
    image_wait = runtime[image_start:image_end]
    assert "el.naturalWidth" in image_wait
    assert "el.naturalHeight" in image_wait
    assert "throw new Error('图片素材未成功解码：" in image_wait
    assert "await el.decode();" in image_wait
