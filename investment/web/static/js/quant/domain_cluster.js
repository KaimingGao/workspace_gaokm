import { downloadJson } from "../shared.js";
import { normalizeProbeCode } from "./names.js";
import { postClusterLive as postClusterLiveApi, formatClusterApiError } from "./cluster_api.js";
import { clusterLandingHtml } from "./cluster_landing.js";
import { PROBE_EMPTY_CLUSTER_FAILED, PROBE_EMPTY_NO_CLUSTER, PROBE_EMPTY_COMPARE_FAILED, probePickerTriggerHtml, probePickerIdentityHtml, summarizeProbeHeterogeneity, buildProbeReadySummaryHtml, buildProbeNotReadySummaryHtml, buildProbeSingletonSummaryHtml, buildProbeNotInClusterPlainText, buildProbeHeteroSummaryHtml, buildProbeMetaSingleton, buildProbeMetaNotInCluster, buildProbeMetaHetero, buildProbePickerMenuHtml, probeStatusBadge } from "./probe_ui.js";
import { createScoreTooltipController } from "../score_tooltip.js";

/** Quant domain: cluster */
export function installClusterProbe(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText, normalizeProbeCode } = q;
  const { readHorizonDays, readRidgeLambda, readClusterK, ensureFactorMeta, factorMetaByName, clusterNameByCodeFromData, buildClusterFactorTablesHtml, buildClusterGroupBodyHtml, buildOlsClustersSummaryHtml, renderProbeStockVsGroupTableHtml, isProbeSingletonCluster, factorIcWeightMergedHtml, oosGateTipHtml, parseOosGateReason, buildCrossSectionResult, oosGateStatusMeta, probeFactorRowsFromExp, probeIcFieldsFromRow, probeIcMapFromExperiment, probeIcMapFromGroupPanel } = q;
  const oosGateTips = createScoreTooltipController();

  function applyProbePickerSelection(code, { silent } = {}) {
    const hidden = document.getElementById("quant-ols-code");
    const trigger = document.getElementById("quant-ols-code-trigger");
    if (!hidden || !trigger) return;
    const c = normalizeProbeCode(code);
    const row =
      state.probePickerRows.find((r) => r.code === c) ||
      (c ? { code: c, name: "", group: "" } : null);
    hidden.value = row && row.code ? row.code : "";
    trigger.innerHTML = probePickerTriggerHtml(row);
    const picker = trigger.closest(".quant-probe-picker");
    const opts = picker
      ? picker.querySelectorAll('[role="option"]')
      : [];
    opts.forEach((el) => {
      const selected = el.getAttribute("data-code") === hidden.value;
      el.setAttribute("aria-selected", selected ? "true" : "false");
      el.classList.toggle("is-selected", selected);
    });
    setProbePickerOpen(false);
    if (!silent && hidden.value) {
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function bindProbePicker() {
    const root = document.getElementById("quant-probe-picker");
    const trigger = document.getElementById("quant-ols-code-trigger");
    const menu = document.getElementById("quant-ols-code-menu");
    if (!root || !trigger || !menu || root._probePickerBound) return;
    root._probePickerBound = true;
    trigger.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setProbePickerOpen(!root.classList.contains("is-open"));
    });
    menu.addEventListener("click", (e) => {
      const opt = e.target && e.target.closest("[data-code]");
      if (!opt) return;
      e.preventDefault();
      applyProbePickerSelection(opt.getAttribute("data-code"));
    });
    document.addEventListener("click", (e) => {
      if (!root.contains(e.target)) setProbePickerOpen(false);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setProbePickerOpen(false);
    });
  }

  async function bootstrapClusterHub() {
    if (bootstrapClusterHub._running) return;
    bootstrapClusterHub._running = true;
    try {
      // 忙碌只留页顶 meta；摘要行留给结果/错误，避免「进页自动分组中」叠三处
      if (els.quantOlsSummary) {
        els.quantOlsSummary.textContent = "";
        els.quantOlsSummary.classList.remove("is-busy");
      }
      if (els.quantFactorList) els.quantFactorList.innerHTML = "";
      setQuantMeta("分组中…", { busy: true });
      await q.suggest.runFactorOlsClustersSuggest();
    } finally {
      bootstrapClusterHub._running = false;
    }
  }

  function clusterPoolBookAndArtifact() {
    const book =
      state.quantLastOlsClusters &&
      state.quantLastOlsClusters.pool_merge &&
      state.quantLastOlsClusters.pool_merge.book &&
      state.quantLastOlsClusters.pool_merge.book.book;
    const art =
      state.quantLastOlsClusters && state.quantLastOlsClusters.pool_artifact;
    return { book: book || [], artifact: art && art.success ? art : null };
  }

  function exportClusterWeightDiff(diff, label) {
    if (!diff || !diff.success) {
      setQuantMeta((diff && diff.error) || "该组无可用权重 diff", { error: true });
      return;
    }
    const note = diff.apply_note || "组权导出仅供人审";
    if (
      !window.confirm(
        `${label || diff.cluster_label || "该组"}：${note}\n\n仍导出 JSON 对照？`
      )
    ) {
      return;
    }
    state.quantLastWeightDiff = diff;
    const safe = String(diff.cluster_label || label || "cluster").replace(
      /[^\w\u4e00-\u9fff\-]+/g,
      "_"
    );
    downloadJson(diff, `signal_config_weight_diff_${safe}.json`);
    setQuantMeta(`已导出 ${diff.cluster_label || label || "组"} 权重 diff · 不写盘`);
  }

  function exportMultiScore() {
    const ms = state.quantLastOlsClusters && state.quantLastOlsClusters.multi_score;
    if (!ms || !ms.success) {
      setQuantMeta("无多权打分结果", { error: true });
      return;
    }
    downloadJson(ms, `cluster_multi_score_${Date.now()}.json`);
    setQuantMeta(
      `已导出多权分 · ${ms.scored_count ?? "—"} 只 · 仅组内序`
    );
  }

  function exportPoolArtifact() {
    const art =
      state.quantLastOlsClusters && state.quantLastOlsClusters.pool_artifact;
    if (!art || !art.success) {
      setQuantMeta("无可用映射产物（请先跑 β 分组）", { error: true });
      return;
    }
    if (
      !window.confirm(
        `${art.apply_note || "研究归档产物"}\n\n导出 JSON？不写 signal_config / 纸面。`
      )
    ) {
      return;
    }
    const stamp = String(art.created_at || "")
      .replace(/[^\d]/g, "")
      .slice(0, 14);
    downloadJson(
      art,
      `cluster_pool_artifact_${stamp || Date.now()}.json`
    );
    setQuantMeta(
      `已导出映射产物 · ${art.n_mapped_codes ?? "—"} 只 · promote_ready=否`
    );
  }

  function fillProbeCodeSelect(rows, preferredCode) {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return;
    bindProbePicker();
    const nextMap = {};
    state.probePickerRows = (rows || []).map((r) => {
      const code = normalizeProbeCode(r.code) || String(r.code || "").trim();
      const name = String(r.name || "").trim().replace(/\s+/g, "");
      const group = String(r.group || r.label || "").trim();
      const label = [name || null, code || null, group || null]
        .filter(Boolean)
        .join(" · ");
      nextMap[label] = code;
      nextMap[code] = code;
      if (name) nextMap[name] = code;
      return { code, name, group };
    });
    state.probeSelectValueToCode = nextMap;
    renderProbePickerMenu();
    if (!state.probePickerRows.length) {
      hidden.value = "";
      const trigger = document.getElementById("quant-ols-code-trigger");
      if (trigger) trigger.innerHTML = probePickerTriggerHtml(null);
      return;
    }
    const pref = normalizeProbeCode(
      resolveProbeInputToCode(preferredCode || hidden.value || "")
    );
    const hit = state.probePickerRows.find((r) => r.code === pref);
    applyProbePickerSelection(hit ? hit.code : state.probePickerRows[0].code, {
      silent: true,
    });
  }

  function findClusterForProbeCode(codeOrName) {
    const data = state.quantLastOlsClusters;
    if (!data || !data.success) return null;
    const key = normalizeProbeCode(resolveProbeInputToCode(codeOrName));
    if (!key) return null;
    const clusters = Array.isArray(data.clusters) ? data.clusters : [];
    for (const cl of clusters) {
      const members = (cl.members || []).map((m) => normalizeProbeCode(m));
      if (members.includes(key)) return cl;
    }
    // 名称模糊：成员里包含输入（少见）
    const lower = key.toLowerCase();
    for (const cl of clusters) {
      for (const m of cl.members || []) {
        if (String(m).toLowerCase().includes(lower)) return cl;
      }
    }
    return null;
  }


  function markProbeReadyFromClusters(data) {
    if (!els.quantProbeSummary) return;
    if (data && data.success) {
      const nCl = data.n_clusters != null ? data.n_clusters : "—";
      const nIn = data.stock_count != null ? data.stock_count : "—";
      setBusyText(
        els.quantProbeSummary,
        `${probeStatusBadge("ok", "就绪")} ${escapeHtml(String(nCl))} 组 · 入组 ${escapeHtml(
          String(nIn)
        )} · 选票后对照`,
        { busy: false, html: true }
      );
      return;
    }
    const err = (data && data.error) || "上方分组未成功";
    setBusyText(
      els.quantProbeSummary,
      `${probeStatusBadge("warn", "未就绪")} ${escapeHtml(err)} · 请先跑分组`,
      { busy: false, html: true }
    );
  }

  function onClusterExportClick(e) {
    const btn =
      e.target && e.target.closest
        ? e.target.closest("[data-cluster-export]")
        : null;
    if (!btn || !state.quantLastOlsClusters) return;
    e.preventDefault();
    e.stopPropagation(); // 勿触发组 details 折叠
    const key = btn.getAttribute("data-cluster-export");
    if (key === "preferred") {
      const pref = state.quantLastOlsClusters.preferred_cluster;
      exportClusterWeightDiff(pref && pref.config_diff, pref && pref.label);
      return;
    }
    if (key === "artifact") {
      exportPoolArtifact();
      return;
    }
    if (key === "paper-preview" || key === "paper-apply" || key === "live-paper") {
      setQuantMeta("调仓已收口到交易执行页 · 请打开 /follow", { error: true });
      return;
    }
    if (key === "multi-score") {
      exportMultiScore();
      return;
    }
    if (key === "multi-rescore") {
      runClusterMultiRescore();
      return;
    }
    if (key === "live-apply" || key === "live-promote") {
      runClusterLiveApply();
      return;
    }
    if (key === "live-shadow") {
      runClusterLiveMode("shadow");
      return;
    }
    if (key === "live-active") {
      runClusterLiveMode("active");
      return;
    }
    if (key === "live-off") {
      runClusterLiveMode("off");
      return;
    }
    if (key === "live-rank") {
      runClusterLiveRank();
      return;
    }
    if (key === "live-refresh") {
      runClusterLiveRefresh();
      return;
    }
    if (key === "live-rollback") {
      runClusterLiveRollback();
      return;
    }
    const idx = Number(key);
    const cl = (state.quantLastOlsClusters.clusters || [])[idx];
    if (!cl) return;
    exportClusterWeightDiff(cl.config_diff, cl.label);
  }


  async function populateOlsCodeOptions() {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return [];
    // 已有分组成员时由 syncProbe 填充，避免覆盖
    if (
      state.quantLastOlsClusters &&
      state.quantLastOlsClusters.success &&
      Array.isArray(state.quantLastOlsClusters.clusters) &&
      state.quantLastOlsClusters.clusters.length
    ) {
      return [];
    }
    const seen = new Set();
    const rows = [];
    const push = (code, name) => {
      const c = String(code || "").trim();
      if (!c || seen.has(c)) return;
      seen.add(c);
      rows.push({ code: c, name: String(name || "").trim() });
    };
    push("600519", "贵州茅台");
    try {
      const res = await fetch("/api/watching");
      const data = await res.json();
      const uni = (data && data.watching) || data || {};
      const wl = uni.watchlist || data.watchlist || [];
      const names = Array.isArray(uni.watchlist_names)
        ? uni.watchlist_names
        : Array.isArray(data.watchlist_names)
          ? data.watchlist_names
          : [];
      for (let i = 0; i < wl.length; i++) {
        const item = wl[i];
        if (typeof item === "string") {
          push(item, names[i] || state.watchingNameByCode[item] || "");
        } else if (item && typeof item === "object") {
          push(item.code || item.stock_code, item.name || item.stock_name || names[i] || "");
        }
      }
    } catch (_) {
      /* 离线/未建池时仍保留茅台选项 */
    }
    fillProbeCodeSelect(rows, hidden.value);
    return rows;
  }

  async function postClusterLive(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok || !data || data.success === false) {
      return {
        ok: false,
        error: formatClusterApiError(data, res.status),
        data,
      };
    }
    return { ok: true, data };
  }

  async function postClusterPaperRebalance({ confirm }) {
    const { book, artifact } = clusterPoolBookAndArtifact();
    if (!book.length) {
      return { ok: false, error: "无分池候选簿" };
    }
    const body = {
      book,
      top_k: book.length,
      confirm: !!confirm,
    };
    if (artifact) {
      body.artifact = {
        schema_version: artifact.schema_version,
        created_at: artifact.created_at,
        n_clusters: artifact.n_clusters,
        n_mapped_codes: artifact.n_mapped_codes,
      };
    }
    const res = await fetch("/api/quant/cluster-paper-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
        (res.status === 404
          ? "接口未找到：请重启 Web"
          : `HTTP ${res.status}`);
      return { ok: false, error: err };
    }
    return { ok: true, data };
  }


  function readOlsCode() {
    const el = document.getElementById("quant-ols-code");
    const raw = el && el.value != null ? String(el.value).trim() : "";
    if (!raw) return "茅台";
    if (/^\d{6}$/.test(normalizeProbeCode(raw))) {
      return normalizeProbeCode(raw);
    }
    return resolveProbeInputToCode(raw) || "茅台";
  }

  async function refreshClusterLiveStatus() {
    try {
      const res = await fetch("/api/quant/cluster-live/status");
      const data = await res.json();
      // await 后重取节点：分组重绘会替换 #quant-cluster-landing，旧引用已脱离 DOM
      const host = document.getElementById("quant-cluster-landing");
      if (!host) return;
      if (!data || !data.success) {
        host.innerHTML = `<p class="sub">落地状态不可用</p>`;
        return;
      }
      host.innerHTML = clusterLandingHtml(data);
    } catch (_) {
      const host = document.getElementById("quant-cluster-landing");
      if (host) host.innerHTML = `<p class="sub">落地状态加载失败</p>`;
    }
  }

  async function refreshCrossSection() {
    if (!els.quantCrossSummary && !els.quantCrossList) return null;
    setBusyText(els.quantCrossSummary, "排序中…", { busy: true });
    if (els.quantCrossList) els.quantCrossList.innerHTML = "";
    const res = await fetch("/api/quant/cross-section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 10, horizon_days: readHorizonDays() }),
    });
    const data = await res.json();
    renderCrossSection(data);
    return data;
  }

  function wireClusterFactorLazyHydrate() {
    if (!els.quantFactorList || els.quantFactorList.dataset.lazyWired === "1") return;
    els.quantFactorList.dataset.lazyWired = "1";
    els.quantFactorList.addEventListener(
      "toggle",
      (e) => {
        const details = e.target;
        if (!details || details.tagName !== "DETAILS" || !details.open) return;
        const body = details.querySelector("[data-cluster-lazy]");
        if (body) hydrateClusterGroupBody(body);
      },
      true
    );
  }

  function hydrateClusterGroupBody(bodyEl) {
    if (!bodyEl || bodyEl.dataset.clusterHydrated === "1") return;
    const idx = Number(bodyEl.getAttribute("data-cluster-lazy"));
    const data = state.quantLastOlsClusters;
    const cl = data && Array.isArray(data.clusters) ? data.clusters[idx] : null;
    if (!cl || typeof buildClusterGroupBodyHtml !== "function") return;
    bodyEl.innerHTML = buildClusterGroupBodyHtml(cl, {
      lastFactorPanelForMerge: state.lastFactorPanelForMerge,
    });
    bodyEl.dataset.clusterHydrated = "1";
    bodyEl.removeAttribute("data-cluster-lazy");
  }

  function renderClusterFactorTables() {
    if (!els.quantFactorList) return false;
    const html = buildClusterFactorTablesHtml(state.quantLastOlsClusters, {
      lastFactorPanelForMerge: state.lastFactorPanelForMerge,
      lazyTables: true,
    });
    if (!html) return false;
    wireClusterFactorLazyHydrate();
    els.quantFactorList.innerHTML = html;
    return true;
  }

  function renderCrossSection(data) {
    if (!els.quantCrossSummary && !els.quantCrossList) return;
    const result = buildCrossSectionResult(data);
    setBusyText(els.quantCrossSummary, result.summary, { busy: false });
    if (els.quantCrossList) els.quantCrossList.innerHTML = result.listHtml;
  }

  function renderFactorOls(data) {
    if (!els.quantOlsSummary && !els.quantFactorList && !els.quantProbeResult) return;
    const summaryHost =
      state.quantLastOlsClusters && state.quantLastOlsClusters.success && els.quantProbeSummary
        ? els.quantProbeSummary
        : els.quantOlsSummary;
    if (!data || !data.success) {
      state.lastOlsForMerge = null;
      setBusyText(
        summaryHost,
        (data && data.error) || "OLS 摘要将显示在此",
        { busy: false }
      );
      renderMergedFactorTable();
      return;
    }
    state.lastOlsForMerge = data;
    const pooled = data.mode === "watching_pooled" || data.task === "factor_ols_pool";
    const codes = Array.isArray(data.stock_codes) ? data.stock_codes.filter(Boolean) : [];
    const codeNote = codes.length ? ` · ${codes.join("、")}` : "";
    const zNote = data.standardized ? " · z-score β" : "";
    const lam = Number(data.ridge_lambda);
    const ridgeNote =
      Number.isFinite(lam) && lam > 0
        ? ` · Ridge λ=${lam}`
        : data.solver === "qr"
          ? " · QR"
          : "";
    let olsText = pooled
      ? `探针·池内 OLS · ${data.stock_count ?? codes.length ?? "—"} 只${codeNote} · R²=${data.r_squared ?? "—"} · n=${data.sample_count ?? "—"} · 全量因子${zNote}${ridgeNote} · 不写 config`
      : `探针·OLS · ${data.stock_code || ""} · R²=${data.r_squared ?? "—"} · n=${data.sample_count ?? "—"} · 全量因子${zNote}${ridgeNote} · 不写 config`;
    if ((data.excluded_features || []).length) {
      olsText += ` · 未入模 ${data.excluded_features.length}（缺测/常数/覆盖）`;
    }
    setBusyText(summaryHost, olsText, { busy: false });
    renderMergedFactorTable();
  }

  function renderMergedFactorTable() {
    // 已有分组：主因子区保持一组一表；探针结果写到探针区
    if (state.quantLastOlsClusters && state.quantLastOlsClusters.success) {
      renderClusterFactorTables();
      if (els.quantProbeResult) renderProbeFactorTable();
      return;
    }
    if (!els.quantFactorList) return;
    if (renderClusterFactorTables()) return;
    const html = factorIcWeightMergedHtml(
      state.lastFactorPanelForMerge,
      state.lastWeightSuggestForMerge,
      state.lastOlsForMerge
    );
    els.quantFactorList.innerHTML =
      html ||
      `<p class="watching-table-empty">点「跑分组」生成一组一表</p>`;
  }

  function renderOlsClusters(data) {
    if (!els.quantOlsClusters) return;
    state.quantLastOlsClusters = data && data.success ? data : null;
    markProbeReadyFromClusters(data);
    const summary = buildOlsClustersSummaryHtml(data);
    els.quantOlsClusters.innerHTML = summary.html;
    if (!summary.ok) {
      try {
        renderMergedFactorTable();
      } catch (_) {
        /* keep error summary visible */
      }
      return;
    }
    renderMergedFactorTable();
    try {
      syncProbeCodeOptionsFromClusters();
    } catch (err) {
      console.warn("[cluster] syncProbeCodeOptionsFromClusters", err);
    }
    refreshClusterLiveStatus();
  }

  function renderProbeFactorTable() {
    const host = els.quantProbeResult;
    if (!host) return;
    const html = factorIcWeightMergedHtml(
      state.lastFactorPanelForMerge,
      state.lastWeightSuggestForMerge,
      state.lastOlsForMerge
    );
    host.innerHTML =
      html || `<p class="watching-table-empty">暂无探针对照</p>`;
  }

  function renderProbePickerMenu() {
    const menu = document.getElementById("quant-ols-code-menu");
    if (!menu) return;
    if (!state.probePickerRows.length) {
      menu.innerHTML =
        `<div class="quant-probe-picker-empty-row">暂无分组成员</div>`;
      return;
    }
    const head =
      `<div class="quant-probe-picker-head" aria-hidden="true">` +
      `<span>标的</span><span>分组</span>` +
      `</div>`;
    const cur = normalizeProbeCode(
      (document.getElementById("quant-ols-code") || {}).value || ""
    );
    const body = state.probePickerRows
      .map((r) => {
        const selected = r.code === cur;
        return (
          `<button type="button" role="option" class="quant-probe-picker-option` +
          `${selected ? " is-selected" : ""}"` +
          ` data-code="${escapeHtml(r.code)}"` +
          ` aria-selected="${selected ? "true" : "false"}">` +
          probePickerIdentityHtml(r) +
          `<span class="quant-probe-picker-group">${escapeHtml(
            r.group || "—"
          )}</span>` +
          `</button>`
        );
      })
      .join("");
    menu.innerHTML = head + body;
  }

  function renderProbeStockVsGroupTable(stockExp, stockOls, cluster) {
    const host = els.quantProbeResult;
    if (!host) return;
    const { html, rows } = renderProbeStockVsGroupTableHtml(stockExp, stockOls, cluster);
    host.innerHTML = html;
    return rows;
  }

  function resolveProbeInputToCode(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    if (state.probeSelectValueToCode[s]) return state.probeSelectValueToCode[s];
    const main = s.split(/[·|｜]/)[0].trim();
    if (state.probeSelectValueToCode[main]) return state.probeSelectValueToCode[main];
    const m = s.match(/\b(\d{6})\b/);
    if (m) return normalizeProbeCode(m[1]);
    const bare = normalizeProbeCode(main);
    if (/^\d{6}$/.test(bare)) return bare;
    const want = main.replace(/\s+/g, "");
    const nameByCode = clusterNameByCodeFromData(state.quantLastOlsClusters);
    for (const [c, nm] of Object.entries(nameByCode || {})) {
      if (String(nm || "").replace(/\s+/g, "") === want) return normalizeProbeCode(c);
    }
    for (const [c, nm] of Object.entries(state.watchingNameByCode || {})) {
      if (String(nm || "").replace(/\s+/g, "") === want) return normalizeProbeCode(c);
    }
    return main || s;
  }

  async function runClusterLiveApply() {
    const art =
      state.quantLastOlsClusters && state.quantLastOlsClusters.pool_artifact;
    if (
      !window.confirm(
        [
          "进入对照（shadow）？",
          "",
          "将执行：",
          "· 晋升当前分组映射为 live",
          "· 模式切换为 shadow（对照）",
          "· 按组ŷ 刷新分池目标簿",
          "",
          "说明：",
          "· 交易执行页选股仍用现行规则，直至「② 启用」",
          "· 不写入 signal_config.weights",
          "· 可随时「关闭 / 回滚」撤销",
        ].join("\n")
      )
    ) {
      return;
    }
    setQuantMeta("正在进入对照…", { busy: true });
    const body = { from_draft: true, mode: "shadow", note: "落地·对照" };
    if (art && art.success && art.code_map) {
      body.artifact = art;
      body.from_draft = false;
    }
    const out = await postClusterLive("/api/quant/cluster-live/apply", body);
    if (!out.ok) {
      setQuantMeta(`对照未完成 · ${out.error}`, { error: true });
      return;
    }
    const n =
      (out.data.refresh &&
        out.data.refresh.rank &&
        out.data.refresh.rank.name_count) ||
      0;
    setQuantMeta(
      `已进入对照（shadow）` +
        (n ? ` · 目标簿 ${n} 只` : "") +
        ` · 启用前交易执行选股未切换`
    );
    refreshClusterLiveStatus();
  }

  async function runClusterLiveMode(mode) {
    let confirmMsg = [
      `切换分组 live 模式为「${mode}」？`,
      "",
      "仅变更选股开关，不改写 signal_config.weights。",
    ].join("\n");
    if (mode === "active") {
      try {
        const stRes = await fetch("/api/quant/cluster-live/status");
        const st = await stRes.json();
        const ev = (st && st.enable_evidence) || {};
        const oos = ev.oos_summary || {};
        const turn = ev.turnover_est || {};
        const cov =
          ev.health && ev.health.coverage != null
            ? `${Math.round(Number(ev.health.coverage) * 100)}%`
            : "—";
        confirmMsg = [
          "启用组ŷ选股？",
          "",
          "证据摘要：",
          `· 目标簿 ${ev.name_count ?? "—"} 只 · 上限 ${ev.max_names ?? "—"}`,
          `· OOS（heuristic 基线 vs ŷ）：通过 ${oos.pass_count ?? 0} · 失败 ${oos.fail_count ?? 0}`,
          `· 相对当前纸面：约卖 ${turn.would_sell_count ?? 0} · 买 ${turn.would_buy_count ?? 0}`,
          `· 映射健康覆盖 ${cov}`,
          "",
          "启用后：",
          "· 交易执行页「预演调仓」将按组ŷ 排序与目标簿执行",
          "· 不写入 signal_config.weights",
          "· 过门 ≠ 自动 promote；可随时关闭或回滚",
        ].join("\n");
        if (ev.gate && ev.gate.ok === false) {
          setQuantMeta(
            `启用受阻 · ${(ev.gate.blockers || []).join("；") || "证据包未通过，见落地卡"}`,
            { error: true }
          );
          refreshClusterLiveStatus();
          return;
        }
      } catch (_) {
        /* 仍走后端门禁 */
      }
    } else if (mode === "off") {
      confirmMsg = [
        "关闭分组 live？",
        "",
        "交易执行页将回到非组ŷ选股路径。",
        "已晋升的映射文件保留，可再次对照 / 启用。",
        "不改写 signal_config.weights。",
      ].join("\n");
    } else if (mode === "shadow") {
      confirmMsg = [
        "切回对照（shadow）？",
        "",
        "映射与目标簿保留；交易执行页选股暂不吃组ŷ。",
        "不改写 signal_config.weights。",
      ].join("\n");
    }
    if (!window.confirm(confirmMsg)) {
      return;
    }
    const busyLabel =
      mode === "active"
        ? "正在启用组ŷ…"
        : mode === "off"
          ? "正在关闭分组 live…"
          : `正在切换 mode=${mode}…`;
    setQuantMeta(busyLabel, { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/mode", { mode });
    if (!out.ok) {
      setQuantMeta(`模式未变更 · ${out.error}`, { error: true });
      return;
    }
    const doneLabel =
      mode === "active"
        ? "已启用组ŷ · 交易执行预演将按组ŷ 选股"
        : mode === "off"
          ? "已关闭分组 live"
          : mode === "shadow"
            ? "已切回对照（shadow）"
            : `分组 live mode=${mode}`;
    setQuantMeta(doneLabel);
    refreshClusterLiveStatus();
  }

  async function runClusterLivePromote() {
    // 兼容旧入口：走一键应用
    return runClusterLiveApply();
  }

  async function runClusterLiveRank() {
    setQuantMeta("分池排序中…", { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/rank", {});
    if (!out.ok) {
      setQuantMeta(`分池排序失败 · ${out.error}`, { error: true });
      return;
    }
    const n = (out.data.book || []).length;
    setQuantMeta(`分池簿 ${n} 只 · v${out.data.cluster_version ?? "—"} · 无跨组总榜`);
    refreshClusterLiveStatus();
  }

  async function runClusterLiveRefresh() {
    setQuantMeta("刷新合并簿…", { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/refresh-book", {});
    if (!out.ok) {
      setQuantMeta(`刷新失败 · ${out.error}`, { error: true });
      return;
    }
    const n = (out.data.rank && out.data.rank.name_count) || 0;
    setQuantMeta(`日更完成 · 簿 ${n} 只 · 未重聚类`);
    refreshClusterLiveStatus();
  }

  async function runClusterLiveRollback() {
    if (
      !window.confirm(
        [
          "回滚 live 分组映射？",
          "",
          "将恢复上一版已晋升的 code_map / return_model。",
          "当前对照或启用状态可能随之变化。",
          "不改写 signal_config.weights。",
        ].join("\n")
      )
    ) {
      return;
    }
    setQuantMeta("正在回滚映射…", { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/rollback", {});
    if (!out.ok) {
      setQuantMeta(`回滚未完成 · ${out.error}`, { error: true });
      return;
    }
    setQuantMeta(`已回滚` + (out.data.version != null ? ` · 现为 v${out.data.version}` : ""));
    refreshClusterLiveStatus();
  }

  async function runClusterMultiRescore() {
    const art =
      state.quantLastOlsClusters && state.quantLastOlsClusters.pool_artifact;
    setQuantMeta("多权复打中…", { busy: true });
    try {
      const body = {
        lookback: 80,
        horizon_days: readHorizonDays(),
        watching_limit: 20,
      };
      if (art && art.success && art.code_map) {
        body.artifact = {
          code_map: art.code_map,
          created_at: art.created_at,
          schema_version: art.schema_version,
        };
      }
      const res = await fetch("/api/quant/cluster-multi-score", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
          (res.status === 404
            ? "接口未找到：请重启 Web"
            : `HTTP ${res.status}`);
        setQuantMeta(`多权复打失败 · ${err}`, { error: true });
        return;
      }
      if (state.quantLastOlsClusters) {
        state.quantLastOlsClusters.multi_score = data;
        renderOlsClusters(state.quantLastOlsClusters);
      }
      setQuantMeta(
        `多权复打完成 · ${data.scored_count ?? "—"} 只 · 仅组内序 · 不进 live`
      );
      if (els.quantOlsSummary) {
        setBusyText(
          els.quantOlsSummary,
          `多权复打 · ${data.scored_count ?? "—"} 只 · ${data.source || "—"}`,
          { busy: false }
        );
      }
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    }
  }

  async function runClusterPaperApply() {
    setQuantMeta("调仓已收口到交易执行页 · 请打开 /follow", { error: true });
  }

  async function runClusterPaperPreview() {
    setQuantMeta("调仓已收口到交易执行页 · 请打开 /follow", { error: true });
  }

  async function runProbeStockVsGroup() {
    const code = readOlsCode();
    const fold = document.getElementById("quant-probe-fold");
    if (fold) fold.open = true;
    if (!state.quantLastOlsClusters || !state.quantLastOlsClusters.success) {
      if (bootstrapClusterHub._running) {
        setBusyText(els.quantProbeSummary, "上方分组进行中，请稍候…", {
          busy: true,
        });
        const ok = await waitForClusterHubReady(180000);
        if (!ok) {
          markProbeReadyFromClusters(state.quantLastOlsClusters);
          if (els.quantProbeResult) {
            els.quantProbeResult.innerHTML =
              `<p class="watching-table-empty">分组未完成或失败，无法对照</p>`;
          }
          return;
        }
      } else {
        markProbeReadyFromClusters(state.quantLastOlsClusters);
        if (els.quantProbeResult) {
          els.quantProbeResult.innerHTML =
            `<p class="watching-table-empty">尚无分组结果 · 请先点「跑分组」</p>`;
        }
        return;
      }
    }
    setBusyText(
      els.quantProbeSummary,
      `对照中… · ${escapeHtml(code)}`,
      { busy: true }
    );
    await ensureFactorMeta();
    const resolvedEarly = normalizeProbeCode(code);
    const clEarly = findClusterForProbeCode(resolvedEarly);
    // 单票组：不二次打 OLS；表内单票β=组β=分组落盘
    if (clEarly && isProbeSingletonCluster(clEarly)) {
      const resolved = resolvedEarly;
      const cl = clEarly;
      renderProbeStockVsGroupTable(null, null, cl);
      const r2g =
        cl.ols && cl.ols.r_squared != null ? cl.ols.r_squared : "—";
      const badge = cl.outlier_singleton
        ? probeStatusBadge("warn", "离群单票组")
        : probeStatusBadge("muted", "单票组");
      setBusyText(
        els.quantProbeSummary,
        `${badge} ${escapeHtml(resolved)} ∈ ${escapeHtml(
          cl.label || "?"
        )} · 组模型=自身 · R²=${escapeHtml(String(r2g))}`,
        { busy: false, html: true }
      );
      setQuantMeta(
        `探针 · ${resolved} 单票组 · 同源β · 不冲分组表`
      );
      return;
    }
    const h =
      Number(
        (state.quantLastOlsClusters && state.quantLastOlsClusters.horizon_days) ||
          readHorizonDays()
      ) || readHorizonDays();
    const ridgeRaw = Number(
      state.quantLastOlsClusters && state.quantLastOlsClusters.ridge_lambda
    );
    const ridge = Number.isFinite(ridgeRaw)
      ? ridgeRaw
      : readRidgeLambda();
    // 与「跑分组」同窗 / 同 ridge，避免假异质
    const clusterLb = Number(
      (state.quantLastOlsClusters && state.quantLastOlsClusters.lookback) || 80
    );
    const probeLb =
      Number.isFinite(clusterLb) && clusterLb > 0 ? clusterLb : 80;
    const [expRes, olsRes] = await Promise.all([
      fetch("/api/quant/factor-experiment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, lookback: probeLb, horizon_days: h }),
      }),
      fetch("/api/quant/factor-ols", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code,
          lookback: probeLb,
          horizon_days: h,
          ridge_lambda: ridge,
        }),
      }),
    ]);
    let exp = null;
    let ols = null;
    try {
      exp = await expRes.json();
    } catch (_) {
      exp = null;
    }
    try {
      ols = await olsRes.json();
    } catch (_) {
      ols = null;
    }
    const resolved = normalizeProbeCode(
      (ols && ols.stock_code) || (exp && exp.stock_code) || code
    );
    const cl = findClusterForProbeCode(resolved);
    if ((!exp || exp.success === false) && (!ols || ols.success === false)) {
      setBusyText(
        els.quantProbeSummary,
        (exp && exp.error) || (ols && ols.error) || "单票 IC/OLS 失败",
        { busy: false }
      );
      if (els.quantProbeResult) {
        els.quantProbeResult.innerHTML = `<p class="watching-table-empty">对照失败</p>`;
      }
      return;
    }
    const probeRows = renderProbeStockVsGroupTable(exp, ols, cl);
    if (!cl) {
      setBusyText(
        els.quantProbeSummary,
        `已算单票 ${resolved} · 未落入当前任一组（可能不在观察池宇宙/数据不足）`,
        { busy: false }
      );
      setQuantMeta(`探针 · ${resolved} 未入组`);
      return;
    }
    const r2s = ols && ols.r_squared != null ? ols.r_squared : "—";
    const r2g =
      cl.ols && cl.ols.r_squared != null ? cl.ols.r_squared : "—";
    const nMem = Number(cl.member_count);
    const memLabel = Number.isFinite(nMem) ? `${nMem}只` : "?只";
    if (isProbeSingletonCluster(cl)) {
      const badge = cl.outlier_singleton
        ? probeStatusBadge("warn", "离群单票组")
        : probeStatusBadge("muted", "单票组");
      setBusyText(
        els.quantProbeSummary,
        `${badge} ${escapeHtml(resolved)} ∈ ${escapeHtml(
          cl.label || "?"
        )} · 组模型=自身 · R²=${escapeHtml(String(r2g))}`,
        { busy: false, html: true }
      );
      setQuantMeta(
        `探针 · ${resolved} 单票组 · 同源β · 不冲分组表`
      );
      return;
    }
    const heteroN = Array.isArray(probeRows)
      ? probeRows.filter((r) => r && r.flag).length
      : 0;
    const maxAbsDb = Array.isArray(probeRows)
      ? probeRows.reduce((acc, r) => {
          if (r && r.dB != null && Number.isFinite(Number(r.dB))) {
            return Math.max(acc, Math.abs(Number(r.dB)));
          }
          return acc;
        }, 0)
      : 0;
    const unfit = heteroN >= 2 || maxAbsDb >= 0.4;
    const badge = unfit
      ? probeStatusBadge("warn", "异质")
      : probeStatusBadge("ok", "可共用");
    const detail = unfit
      ? `异质${heteroN} · max|Δβ|=${maxAbsDb.toFixed(2)}`
      : `异质${heteroN}`;
    setBusyText(
      els.quantProbeSummary,
      `${badge} ${escapeHtml(resolved)} ∈ ${escapeHtml(
        cl.label || "?"
      )} · ${escapeHtml(memLabel)} · R² ${escapeHtml(
        String(r2s)
      )}/${escapeHtml(String(r2g))} · ${escapeHtml(detail)}`,
      { busy: false, html: true }
    );
    setQuantMeta(
      unfit
        ? `探针 · ${resolved} 相对 ${cl.label || "组"} 异质偏大 · 建议视为离群`
        : `探针对照 · ${resolved} vs ${cl.label || "组"} · 不冲分组表`
    );
  }

  function setProbePickerOpen(open) {
    const root = document.getElementById("quant-probe-picker");
    const trigger = document.getElementById("quant-ols-code-trigger");
    const menu = document.getElementById("quant-ols-code-menu");
    if (!root || !trigger || !menu) return;
    const on = !!open && state.probePickerRows.length > 0;
    root.classList.toggle("is-open", on);
    trigger.setAttribute("aria-expanded", on ? "true" : "false");
    menu.hidden = !on;
  }

  function syncProbeCodeOptionsFromClusters() {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return;
    const data = state.quantLastOlsClusters;
    const clusters = data && data.success ? data.clusters || [] : [];
    const nameByCode = clusterNameByCodeFromData(data);
    const seen = new Set();
    const rows = [];
    for (const cl of clusters) {
      const gLabel = cl.label || `G${(cl.cluster_id ?? 0) + 1}`;
      for (const m of cl.members || []) {
        const c = normalizeProbeCode(m);
        if (!c || seen.has(c)) continue;
        seen.add(c);
        const nm = String(nameByCode[c] || state.watchingNameByCode[c] || "")
          .trim()
          .replace(/\s+/g, "");
        rows.push({ code: c, name: nm, group: gLabel });
      }
    }
    if (rows.length) fillProbeCodeSelect(rows, hidden.value);
  }

  async function waitForClusterHubReady(maxMs) {
    const limit = Math.max(1000, Number(maxMs) || 120000);
    const t0 = Date.now();
    while (Date.now() - t0 < limit) {
      if (state.quantLastOlsClusters && state.quantLastOlsClusters.success) return true;
      if (!bootstrapClusterHub._running) {
        // 未在跑且仍无成功结果 → 失败或未触发
        return !!(state.quantLastOlsClusters && state.quantLastOlsClusters.success);
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    return !!(state.quantLastOlsClusters && state.quantLastOlsClusters.success);
  }

  function wireOosGateTips(host) {
    if (!host) return;
    oosGateTips.bindAttrTip(host, {
      selector: "[data-oos-gate]",
      className: "score-tooltip oos-gate-tip",
      buildHtml: (el) => {
        try {
          return oosGateTipHtml(
            JSON.parse(el.getAttribute("data-oos-gate") || "{}")
          );
        } catch (_) {
          return "";
        }
      },
    });
  }

  return {
    applyProbePickerSelection,
    bindProbePicker,
    bootstrapClusterHub,
    clusterPoolBookAndArtifact,
    exportClusterWeightDiff,
    exportMultiScore,
    exportPoolArtifact,
    fillProbeCodeSelect,
    findClusterForProbeCode,
    formatClusterApiError,
    markProbeReadyFromClusters,
    onClusterExportClick,
    oosGateStatusMeta,
    populateOlsCodeOptions,
    postClusterLive,
    postClusterPaperRebalance,
    probeFactorRowsFromExp,
    probeIcFieldsFromRow,
    probeIcMapFromExperiment,
    probeIcMapFromGroupPanel,
    probeStatusBadge,
    readOlsCode,
    refreshClusterLiveStatus,
    refreshCrossSection,
    renderClusterFactorTables,
    renderCrossSection,
    renderFactorOls,
    renderMergedFactorTable,
    renderOlsClusters,
    renderProbeFactorTable,
    renderProbePickerMenu,
    renderProbeStockVsGroupTable,
    resolveProbeInputToCode,
    runClusterLiveApply,
    runClusterLiveMode,
    runClusterLivePromote,
    runClusterLiveRank,
    runClusterLiveRefresh,
    runClusterLiveRollback,
    runClusterMultiRescore,
    runClusterPaperApply,
    runClusterPaperPreview,
    runProbeStockVsGroup,
    setProbePickerOpen,
    syncProbeCodeOptionsFromClusters,
    waitForClusterHubReady,
    wireOosGateTips,
  };
}
