/** 盘前 macro 上下文 · 共享渲染（Dashboard / 研究枢纽）。 */

function defaultCssToken(name, fallback) {
  const root =
    document.querySelector(".dashboard-page") ||
    document.querySelector(".quant-page") ||
    document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

/** 海外科技 / A50 近 N 日双线图（canvas）。 */
export function paintMacroHistoryChart(canvas, rows, opts = {}) {
  if (!canvas || !rows || rows.length < 2) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const cssToken = opts.cssToken || defaultCssToken;
  const w = canvas.width;
  const h = canvas.height;
  const pad = 4;
  const series = [
    {
      data: rows.map((r) => r.overseas_tech_1d_pct),
      color: cssToken("--color-up", "#f5222d"),
    },
    {
      data: rows.map((r) => r.a50_1d_pct),
      color: cssToken("--accent", "#1890ff"),
    },
  ];
  const all = series.flatMap((s) => s.data.map(Number)).filter((v) => Number.isFinite(v));
  if (all.length < 2) return;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const range = max - min || 1;
  const step = (w - pad * 2) / (rows.length - 1);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = cssToken("--line", "#e2e8f0");
  ctx.lineWidth = 1;
  const zeroY = h - pad - ((0 - min) / range) * (h - pad * 2);
  if (zeroY >= pad && zeroY <= h - pad) {
    ctx.beginPath();
    ctx.moveTo(pad, zeroY);
    ctx.lineTo(w - pad, zeroY);
    ctx.stroke();
  }
  for (const s of series) {
    const pts = s.data
      .map((v, i) => ({ i, v: Number(v) }))
      .filter((p) => Number.isFinite(p.v));
    if (pts.length < 2) continue;
    ctx.beginPath();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.5;
    pts.forEach((p, idx) => {
      const x = pad + p.i * step;
      const y = h - pad - ((p.v - min) / range) * (h - pad * 2);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}

export function fmtMacroPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

/** 研究枢纽 / Dashboard 用的紧凑 macro 条 HTML。 */
export function renderMacroContextStrip(ctx, escapeHtml) {
  const esc = escapeHtml || ((s) => String(s));
  if (!ctx || ctx.ok === false) {
    return `<p class="quant-macro-strip-empty">盘前 macro 未就绪 · pre_market_ingest</p>`;
  }
  const macro = ctx.macro || {};
  const reg = ctx.regime || {};
  const flags = ctx.prior_flags || {};
  const hist = ctx.macro_history || [];
  const stale = ctx.freshness && ctx.freshness.needs_ingest;
  const regimeLab =
    reg.regime === "bear"
      ? "熊"
      : reg.regime === "weak"
        ? "弱"
        : reg.regime === "bull"
          ? "牛"
          : reg.regime === "strong"
            ? "强"
            : reg.regime === "neutral"
              ? "中"
              : "—";
  return (
    `<div class="quant-macro-strip ${stale ? "is-stale" : ""} ${flags.any_active ? "is-active" : ""}">` +
    `<span class="quant-macro-strip-k">M 环境</span>` +
    `<span class="quant-macro-strip-item">科技 ${esc(fmtMacroPct(macro.overseas_tech_1d_pct))}</span>` +
    `<span class="quant-macro-strip-item">A50 ${esc(fmtMacroPct(macro.a50_1d_pct))}</span>` +
    `<span class="quant-macro-strip-item">Regime ${esc(regimeLab)}</span>` +
    `<span class="quant-macro-strip-item">${flags.any_active ? "Prior 激活" : "Prior 待机"}</span>` +
    (hist.length >= 5
      ? `<canvas class="quant-macro-strip-chart" id="quant-macro-strip-chart" width="360" height="44" aria-label="宏观近30日"></canvas>`
      : ctx.macro_history_rows === 0
        ? `<span class="quant-macro-strip-hint">macro_backfill 可填历史</span>`
        : "") +
    `</div>`
  );
}

/** 触发盘前 ingest；可选串联 macro_backfill（回测宏观分桶用）。 */
export async function runMarketContextIngest(apiFetch, { backfill = true } = {}) {
  const fetchFn =
    apiFetch ||
    (async (url, opts) => {
      const res = await fetch(url, opts);
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
      return { ok: res.ok, data, error: data && data.detail ? String(data.detail) : null };
    });
  const ingest = await fetchFn("/api/schedule/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "pre_market_ingest" }),
  });
  if (backfill && ingest && ingest.ok !== false) {
    await fetchFn("/api/schedule/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "macro_backfill" }),
    });
  }
  return ingest;
}

export async function loadAndPaintMacroStrip(host, apiFetch, escapeHtml) {
  if (!host) return null;
  try {
    const { ok, data } = await apiFetch("/api/dashboard/market-context");
    const ctx = ok !== false && data ? data : null;
    host.innerHTML = renderMacroContextStrip(ctx, escapeHtml);
    const canvas = host.querySelector("#quant-macro-strip-chart");
    if (canvas && ctx && (ctx.macro_history || []).length >= 5) {
      paintMacroHistoryChart(canvas, ctx.macro_history);
    }
    return ctx;
  } catch (err) {
    host.innerHTML = `<p class="quant-macro-strip-empty">${escapeHtml(String(err.message || err))}</p>`;
    return null;
  }
}
