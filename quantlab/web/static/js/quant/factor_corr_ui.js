import { apiFetch } from "../api_client.js";
import { escapeHtml } from "../shared.js";

/* ===== 因子中文名映射（与 core.signal.factors.meta.registry 同步） ===== */
const FACTOR_CN = {
  momentum: "动量",
  volume_price: "量价",
  volatility: "波动",
  relative_strength: "相对强弱",
  reversal: "反转",
  liquidity: "流动性",
  value: "估值",
  quality: "质量",
  technical_pattern: "技术形态",
  weekly_confirm: "周线确认",
  ma_slope: "均线斜率",
  gap_risk: "跳空风险",
  size: "规模",
  earnings_yield: "盈利收益率",
  growth: "成长",
  dividend: "股息",
  money_flow: "资金流",
  amihud: "非流动性",
  idio_momentum: "特异动量",
  overheat: "过热",
};
function factorCN(name) {
  return FACTOR_CN[name] || name;
}

/* ===== Quant Pro UI Helpers ===== */

const PATH_CHIP_HREF = {
  "quant-pro-cluster-status": "#quant-section-factors",
  "quant-pro-tau-status": "#quant-section-tau",
};

function syncPathStepFromChip(chipId, state) {
  const href = PATH_CHIP_HREF[chipId];
  if (!href) return;
  const step = document.querySelector(`.quant-path-rail a.quant-path-step[href="${href}"]`);
  if (!step) return;
  const pathState =
    state === "ok" ? "done" : state === "busy" ? "busy" : state === "warn" ? "warn" : state === "error" ? "error" : "idle";
  step.dataset.state = pathState;
}

/** 设置状态徽章 chip 状态：idle | busy | ok | warn | error
 *  chip 元素已移除，仅保留 path rail 同步 */
export function setProStatusChip(idOrEl, state, text) {
  const chipId = typeof idOrEl === "string" ? idOrEl : idOrEl?.id;
  if (chipId) syncPathStepFromChip(chipId, state);
  const el = typeof idOrEl === "string" ? document.getElementById(idOrEl) : idOrEl;
  if (!el) return;
  el.dataset.state = state;
  if (text != null) el.textContent = text;
}

/** 更新顶部全景 KPI 的单张卡片
 * @param {string} key
 * @param {string|null|undefined} valueText
 * @param {string|null|undefined} subText
 * @param {string} [state] is-good | is-bad | is-mid | is-empty | ''
 * @param {{ signed?: boolean }} [opts] signed=true → A 股红涨绿跌（IC 等）
 */
function setProOverviewKpi(key, valueText, subText, state, opts = {}) {
  const host = document.getElementById("quant-pro-overview-kpis");
  if (!host) return;
  const card = host.querySelector(`.quant-pro-kpi-card[data-kpi="${key}"]`);
  if (!card) return;
  card.classList.remove("is-good", "is-bad", "is-mid", "is-empty");
  if (state) card.classList.add(state);
  else if (valueText != null && valueText !== "—") card.classList.add("is-mid");
  // eod 固定是 IC；其它卡仅在显式 signed 时走涨跌色
  card.classList.toggle("is-signed", !!opts.signed || key === "eod");
  const valEl = card.querySelector(".quant-pro-kpi-value");
  const subEl = card.querySelector(".quant-pro-kpi-sub");
  if (valEl) valEl.textContent = valueText ?? "—";
  if (subEl && subText != null) subEl.textContent = subText;
}

const overviewPack = {
  watchingN: null,
  watchingHint: null,
  barsPct: null,
};

function paintOverviewWatching() {
  const n = overviewPack.watchingN;
  if (n == null || !Number.isFinite(Number(n))) return;
  const pct = overviewPack.barsPct;
  const hint = overviewPack.watchingHint;
  let sub = "观察池";
  if (pct != null && Number.isFinite(Number(pct))) {
    sub = `日线 ${Number(pct).toFixed(0)}%`;
  } else if (hint) {
    sub = hint;
  }
  setProOverviewKpi(
    "watching",
    `${Number(n)} 只`,
    sub,
    Number(n) > 0 ? "is-mid" : "is-empty"
  );
}

/** 观察池只数 → 概览 KPI（进页拉 watching 时） */
export function syncOverviewUniverse(n, subText) {
  const count = Number(n);
  if (!Number.isFinite(count) || count < 0) return;
  overviewPack.watchingN = count;
  if (subText != null) overviewPack.watchingHint = String(subText);
  paintOverviewWatching();
}

/** 日线 as-of 覆盖 → 观察池副文案 */
export function syncOverviewBarsCoverage(data) {
  if (!data || data.success === false) return;
  const pct = Number(data.coverage_pct);
  if (Number.isFinite(pct)) overviewPack.barsPct = pct;
  // API 字段仍叫 universe_count，语义是观察池只数
  const n = Number(data.universe_count);
  if (Number.isFinite(n) && overviewPack.watchingN == null) {
    overviewPack.watchingN = n;
  }
  paintOverviewWatching();
}

/** 分钟 Ready → 概览 */
export function syncOverviewMinute(data) {
  if (!data || data.success === false) {
    setProOverviewKpi("minute", "—", "Ready% · Short", "is-empty");
    return;
  }
  const total = Number(data.universe_count) || 0;
  const ok = Number(data.cached_ok) || 0;
  const short = Number(data.short) || 0;
  const missing = Number(data.missing) || 0;
  const pct = total > 0 ? Math.round((1000 * ok) / total) / 10 : 0;
  let st = "is-empty";
  if (total > 0) {
    st = data.coverage_ok ? "is-good" : missing > 0 ? "is-bad" : "is-mid";
  }
  const subBits = [];
  if (short > 0) subBits.push(`Short ${short}`);
  if (missing > 0) subBits.push(`Missing ${missing}`);
  setProOverviewKpi(
    "minute",
    total > 0 ? `${pct}%` : "—",
    subBits.join(" · ") || (total > 0 ? "全员 Ready" : "Ready% · Short"),
    st
  );
}

/**
 * Holdout OOS → 概览 IC 卡（ŷ_oo / ŷ_τc 同口径）。
 * 主值优先日频截面 IC（对齐 Qlib）；副文案：日频截面|拼样本 · 命中 · Rank。
 */
function syncOverviewIcHead(kpiKey, oos, emptySub = "日频截面 IC") {
  if (!oos || typeof oos !== "object") {
    setProOverviewKpi(kpiKey, "—", emptySub, "is-empty");
    return false;
  }
  const cs = Number(oos.cs_ic);
  const chrono = Number(oos.ic);
  const useCs = Number.isFinite(cs);
  const ic = useCs ? cs : chrono;
  if (!Number.isFinite(ic)) {
    setProOverviewKpi(kpiKey, "—", emptySub, "is-empty");
    return false;
  }
  // A 股：正 IC 红、负 IC 绿（不走质量色 ok/danger）
  const st = ic > 0 ? "is-good" : ic < 0 ? "is-bad" : "is-mid";
  const parts = [useCs ? "日频截面" : "拼样本"];
  const hitRaw =
    oos.sign_hit != null ? Number(oos.sign_hit) : Number(oos.sign_hit_rate);
  if (Number.isFinite(hitRaw)) {
    const pct = hitRaw <= 1.0001 ? hitRaw * 100 : hitRaw;
    parts.push(`命中 ${pct.toFixed(0)}%`);
  }
  if (useCs && Number.isFinite(Number(oos.cs_rank_ic))) {
    parts.push(`Rank ${Number(oos.cs_rank_ic).toFixed(2)}`);
  }
  setProOverviewKpi(kpiKey, `IC ${ic.toFixed(2)}`, parts.join(" · "), st, {
    signed: true,
  });
  return true;
}

/** 全局 ŷ_oo Holdout OOS → 概览。 */
export function syncOverviewOo(oos) {
  syncOverviewIcHead("eod", oos);
}

/** 全局 ŷ_τc Holdout OOS → 概览（与 ŷ_oo 同口径）。 */
export function syncOverviewTauOos(oos) {
  return syncOverviewIcHead("tau", oos);
}

/** ŷ_τc 概览：无 OOS 时的文案占位（启用态 / 缺失）。有 OOS 用 syncOverviewTauOos。 */
export function syncOverviewTau(value, subText) {
  setProOverviewKpi(
    "tau",
    value != null ? String(value) : "—",
    subText != null ? String(subText) : "ŷ_τc",
    "is-mid"
  );
}


/** 从 IC/分组结果渲染因子摘要卡网格 */
export function renderFactorSummaryCards(data, metaByName) {
  const host = document.getElementById("quant-pro-factor-summaries");
  const grid = document.getElementById("quant-pro-factor-summaries-grid");
  const countEl = document.getElementById("quant-pro-factor-count");
  if (!host || !grid) return;

  const factors =
    (data && data.factors) ||
    (data && Array.isArray(data) ? data : []) ||
    [];
  if (!factors.length) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  const crossSection = factors.some((f) => f && f.mean_score != null && f.ir_annual == null);
  if (countEl) {
    countEl.textContent = crossSection
      ? `截面分数 · 共 ${factors.length} 个（不是 IC）`
      : `全样本 IC · 共 ${factors.length} 个（≠ 组β）`;
  }

  const topByAbsIR = [...factors]
    .sort((a, b) => Math.abs(b.mean_score ?? b.ir_annual ?? b.ic_ir ?? 0) - Math.abs(a.mean_score ?? a.ir_annual ?? a.ic_ir ?? 0))
    .slice(0, 16);

  // 与概览 KPI 同构：一行等分铺满（≤5 列；更多则换行）
  const cols = Math.min(Math.max(topByAbsIR.length, 1), 5);
  grid.style.setProperty("--factor-summary-cols", String(cols));

  let html = "";
  for (const f of topByAbsIR) {
    const name = f.factor || f.name || "";
    const meta = (metaByName && metaByName[name]) || {};
    const label = f.label || meta.label || name;
    const desc = f.description || meta.description || name;
    const icMean = Number(f.ic_mean ?? f.ic ?? NaN);
    const score = Number(f.mean_score ?? f.ir_annual ?? f.ic_ir ?? f.ir ?? NaN);
    const spread = Number(f.std_score ?? NaN);
    const cov = Number(f.coverage ?? f.sample_count ?? NaN);
    const posRate = Number(f.positive_rate ?? f.positive_ratio ?? NaN);
    const cardCross = f.mean_score != null && f.ir_annual == null;

    let state = "mid";
    if (cardCross && Number.isFinite(score)) {
      const absScore = Math.abs(score);
      if (absScore >= 0.5) state = "strong";
      else if (absScore < 0.05) state = "faint";
    } else if (Number.isFinite(score)) {
      const absIR = Math.abs(score);
      if (absIR >= 1.2) state = "strong";
      else if (absIR < 0.3) state = "faint";
      else if (Number.isFinite(icMean) && Math.abs(icMean) < 0.02) state = "weak";
    }
    const stateLabel = cardCross
      ? { strong: "拉开", mid: "中", faint: "接近零" }[state] || "中"
      : { strong: "强", mid: "中", weak: "偏弱", faint: "弱" }[state];

    const valCls = (n) => (n > 0 ? "is-pos" : n < 0 ? "is-neg" : "");
    const leftCls = Number.isFinite(cardCross ? score : icMean) ? valCls(cardCross ? score : icMean) : "";
    const midCls = Number.isFinite(cardCross ? spread : score) ? valCls(cardCross ? spread : score) : "";
    const leftLabel = cardCross ? "均值" : "IC";
    const midLabel = cardCross ? "离散" : "IR";
    const rightLabel = cardCross ? "正分%" : "正IC%";
    const leftVal = cardCross ? score : icMean;
    const midVal = cardCross ? spread : score;
    const leftText = Number.isFinite(leftVal) ? (cardCross && leftVal >= 0 ? "+" : "") + leftVal.toFixed(cardCross ? 2 : 3) : "—";
    const midText = Number.isFinite(midVal)
      ? (midVal >= 0 && !cardCross ? "+" : "") + midVal.toFixed(2)
      : "—";

    html += `
      <div class="quant-pro-factor-card" data-state="${escapeHtml(state)}" title="${escapeHtml(`${name} · ${desc}`)}">
        <div class="quant-pro-factor-card-head">
          <span class="quant-pro-factor-card-name">${escapeHtml(label)}</span>
          <span class="quant-pro-factor-card-state">${escapeHtml(stateLabel)}</span>
        </div>
        <div class="quant-pro-factor-card-desc">${escapeHtml(desc)}</div>
        <div class="quant-pro-factor-card-metrics">
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">${leftLabel}</span>
            <span class="quant-pro-factor-metric-value ${leftCls}">${leftText}</span>
          </div>
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">${midLabel}</span>
            <span class="quant-pro-factor-metric-value ${midCls}">${midText}</span>
          </div>
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">${rightLabel}</span>
            <span class="quant-pro-factor-metric-value is-accent">${Number.isFinite(posRate) ? posRate.toFixed(0) + "%" : (Number.isFinite(cov) ? cov : "—")}</span>
          </div>
        </div>
      </div>
    `;
  }
  grid.innerHTML = html;
}

function vizEmpty(msg) {
  return (
    `<div class="quant-viz-empty" role="status">` +
    `<p class="quant-viz-empty-msg">${escapeHtml(msg)}</p>` +
    `</div>`
  );
}

function renderFactorIR(data, hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (!data || !data.factors || !data.factors.length) {
    host.innerHTML = vizEmpty((data && (data.note || data.error)) || "观察池还没有截面因子分");
    return;
  }

  const factors = [...data.factors]
    .sort((a, b) => Math.abs(b.mean_score || 0) - Math.abs(a.mean_score || 0))
    .slice(0, 14);
  const absScores = factors.map((f) => Math.abs(f.mean_score || 0));
  const maxAbs = Math.max(...absScores, 0.05);
  const hasAnySignal = absScores.some((v) => v > 0.01);

  if (!hasAnySignal) {
    host.innerHTML = vizEmpty("截面分数接近零 · 暂无拉开的因子");
    return;
  }

  let html =
    `<div class="quant-chart-axis-head">` +
    `<span class="quant-chart-title">截面分数（|均值| 排序）</span>` +
    `<span class="quant-chart-axis-hint">中线=0 · 右正左负 · 不是 IC</span>` +
    `</div>` +
    `<div class="factor-ir-bar">` +
    `<div class="factor-ir-head"><span>因子</span><span>均值</span><span class="num">值</span><span class="num">正%</span></div>`;
  for (const f of factors) {
    const ir = Number(f.mean_score || 0);
    const half = ir === 0 ? 0 : Math.max(3, (Math.abs(ir) / maxAbs) * 50);
    const isPos = ir >= 0;
    const cn = factorCN(f.factor);
    const tip = `${cn} · 截面均值 ${ir >= 0 ? "+" : ""}${ir.toFixed(2)} · 离散 ${Number(f.std_score || 0).toFixed(2)} · 正分 ${f.positive_rate?.toFixed?.(0) ?? "—"}% · 不是 IC`;
    html += `
      <div class="factor-ir-row" title="${escapeHtml(tip)}">
        <span class="factor-ir-name">${escapeHtml(cn)}</span>
        <div class="factor-ir-bar-track is-bipolar">
          <span class="factor-ir-zero" aria-hidden="true"></span>
          <div class="factor-ir-bar-fill ${isPos ? "is-positive" : "is-negative"}" style="${
            isPos
              ? `left:50%;width:${half.toFixed(1)}%`
              : `right:50%;width:${half.toFixed(1)}%`
          }"></div>
        </div>
        <span class="factor-ir-value ${isPos ? "is-positive" : "is-negative"}">${
          ir >= 0 ? "+" : ""
        }${ir.toFixed(2)}</span>
        <span class="factor-ir-rate">${f.positive_rate?.toFixed?.(0) || 0}%</span>
      </div>
    `;
  }
  html += `</div>`;
  html += `<p class="factor-ir-footnote">${escapeHtml(
    data.note || "截面因子分数的均值和离散，不是 IC"
  )}</p>`;

  host.innerHTML = html;
}

function unwrapApi(res, label = "加载失败") {
  if (!res || res.ok === false) {
    throw new Error((res && res.error) || label);
  }
  return res.data != null ? res.data : res;
}

export async function loadAndRenderFactorIR(hostId) {
    const host = document.getElementById(hostId);
    if (!host) return;
    host.innerHTML = `<div class="quant-fingerprint is-busy">计算截面分数…</div>`;
    setProStatusChip("quant-pro-factor-status", "busy", "计算截面分数…");
    try {
      const data = unwrapApi(
        await apiFetch("/api/quant/factor-ir"),
        "截面分数加载失败"
      );
      renderFactorIR(data, hostId);
      // 若有 IR 结果，同步渲染因子摘要卡
      if (data && data.factors) renderFactorSummaryCards(data);
      setProStatusChip("quant-pro-factor-status", "ok", "截面分数就绪");
    } catch (err) {
      host.innerHTML = vizEmpty(`加载失败: ${err.message || err}`);
      setProStatusChip("quant-pro-factor-status", "error", "加载失败");
    }
  }
