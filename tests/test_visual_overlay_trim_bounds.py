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
