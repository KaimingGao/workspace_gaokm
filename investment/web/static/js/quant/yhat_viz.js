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
    ctx.strokeStyle = "rgba(100, 116, 139, 0.12)";
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
    ctx.strokeStyle = "rgba(51, 65, 85, 0.55)";
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

  // IQR band (subtle)
  if (pack.p25 != null && pack.p75 != null && pack.p75 > pack.p25) {
    const x0 = xAt(pack.p25);
    const x1 = xAt(pack.p75);
    ctx.fillStyle = "rgba(24, 144, 255, 0.06)";
    ctx.fillRect(x0, pad.t, Math.max(1, x1 - x0), h);
  }

  const drawMarker = (v, { color, dash, label, labelY }) => {
    if (v == null || !Number.isFinite(Number(v))) return;
    const x = xAt(Number(v));
    if (x < pad.l - 1 || x > pad.l + w + 1) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.25;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + 4);
    ctx.stroke();
    if (label) {
      ctx.fillStyle = color;
      ctx.font = fontMono;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      const lx = Math.min(Math.max(x + 3, pad.l + 2), pad.l + w - 36);
      ctx.fillText(label, lx, labelY != null ? labelY : pad.t + 3);
    }
  };

  drawMarker(pack.mean, {
    color: "#1d4ed8",
    dash: [],
    label: "μ",
    labelY: pad.t + 3,
  });
  drawMarker(pack.median, {
    color: "#7c3aed",
    dash: [4, 3],
    label: "med",
    labelY: pad.t + 14,
  });

  const floors = opts.floors || {};
  drawMarker(floors.buy, {
    color: "#c2410c",
    dash: [5, 3],
    label: "买",
    labelY: pad.t + 3,
  });
  drawMarker(floors.hold, {
    color: "#475569",
    dash: [2, 3],
    label: "持",
    labelY: pad.t + 14,
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
    }))
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || opts.width || 360;
  const cssH = canvas.clientHeight || opts.height || 180;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const pad = { t: 12, r: 12, b: 28, l: 36 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = "#94a3b8";
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  if (pts.length < 2) {
    ctx.fillText("样本不足，无法画散点", pad.l, pad.t + 14);
    return { n: pts.length };
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

  // axes
  ctx.strokeStyle = "rgba(100, 116, 139, 0.35)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, sy(0));
  ctx.lineTo(pad.l + w, sy(0));
  ctx.moveTo(sx(0), pad.t);
  ctx.lineTo(sx(0), pad.t + h);
  ctx.stroke();

  // quadrant tint: correct = same sign
  pts.forEach((p) => {
    const hit =
      p.hit === true || p.hit === false
        ? p.hit
        : Math.sign(p.x) === Math.sign(p.y) && p.x !== 0 && p.y !== 0;
    ctx.fillStyle = hit
      ? "rgba(4, 120, 87, 0.65)"
      : "rgba(185, 28, 28, 0.7)";
    ctx.beginPath();
    ctx.arc(sx(p.x), sy(p.y), 3.2, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#64748b";
  ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("ŷ%", pad.l + w - 18, cssH - 8);
  ctx.save();
  ctx.translate(12, pad.t + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("实现%", 0, 0);
  ctx.restore();
  ctx.fillText(`${pts.length} 点 · 绿=方向对 · 红=错`, pad.l, 10);
  return { n: pts.length, xMin, xMax, yMin, yMax };
}

/** 组内 ŷ 条带（兼容旧调用；现委托迷你直方图）。 */
export function buildGroupYhatStripHtml(scores, opts = {}) {
  return buildGroupYhatHistHtml(scores, opts);
}

/**
 * 组内 ŷ 迷你直方图（分箱密度 · 零轴 · μ/med）。
 * 用于研究枢纽分组组头，纯 HTML/CSS，无需 canvas。
 */
export function buildGroupYhatHistHtml(
  scores,
  { escapeHtml, bins = 12, maxBars = 40 } = {}
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

  const bars = pack.bins
    .map((b) => {
      const h = b.count ? Math.max(8, (b.count / maxC) * 100) : 0;
      const mid = (b.x0 + b.x1) / 2;
      const side = mid >= 0 ? "pos" : "neg";
      const tip = `[${b.x0.toFixed(2)}, ${b.x1.toFixed(2)}) · ${b.count} 只`;
      if (!b.count) {
        return `<span class="yhat-ghist-bar is-empty" title="${esc(tip)}"></span>`;
      }
      return (
        `<span class="yhat-ghist-bar ${side}" style="height:${h.toFixed(0)}%" ` +
        `title="${esc(tip)}"></span>`
      );
    })
    .join("");

  const f = (x, d = 2) =>
    x != null && Number.isFinite(x) ? Number(x).toFixed(d) : "—";
  const markers = [];
  if (pack.min < 0 && pack.max > 0) {
    markers.push(
      `<span class="yhat-ghist-mark is-zero" style="left:${pctAt(0)}" title="ŷ=0"></span>`
    );
  }
  if (pack.mean != null) {
    markers.push(
      `<span class="yhat-ghist-mark is-mu" style="left:${pctAt(pack.mean)}" title="μ ${f(
        pack.mean
      )}%"></span>`
    );
  }
  if (pack.median != null) {
    markers.push(
      `<span class="yhat-ghist-mark is-med" style="left:${pctAt(pack.median)}" title="med ${f(
        pack.median
      )}%"></span>`
    );
  }

  // 兼容旧参数名（不再抽样竖条）
  void maxBars;

  return (
    `<div class="yhat-group-hist is-aux" aria-label="组内 ŷ 直方图（辅）">` +
    `<div class="yhat-ghist-meta">` +
    `<span class="yhat-ghist-caption">辅 · 截面 ŷ</span>` +
    `<span>n=${pack.n}</span>` +
    `<span>μ ${esc(f(pack.mean))}%</span>` +
    `<span>med ${esc(f(pack.median))}%</span>` +
    `<span>σ ${esc(f(pack.std))}</span>` +
    (pack.pct_pos != null
      ? `<span>${esc((pack.pct_pos * 100).toFixed(0))}%&gt;0</span>`
      : "") +
    `</div>` +
    `<div class="yhat-ghist-plot">` +
    `<div class="yhat-ghist-bars">${bars}</div>` +
    `<div class="yhat-ghist-marks">${markers.join("")}</div>` +
    `</div>` +
    `<div class="yhat-ghist-axis">` +
    `<span>${esc(f(pack.data_min != null ? pack.data_min : pack.min, 1))}</span>` +
    `<span>0</span>` +
    `<span>${esc(f(pack.data_max != null ? pack.data_max : pack.max, 1))}</span>` +
    `</div>` +
    `<div class="yhat-ghist-legend"><span class="is-mu">μ</span><span class="is-med">med</span><span class="is-zero">0</span></div>` +
    `</div>`
  );
}

/**
 * 组同质性：成员 → 组 β 距离条（洞察：右尾长=硬并组）。
 * gaps: [{ code, distance_to_group_beta, within_cap? }]
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
  const peak = Math.max(...shown.map((r) => r.d), 1e-6);
  const mean = rows.reduce((s, r) => s + r.d, 0) / rows.length;
  const body = shown
    .map((r) => {
      const pct = Math.min(100, (r.d / peak) * 100);
      const name = nameByCode[r.code] || "";
      const lab = name ? `${name} ${r.code}` : r.code;
      const near =
        r.cap != null && Number.isFinite(r.cap) && r.cap > 0 && r.d >= r.cap * 0.85;
      return (
        `<div class="yhat-bdist-row${near ? " is-near-cap" : ""}" title="${esc(
          `${lab} · d=${r.d.toFixed(3)}`
        )}">` +
        `<span class="yhat-bdist-name">${esc(lab)}</span>` +
        `<span class="yhat-bdist-track"><span class="yhat-bdist-bar" style="width:${pct.toFixed(
          1
        )}%"></span></span>` +
        `<span class="yhat-bdist-val">${esc(r.d.toFixed(3))}</span>` +
        `</div>`
      );
    })
    .join("");
  const more =
    rows.length > shown.length
      ? `<div class="yhat-bdist-more">…另 ${rows.length - shown.length} 只</div>`
      : "";
  return (
    `<div class="yhat-insight-card yhat-bdist" aria-label="组 β 距离">` +
    `<div class="yhat-insight-head">` +
    `<span class="yhat-insight-title">同质 · β 距离</span>` +
    `<span class="yhat-insight-meta">n=${rows.length} · 均 ${mean.toFixed(3)} · 峰 ${peak.toFixed(
      3
    )}</span>` +
    `</div>` +
    `<p class="yhat-insight-hint">到组 β 的 L2；越短越同质 · 近 cap 偏橙</p>` +
    body +
    more +
    `</div>`
  );
}

/**
 * 组预测力：日截面 IC spark + ICIR。
 * panel: factor_ic_panel（含 daily_tail / score_ic / rows）
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
        `<div class="yhat-insight-card yhat-gic" aria-label="组 IC">` +
        `<div class="yhat-insight-head"><span class="yhat-insight-title">有效 · 组 IC</span></div>` +
        `<p class="yhat-insight-hint">单票组：时序 IC 回退，无截面 spark</p>` +
        `</div>`
      );
    }
    return "";
  }

  let spark = "";
  if (pts.length >= 2) {
    const w = 160;
    const h = 36;
    const pad = 2;
    const ics = pts.map((p) => p.ic);
    let lo = Math.min(...ics, 0);
    let hi = Math.max(...ics, 0);
    if (hi <= lo) {
      lo -= 0.05;
      hi += 0.05;
    }
    const xAt = (i) => pad + (i / (pts.length - 1)) * (w - pad * 2);
    const yAt = (v) => pad + (1 - (v - lo) / (hi - lo)) * (h - pad * 2);
    const zeroY = yAt(0);
    const poly = pts
      .map((p, i) => `${xAt(i).toFixed(1)},${yAt(p.ic).toFixed(1)}`)
      .join(" ");
    spark =
      `<svg class="yhat-gic-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` +
      `<line class="yhat-gic-zero" x1="${pad}" y1="${zeroY.toFixed(
        1
      )}" x2="${w - pad}" y2="${zeroY.toFixed(1)}" />` +
      `<polyline class="yhat-gic-line" fill="none" points="${poly}" />` +
      `</svg>`;
  }

  const f = (x, d = 3) =>
    x != null && Number.isFinite(x) ? Number(x).toFixed(d) : "—";
  const tone =
    icir != null && icir > 0.3 ? "is-good" : icir != null && icir < 0 ? "is-bad" : "";
  return (
    `<div class="yhat-insight-card yhat-gic ${tone}" aria-label="组 IC">` +
    `<div class="yhat-insight-head">` +
    `<span class="yhat-insight-title">有效 · 组 IC</span>` +
    `<span class="yhat-insight-meta">μIC ${esc(f(icMean))} · ICIR ${esc(f(icir))} · ${
      pts.length
    }d</span>` +
    `</div>` +
    `<p class="yhat-insight-hint">日截面因子 IC 均 · 绕零=无效 · ICIR&gt;0 才谈效果</p>` +
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
 */
export function buildBetaLollipopHtml(coefs, { escapeHtml, maxRows = 10, highlightAbs = 0.05 } = {}) {
  const esc = escapeHtml || ((s) => String(s ?? ""));
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
  rows = rows.slice(0, Math.max(3, maxRows));
  const peak = Math.max(...rows.map((r) => Math.abs(r.beta)), 1e-6);
  const body = rows
    .map((r) => {
      const pct = Math.min(100, (Math.abs(r.beta) / peak) * 100);
      const side = r.beta >= 0 ? "pos" : "neg";
      const hi = Math.abs(r.beta) >= highlightAbs ? " is-hot" : "";
      const sign = r.beta > 0 ? "+" : "";
      const stem =
        side === "pos"
          ? `<span class="yhat-beta-neg-pad"></span>` +
            `<span class="yhat-beta-track pos"><span class="yhat-beta-bar" style="width:${pct.toFixed(
              1
            )}%"></span></span>`
          : `<span class="yhat-beta-track neg"><span class="yhat-beta-bar" style="width:${pct.toFixed(
              1
            )}%"></span></span>` +
            `<span class="yhat-beta-pos-pad"></span>`;
      return (
        `<div class="yhat-beta-row${hi}" title="${esc(r.key)}">` +
        `<span class="yhat-beta-name">${esc(r.label)}</span>` +
        `<span class="yhat-beta-stem" aria-hidden="true">${stem}</span>` +
        `<span class="yhat-beta-val ${side}">${esc(
          `${sign}${r.beta.toFixed(3)}`
        )}</span>` +
        `</div>`
      );
    })
    .join("");
  return (
    `<div class="yhat-beta-lollipop" aria-label="组因子 β">` +
    `<div class="yhat-beta-axis"><span>−β</span><span>0</span><span>+β</span></div>` +
    body +
    `</div>`
  );
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
  const cssW = canvas.clientWidth || opts.width || 280;
  const cssH = canvas.clientHeight || opts.height || 48;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const pad = { t: 6, r: 6, b: 14, l: 6 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  ctx.fillStyle = "#94a3b8";
  ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
  if (pts.length < 2) {
    ctx.fillText("命中率样本不足", pad.l, pad.t + 12);
    return { n: pts.length };
  }
  const yAt = (v) => pad.t + h - Math.max(0, Math.min(1, v)) * h;
  const xAt = (i) => pad.l + (i / (pts.length - 1)) * w;

  // 0.5 参考
  ctx.strokeStyle = "rgba(100, 116, 139, 0.4)";
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(pad.l, yAt(0.5));
  ctx.lineTo(pad.l + w, yAt(0.5));
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 1.6;
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
    ctx.arc(xAt(i), yAt(p.v), 2.4, 0, Math.PI * 2);
    ctx.fill();
  });
  const last = pts[pts.length - 1];
  ctx.fillStyle = "#64748b";
  ctx.fillText(
    `${pts[0].date.slice(5)}→${last.date.slice(5)} · 近 ${((last.v || 0) * 100).toFixed(0)}%`,
    pad.l,
    cssH - 2
  );
  return { n: pts.length, last: last.v };
}
