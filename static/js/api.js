// Small fetch wrapper: adds the CSRF token, turns failures into readable errors, handles session expiry.

const meta = (name) => document.querySelector(`meta[name="${name}"]`)?.content || "";

export const BRAND = meta("compass-brand");
const CSRF = meta("csrf-token");

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = { "X-CSRF-Token": CSRF };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      signal,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    window.dispatchEvent(new CustomEvent("compass:offline"));       // connection.js shows the banner and retries
    throw new ApiError("Can't reach the Compass AI server.", 0, null);
  }
  window.dispatchEvent(new CustomEvent("compass:reachable"));

  if (res.status === 401) {
    location.assign("/login");
    throw new ApiError("Please sign in again.", 401, null);
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (res.status === 403 && data && data.code === "device_required") {
    location.assign("/device");
    throw new ApiError(data.error, 403, data);
  }
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status, data);
  return data;
}
