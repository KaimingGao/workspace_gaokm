/**
 * 对话兜底启动（非 module）：不依赖 import / quant.js。
 * 只要本脚本执行成功，输入框就能发消息。
 */
(function () {
  if (window.__investmentChatBooted) return;
  window.__investmentChatBooted = true;

  var SESSION_KEY = "investment_session_id";
  var form = document.getElementById("form");
  var input = document.getElementById("input");
  var messages = document.getElementById("messages");
  var sendBtn = document.getElementById("send");
  var statusEl = document.getElementById("status");
  var busy = false;
  var sessionId = "";

  try {
    sessionId = localStorage.getItem(SESSION_KEY) || "";
  } catch (e) {
    sessionId = "";
  }

  if (!form || !input || !messages) {
    if (statusEl) statusEl.textContent = "对话控件缺失";
    return;
  }

  function headers() {
    var h = { "Content-Type": "application/json" };
    if (sessionId) h["X-Session-Id"] = sessionId;
    return h;
  }

  function syncSend() {
    if (!sendBtn) return;
    var empty = !(input.value || "").trim();
    sendBtn.disabled = busy || empty;
    sendBtn.setAttribute("aria-disabled", busy || empty ? "true" : "false");
  }

  function append(role, text, pending) {
    var intro = document.getElementById("intro");
    if (intro) intro.hidden = true;
    var el = document.createElement("article");
    el.className = "msg " + role + (pending ? " pending" : "");
    var bubbleHtml = pending
      ? '<span class="typing"><i></i><i></i><i></i></span>'
      : "";
    el.innerHTML =
      '<div class="avatar" aria-hidden="true">' +
      (role === "user" ? "你" : "I") +
      '</div><div class="col">' +
      (role === "user" ? "" : '<div class="role">QuantLab</div>') +
      '<div class="bubble">' +
      bubbleHtml +
      "</div></div>";
    var bubble = el.querySelector(".bubble");
    if (!pending) {
      if (role !== "user" && window.marked) {
        try {
          bubble.innerHTML = marked.parse(text || "");
        } catch (err) {
          bubble.textContent = text || "";
        }
      } else {
        bubble.textContent = text || "";
      }
    }
    messages.appendChild(el);
    var stage = document.getElementById("stage");
    if (stage) stage.scrollTop = stage.scrollHeight;
    return el;
  }

  async function waitChatJob(jobId) {
    var started = Date.now();
    var sawOwn = false;
    while (Date.now() - started < 10 * 60 * 1000) {
      var res = await fetch("/api/jobs/chat");
      var data = await res.json().catch(function () {
        return {};
      });
      var job = (data && data.job) || {};
      var same = !jobId || !job.id || job.id === jobId;
      if (same && job.id) sawOwn = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwn || Date.now() - started > 2500) {
          throw new Error("对话任务已中断（可能服务重启），请重试");
        }
        await new Promise(function (r) {
          setTimeout(r, 400);
        });
        continue;
      }
      if (!same) throw new Error("对话任务已被其它任务覆盖，请重试");
      if (job.status === "failed") throw new Error(job.error || "对话失败");
      if (job.status === "done" || job.status === "succeeded") {
        return job.result || {};
      }
      await new Promise(function (r) {
        setTimeout(r, 600);
      });
    }
    throw new Error("对话超时，请稍后重试");
  }

  async function send(text) {
    var content = (text || "").trim();
    if (!content || busy) return;
    busy = true;
    syncSend();
    append("user", content, false);
    input.value = "";
    syncSend();
    var pending = append("assistant", "", true);
    try {
      var res = await fetch("/api/chat/async", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ message: content }),
      });
      var data = await res.json().catch(function () {
        return {};
      });
      if (!res.ok) throw new Error(data.detail || res.statusText || "请求失败");
      if (data.session_id) {
        sessionId = data.session_id;
        try {
          localStorage.setItem(SESSION_KEY, sessionId);
        } catch (e) {}
      }
      var jobId = (data.job && data.job.id) || null;
      var result = await waitChatJob(jobId);
      if (result.session_id) {
        sessionId = result.session_id;
        try {
          localStorage.setItem(SESSION_KEY, sessionId);
        } catch (e) {}
      }
      pending.remove();
      append("assistant", result.reply || "(空回复)", false);
      if (typeof window.__investmentOnChatReply === "function") {
        try {
          window.__investmentOnChatReply(result);
        } catch (err) {
          console.error("[QuantLab] onChatReply", err);
        }
      }
    } catch (err) {
      pending.remove();
      append("assistant", "出错了：" + (err.message || err), false);
    } finally {
      busy = false;
      syncSend();
      input.focus();
    }
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    send(input.value);
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });

  input.addEventListener("input", syncSend);
  input.addEventListener("compositionend", syncSend);

  if (sendBtn) {
    // 不用依赖 disabled 按钮的 click（部分浏览器不触发）
    sendBtn.addEventListener("click", function (e) {
      e.preventDefault();
      send(input.value);
    });
  }

  var chips = document.getElementById("chips");
  if (chips) {
    chips.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("button[data-q]");
      if (!btn) return;
      send(btn.getAttribute("data-q") || "");
    });
  }

  document.body.dataset.chatReady = "classic";
  window.__investmentSend = send;
  syncSend();
  input.focus();

  // 健康检查（不阻塞发消息）
  fetch("/api/health")
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      if (!statusEl) return;
      if (data.llm_available) {
        statusEl.textContent = "在线";
        statusEl.className = "status ok";
      } else if (data.llm_configured) {
        statusEl.textContent = "模型不可用";
        statusEl.className = "status bad";
      } else {
        statusEl.textContent = "未配置 Key";
        statusEl.className = "status bad";
      }
      var disc = document.getElementById("disclaimer");
      if (data.disclaimer && disc) disc.textContent = data.disclaimer;
    })
    .catch(function () {
      if (statusEl) {
        statusEl.textContent = "对话就绪";
        statusEl.className = "status ok";
      }
    });
})();
