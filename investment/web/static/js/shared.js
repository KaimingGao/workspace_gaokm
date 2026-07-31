/** Shared helpers for Investment web UI modules. */

export function headers(ctx, extra = {}) {
  const h = { "Content-Type": "application/json", ...extra };
  if (ctx.sessionId) h["X-Session-Id"] = ctx.sessionId;
  return h;
}

export function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}


export function hideIntro(introEl) {
  if (introEl) introEl.hidden = true;
}

export function showIntro(introEl) {
  if (introEl) introEl.hidden = false;
}

export function scrollToBottom(stageEl) {
  requestAnimationFrame(() => {
    stageEl.scrollTop = stageEl.scrollHeight;
  });
}

export function formatDailySteps(data) {
  return (data.steps || []).map((s) => `${s.name}:${s.ok ? "OK" : "FAIL"}`).join(" · ");
}


export async function runDaily({ paperRun = false, paperBuy = false, evalMock = false, evalAgent = false } = {}) {
  const res = await fetch("/api/daily/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      paper_run: paperRun,
      paper_buy: paperBuy,
      eval_mock: evalMock,
      eval_agent: evalAgent,
    }),
  });
  const data = await res.json();
  if (!res.ok && res.status !== 422) {
    throw new Error(data.detail || res.statusText);
  }
  return data;
}


export function absoluteShareUrl(path) {
  return new URL(path, window.location.origin).href;
}


export async function copyShareUrl(path) {
  const url = absoluteShareUrl(path);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(url);
    return url;
  }
  const ta = document.createElement("textarea");
  ta.value = url;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
  return url;
}


export function renderReadmeLinksHtml(readmeIndex, { summary = "" } = {}) {
  const entries = readmeIndex?.entries || [];
  const linkItems = entries
    .map(
      (entry) =>
        `<li><button type="button" class="readme-link" data-readme-dir="${entry.dir}">${entry.dir}</button></li>`
    )
    .join("");
  const summaryLine = summary
    ? `<div class="quant-package-summary">${summary}</div>`
    : "";
  return `${summaryLine}<ul>${linkItems}</ul>`;
}


export function attachReadmeLinkHandler(container, ctx) {
  if (!container || container.dataset.readmeBound) return;
  container.dataset.readmeBound = "1";
  container.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-readme-dir]");
    if (!btn) return;
    e.preventDefault();
    if (typeof ctx.openReadmeViewer === "function") {
      ctx.openReadmeViewer(btn.getAttribute("data-readme-dir"));
    }
  });
}

export async function postQuantCiEval() {
  const res = await fetch("/api/evals/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      use_mock: true,
      with_agent: false,
      with_presets: true,
      quant_only: true,
    }),
  });
  const data = await res.json();
  if (!res.ok && res.status !== 422) {
    throw new Error(data.detail || res.statusText);
  }
  return data;
}


export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  downloadBlob(blob, filename);
}

/** 调仓五问 HTML（follow 等页共用） */
export function formatOpsReportHtml(ops) {
  if (!ops || typeof ops !== "object") return "";
  const sid = ops.strategy_id || "—";
  const sver =
    ops.strategy_version != null && ops.strategy_version !== ""
      ? String(ops.strategy_version)
      : "—";
  const cost =
    ops.cost_model === "simple_cn"
      ? "A股简化"
      : ops.cost_model === "zero"
        ? "零成本"
        : ops.cost_model || "—";
  const dq = ops.data_quality || {};
  const fb = Number(ops.fallback_count ?? dq.fallback_count ?? 0);
  const fbCls = fb > 0 ? "is-warn" : "";
  const fbText =
    fb > 0
      ? `降级 ${fb} 只`
      : dq.count != null
        ? `正常 · ${dq.count} 只`
        : "—";
  const blocks = Array.isArray(ops.risk_blocks) ? ops.risk_blocks : [];
  const alerts = Array.isArray(ops.monitor_alerts) ? ops.monitor_alerts : [];
  const blocked = !!ops.buys_blocked || blocks.length > 0;
  const riskText = blocked
    ? blocks.length
      ? `拦截 · ${blocks.length} 条`
      : "拦截加仓"
    : "通过";
  const alertText = alerts.length
    ? alerts
        .slice(0, 3)
        .map((a) => (typeof a === "string" ? a : a.message || a.code || ""))
        .filter(Boolean)
        .join("；")
    : "无";
  const tw = ops.target_weights || {};
  const twKeys = Object.keys(tw);
  const twText = twKeys.length
    ? twKeys
        .slice(0, 4)
        .map((k) => `${k}:${Number(tw[k]).toFixed(1)}%`)
        .join(" ")
    : "—";
  const lim = ops.risk_limits || (ops.optimize && ops.optimize.limits) || {};
  const limText =
    lim.effective_max_position_pct != null
      ? `单票≤${lim.effective_max_position_pct}%` +
        (lim.max_position_pct != null &&
        Number(lim.effective_max_position_pct) !== Number(lim.max_position_pct)
          ? `（基线${lim.max_position_pct}%）`
          : "") +
        ` · 行业≤${lim.effective_max_sector_pct ?? lim.max_sector_pct ?? "—"}% · ≤${lim.max_positions ?? "—"}只`
      : lim.max_position_pct != null
        ? `单票≤${lim.max_position_pct}% · 行业≤${lim.max_sector_pct ?? "—"}% · ≤${lim.max_positions ?? "—"}只`
        : "—";
  const opt = ops.optimize || {};
  const vs = opt.vol_scale || {};
  const volText = vs.high_vol
    ? `高波×${vs.scale ?? "—"}`
    : vs.scale != null && Number(vs.scale) < 0.999
      ? `×${vs.scale}`
      : opt.weight_mode === "score_budget"
        ? "分数预算"
        : opt.weight_mode || "—";
  const mm = ops.monitor_metrics || {};
  const cov = mm.sector_coverage || {};
  const covText =
    cov.total != null
      ? `${cov.mapped ?? 0}/${cov.total}（${
          cov.coverage != null ? `${Math.round(Number(cov.coverage) * 100)}%` : "—"
        }）`
      : "—";
  const exp = ops.exposure || {};
  const secRows = Array.isArray(exp.sectors) ? exp.sectors : [];
  const overSec = exp.over_limit_sectors || [];
  const expText = secRows.length
    ? secRows
        .slice(0, 4)
        .map((r) => `${r.name}:${Number(r.weight_pct).toFixed(0)}%`)
        .join(" ") + (overSec.length ? ` · 超限 ${overSec.join("/")}` : "")
    : "—";
  const sa = ops.source_audit || {};
  const saFb = Number(sa.fallback_count || 0);
  const saText =
    sa.status || saFb
      ? `${sa.status || "—"} · fallback ${saFb}`
      : "—";
  const saCls = saFb > 0 || sa.status === "bad" ? "is-warn" : "";
  return (
    `<dl class="paper-ops-report-grid" aria-label="调仓五问">` +
    `<div><dt>策略</dt><dd>${escapeHtml(sid)} @ ${escapeHtml(sver)}</dd></div>` +
    `<div><dt>成本</dt><dd>${escapeHtml(cost)}</dd></div>` +
    `<div><dt>数据质量</dt><dd class="${fbCls}">${escapeHtml(fbText)}</dd></div>` +
    `<div><dt>源审计</dt><dd class="${saCls}" title="${escapeHtml(
      sa.mismatch_hint || ""
    )}">${escapeHtml(saText)}</dd></div>` +
    `<div><dt>风控</dt><dd class="${blocked ? "is-block" : ""}">${escapeHtml(riskText)}</dd></div>` +
    `<div><dt>监控告警</dt><dd class="${alerts.length ? "is-warn" : ""}">${escapeHtml(alertText)}</dd></div>` +
    `<div><dt>滚动 IC</dt><dd class="${mm.rolling_ic != null && Number(mm.rolling_ic) < 0.02 ? "is-warn" : ""}">${escapeHtml(
      mm.rolling_ic != null ? Number(mm.rolling_ic).toFixed(3) : "—"
    )}</dd></div>` +
    `<div><dt>行业覆盖</dt><dd class="${
      cov.coverage != null && Number(cov.coverage) < 0.5 ? "is-warn" : ""
    }">${escapeHtml(covText)}</dd></div>` +
    `<div><dt>行业敞口</dt><dd class="${overSec.length ? "is-block" : ""}" title="${escapeHtml(expText)}">${escapeHtml(expText)}</dd></div>` +
    `<div><dt>目标权重</dt><dd title="${escapeHtml(twText)}">${escapeHtml(twText)}</dd></div>` +
    `<div><dt>策略限额</dt><dd>${escapeHtml(limText)}</dd></div>` +
    `<div><dt>仓位预算</dt><dd class="${vs.high_vol ? "is-warn" : ""}">${escapeHtml(volText)}</dd></div>` +
    `</dl>` +
    (blocks.length
      ? `<ul class="paper-ops-report-alerts">${blocks
          .slice(0, 5)
          .map((b) => `<li class="down">${escapeHtml(String(b))}</li>`)
          .join("")}</ul>`
      : "") +
    (alerts.length
      ? `<ul class="paper-ops-report-alerts">${alerts
          .slice(0, 5)
          .map((a) => {
            const msg =
              typeof a === "string" ? a : a.message || a.code || JSON.stringify(a);
            return `<li class="is-warn">${escapeHtml(String(msg))}</li>`;
          })
          .join("")}</ul>`
      : "")
  );
}

function cssVar(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (_) {
    return fallback;
  }
}

export function drawEquityChart(canvas, curve, emptyText) {
  if (!canvas) return;
  const g = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 360;
  const h = canvas.clientHeight || 120;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);

  const axis = cssVar("--line", "#e5e7eb");
  const muted = cssVar("--ink-3", "#9ca3af");
  const up = cssVar("--color-up", "#f5222d");
  const down = cssVar("--color-down", "#52c41a");

  const pts = (curve || [])
    .map((p) => ({ y: Number(p.equity) }))
    .filter((p) => Number.isFinite(p.y));
  if (pts.length < 2) {
    g.fillStyle = muted;
    g.font = "12px Manrope, sans-serif";
    g.fillText(emptyText || "暂无曲线", 12, h / 2);
    return;
  }

  const ys = pts.map((p) => p.y);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 12;
  const plotW = w - pad * 2;
  const plotH = h - pad * 2;
  const range = maxY - minY || 1;
  const first = pts[0].y;
  const last = pts[pts.length - 1].y;
  const stroke = last >= first ? up : down;

  g.strokeStyle = axis;
  g.beginPath();
  g.moveTo(pad, pad);
  g.lineTo(pad, h - pad);
  g.lineTo(w - pad, h - pad);
  g.stroke();

  g.strokeStyle = stroke;
  g.lineWidth = 2;
  g.beginPath();
  pts.forEach((p, i) => {
    const x = pad + (i / (pts.length - 1)) * plotW;
    const y = pad + plotH - ((p.y - minY) / range) * plotH;
    if (i === 0) g.moveTo(x, y);
    else g.lineTo(x, y);
  });
  g.stroke();

  g.fillStyle = muted;
  g.font = "10px Manrope, sans-serif";
  g.fillText(String(Math.round(minY)), 4, h - pad);
  g.fillText(String(Math.round(maxY)), 4, pad + 4);
}


export function drawDualEquityChart(canvas, seriesA, seriesB, emptyText) {
  if (!canvas) return;
  const g = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 360;
  const h = canvas.clientHeight || 120;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);

  const norm = (curve, key) =>
    (curve || [])
      .map((p) => Number(p[key] ?? p.equity_norm ?? p.equity))
      .filter((y) => Number.isFinite(y));
  const a = norm(seriesA, "equity_norm");
  const b = norm(seriesB, "equity");
  const all = [...a, ...b];
  if (all.length < 2) {
    g.fillStyle = "#9ca3af";
    g.font = "12px Manrope, sans-serif";
    g.fillText(emptyText || "暂无对照曲线", 12, h / 2);
    return;
  }

  const minY = Math.min(...all);
  const maxY = Math.max(...all);
  const pad = 12;
  const plotW = w - pad * 2;
  const plotH = h - pad * 2;
  const range = maxY - minY || 1;

  const drawLine = (pts, color) => {
    if (pts.length < 2) return;
    g.strokeStyle = color;
    g.lineWidth = 2;
    g.beginPath();
    pts.forEach((y, i) => {
      const x = pad + (i / (pts.length - 1)) * plotW;
      const py = pad + plotH - ((y - minY) / range) * plotH;
      if (i === 0) g.moveTo(x, py);
      else g.lineTo(x, py);
    });
    g.stroke();
  };

  g.strokeStyle = "#e5e7eb";
  g.beginPath();
  g.moveTo(pad, pad);
  g.lineTo(pad, h - pad);
  g.lineTo(w - pad, h - pad);
  g.stroke();

  drawLine(a, "#2563eb");
  drawLine(b, "#059669");
}

