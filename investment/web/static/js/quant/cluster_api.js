/**
 * 分组 live API：错误格式化与 POST。
 */

export function formatClusterApiError(data, status) {
  const raw = data && (data.error != null ? data.error : data.detail);
  if (typeof raw === "string" && raw.trim()) return raw;
  if (Array.isArray(raw)) {
    const bits = raw
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return String(item);
        const loc = Array.isArray(item.loc)
          ? item.loc.filter((x) => x !== "body").join(".")
          : "";
        const msg = item.msg || item.message || "";
        return loc && msg ? `${loc}: ${msg}` : msg || JSON.stringify(item);
      })
      .filter(Boolean);
    if (bits.length) return bits.join("；");
  }
  if (raw && typeof raw === "object") {
    try {
      return JSON.stringify(raw);
    } catch (_) {
      /* ignore */
    }
  }
  if (status === 404) return "接口未找到：请重启 Web";
  return status ? `HTTP ${status}` : "请求失败";
}

/**
 * @param {string} path
 * @param {object} body
 * @param {{ fetchImpl?: typeof fetch }} [opts]
 */
export async function postClusterLive(path, body, opts = {}) {
  const fetchImpl = opts.fetchImpl || fetch;
  const res = await fetchImpl(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  if (!res.ok || !data || data.success === false) {
    return {
      ok: false,
      error: formatClusterApiError(data, res.status),
      data,
    };
  }
  return { ok: true, data };
}
