// §8 state.js：前端只有 Server State + Transient UI State（FE-001 Contract）。
// 事实源是服务端 Composition；这里只维护瞬态（选择/缩放/面板/预览缓存）。
// Reload 后 Composition 保持（服务端持久化）；Selection/Hover 允许丢失。
//
// 实现方式：全部挂 window 属性（非 let+快照）——因为多处模块会整体重赋值
// （MOTIONS = ...、TPLS = ...、currentProject = {...}），let 快照会与 window 脱钩。

// BGMLIST（创作页 BGM 下拉）
window.BGMLIST = [];
// MODEL_ROLES（设置页模型角色）
window.MODEL_ROLES = [];
// ED：工作台编辑态（project/selection/version/zoom/snap）
window.ED = {pid:null, comp:null, sel:null, selSet:new Set(), version:1, zoom:1.0, snap:true, timelineViewMode:'physical'};
// AP：素材选择器瞬态
window.AP = {lastTab: 'project', recent: [], favorites: []};
// GFX：程序化图形创作计划（proposal——非正式事实源 §7；正式持久化一律走 Command）
window.GFX = {plan:null, blocks:[], previewHtml:null, content_type:'knowledge'};
// MOTIONS/TPLS：库缓存（只读 server state 的瞬态副本）
window.TPLS = [];
window.MOTIONS = [];
window.MOTION_BEHAVIOR = {};  // §三十二 Style DNA：{id: {pinned: n, used: n, ...}}
// 当前待确认项目（自然语言改稿累积 edits）
window.currentProject = { pid: '', sb: null, edits: {} };
// 音频预览元素
window._previewEl = null;
// §10 DeveloperState：默认关闭；开启后显示 capability/command/原始错误等内部信息
window.DEV = {mode: false};
// §12 Capability availability：从 /api/capabilities 拉取（generation 禁用态依据）
window.CAPS = {imageAvailable: null, imageReason: '', videoAvailable: null, videoReason: ''};
