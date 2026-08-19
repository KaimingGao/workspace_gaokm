/**
 * 因子 IC + OLS β 合并表 HTML。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { metricClass as defaultMetricClass } from "./bt_result.js";
import { classifyFactor, isRemovedFactor } from "./factor_meta.js";

/**
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 *   metricCell?: typeof defaultMetricCell,
 *   metricClass?: typeof defaultMetricClass,
 *   factorMetaByName: Record<string, object>,
 *   factorMetaByLabel: Record<string, object>,
 *   factorNameCellHtml: (name: string, label?: string, fallbackDescription?: string) => string,
 *   factorTaxonomyCellHtml?: (name: string, label?: string, fallbackDescription?: string) => string,
 * }} deps
 */
export function createFactorIcUi(deps) {
  const esc = deps.escapeHtml || defaultEscapeHtml;
  const researchGridHtml = deps.researchGridHtml || defaultResearchGridHtml;
  const metricCell = deps.metricCell || defaultMetricCell;
  const mcls = deps.metricClass || defaultMetricClass;
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

  function fmtOlsCell(v) {
    if (v == null || v === "") return fmtEmptyCell();
    const n = Number(v);
    if (!Number.isFinite(n)) return esc(String(v));
    const text = n.toFixed(4);
    return metricCell(esc(text), mcls(n));
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
        factorMetaByName[name] = {
          ...(factorMetaByName[name] || {}),
          name,
          label: label !== name ? label : meta0.label || label,
          description: r.description,
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

    return researchGridHtml(
      [
        {
          id: "factor",
          label: "因子",
          flex: true,
          flexMin: "13.5rem",
          flexFr: 2.1,
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
            return `<span title="${esc(tip)}">${metricCell(
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
          return `<span title="${esc(tip)}">${metricCell(
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
    ret_open_to_tau: "开盘→τ 收益 %",
    sector_ret_to_tau: "板块中位开→τ %",
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

  /**
   * ŷ_τ rem Ridge 因子系数表：仅入模因子；KPI + 双向 β 图 + 表内条形。
   * @param {object|null} rm return_model 或含 coefficients 的报告块
   * @param {{ oos?: object } } [opts]
   */
  function remCoefTableHtml(rm, opts = {}) {
    if (!rm || typeof rm !== "object") return "";
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
    const remExtra = new Set([
      "gap_pct",
      "open_gap",
      "sector_gap_breadth",
      "theme_day",
      "gap_atr",
      "gap_vs_sector",
      "ret_open_to_tau",
      "sector_ret_to_tau",
    ]);
    const activeList = Array.isArray(rm.active_features)
      ? rm.active_features.map(String)
      : Object.keys(coefs);
    let rows = activeList
      .map((name) => {
        const v = coefs[name];
        if (v == null || !Number.isFinite(Number(v))) return null;
        const ols = Number(v);
        const mu =
          means[name] != null && Number.isFinite(Number(means[name]))
            ? Number(means[name])
            : null;
        const sd =
          stds[name] != null && Number.isFinite(Number(stds[name]))
            ? Number(stds[name])
            : null;
        return {
          name,
          label: remFactorDisplayLabel(name),
          ols,
          abs: Math.abs(ols),
          kind: remExtra.has(name) ? "开盘" : "日线",
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
      return `<p class="sub">暂无 rem 入模因子</p>`;
    }

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
    const exclN = Object.keys(rm.exclusion_reasons || {}).length;
    const ySpec = (rm.y_spec && rm.y_spec.formula) || "close[T]/open[T]-1";
    const oos = opts.oos || {};
    const openN = rows.filter((r) => r.kind === "开盘").length;
    const posN = rows.filter((r) => r.ols >= 0).length;
    const negN = rows.length - posN;

    const kpi = (label, value, tip) =>
      `<span class="quant-rem-coef-kpi" title="${esc(tip || label)}">` +
      `<span class="quant-rem-coef-kpi-k">${esc(label)}</span>` +
      `<span class="quant-rem-coef-kpi-v">${esc(String(value))}</span>` +
      `</span>`;

    const nFmt =
      n != null && Number.isFinite(Number(n))
        ? Number(n).toLocaleString("en-US")
        : null;

    const kpis =
      `<div class="quant-rem-coef-kpis" role="group" aria-label="rem 模型摘要">` +
      kpi("标签", ySpec, "ŷ_τ 训练标签") +
      (intercept != null ? kpi("截距", intercept.toFixed(3), "模型截距（%）") : "") +
      (nFmt != null ? kpi("样本 n", nFmt, "入模观测数") : "") +
      (r2 != null ? kpi("R²", r2.toFixed(3), "样本内拟合优度") : "") +
      (lam != null ? kpi("λ", lam, "Ridge 收缩") : "") +
      (oos.ic != null && Number.isFinite(Number(oos.ic))
        ? kpi("OOS IC", Number(oos.ic).toFixed(3), "时间切分样本外 IC")
        : "") +
      (oos.sign_hit != null && Number.isFinite(Number(oos.sign_hit))
        ? kpi(
            "命中",
            `${(Number(oos.sign_hit) * 100).toFixed(1)}%`,
            "样本外方向命中率"
          )
        : "") +
      kpi("入模", `${rows.length}`, `开盘 ${openN} · 日线 ${rows.length - openN}`) +
      kpi("β±", `${posN}/${negN}`, "正系数 / 负系数个数") +
      (exclN > 0 ? kpi("省略", exclN, "未入模因子已隐藏") : "") +
      `</div>`;

    const head =
      `<div class="quant-rem-coef-head">` +
      `<div class="quant-rem-coef-head-main">` +
      `<span class="quant-rem-coef-title">系数表</span>` +
      `<span class="quant-rem-coef-sub">按 |β| 降序 · 标准化斜率</span>` +
      `</div>` +
      `<div class="quant-rem-coef-legend" aria-hidden="true">` +
      `<span class="quant-rem-leg is-pos">正β</span>` +
      `<span class="quant-rem-leg is-neg">负β</span>` +
      `<span class="quant-rem-leg is-open">开盘</span>` +
      `<span class="quant-rem-leg is-eod">日线</span>` +
      `</div>` +
      `</div>`;

    const betaCell = (r) => {
      const pos = r.ols >= 0;
      const half = Math.max(6, (r.abs / maxAbs) * 50);
      const tip = `β=${r.ols.toFixed(4)}：因子高 1σ → ŷ_τ 约 ${pos ? "+" : ""}${r.ols.toFixed(3)}pp`;
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
      const shown = r.label || r.name || "因子";
      const key = r.name ? `（${r.name}）` : "";
      if (r.kind === "开盘") {
        return `${shown}${key}：ŷ_τ 开盘/截面特征。正 β 表示该值偏高时 ŷ_τ 更高。`;
      }
      return `${shown}${key}：T−1 日线因子，ŷ_τ Ridge 入模。正 β 表示该值偏高时 ŷ_τ 更高。`;
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

    const grid = researchGridHtml(
      [
        { id: "factor", label: "因子", flex: true, title: "悬停因子名查看口径说明" },
        {
          id: "kind",
          label: "类型",
          width: 72,
          center: true,
          title: "日线=T−1 因子；开盘=缺口/广度等 τ 特征",
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
      ],
      rows,
      (col, r) => {
        if (col.id === "factor") return factorCell(r);
        if (col.id === "kind") {
          const open = r.kind === "开盘";
          return (
            `<span class="quant-rem-kind-chip ${open ? "is-open" : "is-eod"}" title="${esc(
              open ? "开盘/截面特征（τ 特有）" : "T−1 日线因子"
            )}">${esc(r.kind)}</span>`
          );
        }
        if (col.id === "ols") return betaCell(r);
        if (col.id === "share") return shareCell(r);
        if (col.id === "mu") {
          if (r.mu == null) return fmtEmptyCell();
          return metricCell(esc(r.mu.toFixed(2)), "");
        }
        if (col.id === "sd") {
          if (r.sd == null) return fmtEmptyCell();
          return metricCell(esc(r.sd.toFixed(2)), "");
        }
        return fmtEmptyCell();
      },
      {
        emptyText: "暂无 rem 入模因子",
        rowClass: (r) => {
          const bits = [];
          if (r && r.scanHot) bits.push("is-scan-hot");
          if (r && r.ols < 0) bits.push("is-beta-neg");
          return bits.join(" ");
        },
      }
    );

    return (
      `<div class="quant-rem-coef">` +
      head +
      kpis +
      `<div class="quant-rem-coef-frame">${grid}</div>` +
      `</div>`
    );
  }

  return { factorIcWeightMergedHtml, remCoefTableHtml, fmtEmptyCell, fmtOlsCell };
}
