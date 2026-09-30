// §8/§21 api.js：统一所有 HTTP 调用（唯一 fetch 入口）。
// 契约（调用方依赖，与旧 apiPost 一致）：网络/解析异常不抛出，返回 {ok:false, error}。
// 统一 response 解析 + 统一错误态，业务模块不再直接 fetch()。

async function apiGet(url) {
  try {
    const r = await fetch(url);
    const data = await r.json().catch(() => ({}));
    if (!r.ok && data && typeof data === 'object' && !('ok' in data)) data.ok = false;
    return data;
  } catch (e) {
    return {ok: false, error: String(e)};
  }
}

async function apiPost(url, body) {
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
    return await resp.json().catch(() => ({ok: false, error: '响应解析失败'}));
  } catch (e) {
    return {ok: false, error: String(e)};
  }
}

async function apiPut(url, body) {
  try {
    const resp = await fetch(url, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
    return await resp.json().catch(() => ({ok: false, error: '响应解析失败'}));
  } catch (e) {
    return {ok: false, error: String(e)};
  }
}

async function apiDelete(url) {
  try {
    const resp = await fetch(url, {method: 'DELETE'});
    return await resp.json().catch(() => ({ok: false, error: '响应解析失败'}));
  } catch (e) {
    return {ok: false, error: String(e)};
  }
}

/** multipart 文件上传（FormData）。统一错误/解析，替代业务模块直接 fetch 上传。 */
async function apiUpload(url, formData, {signal = null, timeoutMs = null} = {}) {
  const ctrl = new AbortController();
  let tm = null;
  if (timeoutMs) tm = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      method: 'POST', body: formData, signal: signal || ctrl.signal,
    });
    const data = await resp.json().catch(() => ({ok: false, error: '响应解析失败'}));
    if (!resp.ok && !data.error) data.ok = false;
    return data;
  } catch (e) {
    if (ctrl.signal.aborted || (signal && signal.aborted)) return {ok: false, error: '请求超时'};
    return {ok: false, error: String(e)};
  } finally {
    if (tm) clearTimeout(tm);
  }
}

/** 通用请求（method 可选；最终都走统一错误/解析）。 */
async function apiRequest(url, method = 'GET', body = null) {
  if (method === 'GET') return apiGet(url);
  if (method === 'DELETE') return apiDelete(url);
  if (body instanceof FormData) return apiUpload(url, body);
  if (method === 'PUT') return apiPut(url, body);
  return apiPost(url, body);
}

Object.assign(window, {apiGet, apiPost, apiPut, apiDelete, apiUpload, apiRequest});