let base = null;
let currentUser = null;

async function apiBase() {
  if (base) return base;
  // config.json is written at deploy time; local dev falls back to uvicorn.
  let apiUrl = 'http://localhost:8000/';
  try {
    const res = await fetch('./config.json', { cache: 'no-store' });
    if (res.ok) apiUrl = (await res.json()).apiUrl;
  } catch (e) {
    /* local dev */
  }
  base = apiUrl.endsWith('/') ? apiUrl : `${apiUrl}/`;
  return base;
}

// Demo profile: the "Viewing as" switcher picks the caller (X-User header), no login.
export function setUser(userId) {
  currentUser = userId;
}

async function request(path, options, headers) {
  const url = `${await apiBase()}${path.replace(/^\//, '')}`;
  const res = await fetch(url, {
    ...options,
    headers: { ...(currentUser ? { 'X-User': currentUser } : {}), ...headers, ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join('; ') : body.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return body;
}

export function api(path, options = {}) {
  return request(path, options, { 'Content-Type': 'application/json' });
}

export function post(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body ?? {}) });
}

export async function blobUrl(path) {
  const res = await fetch(`${await apiBase()}${path.replace(/^\//, '')}`, {
    headers: currentUser ? { 'X-User': currentUser } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

export function upload(path, formData) {
  return request(path, { method: 'POST', body: formData }, {});
}
