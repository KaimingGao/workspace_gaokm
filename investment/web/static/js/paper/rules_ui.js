/** Paper rules summary chips render helper. */

import { escapeText } from "./fmt.js";

function coerceRankChip(raw, fallback) {
  if (raw == null || raw === "") return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  if (n >= 0.5) {
    if (Math.abs(n - 1) < 1e-9) return 0.01;
    if (Math.abs(n - 1.002) < 1e-6) return 0.02;
    return Math.max(0, n - 1);
  }
  if (Math.abs(n - 0.2) < 1e-6) return 0.02;
  return n;
}

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
    const enter = coerceRankChip(pm.rank_enter, 0.01);
    const strong = coerceRankChip(pm.rank_strong, 0.02);
    const floor =
      pm.cash_floor != null
        ? `${Math.round(Number(pm.cash_floor) / 10000)}万`
        : "50万";
    chips.push([
      "rank",
      `入场${(Number(enter) * 100).toFixed(1)}% · 强${(Number(strong) * 100).toFixed(1)}%`,
    ]);
    const alpha = pm.y_on_alpha != null ? Number(pm.y_on_alpha) : 0;
    if (Number.isFinite(alpha)) {
      chips.push(["α_on", Number(alpha).toFixed(alpha % 1 === 0 ? 0 : 1)]);
    }
    chips.push(["现金地板", floor]);
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

