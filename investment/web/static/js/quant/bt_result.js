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

/** 研究包导出用曲线摘要（纯对象，无 DOM）。 */
export function buildResearchCurves(result) {
  if (!result || !result.success) return null;
  const sic = result.score_ic || {};
  const qb = result.quantile_backtest || {};
  const bench = result.benchmark || {};
  const align = result.ic_equity_align || {};
  return {
    equity_curve: result.equity_curve || [],
    benchmark_equity_curve: bench.equity_curve || [],
    ic_series_tail: sic.ic_series_tail || [],
    ic_rolling_tail: sic.ic_rolling_tail || [],
    quantile_curves: (qb.quantiles || []).map((r) => ({
      label: r.label,
      quantile: r.quantile,
      equity_curve_tail: r.equity_curve_tail || [],
    })),
    long_short_equity_curve: qb.long_short_equity_curve || [],
    ic_equity_align: align.ok
      ? {
          avg_return_spread_pp: align.avg_return_spread_pp,
          aligned_favor_pos_ic: align.aligned_favor_pos_ic,
          pos_ic: align.pos_ic,
          neg_ic: align.neg_ic,
          periods_tail: align.periods_tail || [],
        }
      : align,
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
    ? ` · 成交 ${data.params.execution_mode === "next_open" ? "次日开" : "收盘"}`
    : "";
  const dropN = Number(
    data.params?.dropped_thin_count || (data.dropped_stocks || []).length || 0
  );
  const dropNote = dropN > 0 ? ` · 排除短序列 ${dropN}` : "";
  const doN = Number(data.params?.dropout_n ?? data.request?.dropout_n ?? 0);
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
  const qb = data.quantile_backtest || {};
  const qNote = qb.ok
    ? qb.monotonic_increasing
      ? ` · 分层单调↑ Q差${qb.q_high_minus_q_low_pct ?? "—"}%`
      : ` · 分层非单调 Q差${qb.q_high_minus_q_low_pct ?? "—"}%`
    : "";
  const bench = data.benchmark || {};
  const benchNote =
    bench.ok && bench.excess_pct != null
      ? ` · 超额${Number(bench.excess_pct) >= 0 ? "+" : ""}${bench.excess_pct}%(${
          bench.benchmark_label || "基准"
        })` +
        (bench.ann_ir != null ? ` · IR ${bench.ann_ir}` : "")
      : "";
  const text =
    `标的 ${(data.loaded_stocks || []).length} · 共同日 ${data.params?.common_dates} · 交易 ${m.trade_count} · 累计 ${m.total_return_pct}% · 胜率 ${m.win_rate_pct}% · 成本 ${
      costModel === "simple_cn" ? "A股简化" : costModel
    }${data.params?.neutralize ? ` · 中性化 ${data.params?.neutralized_rebalances || 0} 次` : ""}${fundNote}${oosNote}${regimeNote}${dqNote}${costCmpNote}${wfNote}${attrNote}${pitNote}${auditNote}${matchNote}${dropNote}${dropoutNote}${icNote}${qNote}${benchNote}`;
  const qBad = qb.ok && qb.monotonic_increasing === false;
  return { text, warn: !!(oosFailed || qBad) };
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
    { label: "共同交易日", value: esc(String(params.common_dates ?? "—")) },
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
  const align = data.ic_equity_align || {};
  if (align.ok && align.avg_return_spread_pp != null) {
    cards.push({
      label: "IC窗收益差",
      value: `${Number(align.avg_return_spread_pp) >= 0 ? "+" : ""}${align.avg_return_spread_pp}pp`,
      cls: align.aligned_favor_pos_ic === false ? "down" : mcls(align.avg_return_spread_pp),
    });
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
 * @param {{ escapeHtml?: typeof escapeHtml, fmtPct?: typeof fmtPct, metricClass?: typeof metricClass, researchGridHtml: Function }} deps
 */
export function createBtResultRenderers(deps) {
  const esc = deps.escapeHtml || escapeHtml;
  const fmt = deps.fmtPct || fmtPct;
  const mcls = deps.metricClass || metricClass;
  const researchGridHtml = deps.researchGridHtml;

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

  function renderFitGapPanel(data) {
    const el = document.getElementById("quant-fit-gap");
    if (!el) return;
    if (!data || !data.ok) {
      el.innerHTML = "";
      return;
    }
    const hints = data.hints || [];
    const dd = data.day_diff || {};
    let html =
      `<p class="quant-trades-caption">回测–纸面落差归因（启发式 · warn ${
        data.warn_count ?? 0
      }）</p>` +
      researchGridHtml(
        [
          { id: "level", label: "级别", widthPct: 14, center: true },
          { id: "code", label: "码", widthPct: 22 },
          { id: "message", label: "说明", flex: true },
        ],
        hints.map((h) => ({
          level: h.level || "info",
          code: h.code || "",
          message: h.message || "",
          isWarn: (h.level || "") === "warn",
        })),
        (col, d) => esc(d[col.id] ?? "—"),
        {
          emptyText: "无归因项",
          rowClass: (d) => (d.isWarn ? "down" : ""),
        }
      );
    if (dd && (dd.aligned_days != null || (dd.day_gaps || []).length)) {
      html +=
        `<p class="quant-trades-caption">同窗日 Diff · 对齐 ${esc(
          String(dd.aligned_days ?? 0)
        )} · 纸面独有 ${esc(String(dd.paper_only_days ?? 0))} · 回测独有 ${esc(
          String(dd.bt_only_days ?? 0)
        )}</p>`;
      const gaps = (dd.day_gaps || []).slice(0, 8);
      if (gaps.length) {
        html += researchGridHtml(
          [
            { id: "date", label: "日", widthPct: 22 },
            { id: "paper_ret_pct", label: "纸面%", widthPct: 18, center: true },
            { id: "bt_ret_pct", label: "回测%", widthPct: 18, center: true },
            { id: "gap_pp", label: "Δpp", widthPct: 18, center: true },
          ],
          gaps.map((g) => ({
            date: g.date || "—",
            paper_ret_pct:
              g.paper_ret_pct != null ? Number(g.paper_ret_pct).toFixed(2) : "—",
            bt_ret_pct:
              g.bt_ret_pct != null ? Number(g.bt_ret_pct).toFixed(2) : "—",
            gap_pp: g.gap_pp != null ? Number(g.gap_pp).toFixed(2) : "—",
            isWarn: Math.abs(Number(g.gap_pp) || 0) >= 1,
          })),
          (col, d) => esc(d[col.id] ?? "—"),
          {
            emptyText: "无日差样本",
            rowClass: (d) => (d.isWarn ? "down" : ""),
          }
        );
      }
    }
    html += `<p class="quant-sub">${esc(data.note || "")} · 拟合 KPI 见 <a href="/platform">平台北极星</a> · 枢纽常驻见 <a href="/quant">研究枢纽</a></p>`;
    el.innerHTML = html;
  }

  function buildCards(data) {
    return buildPortfolioBacktestCards(data, { escapeHtml: esc, fmtPct: fmt, metricClass: mcls });
  }

  return {
    renderMetricCards,
    renderBtScopeNote,
    renderFitGapPanel,
    buildPortfolioBacktestCards: buildCards,
  };
}
