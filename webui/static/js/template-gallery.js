/* Template Gallery: browse registered templates and validate a real render before use. */
window.TG = {templates: [], visible: [], selectedKey: "", busy: false};

function tgEscape(v) {
  if (typeof esc === "function") return esc(String(v == null ? "" : v));
  return String(v == null ? "" : v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function tgKey(t) { return String(t.id || "") + "@" + String(t.version || ""); }
function tgSelected() { return TG.templates.find(t => tgKey(t) === TG.selectedKey) || null; }
function tgStatus(runtime) {
  const s = runtime && runtime.status;
  return s === "READY" ? "依赖满足 · 样片未验证" : s === "PARTIAL" ? "可试用 · 有未实现项" : s === "BLOCKED" ? "暂不可执行" : "状态未知";
}
function tgLicense(s) {
  return ({CLEAR:"授权状态：已核验", REVIEW:"授权状态：需复核", UNKNOWN:"授权状态：未知", BLOCKED:"授权状态：禁止使用"})[s] || ("授权状态：" + (s || "未知"));
}

function templateGalleryOpen() {
  templateGalleryClose();
  const mask = document.createElement("div");
  mask.id = "tg-mask";
  mask.className = "tg-mask";
  mask.innerHTML = '<section class="tg-panel" role="dialog" aria-modal="true" aria-labelledby="tg-title">' +
    '<header class="tg-header"><div><h2 id="tg-title">模板与真实样片</h2><p>查看执行条件，用自己的素材试渲染。只有真实渲染并完成 MP4 解码后才记作样片通过。</p></div>' +
    '<div class="tg-header-actions"><input id="tg-package-input" type="file" accept=".zip,application/zip" hidden onchange="templateGalleryImportPackage()">' +
    '<button class="btn-secondary btn-sm" type="button" onclick="document.getElementById(\'tg-package-input\').click()">导入 HTML 视频包 ZIP</button>' +
    '<button class="btn-secondary btn-sm" type="button" onclick="templateGalleryClose()">关闭</button></div></header>' +
    '<div class="tg-filters"><label>类别 <select id="tg-category"><option value="">全部类别</option></select></label>' +
    '<label>画幅 <select id="tg-ratio-filter"><option value="">全部画幅</option><option>9:16</option><option>16:9</option><option>1:1</option><option>3:4</option><option>4:5</option></select></label>' +
    '<span id="tg-count" class="hint" role="status">正在读取模板……</span></div>' +
    '<div class="tg-layout"><div id="tg-list" class="tg-list" aria-label="模板清单"></div><div id="tg-detail" class="tg-detail"><p class="hint">选择模板后查看门禁和素材槽位。</p></div></div>' +
    '<footer class="tg-footer"><span id="tg-status" class="hint" role="status" aria-live="polite"></span><span>预检通过不等于样片通过；不会把元数据冒充成可运行风格包。</span></footer></section>';
  mask.addEventListener("click", e => { if (e.target === mask) templateGalleryClose(); });
  document.body.appendChild(mask);
  document.getElementById("tg-category").addEventListener("change", templateGalleryFilter);
  document.getElementById("tg-ratio-filter").addEventListener("change", templateGalleryFilter);
  return templateGalleryLoad();
}
function templateGalleryClose() {
  const mask = document.getElementById("tg-mask");
  if (mask) mask.remove();
  TG.busy = false;
}
async function templateGalleryLoad() {
  const status = document.getElementById("tg-status");
  if (status) status.textContent = "读取模板与运行前置条件……";
  try {
    const response = await apiGet("/api/registry/templates");
    if (!Array.isArray(response)) throw new Error((response && response.error) || "服务端没有返回模板清单");
    TG.templates = response;
    const cats = [...new Set(response.map(t => t.category).filter(Boolean))].sort();
    const category = document.getElementById("tg-category");
    if (category) category.innerHTML = '<option value="">全部类别</option>' + cats.map(c => '<option value="' + tgEscape(c) + '">' + tgEscape(c) + '</option>').join("");
    templateGalleryFilter();
    if (status) status.textContent = response.length ? "清单已读取。选择模板可查看阻塞项、填写素材并试渲染。" : "当前没有已注册模板。";
    return true;
  } catch (e) {
    const list = document.getElementById("tg-list");
    if (list) list.innerHTML = '<div class="tg-empty">模板读取失败：' + tgEscape(e.message || e) + ' <button class="btn-secondary btn-sm" onclick="templateGalleryLoad()">重试</button></div>';
    if (status) status.textContent = "读取失败；没有使用本地假数据替代。";
    return false;
  }
}
async function templateGalleryImportPackage() {
  const fileInput = document.getElementById("tg-package-input");
  const file = fileInput && fileInput.files && fileInput.files[0];
  const status = document.getElementById("tg-status");
  if (!file) return;
  if (!/\.zip$/i.test(file.name) || file.size <= 0 || file.size > 50 * 1024 * 1024) {
    if (status) status.textContent = "模板包必须是非空 ZIP，且不得超过 50 MiB。";
    if (fileInput) fileInput.value = "";
    return;
  }
  const fd = new FormData();
  fd.append("package_file", file, file.name);
  TG.busy = true;
  if (status) status.textContent = "正在校验 ZIP 路径、manifest、来源授权声明和资源依赖……";
  try {
    const result = await apiUpload("/api/html_templates/import", fd, {timeoutMs: 120000});
    if (!result || !result.ok) {
      throw new Error((result && result.error) || "模板包导入失败");
    }
    TG.busy = false;
    if (fileInput) fileInput.value = "";
    const loaded = await templateGalleryLoad();
    if (!loaded) throw new Error("导入成功但模板清单刷新失败；重试读取模板清单即可");
    TG.selectedKey = String(result.template_id) + "@" + String(result.template_version);
    templateGalleryFilter();
    if (status) status.textContent = result.message || "模板包已导入。";
  } catch (e) {
    TG.busy = false;
    if (status) status.textContent = "导入失败：" + String(e.message || e);
    if (fileInput) fileInput.value = "";
    templateGalleryUpdateButtons();
  }
}
function templateGalleryFilter() {
  const cat = (document.getElementById("tg-category") || {}).value || "";
  const ratio = (document.getElementById("tg-ratio-filter") || {}).value || "";
  TG.visible = TG.templates.filter(t => (!cat || t.category === cat) && (!ratio || (t.supported_ratios || []).includes(ratio)));
  if (!TG.visible.some(t => tgKey(t) === TG.selectedKey)) TG.selectedKey = TG.visible.length ? tgKey(TG.visible[0]) : "";
  const count = document.getElementById("tg-count");
  if (count) count.textContent = "显示 " + TG.visible.length + " / " + TG.templates.length + " 个模板";
  templateGalleryRenderList();
  templateGalleryRenderDetail();
}
function templateGallerySelectByIndex(index) {
  const t = TG.visible[Number(index)];
  if (!t) return;
  TG.selectedKey = tgKey(t);
  templateGalleryRenderList();
  templateGalleryRenderDetail();
}
function templateGalleryRenderList() {
  const list = document.getElementById("tg-list");
  if (!list) return;
  if (!TG.visible.length) { list.innerHTML = '<div class="tg-empty">当前筛选条件下没有模板。</div>'; return; }
  list.innerHTML = TG.visible.map((t, i) => {
    const r = t.runtime || {}, n = (r.blockers || []).length, chosen = tgKey(t) === TG.selectedKey;
    const stateClass = r.status === "BLOCKED" ? "tg-state-blocked" : r.status === "PARTIAL" ? "tg-state-partial" : "tg-state-ready";
    return '<button class="tg-template-card' + (chosen ? ' is-selected' : '') + '" type="button" aria-pressed="' + chosen + '" onclick="templateGallerySelectByIndex(' + i + ')">' +
      '<span class="tg-card-top"><strong>' + tgEscape(t.name || t.id) + '</strong><span class="tg-status-chip ' + stateClass + '">' + tgEscape(r.status || "UNKNOWN") + '</span></span>' +
      '<span class="tg-card-meta">' + tgEscape(t.category || "未分类") + ' · ' + tgEscape((t.supported_ratios || []).join(" / ") || "未声明画幅") + '</span>' +
      '<span class="tg-card-meta">' + tgEscape(t.scene_count || 0) + ' 个场景 · ' + tgEscape(tgStatus(r)) + '</span>' +
      (n ? '<span class="tg-blocker-count">' + n + ' 项阻塞条件</span>' : '') + '</button>';
  }).join("");
}
function tgFileSlot(slot) { return ["image","video","product","person"].includes(slot.type); }
function tgSlotHtml(slot) {
  const id = tgEscape(slot.id), label = tgEscape(slot.label || slot.id);
  const required = slot.required ? '<span class="tg-required">必填</span>' : '<span class="hint">可选</span>';
  if (tgFileSlot(slot)) {
    const accept = slot.type === "image" ? ".jpg,.jpeg,.png,.webp,.bmp" : slot.type === "video" ? ".mp4,.mov,.mkv,.webm,.m4v" : ".mp4,.mov,.mkv,.webm,.m4v,.jpg,.jpeg,.png,.webp,.bmp";
    return '<label class="tg-slot">' + label + ' ' + required + '<input type="file" data-tg-file-slot="' + id + '" accept="' + accept + '"><small>选择图片或视频素材；原始素材不会被模板修改。</small></label>';
  }
  if (slot.type === "text") return '<label class="tg-slot">' + label + ' ' + required + '<input type="text" data-tg-text-slot="' + id + '" placeholder="填写' + label + '"></label>';
  return '<div class="tg-slot tg-slot-unavailable"><strong>' + label + '</strong> ' + required + '<small>此槽位类型没有可验证的输入通道，不能在此假装可用。</small></div>';
}
function templateGalleryRenderDetail() {
  const detail = document.getElementById("tg-detail");
  if (!detail) return;
  const t = tgSelected();
  if (!t) { detail.innerHTML = '<div class="tg-empty">没有符合条件的模板，请调整筛选条件。</div>'; return; }
  const r = t.runtime || {}, blockers = r.blockers || [], warnings = r.warnings || [], ratios = t.supported_ratios || [];
  const first = ratios.includes("9:16") ? "9:16" : (ratios[0] || "");
  const isHtmlPackage = r.adapter === "html-video-python-playwright" && !!r.package_id;
  const inputSection = isHtmlPackage
    ? '<section class="tg-subsection"><h4>HTML 视频包运行说明</h4>' +
      '<p>此包会在隔离的 Chromium 中真实录制，再由 FFmpeg 编码并完整解码 MP4。运行时阻断所有非本机模板服务的网络请求。</p>' +
      '<p>当前它是可验证的独立 HTML 样片，不是可编辑的 CompositionIR。样片运行成功不代表其动画、音频和 HTML 结构已经转换为时间线图层；“创建可编辑项目”暂不可用。</p>' +
      '<p>资源指纹：' + tgEscape(r.package_sha256 || "缺失") + '</p></section>'
    : '<section class="tg-subsection"><h4>使用自己的素材</h4><p class="hint">支持画幅：' + tgEscape(ratios.join("、") || "未声明") + ' · ' + tgEscape(t.scene_count || 0) + ' 个场景</p>' +
      '<label class="tg-slot">输出画幅<select id="tg-ratio-select">' + ratios.map(x => '<option ' + (x === first ? 'selected' : '') + '>' + tgEscape(x) + '</option>').join("") + '</select></label>' +
      ((t.slots || []).length ? t.slots.map(tgSlotHtml).join("") : '<p class="hint">模板未声明可填写槽位。</p>') +
      '<p class="tg-input-note">试渲染和实例化接口要求先满足模板所声明的素材槽位。</p></section>';
  detail.innerHTML = '<h3>' + tgEscape(t.name || t.id) + '</h3><p>' + tgEscape(t.description || "") + '</p>' +
    '<div class="tg-meta-line">' + tgEscape(t.id) + '@' + tgEscape(t.version) + ' · ' + tgEscape(t.category || "未分类") + '</div>' +
    '<div class="tg-summary"><span class="tg-status-chip ' + (r.status === "BLOCKED" ? "tg-state-blocked" : r.status === "PARTIAL" ? "tg-state-partial" : "tg-state-ready") + '">' + tgEscape(tgStatus(r)) + '</span>' +
    '<span class="tg-status-chip">' + tgEscape(tgLicense(t.commercial_status)) + '</span><span class="tg-status-chip">引擎：' + tgEscape(r.engine || "未声明") + '</span></div>' +
    '<section class="tg-subsection"><h4>执行前检查</h4>' +
    (blockers.length ? '<ul class="tg-issues tg-issues-block">' + blockers.map(x => '<li>' + tgEscape(x.message || x.code || "阻塞条件") + '</li>').join("") + '</ul>' : '<p class="tg-pass">依赖门禁通过；仍须通过真实样片渲染和解码。</p>') +
    (warnings.length ? '<ul class="tg-issues tg-issues-warn">' + warnings.map(x => '<li>' + tgEscape(x.message || x.code || "未实现项") + '</li>').join("") + '</ul>' : '') + '</section>' +
    inputSection +
    '<div class="tg-actions"><button id="tg-render" class="btn-secondary btn-sm" type="button" onclick="templateGalleryRenderSample()">真实试渲染并验证 MP4</button>' +
    '<button id="tg-instantiate" class="btn-sm" type="button" onclick="templateGalleryInstantiate()">使用模板创建项目</button></div>' +
    '<div id="tg-detail-status" class="hint" role="status" aria-live="polite"></div><div id="tg-sample-result" class="tg-sample-result"></div>';
  detail.querySelectorAll("[data-tg-file-slot], [data-tg-text-slot]").forEach(el => {
    el.addEventListener("input", templateGalleryUpdateButtons);
    el.addEventListener("change", templateGalleryUpdateButtons);
  });
  const ratio = document.getElementById("tg-ratio-select");
  if (ratio) ratio.addEventListener("change", templateGalleryUpdateButtons);
  templateGalleryUpdateButtons();
}
function tgCollectInputs() {
  const t = tgSelected();
  if (!t) return {fd: null, errors: ["请先选择模板"]};
  const fd = new FormData(), values = {}, errors = [];
  const runtime = t.runtime || {};
  if (runtime.adapter === "html-video-python-playwright" && runtime.package_id) {
    fd.append("template_id", t.id);
    fd.append("template_version", t.version);
    fd.append("slot_values_json", "{}");
    return {fd, errors: []};
  }
  fd.append("template_id", t.id);
  fd.append("template_version", t.version);
  fd.append("ratio", (document.getElementById("tg-ratio-select") || {}).value || "");
  let fileCount = 0;
  (t.slots || []).forEach(slot => {
    if (tgFileSlot(slot)) {
      const input = Array.from(document.querySelectorAll("[data-tg-file-slot]")).find(x => x.dataset.tgFileSlot === slot.id);
      const file = input && input.files && input.files[0];
      if (file) { fd.append(slot.id + "_file", file, file.name); fileCount += 1; }
      else if (slot.required) errors.push("请上传必填素材：" + (slot.label || slot.id));
    } else if (slot.type === "text") {
      const input = Array.from(document.querySelectorAll("[data-tg-text-slot]")).find(x => x.dataset.tgTextSlot === slot.id);
      const value = input ? input.value.trim() : "";
      values[slot.id] = value;
      if (slot.required && !value) errors.push("请填写必填内容：" + (slot.label || slot.id));
    } else if (slot.required) errors.push("必填槽位「" + (slot.label || slot.id) + "」类型暂不支持");
  });
  if (!fileCount) errors.push("至少上传一个图片或视频素材；当前接口不支持纯文字创建模板项目。");
  fd.append("slot_values_json", JSON.stringify(values));
  return {fd, errors};
}
function templateGalleryUpdateButtons() {
  const t = tgSelected();
  if (!t) return;
  const r = t.runtime || {}, items = tgCollectInputs(), ok = !TG.busy && items.errors.length === 0;
  const render = document.getElementById("tg-render"), use = document.getElementById("tg-instantiate");
  if (render) render.disabled = !(ok && r.can_render);
  if (use) use.disabled = !(ok && r.can_instantiate);
  if (TG.busy) {
    if (render) render.textContent = "处理中……";
    if (use) use.textContent = "处理中……";
  } else {
    if (render) render.textContent = "真实试渲染并验证 MP4";
    if (use) use.textContent = (r.adapter === "html-video-python-playwright" && r.package_id)
      ? "暂不可转为可编辑项目" : "使用模板创建项目";
  }
}
function tgBusy(busy, message) {
  TG.busy = busy;
  const status = document.getElementById("tg-detail-status");
  if (status && message) status.textContent = message;
  templateGalleryUpdateButtons();
}
function tgInputErrors(errors) {
  const status = document.getElementById("tg-detail-status");
  if (status) status.textContent = errors.join("；");
}
async function templateGalleryRenderSample() {
  const t = tgSelected();
  if (!t || TG.busy) return;
  const input = tgCollectInputs();
  if (input.errors.length) return tgInputErrors(input.errors);
  tgBusy(true, "正在执行真实渲染与 MP4 全文件解码验证；此过程可能需要几分钟。");
  const box = document.getElementById("tg-sample-result");
  if (box) box.innerHTML = "";
  try {
    const response = await apiUpload("/api/templates/sample_render", input.fd, {timeoutMs: 330000});
    if (!response || !response.ok || !response.download_url) {
      const more = [...(response && response.runtime && response.runtime.blockers || []), ...(response && response.missing_inputs || [])].map(x => x.message || x.code).filter(Boolean).join("；");
      throw new Error([response && response.error, more].filter(Boolean).join("：") || "样片未通过验证");
    }
    if (box) box.innerHTML = '<h4>真实样片已生成</h4><p class="tg-pass">' + tgEscape(response.message || "服务端返回真实样片。") + '</p>' +
      '<video controls preload="metadata" src="' + tgEscape(response.download_url) + '"></video><p><a href="' + tgEscape(response.download_url) + '" download>下载样片 MP4</a></p>' +
      '<p class="hint">解码记录：' + tgEscape((response.runtime || {}).sample_mp4 ? JSON.stringify(response.runtime.sample_mp4) : "服务端已完成 MP4 解码检查") + '</p>';
    tgBusy(false, "真实渲染和 MP4 解码通过；未实现的功能仍按警告列出。");
  } catch (e) {
    tgBusy(false, "样片未通过验证：" + String(e.message || e));
  }
}
async function templateGalleryInstantiate() {
  if (TG.busy) return;
  const input = tgCollectInputs();
  if (input.errors.length) return tgInputErrors(input.errors);
  tgBusy(true, "正在创建可编辑的模板项目……");
  let pid = "";
  try {
    const response = await apiUpload("/api/templates/instantiate", input.fd, {timeoutMs: 120000});
    if (!response || !response.ok || !response.project_id) {
      const more = (response && response.missing_inputs || []).map(x => x.message || x.code).filter(Boolean).join("；");
      throw new Error([response && response.error, more].filter(Boolean).join("；") || "模板项目创建失败");
    }
    pid = response.project_id;
  } catch (e) {
    tgBusy(false, "创建失败：" + String(e.message || e));
    return;
  }
  templateGalleryClose();
  if (typeof closeCreate === "function") closeCreate();
  try {
    const opened = typeof workspaceOpen === "function" ? await workspaceOpen(pid) : {ok: false};
    if (!opened || !opened.ok) editorToast("项目已创建：" + pid + "；自动打开未完成，请在项目页打开。", true);
    else editorToast("模板已实例化为独立 Composition，可在统一时间线继续编辑。");
  } catch (e) {
    editorToast("项目已创建：" + pid + "；打开工作台失败：" + String(e.message || e), true);
  }
}
Object.assign(window, {
  templateGalleryOpen, templateGalleryClose, templateGalleryLoad, templateGalleryFilter,
  templateGallerySelectByIndex, templateGalleryRenderSample, templateGalleryInstantiate,
  templateGalleryUpdateButtons, TG
});
