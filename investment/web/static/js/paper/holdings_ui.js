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
  resolveCalTradeScore,
  resolveNowcastScore,
  isHeuristicScoreScale,
} from "./fmt.js?v=p1227";
import { paginateItems, renderPagerHtml } from "../api_client.js";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p1227";
import {
  isSingleHeadItem,
  singleHeadBadgeHtml,
  yCheckBadgeHtml,
} from "../quant/watching_insights_ui.js?v=p1227";
import { formatWatchingResidual, RESIDUAL_TITLE, NOWCAST_TITLE, TRADE_TITLE, EOD_CAL_TITLE } from "../quant/watching_quotes_ui.js?v=p1227";

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
            : key === "score_cal"
            ? `${EOD_CAL_TITLE} · 点击排序`
            : key === "score_nowcast"
              ? `${NOWCAST_TITLE} · 点击排序`
            : key === "residual"
              ? `${RESIDUAL_TITLE} · 点击排序`
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
      const scoreCal = resolveCalTradeScore(h);
      const scoreNowcast = resolveNowcastScore(h);
      const belowMin = !!h.below_min_score;
      const hardReject = !!h.hard_reject;
      const scoreBase = fmtTableScore(h, score);
      let scoreShown =
        scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
      if (scoreShown === "—" && hardReject) scoreShown = "拒";
      const scoreCalShown = fmtTableScore(h, scoreCal);
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
      const scoreCalOor = !!h.score_calibration_eod_oor;
      const scoreCalTitle =
        scoreCal == null
          ? "暂无 g(ŷ_EOD) 映射（拟合并写入 live 后可见）"
          : scoreCalOor
            ? "g(ŷ_EOD) 域外钳制 · 悬停看 eod tip"
            : EOD_CAL_TITLE;
      const scoreNowcastTitle =
        scoreNowcast == null ? "暂无 nowcast · 有 ŷ_EOD 与 ŷ_τ 后可见" : NOWCAST_TITLE;
      let residual = null;
      if (
        score != null &&
        h.change_pct != null &&
        h.change_pct !== "" &&
        Number.isFinite(Number(h.change_pct))
      ) {
        residual = formatWatchingResidual(score, h.change_pct).residualNum;
      }
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
        `<td class="num">${escapeText(h.shares ?? "—")}</td>` +
        `<td class="num paper-hold-price">${fmtPriceUnit(
          h.price,
          h.unit,
          h.currency
        )}</td>` +
        `<td class="num paper-hold-open" title="当日开盘价">${fmtPriceUnit(
          h.open,
          h.unit,
          h.currency
        )}</td>` +
        `<td class="num paper-hold-chg ${metricCls(h.change_pct)}" title="相对昨收">${fmtPct(
          h.change_pct,
          { signed: true }
        )}</td>` +
        `<td class="num paper-hold-score watching-score-cal has-tip ${scoreCls(
          scoreCal
        )}${scoreCalOor ? " is-cal-oor" : ""}" data-score-detail="${scoreDetailJson}" data-score-tip="cal" title="${escapeText(
          scoreCalTitle
        )}">${escapeText(scoreCalShown)}</td>` +
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
        `<td class="num paper-hold-residual ${metricCls(
          residual
        )}" title="${RESIDUAL_TITLE}">${fmtPct(residual, {
          signed: true,
        })}</td>` +
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
    `<th title="最新成交价">现价</th>` +
    `<th title="当日开盘价">开盘价</th>` +
    `${sortThHtml("涨跌", "chg")}` +
    `${sortThHtml("EOD", "score_cal")}` +
    `${sortThHtml("TRADE", "score")}` +
    `${sortThHtml("NOWCAST", "score_nowcast")}` +
    `${sortThHtml("残差", "residual")}` +
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
