/** Paper UI formatters. */

export function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeText(String(v));
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function fmtPriceUnit(v, unit, currency) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return currency === "CNY" ? `${n}${unit || "元"}` : `${unit || ""}${n}`;
}

export function escapeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function fmtPct(v, { signed = false } = {}) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeText(String(v));
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n}%`;
}

export function metricCls(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}

export function paperFmtPct(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return `${n}%`;
}

export function paperMetricClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}
