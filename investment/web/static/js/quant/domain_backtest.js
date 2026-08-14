import { apiFetch } from "../api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart } from "../lw_charts.js";
import { mountVirtualTable } from "../virtual_table.js";
import { createScoreTooltipController } from "../score_tooltip.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { portfolioBtScoreFloorPayload as buildBtScoreFloorPayload } from "./scoring.js";
import { truncateStockName, watchingNameSpanHtml } from "./names.js";
import {
  simTradesIntentDiffers,
  btSimTradeColumns,
  buildSimTradesCsv,
  resolveSimTradeLegs,
  buildSimTradeRows,
  simTradesCaptionHtml,
  btTradesNumCompare,
  btTradesCellHtml,
  flattenTradesToSimLegs,
  formatFactorWeightsNote,
  formatSimStatus,
} from "./bt_trades.js";
import { readPromoteHardGate, buildPromoteHintsPack, savePromoteHintsPack } from "./promote_cache.js";
import { drawParamHeatmap, paramGridDullness, buildParamGridMetaText, buildParamGridTableHtml } from "./param_grid_ui.js";
import { renderNeutralCompareTable as renderNeutralCompareTableHtml } from "./neutral_compare.js";
import { buildUniversePanelHtml } from "./universe_ui.js";
import {
  fmtPct,
  metricClass,
  buildPortfolioBacktestSummaryText,
  buildPortfolioBacktestFailText,
  applyReplayOverviewKpis,
} from "./bt_result.js?v=p984";
import { downloadBlob } from "../shared.js";

/** Top-K 净值图横轴只展示最近 N 个自然日（含末日）。 */
export const TOPK_NAV_CHART_WINDOW_DAYS = 15;

function _curvePointDate(p) {
  return String((p && (p.date || p.ts || p.time)) || "").slice(0, 10);
}

export function sliceCurveToDateWindow(series, days = TOPK_NAV_CHART_WINDOW_DAYS) {
  const rows = Array.isArray(series) ? series : [];
  if (!rows.length) return rows;
  const last = _curvePointDate(rows[rows.length - 1]);
  const n = Math.max(2, Number(days) || TOPK_NAV_CHART_WINDOW_DAYS);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(last)) return rows.slice(-n);
  const endMs = Date.parse(`${last}T00:00:00Z`);
  if (!Number.isFinite(endMs)) return rows.slice(-n);
  const startStr = new Date(endMs - (n - 1) * 86400000).toISOString().slice(0, 10);
  const sliced = rows.filter((p) => _curvePointDate(p) >= startStr);
  return sliced.length >= 2 ? sliced : rows.slice(-n);
}

/** Quant domain: backtest */
export function installBacktest(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText, btSimScoreTips } = q;
  const { fmtPct, metricClass, renderMetricCards, renderBtScopeNote, renderFitGapPanel, renderRobustnessPanel, buildPortfolioBacktestCards, renderPromoteHintsPanel, BT_SCOPE_LIVE, BT_SCOPE_FROZEN, readHorizonDays, quantBtBusyIds } = q;
  const { renderAttributionTablesHtml, renderIcEquityAlignHtml, renderQuantileTableHtml, buildT0BacktestMetrics, buildT0BacktestDaysHtml, buildCrossSectionResult, renderScoreIcHtml } = q;
  const { researchGridHtml, metricCell } = q;

  function buildIcAlignMarkers(align) {
    const periods = (align && align.ok && align.periods_tail) || [];
    const out = [];
    const seen = new Set();
    periods.forEach((p) => {
      const t = String(p.end_date || "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(t) || seen.has(t)) return;
      seen.add(t);
      const pos = p.bucket === "pos";
      out.push({
        time: t,
        position: "belowBar",
        color: pos ? "#059669" : "#9ca3af",
        shape: "circle",
        text: pos ? "+" : "−",
      });
    });
    return out.slice(-40);
  }

  function cachePromoteHintsFromBacktest(data) {
    if (!data || !data.success) return;
    const pack = buildPromoteHintsPack(data, {
      hardGateAtCache: readPromoteHardGate(),
    });
    savePromoteHintsPack(pack);
    renderPromoteHintsPanel(pack, "quant-promote-hints");
    renderPromoteHintsPanel(pack, "strategy-promote-hints");
  }

  function clearBtTradesTable() {
    state.btTradesTableApi = null;
    state.lastSimTrades = [];
    if (els.quantBtTrades) els.quantBtTrades.innerHTML = "";
  }

  function downloadSimTradesCsv(rows) {
    const blob = new Blob([buildSimTradesCsv(rows, state.watchingNameByCode)], {
      type: "text/csv;charset=utf-8",
    });
    downloadBlob(blob, `topk_sim_trades_${new Date().toISOString().slice(0, 10)}.csv`);
  }

  async function fitReturnScoreModel() {
    setQuantBtBusy(true, "拟合收益排序模型…");
    try {
      const { lookback, horizon_days } = readPortfolioBtParams();
      const { ok, data, error } = await apiFetch("/api/quant/return-model/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback,
          horizon_days,
          watching_limit: 12,
          save_draft: true,
        }),
      });
      if (!ok || !data || !data.success) {
        const msg = (data && (data.error || data.detail)) || error || "拟合失败";
        if (els.quantPortfolioSummary) {
          els.quantPortfolioSummary.textContent = msg;
          els.quantPortfolioSummary.classList.add("down");
        }
        return;
      }
      const draft = data.draft || {};
      const msg =
        `ŷ模型已拟合并落草稿 n=${data.sample_count} R²=${(data.ols && data.ols.r_squared) ?? "—"}` +
        (draft.path ? ` · ${draft.path}` : "") +
        " · 人审 promote 后配合 scoring.rank_mode=predicted_score";
      if (els.quantPortfolioSummary) {
        els.quantPortfolioSummary.classList.remove("down");
        els.quantPortfolioSummary.textContent = msg;
      }
    } finally {
      setQuantBtBusy(false);
    }
  }

  function formatSnapshotAt(iso) {
    if (!iso) return "—";
    const s = String(iso);
    return s.length > 19 ? s.slice(0, 19).replace("T", " ") : s.replace("T", " ");
  }

  async function loadFitGapForBacktest(bt) {
    const { ok, data } = await apiFetch("/api/ops/fit-gap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result: bt || {} }),
    });
    if (ok) renderFitGapPanel(data);
    else renderFitGapPanel(null);
  }

  async function loadLastBacktestSnapshot() {
    try {
      const res = await fetch("/api/quant/last");
      const data = await res.json();
      if (!data || data.empty) return;
      if (data.portfolio_backtest_summary && data.portfolio_backtest_summary.success) {
        const ps = data.portfolio_backtest_summary;
        const nc = data.portfolio_neutral_compare_summary;
        const meta = data.snapshot_meta || {};
        const at = formatSnapshotAt(meta.generated_at || data.generated_at);
        let summary = `日报冻结摘要 · ${at} · 累计 ${ps.total_return_pct ?? "—"}% · 胜率 ${ps.win_rate_pct ?? "—"}% · 交易 ${ps.trade_count ?? "—"}`;
        if (nc && nc.success) {
          summary += ` · 中性化 Δ${nc.delta?.total_return_pct ?? "—"}% (${nc.winner})`;
        }
        els.quantPortfolioSummary.textContent = summary;
        renderBtScopeNote(`${BT_SCOPE_FROZEN} · 冻结于 ${at}`, { warn: true });
        renderMetricCards(els.quantBtMetrics, [
          { label: "累计收益", value: fmtPct(ps.total_return_pct), cls: metricClass(ps.total_return_pct) },
          { label: "胜率", value: fmtPct(ps.win_rate_pct) },
          { label: "交易次数", value: escapeHtml(String(ps.trade_count ?? "—")) },
          { label: "结果来源", value: "日报冻结" },
          { label: "冻结时间", value: escapeHtml(at) },
        ]);
        if (nc && nc.success) {
          state.neutralCompareSource = "frozen";
          renderNeutralCompareTable(nc, null, {
            frozen: true,
            frozenAt: at,
          });
        } else {
          renderNeutralCompareTable(null);
        }
        paintPortfolioChart(ps.equity_curve_tail, "无组合摘要曲线");
        applyReplayOverviewKpis(ps, { source: `冻结 ${at}` });
        setBtTradesCaption("日报仅含摘要曲线；点击「Top-K 回测」加载完整模拟成交账");
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function paintDualPortfolioChart(seriesA, seriesB, emptyText) {
    const host = els.quantPortfolioChart;
    if (!host) return;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (legendEl) {
      legendEl.textContent =
        "中性化对照双曲线。起点 100；横轴近15日（持有期结束日）。蓝=截面中性化ŷ · 绿=未中性化ŷ（同一观察池与参数）。";
    }
    await renderDualLineChart(
      host,
      sliceCurveToDateWindow(seriesA),
      sliceCurveToDateWindow(seriesB),
      { emptyText, disableZoom: true }
    );
  }

  async function paintIcChart(sic) {
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!chartHost || !chartWrap) return;
    const daily = sic.ic_series_tail || [];
    const rolling = sic.ic_rolling_tail || [];
    const toPts = (rows, key) =>
      (rows || []).map((p, i) => {
        const t = String(p.date || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p[key] ?? p.ic),
        };
      });
    const dPts = toPts(daily, "ic");
    const rPts = toPts(rolling, "ic");
    if (dPts.length < 2 && rPts.length < 2) {
      chartWrap.hidden = true;
      return;
    }
    chartWrap.hidden = false;
    const series = [];
    if (dPts.length >= 2) {
      series.push({ label: "日度IC", color: "#93c5fd", lineWidth: 1.5, points: dPts });
    }
    if (rPts.length >= 2) {
      series.push({ label: "滚动IC", color: "#059669", lineWidth: 2.5, points: rPts });
    }
    // 零轴参考：用极短水平线不合适；双线足够。若仅一条也画。
    if (series.length === 1) {
      await renderLineChart(chartHost, series[0].points, {
        emptyText: "IC 序列不足",
        disableZoom: true,
        zeroLine: true,
        color: series[0].color,
      });
      return;
    }
    await renderMultiLineChart(chartHost, series, {
      emptyText: "IC 序列不足",
      disableZoom: true,
      zeroLine: true,
    });
  }

  async function paintNeutralCompareChart(data) {
    const nCurve = (data.neutralized && data.neutralized.equity_curve) || [];
    const aCurve = (data.absolute && data.absolute.equity_curve) || [];
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity ?? p.equity_norm ?? p.value),
        };
      });
    const nPts = toPts(sliceCurveToDateWindow(nCurve));
    const aPts = toPts(sliceCurveToDateWindow(aCurve));
    const benchCurve =
      (data.neutralized && data.neutralized.benchmark && data.neutralized.benchmark.equity_curve) ||
      (data.absolute && data.absolute.benchmark && data.absolute.benchmark.equity_curve) ||
      [];
    const bPts = toPts(sliceCurveToDateWindow(benchCurve));
    const host = els.quantPortfolioChart;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (!host) return;
    const series = [];
    if (nPts.length >= 2) {
      series.push({ label: "中性化", color: "#2563eb", lineWidth: 2, points: nPts });
    }
    if (aPts.length >= 2) {
      series.push({ label: "未中性化ŷ", color: "#059669", lineWidth: 2, points: aPts });
    }
    if (bPts.length >= 2) {
      series.push({ label: "基准", color: "#9ca3af", lineWidth: 1.5, points: bPts });
    }
    if (series.length >= 2) {
      if (legendEl) {
        legendEl.textContent =
          "中性化对照：蓝=截面中性化 · 绿=未中性化ŷ" +
          (bPts.length >= 2 ? " · 灰=同一基准买持" : "") +
          "。起点 100；横轴近15日；超额见对照表。";
      }
      await renderMultiLineChart(host, series, {
        emptyText: "对照曲线不足",
        disableZoom: true,
      });
      return;
    }
    await paintDualPortfolioChart(nCurve, aCurve, "对照曲线不足");
  }

  async function paintPortfolioChart(curve, emptyText, benchCurve, benchLabel, icAlign) {
    const host = els.quantPortfolioChart;
    if (!host) return;
    const legendEl = document.getElementById("quant-portfolio-legend");
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity ?? p.equity_norm ?? p.value),
        };
      });
    const markers = buildIcAlignMarkers(icAlign);
    const windowed = sliceCurveToDateWindow(curve);
    const windowedBench = sliceCurveToDateWindow(benchCurve);
    const winStart = _curvePointDate(windowed[0] || {});
    const pts = toPts(windowed);
    const bpts = toPts(windowedBench);
    const winMarkers =
      winStart && /^\d{4}-\d{2}-\d{2}$/.test(winStart)
        ? markers.filter((m) => String(m.time || "") >= winStart)
        : markers;
    if (bpts.length >= 2 && pts.length >= 2) {
      if (legendEl) {
        legendEl.textContent =
          `Top-K 净值（蓝）vs ${benchLabel || "基准"}（绿）。` +
          (markers.length
            ? "标记：绿点=正IC窗结束 · 灰点=非正IC窗。横轴近15日。"
            : "起点 100；横轴近15日（调仓期结束日）。") +
          "超额看指标卡，勿只看绝对累计。";
      }
      await renderDualLineChart(host, pts, bpts, {
        emptyText,
        disableZoom: true,
        markers: winMarkers,
      });
      return;
    }
    if (legendEl) {
      legendEl.textContent =
        "研究用 Top-K 回测净值（非纸面账本）。起点 100。" +
        (markers.length
          ? " 标记：绿点=正IC窗结束 · 灰点=非正IC窗。"
          : " 单线=当次回测；对照时蓝=中性化、绿=未中性化ŷ。") +
        " 横轴近15日。";
    }
    await renderLineChart(host, pts, { emptyText, disableZoom: true, markers: winMarkers });
  }

  async function paintQuantileChart(qb) {
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!chartHost || !chartWrap) return;
    const rows = (qb && qb.quantiles) || [];
    const series = rows
      .map((r, i) => {
        const curve = r.equity_curve_tail || r.equity_curve || [];
        if (!curve.length) return null;
        const n = rows.length;
        const isEdge = i === 0 || i === n - 1;
        return {
          label: r.label || `Q${r.quantile}`,
          color: Q_COLORS[Math.min(i, Q_COLORS.length - 1)],
          lineWidth: isEdge ? 2.5 : 1.5,
          points: curve.map((p, j) => {
            const t = String(p.date || "").slice(0, 10);
            return {
              time: /^\d{4}-\d{2}-\d{2}$/.test(t)
                ? t
                : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
              value: Number(p.equity ?? p.value),
            };
          }),
        };
      })
      .filter(Boolean);
    const ls = qb.long_short_equity_curve || [];
    if (ls.length >= 2) {
      series.push({
        label: "Q高−Q低",
        color: "#b45309",
        lineWidth: 2.5,
        points: ls.map((p, j) => {
          const t = String(p.date || "").slice(0, 10);
          return {
            time: /^\d{4}-\d{2}-\d{2}$/.test(t)
              ? t
              : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
            value: Number(p.equity ?? p.value),
          };
        }),
      });
    }
    if (series.length < 2) {
      chartWrap.hidden = true;
      return;
    }
    chartWrap.hidden = false;
    await renderMultiLineChart(chartHost, series, {
      emptyText: "分层曲线不足",
      disableZoom: true,
    });
  }

  function paramGridApplyGate() {
    const best = state.lastParamGrid && state.lastParamGrid.best;
    if (!best) {
      return { ok: false, reason: "无最优单元" };
    }
    const bt = state.lastBacktestPack && state.lastBacktestPack.result;
    if (!bt || !bt.success) {
      return {
        ok: false,
        reason: "请先用该 lookback/top_k 跑一次「Top-K 回测」",
      };
    }
    const req = bt.request || {};
    const lb = Number(req.lookback);
    const tk = Number(req.top_k);
    if (lb !== Number(best.lookback) || tk !== Number(best.top_k)) {
      return {
        ok: false,
        reason: `当次回测为 lookback=${lb || "—"}/top_k=${tk || "—"}，与最优 ${best.lookback}/${best.top_k} 不一致`,
      };
    }
    const oos = bt.oos_summary || {};
    if (!oos.ok || oos.failed) {
      return {
        ok: false,
        reason: `OOS 未通过（${oos.reason || "失败"}）；禁止仅凭样本内最优应用`,
      };
    }
    const align = bt.ic_equity_align || {};
    const hints = Array.isArray(bt.promote_hints) ? bt.promote_hints : [];
    const alignWarn =
      (align.ok && align.aligned_favor_pos_ic === false) ||
      hints.some((h) => h && h.code === "ic_align_mismatch");
    if (alignWarn && readPromoteHardGate()) {
      return {
        ok: false,
        reason:
          "IC硬闸已开：正IC窗未优于非正，禁止应用最优（可关闭硬闸后仅二次确认）",
      };
    }
    if (alignWarn) {
      return {
        ok: true,
        warn: true,
        reason:
          "OOS 已过，但正IC窗未优于非正（打分与 Top-K 时段可能不同向）；应用前请确认",
      };
    }
    return { ok: true, reason: "已对齐最优参数且 OOS 通过" };
  }

  function readPortfolioBtParams() {
    const lbEl = document.getElementById("quant-lookback");
    const tkEl = document.getElementById("quant-top-k");
    const wmEl = document.getElementById("quant-weight-mode");
    const dropEl = document.getElementById("quant-dropout-n");
    const stEl = document.getElementById("quant-exclude-st");
    const amtEl = document.getElementById("quant-min-amount-pctile");
    const benchEl = document.getElementById("quant-benchmark-code");
    let lookback = 120;
    let topK = 3;
    let weightMode = "equal";
    let dropoutN = 0;
    let excludeSt = false;
    let minAvgAmountPctile = null;
    let benchmarkCode = "000300";
    if (lbEl && lbEl.value !== "") {
      const n = Number(lbEl.value);
      if (Number.isFinite(n)) lookback = Math.max(40, Math.min(500, Math.round(n)));
    }
    if (tkEl && tkEl.value !== "") {
      const n = Number(tkEl.value);
      if (Number.isFinite(n)) topK = Math.max(1, Math.min(10, Math.round(n)));
    }
    if (wmEl && wmEl.value) {
      const allowed = new Set(["equal", "score_budget", "risk_parity_lite"]);
      if (allowed.has(String(wmEl.value))) weightMode = String(wmEl.value);
    }
    if (dropEl && dropEl.value !== "") {
      const n = Number(dropEl.value);
      if (Number.isFinite(n)) dropoutN = Math.max(0, Math.min(10, Math.round(n)));
    }
    if (stEl) excludeSt = !!stEl.checked;
    if (amtEl && amtEl.value !== "") {
      const n = Number(amtEl.value);
      if (Number.isFinite(n) && n > 0) {
        minAvgAmountPctile = Math.max(0, Math.min(90, n));
      }
    }
    if (benchEl && benchEl.value) {
      const allowedB = new Set(["000300", "000905", "399006", "pool"]);
      if (allowedB.has(String(benchEl.value))) benchmarkCode = String(benchEl.value);
    }
    const rankMode = "predicted_score";
    return {
      lookback,
      top_k: topK,
      horizon_days: readHorizonDays(),
      weight_mode: weightMode,
      dropout_n: dropoutN,
      exclude_st: excludeSt,
      min_avg_amount_pctile: minAvgAmountPctile,
      benchmark_code: benchmarkCode,
      rank_mode: rankMode,
    };
  }

  function refreshParamGridApplyGate() {
    const applyBestBtn = document.getElementById("quant-param-grid-apply-best");
    const meta = document.getElementById("param-grid-meta");
    if (!applyBestBtn) return;
    const gate = paramGridApplyGate();
    applyBestBtn.disabled = !gate.ok;
    if (meta && state.lastParamGrid && state.lastParamGrid.best) {
      const best = state.lastParamGrid.best;
      const base = `最优(样本内) lookback=${best.lookback} · top_k=${best.top_k} · 收益 ${best.total_return_pct}% · ${state.lastParamGrid.cell_count} 格`;
      if (!gate.ok) {
        meta.textContent = `${base} · 不可应用：${gate.reason}`;
      } else if (gate.warn) {
        meta.textContent = `${base} · ⚠可应用但需确认：${gate.reason}`;
      } else {
        meta.textContent = `${base} · 可应用（OOS 已核对）`;
      }
    }
  }

  function renderAttributionTables(attr) {
    const el = document.getElementById("quant-attr-tables");
    if (!el) return;
    if (!attr || !attr.ok) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = renderAttributionTablesHtml(attr);
  }

  function renderBtTradesTable(dataOrTrades) {
    if (!els.quantBtTrades) return;
    const fillEl = document.getElementById("quant-signal-fill");
    if (fillEl) fillEl.innerHTML = "";
    const legs = resolveSimTradeLegs(dataOrTrades);
    if (!legs.length) {
      setBtTradesCaption("暂无模拟成交记录 · 请先跑 Top-K 回测");
      return;
    }
    state.lastSimTrades = legs;
    const filled = legs.filter((r) => (r.status || "filled") === "filled").length;
    const skipped = legs.length - filled;
    const showIntent = simTradesIntentDiffers(legs);
    const rowDeps = {
      nameByCode: state.watchingNameByCode,
      fmtPct,
      metricClass,
      fmtScore,
      scoreCls,
    };
    const rows = buildSimTradeRows(legs, rowDeps);
    const cellDeps = { escapeHtml, watchingNameSpanHtml };
    els.quantBtTrades.innerHTML = simTradesCaptionHtml({
      rowsLen: rows.length,
      filled,
      skipped,
      showIntent,
    });
    const csvBtn = document.getElementById("quant-bt-trades-csv");
    if (csvBtn) {
      csvBtn.addEventListener("click", () => downloadSimTradesCsv(state.lastSimTrades));
    }
    const host = els.quantBtTrades.querySelector(".quant-bt-trades-host");
    state.btTradesTableApi = mountVirtualTable(host, {
      columns: btSimTradeColumns(showIntent),
      emptyText: "暂无模拟成交记录",
      rowHeight: 38,
      rootClass: "watching-react-grid",
      bodyClass: "quant-bt-trades-body",
      compare: btTradesNumCompare,
      cellHtml: (col, d) => btTradesCellHtml(col, d, cellDeps),
    });
    state.btTradesTableApi.setRows(rows);
    if (els.quantBtTrades.dataset.scoreTipWired !== "1") {
      btSimScoreTips.bindHost(els.quantBtTrades, {
        scoreSelector: ".bt-trade-score[data-score-detail], .paper-hold-score[data-score-detail]",
      });
    }
  }

  function renderCostAssumptions(ca) {
    const el = document.getElementById("quant-cost-assumptions");
    if (!el) return;
    if (!ca || !ca.ok) {
      el.innerHTML = ca && ca.reason
        ? `<p class="quant-attr-note">成本假设不可用：${escapeHtml(ca.reason)}</p>`
        : "";
      return;
    }
    const model =
      ca.model === "zero"
        ? "零成本（教学）"
        : ca.cost_mode === "turnover"
          ? "A股简化·按换手"
          : "A股简化";
    const slipLabel =
      ca.max_slippage_bps != null
        ? `${ca.base_slippage_bps ?? "—"}≤${ca.max_slippage_bps}`
        : String(ca.base_slippage_bps ?? "—");
    const turn =
      ca.turnover_cost_sum_pct != null && ca.cost_mode === "turnover"
        ? `${Number(ca.turnover_cost_sum_pct).toFixed(2)}%`
        : "—";
    el.innerHTML =
      researchGridHtml(
        [
          { id: "model", label: "成本模型", flex: true },
          { id: "commission", label: "佣金bps", widthPct: 12, num: true },
          { id: "stamp", label: "印花税bps(卖)", widthPct: 14, num: true },
          { id: "slip", label: "滑点bps", widthPct: 14, num: true },
          { id: "turn", label: "累计换手成本", widthPct: 14, num: true },
          { id: "rt", label: "示意往返%", widthPct: 12, num: true },
        ],
        [
          {
            model,
            commission: String(ca.commission_bps ?? "—"),
            stamp: String(ca.stamp_duty_bps_sell ?? "—"),
            slip: slipLabel,
            turn,
            rt: String(ca.round_trip_pct_on_100x100 ?? "—"),
          },
        ]
      ) + `<p class="quant-attr-note">${escapeHtml(ca.note || "")}</p>`;
  }

  function renderIcEquityAlign(align) {
    const el = document.getElementById("quant-ic-align");
    if (!el) return;
    el.innerHTML = renderIcEquityAlignHtml(align);
  }

  function renderNeutralCompareTable(source, targetEl, opts = {}) {
    renderNeutralCompareTableHtml(source, targetEl || els.quantNeutralCompareTable, opts);
  }

  function renderParamGridResult(data) {
    const meta = document.getElementById("param-grid-meta");
    const table = document.getElementById("param-grid-table");
    if (!data || !data.success) {
      if (meta) meta.textContent = (data && data.error) || "网格失败";
      return;
    }
    state.lastParamGrid = data;
    const dull = paramGridDullness(data);
    if (dull) data.dullness = dull;
    if (meta) meta.textContent = buildParamGridMetaText(data, dull);
    drawParamHeatmap(data.cells || [], data.axes || {}, { best: data.best });
    refreshParamGridApplyGate();
    if (!table) return;
    table.innerHTML = buildParamGridTableHtml(data, {
      researchGridHtml,
      metricCell,
      fmtPct,
      metricClass,
      escapeHtml,
    });
  }

  function renderPortfolioBacktestResult(data) {
    if (!data || !data.success) {
      applyReplayOverviewKpis(null);
      renderMetricCards(els.quantBtMetrics, []);
      renderRobustnessPanel(null);
      renderWfSlices(null);
      renderCostAssumptions(null);
      renderAttributionTables(null);
      renderRegimeBuckets(null);
      renderSignalFillTable(null);
      renderUniversePanel(null);
      renderScoreIc(null);
      renderIcEquityAlign(null);
      renderQuantileTable(null);
      renderFitGapPanel(null);
      const expBtn = document.getElementById("quant-backtest-report-export");
      if (expBtn) expBtn.disabled = true;
      clearBtTradesTable();
      return;
    }
    if (state.neutralCompareSource === "frozen") {
      renderBtScopeNote(
        BT_SCOPE_LIVE + " · 下方中性化对照仍为日报冻结，请点「中性化对照」刷新。",
        { warn: true }
      );
    } else {
      const bench = data.benchmark || {};
      const excessWarn =
        bench.warn_abs_pos_excess_neg
          ? " · ⚠ 绝对收益为正但超额为负（可能只是 beta/池涨）"
          : "";
      renderBtScopeNote(BT_SCOPE_LIVE + excessWarn, {
        warn: !!bench.warn_abs_pos_excess_neg,
      });
    }
    const m = data.metrics || {};
    ctx.lastBacktestMetrics = {
      max_drawdown_pct: m.max_drawdown_pct,
      win_rate_pct: m.win_rate_pct,
      total_return_pct: m.total_return_pct,
    };
    renderMetricCards(els.quantBtMetrics, buildPortfolioBacktestCards(data));
    applyReplayOverviewKpis(data);
    renderRobustnessPanel(data);
    renderUniversePanel(data.universe);
    renderWfSlices(data.wf_slices);
    renderCostAssumptions(data.cost_assumptions);
    renderAttributionTables(data.attribution);
    renderRegimeBuckets(data.regime_buckets);
    // 信号–成交已并入模拟成交账
    renderSignalFillTable(null);
    renderScoreIc(data.score_ic);
    renderIcEquityAlign(data.ic_equity_align);
    renderQuantileTable(data.quantile_backtest);
    state.lastBacktestPack = {
      kind: "portfolio_backtest",
      exported_at: new Date().toISOString(),
      result: data,
    };
    cachePromoteHintsFromBacktest(data);
    refreshParamGridApplyGate();
    const expBtn = document.getElementById("quant-backtest-report-export");
    if (expBtn) expBtn.disabled = false;

    renderBtTradesTable(data);
  }

  function renderQuantileTable(qb) {
    const el = document.getElementById("quant-quantile-table");
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!el) return;
    if (!qb) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    const { html, showChart } = renderQuantileTableHtml(qb);
    el.innerHTML = html;
    if (chartWrap) chartWrap.hidden = !showChart;
    if (!showChart && chartHost) chartHost.replaceChildren();
    if (showChart) paintQuantileChart(qb);
  }

  function renderRegimeBuckets(rb) {
    const el = document.getElementById("quant-regime-buckets");
    if (!el) return;
    if (!rb || !rb.ok || !(rb.buckets || []).length) {
      el.innerHTML = rb && rb.reason
        ? `<p class="quant-attr-note">Regime 分桶不可用：${escapeHtml(rb.reason)}</p>`
        : "";
      return;
    }
    el.innerHTML =
      `<p class="quant-trades-caption">Regime 分桶 · 已标注 ${escapeHtml(
        String(rb.tagged_trades ?? 0)
      )} 笔</p>` +
      researchGridHtml(
        [
          { id: "regime", label: "Regime", flex: true },
          { id: "n", label: "笔数", widthPct: 16, num: true },
          { id: "ret", label: "均收益", widthPct: 20, num: true },
          { id: "win", label: "胜率", widthPct: 18, num: true },
        ],
        (rb.buckets || []).map((b) => ({
          regime: b.regime || "—",
          n: String(b.trade_count ?? "—"),
          retText: fmtPct(b.avg_return_pct),
          retCls: metricClass(b.avg_return_pct),
          win: fmtPct(b.win_rate_pct),
        })),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          if (col.id === "win") return escapeHtml(d.win);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      `<p class="quant-attr-note">${escapeHtml(rb.note || "")}</p>`;
  }

  function renderScoreIc(sic) {
    const el = document.getElementById("quant-score-ic");
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!el) return;
    if (!sic) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    const { html, hideChart } = renderScoreIcHtml(sic);
    el.innerHTML = html;
    if (chartWrap) chartWrap.hidden = !!hideChart;
    if (hideChart && chartHost) chartHost.replaceChildren();
    else if (!hideChart) paintIcChart(sic);
  }

  function renderSignalFillTable(_rows) {
    const el = document.getElementById("quant-signal-fill");
    if (el) el.innerHTML = "";
  }

  function renderT0BacktestResult(data) {
    if (!data || !data.success) {
      renderMetricCards(els.quantT0Metrics, []);
      if (els.quantT0Days) els.quantT0Days.innerHTML = "";
      return;
    }
    renderMetricCards(els.quantT0Metrics, buildT0BacktestMetrics(data));
    if (els.quantT0Days) els.quantT0Days.innerHTML = buildT0BacktestDaysHtml(data);
  }

  function renderUniversePanel(uni) {
    const el = document.getElementById("quant-universe-panel");
    if (!el) return;
    el.innerHTML = buildUniversePanelHtml(uni);
  }

  function renderWfSlices(wf) {
    const host = document.getElementById("quant-wf-slices");
    if (!host) return;
    if (!wf) {
      host.innerHTML = "";
      return;
    }
    if (!wf.ok && !(wf.folds || []).length) {
      host.innerHTML = `<p class="quant-trades-caption">Walk-forward：${escapeHtml(wf.reason || "不可用")}</p>`;
      return;
    }
    const folds = wf.folds || [];
    const mean = wf.mean_test_return_pct;
    const pos = wf.positive_test_folds;
    const meas = wf.measured_test_folds;
    const head =
      `<p class="quant-trades-caption">Walk-forward ${folds.length} 折` +
      (mean != null ? ` · 测试段均收益 ${Number(mean).toFixed(2)}%` : "") +
      (pos != null && meas != null ? ` · 正窗 ${pos}/${meas}` : "") +
      (wf.fail_folds ? ` · 失败 ${wf.fail_folds}` : "") +
      `</p>`;
    if (!folds.length) {
      host.innerHTML = head;
      return;
    }
    host.innerHTML =
      head +
      researchGridHtml(
        [
          { id: "fold", label: "折", widthPct: 8, num: true, center: true },
          { id: "range", label: "测试段", flex: true },
          { id: "ret", label: "测试收益", widthPct: 14, num: true },
          { id: "dd", label: "窗口回撤", widthPct: 14, num: true },
          { id: "trades", label: "交易", widthPct: 10, num: true },
          { id: "status", label: "状态", widthPct: 14, center: true },
        ],
        folds.map((f) => {
          const ret = f.test_return_pct;
          return {
            fold: String(f.fold ?? "—"),
            range: `${f.test_start_date || "—"} → ${f.test_end_date || "—"}`,
            retText: ret != null ? `${Number(ret).toFixed(2)}%` : "—",
            retCls: metricClass(ret),
            dd:
              f.max_drawdown_pct != null ? `${Number(f.max_drawdown_pct).toFixed(2)}%` : "—",
            trades: String(f.trade_count ?? "—"),
            status: f.ok ? "ok" : f.reason || "失败",
          };
        }),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          return escapeHtml(d[col.id] ?? "—");
        }
      );
  }

  async function runParamGrid() {
    const prog = document.getElementById("param-grid-progress");
    const progText = document.getElementById("param-grid-progress-text");
    const btn = document.getElementById("quant-param-grid-run");
    if (prog) {
      prog.hidden = false;
      prog.classList.add("is-busy");
    }
    if (progText) progText.textContent = "参数扫描中…";
    if (btn) btn.disabled = true;
    try {
      const { ok, data, error } = await apiFetch("/api/quant/param-grid", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          top_k_values: [2, 3, 5],
          lookback_values: [60, 90, 120],
          apply_costs: true,
          max_cells: 9,
        }),
      });
      if (!ok) throw new Error(error || data.detail || "网格失败");
      renderParamGridResult(data);
    } finally {
      if (prog) {
        prog.hidden = true;
        prog.classList.remove("is-busy");
      }
      if (btn) btn.disabled = false;
    }
  }

  async function runPortfolioBacktest() {
    setQuantBtBusy(true, "Top-K 回测中（先读本地日线，缺的再补远端；观察池大时可能需一分钟）…");
    try {
      const {
        lookback,
        top_k,
        horizon_days,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        rank_mode,
      } = readPortfolioBtParams();
      const payload = {
        lookback,
        top_k,
        horizon_days,
        ...portfolioBtScoreFloorPayload(rank_mode),
        apply_costs: true,
        fetch_fundamentals: false,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        rank_mode,
        include_score_ic: true,
        include_quantile: true,
        include_benchmark: true,
      };
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), 240000) : null;
      let res;
      try {
        res = await fetch("/api/quant/portfolio-backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: ctrl ? ctrl.signal : undefined,
        });
      } catch (err) {
        const aborted = Boolean(
          err && (err.name === "AbortError" || /abort/i.test(String(err)))
        );
        els.quantPortfolioSummary.textContent = aborted
          ? "回测超过 4 分钟仍未返回。可缩小观察池，或等本地日线缓存补齐后再试。"
          : String((err && err.message) || err || "回测请求失败");
        els.quantPortfolioSummary.classList.add("down");
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "回测失败");
        return { success: false, error: els.quantPortfolioSummary.textContent };
      } finally {
        if (timer) clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        els.quantPortfolioSummary.textContent = buildPortfolioBacktestFailText(
          data,
          res.status
        );
        els.quantPortfolioSummary.classList.add("down");
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "回测失败");
        return data;
      }
      els.quantPortfolioSummary.classList.remove("down");
      const summary = buildPortfolioBacktestSummaryText(data);
      els.quantPortfolioSummary.textContent = summary.text;
      if (summary.warn) {
        els.quantPortfolioSummary.classList.add("down");
      } else {
        els.quantPortfolioSummary.classList.remove("down");
      }
      // Top-K 单曲线与中性化对照无关：清掉日报/上次对照块，避免曲线前残留「绝对分…百分点」
      state.neutralCompareSource = null;
      renderNeutralCompareTable(null);
      renderPortfolioBacktestResult(data);
      paintPortfolioChart(
        data.equity_curve,
        "回测无足够交易点",
        (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
        (data.benchmark && data.benchmark.benchmark_label) || null,
        data.ic_equity_align
      );
      loadFitGapForBacktest(data).catch(() => {});

      // 北极星仪表化（前端缓存）：滚动夏普 / 卡玛 / TTM 等
      try {
        const curve =
          data.equity_curve_tail ||
          (Array.isArray(data.equity_curve) ? data.equity_curve.slice(-120) : []);
        if (Array.isArray(curve) && curve.length) {
          localStorage.setItem(
            "investment_northstar_last_backtest",
            JSON.stringify({ at: Date.now(), curve })
          );
        }
      } catch (_) {
        /* ignore */
      }
      return data;
    } finally {
      setQuantBtBusy(false);
    }
  }

  async function runPortfolioNeutralCompare() {
    setQuantBtBusy(true, "中性化对照回测中（可能需数秒）…");
    try {
      const {
        lookback,
        top_k,
        horizon_days,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
      } = readPortfolioBtParams();
      const payload = {
        lookback,
        top_k,
        horizon_days,
        ...portfolioBtScoreFloorPayload("predicted_score"),
        apply_costs: true,
        fetch_fundamentals: false,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
      };
      const res = await fetch("/api/quant/portfolio-neutral-compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.success) {
        els.quantPortfolioSummary.textContent = data.error || "对照失败";
        renderNeutralCompareTable(null);
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "对照失败");
        return data;
      }
      const nm = (data.neutralized && data.neutralized.metrics) || {};
      const am = (data.absolute && data.absolute.metrics) || {};
      const d = data.delta || {};
      const bc = data.benchmark_compare || {};
      const winner =
        data.winner === "neutralized"
          ? "中性化更优"
          : data.winner === "absolute"
            ? "未中性化ŷ更优"
            : "接近";
      const excessNote =
        bc.ok && bc.delta_excess_pct != null
          ? ` · Δ超额 ${Number(bc.delta_excess_pct) >= 0 ? "+" : ""}${bc.delta_excess_pct}%(${
              bc.benchmark_label || "基准"
            })`
          : "";
      els.quantPortfolioSummary.textContent =
        `${winner} · Δ累计 ${d.total_return_pct ?? "—"}% · 中性 ${nm.total_return_pct ?? "—"}% vs 绝对 ${am.total_return_pct ?? "—"}%` +
        excessNote +
        (data.fundamentals_count ? ` · 基本面 ${data.fundamentals_count} 只` : "");
      state.neutralCompareSource = "live";
      renderNeutralCompareTable(data, null, { frozen: false });
      state.neutralCompareSource = "live";
      renderBtScopeNote(BT_SCOPE_LIVE);
      if (data.neutralized && data.neutralized.success) {
        renderPortfolioBacktestResult({
          ...data.neutralized,
          loaded_stocks: data.loaded_stocks,
          params: { ...(data.neutralized.params || {}), stock_count: (data.loaded_stocks || []).length },
        });
      }
      await paintNeutralCompareChart(data);
      return data;
    } finally {
      setQuantBtBusy(false);
    }
  }

  function setBtTradesCaption(text) {
    state.btTradesTableApi = null;
    state.lastSimTrades = [];
    if (els.quantBtTrades) {
      els.quantBtTrades.innerHTML = `<p class="quant-trades-caption">${escapeHtml(text)}</p>`;
    }
  }

  function setQuantBtBusy(busy, message) {
    if (els.quantBtProgress) {
      els.quantBtProgress.hidden = !busy;
      els.quantBtProgress.classList.toggle("is-busy", !!busy);
      if (busy) {
        try {
          els.quantBtProgress.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {
          /* ignore */
        }
      }
    }
    if (els.quantBtProgressText && message) els.quantBtProgressText.textContent = message;
    for (const id of quantBtBusyIds) {
      const el = document.getElementById(id);
      if (el) el.disabled = !!busy;
    }
  }

  function portfolioBtScoreFloorPayload(rankMode) {
    return buildBtScoreFloorPayload(state.quantScoringFloors, rankMode);
  }

  return {
    buildIcAlignMarkers,
    cachePromoteHintsFromBacktest,
    clearBtTradesTable,
    downloadSimTradesCsv,
    fitReturnScoreModel,
    flattenTradesToSimLegs,
    formatFactorWeightsNote,
    formatSimStatus,
    formatSnapshotAt,
    loadFitGapForBacktest,
    loadLastBacktestSnapshot,
    paintDualPortfolioChart,
    paintIcChart,
    paintNeutralCompareChart,
    paintPortfolioChart,
    paintQuantileChart,
    paramGridApplyGate,
    portfolioBtScoreFloorPayload,
    readPortfolioBtParams,
    refreshParamGridApplyGate,
    renderAttributionTables,
    renderBtTradesTable,
    renderCostAssumptions,
    renderIcEquityAlign,
    renderNeutralCompareTable,
    renderParamGridResult,
    renderPortfolioBacktestResult,
    renderQuantileTable,
    renderRegimeBuckets,
    renderScoreIc,
    renderSignalFillTable,
    renderT0BacktestResult,
    renderUniversePanel,
    renderWfSlices,
    runParamGrid,
    runPortfolioBacktest,
    runPortfolioNeutralCompare,
    setBtTradesCaption,
    setQuantBtBusy,
  };
}
