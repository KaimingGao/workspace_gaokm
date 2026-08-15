/**
 * 观察池 insights 列格式化与 score 单元格 HTML（纯数据 / 字符串）。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { resolveTradeScore, resolveEodScore, resolveEodRemScore, resolveCalTradeScore } from "../paper/fmt.js?v=p1092";

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
  const scoreDetail = watchingScoreDetail(it);
  const scoreBase = fmtScore(scoreNum);
  const scoreText =
    scoreBase === "—" ? "—" : belowMin ? `${scoreBase}↓` : scoreBase;
  const scoreCalText = fmtScore(scoreCalNum);
  const scoreTitle = belowMin
    ? `低于ŷ_EOD门槛 ${it.min_score ?? "—"}（表列为 ŷ_trade）`
    : it.return_model_source === "cluster_shadow_fallback"
      ? "缺全局 return_model · 暂用组 ŷ（shadow）"
      : "ŷ_trade · 悬停看 ŷ_EOD_rem / ŷ_τ";
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
  const { scoreNum, scoreCalNum, belowMin, scoreDetail, scoreText, scoreCalText, scoreTitle, scoreCalTitle, scoreCalOor } =
    buildWatchingScoreDisplay(it, fmtScore, watchingScoreDetail);
  const scoreEodNum = resolveEodScore(it);
  const scoreEodRemNum = resolveEodRemScore(it);
  const volNum = it.volume != null ? parseWatchingVolume(it.volume) : NaN;
  const prev = row && row.getData ? row.getData() : row || {};
  return {
    score: scoreText,
    scoreNum,
    scoreCal: scoreCalText,
    scoreCalNum,
    scoreCalCls: `${scoreCls(scoreCalNum)}${scoreCalOor ? " is-cal-oor" : ""}`.trim(),
    scoreCalTitle,
    scoreCalOor,
    scoreEodNum,
    scoreEodRemNum,
    scoreCls: scoreCls(scoreNum),
    scoreDetail,
    scoreTitle,
    scoreBelowMin: belowMin,
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
  };
}

export function buildWatchingScoreCellHtml(
  { scoreText, scoreDetail, scoreTitle, scoreNum, belowMin },
  scoreClsFn,
  escapeHtml = defaultEscapeHtml
) {
  const esc = escapeHtml;
  return (
    `<span class="watching-score-cell paper-hold-score has-tip ${esc(
      scoreClsFn(scoreNum)
    )}${belowMin ? " score-below-min" : ""}" ` +
    `data-score-detail="${esc(scoreDetail)}" data-score-tip="trade" title="${esc(scoreTitle)}">` +
    `${esc(scoreText)}</span>`
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
