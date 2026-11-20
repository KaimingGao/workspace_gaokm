/** 数据中心观察池只数与「重建并对齐」。 */
import {
  MODEL_FIT_LIMIT,
  markWatchingPoolLimitReady,
  notifyWatchingPoolLimitChanged,
  readWatchingPoolLimit,
} from "./params.js";

export function installResearchUniverseUi(q) {
  const { on, apiFetch } = q;

  const el = {
    maxSize: () => document.getElementById("quant-watching-max-size"),
    poolVal: () => document.getElementById("quant-watching-pool-val"),
    rebuildAlign: () => document.getElementById("quant-ru-rebuild-align"),
  };

  function clampWatchingMaxSize(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 300;
    return Math.max(300, Math.min(MODEL_FIT_LIMIT, Math.round(n)));
  }

  function setRebuildAlignBusy(on) {
    const btn = el.rebuildAlign();
    if (!btn) return null;
    btn.disabled = !!on;
    btn.classList.toggle("is-busy", !!on);
    if (on) btn.setAttribute("aria-busy", "true");
    else btn.removeAttribute("aria-busy");
    return btn;
  }

  async function rebuildAndAlignUniverse() {
    const btn = el.rebuildAlign();
    if (btn && (btn.disabled || btn.getAttribute("aria-busy") === "true")) return;
    setRebuildAlignBusy(true);
    try {
      const { ok, data } = await apiFetch(
        "/api/quant/research-universe/rebuild-and-reconcile",
        { method: "POST" }
      );
      if (!ok || (data && data.ok === false)) return;
      await hydrateWatchingMaxSize();
      notifyWatchingPoolLimitChanged(readWatchingPoolLimit());
    } finally {
      setRebuildAlignBusy(false);
    }
  }

  function paintPoolSize(cap) {
    const n = clampWatchingMaxSize(cap);
    const input = el.maxSize();
    const out = el.poolVal();
    if (input) {
      input.value = String(n);
      input.setAttribute("aria-valuenow", String(n));
    }
    if (out) out.value = String(n);
    return n;
  }

  async function hydrateWatchingMaxSize() {
    const input = el.maxSize();
    let capFromFile = null;
    try {
      const { ok, data } = await apiFetch("/api/watching/file");
      const watching = data && data.watching;
      const cap = Number((watching && watching.max_size) || (data && data.max_size));
      if (ok && Number.isFinite(cap)) capFromFile = cap;
      if (input && capFromFile != null) paintPoolSize(capFromFile);
    } catch (_) {
      if (input) paintPoolSize(input.value || 300);
    } finally {
      // 滑条在数据中心。研究枢纽无滑条时仍用文件上限，避免默认 300 截断满池。
      const ready = input ? readWatchingPoolLimit() : capFromFile;
      markWatchingPoolLimitReady(ready != null ? ready : readWatchingPoolLimit());
    }
  }

  async function saveWatchingMaxSize() {
    const input = el.maxSize();
    if (!input) return;
    const cap = paintPoolSize(input.value);
    const { ok, data } = await apiFetch("/api/watching/max-size", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_size: cap, compose: "feature_pool" }),
    });
    if (!ok || (data && data.ok === false)) {
      await hydrateWatchingMaxSize();
      return;
    }
    const saved = paintPoolSize(data && data.max_size != null ? data.max_size : cap);
    notifyWatchingPoolLimitChanged(saved);
  }

  on("quant-watching-max-size", "input", () => {
    const input = el.maxSize();
    if (input) paintPoolSize(input.value);
  });
  on("quant-watching-max-size", "change", () => {
    void saveWatchingMaxSize();
  });
  on("quant-ru-rebuild-align", "click", () => {
    void rebuildAndAlignUniverse();
  });

  const page = document.body?.dataset?.page;
  if (el.maxSize() || page === "data" || page === "quant") {
    void hydrateWatchingMaxSize().catch(() => {
      markWatchingPoolLimitReady(readWatchingPoolLimit());
    });
  } else {
    markWatchingPoolLimitReady(readWatchingPoolLimit());
  }

  return { hydrateWatchingMaxSize };
}
