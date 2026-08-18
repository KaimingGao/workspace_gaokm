import { apiFetch } from "./api_client.js";
import { renderLineChart, renderDualLineChart } from "./lw_charts.js";
import { escapeHtml } from "./shared.js";

const V = (typeof window !== "undefined" && window.__ASSET_V__) || "p803";

let _initiated = false;
let _navChart = null;
let _drawdownChart = null;
let _varChart = null;

function cssToken(name, fallback) {
  const root =
    document.querySelector(".dashboard-page") || document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

function chartPrimary() {
  return cssToken("--d-chart-primary", cssToken("--accent", "#1890ff"));
}

function chartBench() {
  return cssToken("--d-chart-bench", cssToken("--ok", "#059669"));
}

function fmtPct(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return sign + v.toFixed(decimals) + "%";
}

function fmtNum(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(decimals);
}

/** 收益类：A 股涨跌色 */
function clsReturn(v, threshold = 0) {
  if (v == null || !isFinite(v)) return "";
  return v >= threshold ? "is-good" : "is-bad";
}

/** 质量类：ok / danger，非涨跌 */
function clsQuality(v, threshold = 0) {
  if (v == null || !isFinite(v)) return "";
  return v >= threshold ? "is-quality-good" : "is-quality-bad";
}

function fmtMoney(v) {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(2);
}

function readBenchmarkFlag() {
  const el = document.getElementById("dashboard-benchmark-toggle");
  return el ? !!el.checked : true;
}

function readActiveRange() {
  const active = document.querySelector("#dashboard-nav-range [data-range].is-active");
  return active ? active.dataset.range : "30";
}

function setRangeTabState(rangeTabs, activeBtn) {
  if (!rangeTabs || !activeBtn) return;
  rangeTabs.querySelectorAll("[role='tab']").forEach((b) => {
    const on = b === activeBtn;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
}

function kpiCardHtml(item) {
  const val = item.value;
  const sub = item.sub || "";
  const direction =
    item.semantic === "quality"
      ? clsQuality(item.direction, 0)
      : clsReturn(item.direction, 0);
  const sparkline = item.sparkline
    ? `<canvas class="dashboard-kpi-spark" data-spark='${JSON.stringify(item.sparkline)}' width="80" height="24"></canvas>`
    : "";
  return `
    <div class="dashboard-kpi-card ${direction}" data-kpi="${item.key}">
      <span class="dashboard-kpi-label">${escapeHtml(item.label)}</span>
      <span class="dashboard-kpi-value">${item.formatted || escapeHtml(String(val))}</span>
      <span class="dashboard-kpi-sub">${escapeHtml(sub)}</span>
      ${sparkline}
    </div>
  `;
}

function renderSparklines() {
  document.querySelectorAll(".dashboard-kpi-spark").forEach((canvas) => {
    const data = JSON.parse(canvas.dataset.spark || "[]");
    if (!data.length) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const step = w / (data.length - 1 || 1);
    const colorUp = cssToken("--color-up", "#f5222d");
    const colorDown = cssToken("--color-down", "#52c41a");
    const color = data[data.length - 1] >= data[0] ? colorUp : colorDown;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    // hex → soft fill fallback
    ctx.globalAlpha = 0.08;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;
  });
}

function renderMarketOverview(market) {
  const host = document.getElementById("dashboard-market-cards");
  if (!host) return;

  const indices = market?.indices || [];
  const breadth = market?.breadth || {};
  const turnover = market?.turnover || {};

  const cards = [];

  for (const idx of indices) {
    const pct = idx.change_pct;
    const cls = pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
    const closeTxt =
      idx.close != null ? `vs 昨收 · ${fmtNum(idx.close, 0)}` : "vs 昨收";
    cards.push(`
      <div class="dashboard-market-card dashboard-market-card--index ${cls}" title="涨跌幅相对昨收">
        <span class="dashboard-market-label">${escapeHtml(idx.name)}</span>
        <span class="dashboard-market-value">${idx.change_pct != null ? fmtPct(idx.change_pct) : "—"}</span>
        <span class="dashboard-market-sub">${escapeHtml(closeTxt)}</span>
      </div>
    `);
  }

  if (breadth.total_holdings || breadth.up_count || breadth.down_count || breadth.flat_count) {
    const total = breadth.total_holdings || 1;
    const upPct = ((breadth.up_count / total) * 100).toFixed(0);
    const downPct = ((breadth.down_count / total) * 100).toFixed(0);
    cards.push(`
      <div class="dashboard-market-card is-breadth">
        <span class="dashboard-market-label">涨跌家数</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${breadth.up_count || 0}<span class="dashboard-market-sep">/</span>${breadth.down_count || 0}</span>
          <span class="dashboard-market-change">涨${upPct}% · 跌${downPct}% · 平${breadth.flat_count || 0}</span>
        </div>
      </div>
    `);
  }

  if (turnover.total_value != null || turnover.holding_count) {
    cards.push(`
      <div class="dashboard-market-card">
        <span class="dashboard-market-label">持仓市值</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${fmtMoney(turnover.total_value)}</span>
          <span class="dashboard-market-change">${turnover.holding_count || 0} 只</span>
        </div>
      </div>
    `);
  }

  if (breadth.limit_up || breadth.limit_down) {
    cards.push(`
      <div class="dashboard-market-card is-limit">
        <span class="dashboard-market-label">涨停/跌停</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${breadth.limit_up || 0}<span class="dashboard-market-sep">/</span>${breadth.limit_down || 0}</span>
          <span class="dashboard-market-change">涨停 / 跌停</span>
        </div>
      </div>
    `);
  }

  if (cards.length === 0) {
    cards.push(`<div class="dashboard-empty dashboard-empty--span">暂无市场数据</div>`);
  }

  host.innerHTML = cards.join("");
}

function renderSectorHeatmap(sectors) {
  const grid = document.getElementById("dashboard-sector-grid");
  const sub = document.getElementById("dashboard-sector-sub");
  if (!grid) return;
  if (!sectors || !sectors.length) {
    grid.innerHTML = `<div class="dashboard-empty">暂无板块数据</div>`;
    if (sub) sub.textContent = "持仓行业";
    return;
  }
  if (sub) sub.textContent = `${sectors.length} 个板块`;
  grid.innerHTML = sectors
    .map((s) => {
      const pct = s.change_pct ?? 0;
      const cls = pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
      const upRatio = s.up_ratio || 0;
      const title = `${s.name}\n涨跌: ${fmtPct(pct)}\n市值: ${fmtMoney(s.value)}\n个股: ${s.count}\n上涨比例: ${upRatio}%`;
      return `
      <div class="dashboard-sector-cell ${cls}" data-sector="${escapeHtml(s.name)}" title="${escapeHtml(title)}">
        <span class="dashboard-sector-name">${escapeHtml(s.name)}</span>
        <span class="dashboard-sector-pct">${fmtPct(pct)}</span>
        <span class="dashboard-sector-sub">${s.count}只 · ${upRatio}%涨</span>
      </div>
    `;
    })
    .join("");
}

function renderLeaderboard(strategies) {
  const body = document.getElementById("dashboard-leaderboard");
  if (!body) return;
  if (!strategies || !strategies.length) {
    body.innerHTML = `<div class="dashboard-empty">暂无策略数据</div>`;
    return;
  }
  const rows = strategies
    .map(
      (s, i) => `
    <tr>
      <td class="col-rank">${i + 1}</td>
      <td class="col-name">${escapeHtml(s.name || s.strategy || "—")}</td>
      <td class="col-ret ${s.return_pct > 0 ? "up" : s.return_pct < 0 ? "down" : ""}">${fmtPct(s.return_pct)}</td>
      <td class="col-sharpe">${fmtNum(s.sharpe, 2)}</td>
    </tr>
  `
    )
    .join("");
  body.innerHTML = `
    <table class="dashboard-leaderboard-table">
      <thead>
        <tr>
          <th>#</th>
          <th>策略</th>
          <th class="num">收益率</th>
          <th class="num">夏普</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderSignals(signals) {
  const list = document.getElementById("dashboard-signals-list");
  const countEl = document.getElementById("dashboard-signals-count");
  if (!list) return;
  if (countEl) countEl.textContent = signals ? `${signals.length} 条` : "—";
  if (!signals || !signals.length) {
    list.innerHTML = `<div class="dashboard-empty">暂无信号</div>`;
    return;
  }
  list.innerHTML = signals
    .slice(0, 20)
    .map((s) => {
      const rawDir = String(s.direction || "").toLowerCase();
      const dir =
        rawDir === "long" ||
        rawDir === "buy" ||
        rawDir === "bullish" ||
        s.direction === "多"
          ? "is-long"
          : rawDir === "short" ||
              rawDir === "sell" ||
              rawDir === "bearish" ||
              s.direction === "空"
            ? "is-short"
            : "";
      const dirLabel =
        dir === "is-long" ? "多" : dir === "is-short" ? "空" : String(s.direction || "—");
      const ts = String(s.time || s.ts || "");
      const timeShort = ts.includes("T")
        ? ts.slice(5, 16).replace("T", " ")
        : ts.slice(0, 16);
      const code = s.code || s.stock_code || "—";
      const name = s.name || s.stock_name || "";
      return `
      <div class="dashboard-signal-item ${dir}">
        <span class="dashboard-signal-code">${escapeHtml(String(code))}</span>
        <span class="dashboard-signal-name" title="${escapeHtml(name || String(code))}">${escapeHtml(name || "—")}</span>
        <span class="dashboard-signal-dir">${escapeHtml(dirLabel)}</span>
        <span class="dashboard-signal-score num">${fmtNum(s.score ?? s.predicted_score, 2)}</span>
        <span class="dashboard-signal-time">${escapeHtml(timeShort)}</span>
      </div>
    `;
    })
    .join("");
}

function renderRiskStrip(risk, exposure, varData, kpis, drawdownData) {
  const host = document.getElementById("dashboard-risk-strip");
  if (!host) return;
  const var95 =
    (risk && risk.var_95 != null ? risk.var_95 : null) ??
    (varData && varData.var_95 != null ? varData.var_95 : null);
  const cvar95 =
    (risk && risk.cvar_95 != null ? risk.cvar_95 : null) ??
    (varData && varData.cvar_95 != null ? varData.cvar_95 : null);
  const hhi = exposure && exposure.hhi != null ? exposure.hhi : null;
  const conc = exposure && exposure.concentration ? exposure.concentration : null;
  const concMap = { low: "分散", medium: "中等", high: "集中" };
  const overN = Array.isArray(exposure?.over_limit_sectors)
    ? exposure.over_limit_sectors.length
    : 0;
  const holdingsN = exposure?.holdings_count ?? exposure?.sector_count ?? null;
  const sharpe =
    risk?.sharpe != null
      ? risk.sharpe
      : kpis?.sharpe != null
        ? kpis.sharpe
        : null;
  const maxDd =
    risk?.max_drawdown != null
      ? risk.max_drawdown
      : drawdownData?.max_drawdown != null
        ? drawdownData.max_drawdown
        : kpis?.max_drawdown != null
          ? kpis.max_drawdown
          : null;

  const items = [
    {
      k: "VaR95",
      v: var95 != null ? fmtPct(var95) : "—",
      cls: var95 != null && var95 > 2 ? "is-quality-bad" : "",
    },
    {
      k: "CVaR95",
      v: cvar95 != null ? fmtPct(cvar95) : "—",
      cls: cvar95 != null && cvar95 > 3 ? "is-quality-bad" : "",
    },
    {
      k: "Sharpe",
      v: sharpe != null ? fmtNum(sharpe, 2) : "—",
      cls:
        sharpe != null && sharpe < 0
          ? "is-quality-bad"
          : sharpe != null && sharpe >= 1
            ? "is-quality-good"
            : "",
    },
    {
      k: "最大回撤",
      v: maxDd != null ? fmtPct(maxDd) : "—",
      cls: maxDd != null && maxDd > 5 ? "is-quality-bad" : "",
    },
    {
      k: "集中度",
      v: conc
        ? `${concMap[conc] || conc}${hhi != null ? ` · HHI ${fmtNum(hhi, 3)}` : ""}`
        : "—",
      cls: conc === "high" ? "is-quality-bad" : "",
    },
    {
      k: "超限板块",
      v: String(overN),
      cls: overN > 0 ? "is-quality-bad" : "is-quality-good",
    },
    {
      k: "持仓",
      v: holdingsN != null ? `${holdingsN}` : "—",
      cls: "",
    },
  ];
  host.innerHTML = items
    .map(
      (it) =>
        `<div class="dashboard-risk-strip-item ${it.cls}">` +
        `<span class="dashboard-risk-strip-k">${escapeHtml(it.k)}</span>` +
        `<span class="dashboard-risk-strip-v">${escapeHtml(it.v)}</span>` +
        `</div>`
    )
    .join("");
}

function renderRiskMetrics(risk) {
  const host = document.getElementById("dashboard-risk-cards");
  if (!host) return;
  if (!risk || risk.ok === false) {
    host.innerHTML = `<div class="dashboard-empty dashboard-empty--span">${risk?.message || "暂无风险数据"}</div>`;
    return;
  }

  const items = [
    {
      key: "ann_return",
      label: "年化收益",
      formatted: fmtPct(risk.ann_return),
      direction: risk.ann_return,
      semantic: "return",
    },
    {
      key: "ann_vol",
      label: "年化波动",
      formatted: fmtPct(risk.ann_volatility),
      direction: -risk.ann_volatility,
      semantic: "quality",
    },
    {
      key: "sortino",
      label: "Sortino",
      formatted: fmtNum(risk.sortino, 2),
      direction: risk.sortino - 1,
      semantic: "quality",
    },
    {
      key: "calmar",
      label: "Calmar",
      formatted: fmtNum(risk.calmar, 2),
      direction: risk.calmar - 1,
      semantic: "quality",
    },
  ];

  host.innerHTML = items
    .map((item) => {
      const direction =
        item.semantic === "quality"
          ? clsQuality(item.direction, 0)
          : clsReturn(item.direction, 0);
      return `
      <div class="dashboard-risk-card ${direction}" data-risk="${item.key}">
        <span class="dashboard-risk-label">${escapeHtml(item.label)}</span>
        <span class="dashboard-risk-value">${item.formatted}</span>
      </div>
    `;
    })
    .join("");
}

function renderFactorExposure(exposure) {
  const host = document.getElementById("dashboard-exposure-body");
  const concEl = document.getElementById("dashboard-exposure-concentration");
  if (!host) return;
  if (!exposure || !exposure.ok) {
    host.innerHTML = `<div class="dashboard-empty">${exposure?.message || "暂无暴露数据"}</div>`;
    if (concEl) concEl.textContent = "—";
    return;
  }

  if (concEl) {
    const concMap = { low: "分散", medium: "中等", high: "集中" };
    concEl.textContent = `${concMap[exposure.concentration] || "—"} · HHI ${fmtNum(exposure.hhi, 3)}`;
  }

  const sectors = exposure.sectors || [];
  if (!sectors.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无板块暴露</div>`;
    return;
  }

  const maxPct = Math.max(...sectors.map((s) => s.pct), 1);
  host.innerHTML = `
    <div class="dashboard-exposure-list">
      ${sectors
        .slice(0, 8)
        .map(
          (s) => `
        <div class="dashboard-exposure-row">
          <span class="dashboard-exposure-name">${escapeHtml(s.name)}</span>
          <div class="dashboard-exposure-bar-track">
            <div class="dashboard-exposure-bar" style="width:${((s.pct / maxPct) * 100).toFixed(1)}%"></div>
          </div>
          <span class="dashboard-exposure-pct num">${s.pct.toFixed(1)}%</span>
        </div>
      `
        )
        .join("")}
    </div>
  `;
}

function allocColor(i) {
  const fallback = [
    "#1890ff",
    "#059669",
    "#b45309",
    "#b42318",
    "#0369a1",
    "#4d7c0f",
    "#0f766e",
    "#475569",
  ];
  return cssToken(`--d-alloc-${(i % 8) + 1}`, fallback[i % 8]);
}

/** 资产配置：横向条带（非饼图） */
function renderAllocation(allocation) {
  const host = document.getElementById("dashboard-allocation-legend");
  const sub = document.getElementById("dashboard-allocation-sub");
  if (!host) return;
  if (!allocation || !allocation.sectors || !allocation.sectors.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无配置数据</div>`;
    if (sub) sub.textContent = "板块市值";
    return;
  }
  const total =
    allocation.total_value ||
    allocation.sectors.reduce((s, x) => s + (Number(x.value) || 0), 0) ||
    1;
  if (sub) sub.textContent = `总市值 ${fmtMoney(total)}`;

  const maxPct = Math.max(
    ...allocation.sectors.map((s) =>
      s.pct != null ? Number(s.pct) : ((Number(s.value) || 0) / total) * 100
    ),
    1
  );

  host.innerHTML = `
    <div class="dashboard-allocation-list">
      ${allocation.sectors
        .map((s, i) => {
          const pct =
            s.pct != null
              ? Number(s.pct)
              : ((Number(s.value) || 0) / total) * 100;
          const color = allocColor(i);
          const w = Math.max(2, (pct / maxPct) * 100);
          return `
        <div class="dashboard-allocation-row">
          <span class="dashboard-allocation-swatch" style="background:${color}"></span>
          <span class="dashboard-allocation-name">${escapeHtml(s.name)}</span>
          <div class="dashboard-allocation-bar-track">
            <div class="dashboard-allocation-bar" style="width:${w.toFixed(1)}%;background:${color}"></div>
          </div>
          <span class="dashboard-allocation-pct num">${pct.toFixed(1)}%</span>
        </div>`;
        })
        .join("")}
    </div>
  `;
}

function renderNavChart(points, range, benchmarkPoints) {
  const host = document.getElementById("dashboard-nav-chart");
  const statsEl = document.getElementById("dashboard-nav-stats");
  if (!host) return;
  let filtered = points || [];
  let filteredBenchmark = benchmarkPoints || [];
  if (range && range !== "all") {
    const now = new Date();
    let cutoff;
    if (range === "30") cutoff = new Date(now.getTime() - 30 * 86400000);
    else if (range === "90") cutoff = new Date(now.getTime() - 90 * 86400000);
    else if (range === "ytd") cutoff = new Date(now.getFullYear(), 0, 1);
    if (cutoff) {
      filtered = points.filter((p) => new Date(p.time || p.date) >= cutoff);
      filteredBenchmark = filteredBenchmark.filter(
        (p) => new Date(p.time || p.date) >= cutoff
      );
    }
  }
  const pts = filtered.map((p) => ({
    time: p.time || p.date,
    value: p.value ?? p.equity ?? p.nav,
  }));
  const benchPts = filteredBenchmark.map((p) => ({
    time: p.time || p.date,
    value: p.value ?? p.equity ?? p.nav,
  }));

  const lastLive = !!(pts.length && (filtered[filtered.length - 1] || {}).live);
  const subEl = document.querySelector(".dashboard-nav-card .dashboard-card-sub");
  if (subEl) {
    subEl.textContent = lastLive ? "起点 100 · 末点现价盯市" : "起点 100";
  }

  if (statsEl && pts.length > 1) {
    const vals = pts.map((p) => p.value).filter((v) => v != null && isFinite(v));
    if (vals.length >= 2) {
      const first = vals[0];
      const last = vals[vals.length - 1];
      const ret = (last / first - 1) * 100;
      const peak = Math.max(...vals);
      const dd = ((peak - last) / peak) * 100;

      let benchRet = null;
      let excessRet = null;
      if (benchPts.length >= 2) {
        const bFirst = benchPts[0].value;
        const bLast = benchPts[benchPts.length - 1].value;
        if (isFinite(bFirst) && isFinite(bLast) && bFirst !== 0) {
          benchRet = (bLast / bFirst - 1) * 100;
          excessRet = ret - benchRet;
        }
      }

      const stat = (label, value, cls = "") =>
        `<span class="dashboard-nav-stat"><span class="nav-stat-label">${label}</span>` +
        `<span class="nav-stat-value ${cls}">${value}</span></span>`;
      let statsHtml =
        stat("区间", fmtPct(ret), ret >= 0 ? "up" : "down") +
        stat("峰值", fmtNum(peak, 0)) +
        stat("回撤", fmtPct(-dd), dd > 0 ? "down" : "");
      if (benchRet !== null) {
        statsHtml +=
          stat("基准", fmtPct(benchRet), benchRet >= 0 ? "up" : "down") +
          stat("超额", fmtPct(excessRet), excessRet >= 0 ? "up" : "down");
      }
      statsHtml += stat("n", String(vals.length));
      statsEl.innerHTML = statsHtml;
    }
  }

  const primary = chartPrimary();
  const bench = chartBench();
  if (benchPts.length >= 2) {
    renderDualLineChart(host, pts, benchPts, {
      labelA: "策略净值",
      labelB: "沪深300",
      colorA: primary,
      colorB: bench,
      emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
      disableZoom: false,
    }).then((chart) => {
      _navChart = chart;
    });
  } else {
    renderLineChart(host, pts, {
      color: primary,
      emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
      disableZoom: false,
    }).then((chart) => {
      _navChart = chart;
    });
  }
}

function renderDrawdownChart(drawdownPoints) {
  const host = document.getElementById("dashboard-drawdown-chart");
  const statsEl = document.getElementById("dashboard-drawdown-stats");
  if (!host) return null;
  if (!drawdownPoints || !drawdownPoints.length) {
    if (statsEl) statsEl.textContent = "—";
    host.innerHTML = `<div class="dashboard-empty">暂无回撤数据</div>`;
    return null;
  }
  const pts = drawdownPoints
    .map((p) => ({
      time: p.time || p.date,
      value: Number(p.value),
    }))
    .filter((p) => p.time && Number.isFinite(p.value));
  if (!pts.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无回撤数据</div>`;
    return null;
  }
  const minVal = Math.min(...pts.map((p) => p.value));
  if (statsEl) statsEl.textContent = `最大回撤 ${fmtPct(minVal)} · ${pts.length} 点`;
  return renderLineChart(host, pts, {
    color: chartPrimary(),
    emptyText: "暂无回撤数据",
    disableZoom: false,
    zeroLine: true,
  }).then((chart) => {
    _drawdownChart = chart;
    return chart;
  });
}

function renderVarHistogram(histData) {
  const host = document.getElementById("dashboard-var-chart");
  const statsEl = document.getElementById("dashboard-var-stats");
  if (!host) return null;
  const buckets = (histData && (histData.histogram || histData.buckets)) || [];
  if (!histData || histData.ok === false || !buckets.length) {
    if (statsEl) statsEl.textContent = histData?.message || "—";
    host.innerHTML = `<div class="dashboard-empty">${histData?.message || "暂无VaR历史数据"}</div>`;
    return null;
  }
  const maxCount = Math.max(...buckets.map((b) => Number(b.count) || 0), 1);
  const var95 = histData.var_95;
  const var99 = histData.var_99;
  const cvar95 = histData.cvar_95;
  if (statsEl) {
    statsEl.textContent = `VaR95 ${fmtPct(var95)} · CVaR95 ${fmtPct(cvar95)} · n=${histData.sample_count ?? buckets.length}`;
  }
  const bars = buckets
    .map((b) => {
      const count = Number(b.count) || 0;
      const h = Math.max(2, (count / maxCount) * 100);
      const mid = ((Number(b.range_low) || 0) + (Number(b.range_high) || 0)) / 2;
      const isTail95 = var95 != null && mid <= -Math.abs(Number(var95));
      const isTail99 = var99 != null && mid <= -Math.abs(Number(var99));
      const cls = isTail99 ? "is-tail99" : isTail95 ? "is-tail95" : "";
      const title = `${b.label || `${b.range_low}% ~ ${b.range_high}%`} · ${count}`;
      return `<div class="dashboard-var-bar ${cls}" style="height:${h}%" title="${escapeHtml(title)}"></div>`;
    })
    .join("");
  host.innerHTML =
    `<div class="dashboard-var-hist" aria-label="收益直方图">${bars}</div>` +
    `<p class="dashboard-var-footnote">橙/红柱≈VaR 尾部 · 样本 ${escapeHtml(String(histData.sample_count ?? "—"))}</p>`;
  _varChart = { host };
  return _varChart;
}

async function loadDashboardData(range = "30", showBenchmark = readBenchmarkFlag()) {
  const meta = document.getElementById("dashboard-meta");
  const updated = document.getElementById("dashboard-updated");
  if (meta) meta.setAttribute("aria-busy", "true");
  if (updated) {
    updated.dataset.state = "loading";
    updated.textContent = "加载中";
    updated.setAttribute("aria-busy", "true");
  }

  const unwrap = (res, fallback = {}) => {
    if (!res || res.ok === false) {
      // apiFetch 失败：{ ok:false, data, error }；本地兜底也可能直接是 payload
      if (res && res.data && typeof res.data === "object")
        return { ...fallback, ...res.data, ok: false };
      return { ok: false, ...fallback };
    }
    if (res.data && typeof res.data === "object") return res.data;
    return res;
  };

  const fetchDash = (url, fallback = {}) =>
    apiFetch(url)
      .then((res) => unwrap(res, fallback))
      .catch(() => ({ ok: false, ...fallback }));

  try {
    const benchParam = showBenchmark ? "hs300" : "none";
    const [
      marketData,
      kpis,
      riskData,
      navData,
      sectors,
      signals,
      allocation,
      exposure,
      drawdownData,
      varData,
    ] = await Promise.all([
      fetchDash(`/api/dashboard/market-overview`),
      fetchDash(`/api/dashboard/kpis`),
      fetchDash(`/api/dashboard/risk-metrics`),
      fetchDash(`/api/dashboard/nav-curve?range=${range}&benchmark=${benchParam}`, {
        points: [],
        benchmark_points: [],
      }),
      fetchDash(`/api/dashboard/sector-heatmap`, { sectors: [] }),
      fetchDash(`/api/dashboard/signals`, { signals: [] }),
      fetchDash(`/api/dashboard/allocation`, { sectors: [], total_value: 0 }),
      fetchDash(`/api/dashboard/factor-exposure`),
      fetchDash(`/api/dashboard/drawdown?range=${range}`, { points: [] }),
      fetchDash(`/api/dashboard/var-historical?range=${range}`),
    ]);

    renderMarketOverview(marketData);

    const kpiHost = document.getElementById("dashboard-kpi-cards");
    if (kpiHost && kpis.ok !== false) {
      const items = [
        {
          key: "today_return",
          label: "今日收益",
          value: kpis.today_return,
          formatted: fmtPct(kpis.today_return),
          sub:
            kpis.today_return_basis === "reset"
              ? "自回零"
              : kpis.today_return_basis === "prev_nav"
                ? "vs 昨收账本"
                : "vs 昨收",
          sparkline: kpis.sparkline_today,
          direction: kpis.today_return,
          semantic: "return",
        },
        {
          key: "total_return",
          label: "累计收益",
          value: kpis.total_return,
          formatted: fmtPct(kpis.total_return),
          sub: kpis.period || "全部",
          sparkline: kpis.sparkline_total,
          direction: kpis.total_return,
          semantic: "return",
        },
        {
          key: "sharpe",
          label: "Sharpe",
          value: kpis.sharpe,
          formatted: fmtNum(kpis.sharpe, 2),
          sub: "年化",
          sparkline: kpis.sparkline_sharpe,
          direction: kpis.sharpe - 1,
          semantic: "quality",
        },
        {
          key: "max_dd",
          label: "最大回撤",
          value: kpis.max_drawdown,
          formatted: fmtPct(kpis.max_drawdown),
          sub: "历史极值",
          sparkline: kpis.sparkline_dd,
          direction:
            kpis.max_drawdown != null ? -Math.abs(kpis.max_drawdown) : null,
          semantic: "return",
        },
        {
          key: "win_rate",
          label: "胜率",
          value: kpis.win_rate,
          formatted: fmtPct(kpis.win_rate),
          sub: (kpis.trade_count || 0) + " 笔",
          sparkline: kpis.sparkline_winrate,
          direction: kpis.win_rate - 50,
          semantic: "quality",
        },
      ];
      kpiHost.innerHTML = items.map(kpiCardHtml).join("");
      renderSparklines();
    } else if (kpiHost) {
      kpiHost.innerHTML = `<div class="dashboard-empty dashboard-empty--span">暂无 KPI 数据 · 请先在交易执行页运行纸面</div>`;
    }

    try {
      renderRiskMetrics(riskData);
    } catch (e) {
      console.warn("[Dashboard] risk", e);
    }
    try {
      renderRiskStrip(riskData, exposure, varData, kpis, drawdownData);
    } catch (e) {
      console.warn("[Dashboard] risk-strip", e);
    }

    try {
      if (navData && navData.points) {
        renderNavChart(
          navData.points,
          range,
          showBenchmark ? navData.benchmark || navData.benchmark_points || [] : []
        );
      }
    } catch (e) {
      console.warn("[Dashboard] nav", e);
    }

    try {
      if (drawdownData && drawdownData.points) {
        renderDrawdownChart(drawdownData.points);
      }
    } catch (e) {
      console.warn("[Dashboard] drawdown", e);
    }

    try {
      if (varData && varData.ok !== false) {
        renderVarHistogram(varData);
      } else {
        const host = document.getElementById("dashboard-var-chart");
        const statsEl = document.getElementById("dashboard-var-stats");
        if (statsEl) statsEl.textContent = "—";
        if (host) {
          host.innerHTML = `<div class="dashboard-empty">${varData?.message || "暂无VaR历史数据"}</div>`;
        }
      }
    } catch (e) {
      console.warn("[Dashboard] var", e);
    }

    try {
      renderSectorHeatmap(sectors?.sectors || []);
    } catch (e) {
      console.warn("[Dashboard] sector", e);
    }
    try {
      renderLeaderboard(kpis?.strategies || []);
    } catch (e) {
      console.warn("[Dashboard] leaderboard", e);
    }
    try {
      renderSignals(signals?.signals || []);
    } catch (e) {
      console.warn("[Dashboard] signals", e);
    }
    try {
      renderAllocation(allocation);
    } catch (e) {
      console.warn("[Dashboard] allocation", e);
    }
    try {
      renderFactorExposure(exposure);
    } catch (e) {
      console.warn("[Dashboard] exposure", e);
    }

    const t = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    if (meta) {
      meta.textContent = "纸面组合 · 市场 · 风险 · 信号";
      meta.setAttribute("aria-busy", "false");
    }
    if (updated) {
      updated.dataset.state = "ready";
      updated.textContent = t;
      updated.setAttribute("aria-busy", "false");
    }
  } catch (err) {
    console.error("[Dashboard] load failed", err);
    if (meta) {
      meta.textContent = "加载失败: " + (err.message || err);
      meta.setAttribute("aria-busy", "false");
    }
    if (updated) {
      updated.dataset.state = "error";
      updated.textContent = "失败";
      updated.setAttribute("aria-busy", "false");
    }
  }
}

function bindEvents() {
  const refreshBtn = document.getElementById("dashboard-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      loadDashboardData(readActiveRange(), readBenchmarkFlag());
    });
  }

  const rangeTabs = document.getElementById("dashboard-nav-range");
  if (rangeTabs) {
    const tabs = () => [...rangeTabs.querySelectorAll("[role='tab']")];
    rangeTabs.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-range]");
      if (!btn) return;
      setRangeTabState(rangeTabs, btn);
      loadDashboardData(btn.dataset.range, readBenchmarkFlag());
    });
    rangeTabs.addEventListener("keydown", (e) => {
      const list = tabs();
      if (!list.length) return;
      const i = list.findIndex((b) => b.classList.contains("is-active"));
      let next = -1;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % list.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
        next = (i - 1 + list.length) % list.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = list.length - 1;
      if (next < 0) return;
      e.preventDefault();
      setRangeTabState(rangeTabs, list[next]);
      list[next].focus();
      loadDashboardData(list[next].dataset.range, readBenchmarkFlag());
    });
  }

  const benchToggle = document.getElementById("dashboard-benchmark-toggle");
  if (benchToggle) {
    benchToggle.addEventListener("change", () => {
      loadDashboardData(readActiveRange(), readBenchmarkFlag());
    });
  }
}

export function initDashboard(ctx) {
  if (_initiated) return;
  _initiated = true;
  void V;
  void ctx;
  const rangeTabs = document.getElementById("dashboard-nav-range");
  if (rangeTabs) {
    const active =
      rangeTabs.querySelector("[data-range].is-active") ||
      rangeTabs.querySelector("[data-range]");
    if (active) setRangeTabState(rangeTabs, active);
  }
  bindEvents();
  loadDashboardData("30", readBenchmarkFlag());

  setInterval(() => {
    loadDashboardData(readActiveRange(), readBenchmarkFlag());
  }, 90000);
}
