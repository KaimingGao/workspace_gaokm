/** Paper · 做T 指标与预演表渲染（从 paper.js 抽出）。 */

import { paperFmtPct, paperMetricClass } from "./fmt.js";

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
  const reasonBits = reasons
    .slice(0, 5)
    .map((r) => `${escapeHtml(r.reason)} ×${r.count}`)
    .join(" · ");
  let html =
    `<p class="quant-trades-caption">日线 ${escapeHtml(path)} 下多数日被跳过` +
    (data.skip_days != null ? `（合计 ${data.skip_days} 日）` : "") +
    (reasonBits ? `：${reasonBits}` : "") +
    `。成交表明细只列真实成交日。</p>`;
  if (skips.length) {
    html +=
      `<p class="quant-trades-caption">近期跳过样例</p>` +
      `<table class="quant-weight-table"><thead><tr>` +
      `<th>代码</th><th>日</th><th>原因</th></tr></thead><tbody>` +
      skips
        .slice(0, 10)
        .map(
          (d) =>
            `<tr><td>${escapeHtml(d.stock_code || "")}</td>` +
            `<td>${escapeHtml(d.date || "")}</td>` +
            `<td>${escapeHtml(d.reason || "跳过")}</td></tr>`
        )
        .join("") +
      `</tbody></table>`;
  }
  return html;
}

export function renderPaperT0(els, data) {
  const { metricsEl, daysEl } = els || {};
  if (!metricsEl) return;
  if (!data || !data.success) {
    metricsEl.innerHTML = "";
    if (daysEl) daysEl.innerHTML = "";
    return;
  }
  const opt = data.optimistic_compare || {};
  const deltaRatio = data.optimistic_delta_ratio_pct ?? opt.delta_pnl_ratio_pct;
  const items = [
    ["含敞口净 PnL", data.t0_pnl_with_exposure],
    ["完成往返率", paperFmtPct(data.cover_rate_pct)],
    ["日均 PnL", data.avg_pnl_per_trade_day],
    ["参与率", paperFmtPct(data.participate_rate_pct)],
    ["乐观Δ占比", paperFmtPct(deltaRatio)],
    ["相对底仓%", paperFmtPct(data.pnl_vs_hold_mv_pct)],
    ["做T天数", data.t0_trade_days],
    ["累计 PnL", data.t0_pnl_total],
    ["敞口 PnL", data.exposure_pnl_total],
    ["正T PnL", data.long_t_pnl],
    ["反T PnL", data.reverse_t_pnl],
    ["正T日", data.long_t_days ?? 0],
    ["反T日", data.reverse_t_days ?? 0],
    ["跳过日", data.skip_days],
    ["信号跳过", data.signal_skip_days],
    ["分钟路径日", data.minute_path_days],
    ["日线回退日", data.daily_fallback_days],
  ];
  metricsEl.innerHTML = items
    .map(
      ([label, val]) =>
        `<div class="quant-metric"><span class="label">${label}</span>` +
        `<span class="val ${paperMetricClass(val)}">${val ?? "—"}</span></div>`
    )
    .join("");
  const sample = data.trade_days_sample || data.days || [];
  const days = sample.filter(
    (d) =>
      Number(d.sold_qty) > 0 ||
      Number(d.bought_qty) > 0 ||
      Number(d.pnl) !== 0 ||
      Number(d.exposure_pnl) !== 0
  );
  if (!daysEl) return;
  const skipHtml = renderSkipContext(data);
  if (!days.length) {
    const skip = data.skip_days != null ? ` · 跳过 ${data.skip_days} 日` : "";
    const trades =
      data.t0_trade_days != null ? `指标成交日 ${data.t0_trade_days}` : "无成交样本";
    daysEl.innerHTML =
      `<p class="quant-trades-caption">${trades}${skip} · 明细为空（可 Alt+点选中行试 5m）</p>` +
      skipHtml;
    return;
  }
  const multi = days.some((d) => d.stock_code);
  const captionBits = [];
  captionBits.push(`成交 ${days.length} 行（指标做T日 ${data.t0_trade_days ?? "—"}）`);
  if (data.cover_rate_pct != null) {
    captionBits.push(`完成往返 ${data.cover_rate_pct}%`);
  }
  if (data.long_t_days != null || data.reverse_t_days != null) {
    captionBits.push(`正${data.long_t_days ?? 0}/反${data.reverse_t_days ?? 0}`);
  }
  if (days.length <= 3 && (data.skip_days || 0) > 0) {
    const path = (data.rules && data.rules.path_mode) || data.path_mode || "veto";
    captionBits.push(`日线 ${path} 偏严 · 多数日未成交`);
  }
  captionBits.push(`表内最多 ${Math.min(days.length, 20)} 笔`);
  const enter =
    data.rules && data.rules.dir_enter != null && Number.isFinite(Number(data.rules.dir_enter))
      ? Number(data.rules.dir_enter)
      : 0.35;
  const scoreTip =
    `开盘方向分 direction_score（约 -1～+1）：跳空/昨位/mom3/gap·ATR；≥+${enter} 正T，≤-${enter} 反T，|分|<${enter} 信号跳过。≠ 选股 predicted_score / ŷ。悬停或点击看详情。`;
  daysEl.innerHTML =
    `<p class="quant-trades-caption">${captionBits.join(" · ")}` +
    ` · <span title="${escapeHtml(scoreTip)}">「分」= 方向分，非 ŷ</span></p>` +
    `<table class="quant-weight-table"><thead><tr>` +
    (multi ? `<th>代码</th>` : "") +
    `<th>日</th><th>向</th>` +
    `<th title="${escapeHtml(scoreTip)}">分</th>` +
    `<th>卖/买</th><th>回补</th><th>PnL</th><th>敞口</th></tr></thead><tbody>` +
    days
      .slice(-20)
      .reverse()
      .map((d) => {
        const dir =
          d.direction === "reverse_t" ? "反" : d.direction === "long_t" ? "正" : "—";
        const score =
          d.direction_score != null && d.direction_score !== ""
            ? Number(d.direction_score).toFixed(2)
            : "—";
        const detail = JSON.stringify({
          kind: "t0_direction",
          direction_score: d.direction_score,
          direction_reason: d.direction_reason || "",
          direction: d.direction || "",
          features: d.direction_features || d.features || null,
          direction_features: d.direction_features || d.features || null,
          stock_code: d.stock_code || "",
          date: d.date || "",
          dir_enter: enter,
        });
        const flow =
          Number(d.bought_qty) > 0 ? `买${d.bought_qty}` : `卖${d.sold_qty ?? 0}`;
        const cover =
          Number(d.sold_back_qty) > 0 ? d.sold_back_qty : d.covered_qty ?? 0;
        return (
          `<tr>` +
          (multi ? `<td>${d.stock_code || ""}</td>` : "") +
          `<td>${d.date || ""}</td><td>${dir}</td>` +
          `<td class="num paper-t0-dir-score has-tip" data-score-detail="${escapeHtml(
            detail
          )}" title="悬停查看方向分详情（非 ŷ）">${score}</td>` +
          `<td class="num">${flow}</td>` +
          `<td class="num">${cover}</td>` +
          `<td class="num ${paperMetricClass(d.pnl)}">${d.pnl ?? 0}</td>` +
          `<td class="num ${paperMetricClass(d.exposure_pnl)}">${d.exposure_pnl ?? 0}</td></tr>`
        );
      })
      .join("") +
    `</tbody></table>` +
    (days.length <= 3 ? skipHtml : "");
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
    `<p class="quant-trades-caption">预演 · 成交 ${tradeN} 笔 · PnL ${data.pnl_total ?? 0} · ` +
    `敞口 ${data.exposure_pnl_total ?? 0} · 跳过 ${data.skip_count ?? 0}</p>` +
    `<table class="quant-weight-table"><thead><tr>` +
    `<th>代码</th><th>向</th><th>说明</th><th>PnL</th></tr></thead><tbody>` +
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
          `<tr><td>${r.stock_code || ""}</td><td>${dir}</td>` +
          `<td>${note}</td>` +
          `<td class="num ${paperMetricClass(r.pnl)}">${r.pnl ?? 0}</td></tr>`
        );
      })
      .join("") +
    `</tbody></table>`;
}
