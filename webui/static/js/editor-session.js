/**
 * Editor Session：把 OpenCut Classic 的交互语义融合进现有 Composition/Command 编辑器。
 *
 * 原则：
 * 1. Session 只保存浏览器瞬时状态，不保存 Composition。
 * 2. 所有持久化编辑仍走 Editor Command → Revision → Composition IR。
 * 3. Scene 是本系统 narration 主轴上的时间单元；因此“连续编辑”是既有语义，
 *    不额外制造第二套 ripple timeline。
 * 4. Snap / Box Select / Multi-scene move 是交互增强，不改变 IR 事实源。
 */
(() => {
  const STORE_KEY = 'vd-editor-session-v2';
  const state = {
    projectId: '',
    snapEnabled: true,
    boxSelectMode: false,
    snapTolerancePx: 10,
    snapGuideTime: null,
    snapGuideLabel: '',
    box: null,
    groupDrag: null,
  };

  function storageKey(pid) {
    return STORE_KEY + ':' + (pid || 'global');
  }

  function load(pid) {
    state.projectId = pid || '';
    try {
      const raw = localStorage.getItem(storageKey(pid));
      if (!raw) return;
      const d = JSON.parse(raw);
      if (typeof d.snapEnabled === 'boolean') state.snapEnabled = d.snapEnabled;
    } catch (_) {}
  }

  function persist() {
    try {
      localStorage.setItem(storageKey(state.projectId), JSON.stringify({
        snapEnabled: state.snapEnabled
      }));
    } catch (_) {}
  }

  function setSnap(enabled) {
    state.snapEnabled = !!enabled;
    persist();
    renderToolbarState();
    clearGuide();
  }

  function toggleSnap() {
    setSnap(!state.snapEnabled);
  }

  function toggleBoxSelect() {
    state.boxSelectMode = !state.boxSelectMode;
    renderToolbarState();
    const root = document.getElementById('editor-timeline');
    if (root) root.classList.toggle('session-box-mode', state.boxSelectMode);
    editorToast(state.boxSelectMode ? '框选模式：在时间线空白处拖出矩形选择' : '框选模式已关闭');
  }

  function renderToolbarState() {
    const b = document.getElementById('tl-snap-toggle');
    if (b) {
      b.textContent = '吸附：' + (state.snapEnabled ? '开' : '关');
      b.classList.toggle('tl-switch-on', state.snapEnabled);
      b.title = '时间线吸附；按住 Shift 临时关闭';
    }
    const bx = document.getElementById('tl-box-toggle');
    if (bx) {
      bx.textContent = state.boxSelectMode ? '退出框选' : '框选';
      bx.classList.toggle('tl-switch-on', state.boxSelectMode);
    }
  }

  function timelineRoot() {
    return document.querySelector('#editor-timeline .ed-tl-scroll');
  }

  function timelineRect() {
    const root = timelineRoot();
    return root ? root.getBoundingClientRect() : null;
  }

  function timeFromClientX(clientX) {
    const r = timelineRect();
    const total = Number(window.ED && ED.comp && ED.comp.duration) || 1;
    if (!r || r.width <= 0) return 0;
    return Math.max(0, Math.min(total, ((clientX - r.left) / r.width) * total));
  }

  function geometry() {
    const c = window.ED && ED.comp;
    if (!c) return {total: 0, candidates: []};
    const total = Number(c.duration) || 0;
    const candidates = [{time: 0, label: '开头'}, {time: total, label: '结尾'}];

    (c.scenes || []).forEach((sc, i) => {
      const start = Number(sc.start) || 0;
      const end = start + (Number(sc.duration) || 0);
      candidates.push({time: start, label: '场景 ' + (i + 1) + ' 起点', id: sc.id});
      candidates.push({time: end, label: '场景 ' + (i + 1) + ' 终点', id: sc.id});
      (sc.layers || []).forEach((lay, li) => {
        const ls = start + (Number(lay.start) || 0);
        const le = ls + (Number(lay.duration) || 0);
        candidates.push({time: ls, label: '图层起点', id: sc.id + '#' + li});
        candidates.push({time: le, label: '图层终点', id: sc.id + '#' + li});
        (lay.keyframes || []).forEach((kt) => {
          (kt.keyframes || []).forEach((kf) => {
            const at = ls + (Number(kf.at) || 0);
            if (at >= 0 && at <= total) candidates.push({
              time: at, label: '关键帧 · ' + kt.property, id: sc.id + '#' + li
            });
          });
        });
      });
    });

    const ph = Number(window.PLAYHEAD && PLAYHEAD.at);
    if (Number.isFinite(ph)) candidates.push({time: ph, label: '播放头'});
    return {total, candidates};
  }

  function snapTime(time, options = {}) {
    const raw = Number(time);
    if (!Number.isFinite(raw) || !state.snapEnabled || options.disable || options.shiftKey) {
      clearGuide();
      return raw;
    }
    const r = timelineRect();
    const g = geometry();
    if (!r || !g.total || r.width <= 0) return raw;
    const secPerPx = g.total / r.width;
    const threshold = secPerPx * state.snapTolerancePx;
    let best = null;
    for (const c of g.candidates) {
      if (options.excludeId && c.id === options.excludeId) continue;
      const d = Math.abs(c.time - raw);
      if (d <= threshold && (!best || d < best.distance)) {
        best = {...c, distance: d};
      }
    }
    if (!best) {
      clearGuide();
      return raw;
    }
    setGuide(best.time, best.label);
    return best.time;
  }

  function setGuide(time, label) {
    state.snapGuideTime = Number(time);
    state.snapGuideLabel = label || '吸附';
    const root = timelineRoot();
    if (!root || !Number.isFinite(state.snapGuideTime)) return;
    let line = document.getElementById('ed-session-snap-guide');
    if (!line) {
      line = document.createElement('div');
      line.id = 'ed-session-snap-guide';
      line.className = 'ed-session-snap-guide';
      root.appendChild(line);
    }
    const total = Number(window.ED && ED.comp && ED.comp.duration) || 1;
    const pct = Math.max(0, Math.min(100, state.snapGuideTime / total * 100));
    line.style.left = pct + '%';
    line.dataset.label = state.snapGuideLabel;
    // CSS 初始 display:none；置空 inline 不会盖过它，必须显式 block（B7：
    // 此前 display='' → Guide 永远不可见，吸附只剩数值生效无视觉反馈）
    line.style.display = 'block';
  }

  function clearGuide() {
    state.snapGuideTime = null;
    state.snapGuideLabel = '';
    const line = document.getElementById('ed-session-snap-guide');
    if (line) line.style.display = 'none';
  }

  function boxHitIds(rect, scope) {
    const selector = scope === 'layer' ? '.ed-layer' : '.ed-scene';
    const nodes = [...document.querySelectorAll('#editor-timeline ' + selector)];
    return nodes.filter(node => {
      const r = node.getBoundingClientRect();
      return r.right >= rect.left && r.left <= rect.right &&
             r.bottom >= rect.top && r.top <= rect.bottom;
    }).map(node => scope === 'layer'
      ? node.dataset.scene + '#' + node.dataset.idx
      : node.dataset.scene
    ).filter(Boolean);
  }

  function commitBoxSelection(rect, scope, additive) {
    if (!window.ED || !ED.comp || !window.SEL) return;
    const ids = boxHitIds(rect, scope);
    if (!ids.length) {
      if (!additive && typeof clearSelection === 'function') clearSelection('box-select');
      return;
    }
    const last = ids[ids.length - 1];
    const boxEv = {boxIds: ids, ctrlKey: additive, metaKey: false, shiftKey: false};
    // 走唯一 Selection 写入者（§63 不允许第二套 selection）：boxIds 批量事务
    // 模式——整个命中集合一次提交、只发一次 selection:changed。
    if (scope === 'layer') select('layer', last, boxEv, 'box-select');
    else select('scene', last, boxEv, 'box-select');
  }

  function startBox(ev) {
    const root = timelineRoot();
    if (!root || !state.boxSelectMode || ev.button !== 0) return false;
    const target = ev.target;
    if (target.closest('.ed-scene-header, .ed-layer, .ed-track-clip, .ed-kf-dot, #tl-playhead, button, input, select')) {
      return false;
    }
    const r = root.getBoundingClientRect();
    state.box = {
      startX: ev.clientX,
      startY: ev.clientY,
      scope: target.closest('.ed-scene-body') ? 'layer' : 'scene',
      additive: !!(ev.ctrlKey || ev.metaKey || ev.shiftKey),
      rootRect: r
    };
    let box = document.getElementById('ed-session-box');
    if (!box) {
      box = document.createElement('div');
      box.id = 'ed-session-box';
      box.className = 'ed-session-box';
      root.appendChild(box);
    }
    box.style.display = '';
    updateBox(ev);
    try { root.setPointerCapture(ev.pointerId); } catch (_) {}
    ev.preventDefault();
    ev.stopImmediatePropagation();
    return true;
  }

  function updateBox(ev) {
    const b = state.box;
    const box = document.getElementById('ed-session-box');
    if (!b || !box) return;
    const left = Math.min(b.startX, ev.clientX) - b.rootRect.left;
    const top = Math.min(b.startY, ev.clientY) - b.rootRect.top;
    const width = Math.abs(ev.clientX - b.startX);
    const height = Math.abs(ev.clientY - b.startY);
    box.style.left = left + 'px';
    box.style.top = top + 'px';
    box.style.width = width + 'px';
    box.style.height = height + 'px';
    b.rect = (width > 6 || height > 6) ? {
      left: Math.min(b.startX, ev.clientX),
      top: Math.min(b.startY, ev.clientY),
      right: Math.max(b.startX, ev.clientX),
      bottom: Math.max(b.startY, ev.clientY)
    } : null;
    // 拖框过程只做视觉高亮（零 selection:changed）：selection:changed 会触发
    // 五区同步整树重渲（editorRenderTimeline innerHTML 替换），把正在拖的
    // 矩形框和命中节点全部打掉。提交推迟到 pointerup 单次事务（B3）。
    const selector = b.scope === 'layer' ? '.ed-layer' : '.ed-scene';
    const nodes = [...document.querySelectorAll('#editor-timeline ' + selector)];
    nodes.forEach(node => {
      if (!b.rect) { node.classList.remove('tl-box-hit'); return; }
      const r = node.getBoundingClientRect();
      const hit = r.right >= b.rect.left && r.left <= b.rect.right &&
                  r.bottom >= b.rect.top && r.top <= b.rect.bottom;
      node.classList.toggle('tl-box-hit', hit);
    });
  }

  function endBox() {
    const b = state.box;
    state.box = null;
    const box = document.getElementById('ed-session-box');
    if (box) box.style.display = 'none';
    document.querySelectorAll('#editor-timeline .tl-box-hit')
      .forEach(el => el.classList.remove('tl-box-hit'));
    if (b && b.rect) commitBoxSelection(b.rect, b.scope, b.additive);
  }

  function selectedSceneIds() {
    return [...(window.SEL && SEL.selectionSet || [])]
      .filter(k => k.startsWith('scene:'))
      .map(k => k.slice(6))
      .filter(id => (ED.comp.scenes || []).some(s => s.id === id));
  }

  function startGroupDrag(ev, header) {
    if (!window.SEL || !window.ED || !ED.comp) return false;
    const source = header.closest('.ed-scene');
    const sourceId = source && source.dataset.scene;
    const ids = selectedSceneIds();
    if (!sourceId || ids.length < 2 || !ids.includes(sourceId) || ev.button !== 0) return false;

    state.groupDrag = {
      sourceId,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      moved: false
    };
    try { header.setPointerCapture(ev.pointerId); } catch (_) {}
    ev.preventDefault();
    ev.stopImmediatePropagation();
    ids.forEach(id => {
      const el = document.querySelector('#editor-timeline .ed-scene[data-scene="' + CSS.escape(id) + '"]');
      if (el) el.classList.add('tl-group-dragging');
    });
    return true;
  }

  async function finishGroupDrag(ev) {
    const d = state.groupDrag;
    if (!d) return;
    const ids = selectedSceneIds();
    state.groupDrag = null;
    document.querySelectorAll('#editor-timeline .tl-group-dragging')
      .forEach(el => el.classList.remove('tl-group-dragging'));
    clearGuide();
    if (!d.moved || !ids.length) return;

    const target = [...document.querySelectorAll('#editor-timeline .ed-scene')].find(scEl => {
      const r = scEl.getBoundingClientRect();
      return ev.clientX >= r.left && ev.clientX <= r.right &&
             ev.clientY >= r.top && ev.clientY <= r.bottom;
    });
    if (!target) return;
    const anchorId = target.dataset.scene;
    if (!anchorId || ids.includes(anchorId)) return;

    editorToast('正在移动已选场景…');
    const r = await executeCommand({
      command: 'batch',
      targetId: '',
      payload: {subcommand: 'move', targets: ids, anchorId}
    });
    if (r && r.ok) {
      await refreshWorkspace();
      editorToast('已按原顺序移动所选场景');
    } else {
      editorToast('批量移动未完成：' + ((r && r.error) || '未生效'), true);
    }
  }

  function bindTimeline() {
    const root = timelineRoot();
    if (!root || root.dataset.sessionBound === '1') return;
    root.dataset.sessionBound = '1';

    root.addEventListener('pointerdown', (ev) => {
      if (startBox(ev)) return;
    }, true);

    root.addEventListener('pointermove', (ev) => {
      if (state.box) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        updateBox(ev);
      }
      if (state.groupDrag) {
        const dx = Math.abs(ev.clientX - state.groupDrag.startX);
        if (dx > 8) {
          state.groupDrag.moved = true;
          ev.preventDefault();
          ev.stopImmediatePropagation();
          const t = timeFromClientX(ev.clientX);
          snapTime(t, {shiftKey: ev.shiftKey});
        }
      }
    }, true);

    root.addEventListener('pointerup', (ev) => {
      if (state.box) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        endBox();
        return;
      }
      if (state.groupDrag) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        finishGroupDrag(ev);
      }
    }, true);

    root.addEventListener('pointercancel', () => {
      endBox();
      state.groupDrag = null;
      clearGuide();
    }, true);

    root.addEventListener('pointerleave', ev => {
      // 播放头拖动没有指针捕获：移动中 hover 目标离开子元素会冒泡出
      // pointerleave（实测每步 move 都触发 leave+clear），吸附 Guide 刚
      // set 就被清掉（B8）。拖动期间不清；真正离开（root 外）时才清。
      const phDragging = !!(window.PLAYHEAD && PLAYHEAD.dragging);
      if (!state.groupDrag && !phDragging) clearGuide();
    }, true);

    root.querySelectorAll('.ed-scene-header').forEach(header => {
      header.addEventListener('pointerdown', ev => startGroupDrag(ev, header), true);
    });

    renderToolbarState();
  }

  function init(pid) {
    if (pid && pid !== state.projectId) load(pid);
    else if (!state.projectId) load(pid || '');
    renderToolbarState();
    bindTimeline();
  }

  document.addEventListener('selection:changed', renderToolbarState);
  document.addEventListener('editor:session-init', ev => init(ev.detail && ev.detail.projectId));
  document.addEventListener('editor:timeline-rendered', () => {
    init(window.ED && ED.pid);
  });

  window.editorSessionSnapTime = snapTime;
  window.editorSessionBindTimeline = () => init(window.ED && ED.pid);
  window.editorSessionToggleSnap = toggleSnap;
  window.editorSessionToggleBox = toggleBoxSelect;
  window.editorSessionSetSnap = setSnap;
  window.editorSessionClearSnapGuide = clearGuide;
  window.editorSessionState = state;
})();
