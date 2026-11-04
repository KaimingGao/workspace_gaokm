/** Paper · 自动调仓今日盯盘状态（格式对齐做 T worker desk）。 */

import { stockCellHtml, stampStockFitTiers } from "./t0_table.js?v=p3541";

const SENT_BADGE = {
  bullish: ["is-bull", "多"],
  bearish: ["is-bear", "空"],
  mixed: ["is-mixed", "杂"],
  neutral: ["is-neutral", "中"],
  missing: ["is-neutral", "—"],
};

/** 决策窗标签。缺测且库内也没有标题时是 —，不是中。不进 ŷ。 */
export function sentimentDeskBadgeHtml(label, basis) {
  const key = String(label || "missing").trim() || "missing";
  const pair = SENT_BADGE[key] || ["is-neutral", "中"];
  const title =
    basis === "library"
      ? "库内现有标题。09:30 前未入库，闸不动作。不进 ŷ。"
      : "不进 ŷ。勾选闸时空/多调 fuse±0.1%·rank×2/×0.5，不清仓；缺测不调";
  return `<span class="watching-sent-badge ${pair[0]}" title="${title}">${pair[1]}</span>`;
}

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

function actionCell(action, row) {
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
  if (a === "skip" && isMorphGateSkip(row)) {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-morph" title="形态闸跳过，未下单">跳</span></td>`;
  }
  if (a === "skip" && isSentimentGateSkip(row)) {
    return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-sent" title="舆情软先验 fuse/rank 未过入场，未下单">跳</span></td>`;
  }
  return `<td class="paper-t0-col-dir"><span class="paper-t0-desk-dir is-none">${escapeHtml(
    actionLabel(a)
  )}</span></td>`;
}

function fmtRank(rs, fusePct) {
  if (fusePct != null && fusePct !== "") {
    const p = Number(fusePct);
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

function fmtFillPx(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return n.toFixed(2);
}

function isDeskSkip(r) {
  const action = String((r && r.action) || "").toLowerCase();
  const phase = String((r && r.phase) || "").toLowerCase();
  return (
    action === "skip" ||
    phase === "skip" ||
    phase === "skipped" ||
    phase === "missed"
  );
}

/** 已落账的清 / 开 / 加。形态闸跳过不在这里，由 isMorphGateSkip 另拼进明细。 */
function isDeskFill(r) {
  const action = String((r && r.action) || "").toLowerCase();
  return action === "exit" || action === "open" || action === "add";
}

/** 形态闸跳过才进明细。认 morph_gate，或原因以前缀「形态闸」开头。 */
function isMorphGateSkip(r) {
  if (!isDeskSkip(r)) return false;
  if (r && r.morph_gate === true) return true;
  return String((r && r.reason) || "").trim().startsWith("形态闸");
}

/** 舆情先验跳过。认 sentiment_gate / sentiment_prior，或原因含舆情闸/舆情空/舆情多。 */
function isSentimentGateSkip(r) {
  if (!isDeskSkip(r)) return false;
  if (r && (r.sentiment_gate === true || r.sentiment_prior)) return true;
  const reason = String((r && r.reason) || "").trim();
  return (
    reason.startsWith("舆情闸") ||
    reason.includes("舆情空") ||
    reason.includes("舆情多")
  );
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

function timeCell(ts) {
  const hm = fmtHm(ts);
  const full = String(ts || "").trim();
  const title = full ? `落账 ${full}` : "尚未落账";
  return `<td class="num paper-rb-col-time" title="${escapeHtml(title)}">${escapeHtml(
    hm || "—"
  )}</td>`;
}

function rankDeskBit(raw, nRaw) {
  const y = Number(raw);
  const n = Number(nRaw);
  if (!Number.isFinite(y) || !Number.isFinite(n) || n < 1) return "";
  const ord = Math.round(y);
  const pool = Math.round(n);
  if (Math.abs(y - ord) > 1e-9 || ord < 1 || ord > pool) return "";
  return `${ord}/${pool}`;
}

function rankDeskCell(raw, nRaw, label) {
  const bit = rankDeskBit(raw, nRaw);
  const title = bit
    ? `${label} 当日截面第 ${bit.replace("/", " 名 / ")} · 1=相对分最高`
    : `${label} — · 无已启用模型或未写入`;
  return `<td class="num paper-rb-col-rank" title="${escapeHtml(title)}">${escapeHtml(
    bit ? bit.split("/")[0] : "—"
  )}</td>`;
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
  const yoc = fmtSignedPct(r["y_τc"] != null ? r["y_τc"] : r.y_oc);
  if (yoo) bits.push(`y_oo ${yoo}`);
  if (yoc) bits.push(`y_τc ${yoc}`);
  const ooBit = rankDeskBit(r.y_oo_rank, r.y_oo_rank_n);
  const tcBit = rankDeskBit(r.y_tc_rank, r.y_tc_rank_n);
  if (ooBit) bits.push(`oo_rank ${ooBit}`);
  if (tcBit) bits.push(`τc_rank ${tcBit}`);
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
  const covered = desk.rows.filter((r) => !isDeskSkip(r));
  const morphSkips = desk.rows.filter((r) => isMorphGateSkip(r));
  const sentSkips = desk.rows.filter((r) => isSentimentGateSkip(r));
  const rows = covered.filter((r) => isDeskFill(r)).concat(morphSkips, sentSkips);
  const n = covered.length;
  const sess = desk.session_date || desk.state_session_date || "—";
  const chips =
    deskChip("is-univ", String(desk.universe_label || "持仓"), n) +
    deskChip("is-idle", "监视", counts.watch) +
    deskChip("is-leg1", "开/加", desk.open_count) +
    deskChip("is-done", "完成", counts.done) +
    deskChip("is-legs", "清仓", desk.exit_count) +
    deskChip("is-legs", "减仓", desk.reduce_count) +
    deskChip("is-locked", "形态闸", morphSkips.length) +
    deskChip("is-sent", "舆情先验", sentSkips.length);
  const openAttr = keepOpen ? " open" : "";
  const sessLine = `<span class="paper-t0-desk-sess" title="交易日会话">${escapeHtml(
    String(sess)
  )}</span>`;

  if (!rows.length) {
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

  const body = rows
    .map((r) => {
      const note = String(r.reason || "").trim() || "—";
      const tip = reasonTitle(r) || note;
      const locked = r.phase === "skip" || r.phase === "missed" || r.phase === "skipped";
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
        `<td class="paper-rb-col-sent">${sentimentDeskBadgeHtml(
          r.sentiment_label,
          r.sentiment_basis
        )}</td>` +
        timeCell(r.ts) +
        actionCell(r.action, r) +
        `<td class="num paper-t0-col-legs">${escapeHtml(fmtShares(r.shares))}</td>` +
        `<td class="num paper-rb-col-px" title="${escapeHtml(pxTip)}">${escapeHtml(
          fmtFillPx(r.price)
        )}</td>` +
        `<td class="num paper-rb-col-open">${escapeHtml(fmtPx(r.day_open))}</td>` +
        `<td class="num paper-rb-col-amt">${escapeHtml(fmtAmt(r.amount))}</td>` +
        `<td class="num paper-t0-col-clock">${escapeHtml(
          fmtRank(r.y_oo_fuse_score, r.y_oo_fuse)
        )}</td>` +
        rankDeskCell(r.y_oo_rank, r.y_oo_rank_n, "oo_rank") +
        rankDeskCell(r.y_tc_rank, r.y_tc_rank_n, "τc_rank") +
        `<td class="paper-t0-col-reason" title="${escapeHtml(tip)}">` +
        `<span class="paper-t0-desk-reason">${escapeHtml(note)}</span>` +
        `</td>` +
        `<td class="paper-t0-col-act"><span class="paper-t0-leg-empty">—</span></td>` +
        `</tr>`
      );
    })
    .join("");

  const clock = String(desk.live_fill_clock || desk.fill_clock || "09:30").slice(0, 5);
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
        morphSkips.length ? "形态闸行未下单，成交列为当时现价" : "",
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
    `<col class="paper-rb-col-sent" />` +
    `<col class="paper-rb-col-time" />` +
    `<col class="paper-t0-col-dir" />` +
    `<col class="paper-t0-col-legs" />` +
    `<col class="paper-rb-col-px" />` +
    `<col class="paper-rb-col-open" />` +
    `<col class="paper-rb-col-amt" />` +
    `<col class="paper-t0-col-clock" />` +
    `<col class="paper-rb-col-rank" />` +
    `<col class="paper-rb-col-rank" />` +
    `<col class="paper-t0-col-reason" />` +
    `<col class="paper-t0-col-act" />` +
    `</colgroup>` +
    `<thead><tr>` +
    `<th scope="col" class="paper-t0-col-stock">标的</th>` +
    `<th scope="col" class="paper-rb-col-sent" title="决策窗舆情。不进 ŷ。勾选闸时空/多调 fuse±0.1%·rank×2/×0.5，不清仓；缺测不调">舆情</th>` +
    `<th scope="col" class="num paper-rb-col-time" title="本笔落账时间">时间</th>` +
    `<th scope="col" class="paper-t0-col-dir">动作</th>` +
    `<th scope="col" class="num paper-t0-col-legs" title="本笔股数">手数</th>` +
    `<th scope="col" class="num paper-rb-col-px" title="落账成交价">成交</th>` +
    `<th scope="col" class="num paper-rb-col-open" title="当日开盘价">今开</th>` +
    `<th scope="col" class="num paper-rb-col-amt" title="成交金额">金额</th>` +
    `<th scope="col" class="num paper-t0-col-clock" title="当时 fuse">fuse</th>` +
    `<th scope="col" class="num paper-rb-col-rank" title="ŷ_oo_rank 当日观察池截面名次 · 1=相对分最高 · 过入场后作买序">oo_rank</th>` +
    `<th scope="col" class="num paper-rb-col-rank" title="ŷ_τc_rank 当日截面名次 · 与 oo_rank 共用 rank 入场 · 不进 fuse / 不改买序">τc_rank</th>` +
    `<th scope="col" class="paper-t0-col-reason">说明</th>` +
    `<th scope="col" class="paper-t0-col-act">操作</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>` +
    foot +
    `</div></details>`;
  stampStockFitTiers(el);
}
