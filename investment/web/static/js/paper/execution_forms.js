/** 调仓 / 做 T 规则表单：保存与回填。历史回测改规则，交易执行只读落地。 */

import {
  fillExecutionForm,
  collectExecutionForm,
  fillPathMatrixForm,
  collectPathMatrixForm,
  fillDualScoreForm,
  collectDualScorePatch,
  persistT0Lookback,
  renderExecutionDiffHtml,
  renderExecutionRulesHtml,
  renderRebalanceRulesHtml,
} from "./execution_ui.js?v=p2202";

async function postJson(url, body, { timeoutMs = 12000, method = "POST" } = {}) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method,
      headers: body != null ? { "Content-Type": "application/json" } : undefined,
      body: body != null ? JSON.stringify(body) : undefined,
      signal: ac.signal,
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
  } finally {
    clearTimeout(timer);
  }
}

function isAbortError(err) {
  return !!(
    err &&
    (err.name === "AbortError" || /aborted|AbortError/i.test(String(err.message || err)))
  );
}

async function loadDualScoreOntoForm(form) {
  if (!form) return;
  try {
    const res = await fetch("/api/signal/config/dual-score");
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.dual_score) fillDualScoreForm(form, data.dual_score);
  } catch (_) {
    /* 缺接口时保留表单默认 */
  }
}

export function applyExecutionToUi(exec) {
  const t0RulesEl = document.getElementById("paper-t0-rules");
  if (t0RulesEl && exec) t0RulesEl.innerHTML = renderExecutionRulesHtml(exec);
  const rbRulesEl = document.getElementById("paper-rebalance-rules");
  if (rbRulesEl && exec) rbRulesEl.innerHTML = renderRebalanceRulesHtml(exec);
  const paperT0Form = document.getElementById("paper-t0-form");
  if (paperT0Form && exec) fillExecutionForm(paperT0Form, exec);
  const pathMatrixForm = document.getElementById("paper-path-matrix-form");
  if (pathMatrixForm && exec) {
    fillPathMatrixForm(pathMatrixForm, exec);
    loadDualScoreOntoForm(pathMatrixForm);
  }
}

/**
 * 接线规则表单（仅页面上有表单时生效，即历史回测）。
 * @returns {Promise<void>}
 */
export async function initExecutionRuleForms() {
  const paperT0Form = document.getElementById("paper-t0-form");
  const paperPathMatrixForm = document.getElementById("paper-path-matrix-form");
  if (!paperT0Form && !paperPathMatrixForm) return;

  const setT0EditStatus = (text) => {
    const el = document.getElementById("paper-t0-edit-status");
    if (el) el.textContent = text || "";
  };
  const paperT0DiffBox = document.getElementById("paper-t0-diff-box");
  const paperPathMatrixStatus = document.getElementById("paper-path-matrix-status");
  const setPathMatrixStatus = (msg, { error = false } = {}) => {
    if (!paperPathMatrixStatus) return;
    const text = String(msg || "").trim();
    paperPathMatrixStatus.classList.remove("is-busy", "is-ok");
    paperPathMatrixStatus.classList.toggle("is-error", !!error && !!text);
    paperPathMatrixStatus.textContent = text;
    paperPathMatrixStatus.hidden = !text;
  };

  if (paperT0Form && paperT0Form.dataset.wired !== "1") {
    paperT0Form.dataset.wired = "1";
    paperT0Form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = collectExecutionForm(paperT0Form);
      if (!body) return;
      persistT0Lookback(paperT0Form);
      const saveBtn = document.getElementById("paper-t0-save");
      if (saveBtn) saveBtn.disabled = true;
      setT0EditStatus("保存中…");
      try {
        const { res, data } = await postJson("/api/paper/execution", {
          ...body,
          note: "replay UI",
        });
        if (!res.ok || data.ok === false) {
          const err = data.detail || data.errors || data.error || "保存失败";
          setT0EditStatus(Array.isArray(err) ? err.join("; ") : String(err));
          return;
        }
        setT0EditStatus(data.message || "已保存 · 交易执行将按此落地");
        applyExecutionToUi(data.execution);
      } catch (err) {
        setT0EditStatus(isAbortError(err) ? "保存超时，请重试" : String(err.message || err));
      } finally {
        if (saveBtn) saveBtn.disabled = false;
      }
    });
  }

  const paperPathMatrixSave = document.getElementById("paper-path-matrix-save");
  const paperPathMatrixReset = document.getElementById("paper-path-matrix-reset");
  const setPathMatrixBusy = (busy) => {
    if (paperPathMatrixSave) paperPathMatrixSave.disabled = !!busy;
    if (paperPathMatrixReset) paperPathMatrixReset.disabled = !!busy;
  };
  async function savePathMatrix({ okMsg = "已保存" } = {}) {
    const body = collectPathMatrixForm(paperPathMatrixForm);
    const dualPatch = collectDualScorePatch(paperPathMatrixForm);
    if (!body) return false;
    setPathMatrixBusy(true);
    setPathMatrixStatus("保存中…");
    try {
      const { res, data } = await postJson("/api/paper/execution", {
        ...body,
        note: "replay rank_lots UI",
      });
      if (!res.ok || data.ok === false) {
        const err = data.detail || data.errors || data.error || "保存失败";
        setPathMatrixStatus(
          Array.isArray(err) ? err.join("; ") : String(err),
          { error: true }
        );
        return false;
      }
      if (dualPatch) {
        const dualRes = await fetch("/api/signal/config/dual-score", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(dualPatch),
        });
        const dualData = await dualRes.json().catch(() => ({}));
        if (!dualRes.ok || dualData.success === false) {
          setPathMatrixStatus(
            dualData.detail || dualData.error || "y_fuse 已保存，y_trade 权重未写入",
            { error: true }
          );
          applyExecutionToUi(data.execution);
          return false;
        }
        fillDualScoreForm(paperPathMatrixForm, dualData.dual_score);
      }
      setPathMatrixStatus(okMsg);
      applyExecutionToUi(data.execution);
      return true;
    } catch (err) {
      setPathMatrixStatus(
        isAbortError(err) ? "保存超时，请重试" : String(err.message || err),
        { error: true }
      );
      return false;
    } finally {
      setPathMatrixBusy(false);
    }
  }

  if (paperPathMatrixForm && paperPathMatrixForm.dataset.wired !== "1") {
    paperPathMatrixForm.dataset.wired = "1";
    paperPathMatrixForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      await savePathMatrix({ okMsg: "已保存" });
    });
  }

  if (paperPathMatrixReset && paperPathMatrixReset.dataset.wired !== "1") {
    paperPathMatrixReset.dataset.wired = "1";
    paperPathMatrixReset.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!paperPathMatrixForm) return;
      paperPathMatrixForm.reset();
      await savePathMatrix({ okMsg: "已恢复默认" });
    });
  }

  const paperT0Reset = document.getElementById("paper-t0-reset");
  if (paperT0Reset && paperT0Reset.dataset.wired !== "1") {
    paperT0Reset.dataset.wired = "1";
    paperT0Reset.addEventListener("click", async (e) => {
      e.preventDefault();
      setT0EditStatus("重置中…");
      try {
        const { res, data } = await postJson("/api/paper/execution/reset", null);
        if (!res.ok) {
          setT0EditStatus(data.detail || "重置失败");
          return;
        }
        setT0EditStatus(data.message || "已恢复默认");
        if (paperT0DiffBox) {
          paperT0DiffBox.hidden = true;
          paperT0DiffBox.innerHTML = "";
        }
        persistT0Lookback(paperT0Form);
        applyExecutionToUi(data.execution);
      } catch (err) {
        setT0EditStatus(isAbortError(err) ? "重置超时，请重试" : String(err.message || err));
      }
    });
  }

  const paperT0DiffBtn = document.getElementById("paper-t0-diff");
  if (paperT0DiffBtn && paperT0DiffBtn.dataset.wired !== "1") {
    paperT0DiffBtn.dataset.wired = "1";
    paperT0DiffBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      setT0EditStatus("对照中…");
      try {
        const res = await fetch("/api/paper/execution/diff");
        const data = await res.json();
        if (!res.ok) {
          setT0EditStatus(data.detail || "对照失败");
          return;
        }
        if (paperT0DiffBox) {
          paperT0DiffBox.hidden = false;
          paperT0DiffBox.innerHTML = renderExecutionDiffHtml(data);
        }
        setT0EditStatus(
          data.changed
            ? `相对 Spec 有 ${(data.t0_changes || []).length + (data.coupling_changes || []).length} 项差异`
            : "与策略默认一致"
        );
      } catch (err) {
        setT0EditStatus(String(err.message || err));
      }
    });
  }

  try {
    const res = await fetch("/api/paper/execution");
    const exe = await res.json().catch(() => ({}));
    if (res.ok && exe && exe.ok !== false) applyExecutionToUi(exe);
  } catch (_) {
    /* 未初始化纸面时表单保持默认 */
  }
}
