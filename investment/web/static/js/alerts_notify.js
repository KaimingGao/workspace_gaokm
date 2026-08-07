/** 页内告警铃 + 可选浏览器 Notification（W0 推送尾巴）。 */

import { apiFetch } from "./api_client.js";

const SEEN_KEY = "investment_alert_seen_ts";

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function getSeenTs() {
  try {
    return Number(localStorage.getItem(SEEN_KEY) || 0) || 0;
  } catch (_) {
    return 0;
  }
}

function setSeenTs(ts) {
  try {
    localStorage.setItem(SEEN_KEY, String(ts || Date.now() / 1000));
  } catch (_) {
    /* ignore */
  }
}

function ensureBell() {
  let btn = document.getElementById("btn-alert-bell");
  if (btn) return btn;
  const right = document.querySelector(".topbar-right");
  if (!right) return null;
  btn = document.createElement("button");
  btn.type = "button";
  btn.id = "btn-alert-bell";
  btn.className = "icon-btn alert-bell";
  btn.title = "告警（点击查看 / 授权浏览器通知）";
  btn.setAttribute("aria-label", "告警");
  btn.innerHTML = `🔔<span class="alert-bell-badge" id="alert-bell-badge" hidden>0</span>`;
  const ai = document.getElementById("btn-ai-open");
  if (ai && ai.parentNode === right) right.insertBefore(btn, ai);
  else right.insertBefore(btn, right.firstChild);

  let pop = document.getElementById("alert-bell-pop");
  if (!pop) {
    pop = document.createElement("div");
    pop.id = "alert-bell-pop";
    pop.className = "alert-bell-pop";
    pop.hidden = true;
    pop.innerHTML =
      `<div class="alert-bell-pop-head">` +
      `<strong>告警</strong>` +
      `<button type="button" class="dialog-btn secondary" id="alert-bell-enable">启用浏览器通知</button>` +
      `<button type="button" class="dialog-btn secondary" id="alert-bell-close">关闭</button>` +
      `</div>` +
      `<ul id="alert-bell-list" class="alert-bell-list"></ul>` +
      `<p class="alert-bell-foot"><a href="/platform">系统设置 · 审计</a></p>`;
    document.body.appendChild(pop);
  }
  return btn;
}

function setBadge(n) {
  const badge = document.getElementById("alert-bell-badge");
  const btn = document.getElementById("btn-alert-bell");
  if (!badge || !btn) return;
  if (n > 0) {
    badge.hidden = false;
    badge.textContent = n > 9 ? "9+" : String(n);
    btn.classList.add("has-alerts");
  } else {
    badge.hidden = true;
    btn.classList.remove("has-alerts");
  }
}

function renderList(alerts) {
  const list = document.getElementById("alert-bell-list");
  if (!list) return;
  if (!alerts.length) {
    list.innerHTML = `<li class="alert-bell-empty">暂无出站告警</li>`;
    return;
  }
  list.innerHTML = alerts
    .slice(0, 12)
    .map((a) => {
      const msg =
        typeof a === "string" ? a : a.message || a.code || JSON.stringify(a);
      const lvl = typeof a === "object" && a.level ? a.level : "";
      return `<li><span class="msg">${escapeHtml(msg)}</span>${
        lvl ? `<span class="lvl">${escapeHtml(lvl)}</span>` : ""
      }</li>`;
    })
    .join("");
}

async function maybeNotify(payload) {
  if (!("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  const ts = Number(payload.ts) || 0;
  const seen = getSeenTs();
  if (ts && ts <= seen) return;
  const count = payload.alert_count || (payload.alerts || []).length || 0;
  if (!count) return;
  try {
    const n = new Notification("QuantLab 告警", {
      body: `${count} 条监控告警 · 来源 ${payload.source || "monitor"}`,
      tag: "investment-alert",
    });
    n.onclick = () => {
      window.focus();
      window.location.href = "/platform";
    };
  } catch (_) {
    /* ignore */
  }
  if (ts) setSeenTs(ts);
}

export async function refreshAlertBell() {
  ensureBell();
  const [alertsRes, healthRes] = await Promise.all([
    apiFetch("/api/alerts/last"),
    apiFetch("/api/daily/health"),
  ]);
  const alerts = alertsRes.ok ? alertsRes.data.alerts || [] : [];
  const issues = healthRes.ok ? healthRes.data.issues || [] : [];
  const count =
    (alertsRes.ok && !alertsRes.data.empty ? alertsRes.data.alert_count || alerts.length : 0) +
    issues.length;
  setBadge(count);
  const merged = [
    ...alerts,
    ...issues.map((x) => ({ message: String(x), level: "health" })),
  ];
  renderList(merged);
  if (alertsRes.ok && !alertsRes.data.empty) {
    await maybeNotify(alertsRes.data);
  }
}

export function initAlertBell() {
  const btn = ensureBell();
  if (!btn) return;

  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    const pop = document.getElementById("alert-bell-pop");
    if (!pop) return;
    const open = pop.hidden;
    pop.hidden = !open;
    if (open) {
      await refreshAlertBell();
      setSeenTs(Date.now() / 1000);
      setBadge(0);
    }
  });

  document.getElementById("alert-bell-close")?.addEventListener("click", () => {
    const pop = document.getElementById("alert-bell-pop");
    if (pop) pop.hidden = true;
  });

  document.getElementById("alert-bell-enable")?.addEventListener("click", async () => {
    if (!("Notification" in window)) {
      alert("当前浏览器不支持 Notification");
      return;
    }
    const perm = await Notification.requestPermission();
    const enableBtn = document.getElementById("alert-bell-enable");
    if (enableBtn) {
      enableBtn.textContent =
        perm === "granted" ? "已启用通知" : perm === "denied" ? "通知已拒绝" : "启用浏览器通知";
    }
  });

  refreshAlertBell().catch(() => {});
  window.setInterval(() => {
    if (document.visibilityState === "hidden") return;
    refreshAlertBell().catch(() => {});
  }, 120_000);

  window.__investmentRefreshAlertBell = refreshAlertBell;
}
