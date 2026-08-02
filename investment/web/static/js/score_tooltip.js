/** 评分悬浮注释（交易执行持仓 / 数据中心观察表共用）。 */

import { escapeText } from "./paper/fmt.js";

export function formatWeightSourceNote(raw) {
  const src = String((raw && raw.weight_source) || "").trim();
  const mode = String((raw && raw.cluster_mode) || "").trim();
  const label = raw && raw.cluster_label ? String(raw.cluster_label) : "";
  const ver =
    raw && raw.cluster_version != null && raw.cluster_version !== ""
      ? `v${raw.cluster_version}`
      : "";
  let title = "权重来源";
  let line = "全局 signal_config.weights";
  if (src.startsWith("cluster:")) {
    line = `分组权 · ${src.slice("cluster:".length) || label || "组"}`;
    if (ver) line += ` · ${ver}`;
  } else if (src === "global+shadow") {
    line = "主分=全局权 · 影子对照已算组权分";
    if (label) line += `（${label}）`;
  } else if (src === "global_fallback") {
    line = "全局权回退（未映射到分组）";
  } else if (src === "global" || !src) {
    if (mode === "off" || !mode) line = "全局 signal_config.weights";
    else line = `全局权 · mode=${mode}`;
  } else {
    line = src;
  }
  const bits = [
    `<div class="score-section-title">${title}</div>`,
    `<div class="score-weight-source">${escapeText(line)}</div>`,
  ];
  if (raw && (raw.score_global != null || raw.score_cluster != null)) {
    const g =
      raw.score_global != null ? Number(raw.score_global).toFixed(1) : "—";
    const c =
      raw.score_cluster != null ? Number(raw.score_cluster).toFixed(1) : "—";
    bits.push(
      `<div class="score-weight-dual sub">全局分 ${escapeText(g)} · 组权分 ${escapeText(
        c
      )}</div>`
    );
  }
  return `<div class="score-weight-section">${bits.join("")}</div>`;
}

export function createScoreTooltipController() {
  let tipEl = null;
  let tipAnchor = null;

  function hide() {
    if (tipEl) {
      tipEl.remove();
      tipEl = null;
    }
    tipAnchor = null;
  }

  function showPlain(anchor, text) {
    const msg = String(text || "").trim();
    if (!anchor || !msg) return;
    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip plain-hover-tip";
    tip.innerHTML = `<div class="plain-hover-tip-body">${escapeText(msg)}</div>`;
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = anchor;
    place(tip, anchor);
  }

  function show(cell, { sticky = false } = {}) {
    let raw;
    try {
      raw = JSON.parse(cell.dataset.scoreDetail || "{}");
    } catch (_) {
      raw = {};
    }
    const formula = raw.formula || "";
    const reasons = raw.reasons || [];
    const hardReject = raw.hard_reject;
    const rejectReason = raw.reject_reason || "";
    const hasWeight = !!(raw.weight_source || raw.cluster_mode || raw.cluster_label);

    if (!formula && !reasons.length && !hardReject && !hasWeight) return;

    let html = '<div class="score-detail">';
    html += formatWeightSourceNote(raw);
    if (formula) {
      const highlighted = formula.replace(
        /(\d+\.\d+|[=+\-×])/g,
        '<span class="score-formula-highlight">$1</span>'
      );
      html += `<div class="score-formula-section">
        <div class="score-section-title">评分公式</div>
        <div class="score-formula">${highlighted}</div>
      </div>`;
    }
    if (hardReject && rejectReason) {
      html += `<div class="score-detail-reject">
        <span class="score-detail-reject-icon">⚠</span>
        <span class="score-detail-reject-text">${escapeText(rejectReason)}</span>
      </div>`;
    }
    if (reasons.length) {
      html += `<div class="score-reasons-section">
        <div class="score-section-title">评分理由</div>
        <ul class="score-reasons">`;
      reasons.forEach((rsn) => {
        let cls = "neutral";
        if (/强于|高于|上升|增加|优秀|良好|高/.test(rsn)) cls = "pos";
        else if (/弱于|低于|下降|减少|较差|低/.test(rsn)) cls = "neg";
        html += `<li class="${cls}">${escapeText(rsn)}</li>`;
      });
      html += "</ul></div>";
    }
    html += "</div>";

    hide();
    const tip = document.createElement("div");
    tip.className = "score-tooltip";
    tip.innerHTML = html;
    document.body.appendChild(tip);
    tipEl = tip;
    tipAnchor = cell;
    place(tip, cell);

    if (sticky) {
      tip.dataset.sticky = "1";
      const close = (ev) => {
        if (tip.contains(ev.target) || cell.contains(ev.target)) return;
        hide();
        document.removeEventListener("click", close, true);
      };
      setTimeout(() => document.addEventListener("click", close, true), 0);
    }
  }

  function place(tip, anchor) {
    const rect = anchor.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - tipRect.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
    let top = rect.bottom + 6;
    if (top + tipRect.height > window.innerHeight - 8) {
      top = rect.top - tipRect.height - 6;
    }
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function bindHost(host, { scoreSelector = "[data-score-detail]" } = {}) {
    if (!host || host.dataset.scoreTipWired === "1") return;
    host.dataset.scoreTipWired = "1";
    host.addEventListener("click", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      e.preventDefault();
      e.stopPropagation();
      show(cell, { sticky: true });
    });
    host.addEventListener("mouseover", (e) => {
      const cell = e.target.closest(scoreSelector);
      if (!cell || !host.contains(cell)) return;
      if (tipAnchor === cell && tipEl) return;
      show(cell, { sticky: false });
    });
    host.addEventListener("mouseout", (e) => {
      const from = e.target.closest(scoreSelector);
      if (!from) return;
      const to = e.relatedTarget;
      if (to && from.contains(to)) return;
      if (tipEl && to && tipEl.contains(to)) return;
      if (tipEl && tipEl.dataset.sticky === "1") return;
      hide();
    });
  }

  return {
    show,
    showPlain,
    hide,
    bindHost,
    get tipEl() {
      return tipEl;
    },
    get tipAnchor() {
      return tipAnchor;
    },
  };
}
