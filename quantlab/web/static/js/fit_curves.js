/** Arm·T 运行台曲线。训练画步均损失；Holdout 画 Rank IC，排序头再画 NDCG，回归头再画 MSE。左轴跟第一条线，右轴跟第二条，底轴是 step 或 epoch。 */

function finite(raw) {
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function column(rows, key) {
  return (rows || []).map((row) => finite(row?.[key]));
}

function hasFinite(values) {
  return (values || []).some((n) => n != null);
}

const curveResize = new WeakMap();

function watchBoard(board, draw) {
  if (typeof ResizeObserver === "undefined") return;
  let slot = curveResize.get(board);
  if (!slot) {
    slot = { draw, width: -1 };
    slot.obs = new ResizeObserver(() => {
      const width = Math.round(board.getBoundingClientRect().width);
      if (width < 2 || width === slot.width) return;
      slot.width = width;
      slot.draw();
    });
    slot.obs.observe(board);
    curveResize.set(board, slot);
  }
  slot.draw = draw;
}

function lastFinite(values) {
  for (let i = (values || []).length - 1; i >= 0; i -= 1) {
    if (values[i] != null) return values[i];
  }
  return null;
}

function fmt(n, digits) {
  return n == null ? "" : Number(n).toFixed(digits);
}

function axisNum(n) {
  if (n == null || !Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs !== 0 && (abs >= 10000 || abs < 0.001)) return n.toExponential(1);
  if (abs >= 100) return n.toFixed(0);
  if (abs >= 10) return n.toFixed(1);
  if (abs >= 1) return n.toFixed(2);
  return n.toFixed(3);
}

function axisX(n) {
  if (!Number.isFinite(n)) return "";
  if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
  return axisNum(n);
}

function seriesScale(values) {
  const present = (values || []).filter((n) => n != null);
  if (!present.length) return null;
  let lo = Math.min(...present);
  let hi = Math.max(...present);
  if (lo === hi) {
    const pad = Math.abs(lo) > 1e-6 ? Math.abs(lo) * 0.08 : 1;
    lo -= pad;
    hi += pad;
  } else {
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
  }
  return { lo, hi, span: hi - lo || 1 };
}

function paintCanvas(canvas, series, xs) {
  if (!canvas) return;
  const cssH = 128;
  const cssW = Math.round(canvas.clientWidth || canvas.parentElement?.clientWidth || 0);
  if (cssW < 2) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.height = `${cssH}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.font = "10px ui-sans-serif, system-ui, sans-serif";

  const drawn = series
    .map((item) => ({ ...item, scale: seriesScale(item.values) }))
    .filter((item) => item.scale);
  if (!drawn.length) return;
  const left = drawn[0];
  const right = drawn.length > 1 ? drawn[1] : null;
  const yTicks = (scale) => [scale.hi, (scale.lo + scale.hi) / 2, scale.lo];
  const leftLabels = yTicks(left.scale).map(axisNum);
  const rightLabels = right ? yTicks(right.scale).map(axisNum) : [];
  const padL = Math.ceil(Math.max(28, ...leftLabels.map((text) => ctx.measureText(text).width)) + 8);
  const padR = right
    ? Math.ceil(Math.max(28, ...rightLabels.map((text) => ctx.measureText(text).width)) + 8)
    : 8;
  const padT = 8;
  const padB = 18;
  const innerW = Math.max(1, cssW - padL - padR);
  const innerH = Math.max(1, cssH - padT - padB);
  const yOf = (scale, value) => padT + (1 - (value - scale.lo) / scale.span) * innerH;
  const count = Math.max(left.values.length, right?.values.length || 0, xs?.length || 0);
  const xOf = (index) => padL + (count <= 1 ? innerW / 2 : (index / (count - 1)) * innerW);

  ctx.strokeStyle = "#e3eeeb";
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  yTicks(left.scale).forEach((tick) => {
    const y = yOf(left.scale, tick);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + innerW, y);
    ctx.stroke();
  });
  ctx.strokeStyle = "#9fb8b2";
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, padT + innerH);
  ctx.lineTo(padL + innerW, padT + innerH);
  if (right) {
    ctx.moveTo(padL + innerW, padT);
    ctx.lineTo(padL + innerW, padT + innerH);
  }
  ctx.stroke();

  ctx.textBaseline = "middle";
  yTicks(left.scale).forEach((tick, i) => {
    ctx.fillStyle = "#3d5c56";
    ctx.textAlign = "right";
    ctx.fillText(leftLabels[i], padL - 4, yOf(left.scale, tick));
  });
  if (right) {
    yTicks(right.scale).forEach((tick, i) => {
      ctx.fillStyle = "#047857";
      ctx.textAlign = "left";
      ctx.fillText(rightLabels[i], padL + innerW + 4, yOf(right.scale, tick));
    });
  }

  const xLabels = [];
  for (let index = 0; index < count; index += 1) {
    const raw = xs && index < xs.length ? Number(xs[index]) : index + 1;
    xLabels.push({ index, text: axisX(raw) });
  }
  const shownX =
    xLabels.length <= 4
      ? xLabels
      : [xLabels[0], xLabels[Math.floor((xLabels.length - 1) / 2)], xLabels[xLabels.length - 1]];
  ctx.fillStyle = "#3d5c56";
  ctx.textBaseline = "top";
  shownX.forEach((item, i) => {
    const x = xOf(item.index);
    ctx.textAlign =
      shownX.length === 1 ? "center" : i === 0 ? "left" : i === shownX.length - 1 ? "right" : "center";
    ctx.fillText(item.text, x, padT + innerH + 4);
  });

  for (const item of drawn) {
    const values = item.values || [];
    const denom = Math.max(1, count - 1);
    ctx.beginPath();
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.setLineDash(item.dash || []);
    let moved = false;
    let dots = 0;
    values.forEach((value, index) => {
      if (value == null) {
        moved = false;
        return;
      }
      dots += 1;
      const x = padL + (count <= 1 ? innerW / 2 : (index / denom) * innerW);
      const y = yOf(item.scale, value);
      if (!moved) {
        ctx.moveTo(x, y);
        moved = true;
      } else ctx.lineTo(x, y);
    });
    ctx.stroke();
    if (dots === 1) {
      const index = values.findIndex((value) => value != null);
      const x = padL + (count <= 1 ? innerW / 2 : (index / denom) * innerW);
      ctx.fillStyle = item.color;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(x, yOf(item.scale, values[index]), 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

/** @param {ParentNode|null|undefined} root 含 `[data-fit-curves]` 的运行台。 */
export function paintFitCurves(root, curve) {
  const board = root?.querySelector?.("[data-fit-curves]");
  if (!board) return;
  const steps = Array.isArray(curve?.steps) ? curve.steps : [];
  const epochs = Array.isArray(curve?.epochs) ? curve.epochs : [];
  const avg = column(steps, "avg");
  const rank = column(epochs, "rank_ic");
  const secondKey = epochs.some((row) => finite(row?.ndcg30) != null) ? "ndcg30" : "mse";
  const second = column(epochs, secondKey);
  const showLoss = hasFinite(avg);
  const showHoldout = hasFinite(rank) || hasFinite(second);
  const lossFig = board.querySelector('[data-curve="loss"]')?.closest("figure");
  const holdFig = board.querySelector("[data-curve-holdout]");
  if (lossFig) lossFig.hidden = !showLoss;
  if (holdFig) holdFig.hidden = !showHoldout;
  board.hidden = !(showLoss || showHoldout);
  if (board.hidden) return;
  const lossCap = board.querySelector('[data-curve-caption="loss"]');
  if (lossCap && showLoss) {
    const step = steps.length ? steps[steps.length - 1]?.step : "";
    lossCap.textContent = `avg ${fmt(lastFinite(avg), 4)}${step === "" || step == null ? "" : ` · step ${step}`}`;
  }
  const holdCap = board.querySelector('[data-curve-caption="holdout"]');
  const holdCanvas = board.querySelector('[data-curve="holdout"]');
  if (showHoldout) {
    const bits = [];
    if (hasFinite(rank)) bits.push(`Rank IC ${fmt(lastFinite(rank), 3)}`);
    if (hasFinite(second)) {
      bits.push(
        secondKey === "ndcg30"
          ? `NDCG ${fmt(lastFinite(second), 3)}`
          : `MSE ${fmt(lastFinite(second), 3)}`
      );
    }
    if (holdCap) holdCap.textContent = bits.join(" · ");
    if (holdCanvas) {
      holdCanvas.title =
        secondKey === "ndcg30" ? "蓝线 Rank IC，绿线 NDCG@30" : "蓝线 Rank IC，绿线 MSE";
    }
  }
  const draw = () => {
    paintCanvas(
      board.querySelector('[data-curve="loss"]'),
      [{ color: "#1890ff", values: avg }],
      steps.map((row) => row?.step)
    );
    const holdSeries = [{ color: "#1890ff", values: rank }];
    if (hasFinite(second)) holdSeries.push({ color: "#059669", values: second, dash: [4, 3] });
    paintCanvas(
      holdCanvas,
      holdSeries,
      epochs.map((row) => row?.ep)
    );
  };
  watchBoard(board, draw);
  draw();
}
