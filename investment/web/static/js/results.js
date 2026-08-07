import { escapeHtml } from "./shared.js";

/** Right-pane workspace tabs. */
export function initResults(ctx) {
  const pane = document.getElementById("results-pane");
  if (!pane) return;

  const replyEl = document.getElementById("results-reply");
  const artifactsEl = document.getElementById("results-artifacts");
  const metaEl = document.getElementById("results-meta");
  const tabButtons = Array.from(document.querySelectorAll(".results-tab[data-tab]"));
  const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
  const loaded = {
    quant: false,
    watching: false,
    strategy: false,
    replay: false,
    follow: false,
    followCompare: false,
    portfolio: false,
    paper: false,
    reply: true,
    evals: false,
    usage: false,
    platform: false,
  };

  function isDrawerViewport() {
    return window.matchMedia("(max-width: 720px)").matches;
  }

  function openResults() {
    if (!isDrawerViewport()) return;
    document.body.classList.add("results-open");
  }

  function closeResults() {
    document.body.classList.remove("results-open");
  }

  function setResultsReply(markdown, { openMobile = true, switchTab = false } = {}) {
    if (!replyEl) return;
    const text = (markdown || "").trim();
    if (!text) {
      replyEl.innerHTML =
        '<p class="results-empty">对话后，这里同步最近一次助手回复，便于对照右侧数据。</p>';
      return;
    }
    if (window.marked) {
      replyEl.innerHTML = marked.parse(text);
    } else {
      replyEl.textContent = text;
    }
    if (switchTab) showTab("reply", { openMobile, load: false });
    else if (openMobile && isDrawerViewport()) {
      openResults();
    }
  }

  function renderArtifactCards(artifacts) {
    if (!artifactsEl) return;
    const list = artifacts || [];
    if (!list.length) {
      artifactsEl.innerHTML =
        '<p class="results-empty">Agent 调用工具后，这里显示结构化结果摘要。</p>';
      return;
    }
    artifactsEl.innerHTML = list
      .map((a) => {
        const task = a.params && a.params.task ? ` · ${escapeHtml(String(a.params.task))}` : "";
        const badge = a.success ? "ok" : "bad";
        const json = escapeHtml(JSON.stringify(a.data || {}, null, 2).slice(0, 2400));
        return (
          `<article class="artifact-card ${badge}">` +
          `<div class="artifact-head"><span class="artifact-tool">${escapeHtml(a.tool || "")}${task}</span>` +
          `<span class="artifact-tab">${escapeHtml(a.tab || "")}</span></div>` +
          `<p class="artifact-summary">${escapeHtml(a.summary || "")}</p>` +
          `<pre class="artifact-json">${json}</pre></article>`
        );
      })
      .join("");
  }

  async function ensureTabData(tab) {
    if (tab === "usage") {
      if (typeof ctx.refreshUsage === "function") await ctx.refreshUsage();
      loaded.usage = true;
      return;
    }
    if (tab === "evals") {
      if (typeof ctx.openEvalsPanel === "function") await ctx.openEvalsPanel({ showDialog: false });
      loaded.evals = true;
      return;
    }
    if (tab === "platform") {
      if (typeof ctx.openPlatformPanel === "function") await ctx.openPlatformPanel();
      loaded.platform = true;
      return;
    }
    if ((tab === "paper" || tab === "follow") && typeof ctx.reloadPaper === "function") {
      await ctx.reloadPaper();
      loaded[tab] = true;
      if (tab === "follow" && typeof ctx.openQuantDialog === "function" && !loaded.followCompare) {
        await ctx.openQuantDialog();
        loaded.followCompare = true;
      }
      return;
    }
    if (loaded[tab]) return;
    const quantTabs = new Set(["quant", "watching", "strategy", "replay"]);
    if (quantTabs.has(tab) && typeof ctx.openQuantDialog === "function") {
      await ctx.openQuantDialog();
      loaded[tab] = true;
    } else {
      loaded[tab] = true;
    }
  }

  function setMeta(text, { loading = false, error = false } = {}) {
    if (!metaEl) return;
    const show = !!(text && String(text).trim());
    metaEl.hidden = !show;
    metaEl.classList.toggle("is-loading", !!loading);
    metaEl.classList.toggle("is-busy", !!loading);
    metaEl.classList.toggle("is-error", !!error);
    metaEl.textContent = show ? String(text) : "";
    if (loading) metaEl.setAttribute("aria-busy", "true");
    else metaEl.removeAttribute("aria-busy");
  }

  function setTabLoading(busy, label) {
    const scroll = pane.querySelector(".results-scroll");
    if (busy && label) setMeta(label, { loading: true });
    else if (!busy && metaEl) {
      metaEl.classList.remove("is-loading", "is-busy");
      metaEl.removeAttribute("aria-busy");
    }
    if (scroll) {
      scroll.classList.toggle("is-loading", !!busy);
      scroll.setAttribute("aria-busy", busy ? "true" : "false");
    }
  }

  async function showTab(tab, { openMobile = true, load = true } = {}) {
    const name = tab || "watching";
    tabButtons.forEach((btn) => {
      const on = btn.dataset.tab === name;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    tabPanels.forEach((panel) => {
      const on = panel.dataset.tabPanel === name;
      panel.classList.toggle("active", on);
      if (on) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    });
    document.querySelectorAll("[data-results-tab]").forEach((el) => {
      el.classList.toggle("active", el.dataset.resultsTab === name);
    });
    const scroll = pane.querySelector(".results-scroll");
    if (scroll) scroll.scrollTop = 0;
    setMeta("");
    if (openMobile && isDrawerViewport()) {
      openResults();
    }
    if (!load) return;
    const loadLabels = {
      quant: "加载枢纽…",
      watching: "加载观察…",
      strategy: "加载策略…",
      replay: "加载回溯…",
      follow: "加载模拟…",
      paper: "加载纸面…",
      evals: "加载校验…",
      usage: "加载用量…",
      platform: "加载平台…",
    };
    try {
      if (loadLabels[name]) setTabLoading(true, loadLabels[name]);
      await Promise.race([
        ensureTabData(name),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("加载超时，请稍后点标签重试")), 15000)
        ),
      ]);
      setMeta("");
    } catch (err) {
      setMeta(String(err.message || err), { error: true });
    } finally {
      setTabLoading(false);
    }
  }

  async function applyChatArtifacts(payload = {}) {
    const artifacts = payload.artifacts || [];
    const replyBody = payload.replyBody || "";
    let tab = payload.primary_tab || "reply";
    if (tab === "quant") {
      const last = artifacts[artifacts.length - 1] || {};
      const task = String((last.params && last.params.task) || (last.data && last.data.task) || "").toLowerCase();
      if (task.includes("t0") || task.includes("paper") || task.includes("bridge")) tab = "follow";
      else if (task.includes("backtest") || task.includes("cross") || task.includes("neutral")) tab = "replay";
      else tab = "strategy";
    }

    renderArtifactCards(artifacts);
    setResultsReply(replyBody, { openMobile: false, switchTab: false });

    await showTab(tab, { openMobile: true, load: false });
    if (artifacts.length) {
      const last = artifacts[artifacts.length - 1];
      setMeta(`Agent · ${last.summary || tab}`);
    }

    const byTab = {};
    for (const art of artifacts) {
      if (!art || !art.success) continue;
      const t = art.tab === "quant" ? tab : art.tab || "reply";
      byTab[t] = art;
    }

    try {
      if (["strategy", "replay", "quant", "watching"].includes(tab) || byTab.strategy || byTab.replay) {
        const art =
          byTab[tab] ||
          artifacts.filter((a) => a.tool === "quant" || a.tool === "backtest").pop();
        if (art && typeof ctx.applyQuantArtifact === "function") {
          await ctx.applyQuantArtifact(art);
          loaded[tab] = true;
        } else if (typeof ctx.openQuantDialog === "function") {
          await ctx.openQuantDialog();
          loaded[tab] = true;
        }
      }
      if (tab === "paper" || tab === "follow" || byTab.paper || byTab.follow) {
        if (typeof ctx.reloadPaper === "function") {
          await ctx.reloadPaper();
          loaded.paper = true;
          loaded.follow = true;
        }
      }
    } catch (err) {
      if (metaEl) metaEl.textContent = String(err.message || err);
    }
  }

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      showTab(btn.dataset.tab);
    });
  });

  // 委托绑定：面板内容是动态渲染的，静态遍历会漏掉后插入的跳转链接
  document.addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest("[data-results-tab]");
    if (!el) return;
    e.preventDefault();
    showTab(el.dataset.resultsTab);
  });

  document.body.addEventListener("click", (e) => {
    if (!document.body.classList.contains("results-open") || !isDrawerViewport()) return;
    if (pane.contains(e.target)) return;
    if (e.target.closest && e.target.closest(".topbar")) return;
    closeResults();
  });
  ctx.setResultsReply = setResultsReply;
  ctx.openResults = openResults;
  ctx.closeResults = closeResults;
  ctx.showResultsTab = showTab;
  ctx.applyChatArtifacts = applyChatArtifacts;
}
