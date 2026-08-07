import { apiFetch } from "./api_client.js";
import { escapeHtml } from "./shared.js";
import { renderMultiLineChart } from "./lw_charts.js";

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

export async function loadAndRenderFactorICSeries(hostId, statsId, statusEl, lookback = 60) {
  const host = document.getElementById(hostId);
  const statsEl = document.getElementById(statsId);
  if (!host) return;
  if (statusEl) statusEl.textContent = "计算因子IC时序…";

  try {
    const data = await apiFetch(`/api/dashboard/factor-ic-series?lookback=${lookback}`);
    await renderFactorICSeriesChart(data, host);
    renderFactorICSeriesStats(data, statsEl);
    if (statusEl) statusEl.textContent = `因子IC时序计算完成 · ${data.factors?.length || 0} 个因子`;
  } catch (err) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">加载失败: ${err.message || err}</div>`;
    if (statusEl) statusEl.textContent = "加载失败";
  }
}

async function renderFactorICSeriesChart(data, host) {
  if (!host) return;
  if (!data || !data.factors || !data.factors.length) {
    host.innerHTML = `<div class="factor-corr-warnings-empty">暂无因子IC数据 · 请先运行「跑分组」</div>`;
    return;
  }

  const colors = ["#2563eb", "#059669", "#7c3aed", "#d97706", "#ef4444", "#0891b2", "#db2777", "#65a30d"];
  const seriesList = data.factors.map((f, i) => {
    const points = (f.ic_values || []).map(v => ({
      time: v.time || v.date,
      value: v.value ?? v.ic,
    }));
    return {
      label: f.factor,
      color: colors[i % colors.length],
      points,
    };
  });

  await renderMultiLineChart(host, seriesList, {
    emptyText: "因子IC序列不足",
    disableZoom: true,
    zeroLine: true,
  });
}

function renderFactorICSeriesStats(data, statsEl) {
  if (!statsEl) return;
  if (!data || !data.factors || !data.factors.length) {
    statsEl.innerHTML = "";
    return;
  }

  const rows = data.factors.map(f => {
    const vals = (f.ic_values || []).map(v => v.value ?? v.ic).filter(v => v != null && isFinite(v));
    const n = vals.length;
    if (!n) return null;
    const mean = vals.reduce((a, b) => a + b, 0) / n;
    const variance = vals.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
    const std = Math.sqrt(variance);
    const ir = std > 0 ? (mean / std) * Math.sqrt(252) : 0;
    const posRate = vals.filter(v => v > 0).length / n * 100;
    return { factor: f.factor, mean, ir, posRate, n };
  }).filter(Boolean);

  if (!rows.length) {
    statsEl.innerHTML = `<div class="factor-corr-warnings-empty">暂无有效IC数据</div>`;
    return;
  }

  rows.sort((a, b) => b.ir - a.ir);

  let html = `<table class="quant-weight-table" style="width:100%; border-collapse:collapse; font-size:0.78rem;">`;
  html += `<thead><tr style="border-bottom:1px solid var(--line);">
    <th style="text-align:left; padding:4px 8px;">因子</th>
    <th style="text-align:right; padding:4px 8px;">IC均值</th>
    <th style="text-align:right; padding:4px 8px;">IR(年化)</th>
    <th style="text-align:right; padding:4px 8px;">正收益率</th>
    <th style="text-align:right; padding:4px 8px;">样本数</th>
  </tr></thead><tbody>`;

  for (const r of rows) {
    const meanCls = r.mean >= 0 ? "is-positive" : "is-negative";
    const irCls = r.ir >= 0 ? "is-positive" : "is-negative";
    html += `<tr style="border-bottom:1px solid var(--line);">
      <td style="padding:4px 8px; font-weight:500;">${escapeHtml(r.factor)}</td>
      <td style="text-align:right; padding:4px 8px;" class="${meanCls}">${r.mean.toFixed(4)}</td>
      <td style="text-align:right; padding:4px 8px;" class="${irCls}">${r.ir.toFixed(2)}</td>
      <td style="text-align:right; padding:4px 8px;">${r.posRate.toFixed(1)}%</td>
      <td style="text-align:right; padding:4px 8px;">${r.n}</td>
    </tr>`;
  }

  html += `</tbody></table>`;
  statsEl.innerHTML = html;
}