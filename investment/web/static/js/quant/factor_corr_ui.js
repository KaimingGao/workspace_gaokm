import { apiFetch } from "../api_client.js";
import { escapeHtml } from "../shared.js";
import { renderMultiLineChart } from "../lw_charts.js";

function corrColor(v) {
  if (v == null || !isFinite(v)) return "var(--line)";
  const clamped = Math.max(-1, Math.min(1, v));
  if (clamped >= 0) {
    const alpha = clamped * 0.6;
    return `rgba(239, 68, 68, ${alpha.toFixed(2)})`;
  } else {
    const alpha = Math.abs(clamped) * 0.6;
    return `rgba(59, 130, 246, ${alpha.toFixed(2)})`;
  }
}

function corrTextColor(v) {
  if (v == null || !isFinite(v)) return "var(--ink-3)";
  return Math.abs(v) > 0.5 ? "#fff" : "var(--ink)";
}

function renderFactorCorrHeatmap(data, hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (!data || !data.success || !data.factors || !data.factors.length) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">暂无因子相关性数据 · 请先运行「跑分组」</div>`;
    return;
  }

  const factors = data.factors;
  const matrix = data.matrix || {};

  // Build the grid
  const size = factors.length;
  const cellSize = 48;
  const labelSize = 80;

  let html = `<div class="factor-corr-heatmap" style="grid-template-columns: ${labelSize}px repeat(${size}, ${cellSize}px); max-width: ${labelSize + size * (cellSize + 2)}px">`;

  // Header row
  html += `<div class="factor-corr-label" style="grid-column:1/${size + 2}; display:grid; grid-template-columns:${labelSize}px repeat(${size}, ${cellSize}px); gap:2px; margin-bottom:2px;">`;
  html += `<span></span>`;
  for (const f of factors) {
    html += `<span class="factor-corr-label" title="${escapeHtml(f)}">${escapeHtml(f.length > 6 ? f.slice(0, 5) + "…" : f)}</span>`;
  }
  html += `</div>`;

  // Data rows
  for (const rowFactor of factors) {
    html += `<span class="factor-corr-label" title="${escapeHtml(rowFactor)}" style="width:${labelSize}px; text-align:right; padding-right:8px;">${escapeHtml(rowFactor.length > 6 ? rowFactor.slice(0, 5) + "…" : rowFactor)}</span>`;
    for (const colFactor of factors) {
      const val = matrix[rowFactor]?.[colFactor];
      const display = val != null ? val.toFixed(2) : "—";
      const bg = corrColor(val);
      const fg = corrTextColor(val);
      const title = `${rowFactor} ↔ ${colFactor}: ${display}`;
      html += `<div class="factor-corr-cell" style="background:${bg}; color:${fg};" title="${escapeHtml(title)}">${display}</div>`;
    }
  }
  html += `</div>`;

  // Color scale legend
  html += `<div class="corr-scale"><span>-1</span><div class="corr-scale-bar"></div><span>+1</span></div>`;

  // Warnings
  if (data.warnings && data.warnings.length) {
    html += `<div class="factor-corr-warnings">⚠ ${data.warnings.map(w => escapeHtml(w.message || w.type || "")).join("<br>")}</div>`;
  }

  host.innerHTML = html;
}

function renderFactorIR(data, hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (!data || !data.factors || !data.factors.length) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">暂无因子 IR 数据 · 请先运行「跑分组」</div>`;
    return;
  }

  const factors = data.factors.slice(0, 12);
  const absIRs = factors.map(f => Math.abs(f.ir_annual || 0));
  const maxAbsIR = Math.max(...absIRs, 0.05);
  const hasAnySignal = absIRs.some(v => v > 0.01);

  if (!hasAnySignal) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">所有因子 IR 接近零 · 暂无有效信号</div>`;
    return;
  }

  let html = `<div class="factor-ir-bar">`;
  for (const f of factors) {
    const ir = f.ir_annual || 0;
    const width = ir === 0 ? 0 : Math.max(2, Math.abs(ir) / maxAbsIR * 100);
    const isPos = ir >= 0;
    html += `
      <div class="factor-ir-row">
        <span class="factor-ir-name" title="${escapeHtml(f.factor)}">${escapeHtml(f.factor.length > 8 ? f.factor.slice(0, 7) + "…" : f.factor)}</span>
        <div class="factor-ir-bar-track">
          <div class="factor-ir-bar-fill ${isPos ? "is-positive" : "is-negative"}" style="width:${width.toFixed(1)}%"></div>
        </div>
        <span class="factor-ir-value">${ir >= 0 ? "+" : ""}${ir.toFixed(2)}</span>
        <span class="factor-ir-rate">${f.positive_rate?.toFixed(0) || 0}%</span>
      </div>
    `;
  }
  html += `</div>`;

  html += `<div style="font-size:0.66rem; color:var(--ink-3); padding:4px 10px;">
    ${escapeHtml(data.note || "")} · IR = IC均值/IC标准差 × √(252/horizon)
  </div>`;

  host.innerHTML = html;
}

export async function loadAndRenderFactorCorr(hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  host.innerHTML = `<div class="quant-fingerprint is-busy">计算因子相关性…</div>`;
  try {
    const data = await apiFetch("/api/quant/factor-corr?threshold=0.7");
    renderFactorCorrHeatmap(data, hostId);
  } catch (err) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">加载失败: ${err.message || err}</div>`;
  }
}

export async function loadAndRenderFactorIR(hostId) {
  const host = document.getElementById(hostId);
  if (!host) return;
  host.innerHTML = `<div class="quant-fingerprint is-busy">计算因子 IR…</div>`;
  try {
    const data = await apiFetch("/api/quant/factor-ir?horizon_days=3");
    renderFactorIR(data, hostId);
  } catch (err) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">加载失败: ${err.message || err}</div>`;
  }
}

const IC_SERIES_COLORS = [
  "#2563eb",
  "#059669",
  "#7c3aed",
  "#d97706",
  "#ef4444",
  "#0891b2",
  "#db2777",
  "#65a30d",
];

function _icPointValues(f) {
  return (f.ic_values || [])
    .map((v) => {
      if (v != null && typeof v === "object") {
        const n = Number(v.value ?? v.ic);
        return Number.isFinite(n) ? n : null;
      }
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    })
    .filter((n) => n != null);
}

function renderFactorICSeriesKpis(data) {
  const host = document.getElementById("quant-ic-series-kpis");
  if (!host) return;
  const s = (data && data.summary) || {};
  const factors = (data && data.factors) || [];
  if (!factors.length) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  const fmt = (v, digits = 4) =>
    v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);
  const cluster =
    data.cluster_label != null
      ? `组 ${escapeHtml(String(data.cluster_label))}`
      : escapeHtml(String(data.source || "proxy"));
  host.hidden = false;
  host.innerHTML = `
    <div class="quant-ic-kpi"><span class="quant-ic-kpi-label">IC均值</span><span class="quant-ic-kpi-value">${fmt(s.ic_mean)}</span></div>
    <div class="quant-ic-kpi"><span class="quant-ic-kpi-label">IR均值</span><span class="quant-ic-kpi-value">${fmt(s.ir_mean, 2)}</span></div>
    <div class="quant-ic-kpi"><span class="quant-ic-kpi-label">正IC占比</span><span class="quant-ic-kpi-value">${fmt(s.positive_ratio, 1)}%</span></div>
    <div class="quant-ic-kpi"><span class="quant-ic-kpi-label">样本日</span><span class="quant-ic-kpi-value">${s.day_count ?? "—"}</span></div>
    <div class="quant-ic-kpi"><span class="quant-ic-kpi-label">范围</span><span class="quant-ic-kpi-value">${cluster}</span></div>
  `;
}

export async function loadAndRenderFactorICSeries(hostId, statsId, statusEl, lookback = 60) {
  const host = document.getElementById(hostId);
  const statsEl = document.getElementById(statsId);
  if (!host) return;
  if (statusEl) statusEl.textContent = "计算时序对比…";

  try {
    const data = await apiFetch(`/api/dashboard/factor-ic-series?lookback=${lookback}`);
    renderFactorICSeriesKpis(data);
    await renderFactorICSeriesChart(data, host);
    renderFactorICSeriesStats(data, statsEl);
    const n = data.factors?.length || 0;
    const tag = data.cluster_label ? `组 ${data.cluster_label}` : data.source || "";
    if (statusEl) {
      statusEl.textContent = n
        ? `完成 · ${n} 因子${tag ? ` · ${tag}` : ""}`
        : data.note || "暂无数据";
    }
  } catch (err) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">加载失败: ${escapeHtml(String(err.message || err))}</div>`;
    const kpis = document.getElementById("quant-ic-series-kpis");
    if (kpis) {
      kpis.hidden = true;
      kpis.innerHTML = "";
    }
    if (statsEl) statsEl.innerHTML = "";
    if (statusEl) statusEl.textContent = "加载失败";
  }
}

async function renderFactorICSeriesChart(data, host) {
  if (!host) return;
  if (!data || !data.factors || !data.factors.length) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">暂无 IC 时序 · 请先运行「跑分组」</div>`;
    return;
  }

  const seriesList = data.factors.map((f, i) => {
    const points = (f.ic_values || []).map((v, idx) => {
      if (v != null && typeof v === "object") {
        return {
          time: v.time || v.date || idx + 1,
          value: v.value ?? v.ic,
        };
      }
      return { time: idx + 1, value: v };
    });
    return {
      label: f.label || f.factor,
      color: IC_SERIES_COLORS[i % IC_SERIES_COLORS.length],
      lineWidth: i === 0 ? 2.5 : 2,
      points,
    };
  });

  const meanLine =
    data.summary && data.summary.ic_mean != null
      ? Number(data.summary.ic_mean)
      : null;

  await renderMultiLineChart(host, seriesList, {
    emptyText: "IC 序列不足",
    disableZoom: true,
    zeroLine: true,
    meanLine: Number.isFinite(meanLine) ? meanLine : null,
    meanLineTitle: "IC均值",
  });
}

function renderFactorICSeriesStats(data, statsEl) {
  if (!statsEl) return;
  if (!data || !data.factors || !data.factors.length) {
    statsEl.innerHTML = "";
    return;
  }

  const rows = data.factors
    .map((f) => {
      const vals = _icPointValues(f);
      const n = vals.length || Number(f.sample_count) || 0;
      const mean =
        f.ic_mean != null && Number.isFinite(Number(f.ic_mean))
          ? Number(f.ic_mean)
          : n
            ? vals.reduce((a, b) => a + b, 0) / n
            : null;
      const ir =
        f.ic_ir_annual != null && Number.isFinite(Number(f.ic_ir_annual))
          ? Number(f.ic_ir_annual)
          : f.ic_ir != null
            ? Number(f.ic_ir)
            : null;
      const posRate =
        f.positive_ratio != null && Number.isFinite(Number(f.positive_ratio))
          ? Number(f.positive_ratio)
          : n
            ? (vals.filter((v) => v > 0).length / n) * 100
            : null;
      if (mean == null && ir == null) return null;
      return {
        factor: f.label || f.factor,
        key: f.factor,
        mean: mean ?? 0,
        ir: ir ?? 0,
        posRate: posRate ?? 0,
        n,
      };
    })
    .filter(Boolean);

  if (!rows.length) {
    statsEl.innerHTML = `<div class="factor-corr-warnings-empty">暂无有效 IC</div>`;
    return;
  }

  rows.sort((a, b) => Math.abs(b.ir) - Math.abs(a.ir));

  let html = `<div class="quant-ic-stats-head">因子排序 · |IR|</div>`;
  html += `<table class="quant-weight-table quant-ic-stats-table">`;
  html += `<thead><tr>
    <th>因子</th>
    <th class="num">IC</th>
    <th class="num">IR</th>
    <th class="num">正%</th>
  </tr></thead><tbody>`;

  for (const r of rows) {
    const meanCls = r.mean >= 0 ? "is-positive" : "is-negative";
    const irCls = r.ir >= 0 ? "is-positive" : "is-negative";
    html += `<tr>
      <td title="${escapeHtml(r.key)}">${escapeHtml(r.factor)}</td>
      <td class="num ${meanCls}">${r.mean.toFixed(3)}</td>
      <td class="num ${irCls}">${r.ir.toFixed(2)}</td>
      <td class="num">${r.posRate.toFixed(0)}</td>
    </tr>`;
  }

  html += `</tbody></table>`;
  if (data.note) {
    html += `<p class="quant-ic-stats-note">${escapeHtml(String(data.note))}</p>`;
  }
  statsEl.innerHTML = html;
}