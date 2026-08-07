/**
 * Lightweight Charts helpers (W1).
 * 专业挂载：Shadow DOM 隔离页面 CSS，避免任何 `canvas { height }` 污染库内部节点。
 */

const CDN =
  "https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js";

const SHADOW_CSS = `
:host {
  display: block;
  width: 100%;
  box-sizing: border-box;
  /* 高度由 light DOM / JS 指定，勿写 height:100%（会冲掉宿主定高） */
}
.lw-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
}
.lw-legend {
  position: absolute;
  top: 6px;
  left: 8px;
  right: 72px;
  z-index: 3;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  pointer-events: auto;
}
.lw-legend[hidden] {
  display: none !important;
}
.lw-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font: 500 11px Manrope, system-ui, sans-serif;
  color: #6b7280;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(229, 231, 235, 0.95);
  padding: 2px 7px;
  border-radius: 999px;
  line-height: 1.2;
  cursor: pointer;
  user-select: none;
  margin: 0;
  appearance: none;
  -webkit-appearance: none;
}
.lw-legend-item:hover {
  border-color: rgba(37, 99, 235, 0.45);
  color: #374151;
}
.lw-legend-item.is-off {
  opacity: 0.42;
  text-decoration: line-through;
}
.lw-legend-item.is-solo {
  border-color: rgba(37, 99, 235, 0.65);
  color: #111827;
  background: rgba(239, 246, 255, 0.95);
}
.lw-legend-swatch {
  width: 14px;
  height: 2px;
  border-radius: 1px;
  flex: 0 0 auto;
}
.lw-legend-swatch.is-thick {
  height: 3px;
}
#root {
  display: block;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  position: relative;
  overflow: hidden;
}
#root .quant-chart-empty {
  margin: 0;
  padding: 24px 12px;
  text-align: center;
  color: #6b7280;
  font: 500 13px Manrope, system-ui, sans-serif;
}
`;

function maColor(n) {
  if (n === 5) return "#2563eb";
  if (n === 10) return "#7c3aed";
  return "#059669";
}

function ensureLegendEl(container) {
  const root = container.__lwShadow || container.shadowRoot;
  if (!root) return null;
  let legend = root.querySelector(".lw-legend");
  if (legend) return legend;
  const wrap = root.querySelector(".lw-wrap") || root;
  legend = document.createElement("div");
  legend.className = "lw-legend";
  legend.hidden = true;
  wrap.prepend(legend);
  return legend;
}

/** @param {{ label: string, color: string, thick?: boolean, series?: any }[]} items */
function syncLegend(container, items) {
  const legend = ensureLegendEl(container);
  if (!legend) return;
  if (!items || !items.length) {
    legend.hidden = true;
    legend.innerHTML = "";
    container.__lwLegendSeries = null;
    container.__lwLegendSolo = null;
    return;
  }
  legend.hidden = false;
  legend.innerHTML = items
    .map(
      (it, idx) =>
        `<button type="button" class="lw-legend-item" data-legend-idx="${idx}" title="点击只看此线；再点恢复全部">` +
        `<span class="lw-legend-swatch${it.thick ? " is-thick" : ""}" style="background:${it.color}"></span>` +
        `${it.label}</button>`
    )
    .join("");

  const seriesList = items.map((it) => it.series).filter(Boolean);
  container.__lwLegendSeries = seriesList.length === items.length ? seriesList : null;
  container.__lwLegendSolo = null;

  if (!container.__lwLegendSeries || container.__lwLegendSeries.length < 2) {
    // 单线或无 series 绑定：不可切换
    legend.querySelectorAll(".lw-legend-item").forEach((el) => {
      el.style.cursor = "default";
      el.removeAttribute("title");
    });
    return;
  }

  const applySolo = (soloIdx) => {
    const series = container.__lwLegendSeries || [];
    container.__lwLegendSolo = soloIdx;
    series.forEach((s, i) => {
      const visible = soloIdx == null || i === soloIdx;
      try {
        s.applyOptions({ visible });
      } catch (_) {
        /* ignore */
      }
    });
    legend.querySelectorAll(".lw-legend-item").forEach((el) => {
      const i = Number(el.getAttribute("data-legend-idx"));
      el.classList.toggle("is-solo", soloIdx != null && i === soloIdx);
      el.classList.toggle("is-off", soloIdx != null && i !== soloIdx);
    });
  };

  legend.onclick = (e) => {
    const btn = e.target && e.target.closest ? e.target.closest(".lw-legend-item") : null;
    if (!btn || !legend.contains(btn)) return;
    e.preventDefault();
    e.stopPropagation();
    const idx = Number(btn.getAttribute("data-legend-idx"));
    if (!Number.isFinite(idx)) return;
    const cur = container.__lwLegendSolo;
    applySolo(cur === idx ? null : idx);
  };
}

let _loadPromise = null;

function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (_) {
    return fallback;
  }
}

export function loadLightweightCharts() {
  if (window.LightweightCharts) return Promise.resolve(window.LightweightCharts);
  if (_loadPromise) return _loadPromise;
  _loadPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CDN;
    s.async = true;
    s.onload = () => {
      if (window.LightweightCharts) resolve(window.LightweightCharts);
      else reject(new Error("LightweightCharts 未挂载"));
    };
    s.onerror = () => reject(new Error("LightweightCharts 脚本加载失败"));
    document.head.appendChild(s);
  });
  return _loadPromise;
}

function chartTheme() {
  return {
    layout: {
      background: { color: "transparent" },
      textColor: cssVar("--ink-3", "#6b7280"),
      fontFamily: "IBM Plex Mono, Manrope, sans-serif",
      fontSize: 11,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: cssVar("--line", "#e5e7eb") },
      horzLines: { color: cssVar("--line", "#e5e7eb") },
    },
    rightPriceScale: { borderColor: cssVar("--line", "#e5e7eb") },
    timeScale: { borderColor: cssVar("--line", "#e5e7eb") },
    crosshair: { mode: 1 },
  };
}

/** 在宿主内建立 Shadow 挂载点，屏蔽页面 CSS 对 canvas 的干扰 */
function ensureIsolatedMount(container) {
  if (container.__lwMount && container.__lwMount.isConnected) {
    const root = container.__lwShadow || container.shadowRoot;
    if (root && root.querySelector(".lw-wrap")) {
      let style = root.querySelector("style");
      if (!style) {
        style = document.createElement("style");
        root.prepend(style);
      }
      if (style.textContent !== SHADOW_CSS) style.textContent = SHADOW_CSS;
      return container.__lwMount;
    }
    // 旧结构无图例壳：重建
    container.__lwMount = null;
    container.__lwShadow = null;
  }
  container.textContent = "";
  let root;
  if (container.shadowRoot) {
    root = container.shadowRoot;
  } else if (typeof container.attachShadow === "function") {
    root = container.attachShadow({ mode: "open" });
  } else {
    // 极老环境：退化为普通子节点（仍避免改页面 canvas 选择器）
    const wrap = document.createElement("div");
    wrap.className = "lw-wrap";
    wrap.style.cssText = "width:100%;height:100%;position:relative;box-sizing:border-box;";
    const legend = document.createElement("div");
    legend.className = "lw-legend";
    legend.hidden = true;
    const mount = document.createElement("div");
    mount.id = "root";
    mount.className = "lw-chart-mount";
    mount.style.cssText = "width:100%;height:100%;position:relative;box-sizing:border-box;";
    wrap.appendChild(legend);
    wrap.appendChild(mount);
    container.appendChild(wrap);
    container.__lwMount = mount;
    return mount;
  }
  root.innerHTML =
    `<style>${SHADOW_CSS}</style>` +
    `<div class="lw-wrap"><div class="lw-legend" hidden></div><div id="root"></div></div>`;
  const mount = root.getElementById("root");
  container.__lwMount = mount;
  container.__lwShadow = root;
  return mount;
}

function measureHost(container) {
  const r = container.getBoundingClientRect();
  // 优先用 CSS 定高；避免 shadow :host 干扰后 rect 异常
  const cssH = parseFloat(getComputedStyle(container).height) || 0;
  const cssW = parseFloat(getComputedStyle(container).width) || 0;
  return {
    width: Math.max(280, Math.round(cssW || r.width || container.clientWidth || 360)),
    height: Math.max(160, Math.round(cssH || r.height || container.clientHeight || 240)),
  };
}

/**
 * 规范 LWC 时序：支持 YYYY-MM-DD / ISO 日期时间 / unix 秒；
 * 同刻度去重（保留最后一点），并按时间升序。日内多点勿截成同一天。
 */
function normalizeSeriesPoints(points) {
  const raw = (points || [])
    .map((p) => {
      const v = Number(p.value ?? p.close ?? p.equity);
      if (!Number.isFinite(v)) return null;
      let time = p.time ?? p.date ?? p.ts;
      if (typeof time === "number" && Number.isFinite(time)) {
        if (time > 1e12) time = Math.floor(time / 1000);
        else time = Math.floor(time);
        return { time, value: v };
      }
      const s = String(time || "").trim();
      if (!s) return null;
      if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return { time: s, value: v };
      const ms = Date.parse(s);
      if (Number.isFinite(ms)) return { time: Math.floor(ms / 1000), value: v };
      const day = s.slice(0, 10);
      if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return { time: day, value: v };
      return null;
    })
    .filter(Boolean);

  const byTime = new Map();
  for (const p of raw) byTime.set(p.time, p);
  return Array.from(byTime.values()).sort((a, b) =>
    a.time < b.time ? -1 : a.time > b.time ? 1 : 0
  );
}

function applyMountSize(mount, size) {
  if (!mount) return;
  mount.style.width = `${size.width}px`;
  mount.style.height = `${size.height}px`;
}

function clearWheelGuard(container) {
  if (!container || !container.__lwWheelGuard) return;
  const target = container.__lwWheelTarget || container.__lwMount || container;
  try {
    target.removeEventListener("wheel", container.__lwWheelGuard);
  } catch (_) {
    /* ignore */
  }
  container.__lwWheelGuard = null;
  container.__lwWheelTarget = null;
}

/** 可缩放图：拦住宿主上的滚轮默认行为，避免 .page-main 抢走缩放 */
function attachWheelGuard(container, mount) {
  clearWheelGuard(container);
  const target = container || mount;
  if (!target) return;
  const onWheel = (e) => {
    e.preventDefault();
  };
  // 挂在 light DOM 宿主：Shadow 内滚轮会 retarget 到 host，才能拦住外层滚动
  target.addEventListener("wheel", onWheel, { passive: false });
  container.__lwWheelGuard = onWheel;
  container.__lwWheelTarget = target;
}

function interactionOptions(disableZoom) {
  if (disableZoom) {
    return {
      handleScale: false,
      // 禁止图表吞滚轮，页面仍可正常滚动
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: false,
        horzTouchDrag: false,
        vertTouchDrag: false,
      },
    };
  }
  return {
    handleScale: {
      axisPressedMouseMove: true,
      axisDoubleClickReset: true,
      mouseWheel: true,
      pinch: true,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
  };
}

/** Destroy prior chart bound to container. */
export function disposeChart(container) {
  if (!container) return;
  clearWheelGuard(container);
  if (container.__lwRo) {
    try {
      container.__lwRo.disconnect();
    } catch (_) {
      /* ignore */
    }
    container.__lwRo = null;
  }
  const prev = container.__lwChart;
  if (prev && typeof prev.remove === "function") {
    try {
      prev.remove();
    } catch (_) {
      /* ignore */
    }
  }
  container.__lwChart = null;
  container.__lwSeries = null;
  container.__lwLegendSeries = null;
  container.__lwLegendSolo = null;
  const mount = container.__lwMount;
  if (mount) mount.replaceChildren();
}

function showEmpty(container, text) {
  disposeChart(container);
  syncLegend(container, []);
  const mount = ensureIsolatedMount(container);
  mount.innerHTML = `<p class="quant-chart-empty">${text}</p>`;
}

/**
 * Line series for close prices or equity.
 * @param {HTMLElement} container
 * @param {{ time: string, value: number }[]} points ISO date or YYYY-MM-DD
 * @param {{ emptyText?: string, color?: string, ma?: number[] }} [opts]
 */
export async function renderLineChart(container, points, opts = {}) {
  if (!container) return null;
  const emptyText = opts.emptyText || "暂无曲线";
  const __perfT0 = typeof performance !== "undefined" ? performance.now() : Date.now();
  const seriesPts = normalizeSeriesPoints(points);
  if (seriesPts.length < 2) {
    showEmpty(container, emptyText);
    return null;
  }

  let LC;
  try {
    LC = await loadLightweightCharts();
  } catch (_) {
    showEmpty(container, "图表库加载失败 · 请检查网络");
    return null;
  }

  disposeChart(container);
  const mount = ensureIsolatedMount(container);
  mount.replaceChildren();

  const size = measureHost(container);
  applyMountSize(mount, size);
  const disableZoom = !!opts.disableZoom;
  const chart = LC.createChart(mount, {
    ...chartTheme(),
    autoSize: false,
    width: size.width,
    height: size.height,
    ...interactionOptions(disableZoom),
  });
  const color = opts.color || cssVar("--color-up", "#f5222d");
  const line = chart.addLineSeries({
    color,
    lineWidth: 2,
    priceLineVisible: false,
  });
  try {
    line.setData(seriesPts);
  } catch (err) {
    try {
      chart.remove();
    } catch (_) {
      /* ignore */
    }
    throw err;
  }
  if (opts.zeroLine) {
    try {
      line.createPriceLine({
        price: 0,
        color: "rgba(100, 116, 139, 0.55)",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "0",
      });
    } catch (_) {
      /* ignore */
    }
  }
  if (Array.isArray(opts.markers) && opts.markers.length) {
    try {
      line.setMarkers(opts.markers);
    } catch (_) {
      /* ignore markers */
    }
  }
  try {
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.08, bottom: 0.1 },
    });
  } catch (_) {
    /* ignore */
  }

  const legendItems = [];
  const maList = Array.isArray(opts.ma) ? opts.ma : [];
  if (maList.length) {
    legendItems.push({
      label: opts.mainLabel || "收盘价",
      color,
      thick: true,
      series: line,
    });
  }
  for (const win of maList) {
    const n = Number(win);
    if (!Number.isFinite(n) || n < 2) continue;
    const maPts = [];
    for (let i = 0; i < seriesPts.length; i++) {
      if (i + 1 < n) continue;
      let sum = 0;
      for (let j = i + 1 - n; j <= i; j++) sum += seriesPts[j].value;
      maPts.push({ time: seriesPts[i].time, value: sum / n });
    }
    if (maPts.length < 2) continue;
    const c = maColor(n);
    const maSeries = chart.addLineSeries({
      color: c,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    maSeries.setData(maPts);
    legendItems.push({ label: `MA${n}`, color: c, series: maSeries });
  }
  syncLegend(container, legendItems);

  chart.timeScale().fitContent();
  container.__lwChart = chart;
  container.__lwSeries = line;
  if (!disableZoom) attachWheelGuard(container, mount);

  // 只观察宿主（light DOM），尺寸变化时同步进隔离挂载点
  const ro =
    typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
          if (!container.__lwChart || !container.__lwMount) return;
          const next = measureHost(container);
          applyMountSize(container.__lwMount, next);
          try {
            container.__lwChart.applyOptions(next);
          } catch (_) {
            /* ignore */
          }
        })
      : null;
  if (ro) {
    ro.observe(container);
    container.__lwRo = ro;
  }

  try {
    const dt = (typeof performance !== "undefined" ? performance.now() : Date.now()) - __perfT0;
    if (seriesPts.length >= 1200 && dt > 600) {
      console.warn("[QuantLab][Perf] renderLineChart slow", {
        ms: Math.round(dt),
        points: seriesPts.length,
      });
    }
  } catch (_) {
    /* ignore */
  }
  return chart;
}

/**
 * Dual line (e.g. strategy vs benchmark). pointsA/B: {time, value}[]
 */
export async function renderDualLineChart(container, pointsA, pointsB, opts = {}) {
  if (!container) return null;
  const norm = (pts) => normalizeSeriesPoints(
    (pts || []).map((p) => ({
      time: p.time ?? p.date ?? p.ts,
      value: p.value ?? p.equity_norm ?? p.equity,
    }))
  );
  const a = norm(pointsA);
  const b = norm(pointsB);
  if (a.length < 2 && b.length < 2) {
    showEmpty(container, opts.emptyText || "暂无对照曲线");
    return null;
  }

  let LC;
  try {
    LC = await loadLightweightCharts();
  } catch (_) {
    showEmpty(container, "图表库加载失败");
    return null;
  }

  disposeChart(container);
  const mount = ensureIsolatedMount(container);
  mount.replaceChildren();
  const size = measureHost(container);
  applyMountSize(mount, size);
  const disableZoom = !!opts.disableZoom;
  const chart = LC.createChart(mount, {
    ...chartTheme(),
    autoSize: false,
    width: size.width,
    height: size.height,
    ...interactionOptions(disableZoom),
  });
  const legendItems = [];
  if (a.length >= 2) {
    const sA = chart.addLineSeries({ color: "#2563eb", lineWidth: 2 });
    sA.setData(a);
    if (Array.isArray(opts.markers) && opts.markers.length) {
      try {
        sA.setMarkers(opts.markers);
      } catch (_) {
        /* ignore */
      }
    }
    legendItems.push({
      label: opts.labelA || "策略",
      color: "#2563eb",
      thick: true,
      series: sA,
    });
  }
  if (b.length >= 2) {
    const sB = chart.addLineSeries({ color: "#059669", lineWidth: 2 });
    sB.setData(b);
    legendItems.push({
      label: opts.labelB || "基准",
      color: "#059669",
      series: sB,
    });
  }
  syncLegend(container, legendItems);
  chart.timeScale().fitContent();
  container.__lwChart = chart;
  if (!disableZoom) attachWheelGuard(container, mount);

  const ro =
    typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
          if (!container.__lwChart || !container.__lwMount) return;
          const next = measureHost(container);
          applyMountSize(container.__lwMount, next);
          try {
            container.__lwChart.applyOptions(next);
          } catch (_) {
            /* ignore */
          }
        })
      : null;
  if (ro) {
    ro.observe(container);
    container.__lwRo = ro;
  }
  return chart;
}

/**
 * Multi-line overlay. seriesList: [{ label?, color, points: {time|date, value|equity}[] }]
 */
export async function renderMultiLineChart(container, seriesList, opts = {}) {
  if (!container) return null;
  const norm = (pts) =>
    normalizeSeriesPoints(
      (pts || []).map((p) => ({
        time: p.time ?? p.date ?? p.ts,
        value: p.value ?? p.equity_norm ?? p.equity,
      }))
    );
  const prepared = (seriesList || [])
    .map((s) => ({
      label: s.label || "",
      color: s.color || "#2563eb",
      lineWidth: s.lineWidth || 2,
      points: norm(s.points || s.data || []),
    }))
    .filter((s) => s.points.length >= 2);

  if (!prepared.length) {
    showEmpty(container, opts.emptyText || "暂无多线曲线");
    return null;
  }

  let LC;
  try {
    LC = await loadLightweightCharts();
  } catch (_) {
    showEmpty(container, "图表库加载失败");
    return null;
  }

  disposeChart(container);
  const mount = ensureIsolatedMount(container);
  mount.replaceChildren();
  const size = measureHost(container);
  applyMountSize(mount, size);
  const disableZoom = !!opts.disableZoom;
  const chart = LC.createChart(mount, {
    ...chartTheme(),
    autoSize: false,
    width: size.width,
    height: size.height,
    ...interactionOptions(disableZoom),
  });
  const legendItems = [];
  prepared.forEach((s, idx) => {
    const series = chart.addLineSeries({ color: s.color, lineWidth: s.lineWidth });
    series.setData(s.points);
    legendItems.push({
      label: s.label || "序列",
      color: s.color,
      thick: s.lineWidth >= 2,
      series,
    });
    if (opts.zeroLine && idx === 0) {
      try {
        series.createPriceLine({
          price: 0,
          color: "rgba(100, 116, 139, 0.55)",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: "0",
        });
      } catch (_) {
        /* ignore */
      }
    }
  });
  syncLegend(container, legendItems);
  chart.timeScale().fitContent();
  container.__lwChart = chart;
  if (!disableZoom) attachWheelGuard(container, mount);

  const ro =
    typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
          if (!container.__lwChart || !container.__lwMount) return;
          const next = measureHost(container);
          applyMountSize(container.__lwMount, next);
          try {
            container.__lwChart.applyOptions(next);
          } catch (_) {
            /* ignore */
          }
        })
      : null;
  if (ro) {
    ro.observe(container);
    container.__lwRo = ro;
  }
  return chart;
}
