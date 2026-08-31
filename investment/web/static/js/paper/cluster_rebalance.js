/** Paper · 观察池 path_matrix 预演/落账（Follow 调仓主路径）。 */

import { getDataOfflineOnly } from "../data_offline.js";

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
      dryRun
        ? getDataOfflineOnly()
          ? "观察池矩阵预演（仅本地仓）…"
          : "观察池矩阵预演（可拉远端）…"
        : "矩阵落账（成交）…"
    );
    try {
      const res = await fetch("/api/paper/rebalance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          matrix_mode: true,
          dry_run: !!dryRun,
          offline_only: getDataOfflineOnly(),
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
      const oosEx = ((data.path_matrix || {}).oos_excluded) || 0;
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
            (oosEx > 0 ? ` · OOS过滤 ${oosEx}` : "") +
            (actBits ? ` · ${actBits}` : "") +
            (sellN === 0 && buyN === 0 && data.empty_reason
              ? ` · ${emptyReasonLabel(data.empty_reason)}`
              : "") +
            " · 未写账"
        );
        renderRebalanceReport(report, {
          preview: true,
          // 矩阵预演：买卖已在清单/状态条；成交预估与 path 推迟名单噪音，不下发
          cashImpact: null,
          riskGate: data.risk_gate || null,
          riskBudgetSkips: [],
          dualScore: data.dual_score || null,
          emptyReason: data.empty_reason || null,
          minScore: data.min_score,
          marketContext: data.market_context || null,
          opsReport: null,
          matrixPreview: true,
        });
        const confirmBtn = document.getElementById("paper-rebalance-confirm");
        if (confirmBtn) {
          confirmBtn.hidden = !canConfirm;
          confirmBtn.disabled = !canConfirm;
        }
        const previewNote = document.getElementById("paper-rebalance-preview-note");
        if (previewNote) {
          // 有可确认清单时确认按钮已够；不重复「预演未写账」文案
          if (canConfirm) {
            previewNote.hidden = true;
            previewNote.textContent = "";
          } else {
            previewNote.hidden = false;
            previewNote.textContent = "预演 · 无可执行买卖";
          }
        }
        showProgress(
          100,
          `预演完成 · 观察池 ${poolN} · 已打分 ${scoredN} · 卖 ${sellN} · 买 ${buyN}`
        );
      } else {
        followMatrixPreviewPending = false;
        dismissRebalancePreview();
        await loadPaper({ quiet: true });
        const label = staged
          ? note || "矩阵 · 已挂次日开盘单"
          : note || `矩阵落账 · 卖 ${sellN} · 买 ${buyN}`;
        setPaperMetaText(label);
        showProgress(100, label);
      }
    } catch (err) {
      const msg = String((err && err.message) || err || "预演失败");
      setPaperMetaText(msg);
      const el = document.getElementById("paper-path-matrix-status");
      if (el) {
        el.hidden = false;
        el.classList.remove("is-busy", "is-ok");
        el.classList.add("is-error");
        el.textContent = msg;
      }
      throw err;
    } finally {
      setPaperBusy(false);
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
