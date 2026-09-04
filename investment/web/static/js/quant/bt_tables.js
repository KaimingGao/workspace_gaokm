/**
 * 回测 / 横截面相关表格 HTML 渲染。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { fmtPct as defaultFmtPct, metricClass as defaultMetricClass } from "./bt_result.js";
import { fmtScore, scoreCls } from "../paper/fmt.js";
import { buildT0TradeTableHtml, pickDetailDays } from "../paper/t0_table.js?v=p1945";
import { buildT0MetricCards } from "../paper/t0_report.js?v=p1877";
import { watchingNameSpanHtml } from "./names.js";

/**
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 *   metricCell?: typeof defaultMetricCell,
 *   fmtPct?: typeof defaultFmtPct,
 *   metricClass?: typeof defaultMetricClass,
 *   getWatchingNameByCode?: () => Record<string, string>,
 * }} deps
 */
export function createBtTablesUi(deps = {}) {
  const esc = deps.escapeHtml || defaultEscapeHtml;
  const researchGridHtml = deps.researchGridHtml || defaultResearchGridHtml;
  const metricCell = deps.metricCell || defaultMetricCell;
  const fmtPct = deps.fmtPct || defaultFmtPct;
  const mcls = deps.metricClass || defaultMetricClass;
  const getWatchingNameByCode =
    deps.getWatchingNameByCode || (() => ({}));

  function renderAttributionTablesHtml(attr) {
    if (!attr || !attr.ok) return "";
    const watchingNameByCode = getWatchingNameByCode();
    const byStock = (attr.by_stock || []).slice(0, 6);
    const bySector = (attr.by_sector || []).slice(0, 8);
    const br = attr.brinson || {};
    const fp = attr.factor_proxy || {};
    let html = `<p class="quant-trades-caption">选股超额 ${
      attr.selection_excess_pct != null
        ? `${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
        : "—"
    }</p>`;
    if (br.ok) {
      html +=
        `<p class="quant-trades-caption">Brinson lite · A ${fmtPct(br.allocation_pct)} · S ${fmtPct(
          br.selection_pct
        )} · I ${fmtPct(br.interaction_pct)} · Σ ${fmtPct(br.total_excess_pct)}</p>`;
      const brRows = (br.by_sector || []).slice(0, 8).map((r) => ({
        sector: r.sector || "—",
        weight: r.weight_pct != null ? `${r.weight_pct}%` : "—",
        allocation: fmtPct(r.allocation_pct),
        allocationCls: mcls(r.allocation_pct),
        selection: fmtPct(r.selection_pct),
        selectionCls: mcls(r.selection_pct),
        interaction: fmtPct(r.interaction_pct),
        interactionCls: mcls(r.interaction_pct),
      }));
      if (brRows.length) {
        html += researchGridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "weight", label: "权重", widthPct: 14, num: true },
            { id: "allocation", label: "配置", widthPct: 14, num: true },
            { id: "selection", label: "选股", widthPct: 14, num: true },
            { id: "interaction", label: "交互", widthPct: 14, num: true },
          ],
          brRows,
          (col, d) => {
            if (col.id === "allocation") return metricCell(d.allocation, d.allocationCls);
            if (col.id === "selection") return metricCell(d.selection, d.selectionCls);
            if (col.id === "interaction") return metricCell(d.interaction, d.interactionCls);
            return esc(d[col.id] ?? "—");
          }
        );
      }
    }
    if (fp && fp.ok) {
      html += `<p class="quant-trades-caption">score 高低半组差 ${fmtPct(fp.score_spread_pct)} · n=${esc(
        String(fp.n ?? "—")
      )}</p>`;
    }
    if (byStock.length) {
      html += researchGridHtml(
        [
          { id: "name", label: "股票", flex: true },
          { id: "sector", label: "行业", widthPct: 18, center: true },
          { id: "ret", label: "均收益", widthPct: 18, num: true },
          { id: "n", label: "笔数", widthPct: 12, num: true },
        ],
        byStock.map((r) => {
          const ret = r.avg_return_pct ?? r.return_pct;
          const code = String(r.code || r.stock_code || "").trim();
          const fullName = watchingNameByCode[code] || code;
          return {
            code,
            name: fullName,
            sector: r.sector || "—",
            retText: fmtPct(ret),
            retCls: mcls(ret),
            n: String(r.n ?? "—"),
          };
        }),
        (col, d) => {
          if (col.id === "name") {
            return (
              `<div class="watching-stock" title="${esc(d.name + " " + d.code)}">` +
              watchingNameSpanHtml(d.name) +
              `<span class="watching-code-sub">${esc(d.code)}</span></div>`
            );
          }
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          return esc(d[col.id] ?? "—");
        }
      );
    }
    if (bySector.length) {
      html +=
        `<div style="margin-top:8px">` +
        researchGridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "count", label: "只数", widthPct: 16, num: true },
            { id: "ret", label: "均收益", widthPct: 20, num: true },
          ],
          bySector.map((r) => ({
            sector: r.sector || "—",
            count: String(r.count ?? r.n ?? "—"),
            retText: fmtPct(r.avg_return_pct),
            retCls: mcls(r.avg_return_pct),
          })),
          (col, d) => {
            if (col.id === "ret") return metricCell(d.retText, d.retCls);
            return esc(d[col.id] ?? "—");
          }
        ) +
        `</div>`;
    }
    html += `<p class="quant-attr-note">${esc(
      attr.methodology || attr.note || "非完整因子暴露归因。"
    )}</p>`;
    return html;
  }

  function renderIcEquityAlignHtml(align) {
    if (!align) return "";
    if (!align.ok) {
      return `<p class="quant-trades-caption">IC↔净值对齐：${esc(
        align.reason || "不可用"
      )}</p>`;
    }
    const pos = align.pos_ic || {};
    const neg = align.neg_ic || {};
    const spread = align.avg_return_spread_pp;
    const favor = align.aligned_favor_pos_ic;
    const headCls = favor === false ? " down" : "";
    const head =
      `<p class="quant-trades-caption${headCls}">IC↔净值对齐 · ${align.period_count ?? "—"} 期` +
      (spread != null
        ? ` · 正IC窗均收益−非正 ${Number(spread) >= 0 ? "+" : ""}${spread}pp`
        : "") +
      (favor === false ? " · ⚠正IC窗未优于非正" : favor ? " · 同向" : "") +
      `</p>`;
    return (
      head +
      researchGridHtml(
        [
          { id: "bucket", label: "分桶", widthPct: 18 },
          { id: "n", label: "期数", widthPct: 12, num: true },
          { id: "avg", label: "均期收益", widthPct: 16, num: true },
          { id: "win", label: "胜率", widthPct: 14, num: true },
          { id: "tot", label: "复利累计", widthPct: 16, num: true },
          { id: "note", label: "", flex: true },
        ],
        [
          {
            bucket: "正IC窗",
            n: String(pos.count ?? "—"),
            avgText: pos.avg_return_pct != null ? `${pos.avg_return_pct}%` : "—",
            avgCls: mcls(pos.avg_return_pct),
            win: pos.win_rate_pct != null ? `${pos.win_rate_pct}%` : "—",
            totText:
              pos.total_return_compound_pct != null
                ? `${pos.total_return_compound_pct}%`
                : "—",
            totCls: mcls(pos.total_return_compound_pct),
            note: "",
          },
          {
            bucket: "非正IC窗",
            n: String(neg.count ?? "—"),
            avgText: neg.avg_return_pct != null ? `${neg.avg_return_pct}%` : "—",
            avgCls: mcls(neg.avg_return_pct),
            win: neg.win_rate_pct != null ? `${neg.win_rate_pct}%` : "—",
            totText:
              neg.total_return_compound_pct != null
                ? `${neg.total_return_compound_pct}%`
                : "—",
            totCls: mcls(neg.total_return_compound_pct),
            note: "",
          },
        ],
        (col, d) => {
          if (col.id === "avg") return metricCell(d.avgText, d.avgCls);
          if (col.id === "tot") return metricCell(d.totText, d.totCls);
          if (col.id === "bucket") return esc(d.bucket);
          return esc(d[col.id] ?? "—");
        }
      ) +
      (align.note ? `<p class="sub">${esc(align.note)}</p>` : "")
    );
  }

  function renderQuantileTableHtml(qb) {
    if (!qb) return { html: "", showChart: false };
    if (!qb.ok) {
      return {
        html: `<p class="quant-trades-caption">分层回测：${esc(
          qb.reason || "不可用"
        )}</p>`,
        showChart: false,
      };
    }
    const mono = qb.monotonic_increasing;
    const ls = qb.q_high_minus_q_low_pct;
    const warn = mono === false ? " · 非单调（打分区分度弱或噪声大）" : "";
    const head =
      `<p class="quant-trades-caption${mono === false ? " down" : ""}">分层 Q1–Q${
        qb.n_quantiles || 5
      } · ${qb.fold_count ?? "—"} 期` +
      (ls != null ? ` · Q高−Q低 ${Number(ls) >= 0 ? "+" : ""}${ls}%` : "") +
      (mono === true ? " · 单调↑" : warn) +
      `</p>`;
    const rows = qb.quantiles || [];
    if (!rows.length) {
      return { html: head, showChart: false };
    }
    const html =
      head +
      researchGridHtml(
        [
          { id: "label", label: "分层", widthPct: 18 },
          { id: "ret", label: "累计收益", widthPct: 16, num: true },
          { id: "win", label: "胜率", widthPct: 14, num: true },
          { id: "n", label: "期数", widthPct: 12, num: true },
          { id: "eq", label: "终值", widthPct: 14, num: true },
          { id: "note", label: "", flex: true },
        ],
        rows.map((r) => ({
          label: r.label || `Q${r.quantile}`,
          retText:
            r.total_return_pct != null ? `${Number(r.total_return_pct).toFixed(2)}%` : "—",
          retCls: mcls(r.total_return_pct),
          win: r.win_rate_pct != null ? `${Number(r.win_rate_pct).toFixed(1)}%` : "—",
          n: String(r.trade_count ?? "—"),
          eq: r.final_equity != null ? String(r.final_equity) : "—",
          note: "",
        })),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          if (col.id === "label") return esc(d.label);
          return esc(d[col.id] ?? "—");
        }
      ) +
      (qb.note ? `<p class="sub">${esc(qb.note)}</p>` : "");
    return { html, showChart: true };
  }

  function buildT0BacktestMetrics(data) {
    return buildT0MetricCards(data).map((row) => ({
      label: row.label,
      value: esc(String(row.value ?? "—")),
      cls: row.cls || "",
    }));
  }

  function buildT0BacktestDaysHtml(data) {
    if (!data || !data.success) return "";
    const days = pickDetailDays(data);
    if (!days.length) {
      return `<p class="quant-trades-caption">区间内无做 T 成交日</p>`;
    }
    return buildT0TradeTableHtml({
      data,
      days,
      caption: `<p class="quant-trades-caption">做 T 日明细（最多 20 条，新→旧）</p>`,
      maxRows: 20,
    });
  }

  function buildCrossSectionResult(data) {
    if (!data || !data.success) {
      return {
        ok: false,
        summary: (data && data.error) || "排序失败",
        listHtml: `<p class="watching-table-empty">${esc(
          (data && data.error) || "排序失败"
        )}</p>`,
      };
    }
    const neut = data.neutralization || {};
    const neutNote = neut.applied
      ? ` · 截面中性化(${neut.method || "zscore"})`
      : "";
    const floorLabel =
      data.min_predicted_score != null
        ? `ŷ门槛=${data.min_predicted_score}`
        : `门槛=${data.min_score}`;
    const summary = `Top ${data.ranked_count} / 候选 ${data.candidate_count} · ${floorLabel}${neutNote}`;
    const ranking = Array.isArray(data.ranking) ? data.ranking : [];
    if (!ranking.length) {
      return {
        ok: true,
        summary,
        listHtml: `<p class="watching-table-empty">无排序结果</p>`,
      };
    }
    const rows = ranking.map((r, i) => {
      const code = String(r.stock_code || "").trim();
      const name = r.stock_name || code || "—";
      const score =
        r.score != null && !Number.isNaN(Number(r.score))
          ? fmtScore(r.score)
          : "—";
      const raw =
        r.score_raw != null && !Number.isNaN(Number(r.score_raw))
          ? fmtScore(r.score_raw)
          : r.score_raw != null
            ? String(r.score_raw)
            : "—";
      return {
        rank: String(i + 1),
        code,
        name,
        score,
        scoreCls: scoreCls(r.score),
        raw,
        rawCls: scoreCls(r.score_raw),
        source: r.data_source || "—",
      };
    });
    const listHtml = researchGridHtml(
      [
        {
          id: "rank",
          label: "#",
          widthPct: 8,
          num: true,
          headClass: "watching-col-center",
          cellClass: "watching-col-center",
        },
        { id: "name", label: "股票", flex: true, cellClass: "watching-stock" },
        { id: "score", label: "评分", widthPct: 14, num: true },
        { id: "raw", label: "raw", widthPct: 14, num: true },
        { id: "source", label: "源", widthPct: 22 },
      ],
      rows,
      (col, d) => {
        if (col.id === "name") {
          return (
            `<div class="watching-stock" title="${esc(
              (d.name || "") + " " + (d.code || "")
            )}">` +
            watchingNameSpanHtml(d.name || d.code) +
            `<span class="watching-code-sub">${esc(d.code || "")}</span></div>`
          );
        }
        if (col.id === "score") {
          return `<span class="paper-hold-score ${esc(
            d.scoreCls || ""
          )}">${esc(d.score ?? "—")}</span>`;
        }
        if (col.id === "raw") {
          return `<span class="paper-hold-score ${esc(
            d.rawCls || ""
          )}">${esc(d.raw ?? "—")}</span>`;
        }
        return esc(d[col.id] ?? "—");
      },
      { emptyText: "无排序结果" }
    );
    return { ok: true, summary, listHtml };
  }

  /** @param {object|null|undefined} sic */
  function renderScoreIcHtml(sic) {
    if (!sic) return { html: "", clear: true };
    if (!sic.ok) {
      return {
        html: `<p class="quant-trades-caption">截面 IC：${esc(
          sic.reason || "不可用"
        )}</p>`,
        clear: false,
        hideChart: true,
      };
    }
    const posLabel =
      sic.positive_ic_days != null && sic.day_count
        ? `${sic.positive_ic_days}/${sic.day_count}`
        : "—";
    const html =
      `<p class="quant-trades-caption">截面 IC · 日数 ${sic.day_count ?? "—"} · horizon ${
        sic.horizon_days ?? "—"
      }d` +
      (sic.roll_window ? ` · 滚动窗 ${sic.roll_window}` : "") +
      `</p>` +
      researchGridHtml(
        [
          { id: "ic", label: "IC均值", widthPct: 14, num: true },
          { id: "std", label: "IC标准差", widthPct: 14, num: true },
          {
            id: "icir",
            label: "ICIR",
            widthPct: 12,
            num: true,
            title: "IC均值/IC标准差；看截面预测力是否稳定",
          },
          { id: "pos", label: "正IC日", widthPct: 14, num: true },
          { id: "note", label: "说明", flex: true },
        ],
        [
          {
            ic: sic.ic_mean != null ? String(sic.ic_mean) : "—",
            std: sic.ic_std != null ? String(sic.ic_std) : "—",
            icir: sic.icir != null ? String(sic.icir) : "—",
            pos: posLabel,
            note: sic.note || "池内 score vs 远期收益",
          },
        ],
        (col, d) => esc(d[col.id] ?? "—")
      );
    return { html, clear: false, hideChart: false };
  }

  return {
    renderAttributionTablesHtml,
    renderIcEquityAlignHtml,
    renderQuantileTableHtml,
    buildT0BacktestMetrics,
    buildT0BacktestDaysHtml,
    buildCrossSectionResult,
    renderScoreIcHtml,
  };
}
