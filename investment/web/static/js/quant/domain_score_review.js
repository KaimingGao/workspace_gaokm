/**
 * 昨日复盘：ŷ 方向 vs 前瞻收益。
 */
import { paintYhatScatter, paintHitSparkline } from "./yhat_viz.js";
import { syncOverviewFromScoreReview, setProStatusChip } from "./factor_corr_ui.js";

export function installScoreReview(ctx) {
  const { els, escapeHtml, setQuantMeta, on } = ctx;
  const esc = escapeHtml;

  function tagLabel(tag) {
    const map = {
      hit: "命中",
      factor_fade: "因子失效",
      idiosyncratic: "个股特异",
      model_tilt: "模型偏置",
      data_thin: "数据不足",
      no_direction: "无方向",
    };
    return map[tag] || tag || "—";
  }

  function readAsOf() {
    const el = document.getElementById("quant-score-review-asof");
    const v = el && el.value ? String(el.value).trim() : "";
    return v || null;
  }

  function readHorizon() {
    const el = document.getElementById("quant-score-review-horizon");
    const n = Number(el && el.value);
    return Number.isFinite(n) && n >= 1 ? n : 3;
  }

  function setStatus(text, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("quant-score-review-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok);
  }

  function syncHorizonSelect(horizon) {
    const el = document.getElementById("quant-score-review-horizon");
    if (!el || horizon == null) return;
    const v = String(horizon);
    if (el.value !== v) el.value = v;
  }

  function renderSummary(data) {
    const box = document.getElementById("quant-score-review-summary");
    if (!box) return;
    if (data.empty) {
      box.innerHTML = `<p class="quant-attr-note">${esc(
        data.note || "无账本"
      )}</p>`;
      syncOverviewFromScoreReview(data);
      setProStatusChip("quant-pro-review-status", "warn", "无账本");
      return;
    }
    const s = data.summary || {};
    const hit =
      s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
    const fb =
      data.horizon_fallback_from != null
        ? `<p class="quant-attr-note">h=${esc(
            String(data.horizon_fallback_from)
          )} 实现收益未齐 · 已改用 h=${esc(
            String(data.horizon_days ?? 1)
          )}</p>`
        : "";
    const thinNote =
      s.hit_rate == null && Number(s.data_thin || 0) > 0
        ? `<p class="quant-attr-note">${esc(
            s.blame_line ||
              "薄样本：日线未覆盖前瞻收益。可改小 Horizon / 更早决策日 / 刷新日线后回填"
          )}</p>`
        : "";
    box.innerHTML =
      `<div class="quant-metric-strip">` +
      `<span>as_of <b>${esc(data.as_of || "—")}</b></span>` +
      `<span>h=${esc(String(data.horizon_days ?? "—"))}</span>` +
      `<span>命中 <b>${esc(hit)}</b> (${esc(String(s.hits ?? 0))}/${esc(
        String(s.n_scored ?? 0)
      )})</span>` +
      `<span>错多 ${esc(String(s.wrong_long ?? 0))}</span>` +
      `<span>错空 ${esc(String(s.wrong_short ?? 0))}</span>` +
      `<span>账本 ${esc(String(data.n_ledger ?? 0))} 只</span>` +
      (Number(s.data_thin || 0) > 0
        ? `<span>薄样本 ${esc(String(s.data_thin))}</span>`
        : "") +
      `</div>` +
      fb +
      thinNote +
      (s.blame_line && s.hit_rate != null
        ? `<p class="quant-attr-note">${esc(s.blame_line)}</p>`
        : "") +
      (data.refit_hint
        ? `<p class="quant-attr-note">${esc(data.refit_hint)}</p>`
        : "") +
      (data.note && s.hit_rate == null
        ? `<p class="quant-attr-note">${esc(String(data.note).slice(0, 160))}</p>`
        : "");
    syncOverviewFromScoreReview(data);
    if (s.hit_rate != null && Number.isFinite(Number(s.hit_rate))) {
      const pct = Number(s.hit_rate) * 100;
      setProStatusChip(
        "quant-pro-review-status",
        pct >= 55 ? "ok" : "warn",
        `命中 ${pct.toFixed(0)}%`
      );
    } else {
      const thin = Number(s.data_thin || 0);
      setProStatusChip(
        "quant-pro-review-status",
        "warn",
        thin > 0 ? `薄样本 ${thin}` : s.blame_line || "样本不足"
      );
    }
  }

  function setVizMeta(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text || "";
  }

  function renderScatter(data) {
    const wrap = document.getElementById("quant-score-review-scatter-wrap");
    const canvas = document.getElementById("quant-score-review-scatter");
    if (!wrap || !canvas) return;
    const pts = data.scored_rows || [];
    wrap.hidden = false;
    if (data.empty || pts.length < 2) {
      canvas.hidden = true;
      setVizMeta(
        "quant-score-review-scatter-meta",
        data.empty ? "无账本样本" : "样本不足（需 ≥2 点）"
      );
      return;
    }
    canvas.hidden = false;
    requestAnimationFrame(() => {
      const pack = paintYhatScatter(canvas, pts);
      if (pack && pack.n) {
        const hit =
          pack.hit_rate != null
            ? `${(pack.hit_rate * 100).toFixed(0)}%`
            : "—";
        setVizMeta(
          "quant-score-review-scatter-meta",
          `n=${pack.n} · 方向命中 ${hit} · 绿=对 · 红=错`
        );
      }
    });
  }

  async function renderHitSparkline(horizon) {
    const wrap = document.getElementById("quant-score-review-hit-wrap");
    const canvas = document.getElementById("quant-score-review-hit-spark");
    if (!wrap || !canvas) return;
    wrap.hidden = false;
    try {
      const q = new URLSearchParams({
        horizon_days: String(horizon || 3),
        limit: "20",
        autofill: "false",
      });
      const res = await fetch(`/api/quant/score-review/hit-series?${q}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        canvas.hidden = true;
        setVizMeta("quant-score-review-hit-meta", "暂无命中率序列");
        return;
      }
      const pts = data.points || [];
      if (pts.length < 2) {
        canvas.hidden = true;
        setVizMeta("quant-score-review-hit-meta", "样本不足（需 ≥2 日）");
        return;
      }
      canvas.hidden = false;
      requestAnimationFrame(() => {
        const pack = paintHitSparkline(canvas, pts);
        if (pack) {
          const last =
            pack.last != null ? `${(pack.last * 100).toFixed(0)}%` : "—";
          const mean =
            pack.mean != null ? `${(pack.mean * 100).toFixed(0)}%` : "—";
          setVizMeta(
            "quant-score-review-hit-meta",
            `${pack.n} 日 · 近 ${last} · μ ${mean} · 虚线 50% / μ`
          );
        }
      });
    } catch (_) {
      canvas.hidden = true;
      setVizMeta("quant-score-review-hit-meta", "命中率加载失败");
    }
  }

  function renderBlameTable(title, headers, rows, cellsFn) {
    if (!rows.length) return "";
    const body = rows.map((r) => `<tr>${cellsFn(r)}</tr>`).join("");
    const th = headers.map((h) => `<th>${esc(h)}</th>`).join("");
    return (
      `<p class="quant-trades-caption">${esc(title)}</p>` +
      `<table class="quant-mini-table"><thead><tr>${th}</tr></thead>` +
      `<tbody>${body}</tbody></table>`
    );
  }

  function renderBlame(data) {
    const box = document.getElementById("quant-score-review-blame");
    if (!box) return;
    const fac = data.factor_blame || [];
    const ind = data.industry_blame || [];
    const clu = data.cluster_blame || [];
    if (!fac.length && !ind.length && !clu.length) {
      box.innerHTML = "";
      return;
    }
    box.innerHTML =
      renderBlameTable(
        "错票 · 主导因子",
        ["因子", "次数", "当日相关≈"],
        fac,
        (r) =>
          `<td>${esc(r.factor || "—")}</td>` +
          `<td class="num">${esc(String(r.wrong_count ?? "—"))}</td>` +
          `<td class="num">${
            r.factor_ic_day != null ? esc(Number(r.factor_ic_day).toFixed(3)) : "—"
          }</td>`
      ) +
      renderBlameTable(
        "错票 · 行业",
        ["行业", "次数"],
        ind,
        (r) =>
          `<td>${esc(r.sector || "—")}</td>` +
          `<td class="num">${esc(String(r.wrong_count ?? "—"))}</td>`
      ) +
      renderBlameTable(
        "错票 · 分组",
        ["分组", "次数"],
        clu,
        (r) =>
          `<td>${esc(r.cluster_label || "—")}</td>` +
          `<td class="num">${esc(String(r.wrong_count ?? "—"))}</td>`
      );
  }

  function renderWrongTable(data) {
    const box = document.getElementById("quant-score-review-table");
    if (!box) return;
    const rows = data.wrong_rows || [];
    if (!rows.length) {
      box.innerHTML = data.empty
        ? ""
        : `<p class="quant-attr-note">无方向错误样本（或收益尚未回填）。</p>`;
      return;
    }
    const body = rows
      .map((r) => {
        const y = r.yhat != null ? Number(r.yhat).toFixed(2) : "—";
        const ret = r.realized_h != null ? Number(r.realized_h).toFixed(2) : "—";
        const err = r.abs_err != null ? Number(r.abs_err).toFixed(2) : "—";
        const fac = r.dominant_factor || "—";
        const ic =
          r.factor_ic_day != null ? Number(r.factor_ic_day).toFixed(3) : "—";
        return (
          `<tr>` +
          `<td>${esc(r.code || "—")}</td>` +
          `<td>${esc(r.name || "")}</td>` +
          `<td>${esc(r.sector || "—")}</td>` +
          `<td>${esc(r.cluster_label || "—")}</td>` +
          `<td class="num">${esc(y)}</td>` +
          `<td class="num">${esc(ret)}</td>` +
          `<td class="num">${esc(err)}</td>` +
          `<td>${esc(fac)}</td>` +
          `<td class="num">${esc(ic)}</td>` +
          `<td>${esc(tagLabel(r.tag))}</td>` +
          `</tr>`
        );
      })
      .join("");
    box.innerHTML =
      `<table class="quant-mini-table"><thead><tr>` +
      `<th>代码</th><th>名称</th><th>行业</th><th>组</th>` +
      `<th>ŷ%</th><th>实现%</th><th>|误差|</th>` +
      `<th>主导因子</th><th>因子相关</th><th>标签</th>` +
      `</tr></thead><tbody>${body}</tbody></table>`;
  }

  async function ensureDefaultAsOf() {
    const el = document.getElementById("quant-score-review-asof");
    if (!el || el.value) return;
    try {
      const res = await fetch("/api/quant/score-review/dates?limit=10");
      const data = await res.json().catch(() => ({}));
      if (data.default_as_of) el.value = data.default_as_of;
      else if ((data.dates || [])[0]) el.value = data.dates[0];
    } catch (_) {
      /* ignore */
    }
  }

  async function runReview({ autofill = true } = {}) {
    await ensureDefaultAsOf();
    const asOf = readAsOf();
    const horizon = readHorizon();
    setStatus("复盘计算中…", { busy: true });
    if (setQuantMeta) setQuantMeta("昨日复盘计算中…", { busy: true });
    try {
      const q = new URLSearchParams();
      if (asOf) q.set("as_of", asOf);
      q.set("horizon_days", String(horizon));
      q.set("autofill", autofill ? "true" : "false");
      const res = await fetch(`/api/quant/score-review?${q.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      // 后端可能因薄样本自动降到 h=1；同步控件
      if (data.horizon_days != null) syncHorizonSelect(data.horizon_days);
      const usedH = Number(data.horizon_days) || horizon;
      renderSummary(data);
      renderScatter(data);
      renderHitSparkline(usedH).catch(() => {});
      renderBlame(data);
      renderWrongTable(data);
      const s = data.summary || {};
      const hit =
        s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
      const thin = Number(s.data_thin || 0);
      const fbNote =
        data.horizon_fallback_from != null
          ? ` · 自 h=${data.horizon_fallback_from} 降级`
          : "";
      const statusLine = data.empty
        ? data.note || "无账本"
        : s.hit_rate != null
          ? `as_of ${data.as_of} · 命中 ${hit} · 错票 ${s.wrong ?? 0}${fbNote}`
          : thin > 0
            ? `as_of ${data.as_of} · 薄样本 ${thin}/${data.n_ledger ?? "—"} · 日线未覆盖 h=${usedH}`
            : `as_of ${data.as_of} · ${s.blame_line || "样本不足"}`;
      setStatus(statusLine, {
        ok: !data.empty && s.hit_rate != null,
        error: !!data.empty,
      });
      if (setQuantMeta) {
        const metaLine = data.empty
          ? "昨日复盘：无账本"
          : s.hit_rate != null
            ? `昨日复盘 · 命中 ${hit}${fbNote}`
            : thin > 0
              ? `昨日复盘 · 薄样本 ${thin} · 请改小 Horizon 或刷新日线`
              : `昨日复盘 · ${s.blame_line || "样本不足"}`;
        setQuantMeta(metaLine, {
          busy: false,
          error: !!data.empty || (s.hit_rate == null && thin > 0),
        });
      }
      return data;
    } catch (err) {
      setStatus(`复盘失败：${String(err.message || err)}`, { error: true });
      if (setQuantMeta) {
        setQuantMeta(`昨日复盘失败：${String(err.message || err)}`, {
          busy: false,
          error: true,
        });
      }
      throw err;
    }
  }

  async function fillOutcomes() {
    await ensureDefaultAsOf();
    const asOf = readAsOf();
    const horizon = readHorizon();
    setStatus("回填收益中…", { busy: true });
    const res = await fetch("/api/quant/score-outcomes/fill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: asOf, horizon_days: horizon }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    setStatus(
      `已回填 ${data.filled ?? 0} 只（缺 ${data.missing ?? 0}）`,
      { ok: true }
    );
    return runReview({ autofill: false });
  }

  async function freezeLedger() {
    setStatus("冻结打分中…", { busy: true });
    const res = await fetch("/api/quant/score-ledger/freeze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: readAsOf() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    const asEl = document.getElementById("quant-score-review-asof");
    if (asEl && data.as_of) asEl.value = data.as_of;
    setStatus(`已冻结 ${data.n_rows ?? 0} 只 · ${data.as_of || ""}`, { ok: true });
    return runReview({ autofill: true });
  }

  function jumpRefit() {
    const runBtn = document.getElementById("quant-ols-clusters-run");
    if (runBtn) {
      runBtn.scrollIntoView({ block: "nearest", behavior: "smooth" });
      runBtn.click();
      setStatus("已触发「跑分组」重估", { ok: true });
      if (setQuantMeta) setQuantMeta("复盘 → 跑分组重估中…", { busy: true });
    } else if (setQuantMeta) {
      setQuantMeta("请到研究枢纽点「跑分组」重估组 β", { error: true });
    }
  }

  on("quant-score-review-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runReview({ autofill: true });
    } catch (_) {
      /* status already set */
    }
  });
  on("quant-score-review-fill", "click", async (e) => {
    e.preventDefault();
    try {
      await fillOutcomes();
    } catch (err) {
      setStatus(`回填失败：${String(err.message || err)}`, { error: true });
    }
  });
  on("quant-score-ledger-freeze", "click", async (e) => {
    e.preventDefault();
    try {
      await freezeLedger();
    } catch (err) {
      setStatus(`冻结失败：${String(err.message || err)}`, { error: true });
    }
  });
  on("quant-score-review-refit", "click", (e) => {
    e.preventDefault();
    jumpRefit();
  });

  // 进页直接加载（不再嵌套 details 折叠）
  ensureDefaultAsOf()
    .then(() => runReview({ autofill: true }))
    .catch(() => {});

  return { runReview, fillOutcomes, freezeLedger, ensureDefaultAsOf, jumpRefit };
}
