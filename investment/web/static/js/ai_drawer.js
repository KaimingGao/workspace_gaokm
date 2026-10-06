/** 全局 AI 命令抽屉：任意页 ⌘K / 顶栏打开；POST /api/chat/async + 轮询 job */

import { apiFetch } from "./api_client.js";

const SESSION_KEY = "investment_session_id";

const TAB_HREF = {
  watching: { href: "/watching", label: "去数据中心" },
  follow: { href: "/follow", label: "去交易执行" },
  paper: { href: "/follow", label: "去交易执行" },
  strategy: { href: "/strategy", label: "去策略中心" },
  replay: { href: "/replay", label: "去历史回测" },
  quant: { href: "/quant", label: "去研究枢纽" },
  platform: { href: "/platform", label: "去平台" },
  reply: { href: "/watching", label: "去数据中心" },
};

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

function formatLlmModelHint(prefs) {
  const effective = prefs.llm_model || "qwen-plus";
  const source = prefs.llm_model_source || "default";
  if (source === "env") {
    return `当前生效 ${effective}（.env · DASHSCOPE_MODEL）`;
  }
  if (source === "memory") {
    return `当前生效 ${effective}（memory.json 遗留项；建议改 .env）`;
  }
  return `当前生效 ${effective}（默认；保存后写入 .env）`;
}

function applyLlmPrefs(prefs) {
  const input = document.getElementById("ai-drawer-llm-model");
  const hint = document.getElementById("ai-drawer-llm-hint");
  const tip = formatLlmModelHint(prefs);
  if (input) {
    input.value = prefs.llm_model_saved || prefs.llm_model || "";
    input.disabled = false;
    input.title = tip;
  }
  if (hint) hint.textContent = tip;
}

function settingsPanel() {
  return document.getElementById("ai-drawer-settings-panel");
}

function settingsBtn() {
  return document.getElementById("ai-drawer-settings-btn");
}

function isSettingsOpen() {
  const panel = settingsPanel();
  return !!(panel && !panel.hidden);
}

function closeSettings() {
  const panel = settingsPanel();
  const btn = settingsBtn();
  if (panel) panel.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function openSettings() {
  const panel = settingsPanel();
  const btn = settingsBtn();
  if (panel) panel.hidden = false;
  if (btn) btn.setAttribute("aria-expanded", "true");
  loadLlmPrefs().catch(() => {});
  setTimeout(() => document.getElementById("ai-drawer-llm-model")?.focus(), 20);
}

function toggleSettings() {
  if (isSettingsOpen()) closeSettings();
  else openSettings();
}

async function loadLlmPrefs() {
  const hint = document.getElementById("ai-drawer-llm-hint");
  try {
    const { ok, data, error } = await apiFetch("/api/memory");
    if (!ok) throw new Error(error || "加载偏好失败");
    applyLlmPrefs((data.effective || data.preferences) || {});
    return data;
  } catch (err) {
    if (hint) hint.textContent = String(err.message || err);
    throw err;
  }
}

async function saveLlmPrefs() {
  const input = document.getElementById("ai-drawer-llm-model");
  const hint = document.getElementById("ai-drawer-llm-hint");
  const btn = document.getElementById("ai-drawer-llm-save");
  const preferences = { llm_model: input ? input.value.trim() : "" };
  if (btn) btn.disabled = true;
  try {
    const { ok, data, error } = await apiFetch("/api/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences }),
    });
    if (!ok) throw new Error(error || (data && data.detail) || "保存失败");
    applyLlmPrefs(data.effective || data.preferences || preferences);
    return data;
  } catch (err) {
    if (hint) hint.textContent = String(err.message || err);
    throw err;
  } finally {
    if (btn) btn.disabled = false;
  }
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
  if (wrap.childNodes.length) {
    const host = container.querySelector(".ai-msg-main") || container;
    host.appendChild(wrap);
  }
}

function syncEmptyState(container) {
  const empty = document.getElementById("ai-drawer-empty");
  if (!empty) return;
  const host = container || document.getElementById("ai-drawer-messages");
  empty.hidden = !!(host && host.querySelector(".ai-msg"));
}

function splitUsageFooter(text) {
  const raw = String(text || "");
  const marker = "\n\n---\n";
  const idx = raw.lastIndexOf(marker);
  if (idx === -1) return { body: raw, meta: "" };
  const after = raw.slice(idx + marker.length).trim();
  if (/token|prompt|completion|calls/i.test(after) || /用量|token/i.test(after)) {
    return { body: raw.slice(0, idx).trimEnd(), meta: after };
  }
  return { body: raw, meta: "" };
}

function parseUsageLine(line) {
  const u = {};
  const pairs = [
    ["prompt", "prompt_tokens"],
    ["completion", "completion_tokens"],
    ["reasoning", "reasoning_tokens"],
    ["total", "total_tokens"],
    ["calls", "calls"],
  ];
  for (const [key, field] of pairs) {
    const m = String(line || "").match(new RegExp(`${key}=(\\d+)`));
    if (m) u[field] = Number(m[1]);
  }
  return u;
}

function usageFromMeta(meta) {
  const out = { turn: null, session: null };
  for (const part of String(meta || "").split(/\s*｜\s*/)) {
    const u = parseUsageLine(part);
    if (!Object.keys(u).length) continue;
    if (/累计/.test(part)) out.session = u;
    else out.turn = u;
  }
  return out;
}

function usageHasCounts(u) {
  return !!(u && (u.total_tokens || u.calls));
}

function usageChipsHtml(u) {
  const items = [
    ["prompt", u.prompt_tokens],
    ["completion", u.completion_tokens],
  ];
  if (u.reasoning_tokens) items.push(["reasoning", u.reasoning_tokens]);
  items.push(["total", u.total_tokens], ["calls", u.calls]);
  return items
    .map(
      ([k, v]) =>
        `<span class="ai-msg-usage-chip"><em>${k}</em><strong>${v ?? 0}</strong></span>`
    )
    .join("");
}

function appendUsageBox(main, turn, session) {
  if (!main || (!usageHasCounts(turn) && !usageHasCounts(session))) return;
  const box = document.createElement("div");
  box.className = "ai-msg-usage";
  let html = "";
  if (usageHasCounts(turn)) {
    html +=
      `<div class="ai-msg-usage-row"><span class="ai-msg-usage-label">本轮</span>` +
      `<span class="ai-msg-usage-chips">${usageChipsHtml(turn)}</span></div>`;
  }
  if (usageHasCounts(session)) {
    html +=
      `<div class="ai-msg-usage-row"><span class="ai-msg-usage-label">累计</span>` +
      `<span class="ai-msg-usage-chips">${usageChipsHtml(session)}</span></div>`;
  }
  box.innerHTML = html;
  main.appendChild(box);
}

function appendMsg(container, role, text, pending, extras) {
  const el = document.createElement("article");
  const isError = !pending && role === "assistant" && String(text || "").startsWith("出错了");
  el.className = `ai-msg ${role}${pending ? " pending" : ""}${isError ? " is-error" : ""}`;
  const pendingBody = pending
    ? '<span class="typing" aria-hidden="true"><i></i><i></i><i></i></span>'
    : "";
  const status = pending
    ? '<p class="ai-msg-status msg-pending-tip">思考中…</p>'
    : "";
  el.innerHTML =
    `<div class="ai-msg-role">${role === "user" ? "你" : "AI"}</div>` +
    `<div class="ai-msg-main">` +
    `<div class="ai-msg-bubble">${pendingBody}</div>` +
    status +
    `</div>`;
  const bubble = el.querySelector(".ai-msg-bubble");
  const main = el.querySelector(".ai-msg-main");
  let body = text;
  if (!pending && role === "assistant" && !isError) {
    const split = splitUsageFooter(text);
    body = split.body;
    const parsed = usageFromMeta(split.meta);
    const turn = usageHasCounts(extras && extras.turn) ? extras.turn : parsed.turn;
    const session = usageHasCounts(extras && extras.session)
      ? extras.session
      : parsed.session;
    appendUsageBox(main, turn, session);
  }
  if (!pending && bubble) {
    if (role !== "user" && window.marked) {
      try {
        bubble.innerHTML = marked.parse(body || "");
      } catch (_) {
        bubble.textContent = body || "";
      }
    } else {
      bubble.textContent = body || "";
    }
  }
  container.appendChild(el);
  syncEmptyState(container);
  container.scrollTop = container.scrollHeight;
  return el;
}

export function openAiDrawer(prefill) {
  const drawer = document.getElementById("ai-drawer");
  if (!drawer) return;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.classList.add("ai-drawer-open");
  syncEmptyState();
  loadLlmPrefs().catch(() => {});
  const input = document.getElementById("ai-drawer-input");
  if (input) {
    if (prefill) input.value = prefill;
    setTimeout(() => {
      if (isSettingsOpen()) return;
      input.focus();
    }, 40);
  }
}

export function closeAiDrawer() {
  const drawer = document.getElementById("ai-drawer");
  if (!drawer) return;
  closeSettings();
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
    openAiDrawer();
  });
  document.getElementById("ai-drawer-backdrop")?.addEventListener("click", () => {
    closeAiDrawer();
  });
  document.getElementById("ai-drawer-settings-btn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleSettings();
  });
  document.getElementById("ai-drawer-llm-save")?.addEventListener("click", (e) => {
    e.preventDefault();
    saveLlmPrefs().catch(() => {});
  });
  document.getElementById("ai-drawer-llm-model")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveLlmPrefs().catch(() => {});
    }
  });
  drawer.addEventListener("click", (e) => {
    if (!isSettingsOpen()) return;
    const wrap = e.target.closest?.(".ai-drawer-settings");
    if (!wrap) closeSettings();
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
      if (isSettingsOpen()) closeSettings();
      else closeAiDrawer();
    }
  });

  async function waitChatJob(jobId, pendingEl) {
    const started = Date.now();
    let sawOwn = false;
    while (Date.now() - started < 10 * 60 * 1000) {
      const { ok, data, error } = await apiFetch("/api/jobs/chat");
      if (!ok) throw new Error(error || "轮询对话任务失败");
      const job = (data && data.job) || {};
      const same = !jobId || !job.id || job.id === jobId;
      if (same && job.id) sawOwn = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwn || Date.now() - started > 2500) {
          throw new Error("对话任务已中断（可能服务重启），请重试");
        }
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      if (!same) throw new Error("对话任务已被其它任务覆盖，请重试");
      if (job.status === "failed") {
        throw new Error(job.error || "对话失败");
      }
      if (job.status === "done" || job.status === "succeeded") {
        return job.result || {};
      }
      if (pendingEl) {
        const msg = job.message || "思考中…";
        const pct = Number(job.pct) || 0;
        const tip =
          pendingEl.querySelector(".msg-pending-tip") ||
          pendingEl.querySelector(".pending") ||
          pendingEl;
        if (tip && tip !== pendingEl) {
          tip.textContent = pct ? `${msg} ${pct}%` : msg;
        } else if (pendingEl.dataset) {
          pendingEl.dataset.jobMsg = msg;
        }
      }
      await new Promise((r) => setTimeout(r, 600));
    }
    throw new Error("对话超时，请稍后重试");
  }

  async function send(text) {
    const content = (text || "").trim();
    if (!content || busy || !messages) return;
    busy = true;
    if (sendBtn) sendBtn.disabled = true;
    form?.classList.add("is-busy");
    appendMsg(messages, "user", content, false);
    if (input) input.value = "";
    const pending = appendMsg(messages, "assistant", "", true);
    try {
      const { ok, data, error } = await apiFetch("/api/chat/async", {
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
      const jobId = (data.job && data.job.id) || null;
      const result = await waitChatJob(jobId, pending);
      if (result.session_id) {
        try {
          localStorage.setItem(SESSION_KEY, result.session_id);
        } catch (_) {
          /* ignore */
        }
      }
      pending.remove();
      const msgEl = appendMsg(
        messages,
        "assistant",
        result.reply || "(空回复)",
        false,
        { turn: result.turn_usage, session: result.session_usage }
      );
      appendArtifactJumps(msgEl, result);
      messages.scrollTop = messages.scrollHeight;
      if (typeof window.__investmentOnChatReply === "function") {
        try {
          window.__investmentOnChatReply(result);
        } catch (err) {
          console.error("[QuantLab] onChatReply", err);
        }
      }
    } catch (err) {
      pending.remove();
      appendMsg(messages, "assistant", `出错了：${err.message || err}`, false);
    } finally {
      busy = false;
      if (sendBtn) sendBtn.disabled = false;
      form?.classList.remove("is-busy");
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
            "请先在数据中心点选一只股票，或在交易执行选中持仓，再点「行情」。",
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
