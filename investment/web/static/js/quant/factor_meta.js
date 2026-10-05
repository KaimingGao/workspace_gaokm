/**
 * 因子元数据缓存（/api/quant/factors）+ 经济族/来源分类（与 core.signal.factors.meta.taxonomy 对齐）。
 */
import { escapeHtml } from "../shared.js";

/** 与 Python FAMILY_META 顺序一致 */
const FACTOR_FAMILY_DEFS = [
  { id: "trend", label: "趋势", tip: "价格方向、均线、形态；OLS 趋势族共线只打这一组" },
  { id: "value_quality", label: "价值质量", tip: "基本面：估值、盈利收益率、质量、成长、股息" },
  { id: "liquidity_flow", label: "流动性", tip: "成交、冲击成本、资金流" },
  { id: "risk", label: "风险", tip: "波动、跳空、过热、尾盘异动" },
  { id: "residual", label: "残差", tip: "相对市场、特异动量、规模" },
  { id: "reversal", label: "反转", tip: "短线反转，与动量拆开避免双计" },
  { id: "sentiment", label: "舆情", tip: "研究旁路，默认不进 ŷ" },
  {
    id: "pv_derived",
    label: "量价衍生",
    tip: "Qlib Alpha158 等 OHLCV 衍生；omit_sub_score，仅 raw_* 进 Ridge/树/LTR",
  },
  { id: "other", label: "其他", tip: "未编入 factor_groups" },
];

const FAMILY_MEMBERS = {
  trend: ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
  value_quality: ["value", "earnings_yield", "quality", "growth", "dividend"],
  liquidity_flow: ["liquidity", "money_flow", "amihud", "volume_price"],
  risk: ["volatility", "gap_risk", "tail_anomaly", "overheat"],
  residual: ["relative_strength", "idio_momentum", "size"],
  reversal: ["reversal"],
  sentiment: ["alt_sentiment", "llm_sentiment"],
  pv_derived: ["alpha158"],
};

const PREFIX_FAMILY = [{ prefix: "raw_alpha158_", family: "pv_derived" }];

const SOURCE_OVERRIDE = {
  money_flow: {
    status: "proxy",
    label: "代理",
    note: "无净流入 ingest；默认 MFI 代理；权重须保持 0 除非人审有真源",
  },
  alt_sentiment: {
    status: "prior_only",
    label: "旁路",
    note: "无历史 news 面板；不进 ŷ；仅 sentiment.prior 旁路",
  },
  llm_sentiment: {
    status: "prior_only",
    label: "旁路",
    note: "Qwen LLM 舆情（研究轨）；不进 ŷ；仅作 alt_sentiment 研究对照",
  },
};

/** 已退役 raw_basis 因子（旧分组报告键；表格不再展示） */
const REMOVED_RAW_BASIS_NAMES = new Set([
  "mom3_pct",
  "mom5_pct",
  "mom_overheat",
  "atr_pct_raw",
  "atr_pct_sq",
  "vol_elevated",
  "pe_raw",
  "value_fair",
  "value_expensive",
]);

export function isRemovedFactor(name) {
  return REMOVED_RAW_BASIS_NAMES.has(String(name || "").trim());
}

const FAMILY_ORDER = Object.fromEntries(
  FACTOR_FAMILY_DEFS.map((d, i) => [d.id, i])
);
const FAMILY_BY_ID = Object.fromEntries(FACTOR_FAMILY_DEFS.map((d) => [d.id, d]));
const NAME_TO_FAMILY = {};
for (const [fid, members] of Object.entries(FAMILY_MEMBERS)) {
  for (const n of members) NAME_TO_FAMILY[n] = fid;
}

function fallbackClassify(name) {
  const key = String(name || "").trim();
  let family = NAME_TO_FAMILY[key] || "";
  if (!family) {
    for (const row of PREFIX_FAMILY) {
      if (key.startsWith(row.prefix)) {
        family = row.family;
        break;
      }
    }
  }
  if (!family) family = "other";
  let source = "sourced";
  let sourceLabel = "真源";
  let sourceNote = "";
  const ov = SOURCE_OVERRIDE[key];
  if (ov) {
    source = ov.status;
    sourceLabel = ov.label;
    sourceNote = ov.note;
  }
  const def = FAMILY_BY_ID[family] || FAMILY_BY_ID.other;
  return {
    family: def.id,
    familyLabel: def.label,
    familyTip: def.tip,
    familyOrder: FAMILY_ORDER[def.id] ?? FAMILY_ORDER.other,
    source,
    sourceLabel,
    sourceNote,
    sourced: source === "sourced",
  };
}

/**
 * @param {string} name
 * @param {object} [meta] factorMeta 行（API 优先补来源；族归属以本地 FAMILY_MEMBERS 为准）
 */
export function classifyFactor(name, meta) {
  const key = String(name || "").trim();
  const m = meta && typeof meta === "object" ? meta : {};
  const fallback = fallbackClassify(key);

  // 已编入本地族表 / 前缀族的因子：族标签以本地为准（避免旧 API/实验行把
  // overheat、tail_anomaly、raw_alpha158_* 等钉死在「其他」）。
  const localFamily =
    NAME_TO_FAMILY[key] ||
    (PREFIX_FAMILY.some((r) => key.startsWith(r.prefix)) ? fallback.family : "");
  if (localFamily) {
    const source = String(m.source || fallback.source || "sourced");
    const ov = SOURCE_OVERRIDE[key];
    return {
      family: fallback.family,
      familyLabel: fallback.familyLabel,
      familyTip: fallback.familyTip,
      familyOrder: fallback.familyOrder,
      source,
      sourceLabel: String(
        m.source_label || (ov && ov.label) || fallback.sourceLabel || ""
      ),
      sourceNote: String(
        m.source_note || m.status_note || (ov && ov.note) || fallback.sourceNote || ""
      ),
      sourced: source === "sourced",
    };
  }

  if (m.family && m.family_label) {
    const fid = String(m.family);
    const def = FAMILY_BY_ID[fid];
    const source = String(m.source || "sourced");
    return {
      family: fid,
      familyLabel: String(m.family_label),
      familyTip: String(m.family_tip || (def && def.tip) || ""),
      familyOrder: FAMILY_ORDER[fid] ?? FAMILY_ORDER.other,
      source,
      sourceLabel: String(m.source_label || ""),
      sourceNote: String(m.source_note || m.status_note || ""),
      sourced: source === "sourced",
    };
  }
  return fallback;
}

/** ŷ_τc 开盘/截面特征不在因子注册表，本地兜底注释。 */
const TAU_FEAT_META = {
  gap_pct: {
    label: "跳空 %",
    description:
      "开盘相对昨收的跳空幅度（%）。ŷ_τc 用它抬到昨收基准；τ=open 时即隔夜缺口。",
  },
  open_gap: {
    label: "开盘缺口",
    description:
      "开盘缺口，与「跳空 %」同口径（开盘/昨收−1）。Ridge 拟合时与 gap_pct 二选一，避免双计。",
  },
  sector_gap_breadth: {
    label: "同业缺口广度",
    description:
      "同日池内跳空达到门槛的占比（0–1）。衡量板块/市场开盘风险偏好，不是个股独有缺口。",
  },
  theme_day: {
    label: "主题日",
    description:
      "主题日指示（0/1）：同日缺口广度够高，或截面 |缺口| 中位达到触发线。事件日软标签，不是新闻标题。",
  },
  gap_atr: {
    label: "缺口 / ATR",
    description:
      "开盘缺口除以近 14 日 ATR%。同一跳空在低波动票上更大、高波动票上更小，避免把「常跳」当成强信号。",
  },
  gap_vs_sector: {
    label: "行业相对缺口",
    description:
      "个股跳空 − 同行中位跳空（同伴不足则减全池中位）。正值表示相对板块更强的隔夜冲击，剥离板块 beta。",
  },
  ret_open_to_tau: {
    label: "开盘→τ 收益 %",
    description:
      "开盘到 τ 时刻已实现收益（%）。τ=open 时为 0；分钟 τ 为变长前缀（调仓截钟≤10:00）。ŷ_τc 标签是 τ→close；此特征刻画已实现前缀，用于抬到昨收基准。",
  },
  ret_prev_to_tau: {
    label: "昨收→τ 收益 %",
    description: "昨收到 τ 价已实现收益（%）。含隔夜缺口 + 开盘→τ，刻画到决策点的总位移。",
  },
  range_pct: {
    label: "前缀振幅 %",
    description: "开盘→τ 前缀 (H−L)/open（%）。振幅大表示路径嘈杂，常与回撤/反弹因子共线。",
  },
  loc_hl: {
    label: "HL 位置",
    description: "现价在前缀 [L,H] 箱中的位置 0–1。近 1=贴着前缀高，近 0=贴着前缀低。",
  },
  up_extent: {
    label: "相对开盘上探 %",
    description: "(前缀高−开盘)/开盘（%）。衡量开盘后上冲空间。",
  },
  down_extent: {
    label: "相对开盘下探 %",
    description: "(开盘−前缀低)/开盘（%）。衡量开盘后下探空间。",
  },
  path_sign: {
    label: "路径符号",
    description: "+1 先低后高（探底回升），−1 先高后低（冲高回落）。粗粒度路径形状。",
  },
  pullback_from_high: {
    label: "自高回撤 %",
    description: "相对前缀高点的回撤（%）。大回撤常对应冲高回落；与剩余窗标签的关系需看 β 符号。",
  },
  bounce_from_low: {
    label: "自低反弹 %",
    description: "相对前缀低点的反弹（%）。大反弹常对应探底回升。",
  },
  ret_last_15m: {
    label: "近15m 收益 %",
    description: "τ 前约 15 分钟（约 3 根 5m）收益（%）。刻画临门动量。",
  },
  ret_last_5m: {
    label: "近5m 收益 %",
    description: "ŷ_τ30 专用。末根 5m 收益（%）：closes[-1]/closes[-2]−1。需 ≥2 根前缀。",
  },
  ret_last_30m: {
    label: "近30交易分钟收益 %",
    description:
      "ŷ_τ30 专用。P_τ / P_{τ⊖30} − 1（交易时钟，跳过午休）。开盘后不足 30 交易分钟留空；τ⊖30=09:30 且缺根时用开盘价。",
  },
  session_elapsed: {
    label: "已过交易分钟（09:30=0）",
    description:
      "ŷ_τ30 专用。09:30=0，11:30=120，13:00=120，14:30=210。午休不计入。",
  },
  session_remain: {
    label: "距收盘剩余交易分钟",
    description: "ŷ_τ30 专用。240 − session_elapsed（到 15:00）。",
  },
  crosses_lunch: {
    label: "未来30m是否跨午休",
    description:
      "ŷ_τ30 专用。1 当 elapsed(τ)≤120 且 elapsed(τ⊕30)>120（11:15→13:15、11:30→13:30）；11:00→11:30 为 0。",
  },
  session_vwap_dev: {
    label: "τ价相对会话VWAP %",
    description:
      "ŷ_τ30 专用。(P_τ − VWAP_{09:30→τ}) / P_τ。VWAP 用 5m typical×量；正值=现价在会话均价之上。",
  },
  vol_last_30m_vs_avg: {
    label: "近30m量/前缀均量",
    description:
      "ŷ_τ30 专用。近 30 交易分钟 5m 均量 / 开盘→τ 前缀均量。>1 表示临门相对放量。",
  },
  sector_ret_last_30m: {
    label: "板块中位近30m %",
    description:
      "ŷ_τ30 专用。同日同钟池内 ret_last_30m 中位数（%）。与开→τ 的 sector_ret_to_tau 窗口不同。",
  },
  ret_last_30m_vs_sector: {
    label: "近30m相对板块 %",
    description: "ŷ_τ30 专用。个股 ret_last_30m − 板块中位近30m（%）。",
  },
  t30_lag1: {
    label: "昨同钟真实 τ⊕25/30/35均 %",
    description:
      "ŷ_τ30 专用。同一决策钟昨日已实现 mean(price(τ⊕25/30/35))/price(τ)−1。日期严格早于 asof。",
  },
  t30_ma5: {
    label: "近5日同钟真实 τ⊕25/30/35均 %",
    description:
      "ŷ_τ30 专用。同一决策钟近 5 个交易日已实现 τ⊕25/30/35 均价收益均值。日期严格早于 asof。",
  },
  ret_last_45m: {
    label: "近45交易分钟收益 %",
    description: "ŷ_τ45 专用。交易时钟 ⊖45m 到 τ 的收益（%）；跳过午休。",
  },
  crosses_lunch_45: {
    label: "未来45m是否跨午休",
    description: "ŷ_τ45 专用。τ⊕45m 是否跨 11:30–13:00。与 30m 的 crosses_lunch 分开。",
  },
  vol_last_45m_vs_avg: {
    label: "近45m量/前缀均量",
    description: "ŷ_τ45 专用。近 45 交易分钟均量 / 前缀均量。",
  },
  sector_ret_last_45m: {
    label: "板块中位近45m %",
    description:
      "ŷ_τ45 专用。同日同钟池内 ret_last_45m 中位数（%）。与开→τ 的 sector_ret_to_tau 窗口不同。",
  },
  ret_last_45m_vs_sector: {
    label: "近45m相对板块 %",
    description: "ŷ_τ45 专用。个股 ret_last_45m − 板块中位近45m（%）。",
  },
  t45_lag1: {
    label: "昨同钟真实 τ⊕40/45/50均 %",
    description:
      "ŷ_τ45 专用。同一决策钟昨日已实现 mean(price(τ⊕40/45/50))/price(τ)−1。日期严格早于 asof。",
  },
  t45_ma5: {
    label: "近5日同钟真实 τ⊕40/45/50均 %",
    description:
      "ŷ_τ45 专用。同一决策钟近 5 个交易日已实现 τ⊕40/45/50 均价收益均值。日期严格早于 asof。",
  },
  ret_last_60m: {
    label: "近60交易分钟收益 %",
    description: "ŷ_τ60 专用。交易时钟 ⊖60m 到 τ 的收益（%）；跳过午休。",
  },
  crosses_lunch_60: {
    label: "未来60m是否跨午休",
    description: "ŷ_τ60 专用。τ⊕60m 是否跨 11:30–13:00。与 30m 的 crosses_lunch 分开。",
  },
  vol_last_60m_vs_avg: {
    label: "近60m量/前缀均量",
    description: "ŷ_τ60 专用。近 60 交易分钟均量 / 前缀均量。",
  },
  sector_ret_last_60m: {
    label: "板块中位近60m %",
    description:
      "ŷ_τ60 专用。同日同钟池内 ret_last_60m 中位数（%）。与开→τ 的 sector_ret_to_tau 窗口不同。",
  },
  ret_last_60m_vs_sector: {
    label: "近60m相对板块 %",
    description: "ŷ_τ60 专用。个股 ret_last_60m − 板块中位近60m（%）。",
  },
  t60_lag1: {
    label: "昨同钟真实 τ⊕55/60/65均 %",
    description:
      "ŷ_τ60 专用。同一决策钟昨日已实现 mean(price(τ⊕55/60/65))/price(τ)−1。日期严格早于 asof。",
  },
  t60_ma5: {
    label: "近5日同钟真实 τ⊕55/60/65均 %",
    description:
      "ŷ_τ60 专用。同一决策钟近 5 个交易日已实现 τ⊕55/60/65 均价收益均值。日期严格早于 asof。",
  },
  ret_last_75m: {
    label: "近75交易分钟收益 %",
    description: "ŷ_τ75 专用。交易时钟 ⊖75m 到 τ 的收益（%）；跳过午休。",
  },
  crosses_lunch_75: {
    label: "未来75m是否跨午休",
    description: "ŷ_τ75 专用。τ⊕75m 是否跨 11:30–13:00。与 30m / 60m 的 crosses_lunch 分开。",
  },
  vol_last_75m_vs_avg: {
    label: "近75m量/前缀均量",
    description: "ŷ_τ75 专用。近 75 交易分钟均量 / 前缀均量。",
  },
  sector_ret_last_75m: {
    label: "板块中位近75m %",
    description:
      "ŷ_τ75 专用。同日同钟池内 ret_last_75m 中位数（%）。与开→τ 的 sector_ret_to_tau 窗口不同。",
  },
  ret_last_75m_vs_sector: {
    label: "近75m相对板块 %",
    description: "ŷ_τ75 专用。个股 ret_last_75m − 板块中位近75m（%）。",
  },
  t75_lag1: {
    label: "昨同钟真实 τ⊕70/75/80均 %",
    description:
      "ŷ_τ75 专用。同一决策钟昨日已实现 mean(price(τ⊕70/75/80))/price(τ)−1。日期严格早于 asof。",
  },
  t75_ma5: {
    label: "近5日同钟真实 τ⊕70/75/80均 %",
    description:
      "ŷ_τ75 专用。同一决策钟近 5 个交易日已实现 τ⊕70/75/80 均价收益均值。日期严格早于 asof。",
  },
  ret_last_90m: {
    label: "近90交易分钟收益 %",
    description: "ŷ_τ90 专用。交易时钟 ⊖90m 到 τ 的收益（%）；跳过午休。",
  },
  crosses_lunch_90: {
    label: "未来90m是否跨午休",
    description: "ŷ_τ90 专用。τ⊕90m 是否跨 11:30–13:00。与 30m / 60m 的 crosses_lunch 分开。",
  },
  vol_last_90m_vs_avg: {
    label: "近90m量/前缀均量",
    description: "ŷ_τ90 专用。近 90 交易分钟均量 / 前缀均量。",
  },
  sector_ret_last_90m: {
    label: "板块中位近90m %",
    description:
      "ŷ_τ90 专用。同日同钟池内 ret_last_90m 中位数（%）。与开→τ 的 sector_ret_to_tau 窗口不同。",
  },
  ret_last_90m_vs_sector: {
    label: "近90m相对板块 %",
    description: "ŷ_τ90 专用。个股 ret_last_90m − 板块中位近90m（%）。",
  },
  t90_lag1: {
    label: "昨同钟真实 τ⊕85/90/95均 %",
    description:
      "ŷ_τ90 专用。同一决策钟昨日已实现 mean(price(τ⊕85/90/95))/price(τ)−1。日期严格早于 asof。",
  },
  t90_ma5: {
    label: "近5日同钟真实 τ⊕85/90/95均 %",
    description:
      "ŷ_τ90 专用。同一决策钟近 5 个交易日已实现 τ⊕85/90/95 均价收益均值。日期严格早于 asof。",
  },
  realized_vol: {
    label: "前缀已实现波动 %",
    description: "开盘→τ 的 5m 收益标准差（%）。路径噪声强度。",
  },
  vol_last3_vs_avg: {
    label: "近3根量/均量",
    description: "近 3 根 5m 成交量 / 前缀均量。>1 表示临门放量。",
  },
  tau_elapsed_min: {
    label: "τ距开盘分钟",
    description:
      "决策钟相对 09:30 的分钟数。变长前缀共享 β 时告诉模型前缀有多长（如 10:30→60）。",
  },
  sector_ret_to_tau: {
    label: "板块中位开→τ %",
    description:
      "同日池内开盘→τ 已实现收益的中位数（%）。仅分钟 τ 训练/预测用，剥离板块盘中 beta。",
  },
  ret_vs_sector: {
    label: "开→τ 相对板块 %",
    description: "个股开盘→τ − 板块中位开→τ（%）。正值=相对板块更强的前缀。",
  },
  t_hi_frac: {
    label: "最高点相对前缀进度",
    description:
      "前缀首次最高价时刻相对开盘的进度 0–1。ŷ_τ*_tree 入模，补 path_sign 的时间位置。",
  },
  t_lo_frac: {
    label: "最低点相对前缀进度",
    description:
      "前缀首次最低价时刻相对开盘的进度 0–1。ŷ_τ*_tree 入模，补 path_sign 的时间位置。",
  },
  t_hi_minus_lo: {
    label: "高点进度−低点进度",
    description:
      "t_hi_frac − t_lo_frac。正≈先低后高（V 形进度），负≈先高后低。仅树头。",
  },
  room_to_high: {
    label: "距前缀高点空间 %",
    description: "(前缀高−τ价)/τ价×100。相对收盘锚的剩余上探空间；仅树头。",
  },
  room_to_low: {
    label: "距前缀低点空间 %",
    description: "(τ价−前缀低)/τ价×100。相对收盘锚的已弹/可砸空间；仅树头。",
  },
  mom_accel_5_15: {
    label: "近5m−近15m 动量差 %",
    description: "短窗相对中窗的动量加速度；砸完抬升时为正。仅树头。",
  },
  mom_accel_5_30: {
    label: "近5m−近30m 动量差 %",
    description: "短窗相对长窗的动量加速度；专打 V 形反转。仅树头。",
  },
  vol_down_up: {
    label: "下跌量/上涨量",
    description: "前缀下跌根均量 / 上涨根均量（上限 10）。>1=跌段放量。仅树头。",
  },
  range_efficiency: {
    label: "|开→τ|/振幅（趋势效率 0–1）",
    description: "净涨跌相对振幅的占比。高=单边趋势，低=宽幅震荡。仅树头。",
  },
  vp_confirm: {
    label: "开→τ×(近3量比−1) 量价确认",
    description: "收益与近3根量比的乘积（截断±30）。放量上涨为正，放量下跌为负。仅树头。",
  },
  vol_up_share: {
    label: "上涨量占比 0–1",
    description: "上涨根成交量 / (上涨+下跌)。比 vol_down_up 更对称、不易截断。仅树头。",
  },
  pullback_x_vol: {
    label: "自高回撤×近3量比",
    description: "回撤深度×量比。放量回撤偏派发，缩量回撤偏消化。仅树头。",
  },
  yclose_loc: {
    label: "昨收位置",
    description: "昨收在昨高低中的位置 0–1。刻画隔夜起点相对昨路径的落点。",
  },
  mom3_pct: {
    label: "近3日动量 %",
    description: "近 3 个交易日收盘动量（%）。短端趋势，与开盘缺口正交补充。",
  },
};

/** ŷ_co 路径/开盘 Z 特征（不在因子注册表）。 */
export const CO_FEAT_META = {
  ret_oc: {
    label: "昨开→昨收 %",
    description:
      "T-1 日已实现开→收收益（%）。开盘决策时刻画昨日 intraday 路径，用于估 open[T+1]/close[T]−1 隔夜缺口。",
  },
  ret_cc: {
    label: "昨收→前收 %",
    description:
      "T-1 日收→收涨跌（%）。昨日已实现收→收，辅助 ŷ_co 看路径惯性。",
  },
  y_on_today: {
    label: "今开/昨开 %",
    description:
      "T 日已实现 open/open[T−1]−1（%）。今开相对昨开，不是训练标签（标签为 open[T+1]/close[T]−1）。",
  },
  gap_pct: TAU_FEAT_META.gap_pct,
  sector_gap_breadth: TAU_FEAT_META.sector_gap_breadth,
  theme_day: TAU_FEAT_META.theme_day,
  gap_atr: TAU_FEAT_META.gap_atr,
  gap_vs_sector: TAU_FEAT_META.gap_vs_sector,
  yclose_loc: {
    label: "今开相对昨高低",
    description:
      "今开在昨高低中的位置 0–1。与 τ 头同源；刻画跳空落在昨路径的哪一段。",
  },
  mom3_pct: TAU_FEAT_META.mom3_pct,
  yest_close_loc: {
    label: "昨收位置",
    description:
      "昨收在昨高低中的位置 0–1。近高收盘常伴隔夜惯性；与 yclose_loc（今开相对昨高低）互补。",
  },
  yest_range_pct: {
    label: "昨振幅 %",
    description: "昨 (最高−最低)/收盘 ×100。隔夜波动代理，开盘可得。",
  },
  yest_vol_ratio: {
    label: "昨量/均量",
    description: "昨成交量 / 此前均量。放量日隔夜更易消化消息；MA 不含昨日本身。",
  },
  dist_to_up_limit: {
    label: "距涨停 %",
    description:
      "昨收到涨停剩余空间（百分点）。主板约 10、创业/科创约 20。近涨停隔夜惯性更强。",
  },
  yest_gap: {
    label: "昨隔夜缺口 %",
    description:
      "open[T−1]/close[T−2]−1。上一跳隔夜，不是今日 gap_pct；用于隔夜自相关。",
  },
  on_ma5: {
    label: "近5日隔夜缺口均 %",
    description: "T−1 之前最多 5 个交易日真实隔夜缺口均值。不含今日 gap_pct。",
  },
};

/**
 * @param {{ fetchImpl?: typeof fetch }} [opts]
 */
export function createFactorMetaCache(opts = {}) {
  const fetchImpl = opts.fetchImpl || fetch;
  /** 稳定对象引用：外层可 factorMetaByName[name]=... 原地写入 */
  const factorMetaByName = /** @type {Record<string, any>} */ ({});
  const factorMetaByLabel = /** @type {Record<string, any>} */ ({});
  let factorMetaPromise = null;

  function rememberFactorMeta(list) {
    Object.keys(factorMetaByName).forEach((k) => {
      delete factorMetaByName[k];
    });
    Object.keys(factorMetaByLabel).forEach((k) => {
      delete factorMetaByLabel[k];
    });
    (list || []).forEach((f) => {
      if (!f || !f.name) return;
      factorMetaByName[f.name] = f;
      if (f.label) factorMetaByLabel[f.label] = f;
    });
  }

  async function ensureFactorMeta() {
    const needsRefresh = () => {
      const keys = Object.keys(factorMetaByName);
      if (!keys.length) return true;
      // 本地已编族但缓存行仍缺 family / 钉在 other → 重拉 API
      for (const k of keys) {
        if (!NAME_TO_FAMILY[k]) continue;
        const fam = String((factorMetaByName[k] && factorMetaByName[k].family) || "");
        if (!fam || fam === "other") return true;
      }
      // 本地风险族成员未进缓存
      for (const k of FAMILY_MEMBERS.risk || []) {
        if (!factorMetaByName[k]) return true;
      }
      return false;
    };
    if (!needsRefresh()) return factorMetaByName;
    if (factorMetaPromise) return factorMetaPromise;
    factorMetaPromise = (async () => {
      try {
        const res = await fetchImpl("/api/quant/factors");
        const data = await res.json();
        const list = data.factors || data.rows || [];
        rememberFactorMeta(
          (list || []).map((r) => {
            const name = r.name || r.factor;
            const tax = classifyFactor(name, {
              family: r.family || "",
              family_label: r.family_label || "",
              family_tip: r.family_tip || "",
              source: r.source || "",
              source_label: r.source_label || "",
              source_note: r.source_note || "",
            });
            return {
              name,
              label: r.label,
              description: r.description || "",
              family: tax.family,
              family_label: tax.familyLabel,
              family_tip: tax.familyTip,
              source: tax.source,
              source_label: tax.sourceLabel,
              source_note: tax.sourceNote,
            };
          })
        );
      } catch (_) {
        /* ignore */
      }
      return factorMetaByName;
    })();
    try {
      return await factorMetaPromise;
    } finally {
      factorMetaPromise = null;
    }
  }

  function tauFeatMeta(name, label) {
    const key = name != null ? String(name).trim() : "";
    if (key && TAU_FEAT_META[key]) return TAU_FEAT_META[key];
    const lab = label != null ? String(label).trim() : "";
    if (!lab) return null;
    for (const m of Object.values(TAU_FEAT_META)) {
      if (m && m.label === lab) return m;
    }
    return null;
  }

  function onFeatMeta(name, label) {
    const key = name != null ? String(name).trim() : "";
    if (key && CO_FEAT_META[key]) return CO_FEAT_META[key];
    const lab = label != null ? String(label).trim() : "";
    if (!lab) return null;
    for (const m of Object.values(CO_FEAT_META)) {
      if (m && m.label === lab) return m;
    }
    return null;
  }

  function factorDescription(name, label) {
    const meta =
      (name && factorMetaByName[name]) ||
      (label && factorMetaByLabel[label]) ||
      (name && factorMetaByLabel[name]) ||
      {};
    const fromApi = String(meta.description || "").trim();
    if (fromApi) return fromApi;
    const on = onFeatMeta(name, label);
    if (on && on.description) return String(on.description).trim();
    const tau = tauFeatMeta(name, label);
    return String((tau && tau.description) || "").trim();
  }

  function factorNameCellHtml(name, label, fallbackDescription) {
    const display = label || name || "—";
    const tip =
      factorDescription(name, label) ||
      String(fallbackDescription || "").trim();
    const text = escapeHtml(String(display));
    if (!tip) return text;
    return `<span class="factor-tip" title="${escapeHtml(tip)}">${text}</span>`;
  }

  function factorSourceBadgeHtml(name, meta) {
    const t = classifyFactor(name, meta || (name && factorMetaByName[name]));
    if (!t.source || t.source === "sourced") return "";
    if (t.sourceLabel === t.familyLabel) return "";
    const tip = t.sourceNote || t.sourceLabel;
    return (
      `<span class="quant-factor-src-badge is-${escapeHtml(t.source)}" title="${escapeHtml(
        tip
      )}">${escapeHtml(t.sourceLabel)}</span>`
    );
  }

  function factorFamilyChipHtml(name, meta) {
    const t = classifyFactor(name, meta || (name && factorMetaByName[name]));
    return (
      `<span class="quant-factor-family-badge quant-factor-family-chip" data-family="${escapeHtml(
        t.family
      )}" title="${escapeHtml(t.familyTip || t.familyLabel)}">${escapeHtml(
        t.familyLabel
      )}</span>`
    );
  }

  /** 因子名 + 经济族徽章 + 来源徽章（代理 / 旁路） */
  function factorTaxonomyCellHtml(name, label, fallbackDescription) {
    const meta = (name && factorMetaByName[name]) || {};
    return (
      `<span class="quant-factor-cell">` +
      `<span class="quant-factor-name">${factorNameCellHtml(
        name,
        label,
        fallbackDescription
      )}</span>` +
      factorFamilyChipHtml(name, meta) +
      factorSourceBadgeHtml(name, meta) +
      `</span>`
    );
  }

  return {
    factorMetaByName,
    factorMetaByLabel,
    rememberFactorMeta,
    ensureFactorMeta,
    factorDescription,
    factorNameCellHtml,
    classifyFactor: (name) => classifyFactor(name, factorMetaByName[name]),
    factorSourceBadgeHtml,
    factorFamilyChipHtml,
    factorTaxonomyCellHtml,
  };
}
