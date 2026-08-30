/** Paper · 观察池 path_matrix 预演/落账（Follow 调仓主路径）。 */

/**
 * @param {object} deps
 */
export function createClusterRebalanceController(deps) {
  const {
    setPaperBusy,
    showProgress,
    hideProgress,
    setPaperMetaText,
    emptyReasonLabel,
    renderRebalanceReport,
    dismissRebalancePreview,
    loadPaper,
  } = deps;

  let followMatrixPreviewPending = false;

  async function runWatchingMatrixPreview({ dryRun = true } = {}) {
    setPaperBusy(true);
    showProgress(
      1,
      dryRun ? "观察池矩阵预演（实时算分）…" : "矩阵落账（成交）…"
    );
    try {
      const res = await fetch("/api/paper/rebalance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          matrix_mode: true,
          dry_run: !!dryRun,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false || data.ok === false) {
        const detail = data.detail || data.error || res.statusText;
        throw new Error(
          typeof detail === "string" ? detail : JSON.stringify(detail)
        );
      }
      const sellN = (data.sell_trades || []).length;
      const buyN = (data.buy_trades || []).length;
      const report = data.rebalance_report || [];
      const poolN = data.observation_pool_count ?? "—";
      const scoredN = data.scored_count ?? "—";
      const ci = data.cash_impact || null;
      const turnPct = ci && ci.turnover_pct != null ? ci.turnover_pct : null;
      const byAct = ((data.path_matrix || {}).by_action) || {};
      const actBits = Object.keys(byAct)
        .map((k) => `${k}:${byAct[k]}`)
        .slice(0, 6)
        .join(" ");
      const note = String(data.note || "").trim();
      const staged =
        String(data.fill_action || "") === "staged" ||
        /挂.*开盘|收盘后不成交/.test(note);

      if (dryRun) {
        followMatrixPreviewPending = true;
        const canConfirm = sellN + buyN > 0 && data.confirm_supported !== false;
        setPaperMetaText(
          `矩阵预演 · 观察池 ${poolN} · 已打分 ${scoredN} · 卖 ${sellN} · 买 ${buyN}` +
            (turnPct != null ? ` · 换手 ${turnPct}%` : "") +
            (actBits ? ` · ${actBits}` : "") +
            (sellN === 0 && buyN === 0 && data.empty_reason
              ? ` · ${emptyReasonLabel(data.empty_reason)}`
              : "") +
            " · 未写账"
        );
        renderRebalanceReport(report, {
          preview: true,
          cashImpact: ci,
          riskGate: data.risk_gate || null,
          riskBudgetSkips: data.risk_budget_skips || [],
          dualScore: data.dual_score || null,
          emptyReason: data.empty_reason || null,
          minScore: data.min_score,
          marketContext: data.market_context || null,
          opsReport: {
            ...(data.ops_report || {}),
            note: data.note || "观察池 path_matrix 预演",
          },
        });
        const confirmBtn = document.getElementById("paper-rebalance-confirm");
        if (confirmBtn) {
          confirmBtn.hidden = !canConfirm;
          confirmBtn.disabled = !canConfirm;
        }
        const previewNote = document.getElementById("paper-rebalance-preview-note");
        if (previewNote) {
          previewNote.hidden = false;
          previewNote.textContent = canConfirm
            ? "观察池 + path_matrix 预演。确认后按同一路径落账。"
            : "观察池 + path_matrix 预演 · 无可执行买卖";
        }
      } else {
        followMatrixPreviewPending = false;
        dismissRebalancePreview();
        await loadPaper({ quiet: true });
        const label = staged
          ? note || "矩阵 · 已挂次日开盘单"
          : note || `矩阵落账 · 卖 ${sellN} · 买 ${buyN}`;
        setPaperMetaText(label);
      }
      showProgress(100, "");
    } finally {
      setPaperBusy(false);
      hideProgress();
    }
  }

  return {
    get followMatrixPreviewPending() {
      return followMatrixPreviewPending;
    },
    setFollowMatrixPreviewPending(v) {
      followMatrixPreviewPending = !!v;
    },
    /** @deprecated 分池调仓已移除；保留空实现避免旧调用炸 */
    get followClusterActive() {
      return false;
    },
    get followClusterPreviewPending() {
      return false;
    },
    setFollowClusterActive() {},
    setFollowClusterPreviewPending() {},
    refreshFollowClusterStatus: async () => {},
    runWatchingMatrixPreview,
  };
}
