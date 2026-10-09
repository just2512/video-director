/* TalkCraft-inspired speech-to-shot workspace.
 * User annotations are persisted as project artifacts; scene intent writes still go
 * through the existing Editor Command -> Revision endpoint. No third-party card code
 * is copied into this repository.
 */
(() => {
  const TC = { projectId: "", data: null, loading: false, lastError: "", renderPoll: null, renderStatus: null };
  const $ = (id) => document.getElementById(id);
  const SEMANTICS = ["钩子","自我介绍","介绍他人","数据","引用","对比","定义","步骤","列举","时间地点","机制","选择","过程演示","空间叙事","设问","金句","章节","论点","转折","例证","号召"];
  const NEEDS = ["证据","身份","量化","对比","结构","强调","无"];
  const ROLES = [
    ["", "未指定"], ["hook","开场钩子"], ["context","背景"], ["problem","问题"],
    ["evidence","证据"], ["turn","转折"], ["resolution","解决/结论"], ["cta","行动号召"], ["body","正文"]
  ];

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function selectInput(className, options, value) {
    const node = el("select", className);
    for (const item of options) {
      const opt = el("option", "", item[1]);
      opt.value = item[0];
      opt.selected = item[0] === value;
      node.appendChild(opt);
    }
    return node;
  }
  function field(labelText, input) {
    const wrap = el("label", "tc-field");
    wrap.appendChild(el("span", "tc-field-label", labelText));
    wrap.appendChild(input);
    return wrap;
  }
  function inputText(cls, value, placeholder) {
    const node = el("input", cls);
    node.type = "text";
    node.value = value || "";
    node.placeholder = placeholder || "";
    return node;
  }
  function timeLabel(sec) {
    const ms = Math.max(0, Math.round(Number(sec || 0) * 1000));
    const m = Math.floor(ms / 60000);
    const s = ((ms % 60000) / 1000).toFixed(2).padStart(5, "0");
    return m + ":" + s;
  }
  function setMessage(message, kind) {
    const box = $("tc-message");
    if (!box) return;
    box.textContent = message || "";
    box.className = "tc-message" + (kind ? " is-" + kind : "");
  }
  function statusLabel(status) {
    return ({
      READY: "蓝图已载入", STALE: "输入已变化", NOT_PREPARED: "尚未生成蓝图",
      ALIGNED_FROM_WORDS: "来自词级时间戳", PARTIAL: "部分覆盖 / 待复核",
      UNKNOWN: "时间戳未知", NEEDS_REVIEW: "待人工复核", REVIEWED: "已复核",
      PLANNED_NOT_APPLIED: "仅已写入计划", APPLIED_TO_LAYER: "已应用到图层"
    })[status] || status || "未知状态";
  }

  async function requestPlan() {
    if (!window.ED || !ED.pid) {
      setMessage("请先在工作台打开一个有 CompositionIR 的项目。", "warn");
      return null;
    }
    const pid = ED.pid;
    const r = await apiGet("/api/talkcraft/plan?project_id=" + encodeURIComponent(pid));
    if (!r || r.ok === false) {
      setMessage((r && r.error) || "读取口播创作蓝图失败。", "error");
      return null;
    }
    TC.projectId = pid;
    TC.data = r;
    render();
    return r;
  }

  async function talkcraftLoad(force) {
    if (TC.loading) return;
    if (!window.ED || !ED.pid) return;
    if (!force && TC.projectId === ED.pid && TC.data) return;
    TC.loading = true;
    try { await requestPlan(); }
    finally { TC.loading = false; }
  }

  async function talkcraftPrepare() {
    if (!window.ED || !ED.pid) {
      setMessage("请先打开项目，再生成蓝图。", "warn");
      return;
    }
    const button = $("tc-prepare");
    if (button) { button.disabled = true; button.textContent = "正在整理…"; }
    setMessage("正在读取旁白时间轴、语义线索与现有镜头；不会覆盖 CompositionIR 中的时间和素材。");
    try {
      const r = await apiPost("/api/talkcraft/prepare", {project_id: ED.pid});
      if (!r || r.ok === false) {
        setMessage((r && r.error) || "生成蓝图失败。", "error");
        return;
      }
      TC.projectId = ED.pid;
      TC.data = r;
      render();
      setMessage(r.message || "蓝图已生成。", "success");
    } catch (e) {
      setMessage(String(e), "error");
    } finally {
      if (button) { button.disabled = false; button.textContent = "生成 / 刷新蓝图"; }
    }
  }

  function summarize(data) {
    const box = $("tc-summary");
    if (!box) return;
    box.replaceChildren();
    const needsReview = (data.beats || []).filter(b => b.review_status !== "REVIEWED").length;
    const uncertainTiming = (data.timing && data.timing.sentences || []).filter(s => s.status !== "ALIGNED_FROM_WORDS").length;
    const values = [
      ["镜头", data.shot_count || 0],
      ["语义拍", data.beat_count || 0],
      ["待复核标注", needsReview],
      ["时间戳待复核", uncertainTiming],
      ["时间轴", statusLabel(data.timing_status)]
    ];
    for (const [name, value] of values) {
      const item = el("div", "tc-stat");
      item.appendChild(el("span", "tc-stat-name", name));
      item.appendChild(el("strong", "tc-stat-value", value));
      box.appendChild(item);
    }
  }

  function compatibleRecipes(shot, layerIndex) {
    const layerType = (shot.layer_types || [])[layerIndex] || "";
    return (TC.data.recipes || []).filter(recipe => {
      if (!recipe.id || !recipe.enabled || recipe.commercial_status === "BLOCKED" ||
          recipe.commercial_status === "UNKNOWN") return false;
      if (!(recipe.compatible_layers || []).includes(layerType)) return false;
      const props = new Set((recipe.channels || []).map(ch => ch && ch.property).filter(Boolean));
      // Match the renderer, not just the Registry's broad compatible_layers field.
      if (layerType === "video" && (!props.size || Array.from(props).some(p => p !== "opacity"))) return false;
      if (layerType === "image" && (!props.size || Array.from(props).some(p => !["opacity", "scale"].includes(p)))) return false;
      if (!["video", "image"].includes(layerType)) return false;
      return true;
    });
  }

  function buildBeatCard(beat, shot) {
    const card = el("article", "tc-beat");
    const header = el("div", "tc-beat-head");
    header.appendChild(el("span", "tc-beat-number", beat.scene_id));
    header.appendChild(el("span", "tc-time", timeLabel(beat.start) + " – " + timeLabel(beat.end)));
    const timingState = beat.timing_status === "ALIGNED_FROM_WORDS" ? "有词级锚点（字内插值）" :
      (beat.timing_status === "PARTIAL" ? "部分对齐 · 需复核" : "无可靠词级锚点 · 需复核");
    header.appendChild(el("span", "tc-pill " + (beat.timing_status === "ALIGNED_FROM_WORDS" ? "is-ok" : "is-warn"), timingState));
    header.appendChild(el("span", "tc-pill " + (beat.review_status === "REVIEWED" ? "is-ok" : "is-warn"), statusLabel(beat.review_status)));
    card.appendChild(header);

    const quote = el("blockquote", "tc-script", beat.text || "（空文本）");
    card.appendChild(quote);

    const grid = el("div", "tc-edit-grid");
    const sem = inputText("tc-sem", (beat.sem || []).join("、"), "用“、”分隔，例如 数据、对比");
    const need = inputText("tc-need", (beat.need || []).join("、"), "证据、量化、结构…");
    const entities = inputText("tc-entities", (beat.entities || []).join("、"), "人物、数字、来源 URL…");
    const intent = inputText("tc-intent", beat.intent, "叙事意图：这一句要让观众理解什么？");
    const visual = inputText("tc-visual-intent", beat.visual_intent, "画面意图：观众此刻应该看到什么？");
    const weight = selectInput("tc-weight", [["main","主句 main（可引入新元素）"],["sub","陪衬 sub（只改变已有元素）"]], beat.weight || "main");
    const role = selectInput("tc-role", ROLES, beat.semantic_role || "");
    grid.appendChild(field("语义标签", sem));
    grid.appendChild(field("画面需求", need));
    grid.appendChild(field("实体 / 证据线索", entities));
    grid.appendChild(field("叙事意图", intent));
    grid.appendChild(field("画面意图", visual));
    grid.appendChild(field("句子职责", weight));
    grid.appendChild(field("叙事角色", role));
    card.appendChild(grid);

    const footer = el("div", "tc-beat-actions");
    const reviewedWrap = el("label", "tc-check");
    const reviewed = el("input");
    reviewed.type = "checkbox";
    reviewed.checked = beat.review_status === "REVIEWED";
    reviewedWrap.appendChild(reviewed);
    reviewedWrap.appendChild(document.createTextNode("我已核对本句语义"));
    footer.appendChild(reviewedWrap);
    const save = el("button", "btn-sm", "保存标注");
    save.type = "button";
    save.disabled = TC.data.status === "STALE";
    save.addEventListener("click", async () => {
      save.disabled = true;
      setMessage("正在保存语义拍和场景意图…");
      const payload = {
        project_id: TC.projectId, beat_id: beat.id,
        expected_revision: TC.data.revision,
        composition_version: Number(ED.version),
        sem: sem.value, need: need.value, entities: entities.value,
        intent: intent.value, visual_intent: visual.value,
        weight: weight.value, semantic_role: role.value,
        mark_reviewed: reviewed.checked
      };
      const r = await apiPost("/api/talkcraft/beat", payload);
      save.disabled = false;
      if (!r || r.ok === false) {
        setMessage((r && r.error) || "保存标注失败。", "error");
        if (r && /CONFLICT|版本冲突|REVISION_CONFLICT/.test(r.error || "")) await talkcraftLoad(true);
        return;
      }
      TC.data.revision = r.revision;
      if (r.shot) {
        const oldShot = (TC.data.shots || []).find(s => s.scene_id === r.shot.scene_id);
        if (oldShot) Object.assign(oldShot, r.shot);
      }
      if (window.ED) {
        ED.version = r.composition_version;
        if (ED.comp) ED.comp.version = r.composition_version;
      }
      const current = (TC.data.beats || []).find(b => b.id === beat.id);
      if (current) Object.assign(current, r.beat);
      render();
      setMessage(r.message || "已保存。", "success");
    });
    footer.appendChild(save);
    card.appendChild(footer);

    if (shot) {
      const recipeArea = el("div", "tc-recipe-area");
      const top = el("div", "tc-recipe-title");
      top.appendChild(el("strong", "", "镜头配方"));
      top.appendChild(el("span", "tc-muted", "先保存计划，再明确应用到实际图层"));
      recipeArea.appendChild(top);
      const recipeControls = el("div", "tc-recipe-controls");
      const layerOptions = (shot.layer_types || []).map((t, i) => [String(i), "第 " + (i + 1) + " 层 · " + t]);
      const layerSelect = selectInput("tc-layer-target", layerOptions.length ? layerOptions : [["0","无可用图层"]], "0");
      const recipeSelect = el("select", "tc-recipe-select");
      function populateRecipes() {
        recipeSelect.replaceChildren();
        const allowed = compatibleRecipes(shot, Number(layerSelect.value));
        const placeholder = el("option", "", allowed.length ? "选择可执行的动效配方…" : "当前图层没有可执行的注册配方");
        placeholder.value = "";
        recipeSelect.appendChild(placeholder);
        for (const rec of allowed) {
          const label = rec.name + (rec.commercial_status === "REVIEW" ? " · 使用前复核来源" : "");
          const opt = el("option", "", label);
          opt.value = rec.id;
          opt.selected = rec.id === shot.recipe_id;
          recipeSelect.appendChild(opt);
        }
        if (shot.recipe_id && !allowed.some(r => r.id === shot.recipe_id)) {
          const stale = el("option", "", "现有计划配方不可应用到当前图层");
          stale.value = "";
          recipeSelect.insertBefore(stale, recipeSelect.firstChild);
        }
      }
      layerSelect.addEventListener("change", populateRecipes);
      populateRecipes();
      recipeControls.appendChild(field("目标图层", layerSelect));
      recipeControls.appendChild(field("可执行配方", recipeSelect));
      recipeArea.appendChild(recipeControls);
      const recipeStatus = el("div", "tc-recipe-status", shot.recipe_status ? statusLabel(shot.recipe_status) + (shot.applied_layer ? " · " + shot.applied_layer : "") : "尚未选择配方");
      recipeArea.appendChild(recipeStatus);
      const actions = el("div", "tc-beat-actions");
      const planButton = el("button", "btn-secondary btn-sm", "保存配方到计划");
      planButton.type = "button";
      planButton.disabled = !recipeSelect.value;
      recipeSelect.addEventListener("change", () => { planButton.disabled = !recipeSelect.value; });
      recipeSelect.addEventListener("change", () => { planButton.disabled = !recipeSelect.value; });
      planButton.addEventListener("click", async () => {
        planButton.disabled = true;
        const r = await apiPost("/api/talkcraft/recipe/plan", {
          project_id: TC.projectId, scene_id: shot.scene_id,
          recipe_id: recipeSelect.value, expected_revision: TC.data.shot_revision
        });
        planButton.disabled = false;
        if (!r || r.ok === false) {
          setMessage((r && r.error) || "保存配方计划失败。", "error");
          return;
        }
        TC.data.shot_revision = r.revision;
        const target = (TC.data.shots || []).find(s => s.scene_id === shot.scene_id);
        if (target) Object.assign(target, r.shot);
        render();
        setMessage(r.message, "success");
      });
      actions.appendChild(planButton);
      const applyButton = el("button", "btn-sm", "应用到所选图层");
      applyButton.type = "button";
      applyButton.disabled = !recipeSelect.value || !layerOptions.length;
      recipeSelect.addEventListener("change", () => { applyButton.disabled = !recipeSelect.value || !layerOptions.length; });
      layerSelect.addEventListener("change", () => {
        applyButton.disabled = !recipeSelect.value || !layerOptions.length;
        planButton.disabled = !recipeSelect.value;
      });
      applyButton.addEventListener("click", async () => {
        applyButton.disabled = true;
        const r = await apiPost("/api/talkcraft/recipe/apply", {
          project_id: TC.projectId, scene_id: shot.scene_id,
          layer_index: Number(layerSelect.value), recipe_id: recipeSelect.value,
          expected_version: Number(ED.version)
        });
        applyButton.disabled = false;
        if (!r || r.ok === false) {
          setMessage((r && r.error) || "应用动效失败。", "error");
          if (r && /CONFLICT|版本冲突/.test(r.error || "")) await talkcraftLoad(true);
          return;
        }
        ED.version = r.composition_version;
        if (ED.comp) ED.comp.version = r.composition_version;
        setMessage(r.message + " 正在刷新时间线…", "success");
        await editorLoad();
        if (typeof selectLayer === "function") selectLayer(shot.scene_id, Number(layerSelect.value), null, "talkcraft");
        await talkcraftLoad(true);
      });
      actions.appendChild(applyButton);
      recipeArea.appendChild(actions);
      card.appendChild(recipeArea);
    }

    return card;
  }

  function render() {
    const data = TC.data;
    const state = $("tc-state");
    const list = $("tc-beats");
    if (!data || !state || !list) return;
    state.textContent = statusLabel(data.status);
    state.className = "tc-status " + (data.status === "READY" ? "is-ok" : (data.status === "STALE" ? "is-warn" : ""));
    $("tc-prepare").textContent = data.status === "NOT_PREPARED" ? "生成创作蓝图" : "生成 / 刷新蓝图";
    summarize(data);
    list.replaceChildren();
    if (data.status === "NOT_PREPARED") {
      setMessage("尚未生成蓝图。点击“生成创作蓝图”，系统将读取当前 CompositionIR 和已有词级时间戳；不会自动改动现有成片。");
      return;
    }
    if (data.status === "STALE") {
      setMessage(data.stale_reason || "项目输入已变化。请刷新蓝图后再编辑，避免把旧标注写回新稿件。", "warn");
      const staleNote = el("div", "tc-stale-note",
        "当前只读显示旧蓝图。刷新后会按新文本和时间范围重建语义拍；源文本已变化时，旧标注不会被静默套用。");
      list.appendChild(staleNote);
      return;
    }
    if (!(data.beats || []).length) {
      setMessage("当前项目没有可编辑语义拍。请先完成旁白与场景规划。", "warn");
      return;
    }
    const shots = new Map((data.shots || []).map(s => [s.scene_id, s]));
    for (const beat of data.beats) list.appendChild(buildBeatCard(beat, shots.get(beat.scene_id)));
    if (data.notes && data.notes.length) {
      const notes = el("ul", "tc-notes");
      for (const n of data.notes) notes.appendChild(el("li", "", n));
      list.prepend(notes);
    }
  }

  const RENDER_LABELS = {
    IDLE: "尚未渲染", QUEUED: "排队中", PREPARING: "准备渲染输入",
    RENDERING: "Remotion 正在逐帧渲染", PASS: "渲染与机器 QC 已通过",
    REJECT: "渲染失败或 QC 未通过"
  };

  function updateRenderStatus(state) {
    TC.renderStatus = state;
    const elStatus = $("tc-render-status");
    const output = $("tc-render-output");
    if (elStatus) {
      const label = RENDER_LABELS[state.status] || state.status || "状态未知";
      elStatus.textContent = label + (state.message ? " · " + state.message : "");
      elStatus.className = "tc-render-status" +
        (state.status === "PASS" ? " is-ok" : (state.status === "REJECT" ? " is-error" : ""));
    }
    if (output) {
      if (state.output_url && state.status === "PASS") {
        output.href = state.output_url;
        output.hidden = false;
        output.textContent = "打开最近的 Remotion 成片（" + (state.quality || "render") + "）";
      } else {
        output.hidden = true;
        output.removeAttribute("href");
      }
    }
    const active = ["QUEUED", "PREPARING", "RENDERING"].includes(state.status);
    if (active && TC.renderPoll === null) {
      TC.renderPoll = setInterval(() => { void talkcraftRemotionStatus(); }, 1500);
    } else if (!active && TC.renderPoll !== null) {
      clearInterval(TC.renderPoll);
      TC.renderPoll = null;
    }
    const canRender = !!(TC.data && TC.data.status === "READY") && !active;
    const draft = $("tc-render-draft");
    const final = $("tc-render-final");
    if (draft) draft.disabled = !canRender;
    if (final) final.disabled = !canRender;
  }

  async function talkcraftRemotionStatus() {
    if (!window.ED || !ED.pid) {
      updateRenderStatus({status: "IDLE", message: "请先打开项目。"});
      return null;
    }
    const r = await apiGet("/api/talkcraft/remotion/status?project_id=" + encodeURIComponent(ED.pid));
    if (!r || r.ok === false) {
      updateRenderStatus({status: "REJECT", message: (r && r.error) || "读取渲染状态失败。"});
      return null;
    }
    if (!r.runtime_installed && r.status === "IDLE") {
      r.message = "未安装运行环境。安装方式：cd remotion_runtime && npm install";
    }
    updateRenderStatus(r);
    return r;
  }

  async function talkcraftRemotionRender(quality, confirmed = false) {
    if (!window.ED || !ED.pid) {
      setMessage("请先打开项目，再启动 Remotion 渲染。", "warn");
      return;
    }
    if (!TC.data || TC.data.status !== "READY") {
      setMessage("蓝图尚未就绪或已过期；先生成 / 刷新蓝图。", "warn");
      return;
    }
    const buttons = [$("tc-render-draft"), $("tc-render-final")].filter(Boolean);
    buttons.forEach(b => { b.disabled = true; });
    setMessage(quality === "final" ? "正在检查正式渲染门槛…" : "正在启动 Remotion 预览渲染…");
    try {
      const r = await apiPost("/api/talkcraft/remotion/render", {
        project_id: ED.pid, quality, confirm_budget: confirmed
      });
      if (r && r.confirmation_required) {
        buttons.forEach(b => { b.disabled = false; });
        const proceed = window.confirm(r.message + "\n\n继续后仍会执行完整机器 QC。");
        if (proceed) return talkcraftRemotionRender(quality, true);
        setMessage("已取消正式渲染。", "warn");
        return;
      }
      if (!r || r.ok === false) {
        setMessage((r && r.error) || (r && r.message) || "启动 Remotion 渲染失败。", "error");
        await talkcraftRemotionStatus();
        return;
      }
      setMessage(r.message || "渲染任务已启动。", "success");
      await talkcraftRemotionStatus();
    } catch (e) {
      setMessage(String(e), "error");
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      if (TC.data && TC.data.status === "READY" &&
          !["QUEUED","PREPARING","RENDERING"].includes((TC.renderStatus || {}).status)) {
        buttons.forEach(b => { b.disabled = false; });
      }
    }
  }

  window.talkcraftLoad = talkcraftLoad;
  window.talkcraftPrepare = talkcraftPrepare;
  window.talkcraftRemotionRender = talkcraftRemotionRender;
  window.talkcraftRemotionStatus = talkcraftRemotionStatus;
  window.talkcraftState = () => TC;
})();
