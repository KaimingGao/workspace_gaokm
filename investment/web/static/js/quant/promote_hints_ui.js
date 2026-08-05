/**
 * Promote 提示面板 DOM 渲染。
 */
import { escapeHtml } from "../shared.js";

/**
 * @param {{ escapeHtml?: typeof escapeHtml, researchGridHtml: Function, promoteHintsTtlMs: Function }} deps
 */
export function createPromoteHintsRenderer(deps) {
  const esc = deps.escapeHtml || escapeHtml;
  const researchGridHtml = deps.researchGridHtml;
  const promoteHintsTtlMs = deps.promoteHintsTtlMs;

  return function renderPromoteHintsPanel(pack, elId) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!pack) {
      el.innerHTML = "";
      return;
    }
    const ttlFallback = promoteHintsTtlMs();
    if (pack.expired) {
      const when = pack.at
        ? new Date(pack.at).toISOString().slice(0, 19).replace("T", " ")
        : "—";
      el.innerHTML =
        `<p class="quant-trades-caption down">Promote 提示已过期</p>` +
        `<p class="sub">缓存于 ${esc(when)}，TTL ${Math.round(
          (pack.ttl_ms || ttlFallback) / 3600000
        )}h。请回 <a href="/replay">历史回测</a> 重跑 Top-K。</p>`;
      return;
    }
    const hints = pack.hints || [];
    const align = pack.ic_equity_align || {};
    const when = pack.at
      ? new Date(pack.at).toISOString().slice(0, 19).replace("T", " ")
      : "—";
    const ageH =
      pack.at != null
        ? Math.max(0, (Date.now() - Number(pack.at)) / 3600000).toFixed(1)
        : null;
    const ttlH = Math.round((pack.ttl_ms || ttlFallback) / 3600000);
    const head =
      `<p class="quant-trades-caption${hints.length ? " down" : ""}">Promote 提示` +
      ` · lookback=${pack.lookback ?? "—"} / top_k=${pack.top_k ?? "—"}` +
      ` · ${esc(when)}` +
      (ageH != null ? ` · ${ageH}h/${ttlH}h` : "") +
      (pack.total_return_pct != null ? ` · 累计 ${pack.total_return_pct}%` : "") +
      (pack.excess_pct != null ? ` · 超额 ${pack.excess_pct}%` : "") +
      `</p>`;
    if (!hints.length && !(align && align.ok)) {
      el.innerHTML =
        head +
        `<p class="sub">最近 Top-K 无警示。来源：<a href="/replay">历史回测</a>。</p>`;
      return;
    }
    const hintRows = [];
    hints.forEach((h) => {
      hintRows.push({
        level: String(h.level || "warn"),
        code: String(h.code || "—"),
        text: String(h.text || ""),
      });
    });
    if (
      align &&
      align.ok &&
      align.aligned_favor_pos_ic === false &&
      !hints.some((h) => h.code === "ic_align_mismatch")
    ) {
      hintRows.push({
        level: "warn",
        code: "ic_align_mismatch",
        text: `正IC窗均收益未高于非正（差 ${align.avg_return_spread_pp}pp）`,
      });
    }
    el.innerHTML =
      head +
      (hintRows.length
        ? researchGridHtml(
            [
              { id: "level", label: "级别", widthPct: 14, center: true },
              { id: "code", label: "码", widthPct: 26 },
              { id: "text", label: "说明", flex: true },
            ],
            hintRows,
            (col, d) =>
              col.id === "code"
                ? `<code>${esc(d.code)}</code>`
                : esc(d[col.id] ?? "—"),
            { emptyText: "无 warn 项" }
          )
        : `<p class="sub">无 warn 项。</p>`) +
      `<p class="sub">研究警示，默认不硬拦 promote；回测页可开「IC硬闸」；策略页可开「过期硬拦晋升」。提示 TTL ${ttlH}h（可改）。</p>`;
  };
}
