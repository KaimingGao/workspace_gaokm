import { researchGridHtml, metricCell } from "./research_grid.js";
import { fmtPct, metricClass } from "./bt_result.js";
import {
  weightSuggestLogicTip,
  weightSuggestStatusHtml as buildWeightSuggestStatusHtml,
  factorWeightSuggestCellTip,
} from "./suggest_status_ui.js";
import { formatClusterApiError } from "./cluster_api.js";

/** Quant domain: suggest */
export function installSuggest(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText } = q;
  const { readHorizonDays, readRidgeLambda, readClusterK, readWatchingLimit, ensureFactorMeta, rememberFactorMeta, factorMetaByName, factorMetaByLabel, factorIcWeightMergedHtml, parseOosGateReason, fmtEmptyCell, fmtOlsCell } = q;
  const { researchGridHtml, metricCell, metricClass, fmtPct } = q;

  function formatSuggestError(data, status, fallback = "请求失败") {
    const msg = formatClusterApiError(data, status);
    return msg && msg !== "请求失败" ? msg : fallback;
  }

  function isNetworkFetchError(err) {
    const msg = String((err && err.message) || err || "");
    const name = String((err && err.name) || "");
    return (
      name === "TypeError" ||
      /failed to fetch|networkerror|load failed|network request failed/i.test(msg)
    );
  }

  function explainNetworkFetchError(err, retryHint = "请再点「跑分组」") {
    if (!isNetworkFetchError(err)) {
      return String((err && err.message) || err || "请求失败");
    }
    return `服务断开（可能刚重启），${retryHint}`;
  }

  async function fetchRetry(url, init, { tries = 6, delayMs = 350 } = {}) {
    let lastErr = null;
    for (let i = 0; i < tries; i++) {
      try {
        return await fetch(url, init);
      } catch (err) {
        lastErr = err;
        if (i < tries - 1) {
          await new Promise((r) => setTimeout(r, delayMs * (i + 1)));
        }
      }
    }
    throw lastErr;
  }

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

  async function runAllSuggest() {
    setBusyText(els.quantOlsSummary, "分析中…", { busy: true });
    setBusyText(els.quantThresholdSummary, "分析中…", { busy: true });
    // 忙碌态只留在摘要行，避免表内再叠一句「分析中…」
    if (els.quantFactorList) els.quantFactorList.innerHTML = "";
    if (els.quantThresholdTable) els.quantThresholdTable.innerHTML = "";
    try {
      await runFactorIcSuggest();
      await runFactorOlsSuggest();
      await runThresholdSuggest({ useWatching: false });
      setQuantMeta("建议已更新 · 仅供对照");
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
      throw err;
    }
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
        document.getElementById("quant-probe-fold")?.scrollIntoView?.({
          behavior: "smooth",
          block: "nearest",
        });
      }
      setQuantMeta(icMsg);
      return;
    }
    renderFactorExperiment(exp, sug);
  }

  async function hydrateClustersFromLastReport(stubResult) {
    try {
      const hr = await fetchRetry(
        "/api/quant/factor-ols-clusters/last-report",
        undefined,
        { tries: 4, delayMs: 400 }
      );
      const hrText = await hr.text();
      const hydrated = hrText ? JSON.parse(hrText) : null;
      if (
        hydrated &&
        hydrated.success &&
        Array.isArray(hydrated.clusters) &&
        hydrated.clusters.length
      ) {
        return {
          ...hydrated,
          hydrated_from_job_stub: true,
          job_stub_n_clusters: stubResult && stubResult.n_clusters,
        };
      }
    } catch (_) {
      /* keep stub */
    }
    return null;
  }

  async function waitQuantOlsClustersJob(jobId) {
    const started = Date.now();
    // soft 25min：无心跳才判超时；有心跳则继续等（满池 auto-k+OOS 常 >35min）
    // absolute 90min：极端安全阀，避免永久挂起
    const softCapMs = 25 * 60 * 1000;
    const absoluteCapMs = 90 * 60 * 1000;
    const heartbeatFreshSec = 90;
    let sawOwnJob = false;
    let netFailStreak = 0;
    while (Date.now() - started < absoluteCapMs) {
      let res;
      try {
        // 单次少试几次：断连时由外层循环继续等，避免 6 次就整段失败
        res = await fetchRetry("/api/jobs/quant-ols-clusters?progress=1", undefined, {
          tries: 3,
          delayMs: 400,
        });
        netFailStreak = 0;
      } catch (err) {
        netFailStreak += 1;
        const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
        setBusyText(
          els.quantOlsSummary,
          `分组中… ${sec}s · 服务短暂断开，重连中（${netFailStreak}）…`,
          { busy: true }
        );
        await new Promise((r) => setTimeout(r, Math.min(4000, 500 * netFailStreak)));
        continue;
      }
      if (!res.ok && res.status >= 500) {
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      let payload = {};
      try {
        payload = await res.json();
      } catch (err) {
        if (isNetworkFetchError(err)) {
          netFailStreak += 1;
          await new Promise((r) => setTimeout(r, 500));
          continue;
        }
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      const job = (payload && payload.job) || {};
      const sameJob = !jobId || !job.id || job.id === jobId;
      if (sameJob && job.id) sawOwnJob = true;
      if (job.status === "idle" || !job.id) {
        if (sawOwnJob || Date.now() - started > 2500) {
          throw new Error("分组任务已中断（可能服务重启），请再点「跑分组」");
        }
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      if (!sameJob) {
        throw new Error("分组任务已被其它任务覆盖，请重试");
      }
      if (job.status === "done") {
        // progress=1 不含完整 result；再拉一次完整结果
        let fullJob = job;
        try {
          const fullRes = await fetchRetry("/api/jobs/quant-ols-clusters", undefined, {
            tries: 4,
            delayMs: 400,
          });
          const raw = await fullRes.text();
          let fullPayload = null;
          try {
            fullPayload = raw ? JSON.parse(raw) : null;
          } catch (_) {
            fullPayload = null;
          }
          if (!fullRes.ok || !fullPayload) {
            throw new Error(
              fullRes.ok
                ? "分组结果无法解析（可能含非法浮点）"
                : `HTTP ${fullRes.status}`
            );
          }
          fullJob = (fullPayload && fullPayload.job) || job;
        } catch (_) {
          // 完整 Job 序列化失败时仍可从 last-report 水合
          fullJob = { ...job, result: (job && job.result) || {} };
        }
        let result = (fullJob && fullJob.result) || {};
        // 热重载后 Job 可能只剩摘要：从报告缓存水合
        const hasClusters =
          Array.isArray(result.clusters) && result.clusters.length > 0;
        if (!hasClusters) {
          const hydrated = await hydrateClustersFromLastReport(result);
          if (hydrated) {
            result = hydrated;
            fullJob.result = result;
          }
        }
        return fullJob;
      }
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "分组任务失败");
      }
      const elapsed = Date.now() - started;
      if (elapsed >= softCapMs) {
        const ua = Number(job.updated_at);
        const fresh =
          Number.isFinite(ua) &&
          Date.now() / 1000 - ua < heartbeatFreshSec;
        if (!fresh) {
          throw new Error("分组任务超时（无心跳进展）");
        }
      }
      const pct = Number(job.pct) || 0;
      const msg = job.message || "运行中…";
      const sec = Math.max(1, Math.round(elapsed / 1000));
      const line = `分组中… ${sec}s · ${msg}${
        Number.isFinite(pct) && pct > 0 ? ` · ${Math.round(pct)}%` : ""
      }`;
      setBusyText(els.quantOlsSummary, line, { busy: true });
      await new Promise((r) => setTimeout(r, 400));
    }
    throw new Error("分组任务超时（超过 90 分钟）");
  }

  async function runFactorOlsClustersSuggest() {
    // 进页 bootstrap 与手动「跑分组」共用一次执行，避免双 POST 撞槽
    if (runFactorOlsClustersSuggest._inflight) {
      return runFactorOlsClustersSuggest._inflight;
    }
    runFactorOlsClustersSuggest._inflight = _runFactorOlsClustersSuggestInner()
      .finally(() => {
        runFactorOlsClustersSuggest._inflight = null;
      });
    return runFactorOlsClustersSuggest._inflight;
  }

  async function _runFactorOlsClustersSuggestInner() {
    const started = Date.now();
    let timer = null;
    const tick = () => {
      const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
      // 进度走分组卡头（主操作旁）；页顶保留路径说明
      setBusyText(els.quantOlsSummary, `分组中… ${sec}s · 观察池`, {
        busy: true,
      });
    };
    const stopTick = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };
    tick();
    timer = setInterval(tick, 1000);
    await ensureFactorMeta();
    try {
      let res;
      try {
        res = await fetchRetry(
          "/api/quant/factor-ols-clusters",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              lookback: 80,
              horizon_days: readHorizonDays(),
              watching_limit: readWatchingLimit(),
              // null → 自动目标 k≈n/5（约 4～10）+ 超大组二分；填了则按目标 k
              n_clusters: readClusterK(),
              cluster_method: "hierarchical",
              cluster_linkage: "complete",
              within_dist_quantile: 0.75,
              ridge_lambda: readRidgeLambda(),
              pit_fundamentals: true,
              beta_scale: "feature_zscore",
              run_oos_gate: true,
              oos_tol_pp: 1.0,
              run_group_score: true,
              run_pool_merge: true,
              top_n_per_group: 10,
              refresh_bars: !!(
                (document.getElementById("quant-cluster-refresh-bars") || {})
                  .checked
              ),
              // 默认关：研究全因子；勾选=与 live regime 白名单对齐（表里会裁掉许多因子）
              respect_regime: !!(
                (document.getElementById("quant-cluster-respect-regime") || {})
                  .checked
              ),
            }),
          },
          { tries: 4, delayMs: 500 }
        );
      } catch (err) {
        stopTick();
        throw new Error(explainNetworkFetchError(err));
      }
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
      if (!res.ok) {
        stopTick();
        const detail = formatSuggestError(
          data,
          res.status,
          res.status === 404
            ? "接口未找到：请重启 Web（WEB_RELOAD=off 时需手动重启）"
            : `HTTP ${res.status}`
        );
        setBusyText(els.quantOlsSummary, detail, { busy: false });
        setQuantMeta(`分组失败 · ${detail}`, { error: true });
        if (els.quantOlsClusters) {
          els.quantOlsClusters.textContent = detail;
        }
        throw new Error(detail);
      }
      // 兼容旧后端：忙时返回 error + job → 仍附到现任务
      if (
        data &&
        !data.background &&
        data.job &&
        (data.job.status === "running" ||
          String(data.error || "").includes("已有分组任务"))
      ) {
        data = {
          ...data,
          background: true,
          reused: true,
          success: true,
          ok: true,
        };
      }
      // FH2：后台 Job → 轮询；sync 兼容路径直接带 success 报告
      if (data && data.background && data.job) {
        stopTick();
        if (data.reused) {
          setBusyText(els.quantOlsSummary, "分组进行中 · 已接入现有任务", {
            busy: true,
          });
        }
        const job = await waitQuantOlsClustersJob(data.job.id);
        data = job.result || {};
      }
      stopTick();
      const computeSec = Math.max(1, Math.round((Date.now() - started) / 1000));
      if (!(data && data.success)) {
        const detail = formatSuggestError(
          data,
          0,
          (data && typeof data.error === "string" && data.error) || "分组失败"
        );
        setBusyText(els.quantOlsSummary, detail, { busy: false });
        setQuantMeta(`分组失败 · ${detail}`, { error: true });
        throw new Error(detail);
      }
      setBusyText(
        els.quantOlsSummary,
        data.cache_hit
          ? `命中 24h 缓存 · 渲染中… ${
              data.cache_age_hours != null ? `(${data.cache_age_hours}h 前)` : ""
            }`
          : `分组完成 · 渲染中… ${computeSec}s`,
        { busy: true }
      );
      await new Promise((r) => setTimeout(r, 0));
      try {
        q.cluster.renderOlsClusters(data);
      } catch (renderErr) {
        const detail = String((renderErr && renderErr.message) || renderErr);
        setBusyText(els.quantOlsSummary, detail, { busy: false });
        setQuantMeta(`分组已算完，渲染失败 · ${detail}`, { error: true });
        throw renderErr;
      }
      const nCl = data.n_clusters ?? (data.clusters || []).length ?? "—";
      const nUni = data.universe_count ?? data.stock_count ?? "—";
      const nWatch = (data.watching_codes || []).length;
      const nIn = data.stock_count ?? "—";
      const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
      const os = data.oos_summary || {};
      const oosTag = os.run
        ? ` · OOS✓${os.passed ?? 0}/✗${os.failed ?? 0}`
        : "";
      const pmOk = data.pool_merge && data.pool_merge.success ? " · 分池合成" : "";
      const kTag =
        data.target_k != null
          ? readClusterK() != null
            ? ` · 目标k=${data.target_k}`
            : ` · 自动k=${data.target_k}`
          : "";
      const pitTag =
        data.pit_fundamentals === false ||
        (data.lookahead_flags && data.lookahead_flags.pit_fundamentals === false)
          ? " · 非PIT"
          : " · PIT";
      const br = data.bars_refresh || {};
      const barsTag = data.refresh_bars
        ? ` · 日线远端 ${br.remote_count ?? 0}/${br.total ?? "—"}`
        : " · 仅缓存日线";
      const regimeTag = data.respect_regime ? " · regime裁剪" : " · 全因子";
      const cacheTag = data.cache_hit
        ? ` · 缓存命中${data.cache_age_hours != null ? ` ${data.cache_age_hours}h` : ""}`
        : data.hydrated_from_job_stub || data.hydrated_from_cache
          ? ` · 已从落盘恢复${
              data.restored_from ? `(${data.restored_from})` : ""
            }`
          : "";
      const liveHint =
        " · 研究区已更新；live 映射须点「对照」才会从旧组数切换";
      const doneLine = `分组 · ${nCl} 组${kTag} · 观察 ${nWatch || nUni} · 入组 ${nIn} · ${sec}s${oosTag}${pmOk}${pitTag}${barsTag}${regimeTag}${cacheTag}${liveHint}`;
      setBusyText(els.quantOlsSummary, doneLine, { busy: false });
      setQuantMeta(doneLine);
    } finally {
      stopTick();
    }
  }

  async function runFactorOlsPoolSuggest() {
    setBusyText(els.quantOlsSummary, "研究池 OLS 中…", { busy: true });
    await ensureFactorMeta();
    const res = await fetch("/api/quant/factor-ols-pool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        horizon_days: readHorizonDays(),
        watching_limit: 8,
        ridge_lambda: readRidgeLambda(),
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
      setQuantMeta(`池内 OLS 失败 · ${detail}`, { error: true });
      return;
    }
    q.cluster.renderFactorOls(data);
    if (data && data.success) {
      const codes = Array.isArray(data.stock_codes) ? data.stock_codes.filter(Boolean) : [];
      const codeNote = codes.length ? ` · ${codes.join("、")}` : "";
      setQuantMeta(
        `池内 OLS · ${data.stock_count ?? codes.length ?? "—"} 只${codeNote} · R²=${data.r_squared ?? "—"} · n=${data.sample_count ?? "—"}${
          data.standardized ? " · z-score β" : ""
        }`
      );
    } else {
      setQuantMeta((data && data.error) || "池内 OLS 失败", { error: true });
    }
  }

  async function runFactorOlsSuggest() {
    const code = q.cluster.readOlsCode();
    setBusyText(els.quantOlsSummary, `OLS 实验中… · ${code}`, { busy: true });
    await ensureFactorMeta();
    const res = await fetch("/api/quant/factor-ols", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code,
        lookback: 120,
        horizon_days: readHorizonDays(),
        ridge_lambda: readRidgeLambda(),
      }),
    });
    q.cluster.renderFactorOls(await res.json());
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

  return {
    applyThresholdSuggest,
    factorWeightSuggestCellTip,
    fmtEmptyCell,
    fmtOlsCell,
    fmtWeightCell,
    icTableHtml,
    loadFactorPanel,
    renderFactorExperiment,
    renderFactorPanelTable,
    renderThresholdTable,
    renderWeightDiffTable,
    runAllSuggest,
    runFactorCsIcSuggest,
    runFactorIcSuggest,
    runFactorOlsClustersSuggest,
    runFactorOlsPoolSuggest,
    runFactorOlsSuggest,
    runThresholdSuggest,
    weightDiffTableHtml,
    weightSuggestLogicTip,
    weightSuggestStatusHtml,
  };
}
