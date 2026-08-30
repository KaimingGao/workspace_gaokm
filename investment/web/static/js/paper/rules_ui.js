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
    [
      "ŷ买入",
      rules.min_predicted_score != null
        ? `${rules.min_predicted_score}%`
        : rules.min_score != null
          ? `${rules.min_score}（遗留）`
          : "—",
    ],
    ["最大持仓", String(rules.max_positions ?? "—")],
    [
      "仓位",
      rules.position_pct != null ? `${Math.round(rules.position_pct * 100)}%` : "—",
    ],
    [
      "现金底仓",
      rules.min_cash_pct != null
        ? `${Math.round(Number(rules.min_cash_pct) * 100)}%`
        : "20%",
    ],
    ["止损", rules.stop_loss_pnl != null ? `${rules.stop_loss_pnl}%` : "—"],
    ["做T", t0Label],
  ];
  const timing =
    (exe.rebalance_timing && typeof exe.rebalance_timing === "object"
      ? exe.rebalance_timing
      : null) || {};
  const pm =
    timing.path_matrix && typeof timing.path_matrix === "object"
      ? timing.path_matrix
      : null;
  if (pm) {
    chips.push([
      "择时",
      pm.mode === "linear"
        ? "线性"
        : `path≥${pm.path_enter != null ? pm.path_enter : 1}%`,
    ]);
  }
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

