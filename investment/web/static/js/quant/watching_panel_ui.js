/**
 * 观察面板编排用纯函数：刷新文案 · 名单映射 · 壳层显隐字段。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

export function formatRefreshStats(refresh) {
  const r = refresh || {};
  const stats = Array.isArray(r.source_stats) ? r.source_stats : [];
  const bits = stats.map((s, i) => {
    const label = s.label || `S${i + 1}`;
    if (s.error) return `${label} 失败(${s.error})`;
    if (s.note && !(s.added > 0)) return `${label} +0（${s.note}）`;
    return `${label} +${s.added ?? 0}/${s.requested ?? 0}`;
  });
  const count = r.count != null ? r.count : (r.watchlist || []).length;
  return bits.length
    ? `已刷新 · 共 ${count} 只 · ${bits.join(" · ")}`
    : `已刷新 · 共 ${count} 只`;
}

export function buildWatchingNameByCode(wl, names) {
  const map = {};
  const list = Array.isArray(wl) ? wl : [];
  const nm = Array.isArray(names) ? names : [];
  for (let i = 0; i < list.length; i++) {
    const c = String(list[i] || "")
      .trim()
      .replace(/\.(SH|SZ|BJ)$/i, "");
    if (!c) continue;
    const name = nm[i] && String(nm[i]).trim().replace(/\s+/g, "");
    // 拒绝「名=代码」伪名，避免整表覆盖后探针下拉只剩代码
    if (name && name !== c && !/^\d{6}$/.test(name)) map[c] = name;
  }
  return map;
}

export function watchingPoolMetaText(wlLen, paperN, maxSize) {
  return `观察 ${wlLen} 只 · 已持 ${paperN}/${wlLen} · 上限 ${maxSize || "—"}`;
}

/** @param {{ pool?: number|string|null, held?: number|string|null, maxSize?: number|string|null, buyPct?: number|null, mu?: number|null, med?: number|null, n?: number|null }} opts */
export function applyWatchingOverviewKpis(opts = {}) {
  const set = (id, value, sub, empty) => {
    const el = document.getElementById(id);
    if (!el) return;
    const card = el.closest(".watching-kpi-card") || el.closest(".dashboard-kpi-card");
    el.textContent = value == null || value === "" ? "—" : String(value);
    if (card) card.classList.toggle("is-empty", !!empty);
    if (sub != null) {
      const subEl = document.getElementById(`${id}-sub`);
      if (subEl) subEl.textContent = sub;
    }
  };

  const pool = opts.pool;
  const held = opts.held;
  const maxSize = opts.maxSize;
  const buyPct = opts.buyPct;
  const mu = opts.mu;
  const med = opts.med;
  const n = opts.n;

  if (pool !== undefined) {
    set(
      "watching-kpi-pool",
      pool == null ? "—" : pool,
      maxSize != null ? `上限 ${maxSize}` : "观察池",
      pool == null
    );
    const countEl = document.getElementById("watching-holdings-count");
    if (countEl) countEl.textContent = pool == null ? "" : String(pool);
  }
  if (held !== undefined) {
    set("watching-kpi-held", held == null ? "—" : held, "名单 ∩ 纸面", held == null);
  }
  if (buyPct !== undefined) {
    const txt =
      buyPct == null || !Number.isFinite(buyPct)
        ? "—"
        : `${(buyPct * 100).toFixed(0)}%`;
    set(
      "watching-kpi-buy",
      txt,
      n != null ? `n=${n}` : "ŷ 过买门槛占比",
      buyPct == null || !Number.isFinite(buyPct)
    );
  }
  if (mu !== undefined) {
    const txt =
      mu == null || !Number.isFinite(mu) ? "—" : `${Number(mu).toFixed(2)}%`;
    const sub =
      med != null && Number.isFinite(med)
        ? `med ${Number(med).toFixed(2)}%`
        : "截面均值";
    set("watching-kpi-mu", txt, sub, mu == null || !Number.isFinite(mu));
  }
}

export function watchingQuantListHtml(wl, names, escapeHtml = defaultEscapeHtml) {
  const list = Array.isArray(wl) ? wl : [];
  if (!list.length) return '<li class="sub">watchlist 为空</li>';
  const nm = Array.isArray(names) ? names : [];
  return list
    .map((c, i) => {
      const name = (nm[i] && String(nm[i]).trim()) || "";
      const label = name ? `${c} ${name}` : String(c);
      return `<li>${escapeHtml(label)}</li>`;
    })
    .join("");
}

/** @returns {{ emptyHidden: boolean, gridHidden: boolean, actionsHidden: boolean, searchHidden: boolean, sectionsHidden: boolean }} */
export function watchingPanelShellFlags(exists) {
  if (!exists) {
    return {
      emptyHidden: false,
      gridHidden: true,
      actionsHidden: true,
      searchHidden: true,
      sectionsHidden: true,
    };
  }
  return {
    emptyHidden: true,
    gridHidden: false,
    actionsHidden: false,
    searchHidden: false,
    sectionsHidden: false,
  };
}

export function applyWatchingPanelShell(flags, els) {
  const { emptyEl, gridEl, mainActions, searchWrap, sectionEls } = els || {};
  if (emptyEl) emptyEl.hidden = flags.emptyHidden;
  if (gridEl) gridEl.hidden = flags.gridHidden;
  if (mainActions) mainActions.hidden = flags.actionsHidden;
  if (searchWrap) searchWrap.hidden = flags.searchHidden;
  const sections = Array.isArray(sectionEls) ? sectionEls : [];
  for (const el of sections) {
    if (el) el.hidden = !!flags.sectionsHidden;
  }
}

export function watchingChartSeriesFromPoints(pts) {
  return (pts || [])
    .map((p) => ({
      time: String(p.x || p.date || "").slice(0, 10),
      value: Number(p.y ?? p.close),
    }))
    .filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value));
}

export function watchingChartLabelText(name, code, opts = {}) {
  const title = name || code || "";
  if (opts.error) return `${title} · 加载失败`;
  if (opts.count != null) return `${title} · 收盘价（${opts.count} 日）`;
  return `${title} · 收盘价`;
}
