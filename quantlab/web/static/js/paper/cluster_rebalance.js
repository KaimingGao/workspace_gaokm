/** Paper · 观察池 09:30 开盘预估分复现（不含开仓/加仓，不写账）。 */

import {
  ensureSessionOpenL2,
  isSessionOpenMaterializeRequired,
} from "./session_open_materialize.js";

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

  async function postOpenReplay() {
    const res = await fetch("/api/paper/rebalance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run: true,
        open_replay: true,
        // ŷ / 日K·5m 只读本地仓；补数在数据中心
        offline_only: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
  }

  async function runWatchingMatrixPreview() {
    setPaperBusy(true);
    showProgress(1, "09:30 开盘复现…");
    try {
      let { res, data } = await postOpenReplay();
      if (
        (!res.ok || data.success === false || data.ok === false) &&
        isSessionOpenMaterializeRequired(data)
      ) {
        const filled = await ensureSessionOpenL2({
          fromError: data,
          onProgress: (pct, msg) => showProgress(pct, msg),
        });
        if (!filled.ok) {
          throw new Error(filled.error || "补齐当日开盘 L2 失败");
        }
        showProgress(90, "开盘 L2 已补齐 · 重新 09:30 复现…");
        ({ res, data } = await postOpenReplay());
      }
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
      const pit = data.pit || {};
      const pitBits = [];
      if (pit.open_source === "quote" || pit.pit_phase === "provisional") {
        pitBits.push("临时立方·行情今开");
      } else if (pit.open_source === "daily" || pit.pit_phase === "final") {
        pitBits.push("终态·日线今开");
      }
      if (pit.n_cube_codes != null) pitBits.push(`截面${pit.n_cube_codes}`);
      if (pit.missing_open_n != null && Number(pit.missing_open_n) > 0) {
        pitBits.push(`缺开${pit.missing_open_n}`);
      }
      if (pit.membership_drift && pit.membership_drift.note) {
        pitBits.push(String(pit.membership_drift.note));
      }
      setPaperMetaText(
        `09:30 复现 · 观察池 ${poolN} · 已打分 ${scoredN} · 参考卖 ${sellN}` +
          (turnPct != null ? ` · 换手 ${turnPct}%` : "") +
          (oosEx > 0 ? ` · OOS过滤 ${oosEx}` : "") +
          (actBits ? ` · ${actBits}` : "") +
          (pitBits.length ? ` · ${pitBits.join(" · ")}` : "") +
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
