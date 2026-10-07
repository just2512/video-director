"""HTML Compiler（GFX-003）：GraphicProgram → 确定性 HTML。

方案 §6/§7/§8/§14：HTML 是执行格式（render 的中间产物），不是 AI 的知识格式。
本编译器把**已校验的 GraphicProgram**编译成自包含 HTML：
    - data-composition-* 顶层时序契约
    - 每个 scene 一个 .gfx-scene（data-clip-start/duration 契约由 CSS 变量承载）
    - FrameAdapter runtime：注入 window.__gf.seek(frame)，设置 --t 变量，
      CSS 动画负 delay 让 Chromium 停在 frame 的精确视觉态
    - 纯 CSS 动画（transform/opacity），零 JS 动画库、零外链、零网络

确定性保证（方案 §50/§52）：编译产物不含 Date.now/performance.now/Math.random/
fetch/外链字体——这些标记由 preflight 显式校验。
"""

from __future__ import annotations

from typing import List, Tuple

from video_director.graphics import blocks as blocks_mod
from video_director.graphics.program import GraphicProgram, GraphicScene
from video_director.graphics.procedural import get_provider

#: FrameAdapter runtime（浏览器侧 seek 协议，GFX-005）——注入进每一份编译 HTML
#: 控制协议用 postMessage（sandbox iframe 是 opaque origin，父页面不可直接
#: 访问其 JS——这本身就是沙箱边界正确的证据；消息驱动保持隔离可控）。
_SEEK_RUNTIME = """
<script>
/* FrameAdapter：frame 是唯一时间事实；procedural scene 同样只由 seek(frame) 驱动。 */
window.__gf = (function(){
  var fps = __GF_FPS__;
  var total = __GF_TOTAL__;
  var scenes = __GF_SCENES__;
  var timer = null;
  function seek(frame){
    if (frame < 0) frame = 0;
    if (frame > total) frame = total;
    var t = frame / fps;
    var els = document.querySelectorAll('.gfx-scene');
    for (var i = 0; i < els.length; i++){
      var s = scenes[i];
      var on = (t >= s.start - 1e-6 && t < s.start + s.duration);
      els[i].style.display = on ? '' : 'none';
      els[i].style.setProperty('--t', on ? (t - s.start) + 's' : '-1s');
      els[i].style.setProperty('--dur', (s.duration) + 's');
      if (on && s.engine === 'procedural'){
        var draw = window.__HUASHU_DRAW && window.__HUASHU_DRAW[s.id];
        if (typeof draw !== 'function') throw new Error('PROCEDURAL_DRAW_NOT_READY: ' + s.id);
        draw(t - s.start);
      }
    }
    return {frame: frame, t: t};
  }
  function play(){
    pause();
    var f = window.__gf._last || 0;
    timer = setInterval(function(){
      f = (f + 1) % (total + 1);
      window.__gf._last = f;
      seek(f);
    }, 1000 / fps);
  }
  function pause(){ if (timer) { clearInterval(timer); timer = null; } }
  window.addEventListener('message', function(ev){
    var d = ev.data || {};
    if (d.type === 'gf-seek') { seek(d.frame | 0); }
    else if (d.type === 'gf-play') { play(); }
    else if (d.type === 'gf-pause') { pause(); }
    else if (d.type === 'gf-enable-select') { enableSelect(); }
  });
  function enableSelect(){
    if (window.__gfSelectOn) return;
    window.__gfSelectOn = true;
    document.addEventListener('click', function(e){
      var els = document.querySelectorAll('.gf-sel');
      for (var i=0;i<els.length;i++) els[i].classList.remove('gf-sel');
      var t = e.target.closest('.gfx-type, .gfx-cell, .gfx-num');
      if (!t) return;
      t.classList.add('gf-sel');
      try { parent.postMessage({type:'gf-selected', tag:(t.tagName||''), cls:(t.className||''),
        text:(t.textContent||'').slice(0,40)}, '*'); } catch(e2){}
    });
  }
  return { fps: fps, totalFrames: total, scenes: scenes, seek: seek, play: play, pause: pause };
})();
window.__gfReady = false;
Promise.all(Object.values(window.__HUASHU_READY || {}).map(function(p){ return p; }))
  .then(function(){
    window.__gfReady = true;
    window.__gf.seek(0);
  })
  .catch(function(err){
    window.__gfReadyError = String(err && err.stack || err);
  });
</script>
"""



class CompileError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def compile_to_html(program: GraphicProgram) -> str:
    """GraphicProgram → 自包含确定性 HTML（供 preview iframe 与 render 共用）。

    返回的完整 HTML 文档，iframe srcdoc 直接可用（preview），也作为 render 的
    输入（Chromium 逐帧 scrub）。这两条路径共享同一份产物 → parity by construction
    （方案 §55）。
    """
    if not program.scenes:
        raise CompileError("EMPTY_PROGRAM", "GraphicProgram 无 scene 不可编译")

    scenes_sorted = sorted(program.scenes, key=lambda s: s.start)
    total = scenes_sorted[-1].start + scenes_sorted[-1].duration
    rfp = program.rational_fps

    # 逐 scene 编译：native block 与 procedural provider 共用同一 Scene/Runtime。
    bodies: List[str] = []
    for track_idx, sc in enumerate(scenes_sorted):
        if sc.engine == "procedural":
            if not sc.provider_id:
                raise CompileError(
                    "PROCEDURAL_PROVIDER_MISSING",
                    f"scene {sc.id}: 缺少 provider_id",
                )
            try:
                provider = get_provider(sc.provider_id)
                inner = provider.compile_scene(program, sc)
            except CompileError:
                raise
            except Exception as exc:
                raise CompileError(
                    "PROCEDURAL_COMPILE_FAILED",
                    f"scene {sc.id}: {exc}",
                ) from exc
            bodies.append(
                f'<section class="gfx-scene" data-engine="procedural" '
                f'data-procedural-id="{sc.program_id}" data-start="{sc.start:.3f}" '
                f'data-duration="{sc.duration:.3f}" data-track-index="{track_idx}" '
                f'style="--dur:{sc.duration:.3f}s;--t:-1s">{inner}</section>'
            )
            continue

        try:
            block = blocks_mod.get_block(sc.block_id)
        except KeyError as exc:
            raise CompileError("BLOCK_NOT_FOUND", str(exc)) from exc
        dur = sc.duration
        if not (block.duration_min - 0.01 <= dur <= block.duration_max + 0.01):
            raise CompileError(
                "BLOCK_DURATION_OUT_OF_RANGE",
                f"scene {sc.id}: block {sc.block_id} 时长 {dur}s 越界",
            )
        bodies.append(blocks_mod.render_block(
            block, sc.slot_values, dur, program.style_id,
            start=sc.start, track_index=track_idx,
        ))

    # motion 样式：每种 motion 一套 keyframes（去重）
    motion_ids = sorted({sc.motion_id for sc in scenes_sorted})
    motion_css = "\n".join(
        blocks_mod.block_motion_style(m, 3.0) for m in motion_ids
    )

    # scene 时序 JSON（FrameAdapter 定位 clip 用）
    scenes_json = [
        {"id": sc.id, "start": round(sc.start, 3), "duration": round(sc.duration, 3),
         "motion": sc.motion_id, "engine": sc.engine,
         "provider_id": sc.provider_id, "program_id": sc.program_id}
        for sc in scenes_sorted
    ]
    import json

    runtime = (
        _SEEK_RUNTIME
        .replace("__GF_FPS__", str(rfp.value))
        .replace("__GF_TOTAL__", str(program.total_frames))
        .replace("__GF_SCENES__", json.dumps(scenes_json, ensure_ascii=False))
    )

    # §52 Font 冻结：本地 CJK 字体 → @font-face（渲染/预览同注入 → 字体 parity）
    from video_director.graphics import font_resolver
    font_css = font_resolver.font_face_css()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
{font_css}
{blocks_mod.graphics_base_css(program.style_id)}
{motion_css}
</style>
</head>
<body data-composition-id="{program.id}"
      data-width="{program.width}" data-height="{program.height}"
      data-fps="{int(rfp.numerator)}" data-fps-den="{int(rfp.denominator)}"
      data-duration="{total:.3f}">
{''.join(bodies)}
{runtime}
</body>
</html>"""
    return html