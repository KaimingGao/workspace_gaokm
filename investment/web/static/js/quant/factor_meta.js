/**
 * 因子元数据缓存（/api/quant/factors）。
 */
import { escapeHtml } from "../shared.js";

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

  function factorDescription(name, label) {
    const meta =
      (name && factorMetaByName[name]) ||
      (label && factorMetaByLabel[label]) ||
      (name && factorMetaByLabel[name]) ||
      {};
    const fromApi = String(meta.description || "").trim();
    if (fromApi) return fromApi;
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

  return {
    factorMetaByName,
    factorMetaByLabel,
    rememberFactorMeta,
    ensureFactorMeta,
    factorDescription,
    factorNameCellHtml,
  };
}
