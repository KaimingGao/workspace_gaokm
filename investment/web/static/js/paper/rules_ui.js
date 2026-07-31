/** Paper rules summary chips render helper. */

import { escapeText } from "./fmt.js";

export function renderPaperRulesHtml(data) {
  if (!data || !data.initialized) return "—";
  const rules = (data && data.rules) || {};
  const exe = (data && data.execution) || {};
  const t0 = (exe.t0 && typeof exe.t0 === "object" ? exe.t0 : null) || rules.t0 || {};
  const ratio =
    t0.t0_ratio != null ? `${Math.round(Number(t0.t0_ratio) * 100)}%` : null;
  const dir = t0.direction || null;
  const t0Label = t0.enabled === false
    ? "关"
    : [ratio, dir, t0.fill_mode].filter(Boolean).join(" · ") || "开";
  const chips = [
    ["horizon", `${rules.horizon_days ?? "—"} 天`],
    ["min_score", String(rules.min_score ?? "—")],
    ["最大持仓", String(rules.max_positions ?? "—")],
    [
      "仓位",
      rules.position_pct != null ? `${Math.round(rules.position_pct * 100)}%` : "—",
    ],
    ["止损", rules.stop_loss_pnl != null ? `${rules.stop_loss_pnl}%` : "—"],
    ["做T", t0Label],
  ];
  if (exe.effective_hash) {
    chips.push(["exec", String(exe.effective_hash).slice(0, 8)]);
  }
  return chips
    .map(
      ([k, v]) =>
        `<span class="paper-rule-chip"><span class="k">${escapeText(k)}</span>` +
        `<span class="v">${escapeText(v)}</span></span>`
    )
    .join("");
}

