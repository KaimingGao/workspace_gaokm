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
  isHeuristicScoreScale,
} from "./fmt.js?v=p1128";
import { paginateItems, renderPagerHtml } from "../api_client.js";

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
        ? "按代码排序"
        : key === "score"
          ? "按评分排序"
          : key === "score_cal"
            ? "按校准分排序"
            : key === "pnl"
            ? "按浮盈亏排序 · 相对持仓成本：(现价÷成本−1)×100%"
            : key === "chg"
              ? "按当日涨跌幅排序 · 相对昨收"
              : "按市值排序";
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
      const belowMin = !!h.below_min_score;
      const hardReject = !!h.hard_reject;
      const scoreBase = fmtTableScore(h, score);
      let scoreShown =
        scoreBase !== "—" && belowMin ? `${scoreBase}↓` : scoreBase;
      if (scoreShown === "—" && hardReject) scoreShown = "拒";
      const scoreCalShown = fmtTableScore(h, scoreCal);
      const scoreTitle = hardReject
        ? String(h.reject_reason || "硬拒绝 · 无收益分")
        : isHeuristicScoreScale(h)
          ? score != null
            ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
            : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
          : belowMin
            ? `低于ŷ_EOD门槛 ${h.min_score ?? "—"}（表列为 ŷ_trade）· 悬停看详情`
            : "ŷ_trade · 悬停看 ŷ_EOD_rem / ŷ_τ";
      const oosFailed =
        !!h.oos_failed ||
        isHeuristicScoreScale(h) ||
        String(h.return_model_source || "").startsWith("oos_failed");
      const oosBadge = oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      const scoreCalOor = !!(
        h.score_calibration_eod_oor ||
        h.score_calibration_eod_rem_oor ||
        h.score_calibration_tau_oor
      );
      const scoreCalTitle =
        scoreCal == null
          ? "暂无 g(ŷ) 映射（拟合并写入 live 后可见）"
          : scoreCalOor
            ? "g(ŷ_trade) 对照 · 部分落在拟合域外（端点钳制）· 悬停看校准 tip"
            : "g(ŷ_trade) 对照 · 不进决策 · 悬停看 tip";
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

      const terms = h.score_formula_terms || null;
      const hasTerms =
        terms && Array.isArray(terms.terms) && terms.terms.length > 0;
      const scoreDetailJson = escapeText(
        JSON.stringify({
          predicted_score: h.predicted_score != null ? h.predicted_score : h.score,
          score: h.score != null ? h.score : h.predicted_score,
          predicted_score_tau:
            h.predicted_score_tau != null
              ? h.predicted_score_tau
              : h.score_rem != null
                ? h.score_rem
                : h.predicted_score_rem,
          predicted_score_blend: h.predicted_score_blend,
          predicted_score_eod: h.predicted_score_eod,
          predicted_score_eod_rem: h.predicted_score_eod_rem,
          predicted_score_tau_delta: h.predicted_score_tau_delta,
          predicted_score_nowcast: h.predicted_score_nowcast,
          dual_score_window: h.dual_score_window || null,
          nowcast_as_of: h.nowcast_as_of || null,
          nowcast_K: h.nowcast_K,
          nowcast_q: h.nowcast_q,
          predicted_score_cal: h.predicted_score_cal,
          predicted_score_eod_rem_cal: h.predicted_score_eod_rem_cal,
          predicted_score_tau_cal: h.predicted_score_tau_cal,
          predicted_score_blend_cal: h.predicted_score_blend_cal,
          score_calibration_applied: !!h.score_calibration_applied,
          score_calibration_enabled: !!h.score_calibration_enabled,
          score_calibration_eod_oor: !!h.score_calibration_eod_oor,
          score_calibration_eod_rem_oor: !!h.score_calibration_eod_rem_oor,
          score_calibration_tau_oor: !!h.score_calibration_tau_oor,
          score_calibration_note: h.score_calibration_note || null,
          score_calibration_partial: h.score_calibration_partial || null,
          realized_t1_to_tau: h.realized_t1_to_tau,
          score_rem: h.score_rem != null ? h.score_rem : h.predicted_score_rem,
          gap_pct: h.gap_pct,
          event_prior: h.event_prior || null,
          as_of_tau: h.as_of_tau || h.rem_tau || null,
          y_spec_tau: h.y_spec_tau || null,
          features_tau: h.features_tau || null,
          formula_terms_tau: h.formula_terms_tau || h.score_formula_terms_tau || null,
          score_formula_tau: h.score_formula_tau || null,
          factor_coefficients_tau: h.factor_coefficients_tau || null,
          dual_score_fusion: h.dual_score_fusion || null,
          dual_score_weights: h.dual_score_weights || null,
          formula: hasTerms ? "" : h.score_formula || "",
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
          return_model_source: h.return_model_source || "",
          formula_terms: terms,
          factor_coefficients: hasTerms ? {} : h.factor_coefficients || {},
        })
      );

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
        `<td class="num paper-hold-score has-tip ${scoreCls(score)}${
          belowMin ? " score-below-min" : ""
        }${hardReject ? " score-reject" : ""}" ` +
        `data-score-detail="${scoreDetailJson}" data-score-tip="trade" title="${escapeText(
          scoreTitle
        )}">${escapeText(scoreShown)}</td>` +
        `<td class="num paper-hold-score watching-score-cal has-tip ${scoreCls(
          scoreCal
        )}${scoreCalOor ? " is-cal-oor" : ""}" data-score-detail="${scoreDetailJson}" data-score-tip="cal" title="${escapeText(
          scoreCalTitle
        )}">${escapeText(scoreCalShown)}</td>` +
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
    `<th>股票</th><th class="watching-col-center">情绪</th><th>股数</th><th>现价</th>` +
    `<th title="当日开盘价">开盘价</th>` +
    `${sortThHtml("涨跌", "chg")}` +
    `<th title="持仓加权平均成本，对账用">成本</th>` +
    `${sortThHtml("市值", "market_value")}${sortThHtml("评分", "score")}` +
    `${sortThHtml("校准", "score_cal")}` +
    `${sortThHtml("浮盈亏", "pnl")}` +
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
