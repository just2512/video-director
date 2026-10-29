// P0-J live 预览音频：时间线播放时混音（BGM/SFX/环境音出声）。
//
// 现状约束（不发明新端点）：
//   - voice/music 试听统一走 /api/audio_preview?asset_id=<id>（PreviewService §36），
//     服务器是整文件流，前端 seek 到片段内偏移。
//   - 播放时钟在 preview.js 的 previewPlay/previewPause/previewSeek（全局函数）。
//
// 挂接方式：不入侵 preview.js 源码，在本模块 wrap 这三个全局函数
// （先例同 editor-enhanced.js patchPreview：保留原函数语义，前后补钩子）。
// 播放中由本模块自带的 200ms 扫描器驱动，暂停/seek 跳出片段时回收。
//
// IR 音频片段字段（ED.comp.audio[]，inspector.js _inspectorAudioClip 同源）：
//   start / duration / asset_id / volume / muted / track_id / type / fadeIn / fadeOut
// lane 级静音：ED.comp.audio_tracks[].muted（renderer mix_audio 同款「或」语义）。
(function () {
  'use strict';

  // 预扫描窗口：提前多久开始创建 audio 元素（网络/seek 需要启动时间）。
  var LOOKAHEAD = 1.2;
  // 扫描间隔（ms）：与 preview.js PV.timer 的 80ms 节奏解耦，音频启动不需要那么密。
  var TICK_MS = 200;
  // 静音片段也登记 key，避免边界反复创建后立刻丢弃的抖动。
  var MAX_VOICES = 12;

  var playing = new Map(); // key = clipKey → {el, clipKey, assetId}
  var scanTimer = null;

  function comp() { return window.ED && ED.comp; }

  function mutedLaneIds() {
    var lanes = (comp() || {}).audio_tracks || [];
    var out = {};
    for (var i = 0; i < lanes.length; i++) {
      if (lanes[i] && lanes[i].muted) out[String(lanes[i].id)] = true;
    }
    return out;
  }

  /** 片段在 [winStart, winEnd] 窗口内有声音（片段静音/lane 静音 → false）。 */
  function activeClips(winStart, winEnd) {
    var c = comp();
    if (!c || !Array.isArray(c.audio)) return [];
    var lanes = mutedLaneIds();
    var out = [];
    for (var i = 0; i < c.audio.length; i++) {
      var a = c.audio[i] || {};
      if (!a.asset_id) continue;
      if (a.muted) continue;
      var trackKey = String(a.track_id || '');
      if (trackKey && lanes[trackKey]) continue;
      var s = Number(a.start) || 0;
      var d = Number(a.duration) || 0;
      if (d <= 0 || s + d <= winStart || s >= winEnd) continue;
      out.push({
        key: 'clip:' + i + ':' + String(a.id || ''),
        assetId: String(a.asset_id),
        start: s,
        duration: d,
        volume: Number.isFinite(Number(a.volume)) ? Number(a.volume) : 1,
        fadeIn: Number(a.fadeIn) || 0,
        fadeOut: Number(a.fadeOut) || 0,
      });
    }
    return out;
  }

  function stopAll() {
    playing.forEach(function (v) {
      try { v.el.pause(); } catch (e) {}
      try { v.el.src = ''; } catch (e) {}
    });
    playing.clear();
  }

  /** 停掉 key 对应的 voice（不存在则 no-op）。 */
  function stopOne(key) {
    var v = playing.get(key);
    if (!v) return;
    try { v.el.pause(); } catch (e) {}
    try { v.el.src = ''; } catch (e) {}
    playing.delete(key);
  }

  function startClip(clip, playhead) {
    var url = '/api/audio_preview?asset_id=' + encodeURIComponent(clip.assetId);
    var el = new Audio(url);
    el.volume = Math.max(0, Math.min(1, clip.volume * (window.__edLiveAudioMaster == null ? 1 : window.__edLiveAudioMaster)));
    el.preload = 'auto';
    // 服务器流整文件：片段内偏移 = playhead - clip.start（淡入忽略，视觉反馈先于精细包络）。
    var offset = Math.max(0, playhead - clip.start);
    var started = false;
    el.addEventListener('loadedmetadata', function once() {
      el.removeEventListener('loadedmetadata', once);
      try {
        var max = Math.max(0, (el.duration || 0) - 0.02);
        el.currentTime = Math.min(max, offset);
      } catch (e) { /* seek 失败仍可从头播，不强求 */ }
      if (!started) { el.play().catch(function () {}); started = true; }
    });
    // loadedmetadata 前 play() 在部分浏览器抛 AbortError——统一在事件里播，
    // metadata 已就绪（缓存命中）时立即播放。
    if (el.readyState >= 1 && !started) {
      try {
        var max2 = Math.max(0, (el.duration || 0) - 0.02);
        el.currentTime = Math.min(max2, offset);
      } catch (e) {}
      el.play().catch(function () {});
      started = true;
    }
    el.addEventListener('ended', function () { stopOne(clip.key); });
    el.addEventListener('error', function () { stopOne(clip.key); });
    playing.set(clip.key, { el: el, clipKey: clip.key, assetId: clip.assetId });
  }

  /** 扫描当前播放窗口，启动新片段、停掉离开窗口的片段。 */
  function scan() {
    if (!window.PV || !PV.playing) return;
    var t = Number(PV.playhead) || 0;
    var winEnd = t + LOOKAHEAD;
    var active = activeClips(t, winEnd);
    var wanted = {};
    for (var i = 0; i < active.length; i++) {
      var clip = active[i];
      // 防重复：同一片段 key 播放中不重复 new。
      if (playing.has(clip.key)) {
        wanted[clip.key] = true;
        continue;
      }
      if (playing.size >= MAX_VOICES) continue;
      // 片段起点还没到：只在窗口内才预启动（start - playhead <= LOOKAHEAD 已由 activeClips 保证）。
      startClip(clip, t);
      wanted[clip.key] = true;
    }
    // 播放头离开/片段结束 → 回收（遍历 Map 安全，删当前 key 不影响 forEach）。
    playing.forEach(function (v, key) {
      if (!wanted[key]) stopOne(key);
    });
  }

  function startTimer() {
    if (scanTimer) return;
    scanTimer = setInterval(scan, TICK_MS);
    scan();
  }

  function stopTimer() {
    if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
    stopAll();
  }

  // ---- 挂接：wrap preview.js 的三个播放入口（不入侵原文件）----
  function patch() {
    if (window.__edLiveAudioPatched) return;
    if (typeof window.previewPlay !== 'function' || typeof window.previewPause !== 'function') return;
    window.__edLiveAudioPatched = true;

    var oldPlay = window.previewPlay;
    var oldPause = window.previewPause;
    var oldSeek = window.previewSeek;

    window.previewPlay = function () {
      oldPlay.apply(null, arguments);
      // draft 成片模式用 <video> 原生音轨，不插直播音。
      if (window.PV && PV.mode === 'draft') return;
      startTimer();
    };
    window.previewPause = function () {
      stopTimer();
      oldPause.apply(null, arguments);
    };
    window.previewSeek = function (sec) {
      // 播放头跳出/跳入片段：立即全部回收，下一轮扫描按新位置重建。
      stopTimer();
      var r = oldSeek.apply(null, arguments);
      if (window.PV && PV.playing && PV.mode !== 'draft') startTimer();
      return r;
    };
  }

  // patchPreview 先例是 DOMContentLoaded 后打；本模块在 script 标签末尾同步加载时
  // preview.js 已就位（index.html 的 script 顺序），直接 patch 即可，无需等 DOM。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', patch);
  } else {
    patch();
  }
})();
