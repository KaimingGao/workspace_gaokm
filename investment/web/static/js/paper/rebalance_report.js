/** Paper · 调仓预演报告渲染（从 paper.js 抽出）。 */

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
    resolveTradeScore,
    resolveEodScore,
    resolveTauScore,
    resolveOnScore,
    resolveNowcastScore,
    Y_EOD_TITLE,
    Y_TAU_TITLE,
    Y_ON_TITLE,
    Y_NOWCAST_TITLE,
    TRADE_TITLE,
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
    scoreTips,
    showPlainTooltip,
    hideScoreTooltip,
    metricCls,
    setFollowClusterPreviewPending,
  } = deps;

/** 确认落账或点 × 后收起预演区（持仓/流水另由 loadPaper 刷新）。 */
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
  const nextEl = document.getElementById("paper-validate-next");
  if (nextEl) {
    nextEl.hidden = true;
    nextEl.innerHTML = "";
  }
  if (typeof setFollowClusterPreviewPending === "function") setFollowClusterPreviewPending(false);
}

function emptyReasonLabel(code) {
  const map = {
    empty_ranking: "目标簿为空（无人过闸或未建簿）",
    all_below_eod_floor: "全部低于 EOD 买入门槛",
    buys_blocked: "买入被风控/规则拦截",
    risk_blocked: "风控整批拦截",
    no_executable_changes: "无可执行买卖",
    book_constraints_empty: "簿约束装填后为空",
    no_mapped_scores: "无可用评分映射",
    empty_book: "选股簿为空",
  };
  const k = String(code || "").trim();
  if (!k) return "";
  return map[k] || k;
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
    marketContext = null,
  } = {}
) {
  const section = document.getElementById("paper-rebalance-section");
  const container = document.getElementById("paper-rebalance-report");
  const cashEl = document.getElementById("paper-rebalance-cash");
  const previewNote = document.getElementById("paper-rebalance-preview-note");
  const confirmBtn = document.getElementById("paper-rebalance-confirm");
  if (!section || !container) return;

  if (opsReport) renderOpsReport(opsReport, { forceShow: true });

  // 按分数降序展示（无分排后）
  report = Array.isArray(report)
    ? [...report].sort((a, b) => {
        const as = resolveTradeScore(a);
        const bs = resolveTradeScore(b);
        if (as == null && bs == null) return 0;
        if (as == null) return 1;
        if (bs == null) return -1;
        return bs - as;
      })
    : report;
  const skips = Array.isArray(riskBudgetSkips) ? riskBudgetSkips : [];
  const hasSkips = skips.length > 0;
  const hasRisk =
    riskGate &&
    ((Array.isArray(riskGate.blocks) && riskGate.blocks.length > 0) ||
      (Array.isArray(riskGate.warnings) && riskGate.warnings.length > 0));
  const hasCash =
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
    previewNote.hidden = !preview && !hasEmptyReason;
    let note = preview ? "预演未成交 · 确认后才会改仓" : "";
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
      const floorHint =
        minScore != null && Number.isFinite(Number(minScore))
          ? ` · ŷ_EOD≥${Number(minScore)}%`
          : "";
      note += (note ? " · " : "") + emptyReasonText + floorHint;
    }
    previewNote.textContent = note;
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
      const net = Number(ci.net_cash_flow);
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
        emptyHtml =
          `<div class="paper-rebalance-risk" role="status">` +
          `<p class="paper-rebalance-risk-title is-warn">空仓/无变动原因</p>` +
          `<ul><li>${escapeText(emptyReasonText)}` +
          (minScore != null && Number.isFinite(Number(minScore))
            ? ` · 当前买入门槛 ŷ_EOD≥${escapeText(String(minScore))}%`
            : "") +
          `</li></ul></div>`;
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
              ? ` · ŷ_EOD ${escapeText(String(s.eod_gate_score))}`
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
      cashEl.hidden = false;
      cashEl.innerHTML =
        (marketCtxHtml || "") +
        (hasCash
          ? `<div class="paper-rebalance-cash-head">` +
            `<span class="paper-rebalance-cash-label">资金影响</span>` +
            `<span class="paper-rebalance-cash-note">${costLabel}</span>` +
            `</div>` +
            `<dl class="paper-rebalance-cash-grid" aria-label="资金影响">` +
            `<div><dt>调仓前现金</dt><dd>${fmt(ci.cash_before)}</dd></div>` +
            `<div><dt>预计买入</dt><dd>${fmt(ci.buy_amount)}</dd></div>` +
            `<div><dt>预计卖出</dt><dd>${fmt(ci.sell_amount)}</dd></div>` +
            `<div><dt>净现金流</dt><dd class="${netCls}">${netText}</dd></div>` +
            `<div><dt>调仓后现金</dt><dd>${fmt(ci.cash_after)}</dd></div>` +
            `<div><dt>持仓只数</dt><dd>${escapeText(
              String(ci.position_count_before ?? "—")
            )} → ${escapeText(String(ci.position_count_after ?? "—"))}</dd></div>` +
            (ci.turnover_pct != null
              ? `<div title="双边换手=(买额+卖额)/2/净值"><dt>换手</dt><dd${
                  ci.turnover_over_limit || ci.turnover_capped
                    ? ' class="down"'
                    : ""
                }>${escapeText(String(ci.turnover_pct))}%${
                  ci.max_turnover_pct != null
                    ? ` / ${escapeText(String(ci.max_turnover_pct))}%`
                    : ""
                }${ci.turnover_capped ? " · 已截断" : ""}</dd></div>`
              : "") +
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
    container.innerHTML = hasEmptyReason
      ? `<p class="follow-ops-note">${escapeText(emptyReasonText)}${
          minScore != null && Number.isFinite(Number(minScore))
            ? ` · 门槛 ŷ_EOD≥${escapeText(String(minScore))}%`
            : ""
        }。</p>`
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
      predicted_score_nowcast: r.predicted_score_nowcast,
      nowcast_vs: r.nowcast_vs || null,
      nowcast_as_of: r.nowcast_as_of || null,
      nowcast_K: r.nowcast_K,
      nowcast_q: r.nowcast_q,
      nowcast_x_prior: r.nowcast_x_prior,
      predicted_score_eod: r.predicted_score_eod,
      predicted_score_tau: r.predicted_score_tau,
      predicted_score_on: r.predicted_score_on,
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
      predicted_score_tau_delta: r.predicted_score_tau_delta,
      dual_score_window: r.dual_score_window || null,
      predicted_score_cal: r.predicted_score_cal,
      predicted_score_eod_rem_cal: r.predicted_score_eod_rem_cal,
      predicted_score_tau_cal: r.predicted_score_tau_cal,
      predicted_score_blend_cal: r.predicted_score_blend_cal,
      score_calibration_applied: !!r.score_calibration_applied,
      score_calibration_enabled: !!r.score_calibration_enabled,
      score_calibration_eod_oor: !!r.score_calibration_eod_oor,
      score_calibration_eod_rem_oor: !!r.score_calibration_eod_rem_oor,
      score_calibration_tau_oor: !!r.score_calibration_tau_oor,
      score_calibration_note: r.score_calibration_note || null,
      score_calibration_partial: r.score_calibration_partial || null,
      realized_t1_to_tau: r.realized_t1_to_tau,
      y_spec_tau: r.y_spec_tau,
      features_tau: r.features_tau,
      formula_terms_tau: r.formula_terms_tau || r.score_formula_terms_tau,
      score_formula_tau: r.score_formula_tau,
      factor_coefficients_tau: r.factor_coefficients_tau,
      formula_terms_on: r.formula_terms_on || r.score_formula_terms_on,
      features_on: r.features_on || null,
      y_spec_on: r.y_spec_on || null,
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
    };
  }

  function buildScoreDetail(r) {
    const hasCal =
      r.predicted_score_cal != null ||
      r.predicted_score_eod_rem_cal != null ||
      r.predicted_score_tau_cal != null ||
      r.predicted_score_blend_cal != null ||
      r.score_calibration_note;
    if (
      !r.reasons &&
      !r.score_formula &&
      !r.hard_reject &&
      !r.weight_source &&
      !r.cluster_label &&
      !r.return_model_source &&
      !(r.factor_coefficients && Object.keys(r.factor_coefficients || {}).length) &&
      !(r.score_formula_terms && (r.score_formula_terms.terms || []).length) &&
      !hasCal
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
        `<div class="score-section-title">ŷ_EOD 公式</div>` +
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

  const rows = report
    .map((r, idx) => {
      const cls = decisionClass[r.decision] || "rebalance-hold";
      let tradeScore = resolveTradeScore(r);
      let scoreText = tradeScore != null ? fmtTableScore(r, tradeScore) : "—";
      if (scoreText === "—" && r.hard_reject) {
        scoreText = "拒";
      }
      const belowMin = !!r.below_min_score;
      const scoreShown =
        scoreText !== "—" && scoreText !== "拒" && belowMin
          ? `${scoreText}↓`
          : scoreText;
      let scoreClass = scoreCls(tradeScore);
      if (belowMin) scoreClass += " below-min";
      if (r.hard_reject) scoreClass += " score-reject";
      const singleHead =
        r.dual_score_single_head === true ||
        String(r.dual_score_head || "") === "single_eod" ||
        String(r.dual_score_head || "") === "single_tau";
      if (singleHead) scoreClass += " score-single-head";
      let scoreTip = "";
      if (r.hard_reject) {
        scoreTip = String(r.reject_reason || "硬拒绝 · 无收益分");
      } else if (isHeuristicScoreScale(r)) {
        scoreTip =
          tradeScore != null
            ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
            : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)";
      } else if (singleHead) {
        scoreTip = `ŷ_trade 单头降级（${String(r.dual_score_head || "single")}）· 悬停看详情`;
      } else if (r.y_check && String(r.y_check) !== "ok") {
        scoreTip = `Y·EOD 校验 ${String(r.y_check)} · 悬停看分歧/σ`;
      } else if (belowMin) {
        scoreTip = "低于 ŷ_EOD 门槛";
      } else {
        scoreTip = TRADE_TITLE;
      }
      const head = String(r.dual_score_head || "");
      const singleHeadBadge = singleHead
        ? `<span class="watching-single-head-badge" title="${escapeText(
            head === "single_tau"
              ? "ŷ_trade 单头降级：仅 ŷ_τ（缺 ŷ_EOD）· 与双头票不同量纲"
              : head === "single_eod"
                ? "ŷ_trade 单头降级：仅 ŷ_EOD（缺 ŷ_τ）· 与双头票不同量纲"
                : "ŷ_trade 单头降级 · 与双头票不同量纲"
          )}">单</span>`
        : "";
      const yCheck = String(r.y_check || "");
      const yCheckBadge =
        yCheck && yCheck !== "ok"
          ? `<span class="watching-y-check-badge is-${escapeText(
              yCheck
            )}" title="${escapeText(
              yCheck === "conflict"
                ? "Y·EOD 校验：双头分歧"
                : yCheck === "low_conf"
                  ? "Y·EOD 校验：低置信"
                  : `Y·EOD 校验：${yCheck}`
            )}">${escapeText(
              yCheck === "conflict"
                ? "歧"
                : yCheck === "low_conf"
                  ? "弱"
                  : yCheck === "missing_tau"
                    ? "缺τ"
                    : "校"
            )}</span>`
          : "";
      const scoreEod = resolveEodScore(r);
      const scoreEodShown =
        scoreEod != null ? fmtTableScore(r, scoreEod) : "—";
      const scoreEodTitle = scoreEod == null ? "暂无 ŷ_EOD" : Y_EOD_TITLE;
      const scoreTau = resolveTauScore(r);
      const scoreTauShown =
        scoreTau != null ? fmtTableScore(r, scoreTau) : "—";
      const scoreTauTitle = scoreTau == null ? "暂无 ŷ_τ" : Y_TAU_TITLE;
      const scoreOn = resolveOnScore(r);
      const scoreOnShown =
        scoreOn != null ? fmtTableScore(r, scoreOn) : "—";
      const scoreOnTitle = scoreOn == null ? "暂无 ŷ_ON" : Y_ON_TITLE;
      const scoreNowcast = resolveNowcastScore(r);
      const scoreNowcastShown =
        scoreNowcast != null ? fmtTableScore(r, scoreNowcast) : "—";
      const scoreNowcastTitle =
        scoreNowcast == null ? "暂无 nowcast · 有 ŷ_EOD 与 ŷ_τ 后可见" : Y_NOWCAST_TITLE;
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
          ? `${reasonText}（强看空先验 · 不改 ŷ）；点「确认调仓」后才成交`
          : "舆情先验 · 强看空缩仓（不改 ŷ）；点「确认调仓」后才成交";
      } else if (
        isMarketPrior &&
        (decision.includes("减仓") || decision.includes("卖出"))
      ) {
        decisionTip = reasonText
          ? `${reasonText}（M prior 缩仓 · 不改 ŷ）；点「确认调仓」后才成交`
          : "市场 prior · M 层缩仓（不改 ŷ）；点「确认调仓」后才成交";
      } else if (decision.includes("止损卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认调仓」后才成交`
          : "止损规则触发，预演清仓；点「确认调仓」后才成交";
      } else if (decision.includes("超时卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认调仓」后才成交`
          : "持有超时且趋势向下，预演清仓；点「确认调仓」后才成交";
      } else if (decision.includes("跳过")) {
        decisionTip = isMarketPrior
          ? reasonText
            ? `${reasonText}；点「确认调仓」后仍不会新开仓`
            : "市场 prior · 跳过新开仓；点「确认调仓」后仍不会买入"
          : isPrior
          ? reasonText
            ? `${reasonText}；点「确认调仓」后仍不会新开仓`
            : "舆情先验 · 跳过新开仓；点「确认调仓」后仍不会买入"
          : reasonText || "风险预算（单票/行业上限）未买入";
      } else if (decision.includes("减仓")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认调仓」后才成交`
          : "预演减仓；点「确认调仓」后才成交";
      } else if (decision.includes("卖出")) {
        decisionTip = reasonText
          ? `${reasonText}；点「确认调仓」后才成交`
          : "分池卖出：ŷ_trade 低于卖出门槛（min_hold）；点「确认调仓」后才成交";
      } else if (reasonText) {
        decisionTip = reasonText;
      }

      const name = escapeText(r.stock_name || r.stock_code || "");
      const code = escapeText(r.stock_code || "");
      const chgPct = r.change_pct;
      const chgText = fmtPct(chgPct, { signed: true });
      const chgCls = metricCls(chgPct);
      const prevCloseText = rebalancePrevCloseText(r);
      const priceText = fmtRebalancePx(r.price);
      const inBook = !!(r.in_book || r.inBook);
      const oosFailed =
        !!r.oos_failed ||
        !!r.oosFailed ||
        isHeuristicScoreScale(r) ||
        String(r.return_model_source || "").startsWith("oos_failed");
      const bookBadge = inBook
        ? `<span class="watching-book-badge" title="分池目标簿">簿</span>`
        : "";
      const oosBadge = oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      const sentPriorBadge =
        isPrior &&
        (decision.includes("减仓") ||
          decision.includes("卖出") ||
          decision.includes("跳过"))
          ? `<span class="watching-sent-badge is-bear" title="舆情 prior · 不改 ŷ">S</span>`
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
        }${inBook ? " is-cluster-book" : ""}" role="row" ` +
        `data-detail-idx="${idx}"` +
        `${hasDetail ? ' title="点击展开评分明细"' : ""}>` +
        `<div class="rebalance-stock" role="cell">` +
        expandIcon +
        `<span class="rebalance-stock-main">` +
        `<span class="rebalance-stock-name">` +
        `<span class="rebalance-stock-name-text">${name}</span>` +
        bookBadge +
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
        `<div class="num rebalance-score paper-hold-score has-tip ${scoreClass}" ` +
        `role="cell" data-score-detail="${tipDetailJson}" data-score-tip="trade" ` +
        `title="${escapeText(scoreTip)}">${escapeText(scoreShown)}${singleHeadBadge}${yCheckBadge}</div>` +
        `<div class="num rebalance-score-nowcast paper-hold-score watching-score-nowcast has-tip ${scoreCls(
          scoreNowcast
        )}" role="cell" ` +
        `data-score-detail="${tipDetailJson}" data-score-tip="nowcast" ` +
        `title="${escapeText(scoreNowcastTitle)}">${escapeText(scoreNowcastShown)}</div>` +
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
    `<div class="rebalance-th num" role="columnheader" title="ŷ_EOD · 隔夜主轴">y_eod</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_τ · τ→收盘剩余">y_τ</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_ON · open 链旁路">y_on</div>` +
    `<div class="rebalance-th num" role="columnheader" title="ŷ_trade · 排序/卖门槛">y_trade</div>` +
    `<div class="rebalance-th num" role="columnheader" title="nowcast（nc）· 对照昨收">y_nc</div>` +
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
    scoreSelector: ".rebalance-score[data-score-detail], .rebalance-score-cal[data-score-detail]",
  });

  section.hidden = false;
}


  return { dismissRebalancePreview, emptyReasonLabel, renderRebalanceReport };
}
