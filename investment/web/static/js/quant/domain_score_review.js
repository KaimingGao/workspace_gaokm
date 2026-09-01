/**
 * IC 残差 / 超额对照。账本复盘、校准、τ/nowcast 单日验收 UI 已下线。
 */
export function installScoreReview(ctx) {
  const { setQuantMeta, on } = ctx;

  function setIcStatus(text, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("quant-ic-series-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok);
  }

  async function runYhatResidualShadow() {
    setIcStatus("ŷ 残差对照中…", { busy: true });
    setQuantMeta("ŷ 行业残差 on/off…", { busy: true });
    const res = await fetch("/api/quant/yhat-residual/shadow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        watching_limit: 36,
        top_k: 10,
        prefer_cluster_book: false,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !(data.success || data.ok)) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setIcStatus(`ŷ残差对照失败：${err}`, { error: true });
      setQuantMeta(`ŷ残差对照失败 · ${err}`, { error: true });
      return;
    }
    const cmp = data.compare || {};
    const jac =
      cmp.jaccard_topk != null ? Number(cmp.jaccard_topk).toFixed(2) : "—";
    const sp =
      cmp.spearman_topk_ranks != null
        ? Number(cmp.spearman_topk_ranks).toFixed(2)
        : "—";
    const win = data.winner || "—";
    setIcStatus(
      `ŷ残差对照 · Jaccard ${jac} · 秩相关 ${sp} · ${win} · 源 ${data.source || "—"}`,
      { ok: true }
    );
    setQuantMeta(
      `ŷ残差对照完成 · ${win} · ${data.note || ""} · ${data.promote_hint || "不写盘"}`
    );
  }

  on("quant-yhat-residual-shadow", "click", async (e) => {
    e.preventDefault();
    try {
      await runYhatResidualShadow();
    } catch (err) {
      setIcStatus(`ŷ残差对照失败：${String(err.message || err)}`, { error: true });
    }
  });

  async function runExcessModeShadow() {
    setIcStatus("超额标签对照中…", { busy: true });
    setQuantMeta("绝对 y vs 指数超额 y…", { busy: true });
    const res = await fetch("/api/quant/excess-mode/shadow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        watching_limit: 36,
        horizon_days: 1,
        ridge_lambda: 1.0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !(data.success || data.ok)) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setIcStatus(`超额标签对照失败：${err}`, { error: true });
      setQuantMeta(`超额标签对照失败 · ${err}`, { error: true });
      return;
    }
    const arms = data.arms || {};
    const a = arms.none || {};
    const b = arms.index || {};
    const fmt = (arm) => {
      const o = (arm && arm.oos) || {};
      const ic = o.ic != null ? Number(o.ic).toFixed(2) : "—";
      const hit =
        o.sign_hit != null ? `${(Number(o.sign_hit) * 100).toFixed(0)}%` : "—";
      return `IC ${ic} · 命中 ${hit} · n=${o.n ?? arm.n ?? "—"}`;
    };
    const win = data.winner || "—";
    setIcStatus(
      `超额标签 · 绝对 ${fmt(a)} · 超额 ${fmt(b)} · 胜者 ${win}`,
      { ok: true }
    );
    setQuantMeta(
      `超额标签对照完成 · 胜者 ${win} · ${data.note || ""} · ${data.promote_hint || "不写盘"}`
    );
  }

  on("quant-excess-mode-shadow", "click", async (e) => {
    e.preventDefault();
    try {
      await runExcessModeShadow();
    } catch (err) {
      setIcStatus(`超额标签对照失败：${String(err.message || err)}`, { error: true });
    }
  });

  return {};
}
