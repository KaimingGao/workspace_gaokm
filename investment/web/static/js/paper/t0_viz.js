/** 做T回测可视化（canvas + CSS，无外部图表库）。 */

import { paperMetricClass } from "./fmt.js";
import { SKIP_CAT_TIP, stockCellHtml } from "./t0_table.js";

const THEME = {
  actual: "#2563eb",
  actualLight: "rgba(37,99,235,0.15)",
  optimistic: "#7c3aed",
  optimisticLight: "rgba(124,58,237,0.12)",
  longT: "#059669",
  longTLight: "rgba(5,150,105,0.14)",
  reverseT: "#dc2626",
  reverseTLight: "rgba(220,38,38,0.12)",
  signalSkip: "#d97706",
  signalSkipLight: "rgba(217,119,6,0.55)",
  otherSkip: "#94a3b8",
  otherSkipLight: "rgba(148,163,184,0.7)",
  grid: "rgba(148,163,184,0.22)",
  gridStrong: "rgba(100,116,139,0.35)",
  axis: "#64748b",
  ink: "#334155",
  inkMuted: "#94a3b8",
  zero: "rgba(51,65,85,0.45)",
  /** A 股收益色：红涨绿跌（与 --color-up/down 对齐） */
  pnlUp: "#f5222d",
  pnlDown: "#52c41a",
};

/** 与 core/t0/viz.py SKIP_CAT_COLORS 对齐（旧 payload 无 color 时回退） */
const SKIP_CAT_COLORS = {
  missing_minute: "#94a3b8",
  missing_scores: "#94a3b8",
  y_tau_flat: "#f59e0b",
  y_tau_weak: "#fbbf24",
  y_path_flat: "#d97706",
  y_path_disagree: "#dc2626",
  gap_tier_skip: "#ea580c",
  path_abandon: "#a8a29e",
  y_trade_weak: "#fb923c",
  trade_tau_sign: "#e11d48",
  conflict: "#ef4444",
  amplitude: "#64748b",
  directional_amplitude: "#78716c",
  lot_size: "#a78bfa",
  tplus1: "#c084fc",
  path: "#6366f1",
  trigger_miss: "#cbd5e1",
  other: "#d1d5db",
};

function skipCatColor(id) {
  return SKIP_CAT_COLORS[id] || "#94a3b8";
}

function skipCatTip(id, label) {
  const tip = SKIP_CAT_TIP[id];
  if (tip) return tip;
  return label ? `${label}：未单独标注口径。` : "未知跳过类型。";
}

const FONT = '10px var(--font-mono, ui-monospace, monospace)';
const FONT_SM = '9px var(--font-mono, ui-monospace, monospace)';
/** 与 CSS .paper-t0-viz-chart-box 高度一致 */
const CHART_BOX_H = 172;

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNum(v, d = 1) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(d) : "—";
}

function fmtMoney(v, digits = 0) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return (
    sign +
    n.toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
  );
}

function fmtDateShort(d) {
  const s = String(d || "").slice(0, 10);
  if (s.length < 10) return s || "—";
  return `${s.slice(5, 7)}-${s.slice(8, 10)}`;
}

function setupCanvas(canvas, cssW, cssH) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

/** 读取统一绘图区尺寸（优先 chart-box 容器） */
function getChartSize(canvas, { local = false } = {}) {
  if (!canvas) return { w: 280, h: CHART_BOX_H };
  if (local) {
    const wrap = canvas.parentElement;
    const w = Math.max(wrap?.clientWidth || 100, 96);
    const h = Math.max(wrap?.clientHeight || CHART_BOX_H, CHART_BOX_H);
    return { w, h };
  }
  const box = canvas.closest(".paper-t0-viz-chart-box");
  const w = Math.max(box?.clientWidth || canvas.clientWidth || 280, 200);
  const h = Math.max(box?.clientHeight || CHART_BOX_H, CHART_BOX_H);
  return { w, h };
}

function chartCanvas(vizId) {
  return `<canvas class="paper-t0-viz-canvas" data-viz="${vizId}" role="img"></canvas>`;
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  if (rr <= 0) {
    ctx.rect(x, y, w, h);
    return;
  }
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function drawHGrid(ctx, pad, w, h, ticks) {
  ctx.strokeStyle = THEME.grid;
  ctx.lineWidth = 1;
  ticks.forEach((t) => {
    const y = pad.t + t.frac * (h - pad.t - pad.b);
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(w - pad.r, y);
    ctx.stroke();
    if (t.label != null) {
      ctx.fillStyle = THEME.axis;
      ctx.font = FONT_SM;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(String(t.label), pad.l - 5, y);
    }
  });
}

function niceMax(v) {
  const n = Math.max(1, Number(v) || 1);
  const mag = 10 ** Math.floor(Math.log10(n));
  const norm = n / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

function drawCumulativePnl(canvas, points) {
  if (!canvas || !points || points.length < 1) return;
  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);
  const vals = points.map((p) => Number(p.cum_pnl ?? p.pnl ?? 0));
  const minV = Math.min(0, ...vals);
  const maxV = Math.max(0, ...vals, 1);
  const span = maxV - minV || 1;
  const pad = { l: 44, r: 10, t: 12, b: 24 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const y0 = pad.t + ((maxV - 0) / span) * plotH;

  drawHGrid(ctx, pad, w, h, [
    { frac: 0, label: fmtNum(maxV, 0) },
    { frac: 1, label: fmtNum(minV, 0) },
  ]);

  ctx.strokeStyle = THEME.zero;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(pad.l, y0);
  ctx.lineTo(w - pad.r, y0);
  ctx.stroke();
  ctx.setLineDash([]);

  const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
  grad.addColorStop(0, "rgba(37,99,235,0.18)");
  grad.addColorStop(1, "rgba(37,99,235,0.02)");
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = pad.l + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
    const v = Number(p.cum_pnl ?? p.pnl ?? 0);
    const y = pad.t + ((maxV - v) / span) * plotH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  const lastX =
    pad.l + (points.length === 1 ? plotW / 2 : plotW);
  ctx.lineTo(lastX, h - pad.b);
  ctx.lineTo(pad.l, h - pad.b);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  ctx.strokeStyle = THEME.actual;
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const x = pad.l + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
    const v = Number(p.cum_pnl ?? p.pnl ?? 0);
    const y = pad.t + ((maxV - v) / span) * plotH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  points.forEach((p, i) => {
    const x = pad.l + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
    const v = Number(p.cum_pnl ?? p.pnl ?? 0);
    const y = pad.t + ((maxV - v) / span) * plotH;
    ctx.fillStyle = v >= 0 ? THEME.pnlUp : THEME.pnlDown;
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  });

  const last = points[points.length - 1];
  const total = Number(last.cum_pnl ?? last.pnl ?? 0);
  const first = Number(points[0]?.cum_pnl ?? points[0]?.pnl ?? 0);
  return {
    total,
    n: points.length,
    delta: total - first,
    max: maxV,
    min: minV,
  };
}

/** 实际 vs 乐观 — 分组竖柱 + 兑现率 */
function drawCompareBars(canvas, compare) {
  if (!canvas || !compare) return null;
  const actual = Number(compare.actual_pnl ?? 0);
  const opt = Number(compare.optimistic_pnl ?? 0);
  if (!Number.isFinite(actual) && !Number.isFinite(opt)) return null;

  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const pad = { l: 38, r: 12, t: 14, b: 36 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const minV = Math.min(0, actual, opt);
  const maxV = Math.max(0, actual, opt, 1);
  const span = maxV - minV || 1;
  const yAt = (v) => pad.t + ((maxV - v) / span) * plotH;
  const zeroY = yAt(0);

  drawHGrid(ctx, pad, w, h, [
    { frac: 0, label: fmtMoney(maxV) },
    { frac: 0.5, label: fmtMoney(minV + span / 2) },
    { frac: 1, label: fmtMoney(minV) },
  ]);

  ctx.strokeStyle = THEME.zero;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(pad.l, zeroY);
  ctx.lineTo(w - pad.r, zeroY);
  ctx.stroke();

  const barW = Math.min(48, plotW * 0.28);
  const gap = plotW * 0.18;
  const x0 = pad.l + (plotW - barW * 2 - gap) / 2;
  const series = [
    { label: "实际", val: actual, color: THEME.actual, light: THEME.actualLight },
    { label: "乐观", val: opt, color: THEME.optimistic, light: THEME.optimisticLight },
  ];

  series.forEach((s, i) => {
    const x = x0 + i * (barW + gap);
    const yTop = yAt(Math.max(0, s.val));
    const yBot = yAt(Math.min(0, s.val));
    const bh = Math.max(2, Math.abs(yBot - yTop));
    const y = s.val >= 0 ? yTop : yBot;

    const grad = ctx.createLinearGradient(0, y, 0, y + bh);
    grad.addColorStop(0, s.color);
    grad.addColorStop(1, s.light);
    roundRect(ctx, x, y, barW, bh, 4);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.fillStyle = THEME.ink;
    ctx.font = FONT;
    ctx.textAlign = "center";
    ctx.textBaseline = s.val >= 0 ? "bottom" : "top";
    const ty = s.val >= 0 ? y - 4 : y + bh + 4;
    ctx.fillText(fmtMoney(s.val), x + barW / 2, ty);

    ctx.fillStyle = THEME.axis;
    ctx.font = FONT_SM;
    ctx.textBaseline = "top";
    ctx.fillText(s.label, x + barW / 2, h - pad.b + 8);
  });

  const capture =
    opt > 1e-9 ? Math.round((actual / opt) * 1000) / 10 : null;
  const delta = compare.delta_pnl != null ? Number(compare.delta_pnl) : opt - actual;

  return {
    capture,
    delta,
    deltaRatio: compare.delta_ratio_pct,
    actual,
    opt,
  };
}

/** 日度活跃 — 堆叠柱 + 日期轴 */
function drawDailyActivity(canvas, rows) {
  if (!canvas || !rows || rows.length < 2) return null;
  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const slice = rows.slice(-50);
  const totals = slice.map(
    (r) => Number(r.traded || 0) + Number(r.signal_skip || 0) + Number(r.other_skip || 0)
  );
  const maxTot = niceMax(Math.max(...totals, 1));
  const pad = { l: 28, r: 6, t: 10, b: 28 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const barW = Math.max(3, plotW / slice.length - 1.5);
  const gap = 1.5;

  drawHGrid(ctx, pad, w, h, [
    { frac: 0, label: maxTot },
    { frac: 0.5, label: Math.round(maxTot / 2) },
    { frac: 1, label: 0 },
  ]);

  let sumTraded = 0;
  let sumSignal = 0;
  let sumOther = 0;

  slice.forEach((r, i) => {
    const x = pad.l + i * (barW + gap);
    let y = h - pad.b;
    sumTraded += Number(r.traded || 0);
    sumSignal += Number(r.signal_skip || 0);
    sumOther += Number(r.other_skip || 0);
    [
      { n: Number(r.other_skip || 0), c: THEME.otherSkip },
      { n: Number(r.signal_skip || 0), c: THEME.signalSkip },
      { n: Number(r.traded || 0), c: THEME.longT },
    ].forEach((p) => {
      if (p.n <= 0) return;
      const bh = (p.n / maxTot) * plotH;
      y -= bh;
      roundRect(ctx, x, y, barW, bh, Math.min(2, barW / 2));
      ctx.fillStyle = p.c;
      ctx.fill();
    });
  });

  const dateLabels = [0, Math.floor(slice.length / 2), slice.length - 1];
  ctx.fillStyle = THEME.axis;
  ctx.font = FONT_SM;
  ctx.textBaseline = "top";
  dateLabels.forEach((idx) => {
    const x = pad.l + idx * (barW + gap) + barW / 2;
    ctx.textAlign = idx === 0 ? "left" : idx === slice.length - 1 ? "right" : "center";
    ctx.fillText(fmtDateShort(slice[idx]?.date), x, h - pad.b + 6);
  });

  return { sumTraded, sumSignal, sumOther, days: slice.length };
}

/** y_τ 散点 — 时间轴 + 分区着色 */
function drawYtauScatter(canvas, points, threshold = 0.25) {
  if (!canvas || !points || !points.length) return null;
  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const sorted = points
    .filter((p) => Number.isFinite(Number(p.y_tau)))
    .slice()
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));
  if (!sorted.length) return null;

  const ys = sorted.map((p) => Number(p.y_tau));
  const minY = Math.min(-0.55, ...ys, -threshold - 0.05);
  const maxY = Math.max(0.55, ...ys, threshold + 0.05);
  const span = maxY - minY || 1;
  const pad = { l: 40, r: 10, t: 12, b: 26 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const yLine = (v) => pad.t + ((maxY - v) / span) * plotH;

  const dates = [...new Set(sorted.map((p) => String(p.date).slice(0, 10)))];
  const dateIdx = new Map(dates.map((d, i) => [d, i]));
  const xAt = (p, localI) => {
    const di = dateIdx.get(String(p.date).slice(0, 10)) ?? localI;
    const base = dates.length === 1 ? 0.5 : di / (dates.length - 1);
    const jitter = ((localI % 5) - 2) * 0.008;
    return pad.l + (base + jitter) * plotW;
  };

  // 方向分区底色（y_τ>0→反T，y_τ<0→正T）
  ctx.fillStyle = THEME.reverseTLight;
  ctx.fillRect(pad.l, yLine(maxY), plotW, yLine(threshold) - yLine(maxY));
  ctx.fillStyle = THEME.longTLight;
  ctx.fillRect(pad.l, yLine(-threshold), plotW, yLine(minY) - yLine(-threshold));
  ctx.fillStyle = "rgba(148,163,184,0.07)";
  ctx.fillRect(pad.l, yLine(threshold), plotW, yLine(-threshold) - yLine(threshold));

  drawHGrid(ctx, pad, w, h, [
    { frac: 0, label: fmtNum(maxY, 2) },
    { frac: (yLine(0) - pad.t) / plotH, label: "0" },
    { frac: 1, label: fmtNum(minY, 2) },
  ]);

  // ±τ 门槛
  ctx.setLineDash([5, 4]);
  ctx.strokeStyle = THEME.gridStrong;
  ctx.lineWidth = 1;
  [threshold, -threshold].forEach((lv) => {
    const y = yLine(lv);
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(w - pad.r, y);
    ctx.stroke();
    ctx.fillStyle = THEME.axis;
    ctx.font = FONT_SM;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(lv > 0 ? `+${threshold}%` : `-${threshold}%`, pad.l - 4, y);
  });
  ctx.setLineDash([]);

  let nTraded = 0;
  let nSkip = 0;
  let nLong = 0;
  let nRev = 0;
  let nSkipNeg = 0;

  sorted.forEach((p, i) => {
    const yv = Number(p.y_tau);
    const x = xAt(p, i);
    const y = yLine(yv);
    const traded = p.outcome === "traded";
    if (traded) {
      nTraded += 1;
      if (p.direction === "reverse_t") nRev += 1;
      else nLong += 1;
    } else {
      nSkip += 1;
      if (yv < -0.1) nSkipNeg += 1;
    }
    const color = traded
      ? p.direction === "reverse_t"
        ? THEME.reverseT
        : THEME.longT
      : THEME.signalSkip;
    const r = traded ? 4.5 : 3.5;
    ctx.beginPath();
    ctx.arc(x, y, r + 1.5, 0, Math.PI * 2);
    ctx.fillStyle = traded ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.5)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.globalAlpha = traded ? 1 : 0.65;
    ctx.fill();
    ctx.globalAlpha = 1;
  });

  const dateTicks = [0, Math.floor(dates.length / 2), dates.length - 1];
  ctx.fillStyle = THEME.axis;
  ctx.font = FONT_SM;
  ctx.textBaseline = "top";
  dateTicks.forEach((idx) => {
    const x = pad.l + (dates.length === 1 ? plotW / 2 : (idx / (dates.length - 1)) * plotW);
    ctx.textAlign = idx === 0 ? "left" : idx === dates.length - 1 ? "right" : "center";
    ctx.fillText(fmtDateShort(dates[idx]), x, h - pad.b + 4);
  });

  return { nTraded, nSkip, nSkipNeg, nLong, nRev, threshold };
}

/** 跳过构成 — canvas 环形图；返回扇区几何供悬停 hit-test */
function drawSkipDonut(canvas, categories) {
  if (!canvas || !categories || !categories.length) return null;
  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const total = categories.reduce((s, c) => s + Number(c.count || 0), 0) || 1;
  const pad = 12;
  const cx = w / 2;
  const cy = h / 2;
  const outerR = Math.min(w, h) / 2 - pad;
  const innerR = outerR * 0.58;
  const gap = 0.02;
  const segments = [];

  let start = -Math.PI / 2;
  categories.forEach((c) => {
    const frac = Number(c.count || 0) / total;
    if (frac <= 0) return;
    const sweep = Math.max(0, frac * Math.PI * 2 - gap);
    const a0 = start + gap / 2;
    const a1 = start + sweep;
    const color = c.color || skipCatColor(c.id);
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, a0, a1);
    ctx.arc(cx, cy, innerR, a1, a0, true);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    segments.push({
      id: c.id,
      label: c.label || c.id,
      count: Number(c.count || 0),
      pct: c.pct != null ? c.pct : Math.round(frac * 1000) / 10,
      color,
      tip: skipCatTip(c.id, c.label),
      a0,
      a1,
    });
    start += frac * Math.PI * 2;
  });

  ctx.fillStyle = THEME.ink;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "bold 15px var(--font-mono, monospace)";
  ctx.fillText(String(total), cx, cy - 5);
  ctx.font = FONT_SM;
  ctx.fillStyle = THEME.axis;
  ctx.fillText("跳过", cx, cy + 10);

  const top = categories[0];
  return { total, top, cx, cy, outerR, innerR, segments };
}

/** 将 canvas 弧度归一到 [0, 2π)，以 -π/2（12 点）为 0 */
function skipDonutNormAngle(a) {
  let x = a + Math.PI / 2;
  x = ((x % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
  return x;
}

function wireSkipDonutHover(canvas, meta) {
  if (!canvas || !meta || !meta.segments || !meta.segments.length) return;
  const box = canvas.closest(".paper-t0-viz-chart-box") || canvas.parentElement;
  let tipEl = box && box.querySelector('[data-role="skip-hover-tip"]');
  if (box && !tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "paper-t0-viz-skip-hover-tip";
    tipEl.setAttribute("data-role", "skip-hover-tip");
    tipEl.hidden = true;
    box.appendChild(tipEl);
  }
  const size = getChartSize(canvas);
  meta._w = size.w;
  meta._h = size.h;

  const show = (seg, ev) => {
    if (!tipEl) {
      canvas.title = seg
        ? `${seg.label} ${seg.count}（${seg.pct}%）\n${seg.tip}`
        : "";
      return;
    }
    if (!seg) {
      tipEl.hidden = true;
      tipEl.textContent = "";
      canvas.style.cursor = "default";
      if (box) box.classList.remove("is-skip-tip");
      return;
    }
    tipEl.hidden = false;
    if (box) box.classList.add("is-skip-tip");
    tipEl.innerHTML =
      `<strong>${esc(seg.label)}</strong>` +
      `<span class="paper-t0-viz-skip-hover-meta">${esc(String(seg.count))} · ${esc(
        String(seg.pct)
      )}%</span>` +
      `<p>${esc(seg.tip)}</p>`;
    canvas.style.cursor = "pointer";
    if (ev && box) {
      const br = box.getBoundingClientRect();
      const x = Math.min(Math.max(8, ev.clientX - br.left + 12), Math.max(8, br.width - 180));
      const y = Math.min(Math.max(8, ev.clientY - br.top + 12), Math.max(8, br.height - 72));
      tipEl.style.left = `${x}px`;
      tipEl.style.top = `${y}px`;
    }
  };

  canvas.onmousemove = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const lx = ((ev.clientX - rect.left) / Math.max(1, rect.width)) * meta._w;
    const ly = ((ev.clientY - rect.top) / Math.max(1, rect.height)) * meta._h;
    const dx = lx - meta.cx;
    const dy = ly - meta.cy;
    const r = Math.sqrt(dx * dx + dy * dy);
    if (r < meta.innerR || r > meta.outerR) {
      show(null);
      return;
    }
    const t = skipDonutNormAngle(Math.atan2(dy, dx));
    const seg = meta.segments.find((s) => {
      const from = skipDonutNormAngle(s.a0);
      const to = skipDonutNormAngle(s.a1);
      if (to >= from) return t >= from && t <= to;
      return t >= from || t <= to;
    });
    show(seg || null, ev);
  };
  canvas.onmouseleave = () => show(null);
}

/** 方向 PnL — 双向水平柱 */
function drawDirectionPnl(canvas, pnlByDir, split) {
  if (!canvas || !pnlByDir) return null;
  const longP = Number(pnlByDir.long_t || 0);
  const revP = Number(pnlByDir.reverse_t || 0);
  if (!longP && !revP) return null;

  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);

  const pad = { l: 44, r: 52, t: 20, b: 24 };
  const plotW = w - pad.l - pad.r;
  const maxAbs = Math.max(Math.abs(longP), Math.abs(revP), 1);
  const midX = pad.l + plotW / 2;
  const barH = Math.min(24, (h - pad.t - pad.b - 18) / 2);
  const gap = Math.max(12, (h - pad.t - pad.b - barH * 2) / 2);

  drawHGrid(ctx, { l: pad.l, r: pad.r, t: pad.t, b: pad.b }, w, h, [
    { frac: 0.5, label: 0 },
  ]);

  ctx.strokeStyle = THEME.zero;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(midX, pad.t - 4);
  ctx.lineTo(midX, h - pad.b);
  ctx.stroke();

  const rows = [
    { label: "正T", val: longP, color: THEME.longT, light: THEME.longTLight, days: split?.long_t },
    { label: "反T", val: revP, color: THEME.reverseT, light: THEME.reverseTLight, days: split?.reverse_t },
  ];

  rows.forEach((row, i) => {
    const y = pad.t + i * (barH + gap);
    const bw = (Math.abs(row.val) / maxAbs) * (plotW / 2 - 8);
    const x = row.val >= 0 ? midX : midX - bw;
    const grad = ctx.createLinearGradient(x, 0, x + bw, 0);
    grad.addColorStop(0, row.light);
    grad.addColorStop(1, row.color);
    roundRect(ctx, x, y, Math.max(2, bw), barH, 4);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.fillStyle = THEME.axis;
    ctx.font = FONT_SM;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(row.label, pad.l - 6, y + barH / 2);
    if (row.days != null) {
      ctx.fillStyle = THEME.inkMuted;
      ctx.font = "8px var(--font-mono, monospace)";
      ctx.fillText(`${row.days}日`, pad.l - 6, y + barH / 2 + 10);
    }

    ctx.fillStyle = row.val >= 0 ? THEME.pnlUp : THEME.pnlDown;
    ctx.font = FONT;
    ctx.textAlign = row.val >= 0 ? "left" : "right";
    const tx = row.val >= 0 ? x + bw + 6 : x - 6;
    ctx.fillText(fmtMoney(row.val), tx, y + barH / 2);
  });

  const net = longP + revP;
  const longShare = net !== 0 ? Math.round((longP / net) * 1000) / 10 : null;
  return { longP, revP, net, longShare };
}

function renderKpiRow(summary, compare) {
  const sm = summary || {};
  const chips = [
    ["参与率", sm.participate_rate_pct != null ? `${sm.participate_rate_pct}%` : null],
    ["ŷ覆盖", sm.score_coverage_pct != null ? `${sm.score_coverage_pct}%` : null],
    ["往返率", sm.cover_rate_pct != null ? `${sm.cover_rate_pct}%` : null],
    ["τ门槛", sm.y_tau_enter != null ? `|y_τ|≥${sm.y_tau_enter}%` : null],
    ["信号跳过", sm.signal_skip_rate_pct != null ? `${sm.signal_skip_rate_pct}%` : null],
  ];
  if (sm.avg_y_tau_traded != null) chips.push(["成交τ̄", `${fmtNum(sm.avg_y_tau_traded, 2)}%`]);
  if (sm.avg_y_tau_signal_skip != null) chips.push(["跳过τ̄", `${fmtNum(sm.avg_y_tau_signal_skip, 2)}%`]);
  if (compare && compare.delta_ratio_pct != null) chips.push(["乐观Δ", `${compare.delta_ratio_pct}%`]);
  const html = chips
    .filter(([, v]) => v != null)
    .map(
      ([k, v]) =>
        `<span class="paper-t0-viz-kpi"><em>${esc(k)}</em><strong>${esc(v)}</strong></span>`
    )
    .join("");
  return html ? `<div class="paper-t0-viz-kpis">${html}</div>` : "";
}

function legendChips(items) {
  if (!items.length) return "";
  return (
    `<div class="paper-t0-viz-legend-chips">` +
    items
      .map(
        (it) =>
          `<span class="paper-t0-viz-chip">` +
          `<i style="background:${esc(it.color)}"></i>${esc(it.label)}` +
          (it.val != null ? `<b>${esc(String(it.val))}</b>` : "") +
          `</span>`
      )
      .join("") +
    `</div>`
  );
}

function skipLegendHtml(categories, total) {
  const max = Math.max(1, ...categories.map((c) => Number(c.count || 0)));
  return (
    `<div class="paper-t0-viz-skip-legend">` +
    categories
      .map((c) => {
        const cnt = Number(c.count || 0);
        const pct = c.pct != null ? c.pct : Math.round((cnt / total) * 1000) / 10;
        const barW = Math.round((cnt / max) * 100);
        const tip = skipCatTip(c.id, c.label);
        const title = `${c.label || c.id} · ${cnt}（${pct}%）\n${tip}`;
        return (
          `<div class="paper-t0-viz-skip-row" title="${esc(title)}" tabindex="0">` +
          `<span class="paper-t0-viz-dot" style="background:${esc(c.color || skipCatColor(c.id))}"></span>` +
          `<span class="paper-t0-viz-skip-label">${esc(c.label)}</span>` +
          `<span class="paper-t0-viz-skip-bar"><i style="width:${barW}%;background:${esc(
            c.color || skipCatColor(c.id)
          )}"></i></span>` +
          `<span class="paper-t0-viz-skip-val">${cnt} <em>${pct}%</em></span>` +
          `</div>`
        );
      })
      .join("") +
    `</div>`
  );
}

function skipReasonCellHtml(r) {
  const skipDays = Number(r.skip_days || 0);
  if (!skipDays) {
    if (Number(r.trade_days || 0) > 0) {
      return (
        `<td class="paper-t0-col-viz-skip">` +
        `<span class="paper-t0-skip-none paper-t0-skip-none--ok">全成交</span></td>`
      );
    }
    return `<td class="paper-t0-col-viz-skip"><span class="paper-t0-skip-none">—</span></td>`;
  }
  const breakdown =
    r.skip_breakdown && r.skip_breakdown.length
      ? r.skip_breakdown
      : r.skip_top_id
        ? [
            {
              id: r.skip_top_id,
              label: r.skip_top_label,
              count: r.skip_top_count,
              pct: r.skip_top_pct,
              color: skipCatColor(r.skip_top_id),
            },
          ]
        : [];
  if (!breakdown.length) {
    return `<td class="paper-t0-col-viz-skip"><span class="paper-t0-skip-none">—</span></td>`;
  }
  const top = breakdown[0];
  const color = top.color || skipCatColor(top.id);
  const tip = breakdown
    .map((c) => {
      const pct =
        c.pct != null
          ? c.pct
          : skipDays
            ? Math.round((Number(c.count || 0) / skipDays) * 1000) / 10
            : null;
      const def = skipCatTip(c.id, c.label);
      return `${c.label} ${c.count ?? 0}${pct != null ? ` (${pct}%)` : ""} — ${def}`;
    })
    .join("\n");
  const stack = breakdown
    .map((c) => {
      const w =
        skipDays > 0 ? Math.round((Number(c.count || 0) / skipDays) * 100) : 0;
      const bg = c.color || skipCatColor(c.id);
      return `<i style="width:${Math.max(w, w > 0 ? 2 : 0)}%;background:${esc(bg)}" title="${esc(c.label)}"></i>`;
    })
    .join("");
  const topPct =
    top.pct != null
      ? top.pct
      : skipDays
        ? Math.round((Number(top.count || 0) / skipDays) * 1000) / 10
        : null;
  return (
    `<td class="paper-t0-col-viz-skip">` +
    `<div class="paper-t0-skip-cell" title="${esc(tip)}">` +
    `<div class="paper-t0-skip-head">` +
    `<span class="paper-t0-skip-pill" style="--skip-c:${esc(color)}">` +
    `<i class="paper-t0-skip-dot" aria-hidden="true"></i>` +
    `<span class="paper-t0-skip-lbl">${esc(top.label)}</span>` +
    `<b class="paper-t0-skip-cnt">${esc(String(top.count ?? ""))}</b>` +
    `</span>` +
    (topPct != null ? `<span class="paper-t0-skip-pct">${esc(String(topPct))}%</span>` : "") +
    `</div>` +
    `<div class="paper-t0-skip-stack" aria-hidden="true">${stack}</div>` +
    `</div></td>`
  );
}

function fmtContribShares(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return esc(Math.round(n).toLocaleString("zh-CN"));
}

function fmtContribPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return esc(n.toFixed(2));
}

function fmtContribDate(v) {
  const s = String(v || "").trim();
  if (!s) return "—";
  return esc(s.slice(0, 10));
}

function fmtContribReturn(r) {
  const pct = r.return_pct != null ? Number(r.return_pct) : null;
  if (pct == null || !Number.isFinite(pct)) {
    return { text: "—", cls: "", tip: "缺股数或开始价，无法算累计收益%" };
  }
  const sign = pct > 0 ? "+" : "";
  return {
    text: `${sign}${pct.toFixed(2)}%`,
    cls: paperMetricClass(pct),
    tip: `含敞口净 PnL ${r.net_pnl ?? r.pnl ?? 0} / 虚拟底仓市值 ${r.hold_mv_start ?? "—"}`,
  };
}

function renderStockContrib(rows) {
  if (!rows || !rows.length) return "";
  const body = rows
    .map((r) => {
      const pnl = Number(r.pnl || 0);
      const cls = paperMetricClass(pnl);
      const ret = fmtContribReturn(r);
      const fallback = {
        stock_code: r.stock_code,
        stock_name: r.stock_name,
      };
      return (
        `<tr class="paper-t0-contrib-row">` +
        stockCellHtml(r, fallback) +
        `<td class="num paper-t0-col-viz-shares">${fmtContribShares(r.shares)}</td>` +
        `<td class="paper-t0-col-viz-dt">${fmtContribDate(r.window_start_date)}</td>` +
        `<td class="num paper-t0-col-viz-px">${fmtContribPrice(r.start_price)}</td>` +
        `<td class="paper-t0-col-viz-dt">${fmtContribDate(r.window_end_date)}</td>` +
        `<td class="num paper-t0-col-viz-px">${fmtContribPrice(r.end_price)}</td>` +
        `<td class="num paper-t0-col-viz-lr">${esc(`${r.long_days ?? 0}/${r.reverse_days ?? 0}`)}</td>` +
        `<td class="num paper-t0-col-viz-days">${esc(String(r.trade_days ?? 0))}</td>` +
        `<td class="num paper-t0-col-viz-days">${esc(String(r.skip_days ?? 0))}</td>` +
        skipReasonCellHtml(r) +
        `<td class="num paper-t0-col-viz-pct">${
          r.participate_rate_pct != null ? esc(`${r.participate_rate_pct}%`) : "—"
        }</td>` +
        `<td class="num paper-t0-col-viz-ret ${ret.cls}" title="${esc(ret.tip)}">${esc(ret.text)}</td>` +
        `<td class="num paper-t0-col-pnl ${cls}">${esc(pnl.toFixed(1))}</td>` +
        `</tr>`
      );
    })
    .join("");
  return (
    `<div class="quant-weight-table-wrap paper-t0-viz-stock-wrap watching-table-scroll">` +
    `<table class="quant-weight-table paper-t0-table paper-t0-viz-stock-table watching-desk-table">` +
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-viz-shares" />` +
    `<col class="paper-t0-col-viz-dt" />` +
    `<col class="paper-t0-col-viz-px" />` +
    `<col class="paper-t0-col-viz-dt" />` +
    `<col class="paper-t0-col-viz-px" />` +
    `<col class="paper-t0-col-viz-lr" />` +
    `<col class="paper-t0-col-viz-days" />` +
    `<col class="paper-t0-col-viz-days" />` +
    `<col class="paper-t0-col-viz-skip" />` +
    `<col class="paper-t0-col-viz-pct" />` +
    `<col class="paper-t0-col-viz-ret" />` +
    `<col class="paper-t0-col-pnl" />` +
    `</colgroup>` +
    `<thead><tr class="paper-t0-contrib-head">` +
    `<th scope="col" class="paper-t0-col-stock">股票</th>` +
    `<th scope="col" class="paper-t0-col-viz-shares num" title="回测虚拟底仓股数">股数</th>` +
    `<th scope="col" class="paper-t0-col-viz-dt" title="评估窗口首日">开始日期</th>` +
    `<th scope="col" class="paper-t0-col-viz-px num" title="窗口首日收盘价">开始价</th>` +
    `<th scope="col" class="paper-t0-col-viz-dt" title="评估窗口末日">结束日期</th>` +
    `<th scope="col" class="paper-t0-col-viz-px num" title="窗口末日收盘价">结束价</th>` +
    `<th scope="col" class="paper-t0-col-viz-lr num" title="正T日 / 反T日">正/反</th>` +
    `<th scope="col" class="paper-t0-col-viz-days num">成交</th>` +
    `<th scope="col" class="paper-t0-col-viz-days num">跳过</th>` +
    `<th scope="col" class="paper-t0-col-viz-skip" title="跳过主因（色标+占比条；悬停看构成）">主因</th>` +
    `<th scope="col" class="paper-t0-col-viz-pct num" title="成交日占评估日">参与%</th>` +
    `<th scope="col" class="paper-t0-col-viz-ret num" title="含敞口净PnL/虚拟底仓市值">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>`
  );
}

function vizCard(title, subtitle, chartInner, footHtml = "", legendHtml = "") {
  return (
    `<div class="paper-t0-viz-card paper-t0-viz-card--chart">` +
    `<div class="paper-t0-viz-card-head">` +
    `<h4>${esc(title)}</h4>` +
    (subtitle ? `<span class="paper-t0-viz-card-sub">${esc(subtitle)}</span>` : "") +
    `</div>` +
    `<div class="paper-t0-viz-chart-box">${chartInner}</div>` +
    `<div class="paper-t0-viz-card-foot">${footHtml}</div>` +
    `<div class="paper-t0-viz-card-legend">${legendHtml}</div>` +
    `</div>`
  );
}

/**
 * @param {HTMLElement|null} host
 * @param {object|null} data
 */
export function renderT0Viz(host, data) {
  if (!host) return;
  // 成交明细可能被挂进 viz；重绘前先挪回宿主后，避免 innerHTML 清掉节点
  const daysHost = host.querySelector("#paper-t0-days");
  if (daysHost) host.insertAdjacentElement("afterend", daysHost);

  if (!data || !data.success || !data.viz) {
    if (host._t0VizRo) host._t0VizRo.disconnect();
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  const viz = data.viz;
  const sm = viz.summary || {};
  const hasContent =
    (viz.skip_categories && viz.skip_categories.length) ||
    (viz.cumulative_pnl && viz.cumulative_pnl.length) ||
    (viz.daily_activity && viz.daily_activity.length > 1) ||
    (viz.y_tau_scatter && viz.y_tau_scatter.length) ||
    (viz.stock_contrib && viz.stock_contrib.length) ||
    viz.compare ||
    viz.pnl_by_direction;
  if (!hasContent) {
    if (host._t0VizRo) host._t0VizRo.disconnect();
    host.hidden = true;
    host.innerHTML = "";
    return;
  }

  const ds = viz.direction_split || {};
  const tauEnter = sm.y_tau_enter != null ? sm.y_tau_enter : 0.25;
  const cards = [];

  if (viz.cumulative_pnl && viz.cumulative_pnl.length) {
    cards.push(
      vizCard(
        "累计 PnL",
        `${viz.trade_count ?? 0} 笔成交`,
        chartCanvas("cum"),
        `<div data-role="cum-foot"></div>`,
        legendChips([{ color: THEME.actual, label: "累计 PnL" }])
      )
    );
  }

  if (viz.compare) {
    cards.push(
      vizCard(
        "实际 vs 乐观",
        "上界对照 · 卖高买低",
        chartCanvas("compare"),
        `<div data-role="compare-foot"></div>`,
        legendChips([
          { color: THEME.actual, label: "实际 PnL" },
          { color: THEME.optimistic, label: "乐观上界" },
        ])
      )
    );
  }

  if (viz.daily_activity && viz.daily_activity.length > 1) {
    cards.push(
      vizCard(
        "日度活跃",
        "近 50 评估日 · 堆叠",
        chartCanvas("activity"),
        `<div data-role="activity-foot"></div>`,
        legendChips([
          { color: THEME.longT, label: "成交" },
          { color: THEME.signalSkip, label: "信号跳过" },
          { color: THEME.otherSkip, label: "其它跳过" },
        ])
      )
    );
  }

  if (viz.y_tau_scatter && viz.y_tau_scatter.length) {
    cards.push(
      vizCard(
        "y_τ 散点",
        `|y_τ|≥${tauEnter}% 门槛 · 中间灰带=|y_τ|<τ · 橙=信号跳过（不含振幅/未触达跳过）`,
        chartCanvas("scatter"),
        `<div data-role="scatter-foot"></div>`,
        legendChips([
          { color: THEME.longT, label: "正T成交" },
          { color: THEME.reverseT, label: "反T成交" },
          { color: THEME.signalSkip, label: "信号跳过(含负τ)" },
        ])
      )
    );
  }

  if (viz.skip_categories && viz.skip_categories.length) {
    const total = viz.skip_categories.reduce((s, c) => s + Number(c.count || 0), 0);
    cards.push(
      vizCard(
        "跳过构成",
        `合计 ${total} 次 · 悬停扇区/图例看口径`,
        chartCanvas("skip"),
        skipLegendHtml(viz.skip_categories, total),
        legendChips([{ color: THEME.otherSkip, label: "按跳过原因" }])
      )
    );
  }

  if (viz.pnl_by_direction) {
    cards.push(
      vizCard(
        "方向 PnL",
        "相对零轴 · 正T / 反T",
        chartCanvas("dirpnl"),
        `<div data-role="dir-foot"></div>`,
        legendChips([
          { color: THEME.longT, label: "正T" },
          { color: THEME.reverseT, label: "反T" },
        ])
      )
    );
  }

  const contribHtml =
    viz.stock_contrib && viz.stock_contrib.length >= 1
      ? `<section class="paper-t0-viz-contrib">` +
        `<div class="paper-t0-viz-contrib-head"><h4>分票贡献</h4></div>` +
        renderStockContrib(viz.stock_contrib) +
        `</section>`
      : "";

  host.hidden = false;
  host.innerHTML =
    `<div class="paper-t0-viz-head">` +
    `<span class="paper-t0-viz-title">归因分析</span>` +
    `<span class="quant-sub">正T ${ds.long_t ?? 0} · 反T ${ds.reverse_t ?? 0} · 成交 ${viz.trade_count ?? 0} · 跳过 ${viz.skip_count ?? 0}</span>` +
    `</div>` +
    renderKpiRow(sm, viz.compare) +
    `<div class="paper-t0-viz-grid">${cards.join("")}</div>` +
    contribHtml;

  function paintCharts() {
    const cum = host.querySelector('canvas[data-viz="cum"]');
    if (cum) {
      const meta = drawCumulativePnl(cum, viz.cumulative_pnl);
      const foot = host.querySelector('[data-role="cum-foot"]');
      if (foot && meta) {
        const cls = meta.total >= 0 ? "up" : "down";
        foot.innerHTML = [
          `<span>期末 <b class="${cls}">${fmtMoney(meta.total)}</b></span>`,
          meta.delta != null && meta.n > 1
            ? `<span>区间 <b class="${meta.delta >= 0 ? "up" : "down"}">${fmtMoney(meta.delta)}</b></span>`
            : "",
          `<span>${meta.n} 点</span>`,
        ]
          .filter(Boolean)
          .join('<span class="sep">·</span>');
      }
    }

    const cmp = host.querySelector('canvas[data-viz="compare"]');
    if (cmp) {
      const meta = drawCompareBars(cmp, viz.compare);
      const foot = host.querySelector('[data-role="compare-foot"]');
      if (foot && meta) {
        const lines = [];
        if (meta.capture != null) lines.push(`<span>兑现率 <b>${meta.capture}%</b></span>`);
        if (meta.delta != null) {
          const cls = meta.delta >= 0 ? "up" : "down";
          lines.push(`<span>未捕获空间 <b class="${cls}">${fmtMoney(meta.delta)}</b></span>`);
        }
        if (meta.deltaRatio != null) lines.push(`<span>Δ占比 ${meta.deltaRatio}%</span>`);
        foot.innerHTML = lines.join('<span class="sep">·</span>');
      }
    }

    const act = host.querySelector('canvas[data-viz="activity"]');
    if (act) {
      const meta = drawDailyActivity(act, viz.daily_activity);
      const foot = host.querySelector('[data-role="activity-foot"]');
      if (foot && meta) {
        foot.innerHTML = [
          `<span>成交 <b>${meta.sumTraded}</b></span>`,
          `<span>信号跳过 <b>${meta.sumSignal}</b></span>`,
          `<span>其它 <b>${meta.sumOther}</b></span>`,
          `<span>${meta.days} 日窗口</span>`,
        ].join('<span class="sep">·</span>');
      }
    }

    const sc = host.querySelector('canvas[data-viz="scatter"]');
    if (sc) {
      const meta = drawYtauScatter(sc, viz.y_tau_scatter, tauEnter);
      const foot = host.querySelector('[data-role="scatter-foot"]');
      if (foot && meta) {
        foot.innerHTML = [
          `<span>成交 <b>${meta.nTraded}</b>（正${meta.nLong}/反${meta.nRev}）</span>`,
          `<span>信号跳过 <b>${meta.nSkip}</b></span>`,
          meta.nSkipNeg > 0
            ? `<span>负τ跳过(≤−0.1%) <b>${meta.nSkipNeg}</b> · 多在灰带|y_τ|&lt;τ</span>`
            : "",
          `<span>门槛 |y_τ|≥${meta.threshold}%</span>`,
        ]
          .filter(Boolean)
          .join('<span class="sep">·</span>');
      }
    }

    const skip = host.querySelector('canvas[data-viz="skip"]');
    if (skip) {
      const meta = drawSkipDonut(skip, viz.skip_categories);
      wireSkipDonutHover(skip, meta);
    }

    const dir = host.querySelector('canvas[data-viz="dirpnl"]');
    if (dir) {
      const meta = drawDirectionPnl(dir, viz.pnl_by_direction, ds);
      const foot = host.querySelector('[data-role="dir-foot"]');
      if (foot && meta) {
        const netCls = meta.net >= 0 ? "up" : "down";
        foot.innerHTML = [
          `<span>合计 <b class="${netCls}">${fmtMoney(meta.net)}</b></span>`,
          meta.longShare != null ? `<span>正T贡献 <b>${meta.longShare}%</b></span>` : "",
        ]
          .filter(Boolean)
          .join('<span class="sep">·</span>');
      }
    }
  }

  requestAnimationFrame(() => {
    paintCharts();
    if (host._t0VizRo) host._t0VizRo.disconnect();
    const grid = host.querySelector(".paper-t0-viz-grid");
    if (grid && typeof ResizeObserver !== "undefined") {
      host._t0VizRo = new ResizeObserver(() => {
        requestAnimationFrame(paintCharts);
      });
      host._t0VizRo.observe(grid);
    }
  });
}
