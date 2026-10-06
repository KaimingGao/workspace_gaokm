/** Paper chart drawing helpers. */

export function fmtAxisX(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[2]}-${m[3]}`;
  const t = Date.parse(s);
  if (!Number.isNaN(t)) {
    const d = new Date(t);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${mm}-${dd}`;
  }
  return s.length > 10 ? s.slice(5, 10) : s;
}

export function fmtAxisY(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e4).toFixed(0)}万`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  if (abs >= 100) return String(Math.round(n));
  if (abs >= 10) return String(Math.round(n * 10) / 10);
  return String(Math.round(n * 100) / 100);
}

function _pad(n, w = 2) {
  return String(n).padStart(w, "0");
}

function localIsoMs(d = new Date()) {
  return (
    `${d.getFullYear()}-${_pad(d.getMonth() + 1)}-${_pad(d.getDate())}` +
    `T${_pad(d.getHours())}:${_pad(d.getMinutes())}:${_pad(d.getSeconds())}` +
    `.${_pad(d.getMilliseconds(), 3)}`
  );
}

function bumpIsoMs(ts) {
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return localIsoMs();
  return localIsoMs(new Date(t + 1));
}

/**
 * 曲线历史点来自成交/调仓快照；盘中报价会变。
 * 若现价盯市净值与末点差一截，叠一个只读「现在」点，与摘要总净值对齐。
 */
export function appendLiveNavPoint(points, liveEquity, nowTs) {
  const pts = Array.isArray(points) ? points.slice() : [];
  const eq = Number(liveEquity);
  if (!Number.isFinite(eq)) return pts;
  const last = pts.length ? pts[pts.length - 1] : null;
  if (
    last &&
    Number.isFinite(Number(last.value)) &&
    Math.abs(Number(last.value) - eq) <= 0.01
  ) {
    return pts;
  }
  let time = nowTs || localIsoMs();
  if (last && last.time != null && String(last.time) >= String(time)) {
    time = bumpIsoMs(String(last.time));
  }
  pts.push({ time, value: eq, live: true });
  return pts;
}

export function niceTicks(minV, maxV, count = 4) {
  let lo = Number(minV);
  let hi = Number(maxV);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (hi === lo) {
    const pad = Math.abs(hi) * 0.02 || 1;
    lo -= pad;
    hi += pad;
  }
  const span = hi - lo;
  const rough = span / Math.max(1, count - 1);
  const pow = Math.pow(10, Math.floor(Math.log10(rough || 1)));
  const candidates = [1, 2, 2.5, 5, 10].map((m) => m * pow);
  let step = candidates[0];
  for (const c of candidates) {
    if (rough <= c) {
      step = c;
      break;
    }
    step = c;
  }
  const niceMin = Math.floor(lo / step) * step;
  const niceMax = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = niceMin; v <= niceMax + step * 0.5; v += step) {
    ticks.push(Math.round(v * 1e6) / 1e6);
    if (ticks.length > 8) break;
  }
  return ticks.length >= 2 ? ticks : [lo, hi];
}

export function drawSeries(canvas, pts, { emptyHint, costLine } = {}) {
  if (!canvas) return;
  const g = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 360;
  const h = canvas.clientHeight || 180;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);

  const series = (pts || []).filter((p) => Number.isFinite(p.y));
  if (series.length < 2) {
    g.fillStyle = "#8b95a5";
    g.font = "12px Manrope, sans-serif";
    g.fillText(emptyHint || "暂无足够数据", 14, h / 2);
    return;
  }

  const ys = series.map((p) => p.y);
  const cost =
    costLine != null && Number.isFinite(Number(costLine)) && Number(costLine) > 0
      ? Number(costLine)
      : null;
  const rawMin = Math.min(...ys, ...(cost != null ? [cost] : []));
  const rawMax = Math.max(...ys, ...(cost != null ? [cost] : []));
  const padY = (rawMax - rawMin) * 0.08 || Math.abs(rawMax) * 0.02 || 1;
  const ticks = niceTicks(rawMin - padY, rawMax + padY, 4);
  const minY = ticks[0];
  const maxY = ticks[ticks.length - 1];
  const range = maxY - minY || 1;

  const padL = 46;
  const padR = 12;
  const padT = 14;
  const padB = 24;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const xAt = (i) => padL + (i / (series.length - 1)) * plotW;
  const yAt = (v) => padT + plotH - ((v - minY) / range) * plotH;

  // grid + Y labels
  g.font = "10px Manrope, ui-monospace, monospace";
  ticks.forEach((tv, ti) => {
    const y = yAt(tv);
    g.strokeStyle = ti === 0 ? "#e2e8f0" : "#f1f5f9";
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(padL, y);
    g.lineTo(w - padR, y);
    g.stroke();
    g.fillStyle = "#8b95a5";
    g.textAlign = "right";
    g.textBaseline = "middle";
    g.fillText(fmtAxisY(tv), padL - 6, y);
  });

  // X labels: start / mid / end
  const xIdx = [0, Math.floor((series.length - 1) / 2), series.length - 1];
  const seenX = new Set();
  g.fillStyle = "#8b95a5";
  g.textBaseline = "top";
  xIdx.forEach((i, k) => {
    const label = fmtAxisX(series[i].x);
    if (!label || seenX.has(label)) return;
    seenX.add(label);
    const x = xAt(i);
    g.textAlign = k === 0 ? "left" : k === 2 ? "right" : "center";
    g.fillText(label, x, h - padB + 6);
  });

  // axes frame
  g.strokeStyle = "#e2e8f0";
  g.lineWidth = 1;
  g.beginPath();
  g.moveTo(padL, padT);
  g.lineTo(padL, padT + plotH);
  g.lineTo(w - padR, padT + plotH);
  g.stroke();

  // area under curve
  const grad = g.createLinearGradient(0, padT, 0, padT + plotH);
  grad.addColorStop(0, "rgba(37, 99, 235, 0.18)");
  grad.addColorStop(1, "rgba(37, 99, 235, 0.01)");
  g.beginPath();
  series.forEach((p, i) => {
    const x = xAt(i);
    const y = yAt(p.y);
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  });
  g.lineTo(xAt(series.length - 1), padT + plotH);
  g.lineTo(xAt(0), padT + plotH);
  g.closePath();
  g.fillStyle = grad;
  g.fill();

  // cost line
  if (cost != null) {
    const cy = yAt(cost);
    g.strokeStyle = "#f59e0b";
    g.lineWidth = 1.25;
    g.setLineDash([5, 4]);
    g.beginPath();
    g.moveTo(padL, cy);
    g.lineTo(w - padR, cy);
    g.stroke();
    g.setLineDash([]);
    g.fillStyle = "#d97706";
    g.font = "10px Manrope, sans-serif";
    g.textAlign = "left";
    g.textBaseline = cy < padT + 14 ? "top" : "bottom";
    g.fillText(`成本 ${fmtAxisY(cost)}`, padL + 4, cy < padT + 14 ? cy + 2 : cy - 2);
  }

  // main line
  g.strokeStyle = "#2563eb";
  g.lineWidth = 2.25;
  g.lineJoin = "round";
  g.lineCap = "round";
  g.beginPath();
  series.forEach((p, i) => {
    const x = xAt(i);
    const y = yAt(p.y);
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  });
  g.stroke();

  // end marker + last value
  const last = series[series.length - 1];
  const lx = xAt(series.length - 1);
  const ly = yAt(last.y);
  g.fillStyle = "#2563eb";
  g.beginPath();
  g.arc(lx, ly, 3.2, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#1d4ed8";
  g.font = "600 11px Manrope, sans-serif";
  g.textAlign = "right";
  g.textBaseline = ly < padT + 16 ? "top" : "bottom";
  g.fillText(fmtAxisY(last.y), Math.min(lx - 6, w - padR), ly < padT + 16 ? ly + 4 : ly - 4);
}
