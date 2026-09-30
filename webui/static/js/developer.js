// §10 Developer Mode：开发信息隔离层（FE-006 配套）。
// 普通模式：创作者看到「是什么」「怎么改」；开发者模式：看「为什么」「底层如何执行」。
// 默认关闭；开启不改变任何正式执行路径，只切换展示。

// recipe/shot id → 中文名（§9.2「说人话」：默认 UI 显示中文名而非 motion.xxx）
let _CN_CACHE = {motion: {}, shot: {}};

async function devWarmNameCache(){
  // 预热动效映射（失败不阻塞——映射缺失时回退显示原始 id）。
  // shot 映射由 Shot Picker 的 resolve 响应顺手填充（无独立列表端点）。
  try {
    const r = await (await fetch('/api/registry/motions')).json();
    const list = Array.isArray(r) ? r : (r.motions || []);
    for (const m of list) {
      if (m && m.id) _CN_CACHE.motion[m.id] = m.name || m.id;
      if (m && m.id && m.id.startsWith('motion.')) {
        // graphics.fade-up 等 graphics 域动效同表
        _CN_CACHE.motion['graphics.' + m.id.replace('motion.', '')] = m.name || m.id;
      }
    }
  } catch (e) { /* 回退原始 id */ }
}

/** 动效 id → 中文名（默认 UI；Developer Mode 由调用方自行显示原始 id）。 */
function motionCnName(id){
  if (!id) return '';
  return _CN_CACHE.motion[id] || id.replace(/^(motion|graphics)\./, '');
}
/** 镜头配方 id → 中文名。Picker resolve 时填充缓存。 */
function shotCnName(id){
  if (!id) return '';
  return _CN_CACHE.shot[id] || '镜头方案';
}
/** Shot Picker resolve 响应 → 中文名缓存（顺手调用）。 */
function devCacheShots(candidates){
  for (const c of (candidates || [])) {
    if (c && c.recipe_id) _CN_CACHE.shot[c.recipe_id] = c.name || c.recipe_id;
  }
}

/** Developer Mode 开关（§10：默认关闭；全部开发信息默认折叠）。 */
function devToggleMode(on){
  DEV.mode = (on === undefined) ? !DEV.mode : !!on;
  try { localStorage.setItem('dev_mode', DEV.mode ? '1' : ''); } catch (e) {}
  document.querySelectorAll('.dev-only').forEach(el => {
    el.style.display = DEV.mode ? '' : 'none';
  });
  const badge = document.getElementById('dev-badge');
  if (badge) badge.textContent = DEV.mode ? '🔧 开发者模式：开' : '🔧 开发者模式';
  // 重渲染当前视图让条件显示生效
  if (typeof editorRenderTimeline === 'function' && window.ED && ED.pid) editorRenderTimeline();
  if (typeof edShowInspector === 'function' && window.ED && ED.sel) edShowInspector();
}
function devInit(){
  // 恢复上次模式（默认关）；预热名称映射
  try { DEV.mode = localStorage.getItem('dev_mode') === '1'; } catch (e) {}
  devWarmNameCache();
  document.querySelectorAll('.dev-only').forEach(el => {
    el.style.display = DEV.mode ? '' : 'none';
  });
}

Object.assign(window, {devToggleMode, devInit, devWarmNameCache, devCacheShots,
                        motionCnName, shotCnName, _CN_CACHE});
