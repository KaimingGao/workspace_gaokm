/**
 * 因子元数据缓存（/api/quant/factors）+ 经济族/来源分类（与 core.signal.factors.meta.taxonomy 对齐）。
 */
import { escapeHtml } from "../shared.js";

/** 与 Python FAMILY_META 顺序一致 */
export const FACTOR_FAMILY_DEFS = [
  { id: "trend", label: "趋势", tip: "价格方向、均线、形态；OLS 趋势族共线只打这一组" },
  { id: "value_quality", label: "价值质量", tip: "基本面：估值、盈利收益率、质量、成长、股息" },
  { id: "liquidity_flow", label: "流动性", tip: "成交、冲击成本、资金流" },
  { id: "risk", label: "风险", tip: "波动、跳空" },
  { id: "residual", label: "残差", tip: "相对市场、特异动量、规模" },
  { id: "reversal", label: "反转", tip: "短线反转，与动量拆开避免双计" },
  { id: "sentiment", label: "舆情", tip: "研究旁路，默认不进 ŷ" },
  { id: "other", label: "其他", tip: "未编入 factor_groups" },
];

const FAMILY_MEMBERS = {
  trend: ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
  value_quality: ["value", "earnings_yield", "quality", "growth", "dividend"],
  liquidity_flow: ["liquidity", "money_flow", "amihud", "volume_price"],
  risk: ["volatility", "gap_risk"],
  residual: ["relative_strength", "idio_momentum", "size"],
  reversal: ["reversal"],
  sentiment: ["alt_sentiment", "llm_sentiment"],
};

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
export const REMOVED_RAW_BASIS_NAMES = new Set([
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
  const family = NAME_TO_FAMILY[key] || "other";
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
 * @param {object} [meta] factorMeta 行（API 优先）
 */
export function classifyFactor(name, meta) {
  const m = meta && typeof meta === "object" ? meta : {};
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
  return fallbackClassify(name);
}

/** ŷ_τ 开盘/截面特征不在因子注册表，本地兜底注释。 */
export const TAU_FEAT_META = {
  gap_pct: {
    label: "跳空 %",
    description:
      "开盘相对昨收的跳空幅度（%）。ŷ_τ 用它预测开盘→收盘剩余收益；τ=open 时即隔夜缺口。",
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
      "开盘到 τ 时刻已实现收益（%）。τ=open 时为 0；τ=09:45 时为开盘→09:45。ŷ_τ 预测的是 τ 之后到收盘的剩余。",
  },
  sector_ret_to_tau: {
    label: "板块中位开→τ %",
    description:
      "同日池内开盘→τ 已实现收益的中位数（%）。仅分钟 τ 训练/预测用，剥离板块盘中 beta。",
  },
};

/** ŷ_ON 路径/开盘 Z 特征（不在因子注册表）。 */
export const ON_FEAT_META = {
  ret_oc: {
    label: "开→收 %",
    description:
      "T 日已实现开→收收益（%）。收盘前决策时，刻画当日 intraday 路径，用于估 open[T+1]/open[T]−1。",
  },
  ret_cc: {
    label: "收→收 %",
    description:
      "T 日收→收涨跌（%）。与 EOD 标签同口径的当日已实现部分，辅助 ON 头看路径惯性。",
  },
  y_on_today: {
    label: "今开/昨开 %",
    description:
      "T 日已实现 open/open[T−1]−1（%）。即 y_ON(T) 的当日段，不是训练标签（标签为 open[T+1]/open[T]−1）。",
  },
  gap_pct: TAU_FEAT_META.gap_pct,
  sector_gap_breadth: TAU_FEAT_META.sector_gap_breadth,
  theme_day: TAU_FEAT_META.theme_day,
  gap_atr: TAU_FEAT_META.gap_atr,
  gap_vs_sector: TAU_FEAT_META.gap_vs_sector,
  ret_open_to_tau: TAU_FEAT_META.ret_open_to_tau,
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
    if (Object.keys(factorMetaByName).length) return factorMetaByName;
    if (factorMetaPromise) return factorMetaPromise;
    factorMetaPromise = (async () => {
      try {
        const res = await fetchImpl("/api/quant/factors");
        const data = await res.json();
        const list = data.factors || data.rows || [];
        rememberFactorMeta(
          (list || []).map((r) => ({
            name: r.name || r.factor,
            label: r.label,
            description: r.description || "",
            family: r.family || "",
            family_label: r.family_label || "",
            family_tip: r.family_tip || "",
            source: r.source || "",
            source_label: r.source_label || "",
            source_note: r.source_note || "",
          }))
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
    if (key && ON_FEAT_META[key]) return ON_FEAT_META[key];
    const lab = label != null ? String(label).trim() : "";
    if (!lab) return null;
    for (const m of Object.values(ON_FEAT_META)) {
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
