/** Paper · 调仓预演报告渲染（从 paper.js 抽出）。 */

import { fitTierBadgeForCode, ensureFitTierMap } from "../quant/fit_tier_ui.js?v=p2261";

/**
 * @param {object} deps
 * @returns {{ dismissRebalancePreview: Function, emptyReasonLabel: Function, renderRebalanceReport: Function }}
 */
export function createRebalanceReportController(deps) {
  const {
    escapeText,
    fmtPct,
    fmtTableScore,
    scoreCls,
    resolveEodScore,
    resolveTauScore,
    resolveOnScore,
    Y_EOD_TITLE,
    Y_TAU_TITLE,
    Y_ON_TITLE,
    isHeuristicScoreScale,
    renderOpsReport,
    showValidateNext,
    formatWeightSourceNote,
    formatFactorWeightsSection,
    formatFormulaTermsSection,
    formatScoreHero,
    formatRemScoreSection,
    formatBlendScoreSection,
    formatSentimentGateSection,
    formatMarketPriorSection,
    marketPriorDetailFields,
    tailAnomalyDetailFields,
    overheatDetailFields,
    scoreTips,
    showPlainTooltip,
    hideScoreTooltip,
    metricCls,
    setFollowMatrixPreviewPending,
  } = deps;

function previewMetricChip(cls, label, value) {
  return (
    `<span class="paper-t0-desk-chip ${cls}">` +
    `<span class="paper-t0-desk-chip-k">${escapeText(label)}</span>` +
    `<span class="paper-t0-desk-chip-v">${escapeText(String(value ?? 0))}</span>` +
    `</span>`
  );
}

function paintRebalancePreviewChips({ report, skips, cashImpact } = {}) {
  const chipsEl = document.getElementById("paper-rebalance-preview-chips");
  if (!chipsEl) return;
  const rows = Array.isArray(report) ? report : [];
  let buyN = 0;
  let sellN = 0;
  let holdN = 0;
  let skipN = 0;
  for (const r of rows) {
    const d = String((r && r.decision) || "");
    if (d.includes("买入") || d.includes("加仓")) buyN += 1;
    else if (d.includes("卖出") || d.includes("减仓")) sellN += 1;
    else if (d.includes("跳过")) skipN += 1;
    else holdN += 1;
  }
  const skipExtra = Array.isArray(skips) ? skips.length : 0;
  const turn = cashImpact != null ? cashImpact.turnover_pct : null;
  const turnN = turn == null || turn === "" ? NaN : Number(turn);
  chipsEl.innerHTML =
    previewMetricChip("is-univ", "票", rows.length) +
    previewMetricChip("is-done", "买", buyN) +
    previewMetricChip("is-locked", "卖", sellN) +
    previewMetricChip("is-idle", "持", holdN) +
    previewMetricChip("is-locked", "跳过", skipN + skipExtra) +
    (Number.isFinite(turnN)
      ? previewMetricChip("is-univ", "换手", `${turnN.toFixed(1)}%`)
      : "");
}

/** 确认落账后收起预演区（持仓/流水另由 loadPaper 刷新）。 */
function dismissRebalancePreview() {
  const section = document.getElementById("paper-rebalance-section");
  if (section) section.hidden = true;
  const confirmBtn = document.getElementById("paper-rebalance-confirm");
  if (confirmBtn) {
    confirmBtn.hidden = true;
    confirmBtn.disabled = false;
  }
  const previewNote = document.getElementById("paper-rebalance-preview-note");
  if (previewNote) {
    previewNote.hidden = true;
    previewNote.textContent = "";
  }
  const cashEl = document.getElementById("paper-rebalance-cash");
  if (cashEl) {
    cashEl.hidden = true;
    cashEl.innerHTML = "";
  }
  const container = document.getElementById("paper-rebalance-report");
  if (container) container.innerHTML = "";
  const chipsEl = document.getElementById("paper-rebalance-preview-chips");
  if (chipsEl) chipsEl.innerHTML = "";
  const nextEl = document.getElementById("paper-validate-next");
  if (nextEl) {
    nextEl.hidden = true;
    nextEl.innerHTML = "";
  }
  if (typeof setFollowMatrixPreviewPending === "function") setFollowMatrixPreviewPending(false);
}

function emptyReasonLabel(code) {
  const map = {
    empty_book: "目标簿为空（无人过闸或未建簿）",
    all_below_eod_floor: "全部低于 EOD 买入门槛",
    all_below_fuse_enter: "无人过 fuse 入场",
    cash_below_floor: "现金不足，无法买入",
    buys_blocked: "买入被风控/规则拦截",
    risk_blocked: "风控整批拦截",
    wait_clock: "未到启动时间",
    miss_window: "已过启动窗口",
    holiday: "非交易日",
    book_constraints_empty: "簿约束装填后为空",
    no_mapped_scores: "无可用评分映射",
  };
  const k = String(code || "").trim();
  if (!k) return "";
  return map[k] || k;
}

function fmtFuseEnterPct(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return "";
  const pct = n * 100;
  const rounded = Math.round(pct * 1000) / 1000;
  const text =
    Math.abs(rounded - Math.round(rounded * 10) / 10) < 1e-9
      ? String(Number(rounded.toFixed(1)))
      : String(Number(rounded.toFixed(3)));
  return `${text}%`;
}

function emptyFloorHint({
  minScore,
  fuseEnter,
  matrixPreview,
  emptyReason,
} = {}) {
  const reason = String(emptyReason || "");
  if (reason === "cash_below_floor") return "";
  const enterRaw =
    fuseEnter != null && Number.isFinite(Number(fuseEnter))
      ? Number(fuseEnter)
      : matrixPreview && minScore != null && Number.isFinite(Number(minScore))
        ? Number(minScore)
        : null;
  if (enterRaw != null && (matrixPreview || fuseEnter != null)) {
    const shown = fmtFuseEnterPct(enterRaw);
    return shown ? ` · fuse入场≥${shown}` : "";
  }
  if (minScore != null && Number.isFinite(Number(minScore))) {
    return ` · ŷ_oo≥${Number(minScore)}%`;
  }
  return "";
}

function fmtRebalancePx(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return `${n}元`;
}

function rebalancePrevCloseText(r) {
  if (r.prev_close != null && r.prev_close !== "") {
    const n = Number(r.prev_close);
    if (Number.isFinite(n)) return `${n.toFixed(2)}元`;
    return String(r.prev_close);
  }
  const px = Number(r.price);
  const chg = Number(r.change_pct);
  if (Number.isFinite(px) && px > 0 && Number.isFinite(chg)) {
    return `${(px / (1 + chg / 100)).toFixed(2)}元`;
  }
  return "—";
}

function renderRebalanceReport(
  report,
  {
    preview = false,
    cashImpact = null,
    riskGate = null,
    opsReport = null,
    riskBudgetSkips = null,
    dualScore = null,
    emptyReason = null,
    minScore = null,
    fuseEnter = null,
    emptyDetail = null,
    marketContext = null,
    matrixPreview = false,
  } = {}
) {
  const section = document.getElementById("paper-rebalance-section");
  const container = document.getElementById("paper-rebalance-report");
  const cashEl = document.getElementById("paper-rebalance-cash");
  const previewNote = document.getElementById("paper-rebalance-preview-note");
  const confirmBtn = document.getElementById("paper-rebalance-confirm");
  if (!section || !container) return;

  if (opsReport) renderOpsReport(opsReport, { forceShow: true });
  else if (matrixPreview) renderOpsReport(null);
  // 按 fuse 降序展示（无分排后）
  report = Array.isArray(report)
    ? [...report].sort((a, b) => {
        const fuseOf = (r) => {
          const n = Number(r && r.y_oo_fuse);
          return Number.isFinite(n) ? n : null;
        };
        const as = fuseOf(a);
        const bs = fuseOf(b);
        if (as == null && bs == null) return 0;
        if (as == null) return 1;
        if (bs == null) return -1;
        return bs - as;
      })
    : report;
  const isAlignedSkip = (s) => {
    const r = String((s && s.reason) || "");
    return (
      r.includes("已对齐") ||
      r.includes("不动") ||
      r.includes("欲加仓但") ||
      r.includes("欲减仓但") ||
      r.includes("本窗跳过") ||
      r.includes("本窗推迟") ||
      r.includes("必清但")
    );
  };
  const skips = (Array.isArray(riskBudgetSkips) ? riskBudgetSkips : []).filter(
    (s) => s && !isAlignedSkip(s)
  );
  const hasSkips = skips.length > 0;
  const hasRisk =
    riskGate &&
    ((Array.isArray(riskGate.blocks) && riskGate.blocks.length > 0) ||
      (Array.isArray(riskGate.warnings) && riskGate.warnings.length > 0));
  const hasCash =
    !matrixPreview &&
    cashImpact &&
    (cashImpact.cash_before != null ||
      cashImpact.buy_amount != null ||
      cashImpact.sell_amount != null ||
      cashImpact.turnover_pct != null);
  const hasOps = !!(opsReport && (opsReport.strategy_id || opsReport.cost_model));
  const emptyReasonText = emptyReasonLabel(emptyReason);
  const hasEmptyReason = !!emptyReasonText;
  const emptyReport = !report || report.length === 0;

  if (
    emptyReport &&
    !hasCash &&
    !hasRisk &&
    !hasOps &&
    !hasSkips &&
    !hasEmptyReason
  ) {
    section.hidden = true;
    if (previewNote) previewNote.hidden = true;
    if (confirmBtn) confirmBtn.hidden = true;
    if (cashEl) {
      cashEl.hidden = true;
      cashEl.innerHTML = "";
    }
    const nextEl = document.getElementById("paper-validate-next");
    if (nextEl) {
      nextEl.hidden = true;
      nextEl.innerHTML = "";
    }
    renderOpsReport(null);
    paintRebalancePreviewChips();
    return;
  }

  showValidateNext("rebalance");

  const sectionEl = document.getElementById("follow-fold-rebalance");
  if (sectionEl) {
    try {
      sectionEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {
      /* ignore */
    }
  }
  if (previewNote) {
    // 矩阵预演：有清单时确认按钮已够，不重复「预演未写账」；仅空仓原因写 note
    if (matrixPreview && preview && !hasEmptyReason) {
      previewNote.hidden = true;
      previewNote.textContent = "";
    } else {
    previewNote.hidden = !preview && !hasEmptyReason;
    let note = preview
      ? matrixPreview
        ? ""
        : "预演未成交 · 确认后才会改仓"
      : "";
    if (preview && cashImpact && cashImpact.turnover_capped) {
      const cap =
        cashImpact.max_turnover_pct != null
          ? `（上限 ${cashImpact.max_turnover_pct}%）`
          : "";
      note += ` · 换手软上限已截断买入${cap}`;
    }
    const tauGate = dualScore && dualScore.tau_gate;
    if (
      preview &&
      tauGate &&
      String(tauGate.mode || "") === "freeze_breakglass"
    ) {
      const ef =
        tauGate.effective_floor != null
          ? Number(tauGate.effective_floor)
          : null;
      note +=
        ` · τ 试验档` +
        (ef != null && Number.isFinite(ef) ? `（门槛 ${ef}%）` : "");
    }
    if (hasEmptyReason) {
      const floorHint = emptyFloorHint({
        minScore,
        fuseEnter,
        matrixPreview,
        emptyReason,
      });
      const detailHint =
        emptyDetail && String(emptyDetail).trim()
          ? ` · ${String(emptyDetail).trim()}`
          : "";
      note += (note ? " · " : "") + emptyReasonText + detailHint + floorHint;
    }
    previewNote.textContent = note;
    previewNote.hidden = !note;
    }
  }
  if (confirmBtn) {
    confirmBtn.hidden = !preview || emptyReport;
    confirmBtn.disabled = false;
  }

  if (cashEl) {
    const ci = cashImpact || {};
    if (hasCash || hasRisk || hasSkips || hasEmptyReason) {
      const fmt = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return "—";
        return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
      };
      const netRaw =
        ci.net_cash_flow != null ? ci.net_cash_flow : ci.net_cash;
      const net = Number(netRaw);
      const netCls =
        !Number.isFinite(net) || net === 0 ? "" : net > 0 ? "up" : "down";
      const netText = Number.isFinite(net)
        ? `${net >= 0 ? "+" : ""}${fmt(net)}`
        : "—";
      const costLabel =
        ci.cost_model === "simple_cn"
          ? "A股简化成本 · 现价"
          : ci.cost_model === "zero"
            ? "零成本 · 现价"
            : "现价成交";
      let riskHtml = "";
      if (hasRisk) {
        const blocks = (riskGate.blocks || [])
          .map((b) => `<li class="down">${escapeText(String(b))}</li>`)
          .join("");
        const warns = (riskGate.warnings || [])
          .map((w) => `<li>${escapeText(String(w))}</li>`)
          .join("");
        const mp = riskGate.market_prior || {};
        const mpLine =
          mp.blocks > 0
            ? `<li>市场 prior 跳过新开仓 ${escapeText(String(mp.blocks))} 只</li>`
            : "";
        riskHtml =
          `<div class="paper-rebalance-risk" role="status">` +
          (blocks
            ? `<p class="paper-rebalance-risk-title">风控拦截加仓</p><ul>${blocks}</ul>`
            : "") +
          (mpLine ? `<ul>${mpLine}</ul>` : "") +
          (warns
            ? `<p class="paper-rebalance-risk-title is-warn">风控提示</p><ul>${warns}</ul>`
            : "") +
          `</div>`;
      }
      let emptyHtml = "";
      if (hasEmptyReason) {
        const floorHint = emptyFloorHint({
          minScore,
          fuseEnter,
          matrixPreview,
          emptyReason,
        });
        const detailHint =
          emptyDetail && String(emptyDetail).trim()
            ? ` · ${String(emptyDetail).trim()}`
            : "";
        emptyHtml =
          `<div class="paper-rebalance-risk" role="status">` +
          `<p class="paper-rebalance-risk-title is-warn">空仓/无变动原因</p>` +
          `<ul><li>${escapeText(emptyReasonText)}${escapeText(
            detailHint + floorHint
          )}</li></ul></div>`;
      }
      let skipHtml = "";
      let marketCtxHtml = "";
      const mctx = marketContext && typeof marketContext === "object" ? marketContext : null;
      if (mctx && (mctx.prior_active || (mctx.prior_warnings || []).length)) {
        const macro = mctx.macro || {};
        const tech =
          macro.overseas_tech_1d_pct != null
            ? `${Number(macro.overseas_tech_1d_pct).toFixed(2)}%`
            : "—";
        const warns = (mctx.prior_warnings || []).slice(0, 4).map((w) => escapeText(String(w)));
        marketCtxHtml =
          `<div class="paper-rebalance-risk paper-rebalance-mctx" role="status">` +
          `<p class="paper-rebalance-risk-title">盘前 M prior</p>` +
          `<ul><li>海外科技 ${escapeText(tech)}` +
          (warns.length ? ` · ${warns.join(" · ")}` : "") +
          `</li></ul></div>`;
      }
      if (hasSkips) {
        const priorSkips = skips.filter((s) => s && s.sentiment_prior);
        const marketSkips = skips.filter((s) => s && s.market_prior);
        const floorSkips = skips.filter(
          (s) =>
            s &&
            !s.sentiment_prior &&
            !s.market_prior &&
            (String(s.reason || "").includes("floor") ||
              String(s.reason || "").includes("门槛") ||
              String(s.reason || "") === "below_eod_floor" ||
              String(s.reason || "") === "oos_failed_no_buy")
        );
        const otherSkips = skips.filter(
          (s) => s && !s.sentiment_prior && !s.market_prior && !floorSkips.includes(s)
        );
        const row = (s) => {
          const code = escapeText(String(s.stock_code || ""));
          const name = escapeText(String(s.stock_name || ""));
          const reason = escapeText(String(s.reason || "跳过"));
          const gate =
            s.eod_gate_score != null
              ? ` · ŷ_oo ${escapeText(String(s.eod_gate_score))}`
              : "";
          return `<li><code>${code}</code>${
            name ? ` ${name}` : ""
          } · ${reason}${gate}</li>`;
        };
        skipHtml =
          `<div class="paper-rebalance-risk" role="status">` +
          (priorSkips.length
            ? `<p class="paper-rebalance-risk-title">舆情先验 · 跳过新开仓</p><ul>${priorSkips
                .slice(0, 12)
                .map(row)
                .join("")}</ul>`
            : "") +
          (marketSkips.length
            ? `<p class="paper-rebalance-risk-title">市场 prior · 跳过新开仓</p><ul>${marketSkips
                .slice(0, 12)
                .map(row)
                .join("")}</ul>`
            : "") +
          (floorSkips.length
            ? `<p class="paper-rebalance-risk-title is-warn">未过买入门槛</p><ul>${floorSkips
                .slice(0, 12)
                .map(row)
                .join("")}</ul>`
            : "") +
          (otherSkips.length
            ? `<p class="paper-rebalance-risk-title is-warn">其他跳过买入</p><ul>${otherSkips
                .slice(0, 8)
                .map(row)
                .join("")}</ul>`
            : "") +
          `</div>`;
      }
      const cashCells = [];
      const buyAmt = Number(ci.buy_amount);
      const sellAmt = Number(ci.sell_amount);
      const buyPos = Number.isFinite(buyAmt) && buyAmt > 0;
      const sellPos = Number.isFinite(sellAmt) && sellAmt > 0;
      // 矩阵预演：只留有量买卖 + 换手；现金/净流与卖额重复，不画
      if (!matrixPreview) {
        if (ci.cash_before != null && Number.isFinite(Number(ci.cash_before))) {
          cashCells.push(
            `<div><dt>调仓前现金</dt><dd>${fmt(ci.cash_before)}</dd></div>`
          );
        }
      }
      if (
        ci.buy_amount != null &&
        Number.isFinite(buyAmt) &&
        (!matrixPreview || buyPos)
      ) {
        cashCells.push(
          `<div><dt>预计买入</dt><dd>${fmt(ci.buy_amount)}</dd></div>`
        );
      }
      if (
        ci.sell_amount != null &&
        Number.isFinite(sellAmt) &&
        (!matrixPreview || sellPos)
      ) {
        cashCells.push(
          `<div><dt>预计卖出</dt><dd>${fmt(ci.sell_amount)}</dd></div>`
        );
      }
      if (!matrixPreview && Number.isFinite(net)) {
        cashCells.push(
          `<div><dt>净现金流</dt><dd class="${netCls}">${netText}</dd></div>`
        );
      }
      if (
        !matrixPreview &&
        ci.cash_after != null &&
        Number.isFinite(Number(ci.cash_after))
      ) {
        cashCells.push(
          `<div><dt>调仓后现金</dt><dd>${fmt(ci.cash_after)}</dd></div>`
        );
      }
      const posBefore = ci.position_count_before;
      const posAfter = ci.position_count_after;
      if (
        !matrixPreview &&
        posBefore != null &&
        posAfter != null &&
        Number.isFinite(Number(posBefore)) &&
        Number.isFinite(Number(posAfter))
      ) {
        cashCells.push(
          `<div><dt>持仓只数</dt><dd>${escapeText(String(posBefore))} → ${escapeText(
            String(posAfter)
          )}</dd></div>`
        );
      }
      if (ci.turnover_pct != null) {
        cashCells.push(
          `<div title="双边换手=(买额+卖额)/2/净值"><dt>换手</dt><dd${
            ci.turnover_over_limit || ci.turnover_capped ? ' class="down"' : ""
          }>${escapeText(String(ci.turnover_pct))}%${
            ci.max_turnover_pct != null
              ? ` / ${escapeText(String(ci.max_turnover_pct))}%`
              : ""
          }${ci.turnover_capped ? " · 已截断" : ""}</dd></div>`
        );
      }
      const cashHeadLabel = matrixPreview ? "成交预估" : "资金影响";
      const costNote = matrixPreview ? "" : costLabel;
      cashEl.hidden = false;
      cashEl.innerHTML =
        (marketCtxHtml || "") +
        (hasCash && cashCells.length
          ? `<div class="paper-rebalance-cash-head">` +
            `<span class="paper-rebalance-cash-label">${cashHeadLabel}</span>` +
            (costNote
              ? `<span class="paper-rebalance-cash-note">${costNote}</span>`
              : "") +
            `</div>` +
            `<dl class="paper-rebalance-cash-grid" aria-label="${cashHeadLabel}">` +
            cashCells.join("") +
            `</dl>`
          : "") +
        emptyHtml +
        riskHtml +
        skipHtml;
    } else {
      cashEl.hidden = true;
      cashEl.innerHTML = "";
    }
  }

  if (emptyReport) {
    const floorHint = emptyFloorHint({
      minScore,
      fuseEnter,
      matrixPreview,
      emptyReason,
    });
    const detailHint =
      emptyDetail && String(emptyDetail).trim()
        ? ` · ${String(emptyDetail).trim()}`
        : "";
    container.innerHTML = hasEmptyReason
      ? `<p class="follow-ops-note">${escapeText(emptyReasonText)}${escapeText(
          detailHint + floorHint
        )}。</p>`
      : hasSkips
        ? `<p class="follow-ops-note">本轮无加仓清单（跳过 ${skips.length} 只）。</p>`
        : hasRisk
          ? `<p class="follow-ops-note">因风控拦截，本轮无加仓清单。</p>`
          : `<p class="follow-ops-note">无可执行变动。</p>`;
    section.hidden = false;
    return;
  }

  const decisionClass = {
    加仓: "rebalance-up",
    买入: "rebalance-up",
    减仓: "rebalance-down",
    卖出: "rebalance-down",
    止损卖出: "rebalance-sell",
    超时卖出: "rebalance-sell",
    跳过: "rebalance-hold",
  };

  // 列宽在 CSS 固定轨定义，避免每行独立 grid 用 fr 按内容各算导致错位
  function tipDetailPayload(r) {
    return {
      stock_code: r.stock_code || r.code || null,
      y_oo_fuse: r.y_oo_fuse,
      y_oo: r.y_oo,
      y_oc: r.y_oc,
      y_co: r.y_co != null ? r.y_co : r.y_on,
      fusion_w_oo: r.fusion_w_oo,
      fusion_w_oc: r.fusion_w_oc,
      fusion_w_co: r.fusion_w_co,
      predicted_score_eod: r.predicted_score_eod,
      predicted_score_tau: r.predicted_score_tau,
      predicted_score_on: r.predicted_score_on,
      y_oo_fuse_score: r.y_oo_fuse_score,
      y_oo_rank: r.y_oo_rank != null ? r.y_oo_rank : r.predicted_score_oo_rank,
      y_oo_rank_n: r.y_oo_rank_n,
      y_oo_rank_score: r.y_oo_rank_score,
      predicted_score_oo_rank: r.predicted_score_oo_rank,
      y_tc_rank: r.y_tc_rank != null ? r.y_tc_rank : r.predicted_score_tc_rank,
      y_tc_rank_n: r.y_tc_rank_n,
      y_tc_rank_score: r.y_tc_rank_score,
      predicted_score_tc_rank: r.predicted_score_tc_rank,
      formula_terms_oo_rank: r.formula_terms_oo_rank || r.score_formula_terms_oo_rank,
      score_formula_terms_oo_rank: r.score_formula_terms_oo_rank || r.formula_terms_oo_rank,
      formula_terms_tc_rank: r.formula_terms_tc_rank || r.score_formula_terms_tc_rank,
      score_formula_terms_tc_rank: r.score_formula_terms_tc_rank || r.formula_terms_tc_rank,
      rank_i: r.rank_i,
      rank_n: r.rank_n,
      gap_pct: r.gap_pct,
      predicted_score: r.predicted_score != null ? r.predicted_score : r.score,
      score: r.score != null ? r.score : r.predicted_score,
      min_score: r.min_score,
      below_min_score: r.below_min_score,
      formula_terms: r.score_formula_terms,
      score_rem: r.score_rem,
      event_prior: r.event_prior,
      as_of_tau: r.as_of_tau || r.rem_tau,
      dual_score_fusion: r.dual_score_fusion,
      dual_score_weights: r.dual_score_weights,
      dual_score_head: r.dual_score_head,
      dual_score_single_head: r.dual_score_single_head,
      y_check: r.y_check,
      y_disagree: r.y_disagree,
      y_sigma: r.y_sigma,
      y_mu: r.y_mu,
      eod_trust: r.eod_trust,
      y_tau_to_close: r.y_tau_to_close,
      y_tau_to_close_src: r.y_tau_to_close_src,
      y_state: r.y_state,
      predicted_score_blend: r.predicted_score_blend,
      predicted_score_eod_rem: r.predicted_score_eod_rem,
      dual_score_window: r.dual_score_window || null,
      eod_feature_as_of: r.eod_feature_as_of || null,
      trade_day: r.trade_day || null,
      realized_t1_to_tau: r.realized_t1_to_tau,
      y_spec_tau: r.y_spec_tau,
      features_tau: r.features_tau,
      formula_terms_tau: r.formula_terms_tau || r.score_formula_terms_tau,
      score_formula_tau: r.score_formula_tau,
      factor_coefficients_tau: r.factor_coefficients_tau,
      formula_terms_co:
        r.formula_terms_co ||
        r.score_formula_terms_co ||
        r.formula_terms_on ||
        r.score_formula_terms_on,
      features_co: r.features_co || r.features_on || null,
      y_spec_co: r.y_spec_co || r.y_spec_on || null,
      weight_source: r.weight_source,
      cluster_label: r.cluster_label,
      cluster_mode: r.cluster_mode,
      cluster_version: r.cluster_version,
      score_global: r.score_global,
      score_cluster: r.score_cluster,
      return_model_source: r.return_model_source,
      factor_coefficients: r.factor_coefficients || {},
      reasons: r.reasons || [],
      hard_reject: !!r.hard_reject,
      reject_reason: r.reject_reason || "",
      score_formula: r.score_formula || "",
      sentiment_prior: r.sentiment_prior || null,
      ...marketPriorDetailFields(r),
      ...tailAnomalyDetailFields(r),
      ...overheatDetailFields(r),
    };
  }

  function buildScoreDetail(r) {
    if (
      !r.reasons &&
      !r.score_formula &&
      !r.hard_reject &&
      !r.weight_source &&
      !r.cluster_label &&
      !r.return_model_source &&
      !(r.factor_coefficients && Object.keys(r.factor_coefficients || {}).length) &&
      !(r.score_formula_terms && (r.score_formula_terms.terms || []).length)
    ) {
      return '<div class="score-detail-empty">无评分详情</div>';
    }

    let html = '<div class="score-detail">';
    const tipRaw = tipDetailPayload(r);
    html += formatBlendScoreSection(tipRaw);
    html += formatRemScoreSection(tipRaw);
    html += formatFormulaTermsSection(tipRaw, { key: "tau" });
    html += formatFactorWeightsSection(tipRaw, { key: "tau" });
    html += formatScoreHero(tipRaw);
    html += formatFormulaTermsSection(tipRaw);
    html += formatFactorWeightsSection(tipRaw);
    html += formatFormulaTermsSection(tipRaw, { key: "oo_rank" });
    html += formatFormulaTermsSection(tipRaw, { key: "tc_rank" });
    html += formatWeightSourceNote(tipRaw);
    html += formatSentimentGateSection(tipRaw);
    html += formatMarketPriorSection(tipRaw);

    if (r.score_formula && !(r.score_formula_terms && (r.score_formula_terms.terms || []).length)) {
      let formula = String(r.score_formula || "");
      const tempDiv = document.createElement("div");
      tempDiv.innerHTML = formula;
      formula = tempDiv.textContent || tempDiv.innerText || "";
      html +=
        `<div class="score-formula-section">` +
        `<div class="score-section-title">ŷ_oo 公式</div>` +
        `<div class="score-formula">${escapeText(formula)}</div>` +
        `</div>`;
    }

    if (r.hard_reject && r.reject_reason) {
      html +=
        `<div class="score-detail-reject">` +
        `<span class="score-detail-reject-icon">⚠</span>` +
        `<span class="score-detail-reject-text">${escapeText(r.reject_reason)}</span>` +
        `</div>`;
    }

    const reasons = r.reasons || [];
    if (reasons.length > 0) {
      html +=
        `<div class="score-reasons-section">` +
        `<div class="score-section-title">评分理由</div>` +
        `<ul class="score-reasons">`;
      reasons.forEach((rsn) => {
        let cls = "neutral";
        if (/强于|高于|上升|增加|优秀|良好|高/.test(rsn)) cls = "pos";
        else if (/弱于|低于|下降|减少|较差|低/.test(rsn)) cls = "neg";
        html += `<li class="${cls}">${escapeText(rsn)}</li>`;
      });
      html += "</ul></div>";
    }

    html += "</div>";
    return html;
  }

  function rankOrdinal(raw, nRaw) {
    const y = Number(raw);
    const n = Number(nRaw);
    if (!Number.isFinite(y)) return { text: "—" };
    const ord = Math.round(y);
    const isInt = Math.abs(y - ord) < 1e-9;
    const pool = Number.isFinite(n) && n >= 1 ? Math.round(n) : null;
    if (pool != null && isInt && ord >= 1 && ord <= pool) return { text: String(ord), n: pool };
    return { text: "—" };
  }

  const rows = report
    .map((r, idx) => {
      const cls = decisionClass[r.decision] || "rebalance-hold";
      const scoreEod = resolveEodScore(r);
      const scoreEodShown =
        scoreEod != null ? fmtTableScore(r, scoreEod) : "—";
      const scoreEodTitle = scoreEod == null ? "暂无 ŷ_oo" : Y_EOD_TITLE;
      const scoreTau = resolveTauScore(r);
      const scoreTauShown =
        scoreTau != null ? fmtTableScore(r, scoreTau) : "—";
      const scoreTauTitle = scoreTau == null ? "暂无 ŷ_τc" : Y_TAU_TITLE;
      const scoreOn = resolveOnScore(r);
      const scoreOnShown =
        scoreOn != null ? fmtTableScore(r, scoreOn) : "—";
      const scoreOnTitle = scoreOn == null ? "暂无 ŷ_co" : Y_ON_TITLE;
      const fuseRaw = Number(r.y_oo_fuse);
      const scoreFuse = Number.isFinite(fuseRaw) ? fuseRaw : null;
      const scoreFuseShown =
        scoreFuse != null ? fmtTableScore(r, scoreFuse) : "—";
      const rankI = Number(r.rank_i);
      const rankN = Number(r.rank_n);
      const rankOrd =
        Number.isFinite(rankI) && Number.isFinite(rankN) && rankN > 0
          ? ` · 候选第 ${rankI}/${rankN}`
          : Number.isFinite(rankI)
            ? ` · 候选第 ${rankI}`
            : "";
      const scoreFuseTitle =
        scoreFuse == null
          ? "暂无 fuse"
          : `fuse · τ→open[T+1] 基准（τc 几何融合；OC 扣 open→τ）；<0% 或缺分清仓；过入场才开/加${rankOrd}`;
      const ooRank = rankOrdinal(
        r.y_oo_rank != null ? r.y_oo_rank : r.predicted_score_oo_rank,
        r.y_oo_rank_n
      );
      const tcRank = rankOrdinal(
        r.y_tc_rank != null ? r.y_tc_rank : r.predicted_score_tc_rank,
        r.y_tc_rank_n
      );
      const ooRankTitle =
        ooRank.text === "—"
          ? "oo_rank — · 无已启用模型或未写入；不进 fuse；过入场后作买序；与 τc_rank 共用 rank 入场"
          : `当日截面第 ${ooRank.text} 名 / ${ooRank.n} · 1=相对分最高 · 不进 fuse；过入场后作买序；与 τc_rank 共用 rank 入场`;
      const tcRankTitle =
        tcRank.text === "—"
          ? "τc_rank — · 无已启用模型或未写入 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序"
          : `当日截面第 ${tcRank.text} 名 / ${tcRank.n} · 1=相对分最高 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序`;
      const tipDetailJson = escapeText(
        JSON.stringify(tipDetailPayload(r))
      );
      const oldSh =
        r.old_shares != null
          ? Number(r.old_shares)
          : r.shares_before != null
            ? Number(r.shares_before)
            : null;
      const newSh =
        r.new_shares != null
          ? Number(r.new_shares)
          : r.shares_after != null
            ? Number(r.shares_after)
            : null;
      // 优先用股数差；避免 shares_change=0 却挡住 before/after 回算
      let shChange = null;
      if (
        oldSh != null &&
        newSh != null &&
        Number.isFinite(oldSh) &&
        Number.isFinite(newSh)
      ) {
        shChange = newSh - oldSh;
      } else if (
        r.shares_change != null &&
        Number.isFinite(Number(r.shares_change))
      ) {
        shChange = Number(r.shares_change);
      }
      const changeText =
        shChange == null || !Number.isFinite(shChange)
          ? "—"
          : shChange > 0
            ? `+${Math.round(shChange)}`
            : shChange < 0
              ? `${Math.round(shChange)}`
              : "0";
      const changeCls =
        shChange == null || !Number.isFinite(shChange) || shChange === 0
          ? "is-flat"
          : shChange > 0
            ? "is-up"
            : "is-down";
      const hasDetail = !!(
        r.factors ||
        r.factor_contrib ||
        r.reasons ||
        r.score_formula ||
        r.weight_source
      );
      const expandIcon = hasDetail
        ? '<span class="rebalance-expand-icon" aria-hidden="true">›</span>'
        : '<span class="rebalance-expand-icon is-empty" aria-hidden="true"></span>';

      let decisionTagClass = "hold";
      const decision = String(r.decision || "");
      if (decision.includes("加仓") || decision.includes("买入")) decisionTagClass = "up";
      else if (decision.includes("减仓")) decisionTagClass = "down";
      else if (
        decision.includes("止损") ||
        decision.includes("超时") ||
        decision.includes("卖出")
      )
        decisionTagClass = "sell";

      // 决策标签悬停 = 原因（不再单独占列）
      let decisionTip = "";
      const reasonText = String(r.reason || "").trim();
      const isPrior =
        !!r.sentiment_prior ||
        reasonText.includes("舆情先验") ||
        reasonText.includes("sentiment_prior");
      const isMarketPrior =
        !!r.market_prior ||
        reasonText.includes("market_prior") ||
        reasonText.includes("市场 prior") ||
        reasonText.includes("cross_market");
      if (isPrior && (decision.includes("减仓") || decision.includes("卖出"))) {
        decisionTip = reasonText
          ? `${reasonText}（强看空先验 · 不改 ŷ）；点「确认落账」后才成交`
          : "舆情先验 · 强看空缩仓（不改 ŷ）；点「确认落账」后才成交";
      } else if (
        isMarketPrior &&
        (decision.includes("减仓") || decision.includes("卖出"))
      ) {
        decisionTip = reasonText
          ? `${reasonText}（M prior 缩仓 · 不改 ŷ）；点「确认落账」后才成交`
          : "市场 prior · M 层缩仓（不改 ŷ）；点「确认落账」后才成交";
      } else if (decision.includes("止损卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认落账」后才成交`
          : "止损规则触发，预演清仓；点「确认落账」后才成交";
      } else if (decision.includes("超时卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认落账」后才成交`
          : "持有超时且趋势向下，预演清仓；点「确认落账」后才成交";
      } else if (decision.includes("跳过")) {
        decisionTip = isMarketPrior
          ? reasonText
            ? `${reasonText}；点「确认落账」后仍不会新开仓`
            : "市场 prior · 跳过新开仓；点「确认落账」后仍不会买入"
          : isPrior
          ? reasonText
            ? `${reasonText}；点「确认落账」后仍不会新开仓`
            : "舆情先验 · 跳过新开仓；点「确认落账」后仍不会买入"
          : reasonText || "风险预算（单票/行业上限）未买入";
      } else if (decision.includes("减仓")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认落账」后才成交`
          : "预演减仓；点「确认落账」后才成交";
      } else if (decision.includes("卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认落账」后才成交`
          : "策略调仓卖出：fuse<0 或缺分清仓；点「确认落账」后才成交";
      } else if (reasonText) {
        decisionTip = reasonText;
      }

      const rawCode = String(r.stock_code || "").trim();
      const rawName = String(r.stock_name || "").trim();
      // 名称等于代码时视为缺名，避免「000739 / 000739」双行伪名
      const nameLabel =
        rawName && rawName !== rawCode ? rawName : rawName && !rawCode ? rawName : "—";
      const name = escapeText(nameLabel);
      const code = escapeText(rawCode);
      const chgPct = r.change_pct;
      const chgText = fmtPct(chgPct, { signed: true });
      const chgCls = metricCls(chgPct);
      const prevCloseText = rebalancePrevCloseText(r);
      const priceText = fmtRebalancePx(r.price);
      const oosFailed =
        !!r.oos_failed ||
        !!r.oosFailed ||
        isHeuristicScoreScale(r) ||
        String(r.return_model_source || "").startsWith("oos_failed");
      const oosBadge = oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      const sentPriorBadge =
        isPrior &&
        (decision.includes("减仓") ||
          decision.includes("卖出") ||
          decision.includes("跳过"))
          ? `<span class="watching-sent-badge is-bear" title="舆情参考 · 不调仓">S</span>`
          : "";
      const marketPriorBadge =
        isMarketPrior &&
        (decision.includes("减仓") ||
          decision.includes("卖出") ||
          decision.includes("跳过"))
          ? `<span class="paper-market-prior-badge" title="M prior · 不改 ŷ">M</span>`
          : "";

      return (
        `<div class="rebalance-row ${cls}${hasDetail ? " is-expandable" : ""}${
          oosFailed ? " is-oos-failed" : ""
        }" role="row" ` +
        `data-detail-idx="${idx}"` +
        `${hasDetail ? ' title="点击展开评分明细"' : ""}>` +
        `<div class="rebalance-stock" role="cell">` +
        expandIcon +
        `<span class="rebalance-stock-main">` +
        `<span class="rebalance-stock-name">` +
        `<span class="rebalance-stock-name-text">${name}</span>` +
        fitTierBadgeForCode(rawCode, { escapeHtml: escapeText }) +
        oosBadge +
        sentPriorBadge +
        marketPriorBadge +
        `</span>` +
        `<span class="rebalance-stock-code">${code}</span>` +
        `</span>` +
        `</div>` +
        `<div class="num rebalance-prev-close" role="cell" title="上一交易日收盘价">${escapeText(
          prevCloseText
        )}</div>` +
        `<div class="num rebalance-price" role="cell" title="最新成交价">${escapeText(
          priceText
        )}</div>` +
        `<div class="num rebalance-chg ${chgCls}" role="cell" title="相对昨收">${escapeText(
          chgText
        )}</div>` +
        `<div class="num rebalance-score-eod paper-hold-score watching-score-eod has-tip ${scoreCls(
          scoreEod
        )}" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="eod" ` +
        `title="${escapeText(scoreEodTitle)}">${escapeText(scoreEodShown)}</div>` +
        `<div class="num rebalance-score-tau paper-hold-score watching-score-tau has-tip ${scoreCls(
          scoreTau
        )}" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="tau" ` +
        `title="${escapeText(scoreTauTitle)}">${escapeText(scoreTauShown)}</div>` +
        `<div class="num rebalance-score-on paper-hold-score watching-score-on has-tip ${scoreCls(
          scoreOn
        )}" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="on" ` +
        `title="${escapeText(scoreOnTitle)}">${escapeText(scoreOnShown)}</div>` +
        `<div class="num rebalance-score-fuse rebalance-score paper-hold-score has-tip ${scoreCls(
          scoreFuse
        )}" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="fuse" ` +
        `title="${escapeText(scoreFuseTitle)}">${escapeText(scoreFuseShown)}</div>` +
        `<div class="num rebalance-score-oo-rank paper-hold-score has-tip" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="oo_rank" ` +
        `title="${escapeText(ooRankTitle)}">${escapeText(ooRank.text)}</div>` +
        `<div class="num rebalance-score-tc-rank paper-hold-score has-tip" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="tc_rank" ` +
        `title="${escapeText(tcRankTitle)}">${escapeText(tcRank.text)}</div>` +
        `<div class="num rebalance-shares" role="cell">` +
        `<span class="rebalance-shares-old">${escapeText(
          String(
            oldSh != null && Number.isFinite(oldSh)
              ? Math.round(oldSh)
              : "—"
          )
        )}</span>` +
        `<span class="rebalance-shares-arrow">→</span>` +
        `<span class="rebalance-shares-new">${escapeText(
          String(
            newSh != null && Number.isFinite(newSh)
              ? Math.round(newSh)
              : "—"
          )
        )}</span>` +
        `</div>` +
        `<div class="num rebalance-change ${changeCls}" role="cell">${escapeText(changeText)}</div>` +
        `<div class="rebalance-decision" role="cell"><span class="rebalance-decision-tag ${decisionTagClass}` +
        `${decisionTip ? " has-tip" : ""}"` +
        `${
          decisionTip ? ` data-tip="${escapeText(decisionTip)}"` : ""
        }>${escapeText(decision || "—")}</span></div>` +
        `</div>` +
        (hasDetail
          ? `<div class="rebalance-detail-row" data-detail-for="${idx}" hidden>${buildScoreDetail(
              r
            )}</div>`
          : "")
      );
    })
    .join("");

  container.innerHTML =
    `<div class="rebalance-table-scroll">` +
    `<div class="rebalance-table" role="table">` +
    `<div class="rebalance-table-head" role="row">` +
    `<div class="rebalance-th" role="columnheader">股票</div>` +
    `<div class="rebalance-th num" role="columnheader" title="上一交易日收盘价">昨收</div>` +
    `<div class="rebalance-th num" role="columnheader" title="最新成交价">现价</div>` +
    `<div class="rebalance-th num" role="columnheader" title="相对昨收的当日涨跌幅">涨跌</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_oo · 隔夜主轴">y_oo</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_τc · price(τ)→close">y_τc</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_co · close→次日开">y_co</div>` +
    `<div class="rebalance-th num" role="columnheader" title="fuse · w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)；真实=open[T+1]/price(τ)−1">fuse</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_oo_rank 当日观察池截面名次 · 1=相对分最高 · 悬停看因子组成 · 过入场后作买序；与 τc_rank 共用 rank 入场">oo_rank</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_τc_rank 当日截面名次 · 1=相对分最高 · 悬停看因子组成 · 与 oo_rank 共用 rank 入场；不进 fuse / 不改买序">τc_rank</div>` +
    `<div class="rebalance-th num" role="columnheader">股数</div>` +
    `<div class="rebalance-th num" role="columnheader">变动</div>` +
    `<div class="rebalance-th rebalance-th-decision" role="columnheader" title="悬停看原因">决策</div>` +
    `</div>` +
    `<div class="rebalance-table-body" role="rowgroup">${rows}</div>` +
    `</div></div>`;

  container.querySelectorAll(".rebalance-row[data-detail-idx]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("[data-score-detail], .rebalance-decision-tag[data-tip]")) {
        return;
      }
      const idx = tr.dataset.detailIdx;
      const detailRow = container.querySelector(`[data-detail-for="${idx}"]`);
      if (!detailRow) return;
      const icon = tr.querySelector(".rebalance-expand-icon");
      const open = detailRow.hidden;
      detailRow.hidden = !open;
      if (open) {
        tr.classList.add("rebalance-row-expanded");
        if (icon) icon.textContent = "⌄";
      } else {
        tr.classList.remove("rebalance-row-expanded");
        if (icon) icon.textContent = "›";
      }
    });
  });

  container.querySelectorAll(".rebalance-decision-tag[data-tip]").forEach((el) => {
    el.addEventListener("mouseenter", (e) => {
      e.stopPropagation();
      showPlainTooltip(el, el.getAttribute("data-tip") || "");
    });
    el.addEventListener("mouseleave", () => hideScoreTooltip());
    el.addEventListener("click", (e) => e.stopPropagation());
  });

  scoreTips.bindHost(container, {
    scoreSelector: "[data-score-detail]",
  });
  void ensureFitTierMap(container);

  paintRebalancePreviewChips({
    report,
    skips,
    cashImpact,
  });
  section.hidden = false;
}


  return { dismissRebalancePreview, emptyReasonLabel, renderRebalanceReport };
}
