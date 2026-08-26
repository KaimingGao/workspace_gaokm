/** Paper · 做T 指标与预演表渲染（从 paper.js 抽出）。 */

import { yTauMapScoreTip, normalizeYTauMap } from "./execution_ui.js";
import { renderT0Viz } from "./t0_viz.js";
import { buildT0ReportHtml } from "./t0_report.js";
import {
  buildT0TradeTableHtml,
  pickTradeDays,
  stockCellHtml,
  T0_TRADE_TABLE_MAX_ROWS,
} from "./t0_table.js";

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
  const ampSkip = reasonText.includes("振幅");
  const tplus1Skip =
    reasonText.includes("T+1") || reasonText.includes("可卖旧仓") || reasonText.includes("可卖 0");
  const lotSkip =
    reasonText.includes("动仓不足") || reasonText.includes("不足1手") || reasonText.includes("不足 1 手");
  const dir = (data.rules && data.rules.direction) || data.direction || "—";
  const sell = (data.rules && data.rules.sell_trigger_pct) ?? "2";
  const buy = (data.rules && data.rules.buy_trigger_pct) ?? "1.5";
  const tips = tplus1Skip
    ? "旧仓被 T+1 锁定；需隔日可卖仓才能正T先卖 / 反T卖旧"
    : ampSkip
      ? "振幅门禁偏严；可调低振幅下限或关闭 ATR 自适应"
      : lotSkip
        ? "仓位×做T比例不足 1 手；可提高做T比例或加仓"
        : `未触及卖 +${sell}% / 买 -${buy}% 触发；可降阈值`;
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

export function renderPaperT0(els, data) {
  const { metricsEl, daysEl, vizEl, previewEl } = els || {};
  if (!metricsEl) return;
  if (!data || !data.success) {
    metricsEl.innerHTML = "";
    if (daysEl) daysEl.innerHTML = "";
    renderT0Viz(vizEl, null);
    placeT0DaysBelowContrib(vizEl, daysEl);
    return;
  }

  if (previewEl) {
    previewEl.hidden = true;
    previewEl.innerHTML = "";
  }

  const tradeDays = Number(data.t0_trade_days) || 0;
  const zeroHint = tradeDays <= 0 ? buildZeroTradeHint(data) : "";
  metricsEl.innerHTML = buildT0ReportHtml(data, { zeroHint });

  const days = pickTradeDays(data);
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
    placeT0DaysBelowContrib(vizEl, daysEl);
    return;
  }

  const enter =
    data.rules && data.rules.y_tau_enter != null && Number.isFinite(Number(data.rules.y_tau_enter))
      ? Number(data.rules.y_tau_enter)
      : 0.25;
  const tauMap = normalizeYTauMap(data.rules && data.rules.y_tau_map);
  const captionBits = [
    days.length > T0_TRADE_TABLE_MAX_ROWS
      ? `样本 ${days.length} 笔（表内最近 ${T0_TRADE_TABLE_MAX_ROWS} 笔）`
      : `${days.length} 笔成交`,
    `指标日 ${data.t0_trade_days ?? "—"}`,
    data.cover_rate_pct != null ? `往返 ${data.cover_rate_pct}%` : null,
    `正${data.long_t_days ?? 0}/反${data.reverse_t_days ?? 0}`,
  ].filter(Boolean);
  const caption =
    `<div class="paper-t0-days-head">` +
    `<h4 class="paper-t0-days-title">成交明细</h4>` +
    `<p class="quant-trades-caption">${captionBits.join(" · ")}` +
    ` · <span title="${escapeHtml(yTauMapScoreTip(tauMap, enter))}">τ = y_τ</span></p>` +
    `</div>`;

  daysEl.innerHTML = buildT0TradeTableHtml({
    data,
    days,
    caption,
    maxRows: T0_TRADE_TABLE_MAX_ROWS,
  });
  renderT0Viz(vizEl, data);
  placeT0DaysBelowContrib(vizEl, daysEl);
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

/** 预演 results → 与回测成交明细同构的日行（含跳过，便于看 y_*）。 */
function previewResultsToTradeDays(results) {
  return (results || [])
    .filter((r) => r && (r.stock_code || r.skipped || (r.trades || []).length))
    .map((r) => ({
      ...r,
      direction: r.direction_used || r.direction || "",
      minute_path:
        !!r.minute_path ||
        r.path_mode === "first_touch" ||
        r.intraday_path === "first_touch",
    }))
    .sort((a, b) => {
      const as = a.skipped ? 1 : 0;
      const bs = b.skipped ? 1 : 0;
      return as - bs;
    });
}

export function renderPaperT0Preview(els, data) {
  const { previewEl, confirmEl } = els || {};
  if (!previewEl) return;
  // 保证预演结果始终紧挨「手动预演」按钮行下方
  const actions = document.querySelector(".paper-t0-manual-actions");
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
  const tradeDays = days.filter((d) => !d.skipped && (d.trades || []).length);
  const skipRows = days.filter((d) => d.skipped);
  const tradeN = (data.trades || []).length;
  const pnl = data.pnl_total ?? 0;
  const exposure = data.exposure_pnl_total ?? 0;
  const skipN = data.skip_count ?? skipRows.length;
  const longN = tradeDays.filter((d) => d.direction === "long_t").length;
  const revN = tradeDays.filter((d) => d.direction === "reverse_t").length;
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
    previewMetricChip("is-done", "正/反", `${longN}/${revN}`) +
    previewMetricChip(pnlCls, "PnL", Number.isFinite(pnlN) ? pnlN.toFixed(1) : pnl) +
    previewMetricChip(
      "is-idle",
      "敞口",
      Number.isFinite(Number(exposure)) ? Number(exposure).toFixed(1) : exposure
    ) +
    previewMetricChip("is-locked", "跳过", skipN) +
    `</span>`;
  const enter =
    rules.y_tau_enter != null && Number.isFinite(Number(rules.y_tau_enter))
      ? Number(rules.y_tau_enter)
      : 0.25;
  const tauMap = normalizeYTauMap(rules.y_tau_map);
  const tip = escapeHtml(yTauMapScoreTip(tauMap, enter));
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
          `<p class="quant-trades-caption">成交 ${tradeDays.length} · 跳过 ${skipRows.length}` +
          escapeHtml(skipHint) +
          ` · 正${longN}/反${revN}` +
          ` · <span title="${tip}">τ = y_τ</span></p>` +
          `</div>`,
        maxRows: Math.max(T0_TRADE_TABLE_MAX_ROWS, days.length),
        showReason: true,
        preserveOrder: true,
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
}

function workerLastRunToTableData(lastRun, execution) {
  const results = ((lastRun && lastRun.results) || []).filter(
    (r) => r && !r.skipped && (r.trades || []).length
  );
  const rules = (lastRun && lastRun.rules) || (execution && execution.t0) || {};
  const days = results.map((r) => ({
    ...r,
    direction: r.direction || r.direction_used,
  }));
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
export function renderPaperT0WorkerTrades(el, { t0Auto, execution } = {}) {
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
  const data = workerLastRunToTableData(lr, execution);
  const days = pickTradeDays(data);
  if (!days.length) {
    el.innerHTML = foldShell(
      `<span class="paper-t0-desk-fold-meta">尚无成交</span>`,
      `<p class="paper-t0-desk-empty">尚无成交明细 · 有买/卖落账后显示</p>`
    );
    return;
  }
  const enter =
    data.rules && data.rules.y_tau_enter != null && Number.isFinite(Number(data.rules.y_tau_enter))
      ? Number(data.rules.y_tau_enter)
      : 0.25;
  const tauMap = normalizeYTauMap(data.rules && data.rules.y_tau_map);
  const tag = data.workerTag || "—";
  const longN = days.filter((d) => d.direction === "long_t").length;
  const revN = days.filter((d) => d.direction === "reverse_t").length;
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
    (longN
      ? `<span class="paper-t0-desk-chip is-done"><span class="paper-t0-desk-chip-k">正</span>` +
        `<span class="paper-t0-desk-chip-v">${longN}</span></span>`
      : "") +
    (revN
      ? `<span class="paper-t0-desk-chip is-idle"><span class="paper-t0-desk-chip-k">反</span>` +
        `<span class="paper-t0-desk-chip-v">${revN}</span></span>`
      : "") +
    `</span>`;
  const tip = escapeHtml(yTauMapScoreTip(tauMap, enter));
  el.innerHTML = foldShell(
    chips,
    `<div class="paper-t0-desk-board">` +
      buildT0TradeTableHtml({
        data,
        days,
        maxRows: T0_TRADE_TABLE_MAX_ROWS,
        showDelete: true,
      }) +
      `<p class="paper-t0-desk-foot" title="${tip}">τ = y_τ · ${escapeHtml(
        String(tauMap || "scalp")
      )} · 删除将冲正账本</p>` +
      `</div>`
  );
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
  if (direction === "reverse_t") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-rev" title="反T">反T</span></td>`;
  }
  if (direction === "long_t") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-long" title="正T">正T</span></td>`;
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
  if (locked) {
    if (r.includes("横盘")) return { id: "y_tau_flat", label: "τ横盘" };
    if (r.includes("y_trade") || r.includes("幅度不足")) return { id: "y_trade_weak", label: "幅度" };
    if (r.includes("异号")) return { id: "trade_tau_sign", label: "异号" };
    if (r.includes("午盘后") || r.includes("无成交腿")) return { id: "deadline", label: "午盘截止" };
    if (r.includes("stance")) return { id: "stance", label: "stance" };
    if (r.includes("缺现金") || r.includes("动仓不足") || r.includes("无可卖"))
      return { id: "lot", label: "仓/钱" };
    return { id: "locked", label: "终锁" };
  }
  if (r.includes("振幅")) return { id: "amplitude", label: "振幅" };
  if (r.includes("未触及")) return { id: "trigger", label: "未触价" };
  if (r.includes("午休")) return { id: "lunch", label: "午休" };
  if (r.includes("解锁") || r.includes("重评")) return { id: "reeval", label: "待重评" };
  if (r.includes("算分") || r.includes("对齐") || r.includes("就绪"))
    return { id: "pending", label: "待就绪" };
  if (r.includes("已定方向") || r.includes("等待触价")) return { id: "armed", label: "待触价" };
  return { id: "watch", label: "监视" };
}

function resolveDeskNote(r) {
  if (r.locked) return r.reason || "当日终态锁死";
  if (r.reason) return r.reason;
  if (r.unlocked_from_skip) return "已解锁，等下一根 5m 重评";
  if (r.legs_written > 0) return `${r.legs_written} 腿已落账`;
  if (r.direction) return "已定方向，等待触价 / 下一根 5m";
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

  if (!desk.same_session || !desk.rows.length) {
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
      ? `<p class="paper-t0-desk-foot">终锁票当日不再重评门槛；振幅 / 未触价仍可随 5m 推进。删除后 Worker 可重新监视（不改账本）。</p>`
      : `<p class="paper-t0-desk-foot">≥11:30 无成交腿一律终锁；有腿票继续盯盘至收盘回补。删除仅去盘中状态。</p>`;

  el.innerHTML =
    `<details class="paper-t0-desk-fold"${openAttr}>` +
    `<summary class="paper-t0-desk-fold-sum">` +
    `<span class="paper-t0-desk-fold-title">今日盯盘状态</span>` +
    sessLine +
    `<span class="paper-t0-desk-chips">${chips}</span>` +
    `</summary>` +
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
}
