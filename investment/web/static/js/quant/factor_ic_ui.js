/**
 * 因子 IC + OLS β 合并表 HTML。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { metricClass as defaultMetricClass } from "./bt_result.js";

/**
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 *   metricCell?: typeof defaultMetricCell,
 *   metricClass?: typeof defaultMetricClass,
 *   factorMetaByName: Record<string, object>,
 *   factorMetaByLabel: Record<string, object>,
 *   factorNameCellHtml: (name: string, label?: string) => string,
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
      if (!name) continue;
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
    ]);
    if (!nameSet.size) return "";

    const rows = [...nameSet].sort().map((name) => {
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
      let icReason = base.icReason || (base.ic == null ? icReasonsTop[name] : null) || null;
      if (base.ic == null && !icReason && list.length) icReason = "other";
      const absBeta =
        olsVal != null && Number.isFinite(Number(olsVal))
          ? Math.abs(Number(olsVal))
          : 0;
      return {
        ...base,
        ols: olsVal,
        olsReason,
        icReason,
        scanHot: absBeta >= 0.05,
      };
    });

    return researchGridHtml(
      [
        { id: "factor", label: "因子", flex: true },
        {
          id: "ic",
          label: "IC",
          widthPct: 12,
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
        if (col.id === "factor") return factorNameCellHtml(r.name, r.label);
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

  return { factorIcWeightMergedHtml, fmtEmptyCell, fmtOlsCell };
}
