/** tail_anomaly 分钟尾盘迷你图（Score tooltip 懒加载 + 点击放大）。 */

let _tailModalEl = null;

function cssToken(name, fallback) {
  const root = document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

export function paintTailMinuteChart(canvas, payload) {
  if (!canvas || !payload) return;
  const day = Array.isArray(payload.day_bars) ? payload.day_bars : [];
  const tail = Array.isArray(payload.tail_bars) ? payload.tail_bars : [];
  const bars = day.length >= 2 ? day : tail;
  if (bars.length < 2) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  const padL = 4;
  const padR = 4;
  const padT = 4;
  const volH = Math.floor(h * 0.28);
  const priceH = h - volH - padT - 2;

  const closes = bars.map((b) => Number(b.close)).filter((v) => Number.isFinite(v));
  const vols = bars.map((b) => Number(b.volume)).filter((v) => Number.isFinite(v) && v >= 0);
  if (closes.length < 2) return;

  const cMin = Math.min(...closes);
  const cMax = Math.max(...closes);
  const cRange = cMax - cMin || 1;
  const vMax = vols.length ? Math.max(...vols) : 1;
  const step = (w - padL - padR) / (bars.length - 1);

  const tailStart =
    tail.length && day.length
      ? Math.max(0, day.length - tail.length)
      : Math.max(0, bars.length - tail.length);

  ctx.clearRect(0, 0, w, h);

  if (tailStart > 0 && tailStart < bars.length) {
    const x0 = padL + tailStart * step;
    ctx.fillStyle = "rgba(217, 119, 6, 0.08)";
    ctx.fillRect(x0, padT, w - padR - x0, h - 2);
  }

  ctx.fillStyle = cssToken("--ink-3", "#94a3b8");
  bars.forEach((b, i) => {
    const v = Number(b.volume);
    if (!Number.isFinite(v) || v <= 0) return;
    const x = padL + i * step;
    const bh = (v / vMax) * (volH - 2);
    ctx.fillRect(x - 1.5, h - bh, 3, bh);
  });

  ctx.beginPath();
  ctx.strokeStyle = cssToken("--accent", "#1890ff");
  ctx.lineWidth = 1.5;
  bars.forEach((b, i) => {
    const c = Number(b.close);
    if (!Number.isFinite(c)) return;
    const x = padL + i * step;
    const y = padT + priceH - ((c - cMin) / cRange) * (priceH - 4);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  if (tailStart >= 0 && tailStart < bars.length) {
    ctx.beginPath();
    ctx.strokeStyle = cssToken("--warn", "#d97706");
    ctx.lineWidth = 2;
    for (let i = tailStart; i < bars.length; i++) {
      const c = Number(bars[i].close);
      if (!Number.isFinite(c)) continue;
      const x = padL + i * step;
      const y = padT + priceH - ((c - cMin) / cRange) * (priceH - 4);
      if (i === tailStart) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
}

function ensureTailModal() {
  if (_tailModalEl) return _tailModalEl;
  const el = document.createElement("div");
  el.className = "score-tail-modal";
  el.hidden = true;
  el.innerHTML =
    `<div class="score-tail-modal-backdrop" data-tail-modal-close></div>` +
    `<div class="score-tail-modal-panel" role="dialog" aria-modal="true" aria-label="尾盘分钟线">` +
    `<div class="score-tail-modal-head">` +
    `<span class="score-tail-modal-title">尾盘分钟线</span>` +
    `<button type="button" class="dialog-btn secondary score-tail-modal-close" data-tail-modal-close>关闭</button>` +
    `</div>` +
    `<canvas class="score-tail-modal-canvas" width="640" height="220"></canvas>` +
    `<p class="score-tail-modal-meta"></p>` +
    `</div>`;
  document.body.appendChild(el);
  el.addEventListener("click", (e) => {
    if (e.target.closest("[data-tail-modal-close]")) closeTailModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && _tailModalEl && !_tailModalEl.hidden) closeTailModal();
  });
  _tailModalEl = el;
  return el;
}

export function closeTailModal() {
  if (_tailModalEl) _tailModalEl.hidden = true;
}

export async function openTailChartModal(code, { apiFetch, payload: cached } = {}) {
  const c = String(code || "").trim();
  if (!c) return;
  const modal = ensureTailModal();
  const canvas = modal.querySelector(".score-tail-modal-canvas");
  const meta = modal.querySelector(".score-tail-modal-meta");
  const title = modal.querySelector(".score-tail-modal-title");
  if (title) title.textContent = `尾盘分钟线 · ${c}`;
  if (meta) meta.textContent = "加载分钟线…";
  modal.hidden = false;

  const fetchFn =
    apiFetch ||
    (async (url) => {
      const res = await fetch(url);
      const data = await res.json();
      return { ok: res.ok, data };
    });

  let payload = cached;
  if (!payload || payload.ok === false) {
    try {
      const { ok, data } = await fetchFn(
        `/api/watching/minute-tail?code=${encodeURIComponent(c)}&tail_minutes=60`
      );
      payload = ok !== false && data ? data : null;
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      return;
    }
  }
  if (!payload || payload.ok === false) {
    if (meta) {
      meta.textContent =
        (payload && (payload.hint || payload.reason)) || "分钟线未缓存 · minute_warmup";
    }
    return;
  }
  paintTailMinuteChart(canvas, payload);
  if (meta) {
    const m = payload.meta || {};
    meta.textContent = [
      m.date_max || "",
      m.tail_minutes != null ? `尾 ${m.tail_minutes} 分` : "",
      payload.tail_bars?.length ? `${payload.tail_bars.length} bar` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
}

export async function hydrateTailAnomalyCharts(root, { apiFetch } = {}) {
  if (!root) return;
  const canvases = root.querySelectorAll("canvas.score-tail-chart[data-tail-code]");
  if (!canvases.length) return;

  const fetchFn =
    apiFetch ||
    (async (url) => {
      const res = await fetch(url);
      const data = await res.json();
      return { ok: res.ok, data };
    });

  await Promise.all(
    Array.from(canvases).map(async (canvas) => {
      if (canvas.dataset.loaded === "1") return;
      canvas.dataset.loaded = "1";
      const code = canvas.dataset.tailCode || "";
      const wrap = canvas.closest(".score-tail-chart-wrap");
      const hint = wrap && wrap.querySelector(".score-tail-chart-hint");
      if (!code) return;
      try {
        const { ok, data } = await fetchFn(
          `/api/watching/minute-tail?code=${encodeURIComponent(code)}`
        );
        const payload = ok !== false && data ? data : null;
        if (!payload || payload.ok === false) {
          if (hint) {
            hint.textContent =
              (payload && (payload.hint || payload.reason)) ||
              "分钟线未缓存 · minute_warmup";
          }
          return;
        }
        paintTailMinuteChart(canvas, payload);
        canvas.dataset.tailPayload = JSON.stringify({
          ok: true,
          day_bars: payload.day_bars,
          tail_bars: payload.tail_bars,
          meta: payload.meta,
        });
        if (hint) {
          hint.textContent = payload.meta?.date_max
            ? ` ${payload.meta.date_max} · 点击放大`
            : "点击放大";
        }
        if (wrap && wrap.dataset.tailExpandWired !== "1") {
          wrap.dataset.tailExpandWired = "1";
          wrap.classList.add("score-tail-chart-expand");
          wrap.title = "点击放大尾盘分钟线";
          wrap.addEventListener("click", (e) => {
            e.stopPropagation();
            let cached = null;
            try {
              cached = JSON.parse(canvas.dataset.tailPayload || "null");
            } catch (_) {
              cached = null;
            }
            openTailChartModal(code, { apiFetch: fetchFn, payload: cached }).catch(() => {});
          });
        }
      } catch (err) {
        if (hint) hint.textContent = String(err.message || err);
      }
    })
  );
}
