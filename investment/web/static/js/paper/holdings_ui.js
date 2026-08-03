/**
 * Paper · 持仓表：出处汇总 + 原生 HTML 表（回退）+ 操作条。
 * 主路径由 holdings_table_island.js（virtual_table 纯 DOM 虚拟网格）渲染。
 */

import {
  fmtPriceUnit,
  escapeText,
  fmtPct,
  metricCls,
} from "./fmt.js";
import { paginateItems, renderPagerHtml } from "../api_client.js";

const ORIGIN_HINT = {
  manual: "你手动建仓或加仓",
  strategy: "由「按策略调仓」生成",
  mixed: "手动建仓后被策略加过仓",
};

export function buildPaperOriginBarHtml(summary) {
  const originSummary = (summary && summary.origin_summary) || [];
  if (!originSummary.length) return "";
  const originBrief = originSummary
    .map((o) => `${o.origin_label || o.origin || "—"} ${o.count}只`)
    .join(" · ");
  return (
    `<details class="paper-origin-fold">` +
    `<summary class="paper-origin-fold-summary" title="按出处汇总市值与浮盈亏">出处对照 · ${escapeText(
      originBrief
    )}</summary>` +
    `<div class="paper-origin-summary" aria-label="出处对照明细">` +
    originSummary
      .map((o) => {
        const key = o.origin || "unknown";
        const pnl = o.pnl_pct;
        const pnlText =
          pnl == null
            ? ""
            : ` · ${Number(pnl) >= 0 ? "+" : ""}${Number(pnl).toFixed(1)}%`;
        return (
          `<span class="paper-origin-chip is-${escapeText(
            String(key)
          )}" title="市值占比 ${escapeText(String(o.weight_pct ?? "—"))}%">` +
          `${escapeText(o.origin_label || key)} ${escapeText(String(o.count))}只` +
          ` · ${escapeText(String(o.market_value ?? "—"))}${pnlText}` +
          `</span>`
        );
      })
      .join("") +
    `</div></details>`
  );
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
}) {
  const scoreCls = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "score-na";
    if (n >= 60) return "score-high";
    if (n >= 50) return "score-mid";
    return "score-low";
  };

  const fmtScore = (v) => {
    if (v == null) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(1);
  };

  function sortThHtml(label, key) {
    const active = holdingsSortKey === key;
    const arrow = !active ? "" : holdingsSortDir === "asc" ? " ↑" : " ↓";
    const nextHint =
      key === "code" ? "按代码排序" : key === "score" ? "按评分排序" : "按市值排序";
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
      const score = h.score;
      const belowMin = !!h.below_min_score;
      const scoreBase = fmtScore(score);
      const scoreShown =
        scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
      const scoreTitle = belowMin
        ? `低于选股门槛 ${h.min_score ?? "—"}（仍显示分数）· 悬停看详情`
        : "悬停查看评分与权重来源";
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

      return (
        `<tr data-code="${escapeText(code)}" data-shares="${escapeText(
          h.shares ?? ""
        )}" ` +
        `class="paper-hold-row${active}${adjustActive}${focusCls}" title="点击选中并查看曲线">` +
        `<td class="paper-wl-name">` +
        `<span class="paper-wl-name-text">${escapeText(name)}</span>` +
        `<span class="paper-wl-code">${escapeText(code)}</span>` +
        `</td>` +
        `<td class="num">${escapeText(h.shares ?? "—")}</td>` +
        `<td class="num paper-hold-price">${fmtPriceUnit(
          h.price,
          h.unit,
          h.currency
        )}</td>` +
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
        `<td class="num paper-hold-score ${scoreCls(score)}${
          belowMin ? " score-below-min" : ""
        }" ` +
        `data-score-detail="${escapeText(
          JSON.stringify({
            formula: h.score_formula || "",
            reasons: h.score_reasons || [],
            hard_reject: h.hard_reject,
            reject_reason: h.reject_reason || "",
            weight_source: h.weight_source || "",
            cluster_label: h.cluster_label || "",
            cluster_mode: h.cluster_mode || "",
            cluster_version: h.cluster_version,
            score_global: h.score_global,
            score_cluster: h.score_cluster,
            min_score: h.min_score,
            below_min_score: belowMin,
          })
        )}" title="${escapeText(scoreTitle)}">${escapeText(scoreShown)}</td>` +
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
    `<th>股票</th><th>股数</th><th>现价</th><th title="持仓加权平均成本，对账用">成本</th>` +
    `${sortThHtml("市值", "market_value")}${sortThHtml("评分", "score")}` +
    `<th title="相对持仓成本：(现价÷成本−1)×100%；加仓则为加权成本，非当日涨跌">浮盈亏</th>` +
    `<th>开始</th><th class="paper-hold-origin">出处</th>` +
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
