import { syncOverviewOo } from "./factor_corr_ui.js";
import { researchGridHtml, metricCell } from "./research_grid.js";
import { fmtPct, metricClass } from "./bt_result.js";
import { formatApiDetail } from "../shared.js";
import {
  weightSuggestLogicTip,
  weightSuggestStatusHtml as buildWeightSuggestStatusHtml,
  factorWeightSuggestCellTip,
} from "./suggest_status_ui.js";
/** Quant domain: suggest */
export function installSuggest(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText } = q;
  const {
    readHorizonDays,
    readRidgeLambda,
    readWatchingLimit,
    readHoldoutTradingDays,
    readFitLookbackDays,
    readCrossSectionFitParams,
    ensureFactorMeta,
    rememberFactorMeta,
    factorMetaByName,
    factorMetaByLabel,
    factorIcWeightMergedHtml,
    parseOosGateReason,
    isYhatWeightsDecoupled,
    fmtEmptyCell,
    fmtOlsCell,
  } = q;
  const { researchGridHtml, metricCell, metricClass, fmtPct } = q;
  /** 研究/执行拟合勿叠跑：全池 LightGBM 极重，多点会把内存打爆 */
  let ooFitInflight = false;

  function weightSuggestStatusHtml(suggest) {
    return buildWeightSuggestStatusHtml(suggest, {
      escapeHtml,
      parseOosGateReason,
      isYhatWeightsDecoupled,
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
      const decoupled =
        typeof isYhatWeightsDecoupled === "function"
          ? isYhatWeightsDecoupled(gate)
          : String(gate.reason || "") === "yhat_weights_decoupled";
      let gateTag = "";
      if (suggest && suggest.promote_ready) {
        gateTag = decoupled ? " · 可合并" : " · OOS✓";
      } else if (gate.ok && !gate.passed) {
        gateTag = " · OOS✗";
      } else if (decoupled) {
        gateTag = " · 权重解耦";
      } else if (gate.skipped) {
        gateTag = " · OOS—";
      }
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
      const detail = formatApiDetail(
        data && (data.detail || data.error),
        res.status === 404
          ? "接口未找到：请重启 Web（WEB_RELOAD=off 时需手动重启）"
          : `HTTP ${res.status}`
      );
      setBusyText(els.quantOlsSummary, detail, { busy: false });
      setQuantMeta(`截面 IC 失败 · ${detail}`, { error: true });
      return;
    }
    if (!data || !(data.success || data.ok)) {
      const detail = formatApiDetail(data && data.error, "截面 IC 失败");
      setBusyText(els.quantOlsSummary, detail, { busy: false });
      setQuantMeta(detail, { error: true });
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
      if (els.quantWeightSuggest) els.quantWeightSuggest.innerHTML = weightSuggestStatusHtml(sug);
      state.quantLastWeightDiff = sug.config_diff || null;
      const nOk = rows.filter((r) => r.ic != null).length;
      const gate = sug.oos_gate || {};
      const decoupled =
        typeof isYhatWeightsDecoupled === "function"
          ? isYhatWeightsDecoupled(gate)
          : String(gate.reason || "") === "yhat_weights_decoupled";
      let gateTag = " · OOS—";
      if (sug.promote_ready) {
        gateTag = decoupled ? " · 可合并" : " · OOS✓";
      } else if (gate.ok && !gate.passed) {
        gateTag = " · OOS✗";
      } else if (decoupled) {
        gateTag = " · 权重解耦";
      }
      const icMsg =
        `截面 IC · ${nOk}/${rows.length} · 只读${gateTag}` +
        (sug.ols_used ? " · 含 OLS 回退" : "");
      setQuantMeta(icMsg);
      return;
    }
    renderFactorExperiment(exp, sug);
  }

  function weightDiffTableHtml(suggest) {
    return factorIcWeightMergedHtml(null, suggest, state.lastOlsForMerge);
  }

  // 状态只写 ŷ_oo 卡头 #quant-ols-summary，不刷页顶 #quant-meta
  function paintOoStatus(opts) {
    const o = opts && typeof opts === "object" ? opts : {};
    const view =
      o.suiteView === "live"
        ? "live"
        : o.suiteView === "research"
          ? "research"
          : null;
    if (view && typeof q.paintSuiteHeadStatus === "function") {
      q.paintSuiteHeadStatus("oo", view, o);
      return;
    }
    if (view === "research" && typeof q.paintSuiteResearchBusy === "function") {
      q.paintSuiteResearchBusy("oo", o);
      return;
    }
    q.renderRemStatus(els.quantOlsSummary, {
      ...o,
      omitRoleMetas: true,
      omitFitMeta: true,
    });
  }

  /** 状态接口 → ŷ_τc 卡头字段（拟合 / 研究 / 执行）。 */
  function ooRidgeStatusPayload(data) {
    const src = data && typeof data === "object" ? data : {};
    const draft = src.draft && typeof src.draft === "object" ? src.draft : {};
    const active = src.active && typeof src.active === "object" ? src.active : {};
    const research =
      src.research && typeof src.research === "object" ? src.research : {};
    const liveOn = !!active.exists;
    const researchOn = !!research.exists;
    const draftOn = !!draft.exists;
    return {
      ...src,
      exists: liveOn || researchOn || draftOn,
      live_model_present: liveOn,
      research_exists: researchOn,
      last_report_exists: draftOn,
      shadow: draftOn && !liveOn && !researchOn,
      fitted_at: draft.fitted_at || draft.saved_at || null,
      research_fitted_at:
        research.fitted_at || research.source_saved_at || research.promoted_at || null,
      research_promoted_at: research.promoted_at || null,
      live_fitted_at:
        active.fitted_at || active.source_saved_at || active.promoted_at || null,
      live_promoted_at: active.promoted_at || null,
    };
  }

  function paintOoFitStatus(data, extra = {}) {
    q.paintRidgeEnableStatus(els.quantOlsSummary, ooRidgeStatusPayload(data), {
      omitTauEndBit: true,
      idleMessage: "研究 Holdout OOS",
      ...extra,
    });
  }

  function _setOoPromoteEnabled(_researchExists) {
    // 允许无研究套直接「拟合执行」（Holdout=0 全样本 → 执行槽）
    const btn = document.getElementById("quant-return-model-promote");
    if (btn) btn.disabled = false;
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
    state.lastReturnModelFit = data;
    syncOverviewOo(data.oos || {});
    /* 与 ŷ_τc 一致：成功路径只刷系数表，不另挂 fingerprint 摘要块 */
    clearResult();
    const lgbRm =
      data.backends &&
      data.backends.lightgbm &&
      (data.backends.lightgbm.return_model || data.backends.lightgbm);
    const fi =
      (data.model && data.model.feature_importance) ||
      (lgbRm && lgbRm.feature_importance) ||
      null;
    const rm = {
      ...(data.model || {}),
      sample_count: data.sample_count ?? (data.model || {}).sample_count,
      backend: "lightgbm",
      recommended_backend: "lightgbm",
      feature_importance: Array.isArray(fi) ? fi : (data.model || {}).feature_importance,
    };
    if (typeof q.cacheHeadSuitePacks === "function") {
      q.cacheHeadSuitePacks("oo", {
        suite_research: {
          return_model: rm,
          oos: data.oos || {},
          fitted_at: data.fitted_at || null,
          backend: "lightgbm",
          feature_importance: Array.isArray(fi) ? fi : null,
        },
      });
    }
    if (typeof q.setHeadSuiteView === "function") {
      q.setHeadSuiteView("oo", "research", { paint: true });
    } else if (paintCoef) {
      await paintCoef(rm, {
        oos: data.oos || {},
        showOos: true,
        feature_importance: Array.isArray(fi) ? fi : undefined,
      });
    }
  }

  function _ooOosFromStatus(data) {
    const fromStatus = data && data.oos;
    if (
      fromStatus &&
      typeof fromStatus === "object" &&
      (fromStatus.cs_ic != null ||
        fromStatus.cs_rank_ic != null ||
        fromStatus.cs_icir != null ||
        fromStatus.ic != null ||
        fromStatus.sign_hit != null ||
        fromStatus.sign_hit_rate != null ||
        fromStatus.n_test != null ||
        fromStatus.n_train != null ||
        fromStatus.holdout_trading_days != null)
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
          message: formatApiDetail(
            data && (data.error || data.detail),
            "状态读取失败"
          ),
          error: true,
        });
        if (paintCoef) await paintCoef(null);
        return data;
      }
      const draft = data.draft || {};
      const oos = _ooOosFromStatus(data);
      syncOverviewOo(oos);
      const researchOn = !!(data.research && data.research.exists);
      _setOoPromoteEnabled(researchOn);
      const lastFit = state.lastReturnModelFit || {};
      const fittedAt =
        (data.research && (data.research.fitted_at || data.research.promoted_at)) ||
        draft.fitted_at ||
        draft.saved_at ||
        (lastFit.draft && lastFit.draft.saved_at) ||
        lastFit.fitted_at ||
        lastFit.saved_at ||
        null;
      const liveOn = !!(data.active && data.active.exists);
      // 刚拟合研究：指标来自研究套 Holdout OOS；拟合执行不改因子表指标
      const extra = {
        justFitted: justFittedOnce,
        fittedAt,
        suiteView: "research",
      };
      if (justFittedOnce) {
        // 文案只谈研究套；执行是否已有槽不改 chip（避免「跟到执行」）
        extra.message = researchOn
          ? "研究套已写入 · 因子表=Holdout OOS"
          : "已拟合";
      }
      paintOoFitStatus(data, extra);
      if (justFittedOnce && clearResult) clearResult();
      if (typeof q.cacheHeadSuitePacks === "function") {
        q.cacheHeadSuitePacks("oo", data);
      }
      // 刚拟合：强制研究视图，并把本次 Holdout OOS 写入研究包
      if (justFittedOnce && typeof q.setHeadSuiteView === "function") {
        let rm = lastFit.model ||
          (lastFit.return_model ? lastFit.return_model : null) ||
          data.return_model;
        if (rm && typeof rm === "object") {
          const be = String(rm.backend || rm.solver || data.backend || "").toLowerCase();
          const boosterOk =
            !!rm.booster_b64 ||
            !!(
              rm.booster &&
              typeof rm.booster === "object" &&
              (rm.booster.payload_b64 || rm.booster.format)
            );
          const coefObj =
            rm.coefficients && typeof rm.coefficients === "object"
              ? rm.coefficients
              : {};
          const namesOnlyLgb =
            Array.isArray(rm.feature_names) &&
            rm.feature_names.length > 0 &&
            !Object.keys(coefObj).length;
          const looksLgb =
            be === "lightgbm" ||
            be === "lgb" ||
            String(rm.schema || "").includes("lgb") ||
            boosterOk ||
            namesOnlyLgb;
          if (looksLgb) {
            const lgbBe =
              data.backends &&
              data.backends.lightgbm &&
              (data.backends.lightgbm.return_model || data.backends.lightgbm);
            const fi =
              rm.feature_importance ||
              (data.return_model && data.return_model.feature_importance) ||
              (lgbBe && lgbBe.feature_importance) ||
              (lastFit.model && lastFit.model.feature_importance) ||
              null;
            rm = {
              ...rm,
              backend: "lightgbm",
              solver: rm.solver || "lightgbm",
              feature_importance: Array.isArray(fi) ? fi : rm.feature_importance,
            };
          }
          q.cacheHeadSuitePacks("oo", {
            suite_research: {
              return_model: rm,
              oos,
              fitted_at: fittedAt,
              backend: "lightgbm",
              feature_importance: rm.feature_importance,
            },
          });
        }
        q.setHeadSuiteView("oo", "research", { paint: true });
      } else if (typeof q.paintHeadSuiteCoef === "function") {
        await q.paintHeadSuiteCoef("oo");
      } else if (paintCoef) {
        await paintCoef(data.return_model || null, { oos, showOos: true });
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
    if (ooFitInflight) {
      paintOoStatus({
        state: "busy",
        chip: "拟合中",
        message: "ŷ_oo 正在拟合，请等待当前任务结束",
        busy: true,
        suiteView: "research",
      });
      return;
    }
    ooFitInflight = true;
    const holdN =
      typeof readHoldoutTradingDays === "function" ? readHoldoutTradingDays("oo") : 20;
    paintOoStatus({
      state: "busy",
      chip: "拟合中",
      message: holdN === 0 ? "ŷ_oo 全样本重训…" : "ŷ_oo 拟合 + Holdout OOS…",
      busy: true,
      suiteView: "research",
    });
    try {
      await ensureFactorMeta();
      const csFit = readCrossSectionFitParams("oo");
      const res = await fetch("/api/quant/return-model/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback:
            typeof readFitLookbackDays === "function" ? readFitLookbackDays("oo") : 600,
          horizon_days: 1,
          watching_limit: readWatchingLimit(),
          save_draft: true,
          holdout_trading_days: holdN,
          sync: false,
          ...csFit,
        }),
      });
      const posted = await res.json().catch(() => ({}));
      const sumEl = document.getElementById("quant-oo-summary");
      const awaited = await q.awaitRidgeFitIfBackground(res, posted, {
        persist: false,
        jobName: "oo-lgb",
        modelPath: "/api/quant/return-model/status",
        sumEl,
        label: "ŷ_oo",
        suiteHead: "oo",
        suiteView: "research",
      });
      const data = awaited.data || null;
      const httpOk = awaited.httpOk;
      if (!httpOk || !data || data.success === false) {
        const detail = q.formatHorizonFitErrorMessage(
          data,
          "",
          !res.ok
            ? res.status === 404
              ? "接口未找到：请重启 Web"
              : `HTTP ${res.status}`
            : "拟合失败"
        );
        paintOoStatus({
          state: "error",
          chip: "失败",
          message: detail,
          error: true,
        });
        const resultEl = document.getElementById("quant-oo-result");
        if (resultEl) {
          resultEl.innerHTML = `<p class="watching-table-empty">${escapeHtml(
            detail
          )}</p>`;
        }
        if (typeof q.renderOoCoefTable === "function") {
          await q.renderOoCoefTable(null);
        }
        return;
      }
      await renderReturnModelFit(data);
      // 拟合研究：草稿 → 研究槽（因子表 OOS 来自本次 Holdout）
      paintOoStatus({
        state: "busy",
        chip: "写入中",
        message: "写入研究套…",
        busy: true,
        suiteView: "research",
      });
      const promo = await fetch("/api/quant/return-model/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note: `ui oo fit-research Holdout=${holdN}`,
          persist_role: "research",
        }),
      });
      const promoData = await promo.json().catch(() => null);
      if (!promo.ok || !promoData || !promoData.success) {
        const msg = formatApiDetail(
          promoData && (promoData.error || promoData.detail),
          `研究套写入失败 HTTP ${promo.status}`
        );
        paintOoStatus({
          state: "error",
          chip: "失败",
          message: msg,
          error: true,
        });
        await refreshReturnModelStatus({ justFitted: true });
        return;
      }
      await refreshReturnModelStatus({ justFitted: true });
    } catch (err) {
      paintOoStatus({
        state: "error",
        chip: "失败",
        message: q.describeNetworkFetchError(err),
        error: true,
      });
    } finally {
      ooFitInflight = false;
    }
  }

  /** 拟合执行：Holdout=0 全样本重训 → 执行槽（研究套可选） */
  async function runReturnModelPromote(_persistRole = "live") {
    if (ooFitInflight) {
      paintOoStatus({
        state: "busy",
        chip: "拟合中",
        message: "ŷ_oo 正在拟合，请等待当前任务结束",
        busy: true,
        suiteView: "live",
      });
      return;
    }
    ooFitInflight = true;
    paintOoStatus({
      state: "busy",
      chip: "拟合中",
      message: "ŷ_oo 全样本重训…",
      busy: true,
      suiteView: "live",
    });
    try {
      await ensureFactorMeta();
      const csFit = readCrossSectionFitParams("oo");
      const fitRes = await fetch("/api/quant/return-model/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback:
            typeof readFitLookbackDays === "function" ? readFitLookbackDays("oo") : 600,
          horizon_days: 1,
          watching_limit: readWatchingLimit(),
          save_draft: true,
          holdout_trading_days: 0,
          sync: false,
          ...csFit,
        }),
      });
      const posted = await fitRes.json().catch(() => ({}));
      const sumEl = document.getElementById("quant-oo-summary");
      const awaited = await q.awaitRidgeFitIfBackground(fitRes, posted, {
        persist: false,
        jobName: "oo-lgb",
        modelPath: "/api/quant/return-model/status",
        sumEl,
        label: "ŷ_oo",
        suiteHead: "oo",
        suiteView: "live",
      });
      const fitData = awaited.data || null;
      if (!awaited.httpOk || !fitData || fitData.success === false) {
        paintOoStatus({
          state: "error",
          chip: "失败",
          message: q.formatHorizonFitErrorMessage(
            fitData,
            "",
            !awaited.httpOk ? `HTTP ${fitRes.status}` : "全样本重训失败"
          ),
          error: true,
        });
        return;
      }
      paintOoStatus({
        state: "busy",
        chip: "写入中",
        message: "全样本包写入执行套…",
        busy: true,
        suiteView: "live",
      });
      const promo = await fetch("/api/quant/return-model/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note: "ui oo promote-exec full-sample refit",
          persist_role: "live",
          refit_full_sample: true,
        }),
      });
      const promoData = await promo.json().catch(() => null);
      if (!promo.ok || !promoData || !promoData.success) {
        paintOoStatus({
          state: "error",
          chip: "失败",
          message: formatApiDetail(
            promoData && (promoData.error || promoData.detail),
            `HTTP ${promo.status}`
          ),
          error: true,
        });
        return;
      }
      // 不刷全样本 IS 指标；有研究套时因子表仍用其 Holdout OOS
      const status = await refreshReturnModelStatus();
      const hasResearch = !!(status && status.research && status.research.exists);
      if (typeof q.setHeadSuiteView === "function") {
        q.setHeadSuiteView("oo", "live", { paint: true, keepPrev: false });
      } else if (typeof q.syncBacktestRoleFromSuite === "function") {
        q.syncBacktestRoleFromSuite("live");
      }
      paintOoStatus({
        state: "ok",
        chip: "已拟合",
        message: hasResearch
          ? "执行套已全样本部署 · Holdout OOS 见研究套"
          : "执行套已全样本部署",
        suiteView: "live",
        persistOk: true,
        persistRole: "live",
      });
      if (typeof q.refreshSuiteFitSelect === "function") {
        void q.refreshSuiteFitSelect("oo", { keepPrev: false });
      }
    } catch (err) {
      paintOoStatus({
        state: "error",
        chip: "失败",
        message: q.describeNetworkFetchError(err),
        error: true,
      });
    } finally {
      ooFitInflight = false;
    }
  }

  return {
    factorWeightSuggestCellTip,
    fmtEmptyCell,
    fmtOlsCell,
    fmtWeightCell,
    icTableHtml,
    loadFactorPanel,
    refreshReturnModelStatus,
    renderFactorExperiment,
    renderFactorPanelTable,
    renderWeightDiffTable,
    runFactorCsIcSuggest,
    runFactorIcSuggest,
    runReturnModelFit,
    runReturnModelPromote,
    weightDiffTableHtml,
    weightSuggestLogicTip,
    weightSuggestStatusHtml,
  };
}
