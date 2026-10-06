/** 持仓表「涨跌」列：悬停懒加载「涨跌会话日」5m K 线。 */

import { escapeText, fmtPct } from "./fmt.js?v=p1737";
import { miniKlineSvgFromBars } from "./t0_table.js?v=p2746";

const _cache = new Map();

function labelToHm(label) {
  const s = String(label || "").trim();
  if (/^\d{1,2}:\d{2}$/.test(s)) return s.replace(":", "");
  if (/^\d{3,4}$/.test(s)) return s.padStart(4, "0");
  return s.replace(":", "") || "0000";
}

/** API day_bars → miniKline 点 [hm,o,h,l,c,flag]。 */
export function minuteBarsToKlinePts(dayBars) {
  const rows = Array.isArray(dayBars) ? dayBars : [];
  const out = [];
  for (const b of rows) {
    const c = Number(b.close);
    if (!(c > 0)) continue;
    const o = Number(b.open);
    const h = Number(b.high);
    const l = Number(b.low);
    out.push([
      labelToHm(b.label),
      o > 0 ? o : c,
      h > 0 ? h : c,
      l > 0 ? l : c,
      c,
      0,
    ]);
  }
  return out;
}

function placeTip(tip, anchor) {
  if (!tip || !anchor) return;
  const pad = 8;
  const maxH = Math.max(120, window.innerHeight - pad * 2);
  tip.style.maxHeight = `${maxH}px`;
  const rect = anchor.getBoundingClientRect();
  let tipRect = tip.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  left = Math.max(pad, Math.min(left, window.innerWidth - tipRect.width - pad));
  let top = rect.bottom + 6;
  tipRect = tip.getBoundingClientRect();
  if (top + tipRect.height > window.innerHeight - pad) {
    const above = rect.top - tipRect.height - 6;
    top = above >= pad ? above : pad;
  }
  const h = tip.offsetHeight || tipRect.height;
  top = Math.max(pad, Math.min(top, window.innerHeight - h - pad));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function parsePayload(el) {
  if (!el) return null;
  const codeAttr = String(el.getAttribute("data-hold-chg-code") || "").trim();
  if (codeAttr) {
    const chgRaw = el.getAttribute("data-hold-chg-num");
    let chg = null;
    if (chgRaw != null && chgRaw !== "") {
      const n = Number(chgRaw);
      chg = Number.isFinite(n) ? n : null;
    }
    return {
      code: codeAttr,
      name: String(el.getAttribute("data-hold-chg-name") || "").trim(),
      asof: String(el.getAttribute("data-hold-chg-asof") || "").trim().slice(0, 10),
      chg,
    };
  }
  const raw = el.getAttribute("data-hold-chg-tip");
  if (!raw) return null;
  try {
    const p = JSON.parse(raw);
    if (p && typeof p === "object") return p;
  } catch (_) {
    /* 兼容仅 code 字符串 */
  }
  return { code: String(raw).trim() };
}

export function buildHoldChgTipShell(el) {
  const p = parsePayload(el) || {};
  const code = String(p.code || "").trim();
  const name = String(p.name || "").trim();
  const asof = String(p.asof || "").trim().slice(0, 10);
  const chg = p.chg;
  const chgText =
    chg != null && Number.isFinite(Number(chg))
      ? fmtPct(chg, { signed: true })
      : String(el?.textContent || "").trim() || "—";
  const title = [name, code].filter(Boolean).join(" · ") || "涨跌";
  const dayLabel = asof || "涨跌日";
  return (
    `<div class="paper-t0-process-tip-inner paper-hold-chg-tip-inner">` +
    `<header class="paper-t0-process-tip-head">` +
    `<span class="paper-t0-process-tip-badge">${escapeText(chgText)}</span>` +
    `<span class="paper-t0-process-tip-eyebrow">涨跌日 · 5m K</span>` +
    `</header>` +
    `<p class="paper-t0-process-tip-lead">${escapeText(title)} · ${escapeText(
      dayLabel
    )} · 相对昨收</p>` +
    `<div class="paper-t0-process-tip-chart" data-hold-chg-chart data-code="${escapeText(
      code
    )}" data-asof="${escapeText(asof)}">` +
    `<p class="paper-t0-process-tip-empty">加载 5m K…</p>` +
    `</div>` +
    `<p class="paper-t0-process-tip-foot">涨跌日 5m · 拉取中…</p>` +
    `</div>`
  );
}

async function fetchMinuteDay(code, asof, apiFetch) {
  const key = `${String(code || "").trim()}|${String(asof || "").trim().slice(0, 10)}`;
  if (!String(code || "").trim()) return null;
  const hit = _cache.get(key);
  if (hit && hit.ok !== false) return hit;
  const fetchFn =
    apiFetch ||
    (async (url) => {
      const res = await fetch(url);
      const data = await res.json();
      return { ok: res.ok, data };
    });
  try {
    const q = new URLSearchParams({
      code: String(code).trim(),
      tail_minutes: "60",
      fetch_if_missing: "1",
    });
    if (asof) q.set("as_of", String(asof).trim().slice(0, 10));
    const { ok, data } = await fetchFn(`/api/watching/minute-tail?${q.toString()}`);
    const payload = ok !== false && data ? data : null;
    if (payload && payload.ok !== false) _cache.set(key, payload);
    else _cache.delete(key);
    return payload;
  } catch (err) {
    _cache.delete(key);
    return { ok: false, reason: String(err.message || err) };
  }
}

export async function hydrateHoldChgTip(tip, anchor, { apiFetch } = {}) {
  if (!tip) return;
  const chart = tip.querySelector("[data-hold-chg-chart]");
  if (!chart) return;
  const code = chart.getAttribute("data-code") || "";
  const asof = chart.getAttribute("data-asof") || "";
  if (!code) {
    chart.innerHTML = `<p class="paper-t0-process-tip-empty">缺股票代码</p>`;
    placeTip(tip, anchor);
    return;
  }
  const payload = await fetchMinuteDay(code, asof, apiFetch);
  if (!tip.isConnected) return;
  if (!payload || payload.ok === false) {
    chart.innerHTML = `<p class="paper-t0-process-tip-empty">${escapeText(
      (payload && (payload.hint || payload.reason)) || "分钟线暂不可用"
    )}</p>`;
    placeTip(tip, anchor);
    return;
  }
  const day = Array.isArray(payload.day_bars) ? payload.day_bars : [];
  const pts = minuteBarsToKlinePts(day);
  chart.innerHTML = miniKlineSvgFromBars(pts, {
    emptyHtml: `<p class="paper-t0-process-tip-empty">该涨跌日 5m 不足两根</p>`,
  });
  const foot = tip.querySelector(".paper-t0-process-tip-foot");
  if (foot) {
    const m = payload.meta || {};
    const chartDay = m.date_max || payload.as_of || asof || "涨跌日";
    const sess = m.session_asof || asof || "";
    const src =
      m.source === "stale_cache"
        ? "过期仓"
        : m.source === "cache"
          ? "缓存"
          : m.source === "fetch_refresh"
            ? "已补拉"
            : m.source
              ? String(m.source)
              : "5m";
    const bits = [chartDay, "5m", pts.length ? `${pts.length} 根` : "", src];
    if (sess && chartDay && String(sess).slice(0, 10) !== String(chartDay).slice(0, 10)) {
      bits.push(`会话 ${String(sess).slice(0, 10)}`);
    }
    if (pts.length > 0 && pts.length < 20) {
      bits.push("覆盖偏短");
    }
    foot.textContent = bits.filter(Boolean).join(" · ");
  }
  placeTip(tip, anchor);
}

/** 持仓表涨跌列 tip（原生表 / 虚拟网格共用委托）。 */
export function wireHoldingsChgTips(host, tipCtrl, { apiFetch } = {}) {
  if (!host || !tipCtrl || typeof tipCtrl.bindAttrTip !== "function") return;
  tipCtrl.bindAttrTip(host, {
    selector: "[data-hold-chg-tip]",
    wireKey: "hold-chg-tip",
    className: "score-tooltip paper-t0-process-tip paper-hold-chg-tip",
    buildHtml: (el) => buildHoldChgTipShell(el),
    onShow: (anchor, tip) => {
      hydrateHoldChgTip(tip, anchor, { apiFetch }).catch(() => {});
    },
  });
}
