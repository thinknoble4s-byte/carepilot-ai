let base = null;

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

export async function api(path, options = {}) {
  const url = `${await apiBase()}${path.replace(/^\//, '')}`;
  const res = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}
