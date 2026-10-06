/** W4 · WebSocket 实况：纸面指标 / 健康 / 告警；断线复用 degrade banner。 */

import { noteApiFailure, noteApiSuccess, setApiDegradeBanner } from "./api_client.js";

let _ws = null;
let _retryMs = 2000;
let _timer = null;
let _lastSnap = null;
let _failStreak = 0;
let _pageUnloading = false;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/live`;
}

function reconnectBannerText() {
  if (_failStreak >= 3) {
    return "实况通道连不上 · 请确认已启动 python run_web.py（端口 8000）";
  }
  return "实况通道已断开 · 正在重连…";
}

function fmtNum(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function applyPaper(paper) {
  if (!paper) return;

  // 交易执行页顶部 compact 指标
  const followEl = document.getElementById("follow-stats");
  if (followEl && (document.body.dataset.page || "") === "follow") {
    const pct = paper.total_pnl_pct;
    const pnlCls =
      pct == null || Number(pct) === 0 ? "" : Number(pct) > 0 ? "up" : "down";
    const dd =
      paper.max_drawdown_pct != null ? `${Number(paper.max_drawdown_pct).toFixed(1)}%` : "—";
    const pnlText =
      pct == null
        ? "—"
        : `${Number(pct) >= 0 ? "+" : ""}${Number(pct).toFixed(2)}%`;
    followEl.innerHTML =
      `<div class="paper-stat follow-stat" title="现金 + 持仓市值"><span class="label">净值</span>` +
      `<span class="val">${fmtNum(paper.equity)}</span></div>` +
      `<div class="paper-stat follow-stat" title="可用现金余额"><span class="label">现金</span>` +
      `<span class="val">${fmtNum(paper.cash)}</span></div>` +
      `<div class="paper-stat follow-stat" title="累计盈亏；历史最大回撤 ${dd}"><span class="label">盈亏</span>` +
      `<span class="val ${pnlCls}">${pnlText}</span></div>` +
      `<div class="paper-stat follow-stat" title="当前持仓只数"><span class="label">持仓</span>` +
      `<span class="val">${
        paper.position_count != null ? `${paper.position_count} 只` : "—"
      }</span></div>`;
  }

  const hooks = window.__investmentLivePaperHooks;
  if (Array.isArray(hooks)) {
    for (const fn of hooks) {
      try {
        fn(paper);
      } catch (_) {
        /* ignore */
      }
    }
  }
  if (typeof window.__investmentOnLivePaper === "function") {
    try {
      window.__investmentOnLivePaper(paper);
    } catch (_) {
      /* ignore */
    }
  }
}

function applyHealth(health) {
  if (!health) return;
  const lamp = document.getElementById("risk-lamp");
  if (lamp) {
    lamp.classList.toggle("risk-lamp-ok", !!health.ok);
    lamp.classList.toggle("risk-lamp-warn", !health.ok);
  }
  if (typeof window.__investmentOnLiveHealth === "function") {
    try {
      window.__investmentOnLiveHealth(health);
    } catch (_) {
      /* ignore */
    }
  }
}

function applyAlerts(alerts) {
  if (typeof window.__investmentRefreshAlertBell === "function") {
    try {
      window.__investmentRefreshAlertBell();
    } catch (_) {
      /* ignore */
    }
  }
  if (alerts && !alerts.empty && (alerts.alert_count || 0) > 0) {
    const badge = document.getElementById("alert-bell-badge");
    const btn = document.getElementById("btn-alert-bell");
    if (badge && btn) {
      badge.hidden = false;
      badge.textContent = String(Math.min(alerts.alert_count, 9)) + (alerts.alert_count > 9 ? "+" : "");
      btn.classList.add("has-alerts");
    }
  }
}

function onMessage(ev) {
  let data;
  try {
    data = JSON.parse(ev.data);
  } catch (_) {
    return;
  }
  if (!data || data.type === "pong") return;
  _lastSnap = data;
  noteApiSuccess();
  if (data.paper) applyPaper(data.paper);
  if (data.health) applyHealth(data.health);
  if (data.alerts) applyAlerts(data.alerts);
}

function scheduleReconnect() {
  if (_pageUnloading) return;
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(() => {
    _retryMs = Math.min(_retryMs * 1.5, 30000);
    connectLiveWs();
  }, _retryMs);
}

export function connectLiveWs() {
  if (_pageUnloading) return null;
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
    return _ws;
  }
  try {
    _ws = new WebSocket(wsUrl());
  } catch (err) {
    _failStreak += 1;
    noteApiFailure("WebSocket 不可用");
    setApiDegradeBanner(true, reconnectBannerText());
    scheduleReconnect();
    return null;
  }
  _ws.onopen = () => {
    _retryMs = 2000;
    _failStreak = 0;
    noteApiSuccess();
    try {
      _ws.send(JSON.stringify({ type: "hello" }));
    } catch (_) {
      /* ignore */
    }
  };
  _ws.onmessage = onMessage;
  _ws.onerror = () => {
    noteApiFailure("实况通道异常");
  };
  _ws.onclose = () => {
    if (_pageUnloading) return;
    _failStreak += 1;
    setApiDegradeBanner(true, reconnectBannerText());
    scheduleReconnect();
  };
  return _ws;
}

export function getLastLiveSnapshot() {
  return _lastSnap;
}

export function initLiveWs() {
  window.addEventListener("beforeunload", () => {
    _pageUnloading = true;
    if (_timer) clearTimeout(_timer);
    try {
      if (_ws) _ws.close();
    } catch (_) {
      /* ignore */
    }
  });
  connectLiveWs();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (!_ws || _ws.readyState !== WebSocket.OPEN) connectLiveWs();
      else {
        try {
          _ws.send(JSON.stringify({ type: "refresh" }));
        } catch (_) {
          /* ignore */
        }
      }
    }
  });
  window.__investmentConnectLiveWs = connectLiveWs;
}
