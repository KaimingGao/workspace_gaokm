/**
 * 因子 IC + OLS β 合并表 HTML。
 */
import { escapeHtml } from "../shared.js";
import { researchGridHtml, metricCell } from "./research_grid.js";
import { metricClass } from "./bt_result.js";
import { classifyFactor, isRemovedFactor, CO_FEAT_META } from "./factor_meta.js";

/**
 * @param {{
 *   escapeHtml?: typeof escapeHtml,
 *   researchGridHtml?: typeof researchGridHtml,
 *   metricCell?: typeof metricCell,
 *   metricClass?: typeof metricClass,
 *   factorMetaByName: Record<string, object>,
 *   factorMetaByLabel: Record<string, object>,
 *   factorNameCellHtml: (name: string, label?: string, fallbackDescription?: string) => string,
 *   factorTaxonomyCellHtml?: (name: string, label?: string, fallbackDescription?: string) => string,
 * }} deps
 */
export function createFactorIcUi(deps) {
  const esc = deps.escapeHtml || escapeHtml;
  const gridHtml = deps.researchGridHtml || researchGridHtml;
  const cell = deps.metricCell || metricCell;
  const mcls = deps.metricClass || metricClass;
  const factorMetaByName = deps.factorMetaByName;
  const factorMetaByLabel = deps.factorMetaByLabel;
  const factorNameCellHtml = deps.factorNameCellHtml;
  const factorTaxonomyCellHtml =
    deps.factorTaxonomyCellHtml ||
    ((name, label, fallbackDescription) =>
      factorNameCellHtml(name, label, fallbackDescription));

  function fmtEmptyCell() {
    return `<span class="quant-cell-empty">—</span>`;
  }

  function _tauClocksFromYSpec(formula, ySpec) {
    const grid = Array.isArray(ySpec && ySpec.tau_grid) ? ySpec.tau_grid : [];
    const fromSpec = grid
      .map((x) => String(x || "").trim().slice(0, 5))
      .filter((s) => /^\d{2}:\d{2}$/.test(s));
    if (fromSpec.length >= 2) return fromSpec;
    const m = String(formula || "").match(/τ∈\{([^}]+)\}/);
    if (!m) return fromSpec;
    return m[1]
      .split(",")
      .map((s) => s.trim().slice(0, 5))
      .filter((s) => /^\d{2}:\d{2}$/.test(s));
  }

  function compactYSpecFormula(formula, ySpec) {
    const raw = String(formula || "").trim();
    const clocks = _tauClocksFromYSpec(raw, ySpec);
    if (clocks.length < 3) return raw;
    const base = (raw.split("·")[0] || raw).trim() || raw;
    return `${base} · 5m τ ${clocks[0]}–${clocks[clocks.length - 1]}`;
  }

  function pickTauAnchorKeys(keys) {
    const sorted = [...keys].sort();
    const out = [];
    const add = (k) => {
      if (k && sorted.includes(k) && !out.includes(k)) out.push(k);
    };
    add(sorted[0]);
    if (sorted.length > 1) add(sorted[1]);
    add("10:00");
    add(sorted[sorted.length - 1]);
    return out;
  }

  function tauHitSparkline(keys, byTau, anchorKeys) {
    const sorted = [...keys].sort();
    const anchors = Array.isArray(anchorKeys) ? anchorKeys : [];
    const hits = sorted.map((t) => {
      const n = Number((byTau[t] || {}).sign_hit);
      return Number.isFinite(n) ? n : null;
    });
    const vals = hits.filter((v) => v != null);
    if (vals.length < 2) return "";
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = Math.max(1e-6, hi - lo);
    const w = 88;
    const h = 22;
    const padX = 3;
    const padY = 3;
    const xy = [];
    hits.forEach((v, i) => {
      if (v == null) return;
      const x = padX + (i / Math.max(1, hits.length - 1)) * (w - 2 * padX);
      const y = h - padY - ((v - lo) / span) * (h - 2 * padY);
      const t = sorted[i];
      xy.push({
        t,
        x,
        y,
        live: t === "10:00",
        anchor: anchors.includes(t),
      });
    });
    if (xy.length < 2) return "";
    const line = xy.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const yBase = (h - padY).toFixed(1);
    const area =
      `${xy[0].x.toFixed(1)},${yBase} ${line} ${xy[xy.length - 1].x.toFixed(1)},${yBase}`;
    const y50 =
      lo < 0.5 && hi > 0.5
        ? h - padY - ((0.5 - lo) / span) * (h - 2 * padY)
        : null;
    const live = xy.find((p) => p.live);
    return (
      `<svg class="quant-rem-coef-tau-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` +
      (y50 != null
        ? `<line class="is-base" x1="${padX}" x2="${w - padX}" y1="${y50.toFixed(1)}" y2="${y50.toFixed(1)}"></line>`
        : "") +
      (live
        ? `<line class="is-live-x" x1="${live.x.toFixed(1)}" x2="${live.x.toFixed(1)}" y1="${padY}" y2="${yBase}"></line>`
        : "") +
      `<polygon class="is-area" points="${area}"></polygon>` +
      `<polyline class="is-line" points="${line}"></polyline>` +
      xy
        .map((p) => {
          const r = p.live ? 2.4 : p.anchor ? 1.85 : 1.0;
          const cls = p.live ? "is-live" : p.anchor ? "is-anchor" : "";
          return `<circle class="${cls}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}"></circle>`;
        })
        .join("") +
      `</svg>`
    );
  }

  function fmtOlsCell(v) {
    if (v == null || v === "") return fmtEmptyCell();
    const n = Number(v);
    if (!Number.isFinite(n)) return esc(String(v));
    const text = n.toFixed(4);
    return cell(esc(text), mcls(n));
  }

  /** 因子 IC + 因子系数 β 合并为一张表（一组一表主列=β，非权重） */
  function factorIcWeightMergedHtml(panelOrExp, suggest, ols) {
    const panelRows =
      (panelOrExp && panelOrExp.rows) ||
      (panelOrExp && panelOrExp.panel && panelOrExp.panel.rows) ||
      (panelOrExp && panelOrExp.factors) ||
      [];
    const list = Array.isArray(panelRows) ? panelRows : [];
    const coefFromSuggest =
      suggest && suggest.coefficients && typeof suggest.coefficients === "object"
        ? suggest.coefficients
        : {};
    const isGroupTsIc = !!(panelOrExp && panelOrExp.mode === "group_ts_ic");
    const isGroupCsIc = !!(panelOrExp && panelOrExp.mode === "group_cs_ic");
    const olsCoefs =
      ols && ols.success && ols.coefficients && typeof ols.coefficients === "object"
        ? ols.coefficients
        : {};
    const olsReasons =
      ols && ols.exclusion_reasons && typeof ols.exclusion_reasons === "object"
        ? ols.exclusion_reasons
        : {};
    const icReasonsTop =
      (panelOrExp &&
        panelOrExp.exclusion_reasons &&
        typeof panelOrExp.exclusion_reasons === "object" &&
        panelOrExp.exclusion_reasons) ||
      (panelOrExp &&
        panelOrExp.panel &&
        panelOrExp.panel.exclusion_reasons &&
        typeof panelOrExp.panel.exclusion_reasons === "object" &&
        panelOrExp.panel.exclusion_reasons) ||
      {};
    const IC_REASON = {
      sparse: { short: "缺测", tip: "有效配对不足（n<3）" },
      constant: { short: "常数", tip: "因子分几乎无波动" },
      flat: { short: "收益平", tip: "前瞻收益几乎无波动" },
      other: { short: "未算", tip: "暂无有效 IC" },
    };
    const OLS_REASON = {
      sparse: { short: "缺测", tip: "有效观测过少（如缺基本面 PIT / 数据源）" },
      constant: { short: "常数", tip: "样本内几乎常数，无法估系数" },
      coverage: { short: "覆盖", tip: "为凑完整行被剔除" },
      collinear: { short: "共线", tip: "共线/奇异被剔除" },
      other: { short: "未入模", tip: "未进入最终回归" },
    };

    const byName = {};
    for (const r of list) {
      const name = r.factor || r.name || "";
      if (!name || isRemovedFactor(name)) continue;
      const meta0 = factorMetaByName[name] || {};
      const label =
        (r.label && r.label !== name ? r.label : null) ||
        meta0.label ||
        r.label ||
        name;
      if (r.description) {
        const prev = factorMetaByName[name] || {};
        const tax = classifyFactor(name, prev);
        factorMetaByName[name] = {
          ...prev,
          name,
          label: label !== name ? label : meta0.label || label,
          description: r.description,
          family: tax.family,
          family_label: tax.familyLabel,
          family_tip: tax.familyTip,
          source: tax.source,
          source_label: tax.sourceLabel,
          source_note: tax.sourceNote,
        };
        const lab = factorMetaByName[name].label;
        if (lab) factorMetaByLabel[lab] = factorMetaByName[name];
      }
      const ic =
        r.ic != null ? Number(r.ic) : r.ic_mean != null ? Number(r.ic_mean) : null;
      const pear = r.pearson && typeof r.pearson === "object" ? r.pearson : null;
      const nRaw =
        r.sample_count != null
          ? r.sample_count
          : r.n != null
            ? r.n
            : pear && pear.day_count != null
              ? pear.day_count
              : pear && pear.n != null
                ? pear.n
                : null;
      const n = nRaw != null && Number.isFinite(Number(nRaw)) ? Number(nRaw) : null;
      let icir =
        r.icir != null
          ? Number(r.icir)
          : pear && pear.icir != null
            ? Number(pear.icir)
            : null;
      if (icir != null && !Number.isFinite(icir)) icir = null;
      const icReason =
        r.exclusion_reason ||
        r.ic_reason ||
        (ic == null ? icReasonsTop[name] : null) ||
        null;
      byName[name] = { name, label, ic, n, icir, icReason };
    }
    const nameSet = new Set([
      ...Object.keys(byName),
      ...Object.keys(olsCoefs),
      ...Object.keys(coefFromSuggest),
      ...Object.keys(olsReasons),
      ...Object.keys(icReasonsTop),
    ].filter((n) => !isRemovedFactor(n)));
    if (!nameSet.size) return "";

    const rows = [...nameSet]
      .map((name) => {
        const meta = factorMetaByName[name] || {};
        const base = byName[name] || {
          name,
          label: meta.label || name,
          ic: null,
          n: null,
          icir: null,
          icReason: icReasonsTop[name] || null,
        };
        if (meta.label && (!base.label || base.label === name)) {
          base.label = meta.label;
        }
        const hasOlsKey =
          Object.prototype.hasOwnProperty.call(olsCoefs, name) ||
          Object.prototype.hasOwnProperty.call(coefFromSuggest, name);
        const olsVal = hasOlsKey
          ? olsCoefs[name] != null
            ? olsCoefs[name]
            : coefFromSuggest[name]
          : null;
        const olsReason = olsReasons[name] || null;
        let icReason =
          base.icReason || (base.ic == null ? icReasonsTop[name] : null) || null;
        if (base.ic == null && !icReason && list.length) icReason = "other";
        const absBeta =
          olsVal != null && Number.isFinite(Number(olsVal))
            ? Math.abs(Number(olsVal))
            : 0;
        const tax = classifyFactor(name, meta);
        return {
          ...base,
          ols: olsVal,
          olsReason,
          icReason,
          scanHot: absBeta >= 0.05,
          family: tax.family,
          familyLabel: tax.familyLabel,
          familyTip: tax.familyTip,
          familyOrder: tax.familyOrder,
          source: tax.source,
        };
      })
      .sort(
        (a, b) =>
          (a.familyOrder ?? 99) - (b.familyOrder ?? 99) ||
          String(a.name).localeCompare(String(b.name))
      );

    return gridHtml(
      [
        {
          id: "factor",
          label: "因子",
          flex: true,
          flexMin: "18rem",
          flexFr: 2.4,
          title: "名称 + 经济族 / 来源徽章",
        },
        {
          id: "ic",
          label: "IC",
          widthPct: 11,
          num: true,
          title: isGroupCsIc
            ? "组内日截面 IC 均值：每日组员横截面 corr(因子, 前瞻收益) 再对日平均"
            : isGroupTsIc
              ? "单票组时序 IC 回退：因子值与前瞻收益的 Pearson"
              : "因子与前瞻收益相关（截面为日均 IC）",
        },
        {
          id: "icir",
          label: "ICIR",
          widthPct: 11,
          num: true,
          title: isGroupCsIc
            ? "组内 IC̄/σ(IC)：日截面 IC 序列稳定性"
            : isGroupTsIc
              ? "单票组无日截面序列，ICIR 不适用"
              : "IC̄/σ(IC)：截面 IC 稳定性",
        },
        {
          id: "n",
          label: "n",
          widthPct: 8,
          num: true,
          title: isGroupCsIc
            ? "有效截面日数（每日至少 min_names 只组员）"
            : isGroupTsIc
              ? "单票组该因子有效配对样本数"
              : "有效样本数",
        },
        {
          id: "ols",
          label: "系数β",
          widthPct: 16,
          num: true,
          title:
            "因子系数 β：组内/单票 OLS 对标准化因子的 ŷ% 斜率（可负）。" +
            "选股真源；不是归一化权重。",
        },
      ],
      rows,
      (col, r) => {
        if (col.id === "factor") return factorTaxonomyCellHtml(r.name, r.label);
        if (col.id === "ic") {
          if (r.ic != null) {
            const cls = r.ic >= 0.03 ? "up" : r.ic <= -0.03 ? "down" : "";
            const tip = isGroupCsIc
              ? `组内日均截面 IC=${Number(r.ic).toFixed(4)}`
              : isGroupTsIc
                ? `单票组时序 IC=${Number(r.ic).toFixed(4)}`
                : `IC=${Number(r.ic).toFixed(4)}`;
            return `<span title="${esc(tip)}">${cell(
              esc(Number(r.ic).toFixed(4)),
              cls
            )}</span>`;
          }
          if (r.icReason) {
            const info = IC_REASON[r.icReason] || IC_REASON.other;
            return `<span class="quant-factor-reason" title="${esc(info.tip)}">${esc(
              info.short
            )}</span>`;
          }
          return fmtEmptyCell();
        }
        if (col.id === "icir") {
          if (
            isGroupTsIc &&
            (r.icir == null || !Number.isFinite(Number(r.icir)))
          ) {
            return `<span class="quant-factor-reason" title="单票组时序 IC，无日截面序列">不适用</span>`;
          }
          if (r.icir == null || !Number.isFinite(Number(r.icir))) return fmtEmptyCell();
          const v = Number(r.icir);
          const cls = v >= 0.25 ? "up" : v <= -0.25 ? "down" : "";
          const tip = "ICIR = 日截面 IC 均值 / 标准差";
          return `<span title="${esc(tip)}">${cell(
            esc(v.toFixed(2)),
            cls
          )}</span>`;
        }
        if (col.id === "n")
          return r.n != null ? esc(String(r.n)) : fmtEmptyCell();
        if (col.id === "ols") {
          if (r.ols != null) {
            const tip =
              `因子系数 β=${Number(r.ols).toFixed(4)}：z 上 ŷ 百分点斜率；驱动组收益分`;
            return `<span title="${esc(tip)}">${fmtOlsCell(r.ols)}</span>`;
          }
          if (r.olsReason) {
            const info = OLS_REASON[r.olsReason] || OLS_REASON.other;
            return `<span class="quant-factor-reason" title="${esc(info.tip)}">${esc(
              info.short
            )}</span>`;
          }
          return fmtEmptyCell();
        }
        return fmtEmptyCell();
      },
      {
        emptyText: "暂无因子",
        rowClass: (r) => (r && r.scanHot ? "is-scan-hot" : ""),
      }
    );
  }

  const REM_FEAT_LABELS = {
    gap_pct: "跳空 %",
    open_gap: "开盘缺口",
    sector_gap_breadth: "同业缺口广度",
    theme_day: "主题日",
    gap_atr: "缺口 / ATR",
    gap_vs_sector: "行业相对缺口",
    yclose_loc: "昨收位置",
    mom3_pct: "近3日动量 %",
    tau_lag1: "昨真实 close/open−1 %",
    tau_ma5: "近5日真实 close/open−1 均 %",
    t30_lag1: "昨同钟真实 τ⊕25/30/35均 %",
    t30_ma5: "近5日同钟真实 τ⊕25/30/35均 %",
    ret_last_45m: "近45交易分钟收益 %",
    crosses_lunch_45: "未来45m是否跨午休",
    vol_last_45m_vs_avg: "近45m量/前缀均量",
    sector_ret_last_45m: "板块中位近45m %",
    ret_last_45m_vs_sector: "近45m相对板块 %",
    t45_lag1: "昨同钟真实 τ⊕40/45/50均 %",
    t45_ma5: "近5日同钟真实 τ⊕40/45/50均 %",
    ret_last_60m: "近60交易分钟收益 %",
    crosses_lunch_60: "未来60m是否跨午休",
    vol_last_60m_vs_avg: "近60m量/前缀均量",
    sector_ret_last_60m: "板块中位近60m %",
    ret_last_60m_vs_sector: "近60m相对板块 %",
    t60_lag1: "昨同钟真实 τ⊕55/60/65均 %",
    t60_ma5: "近5日同钟真实 τ⊕55/60/65均 %",
    ret_last_75m: "近75交易分钟收益 %",
    crosses_lunch_75: "未来75m是否跨午休",
    vol_last_75m_vs_avg: "近75m量/前缀均量",
    sector_ret_last_75m: "板块中位近75m %",
    ret_last_75m_vs_sector: "近75m相对板块 %",
    t75_lag1: "昨同钟真实 τ⊕70/75/80均 %",
    t75_ma5: "近5日同钟真实 τ⊕70/75/80均 %",
    ret_last_90m: "近90交易分钟收益 %",
    crosses_lunch_90: "未来90m是否跨午休",
    vol_last_90m_vs_avg: "近90m量/前缀均量",
    sector_ret_last_90m: "板块中位近90m %",
    ret_last_90m_vs_sector: "近90m相对板块 %",
    t90_lag1: "昨同钟真实 τ⊕85/90/95均 %",
    t90_ma5: "近5日同钟真实 τ⊕85/90/95均 %",
    path_lag1: "昨真实极值序 %",
    path_ma5: "近5日真实极值序均 %",
    ret_open_to_tau: "开盘→τ 收益 %",
    ret_prev_to_tau: "昨收→τ 收益 %",
    range_pct: "前缀振幅 %",
    loc_hl: "HL 位置",
    up_extent: "相对开盘上探 %",
    down_extent: "相对开盘下探 %",
    path_sign: "路径符号",
    pullback_from_high: "自高回撤 %",
    bounce_from_low: "自低反弹 %",
    ret_last_15m: "近15m 收益 %",
    ret_last_5m: "近5m 收益 %",
    ret_last_30m: "近30交易分钟收益 %",
    session_elapsed: "已过交易分钟（09:30=0）",
    session_remain: "距收盘剩余交易分钟",
    crosses_lunch: "未来30m是否跨午休",
    session_vwap_dev: "τ价相对会话VWAP %",
    vol_last_30m_vs_avg: "近30m量/前缀均量",
    sector_ret_last_30m: "板块中位近30m %",
    ret_last_30m_vs_sector: "近30m相对板块 %",
    realized_vol: "前缀已实现波动 %",
    vol_last3_vs_avg: "近3根量/均量",
    tau_elapsed_min: "τ距开盘分钟",
    sector_ret_to_tau: "板块中位开→τ %",
    ret_vs_sector: "开→τ 相对板块 %",
    t_hi_frac: "最高点相对前缀进度",
    t_lo_frac: "最低点相对前缀进度",
    t_hi_minus_lo: "高点进度−低点进度",
    room_to_high: "距前缀高点空间 %",
    room_to_low: "距前缀低点空间 %",
    mom_accel_5_15: "近5m−近15m 动量差 %",
    mom_accel_5_30: "近5m−近30m 动量差 %",
    vol_down_up: "下跌量/上涨量",
    range_efficiency: "|开→τ|/振幅（趋势效率 0–1）",
    vp_confirm: "开→τ×(近3量比−1) 量价确认",
    vol_up_share: "上涨量占比 0–1",
    pullback_x_vol: "自高回撤×近3量比",
    ret_oc: "昨开→昨收 %",
    ret_cc: "昨收→前收 %",
    y_on_today: "今开/昨开 %",
    yest_close_loc: "昨收位置",
    yest_range_pct: "昨振幅 %",
    yest_vol_ratio: "昨量/均量",
    dist_to_up_limit: "距涨停 %",
    yest_gap: "昨隔夜缺口 %",
    on_ma5: "近5日隔夜缺口均 %",
    // 与 factor_registry 对齐的兜底中文（meta 未加载时仍可读）
    momentum: "动量",
    volume_price: "量价",
    volatility: "波动",
    relative_strength: "相对强弱",
    reversal: "反转",
    liquidity: "流动性",
    value: "估值",
    quality: "质量",
    technical_pattern: "技术形态",
    weekly_confirm: "周线确认",
    ma_slope: "均线斜率",
    alt_sentiment: "舆情",
    llm_sentiment: "LLM舆情",
    gap_risk: "跳空风险",
    size: "规模",
    earnings_yield: "盈利收益率",
    growth: "成长",
    dividend: "股息",
    money_flow: "资金流",
    amihud: "非流动性",
    idio_momentum: "特异动量",
  };

  function remFactorDisplayLabel(name) {
    const key = String(name || "").trim();
    if (!key) return "—";
    const meta = factorMetaByName[key] || {};
    const fromMeta = meta.label != null ? String(meta.label).trim() : "";
    // meta 若仍是英文键名，继续兜底
    if (fromMeta && fromMeta !== key) return fromMeta;
    return REM_FEAT_LABELS[key] || fromMeta || key;
  }

  const LAG_FEAT_CANON = {};
  const LAG_FEAT_ALIASES = {};

  function canonLagFeatName(name) {
    const key = String(name || "").trim();
    return LAG_FEAT_CANON[key] || key;
  }

  function pickNamedNum(bag, canon) {
    if (!bag || typeof bag !== "object") return null;
    const keys = [canon, ...(LAG_FEAT_ALIASES[canon] || [])];
    for (const k of keys) {
      if (bag[k] != null && Number.isFinite(Number(bag[k]))) return Number(bag[k]);
    }
    return null;
  }

  /**
   * ŷ_oc / ŷ_co Ridge 因子系数表：仅入模因子；KPI + 双向 β 图 + 表内条形。
   * @param {object|null} rm return_model 或含 coefficients 的报告块
   * @param {{ oos?: object, head?: "tau"|"oo"|"co"|"path"|"r"|"t30"|"t45"|"t60"|"t75"|"t90"|"oo_rank", topN?: number } } [opts]
   *   ``topN`` 默认 10：表体只展 |β| TopN，其余进折叠。
   */
  function remCoefTableHtml(rm, opts = {}) {
    if (!rm || typeof rm !== "object") return "";
    const isOo = opts.head === "oo";
    const isCo = opts.head === "co";
    const isOoRank = opts.head === "oo_rank";
    const isT30 = opts.head === "t30";
    const isT45 = opts.head === "t45";
    const isT60 = opts.head === "t60";
    const isT75 = opts.head === "t75";
    const isT90 = opts.head === "t90";
    const isHorizon = isT30 || isT45 || isT60 || isT75 || isT90;
    const isTc = opts.head === "τc" || opts.head === "tc" || opts.head === "tau";
    const isR = opts.head === "r" || isHorizon;
    const isPath = opts.head === "path";
    const yhatTag = isPath
      ? "ŷ_hl"
      : isOo
        ? "ŷ_oo"
      : isOoRank
        ? "ŷ_oo_rank"
        : isCo
        ? "ŷ_co"
        : isT90
          ? "ŷ_τ90"
          : isT75
            ? "ŷ_τ75"
            : isT60
              ? "ŷ_τ60"
              : isT45
                ? "ŷ_τ45"
                : isT30
                  ? "ŷ_τ30"
              : opts.head === "r" || isTc
                ? "ŷ_τc"
                : "ŷ_oc";
    const coefs =
      rm.coefficients && typeof rm.coefficients === "object" ? rm.coefficients : {};
    const means =
      (rm.zscore_means && typeof rm.zscore_means === "object" && rm.zscore_means) ||
      (rm.z_means && typeof rm.z_means === "object" && rm.z_means) ||
      {};
    const stds =
      (rm.zscore_stds && typeof rm.zscore_stds === "object" && rm.zscore_stds) ||
      (rm.z_stds && typeof rm.z_stds === "object" && rm.z_stds) ||
      {};
    const remOpenExtra = new Set([
      "gap_pct",
      "open_gap",
      "sector_gap_breadth",
      "theme_day",
      "gap_atr",
      "gap_vs_sector",
      "yclose_loc",
      "mom3_pct",
    ]);
    const remMinuteExtra = new Set([
      "ret_open_to_tau",
      "ret_prev_to_tau",
      "range_pct",
      "loc_hl",
      "up_extent",
      "down_extent",
      "path_sign",
      "pullback_from_high",
      "bounce_from_low",
      "ret_last_15m",
      "ret_last_5m",
      "ret_last_30m",
      "ret_last_45m",
      "ret_last_60m",
      "ret_last_75m",
      "ret_last_90m",
      "session_elapsed",
      "session_remain",
      "crosses_lunch",
      "crosses_lunch_45",
      "crosses_lunch_60",
      "crosses_lunch_75",
      "crosses_lunch_90",
      "session_vwap_dev",
      "vol_last_30m_vs_avg",
      "vol_last_45m_vs_avg",
      "vol_last_60m_vs_avg",
      "vol_last_75m_vs_avg",
      "vol_last_90m_vs_avg",
      "sector_ret_last_30m",
      "ret_last_30m_vs_sector",
      "sector_ret_last_45m",
      "ret_last_45m_vs_sector",
      "sector_ret_last_60m",
      "ret_last_60m_vs_sector",
      "sector_ret_last_75m",
      "ret_last_75m_vs_sector",
      "sector_ret_last_90m",
      "ret_last_90m_vs_sector",
      "realized_vol",
      "vol_last3_vs_avg",
      "tau_elapsed_min",
      "sector_ret_to_tau",
      "ret_vs_sector",
      "t_hi_frac",
      "t_lo_frac",
      "t_hi_minus_lo",
      "room_to_high",
      "room_to_low",
      "mom_accel_5_15",
      "mom_accel_5_30",
      "vol_down_up",
      "range_efficiency",
      "vp_confirm",
      "vol_up_share",
      "pullback_x_vol",
    ]);
    const onPathExtra = new Set([
      "ret_oc",
      "ret_cc",
      "y_on_today",
      "yest_close_loc",
      "yest_range_pct",
      "yest_vol_ratio",
    ]);
    const onOpenExtra = new Set([
      "gap_pct",
      "sector_gap_breadth",
      "theme_day",
      "gap_atr",
      "gap_vs_sector",
      "yclose_loc",
      "mom3_pct",
      "dist_to_up_limit",
    ]);
    const histLagExtra = new Set([
      "tau_lag1",
      "tau_ma5",
      "t30_lag1",
      "t30_ma5",
      "t45_lag1",
      "t45_ma5",
      "t60_lag1",
      "t60_ma5",
      "t75_lag1",
      "t75_ma5",
      "t90_lag1",
      "t90_ma5",
      "path_lag1",
      "path_ma5",
      "yest_gap",
      "on_ma5",
    ]);
    const pathExtra = new Set([
      "gap_pct",
      "sector_gap_breadth",
      "theme_day",
      "gap_atr",
      "gap_vs_sector",
      "yclose_loc",
      "mom3_pct",
    ]);
    const activeList = Array.isArray(rm.active_features)
      ? rm.active_features.map(String)
      : Object.keys(coefs);
    const seenCanon = new Set();
    const canonActive = [];
    for (const raw of activeList) {
      const name = canonLagFeatName(raw);
      if (seenCanon.has(name)) continue;
      seenCanon.add(name);
      canonActive.push(name);
    }
    let rows = canonActive
      .map((name) => {
        const ols = pickNamedNum(coefs, name);
        if (ols == null) return null;
        const mu = pickNamedNum(means, name);
        const sd = pickNamedNum(stds, name);
        let kind;
        if (histLagExtra.has(name)) {
          kind = "历史";
        } else if (isPath) {
          if (remMinuteExtra.has(name)) kind = "分钟";
          else if (pathExtra.has(name) || remOpenExtra.has(name)) kind = "开盘";
          else kind = "其它";
        } else if (isCo) {
          if (histLagExtra.has(name)) kind = "历史";
          else if (onOpenExtra.has(name)) kind = "开盘";
          else if (onPathExtra.has(name)) kind = "路径";
          else kind = "其它";
        } else if (remMinuteExtra.has(name)) {
          kind = "分钟";
        } else if (remOpenExtra.has(name)) {
          kind = "开盘";
        } else {
          kind = "日线";
        }
        return {
          name,
          label:
            isCo && CO_FEAT_META[name] && CO_FEAT_META[name].label
              ? CO_FEAT_META[name].label
              : remFactorDisplayLabel(name),
          ols,
          abs: Math.abs(ols),
          kind,
          mu,
          sd,
          scanHot: Math.abs(ols) >= 0.05,
        };
      })
      .filter(Boolean);
    const absSum = rows.reduce((s, r) => s + r.abs, 0) || 1;
    const maxAbs = Math.max(...rows.map((r) => r.abs), 1e-9);
    rows = rows
      .map((r) => ({
        ...r,
        share: (r.abs / absSum) * 100,
      }))
      .sort((a, b) => b.abs - a.abs)
      .map((r, i) => ({ ...r, rank: i + 1 }));
    if (!rows.length) {
      return `<p class="sub">暂无 ${yhatTag} 入模因子</p>`;
    }

    const researchRm =
      opts.researchModel && typeof opts.researchModel === "object"
        ? opts.researchModel
        : null;
    const researchIntercept =
      researchRm &&
      researchRm.intercept != null &&
      Number.isFinite(Number(researchRm.intercept))
        ? Number(researchRm.intercept)
        : null;
    const intercept =
      rm.intercept != null && Number.isFinite(Number(rm.intercept))
        ? Number(rm.intercept)
        : null;
    const n =
      rm.sample_count != null
        ? rm.sample_count
        : rm.n_obs != null
          ? rm.n_obs
          : null;
    const r2 =
      rm.r_squared != null && Number.isFinite(Number(rm.r_squared))
        ? Number(rm.r_squared)
        : null;
    const lam =
      rm.ridge_lambda != null && Number.isFinite(Number(rm.ridge_lambda))
        ? Number(rm.ridge_lambda)
        : null;
    const ySpecObj = rm.y_spec && typeof rm.y_spec === "object" ? rm.y_spec : {};
    const ySpecRaw =
      ySpecObj.formula ||
      (isPath
        ? "extreme_order(low,high)"
        : isOo
          ? "open[T+1]/open[T]-1"
        : isCo
          ? "open[T+1]/close[T]-1"
          : isT90
            ? "mean(price[τ+85m],price[τ+90m],price[τ+95m])/price[τ]-1"
            : isT75
              ? "mean(price[τ+70m],price[τ+75m],price[τ+80m])/price[τ]-1"
              : isT60
                ? "mean(price[τ+55m],price[τ+60m],price[τ+65m])/price[τ]-1"
                : isT45
                  ? "mean(price[τ+40m],price[τ+45m],price[τ+50m])/price[τ]-1"
                  : isT30
                    ? "mean(price[τ+25m],price[τ+30m],price[τ+35m])/price[τ]-1"
                : opts.head === "r"
                  ? "price[τ]/close[T]-1"
                  : isTc
                    ? "close[T]/price[τ]-1"
                    : "close[T]/open[T]-1");
    const ySpecProbRaw =
      isHorizon && (ySpecObj.unit === "prob" || String(rm.head_kind || "") === "prob")
        ? ySpecObj.label ||
          (String(ySpecRaw).trim().startsWith("I(")
            ? ySpecRaw
            : `I((${String(ySpecRaw).split("·")[0].trim() || ySpecRaw})>0)`)
        : ySpecRaw;
    const ySpec = compactYSpecFormula(ySpecProbRaw, ySpecObj);
    const ySpecClocks = _tauClocksFromYSpec(ySpecRaw, ySpecObj);
    const oos = opts.oos || {};
    const useProbKpis =
      isHorizon &&
      ((oos.auc != null && Number.isFinite(Number(oos.auc))) ||
        ySpecObj.unit === "prob" ||
        String(rm.head_kind || "") === "prob");
    const openN = rows.filter((r) => r.kind === "开盘" || r.kind === "路径").length;
    const minuteN = rows.filter((r) => r.kind === "分钟").length;
    const histN = rows.filter((r) => r.kind === "历史").length;
    const extraList = [
      ...new Set(
        (Array.isArray(rm.extra_features) ? rm.extra_features : [])
          .map((k) => canonLagFeatName(k))
          .filter(Boolean)
      ),
    ];
    const activeSet = new Set(canonActive);
    const omittedFromExtra = extraList.filter((k) => !activeSet.has(k));
    const exclKeys = Object.keys(rm.exclusion_reasons || {});
    const exclN = new Set([...exclKeys, ...omittedFromExtra]).size;
    const omitTip = omittedFromExtra.length
      ? `未入模：${omittedFromExtra.join("、")}`
      : exclN > 0
        ? "未入模因子已隐藏"
        : "";
    const posN = rows.filter((r) => r.ols >= 0).length;
    const negN = rows.length - posN;
    const ySpecTip =
      `${yhatTag} 训练标签 · ${ySpec}` +
      (ySpecClocks.length >= 2
        ? /5m τ/.test(ySpec)
          ? ` · ${ySpecClocks.length} 钟`
          : ` · ${ySpecClocks.length} 钟每5分 ${ySpecClocks[0]}–${ySpecClocks[ySpecClocks.length - 1]}`
        : "");

    const kpi = (label, value, tip, opts = {}) => {
      let tone = "";
      if (opts.signed) {
        const n = Number(opts.raw != null ? opts.raw : value);
        if (Number.isFinite(n) && n !== 0) tone = n > 0 ? "up" : "down";
      }
      return (
        `<span class="quant-rem-coef-kpi" title="${esc(tip || String(value) || label)}">` +
        `<span class="quant-rem-coef-kpi-k">${esc(label)}</span>` +
        `<span class="quant-rem-coef-kpi-v${tone ? ` ${tone}` : ""}">${esc(String(value))}</span>` +
        `</span>`
      );
    };

    const nFmt =
      n != null && Number.isFinite(Number(n))
        ? Number(n).toLocaleString("en-US")
        : null;
    const interceptTip = isHorizon
        ? "无因子时的 p_up = sigmoid(logistic 截距)。执行套=全样本。"
        : isR
          ? "执行套截距：全样本标签均值 + α_dm。观察/持仓 / 做 T 回测默认用这套。选研究套 Holdout 时数字会不同。"
          : "模型截距（%）";
    const fmtLogitAsP = (z) => {
      const p = 1 / (1 + Math.exp(-Number(z)));
      return Number.isFinite(p) ? `${(p * 100).toFixed(1)}%` : "—";
    };
    const nTip = oos.holdout_trading_days != null
      ? "执行套全样本入模行；Holdout 只改研究套训/测，不改此数"
      : "全面板完整行（OOS 后重估 β）；OOS 训/测 n 见右侧 KPI";
    const oosIcTip = oos.holdout_trading_days != null
        ? `Holdout ${oos.holdout_trading_days} 日拼样本 Pearson（旧口径）`
        : "拼样本 Pearson（旧口径）";
    const csIcTip = "对齐 Qlib IC：每日截面 Pearson 再对日平均";
    const csRankTip = "对齐 Qlib Rank IC：每日截面 Spearman 再对日平均";

    const metrics =
      `<div class="quant-rem-coef-spec-metrics" role="group" aria-label="${esc(
        isPath ? "path 模型摘要" : isOo ? "oo 模型摘要" : isCo ? "co 模型摘要" : isT90 ? "t90 模型摘要" : isT75 ? "t75 模型摘要" : isT60 ? "t60 模型摘要" : isT45 ? "t45 模型摘要" : isT30 ? "t30 模型摘要" : isR ? "r 模型摘要" : "τ 模型摘要"
      )}">` +
      (intercept != null
        ? kpi(
            useProbKpis ? "基率" : isR ? "执行套" : "截距",
            useProbKpis ? fmtLogitAsP(intercept) : intercept.toFixed(3),
            interceptTip
          )
        : "") +
      (isR &&
      researchIntercept != null &&
      (intercept == null || Math.abs(researchIntercept - intercept) > 1e-4)
        ? kpi(
            useProbKpis ? "研究基率" : "研究套",
            useProbKpis
              ? fmtLogitAsP(researchIntercept)
              : researchIntercept.toFixed(3),
            isHorizon
              ? "Holdout 训练集基率 · 做 T 回测 / 历史验证用"
              : "Holdout 训练集截距 · 做 T 回测 / 历史验证用"
          )
        : "") +
      (nFmt != null ? kpi("样本 n", nFmt, nTip) : "") +
      (oos.holdout_trading_days != null && Number.isFinite(Number(oos.holdout_trading_days))
        ? kpi(
            "Holdout",
            `${Number(oos.holdout_trading_days)}日`,
            "近 N 个交易日不进研究套训练"
          )
        : "") +
      (oos.n_train != null && Number.isFinite(Number(oos.n_train))
        ? kpi("训 n", Number(oos.n_train).toLocaleString("en-US"), "研究套训练集")
        : "") +
      (oos.n_test != null && Number.isFinite(Number(oos.n_test))
        ? kpi("测 n", Number(oos.n_test).toLocaleString("en-US"), "Holdout 样本外")
        : "") +
      (!useProbKpis && r2 != null
        ? kpi("R²", r2.toFixed(3), "全面板样本内拟合优度，非 OOS")
        : "") +
      (lam != null ? kpi("λ", lam, useProbKpis ? "logistic Ridge L2（只罚斜率）" : "Ridge 收缩") : "") +
      (useProbKpis && oos.auc != null && Number.isFinite(Number(oos.auc))
        ? kpi("AUC", Number(oos.auc).toFixed(3), "Holdout ROC-AUC；随机≈0.50，promote≥0.52")
        : "") +
      (useProbKpis && oos.acc_at_50 != null && Number.isFinite(Number(oos.acc_at_50))
        ? kpi(
            "acc@0.5",
            `${(Number(oos.acc_at_50) * 100).toFixed(1)}%`,
            "p_up>0.5 对窗收益>0"
          )
        : "") +
      (useProbKpis && oos.brier != null && Number.isFinite(Number(oos.brier))
        ? kpi("Brier", Number(oos.brier).toFixed(3), "越小越好；0.25≈瞎猜")
        : "") +
      (!useProbKpis && oos.cs_ic != null && Number.isFinite(Number(oos.cs_ic))
        ? kpi("IC", Number(oos.cs_ic).toFixed(3), csIcTip, {
            signed: true,
            raw: oos.cs_ic,
          })
        : "") +
      (!useProbKpis && oos.cs_rank_ic != null && Number.isFinite(Number(oos.cs_rank_ic))
        ? kpi("Rank IC", Number(oos.cs_rank_ic).toFixed(3), csRankTip, {
            signed: true,
            raw: oos.cs_rank_ic,
          })
        : "") +
      (!useProbKpis && oos.cs_icir != null && Number.isFinite(Number(oos.cs_icir))
        ? kpi("ICIR", Number(oos.cs_icir).toFixed(3), "日频 IC 均值/标准差", {
            signed: true,
            raw: oos.cs_icir,
          })
        : "") +
      (!useProbKpis && oos.ic != null && Number.isFinite(Number(oos.ic))
        ? kpi(
            oos.cs_ic != null ? "拼样IC" : "OOS IC",
            Number(oos.ic).toFixed(3),
            oosIcTip,
            { signed: true, raw: oos.ic }
          )
        : "") +
      (!useProbKpis && oos.sign_hit_rate != null && Number.isFinite(Number(oos.sign_hit_rate))
        ? kpi(
            "命中",
            `${(Number(oos.sign_hit_rate) * 100).toFixed(1)}%`,
            "样本外方向命中率"
          )
        : !useProbKpis && oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))
          ? kpi(
              "命中",
              `${(Number(oos.sign_hit) * 100).toFixed(1)}%`,
              "样本外方向命中率"
            )
          : !useProbKpis && oos.median_hit != null && Number.isFinite(Number(oos.median_hit))
            ? kpi(
                "中位命中",
                `${(Number(oos.median_hit) * 100).toFixed(1)}%`,
                "ŷ 与 y 是否同在中位以上；≈50% 即无排序信息"
              )
            : "") +
      kpi(
        "入模",
        `${rows.length}`,
        isPath
          ? `开盘 ${openN} · 分钟 ${minuteN} · 历史 ${histN}`
          : isCo
            ? `路径 ${openN}`
            : `开盘 ${openN} · 分钟 ${minuteN} · 历史 ${histN}`
      ) +
      kpi("β±", `${posN}/${negN}`, "正系数 / 负系数个数") +
      (exclN > 0 ? kpi("省略", exclN, omitTip) : "") +
      `</div>`;

    const byTau = oos.by_tau && typeof oos.by_tau === "object" ? oos.by_tau : null;
    const byTauKeys = byTau
      ? Object.keys(byTau).filter((k) => byTau[k] && Number(byTau[k].n) > 0)
      : [];
    const byTauTip = isHorizon
      ? "p_up>0.5 与 τ 后窗收益同号；标签不含开→τ，晚钟不会被已实现路径垫高"
      : isPath
      ? "极值序标签下 τ 越晚特征更贴标签，命中易虚高；优先分档对照，live 仍用决策钟"
      : "τ→close 标签下 τ 越晚命中通常越高（开→τ 已实现垫高）；看开盘/首根/10:00/11:00，不必逐钟";
    const byTauHit = (t) => {
      const b = (byTau && byTau[t]) || {};
      return b.sign_hit != null && Number.isFinite(Number(b.sign_hit))
        ? Number(b.sign_hit)
        : null;
    };
    const byTauChip = (t) => {
      const b = (byTau && byTau[t]) || {};
      const hitN = byTauHit(t);
      const hit = hitN != null
          ? `${(hitN * 100).toFixed(0)}%`
          : "—";
      const ic =
        b.ic != null && Number.isFinite(Number(b.ic))
          ? Number(b.ic).toFixed(2)
          : "—";
      const live = t === "10:00";
      return (
        `<span class="quant-rem-coef-spec-tick${
          live ? " is-live" : ""
        }" title="${esc(`${t} · n=${b.n ?? "—"} · hit=${hit} · IC=${ic}${live ? " · 调仓截钟" : ""}`)}">` +
        `<span class="quant-rem-coef-spec-tick-k">${esc(t)}</span>` +
        `<span class="quant-rem-coef-spec-tick-v">${esc(hit)}</span>` +
        `</span>`
      );
    };
    const byTauKeysSorted = [...byTauKeys].sort();
    const byTauFullTip =
      byTauTip +
      (byTauKeysSorted.length
        ? " · " +
          byTauKeysSorted
            .map((t) => {
              const h = byTauHit(t);
              if (h == null) return `${t} —`;
              return `${t} ${(h * 100).toFixed(0)}%`;
            })
            .join(" · ")
        : "");
    const firstHit = byTauHit(byTauKeysSorted[0]);
    const lastHit = byTauHit(byTauKeysSorted[byTauKeysSorted.length - 1]);
    const spanLbl =
      firstHit != null && lastHit != null
          ? `${(firstHit * 100).toFixed(0)}→${(lastHit * 100).toFixed(0)}%`
        : "";
    const anchorKeys =
      byTauKeysSorted.length > 5
        ? pickTauAnchorKeys(byTauKeysSorted)
        : byTauKeysSorted;
    const spark =
      byTauKeysSorted.length >= 2
        ? tauHitSparkline(byTauKeysSorted, byTau, anchorKeys)
        : "";
    const yDot = ySpec.indexOf("·");
    const yBase = (yDot >= 0 ? ySpec.slice(0, yDot) : ySpec).trim();
    const yGrid = yDot >= 0 ? ySpec.slice(yDot + 1).trim() : "";
    const specTau =
      !isCo && byTauKeys.length > 1
        ? `<div class="quant-rem-coef-spec-tau" title="${esc(byTauFullTip)}">` +
          `<span class="quant-rem-coef-spec-k">OOS · τ</span>` +
          spark +
          (spanLbl
            ? `<span class="quant-rem-coef-spec-span">${esc(spanLbl)}</span>`
            : "") +
          `<span class="quant-rem-coef-spec-anchors">${anchorKeys
            .map(byTauChip)
            .join("")}</span>` +
          `</div>`
        : "";
    const specRow =
      `<div class="quant-rem-coef-spec" role="group" aria-label="${esc(
        `${yhatTag} 标签与 OOS`
      )}">` +
      `<div class="quant-rem-coef-spec-y" title="${esc(ySpecTip)}">` +
      `<span class="quant-rem-coef-spec-k">y</span>` +
      `<span class="quant-rem-coef-spec-f">${esc(yBase)}</span>` +
      (yGrid ? `<span class="quant-rem-coef-spec-grid">${esc(yGrid)}</span>` : "") +
      `</div>` +
      specTau +
      metrics +
      `</div>`;

    const headLegend =
      `<div class="quant-rem-coef-legend" aria-hidden="true">` +
      `<span class="quant-rem-leg is-pos">正β</span>` +
      `<span class="quant-rem-leg is-neg">负β</span>` +
      (isPath
        ? `<span class="quant-rem-leg is-open">开盘</span>` +
          `<span class="quant-rem-leg is-minute">分钟</span>` +
          `<span class="quant-rem-leg is-eod">历史</span>`
        : isCo
          ? `<span class="quant-rem-leg is-open">路径</span>`
          : `<span class="quant-rem-leg is-open">开盘</span>` +
            `<span class="quant-rem-leg is-minute">分钟</span>` +
            `<span class="quant-rem-leg is-eod">历史</span>`) +
      `</div>`;

    const betaCell = (r) => {
      const pos = r.ols >= 0;
      const half = Math.max(6, (r.abs / maxAbs) * 50);
      const tip = isHorizon
        ? `β=${r.ols.toFixed(4)}：因子高 1σ → logit ${pos ? "+" : ""}${r.ols.toFixed(3)}（p_up 随之升降）`
        : `β=${r.ols.toFixed(4)}：因子高 1σ → ${yhatTag} 约 ${pos ? "+" : ""}${r.ols.toFixed(3)}pp`;
      return (
        `<div class="quant-rem-beta-cell" title="${esc(tip)}">` +
        `<div class="quant-rem-beta-track is-bipolar" aria-hidden="true">` +
        `<span class="quant-rem-coef-bar-zero"></span>` +
        `<div class="quant-rem-coef-bar-fill ${pos ? "is-pos" : "is-neg"}" style="${
          pos
            ? `left:50%;width:${half.toFixed(1)}%`
            : `right:50%;width:${half.toFixed(1)}%`
        }"></div>` +
        `</div>` +
        `<span class="quant-rem-beta-num ${pos ? "is-pos" : "is-neg"}">${
          pos ? "+" : ""
        }${r.ols.toFixed(3)}</span>` +
        `</div>`
      );
    };

    const shareCell = (r) => {
      const w = Math.max(2, Math.min(100, r.share));
      return (
        `<div class="quant-rem-share-cell" title="${esc(
          `|β|占比 ${r.share.toFixed(1)}%`
        )}">` +
        `<div class="quant-rem-share-track" aria-hidden="true">` +
        `<div class="quant-rem-share-fill" style="width:${w.toFixed(1)}%"></div>` +
        `</div>` +
        `<span class="quant-rem-share-num">${r.share.toFixed(1)}%</span>` +
        `</div>`
      );
    };

    const remFactorFallbackTip = (r) => {
      const zh = REM_FEAT_LABELS[r.name];
      const shown =
        r.kind === "历史" && zh ? zh : r.label || r.name || "因子";
      const key = r.name ? `（${r.name}）` : "";
      if (isCo) {
        return `${shown}${key}：T-1 路径 / 开盘 Z，用于估 open[T+1]/close[T]−1。正 β 表示该值偏高时 ŷ_co 更高。`;
      }
      if (r.kind === "分钟") {
        return `${shown}${key}：≤τ 的 5m 路径摘要。正 β 表示该值偏高时 ${yhatTag} 更高。`;
      }
      if (r.kind === "开盘") {
        return `${shown}${key}：开盘/截面特征。正 β 表示该值偏高时 ${yhatTag} 更高。`;
      }
      if (r.kind === "历史") {
        return `${shown}${key}：PIT 历史真实标签（不含当日）。正 β 表示该值偏高时 ${yhatTag} 更高。`;
      }
      return `${shown}${key}：T−1 日线因子。正 β 表示该值偏高时 ${yhatTag} 更高。`;
    };

    const factorCell = (r) =>
      `<span class="quant-rem-factor">` +
      `<span class="quant-rem-rank" title="|β| 排名">${esc(String(r.rank))}</span>` +
      `<span class="quant-rem-factor-main">${factorNameCellHtml(
        r.name,
        r.label,
        remFactorFallbackTip(r)
      )}</span>` +
      `</span>`;

    const coefColumns = [
      { id: "factor", label: "因子", flex: true, title: "悬停因子名查看口径说明" },
      {
        id: "kind",
        label: "类型",
        width: 72,
        center: true,
        title: isPath
          ? "开盘=缺口 Z；分钟=≤τ 前缀小包；历史=PIT 昨样本（不含当日）"
          : isCo
            ? "路径=T 日已实现 + 开盘 Z"
            : "开盘=缺口 Z；分钟=≤τ 的 5m 路径；历史=PIT 昨样本（不含当日）",
      },
      {
        id: "ols",
        label: "β",
        width: 300,
        num: true,
        title: "标准化斜率：右正左负；数值叠在彩条靠右",
      },
      {
        id: "share",
        label: "|β|%",
        width: 200,
        num: true,
        title: "|β| / Σ|β|：相对解释权重",
      },
      {
        id: "mu",
        label: "μ",
        width: 88,
        num: true,
        title: "入模样本内原始特征均值（z-score 前）",
      },
      {
        id: "sd",
        label: "σ",
        width: 88,
        num: true,
        title: "入模样本内原始特征标准差",
      },
    ];

    const coefCellHtml = (col, r) => {
      if (col.id === "factor") return factorCell(r);
      if (col.id === "kind") {
        const chipClass =
          r.kind === "开盘" || r.kind === "路径"
            ? "is-open"
            : r.kind === "分钟"
              ? "is-minute"
              : "is-eod";
        const kindTip = isCo
          ? "T 日路径 / 开盘 Z（ŷ_co 头）"
          : r.kind === "分钟"
            ? "≤τ 的 5m 路径摘要（分钟 τ）"
            : r.kind === "开盘"
              ? "开盘/截面特征"
              : r.kind === "历史"
                ? "PIT 历史真实标签（不含当日）"
                : "T−1 日线因子";
        return (
          `<span class="quant-rem-kind-chip ${chipClass}" title="${esc(kindTip)}">${esc(
            r.kind
          )}</span>`
        );
      }
      if (col.id === "ols") return betaCell(r);
      if (col.id === "share") return shareCell(r);
      if (col.id === "mu") {
        if (r.mu == null) return fmtEmptyCell();
        return cell(esc(r.mu.toFixed(2)), "");
      }
      if (col.id === "sd") {
        if (r.sd == null) return fmtEmptyCell();
        return cell(esc(r.sd.toFixed(2)), "");
      }
      return fmtEmptyCell();
    };

    const coefGridOpts = {
      emptyText: isCo ? "暂无 ŷ_co 入模因子" : "暂无 rem 入模因子",
      rowClass: (r) => {
        const bits = [];
        if (r && r.scanHot) bits.push("is-scan-hot");
        if (r && r.ols < 0) bits.push("is-beta-neg");
        return bits.join(" ");
      },
    };

    const topNRaw = opts.topN != null ? Number(opts.topN) : 10;
    const topN =
      Number.isFinite(topNRaw) && topNRaw > 0
        ? Math.max(1, Math.min(200, Math.floor(topNRaw)))
        : 10;
    const topRows = rows.slice(0, topN);
    const restRows = rows.slice(topN);
    const topGrid = gridHtml(
      coefColumns,
      topRows,
      coefCellHtml,
      coefGridOpts
    );
    const restFold =
      restRows.length > 0
        ? `<details class="quant-rem-coef-more">` +
          `<summary class="quant-rem-coef-more-sum">` +
          `其余 ${restRows.length} 个因子` +
          `<span class="quant-rem-coef-more-hint">按 |β| 续排 · 默认折叠</span>` +
          `</summary>` +
          `<div class="quant-rem-coef-more-body">${gridHtml(
            coefColumns,
            restRows,
            coefCellHtml,
            coefGridOpts
          )}</div>` +
          `</details>`
        : "";

    const head =
      `<div class="quant-rem-coef-head">` +
      `<div class="quant-rem-coef-head-main">` +
      `<span class="quant-rem-coef-title">${yhatTag}${useProbKpis ? " 概率头" : " 系数表"}</span>` +
      `<span class="quant-rem-coef-sub">${
        useProbKpis
          ? `按 |β| 降序 · logit · 默认 Top${topN}${
              restRows.length ? ` · 其余 ${restRows.length} 折叠` : ""
            }`
          : `按 |β| 降序 · 默认 Top${topN}${
              restRows.length ? ` · 其余 ${restRows.length} 折叠` : ""
            }`
      }</span>` +
      `</div>` +
      headLegend +
      `</div>`;

    return (
      `<div class="quant-rem-coef">` +
      head +
      specRow +
      `<div class="quant-rem-coef-frame">${topGrid}${restFold}</div>` +
      `</div>`
    );
  }

  return { factorIcWeightMergedHtml, remCoefTableHtml, fmtEmptyCell, fmtOlsCell };
}
