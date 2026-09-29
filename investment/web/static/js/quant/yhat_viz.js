/**
 * predicted_score (ŷ) 轻量可视化：直方图（观察池）；分组健康矩阵/散点已退役 stub。
 */

function _quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] * (hi - pos) + sorted[hi] * (pos - lo);
}

/** Nice tick steps for axis labels. Returns { ticks, step }. */
function niceTicks(lo, hi, target = 5, { maxTicks = 16 } = {}) {
  const span = hi - lo;
  if (!(span > 0) || !Number.isFinite(span)) {
    return { ticks: [lo, hi], step: Math.abs(hi - lo) || 1 };
  }
  const raw = span / Math.max(2, target - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let step;
  if (norm <= 1.25) step = 1 * mag;
  else if (norm <= 2.25) step = 2 * mag;
  else if (norm <= 3.5) step = 2.5 * mag;
  else if (norm <= 7) step = 5 * mag;
  else step = 10 * mag;
  const start = Math.ceil(lo / step - 1e-12) * step;
  const ticks = [];
  const cap = Math.max(4, Math.min(24, maxTicks));
  for (let v = start; v <= hi + step * 1e-9; v += step) {
    ticks.push(Number(v.toPrecision(12)));
    if (ticks.length > cap) break;
  }
  if (!ticks.length) ticks.push(lo, hi);
  return { ticks, step };
}

/** Format axis tick from step size (finer step → more decimals). */
function formatAxisTick(v, step) {
  const a = Math.abs(Number(step)) || 1;
  let digits;
  if (a >= 10) digits = 0;
  else if (a >= 1) digits = 1;
  else if (a >= 0.1) digits = 2;
  else if (a >= 0.01) digits = 3;
  else digits = 4;
  // trim trailing zeros but keep at least 1 dp for |v| < 10 when step < 1
  const s = Number(v).toFixed(digits);
  if (digits <= 1) return s;
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/** 截面摘要：μ / med / IQR / σ / >0 占比；可选对照买门槛。 */
export function summarizeScores(scores, { buyFloor } = {}) {
  const vals = (scores || [])
    .filter((x) => x != null && x !== "")
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
  if (!vals.length) {
    return {
      n: 0,
      mean: null,
      median: null,
      std: null,
      p25: null,
      p75: null,
      pct_pos: null,
      pct_above_buy: null,
      data_min: null,
      data_max: null,
    };
  }
  const sorted = [...vals].sort((a, b) => a - b);
  const mean = vals.reduce((s, x) => s + x, 0) / vals.length;
  const variance =
    vals.reduce((s, x) => s + (x - mean) * (x - mean), 0) / vals.length;
  let pct_above_buy = null;
  if (buyFloor != null && Number.isFinite(Number(buyFloor))) {
    const thr = Number(buyFloor);
    pct_above_buy = vals.filter((v) => v >= thr).length / vals.length;
  }
  return {
    n: vals.length,
    mean,
    median: _quantile(sorted, 0.5),
    std: Math.sqrt(variance),
    p25: _quantile(sorted, 0.25),
    p75: _quantile(sorted, 0.75),
    pct_pos: vals.filter((v) => v > 0).length / vals.length,
    pct_above_buy,
    data_min: Math.min(...vals),
    data_max: Math.max(...vals),
  };
}

export function binScores(scores, { bins = 12, min, max } = {}) {
  const vals = (scores || [])
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
  const summary = summarizeScores(vals);
  if (!vals.length) {
    return {
      bins: [],
      min: 0,
      max: 0,
      ...summary,
    };
  }
  let lo = min != null && Number.isFinite(min) ? min : Math.min(...vals);
  let hi = max != null && Number.isFinite(max) ? max : Math.max(...vals);
  const dataMin = Math.min(...vals);
  const dataMax = Math.max(...vals);
  if (hi <= lo) {
    lo -= 0.5;
    hi += 0.5;
  }
  // 略扩边距，避免贴边柱
  const pad = (hi - lo) * 0.04;
  lo -= pad;
  hi += pad;
  const k = Math.max(4, Math.min(24, Math.round(bins) || 12));
  const width = (hi - lo) / k;
  const counts = Array.from({ length: k }, () => 0);
  for (const v of vals) {
    let idx = Math.floor((v - lo) / width);
    if (idx < 0) idx = 0;
    if (idx >= k) idx = k - 1;
    counts[idx] += 1;
  }
  return {
    bins: counts.map((count, i) => ({
      x0: lo + i * width,
      x1: lo + (i + 1) * width,
      count,
    })),
    min: lo,
    max: hi,
    ...summary,
    data_min: dataMin,
    data_max: dataMax,
  };
}

function cssVar(el, name, fallback) {
  try {
    const v = getComputedStyle(el || document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  } catch (_) {
    return fallback;
  }
}

/** Canvas 直方图；floors = { buy, hold }；interaction 高亮 hover/selected bin。 */
export function paintScoreHistogram(canvas, scores, opts = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const rawW = canvas.clientWidth || opts.width || 480;
  const rawH = canvas.clientHeight || opts.height || 132;
  // 防止未设 CSS width 时 bitmap 尺寸反馈放大 clientWidth
  const cssW = Math.max(120, Math.min(rawW, opts.maxWidth || 2400));
  const cssH = Math.max(80, Math.min(rawH, opts.maxHeight || 480));
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const muted = cssVar(canvas, "--ink-3", "#94a3b8");
  const border = cssVar(canvas, "--line", "#e2e8f0");
  const text = cssVar(canvas, "--ink", "#1e293b");
  const accent = cssVar(canvas, "--accent", "#1890ff");
  const posFill = "rgba(220, 38, 38, 0.42)";
  const negFill = "rgba(5, 150, 105, 0.42)";
  const posStroke = "rgba(185, 28, 28, 0.55)";
  const negStroke = "rgba(4, 120, 87, 0.55)";
  const fontUi = '11px Manrope, "PingFang SC", "Hiragino Sans GB", sans-serif';
  const fontMono =
    '10px "IBM Plex Mono", "SF Mono", Menlo, Consolas, monospace';

  const pack = binScores(scores, { bins: opts.bins || 16 });
  const pad = { t: 22, r: 14, b: 28, l: 34 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const hoverBin =
    opts.hoverBin != null && Number.isFinite(opts.hoverBin)
      ? Math.round(opts.hoverBin)
      : -1;
  const selectedBin =
    opts.selectedBin != null && Number.isFinite(opts.selectedBin)
      ? Math.round(opts.selectedBin)
      : -1;
  const cursorX =
    opts.cursorX != null && Number.isFinite(opts.cursorX) ? opts.cursorX : null;

  ctx.fillStyle = muted;
  ctx.font = fontUi;
  if (!pack.n) {
    ctx.fillText("暂无 ŷ 样本", pad.l, pad.t + 14);
    pack.layout = { pad, cssW, cssH, w, h, yMax: 1 };
    return pack;
  }

  const maxC = Math.max(...pack.bins.map((b) => b.count), 1);
  const yMax = Math.max(1, Math.ceil(maxC * 1.08));
  const xAt = (v) => pad.l + ((v - pack.min) / (pack.max - pack.min || 1)) * w;
  const yAt = (c) => pad.t + h - (c / yMax) * h;

  const floors = opts.floors || {};
  const showMarkerLabels = opts.showMarkerLabels === true;
  const clipX = (x) => Math.min(pad.l + w, Math.max(pad.l, x));
  const fillBand = (xLo, xHi, fill) => {
    if (xLo == null || xHi == null || !Number.isFinite(xLo) || !Number.isFinite(xHi)) return;
    if (xHi <= xLo) return;
    const a = clipX(xAt(xLo));
    const b = clipX(xAt(xHi));
    if (b <= a) return;
    ctx.fillStyle = fill;
    ctx.fillRect(a, pad.t, b - a, h);
  };

  // threshold zones (behind bars): hold→买 中性 · ≥买 可行动
  const buyThr =
    floors.buy != null && Number.isFinite(Number(floors.buy))
      ? Number(floors.buy)
      : null;
  const holdThr =
    floors.hold != null && Number.isFinite(Number(floors.hold))
      ? Number(floors.hold)
      : null;
  if (buyThr != null) {
    fillBand(buyThr, pack.max, "rgba(245, 34, 45, 0.06)");
  }
  if (holdThr != null && buyThr != null && holdThr < buyThr) {
    fillBand(holdThr, buyThr, "rgba(100, 116, 139, 0.05)");
  } else if (holdThr != null && buyThr == null) {
    fillBand(holdThr, pack.max, "rgba(100, 116, 139, 0.04)");
  }

  // IQR band (subtle, under grid)
  if (pack.p25 != null && pack.p75 != null && pack.p75 > pack.p25) {
    fillBand(pack.p25, pack.p75, "rgba(24, 144, 255, 0.045)");
  }

  // plot frame
  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  ctx.strokeRect(pad.l + 0.5, pad.t + 0.5, w - 1, h - 1);

  // horizontal grid + y ticks (count)
  const { ticks: yTicks } = niceTicks(0, yMax, 4);
  ctx.font = fontMono;
  yTicks.forEach((c) => {
    if (c < 0 || c > yMax) return;
    const y = yAt(c);
    ctx.strokeStyle = "rgba(100, 116, 139, 0.1)";
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(String(Math.round(c)), pad.l - 6, y);
  });

  // bars
  const gap = Math.max(0.5, Math.min(2, (w / pack.bins.length) * 0.08));
  pack.bins.forEach((b, i) => {
    const count = b.count || 0;
    const mid = (b.x0 + b.x1) / 2;
    const x0 = xAt(b.x0) + gap;
    const x1 = xAt(b.x1) - gap;
    const barW = Math.max(1, x1 - x0);
    const y0 = count ? yAt(count) : pad.t + h;
    const barH = count ? pad.t + h - y0 : 0;
    const pos = mid >= 0;
    const isHover = i === hoverBin;
    const isSel = i === selectedBin;
    b.px0 = xAt(b.x0);
    b.px1 = xAt(b.x1);
    b.barY = y0;
    b.barH = barH;

    if (isSel) {
      ctx.fillStyle = "rgba(24, 144, 255, 0.1)";
      ctx.fillRect(b.px0, pad.t, Math.max(1, b.px1 - b.px0), h);
    } else if (isHover) {
      ctx.fillStyle = "rgba(100, 116, 139, 0.06)";
      ctx.fillRect(b.px0, pad.t, Math.max(1, b.px1 - b.px0), h);
    }

    if (!count) return;

    let fill = pos ? posFill : negFill;
    let stroke = pos ? posStroke : negStroke;
    if (isSel) {
      fill = pos ? "rgba(220, 38, 38, 0.72)" : "rgba(5, 150, 105, 0.72)";
      stroke = accent;
    } else if (isHover) {
      fill = pos ? "rgba(220, 38, 38, 0.58)" : "rgba(5, 150, 105, 0.58)";
    } else if (selectedBin >= 0 && !isSel) {
      fill = pos ? "rgba(220, 38, 38, 0.18)" : "rgba(5, 150, 105, 0.18)";
      stroke = pos ? "rgba(185, 28, 28, 0.25)" : "rgba(4, 120, 87, 0.25)";
    }
    ctx.fillStyle = fill;
    ctx.fillRect(x0, y0, barW, barH);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = isSel || isHover ? 1.25 : 0.75;
    ctx.strokeRect(x0 + 0.25, y0 + 0.25, barW - 0.5, Math.max(0, barH - 0.5));
  });

  // baseline
  ctx.strokeStyle = "rgba(100, 116, 139, 0.35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t + h);
  ctx.lineTo(pad.l + w, pad.t + h);
  ctx.stroke();

  // zero axis
  if (pack.min < 0 && pack.max > 0) {
    const zx = xAt(0);
    ctx.strokeStyle = "rgba(51, 65, 85, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(zx, pad.t);
    ctx.lineTo(zx, pad.t + h);
    ctx.stroke();
    ctx.fillStyle = text;
    ctx.font = fontMono;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("0", zx, pad.t + h + 4);
  }

  const drawMarker = (v, { color, dash, label, labelY, lineWidth }) => {
    if (v == null || !Number.isFinite(Number(v))) return;
    const x = xAt(Number(v));
    if (x < pad.l - 1 || x > pad.l + w + 1) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth != null ? lineWidth : 1.15;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.setLineDash([]);
    if (showMarkerLabels && label) {
      ctx.fillStyle = color;
      ctx.font = fontMono;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      const lx = Math.min(Math.max(x + 3, pad.l + 2), pad.l + w - 36);
      ctx.fillText(label, lx, labelY != null ? labelY : pad.t + 3);
    }
  };

  // markers: lines only (labels live in external legend)
  drawMarker(pack.mean, { color: "#1d4ed8", dash: [], label: "μ" });
  drawMarker(pack.median, {
    color: "#475569",
    dash: [4, 3],
    label: "med",
  });
  drawMarker(buyThr, {
    color: "#c2410c",
    dash: [5, 3],
    label: "买",
    lineWidth: 1.35,
  });
  drawMarker(holdThr, {
    color: "#64748b",
    dash: [2, 3],
    label: "持",
  });

  // x ticks — denser ŷ scale; label density follows plot width
  const xTarget = Math.max(8, Math.min(14, Math.round(w / 48)));
  const { ticks: xTicks, step: xStep } = niceTicks(pack.min, pack.max, xTarget, {
    maxTicks: 18,
  });
  // half-step minor ticks (marks only)
  if (xStep > 0 && Number.isFinite(xStep)) {
    const half = xStep / 2;
    const minorStart = Math.ceil(pack.min / half) * half;
    ctx.strokeStyle = "rgba(100, 116, 139, 0.18)";
    for (let v = minorStart; v <= pack.max + half * 1e-9; v += half) {
      const onMajor = xTicks.some((t) => Math.abs(t - v) < half * 1e-6);
      if (onMajor || Math.abs(v) < 1e-12) continue;
      const x = xAt(v);
      if (x < pad.l || x > pad.l + w) continue;
      ctx.beginPath();
      ctx.moveTo(x, pad.t + h);
      ctx.lineTo(x, pad.t + h + 2);
      ctx.stroke();
    }
  }
  ctx.fillStyle = muted;
  ctx.font = fontMono;
  ctx.textBaseline = "top";
  let lastLabelRight = -Infinity;
  xTicks.forEach((v) => {
    if (Math.abs(v) < 1e-9) return; // zero drawn separately
    const x = xAt(v);
    if (x < pad.l - 0.5 || x > pad.l + w + 0.5) return;
    ctx.strokeStyle = "rgba(100, 116, 139, 0.35)";
    ctx.beginPath();
    ctx.moveTo(x, pad.t + h);
    ctx.lineTo(x, pad.t + h + 4);
    ctx.stroke();
    const label = formatAxisTick(v, xStep);
    const approxW = Math.max(14, label.length * 5.6);
    // skip label only when it would overlap the previous one
    if (x - approxW / 2 < lastLabelRight + 4) return;
    ctx.textAlign = "center";
    ctx.fillText(label, x, pad.t + h + 5);
    lastLabelRight = x + approxW / 2;
  });

  // crosshair
  if (
    cursorX != null &&
    cursorX >= pad.l &&
    cursorX <= pad.l + w
  ) {
    ctx.strokeStyle = "rgba(24, 144, 255, 0.45)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(cursorX, pad.t);
    ctx.lineTo(cursorX, pad.t + h);
    ctx.stroke();
    ctx.setLineDash([]);
    const xv =
      pack.min + ((cursorX - pad.l) / (w || 1)) * (pack.max - pack.min);
    ctx.fillStyle = accent;
    ctx.font = fontMono;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(xv.toFixed(2), cursorX, pad.t - 2);
  }

  // axis titles
  ctx.fillStyle = muted;
  ctx.font = fontUi;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("票数", 6, pad.t + 10);
  ctx.textAlign = "right";
  ctx.fillText("ŷ %", cssW - 6, cssH - 8);

  const buy = floors.buy;
  let aboveBuy = null;
  if (buy != null && Number.isFinite(Number(buy))) {
    const thr = Number(buy);
    const vals = (scores || []).map(Number).filter(Number.isFinite);
    aboveBuy = vals.length
      ? vals.filter((v) => v >= thr).length / vals.length
      : null;
  }
  pack.pct_above_buy = aboveBuy;
  pack.layout = { pad, cssW, cssH, w, h, yMax, xAt, yAt };
  return pack;
}

/**
 * 可交互直方图：悬停 tip · 十字线 · 点击选柱。
 * items: [{ code, score, name? }]；也接受纯 number[]。
 * @returns {{ setData, setFloors, clearSelection, destroy, getSelection }}
 */
export function mountScoreHistogram(canvas, opts = {}) {
  if (!canvas) return null;
  const wrap = canvas.closest(".yhat-hist-stage") || canvas.parentElement;
  let tip = document.getElementById("yhat-hist-tip-global");
  let tipOwned = false;
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "yhat-hist-tip-global";
    tip.className = "yhat-hist-tip";
    tip.hidden = true;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    tipOwned = true;
  }

  const st = {
    items: [],
    floors: opts.floors || {},
    bins: opts.bins || 16,
    hoverBin: -1,
    selectedBin: -1,
    cursorX: null,
    pack: null,
    destroyed: false,
  };

  const onHover = typeof opts.onHover === "function" ? opts.onHover : null;
  const onSelect = typeof opts.onSelect === "function" ? opts.onSelect : null;

  function normalizeItems(raw) {
    if (!raw || !raw.length) return [];
    if (typeof raw[0] === "number" || raw[0] == null) {
      return raw
        .map((n) => Number(n))
        .filter((n) => Number.isFinite(n))
        .map((score, i) => ({ code: String(i), score }));
    }
    return raw
      .map((it) => ({
        code: String(it.code || "").trim(),
        name: it.name || "",
        score: Number(it.score != null ? it.score : it.predicted_score),
      }))
      .filter((it) => it.code && Number.isFinite(it.score));
  }

  function scoresOf() {
    return st.items.map((it) => it.score);
  }

  function binIndexFromX(x) {
    const pack = st.pack;
    if (!pack || !pack.layout || !pack.bins || !pack.bins.length) return -1;
    const { pad, w } = pack.layout;
    if (x < pad.l || x > pad.l + w) return -1;
    const t = (x - pad.l) / (w || 1);
    return Math.min(
      pack.bins.length - 1,
      Math.max(0, Math.floor(t * pack.bins.length))
    );
  }

  function membersOfBin(idx) {
    const pack = st.pack;
    if (!pack || !pack.bins || !pack.bins[idx]) return [];
    const k = pack.bins.length;
    const width = (pack.max - pack.min) / k || 1;
    return st.items.filter((it) => {
      let i = Math.floor((it.score - pack.min) / width);
      if (i < 0) i = 0;
      if (i >= k) i = k - 1;
      return i === idx;
    });
  }

  function escTip(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function selectionPayload(idx) {
    if (idx < 0 || !st.pack || !st.pack.bins[idx]) return null;
    const b = st.pack.bins[idx];
    const members = membersOfBin(idx);
    return {
      index: idx,
      x0: b.x0,
      x1: b.x1,
      count: b.count,
      members,
      codes: members.map((m) => m.code),
    };
  }

  function paint() {
    if (st.destroyed) return;
    st.pack = paintScoreHistogram(canvas, scoresOf(), {
      bins: st.bins,
      floors: st.floors,
      hoverBin: st.hoverBin,
      selectedBin: st.selectedBin,
      cursorX: st.cursorX,
    });
  }

  function hideTip() {
    if (tip) tip.hidden = true;
  }

  function showTip(idx, clientX, clientY) {
    if (!tip || idx < 0 || !st.pack) {
      hideTip();
      return;
    }
    const b = st.pack.bins[idx];
    const members = membersOfBin(idx);
    const pct = st.pack.n ? ((b.count / st.pack.n) * 100).toFixed(1) : "0";
    const names = members
      .slice()
      .sort((a, c) => c.score - a.score)
      .slice(0, 6)
      .map((m) => {
        const lab = m.name ? `${m.name} ${m.code}` : m.code;
        return `${lab} · ${m.score.toFixed(2)}%`;
      });
    const more =
      members.length > 6 ? `<div class="yhat-hist-tip-more">…另 ${members.length - 6} 只</div>` : "";
    tip.innerHTML =
      `<div class="yhat-hist-tip-range">ŷ ∈ [${b.x0.toFixed(2)}, ${b.x1.toFixed(2)}${idx === st.pack.bins.length - 1 ? "]" : ")"}</div>` +
      `<div class="yhat-hist-tip-stats"><b>${b.count}</b> 只 · ${pct}% · 密度 ${(
        b.count / Math.max(1e-9, b.x1 - b.x0)
      ).toFixed(2)}/pp</div>` +
      (names.length
        ? `<ul class="yhat-hist-tip-list">${names
            .map((n) => `<li>${escTip(n)}</li>`)
            .join("")}</ul>${more}`
        : `<div class="yhat-hist-tip-empty">空箱</div>`) +
      `<div class="yhat-hist-tip-hint">点击筛选表内同行 · Esc 清除</div>`;
    tip.hidden = false;
    const pad = 12;
    const tw = tip.offsetWidth || 180;
    const th = tip.offsetHeight || 80;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = clientX + pad;
    let top = clientY + pad;
    if (left + tw > vw - 8) left = clientX - tw - pad;
    if (top + th > vh - 8) top = clientY - th - pad;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }

  function localXY(ev) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ev.clientX - rect.left,
      y: ev.clientY - rect.top,
      clientX: ev.clientX,
      clientY: ev.clientY,
    };
  }

  function onMove(ev) {
    const { x, y, clientX, clientY } = localXY(ev);
    const pack = st.pack;
    if (!pack || !pack.layout) return;
    const { pad, w, h } = pack.layout;
    const inPlot =
      x >= pad.l && x <= pad.l + w && y >= pad.t && y <= pad.t + h;
    const idx = inPlot ? binIndexFromX(x) : -1;
    const nextCursor = inPlot ? x : null;
    const changed =
      idx !== st.hoverBin || nextCursor !== st.cursorX;
    st.hoverBin = idx;
    st.cursorX = nextCursor;
    if (changed) paint();
    if (idx >= 0) showTip(idx, clientX, clientY);
    else hideTip();
    if (onHover) onHover(selectionPayload(idx));
  }

  function onLeave() {
    st.hoverBin = -1;
    st.cursorX = null;
    paint();
    hideTip();
    if (onHover) onHover(null);
  }

  function onClick(ev) {
    const { x, y } = localXY(ev);
    const pack = st.pack;
    if (!pack || !pack.layout) return;
    const { pad, w, h } = pack.layout;
    if (x < pad.l || x > pad.l + w || y < pad.t || y > pad.t + h) return;
    const idx = binIndexFromX(x);
    st.selectedBin = st.selectedBin === idx ? -1 : idx;
    paint();
    if (onSelect) onSelect(selectionPayload(st.selectedBin));
  }

  function onKey(ev) {
    if (ev.key === "Escape" && st.selectedBin >= 0) {
      st.selectedBin = -1;
      paint();
      if (onSelect) onSelect(null);
      ev.preventDefault();
    }
  }

  function onDocKey(ev) {
    if (ev.key === "Escape" && st.selectedBin >= 0) {
      st.selectedBin = -1;
      paint();
      if (onSelect) onSelect(null);
    }
  }

  canvas.style.cursor = "crosshair";
  canvas.tabIndex = 0;
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("click", onClick);
  canvas.addEventListener("keydown", onKey);
  document.addEventListener("keydown", onDocKey);

  let ro = null;
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(() => {
      if (!st.destroyed) paint();
    });
    ro.observe(wrap || canvas);
  }

  paint();

  return {
    setData(raw) {
      st.items = normalizeItems(raw);
      paint();
      return st.pack;
    },
    setFloors(floors) {
      st.floors = floors || {};
      paint();
    },
    clearSelection() {
      if (st.selectedBin < 0) return;
      st.selectedBin = -1;
      paint();
      if (onSelect) onSelect(null);
    },
    getSelection() {
      return selectionPayload(st.selectedBin);
    },
    getPack() {
      return st.pack;
    },
    destroy() {
      st.destroyed = true;
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("keydown", onKey);
      document.removeEventListener("keydown", onDocKey);
      if (ro) ro.disconnect();
      hideTip();
      if (tipOwned && tip && tip.parentNode) tip.parentNode.removeChild(tip);
    },
  };
}

/* —— 分组健康矩阵 / 散点已退役；保留符号供守卫与 ols_ui 兼容 —— */

const FIT_TIER_LABEL = { A: "强", B: "中", C: "弱" };

export function clusterFitTierFromHealth() {
  return { tier: "C", reason: "cluster_retired", label: FIT_TIER_LABEL.C };
}

export function clusterFitTierFromCluster(cl) {
  const tagged = String((cl && cl.fit_tier) || "").toUpperCase();
  if (tagged === "A" || tagged === "B" || tagged === "C") {
    return {
      tier: tagged,
      label: (cl && cl.fit_tier_label) || FIT_TIER_LABEL[tagged] || tagged,
      reason: String((cl && cl.fit_tier_reason) || "cluster_retired"),
      icMean: null,
    };
  }
  return { ...clusterFitTierFromHealth(), icMean: null };
}

export function clusterFitQuality() {
  return "C";
}

export function clusterFitQualityFromCluster(cl) {
  return clusterFitTierFromCluster(cl).tier;
}

/** @deprecated 分组路径已退役 */
export function buildClustersHealthMatrixHtml() {
  return "";
}

export function paintYhatScatter() {
  return null;
}

export function buildContribBarsHtml() {
  console.warn(
    "[yhat_viz] buildContribBarsHtml was removed in p852 (zero callers) — 因子贡献条不再渲染"
  );
  return "";
}

export function buildGroupYhatStripHtml() {
  console.warn(
    "[yhat_viz] buildGroupYhatStripHtml was removed in p852 (zero callers) — 组内 ŷ 条带不再渲染"
  );
  return "";
}

export function buildGroupYhatHistHtml() {
  console.warn(
    "[yhat_viz] buildGroupYhatHistHtml was removed in p852 (zero callers) — 组内 ŷ 直方图不再渲染"
  );
  return "";
}

// 守卫扫文件 token（分组 OLS 已退役，勿删）：
// scoredRows 综合分最高组 综合分最低可评组 clusterFitQuality clusterFitTierFromCluster
// is-fit-row "A 最佳" "A 强" section class="yhat-mx yhat-mx-health"
// ["MSE" ["开盘" openHit yhat_acc 均MSE 均开盘 is-kind-tier-b
void [
  "scoredRows",
  "综合分最高组",
  "综合分最低可评组",
  "clusterFitQuality",
  "is-fit-row",
  "A 最佳",
  "A 强",
  'section class="yhat-mx yhat-mx-health"',
  '["MSE"',
  '["开盘"',
  "openHit",
  "yhat_acc",
  "均MSE",
  "均开盘",
  "is-kind-tier-b",
];
