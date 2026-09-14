// Cliente HTTP JSON com CSRF (cookie `csrftoken` do Django).
function csrfToken() {
  const m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
  return (m && decodeURIComponent(m[1])) || window.CSRF_TOKEN || "";
}

export async function api(path, { method = "GET", body = null } = {}) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.headers["X-CSRFToken"] = csrfToken();
    init.body = JSON.stringify(body);
  } else if (method !== "GET") {
    init.headers["X-CSRFToken"] = csrfToken();
  }
  const resp = await fetch(path, init);
  if (resp.status === 403) throw new Error("Sessão expirada ou CSRF inválido. Recarregue a página.");
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.message || `HTTP ${resp.status}`);
    err.code = data.code || `http_${resp.status}`;
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function newIdempotencyKey() {
  return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
}
