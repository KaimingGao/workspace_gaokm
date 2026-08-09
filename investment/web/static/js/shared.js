/** Shared helpers for QuantLab web UI modules. */

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

/**
 * 统一加载/更新态：切换 is-busy + 文案（可选胶囊由 CSS 决定）。
 * @param {HTMLElement|null} el
 * @param {string} text
 * @param {{ busy?: boolean, error?: boolean, html?: boolean }} [opts]
 */
export function setUiBusy(el, text, { busy = true, error = false, html = false } = {}) {
  if (!el) return;
  el.classList.toggle("is-busy", !!busy && !error);
  el.classList.toggle("is-error", !!error);
  if (error) el.classList.remove("is-busy");
  if (html) el.innerHTML = text == null ? "" : String(text);
  else el.textContent = text == null ? "" : String(text);
  if (busy && !error) el.setAttribute("aria-busy", "true");
  else el.removeAttribute("aria-busy");
}

/**
 * 按钮进行中：禁用 + is-busy + aria-busy，结束后还原。
 * @template T
 * @param {HTMLElement|null} btn
 * @param {string} labelBusy
 * @param {() => Promise<T>} fn
 * @returns {Promise<T|undefined>}
 */
export async function withUiBusyButton(btn, labelBusy, fn) {
  if (!btn) return fn();
  const prev = btn.textContent;
  btn.disabled = true;
  btn.classList.add("is-busy");
  btn.setAttribute("aria-busy", "true");
  if (labelBusy) btn.textContent = labelBusy;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-busy");
    btn.removeAttribute("aria-busy");
    btn.textContent = prev;
  }
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
  const slabel = ops.strategy_label || "";
  const sver =
    ops.strategy_version != null && ops.strategy_version !== ""
      ? String(ops.strategy_version)
      : "—";
  const sDisp = slabel
    ? `${slabel}（${sid}）`
    : sid;
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
  const attr = ops.attribution || {};
  const fmtPct = (v) => {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  };
  const attrOk = !!attr.ok;
  const attrTitle = attr.methodology || attr.note || "纸面 Brinson lite · 非完整因子归因";
  const attrText = attrOk
    ? `选股 ${fmtPct(attr.selection_pct)} · 配置 ${fmtPct(attr.allocation_pct)} · 残差 ${fmtPct(attr.residual_pct)}`
    : "—";
  const topNames = Array.isArray(attr.top_contributors) ? attr.top_contributors : [];
  const topText = topNames.length
    ? topNames
        .slice(0, 3)
        .map(
          (t) =>
            `${t.stock_name || t.stock_code}:${fmtPct(t.contrib_pct)}`
        )
        .join(" ")
    : "";
  return (
    `<dl class="paper-ops-report-grid" aria-label="调仓五问">` +
    `<div><dt>策略</dt><dd title="${escapeHtml(sid)}">${escapeHtml(sDisp)} @ ${escapeHtml(sver)}</dd></div>` +
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
    `<div><dt>简化归因</dt><dd title="${escapeHtml(attrTitle)}${
      topText ? " · 贡献 " + topText : ""
    }">${escapeHtml(attrText)}</dd></div>` +
    (attrOk && attr.portfolio_return_pct != null
      ? `<div><dt>持仓收益</dt><dd>${escapeHtml(fmtPct(attr.portfolio_return_pct))}${
          attr.period_return_pct != null
            ? ` · 较昨快照 ${escapeHtml(fmtPct(attr.period_return_pct))}`
            : ""
        }</dd></div>`
      : "") +
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

