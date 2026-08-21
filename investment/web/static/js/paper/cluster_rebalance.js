/** Paper · 分池状态与调仓预演/落账（从 paper.js 抽出）。 */

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

  let followClusterActive = false;
  let followClusterPreviewPending = false;

function fmtFollowBookTs(iso) {
  if (!iso) return "";
  let raw = String(iso).trim();
  if (!raw) return "";
  if (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(raw) &&
    !/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)
  ) {
    raw = raw.replace(/\.\d+$/, "") + "Z";
  }
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    return raw.replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
  }
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
    d.getMinutes()
  )}`;
}

function formatFollowClusterStatus(data) {
  const cs = (data && data.cluster_scoring) || {};
  const act = (data && data.active) || {};
  const book = (data && data.book) || {};
  const mode = cs.mode || "off";
  const bookTs = fmtFollowBookTs(book.updated_at);
  const maxNames =
    book.max_names != null
      ? book.max_names
      : cs.max_names != null
        ? cs.max_names
        : "—";
  if (!data || !data.success) {
    return {
      active: false,
      text: "分组打分：状态不可用（研究枢纽未接通）",
    };
  }
  if (mode === "off") {
    return {
      active: false,
      text: "分组打分：未启用 · score 用全局权 · 研究枢纽「对照→启用」后生效",
    };
  }
  if (mode === "shadow") {
    return {
      active: false,
      text: `分组打分：对照中 · v${act.version ?? "—"} · 组权分全局排序 · 上限 ${maxNames}${
      bookTs ? ` · 刷簿 ${bookTs}` : ""
    } · 主分仍全局`,
    };
  }
  return {
    active: !!act.exists,
    text: `分组打分：已启用 · v${act.version ?? "—"} · 组权分全局排序 · 簿 ${
      book.name_count ?? "—"
    }/${maxNames}${bookTs ? ` · 刷簿 ${bookTs}` : ""}（ŷ门槛过滤后截断）`,
  };
}

async function refreshFollowClusterStatus() {
  const el = document.getElementById("follow-cluster-status");
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl
    ? setTimeout(() => {
        try {
          ctrl.abort();
        } catch (_) {}
      }, 8000)
    : null;
  try {
    const res = await fetch("/api/quant/cluster-live/status?light=1", {
      signal: ctrl ? ctrl.signal : undefined,
    });
    const data = await res.json().catch(() => ({}));
    const st = formatFollowClusterStatus(data);
    followClusterActive = st.active;
    if (el) el.textContent = st.text;
  } catch (_) {
    followClusterActive = false;
    if (el) el.textContent = "分组打分：加载失败";
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function runClusterPaperRebalance({ dryRun = true } = {}) {
  setPaperBusy(true);
  showProgress(
    1,
    dryRun ? "分池预演（打分）…" : "分池落账（成交）…"
  );
  try {
    const strategy =
      document.getElementById("paper-strategy")?.value || "short_conservative";
    // 不传 top_k：后端按分池簿长持有；limit 仅横截面用，分池路径忽略（勿传 > schema 上限）
    const res = await fetch("/api/paper/rebalance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cluster_mode: true,
        dry_run: !!dryRun,
        strategy,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false || data.ok === false) {
      const detail = data.detail || data.error || res.statusText;
      const msg = Array.isArray(detail)
        ? detail
            .map((d) => {
              if (!d || typeof d !== "object") return String(d);
              const loc = Array.isArray(d.loc)
                ? d.loc.filter((x) => x !== "body").join(".")
                : "";
              const m = d.msg || d.message || JSON.stringify(d);
              return loc ? `${loc}: ${m}` : m;
            })
            .join("；")
        : typeof detail === "string"
          ? detail
          : JSON.stringify(detail);
      throw new Error(msg || "分池调仓失败");
    }
    const sellN = (data.sell_trades || []).length;
    const buyN = (data.buy_trades || []).length;
    const report = data.rebalance_report || [];
    const bookN = data.observation_pool_count ?? data.top_k ?? "—";
    followClusterPreviewPending = !!dryRun;
    const ci = data.cash_impact || null;
    const turnPct = ci && ci.turnover_pct != null ? ci.turnover_pct : null;
    const reused =
      data.book_reused || (data.cluster_pools || {}).from_cache;
    setPaperMetaText(
      (dryRun
        ? reused
          ? "分池预演·复用簿"
          : "分池预演"
        : reused
          ? "分池落账·已复用簿"
          : "分池落账") +
        ` · 目标簿 ${bookN} 只 · top_k=${data.top_k ?? "—"} · 卖 ${sellN} · 买 ${buyN}` +
        (turnPct != null ? ` · 换手 ${turnPct}%` : "") +
        (sellN === 0 && buyN === 0 && data.empty_reason
          ? ` · ${emptyReasonLabel(data.empty_reason)}`
          : "")
    );
    if (dryRun) {
      const opsFromApi = data.ops_report || null;
      const skips = data.risk_budget_skips || [];
      const priorN = skips.filter((s) => s && s.sentiment_prior).length;
      if (priorN > 0) {
        setPaperMetaText(
          ((document.getElementById("follow-meta") || {}).textContent || "") +
            ` · 舆情跳过 ${priorN}`
        );
      }
      renderRebalanceReport(report, {
        preview: true,
        cashImpact: ci,
        riskGate: data.risk_gate || null,
        riskBudgetSkips: skips,
        dualScore: data.dual_score || null,
        emptyReason: data.empty_reason || data.book_empty_reason || null,
        minScore: data.min_score,
        marketContext: data.market_context || null,
        opsReport: opsFromApi
          ? {
              ...opsFromApi,
              note:
                (opsFromApi.note || data.note || "分池预演未写账") +
                ` · 目标=组权分全局排序簿（${bookN} 只）`,
            }
          : {
              strategy_id: strategy,
              cost_model: (data.summary || {}).cost_model || (ci && ci.cost_model),
              note:
                (data.note || "分池预演未写账") +
                ` · 目标=组权分全局排序簿（${bookN} 只）`,
            },
      });
    } else {
      dismissRebalancePreview();
      await loadPaper({ quiet: true });
    }
    showProgress(100, "");
  } finally {
    setPaperBusy(false);
    hideProgress();
  }
}


  return {
    get followClusterActive() {
      return followClusterActive;
    },
    get followClusterPreviewPending() {
      return followClusterPreviewPending;
    },
    setFollowClusterActive(v) {
      followClusterActive = !!v;
    },
    setFollowClusterPreviewPending(v) {
      followClusterPreviewPending = !!v;
    },
    fmtFollowBookTs,
    formatFollowClusterStatus,
    refreshFollowClusterStatus,
    runClusterPaperRebalance,
  };
}
