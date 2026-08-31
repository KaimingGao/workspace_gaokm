/** 做 T 回测专业报告渲染（纸面 / 量化共用）。 */

import { escapeText, paperFmtPct, paperMetricClass } from "./fmt.js";
import { yTauMapShortLabel } from "./execution_ui.js";
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

/** 做 T 回测「累计收益%」指标卡 tooltip。 */
const CUMRET_DEF =
  "评估窗内做T净盈亏+敞口盈亏，÷ 期初虚拟底仓市值；多票为各票假仓合计，非账户真实收益率";

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
  const minute = Number(data.minute_path_days) || 0;
  const missing = Number(data.missing_minute_days) || 0;
  if (minute > 0) {
    return `5m 第一触达 · ${minute} 日` + (missing ? ` · 缺分钟跳过 ${missing}` : "");
  }
  if (missing > 0) return `缺分钟跳过 ${missing} 日`;
  return "5m 第一触达";
}

function resolveVerdict(data) {
  const net = Number(data.t0_pnl_with_exposure ?? data.t0_pnl_total);
  const trades = Number(data.t0_trade_days) || 0;
  const participate = Number(data.participate_rate_pct);
  const cover = Number(data.cover_rate_pct);
  const signalSkip = Number(data.signal_skip_rate_pct ?? data.viz?.summary?.signal_skip_rate_pct);
  const missing = Number(data.missing_minute_days) || 0;
  const minute = Number(data.minute_path_days) || 0;
  const evalDays = Number(data.eval_days) || trades + (Number(data.skip_days) || 0);

  if (missing > 0 && evalDays > 0 && missing / evalDays >= 0.4) {
    return {
      tone: "warn",
      label: "分钟不足",
      hint: `缺 5m ${missing}/${evalDays} 日；请先分钟预热（东财失败时仅本地缓存）。有缓存票会自动对齐分钟窗口`,
    };
  }
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
  if (trades <= 2 && missing >= Math.max(minute, 1)) {
    return {
      tone: "warn",
      label: "样本不足",
      hint: `仅 ${trades} 日成交且大量缺分钟，盈亏结论不可靠`,
    };
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
  const items = [];
  if (opt.t0_pnl_total != null) {
    items.push({
      label: "乐观上界",
      val: fmtMoney(opt.t0_pnl_total, { signed: true }),
      delta: opt.delta_pnl,
      cls: paperMetricClass(opt.t0_pnl_total),
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
    {
      label: "累计收益%",
      value: paperFmtPct(data.cumulative_return_pct ?? data.pnl_vs_hold_mv_pct),
    },
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
      value: `${fmtMoney(data.buy_then_sell_pnl, { signed: true })} / ${fmtMoney(data.sell_then_buy_pnl, { signed: true })}`,
    },
    {
      label: "正/反日",
      value: `${data.buy_then_sell_days ?? 0} / ${data.sell_then_buy_days ?? 0}`,
    },
    {
      label: "缺分钟跳过",
      value: String(data.missing_minute_days ?? 0),
    },
  ];
}

export function buildT0SummaryLine(data) {
  if (!data || !data.success) return "";
  const scope = resolveT0ScopeLabel(data);
  const net = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
  const ret =
    data.cumulative_return_pct != null
      ? data.cumulative_return_pct
      : data.pnl_vs_hold_mv_pct;
  const verdict = resolveVerdict(data);
  const bits = [
    scope,
    ret != null ? `累计收益 ${fmtPct(ret, 3)}` : `净 PnL ${fmtMoney(net, { signed: true })}`,
    `做T ${data.t0_trade_days ?? "—"} 日`,
    data.cover_rate_pct != null ? `往返 ${fmtPct(data.cover_rate_pct)}` : null,
    data.participate_rate_pct != null ? `参与 ${fmtPct(data.participate_rate_pct)}` : null,
    data.win_rate_pct != null || data.t0_win_rate_pct != null
      ? `胜率 ${fmtPct(data.win_rate_pct ?? data.t0_win_rate_pct)}`
      : null,
    pathLabel(data),
    yTauMapShortLabel(data.rules && data.rules.y_tau_map),
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
  const cumRet =
    data.cumulative_return_pct != null
      ? Number(data.cumulative_return_pct)
      : data.pnl_vs_hold_mv_pct != null
        ? Number(data.pnl_vs_hold_mv_pct)
        : null;
  const heroPrimary =
    cumRet != null && Number.isFinite(cumRet)
      ? fmtPct(cumRet, 3)
      : fmtMoney(net, { signed: true });
  const heroPrimaryCls = paperMetricClass(cumRet != null ? cumRet : net);
  const sm = data.viz?.summary || {};
  const scoreCov = sm.score_coverage_pct;
  const evalDays = data.eval_days ?? (Number(data.t0_trade_days || 0) + Number(data.skip_days || 0));
  const rules = data.rules || {};
  const skipInsight = topSkipInsight(data);
  const virtNote = data.virtual_sizing
    ? `虚拟仓每票 ${Number(data.virtual_shares || 10000).toLocaleString("zh-CN")} 股` +
      ` · 现金 ${(Number(data.virtual_cash || 2e6) / 10000).toFixed(0)} 万`
    : null;
  const hero =
    `<header class="paper-t0-report-hero">` +
    `<div class="paper-t0-report-hero-main">` +
    `<div class="paper-t0-report-scope">${escapeText(scope)}</div>` +
    `<div class="paper-t0-report-net ${heroPrimaryCls}">${escapeText(heroPrimary)}</div>` +
    `<div class="paper-t0-report-sub">` +
    (cumRet != null ? "累计收益比例" : "含敞口净 PnL") +
    ` · 评估 ${evalDays || "—"} 日 · ${escapeText(pathLabel(data))}` +
    (virtNote ? ` · ${escapeText(virtNote)}` : "") +
    `</div>` +
    `</div>` +
    `<div class="paper-t0-report-verdict is-${verdict.tone}" title="${escapeText(verdict.hint)}">` +
    `<span class="paper-t0-report-verdict-label">${escapeText(verdict.label)}</span>` +
    `<span class="paper-t0-report-verdict-hint">${escapeText(verdict.hint)}</span>` +
    `</div>` +
    `</header>`;

  const pnlSection = metricSection("收益", [
    metricCell("累计收益%", fmtPct(cumRet, 3), {
      cls: paperMetricClass(cumRet),
      tip: CUMRET_DEF,
    }),
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
    metricCell(
      "正T / 反T",
      `${fmtMoney(data.buy_then_sell_pnl, { signed: true })} / ${fmtMoney(data.sell_then_buy_pnl, { signed: true })}`,
      { tip: `成交日 ${data.buy_then_sell_days ?? 0} 正 · ${data.sell_then_buy_days ?? 0} 反` }
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
      tip: "dual_y 门槛 / y_check / y_trade 不足等",
    }),
    metricCell(
      "τ 门槛",
      (() => {
        const enter =
          rules.y_tau_enter != null
            ? Number(rules.y_tau_enter)
            : sm.y_tau_enter != null
              ? Number(sm.y_tau_enter)
              : 0.6;
        return `|y_τ|≥${enter}%`;
      })(),
      { tip: "|y_τ| 低于入场则横盘跳过" }
    ),
    metricCell("跳过日", String(data.skip_days ?? 0), {
      tip: skipInsight
        ? `主因 ${skipInsight}（悬停归因区「跳过构成」看全部分类 tip）`
        : "无跳过分类",
    }),
    metricCell("信号跳过", String(data.signal_skip_days ?? 0)),
    metricCell("τ OC 命中", fmtPct(sm.tau_oc_hit_rate_pct ?? data.tau_oc_hit_rate_pct), {
      tip: "成交日 y_τ 符号 vs 实际 open→close",
    }),
    metricCell(
      "|τ|≥0.6 同号",
      (() => {
        const att = data.y_tau_attribution || sm.y_tau_attribution || {};
        const buckets = (att.summary && att.summary.abs_buckets) || {};
        const b = buckets.abs_ge_0_6 || {};
        if (b.sign_hit == null) return "—";
        const pct = Number(b.sign_hit) * 100;
        return `${pct.toFixed(1)}%${b.n != null ? ` · n=${b.n}` : ""}`;
      })(),
      { tip: "成交日 |ŷ_τ|≥0.6 桶同号率（验收）" }
    ),
    metricCell(
      "升级验收",
      (() => {
        const acc = data.upgrade_acceptance || sm.upgrade_acceptance || {};
        if (acc.ok === true) return "通过";
        if (acc.ok === false) return `未过 · ${(acc.blockers || []).join("；") || "—"}`;
        return "—";
      })(),
      {
        tip: "无弱τ/横盘成交；对照 path 横盘/否决笔",
      }
    ),
    metricCell("path 一致", fmtPct(sm.path_agree_rate_pct ?? data.path_agree_rate_pct), {
      tip: "成交日 y_path 符号与 τ 方向一致率",
    }),
  ]);

  const portrait =
    data.score_portrait ||
    sm.score_portrait ||
    (data.viz && data.viz.score_portrait) ||
    {};
  const fmtShare = (pack) => {
    if (!pack || pack.pos_share == null || !Number.isFinite(Number(pack.pos_share))) return "—";
    const p = Math.round(Number(pack.pos_share) * 1000) / 10;
    return `+${pack.pos ?? 0}/−${pack.neg ?? 0} · ${p}%+`;
  };
  const fmtHit = (pack) => {
    if (!pack || pack.hit_rate_pct == null) return "—";
    const n = pack.n_judged != null ? ` · n=${pack.n_judged}` : "";
    return `${Number(pack.hit_rate_pct).toFixed(1)}%${n}`;
  };
  const fmtRatePct = (rate, n) => {
    if (rate == null || !Number.isFinite(Number(rate))) return "—";
    const suffix = n != null ? ` · n=${n}` : "";
    return `${(Number(rate) * 100).toFixed(1)}%${suffix}`;
  };
  const pathByPred = portrait.path_by_pred_sign || {};
  const portraitSection = metricSection("回测样本画像", [
    metricCell(
      "样本日",
      (() => {
        const nAll = portrait.n_days ?? portrait.n_traded;
        const nTr = portrait.n_traded ?? "—";
        const nSk = portrait.n_skipped;
        if (nAll == null) return "—";
        const sk = nSk != null ? ` · 跳过${nSk}` : "";
        return `${nAll}（成交${nTr}${sk}）`;
      })(),
      { tip: "全部回测日（含跳过）；主指标按全样本；括号为成交子集" }
    ),
    metricCell("τ标签", fmtShare(portrait.label_tau), {
      tip: "全样本 tau_realized（open→close）正/负数量",
    }),
    metricCell("path标签", fmtShare(portrait.label_path), {
      tip: "全样本 path_realized（极值序）正/负数量",
    }),
    metricCell(
      "标签同号",
      fmtRatePct(
        portrait.label_joint && portrait.label_joint.same_sign_rate,
        portrait.label_joint && portrait.label_joint.signed_n
      ),
      { tip: "全样本真实 τ OC 与 path 标签同号率（双侧非零）" }
    ),
    metricCell(
      "ŷ同号",
      fmtRatePct(
        portrait.pred_joint && portrait.pred_joint.same_sign_rate,
        portrait.pred_joint && portrait.pred_joint.signed_n
      ),
      { tip: "全样本 ŷ_τ 与 ŷ_path 同号率（双侧非零）" }
    ),
    metricCell("τ预估命中", fmtHit(portrait.tau_hit), {
      tip: "全样本 ŷ_τ 符号 vs tau_realized（|·|<0.05% 不计）",
    }),
    metricCell("path预估命中", fmtHit(portrait.path_hit), {
      tip: "全样本 ŷ_path 符号 vs path_realized（真实=0 不计）",
    }),
    metricCell(
      "成交τ命中",
      fmtHit(portrait.traded && portrait.traded.tau_hit),
      { tip: "仅成交日 τ 预估命中（对照）" }
    ),
    metricCell(
      "成交path命中",
      fmtHit(portrait.traded && portrait.traded.path_hit),
      { tip: "仅成交日 path 预估命中（对照）" }
    ),
    metricCell(
      "path+命中",
      (() => {
        const b = pathByPred.pred_pos || {};
        if (b.hit_rate == null) return "—";
        return `${(Number(b.hit_rate) * 100).toFixed(0)}% · n=${(b.hit || 0) + (b.miss || 0)}`;
      })(),
      { tip: "全样本 ŷ_path>0 时的方向命中" }
    ),
    metricCell(
      "path−错误率",
      (() => {
        const b = pathByPred.pred_neg || {};
        if (b.err_rate == null) return "—";
        return `${(Number(b.err_rate) * 100).toFixed(0)}% · n=${(b.hit || 0) + (b.miss || 0)}`;
      })(),
      { tip: "全样本 ŷ_path<0 时的方向错误率" }
    ),
  ]);

  const pathRules = rules.y_use_path != null ? rules : sm;
  const pathSection = metricSection("路径与对照", [
    metricCell("y_path选向", pathRules.y_use_path === false ? "关" : "开"),
    metricCell(
      "path门槛",
      pathRules.y_path_enter != null ? `|y_p|>${pathRules.y_path_enter}` : ">0.02"
    ),
    metricCell("path跳过", String(sm.path_skip_days ?? data.path_skip_days ?? 0)),
    metricCell("分钟路径日", String(data.minute_path_days ?? 0)),
    metricCell("缺分钟跳过", String(data.missing_minute_days ?? 0), {
      tip:
        Number(data.missing_minute_days) > 0
          ? "无 5m 覆盖的交易日已跳过；缺缓存票请先分钟预热，勿据此判策略盈亏"
          : "无 5m 覆盖的交易日；已删除日线模拟",
    }),
    metricCell(
      "乐观Δ占比",
      fmtPct(data.optimistic_delta_ratio_pct ?? data.optimistic_compare?.delta_pnl_ratio_pct),
      { tip: "相对 trigger 的乐观上界空间；成交日过少或比值爆炸时不展示" }
    ),
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
    portraitSection +
    pathSection +
    `</div>` +
    compareStrip(data) +
    `</div>`
  );
}
