/** Platform panel: audit / schedule. */
import { apiFetch } from "./api_client.js";

const KIND_ZH = {
  decision: "决策",
  schedule: "调度",
  alert: "告警",
  promote: "晋升",
  error: "失败",
  event: "事件",
};

const JOB_ZH = {
  paper_daily: "纸面日更",
  pre_market_ingest: "盘前 ingest",
  bars_warmup: "日线预热",
  minute_warmup: "分钟预热",
  fundamentals_warmup: "财务预热",
};

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatTs(raw, { compact = false } = {}) {
  if (raw == null || raw === "") return "";
  let d = null;
  const n = typeof raw === "number" ? raw : Number(raw);
  if (Number.isFinite(n) && n > 1e12) d = new Date(n);
  else if (Number.isFinite(n) && n > 1e9) d = new Date(n * 1000);
  else {
    const parsed = new Date(String(raw));
    if (!Number.isNaN(parsed.getTime())) d = parsed;
  }
  if (!d || Number.isNaN(d.getTime())) return String(raw);
  const full =
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  if (compact) return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return full;
}

function jobLabel(kind) {
  if (!kind) return "—";
  return JOB_ZH[kind] || String(kind);
}

export function initPlatform(ctx) {
  const meta = document.getElementById("platform-meta");
  if (!meta && !document.getElementById("schedule-paper-run")) return;

  const scheduleOut = document.getElementById("schedule-last-out");
  const alertList = document.getElementById("schedule-alert-list");

  function setMeta(text) {
    if (meta) meta.textContent = text;
  }

  function setKpi(key, value, empty, sub) {
    const card = document.querySelector(`.platform-kpi-card[data-kpi="${key}"]`);
    const val = document.getElementById(`platform-kpi-${key}`);
    const subEl = document.getElementById(`platform-kpi-${key}-sub`);
    if (val) val.textContent = value;
    if (subEl && sub != null) subEl.textContent = sub;
    if (card) card.classList.toggle("is-empty", !!empty);
  }

  function renderAlerts(alerts) {
    const list = Array.isArray(alerts) ? alerts : [];
    if (!alertList) return;
    if (!list.length) {
      alertList.innerHTML =
        '<li class="platform-item"><div class="platform-item-main"><span class="sub">暂无监控告警</span></div></li>';
      return;
    }
    alertList.innerHTML = list
      .slice(0, 12)
      .map((a) => {
        const msg =
          typeof a === "string" ? a : a.message || a.code || JSON.stringify(a);
        const lvl = typeof a === "object" && a.level ? a.level : "info";
        return (
          `<li class="platform-item">` +
          `<span class="platform-kind" data-kind="alert">${escapeHtml(lvl)}</span>` +
          `<div class="platform-item-main"><div class="name">${escapeHtml(msg)}</div></div>` +
          `</li>`
        );
      })
      .join("");
  }

  function renderScheduleKv(last) {
    if (!scheduleOut) return;
    if (!last) {
      scheduleOut.innerHTML = '<div class="platform-kv-empty">尚无 schedule_last_run.json</div>';
      return;
    }
    const blocks = last.risk_blocks;
    const alerts = last.monitor_alerts || (last.ops_report && last.ops_report.monitor_alerts) || [];
    const rows = [
      ["任务", jobLabel(last.kind), false],
      ["时间", formatTs(last.ts) || "—", false],
      ["策略", last.strategy_id || "—", false],
      ["成本", last.cost_model || "—", false],
      ["拦截", Array.isArray(blocks) ? String(blocks.length) : "—", Array.isArray(blocks) && blocks.length > 0],
      ["告警", String((alerts || []).length), (alerts || []).length > 0],
      ["拦买入", last.buys_blocked ? "是" : "否", !!last.buys_blocked],
    ];
    if (last.error) rows.push(["错误", String(last.error), true]);
    if (last.note) rows.push(["说明", String(last.note), false]);
    scheduleOut.innerHTML = rows
      .map(([k, v, warn]) => {
        const cls = warn ? " is-warn" : "";
        return `<div><dt>${escapeHtml(k)}</dt><dd class="${cls.trim()}">${escapeHtml(v)}</dd></div>`;
      })
      .join("");
  }

  function fillScheduleKpis(last, empty) {
    if (empty || !last) {
      setKpi("kind", "—", true);
      setKpi("when", "—", true);
      setKpi("alerts", "—", true);
      return;
    }
    const alerts =
      last.monitor_alerts || (last.ops_report && last.ops_report.monitor_alerts) || [];
    setKpi("kind", jobLabel(last.kind), false);
    setKpi("when", formatTs(last.ts, { compact: true }) || "—", !last.ts);
    setKpi("alerts", String(alerts.length), alerts.length === 0);
  }

  async function loadScheduleLast() {
    const res = await fetch("/api/schedule/last");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    const last = data.last || null;
    const empty = !!(data.empty || !last);
    renderScheduleKv(empty ? null : last);
    const alerts =
      (last && (last.monitor_alerts || (last.ops_report && last.ops_report.monitor_alerts))) ||
      [];
    renderAlerts(alerts);
    fillScheduleKpis(last, empty);
    setMeta(empty ? "尚无上次调度" : `上次调度 · ${jobLabel(last.kind)}`);
    return data;
  }

  async function runPaperDaily() {
    const buyEl = document.getElementById("schedule-paper-buy");
    setMeta("纸面日更运行中…");
    const res = await fetch("/api/schedule/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "paper_daily",
        strategy: "short_conservative",
        simulate_buy: !!(buyEl && buyEl.checked),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    renderAlerts(data.monitor_alerts || []);
    renderScheduleKv({
      kind: data.kind,
      ts: data.ts || Date.now() / 1000,
      strategy_id: data.strategy_id,
      cost_model: data.cost_model,
      risk_blocks: data.risk_blocks,
      monitor_alerts: data.monitor_alerts,
      buys_blocked: data.buys_blocked,
      note: data.note,
    });
    fillScheduleKpis(data, false);
    setMeta(
      `纸面日更完成 · 告警 ${(data.monitor_alerts || []).length} · 策略 ${data.strategy_id || "—"}`
    );
    await loadAuditTimeline().catch(() => {});
    return data;
  }

  async function loadAuditTimeline() {
    const list = document.getElementById("audit-timeline-list");
    if (!list) return null;
    const { ok, data, error } = await apiFetch("/api/audit/timeline?limit=40");
    if (!ok) {
      list.innerHTML =
        `<li class="platform-item"><div class="platform-item-main"><span class="sub">${escapeHtml(error || "加载失败")}</span></div></li>`;
      setKpi("events", "—", true);
      return data;
    }
    const items = data.items || [];
    setKpi("events", String(items.length), !items.length);
    if (!items.length) {
      list.innerHTML =
        '<li class="platform-item"><div class="platform-item-main"><span class="sub">暂无审计事件</span></div></li>';
      return data;
    }
    list.innerHTML = items
      .map((ev) => {
        const kind = ev.kind || "event";
        const title = ev.title || "—";
        const ts = formatTs(ev.ts);
        const sub = [ts, ev.detail].filter(Boolean).join(" · ");
        const href = String(ev.href || "").trim();
        const linked = href && href !== "/platform";
        const titleHtml = linked
          ? `<a href="${escapeHtml(href)}">${escapeHtml(title)}</a>`
          : escapeHtml(title);
        return (
          `<li class="platform-item">` +
          `<span class="platform-kind" data-kind="${escapeHtml(kind)}">${escapeHtml(KIND_ZH[kind] || kind)}</span>` +
          `<div class="platform-item-main">` +
          `<div class="name">${titleHtml}</div>` +
          (sub ? `<div class="sub">${escapeHtml(sub)}</div>` : "") +
          `</div></li>`
        );
      })
      .join("");
    return data;
  }

  async function clearAuditTimeline() {
    const ok = window.confirm(
      "清理时间线记录？将清空 DecisionRecord（decisions.jsonl）和最近出站告警快照。不改策略晋升、不删调度 last-run。不可恢复。"
    );
    if (!ok) return null;
    const { ok: reqOk, data, error } = await apiFetch("/api/audit/timeline/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!reqOk) throw new Error(error || "清理失败");
    const n = data.decisions_cleared || 0;
    const alertBit = data.alerts_last_cleared ? " · 已清告警快照" : "";
    setMeta(`已清理时间线 · DecisionRecord ${n} 条${alertBit}`);
    await loadAuditTimeline();
    return data;
  }

  async function openPlatformPanel() {
    await Promise.all([
      loadScheduleLast().catch(() => {}),
      loadAuditTimeline().catch(() => {}),
    ]);
  }

  const on = (id, type, fn) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(type, (e) => {
      e.preventDefault();
      fn().catch((err) => setMeta(String(err.message || err)));
    });
  };

  on("audit-timeline-refresh", "click", loadAuditTimeline);
  on("audit-timeline-clear", "click", clearAuditTimeline);
  on("schedule-paper-run", "click", runPaperDaily);
  on("schedule-last-refresh", "click", loadScheduleLast);

  ctx.openPlatformPanel = openPlatformPanel;
  ctx.reloadPlatform = openPlatformPanel;
}
