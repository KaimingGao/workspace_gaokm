/**
 * 观察池 HTML 渲染 helpers（纯字符串 / 轻量 DOM 写入）。
 */
import { escapeHtml } from "../shared.js";
import { marketPriorDetailFields, tailAnomalyDetailFields } from "../score_tooltip.js";
import { watchingNameSpanHtml } from "./names.js";

export function describeWatchingSource(src, index) {
  const typ = String(src.type || "").toLowerCase() || "—";
  const label = `S${index + 1}`;
  const typLabel = typ === "static" ? "静态" : typ === "screen" ? "筛选" : typ;
  if (typ === "static") {
    const codes = (src.codes || []).map((c) => String(c));
    return {
      label,
      typ,
      typLabel,
      title: label,
      detail: codes.length ? codes.join("、") : "（空）",
      codes,
    };
  }
  if (typ === "screen") {
    const bits = [];
    if (src.sector) bits.push(String(src.sector));
    if (src.pe_min != null) bits.push(`PE≥${src.pe_min}`);
    if (src.pe_max != null) bits.push(`PE≤${src.pe_max}`);
    if (src.pb_max != null) bits.push(`PB≤${src.pb_max}`);
    if (src.change_min != null) bits.push(`涨跌≥${src.change_min}%`);
    if (src.change_max != null) bits.push(`涨跌≤${src.change_max}%`);
    if (src.limit != null) bits.push(`取${src.limit}`);
    return {
      label,
      typ,
      typLabel,
      title: label,
      detail: bits.join(" · ") || "筛选",
      codes: [],
    };
  }
  return {
    label,
    typ,
    typLabel: typ,
    title: label,
    detail: JSON.stringify(src),
    codes: [],
  };
}

export function shortOriginLabel(raw) {
  const text = String(raw || "").trim();
  if (!text) return "—";
  const m = text.match(/^S(\d+)/i);
  if (m) return `S${m[1]}`;
  if (text.includes("筛选") || text.includes("screen") || text.includes("合并")) {
    return "筛选";
  }
  return text;
}

export function matchWatchlistSource(code, sourceDescs) {
  const raw = String(code || "");
  for (const s of sourceDescs) {
    if (s.typ !== "static") continue;
    for (const c of s.codes) {
      if (c === raw || raw.includes(c) || c.includes(raw)) {
        return s.label;
      }
    }
  }
  return "筛选";
}

export function watchingScoreDetail(it) {
  // τ 字段放前：data-score-detail 属性过长时避免被截掉
  const terms = slimFormulaTerms((it && it.score_formula_terms) || null, 10);
  const tauTerms = slimFormulaTerms(
    (it && (it.formula_terms_tau || it.score_formula_terms_tau)) || null,
    12
  );
  const hasTerms =
    terms && Array.isArray(terms.terms) && terms.terms.length > 0;
  const hasTauTerms =
    tauTerms && Array.isArray(tauTerms.terms) && tauTerms.terms.length > 0;
  // 有分项拆解时不再塞整包系数，缩小属性体积、避免截断坏 JSON
  const coefs = hasTerms ? {} : (it && it.factor_coefficients) || {};
  const coefsTau = hasTauTerms
    ? null
    : (it && it.factor_coefficients_tau) || null;
  const sentInc =
    it && it.sentiment_include_in_score != null
      ? !!it.sentiment_include_in_score
      : null;
  const ep = (it && it.event_prior) || null;
  const eventPrior = ep
    ? {
        theme: !!ep.theme,
        warnings: Array.isArray(ep.warnings) ? ep.warnings.slice(0, 2) : [],
      }
    : null;
  return JSON.stringify({
    stock_code: (it && (it.stock_code || it.code)) || null,
    predicted_score: it && it.predicted_score != null ? it.predicted_score : it && it.score,
    score: it && it.score != null ? it.score : it && it.predicted_score,
    predicted_score_tau:
      it &&
      (it.predicted_score_tau != null
        ? it.predicted_score_tau
        : it.score_rem != null
          ? it.score_rem
          : it.predicted_score_rem),
    predicted_score_blend: it && it.predicted_score_blend,
    predicted_score_eod: it && it.predicted_score_eod,
    predicted_score_eod_rem: it && it.predicted_score_eod_rem,
    predicted_score_tau_delta: it && it.predicted_score_tau_delta,
    predicted_score_nowcast: it && it.predicted_score_nowcast,
    dual_score_window: (it && it.dual_score_window) || null,
    nowcast_as_of: (it && it.nowcast_as_of) || null,
    nowcast_K: it && it.nowcast_K,
    nowcast_q: it && it.nowcast_q,
    // 校准对照：靠前写入，避免属性过长截断
    predicted_score_cal: it && it.predicted_score_cal,
    predicted_score_eod_rem_cal: it && it.predicted_score_eod_rem_cal,
    predicted_score_tau_cal: it && it.predicted_score_tau_cal,
    predicted_score_blend_cal: it && it.predicted_score_blend_cal,
    score_calibration_applied: !!(it && it.score_calibration_applied),
    score_calibration_enabled: !!(it && it.score_calibration_enabled),
    score_calibration_eod_oor: !!(it && it.score_calibration_eod_oor),
    score_calibration_eod_rem_oor: !!(it && it.score_calibration_eod_rem_oor),
    score_calibration_tau_oor: !!(it && it.score_calibration_tau_oor),
    score_calibration_note: (it && it.score_calibration_note) || null,
    score_calibration_partial: (it && it.score_calibration_partial) || null,
    realized_t1_to_tau: it && it.realized_t1_to_tau,
    score_rem: it && (it.score_rem != null ? it.score_rem : it.predicted_score_rem),
    gap_pct: it && it.gap_pct,
    event_prior: eventPrior,
    as_of_tau: (it && (it.as_of_tau || it.rem_tau)) || null,
    rem_tau: (it && it.rem_tau) || null,
    y_spec_tau: (it && it.y_spec_tau) || null,
    features_tau: (it && it.features_tau) || null,
    formula_terms_tau: tauTerms,
    factor_coefficients_tau: coefsTau,
    dual_score_fusion: (it && it.dual_score_fusion) || null,
    dual_score_weights: (it && it.dual_score_weights) || null,
    dual_score_head: (it && it.dual_score_head) || null,
    dual_score_single_head: !!(it && it.dual_score_single_head),
    y_check: (it && it.y_check) || null,
    y_disagree: it && it.y_disagree,
    y_sigma: it && it.y_sigma,
    y_mu: it && it.y_mu,
    eod_trust: it && it.eod_trust,
    y_tau_to_close: it && it.y_tau_to_close,
    y_tau_to_close_src: it && it.y_tau_to_close_src,
    y_state: it && it.y_state,
    formula: hasTerms ? "" : (it && it.score_formula) || "",
    reasons: ((it && it.score_reasons) || []).slice(0, 5),
    hard_reject: !!(it && it.hard_reject),
    reject_reason: (it && it.reject_reason) || "",
    weight_source: (it && it.weight_source) || "",
    cluster_label: (it && it.cluster_label) || "",
    cluster_mode: (it && it.cluster_mode) || "",
    cluster_version: it && it.cluster_version,
    score_global: it && it.score_global,
    score_cluster: it && it.score_cluster,
    min_score: it && it.min_score,
    below_min_score: !!(it && it.below_min_score),
    return_model_source: (it && it.return_model_source) || "",
    score_scale: (it && it.score_scale) || "",
    heuristic_score: it && it.heuristic_score,
    formula_terms: terms,
    factor_coefficients: coefs,
    sentiment_include_in_score: sentInc,
    sentiment_prior: (it && it.sentiment_prior) || null,
    alt_sentiment_beta: it && it.alt_sentiment_beta,
    alt_sentiment_in_yhat: !!(it && it.alt_sentiment_in_yhat),
    risk_hints: ((it && it.risk_hints) || []).slice(0, 3),
    warnings: ((it && it.warnings) || []).slice(0, 3),
    ...marketPriorDetailFields(it),
    ...tailAnomalyDetailFields(it),
  });
}

function slimFormulaTerms(expl, maxTerms = 10) {
  if (!expl || typeof expl !== "object") return expl || null;
  const terms = Array.isArray(expl.terms) ? expl.terms : [];
  if (!terms.length) return expl;
  const sorted = [...terms].sort(
    (a, b) => Math.abs(Number(b?.contrib) || 0) - Math.abs(Number(a?.contrib) || 0)
  );
  return {
    intercept: expl.intercept,
    total: expl.total,
    terms: sorted.slice(0, maxTerms),
    head: expl.head,
  };
}

export function truncateText(s, n) {
  const t = String(s || "").trim();
  if (!t) return "";
  if (t.length <= n) return t;
  return `${t.slice(0, Math.max(0, n - 1))}…`;
}

export function sentimentLabelZh(label) {
  const m = {
    bullish: "多",
    bearish: "空",
    mixed: "杂",
    neutral: "中",
  };
  return m[label] || "中";
}

export function sentimentBadgeHtml(sent, code) {
  const s = sent || {};
  const label = String(s.label || "neutral");
  const cls =
    label === "bullish"
      ? "is-bull"
      : label === "bearish"
        ? "is-bear"
        : label === "mixed"
          ? "is-mixed"
          : "is-neutral";
  const hits = []
    .concat(s.hit_pos || [])
    .concat(s.hit_neg || [])
    .slice(0, 6);
  const scorePart =
    s.score != null && !Number.isNaN(Number(s.score))
      ? ` · 风险 ${Number(s.score).toFixed(2)}`
      : "";
  // 先验旁路：不进 score/ŷ；policy 见 sentiment.prior.mode
  const gateNote =
    s.role === "prior" || s.include_in_score !== true
      ? "先验旁路 · 不参与 predicted_score"
      : "遗留开闸进 ŷ（不推荐）";
  const title = [
    s.note || "规则关键词，非模型",
    gateNote,
    hits.length ? `命中：${hits.join("、")}` : "无关键词命中",
    scorePart.trim(),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    `<span class="watching-sent-badge ${cls}" data-code="${escapeHtml(code || "")}" title="${escapeHtml(title)}">` +
    `${sentimentLabelZh(label)}</span>`
  );
}

/**
 * @param {object} plan
 * @param {{
 *   bodyEl: HTMLElement|null,
 *   confirmBtnEl?: HTMLElement|null,
 *   mode: string,
 *   defaultVal: number,
 *   editableCodes: string[],
 *   amountByCode: Record<string, number>,
 *   sharesByCode: Record<string, number>,
 * }} opts
 */
export function renderWatchingBuildPlan(plan, opts) {
  const body = opts.bodyEl;
  const confirmBtn = opts.confirmBtnEl;
  if (!body) return;
  const items = (plan && plan.items) || [];
  const skipped = (plan && plan.skipped) || [];
  const defaultVal = opts.defaultVal;
  const fmtMoney = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  };
  const editableCodes = opts.editableCodes || [];
  const byCode = {};
  items.forEach((it) => {
    byCode[it.stock_code] = it;
  });
  skipped.forEach((s) => {
    if (!byCode[s.stock_code]) {
      byCode[s.stock_code] = {
        stock_code: s.stock_code,
        stock_name: s.stock_name,
        reason: s.reason,
      };
    }
  });
  const mode = opts.mode || "amount";
  const amountByCode = opts.amountByCode || {};
  const sharesByCode = opts.sharesByCode || {};
  const headCols =
    mode === "amount"
      ? `<th>股票</th><th>现价</th><th>金额</th><th>股数</th><th>花费</th>`
      : `<th>股票</th><th>现价</th><th>股数</th><th>花费</th>`;
  const table = editableCodes.length
    ? `<table class="quant-weight-table watching-build-table"><thead><tr>` +
      headCols +
      `</tr></thead><tbody>` +
      editableCodes
        .map((code) => {
          const it = byCode[code] || { stock_code: code };
          let midCells;
          if (mode === "amount") {
            const amt =
              amountByCode[code] != null
                ? amountByCode[code]
                : defaultVal;
            midCells =
              `<td class="num"><input type="number" class="watching-build-row-amount" data-code="${escapeHtml(
                code
              )}" min="100" step="100" value="${escapeHtml(String(amt))}" title="该只买入金额（元）" /></td>` +
              `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
          } else if (mode === "shares") {
            const shares =
              sharesByCode[code] != null
                ? sharesByCode[code]
                : it.shares != null
                  ? it.shares
                  : Math.floor(defaultVal / 100) * 100;
            midCells =
              `<td class="num"><input type="number" class="watching-build-row-shares" data-code="${escapeHtml(
                code
              )}" min="100" step="100" value="${escapeHtml(String(shares))}" title="该只买入股数" /></td>`;
          } else {
            midCells =
              `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
          }
          const amountCell =
            it.amount != null
              ? fmtMoney(it.amount)
              : it.reason
                ? `<span class="watching-build-skip-reason">${escapeHtml(it.reason)}</span>`
                : "—";
          return (
            `<tr data-code="${escapeHtml(code)}"><td class="watching-build-name">` +
            watchingNameSpanHtml(it.stock_name || code) +
            `<span class="watching-code-sub">${escapeHtml(code)}</span></td>` +
            `<td class="num">${escapeHtml(String(it.price ?? "—"))}</td>` +
            midCells +
            `<td class="num">${amountCell}</td></tr>`
          );
        })
        .join("") +
      `</tbody></table>`
    : `<p class="watching-table-empty">按当前定量没有可建仓的股票</p>`;
  const costNote =
    plan && plan.cost_model === "zero"
      ? `<div class="watching-build-cost-chip">零成本假设</div>`
      : "";
  const totals =
    costNote +
    `<dl class="watching-build-totals">` +
    `<div><dt>买入</dt><dd>${items.length} 只</dd></div>` +
    `<div><dt>合计花费</dt><dd>${fmtMoney(plan && plan.total_amount)} 元</dd></div>` +
    `<div><dt>可用现金</dt><dd>${fmtMoney(plan && plan.cash)} 元</dd></div>` +
    `<div><dt>建仓后现金</dt><dd>${fmtMoney(plan && plan.cash_after)} 元</dd></div>` +
    `</dl>`;
  body.innerHTML = table + totals;
  if (confirmBtn) confirmBtn.disabled = !items.length;
}

/** CDN/React 不可用时的原生表回退 */
export function renderWatchingWatchTableFallback(rows, { onPickCountUpdate } = {}) {
  const watchTable = document.getElementById("watching-watchlist-table");
  if (!watchTable) return;
  const body = (rows || [])
    .map((d) => {
      const code = escapeHtml(d.code || "");
      const name = d.name || d.code || "—";
      const alertCls = d.isSentimentAlert ? " is-sentiment-alert" : "";
      const bookBadge = d.inBook
        ? `<span class="watching-book-badge" title="分池目标簿">簿</span>`
        : "";
      const oosBadge = d.oosFailed
        ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
        : "";
      return (
        `<tr data-code="${code}" class="watching-watch-row${alertCls}${d.inBook ? " is-cluster-book" : ""}${
          d.oosFailed ? " is-oos-failed" : ""
        }">` +
        `<td class="watching-pick-cell"><input type="checkbox" class="watching-pick" value="${code}" data-code="${code}" /></td>` +
        `<td class="watching-stock" title="${escapeHtml(name)} ${code}">` +
        `<span class="watching-name-row">` +
        watchingNameSpanHtml(name) +
        bookBadge +
        oosBadge +
        `</span>` +
        `<span class="watching-code-sub">${code}<span class="watching-mkt"></span></span></td>` +
        `<td class="watching-paper-cell">` +
        (d.onPaper
          ? `<button type="button" class="watching-held-btn" data-code="${code}">${escapeHtml(d.paper || "已持")}</button>`
          : `<button type="button" class="watching-build-btn" data-code="${code}">建仓</button>`) +
        `</td>` +
        `<td class="watching-sent-cell">${d.sentHtml || "—"}</td>` +
        `<td class="num watching-col-num" data-q="price">${escapeHtml(String(d.price ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="open">${escapeHtml(String(d.open ?? "—"))}</td>` +
        `<td class="num watching-col-num watching-chg${d.chgCls ? " " + escapeHtml(d.chgCls) : ""}" data-q="chg">${escapeHtml(String(d.chg ?? "—"))}</td>` +
        (() => {
          const singleHead = !!d.scoreSingleHead;
          const head = d.dualScoreHead || "";
          const headTitle =
            head === "single_tau"
              ? "ŷ_trade 单头降级：仅 ŷ_τ（缺 EOD rem）· 与双头票不同量纲"
              : head === "single_eod"
                ? "ŷ_trade 单头降级：仅 ŷ_EOD（缺 ŷ_τ）· 与双头票不同量纲"
                : "ŷ_trade 单头降级 · 与双头票不同量纲";
          const badge = singleHead
            ? `<span class="watching-single-head-badge" title="${escapeHtml(
                headTitle
              )}">单</span>`
            : "";
          return (
            `<td class="num watching-col-num watching-score-cell paper-hold-score has-tip ${escapeHtml(
              d.scoreCls || ""
            )}${singleHead ? " score-single-head" : ""}" data-q="score" data-score-tip="trade" data-score-detail="${escapeHtml(
              d.scoreDetail || ""
            )}" title="${escapeHtml(d.scoreTitle || "ŷ_trade")}">${escapeHtml(
              String(d.score ?? "—")
            )}${badge}</td>`
          );
        })() +
        `<td class="num watching-col-num watching-score-cell watching-score-cal paper-hold-score has-tip ${escapeHtml(
          d.scoreCalCls || ""
        )}" data-q="score_cal" data-score-tip="cal" data-score-detail="${escapeHtml(
          d.scoreDetail || ""
        )}" title="${escapeHtml(
          d.scoreCalTitle || "g(ŷ_trade) 对照 · 不进决策"
        )}">${escapeHtml(String(d.scoreCal ?? "—"))}</td>` +
        `<td data-q="stance">${escapeHtml(String(d.stance ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="excess">${escapeHtml(String(d.excess ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="vol">${escapeHtml(String(d.vol ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="volr">${escapeHtml(String(d.volr ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="pe">${escapeHtml(String(d.pe ?? "—"))}</td>` +
        `<td class="num watching-col-num" data-q="pb">${escapeHtml(String(d.pb ?? "—"))}</td>` +
        `</tr>`
      );
    })
    .join("");
  watchTable.innerHTML =
    `<div class="watching-table-scroll"><table class="quant-weight-table watching-result-table"><thead><tr>` +
    `<th class="watching-pick-cell"><input type="checkbox" id="watching-select-all" /></th>` +
    `<th>股票</th><th>仓位</th><th>情绪</th><th class="watching-col-num">现价</th>` +
    `<th class="watching-col-num">开盘价</th>` +
    `<th class="watching-col-num">涨跌</th><th class="watching-col-num">评分</th>` +
    `<th class="watching-col-num" title="g(ŷ_trade) 对照 · 不进决策">校准</th><th>倾向</th>` +
    `<th class="watching-col-num">超额</th><th class="watching-col-num">量</th>` +
    `<th class="watching-col-num">量比</th><th class="watching-col-num">PE</th><th class="watching-col-num">PB</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>`;
  if (typeof onPickCountUpdate === "function") onPickCountUpdate();
}

/**
 * 观察池虚拟表初始行。
 * @param {string[]} wl
 * @param {string[]} names
 * @param {Map|Iterable} paperCodes
 * @param {Record<string, number>|null|undefined} scores
 * @param {{
 *   fmtScore: (n: number|null) => string,
 *   scoreCls: (n: number|null) => string,
 *   escapeHtml: (s: string) => string,
 *   alertCodes: Set<string>,
 *   bookCodes?: Set<string>,
 * }} deps
 */
export function buildWatchingWatchRows(wl, names, paperCodes, scores, deps) {
  const { fmtScore, scoreCls, escapeHtml, alertCodes, bookCodes } = deps;
  const inPaper =
    paperCodes instanceof Map
      ? paperCodes
      : new Map(Array.from(paperCodes || []).map((c) => [String(c), null]));
  const inBook = bookCodes instanceof Set ? bookCodes : new Set(bookCodes || []);
  const rowByCode = new Map();
  for (let i = 0; i < (wl || []).length; i++) {
    const code = String(wl[i] || "").trim();
    if (!code) continue;
    const name = (names[i] && String(names[i]).trim()) || "—";
    const scoreRaw = scores && scores[code];
    const rawN =
      scoreRaw == null || Number.isNaN(Number(scoreRaw)) ? null : Number(scoreRaw);
    const scoreNum = rawN != null && Math.abs(rawN) <= 20 ? rawN : null;
    const onPaper = inPaper.has(code);
    const heldShares = onPaper ? inPaper.get(code) : null;
    rowByCode.set(code, {
      code,
      name,
      picked: false,
      market: "",
      paper: onPaper ? (heldShares != null ? `${heldShares} 股` : "已持") : "建仓",
      onPaper,
      inBook: inBook.has(code),
      oosFailed: false,
      sentHtml: `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(
        code
      )}" title="加载中">…</span>`,
      price: "—",
      open: "—",
      chg: "—",
      chgCls: "",
      chgNum: null,
      score: scoreNum == null ? "…" : fmtScore(scoreNum),
      scoreNum,
      scoreCls: scoreCls(scoreNum),
      scoreCal: "…",
      scoreCalNum: null,
      scoreCalCls: "",
      scoreCalTitle: "暂无 g(ŷ) 映射（拟合并写入 live 后可见）",
      stance: "…",
      excess: "…",
      excessNum: null,
      vol: "…",
      volNum: null,
      volr: "…",
      pe: "…",
      pb: "…",
      isHardReject: false,
      isSentimentAlert: alertCodes.has(code),
    });
  }
  return Array.from(rowByCode.values());
}

export function buildWatchingNewsTitleHtml(name, code, sent, sentimentBadgeHtmlFn) {
  return (
    `资讯 · ${escapeHtml(name)}` +
    ` <span class="watching-code-sub">${escapeHtml(code)}</span> ` +
    sentimentBadgeHtmlFn(sent, code)
  );
}

export function buildWatchingNewsMetaText(data, sent) {
  const hitBits = []
    .concat(sent.hit_pos || [])
    .concat(sent.hit_neg || []);
  return [
    data.ok ? `${(data.items || []).length} 条` : data.error || "无资讯",
    sent.note || "规则关键词，非模型",
    hitBits.length ? `命中 ${hitBits.join("、")}` : "",
    data.updated_at ? `更新 ${data.updated_at}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function buildWatchingNewsListHtml(items, data) {
  if (!items.length) {
    return `<li class="watching-news-empty">${escapeHtml(
      data.error || "暂无标题"
    )}</li>`;
  }
  return items
    .map((it) => {
      const t = escapeHtml(it.title || "");
      const meta = [it.time, it.source].filter(Boolean).join(" · ");
      const url = String(it.url || "").trim();
      const body = url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${t}</a>`
        : t;
      return (
        `<li>` +
        `<div class="watching-news-title">${body}</div>` +
        (meta ? `<div class="watching-news-meta">${escapeHtml(meta)}</div>` : "") +
        `</li>`
      );
    })
    .join("");
}

export const WATCHING_NEWS_AI_LOADING_HTML = `
  <div class="watching-news-ai-loading">
    <div class="watching-news-ai-spinner"></div>
    <p>正在分析舆情数据…</p>
  </div>
`;

export function buildWatchingNewsAiAnalysisHtml(analysis, escapeHtml) {
  const esc = escapeHtml;
  let analysisHtml;
  if (typeof marked !== "undefined") {
    analysisHtml = marked.parse(analysis, { gfm: true, breaks: true });
  } else {
    analysisHtml = esc(analysis).replace(/\n/g, "<br/>");
  }
  return `<div class="watching-news-ai-text">${analysisHtml}</div>`;
}

export function buildWatchingNewsAiErrorHtml(message, escapeHtml) {
  return `<div class="watching-news-ai-error">${escapeHtml(message)}</div>`;
}
