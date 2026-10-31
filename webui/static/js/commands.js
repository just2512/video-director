/**
 * 编辑器兼容命令入口：统一把旧时间线/键盘调用收口到 executeCommand。
 * targetId 未显式提供时，从当前 Selection 取得；实际修改仍只经过 backend Command + Revision。
 */
async function editorCmd(command, payload = {}, targetId = null, refresh = true) {
  const projectId = (typeof ED !== 'undefined' && ED) ? ED.pid : '';
  const target = targetId || ((typeof SEL !== 'undefined' && SEL.selection) ? (SEL.selection.id || '') : '');
  const result = await executeCommand({command, targetId: target, payload});
  // A late response for another project must not refresh the active workspace.
  if (result && result.ok && refresh && ED && ED.pid === projectId &&
      result.project_id === projectId && !result.staleProjectResponse) {
    if (typeof refreshWorkspace === 'function') await refreshWorkspace();
    else {
      if (typeof editorRenderTimeline === 'function') editorRenderTimeline();
      if (typeof edShowInspector === 'function') edShowInspector();
    }
  }
  return result;
}

// §20/FE-002 commands.js：所有前端正式写操作的统一入口。
// executeCommand 自动附加 projectId / expectedVersion / commandId；
// 统一处理 APPLIED / CONFLICT / VALIDATION_ERROR / CAPABILITY_UNAVAILABLE / DEPENDENCY_ERROR。
//
// §82 Action 通路统一治理（2026-10-22）——两条前端写入口的契约：
//   executeCommand = 命令级 Action 通道（ActionSpec.command 的直发形态，不带参数适配）；
//   executeAction = 语义级 Action 通道（带 resolve_step 参数适配与动作审计）。
// 两者同源：都落到同一个后端 command + revision，不是双轨、不是旁路。
// 新代码优先 executeAction（获得参数适配与动作审计语义）；存量 executeCommand 调用点
// 不做逐处改造，改由通道统计观测（window.__cmdChannelStats / GET /api/action_channel_stats）。

let _cmdSeq = 0;

// §82 命令级 Action 通道计数：回答「哪些 command 通道在被谁用」（可观测，不影响写路径）。
// 窗口级快照；服务端同源计数见 GET /api/action_channel_stats。
window.__cmdChannelStats = window.__cmdChannelStats || {count: 0, byCommand: {}};

/** 通道计数（executeCommand 入口命中即 +1；byCommand 按 command 名分桶）。 */
function _bumpChannelStat(command) {
  const st = window.__cmdChannelStats;
  st.count++;
  const c = command || 'unknown';
  st.byCommand[c] = (st.byCommand[c] || 0) + 1;
}

/**
 * Check whether an async editor response still belongs to the active project/view.
 * The selector changes before editorLoad completes, so both identities matter.
 */
function editorContextMatches(projectId, expectedVersion, checkVersion = true) {
  if (!projectId || typeof ED === 'undefined' || !ED || ED.pid !== projectId) return false;
  const selector = document.getElementById('editor-project');
  if (selector && selector.value && selector.value !== projectId) return false;
  if (checkVersion && arguments.length >= 2 && !Object.is(ED.version, expectedVersion)) return false;
  return true;
}

/** Conflict recovery is pinned to the originating project and view revision. */
async function resyncComposition(expectedProjectId, expectedContextVersion){
  const pid = expectedProjectId || ED.pid;
  const contextVersion = arguments.length >= 2 ? expectedContextVersion : ED.version;
  if (!pid) return {ok: false, error: '无项目'};
  if (!editorContextMatches(pid, contextVersion)) {
    return {ok:false,status:'PROJECT_CHANGED',project_id:pid};
  }
  const fresh = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(pid));
  if (!editorContextMatches(pid, contextVersion)) {
    return {ok:false,status:'PROJECT_CHANGED',project_id:pid};
  }
  if (!fresh || fresh.error || !fresh.scenes) {
    return {ok:false,status:'LOAD_FAILED',project_id:pid,error:(fresh && fresh.error)||'读取最新项目失败'};
  }
  ED.version = fresh.version ?? ED.version;
  ED.comp = fresh;
  if (typeof editorRenderTimeline === 'function') editorRenderTimeline();
  return {ok:true,project_id:pid,version:ED.version};
}

/** A conflict may refresh only the still-active originating project. */
function handleConflict(expectedProjectId, expectedContextVersion){
  const pid = expectedProjectId || ED.pid;
  const version = arguments.length >= 2 ? expectedContextVersion : ED.version;
  if (editorContextMatches(pid, version)) void resyncComposition(pid, version);
  return {ok:false,status:'CONFLICT',project_id:pid,
    error:'内容已被其他操作更新；请确认当前项目后重新载入并重试'};
}

/**
 * executeBatch：批量命令（同 project/version，原子提交，单 revision）。
 * spec = {commands: [{command, targetId, payload}]}。用 editor 的 batch 命令落单 revision。
 */
async function executeBatch(spec) {
  const commands = (spec && spec.commands) || [];
  if (!commands.length) return {ok: false, status: 'INVALID_PAYLOAD', error: '批量命令为空'};
  return executeCommand({command: 'batch', targetId: '', payload: {operations: commands}});
}

/**
 * executeCommand({command, targetId, payload}) → {ok, status, data}
 * CONFLICT：不静默覆盖——调用方须 resync 并提示用户刷新。
 */
async function executeCommand(spec) {
  const pid = ED.pid;
  if (!pid) throw new Error('先打开项目');
  const contextVersion = ED.version;
  if (!editorContextMatches(pid, contextVersion)) {
    return {ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true,applied:false,
      error:'项目选择已变化；当前命令未发送，请重新打开所选项目。'};
  }
  // §82 命令级 Action 通道计数（Failure 前不落：projectId 缺失直接抛错，不计入通道）
  _bumpChannelStat(spec && spec.command);
  const body = {
    project_id: pid,
    command: spec.command,
    targetId: spec.targetId || '',
    payload: spec.payload || {},
    expectedVersion: (spec.expectedVersion !== undefined) ? spec.expectedVersion : contextVersion,
    author: spec.author || 'user',
    commandId: 'cmd-' + Date.now().toString(36) + '-' + (++_cmdSeq),
  };
  let data;
  try {
    const resp = await fetch('/api/editor/command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    data = await resp.json().catch(() => ({}));
  } catch (e) {
    return {ok: false, status: 'NETWORK_ERROR', error: '网络异常：' + e.message};
  }
  // The HTTP request is pinned to pid, while ED is a single active-project view.
  // A response must not replace another project or a newer view of the same project.
  if (!editorContextMatches(pid, contextVersion)) {
    const applied = !!(data && data.ok === true);
    return {
      ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true,applied,
      requestStatus:applied?'APPLIED':((data && data.status) || 'REJECTED'),
      error:applied
        ? '命令已在原项目提交；当前工作区已切换，未更新当前项目状态。'
        : '原项目请求已返回；当前工作区已切换，未触碰当前项目状态。',
      data,
    };
  }
  if (data.ok) {
    // 同步版本与 Composition（Server State 单向流入前端）
    if (typeof data.composition_version === 'number') ED.version = data.composition_version;
    if (data.composition) {
      // editor command 响应的 composition 是裸 IR dump——不含 server 组装
      // /api/editor/composition 时附加的展示字段（assets 缩略图映射 /
      // transition_proposals）。裸赋值会让 PR#9 的缩略图/匹配面板/转场
      // 待确认区在第一次编辑后全部失效（ED.comp.assets 变 undefined，
      // 2026-10-09 Playwright H 断言实锚）。保留展示字段，只换 IR 本体。
      const keepExtras = {
        assets: ED.comp && ED.comp.assets,
        transition_proposals: ED.comp && ED.comp.transition_proposals,
      };
      ED.comp = Object.assign({}, data.composition);
      if (keepExtras.assets && !ED.comp.assets) ED.comp.assets = keepExtras.assets;
      if (keepExtras.transition_proposals && !ED.comp.transition_proposals)
        ED.comp.transition_proposals = keepExtras.transition_proposals;
    }
    return {ok:true,status:'APPLIED',project_id:pid,data};
  }
  const code = (data.errors && data.errors[0] && data.errors[0].code) || data.status || 'ERROR';
  if (code === 'CONFLICT') {
    return handleConflict(pid, contextVersion);
  }
  if (code === 'CAPABILITY_UNAVAILABLE') {
    return {ok: false, status: 'CAPABILITY_UNAVAILABLE',
            error: friendlyError(data), data};
  }
  return {ok: false, status: code, error: friendlyError(data), data};
}

/** §28：错误必须告诉用户 发生了什么/影响什么/数据没变/下一步做什么。 */
function friendlyError(data) {
  const raw = (data.errors && data.errors[0] && data.errors[0].message)
           || data.error || '操作未生效';
  const cn = {
    'IMAGE_PROVIDER_UNAVAILABLE': '图片生成暂不可用：未配置图片生成服务（可在设置中配置）。项目内容没有变化；可改用程序化视觉或本地素材。',
    'VIDEO_PROVIDER_UNAVAILABLE': '视频生成暂不可用：未配置视频生成服务。项目内容没有变化；可改用本地视频素材。',
  };
  return cn[data.errors?.[0]?.code] || raw;
}

Object.assign(window, {
  executeCommand,
  executeBatch,
  resyncComposition,
  handleConflict,
  editorContextMatches,
  friendlyError,
  cmdChannelStats: () => window.__cmdChannelStats,
});
