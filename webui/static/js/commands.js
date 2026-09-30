// §20/FE-002 commands.js：所有前端正式写操作的统一入口。
// executeCommand 自动附加 projectId / expectedVersion / commandId；
// 统一处理 APPLIED / CONFLICT / VALIDATION_ERROR / CAPABILITY_UNAVAILABLE / DEPENDENCY_ERROR。

let _cmdSeq = 0;

/** 版本冲突 → resync composition（读回最新，刷新时间线）。§20 禁止静默覆盖。 */
async function resyncComposition(){
  const pid = ED.pid;
  if (!pid) return {ok: false, error: '无项目'};
  const fresh = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(pid));
  if (fresh.error) return {ok: false, error: fresh.error};
  ED.version = fresh.version ?? ED.version;
  ED.comp = fresh;
  if (typeof editorRenderTimeline === 'function') editorRenderTimeline();
  return {ok: true, version: ED.version};
}

/** CONFLICT 处理：resync + 明确提示（不静默重试）。 */
function handleConflict(){
  resyncComposition();
  return {ok: false, status: 'CONFLICT',
          error: '内容已被其他操作更新，已为你刷新到最新版本，请重试'};
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
  const body = {
    project_id: pid,
    command: spec.command,
    targetId: spec.targetId || '',
    payload: spec.payload || {},
    expectedVersion: (spec.expectedVersion !== undefined) ? spec.expectedVersion : ED.version,
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
  if (data.ok) {
    // 同步版本与 Composition（Server State 单向流入前端）
    if (typeof data.composition_version === 'number') ED.version = data.composition_version;
    if (data.composition) ED.comp = data.composition;
    return {ok: true, status: 'APPLIED', data};
  }
  const code = (data.errors && data.errors[0] && data.errors[0].code) || data.status || 'ERROR';
  if (code === 'CONFLICT') {
    return handleConflict();
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
  friendlyError,
});
