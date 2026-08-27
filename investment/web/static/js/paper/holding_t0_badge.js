/** 持仓表 · 当日实时做 T 状态徽章（与 core.t0.intraday.holding_t0_intraday_status 同源）。 */

export function holdingT0BadgeHtml(t0, escapeHtml = (s) => String(s ?? "")) {
  if (!t0 || !t0.badge) {
    return `<span class="paper-hold-t0-empty">—</span>`;
  }
  const phase = String(t0.phase || "");
  const direction = String(t0.direction || "");
  const cls = [
    "paper-hold-t0-badge",
    direction === "reverse_t" ? "is-rev" : direction === "long_t" ? "is-long" : "",
    phase === "after_leg1" ? "is-leg1" : "",
    phase === "idle" ? "is-idle" : "",
    phase === "done" ? "is-done" : "",
    phase === "skipped" ? "is-locked" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const title = escapeHtml(t0.title || t0.badge || "");
  const text = escapeHtml(t0.badge || "—");
  return `<span class="${cls}" title="${title}">${text}</span>`;
}
