/** 做T回测可视化（canvas + CSS，无外部图表库）。 */

import { paperMetricClass } from "./fmt.js";
import { SKIP_CAT_TIP, stockCellHtml } from "./t0_table.js?v=p1986";

const THEME = {
  actual: "#2563eb",
  actualLight: "rgba(37,99,235,0.15)",
  sellThenBuy: "#059669",
  sellThenBuyLight: "rgba(5,150,105,0.14)",
  buyThenSell: "#dc2626",
  buyThenSellLight: "rgba(220,38,38,0.12)",
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
  // 缺数 / 中性石板
  missing_minute: "#5c6b7a",
  missing_scores: "#8b98a5",
  y_path_missing: "#44525f",
  price_space_mismatch: "#6a7380",
  trigger_miss: "#b8c0c8",
  other: "#cbd2d9",
  amplitude: "#6e7378",
  directional_amplitude: "#9aa0a6",
  // 门槛不足 · 冷钢蓝 / 青灰
  y_eod_flat: "#3d6a8a",
  y_tau_flat: "#3a7a72",
  r_tau_flat: "#4a6e7a",
  y_tau_weak: "#5a7d8c",
  y_path_flat: "#7a6a55",
  // 异号 / 冲突 · 克制酒红 / 梅紫
  eod_tau_disagree: "#b33a3a",
  trade_tau_disagree: "#8f3d5b",
  tau_leg1_prior: "#a0653a",
  trade_tau_sign: "#c45c4a",
  y_path_disagree: "#6b4c7a",
  conflict: "#a04848",
  // 前缀 / 空间 / 缺口 · 海石青 + 一枚赭石
  path_abandon: "#3f6f68",
  prefix_segment: "#3a6480",
  prefix_vs_path: "#2f5370",
  tau_entry_price: "#2a5f7a",
  tau_exit_price: "#356b85",
  gap_tier_skip: "#b07a3a",
  y_trade_weak: "#9a5b32",
  path: "#4a5f8a",
  // 约束类 · 灰紫 / 藕色
  lot_size: "#6b5b8a",
  cash: "#8a5a6e",
  tplus1: "#6e5c82",
  intraday_legs_open: "#4f7a6e",
};

function skipCatColor(id) {
  return SKIP_CAT_COLORS[id] || "#94a3b8";
}

/** 已知 id 用前端色板（避免旧 payload 的近色 color 把扇区糊成一团） */
function resolveSkipColor(c) {
  const id = c && c.id != null ? String(c.id) : "";
  if (id && Object.prototype.hasOwnProperty.call(SKIP_CAT_COLORS, id)) {
    return SKIP_CAT_COLORS[id];
  }
  const raw = String((c && c.color) || "").trim();
  return raw || skipCatColor(id);
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
      { n: Number(r.traded || 0), c: THEME.sellThenBuy },
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

  // 方向分区底色（y_τ>0→正T，y_τ<0→反T）
  ctx.fillStyle = THEME.buyThenSellLight;
  ctx.fillRect(pad.l, yLine(maxY), plotW, yLine(threshold) - yLine(maxY));
  ctx.fillStyle = THEME.sellThenBuyLight;
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
  let nSellThenBuy = 0;
  let nBuyThenSell = 0;
  let nSkipNeg = 0;

  sorted.forEach((p, i) => {
    const yv = Number(p.y_tau);
    const x = xAt(p, i);
    const y = yLine(yv);
    const traded = p.outcome === "traded";
    if (traded) {
      nTraded += 1;
      if (p.direction === "buy_then_sell") nBuyThenSell += 1;
      else nSellThenBuy += 1;
    } else {
      nSkip += 1;
      if (yv < -0.1) nSkipNeg += 1;
    }
    const color = traded
      ? p.direction === "buy_then_sell"
        ? THEME.buyThenSell
        : THEME.sellThenBuy
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

  return { nTraded, nSkip, nSkipNeg, nSellThenBuy, nBuyThenSell, threshold };
}

/** 跳过构成 — CSS conic-gradient 环形（避免 canvas 填色在部分环境下糊成单色） */
function buildSkipDonutMeta(categories) {
  if (!categories || !categories.length) return null;
  const total = categories.reduce((s, c) => s + Number(c.count || 0), 0) || 1;
  const segments = [];
  const stops = [];
  let start = -Math.PI / 2;
  let degAcc = 0;
  categories.forEach((c) => {
    const frac = Number(c.count || 0) / total;
    if (frac <= 0) return;
    const color = resolveSkipColor(c);
    const a0 = start;
    const a1 = start + frac * Math.PI * 2;
    const d0 = degAcc;
    degAcc += frac * 360;
    stops.push(`${color} ${d0.toFixed(3)}deg ${degAcc.toFixed(3)}deg`);
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
    start = a1;
  });
  if (!segments.length) return null;
  return {
    total,
    top: categories[0],
    segments,
    gradient: `conic-gradient(from -90deg, ${stops.join(", ")})`,
  };
}

function skipDonutChartHtml(categories) {
  const meta = buildSkipDonutMeta(categories);
  if (!meta) return "";
  return (
    `<div class="paper-t0-viz-skip-donut" data-viz="skip-donut" ` +
    `style="background:${esc(meta.gradient)}" role="img" ` +
    `aria-label="跳过构成 ${meta.total}">` +
    `<div class="paper-t0-viz-skip-donut-core">` +
    `<b data-role="skip-donut-total">${esc(String(meta.total))}</b>` +
    `<span>跳过</span>` +
    `</div></div>`
  );
}

/** @deprecated canvas 路径保留给调试；正式渲染用 skipDonutChartHtml */
function drawSkipDonut(canvas, categories) {
  const meta = buildSkipDonutMeta(categories);
  if (!canvas || !meta) return null;
  const { w, h } = getChartSize(canvas);
  const ctx = setupCanvas(canvas, w, h);
  ctx.clearRect(0, 0, w, h);
  const pad = 12;
  const cx = w / 2;
  const cy = h / 2;
  const outerR = Math.min(w, h) / 2 - pad;
  const innerR = outerR * 0.58;
  meta.segments.forEach((seg) => {
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, seg.a0, seg.a1);
    ctx.arc(cx, cy, innerR, seg.a1, seg.a0, true);
    ctx.closePath();
    ctx.fillStyle = seg.color;
    ctx.fill();
  });
  meta.cx = cx;
  meta.cy = cy;
  meta.outerR = outerR;
  meta.innerR = innerR;
  return meta;
}

/** 将弧度归一到 [0, 2π)，以 -π/2（12 点）为 0 */
function skipDonutNormAngle(a) {
  let x = a + Math.PI / 2;
  x = ((x % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
  return x;
}

function wireSkipDonutHover(el, meta) {
  if (!el || !meta || !meta.segments || !meta.segments.length) return;
  const box = el.closest(".paper-t0-viz-chart-box") || el.parentElement;
  let tipEl = box && box.querySelector('[data-role="skip-hover-tip"]');
  if (box && !tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "paper-t0-viz-skip-hover-tip";
    tipEl.setAttribute("data-role", "skip-hover-tip");
    tipEl.hidden = true;
    box.appendChild(tipEl);
  }

  const syncGeom = () => {
    const br = el.getBoundingClientRect();
    meta._w = br.width;
    meta._h = br.height;
    meta.cx = br.width / 2;
    meta.cy = br.height / 2;
    meta.outerR = Math.min(br.width, br.height) / 2;
    meta.innerR = meta.outerR * 0.58;
  };
  syncGeom();

  const show = (seg, ev) => {
    if (!tipEl) {
      el.title = seg ? `${seg.label} ${seg.count}（${seg.pct}%）\n${seg.tip}` : "";
      return;
    }
    if (!seg) {
      tipEl.hidden = true;
      tipEl.textContent = "";
      el.style.cursor = "default";
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
    el.style.cursor = "pointer";
    if (ev && box) {
      const br = box.getBoundingClientRect();
      const x = Math.min(Math.max(8, ev.clientX - br.left + 12), Math.max(8, br.width - 180));
      const y = Math.min(Math.max(8, ev.clientY - br.top + 12), Math.max(8, br.height - 72));
      tipEl.style.left = `${x}px`;
      tipEl.style.top = `${y}px`;
    }
  };

  el.onmousemove = (ev) => {
    syncGeom();
    const rect = el.getBoundingClientRect();
    const lx = ev.clientX - rect.left;
    const ly = ev.clientY - rect.top;
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
  el.onmouseleave = () => show(null);
}

function renderKpiRow(summary) {
  const sm = summary || {};
  const chips = [
    [
      "参与率",
      sm.participate_rate_pct != null ? `${sm.participate_rate_pct}%` : null,
      "成交日 / 可评估日",
    ],
    [
      "ŷ覆盖",
      sm.score_coverage_pct != null ? `${sm.score_coverage_pct}%` : null,
      "有 y_τ 快照的评估日占比",
    ],
    [
      "往返率",
      sm.cover_rate_pct != null ? `${sm.cover_rate_pct}%` : null,
      "卖出回补或买入卖回完成比例",
    ],
    [
      "τ门槛",
      sm.y_tau_enter != null ? `|y_τ|≥${sm.y_tau_enter}%` : null,
      "|y_τ| 低于入场则横盘跳过",
    ],
    [
      "R门槛",
      sm.r_tau_enter != null && Number.isFinite(Number(sm.r_tau_enter))
        ? Number(sm.r_tau_enter) > 0
          ? `|R̂_τ|≥${sm.r_tau_enter}%`
          : "关"
        : `|R̂_τ|≥0.1%`,
      "|R̂_τ| 低于入场则超额不足跳过（0–1.0%；0=关）",
    ],
    [
      "信号跳过",
      sm.signal_skip_rate_pct != null ? `${sm.signal_skip_rate_pct}%` : null,
      "dual_y / 异号 / 幅度不足等信号层跳过占比",
    ],
    [
      "τ·OC命中",
      sm.tau_oc_hit_rate_pct != null ? `${sm.tau_oc_hit_rate_pct}%` : null,
      "成交日 y_τ 符号 vs 实际 open→close",
    ],
    [
      "path一致",
      sm.path_agree_rate_pct != null ? `${sm.path_agree_rate_pct}%` : null,
      "成交日 y_τ 与 y_path 同号率",
    ],
  ];
  if (sm.avg_y_tau_traded != null) {
    chips.push(["成交τ̄", `${fmtNum(sm.avg_y_tau_traded, 2)}%`, "成交日平均 y_τ"]);
  }
  if (sm.avg_y_tau_signal_skip != null) {
    chips.push(["跳过τ̄", `${fmtNum(sm.avg_y_tau_signal_skip, 2)}%`, "信号跳过日平均 y_τ"]);
  }
  const html = chips
    .filter(([, v]) => v != null)
    .map(
      ([k, v, tip]) =>
        `<span class="paper-t0-viz-kpi has-tip" title="${esc(tip || "")}">` +
        `<em>${esc(k)}</em><strong>${esc(v)}</strong></span>`
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
        const payload = {
          kind: "legend",
          skipDays: total,
          breakdown: [
            {
              id: c.id,
              label: c.label,
              count: cnt,
              pct,
              color: resolveSkipColor(c),
              tip,
            },
          ],
        };
        return (
          `<div class="paper-t0-viz-skip-row has-tip" data-skip-tip="${esc(
            JSON.stringify(payload)
          )}" tabindex="0">` +
          `<span class="paper-t0-viz-dot" style="background:${esc(resolveSkipColor(c))}"></span>` +
          `<span class="paper-t0-viz-skip-label">${esc(c.label)}</span>` +
          `<span class="paper-t0-viz-skip-bar"><i style="width:${barW}%;background:${esc(
            resolveSkipColor(c)
          )}"></i></span>` +
          `<span class="paper-t0-viz-skip-val">${cnt} <em>${pct}%</em></span>` +
          `</div>`
        );
      })
      .join("") +
    `</div>`
  );
}

/** 主因悬浮卡 HTML（替代原生 title） */
export function buildSkipTipHtml(payload) {
  if (!payload || typeof payload !== "object") return "";
  if (payload.ok) {
    return (
      `<div class="paper-t0-skip-tip-inner">` +
      `<header class="paper-t0-skip-tip-head">` +
      `<span class="paper-t0-skip-tip-badge is-ok">全成交</span>` +
      `<span class="paper-t0-skip-tip-eyebrow">分票 · 跳过主因</span>` +
      `</header>` +
      `<p class="paper-t0-skip-tip-lead">该票评估窗内无跳过日，全部进入成交路径。</p>` +
      `</div>`
    );
  }
  const skipDays = Number(payload.skipDays || 0);
  const breakdown = Array.isArray(payload.breakdown) ? payload.breakdown : [];
  if (!breakdown.length) return "";
  const top = breakdown[0];
  const kind = payload.kind === "legend" ? "跳过构成" : "分票 · 跳过主因";
  const rows = breakdown
    .map((c, i) => {
      const color = resolveSkipColor(c);
      const tip = c.tip || skipCatTip(c.id, c.label);
      const pct =
        c.pct != null
          ? c.pct
          : skipDays
            ? Math.round((Number(c.count || 0) / skipDays) * 1000) / 10
            : null;
      const isTop = i === 0;
      return (
        `<div class="paper-t0-skip-tip-row${isTop ? " is-top" : ""}">` +
        `<div class="paper-t0-skip-tip-row-head">` +
        `<i class="paper-t0-skip-tip-swatch" style="background:${esc(color)}" aria-hidden="true"></i>` +
        `<strong class="paper-t0-skip-tip-name">${esc(c.label || c.id || "—")}</strong>` +
        `<span class="paper-t0-skip-tip-stat">` +
        `<b>${esc(String(c.count ?? 0))}</b>` +
        (pct != null ? `<em>${esc(String(pct))}%</em>` : "") +
        `</span>` +
        `</div>` +
        (tip ? `<p class="paper-t0-skip-tip-def">${esc(tip)}</p>` : "") +
        `</div>`
      );
    })
    .join("");
  const stack =
    skipDays > 0 && breakdown.length
      ? `<div class="paper-t0-skip-tip-stack" aria-hidden="true">` +
        breakdown
          .map((c) => {
            const w = Math.round((Number(c.count || 0) / skipDays) * 100);
            const bg = resolveSkipColor(c);
            return `<i style="width:${Math.max(w, w > 0 ? 2 : 0)}%;background:${esc(bg)}"></i>`;
          })
          .join("") +
        `</div>`
      : "";
  return (
    `<div class="paper-t0-skip-tip-inner">` +
    `<header class="paper-t0-skip-tip-head">` +
    `<span class="paper-t0-skip-tip-badge" style="--skip-c:${esc(
      resolveSkipColor(top)
    )}">${esc(top.label || top.id || "主因")}</span>` +
    `<span class="paper-t0-skip-tip-eyebrow">${esc(kind)}</span>` +
    `</header>` +
    `<p class="paper-t0-skip-tip-lead">` +
    (payload.kind === "legend"
      ? `本类 ${esc(String(top.count ?? 0))} 次` +
        (top.pct != null ? ` · 占跳过 ${esc(String(top.pct))}%` : "")
      : `跳过 ${esc(String(skipDays))} 日` +
        (top.label ? ` · 主因 ${esc(top.label)}` : "")) +
    `</p>` +
    stack +
    `<div class="paper-t0-skip-tip-list">${rows}</div>` +
    `<p class="paper-t0-skip-tip-foot">色标=跳过分类 · 占比相对本票/全样本跳过日</p>` +
    `</div>`
  );
}

/** 在 viz / 分票表宿主上挂主因专业 tip（事件委托，重绘后仍有效） */
export function wireT0SkipTips(host, tipCtrl) {
  if (!host || !tipCtrl || typeof tipCtrl.bindAttrTip !== "function") return;
  tipCtrl.bindAttrTip(host, {
    selector: "[data-skip-tip]",
    className: "score-tooltip paper-t0-skip-tip",
    buildHtml: (el) => {
      try {
        return buildSkipTipHtml(JSON.parse(el.getAttribute("data-skip-tip") || "{}"));
      } catch (_) {
        return "";
      }
    },
  });
}

function skipReasonCellHtml(r) {
  const skipDays = Number(r.skip_days || 0);
  if (!skipDays) {
    if (Number(r.trade_days || 0) > 0) {
      const payload = { ok: true };
      return (
        `<td class="paper-t0-col-viz-skip">` +
        `<span class="paper-t0-skip-none paper-t0-skip-none--ok has-tip" data-skip-tip="${esc(
          JSON.stringify(payload)
        )}">全成交</span></td>`
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
  const color = resolveSkipColor(top);
  const tipPayload = {
    kind: "stock",
    skipDays,
    breakdown: breakdown.map((c) => ({
      id: c.id,
      label: c.label,
      count: c.count,
      pct:
        c.pct != null
          ? c.pct
          : skipDays
            ? Math.round((Number(c.count || 0) / skipDays) * 1000) / 10
            : null,
      color: resolveSkipColor(c),
      tip: skipCatTip(c.id, c.label),
    })),
  };
  const stack = breakdown
    .map((c) => {
      const w =
        skipDays > 0 ? Math.round((Number(c.count || 0) / skipDays) * 100) : 0;
      const bg = resolveSkipColor(c);
      return `<i style="width:${Math.max(w, w > 0 ? 2 : 0)}%;background:${esc(bg)}"></i>`;
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
    `<div class="paper-t0-skip-cell has-tip" data-skip-tip="${esc(
      JSON.stringify(tipPayload)
    )}">` +
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

function contribInsight(rows) {
  if (!rows || !rows.length) return { meta: "", tip: "" };
  const n = rows.length;
  const totalPnl = rows.reduce((s, r) => s + Number(r.pnl || 0), 0);
  const skipVotes = {};
  rows.forEach((r) => {
    const id = r.skip_top_id || (r.skip_breakdown && r.skip_breakdown[0] && r.skip_breakdown[0].id);
    const label =
      r.skip_top_label ||
      (r.skip_breakdown && r.skip_breakdown[0] && r.skip_breakdown[0].label) ||
      id;
    if (!id) return;
    if (!skipVotes[id]) skipVotes[id] = { id, label, n: 0, count: 0 };
    skipVotes[id].n += 1;
    skipVotes[id].count += Number(r.skip_top_count || (r.skip_breakdown && r.skip_breakdown[0]?.count) || 0);
  });
  const topSkip = Object.values(skipVotes).sort((a, b) => b.n - a.n || b.count - a.count)[0];
  const best = [...rows].sort((a, b) => Number(b.pnl || 0) - Number(a.pnl || 0))[0];
  const worst = [...rows].sort((a, b) => Number(a.pnl || 0) - Number(b.pnl || 0))[0];
  const bits = [
    `${n} 只`,
    `合计 ${fmtMoney(totalPnl)}`,
    topSkip ? `主因 ${topSkip.label}×${topSkip.n}` : null,
  ].filter(Boolean);
  const tipBits = [
    "分票贡献：每票独立虚拟底仓回测后合并；PnL 不可与真实账户权益直接等同。",
    best && best.stock_name
      ? `贡献最大：${best.stock_name || best.stock_code} ${fmtMoney(best.pnl)}`
      : null,
    worst && worst !== best && worst.stock_name
      ? `拖累最大：${worst.stock_name || worst.stock_code} ${fmtMoney(worst.pnl)}`
      : null,
    topSkip ? `跨票跳过主因：${topSkip.label}（${topSkip.n}/${n} 票）— ${skipCatTip(topSkip.id, topSkip.label)}` : null,
  ].filter(Boolean);
  return { meta: bits.join(" · "), tip: tipBits.join("\n") };
}

function renderStockContrib(rows) {
  if (!rows || !rows.length) return "";
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(Number(r.pnl || 0))));
  const body = rows
    .map((r) => {
      const pnl = Number(r.pnl || 0);
      const cls = paperMetricClass(pnl);
      const ret = fmtContribReturn(r);
      const barW = Math.max(4, Math.round((Math.abs(pnl) / maxAbs) * 48));
      const fallback = {
        stock_code: r.stock_code,
        stock_name: r.stock_name,
      };
      return (
        `<tr class="paper-t0-contrib-row">` +
        stockCellHtml(r, fallback) +
        `<td class="num paper-t0-col-viz-shares" title="回测虚拟底仓股数">${fmtContribShares(r.shares)}</td>` +
        `<td class="paper-t0-col-viz-dt" title="评估窗口首日">${fmtContribDate(r.window_start_date)}</td>` +
        `<td class="num paper-t0-col-viz-px" title="窗口首日收盘价">${fmtContribPrice(r.start_price)}</td>` +
        `<td class="paper-t0-col-viz-dt" title="评估窗口末日">${fmtContribDate(r.window_end_date)}</td>` +
        `<td class="num paper-t0-col-viz-px" title="窗口末日收盘价">${fmtContribPrice(r.end_price)}</td>` +
        `<td class="num paper-t0-col-viz-lr" title="正T日 / 反T日${r.mixed_days ? " / 多轮日" : ""}">${esc(
          Number(r.mixed_days)
            ? `${r.buy_then_sell_days ?? 0}/${r.sell_then_buy_days ?? 0}/${r.mixed_days}`
            : `${r.buy_then_sell_days ?? 0}/${r.sell_then_buy_days ?? 0}`
        )}</td>` +
        `<td class="num paper-t0-col-viz-days" title="成交日数">${esc(String(r.trade_days ?? 0))}</td>` +
        `<td class="num paper-t0-col-viz-days" title="跳过日数">${esc(String(r.skip_days ?? 0))}</td>` +
        skipReasonCellHtml(r) +
        `<td class="num paper-t0-col-viz-pct" title="成交日占评估日">${
          r.participate_rate_pct != null ? esc(`${r.participate_rate_pct}%`) : "—"
        }</td>` +
        `<td class="num paper-t0-col-viz-ret ${ret.cls} has-tip" title="${esc(ret.tip)}">${esc(ret.text)}</td>` +
        `<td class="num paper-t0-col-pnl ${cls} has-tip" title="${esc(
          `含敞口净 PnL ${pnl.toFixed(1)} · 条长∝|PnL|`
        )}">` +
        `<span class="paper-t0-viz-pnl-bar" style="width:${barW}px" aria-hidden="true"></span>` +
        `<span class="paper-t0-viz-pnl-num">${esc(pnl.toFixed(1))}</span>` +
        `</td>` +
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
    `<th scope="col" class="paper-t0-col-viz-days num" title="成交日数">成交</th>` +
    `<th scope="col" class="paper-t0-col-viz-days num" title="跳过日数">跳过</th>` +
    `<th scope="col" class="paper-t0-col-viz-skip" title="跳过主因（色标+占比条；悬停看结构化 tip）">主因</th>` +
    `<th scope="col" class="paper-t0-col-viz-pct num" title="成交日占评估日">参与%</th>` +
    `<th scope="col" class="paper-t0-col-viz-ret num" title="含敞口净PnL/虚拟底仓市值">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num" title="含敞口净盈亏；条长∝|PnL|">PnL</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>`
  );
}

function renderContribSection(rows) {
  if (!rows || !rows.length) return "";
  const insight = contribInsight(rows);
  return (
    `<section class="paper-t0-viz-contrib">` +
    `<div class="paper-t0-viz-contrib-head">` +
    `<div class="paper-t0-viz-contrib-title-block">` +
    `<h4>分票贡献</h4>` +
    (insight.meta
      ? `<span class="paper-t0-viz-contrib-meta has-tip" title="${esc(insight.tip)}">${esc(
          insight.meta
        )}</span>`
      : "") +
    `</div>` +
    `<p class="paper-t0-viz-contrib-hint">主因列=跳过构成 Top；悬停看结构化 tip · 多票虚拟仓独立计后合并</p>` +
    `</div>` +
    renderStockContrib(rows) +
    `</section>`
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
    (viz.stock_contrib && viz.stock_contrib.length);
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

  if (viz.daily_activity && viz.daily_activity.length > 1) {
    cards.push(
      vizCard(
        "日度活跃",
        "近 50 评估日 · 堆叠",
        chartCanvas("activity"),
        `<div data-role="activity-foot"></div>`,
        legendChips([
          { color: THEME.sellThenBuy, label: "成交" },
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
          { color: THEME.buyThenSell, label: "正T成交" },
          { color: THEME.sellThenBuy, label: "反T成交" },
          { color: THEME.signalSkip, label: "信号跳过(含负τ)" },
        ])
      )
    );
  }

  if (viz.skip_categories && viz.skip_categories.length) {
    const total = viz.skip_categories.reduce((s, c) => s + Number(c.count || 0), 0);
    const top = viz.skip_categories[0];
    cards.push(
      vizCard(
        "跳过构成",
        `合计 ${total} ${viz.skip_scope === "slot" ? "轮" : "次"}` +
          (top ? ` · 主因 ${top.label} ${top.pct ?? ""}%` : "") +
          ` · 悬停扇区/图例看 tip`,
        skipDonutChartHtml(viz.skip_categories),
        skipLegendHtml(viz.skip_categories, total),
        legendChips([{ color: THEME.otherSkip, label: viz.skip_scope === "slot" ? "按未成交轮次" : "按跳过原因" }])
      )
    );
  }

  const topSkipLabel =
    viz.skip_categories && viz.skip_categories[0]
      ? `${viz.skip_categories[0].label} ${viz.skip_categories[0].pct ?? ""}%`
      : "—";

  const skipRound = Number(viz.skip_round_count);
  const skipHead =
    viz.skip_scope === "slot" && Number.isFinite(skipRound)
      ? `跳过 ${viz.skip_count ?? 0} 日 / ${skipRound} 轮`
      : `跳过 ${viz.skip_count ?? 0}`;
  host.hidden = false;
  host.innerHTML =
    `<div class="paper-t0-viz-head">` +
    `<div class="paper-t0-viz-head-main">` +
    `<span class="paper-t0-viz-title">归因分析</span>` +
    `<span class="quant-sub">正T ${ds.buy_then_sell ?? 0} · 反T ${ds.sell_then_buy ?? 0} · 成交 ${viz.trade_count ?? 0} · ${skipHead}` +
    (viz.skip_categories && viz.skip_categories.length
      ? ` · 主因 ${esc(topSkipLabel)}`
      : "") +
    `</span>` +
    `</div>` +
    `</div>` +
    renderKpiRow(sm) +
    `<div class="paper-t0-viz-grid">${cards.join("")}</div>` +
    renderContribSection(viz.stock_contrib);

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
          `<span>成交 <b>${meta.nTraded}</b>（正${meta.nBuyThenSell}/反${meta.nSellThenBuy}）</span>`,
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

    const skipDonut = host.querySelector('[data-viz="skip-donut"]');
    if (skipDonut) {
      const meta = buildSkipDonutMeta(viz.skip_categories);
      if (meta) {
        skipDonut.style.background = meta.gradient;
        const totalEl = skipDonut.querySelector('[data-role="skip-donut-total"]');
        if (totalEl) totalEl.textContent = String(meta.total);
        wireSkipDonutHover(skipDonut, meta);
      }
    } else {
      const skip = host.querySelector('canvas[data-viz="skip"]');
      if (skip) {
        const meta = drawSkipDonut(skip, viz.skip_categories);
        wireSkipDonutHover(skip, meta);
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
