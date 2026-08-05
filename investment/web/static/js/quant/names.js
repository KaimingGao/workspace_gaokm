/**
 * 观察池 / 研究台股票名展示。
 */
import { escapeHtml } from "../shared.js";

/** @returns {{ display: string, full: string }} */
export function truncateStockName(name, max = 6) {
  const full = String(name || "").trim();
  const chars = Array.from(full);
  if (chars.length <= max) return { display: full, full };
  return { display: `${chars.slice(0, max).join("")}…`, full };
}

export function watchingNameSpanHtml(name) {
  const { display, full } = truncateStockName(name);
  return (
    `<span class="watching-name-text" title="${escapeHtml(full)}" data-full-name="${escapeHtml(full)}">` +
    `${escapeHtml(display)}</span>`
  );
}

export function watchingNameFromEl(nameEl, fallback = "") {
  if (!nameEl) return fallback;
  const full = (nameEl.dataset.fullName || nameEl.getAttribute("title") || "").trim();
  return full || nameEl.textContent.trim() || fallback;
}

export function applyWatchingNameEl(nameEl, name) {
  if (!nameEl) return;
  const { display, full } = truncateStockName(name);
  nameEl.textContent = display;
  nameEl.title = full;
  nameEl.dataset.fullName = full;
}

/** A 股代码规范化（去交易所后缀）。 */
export function normalizeProbeCode(raw) {
  return String(raw || "")
    .trim()
    .replace(/\.(SH|SZ|BJ)$/i, "");
}
