import { normalizeProbeCode, isUsableStockName } from "./names.js";
import { createScoreTooltipController } from "../score_tooltip.js";

/** 分组 / 探针入口已退役；保留试算标的下拉与 OOS tip 绑定。 */
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
    return "";
  }

  function clearRetiredHosts() {
    // 勿覆盖 #quant-ols-summary（ŷ_oo 状态条）；遗留宿主保持空且隐藏。
    if (els.quantOlsClusters) {
      els.quantOlsClusters.innerHTML = "";
      els.quantOlsClusters.hidden = true;
    }
    paintClusterHealth("");
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
      setBusyText(els.quantOlsSummary, (data && data.error) || "OLS 失败", {
        busy: false,
      });
      return;
    }
    const n = data.sample_count != null ? data.sample_count : "—";
    setBusyText(
      els.quantOlsSummary,
      `全局 OLS · n=${escapeHtml(String(n))}`,
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

  clearRetiredHosts();

  return {
    bootstrapClusterHub: clearRetiredHosts,
    buildClusterHealthHtml,
    fillExprCodeSelect,
    paintClusterHealth,
    renderFactorOls,
    wireOosGateTips,
  };
}
