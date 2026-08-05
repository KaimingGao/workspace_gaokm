/**
 * R5.2 · 持仓虚拟表岛挂载控制器（从 paper.js 拆出）。
 * 依赖由 initPaper 注入，避免巨型闭包继续膨胀 paper.js。
 */

export function createHoldingsIslandController(deps) {
  const {
    getAssetV,
    getSortState,
    setSortState,
    persistSort,
    onRowSelect,
    getRowContext,
    setSelectedHoldCode,
    buildOriginBarHtml,
    buildActionBarHtml,
  } = deps;

  let holdingsGrid = null;
  let holdingsGridReady = false;
  let holdingsGridMounting = null;

  function destroy() {
    if (holdingsGrid && typeof holdingsGrid.destroy === "function") {
      try {
        holdingsGrid.destroy();
      } catch (_) {
        /* ignore */
      }
    }
    holdingsGrid = null;
    holdingsGridReady = false;
  }

  async function loadModule() {
    const V = getAssetV();
    return import(`../holdings_table_island.js?v=${V}`);
  }

  async function ensureGrid(host) {
    if (holdingsGridMounting) return holdingsGridMounting;
    holdingsGridMounting = (async () => {
      destroy();
      const mod = await loadModule();
      const sort = getSortState();
      const grid = await mod.mountHoldingsTableIsland(host, {
        initialSort: sort.key
          ? [{ column: sort.key, dir: sort.dir === "asc" ? "asc" : "desc" }]
          : [{ column: "market_value", dir: "desc" }],
      });
      grid.on("sortChanged", (sorters) => {
        const s = Array.isArray(sorters) && sorters.length ? sorters[0] : null;
        const key = s && s.field;
        if (key === "code" || key === "market_value" || key === "score" || key === "pnl") {
          setSortState(key, s.dir === "asc" ? "asc" : "desc");
          persistSort();
        }
      });
      grid.on("rowClick", ({ code, event }) => {
        if (
          event &&
          event.target &&
          event.target.closest &&
          event.target.closest(".paper-hold-score")
        ) {
          return;
        }
        const safe = String(code || "").replace(/"/g, "");
        let el = document.querySelector(`#paper-holdings-table [data-code="${safe}"]`);
        if (!el) {
          const d = grid.getRow(code)?.getData();
          if (!d) return;
          el = {
            dataset: {
              code: String(code),
              shares: d.shares != null ? String(d.shares) : "",
            },
            querySelector(sel) {
              if (String(sel).includes("paper-wl-name-text")) {
                return {
                  dataset: { fullName: d.name || "" },
                  getAttribute() {
                    return d.name || "";
                  },
                  textContent: d.name || code,
                };
              }
              return null;
            },
            scrollIntoView() {},
          };
        }
        onRowSelect(el, { chart: true });
      });
      holdingsGrid = grid;
      holdingsGridReady = true;
      return grid;
    })();
    try {
      return await holdingsGridMounting;
    } finally {
      holdingsGridMounting = null;
    }
  }

  async function upgrade(summary, holdings) {
    const holdingsEl = document.getElementById("paper-holdings-table");
    if (!holdingsEl || !holdings.length) return null;
    const ctx = getRowContext();
    const action = buildActionBarHtml({
      selectedHoldCode: ctx.selectedHoldCode,
      holdings,
    });
    setSelectedHoldCode(action.selectedHoldCode);
    const shell =
      buildOriginBarHtml(summary) +
      `<div id="paper-holdings-react-root" class="watching-react-grid-host paper-holdings-react-host"></div>` +
      action.actionBarHtml;
    holdingsEl.innerHTML = shell;
    const host = document.getElementById("paper-holdings-react-root");
    if (!host) throw new Error("island host missing");
    const mod = await loadModule();
    const grid = await ensureGrid(host);
    const rowCtx = getRowContext();
    const rows = holdings.map((h) =>
      mod.holdingToRow(h, {
        chartMode: rowCtx.chartMode,
        chartStockCode: rowCtx.chartStockCode,
        selectedHoldCode: rowCtx.selectedHoldCode,
        pendingFocusCode: rowCtx.pendingFocusCode,
      })
    );
    grid.setRows(rows);
    if (!grid.getData || !grid.getData().length) {
      throw new Error("island empty after setRows");
    }
    return grid;
  }

  return {
    destroy,
    upgrade,
    isReady: () => holdingsGridReady,
    getGrid: () => holdingsGrid,
  };
}
