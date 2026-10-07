"""Huashu Art Motion adapter.

The production-facing 8 explainer grammars are vendored as source and executed
through Video Director's deterministic Graphics Runtime. Huashu's standalone
HTTP/XHR loader and FFmpeg pipeline are deliberately not used.

The Director supplies only structured grammar/cues/data. Grammar JavaScript is
fixed vendored source and is statically embedded into the compiled HTML.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Dict

from video_director.graphics.contract import FORBIDDEN_EXT_TOKENS, FORBIDDEN_ND_TOKENS
from video_director.graphics.preflight import deterministic_preflight
from video_director.graphics.procedural import ProceduralProviderError, register_provider
from video_director.graphics.program import GraphicProgram, GraphicScene

_ROOT = Path(__file__).resolve().parents[2] / "third_party" / "huashu-art-motion" / "clip"

_HELPERS = (
    "lib/paint.js",
    "lib/motion.js",
    "lib/camera.js",
    "lib/diagram.js",
    "lib/typo.js",
    "lib/chart.js",
    "lib/ui.js",
    "lib/toon.js",
)

_GRAMMARS: Dict[str, str] = {
    "y1_kurzgesagt": "clips/y1_kurzgesagt.js",
    "y2_vox": "clips/y2_vox.js",
    "y3_whiteboard": "clips/y3_whiteboard.js",
    "y4_storytime": "clips/y4_storytime.js",
    "y5_kinetic_type": "clips/y5_kinetic_type.js",
    "t1_3b1b": "clips/t1_3b1b.js",
    "t2_keynote_ui": "clips/t2_keynote_ui.js",
    "t3_finance_chart": "clips/t3_finance_chart.js",
}

_U_RUNTIME = r"""
window.U = {
  TAU: Math.PI * 2,
  clamp: (x, a = 0, b = 1) => Math.max(a, Math.min(b, x)),
  lerp: (a, b, p) => a + (b - a) * p,
  ss: (a, b, x) => { const q = Math.max(0, Math.min(1, (x - a) / (b - a))); return q * q * (3 - 2 * q); },
  seg: (p, a, b) => Math.max(0, Math.min(1, (p - a) / (b - a))),
  stepTime: (t, fps) => Math.floor(t * fps + 1e-6) / fps,
  ease: {
    linear: p => p,
    inOut: p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2,
    out: p => 1 - Math.pow(1 - p, 3),
    in: p => p * p * p
  },
  rng(seed) {
    let a = seed >>> 0;
    return () => {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  },
  hash(i, j = 0) {
    let h = (i * 374761393 + j * 668265263) | 0;
    h = (h ^ (h >>> 13)) * 1274126177;
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  },
  poly(pts, closed = true) {
    const p = new Path2D();
    pts.forEach((q, i) => i ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]));
    if (closed) p.closePath();
    return p;
  }
};
window.STAGE = {W: 1920, H: 1080};
const __stageHooks = [];
U.onStage = f => __stageHooks.push(f);
U.setStage = (w, h) => {
  window.STAGE = {W: w, H: h};
  __stageHooks.forEach(f => f(w, h));
};
U.fontFamily = f => String(f || '').replace(/^(italics+|bolds+|d+s+)*[d.]+pxs+/, '').replace(/^["']|["']$/g, '');
"""

_HTML_STYLE = """
html,body{margin:0;padding:0;overflow:hidden;background:transparent}
canvas{display:block;width:100%;height:100%}
"""

class HuashuProvider:
    provider_id = "huashu"
    supported_grammars = tuple(sorted(_GRAMMARS))

    def _read(self, rel: str) -> str:
        p = _ROOT / rel
        if not p.is_file():
            raise ProceduralProviderError("HUASHU_SOURCE_MISSING", f"缺少 vendored Huashu 文件: {rel}")
        s = p.read_text(encoding="utf-8")
        for token in (*FORBIDDEN_ND_TOKENS, *FORBIDDEN_EXT_TOKENS):
            if token in s:
                raise ProceduralProviderError(
                    "HUASHU_SOURCE_NOT_DETERMINISTIC",
                    f"{rel} 含禁止运行时标记 {token!r}",
                )
        return s

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return "data:%s;base64,%s" % (
            mime, base64.b64encode(path.read_bytes()).decode("ascii")
        )

    @staticmethod
    def _resolve_image_ref(program: GraphicProgram, ref: str) -> str:
        raw = str(ref or "").strip()
        if raw.startswith("data:"):
            return raw
        if raw.startswith(("http://", "https://")):
            raise ProceduralProviderError("HUASHU_REMOTE_ASSET_REJECTED", f"禁止网络图片: {raw[:120]}")
        if raw.startswith("asset:"):
            asset_id = raw.split(":", 1)[1]
            asset = next((a for a in program.assets if a.id == asset_id), None)
            if asset is None:
                raise ProceduralProviderError("HUASHU_ASSET_NOT_FOUND", f"找不到 AssetRef: {asset_id}")
            path = Path(asset.file)
        else:
            path = Path(raw)
        if not path.is_file():
            raise ProceduralProviderError("HUASHU_IMAGE_NOT_FOUND", f"图片不存在: {raw}")
        return HuashuProvider._data_url(path)

    def _normalize_spec(self, program: GraphicProgram, scene: GraphicScene) -> dict:
        grammar = scene.program_id
        if grammar not in _GRAMMARS:
            raise ProceduralProviderError(
                "HUASHU_GRAMMAR_NOT_FOUND",
                f"不支持的 Huashu grammar: {grammar!r}; 可用: {sorted(_GRAMMARS)}",
            )
        data = dict(scene.program_data or {})
        cues = []
        for raw in data.get("cues") or []:
            if not isinstance(raw, dict):
                raise ProceduralProviderError("HUASHU_INVALID_CUE", "cues 必须全部是对象")
            q = dict(raw)
            if "at" not in q:
                raise ProceduralProviderError("HUASHU_INVALID_CUE", "cue 缺少 at")
            q["at"] = float(q["at"])
            if q["at"] < 0 or q["at"] > scene.duration + 1e-6:
                raise ProceduralProviderError(
                    "HUASHU_CUE_OUT_OF_RANGE",
                    f"cue at={q['at']} 超出 scene duration={scene.duration}",
                )
            if q.get("image"):
                q["image"] = self._resolve_image_ref(program, q["image"])
            cues.append(q)
        return {
            "grammar": grammar,
            "duration": float(scene.duration),
            "fps": float(program.fps),
            "width": int(program.width),
            "height": int(program.height),
            "safe": dict(data.get("safe") or {}),
            "theme": dict(data.get("theme") or {}),
            "data": dict(data.get("data") or {}),
            "cues": cues,
        }

    @staticmethod
    def _replace_registration(source: str, grammar: str, scene_id: str) -> str:
        pattern = rf"CLIPS\.{re.escape(grammar)}\s*="
        replacement = f'window.__HUASHU_CLIPS["{scene_id}"] ='
        out, count = re.subn(pattern, replacement, source, count=1)
        if count != 1:
            raise ProceduralProviderError(
                "HUASHU_GRAMMAR_REGISTRATION_INVALID",
                f"{grammar} 未找到唯一 CLIPS.{grammar} 注册点",
            )
        return out

    def compile_scene(self, program: GraphicProgram, scene: GraphicScene) -> str:
        spec = self._normalize_spec(program, scene)
        grammar = scene.program_id
        helper_js = "\n".join(self._read(rel) for rel in _HELPERS)
        clip_js = self._replace_registration(self._read(_GRAMMARS[grammar]), grammar, scene.id)
        spec_js = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        canvas_id = "huashu_canvas_" + re.sub(r"[^A-Za-z0-9_]", "_", scene.id)

        html = f"""
<div class="huashu-procedural" data-huashu-grammar="{grammar}">
  <canvas id="{canvas_id}" width="{program.width}" height="{program.height}"></canvas>
</div>
<script>
{_U_RUNTIME}
var PAINT = window.PAINT = window.PAINT || {{}};
{helper_js}
window.__HUASHU_CLIPS = window.__HUASHU_CLIPS || {{}};
{clip_js}
window.__HUASHU_READY = window.__HUASHU_READY || {{}};
window.__HUASHU_DRAW = window.__HUASHU_DRAW || {{}};
(() => {{
  const spec = {spec_js};
  const cv = document.getElementById("{canvas_id}");
  const ctx2 = {{
    spec, data: spec.data || {{}}, theme: spec.theme || {{}}, cues: (spec.cues || []).slice(),
    W: spec.width, H: spec.height, FPS: spec.fps, u: Math.min(spec.width, spec.height) / 1080,
    portrait: spec.height > spec.width, alpha: false, IMG: {{}}, dur: spec.duration,
    safe: {{top:0,bottom:0,left:0,right:0,...(spec.safe || {{}})}}
  }};
  ctx2.box = {{
    x: ctx2.safe.left, y: ctx2.safe.top,
    w: ctx2.W - ctx2.safe.left - ctx2.safe.right,
    h: ctx2.H - ctx2.safe.top - ctx2.safe.bottom
  }};
  ctx2.of = (...kinds) => ctx2.cues.filter(q => kinds.includes(q.kind));
  ctx2.lt = (t, at) => t - at + 1 / ctx2.FPS;
  ctx2.p = (t, at, d) => Math.max(0, Math.min(1, ctx2.lt(t, at) / d));
  U.setStage(ctx2.W, ctx2.H);
  const clip = window.__HUASHU_CLIPS["{scene.id}"];
  if (!clip || typeof clip.draw !== "function") throw new Error("Huashu clip 未注册: {scene.id}");
  const urls = new Set();
  ctx2.cues.forEach(q => q.image && urls.add(q.image));
  if (ctx2.data.image) urls.add(ctx2.data.image);
  const loads = [...urls].map(url => new Promise((resolve,reject) => {{
    const im = new Image();
    im.onload = () => {{ ctx2.IMG[url] = im; resolve(); }};
    im.onerror = () => reject(new Error("图片加载失败: " + url.slice(0,120)));
    im.src = url;
  }}));
  window.__HUASHU_READY["{scene.id}"] = Promise.all(loads).then(() => {{
    if (clip.init) clip.init(ctx2);
    const g = cv.getContext("2d");
    window.__HUASHU_DRAW["{scene.id}"] = (t) => {{
      g.setTransform(1,0,0,1,0,0);
      g.clearRect(0,0,ctx2.W,ctx2.H);
      clip.draw(g, t, ctx2);
    }};
  }});
}})();
</script>
"""
        report = deterministic_preflight(html)
        if report.status != "PASS":
            raise ProceduralProviderError("HUASHU_PREFLIGHT_REJECT", "; ".join(report.issues))
        return html

    def validate(self) -> None:
        for rel in _HELPERS:
            self._read(rel)
        for grammar, rel in _GRAMMARS.items():
            self._replace_registration(self._read(rel), grammar, "__validate__")


_PROVIDER = HuashuProvider()
register_provider(_PROVIDER)
