/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

export const Y_TAU_MAP_LABELS = {
  scalp: "高抛低吸（y_τ>0→正T）",
  trend: "趋势跟随（y_τ>0→反T）",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 规则卡 / 摘要用短标签（不含 dual_y、策略名）。 */
export const Y_TAU_MAP_SHORT = {
  scalp: "y_τ>0→正T",
  trend: "y_τ>0→反T",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

export function yTauMapLabel(mode) {
  const k = String(mode || "scalp").trim().toLowerCase();
  return Y_TAU_MAP_LABELS[k] || k;
}

export function yTauMapShortLabel(mode) {
  const k = String(mode || "scalp").trim().toLowerCase();
  return Y_TAU_MAP_SHORT[k] || yTauMapLabel(k);
}

/** dual_y 方向分说明（随 τ 映射变化）。enter 单位=收益百分点。 */
export function yTauMapScoreTip(mode, enter = 0.25) {
  const m = String(mode || "scalp").trim().toLowerCase();
  const e = Number(enter);
  const thr = Number.isFinite(e) ? e : 0.25;
  const tail = "与 y_eod 冲突或 |y_trade| 不足则跳过。";
  if (m === "trend") {
    return `dual_y[trend]：|y_τ|≥${thr}% 定方向（y_τ>0 反T，<0 正T）；${tail}`;
  }
  if (m === "fixed_long") {
    return `dual_y[fixed_long]：|y_τ|≥${thr}% 固定正T；${tail}`;
  }
  if (m === "fixed_reverse") {
    return `dual_y[fixed_reverse]：|y_τ|≥${thr}% 固定反T；${tail}`;
  }
  return `dual_y[scalp]：|y_τ|≥${thr}% 定方向（y_τ>0 正T，<0 反T）；${tail}`;
}

/** 回测/预演响应可能只有 rules 或残缺 execution；补齐 t0 供规则卡渲染。 */
export function normalizeExecutionView(execution, rules) {
  if (!execution && !rules) return null;
  const base = execution ? { ...execution } : { ok: true };
  const t0 = { ...(rules || {}), ...(base.t0 || {}) };
  const hasDetail =
    t0.direction != null ||
    t0.path_mode != null ||
    t0.sell_trigger_pct != null ||
    t0.t0_ratio != null;
  if (!hasDetail && !base.effective_hash) return execution || null;
  return {
    ...base,
    ok: base.ok !== false,
    t0,
    coupling: base.coupling || {},
  };
}

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
  const rangeLbl =
    t0.min_range_pct != null && t0.min_range_pct !== ""
      ? `${t0.min_range_pct}%`
      : "自动";
  const rows = [
    ["选向", yTauMapShortLabel(t0.y_tau_map)],
    ["入场", `|y_τ|≥${t0.y_tau_enter ?? 0.25}%`],
    ["y_trade", `|y_trade|≥${t0.y_trade_floor ?? 0.15}%`],
    ["y_eod", `|y_eod|≥${t0.y_eod_prior ?? 0.35}%`],
    ["y_on", `|y_on|≥${t0.y_on_allow ?? 1.2}%`],
    ["振幅", rangeLbl],
    ["成交", t0.fill_mode ?? "—"],
    ["路径", t0.path_mode ?? "first_touch"],
    ["动仓", ratio],
    ["触发", `${sell} / ${buy}`],
    ["ATR", t0.use_atr ? `开 · ${t0.atr_window ?? 14}日` : "关"],
    ["回补", t0.must_cover_same_day ? "收盘强制" : "y_on策略"],
    ["耦合", coup],
    ["hash", hash],
  ];
  const notes = (execution.notes || []).slice(0, 3);
  const dirNote =
    "dual_y：|y_trade|幅度 · y_τ主方向（τ映射可对照）· y_eod冲突跳过 · y_on回补；ŷ 为收益百分点。";
  return (
    `<dl class="paper-t0-rules-grid">` +
    rows
      .map(
        ([k, v]) =>
          `<div class="paper-t0-rules-row"><dt>${escapeText(k)}</dt>` +
          `<dd title="${escapeText(
            k === "选向"
              ? "多层 ŷ：y_trade / y_τ / y_eod / y_on"
              : k === "入场"
                ? "|y_τ|≥门槛（收益百分点）才入场；方向由 τ映射 定"
                : k === "y_trade"
                  ? "|y_trade|≥门槛（blend 预期日波动幅度，收益百分点）"
                  : k === "y_eod"
                    ? "|y_eod|≥此值才参与与 y_τ 冲突检测"
                    : k === "y_on"
                      ? "|y_on|≥此值才允许隔夜敞口（未强制回补时）"
                      : k === "振幅"
                        ? "日振幅下限；空=自动"
                        : k === "路径"
                          ? "做T回测强制 first_touch；纸面预演可读此字段"
                          : ""
          )}">${escapeText(String(v))}</dd></div>`
      )
      .join("") +
    `</dl>` +
    (dirNote
      ? `<p class="quant-sub paper-t0-rules-summary">${escapeText(dirNote)}</p>`
      : "") +
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
  if (t0.min_range_pct != null && t0.min_range_pct !== "") {
    set("min_range_pct", t0.min_range_pct);
  } else {
    set("min_range_pct", 0.5);
  }
  set("fill_mode", t0.fill_mode || "trigger");
  set("direction", "dual_y");
  set("y_tau_map", t0.y_tau_map || "scalp");
  set("path_mode", t0.path_mode || "first_touch");
  set("use_atr", t0.use_atr !== false);
  set("must_cover_same_day", !!t0.must_cover_same_day);
  set("t0_vs_stance", coup);
  set("y_trade_floor", t0.y_trade_floor != null ? t0.y_trade_floor : 0.15);
  set("y_tau_enter", t0.y_tau_enter != null ? t0.y_tau_enter : 0.25);
  set("y_eod_prior", t0.y_eod_prior != null ? t0.y_eod_prior : 0.35);
  set("y_on_allow", t0.y_on_allow != null ? t0.y_on_allow : 1.2);
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
  const numOrNull = (name) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el || el.value === "") return null;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : null;
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
  const t0 = {
    enabled: chk("enabled", true),
    t0_ratio: Math.max(0.05, Math.min(ratioPct / 100, 1)),
    sell_trigger_pct: num("sell_trigger_pct", 2),
    buy_trigger_pct: num("buy_trigger_pct", 1.5),
    fill_mode: str("fill_mode", "trigger"),
    direction: "dual_y",
    y_tau_map: str("y_tau_map", "scalp"),
    path_mode: str("path_mode", "first_touch"),
    use_atr: chk("use_atr", true),
    must_cover_same_day: chk("must_cover_same_day", false),
    y_trade_floor: num("y_trade_floor", 0.15),
    y_tau_enter: Math.max(0.05, Math.min(num("y_tau_enter", 0.25), 5)),
    y_eod_prior: Math.max(0.05, Math.min(num("y_eod_prior", 0.35), 5)),
    y_on_allow: Math.max(0.1, Math.min(num("y_on_allow", 1.2), 10)),
  };
  const minRange = numOrNull("min_range_pct");
  if (minRange != null) t0.min_range_pct = Math.max(0.2, Math.min(minRange, 30));
  return {
    lock: true,
    t0,
    coupling: {
      t0_vs_stance: str("t0_vs_stance", "independent"),
    },
  };
}

/** 做 T 区块根节点：复选框在 form 外，须从 section 查找。 */
function t0PanelRoot(formRoot) {
  if (formRoot) {
    const fromForm = formRoot.closest("#follow-section-t0, .follow-ops-t0");
    if (fromForm) return fromForm;
  }
  return (
    document.getElementById("follow-section-t0") ||
    document.querySelector(".follow-ops-t0") ||
    formRoot ||
    document
  );
}

/**
 * 做T回测范围：表单复选框 + Alt 快捷键。路径固定 5m first_touch（已删除日线模拟）。
 * @param {HTMLElement|null} root 通常为 #paper-t0-form；复选框在同 section 内 form 外
 * @param {{ altKey?: boolean, selectedCode?: string|null }} evt
 */
export function resolveT0BacktestScope(root, evt = {}) {
  const panel = t0PanelRoot(root);
  const selectedCode = String(evt.selectedCode || "").trim();
  const alt = !!evt.altKey;
  const onlyEl = panel.querySelector('[name="t0_bt_only_selected"]');
  const wantOnly = alt || !!(onlyEl && onlyEl.checked);
  if (wantOnly && !selectedCode) {
    return {
      onlySelected: false,
      useMinute: true,
      error: "请先点击持仓表选中一只股票，或取消「仅选中」",
    };
  }
  return {
    onlySelected: wantOnly && !!selectedCode,
    useMinute: true,
    error: null,
  };
}

/**
 * 做T回测请求体：直接读表单当前值（不必先点保存）。
 * @param {{ useMinute?: boolean, onlySelected?: boolean, selectedCode?: string }} opts
 */
export function collectT0BacktestBody(root, opts = {}) {
  const patch = collectExecutionForm(root) || { t0: {} };
  const t0 = patch.t0 || {};
  const lookbackEl = root && root.querySelector('[name="lookback"]');
  let lookback = 10;
  if (lookbackEl && lookbackEl.value !== "") {
    const n = Number(lookbackEl.value);
    if (Number.isFinite(n)) lookback = Math.max(10, Math.min(Math.round(n), 500));
  }
  const body = {
    from_paper: true,
    lookback,
    compare_optimistic: true,
    use_minute: true,
    compare_daily: false,
    t0_ratio: t0.t0_ratio != null ? t0.t0_ratio : 0.4,
    sell_trigger_pct: t0.sell_trigger_pct != null ? t0.sell_trigger_pct : 2,
    buy_trigger_pct: t0.buy_trigger_pct != null ? t0.buy_trigger_pct : 1.5,
    fill_mode: t0.fill_mode || "trigger",
    direction: "dual_y",
    y_tau_map: t0.y_tau_map || "scalp",
    path_mode: "first_touch",
    must_cover_same_day: !!t0.must_cover_same_day,
    use_atr: t0.use_atr !== false,
    y_trade_floor: t0.y_trade_floor != null ? t0.y_trade_floor : 0.15,
    y_tau_enter: t0.y_tau_enter != null ? t0.y_tau_enter : 0.25,
    y_eod_prior: t0.y_eod_prior != null ? t0.y_eod_prior : 0.35,
    y_on_allow: t0.y_on_allow != null ? t0.y_on_allow : 1.2,
  };
  if (t0.min_range_pct != null) body.min_range_pct = t0.min_range_pct;
  else body.min_range_pct = 0.5;
  if (opts.onlySelected && opts.selectedCode) body.code = opts.selectedCode;
  return body;
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
