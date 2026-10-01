import { researchGridHtml, metricCell } from "./research_grid.js";
import { fmtPct, metricClass } from "./bt_result.js";
import {
  weightSuggestLogicTip,
  weightSuggestStatusHtml as buildWeightSuggestStatusHtml,
  factorWeightSuggestCellTip,
} from "./suggest_status_ui.js";
import { openProbeFold } from "./probe_ui.js";

/** Quant domain: suggest */
export function installSuggest(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText } = q;
  const { readHorizonDays, readRidgeLambda, readWatchingLimit, readHoldoutTradingDays, ensureFactorMeta, rememberFactorMeta, factorMetaByName, factorMetaByLabel, factorIcWeightMergedHtml, parseOosGateReason, fmtEmptyCell, fmtOlsCell } = q;
  const { researchGridHtml, metricCell, metricClass, fmtPct } = q;


  function weightSuggestStatusHtml(suggest) {
    return buildWeightSuggestStatusHtml(suggest, {
      escapeHtml,
      parseOosGateReason,
    });
  }

  function fmtWeightCell(v) {
    if (v == null || v === "") return fmtEmptyCell();
    const n = Number(v);
    if (!Number.isFinite(n)) return escapeHtml(String(v));
    /* API 权重多为 0～1；panel.weight_pct 已是百分数 */
    if (Math.abs(n) <= 1.5) return `${(n * 100).toFixed(1)}%`;
    return `${n.toFixed(1)}%`;
  }

  function icTableHtml(exp) {
    return factorIcWeightMergedHtml(exp, null, state.lastOlsForMerge);
  }

  async function loadFactorPanel() {
    if (!els.quantFactorList) return;
    try {
      const res = await fetch("/api/quant/factor-panel");
      const data = await res.json();
      if (!data.success) {
        if (els.quantMeta) els.quantMeta.textContent = data.error || "因子面板加载失败";
        return;
      }
      const rows = data.rows || [];
      rememberFactorMeta(
        rows.map((r) => ({
          name: r.factor || r.name,
          label: r.label,
          description: r.description || "",
        }))
      );
      state.lastFactorPanelForMerge = data;
      // 不预填 IC：等用户点分析；若已有 OLS 则仍可显示 OLS 列
      if (state.lastOlsForMerge || state.lastWeightSuggestForMerge) {
        q.cluster.renderMergedFactorTable();
      }
    } catch (err) {
      if (els.quantMeta) els.quantMeta.textContent = String(err.message || err);
    }
  }

  function renderFactorExperiment(exp, suggest) {
    if (!exp || !exp.success) {
      setQuantMeta((exp && exp.error) || "分析失败", { error: true });
      state.lastWeightSuggestForMerge = null;
      renderFactorPanelTable(null);
      if (els.quantWeightSuggest) els.quantWeightSuggest.textContent = "";
      state.quantLastWeightDiff = null;
      return;
    }
    const panel = exp.panel || { success: true, rows: exp.factors || [] };
    if (panel && panel.success == null) panel.success = true;
    const icCount = exp.panel
      ? exp.panel.ic_ready_count
      : (exp.factors || []).filter((f) => f.ic != null).length;
    if (els.quantMeta) {
      const keepBusy = els.quantMeta.classList.contains("is-busy");
      const gate = (suggest && suggest.oos_gate) || {};
      const gateTag =
        suggest && suggest.promote_ready
          ? " · OOS✓"
          : gate.ok && !gate.passed
            ? " · OOS✗"
            : gate.skipped
              ? " · OOS—"
              : "";
      setQuantMeta(
        `${exp.stock_code || ""} · horizon ${exp.horizon_days} · IC ${icCount}/${
          exp.panel ? exp.panel.factor_count : "—"
        }${gateTag}`,
        { busy: keepBusy }
      );
    }
    if (suggest && suggest.success) {
      if (els.quantWeightSuggest) els.quantWeightSuggest.innerHTML = weightSuggestStatusHtml(suggest);
      state.lastWeightSuggestForMerge = suggest;
      renderFactorPanelTable(panel, suggest);
      state.quantLastWeightDiff = suggest.config_diff || null;
    } else {
      if (els.quantWeightSuggest) els.quantWeightSuggest.textContent = "";
      state.lastWeightSuggestForMerge = null;
      renderFactorPanelTable(panel, null);
      state.quantLastWeightDiff = null;
    }
  }

  function renderFactorPanelTable(panel, suggest) {
    if (!els.quantFactorList) return;
    if (!panel || !panel.success) {
      state.lastFactorPanelForMerge = null;
      state.lastWeightSuggestForMerge = null;
      q.cluster.renderMergedFactorTable();
      return;
    }
    const rows = panel.rows || [];
    rememberFactorMeta(
      rows.map((r) => ({
        name: r.factor || r.name,
        label: r.label,
        description: r.description || "",
      }))
    );
    state.lastFactorPanelForMerge = panel;
    if (suggest && (suggest.success || suggest.suggested_weights)) {
      state.lastWeightSuggestForMerge = suggest;
    }
    q.cluster.renderMergedFactorTable();
  }

  function renderThresholdTable(suggest) {
    if (!els.quantThresholdTable) return;
    if (!suggest || !suggest.success) {
      els.quantThresholdTable.innerHTML =
        `<p class="watching-table-empty">尚未跑阈值建议</p>`;
      return;
    }
    const cur = suggest.current_thresholds || {};
    const sug = suggest.suggested_thresholds || {};
    const deltas = suggest.deltas || {};
    const rows = Object.keys(cur).map((k) => {
      const delta = deltas[k] != null ? deltas[k] : (sug[k] ?? cur[k]) - cur[k];
      return {
        key: k,
        cur: cur[k],
        sug: sug[k] ?? cur[k],
        delta,
      };
    });
    els.quantThresholdTable.innerHTML = researchGridHtml(
      [
        { id: "key", label: "阈值", flex: true },
        { id: "cur", label: "当前", widthPct: 18, num: true },
        { id: "sug", label: "建议", widthPct: 18, num: true },
        { id: "delta", label: "Δ", widthPct: 18, num: true },
      ],
      rows,
      (col, r) => {
        if (col.id === "key") return escapeHtml(String(r.key));
        if (col.id === "cur") return escapeHtml(String(r.cur));
        if (col.id === "sug") return escapeHtml(String(r.sug));
        if (col.id === "delta") {
          const text = `${r.delta > 0 ? "+" : ""}${Number(r.delta).toFixed(1)}`;
          return metricCell(escapeHtml(text), metricClass(r.delta));
        }
        return "—";
      },
      { emptyText: "暂无阈值建议" }
    );
  }

  function renderWeightDiffTable(suggest) {
    if (!els.quantFactorList) return;
    state.lastWeightSuggestForMerge = suggest && suggest.success ? suggest : state.lastWeightSuggestForMerge;
    els.quantFactorList.innerHTML =
      factorIcWeightMergedHtml(
        state.lastFactorPanelForMerge,
        state.lastWeightSuggestForMerge,
        state.lastOlsForMerge
      ) || "";
  }


  async function runFactorCsIcSuggest() {
    setQuantMeta("截面 IC 计算中…", { busy: true });
    setBusyText(els.quantOlsSummary, "截面 IC 中…", { busy: true });
    await ensureFactorMeta();
    const res = await fetch("/api/quant/factor-cs-ic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        horizon_days: readHorizonDays(),
        watching_limit: 12,
        min_names: 5,
        pit_fundamentals: true,
      }),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const detail =
        (data && (data.detail || data.error)) ||
        (res.status === 404
          ? "接口未找到：请重启 Web（WEB_RELOAD=off 时需手动重启）"
          : `HTTP ${res.status}`);
      setBusyText(els.quantOlsSummary, detail, { busy: false });
      setQuantMeta(`截面 IC 失败 · ${detail}`, { error: true });
      return;
    }
    if (!data || !(data.success || data.ok)) {
      setBusyText(els.quantOlsSummary, (data && data.error) || "截面 IC 失败", { busy: false });
      setQuantMeta((data && data.error) || "截面 IC 失败", { error: true });
      return;
    }
    const rows = (data.factors || []).map((f) => {
      const name = f.factor || f.name || "";
      const meta = factorMetaByName[name] || {};
      const spearMean = f.spearman && f.spearman.ic_mean;
      const pearMean = f.pearson && f.pearson.ic_mean;
      const primaryIc =
        f.ic != null ? f.ic : spearMean != null ? spearMean : pearMean;
      return {
        factor: name,
        name,
        label: f.label || meta.label || name,
        ic: primaryIc,
        sample_count:
          f.sample_count != null
            ? f.sample_count
            : (f.spearman && f.spearman.day_count) ||
              (f.pearson && f.pearson.day_count),
        exclusion_reason: f.exclusion_reason || null,
        spearman_ic: spearMean,
        pearson_ic: pearMean,
        icir:
          f.icir != null
            ? f.icir
            : (f.spearman && f.spearman.icir) || (f.pearson && f.pearson.icir),
        ic_kind: f.ic_kind || data.primary_ic_kind || "cs_spearman",
      };
    });
    const panel = {
      rows,
      factors: rows,
      exclusion_reasons: Object.fromEntries(
        rows.filter((r) => r.exclusion_reason).map((r) => [r.factor || r.name, r.exclusion_reason])
      ),
      mode: "factor_cross_section",
      primary_ic_kind: data.primary_ic_kind || "cs_spearman",
    };
    state.lastFactorPanelForMerge = panel;
    if (els.quantFactorList) {
      els.quantFactorList.innerHTML =
        factorIcWeightMergedHtml(panel, state.lastWeightSuggestForMerge, state.lastOlsForMerge) || "";
    }
    const scoreS = (data.score_ic && data.score_ic.spearman) || {};
    const scoreP = (data.score_ic && data.score_ic.pearson) || {};
    const nOk = rows.filter((r) => r.ic != null).length;
    const primaryLabel = "主IC=截面Spearman";
    setBusyText(
      els.quantOlsSummary,
      `截面 IC · ${primaryLabel} · ${data.stock_count ?? "—"} 只 · 日 ${data.day_count ?? "—"} · 因子有效 ${nOk}/${rows.length}` +
        (scoreS.ic_mean != null
          ? ` · 综合 ${scoreS.ic_mean}`
          : scoreP.ic_mean != null
            ? ` · 综合(Pearson) ${scoreP.ic_mean}`
            : "") +
        (data.pit_fundamentals ? " · PIT" : ""),
      { busy: false }
    );
    setQuantMeta(
      `截面 IC（${primaryLabel}）· ${nOk}/${rows.length} 因子 · horizon ${data.horizon_days ?? 1}` +
        (scoreS.icir != null
          ? ` · score ICIR ${scoreS.icir}`
          : scoreP.icir != null
            ? ` · score ICIR(P) ${scoreP.icir}`
            : "")
    );
  }

  async function runFactorIcSuggest() {
    const h = readHorizonDays();
    const [expRes, sugRes] = await Promise.all([
      fetch("/api/quant/factor-experiment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: "茅台", lookback: 120, horizon_days: h }),
      }),
      fetch("/api/quant/weight-suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: "茅台",
          lookback: 120,
          horizon_days: h,
          use_cs_ic: true,
          watching_limit: 12,
          ridge_lambda: readRidgeLambda(),
        }),
      }),
    ]);
    const exp = await expRes.json();
    const sug = await sugRes.json();
    // 若建议已用截面 IC，表内 IC/ICIR 与建议对齐
    if (sug && sug.success && sug.ic_mode === "cs_ic" && sug.factor_cs_ic) {
      const cs = sug.factor_cs_ic;
      const rows = (cs.factors || []).map((f) => {
        const name = f.factor || f.name || "";
        const meta = factorMetaByName[name] || {};
        return {
          factor: name,
          name,
          label: f.label || meta.label || name,
          ic: f.ic != null ? f.ic : f.pearson && f.pearson.ic_mean,
          sample_count:
            f.sample_count != null ? f.sample_count : f.pearson && f.pearson.day_count,
          exclusion_reason: f.exclusion_reason || null,
          icir: f.icir != null ? f.icir : f.pearson && f.pearson.icir,
        };
      });
      state.lastFactorPanelForMerge = {
        rows,
        factors: rows,
        mode: "factor_cross_section",
      };
      state.lastWeightSuggestForMerge = sug;
      if (sug.factor_ols && sug.factor_ols.success) state.lastOlsForMerge = sug.factor_ols;
      q.cluster.renderMergedFactorTable();
      if (els.quantWeightSuggest) els.quantWeightSuggest.innerHTML = weightSuggestStatusHtml(sug);
      state.quantLastWeightDiff = sug.config_diff || null;
      const nOk = rows.filter((r) => r.ic != null).length;
      const gate = sug.oos_gate || {};
      const gateTag = sug.promote_ready
        ? " · OOS✓"
        : gate.ok && !gate.passed
          ? " · OOS✗"
          : " · OOS—";
      const icMsg =
        `探针·截面驱动 · IC ${nOk}/${rows.length} · 只读${gateTag}` +
        (sug.ols_used ? " · 含 OLS 回退" : "");
      if (state.quantLastOlsClusters && state.quantLastOlsClusters.success && els.quantProbeSummary) {
        setBusyText(els.quantProbeSummary, icMsg + " · 不冲组表", { busy: false });
        openProbeFold();
      }
      setQuantMeta(icMsg);
      return;
    }
    renderFactorExperiment(exp, sug);
  }

  async function runFactorOlsClustersSuggest(opts) {
    void opts;
    const msg = "分组已退役（cluster_retired）· 请用全局 ŷ_oo 拟合";
    setBusyText(els.quantOlsSummary, msg, { busy: false });
    setQuantMeta(msg);
    return { success: false, error: "cluster_retired", cluster_retired: true };
  }

  async function runThresholdSuggest({ useWatching = false } = {}) {
    if (!els.quantThresholdSummary) return;
    setBusyText(
      els.quantThresholdSummary,
      useWatching ? "watching OOS 聚合中…" : "OOS 扫描中…",
      { busy: true }
    );
    const res = await fetch("/api/quant/threshold-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        useWatching
          ? { code: "茅台", lookback: 120, use_watching: true, watching_limit: 5 }
          : { code: "茅台", lookback: 120, use_watching: false }
      ),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok || !data || !data.success) {
      const err =
        (data && (data.error || data.detail)) ||
        (res.ok ? "阈值建议失败" : `HTTP ${res.status}`);
      setBusyText(els.quantThresholdSummary, String(err), { busy: false });
      renderThresholdTable(null);
      return;
    }
    if (useWatching) {
      const agg = data.watching_aggregate || {};
      const skip = data.skipped_apply
        ? " · 门槛未改"
        : "";
      const oosScale = (data.oos && data.oos.score_scale) || "";
      const yhatScale =
        (agg.score_scale || oosScale) === "predicted_yhat" ||
        data.score_scale === "predicted";
      const medianLabel = yhatScale
        ? `中位 wait=${agg.median_best_wait ?? agg.median_best_min_score ?? "—"}`
        : `中位 min=${agg.median_best_min_score ?? "—"}`;
      setBusyText(
        els.quantThresholdSummary,
        `${(data.rationale || []).slice(0, 1).join("")} · ${agg.stock_count || 0} 只 · ${medianLabel}${skip}`,
        { busy: false }
      );
    } else {
      setBusyText(
        els.quantThresholdSummary,
        (data.rationale || []).slice(0, 2).join(" · "),
        { busy: false }
      );
    }
    renderThresholdTable(data);
    state.quantLastThresholdSuggest = data;
    state.quantLastThresholdDiff = data.config_diff || null;
  }

  async function applyThresholdSuggest() {
    const sug = state.quantLastThresholdSuggest;
    if (!sug || !sug.success) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent = "请先跑「研究池阈值」再应用";
      }
      return;
    }
    if (sug.skipped_apply) {
      if (els.quantThresholdSummary) {
        els.quantThresholdSummary.textContent =
          "建议与当前接近或样本不足，无可应用改动";
      }
      return;
    }
    const cur = sug.current_thresholds || {};
    const next = sug.suggested_thresholds || {};
    const lines = ["写入 signal_config.stance_thresholds？", ""];
    ["avoid", "wait", "probe"].forEach((k) => {
      const a = cur[k];
      const b = next[k];
      if (a !== b) lines.push(`· ${k}: ${a} → ${b}`);
    });
    lines.push("", "仅改立场分档；不改 weights / scoring 滞回。");
    if (!window.confirm(lines.join("\n"))) return;

    if (els.quantThresholdSummary) {
      setBusyText(els.quantThresholdSummary, "正在写入…", { busy: true });
    }
    const res = await fetch("/api/signal/config/stance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        avoid: next.avoid,
        wait: next.wait,
        probe: next.probe,
        note: "研究枢纽人审·阈值建议",
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
      const msg = data.detail || data.error || "写入失败";
      if (els.quantThresholdSummary) {
        setBusyText(els.quantThresholdSummary, String(msg), { busy: false });
      }
      return;
    }
    const th = data.stance_thresholds || next;
    if (els.quantThresholdSummary) {
      setBusyText(
        els.quantThresholdSummary,
        `已写入 stance · avoid=${th.avoid} wait=${th.wait} probe=${th.probe}`,
        { busy: false }
      );
    }
    // 同步表：当前=刚写入
    state.quantLastThresholdSuggest = {
      ...sug,
      current_thresholds: { ...th },
      suggested_thresholds: { ...th },
      deltas: { avoid: 0, wait: 0, probe: 0 },
      skipped_apply: true,
      rationale: ["已写入当前配置"],
    };
    state.quantLastThresholdDiff = {
      success: true,
      skipped_apply: true,
      patch: {},
    };
    renderThresholdTable(state.quantLastThresholdSuggest);
  }

  function weightDiffTableHtml(suggest) {
    return factorIcWeightMergedHtml(null, suggest, state.lastOlsForMerge);
  }

  function paintOoStatus(opts) {
    const render = q.renderRemStatus;
    if (typeof render === "function") {
      render(els.quantOlsSummary, opts);
      return;
    }
    setBusyText(els.quantOlsSummary, opts.message || opts.chip || "", {
      busy: !!opts.busy,
    });
  }

  function _setOoPromoteEnabled(draftExists) {
    for (const id of [
      "quant-return-model-promote",
      "quant-return-model-persist-research",
    ]) {
      const btn = document.getElementById(id);
      if (btn) btn.disabled = !draftExists;
    }
  }

  async function renderReturnModelFit(data) {
    const resultEl = document.getElementById("quant-oo-result");
    const paintCoef =
      typeof q.renderOoCoefTable === "function" ? q.renderOoCoefTable : null;
    const clearResult =
      typeof q.clearOoResultBox === "function"
        ? q.clearOoResultBox
        : () => {
            if (resultEl) resultEl.innerHTML = "";
          };
    if (!data || !data.success) {
      paintOoStatus({
        state: "error",
        chip: "失败",
        message: (data && data.error) || "拟合失败",
        error: true,
      });
      setQuantMeta((data && data.error) || "ŷ_oo 拟合失败", { error: true });
      if (resultEl) {
        resultEl.innerHTML = `<p class="watching-table-empty">${escapeHtml(
          (data && data.error) || "拟合失败"
        )}</p>`;
      }
      if (paintCoef) await paintCoef(null);
      return;
    }
    const ols = data.ols || {};
    const nCoef = Object.keys((data.model || {}).coefficients || {}).length;
    const draftOk = !!(data.draft && data.draft.success);
    const fittedAt =
      (data.draft && data.draft.saved_at) ||
      data.fitted_at ||
      data.saved_at ||
      null;
    paintOoStatus({
      state: "ok",
      chip: "已拟合",
      message: draftOk ? "草稿已存 · 可启用研究/执行" : "已拟合 · 草稿未落盘",
      sampleCount: data.sample_count,
      fittedAt,
      liveOn: false,
      researchOn: draftOk,
      oos: data.oos || {},
    });
    const line =
      `${data.stock_count ?? "—"} 只 · n=${data.sample_count ?? "—"}` +
      ` · R²=${ols.r_squared ?? "—"} · β ${nCoef} 项` +
      (Number(ols.ridge_lambda) > 0 ? ` · Ridge λ=${ols.ridge_lambda}` : " · OLS");
    setQuantMeta(`ŷ_oo · ${line}`);
    state.lastReturnModelFit = data;
    /* 与 ŷ_τc 一致：摘要进表头 KPI，不另挂 fingerprint，避免系数表上方空白 */
    clearResult();
    if (paintCoef) {
      const rm = {
        ...(data.model || {}),
        r_squared: ols.r_squared != null ? ols.r_squared : (data.model || {}).r_squared,
        ridge_lambda:
          ols.ridge_lambda != null ? ols.ridge_lambda : (data.model || {}).ridge_lambda,
        sample_count: data.sample_count ?? (data.model || {}).sample_count,
      };
      await paintCoef(rm, { oos: data.oos || {} });
    }
    _setOoPromoteEnabled(draftOk);
  }

  function _ooOosFromStatus(data) {
    const fromStatus = data && data.oos;
    if (
      fromStatus &&
      typeof fromStatus === "object" &&
      (fromStatus.ic != null ||
        fromStatus.sign_hit != null ||
        fromStatus.sign_hit_rate != null ||
        fromStatus.n_test != null)
    ) {
      return fromStatus;
    }
    const fromFit = state.lastReturnModelFit && state.lastReturnModelFit.oos;
    if (fromFit && typeof fromFit === "object") return fromFit;
    return fromStatus && typeof fromStatus === "object" ? fromStatus : {};
  }

  async function refreshReturnModelStatus(opts = {}) {
    const justFittedOnce = !!opts.justFitted;
    const paintCoef =
      typeof q.renderOoCoefTable === "function" ? q.renderOoCoefTable : null;
    const clearResult =
      typeof q.clearOoResultBox === "function" ? q.clearOoResultBox : null;
    try {
      const res = await fetch("/api/quant/return-model/status");
      const data = await res.json().catch(() => null);
      if (!res.ok || !data || !data.success) {
        paintOoStatus({
          state: "error",
          chip: "状态",
          message: (data && (data.error || data.detail)) || "状态读取失败",
          error: true,
        });
        if (paintCoef) await paintCoef(null);
        return data;
      }
      const active = data.active || {};
      const draft = data.draft || {};
      const research = data.research || {};
      const oos = _ooOosFromStatus(data);
      _setOoPromoteEnabled(!!draft.exists);
      const lastFit = state.lastReturnModelFit || {};
      const fittedAt =
        draft.saved_at ||
        (lastFit.draft && lastFit.draft.saved_at) ||
        lastFit.fitted_at ||
        lastFit.saved_at ||
        null;
      const liveOn = !!active.exists;
      const researchOn = !!research.exists;
      // 仅拟合成功当次：chip=已拟合；后续「状态」/启用走落盘套芯片，时间仍用草稿 saved_at
      if (justFittedOnce) {
        paintOoStatus({
          state: "ok",
          chip: "已拟合",
          message: draft.exists
            ? liveOn || researchOn
              ? "草稿已更新 · 可再启用研究/执行"
              : "草稿已存 · 可启用研究/执行"
            : "已拟合",
          sampleCount:
            draft.sample_count ||
            lastFit.sample_count ||
            active.sample_count ||
            research.sample_count,
          fittedAt,
          promotedAt: active.promoted_at || research.promoted_at || null,
          liveOn,
          researchOn: researchOn || !!draft.exists,
          oos,
        });
        if (clearResult) clearResult();
        if (paintCoef) {
          const rm =
            data.return_model ||
            lastFit.model ||
            (lastFit.return_model ? lastFit.return_model : null);
          await paintCoef(rm, { oos });
        }
        return data;
      }
      if (liveOn || researchOn) {
        let chip = "待命";
        if (liveOn && researchOn) chip = "研究+执行";
        else if (researchOn) chip = "仅研究";
        else chip = "仅执行";
        paintOoStatus({
          state: "ok",
          chip,
          message: liveOn ? "已落盘" : "研究套已落盘 · 执行未写",
          sampleCount: active.sample_count || research.sample_count,
          fittedAt,
          promotedAt: active.promoted_at || research.promoted_at,
          liveOn,
          researchOn,
          oos,
        });
      } else if (draft.exists) {
        paintOoStatus({
          state: "ok",
          chip: "仅研究",
          message: "有草稿 · 尚未启用研究/执行",
          sampleCount: draft.sample_count,
          fittedAt,
          liveOn: false,
          researchOn: true,
          oos,
        });
      } else {
        paintOoStatus({
          state: "idle",
          chip: "待命",
          message: "拟合后看系数 · 再启用研究/执行",
          liveOn: false,
          researchOn: false,
        });
      }
      if (clearResult) clearResult();
      if (paintCoef) {
        await paintCoef(data.return_model || null, { oos });
      }
      return data;
    } catch (err) {
      paintOoStatus({
        state: "error",
        chip: "状态",
        message: String((err && err.message) || err || "状态读取失败"),
        error: true,
      });
      if (paintCoef) await paintCoef(null);
      return null;
    }
  }

  async function runReturnModelFit() {
    paintOoStatus({
      state: "busy",
      chip: "拟合中",
      message: "观察池堆叠 Ridge / OLS…",
      busy: true,
    });
    setQuantMeta("ŷ_oo 拟合中…", { busy: true });
    await ensureFactorMeta();
    const res = await fetch("/api/quant/return-model/fit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        horizon_days: 1,
        watching_limit: 300,
        ridge_lambda: 1.0,
        save_draft: true,
        holdout_trading_days:
          typeof readHoldoutTradingDays === "function" ? readHoldoutTradingDays() : 20,
      }),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const detail =
        (data && (data.detail || data.error)) ||
        (res.status === 404
          ? "接口未找到：请重启 Web"
          : `HTTP ${res.status}`);
      paintOoStatus({
        state: "error",
        chip: "失败",
        message: detail,
        error: true,
      });
      setQuantMeta(`ŷ_oo 失败 · ${detail}`, { error: true });
      return;
    }
    await renderReturnModelFit(data);
    await refreshReturnModelStatus({ justFitted: true });
  }

  async function runReturnModelPromote(persistRole = "live") {
    const role = String(persistRole || "live").toLowerCase() === "research"
      ? "research"
      : "live";
    const roleLabel = role === "research" ? "研究套" : "执行套";
    const pathHint =
      role === "research"
        ? "return_score_model_research.json"
        : "return_score_model_active.json";
    if (!window.confirm(`将 ŷ_oo 草稿写入${roleLabel}（${pathHint}）？`)) {
      return;
    }
    paintOoStatus({
      state: "busy",
      chip: "写入中",
      message: `写入${roleLabel}…`,
      busy: true,
    });
    const res = await fetch("/api/quant/return-model/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        note: `研究枢纽·ŷ_oo 人审启用${roleLabel}`,
        persist_role: role,
      }),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok || !data || !data.success) {
      const msg = (data && (data.error || data.detail)) || `HTTP ${res.status}`;
      paintOoStatus({
        state: "error",
        chip: "失败",
        message: String(msg),
        error: true,
      });
      setQuantMeta(String(msg), { error: true });
      return;
    }
    paintOoStatus({
      state: "ok",
      chip: role === "research" ? "仅研究" : "仅执行",
      message: `已写入${roleLabel}`,
      sampleCount: data.sample_count,
      promotedAt: data.promoted_at,
      liveOn: role === "live",
      researchOn: role === "research",
    });
    setQuantMeta(`ŷ_oo 已启用${roleLabel}`);
    await refreshReturnModelStatus();
  }

  return {
    applyThresholdSuggest,
    factorWeightSuggestCellTip,
    fmtEmptyCell,
    fmtOlsCell,
    fmtWeightCell,
    icTableHtml,
    loadFactorPanel,
    refreshReturnModelStatus,
    renderFactorExperiment,
    renderFactorPanelTable,
    renderThresholdTable,
    renderWeightDiffTable,
    runFactorCsIcSuggest,
    runFactorIcSuggest,
    runFactorOlsClustersSuggest,
    runReturnModelFit,
    runReturnModelPromote,
    runThresholdSuggest,
    weightDiffTableHtml,
    weightSuggestLogicTip,
    weightSuggestStatusHtml,
  };
}
