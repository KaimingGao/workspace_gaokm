/** Paper · 观察池 09:30 开盘预估分复现（不含开仓/加仓，不写账）。 */

import { getDataOfflineOnly } from "../data_offline.js";
import { ensureWarehouseTopup } from "../data_warehouse_topup.js";
import { BARS_WATCHING_LIMIT } from "../quant/params.js";

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

  async function runWatchingMatrixPreview() {
    setPaperBusy(true);
    const allowTopup = !getDataOfflineOnly();
    showProgress(
      1,
      allowTopup
        ? "09:30 开盘复现（增量补齐后只读）…"
        : "09:30 开盘复现（仅本地仓）…"
    );
    try {
      if (allowTopup) {
        showProgress(1, "增量补齐观察池日K·5m…");
        try {
          await ensureWarehouseTopup({
            force: false,
            watchingLimit: BARS_WATCHING_LIMIT,
            onStatus: (msg) => showProgress(1, msg || "增量补齐…"),
          });
        } catch (topupErr) {
          setPaperMetaText(
            String((topupErr && topupErr.message) || topupErr || "增量补齐失败")
          );
          /* 仍继续只读预演，便于看到缺仓拒分 */
        }
        showProgress(1, "09:30 开盘复现（读已物化特征）…");
      }
      const res = await fetch("/api/paper/rebalance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dry_run: true,
          open_replay: true,
          // ŷ / 日K·5m 永远只读本地；写仓已由 ensureWarehouseTopup 显式完成
          offline_only: true,
        }),
      });      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false || data.ok === false) {
        const detail = data.detail || data.error || res.statusText;
        throw new Error(
          typeof detail === "string" ? detail : JSON.stringify(detail)
        );
      }
      const sellN = (data.sell_trades || []).length;
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

      followMatrixPreviewPending = false;
      setPaperMetaText(
        `09:30 复现 · 观察池 ${poolN} · 已打分 ${scoredN} · 参考卖 ${sellN}` +
          (turnPct != null ? ` · 换手 ${turnPct}%` : "") +
          (oosEx > 0 ? ` · OOS过滤 ${oosEx}` : "") +
          (actBits ? ` · ${actBits}` : "") +
          (sellN === 0 && data.empty_reason
            ? ` · ${emptyReasonLabel(data.empty_reason)}`
            : "") +
          " · 不含开仓/加仓 · 未写账"
      );
      renderRebalanceReport(report, {
        preview: true,
        cashImpact: null,
        riskGate: null,
        riskBudgetSkips: [],
        dualScore: null,
        emptyReason: null,
        emptyDetail: null,
        minScore: data.min_score,
        fuseEnter: data.fuse_enter ?? data.min_score,
        marketContext: null,
        opsReport: null,
        matrixPreview: true,
      });
      const previewNote = document.getElementById("paper-rebalance-preview-note");
      if (previewNote) {
        previewNote.hidden = true;
        previewNote.textContent = "";
      }
      showProgress(
        100,
        `09:30 复现完成 · 观察池 ${poolN} · 已打分 ${scoredN} · 参考卖 ${sellN}`
      );
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
    runWatchingMatrixPreview,
  };
}
