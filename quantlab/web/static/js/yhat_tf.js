/** Transformer 四头隐藏研究台 · /yhat-tf · 总览矩阵 + 单头详情 */

import { apiFetch } from "./api_client.js";

const HEADS = ["oo", "tc", "oo_rank", "tc_rank"];
const GATE_LABEL = {
  oo: "Arm·O",
  tc: "Arm·O",
  oo_rank: "Gate lift",
  tc_rank: "Gate lift",
};
const ACTION_SUCCESS = {
  "save-draft": "草稿已保存",
  fit: "拟合命令已生成，并记录拟合意图",
  "promote-research": "已晋级研究槽",
  "clear-draft": "草稿已清理",
  "clear-research": "研究槽已清理",
};

let activeHead = "oo";
let viewMode = "single";
let refreshing = false;
let refreshPromise = null;
let lastGoodData = null;
let toastTimer = null;
const latestHeads = new Map();
const pendingHeads = new Set();

function $(sel, root = document) {
  return root.querySelector(sel);
}

function $all(sel, root = document) {
  return Array.from(root.querySelectorAll(sel));
}

function panelOf(head) {
  return document.querySelector(`[data-head-panel="${head}"]`);
}

function sectionOf(head) {
  return document.querySelector(`[data-head-section="${head}"]`);
}

function jsonPost(url, body) {
  return apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function stateOf(st) {
  const status = st?.scheme?.status;
  if (status === "holdout_ready") return "ok";
  if (status === "planned") return "warn";
  return "idle";
}

function stateLabel(state) {
  return { ok: "就绪", warn: "方案", busy: "处理中", error: "失败", idle: "待命" }[
    state
  ] || "待命";
}

function setChip(el, state, msg) {
  if (!el) return;
  const chip = el.querySelector(".quant-pro-status-chip");
  const text = el.querySelector(".yhat-tf-status-msg");
  if (chip) {
    chip.setAttribute("data-state", state || "idle");
    chip.textContent = stateLabel(state);
  }
  if (text && msg != null) text.textContent = msg;
}

function showToast(message, tone = "ok") {
  const toast = $("#yhat-tf-toast");
  if (!toast) return;
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.dataset.tone = tone;
  toast.setAttribute("role", tone === "error" ? "alert" : "status");
  toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

function showPanelError(head, message) {
  const notice = document.querySelector(`[data-notice-for="${head}"]`);
  if (!notice) return;
  notice.textContent = message;
  notice.hidden = !message;
}

function setLoadError(message) {
  const box = $("#yhat-tf-load-error");
  if (!box) return;
  box.hidden = !message;
  const text = $("[data-load-error-text]", box);
  if (text && message) text.textContent = message;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ");
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function readParams(head) {
  const panel = panelOf(head);
  const out = {};
  if (!panel) return out;
  $all("[data-param]", panel).forEach((inp) => {
    const key = inp.getAttribute("data-param");
    if (!key) return;
    const raw = String(inp.value ?? "").trim();
    if (inp.type === "number") {
      const n = Number(raw);
      out[key] = Number.isFinite(n) ? n : raw;
    } else {
      out[key] = raw;
    }
  });
  return out;
}

function applyParams(head, params) {
  const panel = panelOf(head);
  if (!panel || !params || typeof params !== "object") return;
  $all("[data-param]", panel).forEach((inp) => {
    const key = inp.getAttribute("data-param");
    if (!key || params[key] == null) return;
    inp.value = String(params[key]);
  });
}

function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch (_) {
    return String(obj);
  }
}

function setMeter(el, pct) {
  if (!el) return;
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  el.style.setProperty("--p", `${p}%`);
}

function metricsOf(st) {
  const candidates = [st?.last?.metrics, st?.research?.metrics, st?.draft?.metrics];
  return candidates.find((item) => item && typeof item === "object") || null;
}

function metricSummary(st) {
  const metrics = metricsOf(st);
  if (!metrics) return "";
  const entries = [
    ["CS-IC", metrics.cs_rank_ic],
    ["NDCG@10", metrics["ndcg@10"] ?? metrics.ndcg_at_10],
    ["Gate lift", metrics.gate_lift],
  ].filter(([, value]) => value != null && value !== "" && Number.isFinite(Number(value)));
  return entries.map(([label, value]) => `${label} ${Number(value).toFixed(4)}`).join(" · ");
}

function updateActionStates(head) {
  const panel = panelOf(head);
  if (!panel) return;
  const st = latestHeads.get(head);
  const busy = refreshing || pendingHeads.has(head);
  panel.setAttribute("aria-busy", String(pendingHeads.has(head)));
  $all("[data-act]", panel).forEach((btn) => {
    const act = btn.dataset.act;
    let disabled = busy;
    if (act === "promote-research" || act === "clear-draft") disabled ||= !st?.has_draft;
    if (act === "clear-research") disabled ||= !st?.has_research;
    btn.disabled = disabled;
  });
}

function applyView({ scroll = false } = {}) {
  HEADS.forEach((head) => {
    const section = sectionOf(head);
    const selected = head === activeHead;
    if (section) section.hidden = viewMode === "single" && !selected;
    if (selected) {
      const panel = panelOf(head);
      if (panel) panel.open = true;
    }
    const card = document.querySelector(`[data-head-select="${head}"]`);
    if (card) {
      card.classList.toggle("is-active", selected);
      card.setAttribute("aria-pressed", String(selected));
    }
  });

  const showAll = viewMode === "all";
  if (showAll) HEADS.forEach((head) => {
    const panel = panelOf(head);
    if (panel) panel.open = true;
  });
  const toggle = $("#yhat-tf-toggle-view");
  if (toggle) {
    toggle.textContent = showAll ? "单头视图" : "全部展开";
    toggle.setAttribute("aria-pressed", String(showAll));
  }
  const desc = $("#yhat-tf-view-desc");
  if (desc) desc.textContent = showAll ? "四头全部展开 · 点击卡片定位" : "单头详情 · 选择上方卡片切换";

  if (scroll) {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    sectionOf(activeHead)?.scrollIntoView({ block: "start", behavior });
  }
}

function selectHead(head, { scroll = true } = {}) {
  if (!HEADS.includes(head)) return;
  activeHead = head;
  applyView({ scroll });
}

function renderOverview(heads) {
  const nReady = heads.filter((h) => h.scheme?.status === "holdout_ready").length;
  const nDraft = heads.filter((h) => h.has_draft).length;
  const nRes = heads.filter((h) => h.has_research).length;
  const setOv = (key, n) => {
    const card = document.querySelector(`[data-overview="${key}"]`);
    if (!card) return;
    const num = card.querySelector("[data-ov-n]");
    const bar = card.querySelector("[data-ov-bar]");
    if (num) num.textContent = String(n);
    setMeter(bar, (n / HEADS.length) * 100);
    card.classList.toggle("is-empty", n === 0);
    card.classList.toggle("is-mid", n > 0 && n < HEADS.length);
    card.classList.toggle("is-good", key === "ready" && n === HEADS.length);
  };
  setOv("ready", nReady);
  setOv("draft", nDraft);
  setOv("research", nRes);
  return { nReady, nDraft, nRes };
}

function renderHeadSummary(st) {
  const head = st.head;
  const state = stateOf(st);
  const chip = document.querySelector(`[data-head-summary-status="${head}"]`);
  if (chip) {
    chip.dataset.state = state;
    chip.textContent = stateLabel(state);
  }
  const slots = document.querySelector(`[data-head-summary-slots="${head}"]`);
  if (slots) {
    slots.textContent = `draft ${st.has_draft ? "有" : "无"} · research ${st.has_research ? "有" : "无"}`;
  }
  const gate = document.querySelector(`[data-head-summary-gate="${head}"]`);
  if (gate) gate.textContent = `Gate · ${st.scheme?.gate || GATE_LABEL[head] || "—"}`;
}

function renderGate(st) {
  const root = document.querySelector(`[data-gate-for="${st.head}"]`);
  if (!root) return;
  const holdout = root.querySelector('[data-gate-item="holdout"]');
  const draft = root.querySelector('[data-gate-item="draft"]');
  const oos = root.querySelector('[data-gate-item="oos"]');
  const ready = st.scheme?.status === "holdout_ready";
  if (holdout) {
    holdout.textContent = `Holdout 配置 · ${ready ? "已就绪" : "未就绪"}`;
    holdout.classList.toggle("is-on", ready);
  }
  if (draft) {
    draft.textContent = `草稿 · ${st.has_draft ? "已保存" : "待保存"}`;
    draft.classList.toggle("is-on", !!st.has_draft);
  }
  if (oos) {
    const summary = metricSummary(st);
    oos.textContent = summary ? `OOS 指标 · ${summary}` : "OOS 指标 · 待回传";
    oos.classList.toggle("is-on", !!summary);
  }
}

function renderResult(st) {
  const panel = panelOf(st.head);
  if (!panel) return;
  const values = {
    last: st.last ? formatTime(st.last.updated_at) : "尚无记录",
    draft: st.draft ? formatTime(st.draft.updated_at) : "未保存",
    research: st.research ? formatTime(st.research.promoted_at || st.research.updated_at) : "未晋级",
    gate: metricSummary(st) || st.scheme?.gate || GATE_LABEL[st.head] || "—",
  };
  Object.entries(values).forEach(([key, value]) => {
    const el = panel.querySelector(`[data-result-field="${key}"]`);
    if (el) el.textContent = value;
  });

  const resultEl = panel.querySelector(`[data-result-for="${st.head}"]`);
  if (resultEl) {
    resultEl.textContent = pretty({
      last: st.last || null,
      draft: st.draft || null,
      research: st.research || null,
      scheme: st.scheme || null,
      live_allowed: false,
    });
  }

  const cli = panel.querySelector(`[data-cli-for="${st.head}"]`);
  if (cli) {
    const cmd = st.last?.cli || st.scheme?.script;
    cli.hidden = !cmd;
    cli.textContent = cmd ? `CLI · ${cmd}` : "";
  }
}

function renderHead(st) {
  const head = st.head;
  latestHeads.set(head, st);
  const scheme = st.scheme || {};
  const msgBits = [
    scheme.loss || null,
    scheme.status || null,
    st.has_research ? "research" : st.has_draft ? "draft" : null,
  ].filter(Boolean);
  setChip(document.querySelector(`[data-status-for="${head}"]`), stateOf(st), msgBits.join(" · ") || "—");

  const slots = document.querySelector(`[data-slots-for="${head}"]`);
  if (slots) {
    const draft = slots.querySelector('[data-slot="draft"]');
    const research = slots.querySelector('[data-slot="research"]');
    const gate = slots.querySelector('[data-slot="gate"]');
    if (draft) {
      draft.textContent = `draft · ${st.has_draft ? "有" : "无"}`;
      draft.classList.toggle("is-on", !!st.has_draft);
    }
    if (research) {
      research.textContent = `research · ${st.has_research ? "有" : "无"}`;
      research.classList.toggle("is-on", !!st.has_research);
    }
    if (gate) gate.textContent = `gate · ${scheme.gate || GATE_LABEL[head] || "—"}`;
  }

  renderHeadSummary(st);
  renderGate(st);
  renderResult(st);
  if (st.draft?.params) applyParams(head, st.draft.params);
  updateActionStates(head);
}

function setRefreshing(value) {
  refreshing = value;
  const page = $(".yhat-tf-page");
  if (page) page.setAttribute("aria-busy", String(value));
  const refresh = $("#yhat-tf-refresh");
  if (refresh) {
    refresh.disabled = value;
    refresh.classList.toggle("is-busy", value);
  }
  const label = $("[data-refresh-label]");
  if (label) label.textContent = value ? "刷新中…" : "刷新";
  HEADS.forEach(updateActionStates);
}

function refreshStatus() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const meta = $("#yhat-tf-meta");
    setRefreshing(true);
    if (!lastGoodData && meta) meta.textContent = "正在读取模型状态…";
    try {
      const { ok, data, error } = await apiFetch("/api/quant/yhat-tf/status");
      if (!ok) throw new Error(error || "status failed");
      lastGoodData = data;
      const heads = Array.isArray(data.heads) ? data.heads : [];
      heads.forEach(renderHead);
      const { nReady, nDraft, nRes } = renderOverview(heads);
      if (meta) {
        meta.textContent = `${data.scheme?.name || "Scheme C"} · ready ${nReady}/4 · draft ${nDraft}/4 · research ${nRes}/4`;
      }
      setLoadError("");
      return data;
    } catch (err) {
      const message = err?.message || String(err);
      if (meta) meta.textContent = lastGoodData ? "状态刷新失败 · 展示上次成功数据" : "状态加载失败 · 请重试";
      setLoadError(
        lastGoodData
          ? `状态刷新失败：${message}。当前保留上次成功数据。`
          : `状态加载失败：${message}。请点击重试。`
      );
      return null;
    } finally {
      setRefreshing(false);
    }
  })();
  return refreshPromise.finally(() => {
    refreshPromise = null;
  });
}

async function onAct(head, act) {
  if (refreshing || pendingHeads.has(head)) return;
  if (act === "clear-draft" || act === "clear-research") {
    const label = act === "clear-draft" ? "草稿" : "研究槽";
    if (!window.confirm(`确认清理 ${head} 的${label}吗？此操作不可撤销。`)) return;
  }

  const statusEl = document.querySelector(`[data-status-for="${head}"]`);
  pendingHeads.add(head);
  updateActionStates(head);
  showPanelError(head, "");
  setChip(statusEl, "busy", "处理中…");
  try {
    let res;
    if (act === "save-draft") {
      res = await jsonPost("/api/quant/yhat-tf/draft", { head, params: readParams(head) });
    } else if (act === "fit") {
      res = await jsonPost("/api/quant/yhat-tf/fit", { head, params: readParams(head) });
    } else if (act === "promote-research") {
      res = await jsonPost("/api/quant/yhat-tf/promote-research", { head });
    } else if (act === "clear-draft") {
      res = await jsonPost("/api/quant/yhat-tf/clear", { head, slot: "draft" });
    } else if (act === "clear-research") {
      res = await jsonPost("/api/quant/yhat-tf/clear", { head, slot: "research" });
    } else {
      return;
    }
    if (!res?.ok) throw new Error(res?.error || "请求失败");
    const refreshed = await refreshStatus();
    if (!refreshed) setChip(statusEl, "warn", "操作已完成 · 状态刷新失败");
    showToast(ACTION_SUCCESS[act] || "操作完成");
  } catch (err) {
    const message = err?.message || String(err);
    setChip(statusEl, "error", message);
    showPanelError(head, message);
    showToast(message, "error");
  } finally {
    pendingHeads.delete(head);
    updateActionStates(head);
  }
}

async function copyCli(head) {
  const cli = document.querySelector(`[data-cli-for="${head}"]`);
  const command = cli?.textContent?.replace(/^CLI · /, "").trim();
  if (!command) {
    showToast("暂无可复制的 CLI", "warn");
    return;
  }
  try {
    await navigator.clipboard.writeText(command);
    showToast("CLI 已复制");
  } catch (_) {
    showToast("浏览器未授权剪贴板，请展开技术详情手动复制", "warn");
  }
}

function bindCompactHead() {
  const scroller = $(".page-main");
  const head = $(".yhat-tf-head");
  if (!scroller || !head) return;
  const update = () => head.classList.toggle("is-compact", scroller.scrollTop > 72);
  scroller.addEventListener("scroll", update, { passive: true });
  update();
}

export function initYhatTf(_ctx) {
  const hashHead = HEADS.find((head) => location.hash === `#${sectionOf(head)?.id}`);
  if (hashHead) activeHead = hashHead;
  applyView();
  bindCompactHead();

  $all("[data-head-select]").forEach((btn) => {
    btn.addEventListener("click", () => selectHead(btn.dataset.headSelect));
  });
  $("#yhat-tf-toggle-view")?.addEventListener("click", () => {
    viewMode = viewMode === "single" ? "all" : "single";
    applyView({ scroll: viewMode === "single" });
  });
  HEADS.forEach((head) => {
    const panel = panelOf(head);
    if (!panel) return;
    $all("[data-act]", panel).forEach((btn) => {
      btn.addEventListener("click", () => onAct(head, btn.dataset.act));
    });
  });
  $all("[data-copy-cli]").forEach((btn) => {
    btn.addEventListener("click", () => copyCli(btn.dataset.copyCli));
  });
  $("#yhat-tf-refresh")?.addEventListener("click", refreshStatus);
  $("#yhat-tf-retry")?.addEventListener("click", refreshStatus);
  window.addEventListener("hashchange", () => {
    const hashHead = HEADS.find((head) => location.hash === `#${sectionOf(head)?.id}`);
    if (hashHead) selectHead(hashHead);
  });
  refreshStatus();
}
