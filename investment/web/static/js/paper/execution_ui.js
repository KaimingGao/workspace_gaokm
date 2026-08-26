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

const COUPLING_LABELS = {
  independent: "独立",
  skip_if_avoid: "avoid 时跳过",
  only_if_hold: "仅 hold/watch",
};

function fmtThresholdPct(val, fallback) {
  const n = Number(val ?? fallback);
  if (!Number.isFinite(n)) return "—";
  const s = Math.abs(n) >= 1 ? n.toFixed(1) : n.toFixed(2);
  return `≥${s}%`;
}

function fmtTriggerPair(sell, buy) {
  const s = Number(sell);
  const b = Number(buy);
  if (!Number.isFinite(s) || !Number.isFinite(b)) return "—";
  return `卖 +${s}% · 买 −${b}%`;
}

/** dual_y 目标价缩放区间（规则卡 gauge / 表注）；单位=触发幅度相对基准的 %。 */
export function adaptiveSizingBounds(t0 = {}) {
  const sell = Number(t0.sell_trigger_pct);
  const buy = Number(t0.buy_trigger_pct);
  const baseSell = Number.isFinite(sell) ? sell : 2;
  const baseBuy = Number.isFinite(buy) ? buy : 1.5;
  const cut = Number(t0.y_ratio_cut ?? 0.75);
  const cutSafe = Number.isFinite(cut) && cut > 0 && cut <= 1 ? cut : 0.75;
  const minSell = Math.max(0.1, +(baseSell * cutSafe).toFixed(2));
  const minBuy = Math.max(0.1, +(baseBuy * cutSafe).toFixed(2));
  const basePct = Math.round(
    Number.isFinite(Number(t0.t0_ratio)) ? Number(t0.t0_ratio) * 100 : 100
  );
  // gauge 用「相对基准」0–100：满目标=100，弱信号≈cut*100
  const maxPct = 100;
  const minPct = Math.max(5, Math.round(cutSafe * 100));
  return {
    basePct: 100,
    minPct,
    maxPct,
    ratioPct: basePct,
    baseSell,
    baseBuy,
    minSell,
    minBuy,
    cut: cutSafe,
  };
}

/** dual_y 目标价缩放只读摘要（规则卡 / 表注）。 */
export function adaptiveSizingRuleLabel(t0 = {}) {
  const { baseSell, baseBuy, minSell, minBuy, cut } = adaptiveSizingBounds(t0);
  return (
    `满目标 卖+${baseSell}%/买−${baseBuy}%` +
    ` · 弱信号×${cut} → +${minSell}%/−${minBuy}%`
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
    return `目标 卖+${fmt(sellEff)}% / 买−${fmt(buyEff)}%（满目标）`;
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

function specMetric(label, value, tip = "") {
  const title = tip ? ` title="${escapeText(tip)}"` : "";
  return (
    `<div class="paper-t0-spec-metric"${title}>` +
    `<span class="paper-t0-spec-metric-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-spec-metric-v">${escapeText(String(value))}</span>` +
    `</div>`
  );
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
  const { basePct, minPct, maxPct, baseSell, baseBuy, minSell, minBuy } = adaptiveSizingBounds(t0);
  const bandLeft = Math.max(0, minPct);
  const bandWidth = Math.max(2, maxPct - bandLeft);
  const tip =
    `满目标 卖+${baseSell}% / 买−${baseBuy}%；` +
    `弱信号压至约 +${minSell}% / −${minBuy}%（ŷ 信心）`;
  return (
    `<div class="paper-t0-spec-panel-viz">` +
    specSubhead("满目标", `+${baseSell}% / −${baseBuy}%`) +
    `<div class="paper-t0-spec-panel-body is-gauge" title="${escapeText(tip)}">` +
    `<div class="paper-t0-spec-gauge-wrap">` +
    `<div class="paper-t0-spec-gauge-track" aria-hidden="true">` +
    `<span class="paper-t0-spec-gauge-band" style="left:${bandLeft}%;width:${bandWidth}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-min" style="left:${minPct}%" title="弱信号 ${minPct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-base" style="left:${basePct}%" title="满目标 ${basePct}%"></span>` +
    `<span class="paper-t0-spec-gauge-mark is-max" style="left:${maxPct}%" title="上限 ${maxPct}%"></span>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-ticks" aria-hidden="true">` +
    `<span class="is-edge" style="left:0%">0</span>` +
    `<span class="is-min" style="left:${minPct}%">${minPct}</span>` +
    `<span class="is-base" style="left:${basePct}%">${basePct}</span>` +
    `<span class="is-max" style="left:${maxPct}%">${maxPct}</span>` +
    `<span class="is-edge" style="left:100%">100</span>` +
    `</div>` +
    `</div>` +
    `<div class="paper-t0-spec-gauge-legend">` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-band"></i>有效区间</span>` +
    `<span class="paper-t0-spec-gauge-key"><i class="dot is-base"></i>满目标</span>` +
    `<span class="paper-t0-spec-gauge-key is-range">${minPct}–${maxPct}%</span>` +
    `</div>` +
    `</div></div>`
  );
}

function buildDualYPipelineHtml(t0) {
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || "5m";
  const pathSub =
    t0.y_use_path !== false
      ? `|y_p|≥${fmtThresholdPct(t0.y_path_enter, 30)}`
      : pathLbl;
  const pathTip =
    t0.y_use_path !== false
      ? "ŷ_path 入场闸：|y_p|≥门槛才做 T；达门槛后与 τ 冲突则否决"
      : "分钟路径首触达";
  const nodes = [
    { stage: "trade", role: "资格", label: "trade", sub: fmtThresholdPct(t0.y_trade_floor, 0.01), tip: "ŷ_trade 幅度门槛" },
    { stage: "tau", role: "方向", label: "τ", sub: fmtThresholdPct(t0.y_tau_enter_strong ?? t0.y_tau_enter, 0.6), tip: "ŷ_τ 定正/反 T（弱信号区跳过）" },
    { stage: "path", role: "路径", label: "path", sub: pathSub, tip: pathTip },
    { stage: "eod", role: "先验", label: "eod", sub: fmtThresholdPct(t0.y_eod_prior, 0.01), tip: "ŷ_eod 同向略抬目标价信心" },
    { stage: "on", role: "回补", label: "on", sub: fmtThresholdPct(t0.y_on_allow, 0.01), tip: "ŷ_on 收盘回补" },
  ];
  return nodes
    .map(
      (n, i) =>
        `<div class="paper-t0-spec-pipe-node" data-stage="${escapeText(n.stage)}" title="${escapeText(n.tip)}">` +
        `<span class="paper-t0-spec-pipe-step">${String(i + 1).padStart(2, "0")}</span>` +
        `<span class="paper-t0-spec-pipe-role">${escapeText(n.role)}</span>` +
        `<span class="paper-t0-spec-pipe-label">${escapeText(n.label)}</span>` +
        `<span class="paper-t0-spec-pipe-sub">${escapeText(n.sub)}</span>` +
        `</div>` +
        (i < nodes.length - 1 ? `<span class="paper-t0-spec-pipe-join" aria-hidden="true">›</span>` : "")
    )
    .join("");
}

export function renderExecutionRulesHtml(execution) {
  if (!execution || execution.ok === false) {
    const err = (execution && execution.error) || "无法加载 ExecutionSpec";
    return `<p class="quant-sub">${escapeText(err)}</p>`;
  }
  const t0 = execution.t0 || {};
  const coup = (execution.coupling && execution.coupling.t0_vs_stance) || "independent";
  const hash = execution.effective_hash || "";
  const { ratioPct } = adaptiveSizingBounds(t0);
  const rangeLbl =
    t0.min_range_pct != null && t0.min_range_pct !== ""
      ? fmtThresholdPct(t0.min_range_pct, null)
      : "自动";
  const fillLbl = FILL_MODE_LABELS[String(t0.fill_mode || "trigger").toLowerCase()] || t0.fill_mode || "—";
  const pathLbl =
    PATH_MODE_LABELS[String(t0.path_mode || "first_touch").toLowerCase()] || t0.path_mode || "—";
  const coupLbl = COUPLING_LABELS[String(coup).toLowerCase()] || coup;
  const triggerLbl = fmtTriggerPair(t0.sell_trigger_pct, t0.buy_trigger_pct);
  const coverLbl = t0.must_cover_same_day ? "收盘强制" : "y_on 策略";
  const atrLbl = t0.use_atr ? `开 · ${t0.atr_window ?? 14}日` : "关";
  const pmLbl =
    t0.t0_pm_degrade != null && String(t0.t0_pm_degrade).trim()
      ? String(t0.t0_pm_degrade).trim()
      : "关";
  const chaseIv =
    t0.t0_pm_chase_interval_min != null && Number.isFinite(Number(t0.t0_pm_chase_interval_min))
      ? Math.max(1, Math.min(Math.round(Number(t0.t0_pm_chase_interval_min)), 60))
      : 10;
  const pmChaseLbl = pmLbl === "关" ? "关" : `${pmLbl} · ${chaseIv}m`;
  const signBlkLbl = t0.y_block_tau_nowcast_sign !== false ? "开" : "关";
  const pathUseLbl = t0.y_use_path !== false ? "开" : "关";
  const pathEnterLbl =
    t0.y_use_path !== false ? fmtThresholdPct(t0.y_path_enter, 30) : "—";
  const gapTierLbl =
    t0.y_gap_tier_mode && String(t0.y_gap_tier_mode).toLowerCase() !== "off"
      ? `${t0.y_gap_tier_mode} · ${fmtThresholdPct(t0.y_gap_tier_pct, 1.5)}`
      : "关";
  const abandonLbl =
    t0.y_path_abandon_enabled !== false
      ? `${t0.y_path_abandon_bars != null ? t0.y_path_abandon_bars : 6}×5m`
      : "关";
  const ncOcLbl = t0.y_nowcast_oc_gate !== false ? "OC" : "原值";
  const pathModelOk = execution.path_model_present !== false;
  const pathModelWarn =
    t0.y_use_path !== false && !pathModelOk
      ? "⚠ ŷ_path 模型未就绪 · /quant 拟合/启用"
      : execution.path_model_shadow
        ? "影子模型（未 promote）"
        : null;

  const notes = (execution.notes || []).slice(0, 2);
  const hashShort = hash ? String(hash).slice(0, 10) : "";

  const headMeta = `dual_y · ${pathLbl} · ${fillLbl}${
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
    specKpi("策略", "dual_y", "多层 ŷ 门控") +
    specKpi("动仓", `${ratioPct}%`, "固定底仓比例（不随 ŷ 缩放）") +
    specKpi("路径", pathLbl, "分钟触价路径") +
    specKpi("成交", fillLbl, "撮合假设") +
    `</div></header>` +
    `<section class="paper-t0-spec-block">` +
    `<h5 class="paper-t0-spec-block-title">决策链</h5>` +
    `<div class="paper-t0-spec-pipeline" aria-label="dual_y 决策链">` +
    buildDualYPipelineHtml(t0) +
    `</div></section>` +
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
    `<section class="paper-t0-spec-block is-exec">` +
    `<h5 class="paper-t0-spec-block-title">执行参数</h5>` +
    `<div class="paper-t0-spec-metrics">` +
    specMetric("触发", triggerLbl, "卖高 / 买低基准；当日按 ŷ 信心压低") +
    specMetric("振幅", rangeLbl, "日振幅下限") +
    specMetric("回补", coverLbl, "收盘强制或 y_on") +
    specMetric("ATR", atrLbl, "波动率自适应触发") +
    specMetric("中点追价", pmChaseLbl, "起算后禁新开 · 中点再触价 · 未触达则收盘平") +
    specMetric("y_path", `${pathUseLbl} · ${pathEnterLbl}`, "ŷ_path 入场门槛；达门槛后与 τ 冲突则否决") +
    (pathModelWarn
      ? specMetric("path模型", pathModelWarn, "研究枢纽 ŷ_path 状态")
      : pathModelOk && t0.y_use_path !== false
        ? specMetric("path模型", execution.path_model_shadow ? "影子" : "已就绪", "path_ridge 推理可用")
        : "") +
    specMetric("缺口分档", gapTierLbl, "大缺口 skip/revert") +
    specMetric("前缀放弃", abandonLbl, "前 N 根 5m 无空间则放弃") +
    specMetric("异号跳过", signBlkLbl, `y_τ↔y_nowcast 异号则跳过（${ncOcLbl}；缺 nowcast 不拦）`) +
    specMetric("stance", coupLbl, "与 stance 耦合") +
    `</div></section>` +
    (execution.summary || hashShort
      ? `<footer class="paper-t0-spec-foot">` +
        (execution.summary ? `<span class="paper-t0-spec-foot-summary">${escapeText(execution.summary)}</span>` : "") +
        (hashShort
          ? `<span class="paper-t0-rules-hash">hash ${escapeText(hashShort)}</span>`
          : "") +
        `</footer>`
      : "") +
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
    else if (el.value === "" || !Number.isFinite(Number(el.value))) el.value = "10";
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
    if (!el || val == null) return;
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = String(val);
  };
  set("enabled", t0.enabled !== false);
  set("t0_ratio", t0.t0_ratio != null ? Math.round(Number(t0.t0_ratio) * 100) : 100);
  set("sell_trigger_pct", t0.sell_trigger_pct);
  set("buy_trigger_pct", t0.buy_trigger_pct);
  if (t0.min_range_pct != null && t0.min_range_pct !== "") {
    set("min_range_pct", t0.min_range_pct);
  } else {
    set("min_range_pct", 0.5);
  }
  set("fill_mode", t0.fill_mode || "trigger");
  set("direction", "dual_y");
  set("y_tau_map", normalizeYTauMap(t0.y_tau_map));
  set("path_mode", t0.path_mode || "first_touch");
  set("use_atr", t0.use_atr !== false);
  set("must_cover_same_day", !!t0.must_cover_same_day);
  set("t0_vs_stance", coup);
  set("y_trade_floor", t0.y_trade_floor != null ? t0.y_trade_floor : 0.01);
  set("y_tau_enter", t0.y_tau_enter != null ? t0.y_tau_enter : 0.4);
  set("y_tau_enter_strong", t0.y_tau_enter_strong != null ? t0.y_tau_enter_strong : 0.6);
  set("y_use_path", t0.y_use_path !== false);
  set("y_path_enter", t0.y_path_enter != null ? t0.y_path_enter : 30);
  set("y_path_required", !!t0.y_path_required);
  set("y_gap_tier_mode", t0.y_gap_tier_mode || "skip_opposite");
  set("y_gap_tier_pct", t0.y_gap_tier_pct != null ? t0.y_gap_tier_pct : 1.5);
  set("y_nowcast_oc_gate", t0.y_nowcast_oc_gate !== false);
  set("y_path_abandon_enabled", t0.y_path_abandon_enabled !== false);
  set(
    "y_path_abandon_bars",
    t0.y_path_abandon_bars != null ? t0.y_path_abandon_bars : 6
  );
  set("y_eod_prior", t0.y_eod_prior != null ? t0.y_eod_prior : 0.01);
  set("y_on_allow", t0.y_on_allow != null ? t0.y_on_allow : 0.01);
  set(
    "y_block_tau_nowcast_sign",
    t0.y_block_tau_nowcast_sign !== false
  );
  set("t0_pm_degrade", t0.t0_pm_degrade != null ? t0.t0_pm_degrade : "14:00");
  set(
    "t0_pm_chase_interval_min",
    t0.t0_pm_chase_interval_min != null ? t0.t0_pm_chase_interval_min : 10
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
  const ratioPct = num("t0_ratio", 100);
  const t0 = {
    enabled: chk("enabled", true),
    t0_ratio: Math.max(0.05, Math.min(ratioPct / 100, 1)),
    sell_trigger_pct: num("sell_trigger_pct", 2),
    buy_trigger_pct: num("buy_trigger_pct", 1.5),
    fill_mode: str("fill_mode", "trigger"),
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(str("y_tau_map", "trend")),
    path_mode: str("path_mode", "first_touch"),
    use_atr: chk("use_atr", true),
    must_cover_same_day: chk("must_cover_same_day", false),
    y_trade_floor: Math.max(0.01, Math.min(num("y_trade_floor", 0.01), 5)),
    y_tau_enter: Math.max(0.01, Math.min(num("y_tau_enter", 0.4), 5)),
    y_tau_enter_strong: Math.max(0.01, Math.min(num("y_tau_enter_strong", 0.6), 5)),
    y_use_path: chk("y_use_path", true),
    y_path_enter: Math.max(1, Math.min(num("y_path_enter", 30), 100)),
    y_path_required: chk("y_path_required", false),
    y_gap_tier_mode: str("y_gap_tier_mode", "skip_opposite"),
    y_gap_tier_pct: Math.max(0.5, Math.min(num("y_gap_tier_pct", 1.5), 10)),
    y_nowcast_oc_gate: chk("y_nowcast_oc_gate", true),
    y_path_abandon_enabled: chk("y_path_abandon_enabled", true),
    y_path_abandon_bars: Math.max(
      2,
      Math.min(Math.round(num("y_path_abandon_bars", 6)), 48)
    ),
    y_eod_prior: Math.max(0.01, Math.min(num("y_eod_prior", 0.01), 5)),
    y_on_allow: Math.max(0.01, Math.min(num("y_on_allow", 0.01), 10)),
    y_block_tau_nowcast_sign: chk("y_block_tau_nowcast_sign", true),
    t0_pm_degrade: str("t0_pm_degrade", "14:00"),
    t0_pm_chase_interval_min: Math.max(
      1,
      Math.min(Math.round(num("t0_pm_chase_interval_min", 10)), 60)
    ),
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
  const lookback = persistT0Lookback(root) ?? 10;
  const body = {
    from_paper: true,
    lookback,
    compare_optimistic: true,
    use_minute: true,
    compare_daily: false,
    t0_ratio: t0.t0_ratio != null ? t0.t0_ratio : 1.0,
    sell_trigger_pct: t0.sell_trigger_pct != null ? t0.sell_trigger_pct : 2,
    buy_trigger_pct: t0.buy_trigger_pct != null ? t0.buy_trigger_pct : 1.5,
    fill_mode: t0.fill_mode || "trigger",
    direction: "dual_y",
    y_tau_map: normalizeYTauMap(t0.y_tau_map),
    path_mode: "first_touch",
    must_cover_same_day: !!t0.must_cover_same_day,
    use_atr: t0.use_atr !== false,
    y_trade_floor: t0.y_trade_floor != null ? t0.y_trade_floor : 0.01,
    y_tau_enter: t0.y_tau_enter != null ? t0.y_tau_enter : 0.4,
    y_tau_enter_strong: t0.y_tau_enter_strong != null ? t0.y_tau_enter_strong : 0.6,
    y_use_path: t0.y_use_path !== false,
    y_path_enter: t0.y_path_enter != null ? t0.y_path_enter : 30,
    y_path_required: !!t0.y_path_required,
    y_gap_tier_mode: t0.y_gap_tier_mode || "skip_opposite",
    y_gap_tier_pct: t0.y_gap_tier_pct != null ? t0.y_gap_tier_pct : 1.5,
    y_nowcast_oc_gate: t0.y_nowcast_oc_gate !== false,
    y_path_abandon_enabled: t0.y_path_abandon_enabled !== false,
    y_path_abandon_bars:
      t0.y_path_abandon_bars != null ? t0.y_path_abandon_bars : 6,
    y_eod_prior: t0.y_eod_prior != null ? t0.y_eod_prior : 0.01,
    y_on_allow: t0.y_on_allow != null ? t0.y_on_allow : 0.01,
    y_block_tau_nowcast_sign: t0.y_block_tau_nowcast_sign !== false,
    t0_pm_degrade: t0.t0_pm_degrade != null ? t0.t0_pm_degrade : "14:00",
    t0_pm_chase_interval_min:
      t0.t0_pm_chase_interval_min != null ? t0.t0_pm_chase_interval_min : 10,
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
