/**
 * predicted_score (ŷ) 轻量可视化：贡献条 · 直方图 · 散点（无新依赖）。
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

/**
 * ŷ vs realized 散点。
 * points: [{ yhat, realized_h, hit, code?, name? }]
 * 悬停显示股票注释；opts.interactive === false 可关闭。
 */
export function paintYhatScatter(canvas, points, opts = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const pts = (points || [])
    .map((p) => ({
      x: Number(p.yhat),
      y: Number(p.realized_h != null ? p.realized_h : p.realized),
      hit: p.hit,
      code: String(p.code || p.stock_code || "").trim(),
      name: String(p.name || p.stock_name || "").trim(),
      tag: p.tag || "",
      abs_err: p.abs_err,
    }))
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || opts.width || 480;
  const cssH = canvas.clientHeight || opts.height || 220;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const muted = cssVar(canvas, "--ink-3", "#94a3b8");
  const border = cssVar(canvas, "--line", "#e2e8f0");
  const text = cssVar(canvas, "--ink", "#1e293b");
  const qualityPos = cssVar(canvas, "--q-quality-pos", "#0f766e");
  const qualityNeg = cssVar(canvas, "--q-quality-neg", "#b42318");
  const pad = { t: 10, r: 14, b: 30, l: 40 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = muted;
  ctx.font = '11px Manrope, "PingFang SC", "Hiragino Sans GB", sans-serif';
  if (pts.length < 2) {
    ctx.fillText("样本不足，无法画散点", pad.l, pad.t + 14);
    return { n: pts.length, hits: 0, layout: { pad, cssW, cssH, w, h }, points: [] };
  }

  let xMin = Math.min(...pts.map((p) => p.x), 0);
  let xMax = Math.max(...pts.map((p) => p.x), 0);
  let yMin = Math.min(...pts.map((p) => p.y), 0);
  let yMax = Math.max(...pts.map((p) => p.y), 0);
  if (xMax <= xMin) {
    xMin -= 1;
    xMax += 1;
  }
  if (yMax <= yMin) {
    yMin -= 1;
    yMax += 1;
  }
  const xPad = (xMax - xMin) * 0.08;
  const yPad = (yMax - yMin) * 0.08;
  xMin -= xPad;
  xMax += xPad;
  yMin -= yPad;
  yMax += yPad;

  const sx = (v) => pad.l + ((v - xMin) / (xMax - xMin)) * w;
  const sy = (v) => pad.t + h - ((v - yMin) / (yMax - yMin)) * h;
  const isHit = (p) =>
    p.hit === true || p.hit === false
      ? p.hit
      : Math.sign(p.x) === Math.sign(p.y) && p.x !== 0 && p.y !== 0;

  // plot frame
  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  ctx.strokeRect(pad.l + 0.5, pad.t + 0.5, w - 1, h - 1);

  // quadrant bands (same-sign = hit) — research quality colors, not A-share
  const zx = sx(0);
  const zy = sy(0);
  const qHit = "rgba(15, 118, 110, 0.06)";
  const qMiss = "rgba(180, 35, 24, 0.05)";
  // Q1 (+,+) hit · Q2 (-,+) miss · Q3 (-,-) hit · Q4 (+,-) miss
  ctx.fillStyle = qHit;
  ctx.fillRect(zx, pad.t, Math.max(0, pad.l + w - zx), Math.max(0, zy - pad.t));
  ctx.fillRect(pad.l, zy, Math.max(0, zx - pad.l), Math.max(0, pad.t + h - zy));
  ctx.fillStyle = qMiss;
  ctx.fillRect(pad.l, pad.t, Math.max(0, zx - pad.l), Math.max(0, zy - pad.t));
  ctx.fillRect(zx, zy, Math.max(0, pad.l + w - zx), Math.max(0, pad.t + h - zy));

  // grid + ticks
  const { ticks: xTicks, step: scatterXStep } = niceTicks(xMin, xMax, 5);
  const { ticks: yTicks, step: scatterYStep } = niceTicks(yMin, yMax, 5);
  ctx.font = '10px "IBM Plex Mono", "SF Mono", Menlo, Consolas, monospace';
  xTicks.forEach((v) => {
    const x = sx(v);
    if (x < pad.l || x > pad.l + w) return;
    ctx.strokeStyle = border;
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(formatAxisTick(v, scatterXStep), x, pad.t + h + 4);
  });
  yTicks.forEach((v) => {
    const y = sy(v);
    if (y < pad.t || y > pad.t + h) return;
    ctx.strokeStyle = border;
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(formatAxisTick(v, scatterYStep), pad.l - 5, y);
  });

  // zero axes
  ctx.strokeStyle = text;
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  ctx.moveTo(pad.l, zy);
  ctx.lineTo(pad.l + w, zy);
  ctx.moveTo(zx, pad.t);
  ctx.lineTo(zx, pad.t + h);
  ctx.stroke();
  ctx.globalAlpha = 1;

  const hoverIndex =
    opts.hoverIndex != null && Number.isFinite(opts.hoverIndex)
      ? Math.round(opts.hoverIndex)
      : -1;
  const plotted = [];
  let hits = 0;
  pts.forEach((p, i) => {
    const hit = isHit(p);
    if (hit) hits += 1;
    const x = sx(p.x);
    const y = sy(p.y);
    plotted.push({ ...p, hit, px: x, py: y });
    const active = i === hoverIndex;
    const r = active ? 6.2 : 3.8;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = hit ? qualityPos : qualityNeg;
    ctx.globalAlpha = active ? 1 : 0.82;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = hit ? qualityPos : qualityNeg;
    ctx.lineWidth = active ? 1.6 : 0.8;
    ctx.stroke();
    if (active) {
      ctx.beginPath();
      ctx.arc(x, y, r + 3.5, 0, Math.PI * 2);
      ctx.strokeStyle = text;
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  });

  ctx.fillStyle = text;
  ctx.font = '10px Manrope, "PingFang SC", "Hiragino Sans GB", sans-serif';
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("ŷ %", cssW - 8, cssH - 8);
  ctx.save();
  ctx.translate(11, pad.t + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText("实现 %", 0, 0);
  ctx.restore();

  const pack = {
    n: pts.length,
    hits,
    hit_rate: pts.length ? hits / pts.length : null,
    xMin,
    xMax,
    yMin,
    yMax,
    layout: { pad, cssW, cssH, w, h },
    points: plotted,
  };

  if (opts.interactive !== false) {
    _bindYhatScatterHover(canvas, points, opts);
  }
  return pack;
}

function _escScatterTip(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _scatterTipEl() {
  let tip = document.getElementById("yhat-scatter-tip-global");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "yhat-scatter-tip-global";
    tip.className = "yhat-hist-tip yhat-scatter-tip";
    tip.hidden = true;
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
  }
  return tip;
}

function _bindYhatScatterHover(canvas, rawPoints, opts = {}) {
  const st = canvas.__yhatScatter || (canvas.__yhatScatter = {});
  st.rawPoints = rawPoints || [];
  st.hoverIndex = st.hoverIndex != null ? st.hoverIndex : -1;
  st.onPoint = typeof opts.onPoint === "function" ? opts.onPoint : st.onPoint || null;
  st.tagLabel =
    typeof opts.tagLabel === "function" ? opts.tagLabel : st.tagLabel || null;

  if (st.bound) {
    // 数据刷新：按当前 hover 重绘一次
    st.pack = paintYhatScatter(canvas, st.rawPoints, {
      interactive: false,
      hoverIndex: st.hoverIndex,
    });
    return;
  }
  st.bound = true;
  canvas.style.cursor = "crosshair";

  const hitRadius = 12;

  function hideTip() {
    const tip = document.getElementById("yhat-scatter-tip-global");
    if (tip) tip.hidden = true;
  }

  function showTip(p, clientX, clientY) {
    const tip = _scatterTipEl();
    if (!p) {
      hideTip();
      return;
    }
    const title = p.name ? `${p.name} ${p.code}` : p.code || "未知名";
    const hitTxt = p.hit ? "方向命中" : "方向失手";
    const tag =
      st.tagLabel && p.tag ? st.tagLabel(p.tag) : p.tag || "";
    const err =
      p.abs_err != null && Number.isFinite(Number(p.abs_err))
        ? Number(p.abs_err).toFixed(2)
        : null;
    tip.innerHTML =
      `<div class="yhat-hist-tip-range">${_escScatterTip(title)}</div>` +
      `<div class="yhat-hist-tip-stats">` +
      `<b>${hitTxt}</b>` +
      (tag ? ` · ${_escScatterTip(tag)}` : "") +
      `</div>` +
      `<ul class="yhat-hist-tip-list">` +
      `<li>ŷ ${_escScatterTip(p.x.toFixed(2))}%</li>` +
      `<li>实现 ${_escScatterTip(p.y.toFixed(2))}%</li>` +
      (err != null ? `<li>|误差| ${_escScatterTip(err)}</li>` : "") +
      `</ul>` +
      (p.code
        ? `<div class="yhat-hist-tip-hint">点击打开单票时间线</div>`
        : "");
    tip.hidden = false;
    const gap = 12;
    const tw = tip.offsetWidth || 180;
    const th = tip.offsetHeight || 80;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = clientX + gap;
    let top = clientY + gap;
    if (left + tw > vw - 8) left = clientX - tw - gap;
    if (top + th > vh - 8) top = clientY - th - gap;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }

  function nearestIndex(x, y, pack) {
    if (!pack || !pack.points || !pack.points.length) return -1;
    let best = -1;
    let bestD = hitRadius * hitRadius;
    pack.points.forEach((p, i) => {
      const dx = p.px - x;
      const dy = p.py - y;
      const d = dx * dx + dy * dy;
      if (d <= bestD) {
        bestD = d;
        best = i;
      }
    });
    return best;
  }

  function repaint(hoverIndex) {
    st.hoverIndex = hoverIndex;
    st.pack = paintYhatScatter(canvas, st.rawPoints, {
      interactive: false,
      hoverIndex,
    });
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
    if (!st.pack) {
      st.pack = paintYhatScatter(canvas, st.rawPoints, {
        interactive: false,
        hoverIndex: st.hoverIndex,
      });
    }
    const pack = st.pack;
    const { pad, w, h } = pack.layout || {};
    const inPlot =
      pad &&
      x >= pad.l &&
      x <= pad.l + w &&
      y >= pad.t &&
      y <= pad.t + h;
    const idx = inPlot ? nearestIndex(x, y, pack) : -1;
    if (idx !== st.hoverIndex) repaint(idx);
    if (idx >= 0 && st.pack && st.pack.points[idx]) {
      canvas.style.cursor = "pointer";
      showTip(st.pack.points[idx], clientX, clientY);
    } else {
      canvas.style.cursor = "crosshair";
      hideTip();
    }
  }

  function onLeave() {
    if (st.hoverIndex >= 0) repaint(-1);
    else st.hoverIndex = -1;
    hideTip();
    canvas.style.cursor = "crosshair";
  }

  function onClick(ev) {
    const { x, y } = localXY(ev);
    const pack =
      st.pack ||
      paintYhatScatter(canvas, st.rawPoints, {
        interactive: false,
        hoverIndex: -1,
      });
    st.pack = pack;
    const idx = nearestIndex(x, y, pack);
    if (idx < 0 || !pack.points[idx]) return;
    const p = pack.points[idx];
    if (st.onPoint) st.onPoint(p);
  }

  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("click", onClick);
  st.destroy = () => {
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerleave", onLeave);
    canvas.removeEventListener("click", onClick);
    hideTip();
    delete canvas.__yhatScatter;
  };

  st.pack = paintYhatScatter(canvas, st.rawPoints, {
    interactive: false,
    hoverIndex: st.hoverIndex,
  });
}


/** 命中率 sparkline（0–1）。 */
export function paintHitSparkline(canvas, points, opts = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const pts = (points || [])
    .map((p) => ({
      date: String(p.date || "").slice(0, 10),
      v: Number(p.hit_rate),
      n: p.n_scored,
    }))
    .filter((p) => Number.isFinite(p.v));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || opts.width || 480;
  const cssH = canvas.clientHeight || opts.height || 64;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const muted = cssVar(canvas, "--ink-3", "#94a3b8");
  const accent = cssVar(canvas, "--accent", "#1890ff");
  const line = cssVar(canvas, "--line", "#e2e8f0");
  const qualityMid = cssVar(canvas, "--q-quality-mid", "#b45309");
  const pad = { t: 8, r: 44, b: 16, l: 28 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = muted;
  ctx.font = '10px Manrope, "PingFang SC", "Hiragino Sans GB", sans-serif';
  if (pts.length < 2) {
    ctx.fillText("命中率样本不足", pad.l, pad.t + 12);
    return { n: pts.length };
  }
  const yAt = (v) => pad.t + h - Math.max(0, Math.min(1, v)) * h;
  const xAt = (i) => pad.l + (i / (pts.length - 1)) * w;
  const mean = pts.reduce((s, p) => s + p.v, 0) / pts.length;

  // band above 50%
  ctx.fillStyle = "rgba(15, 118, 110, 0.06)";
  ctx.fillRect(pad.l, pad.t, w, yAt(0.5) - pad.t);

  // 0.5 / mean guides
  ctx.strokeStyle = line;
  ctx.globalAlpha = 0.9;
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(pad.l, yAt(0.5));
  ctx.lineTo(pad.l + w, yAt(0.5));
  ctx.stroke();
  ctx.strokeStyle = qualityMid;
  ctx.beginPath();
  ctx.moveTo(pad.l, yAt(mean));
  ctx.lineTo(pad.l + w, yAt(mean));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;

  // area fill
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = xAt(i);
    const y = yAt(p.v);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineTo(xAt(pts.length - 1), pad.t + h);
  ctx.lineTo(xAt(0), pad.t + h);
  ctx.closePath();
  ctx.fillStyle = "rgba(24, 144, 255, 0.1)";
  ctx.fill();

  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.75;
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = xAt(i);
    const y = yAt(p.v);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  pts.forEach((p, i) => {
    ctx.fillStyle = p.v >= 0.5 ? "#047857" : "#b91c1c";
    ctx.beginPath();
    ctx.arc(xAt(i), yAt(p.v), 2.6, 0, Math.PI * 2);
    ctx.fill();
  });

  const last = pts[pts.length - 1];
  const lastPct = ((last.v || 0) * 100).toFixed(0);
  ctx.fillStyle = muted;
  ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("50%", 4, yAt(0.5) + 3);
  ctx.fillText(
    `${pts[0].date.slice(5)}→${last.date.slice(5)} · μ ${(mean * 100).toFixed(0)}%`,
    pad.l,
    cssH - 3
  );
  ctx.fillStyle = last.v >= 0.5 ? "#047857" : "#b91c1c";
  ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  ctx.textAlign = "left";
  ctx.fillText(`${lastPct}%`, pad.l + w + 6, yAt(last.v) + 3);
  return { n: pts.length, last: last.v, mean };
}

/* ============================ 跨组健康矩阵 ============================ */

/**
 * 健康矩阵色阶（研究报告风）：低饱和发散、近零中性、强值加深字色。
 * t∈[-1,1]；mode=quality 绿好红弱；mode=return A 股红涨绿跌。
 */
function heatCellStyle(t, mode = "quality") {
  if (t == null || !Number.isFinite(t)) return "";
  const c = Math.max(-1, Math.min(1, Number(t)));
  const abs = Math.abs(c);
  if (abs < 0.06) {
    return "background:color-mix(in srgb, var(--ink) 3.5%, var(--surface));color:var(--ink-2)";
  }
  // 底色轻、字色随强度加深，避免荧光块
  const a = (0.05 + abs * 0.2).toFixed(3);
  const ret = mode === "return";
  let rgb;
  let fg;
  if (c >= 0) {
    rgb = ret ? "185,28,28" : "15,118,110"; // 涨红 / 好青绿
    fg = ret ? "#9f1239" : "#0f766e";
  } else {
    rgb = ret ? "21,128,61" : "159,18,57"; // 跌绿 / 弱玫红
    fg = ret ? "#166534" : "#9f1239";
  }
  const ink = abs >= 0.55 ? fg : abs >= 0.28 ? `color-mix(in srgb, ${fg} 72%, var(--ink))` : "inherit";
  return `background:rgba(${rgb},${a});color:${ink}`;
}

function qualityCellBg(t) {
  return heatCellStyle(t, "quality");
}

function signedReturnCellBg(t) {
  return heatCellStyle(t, "return");
}

/** 从 cluster 抽取健康指标（R²/IC/ICIR/ŷ/ΔOOS/同质度等，供跨组健康矩阵使用）。 */
function extractClusterHealth(cl) {
  const ols = (cl && cl.ols) || null;
  const rm = (cl && cl.return_model) || null;
  const r2 =
    ols && ols.r_squared != null && Number.isFinite(Number(ols.r_squared))
      ? Number(ols.r_squared)
      : rm && rm.r_squared != null && Number.isFinite(Number(rm.r_squared))
        ? Number(rm.r_squared)
        : null;
  const members = Array.isArray(cl && cl.members) ? cl.members.length : null;
  const memberN =
    cl && cl.member_count != null && Number.isFinite(Number(cl.member_count))
      ? Number(cl.member_count)
      : members;
  const sampleN =
    ols && ols.sample_count != null
      ? Number(ols.sample_count)
      : rm && rm.sample_count != null
        ? Number(rm.sample_count)
        : null;

  let icir = null;
  let icMean = null;
  const panel = cl && cl.factor_ic_panel ? cl.factor_ic_panel : null;
  const pear =
    panel && panel.score_ic && panel.score_ic.pearson
      ? panel.score_ic.pearson
      : null;
  if (pear) {
    if (pear.icir != null && Number.isFinite(Number(pear.icir))) icir = Number(pear.icir);
    if (pear.ic != null && Number.isFinite(Number(pear.ic))) icMean = Number(pear.ic);
    else if (pear.ic_mean != null && Number.isFinite(Number(pear.ic_mean)))
      icMean = Number(pear.ic_mean);
  }
  if (icir == null && panel && Array.isArray(panel.rows)) {
    const irs = panel.rows
      .map((r) => Number(r && r.icir))
      .filter((n) => Number.isFinite(n));
    if (irs.length) icir = irs.reduce((s, x) => s + x, 0) / irs.length;
  }
  if (icMean == null && panel && Array.isArray(panel.rows)) {
    const ics = panel.rows
      .map((r) =>
        Number(
          r && (r.ic != null ? r.ic : r.ic_mean != null ? r.ic_mean : NaN)
        )
      )
      .filter((n) => Number.isFinite(n));
    if (ics.length) icMean = ics.reduce((s, x) => s + x, 0) / ics.length;
  }

  const gate = (cl && cl.oos_gate) || {};
  const deltaOos =
    !gate.skipped && gate.delta_oos_pp != null && Number.isFinite(Number(gate.delta_oos_pp))
      ? Number(gate.delta_oos_pp)
      : null;
  const passed = !gate.skipped && !!gate.ok && !!gate.passed;
  const skipped = !!gate.skipped;
  const resOos =
    (gate.research && gate.research.oos) ||
    (gate.suggested && gate.suggested.oos) ||
    {};
  const baseOos =
    (gate.baseline && gate.baseline.oos) ||
    (gate.current && gate.current.oos) ||
    {};
  const yhatOos =
    resOos.oos_return_pct != null && Number.isFinite(Number(resOos.oos_return_pct))
      ? Number(resOos.oos_return_pct)
      : null;
  const baseOosRet =
    baseOos.oos_return_pct != null && Number.isFinite(Number(baseOos.oos_return_pct))
      ? Number(baseOos.oos_return_pct)
      : null;

  const meanDist =
    cl && cl.mean_distance_to_group_beta != null
      ? Number(cl.mean_distance_to_group_beta)
      : null;
  const cap =
    cl && cl.within_dist_cap != null ? Number(cl.within_dist_cap) : null;
  let tightScore = null;
  if (
    meanDist != null &&
    Number.isFinite(meanDist) &&
    cap != null &&
    Number.isFinite(cap) &&
    cap > 0
  ) {
    tightScore = 1 - Math.min(1, meanDist / cap);
  }

  const outlier = !!(cl && cl.outlier_singleton);
  const singleton = !!(cl && (cl.singleton || outlier));

  return {
    r2,
    icMean,
    icir,
    deltaOos,
    yhatOos,
    baseOosRet,
    tightScore,
    meanDist,
    cap,
    memberN,
    sampleN,
    passed,
    skipped,
    gate,
    singleton,
    outlier,
  };
}

/** 0–100 综合健康分：过门/ΔOOS/R²/ICIR/同质加权。 */
function clusterHealthScore(h) {
  if (!h || h.skipped) return null;
  let s = 0;
  let w = 0;
  const add = (v, weight, mapFn) => {
    if (v == null || !Number.isFinite(v)) return;
    s += mapFn(v) * weight;
    w += weight;
  };
  add(h.r2, 22, (v) => Math.max(0, Math.min(1, v / 0.7)) * 100);
  add(h.icir, 18, (v) => Math.max(0, Math.min(1, (v + 0.2) / 0.7)) * 100);
  add(h.icMean, 10, (v) => Math.max(0, Math.min(1, (v + 0.05) / 0.15)) * 100);
  add(h.deltaOos, 28, (v) => Math.max(0, Math.min(1, (v + 1) / 3)) * 100);
  add(h.tightScore, 12, (v) => Math.max(0, Math.min(1, v)) * 100);
  add(h.yhatOos, 10, (v) => Math.max(0, Math.min(1, (v + 2) / 8)) * 100);
  if (w < 1e-6) return h.passed ? 55 : 35;
  let score = s / w;
  if (h.passed) score = Math.min(100, score + 8);
  else score = Math.max(0, score - 12);
  return Math.round(score);
}

/**
 * 跨组健康矩阵：汇总条 + 多指标表（按综合分排序）。
 * @param {object[]} clusters
 * @param {{ escapeHtml?: Function, preferredLabel?: string }} [opts]
 */
export function buildClustersHealthMatrixHtml(
  clusters,
  { escapeHtml, preferredLabel } = {}
) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  if (!Array.isArray(clusters) || !clusters.length) return "";

  const pref = preferredLabel != null ? String(preferredLabel) : "";
  const rows = clusters.map((cl, idx) => {
    const label = String(
      (cl && cl.label) || `G${((cl && cl.cluster_id) ?? idx) + 1}`
    );
    const h = extractClusterHealth(cl);
    const isPref = !!(pref && (label === pref || String(cl && cl.label) === pref));
    const kind = h.outlier
      ? "离群"
      : h.singleton
        ? "单票"
        : isPref
          ? "优先"
          : "组";
    const score = clusterHealthScore(h);
    return { label, kind, isPref, score, ...h };
  });

  // 按组名排序；跳过组沉底
  rows.sort((a, b) => {
    if (a.skipped !== b.skipped) return a.skipped ? 1 : -1;
    return String(a.label).localeCompare(String(b.label), "zh");
  });

  const tR2 = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v - 0.3) / 0.4)));
  const tIc = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 0.08)));
  const tIcir = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 0.5)));
  const tDelta = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 1)));
  const tTight = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) * 2 - 1)));
  const tYhat = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 3)));
  const tScore = (v) =>
    v == null ? null : Math.max(-1, Math.min(1, (Number(v) - 50) / 50));

  const f2 = (x) => (x == null || !Number.isFinite(Number(x)) ? "—" : Number(x).toFixed(2));
  const f3 = (x) => (x == null || !Number.isFinite(Number(x)) ? "—" : Number(x).toFixed(3));
  const fSigned = (x, digits = 2) => {
    if (x == null || !Number.isFinite(Number(x))) return "—";
    const n = Number(x);
    return `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;
  };

  const avg = (arr) =>
    arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : null;
  const passN = rows.filter((r) => r.passed).length;
  const skipN = rows.filter((r) => r.skipped).length;
  const scoredN = rows.length - skipN;
  const avgR2 = avg(rows.map((r) => r.r2).filter((v) => v != null));
  const avgIcir = avg(rows.map((r) => r.icir).filter((v) => v != null));
  const avgDelta = avg(rows.map((r) => r.deltaOos).filter((v) => v != null));
  const avgTight = avg(rows.map((r) => r.tightScore).filter((v) => v != null));
  const avgScore = avg(rows.map((r) => r.score).filter((v) => v != null));
  const best = rows.find((r) => !r.skipped && r.score != null) || null;
  const worst =
    [...rows].reverse().find((r) => !r.skipped && r.score != null) || null;

  const kpi = (k, v, tip) =>
    `<span class="yhat-mx-kpi" title="${esc(tip)}">` +
    `<span class="yhat-mx-kpi-k">${esc(k)}</span>` +
    `<span class="yhat-mx-kpi-v">${esc(v)}</span>` +
    `</span>`;

  const kpiRow =
    `<div class="yhat-mx-kpis" aria-label="跨组健康汇总">` +
    kpi("过门", `${passN}/${scoredN || 0}`, "OOS 过门组数 / 可评组数") +
    kpi("均R²", f2(avgR2), "各组 OLS R² 均值") +
    kpi("均ICIR", f2(avgIcir), "各组 ICIR 均值") +
    kpi("均ΔOOS", fSigned(avgDelta), "ŷ−heuristic 增益均值 (pp)") +
    kpi("均同质", f2(avgTight), "同质分均值（越高越紧）") +
    kpi("均分", avgScore != null ? String(Math.round(avgScore)) : "—", "综合健康分均值") +
    (best
      ? kpi("最佳", `${best.label}·${best.score}`, "综合分最高组")
      : "") +
    (worst && best && worst.label !== best.label
      ? kpi("偏弱", `${worst.label}·${worst.score}`, "综合分最低可评组")
      : "") +
    `</div>`;

  const cols = [
    ["#", "排序（按综合分）"],
    ["组", "组标签"],
    ["只", "组成员数"],
    ["型", "组 / 单票 / 离群 / 优先导出"],
    ["R²", "组 OLS R²；≥0.7 偏绿"],
    ["IC", "组截面 IC 均值"],
    ["ICIR", "组 IC 信息比；正红负绿"],
    ["ŷOOS", "研究臂 OOS 收益%；正红负绿"],
    ["ΔOOS", "ŷ−heuristic 增益 (pp)；正红负绿"],
    ["同质", "1−均β距/cap；越高越同质"],
    ["n", "拟合样本数"],
    ["门", "OOS 门禁"],
    ["分", "综合健康分 0–100"],
  ];
  const headCells = cols
    .map(
      ([c, tip]) =>
        `<th class="yhat-mx-th" title="${esc(tip)}">${esc(c)}</th>`
    )
    .join("");

  const bodyRows = rows
    .map((r, rank) => {
      const rowCls = [
        "yhat-mx-row",
        r.isPref ? "is-pref" : "",
        r.singleton ? "is-singleton-row" : "",
        r.passed ? "is-pass-row" : "",
        r.skipped ? "is-skip-row" : "",
        !r.skipped && !r.passed ? "is-fail-row" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const gateTip = r.skipped
        ? r.gate.note || r.gate.reason || "skipped"
        : r.gate.reason ||
          (r.passed ? "OOS 过门" : "OOS 未过") +
            (r.baseOosRet != null || r.yhatOos != null
              ? ` · 基线 ${f2(r.baseOosRet)}% / ŷ ${f2(r.yhatOos)}%`
              : "");
      const gateCell = r.skipped
        ? `<td class="yhat-mx-td is-skip" title="${esc(gateTip)}">跳</td>`
        : `<td class="yhat-mx-td ${r.passed ? "is-pass" : "is-fail"}" title="${esc(
            gateTip
          )}">${r.passed ? "✓" : "✗"}</td>`;
      const kindCls =
        r.kind === "优先"
          ? " is-kind-pref"
          : r.kind === "离群"
            ? " is-kind-outlier"
            : r.kind === "单票"
              ? " is-kind-single"
              : "";
      const cells =
        `<td class="yhat-mx-td num yhat-mx-td-rank" title="排名">${rank + 1}</td>` +
        `<td class="yhat-mx-td yhat-mx-td-group${r.singleton ? " is-singleton" : ""}${
          r.isPref ? " is-pref-label" : ""
        }" title="${esc(r.label)}">${esc(r.label)}</td>` +
        `<td class="yhat-mx-td num" title="成员数">${
          r.memberN == null ? "—" : esc(String(r.memberN))
        }</td>` +
        `<td class="yhat-mx-td yhat-mx-td-kind${kindCls}" title="${esc(r.kind)}">${esc(
          r.kind
        )}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tR2(r.r2))}" title="R² ${f3(
          r.r2
        )}">${esc(f2(r.r2))}</td>` +
        `<td class="yhat-mx-td num" style="${signedReturnCellBg(tIc(r.icMean))}" title="IC ${f3(
          r.icMean
        )}">${esc(f3(r.icMean))}</td>` +
        `<td class="yhat-mx-td num" style="${signedReturnCellBg(tIcir(r.icir))}" title="ICIR ${f3(
          r.icir
        )}">${esc(f2(r.icir))}</td>` +
        `<td class="yhat-mx-td num" style="${signedReturnCellBg(tYhat(r.yhatOos))}" title="ŷ OOS ${f2(
          r.yhatOos
        )}% · 基线 ${f2(r.baseOosRet)}%">${
          r.yhatOos == null ? "—" : esc(fSigned(r.yhatOos))
        }</td>` +
        `<td class="yhat-mx-td num" style="${signedReturnCellBg(tDelta(r.deltaOos))}" title="ΔOOS ${
          r.deltaOos != null ? fSigned(r.deltaOos) + "pp" : "—"
        }">${r.deltaOos == null ? "—" : esc(fSigned(r.deltaOos))}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tTight(r.tightScore))}" title="均β距 ${f3(
          r.meanDist
        )}${r.cap != null ? " / cap " + f3(r.cap) : ""}">${
          r.tightScore == null
            ? r.meanDist != null
              ? esc(f3(r.meanDist))
              : "—"
            : esc(r.tightScore.toFixed(2))
        }</td>` +
        `<td class="yhat-mx-td num" title="拟合样本 n">${
          r.sampleN == null ? "—" : esc(String(r.sampleN))
        }</td>` +
        gateCell +
        `<td class="yhat-mx-td num yhat-mx-td-score" style="${qualityCellBg(
          tScore(r.score)
        )}" title="综合健康分">${r.score == null ? "—" : esc(String(r.score))}</td>`;
      return `<tr class="${rowCls}">${cells}</tr>`;
    })
    .join("");

  const swatch = (cls, label) =>
    `<span class="yhat-mx-swatch ${cls}" aria-hidden="true"></span>` +
    `<span class="yhat-mx-swatch-lab">${esc(label)}</span>`;
  const legend =
    `<p class="yhat-mx-legend" aria-label="色阶说明">` +
    `<span class="yhat-mx-legend-group">` +
    swatch("is-up", "涨/正") +
    swatch("is-down", "跌/负") +
    swatch("is-good", "质量好") +
    swatch("is-weak", "质量弱") +
    `</span>` +
    `<span class="yhat-mx-legend-note">IC·ŷ·Δ 用涨跌色 · R²·同质·分用质量色 · 按综合分排序` +
    (pref ? ` · 优先 ${esc(pref)}` : "") +
    `</span>` +
    `</p>`;

  return (
    `<details class="yhat-mx yhat-mx-health">` +
    `<summary class="yhat-mx-summary">` +
    `<span class="yhat-mx-title">跨组健康矩阵</span>` +
    `<span class="yhat-mx-meta">过门 ${passN}/${scoredN}${
      skipN ? ` · 跳过 ${skipN}` : ""
    } · ${rows.length} 组</span>` +
    `</summary>` +
    kpiRow +
    `<div class="yhat-mx-scroll">` +
    `<table class="yhat-mx-table yhat-mx-table--health"><thead><tr>${headCells}</tr></thead><tbody>${bodyRows}</tbody></table>` +
    `</div>` +
    legend +
    `</details>`
  );
}

/* ────────────────────────────────────────────────────────────
 * [p852] 防御性桩：以下三个函数已删除（全局零调用方），
 * 重新导出为「调用即 console.warn 并返回空」，保留原导出契约。
 * 若控制台出现 "[yhat_viz] <name> was removed in p852"，
 * 说明存在遗漏的动态调用方，请回查 p852 删除记录并按需恢复实现。
 * ──────────────────────────────────────────────────────────── */
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
