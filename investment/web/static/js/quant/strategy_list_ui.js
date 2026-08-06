/**
 * 策略中心策略列表 HTML。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

/**
 * @param {object} data - /api/strategy/list 响应
 * @param {{ escapeHtml?: typeof defaultEscapeHtml }} [opts]
 * @returns {string}
 */
export function buildStrategyListHtml(data, opts = {}) {
  const esc = opts.escapeHtml || defaultEscapeHtml;
  if (!data || !data.success || !data.strategies) return "";
  const strategies = data.strategies;
  const costLabel = (m) =>
    m === "simple_cn" ? "A股简化成本" : m === "zero" ? "零成本" : m || "—";
  const fmtCell = (label, value) =>
    value == null || value === ""
      ? ""
      : `<div class="strategy-card-kv"><dt>${esc(label)}</dt><dd>${esc(
          String(value)
        )}</dd></div>`;
  const cards = strategies
    .map((s) => {
      const params = s.params || {};
      const risk = s.risk || {};
      const t0 = (((s.execution || {}).overlays || {}).t0) || {};
      const dd =
        risk.target_drawdown_pct != null || risk.max_drawdown_pct != null
          ? `${risk.target_drawdown_pct ?? risk.max_drawdown_pct}%`
          : null;
      const t0v =
        t0.enabled === false
          ? "关"
          : t0.t0_ratio != null
            ? `${Math.round(Number(t0.t0_ratio) * 100)}%`
            : null;
      const kv =
        fmtCell("持有", params.horizon_days != null ? `${params.horizon_days} 日` : null) +
        fmtCell("最多", risk.max_positions != null ? `${risk.max_positions} 只` : null) +
        fmtCell(
          "单票",
          risk.max_position_pct != null ? `≤${risk.max_position_pct}%` : null
        ) +
        fmtCell(
          "行业",
          risk.max_sector_pct != null ? `≤${risk.max_sector_pct}%` : null
        ) +
        fmtCell("回撤", dd) +
        fmtCell("做T", t0v) +
        fmtCell("成本", s.cost_model ? costLabel(s.cost_model) : null);
      const title = s.label || s.strategy_id || s.name || "未命名策略";
      const sid = s.strategy_id || s.name || "";
      return (
        `<article class="strategy-card" data-strategy-id="${esc(sid)}">` +
        `<header class="strategy-card-head">` +
        `<div class="strategy-card-titles">` +
        `<h4 class="strategy-card-title">${esc(title)}</h4>` +
        `<p class="strategy-card-desc">${esc(s.description || "暂无描述")}</p>` +
        `</div>` +
        `<div class="strategy-card-meta">` +
        `<code class="strategy-card-id" title="策略 ID">${esc(sid)}</code>` +
        `<span class="strategy-card-ver">v${esc(String(s.version || "—"))}</span>` +
        `</div>` +
        `</header>` +
        (kv ? `<dl class="strategy-card-grid">${kv}</dl>` : "") +
        `<div class="strategy-card-actions quant-actions-inline">` +
        `<button type="button" class="dialog-btn secondary strategy-promote-btn" data-strategy="${esc(sid)}" data-apply="0">晋升快照</button>` +
        `<button type="button" class="dialog-btn strategy-promote-btn" data-strategy="${esc(sid)}" data-apply="1">晋升并应用到纸面</button>` +
        `</div>` +
        `</article>`
      );
    })
    .join("");
  return `<div class="strategy-card-list">${cards}</div>`;
}

/**
 * @param {object} data
 * @param {{ escapeHtml?: typeof defaultEscapeHtml }} [opts]
 * @returns {{ html: string, buyF: number|null, holdF: number|null }}
 */
export function buildStrategyRiskFootnoteHtml(data, opts = {}) {
  const esc = opts.escapeHtml || defaultEscapeHtml;
  const floors = (data && data.scoring_floors) || {};
  const buyF = floors.min_predicted_score;
  const holdF = floors.min_hold_predicted_score;
  const fmtFloor = (v) =>
    v == null || !Number.isFinite(Number(v))
      ? "—"
      : `${Number(v) >= 0 ? "+" : ""}${Number(v)}%`;
  const floorLine =
    buyF != null || holdF != null
      ? ` live ŷ 滞回：买入/入簿 ≥ ${fmtFloor(buyF)} · 卖出 &lt; ${fmtFloor(holdF)}（signal_config.scoring）。`
      : "";
  const prior = (data && data.sentiment_prior) || {};
  const priorMode = prior.mode ? String(prior.mode) : "";
  const priorLine = priorMode
    ? ` 舆情先验 prior.mode=${esc(priorMode)}（不影响 ŷ）。`
    : "";
  const html =
    `<p class="strategy-footnote">限额 · 成本 · 做T 见上表；改参须人审 promote。` +
    floorLine +
    priorLine +
    ` 选股 β → <a href="/quant">研究枢纽</a> · 验证 → <a href="/replay">回测</a> · 调仓 → <a href="/follow">交易执行</a>。</p>`;
  return { html, buyF, holdF };
}
