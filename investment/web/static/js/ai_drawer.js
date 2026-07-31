/** 全局 AI 命令抽屉：任意页 ⌘K / 顶栏打开；POST /api/chat */

import { apiFetch } from "./api_client.js";

const SESSION_KEY = "investment_session_id";

const TAB_HREF = {
  watching: { href: "/watching", label: "去数据中心" },
  follow: { href: "/follow", label: "去交易执行" },
  paper: { href: "/follow", label: "去交易执行" },
  strategy: { href: "/strategy", label: "去策略中心" },
  replay: { href: "/replay", label: "去历史回测" },
  quant: { href: "/quant", label: "去研究枢纽" },
  platform: { href: "/platform", label: "去系统设置" },
  reply: { href: "/watching", label: "去数据中心" },
};

function pageContextLabel() {
  const page = document.body?.dataset?.page || "";
  const map = {
    watching: "数据中心",
    follow: "交易执行",
    strategy: "策略中心",
    replay: "历史回测",
    quant: "研究枢纽",
    platform: "系统设置",
  };
  return map[page] || page || "系统";
}

function nameFromStockRow(row) {
  if (!row) return "";
  const named = row.querySelector?.(
    "[data-full-name], .watching-name-text, .paper-wl-name-text"
  );
  if (named) {
    return (
      named.dataset?.fullName ||
      named.getAttribute?.("data-full-name") ||
      named.getAttribute?.("title") ||
      String(named.textContent || "").trim() ||
      ""
    );
  }
  return "";
}

/** 当前页选中的股票：页面 hook → 图表高亮行 → 勾选/持仓选中 */
function resolveCurrentStock() {
  if (typeof window.__investmentGetCurrentStock === "function") {
    try {
      const stock = window.__investmentGetCurrentStock();
      if (stock && stock.code) {
        return {
          code: String(stock.code).trim(),
          name: String(stock.name || "").trim(),
        };
      }
    } catch (_) {
      /* ignore */
    }
  }

  const active = document.querySelector(
    "#watching-watchlist-table .is-chart-active[data-code], " +
      "#paper-holdings-table .is-chart-active[data-code], " +
      "#paper-holdings-table .is-adjust-active[data-code], " +
      "#paper-holdings-table .is-focus-holding[data-code]"
  );
  if (active?.dataset?.code) {
    return {
      code: String(active.dataset.code).trim(),
      name: nameFromStockRow(active),
    };
  }

  const picks = Array.from(
    document.querySelectorAll(
      "#watching-watchlist-table .watching-pick:checked[data-code]"
    )
  )
    .map((el) => String(el.dataset.code || el.value || "").trim())
    .filter(Boolean);
  if (picks.length === 1) {
    const row = document.querySelector(
      `#watching-watchlist-table [data-code="${picks[0].replace(/"/g, "")}"]`
    );
    return { code: picks[0], name: nameFromStockRow(row) };
  }

  return null;
}

function quoteQueryForStock(stock) {
  if (!stock?.code) return "";
  const label = stock.name && stock.name !== "—" ? stock.name : stock.code;
  return `${label}现价`;
}

function sessionHeaders() {
  const h = { "Content-Type": "application/json" };
  try {
    const sid = localStorage.getItem(SESSION_KEY) || "";
    if (sid) h["X-Session-Id"] = sid;
  } catch (_) {
    /* ignore */
  }
  return h;
}

function resolveArtifactTab(art, fallback) {
  let tab = (art && art.tab) || fallback || "reply";
  if (tab === "quant") {
    const task = String(
      (art.params && art.params.task) || (art.data && art.data.task) || ""
    ).toLowerCase();
    if (task.includes("t0") || task.includes("paper") || task.includes("bridge")) {
      tab = "follow";
    } else if (task.includes("backtest") || task.includes("cross") || task.includes("neutral")) {
      tab = "replay";
    } else {
      tab = "strategy";
    }
  }
  return tab;
}

function appendArtifactJumps(container, data) {
  const artifacts = (data && data.artifacts) || [];
  const primary = data && data.primary_tab;
  const tabs = new Set();
  if (primary) tabs.add(resolveArtifactTab({ tab: primary }, primary));
  for (const art of artifacts) {
    if (!art) continue;
    tabs.add(resolveArtifactTab(art, primary || "reply"));
  }
  tabs.delete("reply");
  if (!tabs.size && artifacts.length) tabs.add("chat");
  if (!tabs.size) return;

  const wrap = document.createElement("div");
  wrap.className = "ai-artifact-jumps";
  const seen = new Set();
  for (const tab of tabs) {
    const meta = TAB_HREF[tab] || (tab === "chat" ? TAB_HREF.reply : null);
    if (!meta || seen.has(meta.href)) continue;
    seen.add(meta.href);
    const a = document.createElement("a");
    a.className = "dialog-btn secondary";
    a.href = meta.href;
    a.textContent = meta.label;
    a.addEventListener("click", () => {
      try {
        closeAiDrawer();
      } catch (_) {
        /* ignore */
      }
    });
    wrap.appendChild(a);
  }
  if (wrap.childNodes.length) container.appendChild(wrap);
}

function appendMsg(container, role, text, pending) {
  const el = document.createElement("article");
  el.className = `ai-msg ${role}${pending ? " pending" : ""}`;
  const body = pending
    ? '<span class="typing"><i></i><i></i><i></i></span>'
    : "";
  el.innerHTML =
    `<div class="ai-msg-role">${role === "user" ? "你" : "AI"}</div>` +
    `<div class="ai-msg-bubble">${body}</div>`;
  const bubble = el.querySelector(".ai-msg-bubble");
  if (!pending && bubble) {
    if (role !== "user" && window.marked) {
      try {
        bubble.innerHTML = marked.parse(text || "");
      } catch (_) {
        bubble.textContent = text || "";
      }
    } else {
      bubble.textContent = text || "";
    }
  }
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

export function openAiDrawer(prefill) {
  const drawer = document.getElementById("ai-drawer");
  if (!drawer) return;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("ai-drawer-open");
  const ctx = document.getElementById("ai-drawer-ctx");
  if (ctx) {
    const stock = resolveCurrentStock();
    const stockBit = stock
      ? ` · ${stock.name && stock.name !== "—" ? stock.name : stock.code}`
      : "";
    ctx.textContent = `上下文 · ${pageContextLabel()}${stockBit} · 研究/纸面`;
  }
  const input = document.getElementById("ai-drawer-input");
  if (input) {
    if (prefill) input.value = prefill;
    setTimeout(() => input.focus(), 40);
  }
}

export function closeAiDrawer() {
  const drawer = document.getElementById("ai-drawer");
  if (!drawer) return;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  document.body.classList.remove("ai-drawer-open");
}

export function toggleAiDrawer() {
  const drawer = document.getElementById("ai-drawer");
  if (drawer && drawer.classList.contains("open")) closeAiDrawer();
  else openAiDrawer();
}

export function initAiDrawer() {
  const drawer = document.getElementById("ai-drawer");
  if (!drawer) return;

  const form = document.getElementById("ai-drawer-form");
  const input = document.getElementById("ai-drawer-input");
  const messages = document.getElementById("ai-drawer-messages");
  const sendBtn = document.getElementById("ai-drawer-send");
  let busy = false;

  document.getElementById("btn-ai-open")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (document.body.dataset.page === "chat") {
      const main = document.getElementById("input");
      if (main) {
        main.focus();
        return;
      }
    }
    openAiDrawer();
  });
  document.getElementById("ai-drawer-close")?.addEventListener("click", (e) => {
    e.preventDefault();
    closeAiDrawer();
  });
  document.getElementById("ai-drawer-backdrop")?.addEventListener("click", () => {
    closeAiDrawer();
  });

  document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      toggleAiDrawer();
      return;
    }
    if (e.key === "Escape" && drawer.classList.contains("open")) {
      e.preventDefault();
      closeAiDrawer();
    }
  });

  async function send(text) {
    const content = (text || "").trim();
    if (!content || busy || !messages) return;
    busy = true;
    if (sendBtn) sendBtn.disabled = true;
    appendMsg(messages, "user", content, false);
    if (input) input.value = "";
    const pending = appendMsg(messages, "assistant", "", true);
    try {
      const { ok, data, error } = await apiFetch("/api/chat", {
        method: "POST",
        headers: sessionHeaders(),
        body: JSON.stringify({ message: content }),
      });
      if (!ok) throw new Error(error || data.detail || "请求失败");
      if (data.session_id) {
        try {
          localStorage.setItem(SESSION_KEY, data.session_id);
        } catch (_) {
          /* ignore */
        }
      }
      pending.remove();
      const msgEl = appendMsg(messages, "assistant", data.reply || "(空回复)", false);
      appendArtifactJumps(msgEl, data);
      messages.scrollTop = messages.scrollHeight;
      if (typeof window.__investmentOnChatReply === "function") {
        try {
          window.__investmentOnChatReply(data);
        } catch (err) {
          console.error("[Investment] onChatReply", err);
        }
      }
    } catch (err) {
      pending.remove();
      appendMsg(messages, "assistant", `出错了：${err.message || err}`, false);
    } finally {
      busy = false;
      if (sendBtn) sendBtn.disabled = false;
      input?.focus();
    }
  }

  form?.addEventListener("submit", (e) => {
    e.preventDefault();
    send(input?.value);
  });
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-ai-q]");
    if (!btn) return;
    e.preventDefault();
    let q = btn.getAttribute("data-ai-q") || "";
    if (q === "__current_quote__") {
      const stock = resolveCurrentStock();
      q = quoteQueryForStock(stock);
      if (!q) {
        openAiDrawer();
        if (messages) {
          appendMsg(
            messages,
            "assistant",
            "请先在数据中心点选一只股票，或在交易执行选中持仓，再点「当前行情」。",
            false
          );
        }
        return;
      }
    }
    openAiDrawer(q);
    send(q);
  });

  window.__investmentOpenAi = openAiDrawer;
  window.__investmentCloseAi = closeAiDrawer;
}
