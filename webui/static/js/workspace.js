// §65 Workspace Service（方案《FRONTEND_DEEP_UNIFICATION》§65/§9）：
// 工作台协调层。打开项目 → 装载 Composition → 各区域刷新全部事件驱动，
// 不再依赖 switchTab + setTimeout + editorInit 拼接（§56 禁止固定等待猜测）。
//
// 职责：open(projectId) / loadComposition / setSelection / refreshInspector /
//       refreshTimeline / refreshPreview
//
// pollUntil（§56）：所有「创建后打开」「刷新后选中」都等待明确状态而非 sleep。

window.WS = {opening: false};

/** §56 确定性等待：谓词为真即返回；超时返回 false（不再 sleep(400ms) 赌运气）。 */
function pollUntil(predicate, timeoutMs = 5000, intervalMs = 100){
  return new Promise(resolve => {
    const t0 = Date.now();
    const tick = () => {
      let ok = false;
      try { ok = !!predicate(); } catch (e) { ok = false; }
      if (ok) { resolve(true); return; }
      if (Date.now() - t0 >= timeoutMs) { resolve(false); return; }
      setTimeout(tick, intervalMs);
    };
    tick();
  });
}

/** 工作台打开项目（唯一入口：项目页/审阅页/GFX 确认/模板实例化全走这里）。
 *  target 可选 {type:'scene'|'layer'|'scene_pair', id}——Review Locate 带定位跳转（§45）。 */
async function workspaceOpen(pid, target, options){
  if (!pid) { editorToast('缺少项目', true); return {ok: false}; }
  const legacyCompositionMode = !!(options && options.legacyCompositionMode === true);
  WS.opening = true;
  try {
    switchTab('editor');
    // 1. 项目下拉就绪（editable=true 才会出现在下拉——Project Index 过滤）
    const selEl = document.getElementById('editor-project');
    if (selEl) {
      await editorInit();
      const found = await pollUntil(() => {
        for (const opt of selEl.options) if (opt.value === pid) return true;
        return false;
      }, 4000);
      if (!found) {
        editorToast('项目不在可编辑列表中（可能为旧版项目，去「项目」页转换后再编辑）', true);
        return {ok: false};
      }
      selEl.value = pid;
    }
    // 2. Resolve the document authority before opening either editor.
    // Only an explicit exists:false is allowed to use CompositionIR. A malformed,
    // failed, or unavailable FilmDocument response must never silently open stale IR.
    let filmDocument;
    try {
      filmDocument = await apiGet('/api/editor/film?project_id=' + encodeURIComponent(pid));
    } catch (_) {
      filmDocument = null;
    }
    // Another selector change may happen while the authority GET is in flight.
    // Never route an old response into either the Film Studio or CompositionIR loader.
    if (selEl && String(selEl.value || '').trim() !== String(pid).trim()) {
      const message = '项目选择已变化；已丢弃过期的影片文档预检结果，请重新打开所选项目。';
      editorToast(message, true);
      return {ok:false,mode:'superseded',error:'PROJECT_SELECTION_CHANGED'};
    }
    if (!filmDocument || typeof filmDocument.exists !== 'boolean') {
      const message = '无法确认 film.html 是否存在；为避免打开旧 CompositionIR 覆盖影片数据，已停止打开。请刷新后重试。';
      editorToast(message, true);
      return {ok:false,mode:'unknown',error:'FILM_AUTHORITY_UNKNOWN'};
    }
    if (filmDocument.exists === true) {
      if (typeof window.filmStudioOpenIfCurrentProjectHasFilm !== 'function') {
        editorToast('该项目存在 film.html，但 Film Studio 加载桥不可用；未回退到旧时间线。请刷新后重试。', true);
        return {ok:false,mode:'film',error:'FILM_STUDIO_UNAVAILABLE'};
      }
      if (typeof directorLoadLatestRun === 'function') {
        try { await directorLoadLatestRun(pid); } catch (_) { /* Run metadata must not change timeline authority. */ }
      }
      const openedStudio = await window.filmStudioOpenIfCurrentProjectHasFilm(pid, filmDocument);
      if (openedStudio) return {
        ok:filmDocument.ok===true,mode:'film',authority:'film.html',
        revision:filmDocument.rev||'',valid:filmDocument.ok===true,
        error:filmDocument.ok===true?null:(filmDocument.error||'INVALID_FILM')
      };
      editorToast('该项目存在 film.html，但无法打开权威 Film Studio；未回退到旧 CompositionIR。请检查未提交修改或刷新后重试。', true);
      return {ok:false,mode:'film',error:'FILM_STUDIO_OPEN_FAILED'};
    }
    // An explicit exists:false is the only valid route into the legacy editor.
    if (typeof window.filmStudioPrepareLegacyProjectOpen === 'function' &&
        !window.filmStudioPrepareLegacyProjectOpen(pid)) {
      editorToast('当前 Film Studio 尚未安全关闭；本次没有打开旧 CompositionIR。请先完成或放弃未提交修改。', true);
      return {ok:false,mode:'legacy',error:'STUDIO_CLOSE_BLOCKED'};
    }
    // 3. Load Composition only for projects known not to have film.html.
    const r = await editorLoad(filmDocument);
    if (!r || !r.ok) return r || {ok: false};
    if (typeof directorLoadLatestRun === 'function') {
      await directorLoadLatestRun(pid);
    }
    // 3. 定位（§45 Review → Editor 带 Selection）
    if (target) {
      if (target.type === 'scene') {
        selectScene(target.id, null, 'review-locate');
        timelineScrollToScene(target.id);
      } else if (target.type === 'layer') {
        const [sceneId, idxS] = target.id.split('#');
        selectLayer(sceneId, parseInt(idxS), null, 'review-locate');
        timelineScrollToScene(sceneId);
      } else if (target.type === 'scene_pair') {
        const [f, t] = target.id.split('->');
        selectScenePair(f, t, 'review-locate');
        timelineScrollToScene(t);
      }
      // 定位后打开对应 Inspector 分区（§45：open relevant inspector section）
      if (typeof edShowInspector === 'function') edShowInspector();
      if (target.openSection && typeof inspectorOpenSection === 'function') {
        inspectorOpenSection(target.openSection);
      }
    }
    if (!legacyCompositionMode && !document.documentElement.classList.contains('fs-studio-open')) {
      if (typeof window.filmStudioToggle !== 'function') {
        editorToast('统一 Studio 加载桥不可用；没有创建 film.html。', true);
        return {ok:false,mode:'migration',error:'FILM_STUDIO_UNAVAILABLE'};
      }
      const openedStudio = await window.filmStudioToggle(filmDocument);
      if (!openedStudio) {
        editorToast('迁移候选 Studio 未能打开；没有创建 film.html。', true);
        return {ok:false,mode:'migration',error:'FILM_STUDIO_OPEN_FAILED'};
      }
      return {ok:true,mode:'migration',authority:'CompositionIR preview → film.html',project_id:pid};
    }
    return legacyCompositionMode
      ? {ok:true,mode:'legacy',authority:'CompositionIR',project_id:pid}
      : {ok:true};
  } finally {
    WS.opening = false;
  }
}

/** 装载 Composition（editorLoad 返回 {ok}）。 */
async function loadComposition(pid){
  if (!pid) return {ok: false, error: '无项目'};
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(pid));
  if (!comp || !comp.scenes) {
    return {ok: false, error: (comp && comp.error) || '项目无可编辑时间线'};
  }
  return {ok: true, comp};
}

/** 时间线滚动到场景（定位视觉可见）。 */
function timelineScrollToScene(sceneId){
  requestAnimationFrame(() => {
    const el = document.querySelector(`#editor-timeline .ed-scene[data-scene="${sceneId}"]`);
    if (el) el.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
  });
}

/** 全区域刷新（Selection 或 Composition 变化后统一调度）。 */
function refreshWorkspace(){
  if (typeof editorRenderTimeline === 'function') editorRenderTimeline();
  if (typeof edShowInspector === 'function') edShowInspector();
  if (typeof renderWatermarkList === 'function') renderWatermarkList();
  if (typeof previewShowSelectionOverlay === 'function') previewShowSelectionOverlay();
  if (typeof directorRefreshContext === 'function') directorRefreshContext();
}

/** Composition 变化（命令应用后）：重读最新 + 全区域刷新（单一刷新语义）。 */
async function refreshComposition(){
  if (!ED.pid) return;
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
  if (comp && comp.scenes) {
    ED.comp = comp;
    ED.version = comp.version ?? ED.version;
  }
  refreshWorkspace();
}

Object.assign(window, {
  workspaceOpen, loadComposition, timelineScrollToScene,
  refreshWorkspace, refreshComposition, pollUntil, WS,
});
