/**
 * 观察池 insights 列格式化与 score 单元格 HTML（纯数据 / 字符串）。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { resolveTradeScore, resolveEodScore, resolveEodRemScore, resolveCalTradeScore, fmtTableScore, isHeuristicScoreScale } from "../paper/fmt.js?v=p1132";
import { formatWatchingResidual } from "./watching_quotes_ui.js";

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
  return head === "single_eod" || head === "single_tau";
}

export function singleHeadBadgeHtml(it, escapeHtml = defaultEscapeHtml) {
  const head = String((it && it.dual_score_head) || "");
  const title =
    head === "single_tau"
      ? "ŷ_trade 单头降级：仅 ŷ_τ（缺 EOD rem）· 与双头票不同量纲"
      : head === "single_eod"
        ? "ŷ_trade 单头降级：仅 ŷ_EOD（缺 ŷ_τ）· 与双头票不同量纲"
        : "ŷ_trade 单头降级 · 与双头票不同量纲";
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
    missing_tau: { text: "缺τ", title: "Y·EOD 校验：缺 ŷ_τ" },
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
  const scoreNum = resolveTradeScore(it);
  const scoreCalNum = resolveCalTradeScore(it);
  const belowMin = !!it.below_min_score;
  const singleHead = isSingleHeadItem(it);
  const yCheck = String(it.y_check || "");
  const yCheckFail = !!(yCheck && yCheck !== "ok");
  const scoreDetail = watchingScoreDetail(it);
  const scoreBase = fmtTableScore(it, scoreNum);
  const scoreText =
    scoreBase === "—" ? "—" : belowMin ? `${scoreBase}↓` : scoreBase;
  const scoreCalText = fmtTableScore(it, scoreCalNum);
  const scoreTitle = isHeuristicScoreScale(it)
    ? scoreNum != null
      ? "OOS 失败 · 表列组/全局 ŷ% · heuristic 见 tip"
      : "OOS 失败 · 无 ŷ% · tip 看 heuristic(0–100)"
    : yCheckFail
      ? `Y·EOD 校验 ${yCheck} · 悬停看分歧/σ`
    : singleHead
      ? `ŷ_trade 单头降级（${String(it.dual_score_head || "single")}）· 悬停看详情`
    : belowMin
      ? `低于ŷ_EOD门槛 ${it.min_score ?? "—"}（表列为 ŷ_trade）`
      : it.return_model_source === "oos_failed_global"
        ? "OOS 失败 · 主分全局 ŷ% · 悬停看组 ŷ% 对照"
        : it.return_model_source === "cluster_shadow_fallback"
          ? "缺全局 return_model · 暂用组 ŷ（shadow）"
          : "ŷ_trade · 悬停看 ŷ_EOD / heuristic 对照";
  const scoreCalTitle =
    scoreCalNum == null
      ? "暂无 g(ŷ) 映射（拟合并写入 live 后可见）"
      : it.score_calibration_eod_oor ||
          it.score_calibration_eod_rem_oor ||
          it.score_calibration_tau_oor
        ? "g(ŷ_trade) 对照 · 部分落在拟合域外（端点钳制）· 悬停看校准 tip"
        : "g(ŷ_trade) 对照 · 不进决策 · 悬停看校准 tip";
  const scoreCalOor = !!(
    it.score_calibration_eod_oor ||
    it.score_calibration_eod_rem_oor ||
    it.score_calibration_tau_oor
  );
  return {
    scoreNum,
    scoreCalNum,
    belowMin,
    singleHead,
    yCheck,
    yCheckFail,
    dualScoreHead: it.dual_score_head || null,
    scoreDetail,
    scoreText,
    scoreCalText,
    scoreTitle,
    scoreCalTitle,
    scoreCalOor,
  };
}

/**
 * @param {object} it
 * @param {object|null} row existing grid row data
 * @param {{ fmtScore: Function, scoreCls: Function, parseWatchingVolume: Function, watchingScoreDetail: Function }} deps
 */
export function buildWatchingInsightsGridPatch(it, row, deps) {
  const { fmtScore, scoreCls, parseWatchingVolume, watchingScoreDetail } = deps;
  const excess = formatWatchingExcess(it);
  const excessTitle = formatWatchingExcessTitle(it);
  const { scoreNum, scoreCalNum, belowMin, singleHead, yCheck, yCheckFail, dualScoreHead, scoreDetail, scoreText, scoreCalText, scoreTitle, scoreCalTitle, scoreCalOor } =
    buildWatchingScoreDisplay(it, fmtScore, watchingScoreDetail);
  const scoreEodNum = resolveEodScore(it);
  const scoreEodRemNum = resolveEodRemScore(it);
  const volNum = it.volume != null ? parseWatchingVolume(it.volume) : NaN;
  const prev = row && row.getData ? row.getData() : row || {};
  const { residualNum, residual, residualCls } = formatWatchingResidual(
    scoreCalNum,
    prev.chgNum
  );
  return {
    score: scoreText,
    scoreNum,
    scoreCal: scoreCalText,
    scoreCalNum,
    scoreCalCls: `${scoreCls(scoreCalNum)}${scoreCalOor ? " is-cal-oor" : ""}`.trim(),
    scoreCalTitle,
    scoreCalOor,
    residual,
    residualCls,
    residualNum,
    residualTitle: "残差 = 校准 − 涨跌（百分点）；正=校准高于当日涨跌",
    scoreEodNum,
    scoreEodRemNum,
    scoreCls: `${scoreCls(scoreNum)}${singleHead ? " score-single-head" : ""}${
      yCheckFail ? " score-y-check-fail" : ""
    }`.trim(),
    scoreDetail,
    scoreTitle,
    scoreBelowMin: belowMin,
    scoreSingleHead: singleHead,
    yCheck,
    yCheckFail,
    yDisagree: it.y_disagree,
    eodTrust: it.eod_trust,
    dualScoreHead,
    stance: it.stance_short || "—",
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
    `data-score-detail="${esc(scoreDetail)}" data-score-tip="trade" title="${esc(scoreTitle)}">` +
    `${esc(scoreText)}${badges.join("")}</span>`
  );
}

export function buildWatchingCalScoreCellHtml(
  { scoreCalText, scoreDetail, scoreCalTitle, scoreCalNum, scoreCalOor },
  scoreClsFn,
  escapeHtml = defaultEscapeHtml
) {
  const esc = escapeHtml;
  const text = scoreCalText != null && scoreCalText !== "" ? scoreCalText : "—";
  const title = scoreCalTitle || "g(ŷ) 对照";
  const oor = scoreCalOor ? " is-cal-oor" : "";
  if (!scoreDetail) {
    return `<span class="watching-score-cell watching-score-cal paper-hold-score ${esc(
      scoreClsFn(scoreCalNum)
    )}${oor}">${esc(text)}</span>`;
  }
  return (
    `<span class="watching-score-cell watching-score-cal paper-hold-score has-tip ${esc(
      scoreClsFn(scoreCalNum)
    )}${oor}" ` +
    `data-score-detail="${esc(scoreDetail)}" data-score-tip="cal" title="${esc(title)}">` +
    `${esc(text)}</span>`
  );
}

export function buildWatchingInsightsStatusText(okN, total, items) {
  const srcSample = (items || []).find((x) => x && x.weight_source) || {};
  const mode = srcSample.cluster_mode || "";
  const wsrc = String(srcSample.weight_source || "");
  let scoreMode = "组ŷ";
  if (wsrc.startsWith("cluster:")) scoreMode = `组ŷ · ${wsrc.slice("cluster:".length) || "组"}`;
  else if (wsrc === "global+shadow") scoreMode = "对照中 · 映射就绪";
  else if (wsrc === "global_fallback") scoreMode = "未映射组（无ŷ）";
  else if (mode === "active") scoreMode = "active · 组ŷ";
  else if (mode === "shadow") scoreMode = "shadow · 组ŷ映射";
  return `摘要已更新 · ${okN}/${total} · score ${scoreMode}（与交易执行同源）`;
}

export function buildWatchingInsightsErrorStatus(err) {
  if (err && err.name === "AbortError") return "摘要超时（页面仍可用）";
  return `摘要失败：${String((err && err.message) || err)}`;
}

export function buildWatchingInsightsGridErrorPatch(d) {
  const row = d || {};
  return {
    score: row.score === "…" ? "—" : row.score,
    scoreCal: row.scoreCal === "…" ? "—" : row.scoreCal,
    stance: row.stance === "…" ? "—" : row.stance,
    excess: row.excess === "…" ? "—" : row.excess,
    volr: row.volr === "…" ? "—" : row.volr,
    pe: row.pe === "…" ? "—" : row.pe,
    pb: row.pb === "…" ? "—" : row.pb,
  };
}

/** 原生表回退：insight 文本字段（score 单元格另用 buildWatchingScoreCellHtml）。 */
export function buildWatchingInsightsNativeFields(it) {
  return {
    stance: it.stance_short || null,
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
