/** 做 T 回测专业报告渲染（纸面 / 量化共用）。 */

import { escapeText, paperFmtPct, paperMetricClass } from "./fmt.js";
import { SKIP_CAT_LABEL } from "./t0_table.js";

function fmtMoney(v, { signed = false, digits = 0 } = {}) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const sign = signed && n > 0 ? "+" : "";
  return (
    sign +
    n.toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
  );
}

function fmtPct(v, digits = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function resolveT0ScopeLabel(data) {
  if (!data) return "—";
  if (data.scope_label) return String(data.scope_label);
  if (data.from_holdings && Number(data.ok_count) > 1) {
    return `持仓 ${data.ok_count} 只`;
  }
  const name = data.stock_name || data.stock_code;
  return name ? String(name) : "持仓";
}

function pathLabel(data) {
  const rules = data.rules || {};
  const path = rules.path_mode || data.path_mode || (data.use_minute ? "first_touch" : "veto");
  const minute = Number(data.minute_path_days) || 0;
  const fallback = Number(data.daily_fallback_days) || 0;
  if (minute > 0) {
    return `5m 第一触达 · ${minute} 日` + (fallback ? ` · 日线回退 ${fallback}` : "");
  }
  return `日线 ${path}`;
}

function resolveVerdict(data) {
  const net = Number(data.t0_pnl_with_exposure ?? data.t0_pnl_total);
  const trades = Number(data.t0_trade_days) || 0;
  const participate = Number(data.participate_rate_pct);
  const cover = Number(data.cover_rate_pct);
  const signalSkip = Number(data.signal_skip_rate_pct ?? data.viz?.summary?.signal_skip_rate_pct);

  if (!trades) {
    if (Number(data.skip_days) > 0) {
      return {
        tone: "warn",
        label: "未成交",
        hint: "样本内无有效做 T 成交，优先检查 ŷ 覆盖与触发阈值",
      };
    }
    return { tone: "neutral", label: "无样本", hint: "区间内无可评估交易日" };
  }
  if (Number.isFinite(net) && net > 0 && Number.isFinite(cover) && cover >= 80) {
    return { tone: "pos", label: "有效", hint: "净收益为正且往返完成率良好" };
  }
  if (Number.isFinite(net) && net > 0) {
    return { tone: "pos", label: "盈利", hint: "有正收益，关注未完成往返与敞口" };
  }
  if (Number.isFinite(participate) && participate < 5 && Number.isFinite(signalSkip) && signalSkip > 50) {
    return {
      tone: "warn",
      label: "信号稀疏",
      hint: "参与率低且信号跳过占比高，建议检查 dual_y 门槛与 ŷ 快照",
    };
  }
  if (Number.isFinite(net) && net < 0) {
    return { tone: "neg", label: "亏损", hint: "净收益为负，建议对照乐观上界与跳过构成" };
  }
  return { tone: "neutral", label: "观察", hint: "样本偏少，结论仅供参考" };
}

function topSkipInsight(data) {
  const cats = data.viz?.skip_categories || [];
  if (cats.length) {
    const top = cats[0];
    return `${top.label || SKIP_CAT_LABEL[top.id] || top.id} ${top.pct ?? 0}%`;
  }
  const reasons = data.skip_reason_top || [];
  if (reasons.length) return `${reasons[0].reason} ×${reasons[0].count}`;
  return null;
}

function metricCell(label, value, { cls = "", tip = "", hero = false } = {}) {
  const tipAttr = tip ? ` title="${escapeText(tip)}"` : "";
  return (
    `<div class="paper-t0-metric${hero ? " is-hero" : ""}"${tipAttr}>` +
    `<span class="paper-t0-metric-label">${escapeText(label)}</span>` +
    `<span class="paper-t0-metric-val ${cls}">${value ?? "—"}</span>` +
    `</div>`
  );
}

function metricSection(title, cells) {
  if (!cells.length) return "";
  return (
    `<section class="paper-t0-metric-section">` +
    `<h4 class="paper-t0-metric-section-title">${escapeText(title)}</h4>` +
    `<div class="paper-t0-metric-grid">${cells.join("")}</div>` +
    `</section>`
  );
}

function compareStrip(data) {
  const opt = data.optimistic_compare || {};
  const daily = data.daily_compare || {};
  const items = [];
  if (opt.t0_pnl_total != null) {
    items.push({
      label: "乐观上界",
      val: fmtMoney(opt.t0_pnl_total, { signed: true }),
      delta: opt.delta_pnl,
      cls: paperMetricClass(opt.t0_pnl_total),
    });
  }
  if (daily.t0_pnl_total != null) {
    items.push({
      label: "日线 veto",
      val: fmtMoney(daily.t0_pnl_total, { signed: true }),
      delta: daily.delta_pnl,
      cls: paperMetricClass(daily.t0_pnl_total),
    });
  }
  if (!items.length) return "";
  return (
    `<div class="paper-t0-compare-strip">` +
    items
      .map(
        (it) =>
          `<div class="paper-t0-compare-item">` +
          `<span class="paper-t0-compare-label">${escapeText(it.label)}</span>` +
          `<span class="paper-t0-compare-val ${it.cls}">${it.val}</span>` +
          (it.delta != null
            ? `<span class="paper-t0-compare-delta ${paperMetricClass(it.delta)}">Δ ${fmtMoney(
                it.delta,
                { signed: true }
              )}</span>`
            : "") +
          `</div>`
      )
      .join("") +
    `</div>`
  );
}

/** 量化页 metric cards 格式 */
export function buildT0MetricCards(data) {
  if (!data || !data.success) return [];
  const net = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
  const opt = data.optimistic_compare || {};
  const deltaRatio = data.optimistic_delta_ratio_pct ?? opt.delta_pnl_ratio_pct;
  const sm = data.viz?.summary || {};
  const scoreCov = sm.score_coverage_pct ?? data.score_coverage_pct;
  return [
    {
      label: "含敞口净 PnL",
      value: fmtMoney(net, { signed: true }),
      cls: paperMetricClass(net),
    },
    { label: "完成往返率", value: paperFmtPct(data.cover_rate_pct) },
    {
      label: "日均 PnL",
      value: fmtMoney(data.avg_pnl_per_trade_day, { signed: true }),
      cls: paperMetricClass(data.avg_pnl_per_trade_day),
    },
    { label: "参与率", value: paperFmtPct(data.participate_rate_pct) },
    { label: "胜率", value: paperFmtPct(data.win_rate_pct ?? data.t0_win_rate_pct) },
    {
      label: "盈亏比",
      value:
        data.profit_factor != null && Number.isFinite(Number(data.profit_factor))
          ? Number(data.profit_factor).toFixed(2)
          : "—",
    },
    { label: "ŷ 覆盖", value: paperFmtPct(scoreCov) },
    {
      label: "信号跳过率",
      value: paperFmtPct(data.signal_skip_rate_pct ?? sm.signal_skip_rate_pct),
    },
    { label: "乐观Δ占比", value: paperFmtPct(deltaRatio) },
    { label: "相对底仓", value: paperFmtPct(data.pnl_vs_hold_mv_pct) },
    { label: "做T日", value: String(data.t0_trade_days ?? "—") },
    {
      label: "交易 PnL",
      value: fmtMoney(data.t0_pnl_total, { signed: true }),
      cls: paperMetricClass(data.t0_pnl_total),
    },
    {
      label: "敞口 PnL",
      value: fmtMoney(data.exposure_pnl_total, { signed: true }),
      cls: paperMetricClass(data.exposure_pnl_total),
    },
    {
      label: "正/反 PnL",
      value: `${fmtMoney(data.long_t_pnl, { signed: true })} / ${fmtMoney(data.reverse_t_pnl, { signed: true })}`,
    },
    {
      label: "正/反日",
      value: `${data.long_t_days ?? 0} / ${data.reverse_t_days ?? 0}`,
    },
    {
      label: "相对日线Δ",
      value: fmtMoney(data.daily_compare?.delta_pnl, { signed: true }),
      cls: paperMetricClass(data.daily_compare?.delta_pnl),
    },
  ];
}

export function buildT0SummaryLine(data) {
  if (!data || !data.success) return "";
  const scope = resolveT0ScopeLabel(data);
  const net = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
  const verdict = resolveVerdict(data);
  const bits = [
    scope,
    `净 PnL ${fmtMoney(net, { signed: true })}`,
    `做T ${data.t0_trade_days ?? "—"} 日`,
    data.cover_rate_pct != null ? `往返 ${fmtPct(data.cover_rate_pct)}` : null,
    data.participate_rate_pct != null ? `参与 ${fmtPct(data.participate_rate_pct)}` : null,
    data.win_rate_pct != null || data.t0_win_rate_pct != null
      ? `胜率 ${fmtPct(data.win_rate_pct ?? data.t0_win_rate_pct)}`
      : null,
    pathLabel(data),
    "dual_y",
    verdict.label,
    "非实盘",
  ].filter(Boolean);
  return bits.join(" · ");
}

/**
 * 纸面页专业报告 HTML
 * @param {object} data
 * @param {{ zeroHint?: string }} [opts]
 */
export function buildT0ReportHtml(data, opts = {}) {
  if (!data || !data.success) return "";
  const scope = resolveT0ScopeLabel(data);
  const verdict = resolveVerdict(data);
  const net = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
  const sm = data.viz?.summary || {};
  const scoreCov = sm.score_coverage_pct;
  const evalDays = data.eval_days ?? (Number(data.t0_trade_days || 0) + Number(data.skip_days || 0));
  const rules = data.rules || {};
  const skipInsight = topSkipInsight(data);

  const hero =
    `<header class="paper-t0-report-hero">` +
    `<div class="paper-t0-report-hero-main">` +
    `<div class="paper-t0-report-scope">${escapeText(scope)}</div>` +
    `<div class="paper-t0-report-net ${paperMetricClass(net)}">${fmtMoney(net, { signed: true })}</div>` +
    `<div class="paper-t0-report-sub">含敞口净 PnL · 评估 ${evalDays || "—"} 日 · ${escapeText(pathLabel(data))}</div>` +
    `</div>` +
    `<div class="paper-t0-report-verdict is-${verdict.tone}" title="${escapeText(verdict.hint)}">` +
    `<span class="paper-t0-report-verdict-label">${escapeText(verdict.label)}</span>` +
    `<span class="paper-t0-report-verdict-hint">${escapeText(verdict.hint)}</span>` +
    `</div>` +
    `</header>`;

  const pnlSection = metricSection("收益", [
    metricCell("交易 PnL", fmtMoney(data.t0_pnl_total, { signed: true }), {
      cls: paperMetricClass(data.t0_pnl_total),
      tip: "不含隔夜敞口的日内做 T 盈亏",
    }),
    metricCell("敞口 PnL", fmtMoney(data.exposure_pnl_total, { signed: true }), {
      cls: paperMetricClass(data.exposure_pnl_total),
      tip: "未完成回补或底仓变动带来的敞口损益",
    }),
    metricCell("日均 PnL", fmtMoney(data.avg_pnl_per_trade_day, { signed: true }), {
      cls: paperMetricClass(data.avg_pnl_per_trade_day),
    }),
    metricCell("相对底仓", fmtPct(data.pnl_vs_hold_mv_pct, 3), {
      cls: paperMetricClass(data.pnl_vs_hold_mv_pct),
      tip: "交易 PnL / 期初底仓市值",
    }),
    metricCell(
      "正T / 反T",
      `${fmtMoney(data.long_t_pnl, { signed: true })} / ${fmtMoney(data.reverse_t_pnl, { signed: true })}`,
      { tip: `成交日 ${data.long_t_days ?? 0} 正 · ${data.reverse_t_days ?? 0} 反` }
    ),
  ]);

  const execSection = metricSection("执行质量", [
    metricCell("完成往返率", fmtPct(data.cover_rate_pct), {
      tip: "卖出回补或买入卖回完成比例",
    }),
    metricCell("参与率", fmtPct(data.participate_rate_pct), {
      tip: "做 T 成交日 / 可评估日",
    }),
    metricCell("胜率", fmtPct(data.win_rate_pct ?? data.t0_win_rate_pct), {
      tip:
        data.t0_win_days != null
          ? `盈利 ${data.t0_win_days} 日 · 亏损 ${data.t0_loss_days ?? 0} 日`
          : "按成交日 PnL>0 统计",
    }),
    metricCell("盈亏比", data.profit_factor != null ? Number(data.profit_factor).toFixed(2) : "—", {
      tip: "总盈利 / 总亏损（绝对值）",
    }),
    metricCell("未完成往返", String(data.uncover_days ?? 0), {
      tip: data.uncover_rate_pct != null ? `占比 ${fmtPct(data.uncover_rate_pct)}` : "",
    }),
  ]);

  const signalSection = metricSection("信号与覆盖", [
    metricCell("ŷ 覆盖", fmtPct(scoreCov), { tip: "有 y_τ 快照的评估日占比" }),
    metricCell("信号跳过率", fmtPct(data.signal_skip_rate_pct ?? sm.signal_skip_rate_pct), {
      tip: "dual_y 门槛 / 冲突 / y_trade 不足等",
    }),
    metricCell(
      "τ 门槛",
      rules.y_tau_enter != null
        ? `±${rules.y_tau_enter}`
        : sm.y_tau_enter != null
          ? `±${sm.y_tau_enter}`
          : "±0.25"
    ),
    metricCell("跳过日", String(data.skip_days ?? 0), {
      tip: skipInsight ? `主因 ${skipInsight}` : "",
    }),
    metricCell("信号跳过", String(data.signal_skip_days ?? 0)),
  ]);

  const pathSection = metricSection("路径与对照", [
    metricCell("分钟路径日", String(data.minute_path_days ?? 0)),
    metricCell("日线回退", String(data.daily_fallback_days ?? 0)),
    metricCell(
      "乐观Δ占比",
      fmtPct(data.optimistic_delta_ratio_pct ?? data.optimistic_compare?.delta_pnl_ratio_pct),
      { tip: "相对乐观上界的额外空间占比" }
    ),
    metricCell("相对日线Δ", fmtMoney(data.daily_compare?.delta_pnl, { signed: true }), {
      cls: paperMetricClass(data.daily_compare?.delta_pnl),
      tip: "分钟路径 vs 纯日线 veto",
    }),
  ]);

  const zeroHint = opts.zeroHint
    ? `<p class="quant-trades-caption paper-t0-zero-hint">${opts.zeroHint}</p>`
    : "";

  return (
    `<div class="paper-t0-report">` +
    hero +
    zeroHint +
    `<div class="paper-t0-report-sections">` +
    pnlSection +
    execSection +
    signalSection +
    pathSection +
    `</div>` +
    compareStrip(data) +
    `</div>`
  );
}
