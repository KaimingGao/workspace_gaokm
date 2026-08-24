/** Paper · 做T 指标与预演表渲染（从 paper.js 抽出）。 */

import { paperMetricClass } from "./fmt.js";
import { renderT0Viz } from "./t0_viz.js";
import { buildT0ReportHtml } from "./t0_report.js";
import {
  buildT0SkipTableHtml,
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

function renderSkipContext(data) {
  const reasons = data.skip_reason_top || [];
  const skips = data.skip_days_sample || [];
  if (!reasons.length && !skips.length) return "";
  const path = (data.rules && data.rules.path_mode) || data.path_mode || "veto";
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };
  const reasonBits = reasons
    .slice(0, 5)
    .map((r) => `${escapeHtml(r.reason)} ×${r.count}`)
    .join(" · ");
  let html =
    `<details class="paper-t0-skip-fold">` +
    `<summary class="quant-trades-caption">跳过诊断 · 日线 ${escapeHtml(path)}` +
    (data.skip_days != null ? ` · 合计 ${data.skip_days} 日` : "") +
    (reasonBits ? ` · ${reasonBits}` : "") +
    `</summary>`;
  if (skips.length) {
    html += buildT0SkipTableHtml(skips, fallback);
  }
  html += `</details>`;
  return html;
}

function buildZeroTradeHint(data) {
  const reasons = data.skip_reason_top || [];
  const top = reasons[0];
  const ampSkip = reasons.some((r) => String(r.reason || "").includes("振幅"));
  const lotSkip = reasons.some((r) => String(r.reason || "").includes("不足1手"));
  const dir = (data.rules && data.rules.direction) || data.direction || "—";
  const sell = (data.rules && data.rules.sell_trigger_pct) ?? "2";
  const buy = (data.rules && data.rules.buy_trigger_pct) ?? "1.5";
  const tips = ampSkip
    ? "振幅门禁偏严；可调低振幅下限或关闭 ATR 自适应"
    : lotSkip
      ? "仓位×做T比例不足 1 手；可提高做T比例或加仓"
      : `未触及卖 +${sell}% / 买 -${buy}% 触发；可降阈值或启用 5 分钟路径`;
  const mm = data.minute_meta || {};
  const minuteHint =
    data.use_minute === false && (mm.error || (data.minute_path_days || 0) === 0)
      ? ` · 5m${mm.error ? `失败：${escapeHtml(String(mm.error).slice(0, 60))}` : "无覆盖"}，已回退日线`
      : "";
  return (
    `未成交${data.skip_days != null ? `（${data.skip_days} 日跳过）` : ""}` +
    (top ? ` · 主因 ${escapeHtml(top.reason)}×${top.count}` : "") +
    ` · ${escapeHtml(String(dir))} · ${escapeHtml(tips)}${minuteHint}`
  );
}

export function renderPaperT0(els, data) {
  const { metricsEl, daysEl, vizEl, previewEl } = els || {};
  if (!metricsEl) return;
  if (!data || !data.success) {
    metricsEl.innerHTML = "";
    if (daysEl) daysEl.innerHTML = "";
    renderT0Viz(vizEl, null);
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
  const skipHtml = renderSkipContext(data);
  if (!days.length) {
    const skip = data.skip_days != null ? ` · 跳过 ${data.skip_days} 日` : "";
    const trades =
      data.t0_trade_days != null ? `指标成交日 ${data.t0_trade_days}` : "无成交样本";
    daysEl.innerHTML =
      `<div class="paper-t0-days-head">` +
      `<h4 class="paper-t0-days-title">成交明细</h4>` +
      `<p class="quant-trades-caption">${trades}${skip} · 样本为空</p>` +
      `</div>` +
      skipHtml;
    renderT0Viz(vizEl, data);
    return;
  }

  const enter =
    data.rules && data.rules.y_tau_enter != null && Number.isFinite(Number(data.rules.y_tau_enter))
      ? Number(data.rules.y_tau_enter)
      : 0.25;
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
    ` · <span title="dual_y：τ≥+${enter} 正T，τ≤-${enter} 反T">τ = y_τ</span></p>` +
    `</div>`;

  daysEl.innerHTML =
    caption + buildT0TradeTableHtml({ data, days, maxRows: T0_TRADE_TABLE_MAX_ROWS }) + skipHtml;
  renderT0Viz(vizEl, data);
}

export function renderPaperT0Preview(els, data) {
  const { previewEl, confirmEl } = els || {};
  if (!previewEl) return;
  if (!data || !data.success) {
    previewEl.hidden = true;
    previewEl.innerHTML = "";
    if (confirmEl) confirmEl.hidden = true;
    return;
  }
  const rows = data.results || [];
  const tradeN = (data.trades || []).length;
  previewEl.hidden = false;
  if (confirmEl) confirmEl.hidden = tradeN === 0 && !(data.pnl_total > 0);
  previewEl.innerHTML =
    `<p class="quant-trades-caption">今日预演 · 成交 ${tradeN} 笔 · PnL ${data.pnl_total ?? 0} · ` +
    `敞口 ${data.exposure_pnl_total ?? 0} · 跳过 ${data.skip_count ?? 0}</p>` +
    `<table class="quant-weight-table paper-t0-table paper-t0-preview-table">` +
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-dir" />` +
    `<col class="paper-t0-col-reason" />` +
    `<col class="paper-t0-col-pnl" />` +
    `</colgroup>` +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">股票</th>` +
    `<th scope="col" class="paper-t0-col-dir">向</th>` +
    `<th scope="col" class="paper-t0-col-reason">说明</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `</tr></thead><tbody>` +
    rows
      .map((r) => {
        const dir =
          r.direction_used === "reverse_t"
            ? "反"
            : r.direction_used === "long_t"
              ? "正"
              : "—";
        const note = r.skipped
          ? r.reason || "跳过"
          : r.error || `${(r.trades || []).length} 笔`;
        return (
          `<tr>${stockCellHtml(r)}` +
          `<td class="paper-t0-col-dir">${dir}</td>` +
          `<td class="paper-t0-col-reason">${escapeHtml(note)}</td>` +
          `<td class="num paper-t0-col-pnl ${paperMetricClass(r.pnl)}">${r.pnl ?? 0}</td></tr>`
        );
      })
      .join("") +
    `</tbody></table>`;
}
