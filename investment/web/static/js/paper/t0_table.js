/** 做 T 回测表格：统一列定义，避免表头/数据错位。 */

import {
  escapeText,
  paperMetricClass,
  paperProbClass,
  fmtHorizonProb,
  fmtTableScore,
  compoundPct,
  resolveYTradeScore,
  resolveEodScore,
  resolveTauScore,
  resolveOnScore,
  Y_TAU_TITLE,
  TAU_REALIZED_TITLE,
  Y_HL_TITLE,
  PATH_REALIZED_TITLE,
  fmtPathScore,
  Y_T30_TITLE,
  T30_REALIZED_TITLE,
  Y_T45_TITLE,
  T45_REALIZED_TITLE,
  Y_T60_TITLE,
  T60_REALIZED_TITLE,
  Y_T75_TITLE,
  T75_REALIZED_TITLE,
  Y_T90_TITLE,
  T90_REALIZED_TITLE,
  Y_TW_TITLE,
  TW_REALIZED_TITLE,
  blendYtw,
  fmtYtwVote,
} from "./fmt.js?v=p2512";
import { adaptiveSizingDayTip } from "./execution_ui.js?v=p2568";
import { watchingScoreDetail } from "../quant/watching_render.js?v=p2531";
import {
  fitTierBadgeForCode,
  ensureFitTierMap,
  stampFitTierBadges,
} from "../quant/fit_tier_ui.js?v=p2261";

const Y_T30_HAT_KEYS = ["y_τ30", "y_t30", "predicted_score_t30", "y_t30_hat"];
const Y_HL_HAT_KEYS = ["y_hl", "predicted_score_hl", "y_path", "predicted_score_path"];
const Y_T60_HAT_KEYS = ["y_τ60", "y_t60", "predicted_score_t60", "y_t60_hat"];
const Y_T45_HAT_KEYS = ["y_τ45", "y_t45", "predicted_score_t45", "y_t45_hat"];
const Y_T75_HAT_KEYS = ["y_τ75", "y_t75", "predicted_score_t75", "y_t75_hat"];
const Y_T90_HAT_KEYS = ["y_τ90", "y_t90", "predicted_score_t90", "y_t90_hat"];

function finiteYhatNum(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** ŷ_τc = Ridge 模型预估 price→close。旧簿 remaining 盖进 y_τc 时改读 Ridge。 */
function pickYtcModel(r) {
  if (!r || typeof r !== "object") return null;
  const from = (obj) => {
    if (!obj || typeof obj !== "object") return null;
    const rem =
      finiteYhatNum(obj.remaining_oc) ??
      finiteYhatNum(obj.r_hat) ??
      finiteYhatNum(obj.residual);
    const src = String(obj.y_τc_source || "");
    const ridge =
      finiteYhatNum(obj.y_τc_ridge) ??
      finiteYhatNum(obj.predicted_score_r) ??
      finiteYhatNum(obj.y_r_hat) ??
      finiteYhatNum(obj.y_r);
    const ytc =
      finiteYhatNum(obj["y_τc"]) ??
      finiteYhatNum(obj.predicted_score_τc) ??
      finiteYhatNum(obj.y_tc) ??
      finiteYhatNum(obj.predicted_score_tc) ??
      finiteYhatNum(obj.y_to) ??
      finiteYhatNum(obj.predicted_score_to) ??
      finiteYhatNum(obj.y_pc) ??
      finiteYhatNum(obj.predicted_score_pc);
    if (src === "remaining_oc") return ridge;
    if (
      ytc != null &&
      rem != null &&
      Math.abs(ytc - rem) < 1e-4 &&
      ridge != null &&
      Math.abs(ridge - rem) > 1e-4
    ) {
      return ridge;
    }
    return ytc ?? ridge;
  };
  return from(r) ?? from(r.scores);
}

export const SKIP_CAT_LABEL = {
  missing_scores: "缺ŷ快照",
  missing_minute: "缺分钟线",
  y_eod_flat: "y_eod未过门槛",
  y_tau_flat: "y_τ横盘",
  y_tc_flat: "ŷ_τc横盘",
  y_t30_flat: "ŷ_τ30横盘",
  y_t45_flat: "ŷ_τ45横盘",
  y_t60_flat: "ŷ_τ60横盘",
  y_t75_flat: "ŷ_τ75横盘",
  y_t90_flat: "ŷ_τ90横盘",
  r_tau_flat: "R̂_τ超额不足",
  y_tau_weak: "y_τ弱信号",
  y_path_flat: "y_hl横盘",
  y_path_disagree: "y_τ↔y_hl异号",
  y_tc_disagree: "ŷ_τc旁路逆带",
  y_t30_disagree: "ŷ_τ30旁路逆带",
  y_t45_disagree: "ŷ_τ45旁路逆带",
  y_tw_disagree: "ŷ_τw入场逆带",
  y_tw_flat: "ŷ_τw未过入场",
  bar_shape: "收在极值未开腿",
  bar_oc: "K线阴阳逆选向(旧)",
  ytw_prefix: "前序ŷ_τw未达标",
  y_t60_disagree: "ŷ_τ60旁路逆带",
  y_t75_disagree: "ŷ_τ75旁路逆带",
  y_t90_disagree: "ŷ_τ90旁路逆带",
  y_complexity_high: "y_cx太折",
  y_cx_high: "y_cx太折",
  y_tpd_high: "y_tpd反转过密",
  gap_tier_skip: "大缺口反向跳过",
  path_abandon: "前缀无空间放弃(旧)",
  prefix_segment: "固定前缀待确认(旧)",
  prefix_vs_path: "前缀>|ŷ_hl|×裕度(旧)",
  close_band: "R̂_τ 未开轮",
  price_space_mismatch: "日分价空间错位",
  tau_entry_price: "入场价vs开盘×ŷ_oc(旧)",
  tau_exit_price: "出场价vs开盘×ŷ_oc",
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
    "当日缺 ŷ 快照（ŷ_oc / ŷ_trade 等算不出），无法估 ĉ / 开轮。",
  missing_minute:
    "缺当日分钟线，无法模拟触达与成交路径。",
  y_tau_flat:
    "入场：已下线。生产不考虑 |ŷ_oc| 入场档；历史回放可能仍出现。",
  y_tc_flat:
    "入场：已下线。生产不考虑 |ŷ_τc| 入场档；历史回放可能仍出现。",
  y_t30_flat:
    "已下线：个股 ŷ_τ30 入场下限。生产不考虑单独阈值；历史回放可能仍出现。",
  y_t45_flat:
    "已下线：个股 ŷ_τ45 入场下限。生产不考虑单独阈值；历史回放可能仍出现。",
  y_t60_flat:
    "已下线：个股 ŷ_τ60 入场下限。生产不考虑单独阈值；历史回放可能仍出现。",
  y_t75_flat:
    "已下线：个股 ŷ_τ75 入场下限。生产不考虑单独阈值；历史回放可能仍出现。",
  y_t90_flat:
    "已下线：个股 ŷ_τ90 入场下限。生产不考虑单独阈值；历史回放可能仍出现。",
  r_tau_flat:
    "历史跳过类别：旧 |超额 r| 入场闸（r_tau_enter）；新跑批不再产生。",
  y_eod_flat:
    "历史口径：|ŷ_eod| 低于入场门槛。v6 选腿不经 eod 入场闸；强异号仍可跳过。",
  y_tau_weak:
    "历史跳过类别（旧双闸弱信号区）；新跑批不再产生。",
  y_path_flat:
    "入场：已下线。生产不考虑 |ŷ_hl| 入场档；历史回放可能仍出现。缺 y_hl 不拦。",
  y_path_disagree:
    "ŷ_hl 同号闸：|y_hl| 超 HL强%（y_hl_strong）且与 ŷ_oc 异号则跳过；0=任意非零须同号。",
  y_tc_disagree:
    "ŷ_τc 旁路：破带后剩余窗须向 C_τ 回归（反T remaining<0，正T>0）。|ŷ_τc| 超 τc强% 且逆带则跳过；0=任意有符号须同号，100=关。不改 C_τ。",
  y_t30_disagree:
    "已下线：个股 ŷ_τ30 旁路闸。生产只走 ŷ_τw 投票；历史回放可能仍出现。",
  y_t45_disagree:
    "已下线：个股 ŷ_τ45 旁路闸。生产只走 ŷ_τw 投票；历史回放可能仍出现。",
  y_tw_flat:
    "ŷ_τw 未过 Y_τw入场：正T须 ŷ_τw≥入场，反T须 ŷ_τw≤−入场。全弃权计 0 票；入场=0 时 0 票可通过。缺头不开腿。",
  y_tw_disagree:
    "ŷ_τw 未过对应方向 Y_τw入场（正T须 ŷ_τw≥入场，反T须 ŷ_τw≤−入场）。",
  bar_oc:
    "历史口径：旧阴阳门槛（正T须收>开，反T须收<开）。已由 ŷ_oc 破带选向替代；新跑批不应再产生。",
  ytw_prefix:
    "历史口径：旧前序ŷ_τw确认。现 ŷ_τw=五窗相对中位点符号和，选向看 ŷ_oc 破带；新跑批不应再产生。",
  y_t60_disagree:
    "已下线：个股 ŷ_τ60 旁路闸。生产只走 ŷ_τw 投票；历史回放可能仍出现。",
  y_t75_disagree:
    "已下线：个股 ŷ_τ75 旁路闸。生产只走 ŷ_τw 投票；历史回放可能仍出现。",
  y_t90_disagree:
    "已下线：个股 ŷ_τ90 旁路闸。生产只走 ŷ_τw 投票；历史回放可能仍出现。",
  y_complexity_high:
    "历史跳过类别：旧 ŷ_cx 太折上限；新跑批不再产生。",
  y_cx_high:
    "历史跳过类别：旧 ŷ_cx 太折上限；新跑批不再产生。",
  y_tpd_high:
    "历史跳过类别：旧 ŷ_tpd 反转过密上限；新跑批不再产生。",
  eod_tau_disagree:
    "历史口径：强 ŷ_eod 与 ŷ_oc 异号跳过。v6 选腿已下线该闸（仅 y_τ / ĉ_τ 入场）。",
  trade_tau_disagree:
    "历史口径：强 ŷ_trade 与 ŷ_oc 异号跳过。v6 选腿已下线该闸（仅 y_τ / ĉ_τ 入场）。",
    tau_leg1_prior:
    "已下线：旧 oc先验平移带宽。现选向看 C 相对 C_τ 的 ±δ 破带。",
  gap_tier_skip:
    "大缺口档位与拟做方向冲突（如大高开仍想正 T），规则直接跳过。",
  path_abandon:
    "历史口径：固定前缀齐窗后仍未确认。v6 已改为收盘带宽选腿，新跑批不应再产生。",
  prefix_segment:
    "历史口径：固定前缀未齐或阴阳占比未达标。v6 已下线，新跑批不应再产生。",
  prefix_vs_path:
    "历史口径：前缀窗 (H−L)/ref% 超过 |ŷ_hl|×裕度（空间用尽）。v6 已改为收盘带宽选腿，新跑批不应再产生。",
  close_band:
    "该 5m 未开第一腿：未破 C_τ 带、缺 ŷ_τw，或 ŷ_τw 未过正/反T入场。",
  bar_shape:
    "已下线：第一腿 close≥high / close≤low 收贴端。历史账本可能残留「正T须收在最高」/「反T须收在最低」或旧 C>L/C<H 口径。",
  price_space_mismatch:
    "日分价闸：昨收差 |日昨/分昨−1| 超 t0_price_space_prev_dev_pct（默认 5%，0=关）；开盘差已下线。错价则跳过。",
  tau_entry_price:
    "历史口径：确认根买/卖价相对 open×(1+ŷ_oc) 的入场价闸。v6 第一腿按确认根收盘成交，该闸已下线；新跑批不应再产生。",
  tau_exit_price:
    "第二腿：正T卖价须 > open×(1+(clamp(ŷ_oc×裕度,min,max)+价偏)/100)；反T买价须 < 同式。止损/收盘强平不受闸。",
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
    "已开第一腿，但第二腿全天未触达卖出/买回触发价。",
  other:
    "未归入上述类型的其它跳过原因。",
};

const TABLE_CLASS = "quant-weight-table paper-t0-table paper-t0-trades-table";

/** 列轨宽度：colgroup inline width + CSS `.paper-t0-col-*` 同源，避免 fixed 表头/体错位 */
const T0_TRADE_COL_W = {
  stock: "8.75rem",
  date: "104px",
  tau: "126px",
  o: "64px",
  l: "64px",
  h: "64px",
  c: "64px",
  ctau: "76px",
  bandLo: "64px",
  bandUp: "64px",
  ytw: "126px",
  yhl: "126px",
  yt30: "126px",
  yt45: "126px",
  yt60: "126px",
  yt75: "126px",
  yt90: "126px",
  process: "280px",
  retPct: "80px",
  pnl: "80px",
  exp: "80px",
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
    `<td class="rebalance-stock paper-t0-col-stock watching-stock"${
      code ? ` data-code="${escapeText(code)}"` : ""
    } title="${escapeText(title || fullName)}">` +
    `<span class="watching-name-row">` +
    `<span class="watching-name-text" title="${escapeText(fullName)}" data-full-name="${escapeText(
      fullName
    )}">${escapeText(display)}</span>` +
    fitTierBadgeForCode(code, { escapeHtml: escapeText }) +
    `</span>` +
    (code
      ? `<span class="watching-code-sub">${escapeText(code)}</span>`
      : "") +
    `</td>`
  );
}

export function stampStockFitTiers(root) {
  void ensureFitTierMap().then(() => stampFitTierBadges(root));
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
export function expandSlotTradeDays(days, { splitSlots = true } = {}) {
  if (!splitSlots) return days || [];
  const out = [];
  for (const d of days || []) {
    out.push(...tradeDaySlotHosts(d, true));
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

/** 本轮 ŷ_oc / ŷ_τc / ŷ_τ30/60/90 / ŷ_trade / ŷ_nowcast：只读槽位快照，不回退日级分。 */
function slotYhat(r, dayHost) {
  const sc = r && r.scores && typeof r.scores === "object" ? r.scores : {};
  const ft =
    r && r.direction_features && typeof r.direction_features === "object"
      ? r.direction_features
      : {};
  const gateYt = slotGateYTau(r, dayHost);
  let yTau =
    slotYNum(sc, ["y_tau", "y_tau_oc", "predicted_score_tau", "score_rem"]) ??
    slotYNum(ft, ["y_tau", "y_tau_oc"]);
  let yR = pickYtcModel(sc) ?? pickYtcModel(ft);
  let yT30 = slotYNum(sc, Y_T30_HAT_KEYS) ?? slotYNum(ft, Y_T30_HAT_KEYS);
  let yHl = slotYNum(sc, Y_HL_HAT_KEYS) ?? slotYNum(ft, Y_HL_HAT_KEYS);
  let yT60 = slotYNum(sc, Y_T60_HAT_KEYS) ?? slotYNum(ft, Y_T60_HAT_KEYS);
  let yT45 = slotYNum(sc, Y_T45_HAT_KEYS) ?? slotYNum(ft, Y_T45_HAT_KEYS);
  let yT75 = slotYNum(sc, Y_T75_HAT_KEYS) ?? slotYNum(ft, Y_T75_HAT_KEYS);
  let yT90 = slotYNum(sc, Y_T90_HAT_KEYS) ?? slotYNum(ft, Y_T90_HAT_KEYS);
  let yTrade =
    slotYNum(sc, ["y_trade", "predicted_score_blend", "decision_score"]) ??
    slotYNum(ft, ["y_trade"]);
  let yNowcast =
    slotYNum(sc, ["y_nowcast", "y_nc", "predicted_score_nowcast"]) ??
    slotYNum(ft, ["y_nowcast", "y_nc"]);
  // 反 T / prefix_open_fallback 常无 OC 头：ŷ_oc = (1+开盘→τ)(1+ŷ_τc)−1
  if (yTau == null && yR != null) {
    const featTau =
      (sc.features_tau && typeof sc.features_tau === "object" ? sc.features_tau : null) ||
      (ft.features_tau && typeof ft.features_tau === "object" ? ft.features_tau : null);
    const rot = slotYNum(featTau, ["ret_open_to_tau"]);
    const restored = rot != null ? compoundPct(rot, yR) : null;
    if (restored != null && Number.isFinite(restored)) yTau = restored;
  }
  if (yTau == null) yTau = gateYt;
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
  const storedTw =
    slotYNum(sc, ["y_tw", "y_τw", "y_tw_hat"]) ??
    slotYNum(ft, ["y_tw", "y_τw", "y_tw_hat"]) ??
    slotYNum(slotCloseBand(r), ["y_tw", "y_τw"]);
  return {
    y_tau: yTau,
    y_r: yR,
    y_hl: yHl,
    y_t30: yT30,
    y_t45: yT45,
    y_t60: yT60,
    y_t75: yT75,
    y_t90: yT90,
    y_tw: storedTw ?? blendYtw(yT30, yT60, yT90, yT45, yT75, true),
    y_trade: yTrade,
    y_nowcast: yNowcast,
  };
}

/** 本轮 r 真值：close[T]/price(τ)−1；缺字段时用日收 / 该根 5m C。 */
function slotRRealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["r_realized", "y_r_realized"]) ??
    slotYNum(slotScores, ["r_realized", "y_r_realized"]) ??
    slotYNum(d, ["r_realized", "y_r_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  const stored = slotYNum(scan, ["r_realized", "y_r_realized"]);
  if (stored != null) return stored;
  const c = Number(scan.c);
  const dayC = Number(d && d.close);
  if (Number.isFinite(c) && c > 0 && Number.isFinite(dayC) && dayC > 0) {
    return (dayC / c - 1) * 100;
  }
  return null;
}

/** 本轮 y_τ30 真值：mean(price(τ⊕25/30/35))/price(τ)−1；只读扫描/槽位已落盘字段。 */
function slotT30RealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["y_t30_realized", "t30_realized"]) ??
    slotYNum(slotScores, ["y_t30_realized", "t30_realized"]) ??
    slotYNum(d, ["y_t30_realized", "t30_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  return slotYNum(scan, ["y_t30_realized", "t30_realized"]);
}

/** 本轮 y_τ60 真值：mean(price(τ⊕55/60/65))/price(τ)−1；只读扫描/槽位已落盘字段。 */
function slotT45RealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["y_t45_realized", "t45_realized"]) ??
    slotYNum(slotScores, ["y_t45_realized", "t45_realized"]) ??
    slotYNum(d, ["y_t45_realized", "t45_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  return slotYNum(scan, ["y_t45_realized", "t45_realized"]);
}

function slotT60RealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["y_t60_realized", "t60_realized"]) ??
    slotYNum(slotScores, ["y_t60_realized", "t60_realized"]) ??
    slotYNum(d, ["y_t60_realized", "t60_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  return slotYNum(scan, ["y_t60_realized", "t60_realized"]);
}
/** 本轮 y_τ90 真值：mean(price(τ⊕85/90/95))/price(τ)−1；只读扫描/槽位已落盘字段。 */
function slotT75RealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["y_t75_realized", "t75_realized"]) ??
    slotYNum(slotScores, ["y_t75_realized", "t75_realized"]) ??
    slotYNum(d, ["y_t75_realized", "t75_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  return slotYNum(scan, ["y_t75_realized", "t75_realized"]);
}

function slotT90RealizedNum(d, slotRow, hm, slotScores) {
  const n =
    slotYNum(slotRow, ["y_t90_realized", "t90_realized"]) ??
    slotYNum(slotScores, ["y_t90_realized", "t90_realized"]) ??
    slotYNum(d, ["y_t90_realized", "t90_realized"]);
  if (n != null) return n;
  const scan = scanRowForHm(d, hm);
  if (!scan) return null;
  return slotYNum(scan, ["y_t90_realized", "t90_realized"]);
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
  if (yhat.y_tau != null) {
    if (slotScores.y_oc == null) slotScores.y_oc = yhat.y_tau;
  }
  if (yhat.y_r != null) {
    if (slotScores.predicted_score_r == null) slotScores.predicted_score_r = yhat.y_r;
    if (slotScores.y_r_hat == null) slotScores.y_r_hat = yhat.y_r;
    if (slotScores.y_r == null) slotScores.y_r = yhat.y_r;
    if (slotScores["y_τc"] == null) slotScores["y_τc"] = yhat.y_r;
    if (slotScores.y_tc == null) slotScores.y_tc = yhat.y_r;
  }
  if (yhat.y_t30 != null) {
    if (slotScores["y_τ30"] == null) slotScores["y_τ30"] = yhat.y_t30;
    if (slotScores.y_t30 == null) slotScores.y_t30 = yhat.y_t30;
    if (slotScores.predicted_score_t30 == null) slotScores.predicted_score_t30 = yhat.y_t30;
    if (slotScores.y_t30_hat == null) slotScores.y_t30_hat = yhat.y_t30;
  }
  if (yhat.y_hl != null) {
    if (slotScores.y_hl == null) slotScores.y_hl = yhat.y_hl;
    if (slotScores.predicted_score_hl == null) slotScores.predicted_score_hl = yhat.y_hl;
  }
  if (yhat.y_t60 != null) {
    if (slotScores["y_τ60"] == null) slotScores["y_τ60"] = yhat.y_t60;
    if (slotScores.y_t60 == null) slotScores.y_t60 = yhat.y_t60;
    if (slotScores.predicted_score_t60 == null) slotScores.predicted_score_t60 = yhat.y_t60;
    if (slotScores.y_t60_hat == null) slotScores.y_t60_hat = yhat.y_t60;
  }
  if (yhat.y_t90 != null) {
    if (slotScores["y_τ90"] == null) slotScores["y_τ90"] = yhat.y_t90;
    if (slotScores.y_t90 == null) slotScores.y_t90 = yhat.y_t90;
    if (slotScores.predicted_score_t90 == null) slotScores.predicted_score_t90 = yhat.y_t90;
    if (slotScores.y_t90_hat == null) slotScores.y_t90_hat = yhat.y_t90;
  }
  if (yhat.y_tw != null) {
    if (slotScores["y_τw"] == null) slotScores["y_τw"] = yhat.y_tw;
    if (slotScores.y_tw == null) slotScores.y_tw = yhat.y_tw;
    if (slotScores.y_tw_hat == null) slotScores.y_tw_hat = yhat.y_tw;
  }
  const rReal = slotRRealizedNum(d, r, hm, slotScores);
  if (rReal != null) {
    if (slotScores.r_realized == null) slotScores.r_realized = rReal;
    if (slotScores.y_r_realized == null) slotScores.y_r_realized = rReal;
  }
  const t30Real = slotT30RealizedNum(d, r, hm, slotScores);
  if (t30Real != null) {
    if (slotScores.y_t30_realized == null) slotScores.y_t30_realized = t30Real;
    if (slotScores.t30_realized == null) slotScores.t30_realized = t30Real;
  }
  const t45Real = slotT45RealizedNum(d, r, hm, slotScores);
  if (t45Real != null) {
    if (slotScores.y_t45_realized == null) slotScores.y_t45_realized = t45Real;
    if (slotScores.t45_realized == null) slotScores.t45_realized = t45Real;
  }
  const t60Real = slotT60RealizedNum(d, r, hm, slotScores);
  if (t60Real != null) {
    if (slotScores.y_t60_realized == null) slotScores.y_t60_realized = t60Real;
    if (slotScores.t60_realized == null) slotScores.t60_realized = t60Real;
  }
  const t75Real = slotT75RealizedNum(d, r, hm, slotScores);
  if (t75Real != null) {
    if (slotScores.y_t75_realized == null) slotScores.y_t75_realized = t75Real;
    if (slotScores.t75_realized == null) slotScores.t75_realized = t75Real;
  }
  const t90Real = slotT90RealizedNum(d, r, hm, slotScores);
  if (t90Real != null) {
    if (slotScores.y_t90_realized == null) slotScores.y_t90_realized = t90Real;
    if (slotScores.t90_realized == null) slotScores.t90_realized = t90Real;
  }
  const twReal = blendYtw(t30Real, t60Real, t90Real, t45Real, t75Real);
  if (twReal != null) {
    if (slotScores.y_tw_realized == null) slotScores.y_tw_realized = twReal;
  }
  const cb = slotCloseBand(r);
  const rHat =
    slotYNum(cb, ["r_hat", "residual"]) ??
    slotYNum(r, ["r_hat", "residual"]) ??
    slotYNum(slotScores, ["r_hat", "residual"]);
  if (rHat != null) {
    if (slotScores.r_hat == null) slotScores.r_hat = rHat;
    if (slotScores.residual == null) slotScores.residual = rHat;
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
    predicted_score_r: yhat.y_r,
    y_r_hat: yhat.y_r,
    y_r: yhat.y_r,
    y_oc: yhat.y_tau,
    "y_τc": yhat.y_r,
    y_tc: yhat.y_r,
    r_realized: rReal,
    y_r_realized: rReal,
    y_hl: yhat.y_hl,
    predicted_score_hl: yhat.y_hl,
    path_realized: d.path_realized,
    "y_τ30": yhat.y_t30,
    y_t30: yhat.y_t30,
    predicted_score_t30: yhat.y_t30,
    y_t30_hat: yhat.y_t30,
    y_t30_realized: t30Real,
    t30_realized: t30Real,
    "y_τ60": yhat.y_t60,
    y_t60: yhat.y_t60,
    predicted_score_t60: yhat.y_t60,
    y_t60_hat: yhat.y_t60,
    y_t60_realized: t60Real,
    t60_realized: t60Real,
    "y_τ45": yhat.y_t45,
    y_t45: yhat.y_t45,
    predicted_score_t45: yhat.y_t45,
    y_t45_hat: yhat.y_t45,
    y_t45_realized: t45Real,
    t45_realized: t45Real,
    "y_τ75": yhat.y_t75,
    y_t75: yhat.y_t75,
    predicted_score_t75: yhat.y_t75,
    y_t75_hat: yhat.y_t75,
    y_t75_realized: t75Real,
    t75_realized: t75Real,
    "y_τ90": yhat.y_t90,
    y_t90: yhat.y_t90,
    predicted_score_t90: yhat.y_t90,
    y_t90_hat: yhat.y_t90,
    y_t90_realized: t90Real,
    t90_realized: t90Real,
    "y_τw": yhat.y_tw,
    y_tw: yhat.y_tw,
    y_tw_hat: yhat.y_tw,
    y_tw_realized: blendYtw(t30Real, t60Real, t90Real, t45Real, t75Real),
    r_hat: rHat,
    residual: rHat,
    remaining_oc: rHat,
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
    close_band: r.close_band || d.close_band || null,
    c_tau: (cb && (cb.c_tau || cb.close_px)) || d.c_tau,
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
      key === "y_trade" ||
      key === "y_nowcast" ||
      key === "predicted_score_r" ||
      key === "y_r_hat" ||
      key === "y_r" ||
      key === "r_hat" ||
      key === "residual" ||
      key === "remaining_oc" ||
      key === "r_realized" ||
      key === "y_r_realized" ||
      key === "y_τc" ||
      key === "y_tc" ||
      key === "y_oc" ||
      key === "y_τ30" ||
      key === "y_t30" ||
      key === "predicted_score_t30" ||
      key === "y_t30_hat" ||
      key === "y_t30_realized" ||
      key === "t30_realized" ||
      key === "y_τ60" ||
      key === "y_t60" ||
      key === "predicted_score_t60" ||
      key === "y_t60_hat" ||
      key === "y_t60_realized" ||
      key === "t60_realized" ||
      key === "y_τ90" ||
      key === "y_t90" ||
      key === "predicted_score_t90" ||
      key === "y_t90_hat" ||
      key === "y_t90_realized" ||
      key === "t90_realized" ||
      key === "y_τw" ||
      key === "y_tw" ||
      key === "y_tw_hat" ||
      key === "y_tw_realized" ||
      key === "tau_realized")
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

function _horizonProbAgree(pUp, realPct) {
  if (pUp == null || realPct == null) return null;
  return (pUp > 0.5) === (realPct > 0);
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
    text: fmtRtauPct(n),
    tip:
      TAU_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_oc 同号" : agree === false ? " · 与 ŷ_oc 异号" : ""),
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
function fmtSignedNumHtml(text, value, tone = "pct") {
  const cls = tone === "prob" ? paperProbClass(value) : paperMetricClass(value);
  return `<span class="paper-t0-y-num${cls ? ` ${cls}` : ""}">${escapeText(
    text == null || text === "" ? "—" : String(text)
  )}</span>`;
}

/** 预估与真实 label 各自独立上色：ŷ(label)。 */
function fmtPredRealizedHtml(predTxt, predVal, pr, showRealized, tone = "pct") {
  const predSpan = fmtSignedNumHtml(predTxt, predVal, tone);
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

/** 成交明细 y_τ30 tip：单独瘦 payload，避免共用 data-score-detail 过长截断。 */
function t30TipPayload(it) {
  if (!it || typeof it !== "object") return {};
  const feat = it.features_tau && typeof it.features_tau === "object" ? it.features_tau : null;
  const yhat =
    it["y_τ30"] ?? it.y_t30 ?? it.predicted_score_t30 ?? it.y_t30_hat ?? null;
  const real = it.y_t30_realized ?? it.t30_realized ?? null;
  const terms = it.formula_terms_t30 || it.score_formula_terms_t30 || null;
  const spec = it.y_spec_τ30 || it.y_spec_t30 || null;
  return {
    "y_τ30": yhat,
    y_t30: it.y_t30 ?? yhat,
    predicted_score_t30: it.predicted_score_t30 ?? yhat,
    y_t30_hat: it.y_t30_hat ?? yhat,
    y_t30_realized: real,
    t30_realized: it.t30_realized ?? real,
    y_spec_τ30: spec,
    y_spec_t30: it.y_spec_t30 || spec,
    formula_terms_t30: terms,
    score_formula_terms_t30: it.score_formula_terms_t30 || terms,
    features_tau: feat,
    as_of_tau: it.as_of_tau || it.rem_tau || it.hm || null,
    rem_tau: it.rem_tau || it.as_of_tau || it.hm || null,
    gap_pct: it.gap_pct ?? (feat && feat.gap_pct) ?? null,
  };
}

function t30TipDetailAttr(it) {
  return escapeText(JSON.stringify(t30TipPayload(it)));
}

/** 扫描行 tip 的该钟因子；缺则回退日级 scores（旧簿）。 */
function scanTipFeatHost(scanRow, day) {
  const scores =
    (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  const feats =
    (scanRow && scanRow.features_tau) ||
    scores.features_tau ||
    (day &&
      day.direction_features &&
      typeof day.direction_features === "object" &&
      day.direction_features.features_tau) ||
    null;
  return { scores, feats };
}

function t30ScanTipItem(scanRow, day) {
  const { scores, feats } = scanTipFeatHost(scanRow, day);
  return {
    "y_τ30": scanRow && (scanRow["y_τ30"] ?? scanRow.y_t30),
    y_t30: scanRow && (scanRow.y_t30 ?? scanRow["y_τ30"]),
    predicted_score_t30:
      scanRow && (scanRow.predicted_score_t30 ?? scanRow.y_t30_hat ?? scanRow["y_τ30"]),
    y_t30_realized: scanRow && (scanRow.y_t30_realized ?? scanRow.t30_realized),
    t30_realized: scanRow && (scanRow.t30_realized ?? scanRow.y_t30_realized),
    formula_terms_t30: (scanRow && scanRow.formula_terms_t30) || scores.formula_terms_t30,
    score_formula_terms_t30:
      (scanRow && scanRow.score_formula_terms_t30) || scores.score_formula_terms_t30,
    y_spec_τ30: (scanRow && (scanRow.y_spec_τ30 || scanRow.y_spec_t30)) || scores.y_spec_τ30,
    features_tau: feats,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: (scanRow && scanRow.gap_pct) ?? (feats && feats.gap_pct) ?? scores.gap_pct ?? null,
  };
}

function tauScanTipItem(scanRow, day) {
  const { scores, feats } = scanTipFeatHost(scanRow, day);
  const yoc = scanRow && (scanRow.y_oc ?? scanRow.y_tau);
  return {
    y_oc: yoc,
    y_tau: scanRow && (scanRow.y_tau ?? scanRow.y_oc),
    predicted_score_tau: yoc,
    formula_terms_tau: (scanRow && scanRow.formula_terms_tau) || scores.formula_terms_tau,
    score_formula_terms_tau:
      (scanRow && scanRow.score_formula_terms_tau) || scores.score_formula_terms_tau,
    features_tau: feats,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: (scanRow && scanRow.gap_pct) ?? (feats && feats.gap_pct) ?? scores.gap_pct ?? null,
  };
}

function tauTipDetailAttr(scanRow, day) {
  return escapeText(JSON.stringify(tauScanTipItem(scanRow, day)));
}

function pickT30Pred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h["y_τ30"]) ??
      finiteYhatNum(h.y_t30) ??
      finiteYhatNum(h.predicted_score_t30) ??
      finiteYhatNum(h.y_t30_hat);
    if (n != null) return n;
  }
  return null;
}

function pickT30Realized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n = finiteYhatNum(h.y_t30_realized) ?? finiteYhatNum(h.t30_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: T30_REALIZED_TITLE, agree: null };
  }
  const pred = pickT30Pred(predHost) ?? pickT30Pred(d);
  const agree = _horizonProbAgree(pred, n);
  return {
    text: fmtRtauPct(n),
    n,
    tip:
      T30_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ30 同号" : agree === false ? " · 与 ŷ_τ30 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τ30；回测配对 mean(price(τ⊕25/30/35))/price(τ)−1，格式同 y_τc：预估值(真实值)。 */
function t30MergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickT30Pred(predHost) ?? pickT30Pred(realHost);
  const predTxt = pred != null ? fmtHorizonProb(pred) : "—";
  const pr = showRealized
    ? pickT30Realized(realHost, predHost)
    : { n: null, tip: T30_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_T30_TITLE} · ŷ_τ30=${pred.toFixed(3)}` : Y_T30_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : T30_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized, "prob");
  return (
    `<td class="num paper-t0-col-yt30 paper-t0-col-y paper-t0-col-y-t30 paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="t30" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function hlTipPayload(it) {
  if (!it || typeof it !== "object") return {};
  const feat = it.features_tau && typeof it.features_tau === "object" ? it.features_tau : null;
  const yhat =
    it.y_hl ?? it.predicted_score_hl ?? it.y_path ?? it.predicted_score_path ?? null;
  const real = it.path_realized ?? it.y_path_realized ?? it.hl_realized ?? null;
  const terms = it.formula_terms_path || it.score_formula_terms_path || null;
  return {
    y_hl: yhat,
    predicted_score_hl: it.predicted_score_hl ?? yhat,
    path_realized: real,
    y_path_realized: it.y_path_realized ?? real,
    formula_terms_path: terms,
    score_formula_terms_path: it.score_formula_terms_path || terms,
    features_tau: feat,
    as_of_tau: it.as_of_tau || it.rem_tau || it.hm || null,
    gap_pct: it.gap_pct ?? (feat && feat.gap_pct) ?? null,
  };
}

function hlTipDetailAttr(it) {
  return escapeText(JSON.stringify(hlTipPayload(it)));
}

function hlScanTipItem(scanRow, day) {
  const scores = (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  const yhat =
    scanRow &&
    (scanRow.y_hl ?? scanRow.predicted_score_hl ?? scanRow.y_path ?? scanRow.predicted_score_path);
  return {
    y_hl: yhat,
    predicted_score_hl: scanRow && (scanRow.predicted_score_hl ?? yhat),
    path_realized: (scanRow && scanRow.path_realized) || (day && day.path_realized),
    y_path_realized: (scanRow && scanRow.y_path_realized) || (day && day.path_realized),
    formula_terms_path:
      (scanRow && scanRow.formula_terms_path) || scores.formula_terms_path,
    score_formula_terms_path:
      (scanRow && scanRow.score_formula_terms_path) || scores.score_formula_terms_path,
    features_tau: (scanRow && scanRow.features_tau) || scores.features_tau || null,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: scores.gap_pct ?? null,
  };
}

function pickHlPred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h.y_hl) ??
      finiteYhatNum(h.predicted_score_hl) ??
      finiteYhatNum(h.y_path) ??
      finiteYhatNum(h.predicted_score_path);
    if (n != null) return n;
  }
  return null;
}

function pickHlRealized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n =
      finiteYhatNum(h.path_realized) ??
      finiteYhatNum(h.y_path_realized) ??
      finiteYhatNum(h.hl_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: PATH_REALIZED_TITLE, agree: null };
  }
  const pred = pickHlPred(predHost) ?? pickHlPred(d);
  const agree = _signAgree(pred, n, 0);
  return {
    text: fmtPathScore(n),
    n,
    tip:
      PATH_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_hl 同号" : agree === false ? " · 与 ŷ_hl 异号" : ""),
    agree,
  };
}

function hlMergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickHlPred(predHost) ?? pickHlPred(realHost);
  const predTxt = pred != null ? fmtPathScore(pred) : "—";
  const pr = showRealized
    ? pickHlRealized(realHost, predHost)
    : { n: null, tip: PATH_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_HL_TITLE} · ŷ_hl=${pred.toFixed(3)}` : Y_HL_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : PATH_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized);
  return (
    `<td class="num paper-t0-col-yhl paper-t0-col-y paper-t0-col-y-hl paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="hl" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function t60TipPayload(it) {
  const yhat =
    it["y_τ60"] ?? it.y_t60 ?? it.predicted_score_t60 ?? it.y_t60_hat ?? null;
  const real = it.y_t60_realized ?? it.t60_realized ?? null;
  const terms = it.formula_terms_t60 || it.score_formula_terms_t60 || null;
  const spec = it.y_spec_τ60 || it.y_spec_t60 || null;
  return {
    "y_τ60": yhat,
    y_t60: it.y_t60 ?? yhat,
    predicted_score_t60: it.predicted_score_t60 ?? yhat,
    y_t60_hat: it.y_t60_hat ?? yhat,
    y_t60_realized: real,
    t60_realized: it.t60_realized ?? real,
    y_spec_τ60: it.y_spec_τ60 || spec,
    y_spec_t60: it.y_spec_t60 || spec,
    formula_terms_t60: terms,
    score_formula_terms_t60: it.score_formula_terms_t60 || terms,
    features_tau: it.features_tau || null,
    as_of_tau: it.as_of_tau || it.rem_tau || null,
    gap_pct: it.gap_pct ?? null,
  };
}

function t60TipDetailAttr(it) {
  return escapeText(JSON.stringify(t60TipPayload(it)));
}

function t60ScanTipItem(scanRow, day) {
  const scores = (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  return {
    "y_τ60": scanRow && (scanRow["y_τ60"] ?? scanRow.y_t60),
    y_t60: scanRow && (scanRow.y_t60 ?? scanRow["y_τ60"]),
    predicted_score_t60:
      scanRow && (scanRow.predicted_score_t60 ?? scanRow.y_t60_hat ?? scanRow["y_τ60"]),
    y_t60_realized: scanRow && (scanRow.y_t60_realized ?? scanRow.t60_realized),
    t60_realized: scanRow && (scanRow.t60_realized ?? scanRow.y_t60_realized),
    formula_terms_t60: (scanRow && scanRow.formula_terms_t60) || scores.formula_terms_t60,
    score_formula_terms_t60:
      (scanRow && scanRow.score_formula_terms_t60) || scores.score_formula_terms_t60,
    y_spec_τ60: (scanRow && (scanRow.y_spec_τ60 || scanRow.y_spec_t60)) || scores.y_spec_τ60,
    y_spec_t60: (scanRow && (scanRow.y_spec_t60 || scanRow.y_spec_τ60)) || scores.y_spec_t60,
    features_tau: (scanRow && scanRow.features_tau) || scores.features_tau,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: scores.gap_pct ?? null,
  };
}

function pickT45Pred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h["y_τ45"]) ??
      finiteYhatNum(h.y_t45) ??
      finiteYhatNum(h.predicted_score_t45) ??
      finiteYhatNum(h.y_t45_hat);
    if (n != null) return n;
  }
  return null;
}

function t45TipPayload(it) {
  const yhat =
    it["y_τ45"] ?? it.y_t45 ?? it.predicted_score_t45 ?? it.y_t45_hat ?? null;
  const real = it.y_t45_realized ?? it.t45_realized ?? null;
  const terms = it.formula_terms_t45 || it.score_formula_terms_t45 || null;
  const spec = it.y_spec_τ45 || it.y_spec_t45 || null;
  return {
    "y_τ45": yhat,
    y_t45: it.y_t45 ?? yhat,
    predicted_score_t45: it.predicted_score_t45 ?? yhat,
    y_t45_hat: it.y_t45_hat ?? yhat,
    y_t45_realized: real,
    t45_realized: it.t45_realized ?? real,
    y_spec_τ45: it.y_spec_τ45 || spec,
    y_spec_t45: it.y_spec_t45 || spec,
    formula_terms_t45: terms,
    score_formula_terms_t45: it.score_formula_terms_t45 || terms,
    features_tau: it.features_tau || null,
    as_of_tau: it.as_of_tau || it.rem_tau || null,
    gap_pct: it.gap_pct ?? null,
  };
}

function t45TipDetailAttr(it) {
  return escapeText(JSON.stringify(t45TipPayload(it)));
}

function t45ScanTipItem(scanRow, day) {
  const scores = (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  return {
    "y_τ45": scanRow && (scanRow["y_τ45"] ?? scanRow.y_t45),
    y_t45: scanRow && (scanRow.y_t45 ?? scanRow["y_τ45"]),
    predicted_score_t45:
      scanRow && (scanRow.predicted_score_t45 ?? scanRow.y_t45_hat ?? scanRow["y_τ45"]),
    y_t45_realized: scanRow && (scanRow.y_t45_realized ?? scanRow.t45_realized),
    t45_realized: scanRow && (scanRow.t45_realized ?? scanRow.y_t45_realized),
    formula_terms_t45: (scanRow && scanRow.formula_terms_t45) || scores.formula_terms_t45,
    score_formula_terms_t45:
      (scanRow && scanRow.score_formula_terms_t45) || scores.score_formula_terms_t45,
    y_spec_τ45: (scanRow && (scanRow.y_spec_τ45 || scanRow.y_spec_t45)) || scores.y_spec_τ45,
    y_spec_t45: (scanRow && (scanRow.y_spec_t45 || scanRow.y_spec_τ45)) || scores.y_spec_t45,
    features_tau: (scanRow && scanRow.features_tau) || scores.features_tau,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: scores.gap_pct ?? null,
  };
}

function pickT45Realized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n = finiteYhatNum(h.y_t45_realized) ?? finiteYhatNum(h.t45_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: T45_REALIZED_TITLE, agree: null };
  }
  const pred = pickT45Pred(predHost) ?? pickT45Pred(d);
  const agree = _horizonProbAgree(pred, n);
  return {
    text: fmtRtauPct(n),
    n,
    tip:
      T45_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ45 同号" : agree === false ? " · 与 ŷ_τ45 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τ45；回测配对 mean(price(τ⊕40/45/50))/price(τ)−1，格式同 y_τc：预估值(真实值)。 */
function t45MergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickT45Pred(predHost) ?? pickT45Pred(realHost);
  const predTxt = pred != null ? fmtHorizonProb(pred) : "—";
  const pr = showRealized
    ? pickT45Realized(realHost, predHost)
    : { n: null, tip: T45_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_T45_TITLE} · ŷ_τ45=${pred.toFixed(3)}` : Y_T45_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : T45_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized, "prob");
  return (
    `<td class="num paper-t0-col-yt45 paper-t0-col-y paper-t0-col-y-t45 paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="t45" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function pickT60Pred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h["y_τ60"]) ??
      finiteYhatNum(h.y_t60) ??
      finiteYhatNum(h.predicted_score_t60) ??
      finiteYhatNum(h.y_t60_hat);
    if (n != null) return n;
  }
  return null;
}

function pickT60Realized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n = finiteYhatNum(h.y_t60_realized) ?? finiteYhatNum(h.t60_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: T60_REALIZED_TITLE, agree: null };
  }
  const pred = pickT60Pred(predHost) ?? pickT60Pred(d);
  const agree = _horizonProbAgree(pred, n);
  return {
    text: fmtRtauPct(n),
    n,
    tip:
      T60_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ60 同号" : agree === false ? " · 与 ŷ_τ60 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τ60；回测配对 mean(price(τ⊕55/60/65))/price(τ)−1，格式同 y_τc：预估值(真实值)。 */
function t60MergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickT60Pred(predHost) ?? pickT60Pred(realHost);
  const predTxt = pred != null ? fmtHorizonProb(pred) : "—";
  const pr = showRealized
    ? pickT60Realized(realHost, predHost)
    : { n: null, tip: T60_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_T60_TITLE} · ŷ_τ60=${pred.toFixed(3)}` : Y_T60_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : T60_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized, "prob");
  return (
    `<td class="num paper-t0-col-yt60 paper-t0-col-y paper-t0-col-y-t60 paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="t60" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function t90TipPayload(it) {
  const yhat =
    it["y_τ90"] ?? it.y_t90 ?? it.predicted_score_t90 ?? it.y_t90_hat ?? null;
  const real = it.y_t90_realized ?? it.t90_realized ?? null;
  const terms = it.formula_terms_t90 || it.score_formula_terms_t90 || null;
  const spec = it.y_spec_τ90 || it.y_spec_t90 || null;
  return {
    "y_τ90": yhat,
    y_t90: it.y_t90 ?? yhat,
    predicted_score_t90: it.predicted_score_t90 ?? yhat,
    y_t90_hat: it.y_t90_hat ?? yhat,
    y_t90_realized: real,
    t90_realized: it.t90_realized ?? real,
    y_spec_τ90: it.y_spec_τ90 || spec,
    y_spec_t90: it.y_spec_t90 || spec,
    formula_terms_t90: terms,
    score_formula_terms_t90: it.score_formula_terms_t90 || terms,
    features_tau: it.features_tau || null,
    as_of_tau: it.as_of_tau || it.rem_tau || null,
    gap_pct: it.gap_pct ?? null,
  };
}

function t90TipDetailAttr(it) {
  return escapeText(JSON.stringify(t90TipPayload(it)));
}

function t90ScanTipItem(scanRow, day) {
  const scores = (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  return {
    "y_τ90": scanRow && (scanRow["y_τ90"] ?? scanRow.y_t90),
    y_t90: scanRow && (scanRow.y_t90 ?? scanRow["y_τ90"]),
    predicted_score_t90:
      scanRow && (scanRow.predicted_score_t90 ?? scanRow.y_t90_hat ?? scanRow["y_τ90"]),
    y_t90_realized: scanRow && (scanRow.y_t90_realized ?? scanRow.t90_realized),
    t90_realized: scanRow && (scanRow.t90_realized ?? scanRow.y_t90_realized),
    formula_terms_t90: (scanRow && scanRow.formula_terms_t90) || scores.formula_terms_t90,
    score_formula_terms_t90:
      (scanRow && scanRow.score_formula_terms_t90) || scores.score_formula_terms_t90,
    y_spec_τ90: (scanRow && (scanRow.y_spec_τ90 || scanRow.y_spec_t90)) || scores.y_spec_τ90,
    y_spec_t90: (scanRow && (scanRow.y_spec_t90 || scanRow.y_spec_τ90)) || scores.y_spec_t90,
    features_tau: (scanRow && scanRow.features_tau) || scores.features_tau,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: scores.gap_pct ?? null,
  };
}

function pickT75Pred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h["y_τ75"]) ??
      finiteYhatNum(h.y_t75) ??
      finiteYhatNum(h.predicted_score_t75) ??
      finiteYhatNum(h.y_t75_hat);
    if (n != null) return n;
  }
  return null;
}

function t75TipPayload(it) {
  const yhat =
    it["y_τ75"] ?? it.y_t75 ?? it.predicted_score_t75 ?? it.y_t75_hat ?? null;
  const real = it.y_t75_realized ?? it.t75_realized ?? null;
  const terms = it.formula_terms_t75 || it.score_formula_terms_t75 || null;
  const spec = it.y_spec_τ75 || it.y_spec_t75 || null;
  return {
    "y_τ75": yhat,
    y_t75: it.y_t75 ?? yhat,
    predicted_score_t75: it.predicted_score_t75 ?? yhat,
    y_t75_hat: it.y_t75_hat ?? yhat,
    y_t75_realized: real,
    t75_realized: it.t75_realized ?? real,
    y_spec_τ75: it.y_spec_τ75 || spec,
    y_spec_t75: it.y_spec_t75 || spec,
    formula_terms_t75: terms,
    score_formula_terms_t75: it.score_formula_terms_t75 || terms,
    features_tau: it.features_tau || null,
    as_of_tau: it.as_of_tau || it.rem_tau || null,
    gap_pct: it.gap_pct ?? null,
  };
}

function t75TipDetailAttr(it) {
  return escapeText(JSON.stringify(t75TipPayload(it)));
}

function t75ScanTipItem(scanRow, day) {
  const scores = (day && day.scores && typeof day.scores === "object" ? day.scores : {}) || {};
  return {
    "y_τ75": scanRow && (scanRow["y_τ75"] ?? scanRow.y_t75),
    y_t75: scanRow && (scanRow.y_t75 ?? scanRow["y_τ75"]),
    predicted_score_t75:
      scanRow && (scanRow.predicted_score_t75 ?? scanRow.y_t75_hat ?? scanRow["y_τ75"]),
    y_t75_realized: scanRow && (scanRow.y_t75_realized ?? scanRow.t75_realized),
    t75_realized: scanRow && (scanRow.t75_realized ?? scanRow.y_t75_realized),
    formula_terms_t75: (scanRow && scanRow.formula_terms_t75) || scores.formula_terms_t75,
    score_formula_terms_t75:
      (scanRow && scanRow.score_formula_terms_t75) || scores.score_formula_terms_t75,
    y_spec_τ75: (scanRow && (scanRow.y_spec_τ75 || scanRow.y_spec_t75)) || scores.y_spec_τ75,
    y_spec_t75: (scanRow && (scanRow.y_spec_t75 || scanRow.y_spec_τ75)) || scores.y_spec_t75,
    features_tau: (scanRow && scanRow.features_tau) || scores.features_tau,
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
    gap_pct: scores.gap_pct ?? null,
  };
}

function pickT75Realized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n = finiteYhatNum(h.y_t75_realized) ?? finiteYhatNum(h.t75_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: T75_REALIZED_TITLE, agree: null };
  }
  const pred = pickT75Pred(predHost) ?? pickT75Pred(d);
  const agree = _horizonProbAgree(pred, n);
  return {
    text: fmtRtauPct(n),
    n,
    tip:
      T75_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ75 同号" : agree === false ? " · 与 ŷ_τ75 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τ75；回测配对 mean(price(τ⊕70/75/80))/price(τ)−1，格式同 y_τc：预估值(真实值)。 */
function t75MergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickT75Pred(predHost) ?? pickT75Pred(realHost);
  const predTxt = pred != null ? fmtHorizonProb(pred) : "—";
  const pr = showRealized
    ? pickT75Realized(realHost, predHost)
    : { n: null, tip: T75_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_T75_TITLE} · ŷ_τ75=${pred.toFixed(3)}` : Y_T75_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : T75_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized, "prob");
  return (
    `<td class="num paper-t0-col-yt75 paper-t0-col-y paper-t0-col-y-t75 paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="t75" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function pickT90Pred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  const hosts = [scan, d.scores, d, d.direction_features].filter(
    (h) => h && typeof h === "object"
  );
  for (const h of hosts) {
    const n =
      finiteYhatNum(h["y_τ90"]) ??
      finiteYhatNum(h.y_t90) ??
      finiteYhatNum(h.predicted_score_t90) ??
      finiteYhatNum(h.y_t90_hat);
    if (n != null) return n;
  }
  return null;
}

function pickT90Realized(d, predHost) {
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let n = null;
  for (const h of hosts) {
    n = finiteYhatNum(h.y_t90_realized) ?? finiteYhatNum(h.t90_realized);
    if (n != null) break;
  }
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: T90_REALIZED_TITLE, agree: null };
  }
  const pred = pickT90Pred(predHost) ?? pickT90Pred(d);
  const agree = _horizonProbAgree(pred, n);
  return {
    text: fmtRtauPct(n),
    n,
    tip:
      T90_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τ90 同号" : agree === false ? " · 与 ŷ_τ90 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τ90；回测配对 mean(price(τ⊕85/90/95))/price(τ)−1，格式同 y_τc：预估值(真实值)。 */
function t90MergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickT90Pred(predHost) ?? pickT90Pred(realHost);
  const predTxt = pred != null ? fmtHorizonProb(pred) : "—";
  const pr = showRealized
    ? pickT90Realized(realHost, predHost)
    : { n: null, tip: T90_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_T90_TITLE} · ŷ_τ90=${pred.toFixed(3)}` : Y_T90_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : T90_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized, "prob");
  return (
    `<td class="num paper-t0-col-yt90 paper-t0-col-y paper-t0-col-y-t90 paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="t90" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

function twTipPayload(it) {
  if (!it || typeof it !== "object") return {};
  const y30 = it["y_τ30"] ?? it.y_t30 ?? it.predicted_score_t30 ?? it.y_t30_hat ?? null;
  const y45 = it["y_τ45"] ?? it.y_t45 ?? it.predicted_score_t45 ?? it.y_t45_hat ?? null;
  const y60 = it["y_τ60"] ?? it.y_t60 ?? it.predicted_score_t60 ?? it.y_t60_hat ?? null;
  const y75 = it["y_τ75"] ?? it.y_t75 ?? it.predicted_score_t75 ?? it.y_t75_hat ?? null;
  const y90 = it["y_τ90"] ?? it.y_t90 ?? it.predicted_score_t90 ?? it.y_t90_hat ?? null;
  const yhat =
    finiteYhatNum(it.y_tw) ??
    finiteYhatNum(it["y_τw"]) ??
    finiteYhatNum(it.y_tw_hat) ??
    blendYtw(y30, y60, y90, y45, y75, true);
  const r30 = it.y_t30_realized ?? it.t30_realized ?? null;
  const r45 = it.y_t45_realized ?? it.t45_realized ?? null;
  const r60 = it.y_t60_realized ?? it.t60_realized ?? null;
  const r75 = it.y_t75_realized ?? it.t75_realized ?? null;
  const r90 = it.y_t90_realized ?? it.t90_realized ?? null;
  const real = blendYtw(r30, r60, r90, r45, r75);
  return {
    "y_τw": yhat,
    y_tw: it.y_tw ?? yhat,
    y_tw_hat: it.y_tw_hat ?? yhat,
    y_tw_realized: real,
    "y_τ30": y30,
    y_t30: it.y_t30 ?? y30,
    y_t30_realized: r30,
    t30_realized: it.t30_realized ?? r30,
    "y_τ45": y45,
    y_t45: it.y_t45 ?? y45,
    y_t45_realized: r45,
    t45_realized: it.t45_realized ?? r45,
    "y_τ60": y60,
    y_t60: it.y_t60 ?? y60,
    y_t60_realized: r60,
    t60_realized: it.t60_realized ?? r60,
    "y_τ75": y75,
    y_t75: it.y_t75 ?? y75,
    y_t75_realized: r75,
    t75_realized: it.t75_realized ?? r75,
    "y_τ90": y90,
    y_t90: it.y_t90 ?? y90,
    y_t90_realized: r90,
    t90_realized: it.t90_realized ?? r90,
    as_of_tau: it.as_of_tau || it.rem_tau || it.hm || null,
    rem_tau: it.rem_tau || it.as_of_tau || it.hm || null,
  };
}

function twTipDetailAttr(it) {
  return escapeText(JSON.stringify(twTipPayload(it)));
}

function twScanTipItem(scanRow, day) {
  const scores =
    day && day.scores && typeof day.scores === "object" ? day.scores : {};
  const y30 = scanRow && (scanRow["y_τ30"] ?? scanRow.y_t30);
  const y45 = scanRow && (scanRow["y_τ45"] ?? scanRow.y_t45);
  const y60 = scanRow && (scanRow["y_τ60"] ?? scanRow.y_t60);
  const y75 = scanRow && (scanRow["y_τ75"] ?? scanRow.y_t75);
  const y90 = scanRow && (scanRow["y_τ90"] ?? scanRow.y_t90);
  const r30 = scanRow && (scanRow.y_t30_realized ?? scanRow.t30_realized);
  const r45 = scanRow && (scanRow.y_t45_realized ?? scanRow.t45_realized);
  const r60 = scanRow && (scanRow.y_t60_realized ?? scanRow.t60_realized);
  const r75 = scanRow && (scanRow.y_t75_realized ?? scanRow.t75_realized);
  const r90 = scanRow && (scanRow.y_t90_realized ?? scanRow.t90_realized);
  return {
    "y_τw": scanYtwPred(scanRow),
    y_tw: scanRow && (scanRow.y_tw ?? scanRow["y_τw"] ?? scanYtwPred(scanRow)),
    "y_τ30": y30,
    y_t30: scanRow && (scanRow.y_t30 ?? scanRow["y_τ30"]),
    y_t30_realized: r30,
    t30_realized: scanRow && (scanRow.t30_realized ?? scanRow.y_t30_realized),
    "y_τ60": y60,
    y_t60: scanRow && (scanRow.y_t60 ?? scanRow["y_τ60"]),
    y_t60_realized: r60,
    t60_realized: scanRow && (scanRow.t60_realized ?? scanRow.y_t60_realized),
    "y_τ90": y90,
    y_t90: scanRow && (scanRow.y_t90 ?? scanRow["y_τ90"]),
    y_t90_realized: r90,
    t90_realized: scanRow && (scanRow.t90_realized ?? scanRow.y_t90_realized),
    as_of_tau: (scanRow && (scanRow.hm || scanRow.as_of_tau)) || scores.as_of_tau,
  };
}

function pickTWPred(d) {
  if (!d || typeof d !== "object") return null;
  const scan =
    d._scan_score_row && typeof d._scan_score_row === "object" ? d._scan_score_row : null;
  for (const h of [scan, d]) {
    if (!h || typeof h !== "object") continue;
    const n =
      finiteYhatNum(h.y_tw) ?? finiteYhatNum(h["y_τw"]) ?? finiteYhatNum(h.y_tw_hat);
    if (n != null) return n;
  }
  return blendYtw(pickT30Pred(d), pickT60Pred(d), pickT90Pred(d), pickT45Pred(d), pickT75Pred(d), true);
}

function pickTWRealized(d, predHost) {
  const r30 = pickT30Realized(d, predHost);
  const r60 = pickT60Realized(d, predHost);
  const r90 = pickT90Realized(d, predHost);
  const hosts = [
    predHost && predHost._scan_score_row,
    d && d._scan_score_row,
    predHost && predHost.scores,
    d && d.scores,
    predHost,
    d,
  ].filter((h, i, arr) => h && typeof h === "object" && arr.indexOf(h) === i);
  let r45 = null;
  let r75 = null;
  for (const h of hosts) {
    if (r45 == null) r45 = finiteYhatNum(h.y_t45_realized) ?? finiteYhatNum(h.t45_realized);
    if (r75 == null) r75 = finiteYhatNum(h.y_t75_realized) ?? finiteYhatNum(h.t75_realized);
  }
  const n = blendYtw(r30.n, r60.n, r90.n, r45, r75);
  if (n == null || !Number.isFinite(n)) {
    return { text: "—", n: null, tip: TW_REALIZED_TITLE, agree: null };
  }
  const pred = pickTWPred(predHost) ?? pickTWPred(d);
  const agree = _signAgree(pred, n, 0.5);
  return {
    text: fmtYtwVote(n),
    n,
    tip:
      TW_REALIZED_TITLE +
      (agree === true ? " · 与 ŷ_τw 同号" : agree === false ? " · 与 ŷ_τw 异号" : ""),
    agree,
  };
}

/** 槽位 ŷ_τw：符号和；预估近 50% 弃权，全弃权计 0 票。 */
function twMergedCellHtml(host, dayRef, showRealized = true, scoreDetailJson = "") {
  const predHost = host || dayRef;
  const realHost = dayRef || host;
  const pred = pickTWPred(predHost) ?? pickTWPred(realHost);
  const predTxt = pred != null ? fmtYtwVote(pred) : "—";
  const pr = showRealized
    ? pickTWRealized(realHost, predHost)
    : { n: null, tip: TW_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [pred != null ? `${Y_TW_TITLE} · ŷ_τw=${fmtYtwVote(pred)}` : Y_TW_TITLE];
  if (showRealized) {
    tipParts.push(pr.n != null ? pr.tip : TW_REALIZED_TITLE);
    if (pr.agree === true) tipParts.push("预测与真实同号");
    else if (pr.agree === false) tipParts.push("预测与真实异号");
  }
  const html = fmtPredRealizedHtml(predTxt, pred, pr, showRealized);
  return (
    `<td class="num paper-t0-col-ytw paper-t0-col-y paper-t0-col-y-tw paper-t0-y-score paper-t0-y-merged has-tip${agreeCls}" ` +
    `data-score-tip="tw" data-score-detail="${scoreDetailJson}" ` +
    `title="${escapeText(tipParts.join(" · "))}">` +
    `<span class="paper-t0-y-combo">${html}</span>` +
    `</td>`
  );
}

/** y_τ：回测配对真实值；实时做 T 只显示 ŷ。 */
function yPctMergedCellHtml(kind, d, fallback, rules, scoreDetailJson, titleExtra, showRealized = true, liveByCode = null, realHost = null) {
  const it = t0DayScoreItem(d, fallback, rules, liveByCode);
  const pred = resolveTauScore(it);
  let predNum = pred;
  let predTxt = pred != null ? fmtRtauPct(pred) : "—";
  if (predTxt === "—" && !d?.t0_slot_focus) {
    const ds = d.direction_score;
    if (ds != null && ds !== "" && Number.isFinite(Number(ds))) {
      predNum = Number(ds);
      predTxt = fmtRtauPct(predNum);
    }
  }
  const pr = showRealized
    ? pickTauRealized(realHost || d)
    : { n: null, tip: TAU_REALIZED_TITLE };
  const agreeCls = predRealizedAgreeCls(pr, showRealized);
  const tipParts = [
    predNum != null || predTxt !== "—" ? Y_TAU_TITLE : "暂无 ŷ_oc",
  ];
  if (d?.t0_slot_focus) {
    const slot = focusedSlotRow(d);
    const cb = slotCloseBand(slot) || (d.close_band && typeof d.close_band === "object" ? d.close_band : null);
    if (cb && cb.c_hat_score_source === "bar_prefix") {
      tipParts.push("Ĉ 与该根前缀 ŷ_oc 同源；C=本根5m收价");
    } else if (cb && cb.c_hat_score_source === "open_anchor") {
      tipParts.push("Ĉ 用开盘 OC ŷ_oc（旧包）；请重跑回测");
    } else if (cb && cb.c_hat_score_source === "bar_prefix_live") {
      tipParts.push("该根前缀 ŷ_oc 估 Ĉ（与表列同源）");
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
  const slot = focusedSlotRow(day);
  let hm = String(host.t0_slot_hm || "").trim().slice(0, 5);
  if (!hm && slot) hm = String(slotClock(slot, day?.t0_slot_results) || "").slice(0, 5);
  const scanRow = scanRowForHm(day, hm);
  if (!scanRow) return host;
  if (host.t0_slot_focus) {
    return { ...host, _scan_score_row: scanRow };
  }
  const yTau =
    scanRow.y_oc != null
      ? Number(scanRow.y_oc)
      : scanRow.y_tau != null
        ? Number(scanRow.y_tau)
        : null;
  const yTc = pickYtcModel(scanRow);
  const yR =
    scanRow.predicted_score_r != null
      ? Number(scanRow.predicted_score_r)
      : scanRow.y_r_hat != null
        ? Number(scanRow.y_r_hat)
        : scanRow.y_r != null
          ? Number(scanRow.y_r)
          : null;
  const yT30 =
    scanRow["y_τ30"] != null
      ? Number(scanRow["y_τ30"])
      : scanRow.y_t30 != null
        ? Number(scanRow.y_t30)
        : scanRow.predicted_score_t30 != null
          ? Number(scanRow.predicted_score_t30)
          : scanRow.y_t30_hat != null
            ? Number(scanRow.y_t30_hat)
            : null;
  const yHl =
    scanRow.y_hl != null
      ? Number(scanRow.y_hl)
      : scanRow.predicted_score_hl != null
        ? Number(scanRow.predicted_score_hl)
        : scanRow.y_path != null
          ? Number(scanRow.y_path)
          : scanRow.predicted_score_path != null
            ? Number(scanRow.predicted_score_path)
            : null;
  const yT60 =
    scanRow["y_τ60"] != null
      ? Number(scanRow["y_τ60"])
      : scanRow.y_t60 != null
        ? Number(scanRow.y_t60)
        : scanRow.predicted_score_t60 != null
          ? Number(scanRow.predicted_score_t60)
          : scanRow.y_t60_hat != null
            ? Number(scanRow.y_t60_hat)
            : null;
  const yT90 =
    scanRow["y_τ90"] != null
      ? Number(scanRow["y_τ90"])
      : scanRow.y_t90 != null
        ? Number(scanRow.y_t90)
        : scanRow.predicted_score_t90 != null
          ? Number(scanRow.predicted_score_t90)
          : scanRow.y_t90_hat != null
            ? Number(scanRow.y_t90_hat)
            : null;
  const scores = { ...(host.scores && typeof host.scores === "object" ? host.scores : {}) };
  if (yTau != null && Number.isFinite(yTau)) {
    scores.y_tau = yTau;
    scores.y_oc = yTau;
    scores.y_tau_oc = yTau;
    scores.predicted_score_tau_oc = yTau;
    scores.predicted_score_tau = yTau;
    scores.score_rem = yTau;
  }
  if (yR != null && Number.isFinite(yR)) {
    scores.predicted_score_r = yR;
    scores.y_r_hat = yR;
    scores.y_r = yR;
  }
  if (yTc != null && Number.isFinite(yTc)) {
    scores["y_τc"] = yTc;
    scores.y_tc = yTc;
    scores.predicted_score_τc = yTc;
    scores.y_τc_source = "ridge";
  }
  const yTcRidge = finiteYhatNum(scanRow.y_τc_ridge) ?? (yR != null && Number.isFinite(yR) ? yR : null);
  if (yTcRidge != null) scores.y_τc_ridge = yTcRidge;
  if (scanRow.y_τc_source) scores.y_τc_source = scanRow.y_τc_source === "remaining_oc" ? "ridge" : scanRow.y_τc_source;
  const rHat =
    scanRow.r_hat != null
      ? Number(scanRow.r_hat)
      : scanRow.residual != null
        ? Number(scanRow.residual)
        : scanRow.remaining_oc != null
          ? Number(scanRow.remaining_oc)
          : null;
  if (rHat != null && Number.isFinite(rHat)) {
    scores.r_hat = rHat;
    scores.residual = rHat;
    scores.remaining_oc = rHat;
  }
  const rReal =
    scanRow.r_realized != null
      ? Number(scanRow.r_realized)
      : scanRow.y_r_realized != null
        ? Number(scanRow.y_r_realized)
        : null;
  if (rReal != null && Number.isFinite(rReal)) {
    scores.r_realized = rReal;
    scores.y_r_realized = rReal;
  }
  if (yT30 != null && Number.isFinite(yT30)) {
    scores["y_τ30"] = yT30;
    scores.y_t30 = yT30;
    scores.predicted_score_t30 = yT30;
    scores.y_t30_hat = yT30;
  }
  if (yHl != null && Number.isFinite(yHl)) {
    scores.y_hl = yHl;
    scores.predicted_score_hl = yHl;
  }
  const t30Real =
    scanRow.y_t30_realized != null
      ? Number(scanRow.y_t30_realized)
      : scanRow.t30_realized != null
        ? Number(scanRow.t30_realized)
        : null;
  if (t30Real != null && Number.isFinite(t30Real)) {
    scores.y_t30_realized = t30Real;
    scores.t30_realized = t30Real;
  }
  if (yT60 != null && Number.isFinite(yT60)) {
    scores["y_τ60"] = yT60;
    scores.y_t60 = yT60;
    scores.predicted_score_t60 = yT60;
    scores.y_t60_hat = yT60;
  }
  const t60Real =
    scanRow.y_t60_realized != null
      ? Number(scanRow.y_t60_realized)
      : scanRow.t60_realized != null
        ? Number(scanRow.t60_realized)
        : null;
  if (t60Real != null && Number.isFinite(t60Real)) {
    scores.y_t60_realized = t60Real;
    scores.t60_realized = t60Real;
  }
  if (yT90 != null && Number.isFinite(yT90)) {
    scores["y_τ90"] = yT90;
    scores.y_t90 = yT90;
    scores.predicted_score_t90 = yT90;
    scores.y_t90_hat = yT90;
  }
  const t90Real =
    scanRow.y_t90_realized != null
      ? Number(scanRow.y_t90_realized)
      : scanRow.t90_realized != null
        ? Number(scanRow.t90_realized)
        : null;
  if (t90Real != null && Number.isFinite(t90Real)) {
    scores.y_t90_realized = t90Real;
    scores.t90_realized = t90Real;
  }
  const yTw = scanYtwPred(scanRow);
  if (yTw != null && Number.isFinite(yTw)) {
    scores.y_tw = yTw;
    scores["y_τw"] = yTw;
    scores.y_tw_hat = yTw;
  }
  return {
    ...host,
    y_tau: yTau != null && Number.isFinite(yTau) ? yTau : host.y_tau,
    predicted_score_r: yR != null && Number.isFinite(yR) ? yR : host.predicted_score_r,
    y_r_hat: yR != null && Number.isFinite(yR) ? yR : host.y_r_hat,
    y_r: yR != null && Number.isFinite(yR) ? yR : host.y_r,
    y_oc: yTau != null && Number.isFinite(yTau) ? yTau : host.y_oc,
    "y_τc": yTc != null && Number.isFinite(yTc) ? yTc : host["y_τc"],
    y_tc: yTc != null && Number.isFinite(yTc) ? yTc : host.y_tc,
    "y_τ30": yT30 != null && Number.isFinite(yT30) ? yT30 : host["y_τ30"],
    y_t30: yT30 != null && Number.isFinite(yT30) ? yT30 : host.y_t30,
    predicted_score_t30:
      yT30 != null && Number.isFinite(yT30) ? yT30 : host.predicted_score_t30,
    y_t30_hat: yT30 != null && Number.isFinite(yT30) ? yT30 : host.y_t30_hat,
    y_hl: yHl != null && Number.isFinite(yHl) ? yHl : host.y_hl,
    predicted_score_hl:
      yHl != null && Number.isFinite(yHl) ? yHl : host.predicted_score_hl,
    y_t30_realized:
      t30Real != null && Number.isFinite(t30Real) ? t30Real : host.y_t30_realized,
    t30_realized: t30Real != null && Number.isFinite(t30Real) ? t30Real : host.t30_realized,
    "y_τ60": yT60 != null && Number.isFinite(yT60) ? yT60 : host["y_τ60"],
    y_t60: yT60 != null && Number.isFinite(yT60) ? yT60 : host.y_t60,
    predicted_score_t60:
      yT60 != null && Number.isFinite(yT60) ? yT60 : host.predicted_score_t60,
    y_t60_hat: yT60 != null && Number.isFinite(yT60) ? yT60 : host.y_t60_hat,
    y_t60_realized:
      t60Real != null && Number.isFinite(t60Real) ? t60Real : host.y_t60_realized,
    t60_realized: t60Real != null && Number.isFinite(t60Real) ? t60Real : host.t60_realized,
    "y_τ90": yT90 != null && Number.isFinite(yT90) ? yT90 : host["y_τ90"],
    y_t90: yT90 != null && Number.isFinite(yT90) ? yT90 : host.y_t90,
    predicted_score_t90:
      yT90 != null && Number.isFinite(yT90) ? yT90 : host.predicted_score_t90,
    y_t90_hat: yT90 != null && Number.isFinite(yT90) ? yT90 : host.y_t90_hat,
    y_t90_realized:
      t90Real != null && Number.isFinite(t90Real) ? t90Real : host.y_t90_realized,
    t90_realized: t90Real != null && Number.isFinite(t90Real) ? t90Real : host.t90_realized,
    y_tw: yTw != null && Number.isFinite(yTw) ? yTw : host.y_tw,
    "y_τw": yTw != null && Number.isFinite(yTw) ? yTw : host["y_τw"],
    y_tw_hat: yTw != null && Number.isFinite(yTw) ? yTw : host.y_tw_hat,
    r_realized: rReal != null && Number.isFinite(rReal) ? rReal : host.r_realized,
    y_r_realized: rReal != null && Number.isFinite(rReal) ? rReal : host.y_r_realized,
    r_hat: rHat != null && Number.isFinite(rHat) ? rHat : host.r_hat,
    residual: rHat != null && Number.isFinite(rHat) ? rHat : host.residual,
    remaining_oc: rHat != null && Number.isFinite(rHat) ? rHat : host.remaining_oc,
    y_τc_ridge: yTcRidge != null ? yTcRidge : host.y_τc_ridge,
    y_τc_source: scores.y_τc_source || host.y_τc_source,
    c_tau: finitePosPx(scanRow.c_tau ?? scanRow.c_hat) ?? host.c_tau,
    bar_c: finitePosPx(scanRow.c) ?? host.bar_c,
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
  let yR =
    pickScoreNum(d, "predicted_score_r") ??
    pickScoreNum(d, "y_r_hat") ??
    pickScoreNum(d, "y_r");
  let yT30 =
    pickScoreNum(d, "y_τ30") ??
    pickScoreNum(d, "y_t30") ??
    pickScoreNum(d, "predicted_score_t30") ??
    pickScoreNum(d, "y_t30_hat");
  let yHl =
    pickScoreNum(d, "y_hl") ??
    pickScoreNum(d, "predicted_score_hl") ??
    pickScoreNum(d, "y_path") ??
    pickScoreNum(d, "predicted_score_path");
  let yT60 =
    pickScoreNum(d, "y_τ60") ??
    pickScoreNum(d, "y_t60") ??
    pickScoreNum(d, "predicted_score_t60") ??
    pickScoreNum(d, "y_t60_hat");
  let yT90 =
    pickScoreNum(d, "y_τ90") ??
    pickScoreNum(d, "y_t90") ??
    pickScoreNum(d, "predicted_score_t90") ??
    pickScoreNum(d, "y_t90_hat");
  if (slotSnapMode && slot) {
    const yhat = slotYhat(slot, d);
    if (yhat.y_tau != null) yTau = yhat.y_tau;
    if (yhat.y_r != null) yR = yhat.y_r;
    if (yhat.y_hl != null) yHl = yhat.y_hl;
    if (yhat.y_t30 != null) yT30 = yhat.y_t30;
    if (yhat.y_t60 != null) yT60 = yhat.y_t60;
    if (yhat.y_t90 != null) yT90 = yhat.y_t90;
    if (yhat.y_trade != null) yTrade = yhat.y_trade;
  }
  const scanHost = d?._scan_score_row ? d : null;
  if (scanHost?._scan_score_row) {
    const sr = scanHost._scan_score_row;
    if (sr.y_tau != null && Number.isFinite(Number(sr.y_tau))) yTau = Number(sr.y_tau);
    const srR = sr.predicted_score_r ?? sr.y_r_hat ?? sr.y_r;
    if (srR != null && Number.isFinite(Number(srR))) {
      scores.predicted_score_r = Number(srR);
      scores.y_r_hat = Number(srR);
      scores.y_r = Number(srR);
      yR = Number(srR);
    }
    const srT30 = sr["y_τ30"] ?? sr.y_t30 ?? sr.predicted_score_t30 ?? sr.y_t30_hat;
    if (srT30 != null && Number.isFinite(Number(srT30))) {
      scores["y_τ30"] = Number(srT30);
      scores.y_t30 = Number(srT30);
      scores.predicted_score_t30 = Number(srT30);
      scores.y_t30_hat = Number(srT30);
      yT30 = Number(srT30);
    }
    const srT30r = sr.y_t30_realized ?? sr.t30_realized;
    if (srT30r != null && Number.isFinite(Number(srT30r))) {
      scores.y_t30_realized = Number(srT30r);
      scores.t30_realized = Number(srT30r);
    }
    const srT45 = sr["y_τ45"] ?? sr.y_t45 ?? sr.predicted_score_t45 ?? sr.y_t45_hat;
    if (srT45 != null && Number.isFinite(Number(srT45))) {
      scores["y_τ45"] = Number(srT45);
      scores.y_t45 = Number(srT45);
      scores.predicted_score_t45 = Number(srT45);
      scores.y_t45_hat = Number(srT45);
    }
    const srT45r = sr.y_t45_realized ?? sr.t45_realized;
    if (srT45r != null && Number.isFinite(Number(srT45r))) {
      scores.y_t45_realized = Number(srT45r);
      scores.t45_realized = Number(srT45r);
    }
    const srT60 = sr["y_τ60"] ?? sr.y_t60 ?? sr.predicted_score_t60 ?? sr.y_t60_hat;
    if (srT60 != null && Number.isFinite(Number(srT60))) {
      scores["y_τ60"] = Number(srT60);
      scores.y_t60 = Number(srT60);
      scores.predicted_score_t60 = Number(srT60);
      scores.y_t60_hat = Number(srT60);
      yT60 = Number(srT60);
    }
    const srT60r = sr.y_t60_realized ?? sr.t60_realized;
    if (srT60r != null && Number.isFinite(Number(srT60r))) {
      scores.y_t60_realized = Number(srT60r);
      scores.t60_realized = Number(srT60r);
    }
    const srT75 = sr["y_τ75"] ?? sr.y_t75 ?? sr.predicted_score_t75 ?? sr.y_t75_hat;
    if (srT75 != null && Number.isFinite(Number(srT75))) {
      scores["y_τ75"] = Number(srT75);
      scores.y_t75 = Number(srT75);
      scores.predicted_score_t75 = Number(srT75);
      scores.y_t75_hat = Number(srT75);
    }
    const srT75r = sr.y_t75_realized ?? sr.t75_realized;
    if (srT75r != null && Number.isFinite(Number(srT75r))) {
      scores.y_t75_realized = Number(srT75r);
      scores.t75_realized = Number(srT75r);
    }
    const srT90 = sr["y_τ90"] ?? sr.y_t90 ?? sr.predicted_score_t90 ?? sr.y_t90_hat;
    if (srT90 != null && Number.isFinite(Number(srT90))) {
      scores["y_τ90"] = Number(srT90);
      scores.y_t90 = Number(srT90);
      scores.predicted_score_t90 = Number(srT90);
      scores.y_t90_hat = Number(srT90);
      yT90 = Number(srT90);
    }
    const srHl = sr.y_hl ?? sr.predicted_score_hl ?? sr.y_path ?? sr.predicted_score_path;
    if (srHl != null && Number.isFinite(Number(srHl))) {
      scores.y_hl = Number(srHl);
      scores.predicted_score_hl = Number(srHl);
      yHl = Number(srHl);
    }
    const srT90r = sr.y_t90_realized ?? sr.t90_realized;
    if (srT90r != null && Number.isFinite(Number(srT90r))) {
      scores.y_t90_realized = Number(srT90r);
      scores.t90_realized = Number(srT90r);
    }
    const srTw = finiteYhatNum(sr.y_tw) ?? finiteYhatNum(sr["y_τw"]) ?? finiteYhatNum(sr.y_tw_hat);
    if (srTw != null) {
      scores.y_tw = srTw;
      scores["y_τw"] = srTw;
      scores.y_tw_hat = srTw;
    }
    const srRhat = sr.r_hat ?? sr.residual ?? sr.remaining_oc;
    if (srRhat != null && Number.isFinite(Number(srRhat))) {
      scores.r_hat = Number(srRhat);
      scores.residual = Number(srRhat);
    }
    if (sr.features_tau && typeof sr.features_tau === "object") {
      scores.features_tau = sr.features_tau;
    }
    for (const k of Object.keys(sr)) {
      if (
        (k.startsWith("formula_terms_") || k.startsWith("score_formula_terms_")) &&
        sr[k] != null
      ) {
        scores[k] = sr[k];
      }
    }
    if (sr.as_of_tau || sr.hm) scores.as_of_tau = sr.as_of_tau || sr.hm;
    if (sr.gap_pct != null) scores.gap_pct = sr.gap_pct;
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
  const liveTrade = slotSnapMode ? null : live ? resolveYTradeScore(live) : null;
  const liveOn = slotSnapMode ? null : live ? resolveOnScore(live) : null;
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
  const liveTips = slotSnapMode ? null : live;
  const filledFromLive =
    !slotSnapMode &&
    live &&
    ((dayEod == null && liveEod != null) ||
      (dayTau == null && liveTau != null) ||
      (dayTrade == null && liveTrade != null));
  return {
    ...scores,
    stock_code: code || null,
    stock_name: d.stock_name || fallback.stock_name || scores.stock_name || null,
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
    predicted_score_r: slotSnapMode ? yR : scores.predicted_score_r ?? yR,
    y_r_hat: slotSnapMode ? yR : scores.y_r_hat ?? yR,
    y_r: slotSnapMode ? yR : scores.y_r ?? yR,
    r_hat: scores.r_hat ?? d.r_hat ?? null,
    residual: scores.residual ?? scores.r_hat ?? d.residual ?? null,
    remaining_oc: scores.remaining_oc ?? d.remaining_oc ?? null,
    c_tau: finitePosPx(d.c_tau) ?? finitePosPx(scanHost?._scan_score_row?.c_tau) ?? finitePosPx(slotCloseBand(slot)?.c_tau),
    bar_c: finitePosPx(d.bar_c) ?? finitePosPx(scanHost?._scan_score_row?.c),
    y_oc_target:
      finiteYhatNum(scores.y_oc_target) ??
      finiteYhatNum(d.y_oc_target) ??
      finiteYhatNum(slotCloseBand(slot)?.y_oc_target),
    t0_y_oc_target_scale:
      finiteYhatNum(scores.t0_y_oc_target_scale) ??
      finiteYhatNum(d.t0_y_oc_target_scale) ??
      finiteYhatNum(slotCloseBand(slot)?.t0_y_oc_target_scale),
    y_τc_ridge: scores.y_τc_ridge ?? d.y_τc_ridge ?? null,
    y_τc_source: scores.y_τc_source ?? d.y_τc_source ?? null,
    "y_τc":
      pickYtcModel({ ...scores, ...d }) ??
      scores["y_τc"] ??
      scores.predicted_score_τc ??
      (slotSnapMode ? yR : scores.y_r ?? yR),
    predicted_score_τc:
      scores.predicted_score_τc ??
      scores["y_τc"] ??
      (slotSnapMode ? yR : scores.predicted_score_r ?? yR),
    y_hl: slotSnapMode ? yHl : scores.y_hl ?? scores.predicted_score_hl ?? scores.y_path ?? yHl,
    predicted_score_hl:
      slotSnapMode ? yHl : scores.predicted_score_hl ?? scores.y_hl ?? scores.y_path ?? yHl,
    path_realized: scores.path_realized ?? d.path_realized ?? null,
    y_path_realized: scores.y_path_realized ?? d.y_path_realized ?? null,
    formula_terms_path:
      scores.formula_terms_path ||
      scores.score_formula_terms_path ||
      liveTips?.formula_terms_path ||
      liveTips?.score_formula_terms_path ||
      null,
    score_formula_terms_path:
      scores.score_formula_terms_path ||
      scores.formula_terms_path ||
      liveTips?.score_formula_terms_path ||
      liveTips?.formula_terms_path ||
      null,
    "y_τ30": slotSnapMode ? yT30 : scores["y_τ30"] ?? scores.y_t30 ?? yT30,
    y_t30: slotSnapMode ? yT30 : scores.y_t30 ?? scores["y_τ30"] ?? yT30,
    predicted_score_t30:
      slotSnapMode ? yT30 : scores.predicted_score_t30 ?? scores.y_t30_hat ?? yT30,
    y_t30_hat: slotSnapMode ? yT30 : scores.y_t30_hat ?? scores.predicted_score_t30 ?? yT30,
    y_t30_realized: scores.y_t30_realized ?? d.y_t30_realized ?? null,
    t30_realized: scores.t30_realized ?? d.t30_realized ?? null,
    "y_τ60": slotSnapMode ? yT60 : scores["y_τ60"] ?? scores.y_t60 ?? yT60,
    y_t60: slotSnapMode ? yT60 : scores.y_t60 ?? scores["y_τ60"] ?? yT60,
    predicted_score_t60:
      slotSnapMode ? yT60 : scores.predicted_score_t60 ?? scores.y_t60_hat ?? yT60,
    y_t60_hat: slotSnapMode ? yT60 : scores.y_t60_hat ?? scores.predicted_score_t60 ?? yT60,
    y_t60_realized: scores.y_t60_realized ?? d.y_t60_realized ?? null,
    t60_realized: scores.t60_realized ?? d.t60_realized ?? null,
    "y_τ90": slotSnapMode ? yT90 : scores["y_τ90"] ?? scores.y_t90 ?? yT90,
    y_t90: slotSnapMode ? yT90 : scores.y_t90 ?? scores["y_τ90"] ?? yT90,
    predicted_score_t90:
      slotSnapMode ? yT90 : scores.predicted_score_t90 ?? scores.y_t90_hat ?? yT90,
    y_t90_hat: slotSnapMode ? yT90 : scores.y_t90_hat ?? scores.predicted_score_t90 ?? yT90,
    y_t90_realized: scores.y_t90_realized ?? d.y_t90_realized ?? null,
    t90_realized: scores.t90_realized ?? d.t90_realized ?? null,
    y_tw: scores.y_tw ?? scores["y_τw"] ?? scores.y_tw_hat ?? null,
    "y_τw": scores["y_τw"] ?? scores.y_tw ?? scores.y_tw_hat ?? null,
    y_tw_hat: scores.y_tw_hat ?? scores.y_tw ?? scores["y_τw"] ?? null,
    y_spec_τc: scores.y_spec_τc || live?.y_spec_τc || null,
    y_spec_r: scores.y_spec_r || live?.y_spec_r || null,
    formula_terms_r:
      scores.formula_terms_r ||
      scores.score_formula_terms_r ||
      liveTips?.formula_terms_r ||
      liveTips?.score_formula_terms_r ||
      null,
    score_formula_terms_r:
      scores.score_formula_terms_r ||
      scores.formula_terms_r ||
      liveTips?.score_formula_terms_r ||
      liveTips?.formula_terms_r ||
      null,
    y_spec_τ30: scores.y_spec_τ30 || scores.y_spec_t30 || live?.y_spec_τ30 || live?.y_spec_t30 || null,
    y_spec_t30: scores.y_spec_t30 || scores.y_spec_τ30 || live?.y_spec_t30 || live?.y_spec_τ30 || null,
    formula_terms_t30:
      scores.formula_terms_t30 ||
      scores.score_formula_terms_t30 ||
      liveTips?.formula_terms_t30 ||
      liveTips?.score_formula_terms_t30 ||
      null,
    score_formula_terms_t30:
      scores.score_formula_terms_t30 ||
      scores.formula_terms_t30 ||
      liveTips?.score_formula_terms_t30 ||
      liveTips?.formula_terms_t30 ||
      null,
    y_spec_τ45: scores.y_spec_τ45 || scores.y_spec_t45 || liveTips?.y_spec_τ45 || liveTips?.y_spec_t45 || null,
    y_spec_t45: scores.y_spec_t45 || scores.y_spec_τ45 || liveTips?.y_spec_t45 || liveTips?.y_spec_τ45 || null,
    formula_terms_t45:
      scores.formula_terms_t45 ||
      scores.score_formula_terms_t45 ||
      liveTips?.formula_terms_t45 ||
      liveTips?.score_formula_terms_t45 ||
      null,
    score_formula_terms_t45:
      scores.score_formula_terms_t45 ||
      scores.formula_terms_t45 ||
      liveTips?.score_formula_terms_t45 ||
      liveTips?.formula_terms_t45 ||
      null,
    y_spec_τ60: scores.y_spec_τ60 || scores.y_spec_t60 || live?.y_spec_τ60 || live?.y_spec_t60 || null,
    y_spec_t60: scores.y_spec_t60 || scores.y_spec_τ60 || live?.y_spec_t60 || live?.y_spec_τ60 || null,
    formula_terms_t60:
      scores.formula_terms_t60 ||
      scores.score_formula_terms_t60 ||
      liveTips?.formula_terms_t60 ||
      liveTips?.score_formula_terms_t60 ||
      null,
    score_formula_terms_t60:
      scores.score_formula_terms_t60 ||
      scores.formula_terms_t60 ||
      liveTips?.score_formula_terms_t60 ||
      liveTips?.formula_terms_t60 ||
      null,
    y_spec_τ75: scores.y_spec_τ75 || scores.y_spec_t75 || liveTips?.y_spec_τ75 || liveTips?.y_spec_t75 || null,
    y_spec_t75: scores.y_spec_t75 || scores.y_spec_τ75 || liveTips?.y_spec_t75 || liveTips?.y_spec_τ75 || null,
    formula_terms_t75:
      scores.formula_terms_t75 ||
      scores.score_formula_terms_t75 ||
      liveTips?.formula_terms_t75 ||
      liveTips?.score_formula_terms_t75 ||
      null,
    score_formula_terms_t75:
      scores.score_formula_terms_t75 ||
      scores.formula_terms_t75 ||
      liveTips?.score_formula_terms_t75 ||
      liveTips?.formula_terms_t75 ||
      null,
    y_spec_τ90: scores.y_spec_τ90 || scores.y_spec_t90 || live?.y_spec_τ90 || live?.y_spec_t90 || null,
    y_spec_t90: scores.y_spec_t90 || scores.y_spec_τ90 || live?.y_spec_t90 || live?.y_spec_τ90 || null,
    formula_terms_t90:
      scores.formula_terms_t90 ||
      scores.score_formula_terms_t90 ||
      liveTips?.formula_terms_t90 ||
      liveTips?.score_formula_terms_t90 ||
      null,
    score_formula_terms_t90:
      scores.score_formula_terms_t90 ||
      scores.formula_terms_t90 ||
      liveTips?.score_formula_terms_t90 ||
      liveTips?.formula_terms_t90 ||
      null,
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
    as_of_tau: scores.as_of_tau || feats.as_of_tau || d.t0_slot_hm || live?.as_of_tau || null,
    y_to: scores.y_to ?? scores.y_pc ?? yR,
    y_pc: scores.y_pc ?? scores.y_to ?? yR,
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
    predicted_score_eod_rem:
      scores.predicted_score_eod_rem ?? live?.predicted_score_eod_rem ?? null,
    y_spec_tau: scores.y_spec_tau || live?.y_spec_tau || null,
    features_tau: scores.features_tau || feats.features_tau || liveTips?.features_tau || null,
    score_formula_terms:
      scores.score_formula_terms ||
      scores.formula_terms ||
      liveTips?.score_formula_terms ||
      liveTips?.formula_terms ||
      null,
    formula_terms:
      scores.score_formula_terms ||
      scores.formula_terms ||
      liveTips?.score_formula_terms ||
      liveTips?.formula_terms ||
      null,
    factor_coefficients: scores.factor_coefficients || live?.factor_coefficients || null,
    formula_terms_tau:
      scores.formula_terms_tau ||
      scores.score_formula_terms_tau ||
      liveTips?.formula_terms_tau ||
      liveTips?.score_formula_terms_tau ||
      null,
    score_formula_terms_tau:
      scores.score_formula_terms_tau ||
      scores.formula_terms_tau ||
      liveTips?.score_formula_terms_tau ||
      liveTips?.formula_terms_tau ||
      null,
    factor_coefficients_tau:
      scores.factor_coefficients_tau || live?.factor_coefficients_tau || null,
    formula_terms_on:
      scores.formula_terms_on ||
      scores.score_formula_terms_on ||
      liveTips?.formula_terms_on ||
      liveTips?.score_formula_terms_on ||
      null,
    score_formula_terms_on:
      scores.score_formula_terms_on ||
      scores.formula_terms_on ||
      liveTips?.score_formula_terms_on ||
      liveTips?.formula_terms_on ||
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
  const time = fmtT0LegTime(t?.at) || fmtT0LegTime(t?.t0_slot_hm);
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
  lock_win: { label: "赢", cls: "is-stop" },
  giveback: { label: "锁", cls: "is-stop" }, // 旧账本兼容
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
        (slotLegs[0] && slotLegs[0].slotHm) ||
          r.hm ||
          r.t0_slot_hm ||
          (String(r.id || "") === String(d?.t0_slot || "") ? d.t0_slot_hm : "") ||
          (slotLegs[0] && slotLegs[0].time) ||
          ""
      ),
      dir: String(r.direction || ""),
      legs: slotLegs,
      skipped: !!r.skipped && !slotLegs.length,
      reason: String(r.reason || ""),
      scores: r.scores && typeof r.scores === "object" ? r.scores : null,
      yTau: slotYhat(r, d).y_tau,
      closeBand: slotCloseBand(r),
    });
  }
  for (const [id, slotLegs] of byId) {
    if (seen.has(id) || !slotLegs.length) continue;
    groups.push({
      id,
      hm: String((slotLegs[0] && (slotLegs[0].slotHm || slotLegs[0].time)) || ""),
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
  if (Number.isFinite(yt)) bits.push(`τ${yt.toFixed(1)}`);
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
  const showTimes =
    !!d.minute_path || collectLegRecords(d).some((l) => !!l.time);
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

function slotOhlcFromScan(host, hm) {
  const r = scanRowForHm(host, hm);
  if (!r) return { o: null, l: null, h: null, c: null };
  return {
    o: _finitePx(r.o),
    l: _finitePx(r.l),
    h: _finitePx(r.h),
    c: _finitePx(r.c),
  };
}

function firstLegClock(host) {
  const legs = collectLegRecords(host);
  for (const l of legs) {
    const hm = String((l && (l.slotHm || l.time)) || "").trim();
    if (hm) return hm.slice(0, 5);
  }
  return "";
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

function finitePctNum(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function closeBandDeltaPct(rules, host, scan) {
  const fromScan = finitePctNum(scan && scan.delta_pct);
  if (fromScan != null && fromScan >= 0) return fromScan;
  const cb = slotCloseBand(host);
  const fromCb = finitePctNum(cb && (cb.delta_pct ?? cb.t0_close_band_delta_pct));
  if (fromCb != null && fromCb >= 0) return fromCb;
  const dayCb =
    host && host.close_band && typeof host.close_band === "object" ? host.close_band : null;
  const fromDay = finitePctNum(dayCb && (dayCb.delta_pct ?? dayCb.t0_close_band_delta_pct));
  if (fromDay != null && fromDay >= 0) return fromDay;
  const fromRules = finitePctNum(rules && rules.t0_close_band_delta_pct);
  if (fromRules != null && fromRules >= 0) return fromRules;
  return 3;
}

function pickBandThreshPct(obj, keys, fallback) {
  if (obj && typeof obj === "object") {
    for (const k of keys || []) {
      const n = finitePctNum(obj[k]);
      if (n != null) return n;
    }
  }
  return fallback;
}

function closeBandEdgePx(midPx, pct) {
  const c = Number(midPx);
  const p = Number(pct);
  if (!(Number.isFinite(c) && c > 0) || !Number.isFinite(p)) return null;
  return c * (1 + p / 100);
}

function fmtBandPctTip(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const digits = abs < 0.1 && abs > 0 ? 3 : 2;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function tradeBandEdgeTd(kind, px, tip) {
  const col = kind === "upper" ? "band-up" : "band-lo";
  const tone = kind === "upper" ? " is-hi" : " is-lo";
  const tipAttr = tip ? ` title="${escapeText(tip)}"` : "";
  const v = Number(px);
  if (!Number.isFinite(v) || !(v > 0)) {
    return `<td class="num paper-t0-col-${col} is-na${tone}"${tipAttr}>—</td>`;
  }
  return `<td class="num paper-t0-col-${col}${tone}"${tipAttr}>${escapeText(fmtBarPx(v))}</td>`;
}

function tradeBandEdgeCells(cTau, host, rules, scan) {
  const delta = closeBandDeltaPct(rules, host, scan);
  const cb = slotCloseBand(host) || {};
  const src = scan && typeof scan === "object" ? scan : cb;
  const loStored = finitePosPx(src.lower_px) ?? finitePosPx(cb.lower_px);
  const upStored = finitePosPx(src.upper_px) ?? finitePosPx(cb.upper_px);
  const lowerPct = pickBandThreshPct(
    src,
    ["lower_pct", "band_lower_pct"],
    pickBandThreshPct(cb, ["band_lower_pct", "lower_pct"], -delta)
  );
  const upperPct = pickBandThreshPct(
    src,
    ["upper_pct", "band_upper_pct"],
    pickBandThreshPct(cb, ["band_upper_pct", "upper_pct"], delta)
  );
  const lo = loStored ?? closeBandEdgePx(cTau, lowerPct);
  const up = upStored ?? closeBandEdgePx(cTau, upperPct);
  const loTip = `破带下沿 = C_τ×(1−δ/100) · ${fmtBandPctTip(lowerPct)} · C<lower → 正T`;
  const upTip = `破带上沿 = C_τ×(1+δ/100) · ${fmtBandPctTip(upperPct)} · C>upper → 反T`;
  return tradeBandEdgeTd("lower", lo, loTip) + tradeBandEdgeTd("upper", up, upTip);
}

function finiteRtauNum(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtRtauPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs < 0.1 && abs > 0 ? 3 : 2;
  const body = v.toFixed(digits);
  return `${v > 0 ? "+" : ""}${body}%`;
}

/** 成交主表：Ĉ_τ（主显分钟映后；有日原则旁注）。 */
function tradeCtauTd(minutePx, dailyPx) {
  const m = Number(minutePx);
  const d = Number(dailyPx);
  const mOk = Number.isFinite(m) && m > 0;
  const dOk = Number.isFinite(d) && d > 0;
  const bits = ["C_τ = O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)"];
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

/** 本轮触发根 OHLC + Ĉ_τ + 价带沿。 */
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
  if (!hm) hm = firstLegClock(host);
  let ohlc = hm ? slotOhlcFromBars(bars, hm) : { o: null, l: null, h: null, c: null };
  if (ohlc.o == null && ohlc.c == null) {
    ohlc = slotOhlcFromScan(host, hm);
  }
  if (ohlc.o == null && ohlc.c == null) {
    ohlc = {
      o: _finitePx(host && host.open),
      l: _finitePx(host && host.low),
      h: _finitePx(host && host.high),
      c: _finitePx(host && host.close),
    };
  }
  const parts = slotClosePxParts(slotCloseBand(host) ? host : slotRow || host);
  const scan = hm ? scanRowForHm(host, hm) : host && host._scan_score_row;
  const bandHost = slotCloseBand(host) ? host : slotRow || host;
  const cTauMin =
    finitePosPx(scan && (scan.c_tau ?? scan.c_hat)) ??
    finitePosPx(parts.c_tau) ??
    finitePosPx(parts.c_hat) ??
    finitePosPx(host && host.c_tau);
  const cTauDaily = finitePosPx(parts.c_tau_daily) ?? finitePosPx(parts.c_hat_daily);
  return (
    tradeBarPxTd("o", ohlc.o, "本轮触发根 5m 开盘") +
    tradeBarPxTd("l", ohlc.l, "本轮触发根 5m 最低") +
    tradeBarPxTd("h", ohlc.h, "本轮触发根 5m 最高") +
    tradeBarPxTd("c", ohlc.c, "本轮触发根 5m 收盘") +
    tradeCtauTd(cTauMin, cTauDaily) +
    tradeBandEdgeCells(cTauMin, bandHost, rules, scan)
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
  const num = (v) => {
    if (v == null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  if (!cb) {
    return { c_tau: null, c_hat: null, c_tau_daily: null, c_hat_daily: null };
  }
  return {
    c_tau: num(cb.c_tau),
    c_hat: num(cb.close_px),
    c_tau_daily: num(cb.c_tau_daily),
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
    t0Col("paper-t0-col-band-lo", "bandLo") +
    t0Col("paper-t0-col-band-up", "bandUp") +
    t0Col("paper-t0-col-tau", "tau") +
    t0Col("paper-t0-col-ytw", "ytw") +
    t0Col("paper-t0-col-yt30", "yt30") +
    t0Col("paper-t0-col-yt45", "yt45") +
    t0Col("paper-t0-col-yt60", "yt60") +
    t0Col("paper-t0-col-yt75", "yt75") +
    t0Col("paper-t0-col-yt90", "yt90") +
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

function hostFromTradeGroup(d, slotTrades, sid) {
  const first = slotTrades && slotTrades[0];
  const hm =
    String((first && first.t0_slot_hm) || "").trim() ||
    fmtT0LegTime(first && first.at) ||
    "";
  return {
    ...d,
    t0_slot: String(sid || ""),
    t0_slot_hm: hm,
    t0_slot_focus: true,
    minute_path: true,
    trades: slotTrades,
    sell_price: undefined,
    buy_price: undefined,
    sell_at: undefined,
    buy_at: undefined,
    sell_shares: undefined,
    buy_shares: undefined,
    forward_trace: remaskTraceFills(d.forward_trace, slotTrades),
  };
}

function pairOppositeLegs(trades) {
  const sorted = (trades || [])
    .slice()
    .sort((a, b) => tradeAtSortKey(a && a.at) - tradeAtSortKey(b && b.at));
  const groups = [];
  let i = 0;
  while (i < sorted.length) {
    const a = sorted[i];
    const b = sorted[i + 1];
    const sa = legSideKind(a && a.side);
    const sb = b ? legSideKind(b.side) : "";
    if (sa && sb && sa !== sb) {
      groups.push([a, b]);
      i += 2;
    } else {
      groups.push([a]);
      i += 1;
    }
  }
  return groups;
}

function hostsFromTrades(d) {
  const trades = (Array.isArray(d?.trades) ? d.trades : []).filter(
    (t) => t && (t.side || t.shares || t.price)
  );
  if (trades.length <= 1) return [d];
  const bySlot = new Map();
  for (const t of trades) {
    const id = String(t.t0_slot || "").trim();
    if (!id) continue;
    if (!bySlot.has(id)) bySlot.set(id, []);
    bySlot.get(id).push(t);
  }
  if (bySlot.size > 1) {
    return [...bySlot.entries()].map(([sid, slotTrades]) =>
      hostFromTradeGroup(d, slotTrades, sid)
    );
  }
  const paired = pairOppositeLegs(trades);
  if (paired.length > 1) {
    return paired.map((group) =>
      hostFromTradeGroup(d, group, (group[0] && group[0].t0_slot) || "")
    );
  }
  return [d];
}

function tradeDaySlotHosts(d, splitSlots) {
  if (!splitSlots) return [d];
  const rows = Array.isArray(d?.t0_slot_results) ? d.t0_slot_results : [];
  if (rows.length) {
    const filled = rows.filter((r) => r && typeof r === "object" && slotFilled(r));
    if (filled.length) return filled.map((r) => slotDayRow(d, r, rows));
  }
  return hostsFromTrades(d);
}

function tradeTableColCount(ctx) {
  let n = 1;
  if (ctx.showStock) n += 1;
  n += 4; // O L H C
  n += 3; // C_τ lower upper
  n += 1; // y_oc
  n += 1; // y_τw
  n += 1; // y_τ30
  n += 1; // y_τ45
  n += 1; // y_τ60
  n += 1; // y_τ75
  n += 1; // y_τ90
  n += 1; // process
  n += 3; // ret pnl exp
  if (ctx.showReason) n += 1;
  if (ctx.showDelete) n += 1;
  return n;
}

function fmtScanPredReal(pred, real) {
  const p = finiteRtauNum(pred);
  const predTxt = p != null ? fmtRtauPct(p) : "—";
  const r = finiteRtauNum(real);
  if (p != null && r != null) return `${predTxt}(${fmtRtauPct(r)})`;
  return predTxt;
}

function fmtScanHorizonPredReal(pred, real) {
  const p = finiteRtauNum(pred);
  const predTxt = p != null ? fmtHorizonProb(p) : "—";
  const r = finiteRtauNum(real);
  if (p != null && r != null) return `${predTxt}(${fmtRtauPct(r)})`;
  return predTxt;
}

function fmtScanYtw(pred, real) {
  const p = finiteYhatNum(pred);
  const predTxt = p != null ? fmtYtwVote(p) : "—";
  const r = finiteYhatNum(real);
  if (p != null && r != null) return `${predTxt}(${fmtYtwVote(r)})`;
  return predTxt;
}

function scanYtwPred(r) {
  if (!r || typeof r !== "object") return null;
  const stored =
    finiteYhatNum(r.y_tw) ?? finiteYhatNum(r["y_τw"]) ?? finiteYhatNum(r.y_tw_hat);
  if (stored != null) return stored;
  return blendYtw(
    scanYt30Pred(r),
    scanYt60Pred(r),
    scanYt90Pred(r),
    scanYt45Pred(r),
    scanYt75Pred(r),
    true
  );
}

function scanYt30Pred(r) {
  if (!r || typeof r !== "object") return null;
  return r["y_τ30"] ?? r.y_t30 ?? r.predicted_score_t30 ?? r.y_t30_hat;
}

function scanYt45Pred(r) {
  if (!r || typeof r !== "object") return null;
  return r["y_τ45"] ?? r.y_t45 ?? r.predicted_score_t45 ?? r.y_t45_hat;
}

function scanYt60Pred(r) {
  if (!r || typeof r !== "object") return null;
  return r["y_τ60"] ?? r.y_t60 ?? r.predicted_score_t60 ?? r.y_t60_hat;
}
function scanYt75Pred(r) {
  if (!r || typeof r !== "object") return null;
  return r["y_τ75"] ?? r.y_t75 ?? r.predicted_score_t75 ?? r.y_t75_hat;
}
function scanYt90Pred(r) {
  if (!r || typeof r !== "object") return null;
  return r["y_τ90"] ?? r.y_t90 ?? r.predicted_score_t90 ?? r.y_t90_hat;
}

function scanYocPred(r) {
  if (!r || typeof r !== "object") return null;
  return r.y_oc ?? r.y_tau;
}

function scanYhlPred(r) {
  if (!r || typeof r !== "object") return null;
  return r.y_hl ?? r.predicted_score_hl ?? r.y_path ?? r.predicted_score_path;
}

function scanYocReal(d) {
  // 与主表 pickTauRealized 同口径：scores/feats 优先，再顶层，再日 K 开→收。
  // 勿只读顶层 tau_realized——已落账预演会留下早盘末价快照。
  const pack = pickTauRealized(d);
  return pack && pack.n != null ? pack.n : null;
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
    `<span class="paper-t0-scan-debug-lead">11:00 前扫描 · C_τ=O×(1+clip(ŷ_oc×scale)/100) · 带宽 C_τ×(1±δ) · C=5m收价</span>` +
    `<span class="${checkCls.trim()}" title="${escapeText(closeBandScanOhlcTip(ohlc))}">` +
  `${escapeText(dayBit)} · ${escapeText(minBit)}` +
    `</span>` +
    `</div>`
  );
}

function buildCloseBandScanExpandRow(d, dayKey, colSpan, rules) {
  const scan = Array.isArray(d?.close_band_scan) ? d.close_band_scan : [];
  if (!scan.length) return "";
  const ocReal = scanYocReal(d);
  const body = scan
    .map((r) => {
      if (!r || typeof r !== "object") return "";
      const hm = escapeText(String(r.hm || ""));
      const legCls = r.leg1 ? " is-leg1" : "";
      const skips = [
        r.minute_missing ? "分钟缺失" : "",
        r.enter_skip ? String(r.enter_skip) : "",
        r.sign_skip ? String(r.sign_skip) : "",
        r.y_tw_skip ? String(r.y_tw_skip) : "",
      ]
        .filter(Boolean)
        .join(" · ");
      const yt30Txt = fmtScanHorizonPredReal(scanYt30Pred(r), r.y_t30_realized ?? r.t30_realized);
      const yt45Txt = fmtScanHorizonPredReal(scanYt45Pred(r), r.y_t45_realized ?? r.t45_realized);
      const yt60Txt = fmtScanHorizonPredReal(scanYt60Pred(r), r.y_t60_realized ?? r.t60_realized);
      const yt75Txt = fmtScanHorizonPredReal(scanYt75Pred(r), r.y_t75_realized ?? r.t75_realized);
      const yt90Txt = fmtScanHorizonPredReal(scanYt90Pred(r), r.y_t90_realized ?? r.t90_realized);
      const ytwTxt = fmtScanYtw(
        scanYtwPred(r),
        blendYtw(
          r.y_t30_realized ?? r.t30_realized,
          r.y_t60_realized ?? r.t60_realized,
          r.y_t90_realized ?? r.t90_realized,
          r.y_t45_realized ?? r.t45_realized,
          r.y_t75_realized ?? r.t75_realized
        )
      );
      const yocTxt = fmtScanPredReal(scanYocPred(r), ocReal);
      const pickTxt = fmtScanPick(r.pick);
      const skipTxt = skips || "—";
      return (
        `<tr class="paper-t0-scan-row${legCls}">` +
        `<td class="paper-t0-scan-hm">${hm}</td>` +
        `<td class="num paper-t0-scan-px">${escapeText(fmtBarPx(r.o))}</td>` +
        `<td class="num paper-t0-scan-px is-lo">${escapeText(fmtBarPx(r.l))}</td>` +
        `<td class="num paper-t0-scan-px is-hi">${escapeText(fmtBarPx(r.h))}</td>` +
        `<td class="num paper-t0-scan-px">${escapeText(fmtBarPx(r.c))}</td>` +
        tradeCtauTd(finitePosPx(r.c_tau), null) +
        tradeBandEdgeCells(finitePosPx(r.c_tau), { close_band: r }, rules, r) +
        `<td class="num paper-t0-scan-y paper-t0-y-score has-tip" data-score-tip="tau" data-score-detail="${tauTipDetailAttr(
          r,
          d
        )}" title="${escapeText(`${Y_TAU_TITLE} · ${yocTxt}`)}">${escapeText(yocTxt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-ytw has-tip" data-score-tip="tw" data-score-detail="${twTipDetailAttr(
          twScanTipItem(r, d)
        )}" title="${escapeText(`${Y_TW_TITLE} · ${ytwTxt}`)}">${escapeText(ytwTxt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-yt30 has-tip" data-score-tip="t30" data-score-detail="${t30TipDetailAttr(
          t30ScanTipItem(r, d)
        )}" title="${escapeText(`${Y_T30_TITLE} · ${yt30Txt}`)}">${escapeText(yt30Txt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-yt45 has-tip" data-score-tip="t45" data-score-detail="${t45TipDetailAttr(
          t45ScanTipItem(r, d)
        )}" title="${escapeText(`${Y_T45_TITLE} · ${yt45Txt}`)}">${escapeText(yt45Txt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-yt60 has-tip" data-score-tip="t60" data-score-detail="${t60TipDetailAttr(
          t60ScanTipItem(r, d)
        )}" title="${escapeText(`${Y_T60_TITLE} · ${yt60Txt}`)}">${escapeText(yt60Txt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-yt75 has-tip" data-score-tip="t75" data-score-detail="${t75TipDetailAttr(
          t75ScanTipItem(r, d)
        )}" title="${escapeText(`${Y_T75_TITLE} · ${yt75Txt}`)}">${escapeText(yt75Txt)}</td>` +
        `<td class="num paper-t0-scan-y paper-t0-y-score paper-t0-col-yt90 has-tip" data-score-tip="t90" data-score-detail="${t90TipDetailAttr(
          t90ScanTipItem(r, d)
        )}" title="${escapeText(`${Y_T90_TITLE} · ${yt90Txt}`)}">${escapeText(yt90Txt)}</td>` +
        `<td class="paper-t0-scan-pick">${escapeText(pickTxt)}</td>` +
        `<td class="paper-t0-scan-skip" title="${escapeText(skipTxt)}">${escapeText(skipTxt)}</td>` +
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
    `<colgroup><col span="17"></colgroup>` +
    `<thead><tr>` +
    `<th class="paper-t0-scan-hm">钟</th>` +
    `<th class="num paper-t0-scan-px">O</th>` +
    `<th class="num paper-t0-scan-px">L</th>` +
    `<th class="num paper-t0-scan-px">H</th>` +
    `<th class="num paper-t0-scan-px">C</th>` +
    `<th class="num paper-t0-col-ctau" title="C_τ = O×(1+clip(ŷ_oc×scale, l, u)/100)">C_τ</th>` +
    `<th class="num paper-t0-col-band-lo" title="lower = C_τ×(1−δ/100)">lower</th>` +
    `<th class="num paper-t0-col-band-up" title="upper = C_τ×(1+δ/100)">upper</th>` +
    `<th class="num paper-t0-scan-y" title="ŷ_oc · 预估(真实 open→close)">y_oc</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_TW_TITLE)} · 符号和(真实)">y_τw</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_T30_TITLE)} · 预估(真实 τ⊕25/30/35均)">y_τ30</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_T45_TITLE)} · 预估(真实 τ⊕40/45/50均)">y_τ45</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_T60_TITLE)} · 预估(真实 τ⊕55/60/65均)">y_τ60</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_T75_TITLE)} · 预估(真实 τ⊕70/75/80均)">y_τ75</th>` +
    `<th class="num paper-t0-scan-y" title="${escapeText(Y_T90_TITLE)} · 预估(真实 τ⊕85/90/95均)">y_τ90</th>` +
    `<th class="paper-t0-scan-pick">选向</th>` +
    `<th class="paper-t0-scan-skip">跳过</th>` +
    `</tr></thead>` +
    `<tbody>${body}</tbody>` +
    `</table></div></td></tr>`
  );
}

/** 点击「日」列展开 11:00 前每根扫描（需 close_band_scan）。 */
export function wireT0DayDebugExpand(host) {
  if (!host) return;
  if (host.dataset.t0DayDebugWired === "1") return;
  host.dataset.t0DayDebugWired = "1";
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
    const scoreItem = t0DayScoreItem(scoreHost, fallback, rules, liveByCode);
    const scoreDetailJson = escapeText(watchingScoreDetail(scoreItem));
    const t30DetailJson = t30TipDetailAttr(scoreItem);
    const t45DetailJson = t45TipDetailAttr(scoreItem);
    const t60DetailJson = t60TipDetailAttr(scoreItem);
    const t75DetailJson = t75TipDetailAttr(scoreItem);
    const t90DetailJson = t90TipDetailAttr(scoreItem);
    const twDetailJson = twTipDetailAttr(scoreItem);
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
        liveByCode,
        dayRef || d
      ) +
      twMergedCellHtml(scoreHost, dayRef || d, slotYRealized, twDetailJson) +
      t30MergedCellHtml(scoreHost, dayRef || d, slotYRealized, t30DetailJson) +
      t45MergedCellHtml(scoreHost, dayRef || d, slotYRealized, t45DetailJson) +
      t60MergedCellHtml(scoreHost, dayRef || d, slotYRealized, t60DetailJson) +
      t75MergedCellHtml(scoreHost, dayRef || d, slotYRealized, t75DetailJson) +
      t90MergedCellHtml(scoreHost, dayRef || d, slotYRealized, t90DetailJson)
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
      )}" title="展开 11:00 前每根扫描（OLHC · C_τ · lower · upper · y_oc · y_τw · y_τ30…）" aria-expanded="false">${escapeText(
        dayText
      )}<span class="paper-t0-day-debug-caret" aria-hidden="true">▾</span></button>`
    : escapeText(dayText);
  let html =
    `<tr class="${daySkipped ? "is-skipped" : ""}" data-code="${escapeText(code)}" data-t0-day-id="${escapeText(
      dayKey
    )}">` +
    (showStock ? applyTdRowspan(stockCellHtml(d, fallback), span) : "") +
    applyTdRowspan(`<td class="paper-t0-col-date">${dateInner}</td>`, span) +
    tradeSlotPxCells(first, rules, showRealized) +
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
      tradeSlotPxCells(host, rules, showRealized) +
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
 * @param {{ data?: object, days: object[], caption?: string, maxRows?: number, showReason?: boolean, preserveOrder?: boolean, showDelete?: boolean, showRealized?: boolean, splitSlots?: boolean }} opts
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
    splitSlots: splitSlotsOpt,
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
  const fallback = {
    stock_code: data.stock_code,
    stock_name: data.stock_name,
  };

  const pairHint = showRealized ? " · 显示：预估值(真实值)" : "";
  const slotYHint = rules.t0_slots_enabled ? ` · 本轮 ŷ${pairHint}` : pairHint;
  const head =
    (showStock ? `<th scope="col" class="paper-t0-col-stock">股票</th>` : "") +
    `<th scope="col" class="paper-t0-col-date" title="有可展开扫描数据时点击日展开 11:00 前调试">日</th>` +
    `<th scope="col" class="paper-t0-col-o num" title="本轮触发根 5m 开盘">O</th>` +
    `<th scope="col" class="paper-t0-col-l num" title="本轮触发根 5m 最低">L</th>` +
    `<th scope="col" class="paper-t0-col-h num" title="本轮触发根 5m 最高">H</th>` +
    `<th scope="col" class="paper-t0-col-c num" title="本轮触发根 5m 收盘">C</th>` +
    `<th scope="col" class="paper-t0-col-ctau num" title="C_τ = O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)；分钟空间（破带用）">C_τ</th>` +
    `<th scope="col" class="paper-t0-col-band-lo num" title="lower = C_τ×(1−δ/100)；C&lt;lower → 正T">lower</th>` +
    `<th scope="col" class="paper-t0-col-band-up num" title="upper = C_τ×(1+δ/100)；C&gt;upper → 反T">upper</th>` +
    `<th scope="col" class="paper-t0-col-tau num paper-t0-col-y paper-t0-col-y-tau" title="${escapeText(
      `${Y_TAU_TITLE}${slotYHint}`
    )}">y_oc</th>` +
    `<th scope="col" class="paper-t0-col-ytw num paper-t0-col-y paper-t0-col-y-tw" title="${escapeText(
      `${Y_TW_TITLE}${slotYHint}`
    )}">y_τw</th>` +
    `<th scope="col" class="paper-t0-col-yt30 num paper-t0-col-y paper-t0-col-y-t30" title="${escapeText(
      `${Y_T30_TITLE}${slotYHint}`
    )}">y_τ30</th>` +
    `<th scope="col" class="paper-t0-col-yt45 num paper-t0-col-y paper-t0-col-y-t45" title="${escapeText(
      `${Y_T45_TITLE}${slotYHint}`
    )}">y_τ45</th>` +
    `<th scope="col" class="paper-t0-col-yt60 num paper-t0-col-y paper-t0-col-y-t60" title="${escapeText(
      `${Y_T60_TITLE}${slotYHint}`
    )}">y_τ60</th>` +
    `<th scope="col" class="paper-t0-col-yt75 num paper-t0-col-y paper-t0-col-y-t75" title="${escapeText(
      `${Y_T75_TITLE}${slotYHint}`
    )}">y_τ75</th>` +
    `<th scope="col" class="paper-t0-col-yt90 num paper-t0-col-y paper-t0-col-y-t90" title="${escapeText(
      `${Y_T90_TITLE}${slotYHint}`
    )}">y_τ90</th>` +
    `<th scope="col" class="paper-t0-col-process" title="本轮时钟 + 成交腿；悬停看全日 K 线">过程</th>` +
    `<th scope="col" class="paper-t0-col-ret num" title="(PnL+敞口)/动仓名义">收益%</th>` +
    `<th scope="col" class="paper-t0-col-pnl num">PnL</th>` +
    `<th scope="col" class="paper-t0-col-exp num" title="${escapeText(exposureColTitle(data))}">敞口</th>` +
    (showReason ? `<th scope="col" class="paper-t0-col-reason">说明</th>` : "") +
    (showDelete ? `<th scope="col" class="paper-t0-col-act">操作</th>` : "");

  const splitSlots = splitSlotsOpt != null ? !!splitSlotsOpt : !showDelete;
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

