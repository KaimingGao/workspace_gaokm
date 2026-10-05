/**
 * 回测相关表格 HTML 渲染。
 */
import { escapeHtml } from "../shared.js";
import { researchGridHtml, metricCell } from "./research_grid.js";
import { fmtPct, metricClass } from "./bt_result.js";
import { watchingNameSpanHtml } from "./names.js";
import { fitTierBadgeForCode } from "./fit_tier_ui.js";


/**
 * @param {{
 *   escapeHtml?: typeof escapeHtml,
 *   researchGridHtml?: typeof researchGridHtml,
 *   metricCell?: typeof metricCell,
 *   fmtPct?: typeof fmtPct,
 *   metricClass?: typeof metricClass,
 *   getWatchingNameByCode?: () => Record<string, string>,
 * }} deps
 */
export function createBtTablesUi(deps = {}) {
  const esc = deps.escapeHtml || escapeHtml;
  const gridHtml = deps.researchGridHtml || researchGridHtml;
  const cell = deps.metricCell || metricCell;
  const pctFmt = deps.fmtPct || fmtPct;
  const mcls = deps.metricClass || metricClass;
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
        `<p class="quant-trades-caption">Brinson lite · A ${pctFmt(br.allocation_pct)} · S ${pctFmt(
          br.selection_pct
        )} · I ${pctFmt(br.interaction_pct)} · Σ ${pctFmt(br.total_excess_pct)}</p>`;
      const brRows = (br.by_sector || []).slice(0, 8).map((r) => ({
        sector: r.sector || "—",
        weight: r.weight_pct != null ? `${r.weight_pct}%` : "—",
        allocation: pctFmt(r.allocation_pct),
        allocationCls: mcls(r.allocation_pct),
        selection: pctFmt(r.selection_pct),
        selectionCls: mcls(r.selection_pct),
        interaction: pctFmt(r.interaction_pct),
        interactionCls: mcls(r.interaction_pct),
      }));
      if (brRows.length) {
        html += gridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "weight", label: "权重", widthPct: 14, num: true },
            { id: "allocation", label: "配置", widthPct: 14, num: true },
            { id: "selection", label: "选股", widthPct: 14, num: true },
            { id: "interaction", label: "交互", widthPct: 14, num: true },
          ],
          brRows,
          (col, d) => {
            if (col.id === "allocation") return cell(d.allocation, d.allocationCls);
            if (col.id === "selection") return cell(d.selection, d.selectionCls);
            if (col.id === "interaction") return cell(d.interaction, d.interactionCls);
            return esc(d[col.id] ?? "—");
          }
        );
      }
    }
    if (fp && fp.ok) {
      html += `<p class="quant-trades-caption">score 高低半组差 ${pctFmt(fp.score_spread_pct)} · n=${esc(
        String(fp.n ?? "—")
      )}</p>`;
    }
    if (byStock.length) {
      html += gridHtml(
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
            retText: pctFmt(ret),
            retCls: mcls(ret),
            n: String(r.n ?? "—"),
          };
        }),
        (col, d) => {
          if (col.id === "name") {
            return (
              `<div class="watching-stock" title="${esc(d.name + " " + d.code)}">` +
              `<span class="watching-name-row">` +
              watchingNameSpanHtml(d.name) +
              fitTierBadgeForCode(d.code, { escapeHtml: esc }) +
              `</span>` +
              `<span class="watching-code-sub">${esc(d.code)}</span></div>`
            );
          }
          if (col.id === "ret") return cell(d.retText, d.retCls);
          return esc(d[col.id] ?? "—");
        }
      );
    }
    if (bySector.length) {
      html +=
        `<div style="margin-top:8px">` +
        gridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "count", label: "只数", widthPct: 16, num: true },
            { id: "ret", label: "均收益", widthPct: 20, num: true },
          ],
          bySector.map((r) => ({
            sector: r.sector || "—",
            count: String(r.count ?? r.n ?? "—"),
            retText: pctFmt(r.avg_return_pct),
            retCls: mcls(r.avg_return_pct),
          })),
          (col, d) => {
            if (col.id === "ret") return cell(d.retText, d.retCls);
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
      gridHtml(
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

  function _fmtContribDate(v) {
    const s = String(v || "").trim();
    return s ? esc(s.slice(0, 10)) : "—";
  }

  function _fmtContribShares(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return "—";
    return esc(Math.round(n).toLocaleString("zh-CN"));
  }

  function _fmtContribPct(v) {
    const n = Number(v);
    if (v == null || v === "" || !Number.isFinite(n)) {
      return { text: "—", cls: "" };
    }
    const sign = n > 0 ? "+" : "";
    return { text: `${sign}${n.toFixed(2)}%`, cls: mcls(n) };
  }

  function _fmtContribPnl(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  }

  function renderReplayStockContribHtml(rows) {
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) return "";
    const watchingNameByCode = getWatchingNameByCode();
    const maxAbs = Math.max(1, ...list.map((r) => Math.abs(Number(r.pnl || 0))));
    const n = list.length;
    const totalPnl = list.reduce((s, r) => s + Number(r.pnl || 0), 0);
    const best = [...list].sort((a, b) => Number(b.pnl || 0) - Number(a.pnl || 0))[0];
    const worst = [...list].sort((a, b) => Number(a.pnl || 0) - Number(b.pnl || 0))[0];
    const bestName =
      (best && (watchingNameByCode[best.stock_code] || best.stock_name || best.stock_code)) || "";
    const worstName =
      (worst && (watchingNameByCode[worst.stock_code] || worst.stock_name || worst.stock_code)) || "";
    const metaBits = [`${n} 只`, `合计 ${_fmtContribPnl(totalPnl)}`].filter(Boolean);
    const tipBits = [
      "分票贡献：持仓盯市盈亏合计；贡献%=盈亏/回测本金。佣金印花走现金，不进本表。",
      best && bestName ? `贡献最大：${bestName} ${_fmtContribPnl(best.pnl)}` : null,
      worst && worst !== best && worstName
        ? `拖累最大：${worstName} ${_fmtContribPnl(worst.pnl)}`
        : null,
    ].filter(Boolean);
    const body = list
      .map((r) => {
        const code = String(r.stock_code || "").trim();
        const fullName = watchingNameByCode[code] || r.stock_name || code || "—";
        const pnl = Number(r.pnl || 0);
        const cls = mcls(pnl);
        const ret = _fmtContribPct(r.return_pct);
        const contrib = _fmtContribPct(r.contrib_pct);
        const barW = Math.max(4, Math.round((Math.abs(pnl) / maxAbs) * 48));
        const overnight = Number(r.overnight_pnl || 0);
        const intraday = Number(r.intraday_pnl || 0);
        const title = fullName && code && fullName !== code ? `${fullName} ${code}` : fullName || code;
        return (
          `<tr class="paper-t0-contrib-row" data-code="${esc(code)}" data-name="${esc(fullName)}">` +
          `<td class="rebalance-stock paper-t0-col-stock watching-stock" title="${esc(title)}">` +
          `<span class="watching-name-row">` +
          watchingNameSpanHtml(fullName) +
          fitTierBadgeForCode(code, { escapeHtml: esc }) +
          `</span>` +
          (code ? `<span class="watching-code-sub">${esc(code)}</span>` : "") +
          `</td>` +
          `<td class="num paper-t0-col-viz-days" title="窗口内有持仓的交易日">${esc(
            String(r.hold_days ?? 0)
          )}</td>` +
          `<td class="num paper-t0-col-viz-lr" title="买入笔 / 卖出笔">${esc(
            `${r.buy_count ?? 0}/${r.sell_count ?? 0}`
          )}</td>` +
          `<td class="num paper-t0-col-viz-shares" title="窗口末日持股">${_fmtContribShares(
            r.shares_end
          )}</td>` +
          `<td class="paper-t0-col-viz-dt" title="首次持有日">${_fmtContribDate(r.first_date)}</td>` +
          `<td class="paper-t0-col-viz-dt" title="末次持有日">${_fmtContribDate(r.last_date)}</td>` +
          `<td class="num paper-t0-col-viz-ret ${ret.cls}" title="盈亏 / 日均占用资金">${esc(
            ret.text
          )}</td>` +
          `<td class="num paper-t0-col-viz-pct ${contrib.cls}" title="盈亏 / 回测本金">${esc(
            contrib.text
          )}</td>` +
          `<td class="num paper-t0-col-pnl ${cls} has-tip" title="${esc(
            `盯市 ${pnl.toFixed(0)} · 隔夜 ${_fmtContribPnl(overnight)} · 当日 ${_fmtContribPnl(
              intraday
            )} · 条长∝|PnL|`
          )}">` +
          `<span class="paper-t0-viz-pnl-bar" style="width:${barW}px" aria-hidden="true"></span>` +
          `<span class="paper-t0-viz-pnl-num">${esc(_fmtContribPnl(pnl))}</span>` +
          `</td>` +
          `</tr>`
        );
      })
      .join("");
    return (
      `<section class="paper-t0-viz-contrib replay-stock-contrib-section">` +
      `<div class="paper-t0-viz-contrib-head">` +
      `<div class="paper-t0-viz-contrib-title-block">` +
      `<h4>分票贡献</h4>` +
      `<span class="paper-t0-viz-contrib-meta has-tip" title="${esc(tipBits.join("\n"))}">${esc(
        metaBits.join(" · ")
      )}</span>` +
      `</div>` +
      `<p class="paper-t0-viz-contrib-hint">持仓盯市合计 · 贡献%=盈亏/回测本金 · 点股票名看日线</p>` +
      `</div>` +
      `<div class="quant-weight-table-wrap paper-t0-viz-stock-wrap watching-table-scroll">` +
      `<table class="quant-weight-table paper-t0-table paper-t0-viz-stock-table replay-stock-contrib-table">` +
      `<colgroup>` +
      `<col class="paper-t0-col-stock" />` +
      `<col class="paper-t0-col-viz-days" />` +
      `<col class="paper-t0-col-viz-lr" />` +
      `<col class="paper-t0-col-viz-shares" />` +
      `<col class="paper-t0-col-viz-dt" />` +
      `<col class="paper-t0-col-viz-dt" />` +
      `<col class="paper-t0-col-viz-ret" />` +
      `<col class="paper-t0-col-viz-pct" />` +
      `<col class="paper-t0-col-pnl" />` +
      `</colgroup>` +
      `<thead><tr class="paper-t0-contrib-head">` +
      `<th scope="col" class="paper-t0-col-stock">股票</th>` +
      `<th scope="col" class="paper-t0-col-viz-days num" title="窗口内有持仓的交易日">持有日</th>` +
      `<th scope="col" class="paper-t0-col-viz-lr num" title="买入笔 / 卖出笔">买/卖</th>` +
      `<th scope="col" class="paper-t0-col-viz-shares num" title="窗口末日持股">期末</th>` +
      `<th scope="col" class="paper-t0-col-viz-dt" title="首次持有日">首日</th>` +
      `<th scope="col" class="paper-t0-col-viz-dt" title="末次持有日">末日</th>` +
      `<th scope="col" class="paper-t0-col-viz-ret num" title="盈亏 / 日均占用资金">收益%</th>` +
      `<th scope="col" class="paper-t0-col-viz-pct num" title="盈亏 / 回测本金">贡献%</th>` +
      `<th scope="col" class="paper-t0-col-pnl num" title="持仓盯市盈亏；条长∝|PnL|">PnL</th>` +
      `</tr></thead><tbody>${body}</tbody></table></div></section>`
    );
  }

  return {
    renderAttributionTablesHtml,
    renderScoreIcHtml,
    renderReplayStockContribHtml,
  };
}
