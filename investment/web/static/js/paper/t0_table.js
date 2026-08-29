/** 做 T 回测表格：统一列定义，避免表头/数据错位。 */

import {
  escapeText,
  paperMetricClass,
  fmtTableScore,
  fmtScore,
  resolveTradeScore,
  resolveEodScore,
  resolveTauScore,
  resolveOnScore,
  resolveNowcastCcScore,
  Y_EOD_TITLE,
  Y_TAU_TITLE,
  Y_ON_TITLE,
  Y_NC_TITLE,
  Y_NC_OC_TITLE,
  Y_PATH_TITLE,
  PATH_REALIZED_TITLE,
  EOD_REALIZED_TITLE,
  TAU_REALIZED_TITLE,
  resolvePathScore,
  fmtPathScore,
  nowcastOcPct,
} from "./fmt.js?v=p1528";
import {
  adaptiveSizingDayTip,
  normalizeYTauMap,
  yTauMapScoreTip,
} from "./execution_ui.js";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p1486";
import { TRADE_TITLE } from "../quant/watching_quotes_ui.js";

export const SKIP_CAT_LABEL = {
  missing_scores: "缺ŷ快照",
  missing_minute: "缺分钟线",
  y_eod_flat: "y_eod未过门槛",
  y_tau_flat: "y_τ横盘",
  y_tau_weak: "y_τ弱信号",
  y_path_flat: "y_path横盘",
  y_path_disagree: "y_τ↔y_path异号",
  gap_tier_skip: "大缺口反向跳过",
  path_abandon: "前缀无空间放弃",
  prefix_segment: "固定前缀待确认",
  y_trade_weak: "y_trade幅度不足",
  eod_tau_disagree: "y_eod↔y_τ异号",
  trade_tau_disagree: "y_trade↔y_τ异号",
  trade_tau_sign: "异号跳过",
  conflict: "旧冲突",
  amplitude: "振幅不足",
  directional_amplitude: "方向振幅",
  lot_size: "手数不足",
  tplus1: "T+1无可卖",
  path: "路径否决",
  trigger_miss: "未触达",
  other: "其它",
};

/** 跳过类型口径（环形图 / 图例悬停） */
export const SKIP_CAT_TIP = {
  missing_scores:
    "当日缺 ŷ 快照（ŷ_τ / ŷ_trade 等算不出），无法定方向。",
  missing_minute:
    "缺当日分钟线，无法模拟触达与成交路径。",
  y_tau_flat:
    "|ŷ_τ| 低于入场门槛（默认 0.02%）：视为横盘，不定向、不开仓（path 关时）。",
  y_eod_flat:
    "|ŷ_eod| 低于 y_eod_enter（默认 0.01%）：隔夜主轴过弱，dual_y 跳过。",
  y_tau_weak:
    "历史跳过类别（旧双闸弱信号区）；现已并入 y_τ 入场，新跑批不再产生。",
  y_path_flat:
    "ŷ_path 未过 y_path_enter（默认 0.02%），或 ŷ_τ 未过 y_tau_enter（path 开时联合闸）。",
  y_path_disagree:
    "ŷ_τ 与 ŷ_path 异号：dual_y 准入要求两预测同号且各过门槛。",
  eod_tau_disagree:
    "|ŷ_eod|>y_eod_strong（默认 0.1%）且 ŷ_eod 与 ŷ_τ 异号：隔夜主轴与盘中方向冲突，跳过。",
  trade_tau_disagree:
    "|ŷ_trade|>y_trade_strong（默认 0.1%）且 ŷ_trade 与 ŷ_τ 异号：融合幅度与盘中方向冲突，跳过。",
  gap_tier_skip:
    "大缺口档位与拟做方向冲突（如大高开仍想正 T），规则直接跳过。",
  path_abandon:
    "固定前缀 N 根 5m 齐窗后仍未过振幅/后半阴阳占比：放弃当日做 T。",
  prefix_segment:
    "振幅已够，但固定前缀未齐或后半阴阳占比未达标：正 T 待后半上涨、反 T 待后半下跌（Worker 可重试）。",
  y_trade_weak:
    "|ŷ_trade| 未过入场（y_trade_enter），融合分太弱不开仓。",
  trade_tau_sign:
    "强 trade/nc 与 τ 异号，或历史 τ↔nowcast 异号闸跳过。",
  conflict:
    "旧版 eod↔τ / y_check 冲突闸（已下线），历史回放可能仍出现。",
  amplitude:
    "固定前缀高低振幅低于侧向「振幅下限%」，无法形成有效做 T 空间。",
  directional_amplitude:
    "沿选定方向（上移/下移）的可用振幅不足（旧口径；现多并入固定前缀闸）。",
  lot_size:
    "按规则算出的买卖量不足 1 手，或现金买不起。",
  tplus1:
    "T+1 锁定：当日无可卖旧仓（反 T 先卖 / 正 T 卖旧仓走不通）。",
  path:
    "分钟路径规则否决（veto），与预测头 dual_y 不一致类不同。",
  trigger_miss:
    "有方向，但全天未触达卖出/低吸触发价。",
  other:
    "未归入上述类型的其它跳过原因。",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table paper-t0-trades-table";

/** 列轨宽度：colgroup inline width + CSS 同源，避免 fixed 表头/体错位 */
const T0_TRADE_COL_W = {
  stock: "10.5rem",
  date: "104px",
  eod: "82px",
  tau: "82px",
  path: "110px",
  trade: "94px",
  on: "82px",
  nc: "82px",
  dir: "48px",
  process: "320px",
  retPct: "76px",
  pnl: "72px",
  exp: "72px",
  reason: "12rem",
  act: "52px",
};

/** 与数据中心主表一致：名称最多 6 字 + … */
function truncateName(name, max = 6) {
  const full = String(name || "").trim();
  const chars = Array.from(full);
  if (chars.length <= max) return full;
  return `${chars.slice(0, max).join("")}…`;
}

function t0Col(cls, key) {
  const w = T0_TRADE_COL_W[key];
  return `<col class="${cls}" style="width:${w}" />`;
}

/** 成交日：统一 YYYY-MM-DD，避免 ISO 时间戳被列宽裁成 2026-08-2… */
function fmtTradeDate(raw, fallback) {
  const s = String(raw || fallback || "").trim();
  if (!s) return "";
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : s.slice(0, 10);
}

/** 股票名 + 代码（对齐数据中心 watching-stock：名上行、码下行） */
export function stockCellHtml(row, fallback = {}) {
  const code = String((row && row.stock_code) || fallback.stock_code || "").trim();
  const name = String((row && row.stock_name) || fallback.stock_name || "").trim();
  const fullName = name && name !== code ? name : code || "—";
  const title = name && code && name !== code ? `${name} ${code}` : name || code;
  if (!name && !code) {
    return `<td class="rebalance-stock paper-t0-col-stock watching-stock">—</td>`;
  }
  const display = truncateName(fullName);
  return (
    `<td class="rebalance-stock paper-t0-col-stock watching-stock" title="${escapeText(title || fullName)}">` +
    `<span class="watching-name-row">` +
    `<span class="watching-name-text" title="${escapeText(fullName)}" data-full-name="${escapeText(
      fullName
    )}">${escapeText(display)}</span>` +
    `</span>` +
    (code
      ? `<span class="watching-code-sub">${escapeText(code)}</span>`
      : "") +
    `</td>`
  );
}


export function pickTradeDays(data) {
  const sample = (data && (data.trade_days_sample || data.days)) || [];
  return sample.filter((d) => _isTradedDay(d));
}

/** 成交明细：仅成交日（无成交/跳过行不进表）。 */
export function pickDetailDays(data) {
  const traded = Array.isArray(data?.trade_days_sample) ? data.trade_days_sample : [];
  const allDays = Array.isArray(data?.days) ? data.days : [];
  const out = [];
  const seen = new Set();
  const add = (d) => {
    if (!d || typeof d !== "object") return;
    const k = `${String(d.stock_code || "").trim()}|${String(d.date || "").slice(0, 10)}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push(d);
  };
  for (const d of traded) {
    if (_isTradedDay(d)) add(d);
  }
  // trade_days_sample 缺省时从 days 取成交（勿把空数组当成 falsy 再灌全日跳过）
  if (!traded.length) {
    for (const d of allDays) {
      if (_isTradedDay(d)) add(d);
    }
  }
  out.sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
  return out;
}

function _isTradedDay(d) {
  return (
    (Array.isArray(d.trades) && d.trades.length > 0) ||
    Number(d.sold_qty) > 0 ||
    Number(d.bought_qty) > 0 ||
    Number(d.covered_qty) > 0 ||
    Number(d.sold_back_qty) > 0 ||
    Number(d.pnl) !== 0 ||
    Number(d.exposure_pnl) !== 0
  );
}

/** 多票 / 多代码时固定显示股票列，避免表头时有时无。 */
export function shouldShowStockColumn(data, days) {
  if (Number(data?.holding_count) > 1) return true;
  if (Number(data?.ok_count) > 1) return true;
  const codes = new Set();
  for (const d of days || []) {
    const c = String(d?.stock_code || "").trim();
    if (c) codes.add(c);
  }
  return codes.size > 1;
}

function yTauEnter(data) {
  const enter = data?.rules?.y_tau_enter;
  return enter != null && Number.isFinite(Number(enter)) ? Number(enter) : 0.25;
}

/** 敞口列：仅「允许隔夜 + 第二腿未 intraday 完成」时有值；强制回补时损益全在 PnL。 */
function exposureColTitle(data) {
  const forced =
    data?.rules?.must_cover_same_day === true ||
    data?.execution?.t0?.must_cover_same_day === true;
  const dualY = String(data?.rules?.direction || data?.execution?.t0?.direction || "dual_y") === "dual_y";
  if (forced) {
    return "隔夜敞口损益；已强制当日回补 → 全 0（损益在 PnL）";
  }
  if (dualY) {
    return "隔夜敞口损益；dual_y 默认 y_on 策略多强制收盘回补 → 常为 0（损益在 PnL）";
  }
  return "未 intraday 完成第二腿、且允许隔夜时的 mark-to-market；强制回补时为 0";
}

function fmtExposureCell(d) {
  const exp = Number(d.exposure_pnl);
  if (!Number.isFinite(exp) || Math.abs(exp) < 1e-9) {
    const cp = d.cover_policy;
    const forced =
      d.must_cover_same_day === true || (cp && cp.must_cover === true);
    const tip = forced
      ? cp?.reason
        ? `已强制回补：${cp.reason} · 损益计入 PnL`
        : "已强制收盘回补 · 损益计入 PnL"
      : "无隔夜敞口或未 mark 敞口";
    return { text: "0", tip };
  }
  return { text: String(exp), tip: "允许隔夜且第二腿未 intraday 完成的敞口损益" };
}

function pickScore(d, key) {
  const feats = d.direction_features || d.features || {};
  const scores = d.scores || {};
  const v = feats[key] ?? scores[key];
  return v != null && v !== "" && Number.isFinite(Number(v))
    ? `${Number(v).toFixed(2)}%`
    : "—";
}

function pickScoreNum(d, key) {
  const feats = d.direction_features || d.features || {};
  const scores = d.scores || {};
  const v = feats[key] ?? scores[key];
  const n = Number(v);
  return v != null && v !== "" && Number.isFinite(n) ? n : null;
}

function pickPathRealized(d) {
  const n =
    pickScoreNum(d, "path_realized") ??
    (d.path_realized != null && Number.isFinite(Number(d.path_realized))
      ? Number(d.path_realized)
      : null);
  if (n == null) return { text: "—", tip: PATH_REALIZED_TITLE, n: null };
  const reason = String(
    (d.direction_features && d.direction_features.path_realized_reason) ||
      (d.scores && d.scores.path_realized_reason) ||
      d.path_realized_reason ||
      ""
  ).trim();
  const reasonMap = {
    low_then_high: "先低后高",
    high_then_low: "先高后低",
    skew_up: "上冲偏大",
    skew_down: "下探偏大",
    flat_skew: "偏斜中性",
    same_bar_extreme: "同根极值",
    flat_range: "无振幅",
    sell_first: "先触卖(旧)",
    buy_first: "先触买(旧)",
    no_touch: "未触达(旧)",
    same_bar_both: "同根双触(旧)",
    invalid_ref_or_empty: "无效开盘/无分钟",
  };
  const label = reasonMap[reason] || reason || (n > 0 ? "先低后高·正T" : n < 0 ? "先高后低·反T" : "中性");
  const pred = pickScoreNum(d, "y_path");
  let hitNote = "";
  if (pred != null && n !== 0 && Math.abs(pred) >= 1e-9) {
    const agree = (pred > 0 && n > 0) || (pred < 0 && n < 0);
    hitNote = agree ? " · 与 ŷ_path 同号" : " · 与 ŷ_path 异号";
  }
  const trig = d.path_realized_trig && typeof d.path_realized_trig === "object"
    ? d.path_realized_trig
    : null;
  const trigNote =
    trig && trig.path_label_mode
      ? ` · 对照=${trig.path_label_mode}`
      : trig && trig.sell_trig_pct != null && trig.buy_trig_pct != null
        ? ` · 对照触发=卖${trig.sell_trig_pct}%/买${trig.buy_trig_pct}%`
        : trig && trig.note
          ? ` · ${trig.note}`
          : "";
  return {
    text: fmtPathScore(n),
    tip: `${PATH_REALIZED_TITLE} · ${label}${hitNote}${trigNote}`,
    n,
    agree:
      pred != null && n !== 0
        ? (pred > 0 && n > 0) || (pred < 0 && n < 0)
        : null,
  };
}

/** 百分点真实值：优先后端字段，缺则用开/收/昨收推算（兼容旧 payload）。 */
function fmtRealizedPct(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(2)}%`;
}

function _signAgree(pred, real, eps = 0.05) {
  if (pred == null || real == null) return null;
  if (Math.abs(pred) < eps || Math.abs(real) < eps) return null;
  return (pred > 0) === (real > 0);
}

function pickTauRealized(d) {
  let n =
    pickScoreNum(d, "tau_realized") ??
    (d.tau_realized != null && Number.isFinite(Number(d.tau_realized))
      ? Number(d.tau_realized)
      : null);
  if (n == null) {
    const o = Number(d.open);
    const c = Number(d.close);
    if (Number.isFinite(o) && Number.isFinite(c) && o > 0) {
      n = (c / o - 1) * 100;
    }
  }
  if (n == null) return { text: "—", tip: TAU_REALIZED_TITLE, n: null };
  const pred = pickScoreNum(d, "y_tau") ?? pickScoreNum(d, "direction_score");
  const agree = _signAgree(pred, n);
  return {
    text: fmtRealizedPct(n),
    tip:
      TAU_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ 同号" : agree === false ? " · 与 ŷ_τ 异号" : ""),
    n,
    agree,
  };
}

function pickEodRealized(d) {
  let n =
    pickScoreNum(d, "eod_realized") ??
    (d.eod_realized != null && Number.isFinite(Number(d.eod_realized))
      ? Number(d.eod_realized)
      : null);
  if (n == null) {
    const pc = Number(d.prev_close);
    const c = Number(d.close);
    if (Number.isFinite(pc) && Number.isFinite(c) && pc > 0) {
      n = (c / pc - 1) * 100;
    }
  }
  if (n == null) return { text: "—", tip: EOD_REALIZED_TITLE, n: null };
  const pred = pickScoreNum(d, "y_eod");
  const agree = _signAgree(pred, n);
  return {
    text: fmtRealizedPct(n),
    tip:
      EOD_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_EOD 同号" : agree === false ? " · 与 ŷ_EOD 异号" : ""),
    n,
    agree,
  };
}

/** 回测：预估值(真实值)；实时做 T 无收盘真实 label，只显示预估值。 */
function fmtPredRealizedText(predTxt, pr, showRealized) {
  if (!showRealized || pr == null || pr.n == null) return predTxt;
  return `${predTxt}(${pr.text})`;
}

function predRealizedAgreeCls(pr, showRealized) {
  if (!showRealized || !pr) return "";
  if (pr.agree === true) return " is-path-hit";
  if (pr.agree === false) return " is-path-miss";
  return "";
}

/** y_path：回测配对真实值；实时做 T 只显示 ŷ。 */
function pathMergedCellHtml(d, fallback, rules, scoreDetailJson, showRealized = true) {
  const it = t0DayScoreItem(d, fallback, rules);
  const pred = resolvePathScore(it);
  const predTxt = pred != null ? fmtPathScore(pred) : "—";
  const pr = showRealized ? pickPathRealized(d) : { n: null, tip: PATH_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? Y_PATH_TITLE : "暂无 ŷ_path"];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : PATH_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const text = fmtPredRealizedText(predTxt, pr, showRealized);
  return (
    `<td class="num paper-t0-col-path paper-t0-col-y paper-t0-col-y-path paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="path" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${escapeText(text)}</span>` +
    `</td>`
  );
}

/** y_eod / y_τ：回测配对真实值；实时做 T 只显示 ŷ。 */
function yPctMergedCellHtml(kind, d, fallback, rules, scoreDetailJson, titleExtra, showRealized = true) {
  const it = t0DayScoreItem(d, fallback, rules);
  const isEod = kind === "eod";
  const pred = isEod ? resolveEodScore(it) : resolveTauScore(it);
  let predTxt = pred != null ? fmtTableScore(it, pred) : "—";
  if (!isEod && predTxt === "—") {
    const ds = d.direction_score;
    if (ds != null && ds !== "" && Number.isFinite(Number(ds))) {
      predTxt = fmtTableScore(it, Number(ds));
    }
  }
  const pr = showRealized
    ? isEod
      ? pickEodRealized(d)
      : pickTauRealized(d)
    : { n: null, tip: isEod ? EOD_REALIZED_TITLE : TAU_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const yTitle = isEod ? Y_EOD_TITLE : Y_TAU_TITLE;
  const realTitle = isEod ? EOD_REALIZED_TITLE : TAU_REALIZED_TITLE;
  const tipParts = [
    pred != null || predTxt !== "—" ? yTitle : `暂无 ${isEod ? "ŷ_EOD" : "ŷ_τ"}`,
  ];
  if (showRealized) tipParts.push(pr.n != null ? pr.tip : realTitle);
  if (titleExtra) tipParts.push(String(titleExtra));
  if (showRealized) {
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const text = fmtPredRealizedText(predTxt, pr, showRealized);
  const colKey = kind;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="${escapeText(kind)}" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${escapeText(text)}</span>` +
    `</td>`
  );
}

/** 成交日 → 与持仓/观察同源的 tip + resolve 载荷。 */
function t0DayScoreItem(d, fallback = {}, rules = {}) {
  const scores = d.scores && typeof d.scores === "object" ? d.scores : {};
  const feats =
    (d.direction_features && typeof d.direction_features === "object"
      ? d.direction_features
      : null) ||
    (d.features && typeof d.features === "object" ? d.features : {}) ||
    {};
  const yEod = pickScoreNum(d, "y_eod");
  const yTau = pickScoreNum(d, "y_tau");
  const yTrade = pickScoreNum(d, "y_trade");
  const yOn = pickScoreNum(d, "y_on");
  const yNc = pickScoreNum(d, "y_nowcast");
  const yPath = pickScoreNum(d, "y_path");
  return {
    ...scores,
    stock_code: d.stock_code || fallback.stock_code || scores.stock_code || null,
    stock_name: d.stock_name || fallback.stock_name || scores.stock_name || null,
    predicted_score_eod: scores.predicted_score_eod ?? yEod,
    predicted_score: scores.predicted_score ?? scores.predicted_score_eod ?? yEod,
    predicted_score_tau: scores.predicted_score_tau ?? yTau,
    score_rem: scores.score_rem ?? scores.predicted_score_tau ?? yTau,
    predicted_score_blend: scores.predicted_score_blend ?? scores.decision_score ?? yTrade,
    decision_score: scores.decision_score ?? scores.predicted_score_blend ?? yTrade,
    score: scores.score ?? scores.predicted_score_blend ?? scores.decision_score ?? yTrade,
    predicted_score_on: scores.predicted_score_on ?? yOn,
    predicted_score_nowcast: scores.predicted_score_nowcast ?? yNc,
    predicted_score_path: scores.predicted_score_path ?? scores.y_path ?? yPath,
    y_path: scores.y_path ?? yPath,
    y_path_status: scores.y_path_status ?? null,
    y_path_error: scores.y_path_error ?? null,
    features_path: scores.features_path || feats.features_path || null,
    formula_terms_path: scores.formula_terms_path || scores.score_formula_terms_path || null,
    score_formula_terms_path: scores.formula_terms_path || scores.score_formula_terms_path || null,
    path_tip_model: scores.path_tip_model || null,
    y_path_enter:
      rules.y_path_enter != null && Number.isFinite(Number(rules.y_path_enter))
        ? Number(rules.y_path_enter)
        : 0.02,
    rules: rules && Object.keys(rules).length ? rules : null,
    predicted_score_blend_vs: scores.predicted_score_blend_vs ?? null,
    y_check: scores.y_check ?? feats.y_check ?? null,
    eod_trust: scores.eod_trust ?? feats.eod_trust ?? null,
    dual_score_window:
      scores.dual_score_window || feats.dual_score_window || "intraday",
    dual_score_fusion: scores.dual_score_fusion || feats.dual_score_fusion || null,
    dual_score_weights: scores.dual_score_weights || feats.dual_score_weights || null,
    dual_score_head: scores.dual_score_head || feats.dual_score_head || null,
    dual_score_single_head: scores.dual_score_single_head ?? feats.dual_score_single_head ?? null,
    as_of_tau: scores.as_of_tau || feats.as_of_tau || null,
    rem_tau: scores.rem_tau || null,
    gap_pct:
      scores.gap_pct ??
      feats.gap_pct ??
      (scores.features_tau && scores.features_tau.gap_pct != null
        ? scores.features_tau.gap_pct
        : null) ??
      (feats.features_tau && feats.features_tau.gap_pct != null
        ? feats.features_tau.gap_pct
        : null),
    y_nc: feats.y_nc ?? scores.y_nc ?? null,
    y_nc_oc:
      feats.y_nc_oc ??
      feats.y_nowcast_oc ??
      scores.y_nc_oc ??
      scores.y_nowcast_oc ??
      null,
    y_nowcast_oc_gate:
      feats.y_nowcast_oc_gate ??
      scores.y_nowcast_oc_gate ??
      (rules.y_nowcast_oc_gate != null ? rules.y_nowcast_oc_gate : null),
    nowcast_compare_label: feats.nowcast_compare_label ?? scores.nowcast_compare_label ?? null,
    y_nc_enter:
      feats.y_nc_enter ??
      scores.y_nc_enter ??
      (rules.y_nc_enter != null ? rules.y_nc_enter : null),
    y_nc_strong:
      feats.y_nc_strong ??
      scores.y_nc_strong ??
      rules.y_nc_strong ??
      feats.y_nowcast_enter ??
      scores.y_nowcast_enter ??
      (rules.y_nowcast_enter != null ? rules.y_nowcast_enter : null),
    y_nowcast_enter:
      feats.y_nowcast_enter ??
      scores.y_nowcast_enter ??
      rules.y_nowcast_enter ??
      feats.y_nc_strong ??
      scores.y_nc_strong ??
      (rules.y_nc_strong != null ? rules.y_nc_strong : null),
    predicted_score_eod_rem: scores.predicted_score_eod_rem ?? null,
    y_spec_tau: scores.y_spec_tau || null,
    features_tau: scores.features_tau || feats.features_tau || null,
    score_formula_terms: scores.score_formula_terms || scores.formula_terms || null,
    formula_terms: scores.score_formula_terms || scores.formula_terms || null,
    factor_coefficients: scores.factor_coefficients || null,
    formula_terms_tau: scores.formula_terms_tau || scores.score_formula_terms_tau || null,
    score_formula_terms_tau: scores.formula_terms_tau || scores.score_formula_terms_tau || null,
    factor_coefficients_tau: scores.factor_coefficients_tau || null,
    formula_terms_on: scores.formula_terms_on || scores.score_formula_terms_on || null,
    score_formula_terms_on: scores.formula_terms_on || scores.score_formula_terms_on || null,
    cluster_label: scores.cluster_label || "",
    weight_source: scores.weight_source || "",
    direction_reason: d.direction_reason || "",
    direction_score: d.direction_score,
    score_reasons: d.direction_reason ? [String(d.direction_reason)] : [],
    return_model_source:
      scores.return_model_source || scores._score_source || "t0_backtest_compute",
  };
}

/** 表列 y_*：与 holdings_table / watching 同源 resolve。 */
function t0FmtYScore(d, kind, fallback = {}, rules = {}) {
  const it = t0DayScoreItem(d, fallback, rules);
  if (kind === "path") {
    const p = resolvePathScore(it);
    return p != null ? fmtPathScore(p) : "—";
  }
  const resolvers = {
    eod: resolveEodScore,
    tau: resolveTauScore,
    trade: resolveTradeScore,
    on: resolveOnScore,
    nowcast: resolveNowcastCcScore,
  };
  const fn = resolvers[kind];
  return fmtTableScore(it, fn ? fn(it) : null);
}

function resolveNowcastCcValue(d, fallback, rules) {
  const it = t0DayScoreItem(d, fallback, rules);
  const cc = resolveNowcastCcScore(it);
  if (cc != null && Number.isFinite(Number(cc))) return Number(cc);
  return pickScoreNum(d, "y_nowcast");
}

function resolveNowcastOcValue(d, fallback, rules) {
  const it = t0DayScoreItem(d, fallback, rules);
  const feats =
    (d.direction_features && typeof d.direction_features === "object"
      ? d.direction_features
      : null) ||
    (d.features && typeof d.features === "object" ? d.features : {}) ||
    {};
  const cc = resolveNowcastCcValue(d, fallback, rules);
  const gap = it.gap_pct;
  let oc =
    feats.y_nc_oc != null && Number.isFinite(Number(feats.y_nc_oc))
      ? Number(feats.y_nc_oc)
      : feats.y_nowcast_oc != null && Number.isFinite(Number(feats.y_nowcast_oc))
        ? Number(feats.y_nowcast_oc)
        : null;
  if (oc == null && cc != null && gap != null) oc = nowcastOcPct(cc, gap);
  return { oc, cc, gap };
}

function fmtNowcastOcCell(d, fallback, rules, scoreDetailJson) {
  const ocOn = rules.y_nowcast_oc_gate === true;
  const { oc, cc, gap } = resolveNowcastOcValue(d, fallback, rules);
  if (oc == null) {
    const tip =
      gap == null
        ? "缺 gap，无法由 nc 换算 nowcast oc"
        : cc == null
          ? "缺 nc，无法换算 nowcast oc"
          : Y_NC_OC_TITLE;
    return t0YScoreCell("nc_oc", "—", scoreDetailJson, tip);
  }
  const ocText = fmtScore(oc, { digits: 2 });
  const gapText = gap != null ? fmtScore(gap, { digits: 2 }) : "—";
  const ccText = cc != null ? fmtScore(cc, { digits: 2 }) : "—";
  const gateNote = ocOn ? "异号闸用 nowcast oc" : "异号闸用 nc（oc 仅对照）";
  const title = `${Y_NC_OC_TITLE} · ${gateNote} · nc ${ccText} · gap ${gapText}`;
  return t0YScoreCell("nc_oc", ocText, scoreDetailJson, title);
}

function t0YScoreCell(tip, text, detailJson, title) {
  const colKey =
    tip === "nowcast"
      ? "nc"
      : tip === "nc_oc"
        ? "nc-oc"
        : tip;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score has-tip" data-score-tip="${escapeText(
      tip
    )}" data-score-detail="${detailJson}" title="${escapeText(title)}">${escapeText(
      text
    )}</td>`
  );
}

function fmtT0LegTime(v) {
  if (v == null || v === "") return "";
  const s = String(v).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return "";
  const m = s.match(/(\d{1,2}:\d{2})(?::\d{2})?/);
  if (m) return m[1];
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return s.length > 11 ? s.slice(11, 16) : "";
}

function fmtT0LegPrice(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n >= 100 ? n.toFixed(2) : n.toFixed(3);
}

function tradeAtSortKey(at) {
  if (at == null || at === "") return Number.MAX_SAFE_INTEGER;
  const d = new Date(String(at));
  return Number.isNaN(d.getTime()) ? Number.MAX_SAFE_INTEGER : d.getTime();
}

function legSideKind(side) {
  const s = String(side || "").toLowerCase();
  if (s.endsWith("sell")) return "sell";
  if (s.endsWith("buy")) return "buy";
  return "";
}

function normalizeTradeLeg(t) {
  const side = legSideKind(t?.side);
  const qty = Math.round(Number(t?.shares) || 0);
  const price = Number(t?.price);
  const time = fmtT0LegTime(t?.at);
  const note = String(t?.note || "");
  const legKind =
    String(t?.leg_kind || "").trim() ||
    (/收盘|强制/.test(note) ? "eod_cover" : "trigger");
  const eod = legKind === "eod_cover";
  return { side, qty, price, time, eod, legKind, note, at: t?.at };
}

const LEG_KIND_TAG = {
  eod_cover: { label: "收", cls: "is-eod" },
  pm_chase: { label: "追", cls: "is-stop" },
  pm_degrade: { label: "追", cls: "is-stop" }, // 旧账本兼容
};

function legKindTag(legKind) {
  return LEG_KIND_TAG[legKind] || null;
}

function collectLegRecords(d) {
  const trades = (Array.isArray(d?.trades) ? d.trades : [])
    .filter((t) => t && (t.side || t.shares || t.price))
    .slice()
    .sort((a, b) => tradeAtSortKey(a.at) - tradeAtSortKey(b.at));
  if (trades.length) {
    return trades.map(normalizeTradeLeg).filter((l) => l.side && l.qty > 0);
  }
  const legs = tradeLegCells(d);
  const qty = legQtyCells(d);
  const out = [];
  if (Number(qty.sellQty) > 0) {
    out.push({
      side: "sell",
      qty: Number(qty.sellQty),
      price: legs.sellPx,
      time: fmtT0LegTime(legs.sellAt),
      eod: false,
      note: "",
      at: legs.sellAt,
    });
  }
  if (Number(qty.buyQty) > 0) {
    out.push({
      side: "buy",
      qty: Number(qty.buyQty),
      price: legs.buyPx,
      time: fmtT0LegTime(legs.buyAt),
      eod: false,
      note: "",
      at: legs.buyAt,
    });
  }
  return out;
}

function legPlainText(l) {
  const px = fmtT0LegPrice(l.price);
  const side = l.side === "sell" ? "卖" : "买";
  const tag = legKindTag(l.legKind);
  const tagTxt = tag ? `[${tag.label}]` : "";
  if (l.time) return `${l.time}${tagTxt}${side}${l.qty}@${px}`;
  if (l.eod) return `收盘${side}${l.qty}@${px}`;
  return `${tagTxt}${side}${l.qty}@${px}`;
}

function legChipHtml(l, showTimes) {
  const sideLabel = l.side === "sell" ? "卖" : "买";
  const cls = l.side === "sell" ? "is-sell" : "is-buy";
  const tag = legKindTag(l.legKind);
  let timeHtml = "";
  if (showTimes && l.time) {
    timeHtml =
      `<span class="paper-t0-leg-time${tag ? ` ${tag.cls}` : ""}">` +
      `${escapeText(l.time)}` +
      (tag ? `<span class="paper-t0-leg-eod-tag">${escapeText(tag.label)}</span>` : "") +
      `</span>`;
  } else if (showTimes && l.eod) {
    timeHtml = `<span class="paper-t0-leg-time is-eod">收盘</span>`;
  } else if (showTimes && tag) {
    timeHtml =
      `<span class="paper-t0-leg-time ${tag.cls}">` +
      `<span class="paper-t0-leg-eod-tag">${escapeText(tag.label)}</span>` +
      `</span>`;
  }
  const title = l.note ? ` title="${escapeText(l.note)}"` : "";
  return (
    `<span class="paper-t0-leg-chip ${cls}"${title}>` +
    `<span class="paper-t0-leg-side">${sideLabel}</span>` +
    `<span class="paper-t0-leg-body">` +
    (timeHtml ? `${timeHtml}<span class="paper-t0-leg-dot" aria-hidden="true">·</span>` : "") +
    `<span class="paper-t0-leg-qty">${l.qty}</span>` +
    `<span class="paper-t0-leg-at">@</span>` +
    `<span class="paper-t0-leg-px">${escapeText(fmtT0LegPrice(l.price))}</span>` +
    `</span></span>`
  );
}

/** 单日做 T 收益率：(PnL+敞口) / 动仓名义。 */
function dayReturnPct(d) {
  if (d.day_return_pct != null && Number.isFinite(Number(d.day_return_pct))) {
    return Number(d.day_return_pct);
  }
  const net = Number(d.pnl || 0) + Number(d.exposure_pnl || 0);
  const rev = d.direction === "reverse_t";
  const qty = rev
    ? Number(d.bought_qty || d.buy_shares || 0)
    : Number(d.sold_qty || d.sell_shares || 0);
  const legs = tradeLegCells(d);
  const px = rev
    ? Number(legs.buyPx || d.buy_price || 0)
    : Number(legs.sellPx || d.sell_price || 0);
  const notional = qty * px;
  if (!(notional > 0)) return null;
  return (net / notional) * 100;
}

function fmtDayReturnPct(d) {
  const pct = dayReturnPct(d);
  if (pct == null || !Number.isFinite(pct)) {
    return { text: "—", tip: "缺动仓股数或成交价，无法算收益%" };
  }
  const sign = pct > 0 ? "+" : "";
  return {
    text: `${sign}${pct.toFixed(2)}%`,
    tip: `(PnL ${d.pnl ?? 0} + 敞口 ${d.exposure_pnl ?? 0}) / 动仓名义`,
    pct,
  };
}

/** 结构化过程列 HTML（卖/买 chip + 箭头链） */
export function legProcessFlowHtml(d) {
  const legs = collectLegRecords(d);
  if (!legs.length) return `<span class="paper-t0-leg-empty">—</span>`;
  const showTimes = !!d.minute_path;
  const flowCls = showTimes ? "is-minute" : "is-daily";
  const chips = legs
    .map(
      (l, i) =>
        `${i ? `<span class="paper-t0-leg-join" aria-hidden="true"></span>` : ""}${legChipHtml(l, showTimes)}`
    )
    .join("");
  return `<div class="paper-t0-leg-flow ${flowCls}">${chips}</div>`;
}

/** 按 trades 时间序还原完整做 T 链路；无 trades 时回退摘要字段。 */
export function fmtLegProcess(d) {
  const legs = collectLegRecords(d);
  return legs.length ? legs.map(legPlainText).join(" → ") : "—";
}

function legQtyCells(d) {
  const rev = d.direction === "reverse_t";
  if (rev) {
    return {
      sellQty: d.sell_shares ?? d.sold_back_qty ?? 0,
      buyQty: d.buy_shares ?? d.bought_qty ?? 0,
    };
  }
  return {
    sellQty: d.sell_shares ?? d.sold_qty ?? 0,
    buyQty: d.buy_shares ?? d.covered_qty ?? d.bought_qty ?? 0,
  };
}

/** 从 trades 腿 + touch_* / 后端摘要提取卖/买时间与价格。 */
export function tradeLegCells(d) {
  if (d.sell_price != null || d.buy_price != null || d.sell_at || d.buy_at) {
    return {
      sellAt: d.sell_at,
      sellPx: d.sell_price,
      buyAt: d.buy_at,
      buyPx: d.buy_price,
    };
  }
  const trades = Array.isArray(d?.trades) ? d.trades : [];
  const sellLegs = trades.filter((t) => /sell$/i.test(String(t.side || "")));
  const buyLegs = trades.filter((t) => /buy$/i.test(String(t.side || "")));
  const rev = d.direction === "reverse_t";
  if (rev) {
    return {
      sellAt: sellLegs[sellLegs.length - 1]?.at || d.touch_sell_at,
      sellPx: sellLegs[sellLegs.length - 1]?.price,
      buyAt: buyLegs[0]?.at || d.touch_buy_at,
      buyPx: buyLegs[0]?.price,
    };
  }
  return {
    sellAt: sellLegs[0]?.at || d.touch_sell_at,
    sellPx: sellLegs[0]?.price,
    buyAt: buyLegs[buyLegs.length - 1]?.at || d.touch_cover_at || d.touch_buy_at,
    buyPx: buyLegs[buyLegs.length - 1]?.price,
  };
}

export function daysHaveIntradayTime(days) {
  return (days || []).some((d) => !!d.minute_path || collectLegRecords(d).some((l) => !!l.time));
}

function tradeTableColCount(showStock, showReason, showDelete) {
  let n = 13;
  if (showStock) n += 1;
  if (showReason) n += 1;
  if (showDelete) n += 1;
  return n;
}


/** 过程 tip：从 forward_trace 压成迷你 K 线点（[hm,o,h,l,c,flag]）。
 * flag: 1=leg1 · 2=leg2 · 4=前缀 low · 8=前缀 high（可按位或）。
 */
function compactTraceBars(trace, { prefixBars = 0 } = {}) {
  const rows = Array.isArray(trace) ? trace : [];
  const out = [];
  let readyIdx = -1;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const o = Number(r.open);
    const h = Number(r.high);
    const l = Number(r.low);
    const c = Number(r.close);
    if (!(o > 0 && h > 0 && l > 0 && c > 0)) continue;
    let flag = 0;
    if (r.leg1_fill) flag = 1;
    else if (r.leg2_fill) flag = 2;
    if (r.entry_ready || r.leg1_fill) readyIdx = out.length;
    const hm = String(r.time || "").replace(":", "") || String(r.idx ?? out.length);
    out.push([hm, o, h, l, c, flag]);
  }
  if (out.length < 1) return out;
  // 前缀窗：确认根（含）以前；无确认时用 prefixBars / 全长
  let winEnd = readyIdx >= 0 ? readyIdx : out.length - 1;
  const nPref = Number(prefixBars);
  if (!(readyIdx >= 0) && Number.isFinite(nPref) && nPref >= 2) {
    winEnd = Math.min(out.length - 1, Math.max(0, Math.floor(nPref) - 1));
  }
  let loI = 0;
  let hiI = 0;
  let loV = Number(out[0][3]);
  let hiV = Number(out[0][2]);
  for (let i = 1; i <= winEnd; i++) {
    const l = Number(out[i][3]);
    const h = Number(out[i][2]);
    if (l < loV) {
      loV = l;
      loI = i;
    }
    if (h > hiV) {
      hiV = h;
      hiI = i;
    }
  }
  out[loI][5] = Number(out[loI][5] || 0) | 4;
  out[hiI][5] = Number(out[hiI][5] || 0) | 8;
  return out;
}

/** 同步 SVG 迷你 K 线（tip 内用，避免悬停再拉 LW）。 */
export function miniKlineSvgFromBars(
  bars,
  { width = 420, height = 176, dir = "", emptyHtml = "" } = {}
) {
  const pts = Array.isArray(bars) ? bars : [];
  if (pts.length < 2) {
    return (
      emptyHtml || `<p class="paper-t0-process-tip-empty">无 5m OHLC</p>`
    );
  }
  let lo = Infinity;
  let hi = -Infinity;
  for (const b of pts) {
    const l = Number(b[3]);
    const h = Number(b[2]);
    if (l < lo) lo = l;
    if (h > hi) hi = h;
  }
  if (!(hi > lo)) {
    lo *= 0.999;
    hi *= 1.001;
  }
  const padL = 6;
  const padR = 6;
  const padT = 14;
  const padB = 20;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const n = pts.length;
  const slot = plotW / n;
  const bodyW = Math.max(2.5, Math.min(10, slot * 0.58));
  const yScale = (p) => padT + ((hi - p) / (hi - lo)) * plotH;
  const up = "#ef4444";
  const down = "#22c55e";
  // leg1/leg2 相对方向：正T 先买后卖；反T 先卖后买
  const rev = dir === "正T" || dir === "reverse_t";
  const parts = [];
  pts.forEach((b, i) => {
    const o = Number(b[1]);
    const h = Number(b[2]);
    const l = Number(b[3]);
    const c = Number(b[4]);
    const flag = Number(b[5] || 0);
    const x = padL + slot * i + slot / 2;
    const yO = yScale(o);
    const yC = yScale(c);
    const yH = yScale(h);
    const yL = yScale(l);
    const bull = c >= o;
    const color = bull ? up : down;
    const top = Math.min(yO, yC);
    const bot = Math.max(yO, yC);
    const bodyH = Math.max(1.5, bot - top);
    parts.push(
      `<line x1="${x.toFixed(1)}" y1="${yH.toFixed(1)}" x2="${x.toFixed(1)}" y2="${yL.toFixed(1)}" stroke="${color}" stroke-width="1.25"/>` +
        `<rect x="${(x - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}"/>`
    );
    const leg = flag & 3;
    if (leg === 1 || leg === 2) {
      const isBuy = rev ? leg === 1 : leg === 2;
      const cy = isBuy ? yL + 9 : yH - 9;
      const fill = isBuy ? "#2563eb" : "#b45309";
      const label = isBuy ? "买" : "卖";
      parts.push(
        `<circle cx="${x.toFixed(1)}" cy="${cy.toFixed(1)}" r="4" fill="${fill}" stroke="#fff" stroke-width="1"/>` +
          `<text x="${x.toFixed(1)}" y="${(cy + (isBuy ? 12 : -6)).toFixed(1)}" text-anchor="middle" font-size="10" fill="${fill}" font-family="IBM Plex Mono,monospace">${label}</text>`
      );
    }
    if (flag & 4) {
      const ly = leg ? yL + 22 : yL + 11;
      parts.push(
        `<text x="${x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" font-size="9" font-weight="700" fill="#0f766e" font-family="IBM Plex Mono,monospace">L</text>`
      );
    }
    if (flag & 8) {
      const hy = leg ? yH - 16 : yH - 4;
      parts.push(
        `<text x="${x.toFixed(1)}" y="${hy.toFixed(1)}" text-anchor="middle" font-size="9" font-weight="700" fill="#a16207" font-family="IBM Plex Mono,monospace">H</text>`
      );
    }
  });
  const t0 = String(pts[0][0] || "");
  const t1 = String(pts[pts.length - 1][0] || "");
  const fmtHm = (s) => (s.length >= 3 ? `${s.slice(0, -2)}:${s.slice(-2)}` : s);
  parts.push(
    `<text x="${padL}" y="${height - 5}" font-size="10" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(fmtHm(t0))}</text>` +
      `<text x="${width - padR}" y="${height - 5}" text-anchor="end" font-size="10" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(fmtHm(t1))}</text>`
  );
  return (
    `<svg class="paper-t0-process-kline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="5分钟K线">${parts.join("")}</svg>`
  );
}

export function buildProcessTipPayload(d, legTip) {
  const bars = compactTraceBars(d && d.forward_trace, {
    prefixBars: Number(d && d.prefix_bars) || 0,
  });
  const dirRaw = (d && (d.direction || d.direction_used)) || "";
  return {
    text: String(legTip || "").trim(),
    dir: dirRaw === "reverse_t" ? "正T" : dirRaw === "long_t" ? "反T" : "",
    bars,
  };
}

export function buildProcessTipHtml(payload) {
  let p = payload;
  if (typeof p === "string") {
    try {
      p = JSON.parse(p);
    } catch (_) {
      p = { text: p, bars: [] };
    }
  }
  p = p || {};
  const text = String(p.text || "").trim();
  const dir = String(p.dir || "").trim();
  const bars = Array.isArray(p.bars) ? p.bars : [];
  const chart = miniKlineSvgFromBars(bars, { dir });
  return (
    `<div class="paper-t0-process-tip-inner">` +
    `<header class="paper-t0-process-tip-head">` +
    (dir ? `<span class="paper-t0-process-tip-badge">${escapeText(dir)}</span>` : "") +
    `<span class="paper-t0-process-tip-eyebrow">过程 · 5m K</span>` +
    `</header>` +
    (text ? `<p class="paper-t0-process-tip-lead">${escapeText(text)}</p>` : "") +
    `<div class="paper-t0-process-tip-chart">${chart}</div>` +
    `<p class="paper-t0-process-tip-foot">买/卖=成交腿 · L/H=前缀极值 · 数据来自 forward_trace</p>` +
    `</div>`
  );
}

/** 成交明细「过程」列：悬停看链路文案 + 迷你 K 线。 */
export function wireT0ProcessTips(host, tipCtrl) {
  if (!host || !tipCtrl || typeof tipCtrl.bindAttrTip !== "function") return;
  tipCtrl.bindAttrTip(host, {
    selector: "[data-t0-process-tip]",
    wireKey: "t0-process-tip",
    className: "score-tooltip paper-t0-process-tip",
    buildHtml: (el) => {
      try {
        return buildProcessTipHtml(JSON.parse(el.getAttribute("data-t0-process-tip") || "{}"));
      } catch (_) {
        return buildProcessTipHtml({ text: el.getAttribute("title") || "" });
      }
    },
  });
}

function tradeColgroup(showStock, showReason = false, showDelete = false) {
  let html = "<colgroup>";
  if (showStock) html += t0Col("paper-t0-col-stock", "stock");
  html +=
    t0Col("paper-t0-col-date", "date") +
    t0Col("paper-t0-col-eod", "eod") +
    t0Col("paper-t0-col-tau", "tau") +
    t0Col("paper-t0-col-path", "path") +
    t0Col("paper-t0-col-trade", "trade") +
    t0Col("paper-t0-col-on", "on") +
    t0Col("paper-t0-col-nc", "nc") +
    t0Col("paper-t0-col-nc-oc", "ncOc") +
    t0Col("paper-t0-col-dir", "dir") +
    t0Col("paper-t0-col-process", "process") +
    t0Col("paper-t0-col-ret", "retPct") +
    t0Col("paper-t0-col-pnl", "pnl") +
    t0Col("paper-t0-col-exp", "exp");
  if (showReason) html += t0Col("paper-t0-col-reason", "reason");
  if (showDelete) html += t0Col("paper-t0-col-act", "act");
  return `${html}</colgroup>`;
}

export const T0_TRADE_TABLE_MAX_ROWS = 50;

const FS_BTN_OPEN = "全屏";
const FS_BTN_CLOSE = "退出";
let tradesFsBound = false;

function syncTradesFsBodyClass() {
  const open = document.querySelector(".paper-t0-trades-panel.is-fs");
  document.body.classList.toggle("paper-t0-trades-fs-open", !!open);
}

function setTradesFsBtn(panel, on) {
  const btn = panel && panel.querySelector("[data-trades-fs-toggle]");
  if (!btn) return;
  btn.textContent = on ? FS_BTN_CLOSE : FS_BTN_OPEN;
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.title = on ? "退出全屏（Esc）" : "全屏查看成交明细";
}

function exitTradesFs(panel) {
  if (!panel) return;
  panel.classList.remove("is-fs");
  setTradesFsBtn(panel, false);
  syncTradesFsBodyClass();
}

function enterTradesFs(panel) {
  document.querySelectorAll(".paper-t0-trades-panel.is-fs").forEach((el) => {
    if (el !== panel) exitTradesFs(el);
  });
  panel.classList.add("is-fs");
  setTradesFsBtn(panel, true);
  syncTradesFsBodyClass();
}

/** 成交明细全屏：点「全屏」铺满视口，Esc /「退出」收回。 */
export function bindT0TradesFullscreen() {
  if (tradesFsBound || typeof document === "undefined") return;
  tradesFsBound = true;
  document.addEventListener("click", (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest("[data-trades-fs-toggle]");
    if (!btn) return;
    const panel = btn.closest("[data-trades-panel]");
    if (!panel) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (panel.classList.contains("is-fs")) exitTradesFs(panel);
    else enterTradesFs(panel);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const open = document.querySelector(".paper-t0-trades-panel.is-fs");
    if (!open) return;
    ev.preventDefault();
    exitTradesFs(open);
  });
}

function wrapTradesFullscreenPanel(innerHtml, caption) {
  bindT0TradesFullscreen();
  const headMain = caption ? `<div class="paper-t0-trades-fs-head-main">${caption}</div>` : "";
  return (
    `<div class="paper-t0-trades-panel" data-trades-panel>` +
    `<div class="paper-t0-trades-fs-head">` +
    headMain +
    `<button type="button" class="dialog-btn secondary paper-t0-trades-fs-btn" ` +
    `data-trades-fs-toggle aria-pressed="false" title="全屏查看成交明细">${FS_BTN_OPEN}</button>` +
    `</div>` +
    innerHtml +
    `</div>`
  );
}

/**
 * 成交明细表（纸面 / 量化回测共用）
 * @param {{ data?: object, days: object[], caption?: string, maxRows?: number, showReason?: boolean, preserveOrder?: boolean, showDelete?: boolean, showRealized?: boolean }} opts
 */
export function buildT0TradeTableHtml(opts) {
  const {
    data = {},
    days,
    caption = "",
    maxRows = T0_TRADE_TABLE_MAX_ROWS,
    showReason = false,
    preserveOrder = false,
    showDelete = false,
    showRealized = true,
  } = opts || {};
  if (!days || !days.length) return caption || "";
  const showStock = shouldShowStockColumn(data, days);
  const showTime = daysHaveIntradayTime(days);
  const rules = (data && data.rules) || {};
  const enter = yTauEnter(data);
  const tauMap = normalizeYTauMap(rules.y_tau_map);
  const scoreTip = yTauMapScoreTip(tauMap, enter);
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };

  const ncHead = "y_nc";
  const pairHint = showRealized ? " · 显示：预估值(真实值)" : "";
  const head =
    (showStock ? `<th scope="col" class="paper-t0-col-stock">股票</th>` : "") +
    `<th scope="col" class="paper-t0-col-date">日</th>` +
    `<th scope="col" class="paper-t0-col-eod num paper-t0-col-y paper-t0-col-y-eod" title="${escapeText(
      `${Y_EOD_TITLE}${pairHint}`
    )}">y_eod</th>` +
    `<th scope="col" class="paper-t0-col-tau num paper-t0-col-y paper-t0-col-y-tau" title="${escapeText(
      `${scoreTip}${pairHint}`
    )}">y_τ</th>` +
    `<th scope="col" class="paper-t0-col-path num paper-t0-col-y paper-t0-col-y-path" title="${escapeText(
      `${Y_PATH_TITLE}${pairHint}`
    )}">y_path</th>` +
    `<th scope="col" class="paper-t0-col-trade num paper-t0-col-y paper-t0-col-y-trade" title="y_trade 可交易性">y_trade</th>` +
    `<th scope="col" class="paper-t0-col-on num paper-t0-col-y paper-t0-col-y-on" title="${escapeText(
      `${Y_ON_TITLE}`
    )}">y_on</th>` +
    `<th scope="col" class="paper-t0-col-nc num paper-t0-col-y paper-t0-col-y-nc" title="${escapeText(
      Y_NC_TITLE
    )}">${escapeText(ncHead)}</th>` +
    `<th scope="col" class="paper-t0-col-nc-oc num paper-t0-col-y paper-t0-col-y-nc-oc" title="${escapeText(
      Y_NC_OC_TITLE
    )}">y_nc_oc</th>` +
    `<th scope="col" class="paper-t0-col-dir">向</th>` +
    `<th scope="col" class="paper-t0-col-process" title="5m 第一触达时点；缺分钟日已跳过">过程</th>` +
    `<th scope="col" class="paper-t0-col-ret num" title="(PnL+敞口)/动仓名义">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `<th scope="col" class="paper-t0-col-exp num" title="${escapeText(exposureColTitle(data))}">敞口</th>` +
    (showReason ? `<th scope="col" class="paper-t0-col-reason">说明</th>` : "") +
    (showDelete ? `<th scope="col" class="paper-t0-col-act">操作</th>` : "");

  const rows = (preserveOrder ? days.slice(0, maxRows) : days.slice(-maxRows).reverse())
    .map((d) => {
      const skipped = !!d.skipped;
      const dir = d.direction === "reverse_t" ? "正" : d.direction === "long_t" ? "反" : "—";
      const rules = (data && data.rules) || {};
      const scoreDetailJson = escapeText(
        watchingScoreDetail(t0DayScoreItem(d, fallback, rules))
      );
      const legs = tradeLegCells(d);
      const qty = legQtyCells(d);
      const process = fmtLegProcess(d);
      const expCell = fmtExposureCell(d);
      const retCell = fmtDayReturnPct(d);
      const sizingTip = adaptiveSizingDayTip(d, data.rules || {});
      const reason = String(d.reason || d.direction_reason || d.error || "").trim();
      const code = String(d.stock_code || fallback.stock_code || "").trim();
      const name = String(d.stock_name || fallback.stock_name || code).trim();
      const legTip =
        (sizingTip ? `${sizingTip} · ` : "") +
        (skipped && reason
          ? reason
          : process !== "—"
            ? process
            : `卖 ${qty.sellQty}股 @ ${fmtT0LegPrice(legs.sellPx)} · 买 ${qty.buyQty}股 @ ${fmtT0LegPrice(legs.buyPx)}` +
              (showTime ? "" : " · 缺触达时刻"));
      const processTipJson = escapeText(JSON.stringify(buildProcessTipPayload(d, legTip)));
      const delBtn =
        showDelete && code && !skipped
          ? `<button type="button" class="paper-t0-ledger-del" data-code="${escapeText(
              code
            )}" data-name="${escapeText(name)}" title="删除并冲正账本">删除</button>`
          : showDelete
            ? `<span class="paper-t0-leg-empty">—</span>`
            : "";
      return (
        `<tr class="${skipped ? "is-skipped" : ""}"` +
        ` data-code="${escapeText(code)}">` +
        (showStock ? stockCellHtml(d, fallback) : "") +
        `<td class="paper-t0-col-date" title="${escapeText(
          fmtTradeDate(d.date, data.sessionDate || data.session_date)
        )}">` +
        `${escapeText(fmtTradeDate(d.date, data.sessionDate || data.session_date))}</td>` +
        yPctMergedCellHtml(
          "eod",
          d,
          fallback,
          rules,
          scoreDetailJson,
          null,
          showRealized
        ) +
        yPctMergedCellHtml(
          "tau",
          d,
          fallback,
          rules,
          scoreDetailJson,
          d.direction_reason || null,
          showRealized
        ) +
        pathMergedCellHtml(d, fallback, rules, scoreDetailJson, showRealized) +
        t0YScoreCell("trade", t0FmtYScore(d, "trade", fallback, rules), scoreDetailJson, TRADE_TITLE) +
        t0YScoreCell("on", t0FmtYScore(d, "on", fallback, rules), scoreDetailJson, Y_ON_TITLE) +
        t0YScoreCell(
          "nowcast",
          t0FmtYScore(d, "nowcast", fallback, rules),
          scoreDetailJson,
          Y_NC_TITLE
        ) +
        fmtNowcastOcCell(d, fallback, rules, scoreDetailJson) +
        `<td class="paper-t0-col-dir">${dir}</td>` +
        `<td class="paper-t0-col-process has-tip" data-t0-process-tip="${processTipJson}">${
          skipped
            ? `<span class="paper-t0-leg-empty">—</span>`
            : legProcessFlowHtml(d)
        }</td>` +
        `<td class="num paper-t0-col-ret ${paperMetricClass(retCell.pct)}" title="${escapeText(
          retCell.tip
        )}">${escapeText(retCell.text)}</td>` +
        `<td class="num paper-t0-col-pnl ${paperMetricClass(d.pnl)}">${escapeText(String(d.pnl ?? 0))}</td>` +
        `<td class="num paper-t0-col-exp ${paperMetricClass(d.exposure_pnl)}" title="${escapeText(
          expCell.tip
        )}">${escapeText(expCell.text)}</td>` +
        (showReason
          ? `<td class="paper-t0-col-reason" title="${escapeText(reason)}">${escapeText(
              reason || (skipped ? "跳过" : "")
            )}</td>`
          : "") +
        (showDelete ? `<td class="paper-t0-col-act">${delBtn}</td>` : "") +
        `</tr>`
      );
    })
    .join("");

  const moreHint =
    days.length > maxRows
      ? `<p class="quant-trades-caption paper-t0-table-more">表内最近 ${maxRows} 笔 · 样本共 ${days.length} 笔 · 可滚动查看</p>`
      : "";

  const tableBlock =
    `<div class="quant-weight-table-wrap paper-t0-trades-wrap">` +
    `<table class="${TABLE_CLASS}">` +
    tradeColgroup(showStock, showReason, showDelete) +
    `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
    `</div>` +
    moreHint;

  return wrapTradesFullscreenPanel(tableBlock, caption);
}

