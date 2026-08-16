import { apiFetch } from "../api_client.js";
import { mergeScoringFloors } from "./scoring.js";
import { buildStrategyListHtml, buildStrategyRiskFootnoteHtml } from "./strategy_list_ui.js";
import { researchGridHtml } from "./research_grid.js";
import { buildRiskAuditMetaText, buildRiskAuditFoldSummary, buildSectorExposureHtml, buildStyleExposureHtml, buildRiskEffHtml, normalizeRiskBlockRows, buildRiskBlocksHtml } from "./strategy_risk_ui.js";
import { readPromoteExpireHard, readPromoteTtlHours, loadCachedPromoteHints } from "./promote_cache.js";

/** Quant domain: strategy */
export function installStrategy(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText } = q;
  const { factorMetaByName, ensureFactorMeta, renderPromoteHintsPanel, loadCachedPromoteHints, readPromoteExpireHard, readPromoteTtlHours } = q;

  async function loadConfigDiffPreview() {
    if (els.quantDiffSummary) els.quantDiffSummary.textContent = "加载 diff 预览…";
    try {
      const res = await fetch("/api/signal/config/diff-preview?use_saved=true");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderConfigDiffPreview(data);
      return data;
    } catch (err) {
      if (els.quantDiffSummary) els.quantDiffSummary.textContent = String(err.message || err);
      return null;
    }
  }

  async function loadSignalConfigPanel() {
    try {
      await ensureFactorMeta();
      renderFactorDict();
      await loadSentimentPriorForm();
      await loadDualScoreForm();
      return null;
    } catch (_) {
      return null;
    }
  }

  function priorModeLabel(mode) {
    if (mode === "risk") return "仅警告";
    if (mode === "gate") return "约束开仓";
    return "关闭";
  }

  function formatPriorStatus(prior, { saved = false } = {}) {
    const p = prior || {};
    const mode = String(p.mode || "off");
    const bits = [
      saved ? "已保存" : "当前",
      `${priorModeLabel(mode)}（${mode}）`,
      "ŷ 不变",
    ];
    bits.push("看空即触发");
    if (mode === "gate") {
      if (p.block_new_buys) bits.push("禁止新开仓");
      else {
        const s = Number(p.scale_buy_pct);
        bits.push(
          `缩仓 ${Number.isFinite(s) ? Math.round(s * 100) : 50}%`
        );
      }
      if (p.scale_holds) bits.push("已持仓同步");
    }
    return bits.join(" · ");
  }

  function fillSentimentPriorForm(prior) {
    const p = prior || {};
    const mode = String(p.mode || "off").toLowerCase();
    document.querySelectorAll('input[name="strategy-prior-mode"]').forEach((el) => {
      el.checked = el.value === mode;
    });
    const blockEl = document.getElementById("strategy-prior-block-buys");
    if (blockEl) blockEl.checked = !!p.block_new_buys;
    const holdsEl = document.getElementById("strategy-prior-scale-holds");
    if (holdsEl) holdsEl.checked = !!p.scale_holds;
    const scaleEl = document.getElementById("strategy-prior-scale");
    if (scaleEl) {
      const s = Number(p.scale_buy_pct);
      scaleEl.value = Number.isFinite(s) ? String(Math.round(s * 100)) : "50";
    }
    syncPriorGateOptsVisibility();
  }

  function syncPriorGateOptsVisibility() {
    const opts = document.getElementById("strategy-prior-gate-opts");
    if (!opts) return;
    const checked = document.querySelector(
      'input[name="strategy-prior-mode"]:checked'
    );
    const mode = checked ? checked.value : "off";
    opts.hidden = mode !== "gate";
  }

  async function loadSentimentPriorForm() {
    const st = document.getElementById("strategy-prior-status");
    try {
      const res = await fetch("/api/signal/config/sentiment-prior");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const prior = data.sentiment_prior || {};
      fillSentimentPriorForm(prior);
      if (st && !(st.textContent || "").includes("已保存")) {
        st.textContent = formatPriorStatus(prior);
      }
    } catch (err) {
      if (st) st.textContent = String(err.message || err);
    }
  }

  async function saveStrategySentimentPrior() {
    const st = document.getElementById("strategy-prior-status");
    const checked = document.querySelector(
      'input[name="strategy-prior-mode"]:checked'
    );
    const mode = checked ? checked.value : "off";
    const blockEl = document.getElementById("strategy-prior-block-buys");
    const holdsEl = document.getElementById("strategy-prior-scale-holds");
    const scaleEl = document.getElementById("strategy-prior-scale");
    let scalePct = scaleEl && scaleEl.value !== "" ? Number(scaleEl.value) : 50;
    if (!Number.isFinite(scalePct)) scalePct = 50;
    const scale = Math.max(0, Math.min(scalePct, 100)) / 100;
    const modeLabel = priorModeLabel(mode);
    if (
      !window.confirm(
        [
          "保存舆情先验配置？",
          "",
          `· 模式：${modeLabel}（prior.mode=${mode}）`,
          mode === "gate"
            ? blockEl && blockEl.checked
              ? "· Gate：禁止新开仓"
              : `· Gate：新开仓缩至 ${Math.round(scale * 100)}%`
            : "",
          mode === "gate" && holdsEl && holdsEl.checked
            ? `· 已持仓同步缩至 ${Math.round(scale * 100)}%`
            : "",
          "",
          "契约：不改 predicted_score（ŷ）；不改 weights。",
        ]
          .filter(Boolean)
          .join("\n")
      )
    ) {
      return;
    }
    if (st) st.textContent = "正在保存…";
    try {
      const body = {
        mode,
        note: "策略中心人审·舆情先验",
      };
      if (mode === "gate") {
        body.block_new_buys = !!(blockEl && blockEl.checked);
        body.scale_buy_pct = scale;
        body.scale_holds = !!(holdsEl && holdsEl.checked);
      }
      const res = await fetch("/api/signal/config/sentiment-prior", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        if (st) st.textContent = data.detail || data.error || "保存失败";
        return;
      }
      const prior = data.sentiment_prior || {};
      fillSentimentPriorForm(prior);
      if (st) st.textContent = formatPriorStatus(prior, { saved: true });
      loadStrategyList().catch(() => {});
    } catch (err) {
      if (st) st.textContent = String(err.message || err);
    }
  }

  async function loadStrategyList() {
    if (!els.strategyList || !els.strategyListLoading) return;
    try {
      const res = await fetch("/api/quant/strategies");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderStrategyList(data);
    } catch (err) {
      if (els.strategyListLoading) {
        els.strategyListLoading.classList.remove("is-busy");
        els.strategyListLoading.classList.add("is-error");
        els.strategyListLoading.textContent = String(err.message || err);
      }
      }
  }

  async function loadStrategyRiskAudit() {
    const meta = document.getElementById("strategy-risk-meta");
    const expEl = document.getElementById("strategy-exposure");
    const styleEl = document.getElementById("strategy-exposure-style");
    const effEl = document.getElementById("strategy-risk-eff");
    const blkEl = document.getElementById("strategy-risk-blocks");
    if (!expEl || !blkEl) return;
    if (meta) meta.textContent = "加载中…";
    try {
      const { ok, data, error } = await apiFetch("/api/paper");
      if (!ok) throw new Error(error || "纸面接口失败");
      const ops = data.ops_report || {};
      const origin = (data.summary && data.summary.origin_summary) || [];
      const exposure = data.exposure || ops.exposure || {};
      const blocks = ops.risk_blocks || ops.blocks || [];
      const blockItems = ops.risk_block_items || [];
      const ns = data.north_star || {};
      const rbSum = ns.risk_blocks || {};
      const budget =
        (ops.optimize && ops.optimize.vol_scale) ||
        ops.position_budget ||
        ops.risk_budget ||
        {};
      const lim =
        (exposure && exposure.limits) ||
        ops.risk_limits ||
        {};
      if (meta) {
        meta.textContent = buildRiskAuditMetaText({ ops, data, exposure, lim });
      }
      const foldDesc = document.getElementById("strategy-risk-audit-fold-desc");
      if (foldDesc) {
        foldDesc.textContent = buildRiskAuditFoldSummary({ blocks, rbSum, exposure });
      }
      expEl.innerHTML = buildSectorExposureHtml({
        exposure,
        budget,
        lim,
        escapeHtml,
        researchGridHtml,
      });
      if (styleEl) {
        styleEl.innerHTML = buildStyleExposureHtml({
          exposure,
          origin,
          escapeHtml,
          researchGridHtml,
        });
      }
      if (effEl) {
        let blocksAnnotate = [];
        try {
          const br = await apiFetch("/api/paper/risk-blocks?limit=8");
          blocksAnnotate = (br.ok && br.data && br.data.blocks) || [];
        } catch (_) {
          /* ignore */
        }
        effEl.innerHTML = buildRiskEffHtml({
          rbSum,
          ops,
          blocksAnnotate,
          escapeHtml,
          researchGridHtml,
        });
      }
      const blkData = normalizeRiskBlockRows({ blockItems, blocks });
      blkEl.innerHTML = buildRiskBlocksHtml(blkData, { escapeHtml, researchGridHtml });
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      const foldDesc = document.getElementById("strategy-risk-audit-fold-desc");
      if (foldDesc) foldDesc.textContent = "加载失败";
    }
  }

  async function promoteStrategy(strategyId, applyToPaper) {
    const st = document.getElementById("strategy-promote-status");
    const cached = loadCachedPromoteHints();
    if (cached && cached.expired) {
      renderPromoteHintsPanel(cached, "strategy-promote-hints");
      if (readPromoteExpireHard()) {
        if (st) {
          st.textContent =
            "Promote 提示已过期且「过期硬拦晋升」已开；请重跑 Top-K 后再晋升";
        }
        return;
      }
      if (st) st.textContent = "Promote 提示已过期，请先重跑 Top-K 再晋升";
      const go = window.confirm(
        `最近 Top-K 晋升提示已过期（TTL ${readPromoteTtlHours()}h）。仍继续晋升？（建议先回历史回测重跑）`
      );
      if (!go) return;
    }
    const hints = (cached && !cached.expired && cached.hints) || [];
    const align = (cached && !cached.expired && cached.ic_equity_align) || {};
    const hasWarn =
      hints.length > 0 || (align.ok && align.aligned_favor_pos_ic === false);
    if (hasWarn) {
      const lines = hints.map((h) => `· ${h.text || h.code}`).slice(0, 5);
      if (align.ok && align.aligned_favor_pos_ic === false) {
        lines.unshift(`· IC窗收益差 ${align.avg_return_spread_pp}pp（正IC未优于非正）`);
      }
      const ok = window.confirm(
        `最近 Top-K 存在 promote 警示：\n${lines.join("\n")}\n\n仍晋升 ${strategyId}？`
      );
      if (!ok) {
        if (st) st.textContent = "已取消晋升（存在回测警示）";
        return;
      }
    }
    if (st) {
      st.textContent = applyToPaper
        ? `正在晋升 ${strategyId} 并应用到纸面…`
        : `正在晋升 ${strategyId} 快照…`;
    }
    const res = await fetch("/api/strategy/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy: strategyId,
        note: "P2 UI promote",
        apply_to_paper: !!applyToPaper,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    const ver =
      (data.promoted && (data.promoted.version || data.promoted.strategy_version)) || "—";
    if (st) {
      const exe = (data.promoted && data.promoted.execution_summary) || {};
      const t0bit =
        exe.t0_ratio != null
          ? ` · 做T ${Math.round(Number(exe.t0_ratio) * 100)}%` +
            (exe.coupling ? `/${exe.coupling}` : "")
          : "";
      st.textContent = applyToPaper
        ? `已晋升 ${strategyId} @ ${ver} 并写入纸面（含 Execution）${t0bit}`
        : `已晋升 ${strategyId} @ ${ver}（含 Execution 快照；未改 signal_config）${t0bit}`;
    }
    return data;
  }

  function renderConfigDiffPreview(data) {
    if (!data || !data.success) {
      if (els.quantDiffSummary) els.quantDiffSummary.textContent = (data && data.error) || "预览失败";
      if (els.quantDiffTable) els.quantDiffTable.innerHTML = "";
      return;
    }
    const parts = [];
    const rows = [];
    for (const block of [data.weights, data.thresholds]) {
      if (!block || !block.success) continue;
      const label = block.kind === "weights" ? "权重" : "stance 阈值";
      const changes = block.changes || {};
      const keys = Object.keys(changes);
      parts.push(`${label} ${keys.length} 项变更`);
      keys.forEach((k) => {
        const c = changes[k];
        rows.push(
          `<tr><td>${label}:${k}</td><td class="num">${c.from}</td><td class="num">${c.to}</td><td class="num">${c.delta > 0 ? "+" : ""}${c.delta}</td></tr>`
        );
      });
    }
    if (els.quantDiffSummary) {
      els.quantDiffSummary.textContent = parts.length
        ? parts.join(" · ")
        : data.note || "无待合并 diff（可先跑「分析」/「阈值」或「每日量化」）";
    }
    if (els.quantDiffTable) {
      els.quantDiffTable.innerHTML = rows.length
        ? `<table class="quant-weight-table">
            <thead><tr><th>项</th><th>当前</th><th>建议</th><th>Δ</th></tr></thead>
            <tbody>${rows.join("")}</tbody>
          </table>`
        : "";
    }
  }

  function renderFactorDict() {
    const list = document.getElementById("strategy-factor-dict-list");
    if (!list) return;
    const factors = Object.values(factorMetaByName || {});
    if (!factors.length) {
      list.innerHTML = `<p class="results-empty">暂无因子元数据</p>`;
      return;
    }
    list.innerHTML = factors
      .map((f) => {
        const name = f.name || "";
        const label = f.label || name;
        const tip = f.description || label;
        return (
          `<button type="button" class="strategy-factor-chip factor-tip" data-factor="${escapeHtml(name)}" ` +
          `title="${escapeHtml(tip)}">${escapeHtml(label)}</button>`
        );
      })
      .join("");
  }

  function renderStrategyList(data) {
    if (!data || !data.success || !data.strategies) {
      if (els.strategyListLoading) {
        els.strategyListLoading.classList.remove("is-busy");
        els.strategyListLoading.classList.add("is-error");
        els.strategyListLoading.textContent = "加载失败";
      }
      return;
    }
    if (els.strategyListLoading) {
      els.strategyListLoading.classList.remove("is-busy");
      els.strategyListLoading.hidden = true;
    }
    els.strategyList.innerHTML = buildStrategyListHtml(data, { escapeHtml });
    els.strategyList.querySelectorAll(".strategy-promote-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const sid = btn.getAttribute("data-strategy") || "short";
        const apply = btn.getAttribute("data-apply") === "1";
        promoteStrategy(sid, apply).catch((err) => {
          const st = document.getElementById("strategy-promote-status");
          if (st) st.textContent = String(err.message || err);
        });
      });
    });
    const riskBox = document.getElementById("strategy-risk-limits");
    if (riskBox) {
      riskBox.innerHTML = buildStrategyRiskFootnoteHtml(data, { escapeHtml }).html;
    }
    if (data.sentiment_prior) {
      fillSentimentPriorForm(data.sentiment_prior);
    }
    // 顺带填 ŷ 门槛输入
    const floors = data.scoring_floors || {};
    const buyIn = document.getElementById("strategy-floor-buy");
    const holdIn = document.getElementById("strategy-floor-hold");
    if (buyIn && floors.min_predicted_score != null && buyIn.value === "") {
      buyIn.value = String(floors.min_predicted_score);
    }
    if (holdIn && floors.min_hold_predicted_score != null && holdIn.value === "") {
      holdIn.value = String(floors.min_hold_predicted_score);
    }
    // 显示当前已保存阈值
    const floorSt = document.getElementById("strategy-floor-status");
    if (floorSt && floors.min_predicted_score != null) {
      floorSt.textContent = `当前 · 买入 ≥ ${floors.min_predicted_score}% · 卖出 < ${floors.min_hold_predicted_score ?? "—"}%`;
    }
    fillDualScoreForm(data.dual_score);
    renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
  }

  function formatDualStatus(ds, { saved = false } = {}) {
    const d = ds || {};
    const bits = [
      saved ? "已保存" : "当前",
      `正交加权 w_EOD=${d.w_eod ?? "—"} w_τ=${d.w_tau ?? "—"}`,
      `w_mode=${d.w_mode ?? "fixed"}`,
      `τ闸 ≥ ${d.min_predicted_score_tau ?? "—"}%`,
    ];
    return bits.join(" · ");
  }

  function fillDualScoreForm(ds) {
    const d = ds || {};
    const floor = document.getElementById("strategy-dual-tau-floor");
    if (floor && d.min_predicted_score_tau != null) {
      floor.value = String(d.min_predicted_score_tau);
    }
    const we = document.getElementById("strategy-dual-w-eod");
    if (we && d.w_eod != null) we.value = String(d.w_eod);
    const wt = document.getElementById("strategy-dual-w-tau");
    if (wt && d.w_tau != null) wt.value = String(d.w_tau);
    const wm = document.getElementById("strategy-dual-w-mode");
    if (wm && d.w_mode) wm.value = String(d.w_mode);
    const st = document.getElementById("strategy-dual-status");
    if (st) st.textContent = formatDualStatus(d);
  }

  async function loadDualScoreForm() {
    try {
      const res = await fetch("/api/signal/config/dual-score");
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.dual_score) fillDualScoreForm(data.dual_score);
    } catch (_) {
      /* ignore */
    }
  }

  async function saveStrategyDualScore() {
    const st = document.getElementById("strategy-dual-status");
    const floorIn = document.getElementById("strategy-dual-tau-floor");
    const floor =
      floorIn && floorIn.value !== "" ? Number(floorIn.value) : null;
    const weIn = document.getElementById("strategy-dual-w-eod");
    const wtIn = document.getElementById("strategy-dual-w-tau");
    const wmIn = document.getElementById("strategy-dual-w-mode");
    const wEod = weIn && weIn.value !== "" ? Number(weIn.value) : 0.5;
    const wTau = wtIn && wtIn.value !== "" ? Number(wtIn.value) : 0.5;
    const wMode = wmIn && wmIn.value ? String(wmIn.value) : "fixed";
    if (floor == null || !Number.isFinite(floor)) {
      if (st) st.textContent = "请填写 τ 闸";
      return;
    }
    if (!Number.isFinite(wEod) || !Number.isFinite(wTau)) {
      if (st) st.textContent = "请填写融合权重";
      return;
    }
    const lines = [
      "保存融合分数？",
      "",
      "· 模式 = 正交加权（ŷ_trade = w·ŷ_EOD_rem + w·ŷ_τ）",
      `· w_EOD = ${wEod} · w_τ = ${wTau} · w_mode = ${wMode}`,
      `· τ 闸 ≥ ${floor}%`,
      "",
      "Kalman nowcast 默认为影子分，不替换 EOD 主字段。",
      "仅改 signal_config.dual_score；不改 weights / scoring。刷簿/预演后生效。",
    ].filter(Boolean);
    if (!window.confirm(lines.join("\n"))) return;
    if (st) {
      st.classList.add("is-busy");
      st.textContent = "正在保存…";
    }
    try {
      const body = {
        note: "策略中心人审·正交加权",
        fusion_mode: "blend",
        w_eod: wEod,
        w_tau: wTau,
        w_mode: wMode,
      };
      body.min_predicted_score_tau = floor;
      const res = await fetch("/api/signal/config/dual-score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        if (st) {
          st.classList.remove("is-busy");
          st.textContent = data.detail || data.error || "保存失败";
        }
        return;
      }
      fillDualScoreForm(data.dual_score);
      if (st) {
        st.classList.remove("is-busy");
        st.textContent = formatDualStatus(data.dual_score, { saved: true });
      }
    } catch (err) {
      if (st) {
        st.classList.remove("is-busy");
        st.textContent = String(err.message || err);
      }
    }
  }

  async function runStrategyWeightSuggest() {
    const status = document.getElementById("strategy-factor-status");
    const icHost = document.getElementById("strategy-ic-table");
    const wHost = document.getElementById("strategy-weight-table");
    const runBtn = document.getElementById("strategy-weight-suggest-run");
    if (!icHost && !wHost) return;
    if (runBtn) runBtn.disabled = true;
    setStrategyFactorExportEnabled(false);
    if (status) status.textContent = "分析 IC / 权重中…";
    try {
      await ensureFactorMeta();
      const code = await strategyIcCode();
      const res = await fetch("/api/quant/weight-suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          lookback: 120,
          horizon_days: readHorizonDays(),
          use_cs_ic: true,
          watching_limit: 12,
          ridge_lambda: readRidgeLambda(),
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        if (status) status.textContent = data.error || data.detail || "分析失败";
        if (icHost) icHost.innerHTML = "";
        if (wHost) wHost.innerHTML = "";
        state.strategyLastIcExport = null;
        state.strategyLastWeightDiff = null;
        return;
      }
      const exp =
        data.ic_mode === "cs_ic" && data.factor_cs_ic
          ? data.factor_cs_ic
          : data.factor_experiment || {};
      state.strategyLastIcExport = {
        stock_code: data.stock_code || exp.stock_code || code,
        generated_at: new Date().toISOString(),
        panel: exp.panel || null,
        factors: exp.factors || exp.rows || null,
        ic_mode: data.ic_mode,
        note: "只读 IC 导出；不改写 signal_config",
      };
      state.strategyLastWeightDiff = data.config_diff || {
        current_weights: data.current_weights,
        suggested_weights: data.suggested_weights,
        deltas: data.deltas,
      };
      if (icHost) {
        icHost.innerHTML =
          factorIcWeightMergedHtml(exp, data) ||
          `<p class="quant-trades-caption">无因子行</p>`;
      }
      if (wHost) wHost.innerHTML = "";
      if (status) {
        status.textContent =
          `标的 ${state.strategyLastIcExport.stock_code} · ${data.ic_mode || "single"} · 只读建议，须人审后合并配置`;
      }
      setStrategyFactorExportEnabled(true);
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }

  function setStrategyFactorExportEnabled(on) {
    const icBtn = document.getElementById("strategy-ic-export");
    const wBtn = document.getElementById("strategy-weight-diff-export");
    const fbBtn = document.getElementById("strategy-feedback-from-ic");
    if (icBtn) icBtn.disabled = !on;
    if (wBtn) wBtn.disabled = !on;
    if (fbBtn) fbBtn.disabled = !on;
  }

  async function strategyIcCode() {
    try {
      const res = await fetch("/api/watching");
      const data = await res.json();
      const wl = (data && (data.watchlist || data.codes)) || [];
      if (Array.isArray(wl) && wl.length) {
        const first = wl[0];
        return typeof first === "string" ? first : first.code || first.stock_code || "茅台";
      }
    } catch (_) {
      /* ignore */
    }
    return "茅台";
  }

  async function saveStrategyScoringFloors() {
    const st = document.getElementById("strategy-floor-status");
    const buyIn = document.getElementById("strategy-floor-buy");
    const holdIn = document.getElementById("strategy-floor-hold");
    const buy = buyIn && buyIn.value !== "" ? Number(buyIn.value) : null;
    const hold = holdIn && holdIn.value !== "" ? Number(holdIn.value) : null;
    if (
      (buy == null || !Number.isFinite(buy)) &&
      (hold == null || !Number.isFinite(hold))
    ) {
      if (st) st.textContent = "请填写买入或卖出门槛";
      return;
    }
    if (
      !window.confirm(
        [
          "保存 ŷ 滞回门槛？",
          "",
          buy != null && Number.isFinite(buy) ? `· 买入/入簿 ≥ ${buy}%` : "",
          hold != null && Number.isFinite(hold) ? `· 卖出 < ${hold}%` : "",
          "",
          "仅改 signal_config.scoring；不改 weights。保存后请刷新分池簿再预演调仓。",
        ]
          .filter(Boolean)
          .join("\n")
      )
    ) {
      return;
    }
    if (st) st.textContent = "正在保存…";
    try {
      const body = { note: "策略中心人审" };
      if (buy != null && Number.isFinite(buy)) body.min_predicted_score = buy;
      if (hold != null && Number.isFinite(hold)) body.min_hold_predicted_score = hold;
      const res = await fetch("/api/signal/config/scoring", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        if (st) st.textContent = data.detail || data.error || "保存失败";
        return;
      }
      const f = data.scoring_floors || {};
      if (st) {
        st.textContent = `已保存 · 买入 ≥ ${f.min_predicted_score ?? "—"}% · 卖出 < ${
          f.min_hold_predicted_score ?? "—"
        }% · 请刷新分池簿`;
      }
      loadStrategyList().catch(() => {});
    } catch (err) {
      if (st) st.textContent = String(err.message || err);
    }
  }

  return {
    loadConfigDiffPreview,
    loadSignalConfigPanel,
    loadStrategyList,
    loadStrategyRiskAudit,
    promoteStrategy,
    renderConfigDiffPreview,
    renderFactorDict,
    renderStrategyList,
    runStrategyWeightSuggest,
    saveStrategyScoringFloors,
    saveStrategySentimentPrior,
    saveStrategyDualScore,
    loadSentimentPriorForm,
    loadDualScoreForm,
    syncPriorGateOptsVisibility,
    setStrategyFactorExportEnabled,
    strategyIcCode,
  };
}
