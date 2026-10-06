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

/**
 * 是否可作为展示用股票名（拒绝「名=代码」伪名，避免探针下拉只剩代码）。
 * @param {string} code
 * @param {string} name
 */
export function isUsableStockName(code, name) {
  const c = normalizeProbeCode(code);
  const nm = String(name || "").trim().replace(/\s+/g, "");
  if (!nm) return false;
  if (c && nm === c) return false;
  if (/^\d{6}$/.test(nm)) return false;
  return true;
}

/**
 * 从候选里取第一个可用中文名。
 * @param {string} code
 * @param {...(string|null|undefined)} candidates
 */
export function resolveStockDisplayName(code, ...candidates) {
  for (const cand of candidates) {
    if (isUsableStockName(code, cand)) {
      return String(cand || "").trim().replace(/\s+/g, "");
    }
  }
  return "";
}
