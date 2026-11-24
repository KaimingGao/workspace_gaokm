/** 盘前 macro 上下文 · 市场先验概览。 */

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
  canvas.className = "macro-hist-chart-fallback";
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
    ? host.closest(
        ".dashboard-mctx-history, .strategy-macro-history-wrap"
      )
    : null;
  const label = wrap && wrap.querySelector(".dashboard-mctx-history-label");
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

/** 央行中间价常以「人民币/100美元」报价（~670）；归一成 USD/CNY ~6.7。 */
export function fmtUsdCnyLevel(close) {
  if (close == null || !Number.isFinite(Number(close))) return "—";
  const n = Number(close);
  const spot = n >= 50 ? n / 100 : n;
  return spot.toFixed(3);
}

/** 美债收益率水平；Δ 用基点（change 按百分点计）。 */
export function fmtUs10yLevel(close) {
  if (close == null || !Number.isFinite(Number(close))) return "—";
  return `${Number(close).toFixed(3)}%`;
}

export function fmtUs10yDelta(chg) {
  if (chg == null || !Number.isFinite(Number(chg))) return "Δ —";
  const bp = Number(chg) * 100;
  return `Δ ${bp > 0 ? "+" : ""}${bp.toFixed(1)}bp`;
}

function macroToneClass(v) {
  if (v == null || !Number.isFinite(Number(v))) return "is-flat";
  const n = Number(v);
  if (n > 0) return "is-up";
  if (n < 0) return "is-down";
  return "is-flat";
}

/** 汇率：CNH 升值（人民币更强 / 点数下降）偏利好 A 股流动性 → 跌=绿。 */
function cnhToneClass(v) {
  if (v == null || !Number.isFinite(Number(v))) return "is-flat";
  const n = Number(v);
  if (n < 0) return "is-up";
  if (n > 0) return "is-down";
  return "is-flat";
}

/** 美债：收益率上行偏紧 → 涨=红。 */
function us10yToneClass(v) {
  return macroToneClass(v);
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

/** 与 Regime 看板一致的基准短名。 */
export function regimeBenchmarkShort(code) {
  const raw = String(code || "").toLowerCase();
  if (!raw) return "沪深300";
  if (raw.includes("1000") || raw === "csi1000" || raw === "zz1000") return "中证1000";
  if (raw.includes("500") || raw === "csi500" || raw === "zz500") return "中证500";
  if (raw.includes("300") || raw === "hs300" || raw === "csi300" || raw.includes("000300"))
    return "沪深300";
  return String(code);
}

/** Regime 格副文案：基准近端涨跌（今日锚）+ overlay 短注。 */
export function regimeAnchorSub(reg) {
  const r = reg || {};
  const daysRaw = Number(r.window_days);
  const days = Number.isFinite(daysRaw) && daysRaw > 0 ? daysRaw : 20;
  const bench = regimeBenchmarkShort(r.benchmark);
  const roc = r.index_return_pct;
  const rocTxt =
    roc != null && Number.isFinite(Number(roc))
      ? `${Number(roc) > 0 ? "+" : ""}${Number(roc).toFixed(1)}%`
      : "—";
  const overlay = r.macro_overlay_deferred
    ? "→M"
    : r.macro_overlay_applied
      ? "ov"
      : "";
  const head = `${bench} ${days}D ${rocTxt}`;
  return overlay ? `${head} · ${overlay}` : head;
}

function sentimentBandOf(score, deskBand) {
  if (deskBand) return String(deskBand);
  const sc = Number(score);
  if (!Number.isFinite(sc)) return "—";
  if (sc < 40) return "偏弱";
  if (sc < 55) return "中性";
  if (sc < 70) return "偏热";
  return "过热";
}

function sentimentToneClass(score, brokenRate) {
  const sc = Number(score);
  const br = Number(brokenRate);
  if (Number.isFinite(br) && br >= 0.35) return "is-down";
  if (!Number.isFinite(sc)) return "is-flat";
  if (sc < 40) return "is-down";
  if (sc >= 70) return "is-up";
  if (sc >= 55) return "is-up";
  return "is-flat";
}

/** 游资盘前：涨停家数为主，炸板/连板为副。 */
export function sentimentStripMeta(sent, desk, flags) {
  const s = sent || {};
  const deskSent = (desk && desk.sentiment_desk) || {};
  const score = s.sentiment_cycle_score != null ? s.sentiment_cycle_score : deskSent.cycle_score;
  const lu = s.limit_up_count != null ? s.limit_up_count : deskSent.limit_up_count;
  const prev = s.prev_limit_count != null ? s.prev_limit_count : deskSent.prev_limit_count;
  const broken =
    s.broken_limit_rate != null ? s.broken_limit_rate : deskSent.broken_limit_rate;
  const height =
    s.avg_board_height != null ? s.avg_board_height : deskSent.avg_board_height;
  const band = sentimentBandOf(score, deskSent.band);
  const on = !!(flags && flags.market_sentiment);
  const value =
    lu != null && Number.isFinite(Number(lu)) ? String(Math.round(Number(lu))) : "—";
  const parts = [];
  if (broken != null && Number.isFinite(Number(broken))) {
    parts.push(`炸${(Number(broken) * 100).toFixed(0)}%`);
  }
  if (height != null && Number.isFinite(Number(height))) {
    parts.push(`连${Number(height).toFixed(1)}`);
  }
  if (prev != null && Number.isFinite(Number(prev))) {
    parts.push(`昨${Math.round(Number(prev))}`);
  }
  const sub = parts.length ? `${parts.join(" · ")} · ${band}` : band;
  const tip =
    `涨停 ${lu ?? "—"}` +
    (prev != null ? ` / 昨 ${prev}` : "") +
    (broken != null ? ` · 炸板 ${(Number(broken) * 100).toFixed(0)}%` : "") +
    (height != null ? ` · 均连板 ${Number(height).toFixed(1)}` : "") +
    (score != null ? ` · 周期分 ${score}` : "") +
    `（${band}）。游资盘前温度；触发情绪 prior 时压仓，不改 ŷ。`;
  return {
    value,
    sub,
    tip,
    tone: sentimentToneClass(score, broken),
    badge: on || band === "过热" ? "is-fire" : band === "偏热" ? "is-warn" : "",
    badgeText: on ? "M" : band === "过热" ? "HOT" : band === "偏热" ? "暖" : "",
    cellCls: on ? "is-on" : "",
  };
}

function countActivePriors(flags) {
  return [
    flags.cross_market,
    flags.market_sentiment,
    flags.regulatory,
    flags.ipo_drain,
  ].filter(Boolean).length;
}

const TECH_SERIES_KEYS = ["sox", "ndx", "qqq", "kweb"];

/** 科技成分会话日 + 滞后键；服务端字段优先，缺则从 recent_bars 推。 */
export function techSessionMeta(macro) {
  const m = macro || {};
  const series = m.series || {};
  const dates = {};
  for (const k of TECH_SERIES_KEYS) {
    const bars = (series[k] && series[k].recent_bars) || [];
    const d = bars.length ? String(bars[bars.length - 1].date || "").slice(0, 10) : "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(d)) dates[k] = d;
  }
  const serverSession = String(m.tech_session_as_of || "").slice(0, 10);
  const vals = Object.values(dates);
  const session =
    (/^\d{4}-\d{2}-\d{2}$/.test(serverSession) && serverSession) ||
    (vals.length ? vals.slice().sort().slice(-1)[0] : "");
  let stale = [];
  if (Array.isArray(m.stale_tech_keys) && m.stale_tech_keys.length) {
    stale = m.stale_tech_keys.map((k) => String(k || "").toLowerCase()).filter(Boolean);
  } else if (session) {
    stale = TECH_SERIES_KEYS.filter((k) => dates[k] && dates[k] !== session);
  }
  // 只保留确实滞后的键
  stale = stale.filter((k) => dates[k] && session && dates[k] !== session);
  return { session, stale, dates };
}

function shortMd(iso) {
  const s = String(iso || "").slice(0, 10);
  return s.length >= 10 ? s.slice(5) : s || "—";
}

/** 市场先验概览：科技 / A50 / Regime 等 KPI；近30日曲线只在跨市场明细。 */
export function renderMacroContextStrip(ctx, escapeHtml) {
  const esc = escapeHtml || ((s) => String(s));
  if (!ctx || ctx.ok === false) {
    return `<p class="quant-macro-strip-empty">盘前 macro 未就绪 · 运行 <code>pre_market_ingest</code></p>`;
  }
  const macro = ctx.macro || {};
  const reg = ctx.regime || {};
  const flags = ctx.prior_flags || {};
  const sent = ctx.market_sentiment || {};
  const desk = ctx.desk || {};
  const stale = !!(ctx.freshness && ctx.freshness.needs_ingest);
  const macroDegraded = !!ctx.macro_degraded;
  const activeN = countActivePriors(flags);
  const priorOn = !!flags.any_active || activeN > 0;
  const regime = regimeStripMeta(reg.regime);
  const emotion = sentimentStripMeta(sent, desk, flags);
  const series = macro.series || {};
  const cnhSlot = series.cnh || {};
  const us10ySlot = series.us10y || {};
  const cnhChg =
    macro.cnh_1d_pct != null ? macro.cnh_1d_pct : cnhSlot.change_1d_pct;
  const cnhClose = macro.cnh_close != null ? macro.cnh_close : cnhSlot.close;
  const us10yChg =
    macro.us10y_1d_pct != null ? macro.us10y_1d_pct : us10ySlot.change_1d_pct;
  const us10yClose =
    macro.us10y_close != null ? macro.us10y_close : us10ySlot.close;
  const liqScore = Number(macro.liquidity_stress_score);
  const liqOn = Number.isFinite(liqScore) && liqScore > 0;

  const techTone = macroToneClass(macro.overseas_tech_1d_pct);
  const a50Tone = macroToneClass(macro.a50_1d_pct);
  const techMeta = techSessionMeta(macro);
  const techLag = techMeta.stale.length > 0;
  const lagNames = techMeta.stale.map((k) => String(k).toUpperCase()).join("·");
  const regimeSub = regimeAnchorSub(reg);
  const regimeTip =
    `${regimeBenchmarkShort(reg.benchmark)} 近 ${
      Number(reg.window_days) > 0 ? Number(reg.window_days) : 20
    } 日涨跌驱动分档（熊/弱/中/强/牛），影响因子权重。` +
    (reg.macro_overlay_deferred
      ? " overlay→M：科技拖累已交给跨市场 prior。"
      : reg.macro_overlay_applied
        ? " 已叠加海外科技拖累。"
        : " 不改 ŷ。");
  const stateCls = [
    stale || macroDegraded || techLag || liqOn ? "is-stale" : "",
    priorOn ? "is-active" : "",
  ]
    .filter(Boolean)
    .join(" ");

  const techSub = techLag
    ? `缺 ${lagNames}`
    : techMeta.session
      ? `会话 ${shortMd(techMeta.session)}`
      : "海外隔夜";
  const techTip = techLag
    ? `海外科技均涨仅含与会话 ${techMeta.session || "—"} 同日的成分；滞后：${lagNames}（末日 ${techMeta.stale
        .map((k) => `${String(k).toUpperCase()} ${shortMd(techMeta.dates[k])}`)
        .join(" · ")}）。点刷新 ingest。不改 ŷ。`
    : "SOX/NDX/QQQ/KWEB 等隔夜均涨跌。越负越利空 A 股科技；供跨市场 M prior 触发，不改 ŷ。";

  const cells = [
    {
      label: "科技",
      value: fmtMacroPct(macro.overseas_tech_1d_pct),
      tone: techTone,
      sub: techSub,
      tip: techTip,
      badge: techLag ? "is-warn" : "",
      badgeText: techLag ? "LAG" : "",
      cellCls: techLag ? "is-lag" : "",
    },
    {
      label: "A50",
      value: fmtMacroPct(macro.a50_1d_pct),
      tone: a50Tone,
      sub: "当月连续",
      tip: "富时中国 A50 期指当月连续涨跌。与海外科技一并进入跨市场 prior 判断。",
    },
    {
      label: "CNH",
      value: fmtMacroPct(cnhChg),
      tone: cnhToneClass(cnhChg),
      sub: `USD/CNY ${fmtUsdCnyLevel(cnhClose)}`,
      tip: "离岸人民币 / 央行中间价 proxy。点数上行=人民币贬值，偏紧流动性；计入 liquidity_stress。",
      badge: liqOn && Number(cnhChg) > 0.15 ? "is-warn" : "",
      badgeText: liqOn && Number(cnhChg) > 0.15 ? "FX" : "",
      cellCls: liqOn && Number(cnhChg) > 0.15 ? "is-lag" : "",
    },
    {
      label: "美债",
      value: fmtUs10yLevel(us10yClose),
      tone: us10yToneClass(us10yChg),
      sub: fmtUs10yDelta(us10yChg),
      tip: "美债 10Y 收益率水平与日变。上行偏紧全球流动性；计入 liquidity_stress。",
      badge: liqOn && Number(us10yChg) > 0.05 ? "is-warn" : "",
      badgeText: liqOn && Number(us10yChg) > 0.05 ? "YLD" : "",
      cellCls: liqOn && Number(us10yChg) > 0.05 ? "is-lag" : "",
    },
    {
      label: "情绪",
      value: emotion.value,
      tone: emotion.tone,
      sub: emotion.sub,
      tip: emotion.tip,
      badge: emotion.badge,
      badgeText: emotion.badgeText,
      cellCls: emotion.cellCls,
    },
    {
      label: "锚",
      value: regime.lab,
      tone: regime.tone,
      sub: regimeSub,
      tip: regimeTip,
      badge: regime.badge,
      badgeText: regime.badge ? "REG" : "",
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

  const clock = ctx.session_clock || {};
  const clockLabel = String(clock.label || "").trim();
  const clockTip = String(clock.tip || clockLabel || "").trim();
  const clockTone = String(clock.tone || "idle");
  const clockPillCls =
    clockTone === "live" ? "is-fire" : clockTone === "pre" ? "is-muted" : "is-muted";

  const pills = [];
  if (clockLabel) {
    pills.push(
      `<span class="quant-macro-strip-pill ${clockPillCls}" title="${esc(
        clockTip || "会话时钟：美股 RTH × A 股阶段（上海时）"
      )}">${esc(clockLabel)}</span>`
    );
  }
  const nqChg =
    macro.nq_fut_1d_pct != null
      ? macro.nq_fut_1d_pct
      : (series.nq_fut && series.nq_fut.change_1d_pct);
  const esChg =
    macro.es_fut_1d_pct != null
      ? macro.es_fut_1d_pct
      : (series.es_fut && series.es_fut.change_1d_pct);
  if (nqChg != null && Number.isFinite(Number(nqChg))) {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="小型纳指当月连续（东财 spot）">NQ ${esc(
        fmtMacroPct(nqChg)
      )}</span>`
    );
  }
  if (esChg != null && Number.isFinite(Number(esChg))) {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="小型标普当月连续（东财 spot）">ES ${esc(
        fmtMacroPct(esChg)
      )}</span>`
    );
  }
  const wtiChg =
    macro.wti_1d_pct != null ? macro.wti_1d_pct : series.wti && series.wti.change_1d_pct;
  const cuChg =
    macro.copper_1d_pct != null
      ? macro.copper_1d_pct
      : series.copper && series.copper.change_1d_pct;
  if (wtiChg != null && Number.isFinite(Number(wtiChg))) {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="WTI / NYMEX 原油主力">油 ${esc(
        fmtMacroPct(wtiChg)
      )}</span>`
    );
  }
  if (cuChg != null && Number.isFinite(Number(cuChg))) {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="COMEX 铜">铜 ${esc(
        fmtMacroPct(cuChg)
      )}</span>`
    );
  }
  const qvixClose =
    macro.qvix_close != null
      ? macro.qvix_close
      : series.qvix && series.qvix.close;
  const qvixChg =
    macro.qvix_1d_pct != null
      ? macro.qvix_1d_pct
      : series.qvix && series.qvix.change_1d_pct;
  if (qvixClose != null && Number.isFinite(Number(qvixClose))) {
    const qv = Number(qvixClose);
    const qCls =
      qvixChg != null && Number(qvixChg) >= 5
        ? "is-warn"
        : qvixChg != null && Number(qvixChg) <= -5
          ? "is-fire"
          : "is-muted";
    pills.push(
      `<span class="quant-macro-strip-pill ${qCls}" title="50ETF 期权波动率（QVIX）；美股 VIX 不可达时的 A 股波动代理。日变 ${esc(
        fmtMacroPct(qvixChg)
      )}">QVIX ${esc(qv.toFixed(1))}</span>`
    );
  }
  const nb = ctx.northbound || (desk && desk.northbound) || {};
  if (nb.net_inflow_yi != null && Number.isFinite(Number(nb.net_inflow_yi))) {
    const n = Number(nb.net_inflow_yi);
    const nbCls =
      Math.abs(n) < 1e-9
        ? "is-muted"
        : n > 0
          ? "is-fire"
          : "is-warn";
    const nbLab =
      nb.status === "open_zero" || Math.abs(n) < 1e-9
        ? "北向 待开盘"
        : `北向 ${n > 0 ? "+" : ""}${n.toFixed(0)}亿`;
    pills.push(
      `<span class="quant-macro-strip-pill ${nbCls}" title="北向资金净流入（亿元）· as-of ${esc(
        String(nb.as_of || "—")
      )}">${esc(nbLab)}</span>`
    );
  } else if (nb.status === "unavailable" || nb.status === "empty") {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="北向暂无有效净流入">北向 —</span>`
    );
  }
  const calEvents =
    (ctx.macro_calendar && ctx.macro_calendar.events) ||
    (desk.calendar && desk.calendar.events) ||
    [];
  const calList = Array.isArray(calEvents) ? calEvents : [];
  const prefer = ["FOMC", "非农", "CPI", "初请", "PCE", "GDP"];
  const shown = [];
  for (const lab of prefer) {
    const hit = calList.find((e) => e && String(e.label || "") === lab && e.date);
    if (hit) shown.push(hit);
    if (shown.length >= 3) break;
  }
  if (!shown.length) {
    for (const e of calList) {
      if (e && e.date) shown.push(e);
      if (shown.length >= 2) break;
    }
  }
  for (const ev of shown) {
    const tip = [
      ev.title || ev.label,
      ev.region,
      ev.time,
      ev.kind === "last_release" ? "最近公布" : "日程",
    ]
      .filter(Boolean)
      .join(" · ");
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="${esc(tip)}">${esc(
        String(ev.label || "事件")
      )} ${esc(shortMd(ev.date))}</span>`
    );
  }
  if (techMeta.session) {
    pills.push(
      `<span class="quant-macro-strip-pill is-muted" title="海外科技成分对齐的会话日（美股收盘日）">科技 ${esc(
        shortMd(techMeta.session)
      )}</span>`
    );
  }
  if (techLag) {
    pills.push(
      `<span class="quant-macro-strip-pill is-warn" title="${esc(techTip)}">${esc(
        lagNames
      )} 滞后</span>`
    );
  }
  if (liqOn) {
    pills.push(
      `<span class="quant-macro-strip-pill is-warn" title="CNH 贬值或美债上行触发流动性压力分">流动性 ${esc(
        String(liqScore)
      )}</span>`
    );
  }
  if (stale) {
    pills.push(`<span class="quant-macro-strip-pill is-warn">需刷新</span>`);
  } else if (macroDegraded) {
    pills.push(`<span class="quant-macro-strip-pill is-warn">macro 降级</span>`);
  } else if (priorOn) {
    pills.push(
      `<span class="quant-macro-strip-pill is-fire">M 触发 ${activeN}/4</span>`
    );
  }
  const statusPill = pills.join("");

  const hl = ctx.overnight_headline || {};
  const hlText = String(hl.text || "").trim();
  const hlTone = String(hl.tone || "flat");
  const hlToneCls =
    hlTone === "up"
      ? "is-up"
      : hlTone === "down"
        ? "is-down"
        : hlTone === "warn"
          ? "is-warn"
          : "";
  const headlineHtml = hlText
    ? `<p class="quant-macro-strip-headline ${hlToneCls}" title="昨夜→今早摘要（由科技/A50/情绪/锚合成；非预测）">${esc(
        hlText
      )}</p>`
    : "";

  return (
    `<div class="quant-macro-strip ${stateCls} no-side" role="group" aria-label="盘前 M 环境">` +
    `<div class="quant-macro-strip-brand">` +
    `<span class="quant-macro-strip-k" title="盘前 macro / Regime / M prior 快照。不预测当日大盘收盘，不改 ŷ。">M 环境</span>` +
    statusPill +
    `</div>` +
    headlineHtml +
    `<div class="quant-macro-strip-grid">${cellHtml}</div>` +
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
    return ctx;
  } catch (err) {
    host.innerHTML = `<p class="quant-macro-strip-empty">${escapeHtml(String(err.message || err))}</p>`;
    return null;
  }
}
