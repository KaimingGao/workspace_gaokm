/** W4 · 观察页研究 Dock：表 | 图 可拖拽分栏，布局存 localStorage。 */

const STORAGE_KEY = "investment_research_dock";

function loadLayout() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (raw && typeof raw.ratio === "number") {
      return {
        ratio: Math.min(0.85, Math.max(0.25, raw.ratio)),
        // 未显式存过 enabled 时默认单栏
        enabled: typeof raw.enabled === "boolean" ? raw.enabled : false,
      };
    }
  } catch (_) {
    /* ignore */
  }
  return { ratio: 0.58, enabled: false };
}

function saveLayout(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (_) {
    /* ignore */
  }
}

/**
 * 将观察名单块包成 research-dock。可重复调用（已 docked 则只同步开关）。
 */
export function initResearchDock() {
  if ((document.body.dataset.page || "") !== "watching") return;

  const block = document.getElementById("watching-align-grid");
  if (!block) return;

  const layout = loadLayout();

  if (block.dataset.docked === "1") {
    const dock = document.getElementById("research-dock");
    const btn = document.getElementById("btn-research-dock");
    if (dock) {
      dock.classList.toggle("dock-off", !layout.enabled);
      dock.hidden = false;
    }
    if (btn) btn.textContent = layout.enabled ? "单栏" : "双栏";
    return;
  }

  const chart = document.getElementById("watching-chart-section");
  const news = document.getElementById("watching-news-detail");

  const dock = document.createElement("div");
  dock.id = "research-dock";
  dock.className = "research-dock";
  dock.classList.toggle("dock-off", !layout.enabled);

  const paneMain = document.createElement("div");
  paneMain.className = "dock-pane dock-pane-main";
  paneMain.id = "dock-pane-table";

  const splitter = document.createElement("div");
  splitter.className = "dock-splitter";
  splitter.setAttribute("role", "separator");
  splitter.setAttribute("aria-orientation", "vertical");
  splitter.title = "拖拽调整分栏";
  splitter.tabIndex = 0;

  const paneSide = document.createElement("div");
  paneSide.className = "dock-pane dock-pane-side";
  paneSide.id = "dock-pane-detail";

  const keep = [];
  Array.from(block.children).forEach((child) => {
    if (child === chart || child === news) return;
    keep.push(child);
  });
  keep.forEach((c) => paneMain.appendChild(c));
  if (chart) paneSide.appendChild(chart);
  if (news) paneSide.appendChild(news);
  if (!paneSide.childNodes.length) {
    const empty = document.createElement("p");
    empty.className = "quant-fingerprint dock-side-empty";
    empty.textContent = "点选股票查看日线 / 资讯";
    paneSide.appendChild(empty);
  }

  dock.appendChild(paneMain);
  dock.appendChild(splitter);
  dock.appendChild(paneSide);
  block.appendChild(dock);
  block.dataset.docked = "1";

  function applyRatio(ratio) {
    const r = Math.min(0.85, Math.max(0.25, ratio));
    dock.style.setProperty("--dock-main", `${(r * 100).toFixed(1)}%`);
    dock.style.setProperty("--dock-side", `${((1 - r) * 100).toFixed(1)}%`);
    layout.ratio = r;
    saveLayout(layout);
  }
  applyRatio(layout.ratio);

  let dragging = false;
  splitter.addEventListener("pointerdown", (e) => {
    if (dock.classList.contains("dock-off")) return;
    dragging = true;
    splitter.setPointerCapture(e.pointerId);
    document.body.classList.add("dock-dragging");
  });
  splitter.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = dock.getBoundingClientRect();
    if (!rect.width) return;
    applyRatio((e.clientX - rect.left) / rect.width);
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("dock-dragging");
    try {
      splitter.releasePointerCapture(e.pointerId);
    } catch (_) {
      /* ignore */
    }
  };
  splitter.addEventListener("pointerup", endDrag);
  splitter.addEventListener("pointercancel", endDrag);

  const toolbar = document.getElementById("watching-main-actions");
  if (toolbar && !document.getElementById("btn-research-dock")) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "btn-research-dock";
    btn.className = "dialog-btn secondary";
    btn.textContent = layout.enabled ? "单栏" : "双栏";
    btn.title = "切换双栏布局（表 | 图）；单栏时图在表上方";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      layout.enabled = !layout.enabled;
      dock.classList.toggle("dock-off", !layout.enabled);
      btn.textContent = layout.enabled ? "单栏" : "双栏";
      saveLayout(layout);
    });
    toolbar.appendChild(btn);
  }

  window.__investmentEnsureDockSide = () => {
    dock.hidden = false;
    if (layout.enabled) dock.classList.remove("dock-off");
    if (chart) chart.hidden = false;
  };
}
