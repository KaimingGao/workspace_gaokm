/**
 * 观察池 insights 列格式化与 score 单元格 HTML（纯数据 / 字符串）。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { resolveRankingScore, resolveEodScore, resolveTauScore, resolveOnScore, fmtTableScore, isHeuristicScoreScale, Y_EOD_TITLE, Y_OC_REBALANCE_TITLE, Y_ON_TITLE, RANKING_REBALANCE_TITLE } from "../paper/fmt.js?v=p2544";
import { withQuoteGap } from "./watching_quotes_ui.js?v=p2389";

export function isOosFailedItem(it) {
  if (!it || typeof it !== "object") return false;
  if (it.oos_failed === true || it.oosFailed === true) return true;
  const src = String(it.return_model_source || "");
  if (src.startsWith("oos_failed")) return true;
  if (String(it.score_scale || "") === "heuristic_0_100") return true;
  if (String(it.oos_status || "") === "fail" || it.oos_blocked) return true;
  return false;
}

export function isSingleHeadItem(it) {
  if (!it || typeof it !== "object") return false;
  if (it.dual_score_single_head === true) return true;
  const head = String(it.dual_score_head || "");
  return head === "single_oo" || head === "single_tau";
}

export function singleHeadBadgeHtml(it, escapeHtml = defaultEscapeHtml) {
  const head = String((it && it.dual_score_head) || "");
  const win = String((it && it.dual_score_window) || "");
  const tauInTrade =
    it && it.dual_score_weights && it.dual_score_weights.tau_in_trade;
  const tau =
    it &&
    (it.y_tau != null || it["y_τc"] != null || it.score_rem != null || it.predicted_score_tau != null);
  let title = "ranking 单头降级 · 与双头票不同量纲";
  if (head === "single_tau") {
    title = "ranking 单头降级：仅 ŷ_τc（缺 ŷ_oo）· 与双头票不同量纲";
  } else if (head === "single_oo" && tau && (win === "eod_next" || tauInTrade === false)) {
    title = "收盘后 ranking=ŷ_oo（τ 对照保留，不进融合）";
  } else if (head === "single_oo") {
    title = "ranking 单头降级：仅 ŷ_oo（缺 ŷ_τc）· 与双头票不同量纲";
  }
  return `<span class="watching-single-head-badge" title="${escapeHtml(
    title
  )}">单</span>`;
}

export function yCheckBadgeHtml(it, escapeHtml = defaultEscapeHtml) {
  const check = String((it && it.y_check) || "");
  if (!check || check === "ok") return "";
  const labels = {
    conflict: { text: "歧", title: "Y·EOD 校验：双头分歧 · 降低今日执行信任" },
    low_conf: { text: "弱", title: "Y·EOD 校验：低置信（分歧或 σ 偏大）" },
    missing_tau: { text: "缺τ", title: "Y·EOD 校验：缺 ŷ_τc" },
    single_head: { text: "单", title: "Y·EOD 校验：单头降级" },
  };
  const pack = labels[check] || {
    text: "校",
    title: `Y·EOD 校验：${check}`,
  };
  return `<span class="watching-y-check-badge is-${escapeHtml(
    check
  )}" title="${escapeHtml(pack.title)}">${escapeHtml(pack.text)}</span>`;
}

export function oosFailedBadgeHtml(escapeHtml = defaultEscapeHtml) {
  return (
    `<span class="watching-oos-badge" title="${escapeHtml(
      "OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照"
    )}">OOS</span>`
  );
}

export function formatWatchingExcess(it) {
  // 主表只显示百分比；强弱标签进 title，避免窄列 ellipsis 看起来像空值
  if (it.excess_return_pct != null && !Number.isNaN(Number(it.excess_return_pct))) {
    return `${Number(it.excess_return_pct) >= 0 ? "+" : ""}${Number(it.excess_return_pct).toFixed(1)}%`;
  }
  if (it.excess_label) return String(it.excess_label);
  return null;
}

export function formatWatchingExcessTitle(it) {
  const base = formatWatchingExcess(it);
  if (!base) return "";
  if (it.excess_label) return `${base} ${it.excess_label}`;
  return base;
}

/**
 * @param {object} it insight item
 * @param {(n: number|null) => string} fmtScore
 * @param {(it: object) => string} watchingScoreDetail
 */
export function buildWatchingScoreDisplay(it, fmtScore, watchingScoreDetail) {
  const scoreNum = resolveRankingScore(it);
  const scoreEodNum = resolveEodScore(it);
  const scoreTauNum = resolveTauScore(it);
  const scoreOnNum = resolveOnScore(it);
  const belowMin = !!it.below_min_score;
  const singleHead = isSingleHeadItem(it);
  const yCheck = String(it.y_check || "");
  const yCheckFail = !!(yCheck && yCheck !== "ok");
  const scoreDetail = watchingScoreDetail(it);
  const scoreBase = fmtTableScore(it, scoreNum);
  const scoreText =
    scoreBase === "—" ? "—" : belowMin ? `${scoreBase}↓` : scoreBase;
  const scoreEodText = fmtTableScore(it, scoreEodNum);
  const scoreTauText = fmtTableScore(it, scoreTauNum);
  const scoreOnText = fmtTableScore(it, scoreOnNum);
  const scoreTitle = isHeuristicScoreScale(it)
    ? scoreNum != null
      ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
      : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
    : yCheckFail
      ? `Y·EOD 校验 ${yCheck} · 悬停看分歧/σ`
    : singleHead
      ? `ranking 单头降级（${String(it.dual_score_head || "single")}）· 悬停看详情`
    : belowMin
      ? `低于 ŷ_oo 门槛（表列为 ranking）`
      : it.return_model_source === "oos_failed_global"
        ? "OOS 失败 · 组/全局 ŷ 对照"
        : it.return_model_source === "cluster_shadow_fallback"
          ? "缺全局模型 · 组 ŷ shadow"
          : RANKING_REBALANCE_TITLE;
  const scoreEodTitle = scoreEodNum == null ? "暂无 ŷ_oo" : Y_EOD_TITLE;
  const scoreTauTitle = scoreTauNum == null ? "暂无 ŷ_τc" : Y_OC_REBALANCE_TITLE;
  const scoreOnTitle = scoreOnNum == null ? "暂无 ŷ_co" : Y_ON_TITLE;
  return {
    scoreNum,
    scoreEodNum,
    scoreTauNum,
    scoreOnNum,
    belowMin,
    singleHead,
    yCheck,
    yCheckFail,
    dualScoreHead: it.dual_score_head || null,
    scoreDetail,
    scoreText,
    scoreEodText,
    scoreTauText,
    scoreOnText,
    scoreTitle,
    scoreEodTitle,
    scoreTauTitle,
    scoreOnTitle,
    scoreEodCls: scoreClsName(scoreEodNum),
    scoreTauCls: scoreClsName(scoreTauNum),
    scoreOnCls: scoreClsName(scoreOnNum),
  };
}

function scoreClsName(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "score-na";
  if (n > 0) return "score-up";
  if (n < 0) return "score-down";
  return "score-flat";
}

/**
 * @param {object} it
 * @param {object|null} row existing grid row data
 * @param {{ fmtScore: Function, scoreCls: Function, parseWatchingVolume: Function, watchingScoreDetail: Function }} deps
 */
export function buildWatchingInsightsGridPatch(it, row, deps) {
  const prev = row && row.getData ? row.getData() : row || {};
  const quoteLike = {
    open: it.open != null ? it.open : prev.open,
    open_raw: it.open_raw != null ? it.open_raw : prev.openNum,
    price: it.price != null ? it.price : prev.price,
    price_raw: it.price_raw != null ? it.price_raw : prev.price,
    change_percent: prev.chgNum,
    chgNum: prev.chgNum,
    prev_close: prev.prev_close,
  };
  const scored = {
    ...withQuoteGap(it, quoteLike),
    open: quoteLike.open,
    open_raw: quoteLike.open_raw,
    day_open: it.day_open != null ? it.day_open : quoteLike.open_raw,
    price: quoteLike.price,
    price_raw: quoteLike.price_raw,
    price_tau: it.price_tau != null ? it.price_tau : quoteLike.price_raw,
  };
  const { fmtScore, scoreCls, parseWatchingVolume, watchingScoreDetail } = deps;
  const excess = formatWatchingExcess(scored);
  const excessTitle = formatWatchingExcessTitle(scored);
  const { scoreNum, scoreEodNum, scoreTauNum, scoreOnNum, belowMin, singleHead, yCheck, yCheckFail, dualScoreHead, scoreDetail, scoreText, scoreEodText, scoreTauText, scoreOnText, scoreTitle, scoreEodTitle, scoreTauTitle, scoreOnTitle, scoreEodCls, scoreTauCls, scoreOnCls } =
    buildWatchingScoreDisplay(scored, fmtScore, watchingScoreDetail);
  const volNum = it.volume != null ? parseWatchingVolume(it.volume) : NaN;
  return {
    score: scoreText,
    scoreNum,
    scoreEod: scoreEodText,
    scoreEodNum,
    scoreEodCls,
    scoreEodTitle,
    scoreTau: scoreTauText,
    scoreTauNum,
    scoreTauCls,
    scoreTauTitle,
    scoreOn: scoreOnText,
    scoreOnNum,
    scoreOnCls,
    scoreOnTitle,
    scoreCls: `${scoreCls(scoreNum)}${singleHead ? " score-single-head" : ""}${
      yCheckFail ? " score-y-check-fail" : ""
    }`.trim(),
    scoreDetail,
    scoreTitle,
    scoreBelowMin: belowMin,
    scoreSingleHead: singleHead,
    yCheck,
    yCheckFail,
    yDisagree: scored.y_disagree,
    eodTrust: scored.eod_trust,
    dualScoreHead,
    excess: excess || "—",
    excessTitle: excessTitle || excess || "",
    excessNum:
      it.excess_return_pct != null && !Number.isNaN(Number(it.excess_return_pct))
        ? Number(it.excess_return_pct)
        : null,
    vol: it.volume != null ? String(it.volume) : prev.vol,
    volNum: Number.isFinite(volNum) ? volNum : prev.volNum,
    volr:
      it.volume_ratio != null && !Number.isNaN(Number(it.volume_ratio))
        ? Number(it.volume_ratio).toFixed(2)
        : "—",
    pe: it.pe != null && !Number.isNaN(Number(it.pe)) ? Number(it.pe).toFixed(1) : "—",
    pb: it.pb != null && !Number.isNaN(Number(it.pb)) ? Number(it.pb).toFixed(2) : "—",
    isHardReject: !!it.hard_reject,
    oosFailed: isOosFailedItem(it),
  };
}

export function buildWatchingScoreCellHtml(
  { scoreText, scoreDetail, scoreTitle, scoreNum, belowMin, singleHead, dualScoreHead, yCheck },
  scoreClsFn,
  escapeHtml = defaultEscapeHtml
) {
  const esc = escapeHtml;
  const badges = [];
  if (singleHead) {
    badges.push(
      singleHeadBadgeHtml(
        { dual_score_head: dualScoreHead, dual_score_single_head: true },
        escapeHtml
      )
    );
  }
  const yBadge = yCheckBadgeHtml({ y_check: yCheck }, escapeHtml);
  if (yBadge) badges.push(yBadge);
  return (
    `<span class="watching-score-cell paper-hold-score has-tip ${esc(
      scoreClsFn(scoreNum)
    )}${belowMin ? " score-below-min" : ""}${
      singleHead ? " score-single-head" : ""
    }${yCheck && yCheck !== "ok" ? " score-y-check-fail" : ""}" ` +
    `data-score-detail="${esc(scoreDetail)}" data-score-tip="ranking" title="${esc(scoreTitle)}">` +
    `${esc(scoreText)}${badges.join("")}</span>`
  );
}

export function buildWatchingYScoreCellHtml(
  { text, num, title, detail, tip = "eod", skin = "eod" },
  scoreClsFn,
  escapeHtml = defaultEscapeHtml
) {
  const esc = escapeHtml;
  const shown = text != null && text !== "" ? text : "—";
  const cls = scoreClsFn(num);
  const skinCls =
    skin === "tau"
      ? "watching-score-tau"
      : skin === "on"
        ? "watching-score-on"
        : skin === "nowcast"
          ? "watching-score-nowcast"
          : "watching-score-eod";
  if (!detail) {
    return `<span class="watching-score-cell ${skinCls} paper-hold-score ${esc(cls)}">${esc(
      shown
    )}</span>`;
  }
  return (
    `<span class="watching-score-cell ${skinCls} paper-hold-score has-tip ${esc(cls)}" ` +
    `data-score-detail="${esc(detail)}" data-score-tip="${esc(tip)}" title="${esc(
      title || ""
    )}">` +
    `${esc(shown)}</span>`
  );
}

export function buildWatchingCalScoreCellHtml(disp, scoreClsFn, escapeHtml = defaultEscapeHtml) {
  return buildWatchingYScoreCellHtml(
    {
      text: disp.scoreEodText,
      num: disp.scoreEodNum,
      title: disp.scoreEodTitle,
      detail: disp.scoreDetail,
      tip: "eod",
      skin: "eod",
    },
    scoreClsFn,
    escapeHtml
  );
}

export function buildWatchingTauScoreCellHtml(disp, scoreClsFn, escapeHtml = defaultEscapeHtml) {
  return buildWatchingYScoreCellHtml(
    {
      text: disp.scoreTauText,
      num: disp.scoreTauNum,
      title: disp.scoreTauTitle,
      detail: disp.scoreDetail,
      tip: "tau",
      skin: "tau",
    },
    scoreClsFn,
    escapeHtml
  );
}

export function buildWatchingOnScoreCellHtml(disp, scoreClsFn, escapeHtml = defaultEscapeHtml) {
  return buildWatchingYScoreCellHtml(
    {
      text: disp.scoreOnText,
      num: disp.scoreOnNum,
      title: disp.scoreOnTitle,
      detail: disp.scoreDetail,
      tip: "on",
      skin: "on",
    },
    scoreClsFn,
    escapeHtml
  );
}

export function buildWatchingInsightsStatusText(okN, total, items, meta) {
  const srcSample = (items || []).find((x) => x && x.weight_source) || {};
  const mode = srcSample.cluster_mode || "";
  const wsrc = String(srcSample.weight_source || "");
  let scoreMode = "组ŷ";
  if (wsrc.startsWith("cluster:")) scoreMode = `组ŷ · ${wsrc.slice("cluster:".length) || "组"}`;
  else if (wsrc === "global+shadow") scoreMode = "对照中 · 映射就绪";
  else if (wsrc === "global_fallback") scoreMode = "未映射组（无ŷ）";
  else if (mode === "active") scoreMode = "active · 组ŷ";
  else if (mode === "shadow") scoreMode = "shadow · 组ŷ映射";
  const cached = Number(meta && meta.cachedCount);
  const cacheBit =
    Number.isFinite(cached) && cached > 0 ? ` · 缓存 ${cached}` : "";
  return `摘要已更新 · ${okN}/${total}${cacheBit} · score ${scoreMode}（与交易执行同源）`;
}

export function buildWatchingInsightsErrorStatus(err) {
  if (err && err.name === "AbortError") return "摘要超时（页面仍可用）";
  return `摘要失败：${String((err && err.message) || err)}`;
}

export function buildWatchingInsightsGridErrorPatch(d) {
  const row = d || {};
  return {
    score: row.score === "…" ? "—" : row.score,
    scoreEod: row.scoreEod === "…" ? "—" : row.scoreEod,
    scoreTau: row.scoreTau === "…" ? "—" : row.scoreTau,
    scoreOn: row.scoreOn === "…" ? "—" : row.scoreOn,
    excess: row.excess === "…" ? "—" : row.excess,
    volr: row.volr === "…" ? "—" : row.volr,
    pe: row.pe === "…" ? "—" : row.pe,
    pb: row.pb === "…" ? "—" : row.pb,
  };
}

/** 原生表回退：insight 文本字段（score 单元格另用 buildWatchingScoreCellHtml）。 */
export function buildWatchingInsightsNativeFields(it) {
  return {
    excess: formatWatchingExcess(it),
    vol: it.volume || null,
    volr:
      it.volume_ratio != null && !Number.isNaN(Number(it.volume_ratio))
        ? Number(it.volume_ratio).toFixed(2)
        : null,
    pe: it.pe != null && !Number.isNaN(Number(it.pe)) ? Number(it.pe).toFixed(1) : null,
    pb: it.pb != null && !Number.isNaN(Number(it.pb)) ? Number(it.pb).toFixed(2) : null,
  };
}
