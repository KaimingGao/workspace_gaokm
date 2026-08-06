/**
 * 昨日复盘：ŷ 方向 vs 前瞻收益。
 */
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

  function renderSummary(data) {
    const box = document.getElementById("quant-score-review-summary");
    if (!box) return;
    if (data.empty) {
      box.innerHTML = `<p class="quant-attr-note">${esc(
        data.note || "无账本"
      )}</p>`;
      return;
    }
    const s = data.summary || {};
    const hit =
      s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
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
      `</div>` +
      `<p class="quant-attr-note">${esc(s.blame_line || "")}</p>`;
  }

  function renderBlame(data) {
    const box = document.getElementById("quant-score-review-blame");
    if (!box) return;
    const rows = data.factor_blame || [];
    if (!rows.length) {
      box.innerHTML = "";
      return;
    }
    const body = rows
      .map(
        (r) =>
          `<tr><td>${esc(r.factor || "—")}</td>` +
          `<td class="num">${esc(String(r.wrong_count ?? "—"))}</td>` +
          `<td class="num">${
            r.factor_ic_day != null ? esc(Number(r.factor_ic_day).toFixed(3)) : "—"
          }</td></tr>`
      )
      .join("");
    box.innerHTML =
      `<table class="quant-mini-table"><thead><tr>` +
      `<th>错票主导因子</th><th>次数</th><th>当日相关≈</th>` +
      `</tr></thead><tbody>${body}</tbody></table>`;
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
      `<th>代码</th><th>名称</th><th>ŷ%</th><th>实现%</th><th>|误差|</th>` +
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
    const fold = document.getElementById("quant-score-review-fold");
    if (fold) fold.open = true;
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
      renderSummary(data);
      renderBlame(data);
      renderWrongTable(data);
      const s = data.summary || {};
      const hit =
        s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
      setStatus(
        data.empty
          ? data.note || "无账本"
          : `as_of ${data.as_of} · 命中 ${hit} · 错票 ${s.wrong ?? 0}`,
        { ok: !data.empty, error: !!data.empty }
      );
      const sum = document.getElementById("quant-score-review-fold-summary");
      if (sum) {
        sum.textContent = data.empty
          ? "无账本 · 先冻结打分或跑分组/日报"
          : `命中 ${hit} · 错票 ${s.wrong ?? 0} · as_of ${data.as_of}`;
      }
      if (setQuantMeta) {
        setQuantMeta(
          data.empty ? "昨日复盘：无账本" : `昨日复盘 · 命中 ${hit}`,
          { busy: false, error: !!data.empty }
        );
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

  const fold = document.getElementById("quant-score-review-fold");
  if (fold) {
    fold.addEventListener("toggle", () => {
      if (fold.open) {
        ensureDefaultAsOf().then(() => runReview({ autofill: true })).catch(() => {});
      }
    });
  }

  return { runReview, fillOutcomes, freezeLedger, ensureDefaultAsOf };
}
