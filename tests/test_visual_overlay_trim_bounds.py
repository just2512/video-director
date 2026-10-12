"""Regression for visual-overlay right trim bounds in project and source time.""" 
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_visual_overlay_right_trim_clamps_output_and_source_bounds():
    root = Path(__file__).resolve().parents[1]
    helper = root / "webui" / "static" / "js" / "timeline-edit-math.js"
    editor = (root / "webui" / "static" / "js" / "visual-overlay-editor.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "TimelineEditMath.trimEndDelta" in editor
    assert 'src="/static/js/timeline-edit-math.js"' in index

    cases = [
        # Still image: output track end is the only upper bound.
        ({"delta": 5, "start": 8, "duration": 1, "total": 10,
          "assetType": "image", "assetDuration": 0, "sourceIn": 0}, 1),
        # Video: the shorter remaining source range wins.
        ({"delta": 5, "start": 2, "duration": 1, "total": 10,
          "assetType": "video", "assetDuration": 4, "sourceIn": 2}, 1),
        # Video: project end wins when the source is longer.
        ({"delta": 5, "start": 8, "duration": 1, "total": 10,
          "assetType": "video", "assetDuration": 20, "sourceIn": 0}, 1),
        # Shrinking from the left preserves the 0.05-second minimum.
        ({"delta": -5, "start": 2, "duration": 1, "total": 10,
          "assetType": "image", "assetDuration": 0, "sourceIn": 0}, -0.95),
        # A clip already at the project end cannot be extended.
        ({"delta": 2, "start": 9, "duration": 1, "total": 10,
          "assetType": "image", "assetDuration": 0, "sourceIn": 0}, 0),
    ]
    script = """
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {window: {}};
vm.runInNewContext(fs.readFileSync(%s, 'utf8'), sandbox, {filename: 'timeline-edit-math.js'});
const fn = sandbox.window.TimelineEditMath.trimEndDelta;
const cases = %s;
console.log(JSON.stringify(cases.map(([input]) => fn(input))));
""" % (json.dumps(str(helper)), json.dumps(cases))
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    expected = [entry[1] for entry in cases]
    assert len(actual) == len(expected)
    for got, wanted in zip(actual, expected):
        assert abs(got - wanted) < 1e-9, (got, wanted)


def test_overlapping_timeline_clips_get_separate_selectable_lanes():
    root = Path(__file__).resolve().parents[1]
    helper = root / "webui" / "static" / "js" / "timeline-edit-math.js"
    timeline_path = root / "webui" / "static" / "js" / "timeline.js"
    timeline = timeline_path.read_text(encoding="utf-8")
    assert "splitOverlappingTrack(track)" in timeline
    script = """
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {window: {}};
vm.runInNewContext(fs.readFileSync(%s, 'utf8'), sandbox);
const split = sandbox.window.TimelineEditMath.splitOverlappingTrack;
const track = {id:'overlay', label:'画面叠加', addAction:'pick', clips:[
  {positioned:true,leftPct:10,pct:30,visualOverlayIndex:0},
  {positioned:true,leftPct:20,pct:30,visualOverlayIndex:1},
  {positioned:true,leftPct:40,pct:20,visualOverlayIndex:2}
]};
const rows = split(track);
const nonoverlap = split({id:'sfx',label:'音效',clips:[
  {positioned:true,leftPct:0,pct:10,audioIndex:0},
  {positioned:true,leftPct:10,pct:10,audioIndex:1},
  {positioned:true,leftPct:20,pct:10,audioIndex:2}
]});
const invalid = split({id:'narration',label:'旁白',clips:[{pct:50,text:'a'},{pct:50,text:'b'}]});
console.log(JSON.stringify({rows:rows.map(row=>({
  label:row.label,addAction:row.addAction,
  indexes:row.clips.map(clip=>clip.visualOverlayIndex)
})), nonoverlapCount:nonoverlap.length,
sameNonoverlapTrack:nonoverlap[0].id==='sfx', invalidCount:invalid.length}));
""" % (json.dumps(str(helper)))
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert len(result["rows"]) == 2
    assert result["rows"][0]["indexes"] == [0, 2]
    assert result["rows"][1]["indexes"] == [1]
    assert result["rows"][0]["addAction"] == "pick"
    assert result["rows"][1]["addAction"] is None
    assert result["nonoverlapCount"] == 1 and result["sameNonoverlapTrack"]
    assert result["invalidCount"] == 1
