/**
 * OLS / β 分组 / 探针对照 HTML 渲染。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";
import { researchGridHtml as defaultResearchGridHtml, metricCell as defaultMetricCell } from "./research_grid.js";
import { metricClass as defaultMetricClass } from "./bt_result.js";
import { normalizeProbeCode as defaultNormalizeProbeCode } from "./names.js";
import { probeStatusBadge } from "./probe_ui.js";
import {
  buildGroupYhatHistHtml,
  buildBetaLollipopHtml,
  buildBetaDistanceHtml,
  buildGroupIcInsightHtml,
  buildClustersOosDeltaHtml,
} from "./yhat_viz.js";

/**
 * @param {{
 *   escapeHtml?: typeof defaultEscapeHtml,
 *   researchGridHtml?: typeof defaultResearchGridHtml,
 *   metricCell?: typeof defaultMetricCell,
 *   metricClass?: typeof defaultMetricClass,
 *   normalizeProbeCode?: typeof defaultNormalizeProbeCode,
 *   factorNameCellHtml: (name: string, label?: string) => string,
 *   factorMetaByName: Record<string, object>,
 *   factorIcWeightMergedHtml: Function,
 *   watchingNameByCode?: Record<string, string>,
 *   getWatchingNameByCode?: () => Record<string, string>,
 * }} deps
 */
export function createOlsUi(deps) {
  const esc = deps.escapeHtml || defaultEscapeHtml;
  const researchGridHtml = deps.researchGridHtml || defaultResearchGridHtml;
  const metricCell = deps.metricCell || defaultMetricCell;
  const mcls = deps.metricClass || defaultMetricClass;
  const normalizeProbeCode = deps.normalizeProbeCode || defaultNormalizeProbeCode;
  const factorNameCellHtml = deps.factorNameCellHtml;
  const factorMetaByName = deps.factorMetaByName;
  const factorIcWeightMergedHtml = deps.factorIcWeightMergedHtml;
  const getWatchingNameByCodeRaw =
    typeof deps.getWatchingNameByCode === "function"
      ? deps.getWatchingNameByCode
      : () => deps.watchingNameByCode || {};

  function getWatchingNameByCode() {
    const m = getWatchingNameByCodeRaw();
    return m && typeof m === "object" ? m : {};
  }

  function formatStockCodeName(code, name) {
    const c = normalizeProbeCode(code);
    const n = String(name || "").trim().replace(/\s+/g, "");
    if (n && c) return `${n} ${c}`;
    return n || c || "—";
  }

  function clusterNameByCodeFromData(data) {
    const watchingNameByCode = getWatchingNameByCode();
    const nameByCode = { ...watchingNameByCode };
    const fromReport =
      (data && data.name_by_code) ||
      (data && data.stock_names) ||
      {};
    for (const [raw, nm0] of Object.entries(fromReport)) {
      const c = normalizeProbeCode(raw);
      const nm = String(nm0 || "").trim();
      if (c && nm) nameByCode[c] = nm;
    }
    const ha =
      (data && data.holdings_assignment) ||
      (data && data.pool_artifact && data.pool_artifact.holdings_assignment) ||
      {};
    for (const g of ha.groups || []) {
      for (const h of g.holdings || []) {
        const c = normalizeProbeCode(h.stock_code);
        const nm = String(h.stock_name || "").trim();
        if (c && nm) nameByCode[c] = nm;
      }
    }
    for (const h of ha.unmapped || []) {
      const c = normalizeProbeCode(h.stock_code);
      const nm = String(h.stock_name || "").trim();
      if (c && nm) nameByCode[c] = nm;
    }
    for (const cl of (data && data.clusters) || []) {
      for (const r of (cl.group_ranking || cl.ranking || []) || []) {
        const c = normalizeProbeCode(r.stock_code || r.code);
        const nm = String(r.stock_name || "").trim();
        if (c && nm) nameByCode[c] = nm;
      }
    }
    for (const [c, nm] of Object.entries(nameByCode)) {
      if (c && nm && !watchingNameByCode[c]) watchingNameByCode[c] = nm;
    }
    return nameByCode;
  }

  function formatMemberList(codes, nameByCode) {
    const watchingNameByCode = getWatchingNameByCode();
    const map = nameByCode || {};
    const list = (codes || []).map((m) => {
      const c = normalizeProbeCode(m);
      return formatStockCodeName(c, map[c] || watchingNameByCode[c] || "");
    });
    return list.length ? list.join("、") : "—";
  }

  function formatMemberChipsHtml(codes, nameByCode) {
    const watchingNameByCode = getWatchingNameByCode();
    const map = nameByCode || {};
    const chips = (codes || []).map((m) => {
      const c = normalizeProbeCode(m);
      const n = String(map[c] || watchingNameByCode[c] || "")
        .trim()
        .replace(/\s+/g, "");
      if (n && c && n !== c) {
        return (
          `<span class="quant-cluster-member">` +
          `<span class="quant-cluster-member-name">${esc(n)}</span>` +
          `<span class="quant-cluster-member-code">${esc(c)}</span>` +
          `</span>`
        );
      }
      return (
        `<span class="quant-cluster-member">` +
        `<span class="quant-cluster-member-name">${esc(n || c || "—")}</span>` +
        `</span>`
      );
    });
    return chips.length ? chips.join("") : `<span class="sub">—</span>`;
  }

  function parseOosGateReason(gate) {
    const reasonRaw = String((gate && gate.reason) || "").trim();
    const reasonMap = {
      oos_not_worse: "研究臂不劣于基线（容差内）",
      insufficient_oos: "OOS 收益不足，无法判定",
      backtest_failed: "Top-K 回测失败",
      watching_too_small: "有效标的不足",
      bars_too_few: "有效日线不足",
      no_return_model: "无组内 return_model（ŷ）",
      no_baseline_weights: "无全局人工权（heuristic 基线）",
      cluster_too_small: "组成员不足",
      gate_disabled: "未跑 OOS 门禁",
      research_oos_failed: "研究臂自身 OOS 失败旗标",
      no_weight_suggest: "无组内建议权（遗留）",
    };
    if (/^oos_worse_/.test(reasonRaw)) {
      return `研究臂差于基线超过容差（${reasonRaw.replace("oos_worse_", "")}）`;
    }
    return reasonMap[reasonRaw] || reasonRaw || "—";
  }

  function oosGateStatusMeta(gate) {
    if (!gate || typeof gate !== "object") {
      return { key: "none", label: "无记录", kind: "muted" };
    }
    if (gate.skipped) return { key: "skip", label: "已跳过", kind: "muted" };
    if (gate.ok && gate.passed) return { key: "pass", label: "通过", kind: "ok" };
    if (gate.ok) return { key: "fail", label: "未通过", kind: "warn" };
    return { key: "unknown", label: "未判定", kind: "muted" };
  }

  function oosGateTipHtml(gate) {
    const st = oosGateStatusMeta(gate);
    const reason = parseOosGateReason(gate);
    const baseOos =
      (gate && gate.baseline && gate.baseline.oos) ||
      (gate && gate.current && gate.current.oos) ||
      {};
    const resOos =
      (gate && gate.research && gate.research.oos) ||
      (gate && gate.suggested && gate.suggested.oos) ||
      {};
    const delta =
      gate && gate.delta_oos_pp != null && gate.delta_oos_pp !== ""
        ? Number(gate.delta_oos_pp)
        : null;
    const tol =
      gate && gate.oos_tol_pp != null && gate.oos_tol_pp !== ""
        ? gate.oos_tol_pp
        : "1";
    const deltaCls =
      delta == null || !Number.isFinite(delta)
        ? ""
        : delta >= 0
          ? "is-pos"
          : "is-neg";
    const deltaText =
      delta == null || !Number.isFinite(delta)
        ? "—"
        : `${delta > 0 ? "+" : ""}${delta}pp`;
    const metrics = [
      ["ΔOOS", deltaText, deltaCls],
      ["容差", `±${tol}pp`, ""],
      [
        "基线 OOS",
        baseOos.oos_return_pct != null ? `${baseOos.oos_return_pct}%` : "—",
        "",
      ],
      [
        "研究 OOS",
        resOos.oos_return_pct != null ? `${resOos.oos_return_pct}%` : "—",
        "",
      ],
    ]
      .map(
        ([k, v, cls]) =>
          `<div class="oos-gate-tip-metric">` +
          `<dt>${esc(k)}</dt>` +
          `<dd class="${esc(cls)}">${esc(String(v))}</dd>` +
          `</div>`
      )
      .join("");
    const flag =
      resOos.failed && resOos.fail_reason
        ? `<p class="oos-gate-tip-flag">研究臂旗标 · ${esc(
            String(resOos.fail_reason)
          )}</p>`
        : "";
    return (
      `<div class="oos-gate-tip-inner">` +
      `<header class="oos-gate-tip-head">` +
      `<span class="oos-gate-tip-status is-${esc(st.kind)}">${esc(
        st.label
      )}</span>` +
      `<span class="oos-gate-tip-eyebrow">OOS · heuristic → ŷ</span>` +
      `</header>` +
      `<p class="oos-gate-tip-reason">${esc(reason)}</p>` +
      `<dl class="oos-gate-tip-metrics">${metrics}</dl>` +
      flag +
      `<p class="oos-gate-tip-foot">过门 ≠ 自动 promote · 仅研究对照</p>` +
      `</div>`
    );
  }

  function clusterTagHtml(text, kind, title, gate) {
    const k = kind ? ` is-${kind}` : "";
    const attrs = [];
    if (gate && typeof gate === "object" && Object.keys(gate).length) {
      attrs.push(`data-oos-gate="${esc(JSON.stringify(gate))}"`);
      attrs.push(`class="quant-cluster-tag${k} has-oos-tip"`);
    } else {
      attrs.push(`class="quant-cluster-tag${k}"`);
      if (title != null && String(title).trim() !== "") {
        attrs.push(`title="${esc(String(title))}"`);
      }
    }
    return `<span ${attrs.join(" ")}>${esc(String(text))}</span>`;
  }

  function clusterMetricHtml(key, value) {
    if (value == null || value === "" || value === "—") return "";
    return (
      `<span class="quant-cluster-metric">` +
      `<span class="quant-cluster-metric-k">${esc(String(key))}</span>` +
      `<span class="quant-cluster-metric-v">${esc(String(value))}</span>` +
      `</span>`
    );
  }

  function clusterGroupSuggestShim(cl) {
    const sug = (cl && cl.weight_suggest) || {};
    const diff = (cl && cl.config_diff) || {};
    const rm = (cl && cl.return_model) || {};
    const ols = (cl && cl.ols) || {};
    const coefs =
      (rm.coefficients && typeof rm.coefficients === "object"
        ? rm.coefficients
        : null) ||
      (sug.coefficients && typeof sug.coefficients === "object"
        ? sug.coefficients
        : null) ||
      (ols.coefficients && typeof ols.coefficients === "object"
        ? ols.coefficients
        : null) ||
      {};
    const suggested =
      sug.suggested_weights || diff.suggested_weights || null;
    const hasCoefs = Object.keys(coefs).length > 0;
    if ((!suggested || typeof suggested !== "object") && !hasCoefs) {
      return {
        success: false,
        error: sug.error || "无因子系数",
        deprecated_for_scoring: true,
      };
    }
    const current = sug.current_weights || diff.current_weights || {};
    let deltas = sug.deltas || diff.deltas || null;
    if ((!deltas || typeof deltas !== "object") && suggested) {
      deltas = {};
      const keys = new Set([
        ...Object.keys(current || {}),
        ...Object.keys(suggested),
      ]);
      for (const k of keys) {
        const a = Number(current[k]);
        const b = Number(suggested[k]);
        if (Number.isFinite(a) || Number.isFinite(b)) {
          deltas[k] = (Number.isFinite(b) ? b : 0) - (Number.isFinite(a) ? a : 0);
        }
      }
    }
    return {
      success: sug.success !== false || hasCoefs,
      suggested_weights: suggested || {},
      current_weights: current,
      deltas: deltas || {},
      delta_sources: sug.delta_sources || diff.delta_sources || {},
      coefficients: coefs,
      rationale: sug.rationale || diff.rationale || [],
      params: sug.params || diff.params || {},
      ic_mode: sug.ic_mode || diff.ic_mode || "factor_coefs",
      note: sug.note || diff.note || "",
      deprecated_for_scoring: true,
      error: sug.error,
    };
  }

  function clusterGroupOlsShim(cl) {
    const ols = (cl && cl.ols) || null;
    const rm = (cl && cl.return_model) || null;
    if (ols && typeof ols === "object") {
      if (ols.success === false && !ols.coefficients) {
        // fall through to return_model
      } else {
        return ols;
      }
    }
    if (rm && typeof rm === "object" && rm.coefficients) {
      return {
        success: true,
        coefficients: rm.coefficients,
        intercept: rm.intercept,
        sample_count: rm.sample_count,
        r_squared: rm.r_squared,
      };
    }
    return null;
  }

  function clusterTightTopLines(cl) {
    const tightBits = [];
    if (cl && cl.max_within_dist != null) {
      tightBits.push(
        `直径=${cl.max_within_dist}` +
          (cl.within_dist_cap != null ? `≤τ${cl.within_dist_cap}` : "")
      );
    }
    if (cl && cl.mean_distance_to_group_beta != null) {
      tightBits.push(
        `均Δ组β=${cl.mean_distance_to_group_beta}` +
          (cl.max_distance_to_group_beta != null
            ? `(最大${cl.max_distance_to_group_beta})`
            : "")
      );
    } else if (cl && cl.mean_center_dist != null) {
      tightBits.push(`均心距=${cl.mean_center_dist}`);
    }
    const top = ((cl && cl.top_betas) || [])
      .slice(0, 3)
      .map(
        (t) =>
          `${t.factor}:${Number(t.beta) >= 0 ? "+" : ""}${t.beta}`
      )
      .join(" · ");
    return {
      tight: tightBits.join(" · "),
      top: top ? `|β|：${top}` : "",
    };
  }

  /**
   * 单组因子表 body（展开时再算，避免 15 组一次 innerHTML 卡死主线程）。
   * @param {object} cl
   * @param {{ lastFactorPanelForMerge?: object|null }} [merge]
   */
  function buildClusterGroupBodyHtml(cl, merge = {}) {
    const lastFactorPanelForMerge = merge.lastFactorPanelForMerge || null;
    const sug = clusterGroupSuggestShim(cl);
    const ols = clusterGroupOlsShim(cl);
    const panel =
      (cl.factor_ic_panel && cl.factor_ic_panel.success && cl.factor_ic_panel) ||
      lastFactorPanelForMerge;
    const table =
      factorIcWeightMergedHtml(
        panel,
        sug && sug.success ? sug : null,
        ols
      ) ||
      `<p class="watching-table-empty">${esc(
        (sug && sug.error) || "本组暂无因子系数"
      )}</p>`;
    const { tight, top } = clusterTightTopLines(cl);
    const coll = cl.trend_collinearity || {};
    const pairs = Array.isArray(coll.high_corr_pairs) ? coll.high_corr_pairs : [];
    const olsMeta = cl.ols || {};
    const droppedPol = (olsMeta.sample_fingerprint || {}).dropped || {};
    const droppedCols = droppedPol.collinear_policy || [];
    const collLine = droppedCols.length
      ? `共线进模剔除：${droppedCols.slice(0, 4).join("、")}（${
          olsMeta.collinearity_policy || "drop_redundant"
        }）`
      : pairs.length
        ? `趋势族高相关：${pairs
            .slice(0, 3)
            .map((p) => `${p.a}↔${p.b}(${p.corr})`)
            .join(" · ")}`
        : coll.note
          ? String(coll.note)
          : "";
    const ridgeBit =
      olsMeta.ridge_lambda != null && Number(olsMeta.ridge_lambda) > 0
        ? `Ridge λ=${olsMeta.ridge_lambda}`
        : "";
    const diag = [tight, top, collLine, ridgeBit].filter(Boolean).join(" · ");
    const diagHtml = diag
      ? `<details class="quant-cluster-diag-fold">` +
        `<summary>紧度 / |β| / 共线 / λ</summary>` +
        `<div class="quant-cluster-diag">${esc(diag)}</div>` +
        `</details>`
      : "";
    return diagHtml + table;
  }

  /**
   * @param {object} data - quantLastOlsClusters
   * @param {{ lastFactorPanelForMerge?: object|null, lazyTables?: boolean }} [merge]
   * @returns {string|null} HTML or null if not applicable
   */
  function buildClusterFactorTablesHtml(data, merge = {}) {
    if (!data || !data.success) return null;
    const clusters = Array.isArray(data.clusters) ? data.clusters : [];
    if (!clusters.length) return null;
    const pref = data.preferred_cluster || null;
    const nameByCode = clusterNameByCodeFromData(data);
    const lazy = merge.lazyTables !== false;
    const parts = clusters.map((cl, idx) => {
      const label = cl.label || `G${(cl.cluster_id ?? idx) + 1}`;
      const isPref =
        pref &&
        (pref.cluster_id === cl.cluster_id ||
          (pref.label && pref.label === cl.label));
      const ols = clusterGroupOlsShim(cl);
      const canExport = cl.config_diff && cl.config_diff.success;
      const exportBtn = canExport
        ? `<button type="button" class="dialog-btn secondary dialog-btn-keep-case quant-cluster-export" ` +
          `data-cluster-export="${esc(
            String(idx)
          )}" title="导出本组因子系数对照（遗留 diff）">导出本组</button>`
        : "";
      const gate = cl.oos_gate || {};
      const tags = [];
      tags.push(clusterTagHtml(`${cl.member_count ?? 0} 只`));
      if (cl.outlier_singleton) tags.push(clusterTagHtml("离群单票", "warn"));
      else if (cl.singleton) tags.push(clusterTagHtml("单票", "muted"));
      if (isPref) tags.push(clusterTagHtml("导出优先", "accent"));
      if (gate.skipped) {
        tags.push(clusterTagHtml("OOS跳过", "muted", null, gate));
      } else if (gate.ok && gate.passed) {
        tags.push(clusterTagHtml("OOS✓", "ok", null, gate));
      } else if (gate.ok) {
        tags.push(clusterTagHtml("OOS未过", "warn", null, gate));
      } else if (gate && (gate.reason || gate.note)) {
        tags.push(clusterTagHtml("OOS—", "muted", null, gate));
      }
      const metrics =
        clusterMetricHtml("R²", ols && ols.r_squared != null ? ols.r_squared : "—") +
        clusterMetricHtml(
          "n",
          ols && ols.sample_count != null ? ols.sample_count : "—"
        );
      const membersHtml = formatMemberChipsHtml(cl.members, nameByCode);
      let groupScores = (cl.group_ranking || cl.ranking || cl.predicted_ranking || [])
        .map((r) => Number(r.predicted_score != null ? r.predicted_score : r.score))
        .filter((n) => Number.isFinite(n));
      if (!groupScores.length) {
        const bookRows =
          (data.pool_merge &&
            data.pool_merge.book &&
            (data.pool_merge.book.book || data.pool_merge.book)) ||
          [];
        const memberSet = new Set(
          (cl.members || []).map((m) => normalizeProbeCode(m)).filter(Boolean)
        );
        if (Array.isArray(bookRows) && memberSet.size) {
          groupScores = bookRows
            .filter((r) => memberSet.has(normalizeProbeCode(r.stock_code || r.code)))
            .map((r) =>
              Number(r.predicted_score != null ? r.predicted_score : r.score)
            )
            .filter((n) => Number.isFinite(n));
        }
      }
      const stripHtml = buildGroupYhatHistHtml(groupScores, { escapeHtml: esc, bins: 10 });
      const olsShim = clusterGroupOlsShim(cl);
      const coefs =
        (olsShim && olsShim.coefficients) ||
        ((cl.return_model || {}).coefficients) ||
        {};
      const betaHtml = buildBetaLollipopHtml(coefs, {
        escapeHtml: esc,
        maxRows: 8,
        highlightAbs: 0.05,
      });
      const distHtml = buildBetaDistanceHtml(cl.member_beta_gaps || [], {
        escapeHtml: esc,
        nameByCode,
        maxRows: 10,
      });
      const icHtml = buildGroupIcInsightHtml(cl.factor_ic_panel || null, {
        escapeHtml: esc,
      });
      const insightRow =
        distHtml || icHtml
          ? `<div class="yhat-insight-row">${distHtml || ""}${icHtml || ""}</div>`
          : "";
      const bodyInner = lazy
        ? `<p class="sub quant-cluster-lazy-ph">展开查看因子表…</p>`
        : buildClusterGroupBodyHtml(cl, merge);
      const bodyAttr = lazy ? ` data-cluster-lazy="${esc(String(idx))}"` : "";
      return (
        `<article class="quant-cluster-group-table">` +
        `<details class="quant-fold quant-cluster-group-fold">` +
        `<summary class="quant-cluster-group-head">` +
        `<div class="quant-cluster-group-title-row">` +
        `<div class="quant-cluster-group-title">` +
        `<span class="quant-cluster-gid">${esc(String(label))}</span>` +
        `<span class="quant-cluster-tags">${tags.join("")}</span>` +
        `<span class="quant-cluster-metrics">${metrics}</span>` +
        `</div>` +
        (exportBtn
          ? `<div class="quant-cluster-group-actions">${exportBtn}</div>`
          : "") +
        `</div>` +
        `<div class="quant-cluster-members" aria-label="分组成员">${membersHtml}</div>` +
        insightRow +
        (betaHtml || "") +
        (stripHtml || "") +
        `</summary>` +
        `<div class="quant-cluster-group-body"${bodyAttr}>` +
        bodyInner +
        `</div>` +
        `</details>` +
        `</article>`
      );
    });
    return (
      `<div class="quant-cluster-tables-head">` +
      `<span class="quant-cluster-tables-label">因子系数</span>` +
      `<span class="sub">同质 β距 · 有效 IC · 增益 ΔOOS · β 条 · 辅截面 ŷ · 展开组加载</span>` +
      `</div>` +
      buildClustersOosDeltaHtml(clusters, { escapeHtml: esc }) +
      parts.join("")
    );
  }

  function probeFactorRowsFromExp(exp) {
    const a = exp && Array.isArray(exp.factors) ? exp.factors : [];
    if (a.length) return a;
    const b =
      exp && exp.panel && Array.isArray(exp.panel.rows) ? exp.panel.rows : [];
    return b;
  }

  function probeIcFieldsFromRow(r) {
    if (!r || typeof r !== "object") {
      return { ic: null, icir: null, n: null };
    }
    const pear = r.pearson && typeof r.pearson === "object" ? r.pearson : null;
    const ic =
      r.ic != null
        ? Number(r.ic)
        : r.ic_mean != null
          ? Number(r.ic_mean)
          : pear && pear.ic_mean != null
            ? Number(pear.ic_mean)
            : null;
    const icir =
      r.icir != null
        ? Number(r.icir)
        : pear && pear.icir != null
          ? Number(pear.icir)
          : null;
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
    const n = nRaw != null ? Number(nRaw) : null;
    return {
      ic: Number.isFinite(ic) ? ic : null,
      icir: Number.isFinite(icir) ? icir : null,
      n: n != null && Number.isFinite(n) ? n : null,
    };
  }

  function probeIcMapFromExperiment(exp) {
    const out = {};
    for (const f of probeFactorRowsFromExp(exp)) {
      const name = f.factor || f.name || "";
      if (!name) continue;
      out[name] = probeIcFieldsFromRow(f);
    }
    return out;
  }

  function probeIcMapFromGroupPanel(panel) {
    const out = {};
    const rows =
      (panel && Array.isArray(panel.rows) && panel.rows.length
        ? panel.rows
        : panel && Array.isArray(panel.factors)
          ? panel.factors
          : []) || [];
    for (const r of rows) {
      const name = r.factor || r.name || "";
      if (!name) continue;
      out[name] = probeIcFieldsFromRow(r);
    }
    return out;
  }

  function isProbeSingletonCluster(cl) {
    if (!cl) return false;
    const nMem = Number(cl.member_count);
    return (
      !!cl.singleton ||
      !!cl.outlier_singleton ||
      (Number.isFinite(nMem) && nMem < 2)
    );
  }

  function renderProbeStockVsGroupTable(stockExp, stockOls, cluster) {
    const singleton = isProbeSingletonCluster(cluster);
    const groupIc = probeIcMapFromGroupPanel(
      cluster && cluster.factor_ic_panel
    );
    const stockIc = singleton
      ? groupIc
      : probeIcMapFromExperiment(stockExp);
    const groupCoefs =
      (cluster && cluster.ols && cluster.ols.coefficients) || {};
    const stockCoefs = singleton
      ? groupCoefs
      : (stockOls && stockOls.success && stockOls.coefficients) || {};
    const names = new Set([
      ...Object.keys(stockIc),
      ...Object.keys(groupIc),
      ...Object.keys(stockCoefs).filter(
        (k) => !["intercept", "_intercept", "const"].includes(k)
      ),
      ...Object.keys(groupCoefs).filter(
        (k) => !["intercept", "_intercept", "const"].includes(k)
      ),
    ]);
    const rows = [...names].sort().map((name) => {
      const meta = factorMetaByName[name] || {};
      const sIc = stockIc[name] || {};
      const gIc = groupIc[name] || {};
      const sB =
        stockCoefs[name] != null && Number.isFinite(Number(stockCoefs[name]))
          ? Number(stockCoefs[name])
          : null;
      const gB =
        groupCoefs[name] != null && Number.isFinite(Number(groupCoefs[name]))
          ? Number(groupCoefs[name])
          : null;
      const dIc =
        sIc.ic != null && gIc.ic != null ? sIc.ic - gIc.ic : null;
      const dB = sB != null && gB != null ? sB - gB : null;
      return {
        name,
        label: meta.label || name,
        stockIc: sIc.ic,
        groupIc: gIc.ic,
        dIc,
        stockIcir: sIc.icir,
        groupIcir: gIc.icir,
        stockB: sB,
        groupB: gB,
        dB,
        stockN: sIc.n,
        groupN: gIc.n,
        stockTsIc: !singleton,
        flag: singleton
          ? false
          : (dIc != null && Math.abs(dIc) >= 0.08) ||
            (dB != null && Math.abs(dB) >= 0.25),
      };
    });
    const fmtN = (v, digits) =>
      v == null || !Number.isFinite(Number(v))
        ? "—"
        : Number(v).toFixed(digits);
    const groupCs =
      !!(cluster && cluster.factor_ic_panel && cluster.factor_ic_panel.mode === "group_cs_ic");
    const html = researchGridHtml(
      [
        { id: "factor", label: "因子", flex: true },
        { id: "stockIc", label: "单票IC", widthPct: 9, num: true },
        { id: "groupIc", label: "组IC", widthPct: 9, num: true },
        { id: "dIc", label: "ΔIC", widthPct: 8, num: true },
        {
          id: "groupIcir",
          label: "组ICIR",
          widthPct: 9,
          num: true,
          title: groupCs
            ? "组内日截面 IC 的 ICIR；单票时序 IC 无此项"
            : "本组无日截面 IC 序列时不适用",
        },
        { id: "stockB", label: "单票β", widthPct: 9, num: true },
        { id: "groupB", label: "组β", widthPct: 9, num: true },
        { id: "dB", label: "Δβ", widthPct: 8, num: true },
        {
          id: "n",
          label: "n",
          widthPct: 10,
          num: true,
          title: "单票时序样本数 / 组内截面有效日数",
        },
      ],
      rows,
      (col, r) => {
        if (col.id === "factor") {
          const base = factorNameCellHtml(r.name, r.label);
          return r.flag
            ? `${base} <span class="quant-factor-reason" title="与所在组差异偏大">异质</span>`
            : base;
        }
        if (col.id === "stockIc") return fmtN(r.stockIc, 4);
        if (col.id === "groupIc") return fmtN(r.groupIc, 4);
        if (col.id === "dIc") {
          if (r.dIc == null) return "—";
          const t = `${r.dIc > 0 ? "+" : ""}${r.dIc.toFixed(4)}`;
          return metricCell(esc(t), mcls(r.dIc));
        }
        if (col.id === "groupIcir") {
          if (r.groupIcir != null && Number.isFinite(Number(r.groupIcir))) {
            return fmtN(r.groupIcir, 2);
          }
          if (!groupCs) {
            return `<span class="quant-factor-reason" title="非组内截面 IC">不适用</span>`;
          }
          return "—";
        }
        if (col.id === "stockB") return fmtN(r.stockB, 4);
        if (col.id === "groupB") return fmtN(r.groupB, 4);
        if (col.id === "dB") {
          if (r.dB == null) return "—";
          const t = `${r.dB > 0 ? "+" : ""}${r.dB.toFixed(4)}`;
          return metricCell(esc(t), mcls(r.dB));
        }
        if (col.id === "n") {
          const a = r.stockN != null ? String(r.stockN) : "—";
          const b = r.groupN != null ? String(r.groupN) : "—";
          return esc(`${a}/${b}`);
        }
        return "—";
      },
      {
        emptyText: "无对照因子",
        rowClass: (r) => (r && r.flag ? "is-scan-hot is-hetero" : ""),
      }
    );
    return { html, rows };
  }

  /** β 分组摘要区 HTML（不含落地卡异步内容） */
  function buildOlsClustersSummaryHtml(data) {
    if (!data || !data.success) {
      return {
        ok: false,
        html: `<span class="sub">${esc(
          (data && data.error) || "β 分组失败"
        )}</span>`,
      };
    }
    const clusters = Array.isArray(data.clusters) ? data.clusters : [];
    const nWatchAll = (data.watching_codes || []).length;
    const nUni =
      data.universe_count != null
        ? data.universe_count
        : data.stock_count != null
          ? data.stock_count
          : "—";
    const nClustered = data.stock_count != null ? data.stock_count : "—";
    const nFitted =
      data.fitted_count != null ? data.fitted_count : nClustered;
    const outliers = Array.isArray(data.beta_outliers) ? data.beta_outliers : [];
    const nOutlier =
      data.outlier_count != null ? data.outlier_count : outliers.length;
    const nDataSkip = Array.isArray(data.skipped)
      ? data.skipped.filter((s) => {
          const r = String((s && s.reason) || "");
          return r && r.indexOf("β离群") < 0;
        }).length
      : 0;
    const nGroups = data.n_clusters != null ? data.n_clusters : clusters.length || "—";
    const methodBits = [];
    if (data.cluster_method === "kmeans") methodBits.push("k-means");
    else if (data.cluster_linkage === "complete") methodBits.push("complete");
    else methodBits.push("平均连接");
    if (data.target_k != null) methodBits.push(`目标k=${data.target_k}`);
    else if (data.n_clusters_auto) methodBits.push(`k=${nGroups}`);
    if (data.max_cluster_size != null) {
      methodBits.push(`单组≤${data.max_cluster_size}`);
    }
    if (data.cluster_merges && data.cluster_merges.length) {
      methodBits.push(`τ内并组${data.cluster_merges.length}`);
    }
    if (data.beta_scale === "l2") methodBits.push("β 行L2");
    else if (data.beta_scale === "none") methodBits.push("β 原始");
    else methodBits.push("β z-score");
    if (data.cluster_balance && data.cluster_balance.imbalanced) {
      methodBits.push("组规模偏斜");
    }
    methodBits.push("不写 config");
    if (data.respect_regime) methodBits.push("regime对齐");
    if (data.collinearity_policy && data.collinearity_policy !== "keep_all") {
      methodBits.push(`共线=${data.collinearity_policy}`);
    }
    if (data.select_ridge) methodBits.push("选λ");
    if (data.speed_note) methodBits.push("大宇宙加速");
    if (data.daily_pit === false && data.pit_fundamentals !== false) {
      methodBits.push("财务快照");
    }
    const flags = data.lookahead_flags || {};
    const fundMode = String(flags.fundamentals || "");
    const pitOn = data.pit_fundamentals !== false && flags.pit_fundamentals !== false;
    const pitSummary = flags.pit_summary || data.fundamentals_pit_summary || {};
    const pitCover =
      pitSummary.resolved_ok != null && pitSummary.sample_count != null
        ? ` · 末日探针 ${pitSummary.resolved_ok}/${pitSummary.sample_count}`
        : "";
    let pitBanner;
    if (!pitOn || fundMode === "none") {
      pitBanner = `<p class="quant-cluster-pit-flag is-warn">非 PIT · 勿当实盘证据 · ${esc(
        String((flags.note || "").slice(0, 80))
      )}</p>`;
    } else if (fundMode === "pit_as_of_missing") {
      pitBanner = `<p class="quant-cluster-pit-flag is-warn">PIT 已开 · 财务点缺失${esc(
        pitCover
      )} · ${esc(String((flags.note || "").slice(0, 60)))}</p>`;
    } else {
      pitBanner = `<p class="quant-cluster-pit-flag">PIT as_of · 财务=${esc(
        fundMode || "pit_as_of"
      )}${esc(pitCover)}</p>`;
    }
    const ySpec = data.y_spec || {};
    const fp = data.sample_fingerprint || {};
    const yLine =
      ySpec.horizon_days != null
        ? `<p class="quant-cluster-method"><span class="quant-cluster-method-k">y</span>` +
          `<span class="quant-cluster-method-v">${esc(
            `horizon=${ySpec.horizon_days} · ${
              ySpec.include_cost ? "含成本" : "不含成本"
            } · ${ySpec.formula || "fwd ret"}`
          )}</span></p>`
        : "";
    const fpWarn = fp.promote_ok === false;
    const fpLine =
      fp.n_obs != null
        ? `<p class="quant-cluster-method${
            fpWarn ? " is-warn" : ""
          }"><span class="quant-cluster-method-k">样本</span>` +
          `<span class="quant-cluster-method-v">${esc(
            `n_obs=${fp.n_obs} · names=${fp.n_names ?? "—"}` +
              (fp.date_span ? ` · ${fp.date_span}` : "") +
              (fpWarn
                ? ` · 不足不可 promote：${(fp.blockers || []).slice(0, 2).join("；")}`
                : "")
          )}</span></p>`
        : "";
    const metaLine =
      `<div class="quant-cluster-status">` +
      `<div class="quant-cluster-status-stats">` +
      `<span class="quant-cluster-stat"><b>${esc(String(nGroups))}</b> 组</span>` +
      `<span class="quant-cluster-stat"><b>${esc(
        String(nWatchAll || nUni)
      )}</b> 观察</span>` +
      `<span class="quant-cluster-stat"><b>${esc(
        String(nFitted)
      )}</b> 拟合</span>` +
      `<span class="quant-cluster-stat"><b>${esc(
        String(nClustered)
      )}</b> 入组</span>` +
      (data.singleton_outlier_count
        ? `<span class="quant-cluster-stat is-warn"><b>${esc(
            String(data.singleton_outlier_count)
          )}</b> 单票离群</span>`
        : nOutlier
          ? `<span class="quant-cluster-stat is-warn"><b>${esc(
              String(nOutlier)
            )}</b> β离群</span>`
          : "") +
      (nDataSkip
        ? `<span class="quant-cluster-stat is-warn"><b>${esc(
            String(nDataSkip)
          )}</b> 数据不足</span>`
        : "") +
      `</div>` +
      pitBanner +
      yLine +
      fpLine +
      `<p class="quant-cluster-method">` +
      `<span class="quant-cluster-method-k">方法</span>` +
      `<span class="quant-cluster-method-v">${esc(
        methodBits.join(" · ")
      )}</span>` +
      `</p>` +
      `</div>`;

    const rosterNameByCode = clusterNameByCodeFromData(data);
    const outlierNote = outliers.length
      ? `<p class="sub">未入组离群：${esc(
          outliers
            .map((o) => {
              const c = o.code || o.stock_code || "?";
              const d = o.distance != null ? ` d=${o.distance}` : "";
              return `${formatStockCodeName(
                c,
                rosterNameByCode[normalizeProbeCode(c)] || ""
              )}${d}`;
            })
            .join("、")
        )}</p>`
      : "";

    const ha =
      data.holdings_assignment ||
      (data.pool_artifact && data.pool_artifact.holdings_assignment) ||
      {};
    let holdHtml = "";
    const umLines = ((ha && ha.unmapped) || [])
      .map((h) => {
        const nm = formatStockCodeName(h.stock_code, h.stock_name);
        const reason = h.reason ? `（${h.reason}）` : "";
        return `${nm}${reason}`;
      })
      .join("；");
    if (umLines) {
      holdHtml =
        `<p class="sub quant-cluster-unmapped">纸面未入组：${esc(
          umLines
        )}</p>`;
    }

    const landingHost =
      `<div class="quant-cluster-landing" id="quant-cluster-landing" aria-label="分组权落地">` +
      `<p class="quant-fingerprint is-busy">加载落地状态…</p>` +
      `</div>`;

    return {
      ok: true,
      html: `${metaLine}${outlierNote}${holdHtml}${landingHost}`,
    };
  }

  return {
    oosGateTipHtml,
    parseOosGateReason,
    oosGateStatusMeta,
    clusterTagHtml,
    clusterMetricHtml,
    formatMemberChipsHtml,
    formatMemberList,
    formatStockCodeName,
    clusterNameByCodeFromData,
    clusterGroupSuggestShim,
    clusterGroupOlsShim,
    clusterTightTopLines,
    buildClusterFactorTablesHtml,
    buildClusterGroupBodyHtml,
    buildOlsClustersSummaryHtml,
    renderProbeStockVsGroupTable,
    isProbeSingletonCluster,
    probeStatusBadge,
    probeFactorRowsFromExp,
    probeIcFieldsFromRow,
    probeIcMapFromExperiment,
    probeIcMapFromGroupPanel,
  };
}
