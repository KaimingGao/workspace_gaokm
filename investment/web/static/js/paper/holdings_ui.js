/**
 * Paper · 持仓表：出处汇总 + 原生 HTML 表（回退）+ 操作条。
 * 主路径由 holdings_table_island.js（virtual_table 纯 DOM 虚拟网格）渲染。
 */

import {
  fmtPriceUnit,
  escapeText,
  fmtPct,
  metricCls,
  fmtTableScore,
  scoreCls,
  resolveTradeScore,
  resolveEodScore,
  resolveTauScore,
  resolveOnScore,
  resolveNowcastScore,
  isHeuristicScoreScale,
  Y_EOD_TITLE,
  Y_TAU_TITLE,
  Y_ON_TITLE,
  Y_NOWCAST_TITLE,
} from "./fmt.js?v=p1472";
import { paginateItems, renderPagerHtml } from "../api_client.js";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p1457";
import {
  isSingleHeadItem,
  singleHeadBadgeHtml,
  yCheckBadgeHtml,
} from "../quant/watching_insights_ui.js?v=p1457";
import { TRADE_TITLE, formatPrevCloseDisplay, formatOpenDisplay } from "../quant/watching_quotes_ui.js?v=p1227";
import { buildHoldingSharesTip } from "./holding_lots_tip.js?v=p1227";
import { holdingT0BadgeHtml } from "./holding_t0_badge.js?v=p1526";

const ORIGIN_HINT = {
  manual: "你手动建仓或加仓",
  strategy: "由「按策略调仓」生成",
  mixed: "手动建仓后被策略加过仓",
};

function sentPlaceholderHtml(code) {
  const c = String(code || "").trim();
  return (
    `<span class="watching-sent-badge is-neutral" data-code="${escapeText(c)}" title="加载中">…</span>`
  );
}

export function buildPaperOriginBarHtml(summary) {
  // 冗余下线：出处对照（手动 X 只 · 市值 · 浮盈亏 vs 策略 X 只）与
  // 单只持仓表行内的出处 chip 信息重复，故整块不再渲染。
  // 如后续恢复，把 return 上面的代码块重启用即可（原逻辑保留在 git 历史）。
  return "";
}

export function buildPaperHoldActionBarHtml({ selectedHoldCode, holdings }) {
  const selected = (holdings || []).find(
    (h) => selectedHoldCode && String(h.stock_code) === String(selectedHoldCode)
  );
  const nextSelectedHoldCode = selected ? String(selected.stock_code) : null;
  const actionHidden = selected ? "" : " hidden";
  const actionName = selected ? selected.stock_name || selected.stock_code || "" : "";
  const actionCode = selected ? selected.stock_code || "" : "";
  const actionShares = selected ? selected.shares ?? "" : "";
  const actionBarHtml =
    `<div id="paper-hold-action-bar" class="paper-hold-action-bar"${actionHidden}>` +
    `<div class="paper-hold-action-info">` +
    `<strong class="paper-hold-action-name">${escapeText(actionName)}</strong>` +
    `<span class="paper-hold-action-code">${escapeText(actionCode)}</span>` +
    `<span class="paper-hold-action-hint">点行选中后在此加减仓</span>` +
    `</div>` +
    `<div class="paper-hold-action-controls">` +
    `<input type="number" class="paper-hold-shares" min="100" step="100" placeholder="股数" ` +
    `aria-label="股数" title="加减仓股数（100 的倍数；减仓可空=一手）" />` +
    `<button type="button" class="dialog-btn secondary paper-hold-buy" data-code="${escapeText(
      actionCode
    )}">加仓</button>` +
    `<button type="button" class="dialog-btn secondary paper-hold-cut" data-code="${escapeText(
      actionCode
    )}" data-shares="${escapeText(actionShares)}">减仓</button>` +
    `<button type="button" class="dialog-btn secondary paper-hold-flat" data-code="${escapeText(
      actionCode
    )}">清仓</button>` +
    `<button type="button" class="dialog-btn secondary paper-hold-action-dismiss" title="取消选中，关闭操作条">取消</button>` +
    `</div></div>`;
  return { actionBarHtml, selectedHoldCode: nextSelectedHoldCode };
}

/** 原生 HTML 表回退（React 岛不可用时） */
export function buildPaperHoldingsTableHtml({
  summary,
  holdings,
  holdingsPage,
  holdingsPageSize,
  chartMode,
  chartStockCode,
  selectedHoldCode,
  pendingFocusCode,
  holdingsSortKey,
  holdingsSortDir,
  idPrefix = "paper-holdings",
  sentHtmlByCode = null,
}) {
  const sentMap = sentHtmlByCode || {};

  function sortThHtml(label, key) {
    const active = holdingsSortKey === key;
    const arrow = !active ? "" : holdingsSortDir === "asc" ? " ↑" : " ↓";
    const nextHint =
      key === "code"
        ? "股票代码 · 点击排序"
        : key === "score"
          ? `${TRADE_TITLE} · 点击排序`
            : key === "score_eod"
            ? `${Y_EOD_TITLE} · 点击排序`
            : key === "score_tau"
              ? `${Y_TAU_TITLE} · 点击排序`
            : key === "score_on"
              ? `${Y_ON_TITLE} · 点击排序`
            : key === "score_nowcast"
              ? `${Y_NOWCAST_TITLE} · 点击排序`
            : key === "pnl"
            ? "浮盈亏 = 现价 − 成本价（相对成本的浮动盈亏 %）· 点击排序"
            : key === "chg"
              ? "相对昨收的涨跌幅 %。与 EOD / ŷ_trade 同一口径 · 点击排序"
              : "市值 = 现价 × 股数 · 点击排序";
    return (
      `<th class="paper-hold-sort${active ? " is-sorted" : ""}" ` +
      `data-sort="${key}" role="button" tabindex="0" title="${nextHint}">${label}${arrow}</th>`
    );
  }

  const pageState = paginateItems(holdings, holdingsPage, holdingsPageSize);
  const nextPage = pageState.page;
  const pageRows = pageState.items;

  const rows = pageRows
    .map((h) => {
      const name = h.stock_name || h.stock_code || "";
      const code = h.stock_code || "";
      const pnl = h.pnl_pct;
      const startDate = h.bought_date || "—";
      const score = resolveTradeScore(h);
      const scoreEod = resolveEodScore(h);
      const scoreTau = resolveTauScore(h);
      const scoreOn = resolveOnScore(h);
      const scoreNowcast = resolveNowcastScore(h);
      const belowMin = !!h.below_min_score;
      const hardReject = !!h.hard_reject;
      const scoreBase = fmtTableScore(h, score);
      let scoreShown =
        scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
      if (scoreShown === "—" && hardReject) scoreShown = "拒";
      const scoreEodShown = fmtTableScore(h, scoreEod);
      const scoreTauShown = fmtTableScore(h, scoreTau);
      const scoreOnShown = fmtTableScore(h, scoreOn);
      const scoreNowcastShown = fmtTableScore(h, scoreNowcast);
      const singleHead = isSingleHeadItem(h);
      const scoreTitle = hardReject
        ? String(h.reject_reason || "硬拒绝 · 无收益分")
        : isHeuristicScoreScale(h)
          ? score != null
            ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
            : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
          : singleHead
            ? `ŷ_trade 单头降级（${String(h.dual_score_head || "single")}）· 悬停看详情`
          : belowMin
            ? `低于ŷ_EOD门槛 ${h.min_score ?? "—"}（表列为 ŷ_trade）· 悬停看详情`
            : TRADE_TITLE;
      const oosFailed =
        !!h.oos_failed ||
        isHeuristicScoreScale(h) ||
        String(h.return_model_source || "").startsWith("oos_failed");
      const oosBadge = oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      const singleHeadBadge = singleHead ? singleHeadBadgeHtml(h, escapeText) : "";
      const yCheckBadge = yCheckBadgeHtml(h, escapeText);
      const scoreEodTitle = scoreEod == null ? "暂无 ŷ_EOD" : Y_EOD_TITLE;
      const scoreTauTitle = scoreTau == null ? "暂无 ŷ_τ" : Y_TAU_TITLE;
      const scoreOnTitle = scoreOn == null ? "暂无 ŷ_ON" : Y_ON_TITLE;
      const scoreNowcastTitle =
        scoreNowcast == null ? "暂无 nowcast · 有 ŷ_EOD 与 ŷ_τ 后可见" : Y_NOWCAST_TITLE;
      const origin = String(h.origin || "");
      const originLabel = h.origin_label || "—";
      const originTitle = ORIGIN_HINT[origin] || "早期记录未标出处";

      const active =
        chartMode === "stock" &&
        chartStockCode &&
        String(chartStockCode) === String(code)
          ? " is-chart-active"
          : "";
      const adjustActive =
        selectedHoldCode && String(selectedHoldCode) === String(code)
          ? " is-adjust-active"
          : "";
      const focusCls =
        pendingFocusCode && String(pendingFocusCode) === String(code)
          ? " is-focus-holding"
          : "";

      const scoreDetailJson = escapeText(watchingScoreDetail(h));

      return (
        `<tr data-code="${escapeText(code)}" data-shares="${escapeText(
          h.shares ?? ""
        )}" ` +
        `class="paper-hold-row${active}${adjustActive}${focusCls}${
          oosFailed ? " is-oos-failed" : ""
        }" title="点击选中并查看曲线">` +
        `<td class="paper-wl-name">` +
        `<span class="watching-name-row">` +
        `<span class="paper-wl-name-text">${escapeText(name)}</span>` +
        oosBadge +
        `</span>` +
        `<span class="paper-wl-code">${escapeText(code)}</span>` +
        `</td>` +
        `<td class="watching-col-center paper-hold-sent">${
          sentMap[code] || sentPlaceholderHtml(code)
        }</td>` +
        `<td class="num"><span class="paper-hold-shares-qty has-tip" title="${escapeText(
          buildHoldingSharesTip(h) || `持仓 ${h.shares ?? "—"} 股`
        )}">${escapeText(h.shares ?? "—")}</span></td>` +
        `<td class="watching-col-center paper-hold-t0">${holdingT0BadgeHtml(
          h.t0_intraday,
          escapeText
        )}</td>` +
        `<td class="num paper-hold-prev-close" title="上一交易日收盘价">${escapeText(
          formatPrevCloseDisplay(h, { unit: h.unit, currency: h.currency })
        )}</td>` +
        `<td class="num paper-hold-open" title="今日开盘价">${escapeText(
          String(formatOpenDisplay(h, { unit: h.unit, currency: h.currency })).replace(/元$/u, "")
        )}</td>` +
        `<td class="num paper-hold-price">${fmtPriceUnit(
          h.price,
          h.unit,
          h.currency
        )}</td>` +
        `<td class="num paper-hold-chg has-tip ${metricCls(h.change_pct)}" ` +
        `data-hold-chg-tip="${escapeText(
          JSON.stringify({
            code,
            name,
            chg: h.change_pct != null ? Number(h.change_pct) : null,
            asof: h.change_asof || h.quote_as_of || h.as_of || null,
          })
        )}" title="相对昨收 · 悬停看涨跌日5m K">${fmtPct(h.change_pct, {
          signed: true,
        })}</td>` +
        `<td class="num paper-hold-score watching-score-eod has-tip ${scoreCls(
          scoreEod
        )}" data-score-detail="${scoreDetailJson}" data-score-tip="eod" title="${escapeText(
          scoreEodTitle
        )}">${escapeText(scoreEodShown)}</td>` +
        `<td class="num paper-hold-score watching-score-tau has-tip ${scoreCls(
          scoreTau
        )}" data-score-detail="${scoreDetailJson}" data-score-tip="tau" title="${escapeText(
          scoreTauTitle
        )}">${escapeText(scoreTauShown)}</td>` +
        `<td class="num paper-hold-score watching-score-on has-tip ${scoreCls(
          scoreOn
        )}" data-score-detail="${scoreDetailJson}" data-score-tip="on" title="${escapeText(
          scoreOnTitle
        )}">${escapeText(scoreOnShown)}</td>` +
        `<td class="num paper-hold-score has-tip ${scoreCls(score)}${
          belowMin ? " score-below-min" : ""
        }${hardReject ? " score-reject" : ""}${
          singleHead ? " score-single-head" : ""
        }" ` +
        `data-score-detail="${scoreDetailJson}" data-score-tip="trade" title="${escapeText(
          scoreTitle
        )}">${escapeText(scoreShown)}${singleHeadBadge}${yCheckBadge}</td>` +
        `<td class="num paper-hold-score watching-score-nowcast has-tip ${scoreCls(
          scoreNowcast
        )}" data-score-detail="${scoreDetailJson}" data-score-tip="nowcast" title="${escapeText(
          scoreNowcastTitle
        )}">${escapeText(scoreNowcastShown)}</td>` +
        `<td class="num paper-hold-cost" title="持仓加权平均成本">${fmtPriceUnit(
          h.cost,
          h.unit,
          h.currency
        )}</td>` +
        `<td class="num paper-hold-mv">${fmtPriceUnit(
          h.market_value,
          h.unit,
          h.currency
        )}</td>` +
        `<td class="num paper-hold-pnl ${metricCls(pnl)}">${fmtPct(pnl, {
          signed: true,
        })}</td>` +
        `<td class="num paper-hold-since">${escapeText(startDate)}</td>` +
        `<td class="paper-hold-origin">` +
        `<span class="paper-origin-chip is-${escapeText(
          origin || "unknown"
        )}" title="${escapeText(originTitle)}">${escapeText(originLabel)}</span>` +
        `</td>` +
        `</tr>`
      );
    })
    .join("");

  const pagerHtml = renderPagerHtml(pageState, { idPrefix });
  const tableHtml =
    `<div class="paper-holdings-scroll">` +
    `<table class="quant-weight-table paper-holdings-table"><thead><tr>` +
    `<th title="股票名称与代码">股票</th>` +
    `<th class="watching-col-center" title="标题情绪摘要">情绪</th>` +
    `<th title="持仓股数">股数</th>` +
    `<th class="watching-col-center paper-hold-t0-head" title="当日实时做 T：正T/反T · 盯盘/一腿/完成">做T</th>` +
    `<th title="上一交易日收盘价">昨收</th>` +
    `<th title="今日开盘价">今开</th>` +
    `<th title="最新成交价">现价</th>` +
    `${sortThHtml("涨跌", "chg")}` +
    `${sortThHtml("y_eod", "score_eod")}` +
    `${sortThHtml("y_τ", "score_tau")}` +
    `${sortThHtml("y_on", "score_on")}` +
    `${sortThHtml("y_trade", "score")}` +
    `${sortThHtml("y_nc", "score_nowcast")}` +
    `<th title="持仓加权平均成本">成本</th>` +
    `${sortThHtml("市值", "market_value")}` +
    `${sortThHtml("浮盈亏", "pnl")}` +
    `<th title="建仓日期">开始</th>` +
    `<th class="paper-hold-origin" title="仓位来源：手动 / 策略 / 混合">出处</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div>`;

  const action = buildPaperHoldActionBarHtml({ selectedHoldCode, holdings });
  return {
    originBarHtml: buildPaperOriginBarHtml(summary),
    tableHtml,
    pagerHtml,
    actionBarHtml: action.actionBarHtml,
    page: nextPage,
    selectedHoldCode: action.selectedHoldCode,
    countText: (holdings || []).length ? String((holdings || []).length) : "",
  };
}
