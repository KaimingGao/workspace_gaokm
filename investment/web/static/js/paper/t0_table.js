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
  resolveNowcastScore,
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
} from "./fmt.js?v=p1734";
import {
  adaptiveSizingDayTip,
  normalizeYTauMap,
  yTauMapScoreTip,
} from "./execution_ui.js?v=p1814";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p1734";
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
  prefix_vs_path: "前缀>|ŷ_path|×裕度(旧)",
  tau_entry_price: "入场价vs开盘×ŷ_τ",
  tau_exit_price: "出场价vs开盘×ŷ_τ",
  y_trade_weak: "y_trade幅度不足",
  eod_tau_disagree: "y_eod↔y_τ异号",
  trade_tau_disagree: "y_trade↔y_τ异号",
  trade_tau_sign: "异号跳过",
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
    "|ŷ_eod|>y_eod_strong（默认 0.2%）且 ŷ_eod 与定方向 ŷ_τ（OC）异号：隔夜主轴与盘中方向冲突，跳过。",
  trade_tau_disagree:
    "|ŷ_trade|>y_trade_strong（默认 0.2%）且 ŷ_trade 与定方向 ŷ_τ（OC）异号：融合幅度与盘中方向冲突，跳过。",
  gap_tier_skip:
    "大缺口档位与拟做方向冲突（如大高开仍想正 T），规则直接跳过。",
  path_abandon:
    "固定前缀齐窗后仍未确认（后半阴阳占比等），放弃当日/本轮做 T。",
  prefix_segment:
    "固定前缀未齐或后半阴阳占比未达标：正 T 待后半上涨、反 T 待后半下跌（Worker 可重试）。",
  prefix_vs_path:
    "历史口径：前缀窗 (H−L)/ref% 超过 |ŷ_path|×裕度（空间用尽）。现行已改为确认根入场价 vs open×(1+(clamp(ŷ_τ×裕度,min,max)+价偏)/100)。",
  tau_entry_price:
    "确认根第一腿：正T买价须 < open×(1+(clamp(ŷ_τ×裕度,min,max)+价偏)/100)；反T卖价须 > 同式。可调裕度/价偏或关闸。",
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
    "分钟路径规则否决（veto），与预测头 dual_y 不一致类不同。",
  trigger_miss:
    "有方向且确认根已过，但第二腿全天未触达卖出/买回触发价。",
  other:
    "未归入上述类型的其它跳过原因。",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table paper-t0-trades-table";

/** 列轨宽度：colgroup inline width + CSS 同源，避免 fixed 表头/体错位 */
const T0_TRADE_COL_W = {
  stock: "10.5rem",
  date: "104px",
  eod: "82px",
  tau: "108px",
  path: "120px",
  trade: "108px",
  on: "82px",
  nc: "82px",
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
  const defaults = ["10:00", "10:30", "11:00", "11:30"];
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

/** 本轮 ŷ_τ / ŷ_path / ŷ_trade：只读槽位快照，不回退日级分。 */
function slotYhat(r) {
  const sc = r && r.scores && typeof r.scores === "object" ? r.scores : {};
  const ft =
    r && r.direction_features && typeof r.direction_features === "object"
      ? r.direction_features
      : {};
  let yTau =
    slotYNum(sc, ["y_tau", "y_tau_oc", "predicted_score_tau", "score_rem"]) ??
    slotYNum(ft, ["y_tau", "y_tau_oc"]);
  let yPath =
    slotYNum(sc, ["y_path", "predicted_score_path"]) ?? slotYNum(ft, ["y_path"]);
  let yTrade =
    slotYNum(sc, ["y_trade", "predicted_score_blend", "decision_score"]) ??
    slotYNum(ft, ["y_trade"]);
  const reason = String((r && r.reason) || "");
  if (yTau == null) {
    const m = reason.match(/y_τ\s*=\s*(-?[\d.]+)/);
    if (m) yTau = Number(m[1]);
  }
  if (yTrade == null) {
    const m =
      reason.match(/\|y_trade\|\s*=\s*(-?[\d.]+)/) ||
      reason.match(/y_trade\s*=\s*(-?[\d.]+)/);
    if (m) yTrade = Number(m[1]);
  }
  return { y_tau: yTau, y_path: yPath, y_trade: yTrade };
}

function slotDayRow(d, r, rows) {
  const sid = String(r.id || "");
  const filled = slotFilled(r);
  const hm = slotClock(r, rows);
  const slotTrades = (Array.isArray(d.trades) ? d.trades : []).filter(
    (t) => t && String(t.t0_slot || "") === sid
  );
  const yhat = slotYhat(r);
  const slotScores = r.scores && typeof r.scores === "object" ? { ...r.scores } : {};
  const slotFeats =
    r.direction_features && typeof r.direction_features === "object"
      ? { ...r.direction_features }
      : {};
  if (yhat.y_tau != null && slotScores.y_tau == null) slotScores.y_tau = yhat.y_tau;
  if (yhat.y_path != null && slotScores.y_path == null) slotScores.y_path = yhat.y_path;
  if (yhat.y_trade != null && slotScores.y_trade == null) slotScores.y_trade = yhat.y_trade;
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
    y_trade: yhat.y_trade,
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
    (key === "y_tau" || key === "y_path" || key === "y_trade")
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

function pickTradeRealized(d) {
  const pr = pickEodRealized(d);
  const pred = pickScoreNum(d, "y_trade");
  const agree = _signAgree(pred, pr.n);
  return {
    text: pr.text,
    n: pr.n,
    tip:
      "涨跌 · close[T]/close[T−1]−1（与 ŷ_trade 同目标）" +
      (agree === true ? " · 与 ŷ_trade 同号" : agree === false ? " · 与 ŷ_trade 异号" : ""),
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

/** y_eod / y_τ：回测配对真实值；实时做 T 只显示 ŷ。 */
function yPctMergedCellHtml(kind, d, fallback, rules, scoreDetailJson, titleExtra, showRealized = true, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const isEod = kind === "eod";
  const pred = isEod ? resolveEodScore(it) : resolveTauScore(it);
  let predNum = pred;
  let predTxt = pred != null ? fmtTableScore(it, pred) : "—";
  if (!isEod && predTxt === "—" && !d?.t0_slot_focus) {
    const ds = d.direction_score;
    if (ds != null && ds !== "" && Number.isFinite(Number(ds))) {
      predNum = Number(ds);
      predTxt = fmtTableScore(it, predNum);
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
    predNum != null || predTxt !== "—" ? yTitle : `暂无 ${isEod ? "ŷ_EOD" : "ŷ_τ"}`,
  ];
  if (it._scores_live_overlay) {
    tipParts.push("缺快照·已用持仓分补洞");
    if (isEod && it._decision_y_eod != null && Number.isFinite(Number(it._decision_y_eod))) {
      tipParts.push(`决策时快照 ${Number(it._decision_y_eod).toFixed(3)}%`);
    }
  }
  if (showRealized) tipParts.push(pr.n != null ? pr.tip : realTitle);
  if (titleExtra) tipParts.push(String(titleExtra));
  if (showRealized) {
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, predNum, pr, showRealized);
  const colKey = kind;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="${escapeText(kind)}" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

/** 成交日 → tip + resolve 载荷。
 * ``liveByCode``：仅补洞（日快照缺字段时）；**绝不覆盖**已有决策分。
 * 回测/预演多日明细若盖持仓实时分，会出现「多日 ŷ 相同、负τ却正T」。
 */
function t0DayScoreItem(d, fallback = {}, rules = {}, liveByCode = null) {
  const slot = focusedSlotRow(d);
  const slotOnly = !!d?.t0_slot_focus;
  const scores = slotOnly
    ? { ...((slot && slot.scores && typeof slot.scores === "object" ? slot.scores : {}) || {}) }
    : {
        ...((d.scores && typeof d.scores === "object" ? d.scores : {}) || {}),
        ...((slot && slot.scores && typeof slot.scores === "object" ? slot.scores : {}) || {}),
      };
  const feats = slotOnly
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
  const yEod = pickScoreNum(d, "y_eod");
  const yTau = pickScoreNum(d, "y_tau");
  const yTrade = pickScoreNum(d, "y_trade");
  const yOn = pickScoreNum(d, "y_on");
  const yNc = pickScoreNum(d, "y_nowcast");
  const yPath = pickScoreNum(d, "y_path");
  const code = String(
    d.stock_code || fallback.stock_code || scores.stock_code || ""
  ).trim();
  const live =
    liveByCode && code && typeof liveByCode[code] === "object"
      ? liveByCode[code]
      : null;
  const liveEod = slotOnly ? null : live ? resolveEodScore(live) : null;
  const liveTau = slotOnly ? null : live ? resolveTauScore(live) : null;
  const liveTrade = slotOnly ? null : live ? resolveTradeScore(live) : null;
  const liveOn = slotOnly ? null : live ? resolveOnScore(live) : null;
  const liveNc = slotOnly ? null : live ? resolveNowcastScore(live) : null;
  const livePath = slotOnly ? null : live ? resolvePathScore(live) : null;
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
    !slotOnly &&
    live &&
    ((dayEod == null && liveEod != null) ||
      (dayTau == null && liveTau != null) ||
      (dayTrade == null && liveTrade != null) ||
      (dayPath == null && livePath != null));
  return {
    ...scores,
    stock_code: code || null,
    stock_name: d.stock_name || fallback.stock_name || scores.stock_name || null,
    predicted_score_eod: dayEod ?? liveEod,
    predicted_score: dayEod ?? scores.predicted_score ?? liveEod,
    predicted_score_tau: slotOnly ? yTau : dayTau ?? liveTau,
    score_rem: slotOnly ? yTau : dayTau ?? scores.score_rem ?? liveTau,
    y_tau: slotOnly ? yTau : scores.y_tau ?? yTau,
    y_tau_oc: slotOnly ? (dayTauOc ?? yTau) : dayTauOc,
    predicted_score_tau_oc: slotOnly ? (dayTauOc ?? yTau) : dayTauOc,
    predicted_score_blend: slotOnly ? yTrade : dayTrade ?? liveTrade,
    y_trade: slotOnly ? yTrade : scores.y_trade ?? yTrade,
    decision_score: slotOnly ? yTrade : dayTrade ?? scores.decision_score ?? liveTrade,
    score: slotOnly ? yTrade : dayTrade ?? scores.score ?? liveTrade,
    predicted_score_on: dayOn ?? liveOn,
    predicted_score_nowcast: dayNc ?? liveNc,
    predicted_score_path: slotOnly ? yPath : dayPath ?? livePath,
    y_path: slotOnly ? yPath : dayPath ?? livePath,
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

/** 表列 y_*：与 holdings_table / watching 同源 resolve。 */
function t0FmtYScore(d, kind, fallback = {}, rules = {}, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
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

function resolveNowcastCcValue(d, fallback, rules, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const cc = resolveNowcastCcScore(it);
  if (cc != null && Number.isFinite(Number(cc))) return Number(cc);
  return pickScoreNum(d, "y_nowcast");
}

function resolveNowcastOcValue(d, fallback, rules, liveByCode = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const feats =
    (d.direction_features && typeof d.direction_features === "object"
      ? d.direction_features
      : null) ||
    (d.features && typeof d.features === "object" ? d.features : {}) ||
    {};
  const cc = resolveNowcastCcValue(d, fallback, rules, liveByCode);
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

function fmtNowcastOcCell(d, fallback, rules, scoreDetailJson, liveByCode = null) {
  const ocOn = rules.y_nowcast_oc_gate === true;
  const { oc, cc, gap } = resolveNowcastOcValue(d, fallback, rules, liveByCode);
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

function t0YScoreCell(tip, text, detailJson, title, extraCls = "", { html = false } = {}) {
  const colKey =
    tip === "nowcast"
      ? "nc"
      : tip === "nc_oc"
        ? "nc-oc"
        : tip;
  return (
    `<td class="num paper-t0-col-${escapeText(colKey)} paper-t0-col-y paper-t0-col-y-${escapeText(
      colKey
    )} paper-t0-y-score has-tip${extraCls}" data-score-tip="${escapeText(
      tip
    )}" data-score-detail="${detailJson}" title="${escapeText(title)}">${
      html ? text : escapeText(text)
    }</td>`
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
      yTau: slotYhat(r).y_tau,
      yPath: slotYhat(r).y_path,
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
  return bits.length ? ` ${bits.join("/")}` : "";
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
    return { text: "—", tip: "缺动仓股数或成交价，无法算收益%" };
  }
  const sign = pct > 0 ? "+" : "";
  return {
    text: `${sign}${pct.toFixed(2)}%`,
    tip: `(PnL ${d.pnl ?? 0} + 敞口 ${d.exposure_pnl ?? 0}) / 动仓名义`,
    pct,
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

function fmtOhlcTd(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || !(v > 0)) {
    return `<td class="num paper-t0-day-yhat-ohlc is-na">—</td>`;
  }
  return `<td class="num paper-t0-day-yhat-ohlc">${escapeText(fmtBarPx(v))}</td>`;
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
  return {
    dir,
    bars,
    slots: !slotFocus && (filledN > 1 || dirRaw === "mixed"),
    slot_hm: slotHm,
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
    ? `悬停看 O/H/L/C · 买/卖=本轮成交 · ${slotHm} 决策钟 · L/H=本轮前缀极值价`
    : p.slots
      ? "悬停看 O/H/L/C · 买/卖=各轮成交 · K 线为当日 5m（多轮共用）"
      : "悬停看 O/H/L/C · 买/卖=成交腿 · L/H=前缀极值价";
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

function dayLevelYNum(d, keys) {
  const sc = d && d.scores && typeof d.scores === "object" ? d.scores : {};
  const ft =
    d && d.direction_features && typeof d.direction_features === "object"
      ? d.direction_features
      : d && d.features && typeof d.features === "object"
        ? d.features
        : {};
  return slotYNum(sc, keys) ?? slotYNum(ft, keys);
}

function daySlotScoreRows(d) {
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  // 执行末轮 11:30；旧六轮结果里的 13:00/14:00 不再展示（午后不开 leg1）
  const lastLeg1Hm = "11:30";
  if (rows.length) {
    return rows
      .map((r) => {
        const y = slotYhat(r);
        const skipTxt = String(r.reason || "").trim();
        return {
          hm: slotClock(r, rows),
          dir: slotDirShort(r.direction),
          y_tau: y.y_tau,
          y_path: y.y_path,
          y_trade: y.y_trade,
          filled: slotFilled(r),
          skip: r.skipped || !slotFilled(r) ? skipTxt : "",
        };
      })
      .filter((r) => {
        const hm = String(r.hm || "").trim().slice(0, 5);
        return !hm || hm <= lastLeg1Hm;
      });
  }
  return [
    {
      hm: String(d?.t0_slot_hm || "").trim(),
      dir: slotDirShort(d && d.direction),
      y_tau: dayLevelYNum(d, ["y_tau", "y_tau_oc", "predicted_score_tau"]),
      y_path: dayLevelYNum(d, ["y_path", "predicted_score_path"]),
      y_trade: dayLevelYNum(d, ["y_trade", "predicted_score_blend"]),
      filled: !d?.skipped,
      skip: d?.skipped ? String(d.reason || "跳过").trim() : "",
    },
  ];
}

export function buildDayDetailPayload(d, { showRealized = true } = {}) {
  const dirRaw = (d && (d.direction || d.direction_used)) || "";
  const dir =
    dirRaw === "buy_then_sell"
      ? "正T"
      : dirRaw === "sell_then_buy"
        ? "反T"
        : dirRaw === "mixed"
          ? "多轮"
          : "";
  const bars = compactTraceBars(d && d.forward_trace, {
    prefixBars: Number(d && d.prefix_bars) || 0,
    dir: dirRaw,
    markPrefix: false,
  });
  const slots = daySlotScoreRows(d).map((s) => {
    const px = slotOhlcFromBars(bars, s && s.hm);
    return { ...s, ...px };
  });
  const tauR = pickTauRealized(d);
  const pathR = pickPathRealized(d);
  const tradeR = pickTradeRealized(d);
  return {
    dir,
    slots,
    show_realized: !!showRealized,
    tau_realized: tauR.n,
    path_realized: pathR.n,
    trade_realized: tradeR.n,
  };
}

function fmtYhatPair(n, real, { path = false, showRealized = true } = {}) {
  const predOk = n != null && Number.isFinite(Number(n));
  const predTxt = predOk
    ? path
      ? fmtPathScore(n)
      : fmtScore(n, { digits: 2 })
    : "—";
  const realOk = real != null && Number.isFinite(Number(real));
  const pr = realOk
    ? { n: Number(real), text: path ? fmtPathScore(real) : fmtRealizedPct(real) }
    : { n: null };
  return {
    html: fmtPredRealizedHtml(predTxt, predOk ? Number(n) : null, pr, showRealized),
    hit: signHitFlag(n, real, path ? 1e-9 : 0.05),
  };
}

function signHitFlag(pred, real, eps = 0.05) {
  if (pred == null || real == null) return null;
  const p = Number(pred);
  const r = Number(real);
  if (!Number.isFinite(p) || !Number.isFinite(r)) return null;
  if (Math.abs(p) < eps || Math.abs(r) < eps) return null;
  return (p > 0) === (r > 0);
}

function fmtHitTd(hit) {
  if (hit === true) {
    return `<td class="paper-t0-day-yhat-hit is-hit">同</td>`;
  }
  if (hit === false) {
    return `<td class="paper-t0-day-yhat-hit is-miss">异</td>`;
  }
  return `<td class="paper-t0-day-yhat-hit is-na">—</td>`;
}

function fmtYhatTd(n, real, opts = {}) {
  const { html, hit } = fmtYhatPair(n, real, opts);
  return (
    `<td class="num paper-t0-day-yhat-score"><span class="paper-t0-y-combo">${html}</span></td>` +
    fmtHitTd(opts.showHit === false ? null : hit)
  );
}

function shortenSkipReason(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  s = s.replace(/^dual_y[：:]\s*/i, "");
  if (s.length > 48) s = `${s.slice(0, 47)}…`;
  return s;
}

export function buildDayDetailHtml(payload) {
  let p = payload;
  if (typeof p === "string") {
    try {
      p = JSON.parse(p);
    } catch (_) {
      p = {};
    }
  }
  p = p || {};
  const slots = Array.isArray(p.slots) ? p.slots : [];
  const showR = p.show_realized !== false;
  const nFill = slots.filter((s) => s && s.filled).length;
  const nSkip = slots.length - nFill;
  const body = slots
    .map((s) => {
      const filled = !!s.filled;
      const why = filled ? "" : shortenSkipReason(s.skip);
      const st = filled
        ? `<span class="paper-t0-day-status is-fill">成交</span>`
        : `<span class="paper-t0-day-status is-skip">跳过</span>`;
      const dirCls =
        s.dir === "正" ? "is-bts" : s.dir === "反" ? "is-stb" : "";
      return (
        `<tr class="${filled ? "is-filled" : "is-skip"}">` +
        `<td class="paper-t0-day-yhat-hm">${escapeText(s.hm || "—")}</td>` +
        `<td class="paper-t0-day-yhat-dir ${dirCls}">${escapeText(s.dir || "—")}</td>` +
        fmtOhlcTd(s.o) +
        fmtOhlcTd(s.l) +
        fmtOhlcTd(s.h) +
        fmtOhlcTd(s.c) +
        fmtYhatTd(s.y_tau, p.tau_realized, { showRealized: showR }) +
        fmtYhatTd(s.y_path, p.path_realized, { path: true, showRealized: showR }) +
        fmtYhatTd(s.y_trade, p.trade_realized, { showRealized: showR }) +
        `<td class="paper-t0-day-yhat-res">${st}</td>` +
        `<td class="paper-t0-day-yhat-st" title="${escapeText(s.skip || "")}">${escapeText(
          why || (filled ? "—" : "")
        )}</td>` +
        `</tr>`
      );
    })
    .join("");
  const table = slots.length
    ? `<table class="paper-t0-day-yhat">` +
      `<thead><tr>` +
      `<th rowspan="2">时钟</th>` +
      `<th rowspan="2">向</th>` +
      `<th colspan="4">5m</th>` +
      `<th colspan="2">y_τ</th>` +
      `<th colspan="2">y_path</th>` +
      `<th colspan="2">y_trade</th>` +
      `<th rowspan="2">结果</th>` +
      `<th rowspan="2">说明</th>` +
      `</tr><tr>` +
      `<th>O</th><th>L</th><th>H</th><th>C</th>` +
      `<th>分数(label)</th><th>命中</th>` +
      `<th>分数(label)</th><th>命中</th>` +
      `<th>分数(label)</th><th>命中</th>` +
      `</tr></thead><tbody>${body}</tbody></table>`
    : `<p class="paper-t0-day-detail-empty">无槽位预估</p>`;
  return (
    `<div class="paper-t0-day-detail-inner">` +
    `<header class="paper-t0-day-detail-head">` +
    `<span class="paper-t0-day-detail-title">各轮预估</span>` +
    (p.dir ? `<span class="paper-t0-day-detail-badge">${escapeText(p.dir)}</span>` : "") +
    `<span class="paper-t0-day-detail-count">成交 ${nFill} · 跳过 ${nSkip}</span>` +
    `</header>` +
    table +
    `</div>`
  );
}

function closeDayDetails(table, exceptTd) {
  if (!table) return;
  table.querySelectorAll("tr.paper-t0-day-detail").forEach((tr) => tr.remove());
  table.querySelectorAll("td.paper-t0-col-date[aria-expanded='true']").forEach((td) => {
    if (td !== exceptTd) td.setAttribute("aria-expanded", "false");
  });
}

function toggleDayDetail(dateTd) {
  if (!dateTd) return;
  const table = dateTd.closest("table");
  const hostTr = dateTd.closest("tr");
  if (!table || !hostTr) return;
  const dayId = hostTr.getAttribute("data-t0-day-id") || "";
  const open = dateTd.getAttribute("aria-expanded") === "true";
  closeDayDetails(table, dateTd);
  if (open) {
    dateTd.setAttribute("aria-expanded", "false");
    return;
  }
  let payload = {};
  try {
    payload = JSON.parse(dateTd.getAttribute("data-t0-day-detail") || "{}");
  } catch (_) {
    payload = {};
  }
  const rows = dayId
    ? [...table.querySelectorAll("tbody tr[data-t0-day-id]")].filter(
        (tr) =>
          tr.getAttribute("data-t0-day-id") === dayId &&
          !tr.classList.contains("paper-t0-day-detail")
      )
    : [hostTr];
  const last = rows.length ? rows[rows.length - 1] : hostTr;
  const span = table.querySelectorAll("thead tr:first-child th").length || 12;
  const detailTr = document.createElement("tr");
  detailTr.className = "paper-t0-day-detail";
  if (dayId) detailTr.setAttribute("data-t0-day-id", dayId);
  const td = document.createElement("td");
  td.colSpan = span;
  td.innerHTML = buildDayDetailHtml(payload);
  detailTr.appendChild(td);
  last.insertAdjacentElement("afterend", detailTr);
  dateTd.setAttribute("aria-expanded", "true");
}

let dayDetailBound = false;

/** 点击成交明细「日」列：展开各轮预估表。 */
export function bindT0DayDetailExpand() {
  if (dayDetailBound || typeof document === "undefined") return;
  dayDetailBound = true;
  document.addEventListener("click", (ev) => {
    const td = ev.target && ev.target.closest && ev.target.closest("td.paper-t0-col-date[data-t0-day-detail]");
    if (!td) return;
    ev.preventDefault();
    toggleDayDetail(td);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const td = ev.target && ev.target.closest && ev.target.closest("td.paper-t0-col-date[data-t0-day-detail]");
    if (!td) return;
    ev.preventDefault();
    toggleDayDetail(td);
  });
}

function tradeColgroup(showStock, showReason = false, showDelete = false) {
  let html = "<colgroup>";
  if (showStock) html += t0Col("paper-t0-col-stock", "stock");
  html +=
    t0Col("paper-t0-col-date", "date") +
    t0Col("paper-t0-col-on", "on") +
    t0Col("paper-t0-col-nc", "nc") +
    t0Col("paper-t0-col-eod", "eod") +
    t0Col("paper-t0-col-tau", "tau") +
    t0Col("paper-t0-col-path", "path") +
    t0Col("paper-t0-col-trade", "trade") +
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
  bindT0DayDetailExpand();
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
  const dayDetailJson = escapeText(
    watchingScoreDetail(t0DayScoreItem(d, fallback, rules, liveByCode))
  );
  const expCell = fmtExposureCell(d);
  const retCell = fmtDayReturnPct(d);
  const code = String(d.stock_code || fallback.stock_code || "").trim();
  const name = String(d.stock_name || fallback.stock_name || code).trim();
  const reason = String(d.reason || d.direction_reason || d.error || "").trim();
  const sess = data.sessionDate || data.session_date;
  const dayText = fmtTradeDate(d.date, sess);
  const delBtn =
    showDelete && code && !daySkipped
      ? `<button type="button" class="paper-t0-ledger-del" data-code="${escapeText(
          code
        )}" data-name="${escapeText(name)}" title="删除并冲正账本">删除</button>`
      : showDelete
        ? `<span class="paper-t0-leg-empty">—</span>`
        : "";

  const slotYCells = (host) => {
    const scoreDetailJson = escapeText(
      watchingScoreDetail(t0DayScoreItem(host, fallback, rules, liveByCode))
    );
    const slotYRealized = showRealized;
    const tradeTxt = t0FmtYScore(host, "trade", fallback, rules, liveByCode);
    const tradeIt = t0DayScoreItem(host, fallback, rules, liveByCode);
    const tradePred = resolveTradeScore(tradeIt);
    const tradePr = slotYRealized ? pickTradeRealized(host) : { n: null, tip: TRADE_TITLE };
    const tradeTip = [TRADE_TITLE];
    if (slotYRealized) {
      tradeTip.push(tradePr.n != null ? tradePr.tip : "涨跌真实值");
      if (tradePr.agree === true) tradeTip.push("预测与真实同号");
      else if (tradePr.agree === false) tradeTip.push("预测与真实异号");
    }
    return (
      yPctMergedCellHtml(
        "tau",
        host,
        fallback,
        rules,
        scoreDetailJson,
        host.direction_reason || d.direction_reason || null,
        slotYRealized,
        liveByCode
      ) +
      pathMergedCellHtml(host, fallback, rules, scoreDetailJson, slotYRealized, liveByCode) +
      t0YScoreCell(
        "trade",
        `<span class="paper-t0-y-combo">${fmtPredRealizedHtml(
          tradeTxt,
          tradePred,
          tradePr,
          slotYRealized
        )}</span>`,
        scoreDetailJson,
        tradeTip.join(" · "),
        ` paper-t0-y-merged${predRealizedAgreeCls(tradePr, slotYRealized)}`,
        { html: true }
      )
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
  const dayKey = `${code}|${dayText}`;
  const dayExpandJson = escapeText(JSON.stringify(buildDayDetailPayload(d, { showRealized })));
  let html =
    `<tr class="${daySkipped ? "is-skipped" : ""}" data-code="${escapeText(code)}" data-t0-day-id="${escapeText(
      dayKey
    )}">` +
    (showStock ? applyTdRowspan(stockCellHtml(d, fallback), span) : "") +
    applyTdRowspan(
      `<td class="paper-t0-col-date is-expandable" tabindex="0" role="button" aria-expanded="false" ` +
        `data-t0-day-detail="${dayExpandJson}" title="点击展开各轮预估">${escapeText(
          dayText
        )}</td>`,
      span
    ) +
    applyTdRowspan(
      t0YScoreCell(
        "on",
        t0FmtYScore(d, "on", fallback, rules, liveByCode),
        dayDetailJson,
        Y_ON_TITLE
      ),
      span
    ) +
    applyTdRowspan(
      t0YScoreCell(
        "nowcast",
        t0FmtYScore(d, "nowcast", fallback, rules, liveByCode),
        dayDetailJson,
        Y_NC_TITLE
      ),
      span
    ) +
    applyTdRowspan(
      yPctMergedCellHtml("eod", d, fallback, rules, dayDetailJson, null, showRealized, liveByCode),
      span
    ) +
    slotYCells(first) +
    slotProcessCell(first) +
    applyTdRowspan(
      `<td class="num paper-t0-col-ret ${paperMetricClass(retCell.pct)}" title="${escapeText(
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
      slotYCells(host) +
      slotProcessCell(host) +
      `</tr>`;
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
  const enter = yTauEnter(data);
  const tauMap = normalizeYTauMap(rules.y_tau_map);
  const scoreTip = yTauMapScoreTip(tauMap, enter);
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };

  const pairHint = showRealized ? " · 显示：预估值(真实值)" : "";
  const slotYHint = rules.t0_slots_enabled ? ` · 本轮 ŷ${pairHint}` : pairHint;
  const liveHint = liveByCode ? " · 缺快照时可用持仓分补洞（不覆盖决策分）" : "";
  const head =
    (showStock ? `<th scope="col" class="paper-t0-col-stock">股票</th>` : "") +
    `<th scope="col" class="paper-t0-col-date" title="点击日期展开各轮预估">日</th>` +
    `<th scope="col" class="paper-t0-col-on num paper-t0-col-y paper-t0-col-y-on" title="${escapeText(
      `${Y_ON_TITLE}`
    )}">y_on</th>` +
    `<th scope="col" class="paper-t0-col-nc num paper-t0-col-y paper-t0-col-y-nc" title="${escapeText(
      Y_NC_TITLE
    )}">y_nc</th>` +
    `<th scope="col" class="paper-t0-col-eod num paper-t0-col-y paper-t0-col-y-eod" title="${escapeText(
      `${Y_EOD_TITLE}${pairHint}${liveHint}`
    )}">y_eod</th>` +
    `<th scope="col" class="paper-t0-col-tau num paper-t0-col-y paper-t0-col-y-tau" title="${escapeText(
      `${scoreTip}${slotYHint}`
    )}">y_τ</th>` +
    `<th scope="col" class="paper-t0-col-path num paper-t0-col-y paper-t0-col-y-path" title="${escapeText(
      `${Y_PATH_TITLE}${slotYHint}`
    )}">y_path</th>` +
    `<th scope="col" class="paper-t0-col-trade num paper-t0-col-y paper-t0-col-y-trade" title="${escapeText(
      `y_trade 可交易性${slotYHint}`
    )}">y_trade</th>` +
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
    tradeColgroup(showStock, showReason, showDelete) +
    `<thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>` +
    `</div>` +
    moreHint;

  return wrapTradesFullscreenPanel(tableBlock, caption);
}

