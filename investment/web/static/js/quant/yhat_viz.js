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

/** Nice tick steps for axis labels. */
function niceTicks(lo, hi, target = 5) {
  const span = hi - lo;
  if (!(span > 0) || !Number.isFinite(span)) return [lo, hi];
  const raw = span / Math.max(2, target - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let step;
  if (norm <= 1.5) step = 1 * mag;
  else if (norm <= 3) step = 2 * mag;
  else if (norm <= 7) step = 5 * mag;
  else step = 10 * mag;
  const start = Math.ceil(lo / step) * step;
  const ticks = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) {
    ticks.push(Number(v.toPrecision(12)));
    if (ticks.length > 12) break;
  }
  if (!ticks.length) ticks.push(lo, hi);
  return ticks;
}

export function binScores(scores, { bins = 12, min, max } = {}) {
  const vals = (scores || [])
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
  if (!vals.length) {
    return {
      bins: [],
      min: 0,
      max: 0,
      n: 0,
      mean: null,
      median: null,
      std: null,
      p25: null,
      p75: null,
      pct_pos: null,
      data_min: null,
      data_max: null,
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
  const sorted = [...vals].sort((a, b) => a - b);
  const mean = vals.reduce((s, x) => s + x, 0) / vals.length;
  const median = _quantile(sorted, 0.5);
  const p25 = _quantile(sorted, 0.25);
  const p75 = _quantile(sorted, 0.75);
  const variance =
    vals.reduce((s, x) => s + (x - mean) * (x - mean), 0) / vals.length;
  const std = Math.sqrt(variance);
  const pctPos = vals.filter((v) => v > 0).length / vals.length;
  return {
    bins: counts.map((count, i) => ({
      x0: lo + i * width,
      x1: lo + (i + 1) * width,
      count,
    })),
    min: lo,
    max: hi,
    n: vals.length,
    mean,
    median,
    std,
    p25,
    p75,
    pct_pos: pctPos,
    data_min: dataMin,
    data_max: dataMax,
  };
}

/** 因子贡献水平条 HTML（插入 tooltip）。 */
export function buildContribBarsHtml(terms, { escapeHtml, maxAbs } = {}) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  const rows = (terms || [])
    .filter((t) => t && !t.gated)
    .map((t) => ({
      key: t.key || "",
      label: t.label || t.key || "—",
      contrib: Number(t.contrib),
    }))
    .filter((t) => Number.isFinite(t.contrib));
  if (!rows.length) return "";
  const peak =
    maxAbs != null && Number.isFinite(maxAbs) && maxAbs > 0
      ? maxAbs
      : Math.max(...rows.map((r) => Math.abs(r.contrib)), 1e-9);
  const bars = rows
    .slice(0, 10)
    .map((r) => {
      const pct = Math.min(100, (Math.abs(r.contrib) / peak) * 100);
      const side = r.contrib >= 0 ? "pos" : "neg";
      const sign = r.contrib > 0 ? "+" : "";
      return (
        `<div class="yhat-contrib-row" title="${esc(r.key)}">` +
        `<span class="yhat-contrib-name">${esc(r.label)}</span>` +
        `<span class="yhat-contrib-track">` +
        `<span class="yhat-contrib-bar ${side}" style="width:${pct.toFixed(1)}%"></span>` +
        `</span>` +
        `<span class="yhat-contrib-val ${side}">${esc(
          `${sign}${r.contrib.toFixed(3)}`
        )}</span>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="yhat-contrib-bars" aria-label="因子贡献">` +
    `<div class="score-section-title">贡献条</div>` +
    bars +
    `</div>`
  );
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
  const cssW = canvas.clientWidth || opts.width || 480;
  const cssH = canvas.clientHeight || opts.height || 132;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const muted = cssVar(canvas, "--muted", "#64748b");
  const border = cssVar(canvas, "--border-color", "#e2e8f0");
  const text = cssVar(canvas, "--text", "#334155");
  const accent = cssVar(canvas, "--accent", "#1890ff");
  const posFill = "rgba(220, 38, 38, 0.42)";
  const negFill = "rgba(5, 150, 105, 0.42)";
  const posStroke = "rgba(185, 28, 28, 0.55)";
  const negStroke = "rgba(4, 120, 87, 0.55)";
  const fontUi = '11px ui-sans-serif, system-ui, "Segoe UI", sans-serif';
  const fontMono =
    '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

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
  const yTicks = niceTicks(0, yMax, 4);
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

  // x ticks
  const xTicks = niceTicks(pack.min, pack.max, 6);
  ctx.fillStyle = muted;
  ctx.font = fontMono;
  ctx.textBaseline = "top";
  xTicks.forEach((v) => {
    if (Math.abs(v) < 1e-9) return;
    const x = xAt(v);
    if (x < pad.l || x > pad.l + w) return;
    ctx.strokeStyle = "rgba(100, 116, 139, 0.25)";
    ctx.beginPath();
    ctx.moveTo(x, pad.t + h);
    ctx.lineTo(x, pad.t + h + 3);
    ctx.stroke();
    ctx.textAlign = "center";
    ctx.fillText(v.toFixed(Math.abs(v) >= 10 ? 0 : 1), x, pad.t + h + 5);
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
 * points: [{ yhat, realized_h, hit }]
 */
export function paintYhatScatter(canvas, points, opts = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return null;
  const pts = (points || [])
    .map((p) => ({
      x: Number(p.yhat),
      y: Number(p.realized_h != null ? p.realized_h : p.realized),
      hit: p.hit,
      code: p.code || p.stock_code || "",
      name: p.name || p.stock_name || "",
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

  const muted = cssVar(canvas, "--muted", "#64748b");
  const border = cssVar(canvas, "--border-color", "#e2e8f0");
  const text = cssVar(canvas, "--text", "#334155");
  const pad = { t: 10, r: 14, b: 30, l: 40 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = muted;
  ctx.font = '11px ui-sans-serif, system-ui, "Segoe UI", sans-serif';
  if (pts.length < 2) {
    ctx.fillText("样本不足，无法画散点", pad.l, pad.t + 14);
    return { n: pts.length, hits: 0 };
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

  // quadrant bands (same-sign = hit)
  const zx = sx(0);
  const zy = sy(0);
  const qHit = "rgba(4, 120, 87, 0.05)";
  const qMiss = "rgba(185, 28, 28, 0.05)";
  // Q1 (+,+) hit · Q2 (-,+) miss · Q3 (-,-) hit · Q4 (+,-) miss
  ctx.fillStyle = qHit;
  ctx.fillRect(zx, pad.t, Math.max(0, pad.l + w - zx), Math.max(0, zy - pad.t));
  ctx.fillRect(pad.l, zy, Math.max(0, zx - pad.l), Math.max(0, pad.t + h - zy));
  ctx.fillStyle = qMiss;
  ctx.fillRect(pad.l, pad.t, Math.max(0, zx - pad.l), Math.max(0, zy - pad.t));
  ctx.fillRect(zx, zy, Math.max(0, pad.l + w - zx), Math.max(0, pad.t + h - zy));

  // grid + ticks
  const xTicks = niceTicks(xMin, xMax, 5);
  const yTicks = niceTicks(yMin, yMax, 5);
  ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  xTicks.forEach((v) => {
    const x = sx(v);
    if (x < pad.l || x > pad.l + w) return;
    ctx.strokeStyle = "rgba(100, 116, 139, 0.1)";
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.fillStyle = muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(v.toFixed(Math.abs(v) >= 10 ? 0 : 1), x, pad.t + h + 4);
  });
  yTicks.forEach((v) => {
    const y = sy(v);
    if (y < pad.t || y > pad.t + h) return;
    ctx.strokeStyle = "rgba(100, 116, 139, 0.1)";
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + w, y);
    ctx.stroke();
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(v.toFixed(Math.abs(v) >= 10 ? 0 : 1), pad.l - 5, y);
  });

  // zero axes
  ctx.strokeStyle = "rgba(51, 65, 85, 0.45)";
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  ctx.moveTo(pad.l, zy);
  ctx.lineTo(pad.l + w, zy);
  ctx.moveTo(zx, pad.t);
  ctx.lineTo(zx, pad.t + h);
  ctx.stroke();

  let hits = 0;
  pts.forEach((p) => {
    const hit = isHit(p);
    if (hit) hits += 1;
    const x = sx(p.x);
    const y = sy(p.y);
    ctx.beginPath();
    ctx.arc(x, y, 3.8, 0, Math.PI * 2);
    ctx.fillStyle = hit ? "rgba(4, 120, 87, 0.78)" : "rgba(185, 28, 28, 0.8)";
    ctx.fill();
    ctx.strokeStyle = hit ? "rgba(4, 120, 87, 0.95)" : "rgba(153, 27, 27, 0.95)";
    ctx.lineWidth = 0.8;
    ctx.stroke();
  });

  ctx.fillStyle = text;
  ctx.font = '10px ui-sans-serif, system-ui, "Segoe UI", sans-serif';
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("ŷ %", cssW - 8, cssH - 8);
  ctx.save();
  ctx.translate(11, pad.t + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText("实现 %", 0, 0);
  ctx.restore();

  return {
    n: pts.length,
    hits,
    hit_rate: pts.length ? hits / pts.length : null,
    xMin,
    xMax,
    yMin,
    yMax,
  };
}

/** 组内 ŷ 条带（兼容旧调用；现委托迷你直方图）。 */
export function buildGroupYhatStripHtml(scores, opts = {}) {
  return buildGroupYhatHistHtml(scores, opts);
}

/**
 * 组内 ŷ 迷你直方图（分箱密度 · 零轴 · μ/med）。
 * 用于研究枢纽分组组头，纯 HTML/CSS，无需 canvas。
 * Pro 升级：P20/P80 分层线 + 空/多/中性三区阴影背景 + conviction KPI + status chip
 */
export function buildGroupYhatHistHtml(
  scores,
  { escapeHtml, bins = 12, maxBars = 40, universeRef = null } = {}
) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  const vals = (scores || [])
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
  if (!vals.length) return "";
  const k = Math.max(
    6,
    Math.min(16, Math.round(bins) || 12, Math.max(4, vals.length))
  );
  const pack = binScores(vals, { bins: k });
  if (!pack.n) return "";
  const maxC = Math.max(...pack.bins.map((b) => b.count), 1);
  const span = pack.max - pack.min || 1;
  const pctAt = (v) =>
    `${Math.max(0, Math.min(100, ((Number(v) - pack.min) / span) * 100)).toFixed(2)}%`;

  // —— Pro: P20 / P80 分位阈值计算（conviction 分层依据）——
  const sorted = [...vals].sort((a, b) => a - b);
  const n = sorted.length;
  const pQuantile = (q) => {
    const pos = (n - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.min(n - 1, lo + 1);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  };
  const p20 = pQuantile(0.2);
  const p80 = pQuantile(0.8);
  const p10 = pQuantile(0.1);
  const p90 = pQuantile(0.9);
  const topBottomSpread = p80 - p20;
  // Top-Bottom spread / σ（分离度强度，越大分层越有意义）
  const spreadOverSigma = pack.std > 0 ? topBottomSpread / pack.std : 0;
  // Top 3 得分均值 vs μ 的 σ 倍数
  const top3Mean = n >= 3 ? sorted.slice(-3).reduce((s, x) => s + x, 0) / 3 : sorted[sorted.length - 1];
  const topSigma = pack.std > 0 ? (top3Mean - (pack.mean || 0)) / pack.std : 0;
  // 偏斜度方向：判断偏多/偏空
  const diffMuMed = (pack.mean || 0) - (pack.median || 0);
  const pctPos = pack.pct_pos != null ? pack.pct_pos : sorted.filter((v) => v > 0).length / n;

  const bars = pack.bins
    .map((b) => {
      const h = b.count ? Math.max(8, (b.count / maxC) * 100) : 0;
      const mid = (b.x0 + b.x1) / 2;
      const side = mid >= 0 ? "pos" : "neg";
      const tip = `[${b.x0.toFixed(2)}, ${b.x1.toFixed(2)}) · ${b.count} 只`;
      // conviction 分级着色（叠加在 pos/neg 上）
      let tier = "mid";
      if (mid < p20) tier = "short";
      else if (mid > p80) tier = "long";
      if (!b.count) {
        return `<span class="yhat-ghist-bar is-empty is-tier-${tier}" title="${esc(tip)}"></span>`;
      }
      return (
        `<span class="yhat-ghist-bar ${side} is-tier-${tier}" style="height:${h.toFixed(0)}%" ` +
        `title="${esc(tip)}"></span>`
      );
    })
    .join("");

  const f = (x, d = 2) =>
    x != null && Number.isFinite(x) ? Number(x).toFixed(d) : "—";
  const markers = [];
  if (pack.min < 0 && pack.max > 0) {
    markers.push(
      `<span class="yhat-ghist-mark is-zero" style="left:${pctAt(0)}" title="ŷ=0">0</span>`
    );
  }
  if (pack.mean != null) {
    markers.push(
      `<span class="yhat-ghist-mark is-mu" style="left:${pctAt(pack.mean)}" title="μ ${f(
        pack.mean
      )}">μ</span>`
    );
  }
  if (pack.median != null) {
    markers.push(
      `<span class="yhat-ghist-mark is-med" style="left:${pctAt(pack.median)}" title="med ${f(
        pack.median
      )}">med</span>`
    );
  }
  // —— Pro: 分层竖线（P20 减仓建议 / P80 加仓建议）——
  markers.push(
    `<span class="yhat-ghist-mark is-p20" style="left:${pctAt(p20)}" title="P20 短仓建议 = ${f(p20)}">P20</span>`
  );
  markers.push(
    `<span class="yhat-ghist-mark is-p80" style="left:${pctAt(p80)}" title="P80 长仓建议 = ${f(p80)}">P80</span>`
  );

  // 兼容旧参数名（不再抽样竖条）
  void maxBars;

  return (
    `<div class="yhat-group-hist is-aux yhat-group-hist--pro" aria-label="组内 ŷ 直方图（辅）">` +
    `<div class="yhat-ghist-meta yhat-ghist-meta--pro">` +
    `<div class="yhat-ghist-meta-row1">` +
    `<span class="yhat-ghist-caption">辅 · 截面 ŷ</span>` +
    `<span>n=${pack.n}</span>` +
    `<span>μ ${esc(f(pack.mean))}</span>` +
    `<span>med ${esc(f(pack.median))}</span>` +
    `<span>σ ${esc(f(pack.std))}</span>` +
    `<span>${esc((pctPos * 100).toFixed(0))}%&gt;0</span>` +
    `</div>` +
    `<div class="yhat-ghist-meta-row2">` +
    `<span class="yhat-ghist-conv is-strong" title="Top-Bottom / σ">T/B·σ ${esc(spreadOverSigma.toFixed(2))}</span>` +
    `<span class="yhat-ghist-conv" title="Top3 均值超 μ 几个 σ">Top3·σ ${esc(topSigma.toFixed(1))}</span>` +
    `<span class="yhat-ghist-conv" title="P20 / P80 具体阈值">P20/P80 ${esc(f(p20, 3))} / ${esc(f(p80, 3))}</span>` +
    `<span class="yhat-ghist-conv" title="μ-med 差值（正=右拖尾·负=左拖尾）">μ−med ${esc(diffMuMed.toFixed(3))}</span>` +
    `</div>` +
    `</div>` +
    `<div class="yhat-ghist-plot yhat-ghist-plot--pro" style="--tier-short-left:${pctAt(pack.min)};--tier-short-right:${pctAt(p20)};--tier-long-left:${pctAt(p80)};--tier-long-right:${pctAt(pack.max)};">` +
    `<div class="yhat-ghist-bars">${bars}</div>` +
    `<div class="yhat-ghist-marks">${markers.join("")}</div>` +
    `</div>` +
    `<div class="yhat-ghist-axis">` +
    `<span>${esc(f(pack.data_min != null ? pack.data_min : pack.min, 1))}</span>` +
    `<span>P20</span>` +
    `<span>0</span>` +
    `<span>P80</span>` +
    `<span>${esc(f(pack.data_max != null ? pack.data_max : pack.max, 1))}</span>` +
    `</div>` +
    `<div class="yhat-ghist-legend yhat-ghist-legend--pro">` +
    `<span class="is-mu">μ</span>` +
    `<span class="is-med">med</span>` +
    `<span class="is-zero">0</span>` +
    `<span class="is-p20">P20减</span>` +
    `<span class="is-p80">P80加</span>` +
    `</div>` +
    `</div>`
  );
}

/**
 * 组同质性：成员 → 组 β 距离条（洞察：右尾长=硬并组）。
 * gaps: [{ code, distance_to_group_beta, within_cap? }]
 * Pro 升级：H-Score / Tukey Outlier / 偏度 Skew / d̄/dₘₐₓ + 分位渐变着色 + Q3/Q3+1.5IQR 参考线 + status chip
 */
export function buildBetaDistanceHtml(
  gaps,
  { escapeHtml, nameByCode = {}, maxRows = 12 } = {}
) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  const rows = (gaps || [])
    .map((g) => ({
      code: String(g.code || "").trim(),
      d: Number(g.distance_to_group_beta),
      cap: g.within_cap != null ? Number(g.within_cap) : null,
    }))
    .filter((g) => g.code && Number.isFinite(g.d));
  if (!rows.length) return "";
  rows.sort((a, b) => b.d - a.d);
  const shown = rows.slice(0, maxRows);
  const peak = Math.max(...rows.map((r) => r.d), 1e-6);
  const mean = rows.reduce((s, r) => s + r.d, 0) / rows.length;

  // —— Pro: 分位数统计 ——
  const sorted = [...rows].map((r) => r.d).sort((a, b) => a - b);
  const n = sorted.length;
  const quantile = (q) => {
    const pos = (n - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.min(n - 1, lo + 1);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  };
  const q1 = quantile(0.25);
  const q3 = quantile(0.75);
  const iqr = q3 - q1;
  const outlierThr = q3 + 1.5 * iqr;
  const outlierCount = rows.filter((r) => r.d > outlierThr).length;

  // 偏度 Skew（moment 3 / std³，样本修正近似）
  const m3 = sorted.reduce((s, x) => s + Math.pow(x - mean, 3), 0) / n;
  const variance = sorted.reduce((s, x) => s + Math.pow(x - mean, 2), 0) / n;
  const std = Math.sqrt(Math.max(0, variance));
  const skew = std > 0 ? m3 / Math.pow(std, 3) : 0;

  // H-Score：1 - CV，越小越集中
  const cv = mean > 0 ? std / mean : 0;
  const hScore = Math.max(0, Math.min(1, 1 - cv));
  const hGrade = hScore >= 0.85 ? "A" : hScore >= 0.65 ? "B" : hScore >= 0.5 ? "C" : "D";
  const hTone =
    hScore >= 0.85 ? "ok" : hScore >= 0.65 ? "good" : hScore >= 0.5 ? "warn" : "bad";
  const meanToPeak = peak > 0 ? mean / peak : 0;

  // 参考线归一化位置（%）
  const q3Pct = Math.min(100, (q3 / peak) * 100);
  const outPct = Math.min(100, (outlierThr / peak) * 100);

  const body = shown
    .map((r) => {
      const pct = Math.min(100, (r.d / peak) * 100);
      const name = nameByCode[r.code] || "";
      const lab = name ? `${name} ${r.code}` : r.code;
      const near =
        r.cap != null && Number.isFinite(r.cap) && r.cap > 0 && r.d >= r.cap * 0.85;
      // 分位分级着色
      let tier = "tight";
      if (r.d > outlierThr) tier = "outlier";
      else if (r.d > q3) tier = "warn";
      const isOutlier = tier === "outlier";
      return (
        `<div class="yhat-bdist-row yhat-bdist--${tier}${near ? " is-near-cap" : ""}${isOutlier ? " is-outlier" : ""}" title="${esc(
          `${lab} · d=${r.d.toFixed(3)}${isOutlier ? " · Tukey 异常点" : ""}`
        )}">` +
        `<span class="yhat-bdist-name">${esc(lab)}${isOutlier ? '<span class="yhat-bdist-warn" aria-label="异常点" title="Tukey 异常">!</span>' : ""}</span>` +
        `<span class="yhat-bdist-track"><span class="yhat-bdist-bar" style="width:${pct.toFixed(
          1
        )}%"></span></span>` +
        `<span class="yhat-bdist-val">${esc(r.d.toFixed(3))}</span>` +
        `</div>`
      );
    })
    .join("");

  // —— Pro: 参考虚线 overlay ——
  const refLines =
    `<div class="yhat-bdist-refs" aria-hidden="true">` +
    `<span class="yhat-bdist-ref is-q3" style="left:${q3Pct.toFixed(1)}%" title="Q3 = ${q3.toFixed(3)}">Q3</span>` +
    `<span class="yhat-bdist-ref is-out" style="left:${outPct.toFixed(1)}%" title="Tukey 异常阈值 Q3+1.5IQR = ${outlierThr.toFixed(3)}">F</span>` +
    `</div>`;

  const more =
    rows.length > shown.length
      ? `<button type="button" class="yhat-bdist-more yhat-bdist-more--btn" data-bdist-expand title="展开全部 ${rows.length} 行">…另 ${rows.length - shown.length} 只 ▾</button>`
      : "";

  const chipState =
    hTone === "ok" || hTone === "good" ? "ok"
    : hTone === "warn" ? (skew > 1.5 ? "warn" : "ok")
    : "warn";
  const chipText =
    hGrade === "A" || hGrade === "B" ? `H=${hGrade}`
    : hGrade === "C" ? (skew > 1.5 ? "H=C 长尾" : "H=C")
    : "H=D 建议拆";
  const metricStrip =
    `<div class="yhat-metric-strip" title="越短越同质 · F=Tukey 异常阈值 · 右偏=硬并组风险">` +
    `<span>H <b class="is-${hTone}">${hScore.toFixed(2)}</b> ${hGrade}</span>` +
    `<span>Out <b>${outlierCount}/${n}</b></span>` +
    `<span>Skew <b>${skew >= 0 ? "+" : ""}${skew.toFixed(1)}</b></span>` +
    `<span>d̄/峰值 <b>${meanToPeak.toFixed(2)}</b></span>` +
    `</div>`;

  return (
    `<div class="yhat-insight-card yhat-bdist yhat-pro-card" aria-label="组 β 距离">` +
    `<div class="yhat-insight-head yhat-pro-card-head">` +
    `<div class="yhat-pro-card-head-left">` +
    `<div class="yhat-pro-card-title-row">` +
    `<span class="yhat-insight-title">β 距离</span>` +
    `<span class="yhat-insight-meta">n=${n} · 峰 ${peak.toFixed(3)}</span>` +
    `</div>` +
    `</div>` +
    `<span class="quant-pro-status-chip" data-state="${chipState}">${chipText}</span>` +
    `</div>` +
    metricStrip +
    `<div class="yhat-bdist-body-wrap">` +
    body +
    refLines +
    `</div>` +
    more +
    `</div>`
  );
}

/**
 * 组预测力：日截面 IC spark + ICIR。
 * panel: factor_ic_panel（含 daily_tail / score_ic / rows）
 * Pro 升级：放大 260×72 SVG + 正负面积填充 + MA5 + ±1σ 带 + 超σ点 + 6-chip KPI + ΔIC₂₀ 衰减 + status chip
 */
export function buildGroupIcInsightHtml(panel, { escapeHtml } = {}) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  if (!panel || typeof panel !== "object") return "";
  const tail = Array.isArray(panel.daily_tail) ? panel.daily_tail : [];
  const pts = [];
  for (const day of tail) {
    const date = String(day.date || "").slice(0, 10);
    const facs = day.factors && typeof day.factors === "object" ? day.factors : {};
    const vals = Object.keys(facs)
      .map((k) => Number((facs[k] || {}).pearson))
      .filter((n) => Number.isFinite(n));
    if (!vals.length) continue;
    const ic = vals.reduce((s, x) => s + x, 0) / vals.length;
    pts.push({ date, ic });
  }
  const scorePear =
    panel.score_ic && panel.score_ic.pearson && typeof panel.score_ic.pearson === "object"
      ? panel.score_ic.pearson
      : {};
  let icMean =
    scorePear.ic_mean != null && Number.isFinite(Number(scorePear.ic_mean))
      ? Number(scorePear.ic_mean)
      : null;
  let icir =
    scorePear.icir != null && Number.isFinite(Number(scorePear.icir))
      ? Number(scorePear.icir)
      : null;
  if (icMean == null && pts.length) {
    icMean = pts.reduce((s, p) => s + p.ic, 0) / pts.length;
  }
  if (icir == null && Array.isArray(panel.rows)) {
    const irs = panel.rows
      .map((r) => Number(r.icir))
      .filter((n) => Number.isFinite(n));
    if (irs.length) icir = irs.reduce((s, x) => s + x, 0) / irs.length;
  }
  if (!pts.length && icMean == null && icir == null) {
    if (panel.mode === "group_ts_ic") {
      return (
        `<div class="yhat-insight-card yhat-gic yhat-pro-card" aria-label="组 IC">` +
        `<div class="yhat-insight-head yhat-pro-card-head">` +
        `<div class="yhat-pro-card-head-left">` +
        `<div class="yhat-pro-card-title-row">` +
        `<span class="yhat-insight-title">组 IC</span>` +
        `<span class="yhat-insight-meta">单票组</span>` +
        `</div>` +
        `</div>` +
        `<span class="quant-pro-status-chip" data-state="idle">无截面</span>` +
        `</div>` +
        `<div class="yhat-metric-strip"><span>时序 IC 回退 · 无截面 spark</span></div>` +
        `</div>`
      );
    }
    return "";
  }

  // —— Pro: 6-chip KPI 统计 ——
  let icsStd = null;
  let pctPos = null;
  let tStat = null;
  let stars = "";
  let ic20Diff = null;
  let icirGrade = "D";
  if (pts.length) {
    const icsArr = pts.map((p) => p.ic);
    const m = icsArr.reduce((s, x) => s + x, 0) / icsArr.length;
    icsStd = Math.sqrt(icsArr.reduce((s, x) => s + Math.pow(x - m, 2), 0) / icsArr.length);
    pctPos = icsArr.filter((v) => v > 0).length / icsArr.length;
    // t-stat ≈ ICIR * sqrt(T)
    if (icsStd != null && icsStd > 0 && icMean != null) {
      const compIcir = icMean / icsStd;
      if (icir == null || !Number.isFinite(icir)) icir = compIcir;
      tStat = compIcir * Math.sqrt(icsArr.length);
    } else if (icir != null) {
      tStat = icir * Math.sqrt(icsArr.length);
    }
    if (tStat != null) {
      const at = Math.abs(tStat);
      if (at >= 3.09) stars = "***";
      else if (at >= 1.96) stars = "**";
      else if (at >= 1.65) stars = "†";
      else stars = "";
    }
    // ΔIC₂₀ = 近 20 天 均值 - 全期均值（负=衰减）
    const k20 = Math.min(20, icsArr.length);
    if (k20 >= 5) {
      const recent = icsArr.slice(-k20);
      const rMean = recent.reduce((s, x) => s + x, 0) / recent.length;
      ic20Diff = rMean - m;
    }
  }
  if (icir != null && Number.isFinite(icir)) {
    if (icir >= 0.7) icirGrade = "A";
    else if (icir >= 0.4) icirGrade = "B";
    else if (icir >= 0.2) icirGrade = "C";
    else icirGrade = "D";
  }
  const icirTone =
    icirGrade === "A" ? "ok"
    : icirGrade === "B" ? "good"
    : icirGrade === "C" ? "warn"
    : "bad";
  const icTone = icMean != null ? (icMean >= 0 ? "good" : "bad") : "good";
  const pctTone = pctPos != null ? (pctPos >= 0.55 ? "good" : pctPos >= 0.5 ? "warn" : "bad") : "good";
  const decayTone = ic20Diff != null ? (ic20Diff >= 0 ? "ok" : ic20Diff >= -0.01 ? "warn" : "bad") : "good";

  let spark = "";
  if (pts.length >= 2) {
    const w = 260;
    const h = 72;
    const pad = { l: 28, r: 20, t: 8, b: 18 };
    const iw = w - pad.l - pad.r;
    const ih = h - pad.t - pad.b;
    const ics = pts.map((p) => p.ic);
    let lo = Math.min(...ics, 0);
    let hi = Math.max(...ics, 0);
    if (hi <= lo) { lo -= 0.05; hi += 0.05; }
    const icSigma = icsStd != null ? icsStd : 0;
    const xAt = (i) => pad.l + (i / (pts.length - 1)) * iw;
    const yAt = (v) => pad.t + ih - ((v - lo) / (hi - lo)) * ih;
    const zeroY = yAt(0);
    const hiSigmaY = icSigma > 0 ? yAt(icSigma) : null;
    const loSigmaY = icSigma > 0 ? yAt(-icSigma) : null;

    const mainLinePts = pts.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.ic).toFixed(1)}`).join(" ");

    // ±面积填充：正负分开做 polygon 到 0 轴
    let areaPos = "";
    let areaNeg = "";
    if (zeroY != null) {
      const posSegs = [];
      const negSegs = [];
      ics.forEach((v, i) => {
        const x = xAt(i);
        const y = yAt(v);
        if (v >= 0) posSegs.push([x, y]); else negSegs.push([x, y]);
      });
      const closeAtZero = (segs, sign) => {
        if (!segs.length) return "";
        const first = segs[0];
        const last = segs[segs.length - 1];
        const ptsSvg =
          `${xAt(0).toFixed(1)},${zeroY.toFixed(1)} ` +
          segs.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ") +
          ` ${xAt(pts.length - 1).toFixed(1)},${zeroY.toFixed(1)}`;
        return ptsSvg;
      };
      // 简单整体面积填充（不用按符号切，透明度低混色也可）：把整个序列下沿都接到 0 轴
      const fullArea =
        `${xAt(0).toFixed(1)},${zeroY.toFixed(1)} ` +
        pts.map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.ic).toFixed(1)}`).join(" ") +
        ` ${xAt(pts.length - 1).toFixed(1)},${zeroY.toFixed(1)}`;
      // 用 fill-rule: evenodd + 两个半透明层
      areaPos = `<polygon class="yhat-gic-area-pos" points="${fullArea}" />`;
    }

    // MA5 平滑线
    let maLine = "";
    if (pts.length >= 5) {
      const maPts = [];
      for (let i = 0; i < pts.length; i++) {
        const loIdx = Math.max(0, i - 4);
        const win = ics.slice(loIdx, i + 1);
        const mv = win.reduce((s, x) => s + x, 0) / win.length;
        maPts.push(`${xAt(i).toFixed(1)},${yAt(mv).toFixed(1)}`);
      }
      maLine = `<polyline class="yhat-gic-ma" fill="none" points="${maPts.join(" ")}" />`;
    }

    // 超 σ 点 markers
    let outMarkers = "";
    if (icSigma > 0) {
      ics.forEach((v, i) => {
        if (Math.abs(v - (icMean != null ? icMean : 0)) > icSigma) {
          const side = v >= 0 ? "pos" : "neg";
          outMarkers += `<circle class="yhat-gic-out is-${side}" cx="${xAt(i).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="3.2" />`;
        }
      });
    }

    // 最后 5 个点加大圆点
    let tailDots = "";
    const startIdx = Math.max(0, pts.length - 5);
    for (let i = startIdx; i < pts.length; i++) {
      const isLast = i === pts.length - 1;
      tailDots +=
        `<circle class="yhat-gic-tail${isLast ? " is-last" : ""}" ` +
        `cx="${xAt(i).toFixed(1)}" cy="${yAt(pts[i].ic).toFixed(1)}" r="${isLast ? 3.6 : 2.4}" ` +
        `${isLast ? `fill="${pts[i].ic >= 0 ? "#b91c1c" : "#047857"}"` : ""} />`;
    }

    // x 轴起止 date 标签
    const xLabel1 = pts[0].date.slice(5);
    const xLabel2 = pts[pts.length - 1].date.slice(5);

    spark =
      `<svg class="yhat-gic-spark yhat-gic-spark--pro" viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">` +
      // ±1σ 带
      (hiSigmaY != null && loSigmaY != null
        ? `<rect class="yhat-gic-sigma-band" x="${pad.l}" y="${Math.min(hiSigmaY, loSigmaY).toFixed(1)}" width="${iw}" height="${Math.abs(hiSigmaY - loSigmaY).toFixed(1)}" />`
        : "") +
      // 面积正区
      areaPos +
      // 0 轴
      `<line class="yhat-gic-zero" x1="${pad.l}" y1="${zeroY.toFixed(1)}" x2="${w - pad.r}" y2="${zeroY.toFixed(1)}" />` +
      // 主折线
      `<polyline class="yhat-gic-line" fill="none" points="${mainLinePts}" />` +
      // MA5
      maLine +
      // 超 σ 点
      outMarkers +
      // 尾部圆点
      tailDots +
      // 起止日期
      `<text class="yhat-gic-xlab" x="${pad.l}" y="${h - 4}" text-anchor="start">${xLabel1}</text>` +
      `<text class="yhat-gic-xlab" x="${w - pad.r}" y="${h - 4}" text-anchor="end">${xLabel2}</text>` +
      `</svg>`;
  }

  const f = (x, d = 3) =>
    x != null && Number.isFinite(x) ? Number(x).toFixed(d) : "—";

  let chipState = "idle";
  let chipText = "—";
  if (icirGrade === "A" && (ic20Diff == null || ic20Diff >= -0.005)) {
    chipState = "ok"; chipText = "强预测";
  } else if (icirGrade === "B" && (ic20Diff == null || ic20Diff >= -0.01)) {
    chipState = "ok"; chipText = "预测可";
  } else if (icirGrade === "C" && ic20Diff != null && ic20Diff < -0.01) {
    chipState = "warn"; chipText = "衰减";
  } else if (icirGrade === "C") {
    chipState = "warn"; chipText = "弱信号";
  } else if (ic20Diff != null && ic20Diff < -0.01) {
    chipState = "warn"; chipText = "衰减";
  } else if (icirGrade === "D") {
    chipState = "warn"; chipText = "不稳";
  }
  if (icMean != null && icMean < 0 && Math.abs(icMean) > 0.001) {
    chipState = "warn";
    chipText = icirGrade === "A" || icirGrade === "B" ? "方向反" : "噪声";
  }
  const metricStrip =
    `<div class="yhat-metric-strip" title="面积=相对零轴 · 虚线=MA5 · 灰带=±1σ · ΔIC₂₀&lt;0 为衰减">` +
    `<span>μIC <b class="is-${icTone}">${esc(f(icMean, 3))}</b></span>` +
    `<span>ICIR <b class="is-${icirTone}">${esc(f(icir, 2))}</b> ${icirGrade}</span>` +
    `<span>t <b>${tStat != null ? `${tStat.toFixed(1)}${stars || ""}` : "—"}</b></span>` +
    `<span>IC&gt;0 <b>${pctPos != null ? `${(pctPos * 100).toFixed(0)}%` : "—"}</b></span>` +
    `<span>Δ20 <b class="is-${decayTone}">${ic20Diff != null ? `${ic20Diff >= 0 ? "+" : ""}${ic20Diff.toFixed(3)}` : "—"}</b></span>` +
    `</div>`;

  return (
    `<div class="yhat-insight-card yhat-gic yhat-pro-card" aria-label="组 IC">` +
    `<div class="yhat-insight-head yhat-pro-card-head">` +
    `<div class="yhat-pro-card-head-left">` +
    `<div class="yhat-pro-card-title-row">` +
    `<span class="yhat-insight-title">组 IC</span>` +
    `<span class="yhat-insight-meta">${pts.length}d</span>` +
    `</div>` +
    `</div>` +
    `<span class="quant-pro-status-chip" data-state="${chipState}">${chipText}</span>` +
    `</div>` +
    metricStrip +
    (spark || "") +
    `</div>`
  );
}

/**
 * 跨组 OOS 增益条：heuristic → ŷ 的 ΔOOS(pp)。
 */
export function buildClustersOosDeltaHtml(clusters, { escapeHtml } = {}) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  const rows = (clusters || [])
    .map((cl) => {
      const gate = cl.oos_gate || {};
      const label = String(cl.label || `G${(cl.cluster_id || 0) + 1}`);
      if (gate.skipped) {
        return {
          label,
          delta: null,
          skipped: true,
          reason: gate.reason || "skipped",
          passed: false,
        };
      }
      const d = Number(gate.delta_oos_pp);
      if (!Number.isFinite(d)) return null;
      return {
        label,
        delta: d,
        skipped: false,
        passed: !!(gate.ok && gate.passed),
        reason: gate.reason || "",
      };
    })
    .filter(Boolean);
  if (!rows.length) return "";
  const scored = rows.filter((r) => r.delta != null);
  if (!scored.length && !rows.some((r) => r.skipped)) return "";
  const peak = Math.max(...scored.map((r) => Math.abs(r.delta)), 0.5);
  const body = rows
    .map((r) => {
      if (r.skipped || r.delta == null) {
        return (
          `<div class="yhat-oos-row is-skip" title="${esc(r.reason)}">` +
          `<span class="yhat-oos-name">${esc(r.label)}</span>` +
          `<span class="yhat-oos-track"><span class="yhat-oos-skip">跳过</span></span>` +
          `<span class="yhat-oos-val">—</span>` +
          `</div>`
        );
      }
      const pct = Math.min(100, (Math.abs(r.delta) / peak) * 100);
      const side = r.delta >= 0 ? "pos" : "neg";
      const pass = r.passed ? " is-pass" : " is-fail";
      const sign = r.delta > 0 ? "+" : "";
      const stem =
        side === "pos"
          ? `<span class="yhat-oos-neg-pad"></span><span class="yhat-oos-fill pos" style="width:${pct.toFixed(
              1
            )}%"></span>`
          : `<span class="yhat-oos-fill neg" style="width:${pct.toFixed(
              1
            )}%"></span><span class="yhat-oos-pos-pad"></span>`;
      return (
        `<div class="yhat-oos-row${pass}" title="${esc(
          `${r.label} · ΔOOS ${sign}${r.delta.toFixed(2)}pp`
        )}">` +
        `<span class="yhat-oos-name">${esc(r.label)}</span>` +
        `<span class="yhat-oos-track">${stem}</span>` +
        `<span class="yhat-oos-val ${side}">${esc(
          `${sign}${r.delta.toFixed(2)}`
        )}</span>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="yhat-insight-card yhat-oos-delta" aria-label="跨组 OOS Δ">` +
    `<div class="yhat-insight-head">` +
    `<span class="yhat-insight-title">增益 · ΔOOS (pp)</span>` +
    `<span class="yhat-insight-meta">研究臂 ŷ − heuristic · 过门加亮</span>` +
    `</div>` +
    `<p class="yhat-insight-hint">正=组 β→ŷ 优于人工基线；过门≠自动 promote</p>` +
    `<div class="yhat-oos-axis"><span>−</span><span>0</span><span>+</span></div>` +
    body +
    `</div>`
  );
}

/**
 * 组 β 符号条（lollipop / diverging）。
 * coefs: { factor: beta } 或 [{ key, label?, beta }]
 * Pro 升级：因子分类色方点 + t-stat 星标 + IC 侧色点 + 可展开"…另 X 因子"
 */
export function buildBetaLollipopHtml(
  coefs,
  { escapeHtml, maxRows = 10, highlightAbs = 0.05, factorMeta = {}, groupPanel = null } = {}
) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  // 因子分类颜色表
  const CATEGORY_COLORS = {
    value: "#92400e",   // 褐
    quality: "#047857", // 绿
    momentum: "#0f766e",// 青
    volatility: "#ea580c",// 橙
    volatility_vol: "#ea580c",
    liquid: "#2563eb",  // 蓝
    liquidity: "#2563eb",
    growth: "#db2777",  // 粉
    technical: "#64748b",// 灰
    tech: "#64748b",
    sentiment: "#be185d",
    fundamental: "#0369a1",
    industry: "#b45309",
    sector: "#b45309",
    style: "#0f766e",
    size: "#64748b",
    default: "#94a3b8",
  };
  const factorIcMap = {};
  if (groupPanel && Array.isArray(groupPanel.rows)) {
    for (const r of groupPanel.rows) {
      const k = r.factor || r.name || "";
      if (!k) continue;
      const pear = r.pearson && typeof r.pearson === "object" ? r.pearson : r;
      const icMean = pear.ic_mean != null ? Number(pear.ic_mean)
        : r.ic_mean != null ? Number(r.ic_mean)
        : r.ic != null ? Number(r.ic) : null;
      const icir = pear.icir != null ? Number(pear.icir)
        : r.icir != null ? Number(r.icir) : null;
      factorIcMap[k] = { icMean, icir };
    }
  }
  const getFactorMeta = (k) => {
    const m = factorMeta && factorMeta[k] && typeof factorMeta[k] === "object" ? factorMeta[k] : null;
    return m || {};
  };
  const getCategoryColor = (k) => {
    const m = getFactorMeta(k);
    const c = String(m.category || m.cat || "").toLowerCase();
    if (c && CATEGORY_COLORS[c]) return CATEGORY_COLORS[c];
    // 关键词 fuzzy
    const kl = k.toLowerCase();
    if (/value|bp|pe|pb|dividend|yield/.test(kl)) return CATEGORY_COLORS.value;
    if (/quality|roe|roa|gross|margin|accrual|profit/.test(kl)) return CATEGORY_COLORS.quality;
    if (/momentum|ret|ret_|momen|rsi|macd|ma|trend/.test(kl)) return CATEGORY_COLORS.momentum;
    if (/vol|volatility|iv|var|downside|std|risk/.test(kl)) return CATEGORY_COLORS.volatility;
    if (/liquid|turnover|atv|volume|amt|illiq|amihud/.test(kl)) return CATEGORY_COLORS.liquid;
    if (/growth|grow|revenue|earn|sales|capex/.test(kl)) return CATEGORY_COLORS.growth;
    if (/tech|technical|kdj|bias|breakout|reverse|willr|cci/.test(kl)) return CATEGORY_COLORS.technical;
    return CATEGORY_COLORS.default;
  };
  const getTStatStars = (k, beta) => {
    const m = getFactorMeta(k);
    const tv =
      m.t_stat != null ? Number(m.t_stat)
      : m.tStat != null ? Number(m.tStat)
      : m.t != null ? Number(m.t)
      : m.t_value != null ? Number(m.t_value) : null;
    if (tv != null && Number.isFinite(tv)) {
      const a = Math.abs(tv);
      if (a >= 3.09) return { stars: "***", t: tv };
      if (a >= 1.96) return { stars: "**", t: tv };
      if (a >= 1.65) return { stars: "†", t: tv };
      return { stars: "", t: tv };
    }
    // 若无 t，用 |β| 粗估占位：≥highlightAbs 加弱星
    if (Math.abs(beta) >= highlightAbs * 1.5) return { stars: "·" };
    return { stars: "" };
  };
  const getIcSideColor = (k) => {
    const info = factorIcMap[k];
    if (!info || info.icMean == null || !Number.isFinite(info.icMean)) {
      // 回退查 meta
      const m = getFactorMeta(k);
      const fallback =
        m.ic_mean != null ? Number(m.ic_mean)
        : m.ic != null ? Number(m.ic) : null;
      if (fallback == null || !Number.isFinite(fallback)) return null;
      return _icTint(fallback);
    }
    return _icTint(info.icMean);
  };
  function _icTint(icMean) {
    const a = Math.min(1, Math.max(0, Math.abs(icMean) * 20)); // scale: 0.05=1.0
    if (icMean >= 0) {
      // 红
      const alpha = 0.25 + a * 0.75;
      return `rgba(185,28,28,${alpha.toFixed(2)})`;
    }
    const alpha = 0.25 + a * 0.75;
    return `rgba(4,120,87,${alpha.toFixed(2)})`;
  }
  const getCatLabel = (k) => {
    const m = getFactorMeta(k);
    return m.label || m.cn_name || m.name_cn || "";
  };

  let rows = [];
  if (Array.isArray(coefs)) {
    rows = coefs
      .map((t) => ({
        key: t.key || t.factor || "",
        label: t.label || t.key || t.factor || "—",
        beta: Number(t.beta != null ? t.beta : t.value),
      }))
      .filter((t) => t.key && Number.isFinite(t.beta));
  } else if (coefs && typeof coefs === "object") {
    rows = Object.keys(coefs)
      .map((k) => ({
        key: k,
        label: k,
        beta: Number(coefs[k]),
      }))
      .filter((t) => Number.isFinite(t.beta));
  }
  if (!rows.length) return "";
  rows.sort((a, b) => Math.abs(b.beta) - Math.abs(a.beta));
  const totalRows = rows.length;
  rows = rows.slice(0, Math.max(3, maxRows));
  const peak = Math.max(...rows.map((r) => Math.abs(r.beta)), 1e-6);
  const body = rows
    .map((r) => {
      const pct = Math.min(100, (Math.abs(r.beta) / peak) * 100);
      const side = r.beta >= 0 ? "pos" : "neg";
      const hi = Math.abs(r.beta) >= highlightAbs ? " is-hot" : "";
      const sign = r.beta > 0 ? "+" : "";
      const catColor = getCategoryColor(r.key);
      const catLabel = getCatLabel(r.key);
      const tInfo = getTStatStars(r.key, r.beta);
      const icBg = getIcSideColor(r.key);
      const stem =
        side === "pos"
          ? `<span class="yhat-beta-neg-pad"></span>` +
            `<span class="yhat-beta-track pos"><span class="yhat-beta-bar" style="width:${pct.toFixed(
              1
            )}%; background:${catColor}"></span>${tInfo.stars ? `<span class="yhat-beta-tstars" title="${tInfo.t != null ? `t = ${tInfo.t.toFixed(2)}` : "估计显著"}">${tInfo.stars}</span>` : ""}</span>`
          : `<span class="yhat-beta-track neg">${tInfo.stars ? `<span class="yhat-beta-tstars is-left" title="${tInfo.t != null ? `t = ${tInfo.t.toFixed(2)}` : "估计显著"}">${tInfo.stars}</span>` : ""}<span class="yhat-beta-bar" style="width:${pct.toFixed(
              1
            )}%; background:${catColor}"></span></span>` +
            `<span class="yhat-beta-pos-pad"></span>`;
      const icDot = icBg
        ? `<span class="yhat-beta-icdot" style="background:${icBg}" title="因子自身 IC 质量 · ${catLabel ? catLabel + " · " : ""}${r.key}"></span>`
        : `<span class="yhat-beta-icdot is-empty" title="暂无 IC 信息 · ${r.key}"></span>`;
      const title =
        `${r.key}${catLabel ? `（${catLabel}）` : ""} · β=${sign}${r.beta.toFixed(3)}` +
        (tInfo.t != null && Number.isFinite(tInfo.t) ? ` · t=${tInfo.t.toFixed(2)}${tInfo.stars ? ` ${tInfo.stars}` : ""}` : "") +
        (factorIcMap[r.key] && factorIcMap[r.key].icMean != null ? ` · IC=${factorIcMap[r.key].icMean.toFixed(3)}` : "");
      return (
        `<div class="yhat-beta-row${hi}" title="${esc(title)}">` +
        `<span class="yhat-beta-name">` +
        `<span class="yhat-beta-catdot" style="background:${catColor}" title="${esc(catLabel || `分类推断 · ${r.key}`)}"></span>` +
        `<span class="yhat-beta-fname">${esc(r.label)}</span>` +
        `</span>` +
        `<span class="yhat-beta-stem" aria-hidden="true">${stem}</span>` +
        `<span class="yhat-beta-valcol">` +
        `<span class="yhat-beta-val ${side}">${esc(
          `${sign}${r.beta.toFixed(3)}`
        )}</span>` +
        icDot +
        `</span>` +
        `</div>`
      );
    })
    .join("");

  const more = totalRows > rows.length
    ? `<button type="button" class="yhat-beta-more" data-beta-expand>…另 ${totalRows - rows.length} 因子 ▾</button>`
    : "";

  return {
    __betaLollipop: true,
    totalRows,
    shownRows: rows.length,
    html:
      `<div class="yhat-beta-lollipop yhat-beta-lollipop--pro" aria-label="组因子 β">` +
      `<div class="yhat-beta-axis"><span>−β</span><span>0</span><span>+β</span></div>` +
      body +
      more +
      `</div>`,
  };
}

/** 兼容性包装：返回字符串 HTML（旧调用方）。 */
export function buildBetaLollipopHtmlCompat(coefs, opts = {}) {
  const res = buildBetaLollipopHtml(coefs, opts);
  if (res && res.__betaLollipop) return res.html;
  return res || "";
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
  const muted = cssVar(canvas, "--muted", "#64748b");
  const accent = cssVar(canvas, "--accent", "#2563eb");
  const pad = { t: 8, r: 44, b: 16, l: 28 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = muted;
  ctx.font = '10px ui-sans-serif, system-ui, "Segoe UI", sans-serif';
  if (pts.length < 2) {
    ctx.fillText("命中率样本不足", pad.l, pad.t + 12);
    return { n: pts.length };
  }
  const yAt = (v) => pad.t + h - Math.max(0, Math.min(1, v)) * h;
  const xAt = (i) => pad.l + (i / (pts.length - 1)) * w;
  const mean = pts.reduce((s, p) => s + p.v, 0) / pts.length;

  // band above 50%
  ctx.fillStyle = "rgba(4, 120, 87, 0.06)";
  ctx.fillRect(pad.l, pad.t, w, yAt(0.5) - pad.t);

  // 0.5 / mean guides
  ctx.strokeStyle = "rgba(100, 116, 139, 0.45)";
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(pad.l, yAt(0.5));
  ctx.lineTo(pad.l + w, yAt(0.5));
  ctx.stroke();
  ctx.strokeStyle = "rgba(124, 58, 237, 0.55)";
  ctx.beginPath();
  ctx.moveTo(pad.l, yAt(mean));
  ctx.lineTo(pad.l + w, yAt(mean));
  ctx.stroke();
  ctx.setLineDash([]);

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
  ctx.fillStyle = "rgba(37, 99, 235, 0.1)";
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

/* ============================ 跨组对照矩阵 ============================ */

/**
 * 好坏色背景：t∈[-1,1]，正=绿（好），负=红（差），0=中性。
 * 用 inline style 以适配深浅主题。
 */
function qualityCellBg(t) {
  if (t == null || !Number.isFinite(t)) return "";
  const c = Math.max(-1, Math.min(1, t));
  const a = 0.1 + Math.abs(c) * 0.34;
  if (c >= 0) return `background:rgba(4,120,87,${a.toFixed(3)})`;
  return `background:rgba(185,28,28,${a.toFixed(3)})`;
}

/** 从 cluster 抽取健康指标（与 buildGroupIcInsightHtml 同口径）。 */
function extractClusterHealth(cl) {
  const ols = (cl && cl.ols) || null;
  const rm = (cl && cl.return_model) || null;
  const r2 =
    ols && ols.r_squared != null && Number.isFinite(Number(ols.r_squared))
      ? Number(ols.r_squared)
      : rm && rm.r_squared != null && Number.isFinite(Number(rm.r_squared))
        ? Number(rm.r_squared)
        : null;
  const sampleN =
    ols && ols.sample_count != null
      ? Number(ols.sample_count)
      : rm && rm.sample_count != null
        ? Number(rm.sample_count)
        : cl && cl.member_count != null
          ? Number(cl.member_count)
          : null;

  let icir = null;
  const panel = cl && cl.factor_ic_panel ? cl.factor_ic_panel : null;
  if (panel && panel.score_ic && panel.score_ic.pearson) {
    const v = panel.score_ic.pearson.icir;
    if (v != null && Number.isFinite(Number(v))) icir = Number(v);
  }
  if (icir == null && panel && Array.isArray(panel.rows)) {
    const irs = panel.rows
      .map((r) => Number(r && r.icir))
      .filter((n) => Number.isFinite(n));
    if (irs.length) icir = irs.reduce((s, x) => s + x, 0) / irs.length;
  }

  const gate = (cl && cl.oos_gate) || {};
  const deltaOos =
    !gate.skipped && gate.delta_oos_pp != null && Number.isFinite(Number(gate.delta_oos_pp))
      ? Number(gate.delta_oos_pp)
      : null;
  const passed = !gate.skipped && !!gate.ok && !!gate.passed;
  const skipped = !!gate.skipped;

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

  return { r2, icir, deltaOos, tightScore, meanDist, cap, sampleN, passed, skipped, gate };
}

/** 跨组健康矩阵：行=组，列=R²/ICIR/ΔOOS/同质/n/过门，色阶编码好坏。 */
export function buildClustersHealthMatrixHtml(clusters, { escapeHtml } = {}) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  if (!Array.isArray(clusters) || !clusters.length) return "";

  const rows = clusters.map((cl, idx) => {
    const label = String(
      (cl && cl.label) || `G${((cl && cl.cluster_id) ?? idx) + 1}`
    );
    const h = extractClusterHealth(cl);
    return { label, ...h, singleton: !!(cl && (cl.outlier_singleton || cl.singleton)) };
  });

  // 列阈值：t∈[-1,1] 好坏归一
  const tR2 = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v - 0.3) / 0.4)));
  const tIcir = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 0.5)));
  const tDelta = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) / 1)));
  const tTight = (v) => (v == null ? null : Math.max(-1, Math.min(1, (v || 0) * 2 - 1)));

  const f2 = (x) => (x == null ? "—" : Number(x).toFixed(2));
  const f3 = (x) => (x == null ? "—" : Number(x).toFixed(3));

  const headCells = ["组", "R²", "ICIR", "ΔOOS", "同质", "n", "门"]
    .map((c, i) => {
      const tip = ["组标签", "组 OLS R²；≥0.7 绿", "组 IC 信息比；≥0.5 绿", "ŷ−heuristic 增益(pp)", "1−均β距/cap；越高越同质", "组样本数", "OOS 门禁"][i];
      return `<th class="yhat-mx-th" title="${esc(tip)}">${esc(c)}</th>`;
    })
    .join("");

  const bodyRows = rows
    .map((r) => {
      const nameCls = r.singleton ? " is-singleton" : "";
      const gateCell = r.skipped
        ? `<td class="yhat-mx-td is-skip" title="${esc(r.gate.reason || "skipped")}">跳</td>`
        : `<td class="yhat-mx-td ${r.passed ? "is-pass" : "is-fail"}" title="${esc(r.gate.reason || (r.passed ? "通过" : "未过"))}">${r.passed ? "✓" : "✗"}</td>`;
      const cells =
        `<td class="yhat-mx-td${nameCls}" title="${esc(r.label)}">${esc(r.label)}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tR2(r.r2))}" title="R² ${f3(r.r2)}">${esc(f2(r.r2))}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tIcir(r.icir))}" title="ICIR ${f3(r.icir)}">${esc(f2(r.icir))}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tDelta(r.deltaOos))}" title="ΔOOS ${r.deltaOos != null ? (r.deltaOos > 0 ? "+" : "") + r.deltaOos.toFixed(2) + "pp" : "—"}">${r.deltaOos == null ? "—" : esc((r.deltaOos > 0 ? "+" : "") + r.deltaOos.toFixed(2))}</td>` +
        `<td class="yhat-mx-td num" style="${qualityCellBg(tTight(r.tightScore))}" title="均β距 ${f3(r.meanDist)}${r.cap != null ? " / cap " + f3(r.cap) : ""}">${r.tightScore == null ? (r.meanDist != null ? esc(f3(r.meanDist)) : "—") : esc(r.tightScore.toFixed(2))}</td>` +
        `<td class="yhat-mx-td num" title="样本 n">${r.sampleN == null ? "—" : esc(String(r.sampleN))}</td>` +
        gateCell;
      return `<tr class="yhat-mx-row">${cells}</tr>`;
    })
    .join("");

  const passN = rows.filter((r) => r.passed).length;
  const skipN = rows.filter((r) => r.skipped).length;
  const scoredN = rows.length - skipN;

  return (
    `<details class="yhat-mx yhat-mx-health" open>` +
    `<summary class="yhat-mx-summary">` +
    `<span class="yhat-mx-title">跨组健康矩阵</span>` +
    `<span class="yhat-mx-meta">过门 ${passN}/${scoredN}${skipN ? ` · 跳过 ${skipN}` : ""} · 色阶=好坏</span>` +
    `</summary>` +
    `<p class="yhat-insight-hint">一眼判断哪组值得 promote · 行=组 · 列=R²/ICIR/ΔOOS/同质/n/门 · 悬停看明细</p>` +
    `<div class="yhat-mx-scroll"><table class="yhat-mx-table"><thead><tr>${headCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>` +
    `</details>`
  );
}

/** 因子 β 方向色：正=红（A 股涨），负=绿，0=灰。 */
function betaCellBg(beta, peak) {
  if (beta == null || !Number.isFinite(beta) || !peak) return "";
  const t = Math.max(-1, Math.min(1, beta / peak));
  const a = 0.08 + Math.abs(t) * 0.4;
  if (t >= 0) return `background:rgba(185,28,28,${a.toFixed(3)})`;
  return `background:rgba(4,120,87,${a.toFixed(3)})`;
}

/**
 * 因子 β 跨组矩阵：行=因子，列=组，色阶=β 方向（红正绿负）。
 * 同因子跨组符号反转 → 单元格加 is-flip 边框（全局不稳定因子）。
 */
export function buildFactorBetaMatrixHtml(
  clusters,
  { escapeHtml, maxFactors = 12 } = {}
) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
  if (!Array.isArray(clusters) || !clusters.length) return "";

  const groups = clusters.map((cl, idx) => {
    const label = String(
      (cl && cl.label) || `G${((cl && cl.cluster_id) ?? idx) + 1}`
    );
    const rm = (cl && cl.return_model) || null;
    const ols = (cl && cl.ols) || null;
    const coefs =
      (rm && rm.coefficients && typeof rm.coefficients === "object" && rm.coefficients) ||
      (ols && ols.coefficients && typeof ols.coefficients === "object" && ols.coefficients) ||
      {};
    return { label, coefs };
  });

  // 因子并集，按跨组最大 |β| 排序
  const factorMap = new Map();
  for (const g of groups) {
    for (const [k, v] of Object.entries(g.coefs || {})) {
      const b = Number(v);
      if (!Number.isFinite(b)) continue;
      if (!factorMap.has(k)) factorMap.set(k, []);
      factorMap.get(k).push(b);
    }
  }
  if (!factorMap.size) return "";

  const factorRows = Array.from(factorMap.entries())
    .map(([name, betas]) => ({
      name,
      betas,
      maxAbs: Math.max(...betas.map((b) => Math.abs(b)), 0),
      hasFlip: betas.some((b) => b > 0) && betas.some((b) => b < 0),
    }))
    .sort((a, b) => b.maxAbs - a.maxAbs)
    .slice(0, Math.max(3, maxFactors));

  if (!factorRows.length) return "";

  const peak = Math.max(...factorRows.map((r) => r.maxAbs), 1e-6);

  const headCells =
    `<th class="yhat-mx-th yhat-mx-th-factor" title="因子名">因子</th>` +
    groups
      .map(
        (g) => `<th class="yhat-mx-th" title="${esc(g.label)}">${esc(g.label)}</th>`
      )
      .join("");

  const bodyRows = factorRows
    .map((r) => {
      const flipCls = r.hasFlip ? " is-flip-row" : "";
      const cells = groups
        .map((g) => {
          const b = g.coefs[r.name];
          const v = b != null && Number.isFinite(Number(b)) ? Number(b) : null;
          const flipCell = r.hasFlip && v != null ? " is-flip" : "";
          const title = `${r.name} @ ${g.label}: ${v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(3)}`;
          const txt = v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2);
          return `<td class="yhat-mx-td num${flipCell}" style="${betaCellBg(v, peak)}" title="${esc(title)}">${esc(txt)}</td>`;
        })
        .join("");
      return (
        `<tr class="yhat-mx-row${flipCls}">` +
        `<td class="yhat-mx-td yhat-mx-td-factor${r.hasFlip ? " is-flip" : ""}" title="${esc(r.name)}${r.hasFlip ? " · 跨组符号反转" : ""}">${esc(r.name)}</td>` +
        cells +
        `</tr>`
      );
    })
    .join("");

  const flipN = factorRows.filter((r) => r.hasFlip).length;

  return (
    `<details class="yhat-mx yhat-mx-beta">` +
    `<summary class="yhat-mx-summary">` +
    `<span class="yhat-mx-title">因子 β 跨组矩阵</span>` +
    `<span class="yhat-mx-meta">Top ${factorRows.length} · 符号反转 ${flipN} · 红正绿负</span>` +
    `</summary>` +
    `<p class="yhat-insight-hint">行=因子 · 列=组 · 红正绿负(A股) · 边框高亮=跨组符号反转(全局不稳定因子)</p>` +
    `<div class="yhat-mx-scroll"><table class="yhat-mx-table"><thead><tr>${headCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>` +
    `</details>`
  );
}
