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
  alt_sentiment: "舆情",
  llm_sentiment: "LLM舆情",
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
export function factorCN(name) {
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
export function setProOverviewKpi(key, valueText, subText, state, opts = {}) {
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
  universeN: null,
  universeHint: null,
  barsPct: null,
  k: null,
  oosPass: null,
  oosN: null,
  icPos: null,
  outliers: null,
  mode: null,
  research: null,
  live: null,
  version: null,
};

function clusterOosCounts(data) {
  const clusters = Array.isArray(data && data.clusters) ? data.clusters : [];
  let pass = 0;
  let n = 0;
  for (const cl of clusters) {
    if (!cl || cl.singleton) continue;
    const g = cl.oos_gate || {};
    if (g.skipped) continue;
    if (!g || (g.ok == null && g.passed == null && cl.oos_passed == null)) continue;
    n += 1;
    if ((g.ok && g.passed) || cl.oos_passed === true) pass += 1;
  }
  return { pass, n };
}

/** 组内 ŷ_oo 截面 IC 为正的日占比（均值）；不是 ŷ 与收益同号率。 */
function clusterIcPosRatio(data) {
  const clusters = Array.isArray(data && data.clusters) ? data.clusters : [];
  const xs = [];
  for (const cl of clusters) {
    const sp =
      cl &&
      cl.factor_ic_panel &&
      cl.factor_ic_panel.score_ic &&
      cl.factor_ic_panel.score_ic.spearman;
    const r = sp && Number(sp.positive_ic_ratio);
    if (Number.isFinite(r)) xs.push(r);
  }
  if (!xs.length) return null;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function paintOverviewUniverse() {
  const n = overviewPack.universeN;
  if (n == null || !Number.isFinite(Number(n))) return;
  const pct = overviewPack.barsPct;
  const hint = overviewPack.universeHint;
  let sub = "观察池";
  if (pct != null && Number.isFinite(Number(pct))) {
    sub = `日线 ${Number(pct).toFixed(0)}%`;
  } else if (hint) {
    sub = hint;
  }
  setProOverviewKpi(
    "universe",
    `${Number(n)} 只`,
    sub,
    Number(n) > 0 ? "is-mid" : "is-empty"
  );
}

function paintOverviewEod() {
  const k = overviewPack.k;
  if (k == null || !Number.isFinite(Number(k))) {
    return;
  }
  const pass = overviewPack.oosPass;
  const oosN = overviewPack.oosN;
  const mode = overviewPack.mode;
  const modeLabel =
    mode === "active" ? "执行" : mode === "shadow" ? "对照" : mode === "off" ? "未接通" : "";
  const parts = [];
  if (oosN != null && oosN > 0) {
    parts.push(`OOS ${pass}/${oosN}`);
  } else if (overviewPack.outliers) {
    parts.push(`${overviewPack.outliers} 离群`);
  }
  const icPos = overviewPack.icPos;
  if (icPos != null && Number.isFinite(Number(icPos))) {
    parts.push(`IC+日 ${(Number(icPos) * 100).toFixed(0)}%`);
  }
  if (modeLabel) parts.push(modeLabel);
  let st = "is-mid";
  if (oosN != null && oosN > 0) {
    const ratio = pass / oosN;
    st = ratio >= 0.8 ? "is-good" : ratio >= 0.5 ? "is-mid" : "is-bad";
  }
  setProOverviewKpi("eod", `${k} 组`, parts.join(" · ") || "分组完成", st);
}

function paintOverviewLanding() {
  const research = overviewPack.research;
  const live = overviewPack.live;
  const mode = overviewPack.mode || "off";
  if (research == null && live == null && !overviewPack.mode) return;
  const hasR = !!research;
  const execOn = mode === "active";
  const shadow = mode === "shadow";
  let val = "未启用";
  let st = "is-empty";
  if (hasR && execOn) {
    val = "研究+执行";
    st = "is-good";
  } else if (hasR && shadow) {
    val = "研究+对照";
    st = "is-mid";
  } else if (hasR) {
    val = "仅研究";
    st = "is-mid";
  } else if (execOn) {
    val = "仅执行";
    st = "is-bad";
  } else if (live && shadow) {
    val = "对照 · 缺研究";
    st = "is-bad";
  }
  const ver =
    overviewPack.version != null && overviewPack.version !== ""
      ? `v${overviewPack.version}`
      : "";
  const subParts = [];
  if (ver) subParts.push(ver);
  if (hasR) subParts.push("研究套");
  else if (live) subParts.push("缺研究档");
  if (execOn) subParts.push("执行 active");
  else if (shadow) subParts.push("shadow");
  setProOverviewKpi("landing", val, subParts.join(" · ") || "研究套 · 执行套", st);
}

/** 观察池只数 → 概览 KPI（进页拉 watching 时） */
export function syncOverviewUniverse(n, subText) {
  const count = Number(n);
  if (!Number.isFinite(count) || count < 0) return;
  overviewPack.universeN = count;
  if (subText != null) overviewPack.universeHint = String(subText);
  paintOverviewUniverse();
}

/** 日线 as-of 覆盖 → 观察池副文案 */
export function syncOverviewBarsCoverage(data) {
  if (!data || data.success === false) return;
  const pct = Number(data.coverage_pct);
  if (Number.isFinite(pct)) overviewPack.barsPct = pct;
  const n = Number(data.universe_count);
  if (Number.isFinite(n) && overviewPack.universeN == null) {
    overviewPack.universeN = n;
  }
  paintOverviewUniverse();
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

/** 分组结果 → ŷ_oo 卡 */
export function syncOverviewFromClusters(data) {
  if (!data || data.success === false) return;
  const clusters = Array.isArray(data.clusters) ? data.clusters : [];
  const kRaw =
    data.n_clusters != null
      ? Number(data.n_clusters)
      : clusters.length
        ? clusters.length
        : null;
  const k = kRaw != null && Number.isFinite(kRaw) ? kRaw : null;
  const nRaw =
    data.universe_count != null
      ? Number(data.universe_count)
      : data.stock_count != null
        ? Number(data.stock_count)
        : Array.isArray(data.watching_codes)
          ? data.watching_codes.length
          : null;
  const n = nRaw != null && Number.isFinite(nRaw) ? nRaw : null;
  const outliers = clusters.filter((c) => c && c.outlier_singleton).length;
  if (n != null && overviewPack.universeN == null) {
    overviewPack.universeN = n;
    paintOverviewUniverse();
  }
  if (k != null) overviewPack.k = k;
  overviewPack.outliers = outliers || null;
  const oos = clusterOosCounts(data);
  overviewPack.oosPass = oos.n > 0 ? oos.pass : null;
  overviewPack.oosN = oos.n > 0 ? oos.n : null;
  overviewPack.icPos = clusterIcPosRatio(data);
  paintOverviewEod();
}

/** 命中率（0–1 或已是百分比）→ ŷ_oo 卡副文案（无 OOS 计数时） */
export function syncOverviewHit(hitRate, subText) {
  let pct = Number(hitRate);
  if (!Number.isFinite(pct)) return;
  if (pct <= 1.0001) pct *= 100;
  if (overviewPack.k == null) return;
  if (overviewPack.oosN) {
    paintOverviewEod();
    return;
  }
  const st = pct >= 55 ? "is-good" : pct >= 50 ? "is-mid" : "is-bad";
  const mode = overviewPack.mode;
  const modeLabel =
    mode === "active" ? "执行" : mode === "shadow" ? "对照" : "";
  const sub = [subText != null ? String(subText) : `命中 ${pct.toFixed(0)}%`, modeLabel]
    .filter(Boolean)
    .join(" · ");
  setProOverviewKpi("eod", `${overviewPack.k} 组`, sub, st);
}

/** live 状态 → 落地卡，并补 ŷ_oo mode */
export function syncOverviewLanding(data) {
  if (!data || data.success === false) return;
  const cs = data.cluster_scoring || {};
  const act = data.active || {};
  const research = data.research || {};
  overviewPack.mode = cs.mode || "off";
  overviewPack.live = !!act.exists;
  overviewPack.research = research.exists != null ? !!research.exists : null;
  overviewPack.version = act.version != null ? act.version : null;
  if (overviewPack.k == null && act.n_clusters != null) {
    const nk = Number(act.n_clusters);
    if (Number.isFinite(nk)) overviewPack.k = nk;
  }
  paintOverviewLanding();
  paintOverviewEod();
}

/** 全局 ŷ_oo Holdout OOS → 概览。主值优先日频截面 IC（对齐 Qlib）；命中进副文案。 */
export function syncOverviewOo(oos) {
  if (!oos || typeof oos !== "object") {
    setProOverviewKpi("eod", "—", "日频截面 IC", "is-empty");
    return;
  }
  const cs = Number(oos.cs_ic);
  const chrono = Number(oos.ic);
  const useCs = Number.isFinite(cs);
  const ic = useCs ? cs : chrono;
  if (!Number.isFinite(ic)) {
    setProOverviewKpi("eod", "—", "日频截面 IC", "is-empty");
    return;
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
  setProOverviewKpi("eod", `IC ${ic.toFixed(2)}`, parts.join(" · "), st, {
    signed: true,
  });
}

/** ŷ_oc 复盘 / τ 模型 → 概览副轴 KPI
 * @param {"hit"|"ic"|"text"} mode
 */
export function syncOverviewTau(value, subText, mode = "hit") {
  if (mode === "text") {
    setProOverviewKpi(
      "tau",
      value != null ? String(value) : "—",
      subText != null ? String(subText) : "ŷ_oc",
      "is-mid"
    );
    return;
  }
  const n = Number(value);
  if (!Number.isFinite(n)) {
    setProOverviewKpi(
      "tau",
      "—",
      subText != null ? String(subText) : "ŷ_oc",
      "is-empty"
    );
    return;
  }
  if (mode === "ic") {
    const st = n > 0 ? "is-good" : n < 0 ? "is-bad" : "is-mid";
    setProOverviewKpi(
      "tau",
      `IC ${n.toFixed(2)}`,
      subText != null ? String(subText) : "ŷ_oc IC",
      st,
      { signed: true }
    );
    return;
  }
  let pct = n;
  if (pct <= 1.0001) pct *= 100;
  const st = pct >= 55 ? "is-good" : pct >= 50 ? "is-mid" : "is-bad";
  setProOverviewKpi(
    "tau",
    `${pct.toFixed(0)}%`,
    subText != null ? String(subText) : "ŷ_oc 命中 · open→收",
    st
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
  if (countEl) countEl.textContent = `全样本 IC · 共 ${factors.length} 个（≠ 组β）`;

  const topByAbsIR = [...factors]
    .sort((a, b) => Math.abs(b.ir_annual ?? b.ic_ir ?? 0) - Math.abs(a.ir_annual ?? a.ic_ir ?? 0))
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
    const ir = Number(f.ir_annual ?? f.ic_ir ?? f.ir ?? NaN);
    const cov = Number(f.coverage ?? f.sample_count ?? NaN);
    const posRate = Number(f.positive_rate ?? f.positive_ratio ?? NaN);

    // IR 强度标签（研究语义，非生产/上线状态）
    let state = "mid";
    if (Number.isFinite(ir)) {
      const absIR = Math.abs(ir);
      if (absIR >= 1.2) state = "strong";
      else if (absIR < 0.3) state = "faint";
      else if (Number.isFinite(icMean) && Math.abs(icMean) < 0.02) state = "weak";
    }
    const stateLabel = { strong: "强", mid: "中", weak: "偏弱", faint: "弱" }[state];

    const valCls = (n) => (n > 0 ? "is-pos" : n < 0 ? "is-neg" : "");
    const icCls = Number.isFinite(icMean) ? valCls(icMean) : "";
    const irCls = Number.isFinite(ir) ? valCls(ir) : "";

    html += `
      <div class="quant-pro-factor-card" data-state="${escapeHtml(state)}" title="${escapeHtml(`${name} · ${desc}`)}">
        <div class="quant-pro-factor-card-head">
          <span class="quant-pro-factor-card-name">${escapeHtml(label)}</span>
          <span class="quant-pro-factor-card-state">${escapeHtml(stateLabel)}</span>
        </div>
        <div class="quant-pro-factor-card-desc">${escapeHtml(desc)}</div>
        <div class="quant-pro-factor-card-metrics">
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">IC</span>
            <span class="quant-pro-factor-metric-value ${icCls}">${Number.isFinite(icMean) ? icMean.toFixed(3) : "—"}</span>
          </div>
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">IR</span>
            <span class="quant-pro-factor-metric-value ${irCls}">${Number.isFinite(ir) ? (ir >= 0 ? "+" : "") + ir.toFixed(2) : "—"}</span>
          </div>
          <div class="quant-pro-factor-metric">
            <span class="quant-pro-factor-metric-label">正IC%</span>
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
    .sort((a, b) => Math.abs(b.ir_annual || 0) - Math.abs(a.ir_annual || 0))
    .slice(0, 14);
  const absIRs = factors.map((f) => Math.abs(f.ir_annual || 0));
  const maxAbsIR = Math.max(...absIRs, 0.05);
  const hasAnySignal = absIRs.some((v) => v > 0.01);

  if (!hasAnySignal) {
    host.innerHTML = vizEmpty("所有因子 IR 接近零 · 暂无有效信号");
    return;
  }

  let html =
    `<div class="quant-chart-axis-head">` +
    `<span class="quant-chart-title">年化 IR（|IR| 排序）</span>` +
    `<span class="quant-chart-axis-hint">中线=0 · 右正左负</span>` +
    `</div>` +
    `<div class="factor-ir-bar">` +
    `<div class="factor-ir-head"><span>因子</span><span>IR</span><span class="num">值</span><span class="num">正%</span></div>`;
  for (const f of factors) {
    const ir = Number(f.ir_annual || 0);
    const half = ir === 0 ? 0 : Math.max(3, (Math.abs(ir) / maxAbsIR) * 50);
    const isPos = ir >= 0;
    const cn = factorCN(f.factor);
    const tip = `${cn} · IR ${ir >= 0 ? "+" : ""}${ir.toFixed(2)} · 正IC ${f.positive_rate?.toFixed?.(0) ?? "—"}%`;
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
    data.note || ""
  )} · IR = IC均值/IC标准差 × √(252/horizon)</p>`;

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
    host.innerHTML = `<div class="quant-fingerprint is-busy">计算因子 IR…</div>`;
    setProStatusChip("quant-pro-factor-status", "busy", "计算 IR…");
    try {
      const data = unwrapApi(
        await apiFetch("/api/quant/factor-ir?horizon_days=3"),
        "IR 加载失败"
      );
      renderFactorIR(data, hostId);
      // 若有 IR 结果，同步渲染因子摘要卡
      if (data && data.factors) renderFactorSummaryCards(data);
      setProStatusChip("quant-pro-factor-status", "ok", "IR 就绪");
    } catch (err) {
      host.innerHTML = vizEmpty(`加载失败: ${err.message || err}`);
      setProStatusChip("quant-pro-factor-status", "error", "加载失败");
    }
  }
