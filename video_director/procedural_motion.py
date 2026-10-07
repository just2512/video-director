"""Huashu Procedural Motion Provider.

将 huashu-art-motion 的参数化解说动画接入 Video Director 的 Graphics Renderer。
Huashu 只提供“视觉语言/绘制实现”，不拥有第二套编辑器、WorkflowRun 或生产渲染入口。

硬边界：
- AI 只提交结构化 MotionProgram，不提交 JS。
- 运行时只消费已固定在仓库里的 bundle；不做 XHR/fetch/网络加载。
- Preview/Final 都进入现有 graphics.render_graphics()，保持单一渲染事实源。
- 所有生成结果进入项目 plans + Asset Catalog，避免把状态留在进程内存。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from video_director import config
from video_director.graphics.render import render_graphics

_BUNDLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "third_party"
    / "huashu"
    / "huashu_motion_bundle.js"
)

# 当前正式吸收的 Huashu 8 种“解说动画语法”。
# 35 种艺术史 scene 仍属于另一类“世界/场景 renderer”，不能伪装成 clip grammar。
HUASHU_GRAMMARS = (
    "y1_kurzgesagt",
    "y2_vox",
    "y3_whiteboard",
    "y4_storytime",
    "y5_kinetic_type",
    "t1_3b1b",
    "t2_keynote_ui",
    "t3_finance_chart",
)

# grammar -> 默认可由脚本文案生成的 cue 类型。
_AUTO_POINT_GRAMMARS = {
    "y1_kurzgesagt",
    "y2_vox",
    "y3_whiteboard",
    "y4_storytime",
    "y5_kinetic_type",
    "t1_3b1b",
}


class ProceduralMotionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


class MotionAsset(BaseModel):
    """项目内可引用的本地素材。"""

    id: str
    file: str

    @field_validator("file")
    @classmethod
    def _local_only(cls, value: str) -> str:
        value = str(value)
        if value.startswith(("http://", "https://", "data:")):
            raise ValueError("Huashu motion 只接受本地素材，不接受远程 URL/data URI 作为输入")
        return value


class MotionProgram(BaseModel):
    """AI/Director 与 Procedural Renderer 之间的稳定契约。"""

    id: str = Field(min_length=1)
    engine: str = "huashu"
    grammar: str
    duration: float = Field(gt=0, le=300)
    fps: int = Field(gt=0, le=120)
    width: int = Field(gt=0, le=7680)
    height: int = Field(gt=0, le=7680)
    safe: Dict[str, Any] = Field(default_factory=dict)
    theme: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)
    cues: List[Dict[str, Any]] = Field(default_factory=list)
    assets: List[MotionAsset] = Field(default_factory=list)
    alpha: bool = False
    provenance_ref: str = "src-huashu"
    commercial_status: str = "CLEAR"

    @field_validator("engine")
    @classmethod
    def _engine(cls, value: str) -> str:
        if value != "huashu":
            raise ValueError("当前 MotionProgram engine 必须为 huashu")
        return value

    @field_validator("grammar")
    @classmethod
    def _grammar(cls, value: str) -> str:
        if value not in HUASHU_GRAMMARS:
            raise ValueError(
                f"未知 Huashu grammar {value!r}，可用: {', '.join(HUASHU_GRAMMARS)}"
            )
        return value

    @property
    def total_frames(self) -> int:
        return max(1, int(round(self.duration * self.fps)))

    def to_spec(self, asset_keys: Optional[Dict[str, str]] = None) -> dict:
        asset_keys = asset_keys or {}
        spec = {
            "grammar": self.grammar,
            "duration": float(self.duration),
            "fps": float(self.fps),
            "width": int(self.width),
            "height": int(self.height),
            "safe": dict(self.safe),
            "theme": dict(self.theme),
            "data": dict(self.data),
            "cues": json.loads(json.dumps(self.cues, ensure_ascii=False)),
            "alpha": bool(self.alpha),
        }

        def rewrite(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: rewrite(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            if isinstance(value, str):
                return asset_keys.get(value, value)
            return value

        spec["cues"] = rewrite(spec["cues"])
        spec["data"] = rewrite(spec["data"])
        return spec


def available_grammars() -> List[dict]:
    """供 Director/UI 查询的稳定 grammar 清单。"""
    names = {
        "y1_kurzgesagt": "Kurzgesagt 式尺度穿行",
        "y2_vox": "Vox 式拼贴/证据串联",
        "y3_whiteboard": "白板手绘讲解",
        "y4_storytime": "Storytime 角色讲解",
        "y5_kinetic_type": "Kinetic Type 动态文字",
        "t1_3b1b": "3Blue1Brown / Manim 式变形讲解",
        "t2_keynote_ui": "发布会 / Keynote UI",
        "t3_finance_chart": "财经图表",
    }
    return [
        {
            "id": grammar,
            "name": names[grammar],
            "engine": "huashu",
            "commercial_status": "CLEAR",
            "provenance_ref": "src-huashu",
        }
        for grammar in HUASHU_GRAMMARS
    ]


def _split_script(text: str) -> List[str]:
    import re

    return [x.strip() for x in re.split(r"[。！？!?；;\n]+", text.strip()) if x.strip()]


def _auto_cues(grammar: str, prompt: str, duration: float) -> List[dict]:
    sentences = _split_script(prompt)
    if not sentences:
        raise ProceduralMotionError("INVALID_PROMPT", "prompt/script 不能为空")
    if grammar == "t3_finance_chart":
        raise ProceduralMotionError(
            "MISSING_REQUIREMENT",
            "t3_finance_chart 需要 data.chart + data.series，并明确提供 cues；不会凭空生成数据",
        )

    # 文案驱动的默认 cue 是确定性的，不让 LLM 直接决定 JS/画布逻辑。
    step = duration / max(1, len(sentences))
    cues: List[dict] = []
    if grammar == "t2_keynote_ui":
        cues.append({"at": 0.0, "kind": "title", "text": sentences[0]})
        for i, text in enumerate(sentences[1:], 1):
            cues.append(
                {
                    "at": round(i * step, 3),
                    "kind": "card",
                    "text": text,
                    "sub": "",
                    "data": {"step": i},
                }
            )
    else:
        cues.append({"at": 0.0, "kind": "title", "text": sentences[0]})
        for i, text in enumerate(sentences[1:], 1):
            cues.append({"at": round(i * step, 3), "kind": "point", "text": text})
    return cues


def _read_local_assets(program: MotionProgram) -> tuple[MotionProgram, Dict[str, str], str]:
    """本地素材 → data URI，并把原路径换成 asset:// key。"""
    key_map: Dict[str, str] = {}
    html_assets: List[tuple[str, str]] = []
    asset_fingerprint = hashlib.sha256()

    for asset in program.assets:
        path = Path(asset.file).expanduser().resolve()
        if not path.is_file():
            raise ProceduralMotionError(
                "ASSET_NOT_FOUND", f"Huashu motion 素材不存在：{path}"
            )
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        key = f"asset://{asset.id or digest[:16]}"
        key_map[str(asset.file)] = key
        key_map[str(path)] = key
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        html_assets.append((key, data_uri))
        asset_fingerprint.update(digest.encode("ascii"))

    if program.assets:
        spec = program.to_spec(key_map)
        materialized = program.model_copy(update={"cues": spec["cues"], "data": spec["data"]})
    else:
        materialized = program

    return materialized, dict(html_assets), asset_fingerprint.hexdigest()


def _safe_script_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    # 防止用户文本形成 </script>，同时保留 JSON 语义。
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def compile_motion_html(program: MotionProgram) -> str:
    """MotionProgram → 自包含 HTML，交给现有 graphics.render_graphics。"""
    if not _BUNDLE_PATH.is_file():
        raise ProceduralMotionError(
            "BUNDLE_MISSING", f"Huashu bundle 缺失：{_BUNDLE_PATH}"
        )

    bundle = _BUNDLE_PATH.read_text(encoding="utf-8")
    # Bundle 必须保持离线确定性；一旦被意外引入网络/时钟原语，直接拒绝。
    forbidden = (
        "Date.now",
        "performance.now",
        "Math.random",
        "requestAnimationFrame",
        "crypto.getRandomValues",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "@import",
        "url(http",
        "url(https",
        "<script src",
        "<link rel=",
    )
    found = [token for token in forbidden if token in bundle]
    if found:
        raise ProceduralMotionError(
            "NONDETERMINISTIC_BUNDLE",
            "Huashu bundle 包含禁止的运行时原语：" + ", ".join(found),
        )

    materialized, asset_uris, _ = _read_local_assets(program)
    spec = materialized.to_spec()

    asset_html = "".join(
        f'<img data-huashu-key="{key}" src="{uri}" alt="" '
        'style="display:none" />'
        for key, uri in asset_uris.items()
    )

    runtime = """
(() => {
  const cv = document.getElementById("c");
  const c = cv.getContext("2d");
  const spec = __HUASHU_SPEC__;
  const assets = document.querySelectorAll("[data-huashu-key]");
  const C = window.CLIP = window.CLIP || {};
  window.CLIPS = window.CLIPS || {};
  window.__canvas = cv;

  C.lt = (t, at, fps) => t - at + 1 / fps;
  C.p = (t, at, dur, fps) => U.clamp(C.lt(t, at, fps) / dur);
  C.fit = (iw, ih, bx, by, bw, bh) => {
    const s = Math.min(bw / iw, bh / ih);
    const w = iw * s, h = ih * s;
    return {x: bx + (bw - w) / 2, y: by + (bh - h) / 2, w, h, s};
  };
  C.sub = (r, box) => ({
    x: box.x + r[0] * box.w, y: box.y + r[1] * box.h,
    w: r[2] * box.w, h: r[3] * box.h
  });
  C.text = (cue, ...fallback) =>
    (cue && (cue.text ?? cue.data?.text)) ??
    fallback.find(v => v != null) ?? "";

  cv.width = spec.width; cv.height = spec.height;
  U.setStage(spec.width, spec.height);

  const imgs = {};
  assets.forEach(img => {
    imgs[img.dataset.huashuKey] = img;
  });

  const cues0 = (spec.cues || [])
    .map((q, i) => ({...q, i, at: +q.at || 0}))
    .sort((a, b) => a.at - b.at || a.i - b.i);
  const cues = cues0.filter(q => q.at <= spec.duration + 1e-9);
  for (const q of cues) {
    if (q.kind === "number") {
      const cap = q.text ?? q.sub ?? q.data?.text;
      if (cap != null) { q.text = cap; q.sub = cap; }
    }
  }

  const safe = {top: 0, bottom: 0, left: 0, right: 0, ...(spec.safe || {})};
  const box = {
    x: safe.left, y: safe.top,
    w: spec.width - safe.left - safe.right,
    h: spec.height - safe.top - safe.bottom
  };
  const ctx = {
    spec,
    data: spec.data || {},
    theme: spec.theme || {},
    cues,
    W: spec.width,
    H: spec.height,
    FPS: spec.fps,
    u: Math.min(spec.width, spec.height) / 1080,
    portrait: spec.height > spec.width,
    alpha: !!spec.alpha,
    IMG: imgs,
    dur: spec.duration,
    safe,
    box,
    of: (...kinds) => cues.filter(q => kinds.includes(q.kind)),
    lt: (t, at) => C.lt(t, at, spec.fps),
    p: (t, at, d) => C.p(t, at, d, spec.fps)
  };

  const G = CLIPS[spec.grammar];
  if (!G || typeof G.draw !== "function") {
    throw new Error("Huashu grammar 未注册：" + spec.grammar);
  }
  if (G.init) G.init(ctx);

  const band = (safe.fill && !spec.alpha) ? (cc => {
    cc.fillStyle = safe.fill;
    if (safe.top) cc.fillRect(0, 0, spec.width, safe.top);
    if (safe.bottom) cc.fillRect(0, spec.height - safe.bottom, spec.width, safe.bottom);
    if (safe.left) cc.fillRect(0, 0, safe.left, spec.height);
    if (safe.right) cc.fillRect(spec.width - safe.right, 0, safe.right, spec.height);
  }) : null;

  function drawFrame(frame) {
    const t = Math.max(0, Math.min(spec.duration, frame / spec.fps));
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, spec.width, spec.height);
    c.save();
    G.draw(c, t, ctx);
    c.restore();
    if (band) band(c);
  }

  window.__gf = {
    seek(frame) {
      drawFrame(Number(frame) || 0);
    }
  };
  window.__total = spec.duration;
  window.__fps = spec.fps;
  window.__size = [spec.width, spec.height];
  window.__gfx_ready = true;
  drawFrame(0);
})();
""".replace("__HUASHU_SPEC__", _safe_script_json(spec))

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;padding:0;overflow:hidden;background:transparent}"
        "canvas{display:block}</style></head><body>"
        f"{asset_html}<canvas id=\"c\"></canvas>"
        f"<script>{bundle}</script>"
        f"<script>{runtime}</script>"
        "</body></html>"
    ).replace("</script>", "<\\/script>")


def build_motion_program(
    prompt: str,
    grammar: str,
    *,
    duration: float,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    safe: Optional[dict] = None,
    theme: Optional[dict] = None,
    data: Optional[dict] = None,
    cues: Optional[List[dict]] = None,
    assets: Optional[List[dict]] = None,
    alpha: bool = False,
) -> MotionProgram:
    grammar = str(grammar or "").strip()
    if grammar not in HUASHU_GRAMMARS:
        raise ProceduralMotionError(
            "UNKNOWN_GRAMMAR",
            f"未知 grammar {grammar!r}，可用：{', '.join(HUASHU_GRAMMARS)}",
        )
    prompt = str(prompt or "").strip()
    if not prompt and not cues:
        raise ProceduralMotionError("INVALID_PROMPT", "prompt/script 不能为空")

    effective_cues = cues
    if not effective_cues:
        effective_cues = _auto_cues(grammar, prompt, float(duration))

    if grammar == "t3_finance_chart":
        chart_data = data or {}
        series = chart_data.get("series")
        if not isinstance(series, list) or not series:
            raise ProceduralMotionError(
                "MISSING_REQUIREMENT",
                "t3_finance_chart 缺少 data.series，不生成虚构数据",
            )

    program = MotionProgram(
        id=f"motion-{uuid.uuid4().hex[:10]}",
        grammar=grammar,
        duration=float(duration),
        fps=int(fps),
        width=int(width),
        height=int(height),
        safe=safe or {},
        theme=theme or {},
        data=data or {},
        cues=effective_cues,
        assets=[MotionAsset(**a) if not isinstance(a, MotionAsset) else a for a in (assets or [])],
        alpha=bool(alpha),
    )
    return program


def create_motion_asset(project_id: str, payload: dict) -> dict:
    """Material Tool Registry 的唯一生产入口。"""
    grammar = payload.get("grammar") or payload.get("grammarId")
    if not grammar:
        raise ProceduralMotionError("INVALID_PAYLOAD", "material.motion.create 缺 grammar")
    program = build_motion_program(
        payload.get("prompt") or payload.get("script") or "",
        grammar,
        duration=float(payload.get("duration") or 5.0),
        fps=float(payload.get("fps") or 30.0),
        width=int(payload.get("width") or 1080),
        height=int(payload.get("height") or 1920),
        safe=payload.get("safe") or {},
        theme=payload.get("theme") or {},
        data=payload.get("data") or {},
        cues=payload.get("cues"),
        assets=payload.get("assets") or [],
        alpha=bool(payload.get("alpha", False)),
    )

    base = config.project_dir(project_id)
    plan_dir = base / "plans" / "motion"
    out_dir = base / "assets" / "generated" / "motion" / program.id
    plan_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_path = plan_dir / f"{program.id}.json"
    plan_path.write_text(
        json.dumps(program.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    html = compile_motion_html(program)
    report = __import__("video_director.graphics.preflight", fromlist=["deterministic_preflight"]).deterministic_preflight(html)
    if report.status != "PASS":
        raise ProceduralMotionError(
            "NONDETERMINISTIC_MOTION",
            "Huashu motion 未通过确定性预检：" + "; ".join(report.issues),
        )

    output = out_dir / "motion.mp4"
    render_graphics(
        html=html,
        out_path=str(output),
        width=program.width,
        height=program.height,
        fps=int(program.fps),
        total_frames=program.total_frames if hasattr(program, "total_frames") else max(1, int(round(program.duration * program.fps))),
        theme="huashu",
        quality=str(payload.get("quality") or "draft"),
    )

    if not output.is_file() or output.stat().st_size == 0:
        raise ProceduralMotionError("RENDER_EMPTY", "Huashu motion 渲染结束但没有产物")

    from video_director import asset_catalog

    entry = asset_catalog.register_generated(
        str(output),
        prompt=f"Huashu/{program.grammar}: {str(payload.get('prompt') or payload.get('script') or '')[:120]}",
        tags=["huashu", "procedural-motion", program.grammar],
    )

    manifest = {
        "program_id": program.id,
        "asset_id": entry["asset_id"],
        "grammar": program.grammar,
        "engine": "huashu",
        "output": str(output),
        "plan": str(plan_path),
        "created_at": time.time(),
        "provenance_ref": program.provenance_ref,
        "commercial_status": program.commercial_status,
    }
    (out_dir / "artifact.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "program": program.model_dump(mode="json"),
        "asset": entry,
        "artifact": manifest,
    }
