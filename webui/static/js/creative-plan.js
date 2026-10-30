/* Semantic-beat and shot-plan editor; state persists through project-scoped API. */
(() => {
  const state = {plan: null, projectId: "", exists: false, stale: false, staleReasons: [], referenceOnly:false, report: null};
  let creativePlanLoadGeneration = 0;
  const el = (id) => document.getElementById(id);
  const escHtml = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  const listValue = (value) => (value || []).join("、");
  const splitValues = (value) => String(value || "").split(/[，,;；、]/)
    .map((item) => item.trim()).filter(Boolean);
  const seconds = (ms) => (Number(ms || 0) / 1000).toFixed(2) + "s";
  const projectId = () => (document.getElementById("editor-project") || {}).value || (window.ED && ED.pid) || "";
  const statusText = (value) => ({
    PASS: "PASS", REJECT: "REJECT", MISSING: "MISSING", NEEDS_REVIEW: "NEEDS_REVIEW",
    ALIGNED: "已对齐", PARTIAL: "部分插值 / 需复核"
  })[value] || value || "UNKNOWN";

  function renderStatus(message, isError) {
    const status = el("creative-plan-status");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("creative-plan-error", !!isError);
  }

  function timingFor(beat) {
    return state.plan && (state.plan.character_timings || []).find((item) => item.sentence_id === beat.sentence_id);
  }

  function renderReport(report) {
    state.report = report;
    const box = el("creative-plan-report");
    if (!box) return;
    const issues = report.issues || [];
    if (!issues.length) {
      box.innerHTML = '<div class="creative-plan-verdict creative-plan-pass">预检：PASS。当前计划通过已实现的契约检查；这不替代最终成片质检。</div>';
      return;
    }
    box.innerHTML = '<div class="creative-plan-verdict creative-plan-' + escHtml(report.verdict.toLowerCase()) +
      '">预检：' + escHtml(statusText(report.verdict)) + '</div><ul class="creative-plan-issues">' +
      issues.map((item) => '<li><b>' + escHtml(item.status) + '</b> · ' + escHtml(item.code) + '：' +
        escHtml(item.message) + (item.shot_id ? ' <span>(' + escHtml(item.shot_id) + ')</span>' : '') +
        '</li>').join("") + '</ul>';
  }

  function render() {
    const content = el("creative-plan-content");
    if (!content) return;
    if (!state.plan) {
      content.innerHTML = '<div class="creative-plan-empty">尚无创作草案。生成时只读取项目已有稿件与时间戳，不会暗中调用模型或联网。</div>';
      return;
    }
    const p = state.plan;
    const options = (p.shots || []).map((shot) =>
      '<option value="' + escHtml(shot.shot_id) + '">' + escHtml(shot.shot_id) + '</option>'
    ).join("");
    const beatRows = (p.semantic_beats || []).map((beat, i) => {
      const timing = timingFor(beat);
      const chars = (timing && timing.characters) || [];
      const anchorLabel = !timing || timing.status === "MISSING"
        ? "没有可验证字级锚点"
        : (chars.length + " 个字符锚点 · " + statusText(timing.status));
      const first = chars[0], last = chars[chars.length - 1];
      return '<tr><td><div class="creative-plan-sentence">' + escHtml(beat.text) + '</div>' +
        '<div class="creative-plan-meta">' + escHtml(beat.sentence_id) + '</div>' +
        '<div class="creative-plan-meta">' + escHtml(anchorLabel) + '</div>' +
        '<div class="creative-plan-meta">' + (first && last ? escHtml(seconds(first.start_ms) + " – " + seconds(last.end_ms)) : "时间待补") + '</div></td>' +
        '<td><input data-beat-intent="' + i + '" value="' + escHtml(listValue(beat.intent)) + '" placeholder="意图：数据、引用、自我介绍">' +
        '<input data-beat-entities="' + i + '" value="' + escHtml(listValue(beat.entities)) + '" placeholder="实体：人物、数字、品牌、来源"></td>' +
        '<td><input data-beat-need="' + i + '" value="' + escHtml(listValue(beat.visual_need)) + '" placeholder="画面需求：证据、身份、量化、对比、结构、强调">' +
        '<select data-beat-weight="' + i + '"><option value="main">main · 主句</option><option value="sub">sub · 陪衬</option></select>' +
        '<select data-beat-shot="' + i + '"><option value="">未分配</option>' + options + '</select></td></tr>';
    }).join("");
    const shotCards = (p.shots || []).map((shot, i) => {
      const layer = (shot.layers || [])[0] || {};
      const beatLabels = (shot.beat_ids || []).map((id) => {
        const beat = (p.semantic_beats || []).find((item) => item.id === id);
        return beat ? beat.text : id;
      });
      return '<div class="creative-plan-shot"><div class="creative-plan-shot-head"><b>' + escHtml(shot.shot_id) +
        '</b><span class="creative-plan-meta">' + escHtml(seconds(shot.start_ms) + " – " + seconds(shot.end_ms)) +
        '</span></div><div class="creative-plan-meta">语义拍：' + escHtml(beatLabels.join(" / ") || "尚未分配") + '</div>' +
        '<label>镜头叙事意图</label><input data-shot-intent="' + i + '" value="' + escHtml(shot.intent) +
        '" placeholder="这一镜让观众理解或感受到什么？">' +
        '<label>动效配方 ID（必须存在于当前 Registry）</label><input data-shot-recipe="' + i + '" value="' +
        escHtml(layer.recipe_id || "") + '" placeholder="motion.recipe-id">' +
        '<label>素材 ID（逗号分隔）</label><input data-shot-assets="' + i + '" value="' +
        escHtml((layer.asset_ids || []).join("、")) + '" placeholder="asset-id-1、asset-id-2">' +
        '<label>证据引用（资产 / 文件 ID）</label><input data-shot-evidence="' + i + '" value="' +
        escHtml((layer.evidence_refs || []).join("、")) + '" placeholder="来源截图或证据资产 ID">' +
        '<div class="creative-plan-meta">未解决：' + escHtml((shot.unresolved_items || []).join("；") || "无") + '</div></div>';
    }).join("");
    const timingRows = (p.character_timings || []).map((timing) => {
      const sources = {};
      (timing.characters || []).forEach((char) => { sources[char.source] = (sources[char.source] || 0) + 1; });
      const sourceText = Object.keys(sources).map((key) => key + "：" + sources[key]).join(" · ");
      return '<tr><td>' + escHtml(timing.text) + '</td><td>' + escHtml(statusText(timing.status)) +
        '</td><td>' + escHtml(String((timing.characters || []).length)) + '</td><td>' +
        escHtml(sourceText || "无锚点") + '</td><td>' +
        escHtml((timing.review_reasons || []).join("；") || "无") + '</td></tr>';
    }).join("");

    content.innerHTML =
      '<div class="creative-plan-meta">计划版本 v' + escHtml(p.version) + ' · 时间来源：' +
      escHtml(p.timing_source) + ' · 语义拍：' + escHtml((p.semantic_beats || []).length) +
      ' · 镜头：' + escHtml((p.shots || []).length) + '</div>' +
      (state.stale ? '<div class="creative-plan-warning">稿件或配音指纹已变化。计划只供查看，不能保存；重新生成会覆盖旧标注。</div>' : '') +
      '<div class="creative-plan-section"><h4>语义拍（逐句标注）</h4><div class="creative-plan-table-wrap">' +
      '<table class="creative-plan-table"><thead><tr><th>原句 / 时间锚点</th><th>意图 / 实体</th>' +
      '<th>画面需求 / 叙事权重 / 镜头</th></tr></thead><tbody>' +
      (beatRows || '<tr><td colspan="3">没有可标注的旁白句子。</td></tr>') +
      '</tbody></table></div></div><div class="creative-plan-section"><h4>镜头计划</h4>' +
      '<div class="creative-plan-shot-grid">' + shotCards + '</div></div>' +
      '<div class="creative-plan-section"><h4>字级时间来源审计</h4><div class="creative-plan-table-wrap">' +
      '<table class="creative-plan-table"><thead><tr><th>原句</th><th>状态</th><th>字符锚点</th><th>时间来源</th><th>需复核原因</th></tr></thead><tbody>' +
      (timingRows || '<tr><td colspan="5">没有时间轴产物。</td></tr>') +
      '</tbody></table></div></div>';

    (p.semantic_beats || []).forEach((beat, i) => {
      const shotSelect = content.querySelector('[data-beat-shot="' + i + '"]');
      const weightSelect = content.querySelector('[data-beat-weight="' + i + '"]');
      if (shotSelect) shotSelect.value = beat.shot_id || "";
      if (weightSelect) weightSelect.value = beat.weight || "main";
    });
    if (state.report) renderReport(state.report);
  }

  window.creativePlanToggle = async function () {
    const panel = el("creative-plan-panel");
    if (!panel) return;
    const opening = panel.style.display === "none" || !panel.style.display;
    panel.style.display = opening ? "" : "none";
    if (opening) await window.creativePlanReload();
  };

  window.creativePlanReload = async function () {
    const pid = projectId();
    if (!pid) { renderStatus("请先在工作台打开一个项目。", true); return; }
    const requestGeneration = ++creativePlanLoadGeneration;
    renderStatus("正在读取项目创作计划……");
    const result = await apiGet("/api/editor/creative-plan?project_id=" + encodeURIComponent(pid));
    if (projectId() !== pid || requestGeneration !== creativePlanLoadGeneration) return;
    if (result && result.plan && result.plan.project_id &&
        String(result.plan.project_id) !== String(pid)) {
      renderStatus("创作计划响应项目与当前项目不一致；已丢弃过期结果。", true);
      return;
    }
    if (!result || result.ok === false || result.error) {
      renderStatus("读取失败：" + (result.error || "未知错误"), true);
      return;
    }
    state.projectId = pid;
    state.exists = !!result.exists;
    state.plan = result.plan || null;
    state.referenceOnly = result.reference_only === true;
    state.stale = !!result.stale && !state.referenceOnly;
    state.staleReasons = result.stale_reasons || [];
    state.report = null;
    if (!state.plan) renderStatus(state.referenceOnly
      ? "当前项目没有旧版语义计划；film.html 仍是唯一时间线。"
      : "尚无创作计划。点击“从当前稿件生成草案”开始。");
    else if (state.referenceOnly) renderStatus("已载入旧版语义计划，只读作导演参考；旧时间戳与 shot ID 不用于当前影片。"+(state.staleReasons.length?" 说明："+state.staleReasons.join("；"):""));
    else if (state.stale) renderStatus("计划已过期：" + state.staleReasons.join("；"), true);
    else renderStatus("已加载。保存会进行版本检查，不会直接修改 CompositionIR。");
    render();
    const initializeButton=document.querySelector('#creative-plan-panel [data-action="ui.creative_plan_initialize"]');
    const validateButton=document.querySelector('#creative-plan-panel [data-action="ui.creative_plan_validate"]');
    [initializeButton,validateButton].forEach((button) => {
      if (!button) return;
      button.disabled=state.referenceOnly;
      button.title=state.referenceOnly
        ? "旧计划依赖 CompositionIR；请先转换为当前 film.html 的 revision 绑定提案"
        : "";
    });
  };

  window.creativePlanInitialize = async function () {
    const pid = projectId();
    if (!pid) { renderStatus("请先在工作台打开一个项目。", true); return; }
    if (state.projectId === pid && state.exists && !window.confirm("重新生成会覆盖已有语义标注和镜头计划，是否继续？")) return;
    renderStatus("正在读取项目现有时间戳；缺少时间戳的句子会明确标为 MISSING……");
    const result = await apiPost("/api/editor/creative-plan", {
      project_id: pid, action: "initialize", replace: true
    });
    if (projectId() !== pid) return;
    if (result && result.plan && result.plan.project_id &&
        String(result.plan.project_id) !== String(pid)) {
      renderStatus("创作计划初始化响应项目与当前项目不一致；已丢弃结果。", true);
      return;
    }
    if (!result || result.ok === false || result.error) {
      renderStatus("生成失败：" + (result.error || "未知错误"), true);
      return;
    }
    state.projectId = pid;
    state.plan = result.plan;
    state.exists = true;
    state.stale = false;
    state.staleReasons = [];
    state.report = null;
    renderStatus("草案已生成。时间文件来源：" + result.alignment_source +
      "；读取到时间段：" + result.word_boundary_count + "。语义意图与画面需求仍需明确填写。");
    render();
  };

  window.creativePlanSave = async function (quiet) {
    const pid = projectId();
    if (!state.plan || state.stale) {
      renderStatus(state.stale ? "计划已过期，请重新生成。" : "先生成创作草案。", true);
      return false;
    }
    if (!pid || state.projectId !== pid) {
      renderStatus("当前创作计划不属于当前项目；已拒绝保存，请先重新载入当前项目的计划。", true);
      return false;
    }
    const content = el("creative-plan-content");
    if (!content) return false;
    state.plan.semantic_beats.forEach((beat, i) => {
      beat.intent = splitValues(content.querySelector('[data-beat-intent="' + i + '"]').value);
      beat.entities = splitValues(content.querySelector('[data-beat-entities="' + i + '"]').value);
      beat.visual_need = splitValues(content.querySelector('[data-beat-need="' + i + '"]').value);
      beat.weight = content.querySelector('[data-beat-weight="' + i + '"]').value;
      beat.shot_id = content.querySelector('[data-beat-shot="' + i + '"]').value || null;
    });
    state.plan.shots.forEach((shot, i) => {
      shot.intent = content.querySelector('[data-shot-intent="' + i + '"]').value.trim();
      const recipeId = content.querySelector('[data-shot-recipe="' + i + '"]').value.trim();
      const assetIds = splitValues(content.querySelector('[data-shot-assets="' + i + '"]').value);
      const evidenceRefs = splitValues(content.querySelector('[data-shot-evidence="' + i + '"]').value);
      shot.layers = (recipeId || assetIds.length || evidenceRefs.length) ? [{
        role: "主视觉", recipe_id: recipeId || null, asset_ids: assetIds,
        evidence_refs: evidenceRefs, required: true
      }] : [];
      shot.beat_ids = state.plan.semantic_beats.filter((beat) => beat.shot_id === shot.shot_id).map((beat) => beat.id);
      shot.unresolved_items = [];
      if (!shot.intent) shot.unresolved_items.push("未确认镜头意图");
      if (!shot.layers.length) shot.unresolved_items.push("未配置画面素材或动效配方");
    });
    renderStatus("正在保存……");
    const result = await apiPost("/api/editor/creative-plan", {
      project_id: pid, action: "save",
      expected_version: state.plan.version, plan: state.plan
    });
    if (projectId() !== pid || state.projectId !== pid) return false;
    if (result && result.plan && result.plan.project_id &&
        String(result.plan.project_id) !== String(pid)) return false;
    if (!result || result.ok === false || result.error) {
      if (result && result.stale) state.stale = true;
      renderStatus("保存失败：" + (result.error || "未知错误"), true);
      render();
      return false;
    }
    state.plan = result.plan;
    state.exists = true;
    state.report = null;
    renderStatus(result.reference_only === true
      ? "已保存 v" + state.plan.version + "。该创作计划仅作为 film.html 项目的导演参考；正式影片未被改写。"
      : "已保存 v" + state.plan.version + "。创作计划已持久化，CompositionIR 尚未被直接改写。");
    if (!quiet) render();
    return true;
  };

  window.creativePlanExportForFilmDirector = function () {
    if (!state.plan || !state.projectId || state.projectId !== projectId()) return null;
    return {
      project_id:state.plan.project_id,version:state.plan.version,
      reference_only:state.referenceOnly===true,stale:state.stale===true||state.referenceOnly===true,
      stale_reasons:(state.staleReasons||[]).slice(0,4),
      semantic_beats:(state.plan.semantic_beats||[]).slice(0,8).map((beat) => ({
        sentence_id:String(beat.sentence_id||'').slice(0,100),text:String(beat.text||'').slice(0,160),
        intent:String(beat.intent||'').slice(0,120),entities:(beat.entities||[]).slice(0,4),
        visual_need:(beat.visual_need||[]).slice(0,4),shot_id:String(beat.shot_id||'').slice(0,100)
      })),
      shots:(state.plan.shots||[]).slice(0,6).map((shot) => ({
        shot_id:String(shot.shot_id||'').slice(0,100),intent:String(shot.intent||'').slice(0,140),
        unresolved_items:(shot.unresolved_items||[]).slice(0,4),
        layers:(shot.layers||[]).slice(0,2).map((layer) => ({
          role:String(layer.role||'').slice(0,60),recipe_id:String(layer.recipe_id||'').slice(0,90),
          asset_ids:(layer.asset_ids||[]).slice(0,4),evidence_refs:(layer.evidence_refs||[]).slice(0,4)
        }))
      }))
    };
  };

  window.creativePlanValidate = async function () {
    if (!await window.creativePlanSave(true)) return;
    const pid = projectId();
    if (!pid || state.projectId !== pid) {
      renderStatus("当前创作计划不属于当前项目；已取消预检。", true);
      return;
    }
    renderStatus("正在对照当前 CompositionIR、时间锚点、镜头 ID 与 Registry 预检……");
    const result = await apiPost("/api/editor/creative-plan", {
      project_id: pid, action: "validate"
    });
    if (projectId() !== pid || state.projectId !== pid) return;
    if (!result || result.ok === false || result.error) {
      renderStatus("预检失败：" + (result.error || "未知错误"), true);
      return;
    }
    renderReport(result.report);
    renderStatus("预检完成：" + statusText(result.report.verdict) +
      "。PASS 仅代表已实现的契约检查通过，不替代成片 QC 与独立审片。");
  };
})();
