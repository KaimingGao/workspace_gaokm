/** 做 T 回测专业报告渲染（纸面 / 量化共用）。 */

import { escapeText, paperFmtPct, paperMetricClass } from "./fmt.js";

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

export function fmtT0DirDays(data) {
  const b = Number(data?.buy_then_sell_days) || 0;
  const s = Number(data?.sell_then_buy_days) || 0;
  const m = Number(data?.mixed_days) || 0;
  return m > 0 ? `${b} / ${s} / ${m}` : `${b} / ${s}`;
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
    return { tone: "neg", label: "亏损", hint: "净收益为负，建议对照跳过构成与成交明细" };
  }
  return { tone: "neutral", label: "观察", hint: "样本偏少，结论仅供参考" };
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

function metricSection(title, cells, extraClass = "", footerHtml = "") {
  if (!cells.length) return "";
  const cls = extraClass
    ? `paper-t0-metric-section ${extraClass}`
    : "paper-t0-metric-section";
  return (
    `<section class="${cls}">` +
    `<h4 class="paper-t0-metric-section-title">${escapeText(title)}</h4>` +
    `<div class="paper-t0-metric-grid">${cells.join("")}</div>` +
    (footerHtml || "") +
    `</section>`
  );
}

/** 量化页 metric cards 格式 */
export function buildT0MetricCards(data) {
  if (!data || !data.success) return [];
  const net = data.t0_pnl_with_exposure ?? data.t0_pnl_total;
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
      value: fmtT0DirDays(data),
    },
    {
      label: "缺分钟跳过",
      value: String(data.missing_minute_days ?? 0),
    },
  ];
}

function _hitTone(pct) {
  if (pct == null || !Number.isFinite(Number(pct))) return "";
  const p = Number(pct);
  if (p >= 70) return "is-strong";
  if (p >= 55) return "is-ok";
  if (p >= 45) return "is-soft";
  return "is-weak";
}

function _fillBarHtml(traded, total) {
  const n = Number(total) || 0;
  const t = Number(traded) || 0;
  if (n <= 0) return "";
  const pct = Math.min(100, Math.max(0, (t / n) * 100));
  return (
    `<div class="paper-t0-portrait-fillbar" title="成交 ${t} / 样本 ${n}" aria-hidden="true">` +
    `<span style="width:${pct.toFixed(2)}%"></span>` +
    `</div>`
  );
}

function _coverBarHtml(have, total) {
  const n = Number(total) || 0;
  const h = Number(have) || 0;
  if (n <= 0) return "";
  const pct = Math.min(100, Math.max(0, (h / n) * 100));
  const low = h > 0 && h / n < 0.25 ? " is-low" : "";
  return (
    `<div class="paper-t0-portrait-coverbar${low}" title="有ŷ ${h} / 样本 ${n}" aria-hidden="true">` +
    `<span style="width:${pct.toFixed(2)}%"></span>` +
    `</div>`
  );
}

/** 回测样本画像：样本日摘要 + 分槽位 */
function buildPortraitSectionHtml(portrait) {
  if (!portrait || typeof portrait !== "object") return "";

  const nAll = Number(portrait.n_days ?? portrait.n_traded);
  const nTr = Number(portrait.n_traded) || 0;
  const nSk =
    portrait.n_skipped != null
      ? Number(portrait.n_skipped)
      : Number.isFinite(nAll)
        ? Math.max(0, nAll - nTr)
        : null;
  const participate =
    Number.isFinite(nAll) && nAll > 0 ? (nTr / nAll) * 100 : null;

  const fmtHitParts = (pack) => {
    if (!pack || pack.hit_rate_pct == null) {
      return { primary: "—", sub: "", tone: "" };
    }
    const pct = Number(pack.hit_rate_pct);
    const n = pack.n_judged;
    return {
      primary: `${pct.toFixed(1)}%`,
      sub: n != null ? `n=${n}` : "",
      tone: _hitTone(pct),
    };
  };

  const universe =
    `<div class="paper-t0-portrait-universe" title="全部回测日（含跳过）">` +
    `<div class="paper-t0-portrait-universe-main">` +
    `<span class="paper-t0-portrait-universe-n">${
      Number.isFinite(nAll) ? escapeText(String(nAll)) : "—"
    }</span>` +
    `<span class="paper-t0-portrait-universe-unit">样本日</span>` +
    `</div>` +
    `<div class="paper-t0-portrait-universe-meta">` +
    `<span class="is-fill">成交 <b>${escapeText(String(nTr))}</b></span>` +
    (nSk != null
      ? `<span class="is-skip">跳过 <b>${escapeText(String(nSk))}</b></span>`
      : "") +
    (participate != null
      ? `<span class="is-rate">参与 ${escapeText(participate.toFixed(1))}%</span>`
      : "") +
    `</div>` +
    _fillBarHtml(nTr, nAll) +
    `</div>`;

  const slotRows = ((portrait.by_slot && portrait.by_slot.slots) || [])
    .filter((s) => s && s.hm)
    .slice()
    .sort((a, b) =>
      String(a.hm || "").localeCompare(String(b.hm || ""), "en")
    );
  const daySampleN = Number.isFinite(nAll) ? nAll : 0;
  const slotSampleN = slotRows.reduce(
    (m, s) => Math.max(m, Number(s.n_days) || 0),
    0
  );
  const yhatCoverN = slotRows.reduce(
    (m, s) => Math.max(m, Number(s.n_with_yhat) || 0),
    0
  );
  const slotCoverNote =
    slotSampleN > 0
      ? yhatCoverN > 0 && yhatCoverN < slotSampleN
        ? `样本 ${slotSampleN} · 有ŷ最多 ${yhatCoverN}`
        : daySampleN > 0
          ? `样本 ${slotSampleN}（对齐日级）`
          : `样本 ${slotSampleN}`
      : "";
  const denomSpread = slotRows.some(
    (s) => (Number(s.n_days) || 0) > 0 && (Number(s.n_days) || 0) < slotSampleN
  );
  const coverWarn =
    denomSpread
      ? `<p class="paper-t0-portrait-slots-warn">分槽分母未对齐：旧回测只记破带开轮钟。请用当前引擎重跑，分槽读 close_band_scan 每根 5m。</p>`
      : yhatCoverN > 0 &&
          slotSampleN > 0 &&
          yhatCoverN / slotSampleN < 0.25
        ? `<p class="paper-t0-portrait-slots-warn">槽位 R_τ/y_oc/y_τc/y_τ30/y_τ60/y_τ90 ŷ 覆盖偏低（最多 ${yhatCoverN}/${slotSampleN}）：缺分钟日无扫描分；请用最新回测引擎重跑以保留 close_band_scan。</p>`
        : "";

  const fmtHitCell = (pack) => {
    const p = fmtHitParts(pack);
    if (p.primary === "—") return `<span class="is-empty">—</span>`;
    return (
      `<span class="paper-t0-portrait-cell ${p.tone}">` +
      `<b>${escapeText(p.primary)}</b>` +
      (p.sub ? `<small>${escapeText(p.sub)}</small>` : "") +
      `</span>`
    );
  };

  const slotTable =
    slotRows.length > 0
      ? `<div class="paper-t0-portrait-slots" title="${escapeText(
          (portrait.by_slot && portrait.by_slot.note) ||
            "各钟 R_τ / y_oc / y_τc / y_τ30 / y_τ60 / y_τ90 ↔ 标签；样本与日级对齐；缺该钟 ŷ 计 flat；成/跳=该钟是否成交"
        )}">` +
        `<div class="paper-t0-portrait-slots-head">` +
        `<span>分槽位</span>` +
        (slotCoverNote
          ? `<span class="paper-t0-portrait-slots-cover">${escapeText(
              slotCoverNote
            )}</span>`
          : "") +
        `</div>` +
        coverWarn +
        `<table class="paper-t0-portrait-slot-table">` +
        `<thead><tr>` +
        `<th>槽位</th>` +
        `<th title="有该钟扫描 ŷ / 与日级同样本；旧回测无 scan 则仅破带钟有ŷ">覆盖</th>` +
        `<th title="该钟破带成交 / 样本">成交</th>` +
        `<th title="R̂_τ ↔ close[T]/price(τ)−1">R_τ</th>` +
        `<th title="ŷ_oc ↔ open→close">y_oc</th>` +
        `<th title="ŷ_τc ↔ close[T]/price(τ)−1">y_τc</th>` +
        `<th title="ŷ_τ30 ↔ price(τ⊕30m)/price(τ)−1">y_τ30</th>` +
        `<th title="ŷ_τ60 ↔ price(τ⊕60m)/price(τ)−1">y_τ60</th>` +
        `<th title="ŷ_τ90 ↔ price(τ⊕90m)/price(τ)−1">y_τ90</th>` +
        `<th title="破带方向是否与 ŷ_τc 向 C_τ 回归同向（反T remaining&lt;0，正T&gt;0）；不是剩余窗命中率">旁路</th>` +
        `<th title="破带方向是否与 ŷ_τ30 后 30 交易分钟同号（反T ŷ_τ30&lt;0，正T&gt;0）">τ30旁路</th>` +
        `<th title="破带方向是否与 ŷ_τ60 后 60 交易分钟同号（反T ŷ_τ60&lt;0，正T&gt;0）">τ60旁路</th>` +
        `<th title="破带方向是否与 ŷ_τ90 后 90 交易分钟同号（反T ŷ_τ90&lt;0，正T&gt;0）">τ90旁路</th>` +
        `<th title="该钟已成交子集 · R_τ">成交R_τ</th>` +
        `<th title="该钟已成交子集 · y_oc">成交y_oc</th>` +
        `<th title="该钟已成交子集 · y_τc">成交y_τc</th>` +
        `<th title="该钟已成交子集 · y_τ30">成交y_τ30</th>` +
        `<th title="该钟已成交子集 · y_τ60">成交y_τ60</th>` +
        `<th title="该钟已成交子集 · y_τ90">成交y_τ90</th>` +
        `<th title="该钟已成交子集 · 旁路顺带">成交旁路</th>` +
        `<th title="该钟已成交子集 · τ30旁路">成交τ30旁路</th>` +
        `<th title="该钟已成交子集 · τ60旁路">成交τ60旁路</th>` +
        `<th title="该钟已成交子集 · τ90旁路">成交τ90旁路</th>` +
        `</tr></thead><tbody>` +
        slotRows
          .map((s) => {
            const nSlot = Number(s.n_days) || 0;
            const nSlotTr = Number(s.n_traded) || 0;
            const nY = Number(s.n_with_yhat) || 0;
            const coverLow = nSlot > 0 && nY / nSlot < 0.25;
            const traded = s.traded || {};
            return (
              `<tr class="${coverLow ? "is-cover-low" : ""}">` +
              `<td class="paper-t0-portrait-hm">${escapeText(s.hm)}</td>` +
              `<td class="paper-t0-portrait-cover-cell">` +
              `<span class="paper-t0-portrait-cover-txt">` +
              `<b>${escapeText(String(nY))}</b>` +
              `<small>/ ${escapeText(String(nSlot || "—"))}</small>` +
              `</span>` +
              _coverBarHtml(nY, nSlot) +
              `</td>` +
              `<td class="paper-t0-portrait-fill-cell">` +
              `<span class="paper-t0-portrait-fill-txt">` +
              `<b>${escapeText(String(nSlotTr))}</b>` +
              `<small>/ ${escapeText(String(nSlot || "—"))}</small>` +
              `</span>` +
              _fillBarHtml(nSlotTr, nSlot) +
              `</td>` +
              `<td>${fmtHitCell(s.r_tau_hit)}</td>` +
              `<td>${fmtHitCell(s.oc_hit)}</td>` +
              `<td>${fmtHitCell(s.y_tc_hit)}</td>` +
              `<td>${fmtHitCell(s.y_t30_hit)}</td>` +
              `<td>${fmtHitCell(s.y_t60_hit)}</td>` +
              `<td>${fmtHitCell(s.y_t90_hit)}</td>` +
              `<td>${fmtHitCell(s.y_tc_band)}</td>` +
              `<td>${fmtHitCell(s.y_t30_band)}</td>` +
              `<td>${fmtHitCell(s.y_t60_band)}</td>` +
              `<td>${fmtHitCell(s.y_t90_band)}</td>` +
              `<td>${fmtHitCell(traded.r_tau_hit)}</td>` +
              `<td>${fmtHitCell(traded.oc_hit)}</td>` +
              `<td>${fmtHitCell(traded.y_tc_hit)}</td>` +
              `<td>${fmtHitCell(traded.y_t30_hit)}</td>` +
              `<td>${fmtHitCell(traded.y_t60_hit)}</td>` +
              `<td>${fmtHitCell(traded.y_t90_hit)}</td>` +
              `<td>${fmtHitCell(traded.y_tc_band)}</td>` +
              `<td>${fmtHitCell(traded.y_t30_band)}</td>` +
              `<td>${fmtHitCell(traded.y_t60_band)}</td>` +
              `<td>${fmtHitCell(traded.y_t90_band)}</td>` +
              `</tr>`
            );
          })
          .join("") +
        `</tbody></table></div>`
      : "";

  return (
    `<section class="paper-t0-metric-section is-portrait">` +
    `<div class="paper-t0-portrait-head">` +
    `<h4 class="paper-t0-metric-section-title">回测样本画像</h4>` +
    `<p class="paper-t0-portrait-lead">分槽位：该钟扫描 ŷ vs 标签；成交=该钟是否破带开轮；旁路=破带方向↔ŷ_τc向C_τ回归；τ30旁路=破带方向↔ŷ_τ30后30m同号；τ90旁路=破带方向↔ŷ_τ90后90m同号</p>` +
    `</div>` +
    universe +
    slotTable +
    `</section>`
  );
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
  const evalDays = data.eval_days ?? (Number(data.t0_trade_days || 0) + Number(data.skip_days || 0));
  const virtNote = data.virtual_sizing
    ? `虚拟仓每票 ${Number(data.virtual_shares || 1000).toLocaleString("zh-CN")} 股` +
      ` · 本金 ${(Number(data.virtual_cash || 2e5) / 10000).toFixed(0)} 万`
    : null;
  const hero =
    `<header class="paper-t0-report-hero">` +
    `<div class="paper-t0-report-hero-main">` +
    `<div class="paper-t0-report-scope">${escapeText(scope)}</div>` +
    `<div class="paper-t0-report-net ${heroPrimaryCls}">${escapeText(heroPrimary)}</div>` +
    `<div class="paper-t0-report-sub">` +
    (cumRet != null ? "累计收益比例（本金）" : "含敞口净 PnL") +
    ` · 评估 ${evalDays || "—"} 日 · ${escapeText(pathLabel(data))}` +
    (virtNote ? ` · ${escapeText(virtNote)}` : "") +
    `</div>` +
    `</div>` +
    `<div class="paper-t0-report-verdict is-${verdict.tone}" title="${escapeText(verdict.hint)}">` +
    `<span class="paper-t0-report-verdict-label">${escapeText(verdict.label)}</span>` +
    `<span class="paper-t0-report-verdict-hint">${escapeText(verdict.hint)}</span>` +
    `</div>` +
    `</header>`;

  const portrait =
    data.score_portrait ||
    sm.score_portrait ||
    (data.viz && data.viz.score_portrait) ||
    {};
  const portraitSection = buildPortraitSectionHtml(portrait);

  const zeroHint = opts.zeroHint
    ? `<p class="quant-trades-caption paper-t0-zero-hint">${opts.zeroHint}</p>`
    : "";

  return (
    `<div class="paper-t0-report">` +
    hero +
    zeroHint +
    `<div class="paper-t0-report-sections">` +
    portraitSection +
    `</div>` +
    `</div>`
  );
}
