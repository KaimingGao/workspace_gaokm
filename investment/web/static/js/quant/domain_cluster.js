import { normalizeProbeCode, isUsableStockName } from "./names.js";
import { createScoreTooltipController } from "../score_tooltip.js";
import { syncOverviewLanding } from "./factor_corr_ui.js";

const RETIRED_MSG = "分组已退役（cluster_retired）· 请用全局 ŷ_oo / factor-ols";

/** 分组 / 探针 / 横截面入口已退役。公式试算标的下拉与 OOS 提示仍在。 */
export function installClusterProbe(q) {
  const {
    els,
    state,
    escapeHtml,
    setBusyText,
    oosGateTipHtml,
  } = q;
  const oosGateTips = createScoreTooltipController();

  function paintClusterHealth(html) {
    const host = els.quantOlsHealth;
    if (!host) return;
    const body = String(html || "").trim();
    host.innerHTML = body;
    host.hidden = !body;
  }

  function buildClusterHealthHtml() {
    return `<p class="sub">${escapeHtml(RETIRED_MSG)}</p>`;
  }

  async function bootstrapClusterHub() {
    if (bootstrapClusterHub._running) return;
    bootstrapClusterHub._running = true;
    try {
      setBusyText(els.quantOlsSummary, RETIRED_MSG, { busy: false });
      if (els.quantOlsClusters) {
        els.quantOlsClusters.hidden = false;
        els.quantOlsClusters.innerHTML = `<p class="sub">${escapeHtml(RETIRED_MSG)}</p>`;
      }
      paintClusterHealth(buildClusterHealthHtml());
      try {
        syncOverviewLanding({ cluster_retired: true });
      } catch (_) {
        /* ignore */
      }
    } finally {
      bootstrapClusterHub._running = false;
    }
  }

  async function hydrateWatchingNamesFromApi() {
    try {
      const res = await fetch("/api/watching");
      const data = await res.json();
      const uni = (data && data.watching) || data || {};
      const wl = uni.watchlist || data.watchlist || [];
      const names = Array.isArray(uni.watchlist_names)
        ? uni.watchlist_names
        : Array.isArray(data.watchlist_names)
          ? data.watchlist_names
          : [];
      state.watchingNameByCode = state.watchingNameByCode || {};
      for (let i = 0; i < wl.length; i++) {
        const item = wl[i];
        if (typeof item === "string") {
          const nm = names[i] || "";
          if (isUsableStockName(item, nm)) state.watchingNameByCode[item] = nm;
        } else if (item && typeof item === "object") {
          const code = String(item.code || item.stock_code || "").trim();
          const nm = item.name || item.stock_name || names[i] || "";
          if (code && isUsableStockName(code, nm)) state.watchingNameByCode[code] = nm;
        }
      }
      return wl;
    } catch (_) {
      return [];
    }
  }

  async function fillExprCodeSelect() {
    const sel = document.getElementById("quant-expr-code");
    if (!sel || sel.tagName !== "SELECT") return;
    const prev = String(sel.value || "").trim();
    const wl = await hydrateWatchingNamesFromApi();
    const seen = new Set();
    const rows = [];
    const push = (code, name) => {
      const c = normalizeProbeCode(code);
      if (!c || seen.has(c)) return;
      seen.add(c);
      const nm = String(name || state.watchingNameByCode?.[c] || "").trim();
      rows.push({ code: c, name: nm && nm !== c ? nm : "" });
    };
    for (const item of wl || []) {
      if (typeof item === "string") push(item, "");
      else if (item && typeof item === "object") {
        push(item.code || item.stock_code, item.name || item.stock_name || "");
      }
    }
    if (!rows.length) return;
    sel.innerHTML = rows
      .map((r) => {
        const label = r.name ? `${r.name} ${r.code}` : r.code;
        return `<option value="${escapeHtml(r.code)}">${escapeHtml(label)}</option>`;
      })
      .join("");
    const pref = normalizeProbeCode(prev);
    const hit =
      rows.find((r) => r.code === pref) ||
      rows.find((r) => r.code === "600519") ||
      rows[0];
    sel.value = hit.code;
  }

  function renderFactorOls(data) {
    if (!els.quantOlsSummary) return;
    if (!data || !data.success) {
      setBusyText(els.quantOlsSummary, (data && data.error) || RETIRED_MSG, {
        busy: false,
      });
      return;
    }
    const n = data.sample_count != null ? data.sample_count : "—";
    setBusyText(
      els.quantOlsSummary,
      `全局 OLS · n=${escapeHtml(String(n))} · 分组路径已退役`,
      { busy: false }
    );
  }

  function wireOosGateTips(host) {
    if (!host) return;
    oosGateTips.bindAttrTip(host, {
      selector: "[data-oos-gate]",
      className: "score-tooltip oos-gate-tip",
      buildHtml: (el) => {
        try {
          return oosGateTipHtml(JSON.parse(el.getAttribute("data-oos-gate") || "{}"));
        } catch (_) {
          return "";
        }
      },
    });
  }

  bootstrapClusterHub().catch(() => {});

  return {
    bootstrapClusterHub,
    buildClusterHealthHtml,
    fillExprCodeSelect,
    paintClusterHealth,
    renderFactorOls,
    wireOosGateTips,
  };
}
