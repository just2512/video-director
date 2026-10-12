/* Style gallery: choose a real built-in StyleContract through the existing Director pipeline. */
window.SG = {styles: [], selectedId: "", loading: false};

function sgEsc(value) {
  if (typeof esc === "function") return esc(String(value == null ? "" : value));
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function styleGalleryOpen() {
  styleGalleryClose();
  const mask = document.createElement("div");
  mask.id = "sg-mask";
  mask.className = "sg-mask";
  mask.innerHTML = '<section class="sg-panel" role="dialog" aria-modal="true" aria-labelledby="sg-title">' +
    '<header class="sg-header"><div><h2 id="sg-title">创作风格</h2><p>这里展示实际内置 StyleContract。选择后填入新建项目的风格意图，由既有 AI 导演链路应用。</p></div>' +
    '<button class="btn-secondary btn-sm" type="button" onclick="styleGalleryClose()">关闭</button></header>' +
    '<div class="sg-notice">边界说明：Registry 风格记录的独立运行状态仍是 METADATA_ONLY。下列内置风格参数可以进入当前创作链路，但这不代表任意外部风格包已经可执行、拥有独立样片或完整移植了参考项目的模板运行时。</div>' +
    '<div id="sg-status" class="hint" role="status" aria-live="polite">正在读取创作风格……</div>' +
    '<div id="sg-sample-result"></div><div id="sg-list" class="sg-list"></div></section>';
  mask.addEventListener("click", e => { if (e.target === mask) styleGalleryClose(); });
  document.body.appendChild(mask);
  styleGalleryLoad();
}
function styleGalleryClose() {
  const mask = document.getElementById("sg-mask");
  if (mask) mask.remove();
}
async function styleGalleryLoad() {
  const status = document.getElementById("sg-status");
  try {
    const data = await apiGet("/api/registry/styles");
    if (!Array.isArray(data)) throw new Error((data && data.error) || "风格接口未返回清单");
    SG.styles = data;
    const list = document.getElementById("sg-list");
    if (!list) return;
    const editorPane = document.getElementById("pane-editor");
    const canSampleCurrent = !!(window.ED && ED.pid && ED.comp && editorPane && editorPane.style.display !== "none");
    if (!data.length) {
      list.innerHTML = '<div class="sg-empty">当前没有风格注册项。</div>';
    } else {
      list.innerHTML = data.map(style => {
        const contract = style.builtin_contract;
        const runtime = style.runtime || {};
        const usable = !!(contract && contract.application_mode === "create_style_intent");
        const params = contract ? [
          ["字幕位置", contract.subtitle_position],
          ["字号（1080 基准）", contract.subtitle_font_size],
          ["字幕行数", contract.subtitle_max_lines],
          ["图片运动倍率", contract.image_motion_intensity],
          ["转场预算", contract.transition_budget],
          ["BGM 音量", contract.bgm_volume],
          ["音效密度", contract.sfx_density]
        ] : [];
        const values = params.map(p => '<div class="sg-param"><span>' + sgEsc(p[0]) + '</span><b>' + sgEsc(p[1]) + '</b></div>').join("");
        const sampleAction = (canSampleCurrent && usable)
          ? '<button class="btn-secondary btn-sm" type="button" data-style-sample="' + sgEsc(style.id) + '">渲染当前时间线样片</button>'
          : '';
        return '<article class="sg-card"><div class="sg-card-head"><div><h3>' + sgEsc(style.name || style.id) + '</h3><p>' + sgEsc(style.description || "") + '</p></div>' +
          '<span class="sg-chip">' + sgEsc(runtime.status || "UNKNOWN") + '</span></div>' +
          '<div class="sg-meta">' + sgEsc(style.id) + '@' + sgEsc(style.version) + ' · 授权：' + sgEsc(style.commercial_status || "UNKNOWN") +
          ' · 来源层：' + sgEsc(style.source_layer || "unknown") + '</div>' +
          (contract ? '<div class="sg-params">' + values + '</div><p class="sg-contract-note">' + sgEsc(contract.message || "") + '</p>' :
            '<p class="sg-blocked">没有与当前内置执行链匹配的 StyleContract；此记录仅供参考，不能在这里应用。</p>') +
          '<div class="sg-actions"><button class="btn-sm" type="button" ' + (usable ? 'data-style-id="' + sgEsc(style.id) + '" data-style-apply="1"' : 'disabled') + '>采用此内置创作风格</button>' + sampleAction + '</div></article>';
      }).join("");
    }
    if (status) status.textContent = "已读取 " + data.length + " 个注册项。请区分内置创作契约与独立风格包运行状态。";
    list.querySelectorAll("[data-style-sample]").forEach(button => button.addEventListener("click", () => {
      styleGalleryRenderSample(button.dataset.styleSample, button);
    }));
    list.querySelectorAll("[data-style-apply]").forEach(button => button.addEventListener("click", () => {
      const style = SG.styles.find(x => x.id === button.dataset.styleId);
      if (!style || !style.builtin_contract) return;
      const intentById = {
        "style.documentary-calm": "纪录片、沉稳、专业",
        "style.knowledge-explainer": "知识讲解、科普、教学",
        "style.energetic-social": "快节奏、冲击、营销",
        "style.cinematic-story": "电影叙事、悬疑、史诗"
      };
      const intent = intentById[style.id];
      if (!intent) {
        if (status) status.textContent = "此 StyleContract 暂无映射到现有导演的创作意图；未写入表单。";
        return;
      }
      const input = document.getElementById("cf-style");
      if (!input) {
        if (status) status.textContent = "没有打开新建项目面板；风格未应用。";
        return;
      }
      input.value = intent;
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
      styleGalleryClose();
      if (typeof editorToast === "function") editorToast("已填入内置创作风格意图。实际执行仍由现有 AI 导演创建链路完成。");
    }));
  } catch (error) {
    if (status) status.textContent = "读取失败：" + String(error.message || error) + "；未用假数据替代。";
  }
}
Object.assign(window, {styleGalleryOpen, styleGalleryClose, styleGalleryLoad, SG});
