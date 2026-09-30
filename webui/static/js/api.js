// §8 api.js：统一所有 HTTP 调用（FE-002）。
// 新代码一律走这里；既有模块内的 fetch 为零改动迁移，逐步收口。
// 契约（与旧 apiPost 一致，调用方依赖）：网络/解析异常不抛出，返回 {ok:false, error}。

async function apiGet(url) {
  try {
    const r = await fetch(url);
    const data = await r.json();
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
    return await resp.json();
  } catch (e) {
    return {ok: false, error: String(e)};
  }
}

Object.assign(window, {apiGet, apiPost});
