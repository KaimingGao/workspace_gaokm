/** Paper · Execution / 做T 生效规则卡 + 编辑表单。 */

import { escapeText } from "./fmt.js";

export const Y_TAU_MAP_LABELS = {
  trend: "符号定方向",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 表单 / 规则卡选项文案（与 follow 下拉 value 一一对应）。 */
export const Y_TAU_MAP_SELECT_LABELS = {
  trend: "符号定方向",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 规则卡 / 摘要用短标签（表格列头等紧凑场景）。 */
export const Y_TAU_MAP_SHORT = {
  trend: "符号",
  fixed_long: "固定正T",
  fixed_reverse: "固定反T",
};

/** 与后端 normalize_y_tau_map 对齐（scalp 等别名 → trend）。 */
export function normalizeYTauMap(mode) {
  const k = String(mode || "trend").trim().toLowerCase();
  if (k === "scalp" || k === "follow" || k === "momentum" || k === "invert") {
    return "trend";
  }
  if (k === "long" || k === "always_long") return "fixed_long";
  if (k === "reverse" || k === "always_reverse") return "fixed_reverse";
  if (k in Y_TAU_MAP_SELECT_LABELS) return k;
  return "trend";
}

export function yTauMapLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_LABELS[k] || k;
}

export function yTauMapShortLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_SHORT[k] || yTauMapLabel(k);
}

export function yTauMapRuleLabel(mode) {
  const k = normalizeYTauMap(mode);
  return Y_TAU_MAP_SELECT_LABELS[k] || k;
}

const FILL_MODE_LABELS = {
  trigger: "触价",
  mid: "触价·中点",
  optimistic: "乐观（高卖低买）",
};

const PATH_MODE_LABELS = {
  first_touch: "5m 首触达",
};

function fmtThresholdPct(val, fallback, op = "≥") {
  const n = Number(val ?? fallback);
  if (!Number.isFinite(n)) return "—";
  const s = Math.abs(n) >= 1 ? n.toFixed(1) : n.toFixed(2);
  return `${op}${s}%`;
}

/** dual_y 目标价缩放区间（规则卡 gauge / 表注）；单位=触发幅度相对基准的 %。 */
export function adaptiveSizingBounds(t0 = {}) {
  const sell = Number(t0.sell_trigger_pct);
  const buy = Number(t0.buy_trigger_pct);
  const baseSell = Number.isFinite(sell) ? sell : 2;
  const baseBuy = Number.isFinite(buy) ? buy : 1.5;
  const cut = Number(t0.y_ratio_cut ?? 0.6);
  const cutSafe = Number.isFinite(cut) && cut > 0 && cut <= 1 ? cut : 0.6;
  const cap = Number(t0.y_ratio_boost_cap ?? 2);
  const capSafe =
    Number.isFinite(cap) && cap >= 1 ? Math.min(Math.max(cap, cutSafe), 2) : 2;
  const minSell = Math.max(0.1, +(baseSell * cutSafe).toFixed(2));
  const minBuy = Math.max(0.1, +(baseBuy * cutSafe).toFixed(2));
  const maxSell = Math.max(0.1, +(baseSell * capSafe).toFixed(2));
  const maxBuy = Math.max(0.1, +(baseBuy * capSafe).toFixed(2));
  const ratioPct = 100;
  // 刻度：相对基准触发 %（基准=100，弱≈cut×100，强上限=cap×100）
  const maxPct = Math.round(capSafe * 100);
  const minPct = Math.max(5, Math.round(cutSafe * 100));
  const basePct = 100;
  return {
    basePct,
    minPct,
    maxPct,
    ratioPct,
    baseSell,
    baseBuy,
    minSell,
    minBuy,
    maxSell,
    maxBuy,
    cut: cutSafe,
    cap: capSafe,
  };
}

/** dual_y 目标价缩放只读摘要（规则卡 / 表注）。 */
export function adaptiveSizingRuleLabel(t0 = {}) {
  const { baseSell, baseBuy, minSell, minBuy, maxSell, maxBuy, cut, cap } =
    adaptiveSizingBounds(t0);
  return (
    `基准 卖+${baseSell}%/买−${baseBuy}%` +
    ` · 弱×${cut}→+${minSell}%/−${minBuy}%` +
    ` · 强×${cap}→+${maxSell}%/−${maxBuy}%`
  );
}

/** 成交日行 hover：基准触发 → 当日有效目标价。 */
export function adaptiveSizingDayTip(day, rules = {}) {
  const scale = Number(day?.trigger_scale);
  const sellBase = Number(day?.sell_trigger_pct_base ?? rules?.sell_trigger_pct);
  const buyBase = Number(day?.buy_trigger_pct_base ?? rules?.buy_trigger_pct);
  const sellEff = Number(day?.sell_trigger_pct ?? sellBase);
  const buyEff = Number(day?.buy_trigger_pct ?? buyBase);
  if (!Number.isFinite(sellEff) || !Number.isFinite(buyEff)) return "";
  const fmt = (n) => (Number.isFinite(n) ? Number(n).toFixed(2).replace(/\.?0+$/, "") : "—");
  if (!Number.isFinite(scale) || Math.abs(scale - 1) < 0.005) {
    return `目标 卖+${fmt(sellEff)}% / 买−${fmt(buyEff)}%（基准）`;
  }
  if (Number.isFinite(sellBase) && Number.isFinite(buyBase)) {
    return (
      `目标 卖+${fmt(sellBase)}%→+${fmt(sellEff)}%` +
      ` · 买−${fmt(buyBase)}%→−${fmt(buyEff)}%` +
      `（ŷ×${scale.toFixed(2)}）`
    );
  }
  return `目标 卖+${fmt(sellEff)}% / 买−${fmt(buyEff)}%（ŷ×${scale.toFixed(2)}）`;
}

/** dual_y 方向分说明（随 τ 映射变化）。enter 单位=收益百分点。 */
export function yTauMapScoreTip(mode, enter = 0.25) {
  const m = normalizeYTauMap(mode);
  const e = Number(enter);
  const thr = Number.isFinite(e) ? e : 0.25;
  const tail = "|y_trade| 不足则跳过。";
  if (m === "fixed_long") {
    return `dual_y[固定正T]：|y_τ|≥${thr}% 固定正T；${tail}`;
  }
  if (m === "fixed_reverse") {
    return `dual_y[固定反T]：|y_τ|≥${thr}% 固定反T；${tail}`;
  }
  return `dual_y：|y_τ|≥${thr}% 定方向（符号映射）；${tail}`;
}

/** dual_y 各头准入阈值（与 score_policy 默认对齐）。 */
function dualYGateThresholds(t0) {
  const x = t0 || {};
  const pick = (primary, legacy, fallback) => {
    if (x[primary] != null && x[primary] !== "") return Number(x[primary]);
    if (legacy && x[legacy] != null && x[legacy] !== "") return Number(x[legacy]);
    return fallback;
  };
  return {
    tradeEnter: pick("y_trade_enter", "y_trade_floor", 0.02),
    tradeStrong: pick("y_trade_strong", "y_trade_tau_sign_gate", 0.1),
    tauEnter: pick("y_tau_enter", null, 0.02),
    tauEnterLong: pick("y_tau_enter_long", "y_tau_enter", 0.02),
    tauEnterReverse: pick("y_tau_enter_reverse", "y_tau_enter", 0.02),
    pathEnter: pick("y_path_enter", null, 0.02),
    pathEnterLong: pick("y_path_enter_long", "y_path_enter", 0.02),
    pathEnterReverse: pick("y_path_enter_reverse", "y_path_enter", 0.02),
    eodEnter: pick("y_eod_enter", null, 0.01),
    eodStrong: pick("y_eod_strong", "y_eod_tau_sign_gate", 0.1),
    pathOn: x.y_use_path !== false,
    eodPrior: pick("y_eod_prior", null, 0.02),
    ncEnter: pick("y_nc_enter", null, 0.01),
    ncStrong: pick("y_nc_strong", "y_nowcast_enter", 3.0),
    ncBlock: x.y_block_tau_nowcast_sign !== false,
    ncOc: x.y_nowcast_oc_gate === true,
  };
}

function buildDualYGateMatrixHtml(t0) {
  const g = dualYGateThresholds(t0);
  const tradeGate = fmtThresholdPct(g.tradeEnter, 0.02, "≥");
  const tradeStrong = fmtThresholdPct(g.tradeStrong, 0.1, ">");
  const tauLong = fmtThresholdPct(g.tauEnterLong, 0.02, "≥");
  const tauRev = fmtThresholdPct(g.tauEnterReverse, 0.02, "≥");
  const pathLong = fmtThresholdPct(g.pathEnterLong, 0.02, "≥");
  const pathRev = fmtThresholdPct(g.pathEnterReverse, 0.02, "≥");
  const eodGate = fmtThresholdPct(g.eodEnter, 0.01, "≥");
  const eodStrong = fmtThresholdPct(g.eodStrong, 0.1, ">");
  const rows = [
    {
      stage: "trade",
      role: "幅度",
      head: "trade",
      enter: tradeGate,
      strong: `${tradeStrong}同τ`,
      tip: "入场 y_trade_enter；|ŷ|>y_trade_strong 须与 τ 同号",
    },
    {
      stage: "tau",
      role: "方向",
      head: "τ",
      enter: `正${tauLong}/反${tauRev}`,
      strong: g.pathOn ? "联合path" : "方向锚",
      tip: g.pathOn
        ? "path 开：正/反分侧 τ·path 同号双过 enter；符号映射正/反 T"
        : "path 关：正/反分侧 |ŷ_τ|≥enter 定方向",
    },
    {
      stage: "path",
      role: "路径",
      head: "path",
      enter: g.pathOn ? `正${pathLong}/反${pathRev}` : "关",
      strong: g.pathOn ? "须同τ" : "—",
      tip: g.pathOn
        ? "path 开：正/反分侧 |ŷ_path| 过 enter（与 τ 侧向独立）"
        : "y_use_path 关",
    },
    {
      stage: "eod",
      role: "隔夜",
      head: "eod",
      enter: eodGate,
      strong: `${eodStrong}同τ`,
      tip: `入场 y_eod_enter；|ŷ|>${g.eodStrong}% 同 τ；prior ${g.eodPrior}% 仅抬目标`,
    },
  ];
  if (g.ncBlock) {
    const ncGate = fmtThresholdPct(g.ncEnter, 0.01, "≥");
    const ncStrong = fmtThresholdPct(g.ncStrong, 3.0, ">");
    rows.push({
      stage: "nc",
      role: "对照",
      head: "nc",
      enter: ncGate,
      strong: `${ncStrong}同τ`,
      tip: g.ncOc
        ? "入场 y_nc_enter；|y_nc_oc|>strong 须与 τ 同号"
        : "入场 y_nc_enter；|nc|>strong 须与 τ 同号",
    });
  }
  return (
    `<div class="paper-t0-spec-gate-matrix" aria-label="dual_y 准入门槛">` +
    rows
      .map(
        (r, i) =>
          `<div class="paper-t0-spec-gate-row" data-stage="${escapeText(r.stage)}" title="${escapeText(r.tip)}">` +
          `<span class="paper-t0-spec-gate-step">${String(i + 1).padStart(2, "0")}</span>` +
          `<span class="paper-t0-spec-gate-role">${escapeText(r.role)}</span>` +
          `<span class="paper-t0-spec-gate-head">${escapeText(r.head)}</span>` +
          `<span class="paper-t0-spec-gate-kv">` +
          `<span class="paper-t0-spec-gate-k">入场</span>` +
          `<span class="paper-t0-spec-gate-floor">${escapeText(r.enter)}</span>` +
          `</span>` +
          `<span class="paper-t0-spec-gate-kv">` +
          `<span class="paper-t0-spec-gate-k">强</span>` +
          `<span class="paper-t0-spec-gate-sign">${escapeText(r.strong)}</span>` +
          `</span>` +
          `</div>` +
          (i < rows.length - 1
            ? `<span class="paper-t0-spec-gate-join" aria-hidden="true">›</span>`
            : "")
      )
      .join("") +
    `</div>`
  );
}

/** 回测/预演响应可能只有 rules 或残缺 execution；补齐 t0 供规则卡渲染。 */
export { nowcastOcPct } from "./fmt.js";

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

function specKpi(label, value, tip = "") {
  const title = tip ? ` title="${escapeText(tip)}"` : "";
  return (
    `<div class="paper-t0-spec-kpi"${title}>` +
    `<span class="paper-t0-spec-kpi-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-kpi-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
}

function specSubhead(label, value) {
  return (
    `<div class="paper-t0-spec-subhead">` +
    `<span class="paper-t0-spec-subhead-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-subhead-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
}

function specPanelTitle(kind, text) {
  return `<h5 class="paper-t0-spec-panel-title"><span class="paper-t0-spec-panel-mark is-${kind}" aria-hidden="true"></span>${escapeText(text)}</h5>`;
}

function buildTauMapVisualHtml(mode, enterRaw) {
  const k = normalizeYTauMap(mode);
  const gate = `|y_τ| ${fmtThresholdPct(enterRaw, 0.25)}`;

  function tauRow(polarity, sign, cond, action, actionKind, tip = "") {
    const title = tip ? ` title="${escapeText(tip)}"` : "";
    return (
      `<div class="paper-t0-spec-tau-row is-${polarity}"${title}>` +
      `<span class="paper-t0-spec-tau-sign">${sign}</span>` +
      `<span class="paper-t0-spec-tau-cond">${cond}</span>` +
      `<span class="paper-t0-spec-tau-arrow" aria-hidden="true">→</span>` +
      `<span class="paper-t0-spec-tau-act is-${actionKind}">${escapeText(action)}</span>` +
      `</div>`
    );
  }

  let rows = "";
  if (k === "fixed_long") {
    rows = tauRow("single", "±", `<span class="mono">|y_τ|</span> 达标`, "正T", "long", "固定正T");
  } else if (k === "fixed_reverse") {
    rows = tauRow("single", "±", `<span class="mono">|y_τ|</span> 达标`, "反T", "reverse", "固定反T");
  } else {
    rows =
      tauRow("up", "+", `<span class="mono">y_τ</span> &gt; 0`, "反T", "reverse", "卖高优先") +
      tauRow("down", "−", `<span class="mono">y_τ</span> &lt; 0`, "正T", "long", "买低优先");
  }

  return (
    `<div class="paper-t0-spec-panel-viz">` +
    specSubhead("门控", gate) +
    `<div class="paper-t0-spec-panel-body is-tau">` +
    `<div class="paper-t0-spec-tau-wrap">${rows}</div>` +
    `</div></div>`
  );
}

function buildSizingGaugeHtml(t0) {
  const {
    basePct,
    minPct,
    maxPct,
    baseSell,
    baseBuy,
    minSell,
    minBuy,
    maxSell,
    maxBuy,
  } = adaptiveSizingBounds(t0);
  const span = Math.max(maxPct, 1);
  const pos = (v) => Math.max(0, Math.min(100, (Number(v) / span) * 100));
  const minPos = pos(minPct);
  const basePos = pos(basePct);
  const maxPos = pos(maxPct);
  const bandLeft = minPos;
  const bandWidth = Math.max(2, maxPos - minPos);
  const tip =
    `基准 卖+${baseSell}% / 买−${baseBuy}%；` +
    `弱→+${minSell}%/−${minBuy}% · 强→+${maxSell}%/−${maxBuy}%（ŷ 信心）`;
  return (
    `<div class="paper-t0-spec-panel-viz">` +
    specSubhead("基准", `+${baseSell}% / −${baseBuy}%`) +
    `<div class="paper-t0-spec-panel-body is-gauge" title="${escapeText(tip)}">` +
    `<div class="paper-t0-spec-gauge-wrap">` +
    `<div class="paper-t0-spec-gauge-track" aria-hidden="true">` +
    `<span class="paper-t0-spec-gauge-band" style="left:${bandLeft}%;width:${bandWidth}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-min" style="left:${minPos}%" title="弱信号 ${minPct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-base" style="left:${basePos}%" title="基准 ${basePct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-max" style="left:${maxPos}%" title="上限 ${maxPct}%"></span>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-ticks" aria-hidden="true">` +
    `<span class="is-edge" style="left:0%">0</span>` +
    `<span class="is-min" style="left:${minPos}%">${minPct}</span>` +
    `<span class="is-base" style="left:${basePos}%">${basePct}</span>` +
    `<span class="is-max" style="left:${maxPos}%">${maxPct}</span>` +
    `</div>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-legend">` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-band"></i>有效区间</span>` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-base"></i>基准</span>` +
    `<span class="paper-t0-spec-gauge-key is-range">${minPct}–${maxPct}%</span>` +
    `</div>` +
    `</div></div>`
  );
}

export function renderExecutionRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载 ExecutionSpec";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const t0 = execution.t0 || {};
  const { ratioPct } = adaptiveSizingBounds(t0);
  const fillLong =
    FILL_MODE_LABELS[
      String(t0.fill_mode_long || t0.fill_mode || "trigger").toLowerCase()
    ] || t0.fill_mode_long || t0.fill_mode || "—";
  const fillRev =
    FILL_MODE_LABELS[
      String(t0.fill_mode_reverse || t0.fill_mode || "trigger").toLowerCase()
    ] || t0.fill_mode_reverse || t0.fill_mode || "—";
  const fillLbl = `正${fillLong}/反${fillRev}`;
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || t0.path_mode || "—";
  const pathModelOk = execution.path_model_present !== false;
  const pathModelWarn =
    t0.y_use_path !== false && !pathModelOk
      ? "⚠ ŷ_path 模型未就绪 · /quant 拟合/启用"
      : execution.path_model_shadow
        ? "影子模型（未 promote）"
        : null;

  const notes = (execution.notes || []).slice(0, 2);

  const enabledLbl = t0.enabled === false ? "关" : "开";
  const headMeta = `做T ${enabledLbl} · dual_y · ${pathLbl} · ${fillLbl}${
    pathModelWarn ? ` · ${pathModelWarn}` : ""
  }`;

  return (
    `<div class="paper-t0-spec">` +
    `<header class="paper-t0-spec-head">` +
    `<div class="paper-t0-spec-head-main">` +
    `<h4 class="paper-t0-spec-head-title">生效规格</h4>` +
    `<p class="paper-t0-spec-head-meta">${escapeText(headMeta)}</p>` +
    `</div>` +
    `<div class="paper-t0-spec-kpi-strip" aria-label="核心参数">` +
    specKpi("启用", enabledLbl, "做 T overlay 总开关") +
    specKpi("策略", "dual_y", "多层 ŷ 门控") +
    specKpi("动仓", `${ratioPct}%`, "固定底仓比例（不随 ŷ 缩放）") +
    specKpi("路径", pathLbl, "分钟触价路径") +
    specKpi("成交", fillLbl, "撮合假设") +
    `</div></header>` +
    buildDualYGateMatrixHtml(t0) +
    `<div class="paper-t0-spec-grid">` +
    `<section class="paper-t0-spec-panel is-direction">` +
    specPanelTitle("tau", "τ 方向映射") +
    buildTauMapVisualHtml(t0.y_tau_map, t0.y_tau_enter) +
    `<p class="paper-t0-spec-panel-foot">${escapeText(yTauMapRuleLabel(t0.y_tau_map))}</p>` +
    `</section>` +
    `<section class="paper-t0-spec-panel is-sizing">` +
    specPanelTitle("size", "目标价缩放") +
    buildSizingGaugeHtml(t0) +
    `<p class="paper-t0-spec-panel-foot">${escapeText(adaptiveSizingRuleLabel(t0))} · ŷ 信心</p>` +
    `</section>` +
    `</div>` +
    (notes.length
      ? `<ul class="paper-t0-rules-notes">${notes
          .map((n) => `<li>${escapeText(String(n))}</li>`)
          .join("")}</ul>`
      : "") +
    `</div>`
  );
}

/** 回测窗不进 ExecutionSpec；用 localStorage 跨刷新记住。 */
const T0_LOOKBACK_KEY = "paper.t0.lookback";
const T0_LOOKBACK_MIGRATE_KEY = "paper.t0.lookback.migrated_v2";

function _clampLookback(n) {
  return Math.max(10, Math.min(Math.round(Number(n)), 500));
}

/** 读表单回测窗并写入 localStorage；无有效值时返回 null。 */
export function persistT0Lookback(root) {
  const el = root && root.querySelector('[name="lookback"]');
  if (!el || el.value === "") return null;
  const n = Number(el.value);
  if (!Number.isFinite(n)) return null;
  const lookback = _clampLookback(n);
  try {
    localStorage.setItem(T0_LOOKBACK_KEY, String(lookback));
  } catch (_) {
    /* ignore */
  }
  return lookback;
}

function _restoreT0Lookback(root) {
  try {
    // 旧默认曾误升到 90：只清一次
    if (!localStorage.getItem(T0_LOOKBACK_MIGRATE_KEY)) {
      if (localStorage.getItem(T0_LOOKBACK_KEY) === "90") localStorage.removeItem(T0_LOOKBACK_KEY);
      localStorage.setItem(T0_LOOKBACK_MIGRATE_KEY, "1");
    }
    const saved = localStorage.getItem(T0_LOOKBACK_KEY);
    const n = saved != null ? Number(saved) : NaN;
    const el = root && root.querySelector('[name="lookback"]');
    if (!el) return;
    if (Number.isFinite(n)) el.value = String(_clampLookback(n));
    else if (el.value === "" || !Number.isFinite(Number(el.value))) el.value = "30";
  } catch (_) {
    /* ignore */
  }
}

/** 用生效 execution 填充表单控件。 */
export function fillExecutionForm(root, execution) {
  if (!root || !execution || !execution.t0) return;
  const t0 = execution.t0;
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const set = (name, val) => {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el) return;
    if (el.type === "checkbox") {
      el.checked = !!val;
      return;
    }
    if (val == null) return;
    el.value = String(val);
  };
  set("enabled", t0.enabled !== false);
  set(
    "sell_trigger_pct_long",
    t0.sell_trigger_pct_long != null ? t0.sell_trigger_pct_long : t0.sell_trigger_pct
  );
  set(
    "buy_trigger_pct_long",
    t0.buy_trigger_pct_long != null ? t0.buy_trigger_pct_long : t0.buy_trigger_pct
  );
  set(
    "sell_trigger_pct_reverse",
    t0.sell_trigger_pct_reverse != null ? t0.sell_trigger_pct_reverse : t0.sell_trigger_pct
  );
  set(
    "buy_trigger_pct_reverse",
    t0.buy_trigger_pct_reverse != null ? t0.buy_trigger_pct_reverse : t0.buy_trigger_pct
  );
  set(
    "y_ratio_cut",
    t0.y_ratio_cut != null ? Math.round(Number(t0.y_ratio_cut) * 100) : 60
  );
  set(
    "y_ratio_boost_cap",
    t0.y_ratio_boost_cap != null
      ? Math.round(Number(t0.y_ratio_boost_cap) * 100)
      : 200
  );
  if (t0.min_range_pct_long != null && t0.min_range_pct_long !== "") {
    set("min_range_pct_long", t0.min_range_pct_long);
  } else if (t0.min_range_pct != null && t0.min_range_pct !== "") {
    set("min_range_pct_long", t0.min_range_pct);
  } else {
    set("min_range_pct_long", 1);
  }
  if (t0.min_range_pct_reverse != null && t0.min_range_pct_reverse !== "") {
    set("min_range_pct_reverse", t0.min_range_pct_reverse);
  } else if (t0.min_range_pct != null && t0.min_range_pct !== "") {
    set("min_range_pct_reverse", t0.min_range_pct);
  } else {
    set("min_range_pct_reverse", 1);
  }
  set("fill_mode", t0.fill_mode || "trigger");
  set("direction", "dual_y");
  set("y_tau_map", normalizeYTauMap(t0.y_tau_map));
  set("path_mode", t0.path_mode || "first_touch");
  set("use_atr", t0.use_atr === true);
  set("must_cover_same_day_long", t0.must_cover_same_day_long === true);
  set(
    "must_cover_same_day_reverse",
    (t0.must_cover_same_day_reverse ?? t0.must_cover_same_day) !== false
  );
  set("t0_vs_stance", coup);
  set("fill_mode_long", t0.fill_mode_long || t0.fill_mode || "trigger");
  set("fill_mode_reverse", t0.fill_mode_reverse || t0.fill_mode || "trigger");
  const g = dualYGateThresholds(t0);
  set(
    "y_trade_enter",
    g.tradeEnter != null && Number.isFinite(g.tradeEnter) ? g.tradeEnter : 0.01
  );
  set(
    "y_tau_enter_long",
    g.tauEnterLong != null && Number.isFinite(g.tauEnterLong) ? g.tauEnterLong : 0.01
  );
  set(
    "y_tau_enter_reverse",
    g.tauEnterReverse != null && Number.isFinite(g.tauEnterReverse)
      ? g.tauEnterReverse
      : 0.01
  );
  set("y_use_path", t0.y_use_path !== false);
  set(
    "y_path_enter_long",
    g.pathEnterLong != null && Number.isFinite(g.pathEnterLong) ? g.pathEnterLong : 0.01
  );
  set(
    "y_path_enter_reverse",
    g.pathEnterReverse != null && Number.isFinite(g.pathEnterReverse)
      ? g.pathEnterReverse
      : 0.01
  );
  set("y_path_required", !!t0.y_path_required);
  set("y_gap_tier_mode", t0.y_gap_tier_mode || "skip_opposite");
  set("y_gap_tier_pct", t0.y_gap_tier_pct != null ? t0.y_gap_tier_pct : 1.0);
  set("y_nowcast_oc_gate", t0.y_nowcast_oc_gate === true);
  set("y_path_abandon_enabled", t0.y_path_abandon_enabled !== false);
  set(
    "y_path_abandon_bars",
    t0.y_path_abandon_bars != null ? t0.y_path_abandon_bars : 12
  );
  set("y_prefix_segment_enabled", t0.y_prefix_segment_enabled !== false);
  set("y_prefix_segment_enabled_long", t0.y_prefix_segment_enabled_long !== false);
  set("y_prefix_segment_enabled_reverse", t0.y_prefix_segment_enabled_reverse !== false);
  set(
    "y_prefix_pullback_pct_long",
    t0.y_prefix_pullback_pct_long != null ? t0.y_prefix_pullback_pct_long : 0.5
  );
  set(
    "y_prefix_bounce_pct_reverse",
    t0.y_prefix_bounce_pct_reverse != null ? t0.y_prefix_bounce_pct_reverse : 0.5
  );
  set("y_eod_prior", t0.y_eod_prior != null ? t0.y_eod_prior : 0.01);
  set("y_eod_enter", t0.y_eod_enter != null ? t0.y_eod_enter : 0.01);
  set(
    "y_eod_strong",
    g.eodStrong != null && Number.isFinite(g.eodStrong) ? g.eodStrong : 2
  );
  set(
    "y_trade_strong",
    g.tradeStrong != null && Number.isFinite(g.tradeStrong) ? g.tradeStrong : 2
  );
  set("y_on_allow", t0.y_on_allow != null ? t0.y_on_allow : 0.01);
  set(
    "y_block_tau_nowcast_sign",
    t0.y_block_tau_nowcast_sign !== false
  );
  set(
    "y_nc_enter",
    g.ncEnter != null && Number.isFinite(g.ncEnter) ? g.ncEnter : 0.01
  );
  set(
    "y_nc_strong",
    g.ncStrong != null && Number.isFinite(g.ncStrong) ? g.ncStrong : 2.0
  );
  set(
    "t0_pm_degrade_long",
    t0.t0_pm_degrade_long != null ? t0.t0_pm_degrade_long : "15:00"
  );
  set(
    "t0_pm_degrade_reverse",
    t0.t0_pm_degrade_reverse ?? t0.t0_pm_degrade ?? "13:00"
  );
  set(
    "t0_pm_chase_interval_min_long",
    t0.t0_pm_chase_interval_min_long ??
      t0.t0_pm_chase_interval_min ??
      10
  );
  set(
    "t0_pm_chase_interval_min_reverse",
    t0.t0_pm_chase_interval_min_reverse ??
      t0.t0_pm_chase_interval_min ??
      10
  );
  _restoreT0Lookback(root);
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
  const cutPct = Math.max(20, Math.min(num("y_ratio_cut", 60), 100));
  let boostPct = Math.max(100, Math.min(num("y_ratio_boost_cap", 200), 200));
  if (boostPct < cutPct) boostPct = cutPct;
  const t0 = {
    enabled: chk("enabled", true),
    t0_ratio: 1.0,
    sell_trigger_pct_long: Math.max(0.1, Math.min(num("sell_trigger_pct_long", 1), 20)),
    buy_trigger_pct_long: Math.max(0.1, Math.min(num("buy_trigger_pct_long", 1), 20)),
    sell_trigger_pct_reverse: Math.max(0.1, Math.min(num("sell_trigger_pct_reverse", 1), 20)),
    buy_trigger_pct_reverse: Math.max(0.1, Math.min(num("buy_trigger_pct_reverse", 1), 20)),
    // 兜底键：取两侧较松卖/买，兼容旧读端
    sell_trigger_pct: Math.max(
      0.1,
      Math.min(
        Math.min(num("sell_trigger_pct_long", 1), num("sell_trigger_pct_reverse", 1)),
        20
      )
    ),
    buy_trigger_pct: Math.max(
      0.1,
      Math.min(
        Math.min(num("buy_trigger_pct_long", 1), num("buy_trigger_pct_reverse", 1)),
        20
      )
    ),
    y_ratio_cut: Math.max(0.2, Math.min(cutPct / 100, 1)),
    y_ratio_boost_cap: Math.max(1.0, Math.min(boostPct / 100, 2)),
    fill_mode_long: str("fill_mode_long", "trigger"),
    fill_mode_reverse: str("fill_mode_reverse", "trigger"),
    fill_mode: str("fill_mode_long", "trigger"),
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(str("y_tau_map", "trend")),
    path_mode: str("path_mode", "first_touch"),
    use_atr: chk("use_atr", false),
    must_cover_same_day_long: chk("must_cover_same_day_long", false),
    must_cover_same_day_reverse: chk("must_cover_same_day_reverse", true),
    must_cover_same_day: chk("must_cover_same_day_reverse", true),
    y_trade_enter: Math.max(0.01, Math.min(num("y_trade_enter", 0.02), 5)),
    y_trade_strong: Math.max(0.05, Math.min(num("y_trade_strong", 0.1), 5)),
    y_trade_floor: Math.max(0.01, Math.min(num("y_trade_enter", 0.02), 5)),
    y_tau_enter_long: Math.max(0.01, Math.min(num("y_tau_enter_long", 0.02), 5)),
    y_tau_enter_reverse: Math.max(
      0.01,
      Math.min(num("y_tau_enter_reverse", 0.02), 5)
    ),
    // 兜底键 = min(正,反)，兼容旧读端
    y_tau_enter: Math.max(
      0.01,
      Math.min(
        Math.min(num("y_tau_enter_long", 0.02), num("y_tau_enter_reverse", 0.02)),
        5
      )
    ),
    y_tau_enter_strong: Math.max(
      0.01,
      Math.min(
        Math.min(num("y_tau_enter_long", 0.02), num("y_tau_enter_reverse", 0.02)),
        5
      )
    ),
    y_use_path: chk("y_use_path", true),
    y_path_enter_long: Math.max(0.01, Math.min(num("y_path_enter_long", 0.02), 5)),
    y_path_enter_reverse: Math.max(
      0.01,
      Math.min(num("y_path_enter_reverse", 0.02), 5)
    ),
    y_path_enter: Math.max(
      0.01,
      Math.min(
        Math.min(num("y_path_enter_long", 0.02), num("y_path_enter_reverse", 0.02)),
        5
      )
    ),
    y_path_required: chk("y_path_required", false),
    y_gap_tier_mode: str("y_gap_tier_mode", "skip_opposite"),
    y_gap_tier_pct: Math.max(0.5, Math.min(num("y_gap_tier_pct", 1.0), 10)),
    y_nowcast_oc_gate: chk("y_nowcast_oc_gate", false),
    y_path_abandon_enabled: chk("y_path_abandon_enabled", true),
    y_path_abandon_bars: Math.max(
      2,
      Math.min(Math.round(num("y_path_abandon_bars", 12)), 48)
    ),
    y_prefix_segment_enabled: chk("y_prefix_segment_enabled", true),
    y_prefix_segment_enabled_long: chk("y_prefix_segment_enabled_long", true),
    y_prefix_segment_enabled_reverse: chk("y_prefix_segment_enabled_reverse", true),
    y_prefix_pullback_pct_long: Math.max(
      0,
      Math.min(num("y_prefix_pullback_pct_long", 0.25), 2)
    ),
    y_prefix_bounce_pct_reverse: Math.max(
      0,
      Math.min(num("y_prefix_bounce_pct_reverse", 0.25), 2)
    ),
    y_eod_prior: Math.max(0.01, Math.min(num("y_eod_prior", 0.02), 5)),
    y_eod_enter: Math.max(0.01, Math.min(num("y_eod_enter", 0.01), 5)),
    y_eod_strong: Math.max(0.05, Math.min(num("y_eod_strong", 0.1), 5)),
    y_eod_tau_sign_gate: Math.max(0.05, Math.min(num("y_eod_strong", 0.1), 5)),
    y_trade_tau_sign_gate: Math.max(0.05, Math.min(num("y_trade_strong", 0.1), 5)),
    y_on_allow: Math.max(0.01, Math.min(num("y_on_allow", 0.02), 10)),
    y_block_tau_nowcast_sign: chk("y_block_tau_nowcast_sign", true),
    y_nc_enter: Math.max(0.01, Math.min(num("y_nc_enter", 0.01), 10)),
    y_nc_strong: Math.max(0.05, Math.min(num("y_nc_strong", 3.0), 10)),
    y_nowcast_enter: Math.max(0.05, Math.min(num("y_nc_strong", 3.0), 10)),
    t0_pm_degrade_long: str("t0_pm_degrade_long", "15:00"),
    t0_pm_degrade_reverse: str("t0_pm_degrade_reverse", "14:00"),
    t0_pm_degrade: str("t0_pm_degrade_reverse", "14:00"),
    t0_pm_chase_interval_min_long: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_long", 10)), 60)
    ),
    t0_pm_chase_interval_min_reverse: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_reverse", 10)), 60)
    ),
    t0_pm_chase_interval_min: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min_reverse", 10)), 60)
    ),
  };
  const minRangeLong = numOrNull("min_range_pct_long");
  const minRangeRev = numOrNull("min_range_pct_reverse");
  if (minRangeLong != null) {
    t0.min_range_pct_long = Math.max(0.1, Math.min(minRangeLong, 30));
  }
  if (minRangeRev != null) {
    t0.min_range_pct_reverse = Math.max(0.1, Math.min(minRangeRev, 30));
  }
  if (t0.min_range_pct_long != null || t0.min_range_pct_reverse != null) {
    const a = t0.min_range_pct_long != null ? t0.min_range_pct_long : 0.1;
    const b = t0.min_range_pct_reverse != null ? t0.min_range_pct_reverse : 0.1;
    t0.min_range_pct = Math.min(a, b);
  }
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
  const lookback = persistT0Lookback(root) ?? 30;
  const body = {
    from_paper: true,
    lookback,
    compare_optimistic: true,
    use_minute: true,
    compare_daily: false,
    t0_ratio: 1.0,
    sell_trigger_pct: t0.sell_trigger_pct != null ? t0.sell_trigger_pct : 1,
    buy_trigger_pct: t0.buy_trigger_pct != null ? t0.buy_trigger_pct : 1,
    sell_trigger_pct_long:
      t0.sell_trigger_pct_long != null ? t0.sell_trigger_pct_long : t0.sell_trigger_pct != null ? t0.sell_trigger_pct : 1,
    buy_trigger_pct_long:
      t0.buy_trigger_pct_long != null ? t0.buy_trigger_pct_long : t0.buy_trigger_pct != null ? t0.buy_trigger_pct : 5,
    sell_trigger_pct_reverse:
      t0.sell_trigger_pct_reverse != null
        ? t0.sell_trigger_pct_reverse
        : t0.sell_trigger_pct != null
          ? t0.sell_trigger_pct
          : 5,
    buy_trigger_pct_reverse:
      t0.buy_trigger_pct_reverse != null
        ? t0.buy_trigger_pct_reverse
        : t0.buy_trigger_pct != null
          ? t0.buy_trigger_pct
          : 1,
    y_ratio_cut: t0.y_ratio_cut != null ? t0.y_ratio_cut : 0.6,
    y_ratio_boost_cap: t0.y_ratio_boost_cap != null ? t0.y_ratio_boost_cap : 2.0,
    fill_mode: t0.fill_mode || "trigger",
    fill_mode_long: t0.fill_mode_long || t0.fill_mode || "trigger",
    fill_mode_reverse: t0.fill_mode_reverse || t0.fill_mode || "trigger",
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(t0.y_tau_map),
    path_mode: "first_touch",
    must_cover_same_day_long: t0.must_cover_same_day_long === true,
    must_cover_same_day_reverse:
      (t0.must_cover_same_day_reverse ?? t0.must_cover_same_day) !== false,
    must_cover_same_day:
      (t0.must_cover_same_day_reverse ?? t0.must_cover_same_day) !== false,
    use_atr: t0.use_atr === true,
    y_trade_enter: t0.y_trade_enter != null ? t0.y_trade_enter : t0.y_trade_floor != null ? t0.y_trade_floor : 0.01,
    y_trade_strong:
      t0.y_trade_strong != null
        ? t0.y_trade_strong
        : t0.y_trade_tau_sign_gate != null
          ? t0.y_trade_tau_sign_gate
          : 2,
    y_trade_floor:
      t0.y_trade_enter != null
        ? t0.y_trade_enter
        : t0.y_trade_floor != null
          ? t0.y_trade_floor
          : 0.01,
    y_tau_enter: t0.y_tau_enter != null ? t0.y_tau_enter : 0.01,
    y_tau_enter_long:
      t0.y_tau_enter_long != null
        ? t0.y_tau_enter_long
        : t0.y_tau_enter != null
          ? t0.y_tau_enter
          : 0.01,
    y_tau_enter_reverse:
      t0.y_tau_enter_reverse != null
        ? t0.y_tau_enter_reverse
        : t0.y_tau_enter != null
          ? t0.y_tau_enter
          : 0.01,
    y_use_path: t0.y_use_path !== false,
    y_path_enter: t0.y_path_enter != null ? t0.y_path_enter : 0.01,
    y_path_enter_long:
      t0.y_path_enter_long != null
        ? t0.y_path_enter_long
        : t0.y_path_enter != null
          ? t0.y_path_enter
          : 0.01,
    y_path_enter_reverse:
      t0.y_path_enter_reverse != null
        ? t0.y_path_enter_reverse
        : t0.y_path_enter != null
          ? t0.y_path_enter
          : 0.01,
    y_path_required: !!t0.y_path_required,
    y_gap_tier_mode: t0.y_gap_tier_mode || "skip_opposite",
    y_gap_tier_pct: t0.y_gap_tier_pct != null ? t0.y_gap_tier_pct : 1.0,
    y_nowcast_oc_gate: t0.y_nowcast_oc_gate === true,
    y_path_abandon_enabled: t0.y_path_abandon_enabled !== false,
    y_path_abandon_bars:
      t0.y_path_abandon_bars != null ? t0.y_path_abandon_bars : 12,
    y_prefix_segment_enabled: t0.y_prefix_segment_enabled !== false,
    y_prefix_segment_enabled_long: t0.y_prefix_segment_enabled_long !== false,
    y_prefix_segment_enabled_reverse: t0.y_prefix_segment_enabled_reverse !== false,
    y_prefix_pullback_pct_long:
      t0.y_prefix_pullback_pct_long != null ? t0.y_prefix_pullback_pct_long : 0.5,
    y_prefix_bounce_pct_reverse:
      t0.y_prefix_bounce_pct_reverse != null ? t0.y_prefix_bounce_pct_reverse : 0.5,
    y_eod_prior: t0.y_eod_prior != null ? t0.y_eod_prior : 0.01,
    y_eod_enter: t0.y_eod_enter != null ? t0.y_eod_enter : 0.01,
    y_eod_strong:
      t0.y_eod_strong != null
        ? t0.y_eod_strong
        : t0.y_eod_tau_sign_gate != null
          ? t0.y_eod_tau_sign_gate
          : 2,
    y_eod_tau_sign_gate:
      t0.y_eod_strong != null
        ? t0.y_eod_strong
        : t0.y_eod_tau_sign_gate != null
          ? t0.y_eod_tau_sign_gate
          : 2,
    y_trade_tau_sign_gate:
      t0.y_trade_strong != null
        ? t0.y_trade_strong
        : t0.y_trade_tau_sign_gate != null
          ? t0.y_trade_tau_sign_gate
          : 2,
    y_on_allow: t0.y_on_allow != null ? t0.y_on_allow : 0.01,
    y_block_tau_nowcast_sign: t0.y_block_tau_nowcast_sign !== false,
    y_nc_enter: t0.y_nc_enter != null ? t0.y_nc_enter : 0.01,
    y_nc_strong:
      t0.y_nc_strong != null
        ? t0.y_nc_strong
        : t0.y_nowcast_enter != null
          ? t0.y_nowcast_enter
          : 2,
    y_nowcast_enter:
      t0.y_nc_strong != null
        ? t0.y_nc_strong
        : t0.y_nowcast_enter != null
          ? t0.y_nowcast_enter
          : 2,
    t0_pm_degrade_long: t0.t0_pm_degrade_long != null ? t0.t0_pm_degrade_long : "15:00",
    t0_pm_degrade_reverse:
      t0.t0_pm_degrade_reverse ?? t0.t0_pm_degrade ?? "13:00",
    t0_pm_degrade: t0.t0_pm_degrade_reverse ?? t0.t0_pm_degrade ?? "13:00",
    t0_pm_chase_interval_min_long:
      t0.t0_pm_chase_interval_min_long ??
      t0.t0_pm_chase_interval_min ??
      10,
    t0_pm_chase_interval_min_reverse:
      t0.t0_pm_chase_interval_min_reverse ??
      t0.t0_pm_chase_interval_min ??
      10,
    t0_pm_chase_interval_min:
      t0.t0_pm_chase_interval_min_reverse ??
      t0.t0_pm_chase_interval_min ??
      10,
  };
  if (t0.min_range_pct_long != null) body.min_range_pct_long = t0.min_range_pct_long;
  if (t0.min_range_pct_reverse != null) body.min_range_pct_reverse = t0.min_range_pct_reverse;
  if (t0.min_range_pct != null) body.min_range_pct = t0.min_range_pct;
  else body.min_range_pct = 0.1;
  if (opts.onlySelected && opts.selectedCode) body.code = opts.selectedCode;
  return body;
}

export function renderExecutionDiffHtml(diff) {
  if (!diff || !diff.ok) {
    return `<p class="quant-sub">${escapeText((diff && diff.error) || "无 diff")}</p>`;
  }
  if (!diff.changed) {
    return `<p class="quant-sub">与策略默认一致</p>`;
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
