/** Shared API client: degrade banner + JSON fetch. */

let _failStreak = 0;
let _lastOkAt = null;

function bannerEl() {
  return document.getElementById("api-degrade-banner");
}

export function getApiLastOkAt() {
  return _lastOkAt;
}

export function setApiDegradeBanner(show, message) {
  const el = bannerEl();
  if (!el) return;
  if (show) {
    el.hidden = false;
    el.setAttribute("aria-hidden", "false");
    const text = el.querySelector(".api-degrade-banner-text");
    if (text) {
      text.textContent =
        message ||
        "后端暂时不可用 · 显示上次成功数据（若有）· 恢复后自动重试";
    }
  } else {
    el.hidden = true;
    el.setAttribute("aria-hidden", "true");
  }
}

export function noteApiSuccess() {
  _failStreak = 0;
  _lastOkAt = Date.now();
  setApiDegradeBanner(false);
}

export function noteApiFailure(message) {
  _failStreak += 1;
  if (_failStreak >= 1) {
    const when = _lastOkAt
      ? ` · 上次成功 ${new Date(_lastOkAt).toLocaleTimeString("zh-CN", { hour12: false })}`
      : "";
    setApiDegradeBanner(true, (message || "接口请求失败") + when);
  }
}

/**
 * @param {string} url
 * @param {RequestInit} [opts]
 * @returns {Promise<{ ok: boolean, status: number, data: any, error?: string }>}
 */
export async function apiFetch(url, opts = {}) {
  try {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      noteApiSuccess();
      return { ok: true, status: res.status, data };
    }
    const detail =
      (typeof data.detail === "string" && data.detail) ||
      data.error ||
      res.statusText ||
      `HTTP ${res.status}`;
    noteApiFailure(String(detail));
    return { ok: false, status: res.status, data, error: String(detail) };
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    noteApiFailure(msg);
    return { ok: false, status: 0, data: {}, error: msg };
  }
}

/** Paginate an array for dense tables (W0.5). */
export function paginateItems(items, page, pageSize) {
  const list = Array.isArray(items) ? items : [];
  const size = Math.max(1, Number(pageSize) || 50);
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / size) || 1);
  const p = Math.min(Math.max(1, Number(page) || 1), pages);
  const start = (p - 1) * size;
  return {
    items: list.slice(start, start + size),
    page: p,
    pageSize: size,
    total,
    pages,
    hasPrev: p > 1,
    hasNext: p < pages,
  };
}

export function renderPagerHtml(state, { idPrefix = "pager" } = {}) {
  if (!state || state.total <= state.pageSize) return "";
  return (
    `<div class="table-pager" id="${idPrefix}-pager" role="navigation" aria-label="分页">` +
    `<button type="button" class="dialog-btn secondary table-pager-btn" data-pager="prev" ${
      state.hasPrev ? "" : "disabled"
    }>上一页</button>` +
    `<span class="table-pager-meta">${state.page} / ${state.pages} · 共 ${state.total}</span>` +
    `<button type="button" class="dialog-btn secondary table-pager-btn" data-pager="next" ${
      state.hasNext ? "" : "disabled"
    }>下一页</button>` +
    `</div>`
  );
}
