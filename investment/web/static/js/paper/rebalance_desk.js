/** Paper · 自动调仓今日盯盘状态（格式对齐做 T worker desk）。 */

import { stockCellHtml, stampStockFitTiers } from "./t0_table.js?v=p2746";

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
  if (a === "reduce") return "减";
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
  if (a === "reduce") {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-sell-then-buy" title="减仓">减</span></td>`;
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
  if (a === "reduce") return { id: "deadline", label: "减仓" };
  if (a === "hold") return { id: "watch", label: "持有" };
  return { id: "watch", label: "监视" };
}

function fmtRank(rs, rankingPct) {
  if (rankingPct != null && rankingPct !== "") {
    const p = Number(rankingPct);
    if (Number.isFinite(p)) return `${p.toFixed(2)}%`;
  }
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

function fmtPx(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return n >= 100 ? n.toFixed(2) : String(n);
}

function fmtAmt(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "—";
  return `${Math.round(n)}`;
}

function fmtHm(ts) {
  const s = String(ts || "");
  const m = s.match(/T(\d{2}:\d{2})/);
  return m ? m[1] : "";
}

function fmtSignedPct(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function fillAnchor(r) {
  const px = Number(r.price);
  const prev = Number(r.prev_close);
  const open = Number(r.day_open);
  if (!Number.isFinite(px) || px <= 0) return "";
  if (Number.isFinite(prev) && prev > 0 && Math.abs(px - prev) < 1e-6) return "成交=昨收";
  if (Number.isFinite(open) && open > 0 && Math.abs(px - open) < 1e-6) return "成交=今开";
  return "成交=现价";
}

function reasonTitle(r) {
  const bits = [];
  const yoo = fmtSignedPct(r.y_oo);
  const yoc = fmtSignedPct(r.y_oc);
  const yco = fmtSignedPct(r.y_co);
  if (yoo) bits.push(`y_oo ${yoo}`);
  if (yoc) bits.push(`y_τc ${yoc}`);
  if (yco) bits.push(`y_co ${yco}`);
  const oldSh = Number(r.old_shares);
  const newSh = Number(r.new_shares);
  if (Number.isFinite(oldSh) && Number.isFinite(newSh)) {
    bits.push(`仓 ${Math.round(oldSh)}→${Math.round(newSh)}`);
  }
  const hm = fmtHm(r.ts);
  if (hm) bits.push(hm);
  const anchor = fillAnchor(r);
  if (anchor) bits.push(anchor);
  const note = String(r.reason || "").trim();
  if (note && note !== "—") bits.unshift(note);
  return bits.join(" · ");
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
    deskChip("is-legs", "清仓", desk.exit_count) +
    deskChip("is-legs", "减仓", desk.reduce_count);
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
      const tip = reasonTitle(r) || note;
      const locked = r.phase === "skip" || r.phase === "missed" || r.phase === "skipped";
      const cat = classifyAction(r.action, locked);
      const hm = fmtHm(r.ts);
      const anchor = fillAnchor(r);
      const pxTip = [hm, anchor, r.prev_close != null ? `昨收 ${fmtPx(r.prev_close)}` : ""]
        .filter(Boolean)
        .join(" · ");
      return (
        `<tr class="${locked ? "is-locked" : ""}" data-phase="${escapeHtml(
          String(r.phase || "watch")
        )}" data-code="${escapeHtml(String(r.stock_code || ""))}">` +
        stockCellHtml(r) +
        actionCell(r.action) +
        `<td class="paper-t0-col-phase">${phaseBadge(r.phase, locked)}</td>` +
        `<td class="num paper-t0-col-legs">${escapeHtml(fmtShares(r.shares))}</td>` +
        `<td class="num paper-rb-col-px" title="${escapeHtml(pxTip)}">${escapeHtml(
          fmtPx(r.price)
        )}</td>` +
        `<td class="num paper-rb-col-open">${escapeHtml(fmtPx(r.day_open))}</td>` +
        `<td class="num paper-rb-col-amt">${escapeHtml(fmtAmt(r.amount))}</td>` +
        `<td class="num paper-t0-col-clock">${escapeHtml(
          fmtRank(r.ranking_score, r.ranking)
        )}</td>` +
        `<td class="paper-t0-col-cat">` +
        `<span class="paper-t0-desk-cat is-${escapeHtml(cat.id)}">${escapeHtml(
          cat.label
        )}</span>` +
        `</td>` +
        `<td class="paper-t0-col-reason" title="${escapeHtml(tip)}">` +
        `<span class="paper-t0-desk-reason">${escapeHtml(note)}</span>` +
        `</td>` +
        `<td class="paper-t0-col-act"><span class="paper-t0-leg-empty">—</span></td>` +
        `</tr>`
      );
    })
    .join("");

  const clock = String(desk.fill_clock || "09:30").slice(0, 5);
  const windowLbl = desk.window_label || `${clock}–10:00`;
  const src = desk.source === "follow" ? "手动" : desk.source === "auto" ? "自动" : "";
  const hm = fmtHm(desk.fill_ts);
  const buyAmt = Number(desk.buy_amount);
  const sellAmt = Number(desk.sell_amount);
  const amtBits = [];
  if (Number.isFinite(sellAmt) && sellAmt > 0) amtBits.push(`卖 ${Math.round(sellAmt)}`);
  if (Number.isFinite(buyAmt) && buyAmt > 0) amtBits.push(`买 ${Math.round(buyAmt)}`);
  const footBits = desk.filled
    ? [
        "今日已调仓",
        src,
        hm,
        amtBits.join(" / "),
        "成交价为落账价；悬停说明看 y_oo / y_τc / 昨收",
        "过 10:00 不补跑",
      ]
    : [
        `落账前按持仓占位监视`,
        `${windowLbl} 现价成交一次后换成开/加/清`,
      ];
  const foot = `<p class="paper-t0-desk-foot">${escapeHtml(
    footBits.filter(Boolean).join(" · ")
  )}</p>`;

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
    `<table class="quant-weight-table paper-t0-table paper-t0-desk-table paper-rb-desk-table">` +
    `<colgroup>` +
    `<col class="paper-t0-col-stock" />` +
    `<col class="paper-t0-col-dir" />` +
    `<col class="paper-t0-col-phase" />` +
    `<col class="paper-t0-col-legs" />` +
    `<col class="paper-rb-col-px" />` +
    `<col class="paper-rb-col-open" />` +
    `<col class="paper-rb-col-amt" />` +
    `<col class="paper-t0-col-clock" />` +
    `<col class="paper-t0-col-cat" />` +
    `<col class="paper-t0-col-reason" />` +
    `<col class="paper-t0-col-act" />` +
    `</colgroup>` +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">标的</th>` +
    `<th scope="col" class="paper-t0-col-dir">动作</th>` +
    `<th scope="col" class="paper-t0-col-phase">阶段</th>` +
    `<th scope="col" class="num paper-t0-col-legs" title="本笔股数">手数</th>` +
    `<th scope="col" class="num paper-rb-col-px" title="落账成交价">成交</th>` +
    `<th scope="col" class="num paper-rb-col-open" title="当日开盘价">今开</th>` +
    `<th scope="col" class="num paper-rb-col-amt" title="成交金额">金额</th>` +
    `<th scope="col" class="num paper-t0-col-clock" title="当时 ranking">rank</th>` +
    `<th scope="col" class="paper-t0-col-cat">类别</th>` +
    `<th scope="col" class="paper-t0-col-reason">说明</th>` +
    `<th scope="col" class="paper-t0-col-act">操作</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>` +
    foot +
    `</div></details>`;
  stampStockFitTiers(el);
}
