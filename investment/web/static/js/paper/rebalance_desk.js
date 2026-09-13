/** Paper · 自动调仓今日盯盘状态（格式对齐做 T worker desk）。 */

import { stockCellHtml, stampStockFitTiers } from "./t0_table.js?v=p2297";

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function deskChip(cls, label, value) {
  const v = Number(value || 0);
  if (!v && cls !== "is-univ") return "";
  return (
    `<span class="paper-t0-desk-chip ${cls}">` +
    `<span class="paper-t0-desk-chip-k">${escapeHtml(label)}</span>` +
    `<span class="paper-t0-desk-chip-v">${escapeHtml(String(v))}</span>` +
    `</span>`
  );
}

function actionLabel(action) {
  const a = String(action || "").toLowerCase();
  if (a === "open") return "开";
  if (a === "add") return "加";
  if (a === "exit") return "清";
  if (a === "hold") return "持";
  if (a === "skip") return "跳";
  return "—";
}

function actionCell(action) {
  const a = String(action || "").toLowerCase();
  if (a === "open" || a === "add") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-buy-then-sell" title="${
      a === "open" ? "开仓" : "加仓"
    }">${escapeHtml(actionLabel(a))}</span></td>`;
  }
  if (a === "exit") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-sell-then-buy" title="清仓">清</span></td>`;
  }
  return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-none">${escapeHtml(
    actionLabel(a)
  )}</span></td>`;
}

const ROW_PHASE_LABEL = {
  watch: "监视",
  wait: "监视",
  idle: "监视",
  running: "落账中",
  done: "完成",
  hold: "监视",
  skip: "跳过",
  missed: "跳过",
  skipped: "跳过",
};

function phaseBadge(phase, locked) {
  const ph = String(phase || "watch").toLowerCase();
  const label = ROW_PHASE_LABEL[ph] || ph;
  const cls =
    locked || ph === "skip" || ph === "skipped" || ph === "missed"
      ? "is-locked"
      : ph === "done"
        ? "is-done"
        : ph === "running"
          ? "is-after_leg1"
          : "is-idle";
  return `<span class="paper-t0-desk-phase ${cls}">${escapeHtml(label)}</span>`;
}

function classifyAction(action, locked) {
  const a = String(action || "").toLowerCase();
  if (locked || a === "skip") return { id: "locked", label: "跳过" };
  if (a === "open") return { id: "armed", label: "开仓" };
  if (a === "add") return { id: "armed", label: "加仓" };
  if (a === "exit") return { id: "deadline", label: "清仓" };
  if (a === "hold") return { id: "watch", label: "持有" };
  return { id: "watch", label: "监视" };
}

function fmtRank(rs) {
  if (rs == null || rs === "") return "—";
  const n = Number(rs);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

function fmtShares(sh) {
  const n = Number(sh);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return String(Math.round(n));
}

/**
 * @param {HTMLElement | null} el
 * @param {object | null} desk
 */
export function renderPaperRebalanceWorkerDesk(el, desk) {
  if (!el) return;
  if (!desk || !Array.isArray(desk.rows)) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const prevFold = el.querySelector("details.paper-t0-desk-fold");
  const keepOpen = prevFold ? !!prevFold.open : false;
  const counts = desk.counts || {};
  const n = Number(desk.universe_count || desk.rows.length || 0);
  const sess = desk.session_date || desk.state_session_date || "—";
  const skipN = Number(counts.skip || 0);
  const chips =
    deskChip("is-univ", "持仓", n) +
    deskChip("is-locked", "跳过", skipN) +
    deskChip("is-idle", "监视", counts.watch) +
    deskChip("is-leg1", "开/加", desk.open_count) +
    deskChip("is-done", "完成", counts.done) +
    deskChip("is-legs", "清仓", desk.exit_count);
  const openAttr = keepOpen ? " open" : "";
  const sessLine = `<span class="paper-t0-desk-sess" title="交易日会话">${escapeHtml(
    String(sess)
  )}</span>`;

  if (!desk.rows.length) {
    el.innerHTML =
      `<details class="paper-t0-desk-fold"${openAttr}>` +
      `<summary class="paper-t0-desk-fold-sum">` +
      `<span class="paper-t0-desk-fold-title">今日盯盘状态</span>` +
      sessLine +
      `<span class="paper-t0-desk-chips">${chips || ""}</span>` +
      `</summary>` +
      `<p class="paper-t0-desk-empty">${escapeHtml(
        desk.note || "尚无今日调仓状态"
      )}</p>` +
      `</details>`;
    return;
  }

  const staleBanner =
    desk.state_aligned === false
      ? `<p class="paper-t0-desk-empty paper-t0-desk-stale">${escapeHtml(
          desk.note || "盘中状态尚未对齐今日会话"
        )}</p>`
      : "";

  const body = desk.rows
    .map((r) => {
      const note = String(r.reason || "").trim() || "—";
      const locked = r.phase === "skip" || r.phase === "missed" || r.phase === "skipped";
      const cat = classifyAction(r.action, locked);
      return (
        `<tr class="${locked ? "is-locked" : ""}" data-phase="${escapeHtml(
          String(r.phase || "watch")
        )}" data-code="${escapeHtml(String(r.stock_code || ""))}">` +
        stockCellHtml(r) +
        actionCell(r.action) +
        `<td class="paper-t0-col-phase">${phaseBadge(r.phase, locked)}</td>` +
        `<td class="num paper-t0-col-legs">${escapeHtml(fmtShares(r.shares))}</td>` +
        `<td class="num paper-t0-col-clock">${escapeHtml(fmtRank(r.ranking_score))}</td>` +
        `<td class="paper-t0-col-cat">` +
        `<span class="paper-t0-desk-cat is-${escapeHtml(cat.id)}">${escapeHtml(
          cat.label
        )}</span>` +
        `</td>` +
        `<td class="paper-t0-col-reason" title="${escapeHtml(note)}">` +
        `<span class="paper-t0-desk-reason">${escapeHtml(note)}</span>` +
        `</td>` +
        `<td class="paper-t0-col-act"><span class="paper-t0-leg-empty">—</span></td>` +
        `</tr>`
      );
    })
    .join("");

  const foot = desk.filled
    ? `<p class="paper-t0-desk-foot">今日已调仓。开/加/清为已落账手数；rank 为当时 ranking。过 10:00 不补跑。</p>`
    : `<p class="paper-t0-desk-foot">落账前按持仓占位监视；09:30–10:00 现价成交一次后换成开/加/清/持。</p>`;

  el.innerHTML =
    `<details class="paper-t0-desk-fold"${openAttr}>` +
    `<summary class="paper-t0-desk-fold-sum">` +
    `<span class="paper-t0-desk-fold-title">今日盯盘状态</span>` +
    sessLine +
    `<span class="paper-t0-desk-chips">${chips}</span>` +
    `</summary>` +
    staleBanner +
    `<div class="paper-t0-desk-board">` +
    `<div class="quant-weight-table-wrap paper-t0-desk-wrap">` +
    `<table class="quant-weight-table paper-t0-table paper-t0-desk-table">` +
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-dir" />` +
    `<col class="paper-t0-col-phase" />` +
    `<col class="paper-t0-col-legs" />` +
    `<col class="paper-t0-col-clock" />` +
    `<col class="paper-t0-col-cat" />` +
    `<col class="paper-t0-col-reason" />` +
    `<col class="paper-t0-col-act" />` +
    `</colgroup>` +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">标的</th>` +
    `<th scope="col" class="paper-t0-col-dir">动作</th>` +
    `<th scope="col" class="paper-t0-col-phase">阶段</th>` +
    `<th scope="col" class="num paper-t0-col-legs">手数</th>` +
    `<th scope="col" class="num paper-t0-col-clock">rank</th>` +
    `<th scope="col" class="paper-t0-col-cat">类别</th>` +
    `<th scope="col" class="paper-t0-col-reason">说明</th>` +
    `<th scope="col" class="paper-t0-col-act">操作</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>` +
    foot +
    `</div></details>`;
  stampStockFitTiers(el);
}
