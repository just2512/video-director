/* FilmCraft-inspired Source Monitor. Uses the existing project asset catalog,
   shared playhead and versioned editor command path; never writes CompositionIR directly. */
window.SOURCE_MONITOR = window.SOURCE_MONITOR || {assets: [], assetId: "", asset: null, busy: false};

function _smEscape(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
function _smNode(id) { return document.getElementById(id); }
function _smStatus(message, isError) {
  const node = _smNode("ed-source-status");
  if (node) {
    node.textContent = String(message || "");
    node.style.color = isError ? "var(--danger)" : "";
  }
  if (isError && typeof editorToast === "function") editorToast(String(message || "源监视器操作失败"), true);
}
function _smFrameTime(seconds) {
  const fps = Math.max(1, Number(window.ED && ED.comp && ED.comp.fps) || 30);
  return Math.round(Number(seconds) * fps) / fps;
}
function _smNumber(id) {
  const node = _smNode(id);
  const value = node ? Number(node.value) : NaN;
  return Number.isFinite(value) ? value : NaN;
}
function _smCurrentVideo() { return _smNode("ed-source-video"); }
function _smSourceUrl(asset) {
  if (!asset) return "";
  const localUrl = String(asset.file_url || "");
  if (localUrl.startsWith("/file?path=") || localUrl.startsWith("/api/asset_preview?")) return localUrl;
  const path = asset.file || asset.asset_file || "";
  return path ? "/file?path=" + encodeURIComponent(path) : "";
}
function _smAssetId(asset) { return String((asset && (asset.asset_id || asset.id)) || ""); }

function editorSourceMonitorRefreshTracks() {
  const select = _smNode("ed-source-target-track");
  if (!select) return;
  const comp = window.ED && ED.comp;
  const tracks = comp && Array.isArray(comp.visual_tracks) ? comp.visual_tracks.slice() : [];
  const previous = select.value;
  if (!comp || !ED.pid || !tracks.length) {
    select.innerHTML = '<option value="">先打开含画面轨道的项目</option>';
    select.disabled = true;
    const add = _smNode("ed-source-add");
    if (add) add.disabled = true;
    return;
  }
  tracks.sort((a, b) => (Number(a.order) || 0) - (Number(b.order) || 0));
  select.innerHTML = tracks.map(track => {
    const locked = Boolean(track.locked);
    const label = (track.name || track.id) + (locked ? " · 已锁定" : "");
    return '<option value="' + _smEscape(track.id) + '"' + (locked ? " disabled" : "") + '>' +
      _smEscape(label) + '</option>';
  }).join("");
  const usable = tracks.filter(track => !track.locked);
  const selected = usable.find(track => String(track.id) === previous) || usable[0];
  select.value = selected ? String(selected.id) : "";
  select.disabled = usable.length === 0;
  const add = _smNode("ed-source-add");
  if (add) add.disabled = !selected || !window.SOURCE_MONITOR.asset;
}
function editorSourceMonitorRefresh() {
  const state = window.SOURCE_MONITOR;
  const select = _smNode("ed-source-asset");
  const meta = _smNode("ed-source-meta");
  if (!select) return Promise.resolve({ok:false});
  if (state.busy) return Promise.resolve({ok:false, busy:true});
  state.busy = true;
  select.disabled = true;
  if (meta) meta.textContent = "正在刷新素材…";
  const request = (typeof apiGet === "function")
    ? apiGet("/api/assets_library")
    : Promise.reject(new Error("素材库 API 未加载"));
  return Promise.resolve(request).then(data => {
    if (!Array.isArray(data)) throw new Error((data && data.error) || "素材库响应格式错误");
    const assets = data.filter(item => item && ["video", "image"].includes(String(item.type || "").toLowerCase()))
      .map(item => Object.assign({}, item, {asset_id: _smAssetId(item)}))
      .filter(item => item.asset_id && _smSourceUrl(item));
    const previous = select.value || state.assetId;
    state.assets = assets;
    select.innerHTML = '<option value="">选择视频或图片素材…</option>' + assets.map(asset => {
      const label = (asset.name || asset.filename || asset.asset_id) + " · " + asset.type;
      return '<option value="' + _smEscape(asset.asset_id) + '">' + _smEscape(label) + '</option>';
    }).join("");
    const picked = assets.find(asset => asset.asset_id === previous) || assets[0] || null;
    select.value = picked ? picked.asset_id : "";
    select.disabled = assets.length === 0;
    if (meta) meta.textContent = assets.length ? (assets.length + " 个可用素材") : "素材库暂无视频 / 图片";
    editorSourceMonitorRefreshTracks();
    editorSourceMonitorSelectAsset();
    if (!assets.length) _smStatus("素材库中没有可预览的视频或图片；先导入素材后刷新。", false);
    return {ok:true, count:assets.length};
  }).catch(error => {
    if (meta) meta.textContent = "素材库不可用";
    _smStatus("素材库读取失败：" + (error.message || error), true);
    return {ok:false, error:String(error)};
  }).finally(() => { state.busy = false; });
}

function editorSourceMonitorSelectAsset() {
  const state = window.SOURCE_MONITOR;
  const select = _smNode("ed-source-asset");
  const stage = _smNode("ed-source-stage");
  const meta = _smNode("ed-source-meta");
  const inputIn = _smNode("ed-source-in");
  const inputOut = _smNode("ed-source-out");
  const markIn = _smNode("ed-source-mark-in");
  const markOut = _smNode("ed-source-mark-out");
  const audioRow = _smNode("ed-source-audio-row");
  const audioToggle = _smNode("ed-source-audio-enabled");
  const asset = state.assets.find(item => item.asset_id === (select && select.value)) || null;
  state.assetId = asset ? asset.asset_id : "";
  state.asset = asset;
  if (inputIn) inputIn.value = "0";
  const duration = Number(asset && asset.duration);
  const maxSource = Number.isFinite(duration) && duration > 0 ? duration : 3;
  if (inputOut) inputOut.value = Math.min(3, maxSource).toFixed(3);
  if (audioToggle) audioToggle.checked = false;
  const isVideo = !!asset && String(asset.type).toLowerCase() === "video";
  if (markIn) markIn.disabled = !isVideo;
  if (markOut) markOut.disabled = !isVideo;
  if (audioRow) audioRow.style.display = isVideo && asset.has_audio !== false && asset.asset_has_audio !== false ? "" : "none";
  if (meta) meta.textContent = asset ? ((asset.name || asset.asset_id) + " · " + (duration > 0 ? duration.toFixed(2) + "s" : asset.type)) : "先选择项目素材";
  if (!stage) { editorSourceMonitorRefreshTracks(); return; }
  if (!asset) {
    stage.textContent = "选择素材后在此监看源画面。";
    editorSourceMonitorRefreshTracks();
    return;
  }
  const src = _smSourceUrl(asset);
  if (!src) {
    stage.textContent = "该素材没有安全可用的预览地址。";
    editorSourceMonitorRefreshTracks();
    return;
  }
  if (isVideo) {
    stage.innerHTML = '<video id="ed-source-video" controls playsinline preload="metadata" src="' + _smEscape(src) + '"></video>';
    const video = _smCurrentVideo();
    if (video) {
      video.addEventListener("loadedmetadata", () => {
        if (!state.asset || state.asset.asset_id !== asset.asset_id) return;
        const actualDuration = Number(video.duration);
        if (Number.isFinite(actualDuration) && actualDuration > 0) {
          const currentOut = _smNumber("ed-source-out");
          if (!Number.isFinite(currentOut) || currentOut > actualDuration) {
            _smNode("ed-source-out").value = Math.min(3, actualDuration).toFixed(3);
          }
          if (meta) meta.textContent = (asset.name || asset.asset_id) + " · " + actualDuration.toFixed(2) + "s";
        }
      });
      video.addEventListener("timeupdate", () => {
        if (state.asset && state.asset.asset_id === asset.asset_id && meta) {
          meta.textContent = (asset.name || asset.asset_id) + " · " + Number(video.currentTime || 0).toFixed(2) + " / " +
            (Number.isFinite(video.duration) ? video.duration.toFixed(2) : "—") + "s";
        }
      });
      video.addEventListener("error", () => {
        if (state.asset && state.asset.asset_id === asset.asset_id) _smStatus("源视频无法预览；检查素材文件及浏览器支持的编码。", true);
      });
    }
  } else {
    stage.innerHTML = '<img id="ed-source-image" src="' + _smEscape(src) + '" alt="' + _smEscape(asset.name || "源图片") + '">';
  }
  editorSourceMonitorRefreshTracks();
  _smStatus(isVideo ? "可播放 / 拖动源视频，并标记入点和出点。" : "静态图片：设置片段时长后放置到轨道。", false);
}
function editorSourceMonitorMarkIn() {
  const video = _smCurrentVideo();
  if (!video || !Number.isFinite(Number(video.currentTime))) {
    _smStatus("请先在源监视器加载视频并定位到入点。", true);
    return;
  }
  const at = _smFrameTime(video.currentTime);
  _smNode("ed-source-in").value = at.toFixed(3);
  _smStatus("源入点已标记：" + at.toFixed(3) + "s。", false);
}
function editorSourceMonitorMarkOut() {
  const video = _smCurrentVideo();
  if (!video || !Number.isFinite(Number(video.currentTime))) {
    _smStatus("请先在源监视器加载视频并定位到出点。", true);
    return;
  }
  const at = _smFrameTime(video.currentTime);
  _smNode("ed-source-out").value = at.toFixed(3);
  _smStatus("源出点已标记：" + at.toFixed(3) + "s。", false);
}
async function editorSourceMonitorAddToTimeline() {
  const state = window.SOURCE_MONITOR;
  if (!window.ED || !ED.pid || !ED.comp) {
    _smStatus("请先打开项目，再把素材放置到时间线。", true);
    return;
  }
  const asset = state.asset;
  if (!asset) {
    _smStatus("请先从素材箱选择源素材。", true);
    return;
  }
  editorSourceMonitorRefreshTracks();
  const trackId = String((_smNode("ed-source-target-track") || {}).value || "");
  const track = (ED.comp.visual_tracks || []).find(item => String(item.id) === trackId);
  if (!track || track.locked) {
    _smStatus("请选择一条已解锁的目标画面轨。", true);
    return;
  }
  const isVideo = String(asset.type).toLowerCase() === "video";
  let sourceIn = isVideo ? _smNumber("ed-source-in") : 0;
  let sourceOut = _smNumber("ed-source-out");
  if (!Number.isFinite(sourceIn) || !Number.isFinite(sourceOut) || sourceIn < 0 || sourceOut <= sourceIn) {
    _smStatus(isVideo ? "入点 / 出点无效：出点必须大于入点。" : "静态图片时长必须大于 0。", true);
    return;
  }
  sourceIn = _smFrameTime(sourceIn);
  sourceOut = _smFrameTime(sourceOut);
  const duration = sourceOut - sourceIn;
  if (!Number.isFinite(duration) || duration < (1 / Math.max(1, Number(ED.comp.fps) || 30)) / 2) {
    _smStatus("有效片段太短：请至少选择半帧以上的区间。", true);
    return;
  }
  const sourceDuration = Number(asset.duration);
  if (isVideo && Number.isFinite(sourceDuration) && sourceDuration > 0 && sourceIn + duration > sourceDuration + 0.02) {
    _smStatus("源区间超出素材时长；请重新标记出点。", true);
    return;
  }
  const total = Number(ED.comp.duration) || 0;
  const rawStart = (window.PLAYHEAD && Number(PLAYHEAD.at)) || (window.PV && Number(PV.playhead)) || 0;
  const start = _smFrameTime(rawStart);
  if (!Number.isFinite(total) || total <= 0 || start < 0 || start + duration > total + 1e-6) {
    _smStatus("当前播放头之后放不下整个片段。请移动播放头或缩短源区间。", true);
    return;
  }
  const presentationMode = String((_smNode("ed-source-presentation") || {}).value || "full_frame");
  const sourceAudioEnabled = isVideo && !!(_smNode("ed-source-audio-enabled") || {}).checked;
  const button = _smNode("ed-source-add");
  if (button) button.disabled = true;
  state.busy = true;
  try {
    const result = await executeCommand({
      command: "add_visual_overlay",
      targetId: "overlays",
      payload: {assetId:asset.asset_id, start, duration, sourceIn, trackId, presentationMode, sourceAudioEnabled}
    });
    if (!result || !result.ok) {
      _smStatus("放置失败：" + ((result && result.error) || "编辑命令未通过。"), true);
      return;
    }
    _smStatus("已将 " + (asset.name || asset.asset_id) + " 放到 " + (track.name || track.id) +
      " · " + start.toFixed(2) + "–" + (start + duration).toFixed(2) + "s。", false);
    await refreshWorkspace();
    editorSourceMonitorRefreshTracks();
  } catch (error) {
    _smStatus("放置失败：" + (error.message || error), true);
  } finally {
    state.busy = false;
    editorSourceMonitorRefreshTracks();
  }
}

function editorSourceMonitorInit() {
  if (!_smNode("ed-source-asset")) return;
  editorSourceMonitorRefresh();
  editorSourceMonitorRefreshTracks();
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", editorSourceMonitorInit, {once:true});
} else {
  editorSourceMonitorInit();
}
