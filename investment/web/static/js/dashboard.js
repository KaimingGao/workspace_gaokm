import { apiFetch } from "./api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart } from "./lw_charts.js";
import { escapeHtml } from "./shared.js";

const V = (typeof window !== "undefined" && window.__ASSET_V__) || "p747";

let _initiated = false;
let _navChart = null;
let _drawdownChart = null;
let _varChart = null;
let _factorICChart = null;

function fmtPct(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return sign + v.toFixed(decimals) + "%";
}

function fmtNum(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(decimals);
}

function clsGoodBad(v, threshold = 0) {
  if (v == null || !isFinite(v)) return "";
  return v >= threshold ? "is-good" : "is-bad";
}

function fmtMoney(v) {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(2);
}

function kpiCardHtml(item) {
  const val = item.value;
  const sub = item.sub || "";
  const direction = clsGoodBad(item.direction, 0);
  const sparkline = item.sparkline ? `<canvas class="dashboard-kpi-spark" data-spark='${JSON.stringify(item.sparkline)}' width="80" height="24"></canvas>` : "";
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
    const color = data[data.length - 1] >= data[0] ? "#059669" : "#ef4444";
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
    ctx.fillStyle = color.replace(")", ",0.08)").replace("rgb", "rgba");
    ctx.fill();
  });
}

function renderMarketOverview(market) {
  const host = document.getElementById("dashboard-market-cards");
  if (!host) return;

  const indices = market?.indices || [];
  const breadth = market?.breadth || {};
  const turnover = market?.turnover || {};

  const cards = [];

  // Index cards
  for (const idx of indices) {
    const pct = idx.change_pct;
    const cls = pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
    cards.push(`
      <div class="dashboard-market-card ${cls}">
        <span class="dashboard-market-label">${escapeHtml(idx.name)}</span>
        <span class="dashboard-market-value">${idx.close != null ? fmtNum(idx.close, 0) : "—"}</span>
        <span class="dashboard-market-change">${idx.change_pct != null ? fmtPct(idx.change_pct) : "—"}</span>
      </div>
    `);
  }

  // Breadth cards — 有持仓就展示（含全平）
  if (breadth.total_holdings || breadth.up_count || breadth.down_count || breadth.flat_count) {
    const total = breadth.total_holdings || 1;
    const upPct = (breadth.up_count / total * 100).toFixed(0);
    const downPct = (breadth.down_count / total * 100).toFixed(0);
    cards.push(`
      <div class="dashboard-market-card is-breadth">
        <span class="dashboard-market-label">涨跌家数</span>
        <span class="dashboard-market-value">${breadth.up_count || 0} / ${breadth.down_count || 0}</span>
        <span class="dashboard-market-change">涨 ${upPct}% · 跌 ${downPct}% · 平 ${breadth.flat_count || 0}</span>
      </div>
    `);
  }

  // Turnover
  if (turnover.total_value) {
    cards.push(`
      <div class="dashboard-market-card">
        <span class="dashboard-market-label">持仓市值</span>
        <span class="dashboard-market-value">${fmtMoney(turnover.total_value)}</span>
        <span class="dashboard-market-change">${turnover.holding_count || 0} 只</span>
      </div>
    `);
  }

  // Limit up/down
  if (breadth.limit_up || breadth.limit_down) {
    cards.push(`
      <div class="dashboard-market-card is-limit">
        <span class="dashboard-market-label">涨停/跌停</span>
        <span class="dashboard-market-value">${breadth.limit_up || 0} / ${breadth.limit_down || 0}</span>
        <span class="dashboard-market-change">涨停 / 跌停</span>
      </div>
    `);
  }

  if (cards.length === 0) {
    cards.push(`<div class="dashboard-empty" style="grid-column:1/-1">暂无市场数据</div>`);
  }

  host.innerHTML = cards.join("");
}

function renderSectorHeatmap(sectors) {
  const grid = document.getElementById("dashboard-sector-grid");
  const sub = document.getElementById("dashboard-sector-sub");
  if (!grid) return;
  if (!sectors || !sectors.length) {
    grid.innerHTML = `<div class="dashboard-empty">暂无板块数据</div>`;
    return;
  }
  grid.innerHTML = sectors.map((s) => {
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
  }).join("");
}

function renderLeaderboard(strategies) {
  const body = document.getElementById("dashboard-leaderboard");
  if (!body) return;
  if (!strategies || !strategies.length) {
    body.innerHTML = `<div class="dashboard-empty">暂无策略数据</div>`;
    return;
  }
  const rows = strategies.map((s, i) => `
    <tr>
      <td class="col-rank">${i + 1}</td>
      <td class="col-name">${escapeHtml(s.name || s.strategy || "—")}</td>
      <td class="col-ret ${s.return_pct > 0 ? "up" : s.return_pct < 0 ? "down" : ""}">${fmtPct(s.return_pct)}</td>
      <td class="col-sharpe">${fmtNum(s.sharpe, 2)}</td>
    </tr>
  `).join("");
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
  list.innerHTML = signals.slice(0, 20).map((s) => {
    const dir = s.direction === "long" || s.direction === "buy" || s.direction === "多" ? "is-long" : s.direction === "short" || s.direction === "sell" || s.direction === "空" ? "is-short" : "";
    return `
      <div class="dashboard-signal-item ${dir}">
        <span class="dashboard-signal-code">${escapeHtml(s.code || s.stock_code || "—")}</span>
        <span class="dashboard-signal-dir">${escapeHtml(String(s.direction || "—"))}</span>
        <span class="dashboard-signal-score num">${fmtNum(s.score ?? s.predicted_score, 2)}</span>
        <span class="dashboard-signal-time">${escapeHtml(s.time || s.ts || "")}</span>
      </div>
    `;
  }).join("");
}

function renderRiskMetrics(risk) {
  const host = document.getElementById("dashboard-risk-cards");
  if (!host) return;
  if (!risk || risk.ok === false) {
    host.innerHTML = `<div class="dashboard-empty" style="grid-column:1/-1">${risk?.message || "暂无风险数据"}</div>`;
    return;
  }

  const items = [
    { key: "ann_return", label: "年化收益", value: risk.ann_return, formatted: fmtPct(risk.ann_return), direction: risk.ann_return },
    { key: "ann_vol", label: "年化波动", value: risk.ann_volatility, formatted: fmtPct(risk.ann_volatility), direction: -risk.ann_volatility },
    { key: "sharpe", label: "Sharpe", value: risk.sharpe, formatted: fmtNum(risk.sharpe, 2), direction: risk.sharpe - 1 },
    { key: "sortino", label: "Sortino", value: risk.sortino, formatted: fmtNum(risk.sortino, 2), direction: risk.sortino - 1 },
    { key: "max_dd", label: "最大回撤", value: risk.max_drawdown, formatted: fmtPct(risk.max_drawdown), direction: -risk.max_drawdown },
    { key: "var95", label: "VaR(95%)", value: risk.var_95, formatted: fmtPct(risk.var_95), direction: -risk.var_95 },
    { key: "cvar95", label: "CVaR(95%)", value: risk.cvar_95, formatted: fmtPct(risk.cvar_95), direction: -risk.cvar_95 },
    { key: "calmar", label: "Calmar", value: risk.calmar, formatted: fmtNum(risk.calmar, 2), direction: risk.calmar - 1 },
  ];

  host.innerHTML = items.map(item => {
    const direction = clsGoodBad(item.direction, 0);
    return `
      <div class="dashboard-risk-card ${direction}" data-risk="${item.key}">
        <span class="dashboard-risk-label">${escapeHtml(item.label)}</span>
        <span class="dashboard-risk-value">${item.formatted}</span>
      </div>
    `;
  }).join("");
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

  const maxPct = Math.max(...sectors.map(s => s.pct), 1);
  host.innerHTML = `
    <div class="dashboard-exposure-list">
      ${sectors.slice(0, 8).map(s => `
        <div class="dashboard-exposure-row">
          <span class="dashboard-exposure-name">${escapeHtml(s.name)}</span>
          <div class="dashboard-exposure-bar-track">
            <div class="dashboard-exposure-bar" style="width:${(s.pct / maxPct * 100).toFixed(1)}%"></div>
          </div>
          <span class="dashboard-exposure-pct num">${s.pct.toFixed(1)}%</span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderAllocation(allocation) {
  const legend = document.getElementById("dashboard-allocation-legend");
  if (!legend) return;
  if (!allocation || !allocation.sectors || !allocation.sectors.length) {
    legend.innerHTML = `<div class="dashboard-empty">暂无配置数据</div>`;
    return;
  }
  const total = allocation.total_value || 1;
  const colors = ["#2563eb", "#059669", "#7c3aed", "#d97706", "#ef4444", "#0891b2", "#db2777", "#65a30d", "#0d9488", "#4f46e5"];
  const items = allocation.sectors.map((s, i) => {
    const pct = ((s.value / total) * 100).toFixed(1);
    const color = colors[i % colors.length];
    return `
      <div class="dashboard-allocation-item">
        <span class="dashboard-allocation-swatch" style="background:${color}"></span>
        <span class="dashboard-allocation-name">${escapeHtml(s.name)}</span>
        <span class="dashboard-allocation-pct num">${pct}%</span>
      </div>
    `;
  }).join("");
  legend.innerHTML = items;

  const chart = document.getElementById("dashboard-allocation-chart");
  if (chart) {
    const ctx = chart.getContext("2d");
    const w = chart.width = chart.offsetWidth;
    const h = chart.height = chart.offsetHeight;
    const cx = w / 2;
    const cy = h / 2;
    const r = Math.min(cx, cy) - 10;
    const innerR = r * 0.6;
    ctx.clearRect(0, 0, w, h);
    let startAngle = -Math.PI / 2;
    allocation.sectors.forEach((s, i) => {
      const slice = (s.value / total) * Math.PI * 2;
      const color = colors[i % colors.length];
      ctx.beginPath();
      ctx.arc(cx, cy, r, startAngle, startAngle + slice);
      ctx.arc(cx, cy, innerR, startAngle + slice, startAngle, true);
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
      startAngle += slice;
    });
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim() || "#1e293b";
    ctx.font = "600 13px Manrope, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("总资产", cx, cy - 12);
    ctx.font = "700 15px IBM Plex Mono, monospace";
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2563eb";
    ctx.fillText(total.toLocaleString("zh-CN", { maximumFractionDigits: 0 }), cx, cy + 12);
  }
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
        filteredBenchmark = filteredBenchmark.filter((p) => new Date(p.time || p.date) >= cutoff);
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

    if (statsEl && pts.length > 1) {
      const vals = pts.map(p => p.value).filter(v => v != null && isFinite(v));
      if (vals.length >= 2) {
        const first = vals[0];
        const last = vals[vals.length - 1];
        const ret = ((last / first - 1) * 100);
        const peak = Math.max(...vals);
        const dd = ((peak - last) / peak * 100);

        let benchRet = null;
        let excessRet = null;
        if (benchPts.length >= 2) {
          const bFirst = benchPts[0].value;
          const bLast = benchPts[benchPts.length - 1].value;
          if (isFinite(bFirst) && isFinite(bLast) && bFirst !== 0) {
            benchRet = ((bLast / bFirst - 1) * 100);
            excessRet = ret - benchRet;
          }
        }

        let statsHtml = `
          <div class="dashboard-nav-stat">
            <span class="nav-stat-label">区间收益</span>
            <span class="nav-stat-value ${ret >= 0 ? "up" : "down"}">${fmtPct(ret)}</span>
          </div>
          <div class="dashboard-nav-stat">
            <span class="nav-stat-label">期间最高</span>
            <span class="nav-stat-value">${fmtNum(peak, 0)}</span>
          </div>
          <div class="dashboard-nav-stat">
            <span class="nav-stat-label">当前回撤</span>
            <span class="nav-stat-value ${dd > 0 ? "down" : ""}">${fmtPct(-dd)}</span>
          </div>
        `;
        if (benchRet !== null) {
          statsHtml += `
            <div class="dashboard-nav-stat">
              <span class="nav-stat-label">基准收益</span>
              <span class="nav-stat-value ${benchRet >= 0 ? "up" : "down"}">${fmtPct(benchRet)}</span>
            </div>
            <div class="dashboard-nav-stat">
              <span class="nav-stat-label">超额收益</span>
              <span class="nav-stat-value ${excessRet >= 0 ? "up" : "down"}">${fmtPct(excessRet)}</span>
            </div>
          `;
        }
        statsHtml += `
          <div class="dashboard-nav-stat">
            <span class="nav-stat-label">数据点</span>
            <span class="nav-stat-value">${vals.length}</span>
          </div>
        `;
        statsEl.innerHTML = statsHtml;
      }
    }

    if (benchPts.length >= 2) {
      renderDualLineChart(host, pts, benchPts, {
        labelA: "策略净值",
        labelB: "沪深300",
        emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
        disableZoom: false,
      }).then((chart) => {
        _navChart = chart;
      });
    } else {
      renderLineChart(host, pts, {
        color: "#2563eb",
        emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
        disableZoom: false,
      }).then((chart) => {
        _navChart = chart;
      });
    }
  }

function renderDrawdownChart(drawdownPoints) {
    const host = document.getElementById("dashboard-drawdown-chart");
    if (!host) return null;
    if (!drawdownPoints || !drawdownPoints.length) {
      host.innerHTML = `<div class="dashboard-empty">暂无回撤数据</div>`;
      return null;
    }
    const ctx = host.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = host.clientWidth || host.offsetWidth || 400;
    const h = host.clientHeight || host.offsetHeight || 200;
    host.width = w * dpr;
    host.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const padL = 50, padR = 12, padT = 16, padB = 28;
    const chartW = w - padL - padR;
    const chartH = h - padT - padB;

    const values = drawdownPoints.map(p => p.value);
    const minVal = Math.min(...values);
    const maxVal = 0;
    const range = maxVal - minVal || 1;

    const times = drawdownPoints.map(p => new Date(p.time || p.date).getTime());
    const tMin = Math.min(...times);
    const tMax = Math.max(...times);
    const tRange = tMax - tMin || 1;

    const xOf = (t) => padL + ((t - tMin) / tRange) * chartW;
    const yOf = (v) => padT + (1 - (v - minVal) / range) * chartH;

    ctx.fillStyle = "#eff6ff";
    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    drawdownPoints.forEach((p, i) => {
      const x = xOf(new Date(p.time || p.date).getTime());
      const y = yOf(p.value);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    const lastX = xOf(new Date(drawdownPoints[drawdownPoints.length - 1].time || drawdownPoints[drawdownPoints.length - 1].date).getTime());
    const firstX = xOf(new Date(drawdownPoints[0].time || drawdownPoints[0].date).getTime());
    const yBase = yOf(0);
    ctx.lineTo(lastX, yBase);
    ctx.lineTo(firstX, yBase);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    ctx.strokeStyle = "rgba(100,116,139,0.3)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padT + (i / 4) * chartH;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
    }

    const minIdx = values.indexOf(minVal);
    if (minIdx >= 0) {
      const minX = xOf(new Date(drawdownPoints[minIdx].time || drawdownPoints[minIdx].date).getTime());
      const minY = yOf(minVal);
      ctx.fillStyle = "#ef4444";
      ctx.beginPath();
      ctx.arc(minX, minY, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#ef4444";
      ctx.font = "600 11px Manrope, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(`最大回撤 ${fmtPct(minVal)}`, minX, minY - 8);
    }

    ctx.fillStyle = "#6b7280";
    ctx.font = "10px IBM Plex Mono, monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = maxVal - (i / 4) * range;
      const y = padT + (i / 4) * chartH;
      ctx.fillText(fmtPct(v), padL - 6, y + 3);
    }

    _drawdownChart = { ctx, host };
    return _drawdownChart;
  }

  function renderVarHistogram(histData) {
    const host = document.getElementById("dashboard-var-histogram");
    if (!host) return null;
    if (!histData || !histData.buckets || !histData.buckets.length) {
      host.innerHTML = `<div class="dashboard-empty">暂无VaR历史数据</div>`;
      return null;
    }
    const ctx = host.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = host.clientWidth || host.offsetWidth || 400;
    const h = host.clientHeight || host.offsetHeight || 200;
    host.width = w * dpr;
    host.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const padL = 50, padR = 12, padT = 16, padB = 28;
    const chartW = w - padL - padR;
    const chartH = h - padT - padB;

    const buckets = histData.buckets;
    const maxCount = Math.max(...buckets.map(b => b.count), 1);
    const n = buckets.length;
    const barW = (chartW / n) * 0.85;
    const gap = (chartW / n) * 0.15;

    const var95 = histData.var_95;
    const var99 = histData.var_99;

    buckets.forEach((b, i) => {
      const x = padL + i * (barW + gap);
      const barH = (b.count / maxCount) * chartH;
      const y = padT + chartH - barH;
      const isTail95 = var95 != null && b.range_end <= var95;
      const isTail99 = var99 != null && b.range_end <= var99;
      const color = isTail99 ? "#dc2626" : isTail95 ? "#f59e0b" : "#2563eb";
      ctx.fillStyle = color;
      ctx.fillRect(x, y, barW, barH);
    });

    if (var95 != null) {
      const v95Y = padT + chartH;
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(padL, v95Y);
      ctx.lineTo(w - padR, v95Y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#f59e0b";
      ctx.font = "600 11px Manrope, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`VaR 95% ${fmtPct(var95)}`, w - padR, v95Y - 4);
    }
    if (var99 != null) {
      const v99Y = padT + chartH;
      ctx.strokeStyle = "#dc2626";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 3]);
      ctx.beginPath();
      ctx.moveTo(padL, v99Y);
      ctx.lineTo(w - padR, v99Y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#dc2626";
      ctx.font = "600 11px Manrope, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`VaR 99% ${fmtPct(var99)}`, w - padR, v99Y - 18);
    }

    ctx.strokeStyle = "rgba(100,116,139,0.3)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padT + (i / 4) * chartH;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
    }

    ctx.fillStyle = "#6b7280";
    ctx.font = "10px IBM Plex Mono, monospace";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = maxCount - (i / 4) * maxCount;
      const y = padT + (i / 4) * chartH;
      ctx.fillText(String(Math.round(v)), padL - 6, y + 3);
    }

    _varChart = { ctx, host };
    return _varChart;
  }

  function renderFactorICSeries(factorData) {
    const host = document.getElementById("dashboard-factor-ic-chart");
    if (!host) return null;
    if (!factorData || !factorData.factors || !factorData.factors.length) {
      host.innerHTML = `<div class="dashboard-empty">暂无因子IC数据</div>`;
      return null;
    }
    const palette = ["#2563eb", "#059669", "#7c3aed", "#d97706", "#ef4444", "#0891b2", "#db2777", "#65a30d"];
    const seriesList = factorData.factors.map((f, i) => ({
      label: f.factor,
      color: palette[i % palette.length],
      points: f.ic_values.map(v => ({ time: v.time, value: v.value })),
    }));
    return renderMultiLineChart(host, seriesList, {
      emptyText: "暂无因子IC数据",
      zeroLine: true,
      disableZoom: false,
    }).then((chart) => {
      _factorICChart = chart;
      return chart;
    });
  }

  async function loadDashboardData(range = "30", showBenchmark = true) {
  const meta = document.getElementById("dashboard-meta");
  const updated = document.getElementById("dashboard-updated");
  if (meta) meta.textContent = "加载中…";

  const unwrap = (res, fallback = {}) => {
    if (!res || res.ok === false) {
      // apiFetch 失败：{ ok:false, data, error }；本地兜底也可能直接是 payload
      if (res && res.data && typeof res.data === "object") return { ...fallback, ...res.data, ok: false };
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
    const [marketData, kpis, riskData, navData, sectors, signals, allocation, exposure, drawdownData, varData] = await Promise.all([
        fetchDash(`/api/dashboard/market-overview`),
        fetchDash(`/api/dashboard/kpis`),
        fetchDash(`/api/dashboard/risk-metrics`),
        fetchDash(`/api/dashboard/nav-curve?range=${range}&benchmark=${benchParam}`, { points: [], benchmark_points: [] }),
        fetchDash(`/api/dashboard/sector-heatmap`, { sectors: [] }),
        fetchDash(`/api/dashboard/signals`, { signals: [] }),
        fetchDash(`/api/dashboard/allocation`, { sectors: [], total_value: 0 }),
        fetchDash(`/api/dashboard/factor-exposure`),
        fetchDash(`/api/dashboard/drawdown?range=${range}`, { points: [] }),
        fetchDash(`/api/dashboard/var-historical?range=${range}`),
      ]);

    // Render market overview
    renderMarketOverview(marketData);

    // Render KPIs
    const kpiHost = document.getElementById("dashboard-kpi-cards");
    if (kpiHost && kpis.ok !== false) {
      const items = [
        { key: "today_return", label: "今日收益", value: kpis.today_return, formatted: fmtPct(kpis.today_return), sub: "vs 昨收", sparkline: kpis.sparkline_today, direction: kpis.today_return },
        { key: "total_return", label: "累计收益", value: kpis.total_return, formatted: fmtPct(kpis.total_return), sub: kpis.period || "全部", sparkline: kpis.sparkline_total, direction: kpis.total_return },
        { key: "sharpe", label: "Sharpe", value: kpis.sharpe, formatted: fmtNum(kpis.sharpe, 2), sub: "年化", sparkline: kpis.sparkline_sharpe, direction: kpis.sharpe - 1 },
        { key: "max_dd", label: "最大回撤", value: kpis.max_drawdown, formatted: fmtPct(kpis.max_drawdown), sub: "历史极值", sparkline: kpis.sparkline_dd, direction: kpis.max_drawdown },
        { key: "win_rate", label: "胜率", value: kpis.win_rate, formatted: fmtPct(kpis.win_rate), sub: "交易笔数 " + (kpis.trade_count || 0), sparkline: kpis.sparkline_winrate, direction: kpis.win_rate - 50 },
      ];
      kpiHost.innerHTML = items.map(kpiCardHtml).join("");
      renderSparklines();
    } else if (kpiHost) {
      kpiHost.innerHTML = `<div class="dashboard-empty" style="grid-column:1/-1">暂无 KPI 数据 · 请先在交易执行页运行纸面</div>`;
    }

    // Render risk metrics
    renderRiskMetrics(riskData);

    // Render NAV chart
      if (navData && navData.points) {
        renderNavChart(navData.points, range, navData.benchmark || navData.benchmark_points || []);
      }

      // Render drawdown chart
      if (drawdownData && drawdownData.points) {
        renderDrawdownChart(drawdownData.points);
      }

      // Render VaR histogram
      if (varData && varData.ok !== false) {
        renderVarHistogram(varData);
      }

    // Render sector heatmap
    renderSectorHeatmap(sectors?.sectors || []);

    // Render leaderboard
    const strategies = kpis?.strategies || [];
    renderLeaderboard(strategies);

    // Render signals
    renderSignals(signals?.signals || []);

    // Render allocation
    renderAllocation(allocation);

    // Render factor exposure
    renderFactorExposure(exposure);

    if (meta) meta.textContent = "数据就绪 · 上次刷新 " + new Date().toLocaleTimeString("zh-CN");
    if (updated) updated.textContent = "更新于 " + new Date().toLocaleTimeString("zh-CN");
  } catch (err) {
    console.error("[Dashboard] load failed", err);
    if (meta) meta.textContent = "加载失败: " + (err.message || err);
  }
}

function bindEvents() {
  const refreshBtn = document.getElementById("dashboard-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      const activeRange = document.querySelector("#dashboard-nav-range .is-active");
      const range = activeRange ? activeRange.dataset.range : "30";
      loadDashboardData(range);
    });
  }

  const rangeTabs = document.getElementById("dashboard-nav-range");
  if (rangeTabs) {
    rangeTabs.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-range]");
      if (!btn) return;
      rangeTabs.querySelectorAll("button").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      loadDashboardData(btn.dataset.range);
    });
  }

  const benchToggle = document.getElementById("dashboard-benchmark-toggle");
  if (benchToggle) {
    benchToggle.addEventListener("change", () => {
      const activeRange = document.querySelector("#dashboard-nav-range .is-active");
      const range = activeRange ? activeRange.dataset.range : "30";
      const useBench = benchToggle.checked;
      loadDashboardData(range, useBench);
    });
  }
}

export function initDashboard(ctx) {
  if (_initiated) return;
  _initiated = true;
  bindEvents();
  const benchInit = document.getElementById("dashboard-benchmark-toggle")?.checked ?? true;
  loadDashboardData("30", benchInit);

  setInterval(() => {
    const activeRange = document.querySelector("#dashboard-nav-range .is-active");
    const range = activeRange ? activeRange.dataset.range : "30";
    const bench = document.getElementById("dashboard-benchmark-toggle")?.checked ?? true;
    loadDashboardData(range, bench);
  }, 60000);
}