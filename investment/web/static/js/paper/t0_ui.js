/** Paper · 做T 指标与预演表渲染（从 paper.js 抽出）。 */

import { yTauMapScoreTip } from "./execution_ui.js";
import { renderT0Viz, wireT0SkipTips } from "./t0_viz.js?v=p2261";
import { buildT0ReportHtml, fmtT0DirDays } from "./t0_report.js?v=p2270";
import {
  buildT0TradeTableHtml,
  pickDetailDays,
  pickTradeDays,
  stockCellHtml,
  T0_TRADE_TABLE_MAX_ROWS,
  wireT0DayDebugExpand,
  wireT0ProcessTips,
  stampStockFitTiers,
} from "./t0_table.js?v=p2261";

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildZeroTradeHint(data) {
  const reasons = data.skip_reason_top || [];
  const top = reasons[0];
  const reasonText = reasons.map((r) => String(r.reason || "")).join(" ");
  const ampSkip =
    reasonText.includes("振幅不足") ||
    (reasonText.includes("振幅") &&
      !reasonText.includes("空间用尽") &&
      !reasonText.includes("ŷ_path") &&
      !reasonText.includes("y_path"));
  const prefixVsPathSkip =
    reasonText.includes("空间用尽") ||
    reasonText.includes(">|ŷ_path|") ||
    (reasonText.includes("前缀振幅") &&
      (reasonText.includes("ŷ_path") || reasonText.includes("y_path")));
  const tauEntrySkip =
    reasonText.includes("τ带") ||
    reasonText.includes("τ入场") ||
    (reasonText.includes("开盘×(1+") &&
      (reasonText.includes("买价") || reasonText.includes("卖价")) &&
      !reasonText.includes("τ出场"));
  const tauExitSkip = reasonText.includes("τ出场");
  const tplus1Skip =
    reasonText.includes("T+1") || reasonText.includes("可卖旧仓") || reasonText.includes("可卖 0");
  const lotSkip =
    reasonText.includes("动仓不足") || reasonText.includes("不足1手") || reasonText.includes("不足 1 手");
  const compositeSkip =
    reasonText.includes("复合确认") ||
    reasonText.includes("未达开盘锚") ||
    reasonText.includes("动量未翻转");
  const envSkip =
    reasonText.includes("环境闸") ||
    (reasonText.includes("环境") &&
      (reasonText.includes("振幅") || reasonText.includes("path") || reasonText.includes("单边")));
  const multiSlotMiss = reasonText.includes("多轮均未成交");
  const dir = (data.rules && data.rules.direction) || data.direction || "—";
  const tips = tplus1Skip
    ? "旧仓被 T+1 锁定；需隔日可卖仓才能反T先卖 / 正T卖旧"
    : compositeSkip
      ? "历史：复合确认/开盘锚/动量闸（v6 已下线）；重跑预演后应消失"
      : envSkip
        ? "历史：环境闸（振幅/|ŷ_path|/单边，v6 已下线）；重跑预演后应消失"
        : multiSlotMiss
          ? "各轮均未开仓；看明细槽位 reason，或放宽带宽 δ / 检查 ĉ_τ"
          : tauExitSkip
            ? "第二腿出场价未过 open×(1+(clamp(ŷ_τ×裕度,min,max)+价偏)/100)；可调裕度/价偏或关 τ卖价闸/τ买价闸"
            : tauEntrySkip
              ? "历史：触发根入场价未过τ带（v6 已下线，第一腿按确认根收盘）；重跑预演后应消失"
              : prefixVsPathSkip
                ? "历史：前缀振幅>|ŷ_path|×裕度；v6 已改收盘带宽选腿，重跑预演后应消失"
                : ampSkip
                  ? "旧振幅下限跳过（门禁已下线）；重跑预演后应消失"
                  : lotSkip
                    ? "仓位×做T比例不足 1 手；可提高做T比例或加仓"
                    : "未过选向/选腿/价闸；看 skip_reason_top 与槽位明细";
  const mm = data.minute_meta || {};
  const missingMin = Number(data.missing_minute_days) || 0;
  const minuteHint =
    missingMin > 0
      ? ` · 缺分钟 ${missingMin} 日已跳过`
      : data.use_minute === false
        ? ` · 5m 不可用${mm.error ? `：${escapeHtml(String(mm.error).slice(0, 60))}` : ""}`
        : "";
  return (
    `未成交${data.skip_days != null ? `（${data.skip_days} 日跳过）` : ""}` +
    (top ? ` · 主因 ${escapeHtml(top.reason)}×${top.count}` : "") +
    ` · ${escapeHtml(String(dir))} · ${escapeHtml(tips)}${minuteHint}`
  );
}

function placeT0DaysBelowContrib(vizEl, daysEl) {
  if (!daysEl) return;
  if (vizEl && !vizEl.hidden) {
    vizEl.appendChild(daysEl);
    return;
  }
  if (vizEl) vizEl.insertAdjacentElement("afterend", daysEl);
}

function finishT0Days(vizEl, daysEl, tipCtrl) {
  placeT0DaysBelowContrib(vizEl, daysEl);
  wireT0DayDebugExpand(daysEl);
  if (!tipCtrl) return;
  wireT0SkipTips(vizEl, tipCtrl);
  wireT0ProcessTips(daysEl, tipCtrl);
  if (daysEl && daysEl.dataset.scoreTipWired !== "1" && typeof tipCtrl.bindHost === "function") {
    tipCtrl.bindHost(daysEl, {
      scoreSelector:
        ".paper-t0-y-score[data-score-detail], .paper-t0-dir-score[data-score-detail]",
    });
  }
}

export function renderPaperT0(els, data) {
  const { metricsEl, daysEl, vizEl, previewEl, tipCtrl } = els || {};
  if (!metricsEl) return;
  if (!data || !data.success) {
    metricsEl.innerHTML = "";
    if (daysEl) daysEl.innerHTML = "";
    renderT0Viz(vizEl, null);
    finishT0Days(vizEl, daysEl, tipCtrl);
    return;
  }

  if (previewEl) {
    previewEl.hidden = true;
    previewEl.innerHTML = "";
  }

  const tradeDays = Number(data.t0_trade_days) || 0;
  const zeroHint = tradeDays <= 0 ? buildZeroTradeHint(data) : "";
  metricsEl.innerHTML = buildT0ReportHtml(data, { zeroHint });

  const days = pickDetailDays(data);
  if (!daysEl) {
    renderT0Viz(vizEl, data);
    return;
  }
  if (!days.length) {
    const skip = data.skip_days != null ? ` · 跳过 ${data.skip_days} 日` : "";
    const trades =
      data.t0_trade_days != null ? `指标成交日 ${data.t0_trade_days}` : "无成交样本";
    daysEl.innerHTML =
      `<div class="paper-t0-days-head">` +
      `<h4 class="paper-t0-days-title">成交明细</h4>` +
      `<p class="quant-trades-caption">${trades}${skip} · 样本为空</p>` +
      `</div>`;
    renderT0Viz(vizEl, data);
    finishT0Days(vizEl, daysEl, tipCtrl);
    return;
  }

  const sessDate =
    data.session_date ||
    data.as_of ||
    (days[0] && (days[0].date || days[0].session_date)) ||
    null;
  const captionBits = [
    days.length > T0_TRADE_TABLE_MAX_ROWS
      ? `样本 ${days.length} 笔（表内最近 ${T0_TRADE_TABLE_MAX_ROWS} 笔）`
      : `${days.length} 笔成交`,
    `指标日 ${data.t0_trade_days ?? "—"}`,
    data.cover_rate_pct != null ? `往返 ${data.cover_rate_pct}%` : null,
    `向 ${fmtT0DirDays(data)}`,
    data.rules && data.rules.t0_slots_enabled ? "仅成交槽位" : null,
    sessDate ? `会话 ${sessDate}` : null,
  ].filter(Boolean);
  const caption =
    `<div class="paper-t0-days-head">` +
    `<h4 class="paper-t0-days-title">成交明细</h4>` +
    `<p class="quant-trades-caption">${captionBits.join(" · ")}` +
    ` · <span title="${escapeHtml(yTauMapScoreTip())}">收盘带宽</span>` +
    ` · <span title="y_* 为该行「日」列会话快照（做T决策时冻结）；5m 路径仓在研究枢纽写入；≠持仓实时分 / ≠调仓ŷ开关">会话快照ŷ · 5m仓</span></p>` +
    `</div>`;

  daysEl.innerHTML = buildT0TradeTableHtml({
    data,
    days,
    caption,
    maxRows: T0_TRADE_TABLE_MAX_ROWS,
  });
  renderT0Viz(vizEl, data);
  stampStockFitTiers(daysEl);
  finishT0Days(vizEl, daysEl, tipCtrl);
}

function previewMetricChip(cls, label, value) {
  return (
    `<span class="paper-t0-desk-chip ${cls}">` +
    `<span class="paper-t0-desk-chip-k">${escapeHtml(label)}</span>` +
    `<span class="paper-t0-desk-chip-v">${escapeHtml(String(value ?? 0))}</span>` +
    `</span>`
  );
}

function buildSkipReasonTop(skips) {
  const counts = {};
  for (const d of skips || []) {
    const r = String(d.reason || d.skip_category || "other");
    counts[r] = (counts[r] || 0) + 1;
  }
  return Object.entries(counts)
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count);
}

/** 预演 results → 成交明细日行（仅有成交；跳过不进表）。 */
function previewResultsToTradeDays(results) {
  return (results || [])
    .filter((r) => r && !r.skipped && (r.trades || []).length)
    .map((r) => ({
      ...r,
      direction: r.direction_used || r.direction || "",
      minute_path:
        !!r.minute_path ||
        r.path_mode === "first_touch" ||
        r.intraday_path === "first_touch",
    }));
}

export function renderPaperT0Preview(els, data) {
  const { previewEl, confirmEl } = els || {};
  if (!previewEl) return;
  // 只跟做 T 的「手动预演」行（#paper-t0-run）。两边都叫 .paper-t0-manual-actions，
  // document.querySelector 会命中策略调仓那一块，把预演结果挪到调仓卡下。
  const t0Run = document.getElementById("paper-t0-run");
  const actions = t0Run && t0Run.closest(".paper-t0-manual-actions");
  if (actions && previewEl.previousElementSibling !== actions) {
    actions.insertAdjacentElement("afterend", previewEl);
  }
  if (!data || !data.success) {
    previewEl.hidden = true;
    previewEl.innerHTML = "";
    if (confirmEl) confirmEl.hidden = true;
    return;
  }
  const allRows = data.results || [];
  const days = previewResultsToTradeDays(allRows);
  const tradeDays = days;
  const skipRows = allRows.filter((r) => r && r.skipped);
  const tradeN = (data.trades || []).length;
  const pnl = data.pnl_total ?? 0;
  const exposure = data.exposure_pnl_total ?? 0;
  const skipN = data.skip_count ?? skipRows.length;
  const sellThenBuyN = tradeDays.filter((d) => d.direction === "sell_then_buy").length;
  const buyThenSellN = tradeDays.filter((d) => d.direction === "buy_then_sell").length;
  const pnlN = Number(pnl);
  const pnlCls =
    Number.isFinite(pnlN) && pnlN > 0
      ? "is-done"
      : Number.isFinite(pnlN) && pnlN < 0
        ? "is-locked"
        : "is-univ";
  const rules = data.rules || (data.execution && data.execution.t0) || {};
  const tableData = {
    ...data,
    rules,
    execution: data.execution || { t0: rules },
    holding_count: Math.max(allRows.length, days.length, 1),
    trade_days_sample: tradeDays,
    t0_trade_days: tradeDays.length,
  };
  previewEl.hidden = false;
  if (confirmEl) confirmEl.hidden = tradeN === 0 && !(data.pnl_total > 0);
  const prevFold = previewEl.querySelector("details.paper-t0-preview-fold");
  const keepOpen = prevFold ? !!prevFold.open : true;
  const openAttr = keepOpen ? " open" : "";
  const sess =
    data.session_date ||
    (days.find((d) => d && d.date) || {}).date ||
    "";
  const chips =
    `<span class="paper-t0-desk-chips">` +
    (sess
      ? `<span class="paper-t0-desk-sess" title="会话日">${escapeHtml(
          String(sess).slice(0, 10)
        )}</span>`
      : "") +
    previewMetricChip("is-univ", "票", `${tradeDays.length}/${allRows.length}`) +
    previewMetricChip("is-univ", "腿", tradeN) +
    previewMetricChip("is-done", "正/反", `${buyThenSellN}/${sellThenBuyN}`) +
    previewMetricChip(pnlCls, "PnL", Number.isFinite(pnlN) ? pnlN.toFixed(1) : pnl) +
    previewMetricChip(
      "is-idle",
      "敞口",
      Number.isFinite(Number(exposure)) ? Number(exposure).toFixed(1) : exposure
    ) +
    previewMetricChip("is-locked", "跳过", skipN) +
    `</span>`;
  const tip = escapeHtml(yTauMapScoreTip());
  const topSkip = buildSkipReasonTop(skipRows).slice(0, 2);
  const skipHint = topSkip.length
    ? ` · 主因 ${topSkip.map((t) => `${t.reason}×${t.count}`).join(" / ")}`
    : "";
  const tradeBlock = days.length
    ? buildT0TradeTableHtml({
        data: tableData,
        days,
        caption:
          `<div class="paper-t0-days-head paper-t0-preview-days-head">` +
          `<h4 class="paper-t0-days-title">预演明细</h4>` +
          `<p class="quant-trades-caption">成交 ${tradeDays.length} · 跳过 ${skipN}` +
          escapeHtml(skipHint) +
          ` · 正${buyThenSellN}/反${sellThenBuyN}` +
          ` · <span title="${tip}">收盘带宽</span>` +
          ` · <span title="y_* 为该成交会话日快照，不是持仓表实时分">会话日快照分</span></p>` +
          `</div>`,
        maxRows: Math.max(T0_TRADE_TABLE_MAX_ROWS, days.length),
        preserveOrder: true,
        showRealized: false,
      })
    : `<p class="paper-t0-desk-empty">无持仓结果</p>`;
  previewEl.innerHTML =
    `<details class="paper-t0-desk-fold paper-t0-preview-fold"${openAttr}>` +
    `<summary class="paper-t0-desk-fold-sum">` +
    `<span class="paper-t0-desk-fold-title">今日预演</span>` +
    chips +
    `</summary>` +
    `<div class="paper-t0-desk-board">` +
    tradeBlock +
    `<p class="paper-t0-desk-foot" title="${tip}">预演不改账本 · 确认「手动落账」后才写入 · τ = y_τ</p>` +
    `</div></details>`;
  stampStockFitTiers(previewEl);
}

function workerLastRunToTableData(lastRun, execution) {
  const results = ((lastRun && lastRun.results) || []).filter(
    (r) => r && !r.skipped && (r.trades || []).length
  );
  const rules = (lastRun && lastRun.rules) || (execution && execution.t0) || {};
  const sess = String(lastRun?.session_date || "").slice(0, 10);
  const days = results.map((r) => {
    const raw = String(r.date || sess || "").trim();
    const iso = (raw.match(/^(\d{4}-\d{2}-\d{2})/) || [])[1] || raw.slice(0, 10);
    return {
      ...r,
      direction: r.direction || r.direction_used,
      date: iso || sess,
    };
  });
  const traded = pickTradeDays({ trade_days_sample: days, days });
  const tag = lastRun?.source === "paper_t0_auto" ? "自动" : "手动";
  return {
    success: true,
    rules,
    execution: execution || { t0: rules },
    trade_days_sample: days,
    t0_trade_days: traded.length,
    holding_count: days.length,
    workerTag: tag,
    sessionDate: lastRun?.session_date || "",
  };
}

/** Worker 上次落账成交/跳过明细（复用回测成交表）。 */
export function renderPaperT0WorkerTrades(el, { t0Auto, execution, liveScoresByCode } = {}) {
  if (!el) return;
  const lr = (t0Auto && t0Auto.last_run) || null;
  if (!lr || lr.ts == null) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const prevFold = el.querySelector("details.paper-t0-ledger-fold");
  const keepOpen = prevFold ? !!prevFold.open : false;
  const openAttr = keepOpen ? " open" : "";

  const foldShell = (metaHtml, bodyHtml) =>
    `<details class="paper-t0-desk-fold paper-t0-ledger-fold"${openAttr}>` +
    `<summary class="paper-t0-desk-fold-sum">` +
    `<span class="paper-t0-desk-fold-title">落账明细</span>` +
    metaHtml +
    `</summary>` +
    bodyHtml +
    `</details>`;

  const results = lr.results || [];
  if (!results.length) {
    el.innerHTML = foldShell(
      `<span class="paper-t0-desk-fold-meta">尚无成交</span>`,
      `<p class="paper-t0-desk-empty">尚无成交明细 · 有买/卖落账后显示</p>`
    );
    return;
  }
  const data = {
    ...workerLastRunToTableData(lr, execution),
    liveScoresByCode: liveScoresByCode || null,
  };
  const days = pickTradeDays(data);
  if (!days.length) {
    el.innerHTML = foldShell(
      `<span class="paper-t0-desk-fold-meta">尚无成交</span>`,
      `<p class="paper-t0-desk-empty">尚无成交明细 · 有买/卖落账后显示</p>`
    );
    return;
  }
  const tip = escapeHtml(yTauMapScoreTip());
  const tag = data.workerTag || "—";
  const sellThenBuyN = days.filter((d) => d.direction === "sell_then_buy").length;
  const buyThenSellN = days.filter((d) => d.direction === "buy_then_sell").length;
  const countLabel =
    days.length > T0_TRADE_TABLE_MAX_ROWS
      ? `${days.length} 笔 · 表内 ${T0_TRADE_TABLE_MAX_ROWS}`
      : `${days.length} 笔`;
  const chips =
    `<span class="paper-t0-desk-chips">` +
    (data.sessionDate
      ? `<span class="paper-t0-desk-sess" title="落账会话">${escapeHtml(
          String(data.sessionDate)
        )}</span>`
      : "") +
    `<span class="paper-t0-desk-chip is-univ">` +
    `<span class="paper-t0-desk-chip-k">${escapeHtml(tag)}</span>` +
    `<span class="paper-t0-desk-chip-v">${escapeHtml(countLabel)}</span>` +
    `</span>` +
    (buyThenSellN
      ? `<span class="paper-t0-desk-chip is-done"><span class="paper-t0-desk-chip-k">正</span>` +
        `<span class="paper-t0-desk-chip-v">${buyThenSellN}</span></span>`
      : "") +
    (sellThenBuyN
      ? `<span class="paper-t0-desk-chip is-idle"><span class="paper-t0-desk-chip-k">反</span>` +
        `<span class="paper-t0-desk-chip-v">${sellThenBuyN}</span></span>`
      : "") +
    `</span>`;
  el.innerHTML = foldShell(
    chips,
    `<div class="paper-t0-desk-board">` +
      buildT0TradeTableHtml({
        data,
        days,
        maxRows: T0_TRADE_TABLE_MAX_ROWS,
        showDelete: true,
        showRealized: false,
      }) +
      `<p class="paper-t0-desk-foot" title="${tip}">收盘带宽 · 删除将冲正账本</p>` +
      `</div>`
  );
  stampStockFitTiers(el);
}

const DESK_PHASE_LABEL = {
  skipped: "终锁",
  idle: "监视",
  after_leg1: "一腿",
  done: "完成",
};

function deskPhaseBadge(phase, locked) {
  const ph = String(phase || "idle");
  const label = DESK_PHASE_LABEL[ph] || ph;
  const cls = locked || ph === "skipped" ? "is-locked" : `is-${ph}`;
  return `<span class="paper-t0-desk-phase ${cls}">${escapeHtml(label)}</span>`;
}

function deskDirCell(direction) {
  if (direction === "buy_then_sell") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-buy-then-sell" title="先买后卖">正T</span></td>`;
  }
  if (direction === "sell_then_buy") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-sell-then-buy" title="先卖后买">反T</span></td>`;
  }
  return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-none">—</span></td>`;
}

function deskClock(ts) {
  const s = String(ts || "").trim();
  if (!s) return "—";
  const m = s.match(/(\d{1,2}:\d{2})(?::\d{2})?$/);
  return m ? m[1] : s.slice(11, 16) || s;
}

function classifyDeskNote(note, locked) {
  const r = String(note || "");
  const tplus1 =
    r.includes("T+1") ||
    r.includes("可卖旧仓") ||
    r.includes("卖不掉旧仓") ||
    (r.includes("可卖 0") && r.includes("无法先卖"));
  if (tplus1) return { id: "lot", label: "仓/钱" };
  if (locked) {
    if (r.includes("超额不足") || r.includes("R̂_τ 缺失") || r.includes("|R̂_τ|"))
      return { id: "r_tau_flat", label: "R不足" };
    if (r.includes("横盘")) return { id: "y_tau_flat", label: "τ横盘" };
    if (r.includes("y_trade") || r.includes("幅度不足")) return { id: "y_trade_weak", label: "幅度" };
    if (r.includes("异号")) return { id: "trade_tau_sign", label: "异号" };
    if (r.includes("午盘后") || r.includes("无成交腿")) return { id: "deadline", label: "午盘截止" };
    if (r.includes("stance")) return { id: "stance", label: "stance" };
    if (r.includes("缺现金") || r.includes("动仓不足") || r.includes("无可卖"))
      return { id: "lot", label: "仓/钱" };
    return { id: "locked", label: "终锁" };
  }
  if (r.includes("τ出场")) return { id: "tau_exit", label: "出场价" };
  if (r.includes("τ带") || r.includes("τ入场") || (r.includes("开盘×(1+") && (r.includes("买价") || r.includes("卖价"))))
    return { id: "tau_entry", label: "入场价" };
  if (r.includes("空间用尽") || (r.includes("前缀振幅") && (r.includes("ŷ_path") || r.includes("y_path"))))
    return { id: "prefix_vs_path", label: "空间用尽" };
  if (r.includes("振幅不足") || (r.includes("振幅") && !r.includes("前缀")))
    return { id: "amplitude", label: "振幅" };
  if (r.includes("未触及")) return { id: "trigger", label: "未触价" };
  if (r.includes("午休")) return { id: "lunch", label: "午休" };
  if (r.includes("解锁") || r.includes("重评")) return { id: "reeval", label: "待重评" };
  if (r.includes("算分") || r.includes("对齐") || r.includes("就绪"))
    return { id: "pending", label: "待就绪" };
  if (r.includes("已定方向") || r.includes("等待确认") || r.includes("等待触价"))
    return { id: "armed", label: "待确认" };
  return { id: "watch", label: "监视" };
}

function resolveDeskNote(r) {
  if (r.locked) return r.reason || "当日终态锁死";
  if (r.reason) return r.reason;
  if (r.unlocked_from_skip) return "已解锁，等下一根 5m 重评";
  if (r.legs_written > 0) return `${r.legs_written} 腿已落账`;
  if (r.direction) return "已定方向，等待下一根 5m / 第二腿触价";
  return "等待下一根 5m";
}

function deskChip(cls, label, value) {
  const v = Number(value || 0);
  if (!v && cls !== "is-univ") return "";
  return (
    `<span class="paper-t0-desk-chip ${cls}">` +
    `<span class="paper-t0-desk-chip-k">${escapeHtml(label)}</span>` +
    `<span class="paper-t0-desk-chip-v">${escapeHtml(String(v))}</span>` +
    `</span>`
  );
}

/** 今日盘中盯盘状态（idle / skipped 锁死 / 已一腿 / 完成）。 */
export function renderPaperT0WorkerDesk(el, desk) {
  if (!el) return;
  if (!desk || !Array.isArray(desk.rows)) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const prevFold = el.querySelector("details.paper-t0-desk-fold");
  const keepOpen = prevFold ? !!prevFold.open : false;
  const counts = desk.counts || {};
  const lockedN = Number(desk.locked_count || 0);
  const n = Number(desk.universe_count || desk.rows.length || 0);
  const sess = desk.session_date || desk.state_session_date || "—";
  const chips =
    deskChip("is-univ", "持仓", n) +
    deskChip("is-locked", "终锁", lockedN) +
    deskChip("is-idle", "监视", counts.idle) +
    deskChip("is-leg1", "一腿", counts.after_leg1) +
    deskChip("is-done", "完成", counts.done) +
    deskChip("is-legs", "腿Σ", desk.legs_written_total);
  const openAttr = keepOpen ? " open" : "";
  const sessLine = `<span class="paper-t0-desk-sess" title="交易日会话">${escapeHtml(
    String(sess)
  )}</span>`;

  if (!desk.rows.length) {
    el.innerHTML =
      `<details class="paper-t0-desk-fold"${openAttr}>` +
      `<summary class="paper-t0-desk-fold-sum">` +
      `<span class="paper-t0-desk-fold-title">今日盯盘状态</span>` +
      sessLine +
      `<span class="paper-t0-desk-chips">${chips || ""}</span>` +
      `</summary>` +
      `<p class="paper-t0-desk-empty">${escapeHtml(
        desk.note || "尚无今日盘中状态"
      )}</p>` +
      `</details>`;
    return;
  }

  const staleBanner =
    desk.state_aligned === false
      ? `<p class="paper-t0-desk-empty paper-t0-desk-stale">${escapeHtml(
          desk.note || "盘中状态尚未对齐今日会话"
        )}</p>`
      : "";

  const body = desk.rows
    .map((r) => {
      const note = resolveDeskNote(r);
      const cat = classifyDeskNote(note, r.locked);
      const clock = deskClock(r.last_bar_ts);
      const legs = Number(r.legs_written || 0);
      const code = String(r.stock_code || "").trim();
      const name = String(r.stock_name || code).trim();
      const delBtn = code
        ? `<button type="button" class="paper-t0-desk-clear" data-code="${escapeHtml(
            code
          )}" data-name="${escapeHtml(name)}" data-legs="${legs}" title="删除盯盘状态（不冲正账本）">删除</button>`
        : `<span class="paper-t0-leg-empty">—</span>`;
      return (
        `<tr class="${r.locked ? "is-locked" : ""}" data-phase="${escapeHtml(
          String(r.phase || "idle")
        )}" data-code="${escapeHtml(code)}">` +
        stockCellHtml(r) +
        deskDirCell(r.direction) +
        `<td class="paper-t0-col-phase">${deskPhaseBadge(r.phase, r.locked)}</td>` +
        `<td class="num paper-t0-col-legs">${legs > 0 ? legs : "—"}</td>` +
        `<td class="num paper-t0-col-clock" title="${escapeHtml(
          String(r.last_bar_ts || "")
        )}">${escapeHtml(clock)}</td>` +
        `<td class="paper-t0-col-cat">` +
        `<span class="paper-t0-desk-cat is-${escapeHtml(cat.id)}">${escapeHtml(
          cat.label
        )}</span>` +
        `</td>` +
        `<td class="paper-t0-col-reason" title="${escapeHtml(note)}">` +
        `<span class="paper-t0-desk-reason">${escapeHtml(note)}</span>` +
        `</td>` +
        `<td class="paper-t0-col-act">${delBtn}</td>` +
        `</tr>`
      );
    })
    .join("");

  const foot =
    lockedN > 0
      ? `<p class="paper-t0-desk-foot">终锁票当日不再重评门槛；带宽未破仍可随 5m 推进。删除后 Worker 可重新监视（不改账本）。</p>`
      : `<p class="paper-t0-desk-foot">≥11:30 无成交腿一律终锁；有腿票继续盯盘至收盘回补。删除仅去盘中状态。</p>`;

  el.innerHTML =
    `<details class="paper-t0-desk-fold"${openAttr}>` +
    `<summary class="paper-t0-desk-fold-sum">` +
    `<span class="paper-t0-desk-fold-title">今日盯盘状态</span>` +
    sessLine +
    `<span class="paper-t0-desk-chips">${chips}</span>` +
    `</summary>` +
    staleBanner +
    `<div class="paper-t0-desk-board">` +
    `<div class="quant-weight-table-wrap paper-t0-desk-wrap">` +
    `<table class="quant-weight-table paper-t0-table paper-t0-desk-table">` +
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-dir" />` +
    `<col class="paper-t0-col-phase" />` +
    `<col class="paper-t0-col-legs" />` +
    `<col class="paper-t0-col-clock" />` +
    `<col class="paper-t0-col-cat" />` +
    `<col class="paper-t0-col-reason" />` +
    `<col class="paper-t0-col-act" />` +
    `</colgroup>` +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">标的</th>` +
    `<th scope="col" class="paper-t0-col-dir">方向</th>` +
    `<th scope="col" class="paper-t0-col-phase">阶段</th>` +
    `<th scope="col" class="num paper-t0-col-legs">腿</th>` +
    `<th scope="col" class="num paper-t0-col-clock">最近K</th>` +
    `<th scope="col" class="paper-t0-col-cat">类别</th>` +
    `<th scope="col" class="paper-t0-col-reason">说明</th>` +
    `<th scope="col" class="paper-t0-col-act">操作</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>` +
    foot +
    `</div></details>`;
  stampStockFitTiers(el);
}
