import { initChat } from "./js/chat.js";
import { initTheme } from "./js/theme.js";
import { initAiDrawer } from "./js/ai_drawer.js";
import { initCommandPalette } from "./js/command_palette.js";

const SESSION_KEY = "investment_session_id";
const page = document.body.dataset.page || "watching";

try {
  initTheme();
} catch (err) {
  console.error("[QuantLab] init theme failed", err);
}

try {
  initAiDrawer();
} catch (err) {
  console.error("[QuantLab] init ai drawer failed", err);
}

try {
  initCommandPalette();
  document.getElementById("btn-command-palette")?.addEventListener("click", (e) => {
    e.preventDefault();
    window.__investmentOpenCommandPalette?.();
  });

  // W3：键盘可完成「打开回溯 → 跑网格 → 看热力 → 打开策略只读稿」
  // 触发方式：Alt + Shift + G
  document.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (!(e.altKey && e.shiftKey && String(e.key || "").toLowerCase() === "g")) return;
    const tag = (e.target && e.target.tagName) || "";
    if (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      e.target?.isContentEditable
    ) {
      return;
    }
    e.preventDefault();
    window.location.href = "/replay?auto_param_grid_chain=1#param-grid";
  });
} catch (err) {
  console.error("[QuantLab] init command palette failed", err);
}

// W4: 实况 WebSocket（纸面/健康）；断线复用 degrade banner
try {
  import("./js/live_ws.js").then((m) => m.initLiveWs());
} catch (err) {
  console.error("[QuantLab] init live ws failed", err);
}

// W0.2: 启动时探测健康；失败则亮降级 Banner
import("./js/api_client.js")
  .then((m) => m.apiFetch("/api/health"))
  .catch(() => {});

if (window.marked) {
  marked.setOptions({ breaks: true, gfm: true });
}

const ctx = {
  SESSION_KEY,
  sessionId: localStorage.getItem(SESSION_KEY) || "",
  page,
  busy: false,
  sendMessage: null,
  openQuantDialog: null,
  openReadmeViewer: null,
  refreshHealth: null,
  autoResize: null,
  syncSendEnabled: null,
  focusInput: null,
  setResultsReply: null,
  openResults: null,
  closeResults: null,
  showResultsTab: null,
  applyChatArtifacts: null,
  applyQuantArtifact: null,
  reloadPaper: null,
  openPlatformPanel: null,
  reloadPlatform: null,
};

function safeInit(name, fn) {
  try {
    fn();
    return true;
  } catch (err) {
    console.error(`[QuantLab] init ${name} failed`, err);
    const meta = document.getElementById("results-meta") || document.getElementById("status");
    if (meta) meta.textContent = `初始化失败(${name}): ${err.message || err}`;
    return false;
  }
}

async function loadModule(name, path) {
  try {
    return await import(path);
  } catch (err) {
    console.error(`[QuantLab] import ${name} failed`, err);
    return null;
  }
}

const V = (typeof window !== "undefined" && window.__ASSET_V__) || "p780";
const QUANT_PAGES = new Set(["chat", "quant", "watching", "strategy", "replay", "follow", "dashboard"]);
const PAPER_PAGES = new Set(["chat", "paper", "follow"]);

if (page === "chat") {
  safeInit("chat", () => initChat(ctx));
  if (ctx.sendMessage && document.body.dataset.chatReady !== "classic") {
    document.body.dataset.chatReady = "1";
  }
  if (document.body.dataset.chatReady !== "classic" && ctx.refreshHealth) {
    ctx.refreshHealth();
  }
  if (document.body.dataset.chatReady !== "classic") {
    if (ctx.autoResize) ctx.autoResize();
    if (ctx.syncSendEnabled) ctx.syncSendEnabled();
    if (ctx.focusInput) ctx.focusInput();
  }
} else if (ctx.refreshHealth) {
  try {
    ctx.refreshHealth();
  } catch (_) {
    /* health may need chat init */
  }
}

async function bootWorkspace() {
  const __perfT0 = typeof performance !== "undefined" ? performance.now() : Date.now();

  const [paperMod, quantMod, resultsMod, evalsMod, platformMod] = await Promise.all([
    loadModule("paper", `./js/paper.js?v=${V}`),
    loadModule("quant", `./js/quant.js?v=${V}`),
    loadModule("results", `./js/results.js?v=${V}`),
    loadModule("evals", `./js/evals.js?v=${V}`),
    loadModule("platform", `./js/platform.js?v=${V}`),
  ]);

  const failed = [];
  if (!paperMod) failed.push("paper");
  if (!quantMod) failed.push("quant");
  if (!resultsMod) failed.push("results");
  if (!evalsMod) failed.push("evals");
  if (!platformMod) failed.push("platform");

  if (paperMod && PAPER_PAGES.has(page)) {
    safeInit("paper", () => paperMod.initPaper(ctx));
  }
  if (quantMod && QUANT_PAGES.has(page)) {
    safeInit("quant", () => quantMod.initQuant(ctx));
  }
  if (page === "watching") {
    const dockMod = await loadModule("research_dock", `./js/research_dock.js?v=${V}`);
    if (dockMod) safeInit("research_dock", () => dockMod.initResearchDock());
  }
  if (page === "dashboard") {
    const dashboardMod = await loadModule("dashboard", `./js/dashboard.js?v=${V}`);
    if (dashboardMod) safeInit("dashboard", () => dashboardMod.initDashboard(ctx));
  }
  if (page === "chat") {
    if (platformMod) safeInit("platform", () => platformMod.initPlatform(ctx));
    if (resultsMod) safeInit("results", () => resultsMod.initResults(ctx));
    if (evalsMod) safeInit("evals", () => evalsMod.initEvals(ctx));
  }
  if (page === "platform") {
    if (platformMod) safeInit("platform", () => platformMod.initPlatform(ctx));
    // 系统设置页默认直接加载平台面板数据
    if (typeof ctx.openPlatformPanel === "function") {
      await ctx.openPlatformPanel().catch((err) => console.error("[QuantLab] openPlatformPanel", err));
    }
  }

  if (page === "chat" && ctx.showResultsTab) {
    const params = new URLSearchParams(location.search);
    let openTab = params.get("tab");
    if (!openTab) {
      const open = params.get("open");
      if (open === "evals") openTab = "evals";
      else if (open === "usage") openTab = "usage";
      else openTab = "watching";
    }
    await ctx.showResultsTab(openTab, { openMobile: false, load: false });
    setTimeout(() => {
      ctx.showResultsTab(openTab, { openMobile: openTab === "evals" || openTab === "usage", load: true }).catch((err) => {
        console.error("[QuantLab] showResultsTab load failed", err);
      });
    }, 50);
  } else if (QUANT_PAGES.has(page) && page !== "chat" && ctx.openQuantDialog) {
    ctx.openQuantDialog().catch((err) => console.error("[QuantLab] openQuantDialog", err));
  }

  if (failed.length) {
    const statusEl = document.getElementById("status");
    const meta = document.getElementById("results-meta");
    const msg = `部分面板未加载: ${failed.join(", ")}`;
    if (meta) meta.textContent = msg;
    if (statusEl && statusEl.textContent.includes("面板")) {
      statusEl.textContent = msg;
      statusEl.className = "status bad";
    }
  }

  // W4：性能预算持续观测（首屏/初始化耗时）
  try {
    const dt = (typeof performance !== "undefined" ? performance.now() : Date.now()) - __perfT0;
    if (dt > 2500) {
      console.warn("[QuantLab][Perf] bootWorkspace slow", { ms: Math.round(dt) });
      const statusEl = document.getElementById("status");
      if (statusEl && !String(statusEl.textContent || "").includes("Perf")) {
        statusEl.textContent = `性能：首屏初始化耗时 ${Math.round(dt)}ms（持续观测）`;
        statusEl.className = "status warn";
      }
    }
  } catch (_) {
    /* ignore */
  }
}

bootWorkspace().catch((err) => {
  console.error("[QuantLab] workspace boot failed", err);
  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = `对话可用 · 右侧面板加载失败: ${err.message || err}`;
    statusEl.className = "status bad";
  }
});
