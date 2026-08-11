import {
  headers,
  hideIntro,
  showIntro,
  scrollToBottom,
} from "./shared.js";

/** Chat, messages, form, usage, health. */
export function initChat(ctx) {
  const messagesEl = document.getElementById("messages");
  const stageEl = document.getElementById("stage");
  const introEl = document.getElementById("intro");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const statusEl = document.getElementById("status");
  const disclaimerEl = document.getElementById("disclaimer");
  const turnMetaEl = document.getElementById("turn-meta");
  const usageDialog = document.getElementById("usage-dialog");
  const usageBody = document.getElementById("usage-body");
  const isChat = !!form && !!input;

  function autoResize() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }

  function syncSendEnabled() {
    if (!sendBtn || !input) return;
    sendBtn.disabled = ctx.busy || !input.value.trim();
  }

  function splitReply(text) {
    const raw = text || "";
    const marker = "\n\n---\n";
    const idx = raw.lastIndexOf(marker);
    if (idx === -1) return { body: raw, meta: "" };
    const after = raw.slice(idx + marker.length).trim();
    if (/token|prompt|completion|calls/i.test(after) || /用量|token/i.test(after)) {
      return { body: raw.slice(0, idx).trimEnd(), meta: after };
    }
    return { body: raw, meta: "" };
  }

  function appendMessage(role, htmlOrText, { pending = false, raw = false, meta = "" } = {}) {
    hideIntro(introEl);
    const wrap = document.createElement("article");
    wrap.className = `msg ${role}${pending ? " pending" : ""}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "你" : "I";
    avatar.setAttribute("aria-hidden", "true");

    const col = document.createElement("div");
    col.className = "col";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    if (pending) {
      bubble.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    } else if (raw || role === "user") {
      bubble.textContent = htmlOrText;
    } else if (window.marked) {
      bubble.innerHTML = marked.parse(htmlOrText || "");
    } else {
      bubble.textContent = htmlOrText;
    }

    if (role !== "user") {
      const roleEl = document.createElement("div");
      roleEl.className = "role";
      roleEl.textContent = "QuantLab";
      col.appendChild(roleEl);
    }
    col.appendChild(bubble);

    if (meta) {
      const metaEl = document.createElement("div");
      metaEl.className = "meta";
      metaEl.textContent = meta;
      col.appendChild(metaEl);
    }

    wrap.appendChild(avatar);
    wrap.appendChild(col);
    messagesEl.appendChild(wrap);
    scrollToBottom(stageEl);
    return wrap;
  }

  function formatUsage(u = {}) {
    const p = u.prompt_tokens || 0;
    const c = u.completion_tokens || 0;
    const t = u.total_tokens || 0;
    const n = u.calls || 0;
    const r = u.reasoning_tokens || 0;
    let s = `prompt ${p} · completion ${c} · total ${t} · calls ${n}`;
    if (r) s += ` · reasoning ${r}`;
    return s;
  }

  function setTurnMeta(u) {
    if (!turnMetaEl) return;
    if (!u || !(u.total_tokens || u.calls)) {
      turnMetaEl.hidden = true;
      turnMetaEl.textContent = "";
      return;
    }
    turnMetaEl.hidden = false;
    turnMetaEl.textContent = `本轮 ${formatUsage(u)}`;
  }

  function renderUsageDl(data) {
    if (!usageBody) return;
    usageBody.innerHTML = "";
    const rows = [
      ["会话", data.session_id || ctx.sessionId || "—"],
      ["本轮", formatUsage(data.turn_usage)],
      ["累计", formatUsage(data.session_usage)],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      usageBody.appendChild(dt);
      usageBody.appendChild(dd);
    }
  }

  async function refreshUsage({ showDialog = false } = {}) {
    if (!usageBody) return;
    try {
      const res = await fetch("/api/usage", { headers: headers(ctx) });
      const data = await res.json();
      renderUsageDl(data);
    } catch (err) {
      usageBody.innerHTML = "";
      const dt = document.createElement("dt");
      dt.textContent = "错误";
      const dd = document.createElement("dd");
      dd.textContent = String(err.message || err);
      usageBody.appendChild(dt);
      usageBody.appendChild(dd);
    }
    if (showDialog && usageDialog && typeof usageDialog.showModal === "function") {
      usageDialog.showModal();
    }
  }

  async function openUsage() {
    if (document.body.dataset.page === "chat" && ctx.showResultsTab) {
      await ctx.showResultsTab("usage", { openMobile: true, load: true });
      return;
    }
    await refreshUsage({ showDialog: true });
  }

  ctx.refreshUsage = refreshUsage;
  ctx.openUsage = openUsage;

  const usageRefreshBtn = document.getElementById("usage-refresh");
  if (usageRefreshBtn) {
    usageRefreshBtn.addEventListener("click", () => refreshUsage());
  }

  let llmAvailable = false;

  async function refreshHealth() {
    if (!statusEl) return;
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      if (data.disclaimer && disclaimerEl) disclaimerEl.textContent = data.disclaimer;
      llmAvailable = !!data.llm_available;
      ctx.llmAvailable = llmAvailable;
      if (data.llm_available) {
        statusEl.textContent = "在线";
        statusEl.className = "status ok";
        statusEl.title = data.model || "";
      } else if (data.llm_configured) {
        statusEl.textContent = "模型不可用";
        statusEl.className = "status bad";
        statusEl.title = data.llm_error || "";
      } else {
        statusEl.textContent = "未配置 Key";
        statusEl.className = "status bad";
        statusEl.title = "请在 .env 配置 DASHSCOPE_API_KEY";
      }
    } catch (e) {
      statusEl.textContent = "未连接";
      statusEl.className = "status bad";
    }
  }

  ctx.refreshHealth = refreshHealth;
  ctx.autoResize = autoResize;
  ctx.syncSendEnabled = syncSendEnabled;
  ctx.focusInput = () => input && input.focus();
  ctx.sendMessage = null;

  if (!isChat) {
    return;
  }

  // classic chat_boot.js 已接管发送：避免双重绑定，只挂载 artifacts 回调
  if (document.body.dataset.chatReady === "classic" && window.__investmentSend) {
    ctx.sendMessage = (text) => window.__investmentSend(text);
    window.__investmentOnChatReply = (data) => {
      const { body } = splitReply(data.reply || "");
      setTurnMeta(data.turn_usage);
      if (ctx.applyChatArtifacts) {
        Promise.resolve(
          ctx.applyChatArtifacts({
            artifacts: data.artifacts || [],
            primary_tab: data.primary_tab || "reply",
            replyBody: body,
          })
        ).catch((err) => console.error("[QuantLab] applyChatArtifacts", err));
      } else if (ctx.setResultsReply) {
        ctx.setResultsReply(body, { openMobile: false, switchTab: false });
      }
    };
    const btnReset = document.getElementById("btn-reset");
    if (btnReset && btnReset.tagName === "BUTTON") {
      btnReset.addEventListener("click", async () => {
        try {
          const res = await fetch("/api/reset", { method: "POST", headers: headers(ctx) });
          const data = await res.json();
          if (data.session_id) {
            ctx.sessionId = data.session_id;
            localStorage.setItem(ctx.SESSION_KEY, ctx.sessionId);
          }
          messagesEl.innerHTML = "";
          showIntro(introEl);
          turnMetaEl.hidden = true;
          turnMetaEl.textContent = "";
          if (ctx.setResultsReply) ctx.setResultsReply("");
          input.focus();
        } catch (err) {
          appendMessage("assistant", `重置失败：${err.message || err}`, { raw: true });
        }
      });
    }
    // open=/tab= 由 app.js bootWorkspace 统一处理
    return;
  }

  async function sendMessage(text) {
    const content = (text || "").trim();
    if (!content || ctx.busy) return;
    ctx.busy = true;
    syncSendEnabled();
    appendMessage("user", content, { raw: true });
    input.value = "";
    autoResize();
    syncSendEnabled();
    const pending = appendMessage("assistant", "", { pending: true });

    try {
      const res = await fetch("/api/chat/async", {
        method: "POST",
        headers: headers(ctx),
        body: JSON.stringify({ message: content }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || res.statusText || "请求失败");
      }
      if (data.session_id) {
        ctx.sessionId = data.session_id;
        localStorage.setItem(ctx.SESSION_KEY, ctx.sessionId);
      }
      const jobId = (data.job && data.job.id) || null;
      const started = Date.now();
      let result = null;
      while (Date.now() - started < 10 * 60 * 1000) {
        const jr = await fetch("/api/jobs/chat");
        const jd = await jr.json().catch(() => ({}));
        const job = (jd && jd.job) || {};
        if (jobId && job.id && job.id !== jobId) {
          throw new Error("对话任务已被其它任务覆盖，请重试");
        }
        if (job.status === "failed") {
          throw new Error(job.error || "对话失败");
        }
        if (job.status === "done" || job.status === "succeeded") {
          result = job.result || {};
          break;
        }
        await new Promise((r) => setTimeout(r, 600));
      }
      if (!result) throw new Error("对话超时，请稍后重试");
      if (result.session_id) {
        ctx.sessionId = result.session_id;
        localStorage.setItem(ctx.SESSION_KEY, ctx.sessionId);
      }
      pending.remove();
      const { body, meta } = splitReply(result.reply || "");
      const usageMeta =
        meta ||
        (result.turn_usage && (result.turn_usage.total_tokens || result.turn_usage.calls)
          ? formatUsage(result.turn_usage)
          : "");
      appendMessage("assistant", body, { meta: usageMeta });
      setTurnMeta(result.turn_usage);
      // 先释放输入，避免右侧面板加载卡住时对话框无法再发
      ctx.busy = false;
      syncSendEnabled();
      input.focus();
      if (ctx.applyChatArtifacts) {
        Promise.resolve(
          ctx.applyChatArtifacts({
            artifacts: result.artifacts || [],
            primary_tab: result.primary_tab || "reply",
            replyBody: body,
          })
        ).catch((err) => console.error("[QuantLab] applyChatArtifacts", err));
      } else if (ctx.setResultsReply) {
        ctx.setResultsReply(body, { openMobile: false, switchTab: false });
      }
    } catch (err) {
      pending.remove();
      appendMessage("assistant", `出错了：${err.message || err}`, { raw: true });
    } finally {
      ctx.busy = false;
      syncSendEnabled();
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(input.value);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input.value);
    }
  });

  input.addEventListener("input", () => {
    autoResize();
    syncSendEnabled();
  });
  input.addEventListener("compositionend", () => {
    syncSendEnabled();
  });

  // 点击发送：即使 button disabled 属性异常，也允许从 keydown/芯片触发
  if (sendBtn) {
    sendBtn.addEventListener("click", (e) => {
      e.preventDefault();
      sendMessage(input.value);
    });
  }
  const chips = document.getElementById("chips");
  if (chips) {
    chips.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-q]");
      if (!btn) return;
      sendMessage(btn.dataset.q);
    });
  }

  const btnReset = document.getElementById("btn-reset");
  if (btnReset && btnReset.tagName === "BUTTON") {
    btnReset.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/reset", { method: "POST", headers: headers(ctx) });
        const data = await res.json();
        if (data.session_id) {
          ctx.sessionId = data.session_id;
          localStorage.setItem(ctx.SESSION_KEY, ctx.sessionId);
        }
        messagesEl.innerHTML = "";
        showIntro(introEl);
        turnMetaEl.hidden = true;
        turnMetaEl.textContent = "";
        if (ctx.setResultsReply) ctx.setResultsReply("");
        input.focus();
      } catch (err) {
        appendMessage("assistant", `重置失败：${err.message || err}`, { raw: true });
      }
    });
  }

  const btnUsage = document.getElementById("btn-usage");
  if (btnUsage && btnUsage.tagName === "BUTTON") {
    btnUsage.addEventListener("click", () => openUsage());
  }

  ctx.sendMessage = sendMessage;

  const pendingChat = sessionStorage.getItem("pending_chat");
  if (pendingChat) {
    sessionStorage.removeItem("pending_chat");
    setTimeout(() => sendMessage(pendingChat), 0);
  }

  const open = new URLSearchParams(location.search).get("open");
  if (open === "usage") {
    setTimeout(() => openUsage(), 0);
  } else if (open === "evals") {
    setTimeout(() => {
      if (ctx.showResultsTab) ctx.showResultsTab("evals", { openMobile: true, load: true });
      else document.getElementById("btn-evals")?.click();
    }, 50);
  }
}
