/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

export function renderExecutionRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载 ExecutionSpec";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const t0 = execution.t0 || {};
  const ratio =
    t0.t0_ratio != null ? `${Math.round(Number(t0.t0_ratio) * 100)}%` : "—";
  const sell = t0.sell_trigger_pct != null ? `+${t0.sell_trigger_pct}%` : "—";
  const buy = t0.buy_trigger_pct != null ? `-${t0.buy_trigger_pct}%` : "—";
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const hash = execution.effective_hash || "—";
  const rows = [
    ["选向", t0.direction ?? "—"],
    ["成交", t0.fill_mode ?? "—"],
    ["路径", t0.path_mode ?? "—"],
    ["动仓", ratio],
    ["触发", `${sell} / ${buy}`],
    ["ATR", t0.use_atr ? `开 · ${t0.atr_window ?? 14}日` : "关"],
    ["耦合", coup],
    ["hash", hash],
  ];
  const notes = (execution.notes || []).slice(0, 3);
  return (
    `<dl class="paper-t0-rules-grid">` +
    rows
      .map(
        ([k, v]) =>
          `<div class="paper-t0-rules-row"><dt>${escapeText(k)}</dt>` +
          `<dd>${escapeText(String(v))}</dd></div>`
      )
      .join("") +
    `</dl>` +
    (execution.summary
      ? `<p class="quant-sub paper-t0-rules-summary">${escapeText(execution.summary)}</p>`
      : "") +
    (notes.length
      ? `<ul class="paper-t0-rules-notes">${notes
          .map((n) => `<li>${escapeText(String(n))}</li>`)
          .join("")}</ul>`
      : "")
  );
}

/** 用生效 execution 填充表单控件。 */
export function fillExecutionForm(root, execution) {
  if (!root || !execution || !execution.t0) return;
  const t0 = execution.t0;
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || val == null) return;
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = String(val);
  };
  set("enabled", t0.enabled !== false);
  set("t0_ratio", t0.t0_ratio != null ? Math.round(Number(t0.t0_ratio) * 100) : 40);
  set("sell_trigger_pct", t0.sell_trigger_pct);
  set("buy_trigger_pct", t0.buy_trigger_pct);
  set("fill_mode", t0.fill_mode || "trigger");
  set("direction", t0.direction || "signal");
  set("path_mode", t0.path_mode || "dual_touch");
  set("use_atr", t0.use_atr !== false);
  set("t0_vs_stance", coup);
}

/** 从表单收集 PATCH body。 */
export function collectExecutionForm(root) {
  if (!root) return null;
  const num = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return fallback;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : fallback;
  };
  const str = (name, fallback) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el && el.value !== "" ? el.value : fallback;
  };
  const chk = (name, fallback = true) => {
    const el = root.querySelector(`[name="${name}"]`);
    return el ? !!el.checked : fallback;
  };
  const ratioPct = num("t0_ratio", 40);
  return {
    lock: true,
    t0: {
      enabled: chk("enabled", true),
      t0_ratio: Math.max(0.05, Math.min(ratioPct / 100, 1)),
      sell_trigger_pct: num("sell_trigger_pct", 2),
      buy_trigger_pct: num("buy_trigger_pct", 1.5),
      fill_mode: str("fill_mode", "trigger"),
      direction: str("direction", "signal"),
      path_mode: str("path_mode", "dual_touch"),
      use_atr: chk("use_atr", true),
    },
    coupling: {
      t0_vs_stance: str("t0_vs_stance", "independent"),
    },
  };
}

export function renderExecutionDiffHtml(diff) {
  if (!diff || !diff.ok) {
    return `<p class="quant-sub">${escapeText((diff && diff.error) || "无 diff")}</p>`;
  }
  if (!diff.changed) {
    return `<p class="quant-sub">与策略默认一致 · hash ${escapeText(diff.paper_hash || "—")}</p>`;
  }
  const rows = [...(diff.t0_changes || []), ...(diff.coupling_changes || [])].slice(0, 12);
  return (
    `<p class="quant-sub">相对 Spec 有 ${rows.length} 项差异</p>` +
    `<table class="quant-weight-table"><thead><tr><th>字段</th><th>Spec</th><th>纸面</th></tr></thead><tbody>` +
    rows
      .map(
        (r) =>
          `<tr><td>${escapeText(r.path)}</td>` +
          `<td>${escapeText(String(r.from))}</td>` +
          `<td>${escapeText(String(r.to))}</td></tr>`
      )
      .join("") +
    `</tbody></table>`
  );
}
