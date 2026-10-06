/** 盘前 macro 上下文 · 共享渲染（Dashboard / 研究枢纽）。 */

import { renderCompactSparkline } from "./lw_charts.js";

function defaultCssToken(name, fallback) {
  const root =
    document.querySelector(".dashboard-page") ||
    document.querySelector(".quant-page") ||
    document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

/** 与 sparkline 同色：红=海外科技 · 蓝=A50。图线为窗口内累计涨跌%。 */
export function macroHistoryLegendHtml() {
  return (
    `<span class="macro-hist-legend">` +
    `<span class="macro-hist-legend-item">` +
    `<i class="macro-hist-legend-swatch is-tech" aria-hidden="true"></i>海外科技` +
    `<b class="macro-hist-legend-val" data-series="tech"></b></span>` +
    `<span class="macro-hist-legend-item">` +
    `<i class="macro-hist-legend-swatch is-a50" aria-hidden="true"></i>A50` +
    `<b class="macro-hist-legend-val" data-series="a50"></b></span>` +
    `</span>`
  );
}

function cumulativeFromDaily(rows, key) {
  let factor = 1;
  const points = [];
  for (const r of rows || []) {
    const time = String(r.date || r.time || "").slice(0, 10);
    const v = Number(r[key]);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(time) || !Number.isFinite(v)) continue;
    factor *= 1 + v / 100;
    points.push({ time, value: (factor - 1) * 100 });
  }
  return { points };
}

function sparkTimeKey(time) {
  if (typeof time === "string") return time.slice(0, 10);
  if (typeof time === "number" && Number.isFinite(time)) {
    const ms = time > 1e12 ? time : time * 1000;
    return new Date(ms).toISOString().slice(0, 10);
  }
  return "";
}

function sparkTimeLabel(time) {
  const key = sparkTimeKey(time);
  return key.length >= 10 ? key.slice(5) : "";
}

function lastPointValue(points) {
  if (!points || !points.length) return null;
  const v = Number(points[points.length - 1].value);
  return Number.isFinite(v) ? v : null;
}

function setLegendVals(root, tech, a50) {
  if (!root) return;
  const techEl = root.querySelector('.macro-hist-legend-val[data-series="tech"]');
  const a50El = root.querySelector('.macro-hist-legend-val[data-series="a50"]');
  const apply = (el, v) => {
    if (!el) return;
    el.textContent = fmtMacroPct(v);
    el.classList.remove("is-up", "is-down", "is-flat");
    if (v == null || !Number.isFinite(Number(v))) el.classList.add("is-flat");
    else if (Number(v) > 0) el.classList.add("is-up");
    else if (Number(v) < 0) el.classList.add("is-down");
    else el.classList.add("is-flat");
  };
  apply(techEl, tech);
  apply(a50El, a50);
}

function paintMacroHistoryCanvas(canvas, rows, opts = {}) {
  if (!canvas || !rows || rows.length < 2) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const cssToken = opts.cssToken || defaultCssToken;
  const tech = cumulativeFromDaily(rows, "overseas_tech_1d_pct");
  const a50 = cumulativeFromDaily(rows, "a50_1d_pct");
  const series = [
    { points: tech.points, color: cssToken("--color-up", "#f5222d") },
    { points: a50.points, color: cssToken("--accent", "#1890ff") },
  ].filter((s) => s.points.length >= 2);
  const all = series.flatMap((s) => s.points.map((p) => p.value));
  if (all.length < 2) return;

  const cssW = Math.max(1, Math.round(canvas.clientWidth || Number(canvas.getAttribute("width")) || 240));
  const cssH = Math.max(1, Math.round(canvas.clientHeight || Number(canvas.getAttribute("height")) || 40));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const w = cssW;
  const h = cssH;
  const pad = 4;
  const min = Math.min(...all, 0);
  const max = Math.max(...all, 0);
  const range = max - min || 1;

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = cssToken("--line", "#e2e8f0");
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  const zeroY = h - pad - ((0 - min) / range) * (h - pad * 2);
  if (zeroY >= pad && zeroY <= h - pad) {
    ctx.beginPath();
    ctx.moveTo(pad, zeroY);
    ctx.lineTo(w - pad, zeroY);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  const xAt = (i, len) => pad + (len <= 1 ? 0 : (i / (len - 1)) * (w - pad * 2));
  series.forEach((s, si) => {
    ctx.beginPath();
    ctx.strokeStyle = s.color;
    ctx.lineWidth = si === 0 ? 2 : 1.25;
    s.points.forEach((p, idx) => {
      const x = xAt(idx, s.points.length);
      const y = h - pad - ((p.value - min) / range) * (h - pad * 2);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const last = s.points[s.points.length - 1];
    const lx = xAt(s.points.length - 1, s.points.length);
    const ly = h - pad - ((last.value - min) / range) * (h - pad * 2);
    ctx.beginPath();
    ctx.fillStyle = s.color;
    ctx.arc(lx, ly, 2.2, 0, Math.PI * 2);
    ctx.fill();
  });
}

function paintCanvasFallback(host, rows, opts) {
  if (!host) return;
  const mount = host.__lwMount || host;
  const canvas = document.createElement("canvas");
  canvas.className = "quant-macro-strip-chart-fallback";
  canvas.setAttribute("width", "156");
  canvas.setAttribute("height", "44");
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText = "display:block;width:100%;height:100%;";
  try {
    mount.replaceChildren(canvas);
  } catch (_) {
    host.replaceChildren(canvas);
  }
  requestAnimationFrame(() => paintMacroHistoryCanvas(canvas, rows, opts));
}

function bindSparklineReadout(host, techCum, a50Cum) {
  const wrap = host && host.closest
    ? host.closest(".quant-macro-strip-chart-wrap, .dashboard-mctx-history")
    : null;
  const label = wrap && wrap.querySelector(".quant-macro-strip-chart-label, .dashboard-mctx-history-label");
  const idleLabel = label ? label.textContent.trim() : "";
  const lastTech = lastPointValue(techCum.points);
  const lastA50 = lastPointValue(a50Cum.points);
  setLegendVals(wrap, lastTech, lastA50);
  return (info) => {
    if (!info) {
      if (label) label.textContent = idleLabel;
      setLegendVals(wrap, lastTech, lastA50);
      return;
    }
    if (label) label.textContent = sparkTimeLabel(info.time) || idleLabel;
    const byLabel = Object.fromEntries((info.values || []).map((v) => [v.label, v.value]));
    setLegendVals(wrap, byLabel["海外科技"], byLabel["A50"]);
  };
}

/** 海外科技 / A50 近 N 日累计 sparkline（LWC；失败回退 canvas）。 */
export function paintMacroHistoryChart(host, rows, opts = {}) {
  if (!host || !rows || rows.length < 2) return;
  if (host.tagName === "CANVAS") {
    paintMacroHistoryCanvas(host, rows, opts);
    return;
  }
  const cssToken = opts.cssToken || defaultCssToken;
  const techCum = cumulativeFromDaily(rows, "overseas_tech_1d_pct");
  const a50Cum = cumulativeFromDaily(rows, "a50_1d_pct");
  const series = [
    {
      label: "海外科技",
      color: cssToken("--color-up", "#f5222d"),
      points: techCum.points,
    },
    {
      label: "A50",
      color: cssToken("--accent", "#1890ff"),
      points: a50Cum.points,
    },
  ];
  const onCrosshair = bindSparklineReadout(host, techCum, a50Cum);
  renderCompactSparkline(host, series, {
    zeroLine: true,
    emptyText: "",
    onCrosshair,
  }).then((chart) => {
    if (!chart) paintCanvasFallback(host, rows, opts);
  }).catch(() => {
    paintCanvasFallback(host, rows, opts);
  });
}

export function fmtMacroPct(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function macroToneClass(v) {
  if (v == null || !Number.isFinite(Number(v))) return "is-flat";
  const n = Number(v);
  if (n > 0) return "is-up";
  if (n < 0) return "is-down";
  return "is-flat";
}

function regimeStripMeta(code) {
  const c = String(code || "").toLowerCase();
  if (c === "bear") return { lab: "熊", tone: "is-down", badge: "is-fire" };
  if (c === "weak") return { lab: "弱", tone: "is-down", badge: "is-warn" };
  if (c === "bull") return { lab: "牛", tone: "is-up", badge: "is-ok" };
  if (c === "strong") return { lab: "强", tone: "is-up", badge: "is-ok" };
  if (c === "neutral") return { lab: "中", tone: "is-flat", badge: "" };
  return { lab: "—", tone: "is-flat", badge: "is-muted" };
}

function countActivePriors(flags) {
  return [
    flags.cross_market,
    flags.market_sentiment,
    flags.regulatory,
    flags.ipo_drain,
  ].filter(Boolean).length;
}

/** 研究枢纽 / Dashboard 用的紧凑 macro 条 HTML。 */
export function renderMacroContextStrip(ctx, escapeHtml) {
  const esc = escapeHtml || ((s) => String(s));
  if (!ctx || ctx.ok === false) {
    return `<p class="quant-macro-strip-empty">盘前 macro 未就绪 · 运行 <code>pre_market_ingest</code></p>`;
  }
  const macro = ctx.macro || {};
  const reg = ctx.regime || {};
  const flags = ctx.prior_flags || {};
  const hist = ctx.macro_history || [];
  const stale = !!(ctx.freshness && ctx.freshness.needs_ingest);
  const macroDegraded = !!ctx.macro_degraded;
  const activeN = countActivePriors(flags);
  const priorOn = !!flags.any_active || activeN > 0;
  const regime = regimeStripMeta(reg.regime);
  const techTone = macroToneClass(macro.overseas_tech_1d_pct);
  const a50Tone = macroToneClass(macro.a50_1d_pct);
  const overlayNote = reg.macro_overlay_deferred
    ? "overlay→M"
    : reg.macro_overlay_applied
      ? "overlay on"
      : "权重环境";
  const priorSub =
    (ctx.prior_warnings || []).slice(0, 1).join("") ||
    (priorOn ? "调仓闸生效" : "ŷ 轴不变");
  const stateCls = [
    stale || macroDegraded ? "is-stale" : "",
    priorOn ? "is-active" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const cells = [
    {
      label: "科技",
      value: fmtMacroPct(macro.overseas_tech_1d_pct),
      tone: techTone,
      sub: "海外隔夜",
      tip: "SOX/NDX/QQQ/KWEB 等隔夜均涨跌。越负越利空 A 股科技；供跨市场 M prior 触发，不改 ŷ。",
    },
    {
      label: "A50",
      value: fmtMacroPct(macro.a50_1d_pct),
      tone: a50Tone,
      sub: "期指近端",
      tip: "富时中国 A50 期指近端涨跌。与海外科技一并进入跨市场 prior 判断。",
    },
    {
      label: "Regime",
      value: regime.lab,
      tone: regime.tone,
      sub: overlayNote,
      tip: "按基准近端涨跌分档（熊/弱/中/强/牛），影响因子权重。overlay→M 表示科技拖累已交给跨市场 prior。",
      badge: regime.badge,
      badgeText: regime.badge ? "REG" : "",
    },
    {
      label: "Prior",
      value: priorOn ? (activeN > 0 ? `激活 · ${activeN}/4` : "激活") : "待机",
      tone: priorOn ? "is-down" : "is-flat",
      sub: priorSub.slice(0, 18),
      tip: "M 层 prior（跨市场/情绪/监管/IPO）是否触发。激活时调仓可缩仓或禁买；ŷ 排名轴不变。",
      badge: priorOn ? "is-fire" : "",
      badgeText: priorOn ? "ON" : "",
      cellCls: priorOn ? "is-on" : "",
    },
  ];

  const cellHtml = cells
    .map((c) => {
      const badge =
        c.badgeText
          ? `<span class="quant-macro-strip-badge ${c.badge || ""}">${esc(c.badgeText)}</span>`
          : "";
      return (
        `<div class="quant-macro-strip-cell ${c.cellCls || ""}" title="${esc(c.tip)}">` +
        `<div class="quant-macro-strip-cell-top">` +
        `<span class="quant-macro-strip-label">${esc(c.label)}</span>` +
        badge +
        `</div>` +
        `<span class="quant-macro-strip-val ${c.tone}">${esc(c.value)}</span>` +
        `<span class="quant-macro-strip-sub">${esc(c.sub)}</span>` +
        `</div>`
      );
    })
    .join("");

  const statusPill = stale
    ? `<span class="quant-macro-strip-pill is-warn">需刷新</span>`
    : macroDegraded
      ? `<span class="quant-macro-strip-pill is-warn">macro 降级</span>`
      : priorOn
        ? `<span class="quant-macro-strip-pill is-fire">M 触发 ${activeN}/4</span>`
        : `<span class="quant-macro-strip-pill is-muted">M 待机</span>`;

  const chartOrHint =
    hist.length >= 5
      ? `<div class="quant-macro-strip-chart-wrap" title="海外科技 / 富时A50 近30日累计涨跌%（窗口起点=0；灰虚线=0；悬停看当日累计）">` +
        `<span class="quant-macro-strip-chart-label">近30日</span>` +
        `<div class="quant-macro-strip-chart" id="quant-macro-strip-chart" role="img" aria-label="海外科技与A50近30日累计涨跌"></div>` +
        macroHistoryLegendHtml() +
        `</div>`
      : ctx.macro_history_rows === 0
        ? `<span class="quant-macro-strip-hint">历史未回填 · <code>macro_backfill</code></span>`
        : "";
  const layoutCls = chartOrHint ? "has-side" : "no-side";

  return (
    `<div class="quant-macro-strip ${stateCls} ${layoutCls}" role="group" aria-label="盘前 M 环境">` +
    `<div class="quant-macro-strip-brand">` +
    `<span class="quant-macro-strip-k" title="盘前 macro / Regime / M prior 快照。不预测当日大盘收盘，不改 ŷ。">M 环境</span>` +
    statusPill +
    `</div>` +
    `<div class="quant-macro-strip-grid">${cellHtml}</div>` +
    chartOrHint +
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
    const chartHost = host.querySelector("#quant-macro-strip-chart");
    if (chartHost && ctx && (ctx.macro_history || []).length >= 5) {
      paintMacroHistoryChart(chartHost, ctx.macro_history);
    }
    return ctx;
  } catch (err) {
    host.innerHTML = `<p class="quant-macro-strip-empty">${escapeHtml(String(err.message || err))}</p>`;
    return null;
  }
}
