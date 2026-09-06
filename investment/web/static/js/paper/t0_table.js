/** 做 T 回测表格：统一列定义，避免表头/数据错位。 */

import {
  escapeText,
  paperMetricClass,
  fmtTableScore,
  resolveTradeScore,
  resolveEodScore,
  resolveTauScore,
  resolveOnScore,
  resolveNowcastScore,
  Y_PATH_TITLE,
  Y_TAU_TITLE,
  PATH_REALIZED_TITLE,
  TAU_REALIZED_TITLE,
  Y_COMPLEXITY_TITLE,
  Y_TPD_TITLE,
  resolvePathScore,
  fmtPathScore,
} from "./fmt.js?v=p1961";

const Y_COMPLEXITY_HAT_KEYS = [
  "predicted_score_complexity",
  "y_complexity_hat",
  "predicted_score_cx",
  "y_cx_hat",
];

const Y_TPD_HAT_KEYS = ["predicted_score_tpd", "y_tpd_hat"];

function writeComplexityHat(obj, val) {
  if (obj == null || val == null || !Number.isFinite(Number(val))) return;
  const n = Number(val);
  obj.predicted_score_complexity = n;
  obj.y_complexity_hat = n;
  obj.predicted_score_cx = n;
  obj.y_cx_hat = n;
}

function writeTpdHat(obj, val) {
  if (obj == null || val == null || !Number.isFinite(Number(val))) return;
  const n = Number(val);
  obj.predicted_score_tpd = n;
  obj.y_tpd_hat = n;
}
import {
  adaptiveSizingDayTip,
  yTauMapScoreTip,
} from "./execution_ui.js?v=p1945";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p1734";

export const SKIP_CAT_LABEL = {
  missing_scores: "缺ŷ快照",
  missing_minute: "缺分钟线",
  y_eod_flat: "y_eod未过门槛",
  y_tau_flat: "y_τ横盘",
  r_tau_flat: "R̂_τ超额不足",
  y_tau_weak: "y_τ弱信号",
  y_path_flat: "y_path横盘",
  y_path_disagree: "y_τ↔y_path异号",
  y_complexity_high: "y_complexity太折",
  y_cx_high: "y_complexity太折",
  y_tpd_high: "y_tpd反转过密",
  gap_tier_skip: "大缺口反向跳过",
  path_abandon: "前缀无空间放弃(旧)",
  prefix_segment: "固定前缀待确认(旧)",
  prefix_vs_path: "前缀>|ŷ_path|×裕度(旧)",
  close_band: "收盘带宽未破带",
  price_space_mismatch: "日分价空间错位",
  tau_entry_price: "入场价vs开盘×ŷ_τ(旧)",
  tau_exit_price: "出场价vs开盘×ŷ_τ",
  y_trade_weak: "y_trade幅度不足",
  eod_tau_disagree: "y_eod↔y_τ异号",
  trade_tau_disagree: "y_trade↔y_τ异号",
  trade_tau_sign: "异号跳过",
  tau_leg1_prior: "局部↔整体趋势不一致",
  conflict: "旧冲突",
  amplitude: "振幅不足(旧)",
  directional_amplitude: "方向振幅(旧)",
  lot_size: "手数不足",
  tplus1: "T+1无可卖",
  path: "路径否决",
  trigger_miss: "未触达",
  other: "其它",
};

/** 跳过类型口径（环形图 / 图例悬停） */
export const SKIP_CAT_TIP = {
  missing_scores:
    "当日缺 ŷ 快照（ŷ_τ / ŷ_trade 等算不出），无法估 ĉ / 开轮。",
  missing_minute:
    "缺当日分钟线，无法模拟触达与成交路径。",
  y_tau_flat:
    "|ŷ_τ| 低于 y_tau_enter（默认 0.01%）视为横盘：即使收价破 ĉ±δ 也不开第一腿。",
  r_tau_flat:
    "|R̂_τ| 低于 r_tau_enter（0–1.0%；0=关）视为超额不足：即使收价破带也不开第一腿。",
  y_eod_flat:
    "历史口径：|ŷ_eod| 低于入场门槛。v6 选腿不经 eod 入场闸；强异号仍可跳过。",
  y_tau_weak:
    "历史跳过类别（旧双闸弱信号区）；现已并入 y_τ 入场，新跑批不再产生。",
  y_path_flat:
    "y_use_path 开时：缺 ŷ_path，或 |ŷ_path| 低于 y_path_enter（默认 0.01%），即使收价破 ĉ±δ 也不开第一腿。",
  y_path_disagree:
    "y_use_path 开时：|ŷ_path| 超过 y_path_strong（默认 0.2%）却与 ŷ_τ 异号则跳过；低于强阈允许异号。",
  y_complexity_high:
    "ŷ_complexity > y_complexity_max（0.00–1.00，默认 1.00≈关）视为太折：即使收价破 ĉ±δ 也不开第一腿。缺 ŷ_complexity 不挡。",
  y_cx_high:
    "ŷ_complexity > y_complexity_max（0.00–1.00，默认 1.00≈关）视为太折：即使收价破 ĉ±δ 也不开第一腿。缺 ŷ_complexity 不挡。",
  y_tpd_high:
    "ŷ_tpd > y_tpd_max（0.00–1.00，默认 0.40；1.00≈关）视为反转过密：即使收价破 ĉ±δ 也不开第一腿。缺 ŷ_tpd 不挡。",
  eod_tau_disagree:
    "历史口径：强 ŷ_eod 与 ŷ_τ 异号跳过。v6 选腿已下线该闸（仅 y_τ / ĉ_τ + path 入场/强）。",
  trade_tau_disagree:
    "历史口径：强 ŷ_trade 与 ŷ_τ 异号跳过。v6 选腿已下线该闸（仅 y_τ / ĉ_τ + path 入场/强）。",
  tau_leg1_prior:
    "局部 r=(p/ĉ−1)% vs 整体 y_τ。score：s=clip(k·y_τ,±α·δ)，upper=δ+s、lower=−δ+s；α∈[0.1,1.0]（默认 0.1）；off=关。旧 skip 硬跳过已下线并入 score。",
  gap_tier_skip:
    "大缺口档位与拟做方向冲突（如大高开仍想正 T），规则直接跳过。",
  path_abandon:
    "历史口径：固定前缀齐窗后仍未确认。v6 已改为收盘带宽选腿，新跑批不应再产生。",
  prefix_segment:
    "历史口径：固定前缀未齐或阴阳占比未达标。v6 已下线，新跑批不应再产生。",
  prefix_vs_path:
    "历史口径：前缀窗 (H−L)/ref% 超过 |ŷ_path|×裕度（空间用尽）。v6 已改为收盘带宽选腿，新跑批不应再产生。",
  close_band:
    "v6：该 5m 收价未破 ĉ±δ 带宽，或缺有效 ĉ 源，本轮不开第一腿。",
  price_space_mismatch:
    "日分价闸：开盘差 |日开/分开−1| 超 t0_price_space_max_dev_pct（默认 5%），或昨收差 |日昨/分昨−1| 超 t0_price_space_prev_dev_pct；错价则跳过。",
  tau_entry_price:
    "历史口径：确认根买/卖价相对 open×(1+ŷ_τ) 的入场价闸。v6 第一腿按确认根收盘成交，该闸已下线；新跑批不应再产生。",
  tau_exit_price:
    "第二腿：正T卖价须 > open×(1+(clamp(ŷ_τ×裕度,min,max)+价偏)/100)；反T买价须 < 同式。止损/收盘强平不受闸。",
  y_trade_weak:
    "|ŷ_trade| 未过入场（y_trade_enter），融合分太弱不开仓。",
  trade_tau_sign:
    "强 trade/nc 与 τ 异号，或历史 τ↔nowcast 异号闸跳过。",
  conflict:
    "旧版 eod↔τ / y_check 冲突闸（已下线），历史回放可能仍出现。",
  amplitude:
    "旧口径：固定前缀高低振幅低于「振幅下限%」。门禁已下线；新跑批不应再出现（历史账本可能残留）。",
  directional_amplitude:
    "旧口径：沿选定方向可用振幅不足；现多并入固定前缀闸。",
  lot_size:
    "按规则算出的买卖量不足 1 手，或现金买不起。",
  tplus1:
    "T+1 锁定：当日无可卖旧仓（反 T 先卖 / 正 T 卖旧仓走不通）。",
  path:
    "分钟路径规则否决（veto），与收盘带宽选腿不同。",
  trigger_miss:
    "已破带开第一腿，但第二腿全天未触达卖出/买回触发价。",
  other:
    "未归入上述类型的其它跳过原因。",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table paper-t0-trades-table";

/** 列轨宽度：colgroup inline width + CSS `.paper-t0-col-*` 同源，避免 fixed 表头/体错位 */
const T0_TRADE_COL_W = {
  stock: "8.75rem",
  date: "104px",
  eod: "82px",
  tau: "112px",
  path: "112px",
  cx: "108px",
  tpd: "108px",
  trade: "108px",
  on: "82px",
  nc: "108px",
  o: "62px",
  l: "62px",
  h: "62px",
  c: "62px",
  ctau: "76px",
  rtau: "80px",
  process: "340px",
  retPct: "80px",
  pnl: "84px",
  exp: "76px",
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

function fmtTradeDateCell(d, fallback) {
  const day = fmtTradeDate(d?.date, fallback);
  const hm = String(d?.t0_slot_hm || "").trim();
  if (day && hm && d?.t0_slot_focus) {
    const md = day.length >= 10 ? day.slice(5) : day;
    return `${md} ${hm}`;
  }
  return day;
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

function firstFilledSlotRow(d) {
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  if (!d?.t0_slots_enabled || !rows.length) return null;
  for (const r of rows) {
    if (!r || r.skipped) continue;
    const filled =
      Number(r.sold_qty || 0) > 0 ||
      Number(r.bought_qty || 0) > 0 ||
      Number(r.trades || 0) > 0;
    if (filled) return r;
  }
  return null;
}

function focusedSlotRow(d) {
  const id = String(d?.t0_slot || "").trim();
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  if (id && rows.length) {
    const hit = rows.find((r) => r && String(r.id || "") === id);
    if (hit) return hit;
  }
  return firstFilledSlotRow(d);
}

function slotClock(r, rows) {
  if (r && r.hm) return String(r.hm);
  const i = (rows || []).indexOf(r);
  const defaults = ["11:00"];
  if (i >= 0 && i < defaults.length) return defaults[i];
  return "";
}

function slotFilled(r) {
  if (!r || r.skipped) return false;
  return (
    Number(r.sold_qty || 0) > 0 ||
    Number(r.bought_qty || 0) > 0 ||
    Number(r.trades || 0) > 0
  );
}

/** 同日各轮拆成独立日对象（测试/导出用）；成交表明细用 rowspan，不拆行。 */
function expandSlotTradeDays(days, { splitSlots = true } = {}) {
  if (!splitSlots) return days || [];
  const out = [];
  for (const d of days || []) {
    const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
    if (!rows.length) {
      out.push(d);
      continue;
    }
    const filled = rows.filter(slotFilled);
    if (!filled.length) {
      out.push(d);
      continue;
    }
    for (const r of filled) {
      out.push(slotDayRow(d, r, rows));
    }
  }
  return sortSlotTradeDays(out);
}

function sortSlotTradeDays(days) {
  return (days || []).slice().sort((a, b) => {
    const c = String(a.stock_code || "").localeCompare(String(b.stock_code || ""));
    if (c) return c;
    const dd = String(b.date || "").localeCompare(String(a.date || ""));
    if (dd) return dd;
    return String(a.t0_slot_hm || a.t0_slot || "").localeCompare(
      String(b.t0_slot_hm || b.t0_slot || "")
    );
  });
}

function slotYNum(obj, keys) {
  for (const k of keys || []) {
    if (obj && obj[k] != null && obj[k] !== "" && Number.isFinite(Number(obj[k]))) {
      return Number(obj[k]);
    }
  }
  return null;
}

/** 该根前缀 y_τ：与 Ĉ 同源；旧包可从 ĉ/日开反推。 */
function slotGateYTau(r, dayHost) {
  const cb = slotCloseBand(r);
  if (!cb) return null;
  const stored = slotYNum(cb, ["y_tau"]);
  if (stored != null) return stored;
  const cDaily = slotYNum(cb, [
    "c_tau_daily",
    "close_px_daily",
    "c_tau",
    "close_px",
  ]);
  const oDay = Number(dayHost && dayHost.open);
  if (cDaily != null && Number.isFinite(oDay) && oDay > 0) {
    return (cDaily / oDay - 1) * 100;
  }
  return null;
}

/** 本轮 ŷ_τ / ŷ_path / ŷ_complexity / ŷ_tpd / ŷ_trade / ŷ_nowcast：只读槽位快照，不回退日级分。 */
function slotYhat(r, dayHost) {
  const sc = r && r.scores && typeof r.scores === "object" ? r.scores : {};
  const ft =
    r && r.direction_features && typeof r.direction_features === "object"
      ? r.direction_features
      : {};
  const gateYt = slotGateYTau(r, dayHost);
  let yTau =
    slotYNum(sc, ["y_tau", "y_tau_oc", "predicted_score_tau", "score_rem"]) ??
    slotYNum(ft, ["y_tau", "y_tau_oc"]) ??
    gateYt;
  let yPath =
    slotYNum(sc, ["y_path", "predicted_score_path"]) ?? slotYNum(ft, ["y_path"]);
  let yCx =
    slotYNum(sc, Y_COMPLEXITY_HAT_KEYS) ??
    slotYNum(ft, Y_COMPLEXITY_HAT_KEYS);
  let yTpd = slotYNum(sc, Y_TPD_HAT_KEYS) ?? slotYNum(ft, Y_TPD_HAT_KEYS);
  let yTrade =
    slotYNum(sc, ["y_trade", "predicted_score_blend", "decision_score"]) ??
    slotYNum(ft, ["y_trade"]);
  let yNowcast =
    slotYNum(sc, ["y_nowcast", "y_nc", "predicted_score_nowcast"]) ??
    slotYNum(ft, ["y_nowcast", "y_nc"]);
  const reason = String((r && r.reason) || "");
  if (yTau == null) {
    const m = reason.match(/y_τ\s*=\s*(-?[\d.]+)/);
    if (m) yTau = Number(m[1]);
  }
  if (yCx == null) {
    const m = reason.match(/y_complexity\s*=\s*(-?[\d.]+)/) || reason.match(/y_cx\s*=\s*(-?[\d.]+)/);
    if (m) yCx = Number(m[1]);
  }
  if (yTpd == null) {
    const m = reason.match(/y_tpd\s*=\s*(-?[\d.]+)/);
    if (m) yTpd = Number(m[1]);
  }
  if (yTrade == null) {
    const m =
      reason.match(/\|y_trade\|\s*=\s*(-?[\d.]+)/) ||
      reason.match(/y_trade\s*=\s*(-?[\d.]+)/);
    if (m) yTrade = Number(m[1]);
  }
  return {
    y_tau: yTau,
    y_path: yPath,
    y_complexity: yCx,
    y_cx: yCx,
    y_tpd: yTpd,
    y_trade: yTrade,
    y_nowcast: yNowcast,
  };
}

function slotDayRow(d, r, rows) {
  const sid = String(r.id || "");
  const filled = slotFilled(r);
  const hm = slotClock(r, rows);
  const slotTrades = (Array.isArray(d.trades) ? d.trades : []).filter(
    (t) => t && String(t.t0_slot || "") === sid
  );
  const yhat = slotYhat(r, d);
  const slotScores = r.scores && typeof r.scores === "object" ? { ...r.scores } : {};
  const slotFeats =
    r.direction_features && typeof r.direction_features === "object"
      ? { ...r.direction_features }
      : {};
  // 槽位 scores 优先；缺字段时用 close_band.y_tau（与 Ĉ 同源）
  if (yhat.y_tau != null) {
    if (slotScores.y_tau == null) slotScores.y_tau = yhat.y_tau;
    if (slotScores.y_tau_oc == null) slotScores.y_tau_oc = yhat.y_tau;
  }
  if (yhat.y_path != null && slotScores.y_path == null) slotScores.y_path = yhat.y_path;
  if (yhat.y_complexity != null || yhat.y_cx != null) {
    const n = yhat.y_complexity ?? yhat.y_cx;
    if (slotScores.predicted_score_complexity == null) slotScores.predicted_score_complexity = n;
    if (slotScores.y_complexity_hat == null) slotScores.y_complexity_hat = n;
    if (slotScores.predicted_score_cx == null) slotScores.predicted_score_cx = n;
    if (slotScores.y_cx_hat == null) slotScores.y_cx_hat = n;
  }
  if (yhat.y_tpd != null) {
    if (slotScores.predicted_score_tpd == null) slotScores.predicted_score_tpd = yhat.y_tpd;
    if (slotScores.y_tpd_hat == null) slotScores.y_tpd_hat = yhat.y_tpd;
  }
  if (yhat.y_trade != null && slotScores.y_trade == null) slotScores.y_trade = yhat.y_trade;
  if (yhat.y_nowcast != null) {
    if (slotScores.y_nowcast == null) slotScores.y_nowcast = yhat.y_nowcast;
    if (slotScores.predicted_score_nowcast == null) {
      slotScores.predicted_score_nowcast = yhat.y_nowcast;
    }
  }
  return {
    ...d,
    t0_slot: sid,
    t0_slot_hm: hm,
    t0_slot_focus: true,
    t0_slot_skipped: !filled,
    skipped: false,
    reason: r.reason || "",
    direction: r.direction || (filled ? d.direction : null),
    pnl: filled ? r.pnl : 0,
    exposure_pnl: filled ? r.exposure_pnl : 0,
    sold_qty: filled ? r.sold_qty : 0,
    covered_qty: filled ? r.covered_qty : 0,
    uncovered_qty: filled ? r.uncovered_qty : 0,
    bought_qty: filled ? r.bought_qty : 0,
    sold_back_qty: filled ? r.sold_back_qty : 0,
    exit_reason: filled ? r.exit_reason : null,
    y_tau: yhat.y_tau,
    y_path: yhat.y_path,
    predicted_score_complexity: yhat.y_complexity ?? yhat.y_cx,
    y_complexity_hat: yhat.y_complexity ?? yhat.y_cx,
    predicted_score_cx: yhat.y_complexity ?? yhat.y_cx,
    y_cx_hat: yhat.y_complexity ?? yhat.y_cx,
    predicted_score_tpd: yhat.y_tpd,
    y_tpd_hat: yhat.y_tpd,
    y_trade: yhat.y_trade,
    y_nowcast: yhat.y_nowcast,
    scores: slotScores,
    direction_features: slotFeats,
    trades: filled ? (slotTrades.length ? slotTrades : d.trades) : [],
    // 过程 tip K 线只标本轮买卖（全日 forward_trace 含各轮 fill）
    forward_trace: remaskTraceFills(
      d.forward_trace,
      filled ? (slotTrades.length ? slotTrades : d.trades) : []
    ),
    prefix_bars:
      r.prefix_bars != null
        ? Number(r.prefix_bars) || 0
        : slotPrefixBarsFromHm(hm, d.prefix_bars),
    sell_price: undefined,
    buy_price: undefined,
    sell_at: undefined,
    buy_at: undefined,
    sell_shares: undefined,
    buy_shares: undefined,
    day_return_pct: null,
    direction_score: yhat.y_tau,
  };
}

/** 默认槽位时钟 → 前缀根数（与 core/t0/config.DEFAULT_T0_SLOTS 对齐）。 */
function slotPrefixBarsFromHm(hm, fallback) {
  const map = {
    "09:30": 0,
    "10:00": 6,
    "10:30": 12,
    "11:00": 18,
    "11:30": 24,
    "13:00": 25,
    "14:00": 37,
  };
  const key = String(hm || "").trim().slice(0, 5);
  if (key && Object.prototype.hasOwnProperty.call(map, key)) return map[key];
  const n = Number(fallback);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

/** 用本轮 trades 重打 buy/sell 标记；清掉其它轮的 leg/fill 旗。 */
function remaskTraceFills(trace, trades) {
  const rows = (Array.isArray(trace) ? trace : []).map((r) => {
    if (!r || typeof r !== "object") return r;
    return {
      ...r,
      buy_fill: false,
      sell_fill: false,
      leg1_fill: false,
      leg2_fill: false,
    };
  });
  if (!rows.length) return rows;
  for (const t of trades || []) {
    if (!t || typeof t !== "object") continue;
    const at = String(t.at || "");
    if (!at) continue;
    const side = String(t.side || "").toLowerCase();
    const isBuy = side.endsWith("buy");
    const isSell = side.endsWith("sell");
    if (!isBuy && !isSell) continue;
    const hm = at.length >= 16 ? at.slice(11, 16) : "";
    for (const row of rows) {
      const dt = String(row.datetime || "");
      const tm = String(row.time || "");
      if ((at && at === dt) || (hm && hm === tm)) {
        if (isBuy) row.buy_fill = true;
        if (isSell) row.sell_fill = true;
        break;
      }
    }
  }
  return rows;
}

function scoreLookupHost(d) {
  const slot = focusedSlotRow(d);
  if (!slot) return d;
  if (d?.t0_slot_focus) {
    return {
      ...d,
      scores: slot.scores && typeof slot.scores === "object" ? slot.scores : {},
      direction_features:
        slot.direction_features && typeof slot.direction_features === "object"
          ? slot.direction_features
          : {},
    };
  }
  return {
    ...d,
    scores: { ...(d.scores || {}), ...(slot.scores || {}) },
    direction_features: {
      ...(d.direction_features || d.features || {}),
      ...(slot.direction_features || {}),
    },
  };
}

function pickScore(d, key) {
  const n = pickScoreNum(d, key);
  return n != null ? `${n.toFixed(2)}%` : "—";
}

function pickScoreNum(d, key) {
  if (
    d?.t0_slot_focus &&
    (key === "y_tau" ||
      key === "y_path" ||
      key === "y_trade" ||
      key === "y_nowcast" ||
      key === "predicted_score_complexity" ||
      key === "y_complexity_hat" ||
      key === "predicted_score_cx" ||
      key === "y_cx_hat" ||
      key === "predicted_score_tpd" ||
      key === "y_tpd_hat")
  ) {
    const n = Number(d[key]);
    return d[key] != null && d[key] !== "" && Number.isFinite(n) ? n : null;
  }
  const host = scoreLookupHost(d);
  const feats = host.direction_features || host.features || {};
  const scores = host.scores || {};
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


/** 回测：预估值(真实值)；实时做 T 无收盘真实 label，只显示预估值。 */
function fmtPredRealizedText(predTxt, pr, showRealized) {
  if (!showRealized || pr == null || pr.n == null) return predTxt;
  return `${predTxt}(${pr.text})`;
}

/** 单段数字：按自身符号上色（A 股红涨绿跌）。 */
function fmtSignedNumHtml(text, value) {
  const cls = paperMetricClass(value);
  return `<span class="paper-t0-y-num${cls ? ` ${cls}` : ""}">${escapeText(
    text == null || text === "" ? "—" : String(text)
  )}</span>`;
}

/** 预估与真实 label 各自独立上色：ŷ(label)。 */
function fmtPredRealizedHtml(predTxt, predVal, pr, showRealized) {
  const predSpan = fmtSignedNumHtml(predTxt, predVal);
  if (!showRealized || pr == null || pr.n == null) return predSpan;
  return (
    predSpan +
    `<span class="paper-t0-y-sep">(</span>` +
    fmtSignedNumHtml(pr.text, pr.n) +
    `<span class="paper-t0-y-sep">)</span>`
  );
}

function predRealizedAgreeCls(pr, showRealized) {
  if (!showRealized || !pr) return "";
  if (pr.agree === true) return " is-path-hit";
  if (pr.agree === false) return " is-path-miss";
  return "";
}

function cxAsUnit01(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  const u = n > 1.5 ? n / 100 : n;
  if (u < 0) return 0;
  if (u > 1) return 1;
  return u;
}

function fmtCxScore(v) {
  const n = cxAsUnit01(v);
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

/** 全日 y_complexity：0–1 按%显示；无正负、不上红绿；≥0.70 标高折。 */
function pickCxRealized(d) {
  const raw =
    pickScoreNum(d, "complexity_realized") ??
    pickScoreNum(d, "y_complexity") ??
    pickScoreNum(d, "cx_realized") ??
    (d?.complexity_realized != null && Number.isFinite(Number(d.complexity_realized))
      ? Number(d.complexity_realized)
      : null) ??
    (d?.y_complexity != null && Number.isFinite(Number(d.y_complexity))
      ? Number(d.y_complexity)
      : null) ??
    (d?.cx_realized != null && Number.isFinite(Number(d.cx_realized))
      ? Number(d.cx_realized)
      : null) ??
    (d?.y_cx != null && Number.isFinite(Number(d.y_cx)) ? Number(d.y_cx) : null);
  const n = cxAsUnit01(raw);
  if (n == null) return { text: "—", n: null, tip: Y_COMPLEXITY_TITLE, high: false };
  const reason = String(
    (d.direction_features &&
      (d.direction_features.complexity_realized_reason ||
        d.direction_features.cx_realized_reason)) ||
      (d.scores &&
        (d.scores.complexity_realized_reason || d.scores.cx_realized_reason)) ||
      d.complexity_realized_reason ||
      d.cx_realized_reason ||
      ""
  ).trim();
  const effRaw = d.cx_efficiency ?? (d.scores && d.scores.cx_efficiency);
  const effN = Number(effRaw);
  const extra = [];
  if (reason && reason !== "ok") extra.push(reason);
  if (Number.isFinite(effN)) extra.push(`ER=${effN.toFixed(2)}`);
  return {
    text: fmtCxScore(n),
    n,
    tip: extra.length ? `${Y_COMPLEXITY_TITLE} · ${extra.join(" · ")}` : Y_COMPLEXITY_TITLE,
    high: n >= 0.7,
  };
}

function pickCxPred(d) {
  const raw =
    pickScoreNum(d, "predicted_score_complexity") ??
    pickScoreNum(d, "y_complexity_hat") ??
    pickScoreNum(d, "predicted_score_cx") ??
    pickScoreNum(d, "y_cx_hat") ??
    (d?.predicted_score_complexity != null && Number.isFinite(Number(d.predicted_score_complexity))
      ? Number(d.predicted_score_complexity)
      : null) ??
    (d?.y_complexity_hat != null && Number.isFinite(Number(d.y_complexity_hat))
      ? Number(d.y_complexity_hat)
      : null) ??
    (d?.predicted_score_cx != null && Number.isFinite(Number(d.predicted_score_cx))
      ? Number(d.predicted_score_cx)
      : null) ??
    (d?.y_cx_hat != null && Number.isFinite(Number(d.y_cx_hat)) ? Number(d.y_cx_hat) : null);
  return cxAsUnit01(raw);
}

/** 槽位 ŷ_complexity；回测配对全日 label。无 rowspan。 */
function cxMergedCellHtml(host, dayRef, showRealized = true) {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pr = showRealized
    ? pickCxRealized(realHost)
    : { text: "—", n: null, tip: Y_COMPLEXITY_TITLE, high: false };
  const pred = pickCxPred(predHost);
  const highCls = (pred != null && pred >= 0.7) || pr.high ? " is-cx-high" : "";
  const predTxt = pred != null ? fmtCxScore(pred) : "—";
  const shown =
    showRealized && pred != null && pr.n != null
      ? `${predTxt}(${pr.text})`
      : showRealized && pr.n != null
        ? pr.text
        : predTxt;
  const tip = pred != null ? `${pr.tip} · ŷ_complexity=${pred.toFixed(3)}` : pr.tip;
  return (
    `<td class="num paper-t0-col-cx paper-t0-col-y paper-t0-col-y-cx has-tip${highCls}" ` +
    `title="${escapeText(tip)}">${escapeText(shown)}</td>`
  );
}

function pickTpdRealized(d) {
  const raw =
    pickScoreNum(d, "tpd_realized") ??
    pickScoreNum(d, "y_tpd") ??
    pickScoreNum(d, "y_complexity_tpd") ??
    (d?.tpd_realized != null && Number.isFinite(Number(d.tpd_realized))
      ? Number(d.tpd_realized)
      : null) ??
    (d?.y_tpd != null && Number.isFinite(Number(d.y_tpd)) ? Number(d.y_tpd) : null) ??
    (d?.y_complexity_tpd != null && Number.isFinite(Number(d.y_complexity_tpd))
      ? Number(d.y_complexity_tpd)
      : null);
  const n = cxAsUnit01(raw);
  if (n == null) return { text: "—", n: null, tip: Y_TPD_TITLE, high: false };
  return {
    text: fmtCxScore(n),
    n,
    tip: Y_TPD_TITLE,
    high: n >= 0.7,
  };
}

function pickTpdPred(d) {
  const raw =
    pickScoreNum(d, "predicted_score_tpd") ??
    pickScoreNum(d, "y_tpd_hat") ??
    (d?.predicted_score_tpd != null && Number.isFinite(Number(d.predicted_score_tpd))
      ? Number(d.predicted_score_tpd)
      : null) ??
    (d?.y_tpd_hat != null && Number.isFinite(Number(d.y_tpd_hat)) ? Number(d.y_tpd_hat) : null);
  return cxAsUnit01(raw);
}

/** 槽位 ŷ_tpd；回测配对全日 label。无 rowspan。 */
function tpdMergedCellHtml(host, dayRef, showRealized = true) {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pr = showRealized
    ? pickTpdRealized(realHost)
    : { text: "—", n: null, tip: Y_TPD_TITLE, high: false };
  const pred = pickTpdPred(predHost);
  const highCls = (pred != null && pred >= 0.7) || pr.high ? " is-tpd-high" : "";
  const predTxt = pred != null ? fmtCxScore(pred) : "—";
  const shown =
    showRealized && pred != null && pr.n != null
      ? `${predTxt}(${pr.text})`
      : showRealized && pr.n != null
        ? pr.text
        : predTxt;
  const tip = pred != null ? `${pr.tip} · ŷ_tpd=${pred.toFixed(3)}` : pr.tip;
  return (
    `<td class="num paper-t0-col-tpd paper-t0-col-y paper-t0-col-y-tpd has-tip${highCls}" ` +
    `title="${escapeText(tip)}">${escapeText(shown)}</td>`
  );
}

/** y_path：回测配对真实值；实时做 T 只显示 ŷ。 */
function pathMergedCellHtml(d, fallback, rules, scoreDetailJson, showRealized = true, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const pred = resolvePathScore(it);
  const predTxt = pred != null ? fmtPathScore(pred) : "—";
  const pr = showRealized ? pickPathRealized(d) : { n: null, tip: PATH_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? Y_PATH_TITLE : "暂无 ŷ_path"];
  if (it._scores_live_overlay) tipParts.push("缺快照·已用持仓分补洞");
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : PATH_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized);
  return (
    `<td class="num paper-t0-col-path paper-t0-col-y paper-t0-col-y-path paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="path" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

/** y_τ：回测配对真实值；实时做 T 只显示 ŷ。 */
function yPctMergedCellHtml(kind, d, fallback, rules, scoreDetailJson, titleExtra, showRealized = true, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const pred = resolveTauScore(it);
  let predNum = pred;
  let predTxt = pred != null ? fmtTableScore(it, pred) : "—";
  if (predTxt === "—" && !d?.t0_slot_focus) {
    const ds = d.direction_score;
    if (ds != null && ds !== "" && Number.isFinite(Number(ds))) {
      predNum = Number(ds);
      predTxt = fmtTableScore(it, predNum);
    }
  }
  const pr = showRealized
    ? pickTauRealized(d)
    : { n: null, tip: TAU_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [
    predNum != null || predTxt !== "—" ? Y_TAU_TITLE : "暂无 ŷ_τ",
  ];
  if (d?.t0_slot_focus) {
    const slot = focusedSlotRow(d);
    const cb = slotCloseBand(slot) || (d.close_band && typeof d.close_band === "object" ? d.close_band : null);
    if (cb && cb.c_hat_score_source === "bar_prefix") {
      tipParts.push("Ĉ 与该根前缀 ŷ_τ 同源；C=本根5m收价");
    } else if (cb && cb.c_hat_score_source === "open_anchor") {
      tipParts.push("Ĉ 用开盘 OC ŷ_τ（旧包）；请重跑回测");
    } else if (cb && cb.c_hat_score_source === "bar_prefix_live") {
      tipParts.push("该根前缀 ŷ_τ 估 Ĉ（与表列同源）");
    }
  }
  if (it._scores_live_overlay) tipParts.push("缺快照·已用持仓分补洞");
  if (showRealized) tipParts.push(pr.n != null ? pr.tip : TAU_REALIZED_TITLE);
  if (titleExtra) tipParts.push(String(titleExtra));
  if (showRealized) {
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, predNum, pr, showRealized);
  const colKey = kind === "tau" ? "tau" : kind;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="tau" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +

    `</td>`
  );
}

/** 成交日 → tip + resolve 载荷。
 * ``liveByCode``：仅补洞（日快照缺字段时）；**绝不覆盖**已有决策分。
 * 回测/预演多日明细若盖持仓实时分，会出现「多日 ŷ 相同、负τ却正T」。
 */
function scanRowForHm(d, hm) {
  const scan = Array.isArray(d?.close_band_scan) ? d.close_band_scan : [];
  const key = String(hm || "").trim().slice(0, 5);
  if (!key || !scan.length) return null;
  return (
    scan.find((r) => r && String(r.hm || "").trim().slice(0, 5) === key) || null
  );
}

/** 成交主表 ŷ 载荷：与展开扫描触发根同源，禁止持仓 live 盖决策快照。 */
function tradeScoreHost(day, host) {
  if (!host || typeof host !== "object") return host;
  if (host.t0_slot_focus) return host;
  const slot = focusedSlotRow(day);
  let hm = String(host.t0_slot_hm || "").trim().slice(0, 5);
  if (!hm && slot) hm = String(slotClock(slot, day?.t0_slot_results) || "").slice(0, 5);
  const scanRow = scanRowForHm(day, hm);
  if (!scanRow) return host;
  const yTau = scanRow.y_tau != null ? Number(scanRow.y_tau) : null;
  const yPath = scanRow.y_path != null ? Number(scanRow.y_path) : null;
  const yCx =
    scanRow.y_complexity != null
      ? Number(scanRow.y_complexity)
      : scanRow.y_cx != null
        ? Number(scanRow.y_cx)
        : scanRow.predicted_score_complexity != null
          ? Number(scanRow.predicted_score_complexity)
          : scanRow.predicted_score_cx != null
            ? Number(scanRow.predicted_score_cx)
            : null;
  const yTpd =
    scanRow.predicted_score_tpd != null
      ? Number(scanRow.predicted_score_tpd)
      : scanRow.y_tpd_hat != null
        ? Number(scanRow.y_tpd_hat)
        : scanRow.y_tpd != null
          ? Number(scanRow.y_tpd)
          : null;
  const scores = { ...(host.scores && typeof host.scores === "object" ? host.scores : {}) };
  if (yTau != null && Number.isFinite(yTau)) {
    scores.y_tau = yTau;
    scores.y_tau_oc = yTau;
    scores.predicted_score_tau_oc = yTau;
    scores.predicted_score_tau = yTau;
    scores.score_rem = yTau;
  }
  if (yPath != null && Number.isFinite(yPath)) {
    scores.y_path = yPath;
    scores.predicted_score_path = yPath;
  }
  if (yCx != null && Number.isFinite(yCx)) {
    writeComplexityHat(scores, yCx);
  }
  if (yTpd != null && Number.isFinite(yTpd)) {
    writeTpdHat(scores, yTpd);
  }
  return {
    ...host,
    y_tau: yTau != null && Number.isFinite(yTau) ? yTau : host.y_tau,
    y_path: yPath != null && Number.isFinite(yPath) ? yPath : host.y_path,
    predicted_score_complexity:
      yCx != null && Number.isFinite(yCx) ? yCx : host.predicted_score_complexity,
    y_complexity_hat: yCx != null && Number.isFinite(yCx) ? yCx : host.y_complexity_hat,
    predicted_score_cx: yCx != null && Number.isFinite(yCx) ? yCx : host.predicted_score_cx,
    y_cx_hat: yCx != null && Number.isFinite(yCx) ? yCx : host.y_cx_hat,
    predicted_score_tpd: yTpd != null && Number.isFinite(yTpd) ? yTpd : host.predicted_score_tpd,
    y_tpd_hat: yTpd != null && Number.isFinite(yTpd) ? yTpd : host.y_tpd_hat,
    direction_score:
      yTau != null && Number.isFinite(yTau) ? yTau : host.direction_score,
    scores,
    _scan_score_row: scanRow,
  };
}

function t0DayScoreItem(d, fallback = {}, rules = {}, liveByCode = null) {
  const slot = focusedSlotRow(d);
  const slotSnapMode = !!d?.t0_slot_focus || (!!slot && slotFilled(slot));
  const scores = slotSnapMode
    ? { ...((slot && slot.scores && typeof slot.scores === "object" ? slot.scores : {}) || {}) }
    : {
        ...((d.scores && typeof d.scores === "object" ? d.scores : {}) || {}),
        ...((slot && slot.scores && typeof slot.scores === "object" ? slot.scores : {}) || {}),
      };
  const feats = slotSnapMode
    ? {
        ...((slot && slot.direction_features && typeof slot.direction_features === "object"
          ? slot.direction_features
          : {}) || {}),
      }
    : {
        ...((d.direction_features && typeof d.direction_features === "object"
          ? d.direction_features
          : d.features && typeof d.features === "object"
            ? d.features
            : {}) || {}),
        ...((slot && slot.direction_features && typeof slot.direction_features === "object"
          ? slot.direction_features
          : {}) || {}),
      };
  let yEod = pickScoreNum(d, "y_eod");
  let yTau = pickScoreNum(d, "y_tau");
  let yTrade = pickScoreNum(d, "y_trade");
  let yOn = pickScoreNum(d, "y_on");
  let yNc = pickScoreNum(d, "y_nowcast");
  let yPath = pickScoreNum(d, "y_path");
  let yCx =
    pickScoreNum(d, "predicted_score_complexity") ??
    pickScoreNum(d, "y_complexity_hat") ??
    pickScoreNum(d, "predicted_score_cx") ??
    pickScoreNum(d, "y_cx_hat");
  let yTpd = pickScoreNum(d, "predicted_score_tpd") ?? pickScoreNum(d, "y_tpd_hat");
  if (slotSnapMode && slot) {
    const yhat = slotYhat(slot, d);
    if (yhat.y_tau != null) yTau = yhat.y_tau;
    if (yhat.y_path != null) yPath = yhat.y_path;
    if (yhat.y_complexity != null) yCx = yhat.y_complexity;
    else if (yhat.y_cx != null) yCx = yhat.y_cx;
    if (yhat.y_tpd != null) yTpd = yhat.y_tpd;
    if (yhat.y_trade != null) yTrade = yhat.y_trade;
  }
  const scanHost = d?._scan_score_row ? d : null;
  if (scanHost?._scan_score_row) {
    const sr = scanHost._scan_score_row;
    if (sr.y_tau != null && Number.isFinite(Number(sr.y_tau))) yTau = Number(sr.y_tau);
    if (sr.y_path != null && Number.isFinite(Number(sr.y_path))) yPath = Number(sr.y_path);
    const srCx = sr.y_complexity ?? sr.y_cx;
    if (srCx != null && Number.isFinite(Number(srCx))) {
      writeComplexityHat(scores, Number(srCx));
    }
    const srTpd = sr.predicted_score_tpd ?? sr.y_tpd_hat ?? sr.y_tpd;
    if (srTpd != null && Number.isFinite(Number(srTpd))) {
      writeTpdHat(scores, Number(srTpd));
      yTpd = Number(srTpd);
    }
  }
  const code = String(
    d.stock_code || fallback.stock_code || scores.stock_code || ""
  ).trim();
  const live =
    liveByCode && code && typeof liveByCode[code] === "object"
      ? liveByCode[code]
      : null;
  const liveEod = slotSnapMode ? null : live ? resolveEodScore(live) : null;
  const liveTau = slotSnapMode ? null : live ? resolveTauScore(live) : null;
  const liveTrade = slotSnapMode ? null : live ? resolveTradeScore(live) : null;
  const liveOn = slotSnapMode ? null : live ? resolveOnScore(live) : null;
  const liveNc = slotSnapMode ? null : live ? resolveNowcastScore(live) : null;
  const livePath = slotSnapMode ? null : live ? resolvePathScore(live) : null;
  const dayEod = scores.predicted_score_eod ?? yEod;
  const dayTau = scores.predicted_score_tau ?? scores.score_rem ?? yTau;
  const ftTau =
    (scores.formula_terms_tau && typeof scores.formula_terms_tau === "object"
      ? scores.formula_terms_tau
      : null) ||
    (scores.score_formula_terms_tau && typeof scores.score_formula_terms_tau === "object"
      ? scores.score_formula_terms_tau
      : null) ||
    (feats.formula_terms_tau && typeof feats.formula_terms_tau === "object"
      ? feats.formula_terms_tau
      : null);
  const dayTauOc =
    scores.y_tau_oc ??
    scores.predicted_score_tau_oc ??
    (ftTau && ftTau.y_tau_raw != null ? ftTau.y_tau_raw : null) ??
    (ftTau && ftTau.total != null ? ftTau.total : null) ??
    (feats.y_tau_oc != null ? feats.y_tau_oc : null) ??
    (feats.predicted_score_tau_oc != null ? feats.predicted_score_tau_oc : null) ??
    null;
  const dayTrade =
    scores.predicted_score_blend ?? scores.decision_score ?? scores.score ?? yTrade;
  const dayOn = scores.predicted_score_on ?? yOn;
  const dayNc = scores.predicted_score_nowcast ?? yNc;
  const dayPath = scores.predicted_score_path ?? scores.y_path ?? yPath;
  const filledFromLive =
    !slotSnapMode &&
    live &&
    ((dayEod == null && liveEod != null) ||
      (dayTau == null && liveTau != null) ||
      (dayTrade == null && liveTrade != null) ||
      (dayPath == null && livePath != null));
  return {
    ...scores,
    stock_code: code || null,
    stock_name: d.stock_name || fallback.stock_name || scores.stock_name || null,
    // 槽位行：锁存决策 y_trade，避免 resolveTradeScore 按 gap 再 fuse
    _t0_lock_trade: !!slotSnapMode,
    predicted_score_eod: dayEod ?? liveEod,
    predicted_score: dayEod ?? scores.predicted_score ?? liveEod,
    predicted_score_tau: slotSnapMode ? yTau : dayTau ?? liveTau,
    score_rem: slotSnapMode ? yTau : dayTau ?? scores.score_rem ?? liveTau,
    y_tau: slotSnapMode ? yTau : scores.y_tau ?? yTau,
    y_tau_oc: slotSnapMode ? (dayTauOc ?? yTau) : dayTauOc,
    predicted_score_tau_oc: slotSnapMode ? (dayTauOc ?? yTau) : dayTauOc,
    predicted_score_blend: slotSnapMode ? yTrade : dayTrade ?? liveTrade,
    y_trade: slotSnapMode ? yTrade : scores.y_trade ?? yTrade,
    decision_score: slotSnapMode ? yTrade : dayTrade ?? scores.decision_score ?? liveTrade,
    score: slotSnapMode ? yTrade : dayTrade ?? scores.score ?? liveTrade,
    predicted_score_on: dayOn ?? liveOn,
    predicted_score_nowcast: dayNc ?? liveNc,
    predicted_score_path: slotSnapMode ? yPath : dayPath ?? livePath,
    y_path: slotSnapMode ? yPath : dayPath ?? livePath,
    predicted_score_complexity: slotSnapMode ? yCx : scores.predicted_score_complexity ?? yCx,
    y_complexity_hat: slotSnapMode ? yCx : scores.y_complexity_hat ?? yCx,
    predicted_score_cx: slotSnapMode ? yCx : scores.predicted_score_cx ?? yCx,
    y_cx_hat: slotSnapMode ? yCx : scores.y_cx_hat ?? yCx,
    predicted_score_tpd: slotSnapMode ? yTpd : scores.predicted_score_tpd ?? yTpd,
    y_tpd_hat: slotSnapMode ? yTpd : scores.y_tpd_hat ?? yTpd,
    y_path_status: scores.y_path_status ?? live?.y_path_status ?? null,
    y_path_error: scores.y_path_error ?? live?.y_path_error ?? null,
    features_path:
      scores.features_path || feats.features_path || live?.features_path || null,
    formula_terms_path:
      scores.formula_terms_path ||
      scores.score_formula_terms_path ||
      live?.formula_terms_path ||
      live?.score_formula_terms_path ||
      null,
    score_formula_terms_path:
      scores.score_formula_terms_path ||
      scores.formula_terms_path ||
      live?.score_formula_terms_path ||
      live?.formula_terms_path ||
      null,
    path_tip_model: scores.path_tip_model || null,
    y_path_enter:
      rules.y_path_enter != null && Number.isFinite(Number(rules.y_path_enter))
        ? Number(rules.y_path_enter)
        : 0.02,
    rules: rules && Object.keys(rules).length ? rules : null,
    predicted_score_blend_vs: scores.predicted_score_blend_vs ?? null,
    y_check: scores.y_check ?? feats.y_check ?? live?.y_check ?? null,
    eod_trust: scores.eod_trust ?? feats.eod_trust ?? live?.eod_trust ?? null,
    dual_score_window:
      scores.dual_score_window ||
      feats.dual_score_window ||
      live?.dual_score_window ||
      "intraday",
    dual_score_fusion:
      scores.dual_score_fusion || feats.dual_score_fusion || live?.dual_score_fusion || null,
    dual_score_weights:
      scores.dual_score_weights || feats.dual_score_weights || live?.dual_score_weights || null,
    dual_score_head:
      scores.dual_score_head || feats.dual_score_head || live?.dual_score_head || null,
    dual_score_single_head:
      scores.dual_score_single_head ??
      feats.dual_score_single_head ??
      live?.dual_score_single_head ??
      null,
    as_of_tau: scores.as_of_tau || feats.as_of_tau || live?.as_of_tau || null,
    rem_tau: scores.rem_tau || null,
    gap_pct:
      (scores.features_tau && scores.features_tau.gap_pct != null
        ? scores.features_tau.gap_pct
        : null) ??
      (feats.features_tau && feats.features_tau.gap_pct != null
        ? feats.features_tau.gap_pct
        : null) ??
      scores.gap_pct ??
      feats.gap_pct ??
      null,
    y_nc: dayNc ?? feats.y_nc ?? scores.y_nc ?? liveNc ?? null,
    y_nc_oc:
      feats.y_nc_oc ??
      feats.y_nowcast_oc ??
      scores.y_nc_oc ??
      scores.y_nowcast_oc ??
      live?.y_nc_oc ??
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
      (rules.y_nc_strong != null ? rules.y_nc_strong : null) ??
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
    predicted_score_eod_rem:
      scores.predicted_score_eod_rem ?? live?.predicted_score_eod_rem ?? null,
    y_spec_tau: scores.y_spec_tau || live?.y_spec_tau || null,
    features_tau: scores.features_tau || feats.features_tau || live?.features_tau || null,
    score_formula_terms:
      scores.score_formula_terms ||
      scores.formula_terms ||
      live?.score_formula_terms ||
      live?.formula_terms ||
      null,
    formula_terms:
      scores.score_formula_terms ||
      scores.formula_terms ||
      live?.score_formula_terms ||
      live?.formula_terms ||
      null,
    factor_coefficients: scores.factor_coefficients || live?.factor_coefficients || null,
    formula_terms_tau:
      scores.formula_terms_tau ||
      scores.score_formula_terms_tau ||
      live?.formula_terms_tau ||
      live?.score_formula_terms_tau ||
      null,
    score_formula_terms_tau:
      scores.score_formula_terms_tau ||
      scores.formula_terms_tau ||
      live?.score_formula_terms_tau ||
      live?.formula_terms_tau ||
      null,
    factor_coefficients_tau:
      scores.factor_coefficients_tau || live?.factor_coefficients_tau || null,
    formula_terms_on:
      scores.formula_terms_on ||
      scores.score_formula_terms_on ||
      live?.formula_terms_on ||
      live?.score_formula_terms_on ||
      null,
    score_formula_terms_on:
      scores.score_formula_terms_on ||
      scores.formula_terms_on ||
      live?.score_formula_terms_on ||
      live?.formula_terms_on ||
      null,
    cluster_label: scores.cluster_label || live?.cluster_label || "",
    weight_source: scores.weight_source || live?.weight_source || "",
    direction_reason: d.direction_reason || "",
    direction_score: d.direction_score,
    score_reasons: d.direction_reason ? [String(d.direction_reason)] : [],
    return_model_source:
      scores.return_model_source ||
      scores._score_source ||
      live?.return_model_source ||
      "t0_backtest_compute",
    // 决策时冻结分（tip 可对照）；live 仅补洞时标记
    _decision_y_eod: dayEod,
    _scores_live_overlay: !!filledFromLive,
  };
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
  return {
    side,
    qty,
    price,
    time,
    eod,
    legKind,
    note,
    at: t?.at,
    slotId: String(t?.t0_slot || ""),
    slotHm: String(t?.t0_slot_hm || ""),
  };
}

const LEG_KIND_TAG = {
  eod_cover: { label: "收", cls: "is-eod" },
  pm_chase: { label: "追", cls: "is-stop" },
  pm_degrade: { label: "追", cls: "is-stop" }, // 旧账本兼容
  stop: { label: "损", cls: "is-stop" },
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

function slotDirShort(dir) {
  if (dir === "buy_then_sell") return "正";
  if (dir === "sell_then_buy") return "反";
  return "";
}

/** 多轮日：按槽位分组成交；无槽位标记时返回 null（走单链）。 */
function processSlotGroups(d) {
  const legs = collectLegRecords(d);
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  if (!d?.t0_slots_enabled || !rows.length) return null;
  const hasSlotLegs = legs.some((l) => l.slotId);
  if (!hasSlotLegs && !d.t0_slot_focus) return null;
  const byId = new Map();
  for (const l of legs) {
    const id = String(l.slotId || "");
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(l);
  }
  const groups = [];
  const seen = new Set();
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const id = String(r.id || "");
    let slotLegs = byId.get(id) || [];
    if (
      !slotLegs.length &&
      d.t0_slot_focus &&
      String(d.t0_slot || "") === id &&
      legs.length
    ) {
      slotLegs = legs.slice();
    }
    seen.add(id);
    groups.push({
      id,
      hm: String(
        r.hm ||
          (String(r.id || "") === String(d?.t0_slot || "") ? d.t0_slot_hm : "") ||
          (slotLegs[0] && slotLegs[0].slotHm) ||
          ""
      ),
      dir: String(r.direction || ""),
      legs: slotLegs,
      skipped: !!r.skipped && !slotLegs.length,
      reason: String(r.reason || ""),
      scores: r.scores && typeof r.scores === "object" ? r.scores : null,
      yTau: slotYhat(r, d).y_tau,
      yPath: slotYhat(r, d).y_path,
      closeBand: slotCloseBand(r),
    });
  }
  for (const [id, slotLegs] of byId) {
    if (seen.has(id) || !slotLegs.length) continue;
    groups.push({
      id,
      hm: String(slotLegs[0].slotHm || ""),
      dir: "",
      legs: slotLegs,
      skipped: false,
      reason: "",
    });
  }
  return groups;
}

function slotYhatBit(g) {
  const bits = [];
  const yt = g && g.yTau != null ? Number(g.yTau) : NaN;
  const yp = g && g.yPath != null ? Number(g.yPath) : NaN;
  if (Number.isFinite(yt)) bits.push(`τ${yt.toFixed(1)}`);
  if (Number.isFinite(yp)) bits.push(`p${yp.toFixed(1)}`);
  const band = fmtCloseBandNote(g && g.closeBand);
  if (band) bits.push(band);
  return bits.length ? ` ${bits.join(" · ")}` : "";
}

function processGroupsForRow(d) {
  const groups = processSlotGroups(d);
  if (!groups) return null;
  const focus = String(d?.t0_slot || "").trim();
  if (focus && d?.t0_slot_focus) {
    const mine = groups.filter((g) => String(g.id || "") === focus);
    return mine.length ? mine : groups;
  }
  return groups;
}

function fmtSlotProcessLines(groups, { yhat = true } = {}) {
  return (groups || [])
    .filter((g) => (g.legs || []).length || g.skipped)
    .map((g) => {
      const bits = [g.hm, slotDirShort(g.dir)].filter(Boolean);
      const ybit = yhat ? slotYhatBit(g) : "";
      if ((g.legs || []).length) {
        const chain = g.legs.map(legPlainText).join(" → ");
        return `${bits.join(" ")}${ybit} ${chain}`.trim();
      }
      const why = String(g.reason || "未成交").trim();
      return `${bits.join(" ")}${ybit} ${why}`.trim();
    });
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
  const legs = tradeLegCells(d);
  if (d.direction === "mixed") {
    const buyN = Number(d.bought_qty || d.buy_shares || 0) * Number(legs.buyPx || d.buy_price || 0);
    const sellN = Number(d.sold_qty || d.sell_shares || 0) * Number(legs.sellPx || d.sell_price || 0);
    const notional = buyN + sellN;
    if (!(notional > 0)) return null;
    return (net / notional) * 100;
  }
  const isBuyThenSell = d.direction === "buy_then_sell";
  const qty = isBuyThenSell
    ? Number(d.bought_qty || d.buy_shares || 0)
    : Number(d.sold_qty || d.sell_shares || 0);
  const px = isBuyThenSell
    ? Number(legs.buyPx || d.buy_price || 0)
    : Number(legs.sellPx || d.sell_price || 0);
  const notional = qty * px;
  if (!(notional > 0)) return null;
  return (net / notional) * 100;
}

function fmtDayReturnPct(d) {
  const pct = dayReturnPct(d);
  if (pct == null || !Number.isFinite(pct)) {
    return { text: "—", tip: "缺动仓股数或成交价，无法算收益%", pct: null, heat: "" };
  }
  const sign = pct > 0 ? "+" : "";
  const a = Math.abs(pct);
  const heat = a >= 1 ? " is-ret-hot" : a >= 0.35 ? " is-ret-warm" : " is-ret-cool";
  return {
    text: `${sign}${pct.toFixed(2)}%`,
    tip: `(PnL ${d.pnl ?? 0} + 敞口 ${d.exposure_pnl ?? 0}) / 动仓名义`,
    pct,
    heat,
  };
}

/** 结构化过程列 HTML（卖/买 chip + 箭头链；多轮按时钟分组） */
export function legProcessFlowHtml(d) {
  const groups = processGroupsForRow(d);
  const showTimes = !!d.minute_path;
  if (groups) {
    const shown = groups.filter((g) => (g.legs || []).length || g.skipped);
    if (shown.length) {
      const parts = shown.map((g, gi) => {
        const chips = (g.legs || [])
          .map(
            (l, i) =>
              `${i ? `<span class="paper-t0-leg-join" aria-hidden="true"></span>` : ""}${legChipHtml(l, showTimes)}`
          )
          .join("");
        const skipHtml =
          g.skipped && !(g.legs || []).length
            ? `<span class="paper-t0-leg-skip" title="${escapeText(g.reason || "未成交")}">${escapeText(
                String(g.reason || "未成交")
              )}</span>`
            : "";
        const ds = slotDirShort(g.dir);
        const dirCls = ds === "正" ? "is-bts" : ds === "反" ? "is-stb" : "";
        const badge =
          `<span class="paper-t0-leg-round-hm">` +
          `${escapeText(g.hm || g.id || "")}` +
          (ds
            ? `<span class="paper-t0-leg-round-dir ${dirCls}">${escapeText(ds)}</span>`
            : "") +
          `</span>`;
        const sep = gi
          ? `<span class="paper-t0-leg-round-sep" aria-hidden="true"></span>`
          : "";
        return `${sep}<span class="paper-t0-leg-round${g.skipped ? " is-skip" : ""}">${badge}${chips}${skipHtml}</span>`;
      });
      return `<div class="paper-t0-leg-flow is-minute is-slots">${parts.join("")}</div>`;
    }
  }
  const legs = collectLegRecords(d);
  if (!legs.length) return `<span class="paper-t0-leg-empty">—</span>`;
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
  const groups = processGroupsForRow(d);
  if (groups) {
    const lines = fmtSlotProcessLines(groups, { yhat: !d.t0_slot_focus });
    if (lines.length) return lines.join(" · ");
  }
  const legs = collectLegRecords(d);
  return legs.length ? legs.map(legPlainText).join(" → ") : "—";
}

function legQtyCells(d) {
  const isBuyThenSell = d.direction === "buy_then_sell";
  if (isBuyThenSell) {
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
  const isBuyThenSell = d.direction === "buy_then_sell";
  if (isBuyThenSell) {
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

/** 过程 tip：从 forward_trace 压成迷你 K 线点（[hm,o,h,l,c,flag]）。
 * flag: 1=leg1 · 2=leg2 · 4=前缀 low · 8=前缀 high（可按位或）。
 */
function compactTraceBars(trace, { prefixBars = 0, dir = "", markPrefix = true } = {}) {
  const rows = Array.isArray(trace) ? trace : [];
  const bts = dir === "正T" || dir === "buy_then_sell";
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
    if (r.buy_fill) flag |= 1;
    if (r.sell_fill) flag |= 2;
    if (!(flag & 3)) {
      if (r.leg1_fill) flag |= bts ? 1 : 2;
      if (r.leg2_fill) flag |= bts ? 2 : 1;
    }
    if (r.entry_ready || r.leg1_fill || r.buy_fill || r.sell_fill) readyIdx = out.length;
    const hm = String(r.time || "").replace(":", "") || String(r.idx ?? out.length);
    out.push([hm, o, h, l, c, flag]);
  }
  if (out.length < 1 || markPrefix === false) return out;
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

function barHmKey(hm) {
  const s = String(hm || "").trim();
  const colon = s.match(/(\d{1,2}):(\d{2})/);
  if (colon) return `${colon[1].padStart(2, "0")}${colon[2]}`;
  const d = s.replace(/\D/g, "");
  if (d.length >= 4) return d.slice(0, 4).padStart(4, "0");
  return d;
}

function fmtBarHm(hm) {
  const k = barHmKey(hm);
  if (k.length >= 4) return `${k.slice(0, 2)}:${k.slice(2, 4)}`;
  return String(hm || "").trim() || "—";
}

function fmtBarPx(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || !(v > 0)) return "—";
  if (v >= 1000) return v.toFixed(1);
  if (v >= 100) return v.toFixed(2);
  if (v >= 10) return v.toFixed(3);
  return v.toFixed(3);
}

function barOhlcTitle(b) {
  const hm = fmtBarHm(b[0]);
  return `${hm}  O=${fmtBarPx(b[1])}  H=${fmtBarPx(b[2])}  L=${fmtBarPx(b[3])}  C=${fmtBarPx(b[4])}`;
}

/** 同步 SVG 迷你 K 线（tip 内用，避免悬停再拉 LW）。 */
export function miniKlineSvgFromBars(
  bars,
  { width = 420, height = 176, dir = "", emptyHtml = "", markers = [] } = {}
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
  const marks = Array.isArray(markers) ? markers.filter((m) => m && barHmKey(m.hm)) : [];
  const padL = 44;
  const padR = 8;
  const padT = marks.length ? 34 : 14;
  const padB = 20;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const n = pts.length;
  const slot = plotW / n;
  const bodyW = Math.max(2.5, Math.min(10, slot * 0.58));
  const yScale = (p) => padT + ((hi - p) / (hi - lo)) * plotH;
  const up = "#ef4444";
  const down = "#22c55e";
  const parts = [];
  const mid = (hi + lo) / 2;
  parts.push(
    `<text x="${padL - 4}" y="${(padT + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(
      fmtBarPx(hi)
    )}</text>` +
      `<text x="${padL - 4}" y="${(padT + plotH / 2 + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(
        fmtBarPx(mid)
      )}</text>` +
      `<text x="${padL - 4}" y="${(padT + plotH + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(
        fmtBarPx(lo)
      )}</text>`
  );
  const idxByHm = new Map();
  pts.forEach((b, i) => {
    const k = barHmKey(b[0]);
    if (k && !idxByHm.has(k)) idxByHm.set(k, i);
  });
  marks.forEach((m) => {
    const i = idxByHm.get(barHmKey(m.hm));
    if (i == null) return;
    const x = padL + slot * i + slot / 2;
    const stroke = m.filled ? "#93c5fd" : "#cbd5e1";
    parts.push(
      `<line x1="${x.toFixed(1)}" y1="${padT}" x2="${x.toFixed(1)}" y2="${(
        height - padB
      ).toFixed(1)}" stroke="${stroke}" stroke-width="1" stroke-dasharray="3 3"/>`
    );
    const lab = String(m.label || m.hm || "").trim();
    if (lab) {
      parts.push(
        `<text x="${x.toFixed(1)}" y="${(padT - 8).toFixed(1)}" text-anchor="middle" font-size="9" fill="${
          m.filled ? "#1d4ed8" : "#64748b"
        }" font-family="IBM Plex Mono,monospace">${escapeText(lab)}</text>`
      );
    }
  });
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
    const tip = barOhlcTitle(b);
    parts.push(
      `<g>` +
        `<title>${escapeText(tip)}</title>` +
        `<line x1="${x.toFixed(1)}" y1="${yH.toFixed(1)}" x2="${x.toFixed(1)}" y2="${yL.toFixed(1)}" stroke="${color}" stroke-width="1.25"/>` +
        `<rect x="${(x - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}"/>` +
        `</g>`
    );
    const hasBuy = !!(flag & 1);
    const hasSell = !!(flag & 2);
    const sideDx = Math.max(14, bodyW * 0.65 + 8);
    if (hasBuy) {
      const cy = yL + 9;
      parts.push(
        `<circle cx="${x.toFixed(1)}" cy="${cy.toFixed(1)}" r="4" fill="#2563eb" stroke="#fff" stroke-width="1"/>` +
          `<text x="${x.toFixed(1)}" y="${(cy + 12).toFixed(1)}" text-anchor="middle" font-size="10" fill="#2563eb" font-family="IBM Plex Mono,monospace">买</text>`
      );
    }
    if (hasSell) {
      const cy = yH - 9;
      parts.push(
        `<circle cx="${x.toFixed(1)}" cy="${cy.toFixed(1)}" r="4" fill="#b45309" stroke="#fff" stroke-width="1"/>` +
          `<text x="${x.toFixed(1)}" y="${(cy - 6).toFixed(1)}" text-anchor="middle" font-size="10" fill="#b45309" font-family="IBM Plex Mono,monospace">卖</text>`
      );
    }
    if (flag & 4) {
      const lx = hasBuy ? x - sideDx : x;
      const ly = hasBuy ? yL + 3 : yL + 11;
      const lAnchor = hasBuy ? "end" : "middle";
      parts.push(
        `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${lAnchor}" font-size="9" font-weight="700" fill="#0f766e" font-family="IBM Plex Mono,monospace">L ${escapeText(
          fmtBarPx(l)
        )}</text>`
      );
    }
    if (flag & 8) {
      const hx = hasSell ? x + sideDx : x;
      const hy = hasSell ? yH - 2 : yH - 4;
      const hAnchor = hasSell ? "start" : "middle";
      parts.push(
        `<text x="${hx.toFixed(1)}" y="${hy.toFixed(1)}" text-anchor="${hAnchor}" font-size="9" font-weight="700" fill="#a16207" font-family="IBM Plex Mono,monospace">H ${escapeText(
          fmtBarPx(h)
        )}</text>`
      );
    }
  });
  const t0 = String(pts[0][0] || "");
  const t1 = String(pts[pts.length - 1][0] || "");
  parts.push(
    `<text x="${padL}" y="${height - 5}" font-size="10" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(
      fmtBarHm(t0)
    )}</text>` +
      `<text x="${width - padR}" y="${height - 5}" text-anchor="end" font-size="10" fill="#94a3b8" font-family="IBM Plex Mono,monospace">${escapeText(
        fmtBarHm(t1)
      )}</text>`
  );
  return (
    `<svg class="paper-t0-process-kline" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="5分钟K线">${parts.join("")}</svg>`
  );
}

function findBarByHm(bars, hm) {
  const k = barHmKey(hm);
  if (!k) return null;
  const rows = Array.isArray(bars) ? bars : [];
  for (const b of rows) {
    if (barHmKey(b[0]) === k) return b;
  }
  return null;
}

function slotOhlcFromBars(bars, hm) {
  const b = findBarByHm(bars, hm);
  if (!b) return { o: null, l: null, h: null, c: null };
  return {
    o: Number(b[1]),
    l: Number(b[3]),
    h: Number(b[2]),
    c: Number(b[4]),
  };
}

function _finitePx(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** 成交主表：触发根 O/L/H/C。 */
function tradeBarPxTd(colKey, n, title = "") {
  const tip = String(title || "").trim();
  const tipAttr = tip ? ` title="${escapeText(tip)}"` : "";
  const v = Number(n);
  const tone = colKey === "l" ? " is-lo" : colKey === "h" ? " is-hi" : "";
  if (!Number.isFinite(v) || !(v > 0)) {
    return `<td class="num paper-t0-col-${escapeText(colKey)} is-na${tone}"${tipAttr}>—</td>`;
  }
  return `<td class="num paper-t0-col-${escapeText(colKey)}${tone}"${tipAttr}>${escapeText(
    fmtBarPx(v)
  )}</td>`;
}

/** 成交主表：Ĉ_τ（主显分钟映后；有日原则旁注）。 */
function tradeCtauTd(minutePx, dailyPx) {
  const m = Number(minutePx);
  const d = Number(dailyPx);
  const mOk = Number.isFinite(m) && m > 0;
  const dOk = Number.isFinite(d) && d > 0;
  const bits = ["ĉ_τ"];
  if (dOk) bits.push(`日原 ${fmtBarPx(d)}（估空间·未÷S）`);
  if (mOk) bits.push(`分钟 ${fmtBarPx(m)}（破带·÷S 后）`);
  const tipAttr = ` title="${escapeText(bits.join(" · "))}"`;
  if (!mOk) {
    return `<td class="num paper-t0-col-ctau is-na"${tipAttr}>—</td>`;
  }
  const differ =
    dOk && Math.abs(d - m) >= 0.0005
      ? `<span class="paper-t0-day-yhat-daily" title="${escapeText(
          `日原 ${fmtBarPx(d)}`
        )}">日${escapeText(fmtBarPx(d))}</span>`
      : "";
  return (
    `<td class="num paper-t0-col-ctau"${tipAttr}>` +
    `<span class="paper-t0-day-yhat-min">${escapeText(fmtBarPx(m))}</span>` +
    differ +
    `</td>`
  );
}

function slotRtauPct(slotRow, barClose, cTau) {
  const cb = slotCloseBand(slotRow);
  const num = (v) => {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  let r = num(cb && (cb.band_r_pct ?? cb.r_pct));
  if (r == null) {
    const c = Number(barClose);
    const mid = Number(cTau);
    if (Number.isFinite(c) && c > 0 && Number.isFinite(mid) && mid > 0) {
      r = (c / mid - 1) * 100;
    }
  }
  return r;
}

/** R̂_τ 入场阈 = 页面 R入场%（r_tau_enter，0–1.0；0=关）。带宽因 τ 先验会漂，上下沿不对称，不能当阈值。 */
function slotRtauEnterPct(slotRow, rPct, direction, rTauEnterCfg) {
  const cb = slotCloseBand(slotRow);
  const cfgN = Number(rTauEnterCfg ?? (cb && cb.band_r_enter_pct));
  if (!Number.isFinite(cfgN) || cfgN <= 0) return null;
  const dir = String(
    direction || (slotRow && (slotRow.direction || slotRow.direction_used)) || ""
  ).trim();
  const r = Number(rPct);
  const wantDown =
    dir === "buy_then_sell" ||
    (dir !== "sell_then_buy" && Number.isFinite(r) && r < -1e-9);
  return { pct: wantDown ? -cfgN : cfgN, label: "R入场%" };
}

function fmtRtauPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs < 0.1 && abs > 0 ? 3 : 2;
  const body = v.toFixed(digits);
  return `${v > 0 ? "+" : ""}${body}%`;
}

/** 成交主表：R̂_τ + 页面 R入场%（0–1.0）。 */
function tradeRtauTd(rPct, slotRow, direction, rTauEnterCfg) {
  const cb = slotCloseBand(slotRow);
  const num = (v) => {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const edge = slotRtauEnterPct(slotRow, rPct, direction, rTauEnterCfg);
  const enter = edge && edge.pct;
  const bits = ["r̂_τ = (C/ĉ_τ−1)×100 · 相对 ĉ 的超额"];
  const cfgN = Number(rTauEnterCfg ?? (cb && cb.band_r_enter_pct));
  if (Number.isFinite(cfgN) && cfgN > 0) {
    bits.push(`入场阈 |R̂_τ|≥${cfgN}%（页面 R入场%，与带宽/τ先验漂移无关）`);
  } else {
    bits.push("本条无 R入场阈值");
  }
  const up = num(cb && (cb.band_upper_pct ?? cb.upper_pct));
  const lo = num(cb && (cb.band_lower_pct ?? cb.lower_pct));
  if (up != null) bits.push(`破带上沿 ${up.toFixed(2)}%`);
  if (lo != null) bits.push(`破带下沿 ${lo.toFixed(2)}%`);
  const tipAttr = ` title="${escapeText(bits.join(" · "))}"`;
  const v = num(rPct);
  if (v == null) {
    return `<td class="num paper-t0-col-rtau is-na"${tipAttr}>—</td>`;
  }
  const tone = v > 1e-9 ? " up" : v < -1e-9 ? " down" : "";
  const floor = Number.isFinite(cfgN) && cfgN > 0 ? cfgN : null;
  const cleared =
    floor == null
      ? ""
      : Math.abs(v) + 1e-12 >= floor
        ? " is-cleared"
        : " is-short";
  const enterLine =
    enter != null
      ? `<span class="paper-t0-rtau-enter" title="${escapeText(
          `入场阈 |R̂_τ|≥${cfgN}% · 页面 R入场%`
        )}">入${escapeText(fmtRtauPct(enter))}</span>`
      : "";
  return (
    `<td class="num paper-t0-col-rtau${tone}${cleared}"${tipAttr}>` +
    `<span class="paper-t0-rtau-val">${escapeText(fmtRtauPct(v))}</span>` +
    enterLine +
    `</td>`
  );
}

/** 本轮触发根 OHLC + Ĉ_τ + R̂_τ。 */
function tradeSlotPxCells(host, rules) {
  const bars = compactTraceBars(host && host.forward_trace, {
    prefixBars: Number(host && host.prefix_bars) || 0,
    dir: (host && (host.direction || host.direction_used)) || "",
    markPrefix: false,
  });
  let hm = String(host?.t0_slot_hm || "").trim();
  let slotRow = null;
  if (host?.t0_slot_focus) {
    slotRow = focusedSlotRow(host);
  } else {
    const rows = Array.isArray(host?.t0_slot_results) ? host.t0_slot_results : [];
    for (const r of rows) {
      if (slotFilled(r)) {
        slotRow = r;
        break;
      }
    }
  }
  if (!hm && slotRow) hm = slotClock(slotRow, host?.t0_slot_results);
  let ohlc = hm ? slotOhlcFromBars(bars, hm) : { o: null, l: null, h: null, c: null };
  if (ohlc.o == null && ohlc.c == null) {
    ohlc = {
      o: _finitePx(host && host.open),
      l: _finitePx(host && host.low),
      h: _finitePx(host && host.high),
      c: _finitePx(host && host.close),
    };
  }
  const px = slotClosePxParts(slotRow);
  const cTau = px.c_tau ?? px.c_hat;
  const cTauDaily = px.c_tau_daily ?? px.c_hat_daily;
  const rTau = slotRtauPct(slotRow, ohlc.c, cTau);
  return (
    tradeBarPxTd("o", ohlc.o, "本轮触发根 5m 开盘") +
    tradeBarPxTd("l", ohlc.l, "本轮触发根 5m 最低") +
    tradeBarPxTd("h", ohlc.h, "本轮触发根 5m 最高") +
    tradeBarPxTd("c", ohlc.c, "本轮触发根 5m 收盘") +
    tradeCtauTd(cTau, cTauDaily) +
    tradeRtauTd(
      rTau,
      slotRow,
      host && (host.direction || host.direction_used),
      rules && rules.r_tau_enter
    )
  );
}

export function buildProcessTipPayload(d, legTip) {
  const groups = processGroupsForRow(d);
  const filledN = groups ? groups.filter((g) => (g.legs || []).length).length : 0;
  const slotFocus = !!d?.t0_slot_focus;
  const dirRaw = (d && (d.direction || d.direction_used)) || "";
  const dir =
    dirRaw === "buy_then_sell"
      ? "正T"
      : dirRaw === "sell_then_buy"
        ? "反T"
        : dirRaw === "mixed"
          ? "多轮"
          : "";
  void legTip;
  let trace = d && d.forward_trace;
  // 槽位行：只标本轮 trades（slotDayRow 已 remask；此处兜底）
  if (slotFocus && Array.isArray(d.trades)) {
    trace = remaskTraceFills(trace, d.trades);
  }
  const bars = compactTraceBars(trace, {
    prefixBars: Number(d && d.prefix_bars) || 0,
    dir: dirRaw,
    markPrefix: slotFocus || filledN <= 1,
  });
  const slotHm = slotFocus ? String(d?.t0_slot_hm || "").trim() : "";
  const slotRow = slotFocus ? focusedSlotRow(d) : null;
  const closeBandNote = fmtCloseBandNote(slotCloseBand(slotRow));
  return {
    dir,
    bars,
    slots: !slotFocus && (filledN > 1 || dirRaw === "mixed"),
    slot_hm: slotHm,
    close_band: closeBandNote || null,
  };
}

export function buildProcessTipHtml(payload) {
  let p = payload;
  if (typeof p === "string") {
    try {
      p = JSON.parse(p);
    } catch (_) {
      p = { bars: [] };
    }
  }
  p = p || {};
  const dir = String(p.dir || "").trim();
  const bars = Array.isArray(p.bars) ? p.bars : [];
  const slotHm = String(p.slot_hm || "").trim();
  const chart = miniKlineSvgFromBars(bars, {
    dir,
    markers: slotHm ? [{ hm: slotHm, label: slotHm, filled: true }] : [],
  });
  const foot = slotHm
    ? `悬停看 O/H/L/C · 买/卖=本轮成交 · ${slotHm} 决策钟 · L/H=本轮前缀极值价${
        p.close_band ? ` · ${p.close_band}` : ""
      }`
    : p.slots
      ? "悬停看 O/H/L/C · 买/卖=各轮成交 · K 线为当日 5m（多轮共用）"
      : `悬停看 O/H/L/C · 买/卖=成交腿 · L/H=前缀极值价${
          p.close_band ? ` · ${p.close_band}` : ""
        }`;
  return (
    `<div class="paper-t0-process-tip-inner">` +
    `<header class="paper-t0-process-tip-head">` +
    (dir ? `<span class="paper-t0-process-tip-badge">${escapeText(dir)}</span>` : "") +
    `<span class="paper-t0-process-tip-eyebrow">过程 · 5m K</span>` +
    `</header>` +
    `<div class="paper-t0-process-tip-chart">${chart}</div>` +
    `<p class="paper-t0-process-tip-foot">${foot}</p>` +
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

function fmtCloseBandNote(cb) {
  if (!cb || typeof cb !== "object") return "";
  const c = cb.close_px;
  const d = cb.delta_px;
  const l2 = cb.leg2_target;
  if (c == null && d == null && l2 == null) return "";
  const bits = [];
  if (c != null && Number.isFinite(Number(c))) bits.push(`ĉ=${Number(c)}`);
  if (d != null && Number.isFinite(Number(d))) bits.push(`δ=${Number(d)}`);
  if (l2 != null && Number.isFinite(Number(l2))) bits.push(`→leg2 ${Number(l2)}`);
  return bits.join(" ");
}

function slotCloseBand(r) {
  const cb = r && r.close_band && typeof r.close_band === "object" ? r.close_band : null;
  return cb;
}

/** 从 close_band 取出 ĉ_τ / ĉ（分钟映后 + 日原）。 */
function slotClosePxParts(r) {
  const cb = slotCloseBand(r);
  if (!cb) {
    return {
      c_tau: null,
      c_trade: null,
      c_nowcast: null,
      c_hat: null,
      c_tau_daily: null,
      c_trade_daily: null,
      c_nowcast_daily: null,
      c_hat_daily: null,
    };
  }
  const num = (v) => {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  return {
    c_tau: num(cb.c_tau),
    c_trade: num(cb.c_trade),
    c_nowcast: num(cb.c_nowcast),
    c_hat: num(cb.close_px),
    c_tau_daily: num(cb.c_tau_daily),
    c_trade_daily: num(cb.c_trade_daily),
    c_nowcast_daily: num(cb.c_nowcast_daily),
    c_hat_daily: num(cb.close_px_daily),
  };
}

function tradeColgroup(showStock, showReason = false, showDelete = false, showRealized = false) {
  let html = "<colgroup>";
  if (showStock) html += t0Col("paper-t0-col-stock", "stock");
  html +=
    t0Col("paper-t0-col-date", "date") +
    t0Col("paper-t0-col-o", "o") +
    t0Col("paper-t0-col-l", "l") +
    t0Col("paper-t0-col-h", "h") +
    t0Col("paper-t0-col-c", "c") +
    t0Col("paper-t0-col-ctau", "ctau") +
    t0Col("paper-t0-col-rtau", "rtau") +
    t0Col("paper-t0-col-tau", "tau") +
    t0Col("paper-t0-col-path", "path") +
    (showRealized ? t0Col("paper-t0-col-cx", "cx") + t0Col("paper-t0-col-tpd", "tpd") : "") +
    t0Col("paper-t0-col-process", "process") +
    t0Col("paper-t0-col-ret", "retPct") +
    t0Col("paper-t0-col-pnl", "pnl") +
    t0Col("paper-t0-col-exp", "exp");
  if (showReason) html += t0Col("paper-t0-col-reason", "reason");
  if (showDelete) html += t0Col("paper-t0-col-act", "act");
  return `${html}</colgroup>`;
}

export const T0_TRADE_TABLE_MAX_ROWS = 500;

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

function applyTdRowspan(html, span) {
  const n = Number(span);
  if (!(n > 1) || typeof html !== "string") return html;
  return html.replace("<td ", `<td rowspan="${n}" `);
}

function tradeDaySlotHosts(d, splitSlots) {
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  if (!splitSlots || !rows.length) return [d];
  const filled = rows.filter((r) => r && typeof r === "object" && slotFilled(r));
  if (!filled.length) return [d];
  return filled.map((r) => slotDayRow(d, r, rows));
}

function tradeTableColCount(ctx) {
  let n = 1;
  if (ctx.showStock) n += 1;
  n += 6; // O L H C Ĉ_τ R̂_τ
  n += 2; // y_τ y_path
  if (ctx.showRealized) n += 2; // y_complexity y_tpd
  n += 1; // process
  n += 3; // ret pnl exp
  if (ctx.showReason) n += 1;
  if (ctx.showDelete) n += 1;
  return n;
}

function fmtScanPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  return `${v.toFixed(2)}%`;
}

function fmtScanRtau(r, rTauEnterCfg) {
  const rTxt = fmtScanPct(r && r.r_pct);
  const pick = String((r && r.pick) || "").trim();
  const fakeSlot = {
    close_band: {
      band_upper_pct: r && r.upper_pct,
      band_lower_pct: r && r.lower_pct,
    },
    direction: pick,
  };
  const edge = slotRtauEnterPct(
    fakeSlot,
    r && r.r_pct,
    pick,
    rTauEnterCfg != null ? rTauEnterCfg : r && r.r_enter
  );
  const thN = Number(edge && edge.pct);
  if (!Number.isFinite(thN)) return rTxt;
  return `${rTxt} 入${fmtRtauPct(thN)}`;
}

function fmtScanPick(pick) {
  const p = String(pick || "").trim();
  if (p === "buy_then_sell") return "正";
  if (p === "sell_then_buy") return "反";
  return "—";
}

function finitePosPx(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** 日/分钟 OHLC 对账：优先 price_space，回退日 bar / scan / forward_trace。 */
function closeBandDayOhlcCheck(d) {
  const ps = d?.price_space && typeof d.price_space === "object" ? d.price_space : {};
  const scan = Array.isArray(d?.close_band_scan) ? d.close_band_scan : [];
  const trace = Array.isArray(d?.forward_trace) ? d.forward_trace : [];

  const dailyOpen = finitePosPx(ps.daily_open) ?? finitePosPx(d?.open);
  const dailyClose = finitePosPx(ps.daily_close) ?? finitePosPx(d?.close);

  let minFirstOpen = finitePosPx(ps.minute_open);
  let minLastClose = finitePosPx(ps.minute_close);

  if (minFirstOpen == null && scan.length) {
    minFirstOpen = finitePosPx(scan[0].o);
  }
  if (minFirstOpen == null && trace.length) {
    minFirstOpen = finitePosPx(trace[0]?.open);
  }

  if (minLastClose == null && trace.length) {
    for (let i = trace.length - 1; i >= 0; i -= 1) {
      const c = finitePosPx(trace[i]?.close);
      if (c != null) {
        minLastClose = c;
        break;
      }
    }
  }
  // scan 仅至 11:00；无全日 trace 时才用末根扫描收价
  if (minLastClose == null && scan.length) {
    minLastClose = finitePosPx(scan[scan.length - 1].c);
  }

  return { dailyOpen, dailyClose, minFirstOpen, minLastClose };
}

function closeBandOhlcDevPct(a, b) {
  if (a == null || b == null || !(a > 0)) return null;
  return (b / a - 1) * 100;
}

function closeBandScanOhlcTip(ohlc) {
  const { dailyOpen, dailyClose, minFirstOpen, minLastClose } = ohlc || {};
  const bits = ["日 K 与分钟序列对账"];
  const oDev = closeBandOhlcDevPct(dailyOpen, minFirstOpen);
  const cDev = closeBandOhlcDevPct(dailyClose, minLastClose);
  if (oDev != null) bits.push(`开盘 |O_分/O_日−1|=${Math.abs(oDev).toFixed(3)}%`);
  if (cDev != null) bits.push(`收盘 |C_分末/C_日−1|=${Math.abs(cDev).toFixed(3)}%`);
  return bits.join(" · ");
}

function closeBandScanTitleHtml(d) {
  const ohlc = closeBandDayOhlcCheck(d);
  const { dailyOpen, dailyClose, minFirstOpen, minLastClose } = ohlc;
  const oDev = closeBandOhlcDevPct(dailyOpen, minFirstOpen);
  const cDev = closeBandOhlcDevPct(dailyClose, minLastClose);
  const warn =
    (oDev != null && Math.abs(oDev) > 0.25) || (cDev != null && Math.abs(cDev) > 0.25);
  const dayBit = `日线 O ${fmtBarPx(dailyOpen)} · C ${fmtBarPx(dailyClose)}`;
  const minBit = `分钟首 O ${fmtBarPx(minFirstOpen)} · 末 C ${fmtBarPx(minLastClose)}`;
  const checkCls = warn ? " paper-t0-scan-ohlc-check is-mismatch" : " paper-t0-scan-ohlc-check";
  return (
    `<div class="paper-t0-scan-debug-title">` +
    `<span class="paper-t0-scan-debug-lead">11:00 前扫描 · Ĉ=该根前缀 ŷ_τ · C=5m收价</span>` +
    `<span class="${checkCls.trim()}" title="${escapeText(closeBandScanOhlcTip(ohlc))}">` +
  `${escapeText(dayBit)} · ${escapeText(minBit)}` +
    `</span>` +
    `</div>`
  );
}

function buildCloseBandScanExpandRow(d, dayKey, colSpan, rules) {
  const scan = Array.isArray(d?.close_band_scan) ? d.close_band_scan : [];
  if (!scan.length) return "";
  const rEnter = rules && rules.r_tau_enter;
  const body = scan
    .map((r) => {
      if (!r || typeof r !== "object") return "";
      const hm = escapeText(String(r.hm || ""));
      const legCls = r.leg1 ? " is-leg1" : "";
      const skips = [
        r.minute_missing ? "分钟缺失" : "",
        r.enter_skip ? String(r.enter_skip) : "",
        r.sign_skip ? String(r.sign_skip) : "",
      ]
        .filter(Boolean)
        .join(" · ");
      return (
        `<tr class="paper-t0-scan-row${legCls}">` +
        `<td class="paper-t0-scan-hm">${hm}</td>` +
        `<td class="num">${escapeText(fmtBarPx(r.o))}</td>` +
        `<td class="num is-lo">${escapeText(fmtBarPx(r.l))}</td>` +
        `<td class="num is-hi">${escapeText(fmtBarPx(r.h))}</td>` +
        `<td class="num">${escapeText(fmtBarPx(r.c))}</td>` +
        `<td class="num">${escapeText(fmtBarPx(r.c_tau))}</td>` +
        `<td class="num">${escapeText(fmtScanRtau(r, rEnter))}</td>` +
        `<td class="num">${escapeText(fmtScanPct(r.y_tau))}</td>` +
        `<td class="num">${escapeText(fmtPathScore(r.y_path))}</td>` +
        `<td class="num">${escapeText(fmtCxScore(r.y_complexity ?? r.y_cx ?? r.predicted_score_complexity ?? r.predicted_score_cx))}</td>` +
        `<td class="num">${escapeText(fmtCxScore(r.predicted_score_tpd ?? r.y_tpd_hat ?? r.y_tpd))}</td>` +
        `<td>${escapeText(fmtScanPick(r.pick))}</td>` +
        `<td class="paper-t0-scan-skip">${escapeText(skips || "—")}</td>` +
        `</tr>`
      );
    })
    .join("");
  return (
    `<tr class="paper-t0-day-debug" data-t0-day-id="${escapeText(dayKey)}" hidden>` +
    `<td colspan="${colSpan}" class="paper-t0-day-debug-cell">` +
    `<div class="paper-t0-scan-debug-wrap">` +
    closeBandScanTitleHtml(d) +
    `<table class="paper-t0-scan-debug">` +
    `<thead><tr>` +
    `<th>钟</th><th class="num">O</th><th class="num">L</th><th class="num">H</th>` +
    `<th class="num">C</th><th class="num">Ĉ_τ</th><th class="num" title="r̂_τ = (C/ĉ_τ−1)×100 · 入场阈=页面 R入场%（0–1.0；0=关；与带宽δ/τ先验漂移无关）">R̂_τ</th>` +
    `<th class="num">y_τ</th><th class="num">y_path</th><th class="num" title="${escapeText(
      Y_COMPLEXITY_TITLE
    )}">y_complexity</th>` +
    `<th class="num" title="${escapeText(Y_TPD_TITLE)}">y_tpd</th>` +
    `<th>破带</th><th>跳过</th>` +
    `</tr></thead>` +
    `<tbody>${body}</tbody>` +
    `</table></div></td></tr>`
  );
}

/** 点击「日」列展开 11:00 前每根扫描（需 close_band_scan）。 */
export function wireT0DayDebugExpand(host) {
  if (!host) return;
  host.addEventListener("click", (ev) => {
    const btn =
      ev.target && ev.target.closest ? ev.target.closest(".paper-t0-day-debug-toggle") : null;
    if (!btn || !host.contains(btn)) return;
    ev.preventDefault();
    const id = String(btn.getAttribute("data-t0-day-id") || "").trim();
    if (!id) return;
    const esc =
      typeof CSS !== "undefined" && CSS.escape ? CSS.escape(id) : id.replace(/"/g, "\\\"");
    const row = host.querySelector(`.paper-t0-day-debug[data-t0-day-id="${esc}"]`);
    if (!row) return;
    const opening = row.hidden;
    row.hidden = !opening;
    btn.classList.toggle("is-open", opening);
    btn.setAttribute("aria-expanded", opening ? "true" : "false");
  });
}

function renderTradeDayHtml(d, ctx) {
  const {
    data,
    fallback,
    rules,
    liveByCode,
    showStock,
    showRealized,
    showReason,
    showDelete,
    showTime,
    splitSlots,
  } = ctx;
  const hosts = tradeDaySlotHosts(d, splitSlots);
  const span = hosts.length;
  const daySkipped = !!d.skipped;
  const expCell = fmtExposureCell(d);
  const retCell = fmtDayReturnPct(d);
  const code = String(d.stock_code || fallback.stock_code || "").trim();
  const name = String(d.stock_name || fallback.stock_name || code).trim();
  const reason = String(d.reason || d.direction_reason || d.error || "").trim();
  const sess = data.sessionDate || data.session_date;
  const dayText = fmtTradeDate(d.date, sess);
  const dayKey = `${code}|${dayText}`;
  const hasScan = Array.isArray(d.close_band_scan) && d.close_band_scan.length > 0;
  const delBtn =
    showDelete && code && !daySkipped
      ? `<button type="button" class="paper-t0-ledger-del" data-code="${escapeText(
          code
        )}" data-name="${escapeText(name)}" title="删除并冲正账本">删除</button>`
      : showDelete
        ? `<span class="paper-t0-leg-empty">—</span>`
        : "";

  const slotYCells = (host, dayRef) => {
    const scoreHost = tradeScoreHost(dayRef || d, host);
    const scoreDetailJson = escapeText(
      watchingScoreDetail(t0DayScoreItem(scoreHost, fallback, rules, liveByCode))
    );
    const slotYRealized = showRealized;
    return (
      yPctMergedCellHtml(
        "tau",
        scoreHost,
        fallback,
        rules,
        scoreDetailJson,
        host.direction_reason || d.direction_reason || null,
        slotYRealized,
        liveByCode
      ) +
      pathMergedCellHtml(scoreHost, fallback, rules, scoreDetailJson, slotYRealized, liveByCode) +
      (showRealized
        ? cxMergedCellHtml(scoreHost, dayRef || d, slotYRealized) +
          tpdMergedCellHtml(scoreHost, dayRef || d, slotYRealized)
        : "")
    );
  };

  const slotProcessCell = (host) => {
    const process = fmtLegProcess(host);
    const legs = tradeLegCells(host);
    const qty = legQtyCells(host);
    const sizingTip = adaptiveSizingDayTip(d, rules);
    const hostReason = String(host.reason || reason).trim();
    const legTip =
      (sizingTip ? `${sizingTip} · ` : "") +
      (daySkipped && hostReason && !host.t0_slot_focus
        ? hostReason
        : process !== "—"
          ? process
          : `卖 ${qty.sellQty}股 @ ${fmtT0LegPrice(legs.sellPx)} · 买 ${qty.buyQty}股 @ ${fmtT0LegPrice(legs.buyPx)}` +
            (showTime ? "" : " · 缺触达时刻"));
    const processTipJson = escapeText(JSON.stringify(buildProcessTipPayload(host, legTip)));
    return (
      `<td class="paper-t0-col-process has-tip" data-t0-process-tip="${processTipJson}">` +
      (daySkipped && !host.t0_slot_focus
        ? `<span class="paper-t0-leg-empty">—</span>`
        : legProcessFlowHtml(host)) +
      `</td>`
    );
  };

  const first = hosts[0];
  const dateInner = hasScan
    ? `<button type="button" class="paper-t0-day-debug-toggle" data-t0-day-id="${escapeText(
        dayKey
      )}" title="展开 11:00 前每根扫描（OLHC · Ĉ_τ · R̂_τ · y_τ · y_path · y_complexity · y_tpd）" aria-expanded="false">${escapeText(
        dayText
      )}<span class="paper-t0-day-debug-caret" aria-hidden="true">▾</span></button>`
    : escapeText(dayText);
  let html =
    `<tr class="${daySkipped ? "is-skipped" : ""}" data-code="${escapeText(code)}" data-t0-day-id="${escapeText(
      dayKey
    )}">` +
    (showStock ? applyTdRowspan(stockCellHtml(d, fallback), span) : "") +
    applyTdRowspan(`<td class="paper-t0-col-date">${dateInner}</td>`, span) +
    tradeSlotPxCells(first, rules) +
    slotYCells(first, d) +
    slotProcessCell(first) +
    applyTdRowspan(
      `<td class="num paper-t0-col-ret ${paperMetricClass(retCell.pct)}${retCell.heat || ""}" title="${escapeText(
        retCell.tip
      )}">${escapeText(retCell.text)}</td>`,
      span
    ) +
    applyTdRowspan(
      `<td class="num paper-t0-col-pnl ${paperMetricClass(d.pnl)}">${escapeText(String(d.pnl ?? 0))}</td>`,
      span
    ) +
    applyTdRowspan(
      `<td class="num paper-t0-col-exp ${paperMetricClass(d.exposure_pnl)}" title="${escapeText(
        expCell.tip
      )}">${escapeText(expCell.text)}</td>`,
      span
    ) +
    (showReason
      ? applyTdRowspan(
          `<td class="paper-t0-col-reason" title="${escapeText(reason)}">${escapeText(
            reason || (daySkipped ? "跳过" : "")
          )}</td>`,
          span
        )
      : "") +
    (showDelete ? applyTdRowspan(`<td class="paper-t0-col-act">${delBtn}</td>`, span) : "") +
    `</tr>`;

  for (let i = 1; i < hosts.length; i++) {
    const host = hosts[i];
    html +=
      `<tr class="paper-t0-slot-cont${host.t0_slot_skipped ? " is-skipped" : ""}"` +
      ` data-code="${escapeText(code)}" data-t0-day-id="${escapeText(dayKey)}">` +
      tradeSlotPxCells(host, rules) +
      slotYCells(host, d) +
      slotProcessCell(host) +
      `</tr>`;
  }
  if (hasScan) {
    html += buildCloseBandScanExpandRow(d, dayKey, tradeTableColCount(ctx), rules);
  }
  return html;
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
    liveScoresByCode = null,
  } = opts || {};
  if (!days || !days.length) return caption || "";
  const tableDays = days;
  const liveByCode =
    liveScoresByCode ||
    (data && typeof data.liveScoresByCode === "object" ? data.liveScoresByCode : null);
  const showStock = shouldShowStockColumn(data, tableDays);
  const showTime = daysHaveIntradayTime(tableDays);
  const rules = (data && data.rules) || {};
  const scoreTip = yTauMapScoreTip();
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };

  const pairHint = showRealized ? " · 显示：预估值(真实值)" : "";
  const slotYHint = rules.t0_slots_enabled ? ` · 本轮 ŷ${pairHint}` : pairHint;
  const tauGateHint =
    " · 收盘带宽：每根前缀 ŷ_τ 估 Ĉ，再与本根 5m 收价 C 比 r";
  const head =
    (showStock ? `<th scope="col" class="paper-t0-col-stock">股票</th>` : "") +
    `<th scope="col" class="paper-t0-col-date" title="有可展开扫描数据时点击日展开 11:00 前调试">日</th>` +
    `<th scope="col" class="paper-t0-col-o num" title="本轮触发根 5m 开盘">O</th>` +
    `<th scope="col" class="paper-t0-col-l num" title="本轮触发根 5m 最低">L</th>` +
    `<th scope="col" class="paper-t0-col-h num" title="本轮触发根 5m 最高">H</th>` +
    `<th scope="col" class="paper-t0-col-c num" title="本轮触发根 5m 收盘">C</th>` +
    `<th scope="col" class="paper-t0-col-ctau num" title="ĉ_τ · 主显分钟映后（破带用）；悬停看日原">Ĉ_τ</th>` +
    `<th scope="col" class="paper-t0-col-rtau num" title="r̂_τ = (C/ĉ_τ−1)×100 · 相对 ĉ 的超额；入场阈=页面 R入场%（0–1.0；0=关；与带宽δ/τ先验漂移无关）">R̂_τ</th>` +
    `<th scope="col" class="paper-t0-col-tau num paper-t0-col-y paper-t0-col-y-tau" title="${escapeText(
      `${scoreTip}${slotYHint}${tauGateHint}`
    )}">y_τ</th>` +
    `<th scope="col" class="paper-t0-col-path num paper-t0-col-y paper-t0-col-y-path" title="${escapeText(
      `${Y_PATH_TITLE}${slotYHint}`
    )}">y_path</th>` +
    (showRealized
      ? `<th scope="col" class="paper-t0-col-cx num paper-t0-col-y paper-t0-col-y-cx" title="${escapeText(
          `${Y_COMPLEXITY_TITLE}${slotYHint}`
        )}">y_complexity</th>` +
        `<th scope="col" class="paper-t0-col-tpd num paper-t0-col-y paper-t0-col-y-tpd" title="${escapeText(
          `${Y_TPD_TITLE}${slotYHint}`
        )}">y_tpd</th>`
      : "") +
    `<th scope="col" class="paper-t0-col-process" title="本轮时钟 + 成交腿；悬停看全日 K 线">过程</th>` +
    `<th scope="col" class="paper-t0-col-ret num" title="(PnL+敞口)/动仓名义">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `<th scope="col" class="paper-t0-col-exp num" title="${escapeText(exposureColTitle(data))}">敞口</th>` +
    (showReason ? `<th scope="col" class="paper-t0-col-reason">说明</th>` : "") +
    (showDelete ? `<th scope="col" class="paper-t0-col-act">操作</th>` : "");

  const splitSlots = !showDelete;
  const viewDays = preserveOrder
    ? tableDays.slice(0, maxRows)
    : tableDays.slice(-maxRows).reverse();
  const rowCtx = {
    data,
    fallback,
    rules,
    liveByCode,
    showStock,
    showRealized,
    showReason,
    showDelete,
    showTime,
    splitSlots,
  };
  const rows = viewDays.map((d) => renderTradeDayHtml(d, rowCtx)).join("");

  const moreHint =
    tableDays.length > maxRows
      ? `<p class="quant-trades-caption paper-t0-table-more">表内最近 ${maxRows} 日 · 样本共 ${tableDays.length} 日 · 可滚动查看</p>`
      : "";

  const tableBlock =
    `<div class="quant-weight-table-wrap paper-t0-trades-wrap">` +
    `<table class="${TABLE_CLASS}">` +
    tradeColgroup(showStock, showReason, showDelete, showRealized) +
    `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
    `</div>` +
    moreHint;

  return wrapTradesFullscreenPanel(tableBlock, caption);
}

