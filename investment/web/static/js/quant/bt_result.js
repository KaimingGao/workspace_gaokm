/**
 * Top-K / 组合回测结果区 HTML 与指标卡渲染。
 */
import { escapeHtml } from "../shared.js";

export function fmtPct(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeHtml(String(v));
  return `${n}%`;
}

export function metricClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}

function _kpiNum(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function _kpiPct(v, { signed = true } = {}) {
  const n = _kpiNum(v);
  if (n == null) return "—";
  const body = Math.abs(n).toFixed(2);
  if (!signed) return `${body}%`;
  return `${n >= 0 ? "+" : "-"}${body}%`;
}

/** |y_fuse| < 0.05% → 无方向，与后端 HIT_YHAT_EPS / score_ledger 同口径。 */
const HIT_YHAT_EPS = 0.05;

function _fuseHitFromTrades(rows) {
  let hits = 0;
  let n = 0;
  for (const r of rows || []) {
    if (!r || typeof r !== "object") continue;
    if (String(r.status || "") === "skipped" || String(r.status || "") === "held") continue;
    let yf = _kpiNum(r.ranking);
    if (yf == null) yf = _kpiNum(r.y_fuse);
    if (yf == null) {
      const rs = _kpiNum(r.ranking_score);
      if (rs != null) yf = rs * 100;
    }
    const real = _kpiNum(r.realized_tau) ?? _kpiNum(r.realized_cc);
    if (yf == null || real == null) continue;
    if (Math.abs(yf) < HIT_YHAT_EPS) continue;
    if (Math.abs(real) < 1e-12) continue;
    n += 1;
    if ((yf > 0) === (real > 0)) hits += 1;
  }
  if (n <= 0) return { hit_rate_pct: null, hit_n: 0, hit_hits: 0 };
  return {
    hit_rate_pct: Math.round((hits / n) * 1000) / 10,
    hit_n: n,
    hit_hits: hits,
  };
}

/**
 * 历史回测页概览 KPI（纯数据，无 DOM）。
 * 兼容完整回测结果（metrics+benchmark）与摘要顶层字段。
 */
export function buildReplayOverviewKpis(data, { source = "" } = {}) {
  const blank = (sub) => ({ value: "—", sub, empty: true, cls: "" });
  if (!data || data.success === false) {
    return {
      return: blank("先跑回测"),
      excess: blank("相对基准"),
      dd: blank("历史 MaxDD"),
      win: blank("日净值>0"),
      hit: blank("sign(ranking)"),
    };
  }
  const m =
    data.metrics && typeof data.metrics === "object" ? data.metrics : data;
  const bench = data.benchmark && typeof data.benchmark === "object" ? data.benchmark : {};
  const ret = m.total_return_pct;
  const win = m.win_rate_pct;
  const dd = m.max_drawdown_pct;
  const excess = bench.ok ? bench.excess_pct : m.excess_pct;
  const legs =
    data.alpha_beta_legs && typeof data.alpha_beta_legs === "object"
      ? data.alpha_beta_legs
      : {};
  const src = source || (data.params ? "当次回测" : "回测");
  const retN = _kpiNum(ret);
  const exN = _kpiNum(excess);
  const ddN = _kpiNum(dd);
  const winN = _kpiNum(win);
  const daysN = _kpiNum(m.day_count ?? (Array.isArray(data.equity_curve) ? data.equity_curve.length : null));
  let hitRate = _kpiNum(m.hit_rate_pct);
  let hitCount = _kpiNum(m.hit_n);
  let hitHits = _kpiNum(m.hit_hits);
  if (hitRate == null || hitCount == null) {
    const fb = _fuseHitFromTrades(data.sim_trades || data.trades);
    if (hitRate == null) hitRate = _kpiNum(fb.hit_rate_pct);
    if (hitCount == null) hitCount = _kpiNum(fb.hit_n);
    if (hitHits == null) hitHits = _kpiNum(fb.hit_hits);
  }
  const excessSub =
    exN == null
      ? "相对基准"
      : legs.beta_leg_approx_pct != null
        ? `超额 · β腿≈${Number(legs.beta_leg_approx_pct).toFixed(1)}%`
        : `相对 ${bench.benchmark_label || "基准"}`;
  let hitSub = "sign(ranking)";
  if (hitCount != null && hitCount > 0 && hitHits != null) {
    hitSub = `${hitHits}/${hitCount} 笔`;
  } else if (hitCount != null && hitCount > 0) {
    hitSub = `${hitCount} 笔`;
  } else if (hitRate == null) {
    hitSub = "无方向成交";
  }
  return {
    return: {
      value: _kpiPct(ret),
      sub: src,
      empty: retN == null,
      cls: metricClass(retN),
    },
    excess: {
      value: _kpiPct(excess),
      sub: excessSub,
      empty: exN == null,
      cls: metricClass(exN),
    },
    dd: {
      value: _kpiPct(dd, { signed: false }),
      sub: "历史 MaxDD",
      empty: ddN == null,
      cls: ddN != null && ddN > 0 ? "down" : "",
    },
    win: {
      value: winN == null ? "—" : `${winN.toFixed(1)}%`,
      sub: daysN != null ? `${daysN} 日净值>0` : "日净值>0",
      empty: winN == null,
      cls: "",
    },
    hit: {
      value: hitRate == null ? "—" : `${hitRate.toFixed(1)}%`,
      sub: hitSub,
      empty: hitRate == null,
      cls: "",
    },
  };
}

const _REPLAY_KPI_IDS = {
  return: "replay-kpi-return",
  excess: "replay-kpi-excess",
  dd: "replay-kpi-dd",
  win: "replay-kpi-win",
  hit: "replay-kpi-hit",
};

/** 把概览 KPI 写进历史回测页 DOM。 */
export function applyReplayOverviewKpis(data, opts = {}) {
  const pack = buildReplayOverviewKpis(data, opts);
  Object.entries(_REPLAY_KPI_IDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const item = pack[key];
    const card = el.closest(".replay-kpi-card") || el.closest(".dashboard-kpi-card");
    el.textContent = item.value;
    el.classList.remove("up", "down");
    if (item.cls) el.classList.add(item.cls);
    if (card) card.classList.toggle("is-empty", !!item.empty);
    const sub = document.getElementById(`${id}-sub`);
    if (sub) sub.textContent = item.sub;
  });
  return pack;
}

const _REPLAY_T0_KPI_IDS = {
  return: "replay-t0-kpi-return",
  pnl: "replay-t0-kpi-pnl",
  win: "replay-t0-kpi-win",
  cover: "replay-t0-kpi-cover",
};

function _kpiMoney(v) {
  const n = _kpiNum(v);
  if (n == null) return "—";
  const abs = Math.abs(n).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  if (n > 0) return `+${abs}`;
  if (n < 0) return `-${abs}`;
  return abs;
}

/**
 * 做 T 回测概览 KPI（纯数据）。
 */
export function buildReplayT0Kpis(data) {
  const blank = (sub) => ({ value: "—", sub, empty: true, cls: "" });
  if (!data || data.success === false) {
    return {
      return: blank("先跑回测"),
      pnl: blank("含敞口"),
      win: blank("做 T 日"),
      cover: blank("完成往返率"),
    };
  }
  const ret = data.cumulative_return_pct ?? data.pnl_vs_hold_mv_pct;
  const pnl = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
  const win = data.win_rate_pct ?? data.t0_win_rate_pct;
  const cover = data.cover_rate_pct;
  const days = data.t0_trade_days;
  const retN = _kpiNum(ret);
  const pnlN = _kpiNum(pnl);
  const winN = _kpiNum(win);
  const coverN = _kpiNum(cover);
  const daysN = _kpiNum(days);
  return {
    return: {
      value: _kpiPct(ret),
      sub: retN == null ? "累计收益" : "相对本金",
      empty: retN == null,
      cls: metricClass(retN),
    },
    pnl: {
      value: _kpiMoney(pnl),
      sub: "含敞口",
      empty: pnlN == null,
      cls: metricClass(pnlN),
    },
    win: {
      value: winN == null ? "—" : `${winN.toFixed(1)}%`,
      sub: daysN != null ? `${daysN} 日` : "做 T 日",
      empty: winN == null,
      cls: "",
    },
    cover: {
      value: coverN == null ? "—" : `${coverN.toFixed(1)}%`,
      sub: "完成往返率",
      empty: coverN == null,
      cls: "",
    },
  };
}

/** 把做 T KPI 写进历史回测页 DOM。 */
export function applyReplayT0Kpis(data) {
  if (!document.getElementById("replay-t0-kpi-row")) return null;
  const pack = buildReplayT0Kpis(data);
  Object.entries(_REPLAY_T0_KPI_IDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    const item = pack[key];
    const card = el.closest(".replay-kpi-card") || el.closest(".dashboard-kpi-card");
    el.textContent = item.value;
    el.classList.remove("up", "down");
    if (item.cls) el.classList.add(item.cls);
    if (card) card.classList.toggle("is-empty", !!item.empty);
    const sub = document.getElementById(`${id}-sub`);
    if (sub) sub.textContent = item.sub;
  });
  return pack;
}

/** 研究包导出用曲线摘要（纯对象，无 DOM）。 */
export function buildResearchCurves(result) {
  if (!result || !result.success) return null;
  const sic = result.score_ic || {};
  const bench = result.benchmark || {};
  return {
    equity_curve: result.equity_curve || [],
    benchmark_equity_curve: bench.equity_curve || [],
    ic_series_tail: sic.ic_series_tail || [],
    ic_rolling_tail: sic.ic_rolling_tail || [],
  };
}

/** Top-K 回测失败时的一行摘要。 */
export function buildPortfolioBacktestFailText(data, resStatus) {
  const dropped = (data.dropped_stocks || [])
    .slice(0, 3)
    .map((d) => d.stock_code || d)
    .filter(Boolean);
  const dropNote = dropped.length ? ` · 已排除短序列 ${dropped.join("/")}` : "";
  return (
    (data && (data.error || data.detail)) ||
    `失败 HTTP ${resStatus}` + dropNote
  );
}

/**
 * Top-K 回测成功后的一行摘要文案。
 * @returns {{ text: string, warn: boolean }}
 */
export function buildPortfolioBacktestSummaryText(data) {
  const m = data.metrics || {};
  const costModel =
    data.cost_model || (data.params || {}).cost_model || "simple_cn";
  const oos = data.oos_summary || {};
  const regime = data.regime_summary || {};
  const dq = data.data_quality || {};
  const fundNote =
    data.params?.fundamentals_used && data.params?.fundamentals_count
      ? ` · 基本面 ${data.params.fundamentals_count} 只`
      : "";
  const oosFailed = oos.ok === false || oos.failed === true;
  const oosNote = oos.ok
    ? oosFailed
      ? ` · OOS失败 外${oos.oos_return_pct ?? "—"}%`
      : ` · OOS ${oos.oos_return_pct ?? "—"}%`
    : oos.reason
      ? ` · OOS失败 ${oos.reason}`
      : "";
  const regimeNote = regime.regime ? ` · ${regime.regime}` : "";
  const dqNote =
    Number(dq.fallback_count || 0) > 0 || Number(dq.gated_count || 0) > 0
      ? ` · 数据降级 ${dq.fallback_count || 0}${Number(dq.gated_count || 0) > 0 ? `/门禁${dq.gated_count}` : ""}`
      : "";
  const cc = data.cost_compare || {};
  const costCmpNote =
    cc.ok && cc.return_gap_pp != null
      ? ` · 成本Δ ${Number(cc.return_gap_pp) >= 0 ? "+" : ""}${cc.return_gap_pp}pp` +
        (cc.avg_impact_bps != null && Number(cc.avg_impact_bps) > 0
          ? ` · 冲击≈${Number(cc.avg_impact_bps).toFixed(1)}bps`
          : "")
      : "";
  const wf = data.wf_slices || {};
  const wfNote =
    wf.ok && wf.mean_test_return_pct != null
      ? ` · WF均收益 ${Number(wf.mean_test_return_pct).toFixed(2)}%`
      : wf.reason
        ? ` · WF ${wf.reason}`
        : "";
  const attr = data.attribution || {};
  const attrNote =
    attr.ok && attr.selection_excess_pct != null
      ? ` · 选股超额 ${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
      : attr.by_sector && attr.by_sector.length
        ? ` · 归因行业 ${attr.by_sector.length}`
        : "";
  const pit = data.pit_report || {};
  const fundPit = pit.fundamentals || {};
  const pitNote = pit.bars_pit
    ? pit.fundamentals_pit
      ? " · PIT日线+财务"
      : fundPit.missing_as_of
        ? ` · PIT日线 · 财务缺${fundPit.missing_as_of}`
        : " · PIT日线"
    : "";
  const sa = data.source_audit || {};
  const auditNote =
    Number(sa.fallback_count || 0) > 0
      ? ` · 源不一致风险 ${sa.fallback_count}`
      : "";
  const matchNote = data.params?.execution_mode
    ? ` · 成交 ${
        data.params.execution_mode === "open_930"
          ? "09:30开"
          : data.params.execution_mode === "minute_5m"
            ? `${data.params.fill_clock || "5m"}成交`
          : data.params.execution_mode === "next_open"
            ? "次日开"
            : "收盘"
      }`
    : "";
  const tauOff =
    data.params?.apply_tau_buy_gate === false ||
    data.request?.apply_tau_buy_gate === false ||
    data.request?.rank_key === "predicted_score_eod";
  const eng =
    data.params?.engine || data.request?.engine || data.engine || "paper_replay";
  const scoreAxisNote =
    eng === "paper_replay" || eng === "rank_lots"
      ? ""
      : tauOff
        ? " · 选股 ŷ_oo·关τ闸"
        : data.params?.apply_tau_buy_gate === true
          ? " · 选股 ŷ_trade·τ闸开"
          : " · 选股 ŷ_oo·关τ闸";
  const engNote =
    eng === "topk_research"
      ? " · 引擎 研究Top-K（已下线）"
      : " · 引擎 rank_lots";
  const dropN = Number(
    data.params?.dropped_thin_count || (data.dropped_stocks || []).length || 0
  );
  const dropNote = dropN > 0 ? ` · 排除短序列 ${dropN}` : "";
  const doN =
    eng === "topk_research"
      ? Number(data.params?.dropout_n ?? data.request?.dropout_n ?? 0)
      : 0;
  const dropoutNote = doN > 0 ? ` · dropout ${doN}` : "";
  const sic = data.score_ic || {};
  const icNote = sic.ok
    ? ` · IC ${sic.ic_mean ?? "—"}/ICIR ${sic.icir ?? "—"}` +
      (sic.positive_ic_ratio != null
        ? ` · 正IC${(Number(sic.positive_ic_ratio) * 100).toFixed(0)}%`
        : "")
    : sic.reason
      ? ` · IC略 ${sic.reason}`
      : "";
  const cashInit = Number(data.params?.initial_cash ?? data.request?.initial_cash);
  const cashFloor = Number(data.params?.cash_floor ?? data.request?.cash_floor);
  const cashNote = Number.isFinite(cashInit)
    ? ` · 初始${Math.round(cashInit / 10000)}万` +
      (Number.isFinite(cashFloor) && cashFloor > 0
        ? `/地板${Math.round(cashFloor / 10000)}万`
        : "")
    : "";
  const sess = String(data.params?.session_day || "").slice(0, 10);
  const sessNote = /^\d{4}-\d{2}-\d{2}$/.test(sess) ? ` · 含当日 ${sess}` : "";
  const bench = data.benchmark || {};
  const benchNote =
    bench.ok && bench.excess_pct != null
      ? ` · 超额${Number(bench.excess_pct) >= 0 ? "+" : ""}${bench.excess_pct}%(${
          bench.benchmark_label || "基准"
        })` +
        (bench.ann_ir != null ? ` · IR ${bench.ann_ir}` : "")
      : "";
  const text =
    `标的 ${(data.loaded_stocks || []).length}` +
    (data.request?.lookback != null || data.params?.lookback != null
      ? ` · lookback ${data.request?.lookback ?? data.params?.lookback}`
      : "") +
    ` · 交易日 ${data.params?.trade_days ?? data.params?.common_dates ?? "—"} · 交易 ${m.trade_count} · 累计 ${m.total_return_pct}% · 胜率 ${m.win_rate_pct}% · 成本 ${
      costModel === "simple_cn" ? "A股简化" : costModel
    }${data.params?.neutralize ? ` · 中性化 ${data.params?.neutralized_rebalances || 0} 次` : ""}${fundNote}${oosNote}${regimeNote}${dqNote}${costCmpNote}${wfNote}${attrNote}${pitNote}${auditNote}${matchNote}${scoreAxisNote}${engNote}${cashNote}${dropNote}${dropoutNote}${icNote}${benchNote}${sessNote}`;
  return { text, warn: !!oosFailed };
}

/** @returns {Array<{ label: string, value: string, cls?: string }>} */
export function buildPortfolioBacktestCards(data, { escapeHtml: esc, fmtPct: fmt, metricClass: mcls }) {
  const m = data.metrics || {};
  const params = data.params || {};
  const costModel = data.cost_model || params.cost_model || (params.apply_costs ? "simple_cn" : "zero");
  const costMode = (data.cost_assumptions || {}).cost_mode || params.cost_mode;
  const costLabel =
    costModel === "zero"
      ? "零成本"
      : costMode === "turnover"
        ? "A股简化·换手"
        : costModel === "simple_cn"
          ? "A股简化"
          : String(costModel);
  const oos = data.oos_summary || {};
  const regime = data.regime_summary || {};
  const oosFailed = oos.ok === false || oos.failed === true;
  const oosLabel = oos.ok
    ? oos.failed
      ? `失败 · 内 ${oos.is_return_pct ?? "—"}% / 外 ${oos.oos_return_pct ?? "—"}%`
      : `样本内 ${oos.is_return_pct ?? "—"}% / 外 ${oos.oos_return_pct ?? "—"}%`
    : oos.reason || "—";
  const regimeLabel =
    regime.regime ||
    (regime.ok === false ? regime.reason || "—" : "—");
  const dq = data.data_quality || {};
  const fb = Number(dq.fallback_count || 0);
  const gated = Number(dq.gated_count || 0);
  const dqLabel =
    fb > 0 || gated > 0
      ? `降级 ${fb}${gated > 0 ? ` · 门禁 ${gated}` : ""}`
      : dq.count != null
        ? `正常 ${dq.count}`
        : "—";
  const dropN = Number(params.dropped_thin_count || (data.dropped_stocks || []).length || 0);
  const cc = data.cost_compare || {};
  const cards = [
    { label: "累计收益", value: fmt(m.total_return_pct), cls: mcls(m.total_return_pct) },
    { label: "胜率", value: fmt(m.win_rate_pct) },
    { label: "交易次数", value: esc(String(m.trade_count ?? data.trade_count ?? "—")) },
    { label: "最大回撤", value: fmt(m.max_drawdown_pct), cls: mcls(-(Number(m.max_drawdown_pct) || 0)) },
    { label: "平均收益", value: fmt(m.avg_return_pct), cls: mcls(m.avg_return_pct) },
    { label: "Sharpe≈", value: esc(String(m.sharpe_approx ?? "—")) },
    { label: "交易日", value: esc(String(params.trade_days ?? params.common_dates ?? "—")) },
    { label: "标的数", value: esc(String((data.loaded_stocks || []).length || params.stock_count || "—")) },
    {
      label: "排除短序列",
      value: esc(String(dropN)),
      cls: dropN > 0 ? "down" : "",
    },
    { label: "成本模型", value: esc(costLabel) },
    {
      label: "权重模式",
      value: esc(
        params.weight_mode === "score_budget"
          ? "分数预算"
          : params.weight_mode === "risk_parity_lite"
            ? "风险平价"
            : params.weight_mode === "equal" || !params.weight_mode
              ? "等权"
              : String(params.weight_mode)
      ),
    },
    {
      label: "选股口径",
      value: esc(
        params.apply_tau_buy_gate === true || data.request?.apply_tau_buy_gate === true
          ? "ŷ_trade · τ闸开"
          : "ŷ_oo · 关τ闸"
      ),
    },
    { label: "OOS", value: esc(String(oosLabel)), cls: oosFailed ? "down" : "" },
    { label: "Regime", value: esc(String(regimeLabel)) },
    { label: "数据质量", value: esc(dqLabel), cls: fb > 0 || gated > 0 ? "down" : "" },
    { label: "结果来源", value: "当次回测" },
  ];
  const bench = data.benchmark || {};
  if (bench.ok) {
    cards.push(
      {
        label: `超额·${bench.benchmark_label || "基准"}`,
        value: fmt(bench.excess_pct),
        cls: mcls(bench.excess_pct),
      },
      {
        label: "基准收益",
        value: fmt(bench.benchmark_return_pct),
        cls: mcls(bench.benchmark_return_pct),
      }
    );
    if (bench.ann_excess_pct != null) {
      cards.push({
        label: "年化超额",
        value: fmt(bench.ann_excess_pct),
        cls: mcls(bench.ann_excess_pct),
      });
    }
    if (bench.ann_ir != null || bench.ir != null) {
      cards.push({
        label: "超额IR",
        value: esc(
          String(bench.ann_ir != null ? bench.ann_ir : bench.ir)
        ),
      });
    }
    if (bench.warn_abs_pos_excess_neg) {
      cards.push({
        label: "超额警示",
        value: "绝对+超额−",
        cls: "down",
      });
    }
  }
  const sic = data.score_ic || {};
  if (sic.ok) {
    cards.push(
      { label: "IC均值", value: esc(String(sic.ic_mean ?? "—")) },
      { label: "ICIR", value: esc(String(sic.icir ?? "—")) }
    );
    if (sic.positive_ic_ratio != null) {
      cards.push({
        label: "正IC占比",
        value: `${(Number(sic.positive_ic_ratio) * 100).toFixed(0)}%`,
        cls: Number(sic.positive_ic_ratio) >= 0.55 ? "" : "down",
      });
    }
  }
  if (cc.ok) {
    const gap = cc.return_gap_pp;
    const zRet = (cc.zero || {}).total_return_pct;
    const cRet = (cc.simple_cn || {}).total_return_pct;
    cards.push(
      {
        label: "成本对照Δ",
        value:
          gap != null
            ? `${Number(gap) >= 0 ? "+" : ""}${Number(gap).toFixed(2)}pp`
            : "—",
        cls: gap != null && Number(gap) < 0 ? "down" : "",
      },
      {
        label: "零成本收益",
        value: zRet != null ? `${Number(zRet).toFixed(2)}%` : "—",
        cls: mcls(zRet),
      },
      {
        label: "含成本收益",
        value: cRet != null ? `${Number(cRet).toFixed(2)}%` : "—",
        cls: mcls(cRet),
      }
    );
    if (cc.avg_impact_bps != null && Number(cc.avg_impact_bps) > 0) {
      cards.push({
        label: "均冲击",
        value: `${Number(cc.avg_impact_bps).toFixed(1)} bps`,
      });
    }
  }
  const pitR = data.pit_report || {};
  const fundPit = pitR.fundamentals || {};
  if (pitR.bars_pit) {
    cards.push({
      label: "财务PIT",
      value: pitR.fundamentals_pit
        ? "是"
        : fundPit.missing_as_of
          ? `缺 ${fundPit.missing_as_of}`
          : fundPit.resolved_ok
            ? `部分 ${fundPit.resolved_ok}`
            : "快照/无",
      cls: pitR.fundamentals_pit ? "" : "down",
    });
  }
  const sa = data.source_audit || {};
  if (sa.status && sa.status !== "empty") {
    cards.push({
      label: "源审计",
      value:
        Number(sa.fallback_count || 0) > 0
          ? `fallback ${sa.fallback_count}`
          : sa.status === "ok"
            ? "一致"
            : String(sa.status),
      cls: Number(sa.fallback_count || 0) > 0 || sa.status === "bad" ? "down" : "",
    });
  }
  const attr = data.attribution || {};
  if (attr.ok) {
    const topSec = (attr.by_sector || [])[0];
    cards.push(
      {
        label: "选股超额",
        value:
          attr.selection_excess_pct != null
            ? `${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
            : "—",
        cls: mcls(attr.selection_excess_pct),
      },
      {
        label: "主贡献行业",
        value: topSec
          ? `${topSec.sector} ${Number(topSec.avg_return_pct) >= 0 ? "+" : ""}${topSec.avg_return_pct}%`
          : "—",
      }
    );
  }
  if (data.pit_report && data.pit_report.bars_pit) {
    cards.push({
      label: "PIT日线",
      value: esc(String(params.execution_mode || "as_of")),
    });
  }
  return cards;
}

/**
 * @param {{ escapeHtml?: typeof escapeHtml, fmtPct?: typeof fmtPct, metricClass?: typeof metricClass }} deps
 */
export function createBtResultRenderers(deps) {
  const esc = deps.escapeHtml || escapeHtml;
  const fmt = deps.fmtPct || fmtPct;
  const mcls = deps.metricClass || metricClass;

  function renderMetricCards(host, items) {
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = "";
      return;
    }
    host.innerHTML = items
      .map(
        (it) =>
          `<div class="quant-metric"><span class="label">${esc(it.label)}</span>` +
          `<span class="val ${it.cls || ""}">${it.value}</span></div>`
      )
      .join("");
  }

  function renderBtScopeNote(text, { warn = false } = {}) {
    const el = document.getElementById("quant-bt-scope-note");
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("down");
      return;
    }
    el.hidden = false;
    el.textContent = text;
    if (warn) el.classList.add("down");
    else el.classList.remove("down");
  }

  /** OOS + Walk-forward 稳健性条（验证台第一眼） */
  function renderRobustnessPanel(data) {
    const el = document.getElementById("quant-robustness");
    if (!el) return;
    if (!data || !data.success) {
      el.innerHTML = "";
      return;
    }
    const oos = data.oos_summary || {};
    const wf = data.wf_slices || {};
    const regime = data.regime_summary || {};
    const oosFailed = oos.ok === false || oos.failed === true;
    const isRet = oos.is_return_pct != null ? `${Number(oos.is_return_pct).toFixed(2)}%` : "—";
    const oosRet = oos.oos_return_pct != null ? `${Number(oos.oos_return_pct).toFixed(2)}%` : "—";
    const oosCls = oosFailed ? "down" : "";
    const wfMean =
      wf.mean_test_return_pct != null ? `${Number(wf.mean_test_return_pct).toFixed(2)}%` : "—";
    const wfPos =
      wf.positive_test_folds != null && wf.measured_test_folds != null
        ? `${wf.positive_test_folds}/${wf.measured_test_folds}`
        : "—";
    const foldN = (wf.folds || []).length || wf.fold_count || "—";
    const regimeLabel = regime.regime || (regime.ok === false ? regime.reason || "—" : "—");
    const mcs = data.macro_context_summary || {};
    const macroKpi =
      mcs.ok && mcs.avg_overseas_tech_1d_pct != null
        ? `<div class="quant-validation-kpi"><span class="k">海外科技均</span><span class="v ${mcls(
            mcs.avg_overseas_tech_1d_pct
          )}">${esc(fmt(mcs.avg_overseas_tech_1d_pct))}</span></div>`
        : "";
    const note =
      oosFailed
        ? oos.reason || "OOS 未过闸：样本内好看不等于样本外有效"
        : wf.ok === false && wf.reason
          ? `WF：${wf.reason}`
          : "按权益曲线切分 OOS · WF 为扩展窗测试折；网格扫描默认跳过 WF";
    el.innerHTML =
      `<div class="quant-validation-block">` +
      `<p class="quant-trades-caption">稳健性 · OOS / Walk-forward</p>` +
      `<div class="quant-validation-strip" aria-label="稳健性 KPI">` +
      `<div class="quant-validation-kpi"><span class="k">样本内</span><span class="v">${esc(isRet)}</span></div>` +
      `<div class="quant-validation-kpi"><span class="k">OOS</span><span class="v ${oosCls}">${esc(oosRet)}</span></div>` +
      `<div class="quant-validation-kpi"><span class="k">WF均收益</span><span class="v">${esc(wfMean)}</span></div>` +
      `<div class="quant-validation-kpi"><span class="k">WF正窗</span><span class="v">${esc(String(wfPos))}</span></div>` +
      `<div class="quant-validation-kpi"><span class="k">WF折</span><span class="v">${esc(String(foldN))}</span></div>` +
      `<div class="quant-validation-kpi"><span class="k">Regime</span><span class="v">${esc(String(regimeLabel))}</span></div>` +
      macroKpi +
      `</div>` +
      `<p class="quant-sub">${esc(note)}</p>` +
      `</div>`;
  }

  function buildCards(data) {
    return buildPortfolioBacktestCards(data, { escapeHtml: esc, fmtPct: fmt, metricClass: mcls });
  }

  return {
    renderMetricCards,
    renderBtScopeNote,
    renderRobustnessPanel,
    buildPortfolioBacktestCards: buildCards,
  };
}
