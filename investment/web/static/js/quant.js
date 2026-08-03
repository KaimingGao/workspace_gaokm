import {
  escapeHtml,
  renderReadmeLinksHtml,
  attachReadmeLinkHandler,
  postQuantCiEval,
  downloadBlob,
  downloadJson,
} from "./shared.js";
import { apiFetch } from "./api_client.js";
import { renderLineChart, renderDualLineChart, renderMultiLineChart } from "./lw_charts.js";
import { mountJsonEditor, setJsonEditorValue, getJsonEditorValue } from "./monaco_spec.js";
import { mountVirtualTable, colStyle } from "./virtual_table.js";
import { createScoreTooltipController } from "./score_tooltip.js";

/** Quant research panel. */
export function initQuant(ctx) {
  let lastBacktestPack = null;
  let lastParamGrid = null;
  /** @type {'frozen'|'live'|null} */
  let neutralCompareSource = null;

  const BT_SCOPE_LIVE =
    "口径：日线 PIT + 可选财务；不含舆情加减分；无 live 质量门禁（thin/fallback 仍可能进分）。" +
    "成本按换手计费。有效≠正确：先看上方 IC/分层/超额，再解读 Top-K 累计收益。";
  const BT_SCOPE_FROZEN =
    "以下为 quant_daily 冻结摘要，不是刚才点的 Top-K；点「Top-K 回测」或「中性化对照」刷新当次结果。";
  let btTradesTableApi = null;
  /** code → 中文名（观察池加载时更新，归因表截断用） */
  let watchingNameByCode = {};
  /** 探针 picker：展示文案 → 代码；行缓存 */
  let probeSelectValueToCode = {};
  let probePickerRows = [];
  /** 数据中心当前点选（图表/资讯）的股票 */
  let watchingFocusCode = null;
  let watchingFocusName = "";

  function on(id, type, handler) {
    const el = typeof id === "string" ? document.getElementById(id) : id;
    if (!el) return null;
    el.addEventListener(type, handler);
    return el;
  }

  /** 观察表股票名：至少完整展示前 max 字，超出才加省略号；全名走 title / data-full-name */
  function truncateStockName(name, max = 6) {
    const full = String(name || "").trim();
    const chars = Array.from(full);
    if (chars.length <= max) return { display: full, full };
    return { display: `${chars.slice(0, max).join("")}…`, full };
  }

  function watchingNameSpanHtml(name) {
    const { display, full } = truncateStockName(name);
    return (
      `<span class="watching-name-text" title="${escapeHtml(full)}" data-full-name="${escapeHtml(full)}">` +
      `${escapeHtml(display)}</span>`
    );
  }

  function watchingNameFromEl(nameEl, fallback = "") {
    if (!nameEl) return fallback;
    const full = (nameEl.dataset.fullName || nameEl.getAttribute("title") || "").trim();
    return full || nameEl.textContent.trim() || fallback;
  }

  function applyWatchingNameEl(nameEl, name) {
    if (!nameEl) return;
    const { display, full } = truncateStockName(name);
    nameEl.textContent = display;
    nameEl.title = full;
    nameEl.dataset.fullName = full;
  }

  /** 因子定义（来自 /api/quant/factors），悬停 title 展示 */
  let factorMetaByName = {};
  let factorMetaByLabel = {};
  let factorMetaPromise = null;

  function rememberFactorMeta(list) {
    const byName = {};
    const byLabel = {};
    (list || []).forEach((f) => {
      if (!f || !f.name) return;
      byName[f.name] = f;
      if (f.label) byLabel[f.label] = f;
    });
    factorMetaByName = byName;
    factorMetaByLabel = byLabel;
  }

  async function ensureFactorMeta() {
    if (Object.keys(factorMetaByName).length) return factorMetaByName;
    if (factorMetaPromise) return factorMetaPromise;
    factorMetaPromise = (async () => {
      try {
        const res = await fetch("/api/quant/factors");
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

  function factorDescription(name, label) {
    const meta =
      (name && factorMetaByName[name]) ||
      (label && factorMetaByLabel[label]) ||
      (name && factorMetaByLabel[name]) ||
      {};
    return String(meta.description || "").trim();
  }

  /** 因子名单元格：悬停显示定义注释 */
  function factorNameCellHtml(name, label) {
    const display = label || name || "—";
    const tip = factorDescription(name, label);
    const text = escapeHtml(String(display));
    if (!tip) return text;
    return `<span class="factor-tip" title="${escapeHtml(tip)}">${text}</span>`;
  }

  const quantDialog = document.getElementById("quant-dialog");
  const quantMeta = document.getElementById("quant-meta");
  const quantWatchingMeta = document.getElementById("quant-watching-meta");
  const quantWatchingList = document.getElementById("quant-watching-list");
  const quantSignalSummary = document.getElementById("quant-signal-summary");
  const quantSignalTable = document.getElementById("quant-signal-table");
  const quantSignalConfig = document.getElementById("quant-signal-config");
  const strategyList = document.getElementById("strategy-list");
  const strategyListLoading = document.getElementById("strategy-list-loading");
  const quantDiffSummary = document.getElementById("quant-diff-summary");
  const quantDiffTable = document.getElementById("quant-diff-table");
  const quantCrossSummary = document.getElementById("quant-cross-summary");
  const quantCrossList = document.getElementById("quant-cross-list");
  const quantFactorList = document.getElementById("quant-factor-list");
  const quantWeightSuggest = document.getElementById("quant-weight-suggest");
  const quantWeightTable = document.getElementById("quant-weight-table");
  const quantOlsSummary = document.getElementById("quant-ols-summary");
  const quantOlsClusters = document.getElementById("quant-ols-clusters");
  const quantProbeSummary = document.getElementById("quant-probe-summary");
  const quantProbeResult = document.getElementById("quant-probe-result");
  const quantOlsTable = document.getElementById("quant-ols-table"); // 兼容旧 DOM；已并入 quant-factor-list
  const quantPortfolioSummary = document.getElementById("quant-portfolio-summary");
  const quantBtProgress = document.getElementById("quant-bt-progress");
  const quantBtProgressText = document.getElementById("quant-bt-progress-text");
  const quantBtMetrics = document.getElementById("quant-bt-metrics");
  const quantBtTrades = document.getElementById("quant-bt-trades");
  const quantNeutralCompareTable = document.getElementById("quant-neutral-compare-table");
  const quantPortfolioChart = document.getElementById("quant-portfolio-chart");
  // 做 T 展示面在纸面 Tab（执行面）；Agent 产物仍可写回这些节点
  const quantT0Summary = document.getElementById("paper-t0-summary");
  const quantT0Metrics = document.getElementById("paper-t0-metrics");
  const quantT0Days = document.getElementById("paper-t0-days");
  const quantThresholdSummary = document.getElementById("quant-threshold-summary");
  const quantThresholdTable = document.getElementById("quant-threshold-table");
  const quantInterpretBody = document.getElementById("quant-interpret-body");
  const quantInterpretNeutral = document.getElementById("quant-interpret-neutral");
  const quantExportPreviewMeta = document.getElementById("quant-export-preview-meta");
  const quantExportPreviewToc = document.getElementById("quant-export-preview-toc");
  const quantExportPreviewBody = document.getElementById("quant-export-preview-body");
  const quantOpsSummary = document.getElementById("quant-ops-summary");
  const quantOpsPackage = document.getElementById("quant-ops-package");
  const quantOpsPreset = document.getElementById("quant-ops-preset");
  const quantOpsPresetFlags = document.getElementById("quant-ops-preset-flags");
  const readmeDialog = document.getElementById("readme-dialog");
  const readmeTitle = document.getElementById("readme-title");
  const readmeMeta = document.getElementById("readme-meta");
  const readmeBody = document.getElementById("readme-body");
  const readmeDocLinks = document.getElementById("readme-doc-links");

  const quantBtBusyIds = [
    "quant-portfolio-run",
    "quant-portfolio-compare",
  ];

  function setQuantBtBusy(busy, message) {
    if (quantBtProgress) {
      quantBtProgress.hidden = !busy;
      if (busy) {
        try {
          quantBtProgress.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (_) {
          /* ignore */
        }
      }
    }
    if (quantBtProgressText && message) quantBtProgressText.textContent = message;
    for (const id of quantBtBusyIds) {
      const el = document.getElementById(id);
      if (el) el.disabled = !!busy;
    }
  }
  let quantLastWeightDiff = null;
  /** 最近一次 β 分组完整响应（含各组 config_diff / preferred_cluster） */
  let quantLastOlsClusters = null;
  let quantLastThresholdDiff = null;
  let strategyLastIcExport = null;
  let strategyLastWeightDiff = null;
  let dailyPresetsCache = [];
  /** 研究页持有期；打开时从 memory 灌入，可「存为默认」写回 */
  let prefsHorizonDays = 3;

  function clampHorizonDays(v, fallback = 3) {
    const n = Number(v);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(1, Math.min(10, Math.round(n)));
  }

  function syncHorizonInputs(h) {
    const v = String(clampHorizonDays(h, prefsHorizonDays));
    document.querySelectorAll("#quant-horizon").forEach((el) => {
      el.value = v;
    });
  }

  function readHorizonDays() {
    const el = document.getElementById("quant-horizon");
    if (el && el.value !== "") {
      const n = clampHorizonDays(el.value, prefsHorizonDays);
      prefsHorizonDays = n;
      return n;
    }
    return prefsHorizonDays;
  }

  function clampRidgeLambda(v, fallback = 0) {
    const n = Number(v);
    if (!Number.isFinite(n) || n < 0) return fallback;
    return Math.min(100, n);
  }

  function readRidgeLambda() {
    const el = document.getElementById("quant-ridge-lambda");
    if (el && el.value !== "") return clampRidgeLambda(el.value, 0);
    return 0;
  }

  function resolveProbeInputToCode(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    if (probeSelectValueToCode[s]) return probeSelectValueToCode[s];
    const main = s.split(/[·|｜]/)[0].trim();
    if (probeSelectValueToCode[main]) return probeSelectValueToCode[main];
    const m = s.match(/\b(\d{6})\b/);
    if (m) return normalizeProbeCode(m[1]);
    const bare = normalizeProbeCode(main);
    if (/^\d{6}$/.test(bare)) return bare;
    const want = main.replace(/\s+/g, "");
    const nameByCode = clusterNameByCodeFromData(quantLastOlsClusters);
    for (const [c, nm] of Object.entries(nameByCode || {})) {
      if (String(nm || "").replace(/\s+/g, "") === want) return normalizeProbeCode(c);
    }
    for (const [c, nm] of Object.entries(watchingNameByCode || {})) {
      if (String(nm || "").replace(/\s+/g, "") === want) return normalizeProbeCode(c);
    }
    return main || s;
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

  function probePickerIdentityHtml(row) {
    const name = (row && row.name) || (row && row.code) || "";
    const code = (row && row.code) || "";
    return (
      `<span class="quant-probe-picker-identity">` +
      `<span class="quant-probe-picker-name">${escapeHtml(name)}</span>` +
      `<span class="quant-probe-picker-code">${escapeHtml(code)}</span>` +
      `</span>`
    );
  }

  function probePickerTriggerHtml(row) {
    if (!row || !row.code) {
      return `<span class="quant-probe-picker-empty">分组后可选</span>`;
    }
    const group = row.group || "—";
    return (
      probePickerIdentityHtml(row) +
      `<span class="quant-probe-picker-group">${escapeHtml(group)}</span>` +
      `<span class="quant-probe-picker-caret" aria-hidden="true"></span>`
    );
  }

  function setProbePickerOpen(open) {
    const root = document.getElementById("quant-probe-picker");
    const trigger = document.getElementById("quant-ols-code-trigger");
    const menu = document.getElementById("quant-ols-code-menu");
    if (!root || !trigger || !menu) return;
    const on = !!open && probePickerRows.length > 0;
    root.classList.toggle("is-open", on);
    trigger.setAttribute("aria-expanded", on ? "true" : "false");
    menu.hidden = !on;
  }

  function applyProbePickerSelection(code, { silent } = {}) {
    const hidden = document.getElementById("quant-ols-code");
    const trigger = document.getElementById("quant-ols-code-trigger");
    if (!hidden || !trigger) return;
    const c = normalizeProbeCode(code);
    const row =
      probePickerRows.find((r) => r.code === c) ||
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

  function renderProbePickerMenu() {
    const menu = document.getElementById("quant-ols-code-menu");
    if (!menu) return;
    if (!probePickerRows.length) {
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
    const body = probePickerRows
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

  /** 专业 picker：名称 / 代码 / 分组 分栏；隐藏域存代码 */
  function fillProbeCodeSelect(rows, preferredCode) {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return;
    bindProbePicker();
    const nextMap = {};
    probePickerRows = (rows || []).map((r) => {
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
    probeSelectValueToCode = nextMap;
    renderProbePickerMenu();
    if (!probePickerRows.length) {
      hidden.value = "";
      const trigger = document.getElementById("quant-ols-code-trigger");
      if (trigger) trigger.innerHTML = probePickerTriggerHtml(null);
      return;
    }
    const pref = normalizeProbeCode(
      resolveProbeInputToCode(preferredCode || hidden.value || "")
    );
    const hit = probePickerRows.find((r) => r.code === pref);
    applyProbePickerSelection(hit ? hit.code : probePickerRows[0].code, {
      silent: true,
    });
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

  async function populateOlsCodeOptions() {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return [];
    // 已有分组成员时由 syncProbe 填充，避免覆盖
    if (
      quantLastOlsClusters &&
      quantLastOlsClusters.success &&
      Array.isArray(quantLastOlsClusters.clusters) &&
      quantLastOlsClusters.clusters.length
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
          push(item, names[i] || watchingNameByCode[item] || "");
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

  async function loadPrefsHorizon() {
    try {
      const res = await fetch("/api/prefs");
      const data = await res.json();
      const prefs = (data && data.preferences) || {};
      prefsHorizonDays = clampHorizonDays(prefs.horizon_days, 3);
    } catch (_) {
      prefsHorizonDays = 3;
    }
    syncHorizonInputs(prefsHorizonDays);
    return prefsHorizonDays;
  }

  async function saveHorizonAsDefault() {
    const h = readHorizonDays();
    syncHorizonInputs(h);
    const res = await fetch("/api/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences: { horizon_days: h } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && (data.detail || data.error)) || res.statusText);
    }
    const eff = (data && data.effective) || {};
    prefsHorizonDays = clampHorizonDays(eff.horizon_days, h);
    syncHorizonInputs(prefsHorizonDays);
    return prefsHorizonDays;
  }

  async function openQuantDialog(options = {}) {
    const { autoBacktest = false } = options;
    const page = document.body.dataset.page;
    const useDialog =
      page !== "quant" &&
      page !== "chat" &&
      page !== "strategy" &&
      page !== "replay" &&
      page !== "watching" &&
      page !== "follow" &&
      quantDialog &&
      typeof quantDialog.showModal === "function";
    const hasStrategy = !!document.getElementById("quant-signal-summary") || !!document.getElementById("strategy-list");
    const hasWatching = !!document.getElementById("quant-watching-list") || !!document.getElementById("quant-watching-meta");
    const hasReplay = !!document.getElementById("quant-portfolio-run");
    const hasOps = !!document.getElementById("quant-ops-summary");
    try {
      if (quantMeta) quantMeta.textContent = "加载面板…";
      // 先拉研究默认 horizon（memory）与 OLS 标的列表，再跑 IC/OLS/回测
      await loadPrefsHorizon().catch(() => {});
      await populateOlsCodeOptions().catch(() => {});
      const foreground = [];
      if (hasWatching || hasReplay) {
        foreground.push(
          loadWatchingPanel().catch((err) => {
            setPoolMeta(String(err.message || err));
          })
        );
      }
      if (hasStrategy) {
        foreground.push(
          loadSignalConfigPanel().catch((err) => {
            if (quantSignalSummary) quantSignalSummary.textContent = String(err.message || err);
      })
        );
        foreground.push(
          loadStrategyList().catch(() => {})
        );
      }
      // 前台只等名单/策略骨架，超时也放行，避免右侧一直「加载观察…」
      if (foreground.length) {
        await Promise.race([
          Promise.all(foreground),
          new Promise((resolve) => setTimeout(resolve, 12000)),
        ]);
      }
      if (quantMeta) {
        setQuantMeta("分组为主路径 · 不写 signal_config");
      }
      if (useDialog) quantDialog.showModal();
      // 运维/因子/桥接等后台拉取：不阻塞 tab 加载态
      const background = [];
      if (hasOps) background.push(loadOpsPanel().catch(() => {}));
      if (hasStrategy || quantFactorList) {
        background.push(loadFactorPanel().catch(() => {}));
      }
      if (hasReplay) background.push(loadLastBacktestSnapshot().catch(() => {}));
      // 研究枢纽：进页自动跑分组（主路径）；不预跑全局 IC
      if (page === "quant" && (quantFactorList || quantOlsClusters)) {
        background.push(bootstrapClusterHub().catch(() => {}));
      }
      Promise.all(background)
        .then(async () => {
          if (autoBacktest && hasReplay) await runPortfolioBacktest();
        })
        .catch(() => {});
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
      if (useDialog) quantDialog.showModal();
    }
  }

  function setPoolMeta(text) {
    for (const id of ["quant-watching-meta", "replay-pool-meta"]) {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }
  }

  function setWatchingRefreshStatus(text, { error = false } = {}) {
    const el = document.getElementById("watching-refresh-status");
    if (!el) return;
    const msg = String(text || "").trim();
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("is-error");
      return;
    }
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("is-error", !!error);
  }

  function formatRefreshStats(refresh) {
    const r = refresh || {};
    const stats = Array.isArray(r.source_stats) ? r.source_stats : [];
    const bits = stats.map((s, i) => {
      const label = s.label || `S${i + 1}`;
      if (s.error) return `${label} 失败(${s.error})`;
      if (s.note && !(s.added > 0)) return `${label} +0（${s.note}）`;
      return `${label} +${s.added ?? 0}/${s.requested ?? 0}`;
    });
    const count = r.count != null ? r.count : (r.watchlist || []).length;
    return bits.length
      ? `已刷新 · 共 ${count} 只 · ${bits.join(" · ")}`
      : `已刷新 · 共 ${count} 只`;
  }

  let watchingSearchTimer = null;
  let watchingSearchSeq = 0;

  function hideWatchingSearchResults() {
    const box = document.getElementById("watching-search-results");
    if (box) {
      box.hidden = true;
      box.innerHTML = "";
    }
  }

  function renderWatchingSearchResults(items, query, note) {
    const box = document.getElementById("watching-search-results");
    if (!box) return;
    const list = Array.isArray(items) ? items : [];
    if (!list.length) {
      box.hidden = false;
      const tip = note
        ? `<p class="watching-search-empty">${escapeHtml(String(note))}</p>`
        : "";
      box.innerHTML =
        `<p class="watching-search-empty">未找到「${escapeHtml(query || "")}」</p>` + tip;
      return;
    }
    box.hidden = false;
    box.innerHTML = list
      .map((it) => {
        const code = String(it.stock_code || "");
        const name = String(it.stock_name || code);
        const hint = String(it.hint || "加入");
        return (
          `<button type="button" class="watching-search-item" data-code="${escapeHtml(code)}" data-name="${escapeHtml(name)}">` +
          `<span class="watching-search-item-main">` +
          `<span class="watching-search-item-name">${escapeHtml(name)}</span>` +
          `<span class="watching-search-item-code">${escapeHtml(code)}</span>` +
          `</span>` +
          `<span class="watching-search-item-hint">${escapeHtml(hint)}</span>` +
          `</button>`
        );
      })
      .join("");
  }

  async function runWatchingSearch(raw) {
    const q = String(raw || "").trim();
    const box = document.getElementById("watching-search-results");
    if (!q) {
      hideWatchingSearchResults();
      return;
    }
    const seq = ++watchingSearchSeq;
    // 有旧结果时不闪「搜索中」，避免体感卡顿
    if (box && box.hidden) {
      box.hidden = false;
      box.innerHTML = `<p class="watching-search-empty">搜索中…</p>`;
    }
    try {
      const res = await fetch(`/api/watching/search?q=${encodeURIComponent(q)}&limit=8`);
      const data = await res.json();
      if (seq !== watchingSearchSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderWatchingSearchResults(data.items || [], q, data.note || "");
    } catch (err) {
      if (seq !== watchingSearchSeq) return;
      if (box) {
        box.hidden = false;
        box.innerHTML = `<p class="watching-search-empty">${escapeHtml(String(err.message || err))}</p>`;
      }
    }
  }

  async function addWatchingWatchItem(query) {
    const q = String(query || "").trim();
    if (!q) return;
    setWatchingRefreshStatus(`正在加入「${q}」…`);
    try {
      const res = await fetch("/api/watching/watchlist/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, sync_paper: false }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      hideWatchingSearchResults();
      const input = document.getElementById("watching-search-input");
      if (input) input.value = "";
      await loadWatchingPanel();
      setWatchingRefreshStatus(data.message || "已加入观察");
    } catch (err) {
      const msg = String(err.message || err);
      setWatchingRefreshStatus(msg, { error: true });
    }
  }

  function wireWatchingSearch() {
    const input = document.getElementById("watching-search-input");
    const box = document.getElementById("watching-search-results");
    if (!input || input.dataset.wired === "1") return;
    input.dataset.wired = "1";
    input.addEventListener("input", () => {
      const q = input.value.trim();
      if (watchingSearchTimer) clearTimeout(watchingSearchTimer);
      if (!q) {
        hideWatchingSearchResults();
        return;
      }
      watchingSearchTimer = setTimeout(() => runWatchingSearch(q), 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        hideWatchingSearchResults();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const first = box && box.querySelector(".watching-search-item");
        if (first) {
          addWatchingWatchItem(first.dataset.code || first.dataset.name || input.value);
        } else if (input.value.trim()) {
          addWatchingWatchItem(input.value.trim());
        }
      }
    });
    if (box) {
      box.addEventListener("click", (e) => {
        const btn = e.target.closest(".watching-search-item");
        if (!btn) return;
        e.preventDefault();
        addWatchingWatchItem(btn.dataset.code || btn.dataset.name);
      });
    }
    document.addEventListener("click", (e) => {
      const wrap = document.getElementById("watching-search-wrap");
      if (!wrap || wrap.contains(e.target)) return;
      hideWatchingSearchResults();
    });
  }


  async function loadFollowCard() {
    const el = document.getElementById("quant-follow-summary");
    if (!el) return null;
    el.textContent = "纸面：加载中…";
    try {
      const res = await fetch("/api/paper");
      const data = await res.json();
      if (!data.initialized) {
        el.textContent = "纸面：未初始化 · 打开纸面 Tab 创建";
        return data;
      }
      const wl = (data.watchlist || []).length;
      const eq = data.equity ?? data.nav ?? data.summary?.equity;
      el.textContent = [
        data.name || "paper",
        `观察 ${wl} 只`,
        eq != null ? `净值 ${eq}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return data;
    } catch (err) {
      el.textContent = `纸面：${err.message || err}`;
      return null;
    }
  }

  async function gotoFollowTab() {
    const page = document.body.dataset.page;
    if (typeof ctx.showResultsTab === "function" && (page === "chat" || !page)) {
      await ctx.showResultsTab("follow", { openMobile: true, load: true });
    }
  }

  async function gotoPaperTab() {
    const page = document.body.dataset.page;
    if (typeof ctx.showResultsTab === "function" && (page === "chat" || !page)) {
      await ctx.showResultsTab("paper", { openMobile: true, load: true });
    }
  }

  function fmtPct(v) {
    if (v === null || v === undefined || v === "") return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return escapeHtml(String(v));
    return `${n}%`;
  }

  function metricClass(v) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "up" : "down";
  }

  /** 与数据中心同壳：watching-react-grid（小表非虚拟；壳/样式对齐 watching） */
  function researchGridHtml(columns, rows, cellHtml, opts = {}) {
    const cols = Array.isArray(columns) ? columns : [];
    const data = Array.isArray(rows) ? rows : [];
    if (!cols.length) return "";
    const emptyText = opts.emptyText || "暂无数据";
    const rowClassFn = typeof opts.rowClass === "function" ? opts.rowClass : null;
    const head =
      `<div class="watching-react-grid-head"><div class="watching-react-grid-row is-head">` +
      cols
        .map((col) => {
          const extra = [
            col.num ? "watching-col-num" : "",
            col.center ? "watching-col-center" : "",
            col.headClass || "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            `<div class="watching-react-grid-cell${extra ? ` ${extra}` : ""}" ` +
            `style="${colStyle(col)}" title="${escapeHtml(col.title || col.label || "")}">` +
            `${escapeHtml(col.label || "")}</div>`
          );
        })
        .join("") +
      `</div></div>`;
    const body =
      data.length === 0
        ? `<p class="watching-table-empty">${escapeHtml(emptyText)}</p>`
        : data
            .map((d) => {
              const rowExtra = rowClassFn ? rowClassFn(d) : "";
              return (
                `<div class="watching-react-grid-row${rowExtra ? ` ${escapeHtml(rowExtra)}` : ""}">` +
                cols
                  .map((col) => {
                    const extra = [
                      col.num ? "watching-col-num num" : "",
                      col.center ? "watching-col-center" : "",
                      col.cellClass || "",
                    ]
                      .filter(Boolean)
                      .join(" ");
                    const inner =
                      typeof cellHtml === "function"
                        ? cellHtml(col, d)
                        : escapeHtml(d[col.id] ?? "—");
                    return (
                      `<div class="watching-react-grid-cell${extra ? ` ${extra}` : ""}" ` +
                      `style="${colStyle(col)}">${inner}</div>`
                    );
                  })
                  .join("") +
                `</div>`
              );
            })
            .join("");
    return (
      `<div class="watching-react-grid quant-research-grid">` +
      head +
      `<div class="watching-react-grid-body quant-research-grid-body">${body}</div>` +
      `</div>`
    );
  }

  function metricCell(text, cls) {
    return `<span class="bt-trade-ret ${cls || ""}">${text}</span>`;
  }

  const BT_SIM_TRADE_COLS_BASE = [
    { id: "signal", label: "信号日", widthPct: 9 },
    { id: "entry", label: "买入日", widthPct: 9 },
    { id: "exit", label: "卖出日", widthPct: 9 },
    { id: "name", label: "股票", flex: true },
    {
      id: "score",
      label: "score",
      widthPct: 7,
      num: true,
      sortable: true,
      title: "悬停查看评分公式与分组因子权重",
    },
    {
      id: "intent",
      label: "意图价",
      widthPct: 8,
      num: true,
      sortable: true,
      title: "信号日收盘（决策参照）；与买入价不同才显示本列",
    },
    {
      id: "buy",
      label: "买入价",
      widthPct: 8,
      num: true,
      sortable: true,
      title: "实际入场价（next_open=次日开盘）",
    },
    { id: "sell", label: "卖出价", widthPct: 8, num: true, sortable: true },
    { id: "ret", label: "收益", widthPct: 7, num: true, sortable: true },
    { id: "status", label: "状态", widthPct: 8 },
  ];

  function simTradesIntentDiffers(legs) {
    for (const r of legs || []) {
      const intent = r.intent_price;
      const buy = r.entry_price;
      if (intent == null || buy == null) continue;
      if (Number(intent) !== Number(buy)) return true;
    }
    return false;
  }

  function btSimTradeColumns(showIntent) {
    if (showIntent) return BT_SIM_TRADE_COLS_BASE;
    return BT_SIM_TRADE_COLS_BASE.filter((c) => c.id !== "intent");
  }

  /** @type {Array<object>} */
  let lastSimTrades = [];
  const btSimScoreTips = createScoreTooltipController();

  function clearBtTradesTable() {
    btTradesTableApi = null;
    lastSimTrades = [];
    if (quantBtTrades) quantBtTrades.innerHTML = "";
  }

  function setBtTradesCaption(text) {
    btTradesTableApi = null;
    lastSimTrades = [];
    if (quantBtTrades) {
      quantBtTrades.innerHTML = `<p class="quant-trades-caption">${escapeHtml(text)}</p>`;
    }
  }

  function flattenTradesToSimLegs(trades) {
    const out = [];
    for (const t of trades || []) {
      const legs = Array.isArray(t.legs) ? t.legs : [];
      if (!legs.length) continue;
      for (const l of legs) {
        out.push({
          stock_code: l.stock_code || l.code || "",
          signal_date: t.signal_date || l.signal_date,
          entry_date: l.entry_date || t.entry_date,
          exit_date: l.exit_date || t.exit_date,
          intent_price: l.intent_price,
          entry_price: l.fill_price ?? l.entry_price,
          exit_price: l.exit_price,
          return_pct: l.return_pct,
          score: l.score,
          status: "filled",
          port_return_pct: t.return_pct,
          cluster_label: l.cluster_label,
          score_weight_source: l.score_weight_source,
          factor_weights: l.factor_weights,
          factor_weights_note: l.factor_weights_note,
          score_formula: l.score_formula,
          score_reasons: l.score_reasons,
          score_raw: l.score_raw,
        });
      }
    }
    return out;
  }

  function formatSimStatus(st) {
    const s = String(st || "filled");
    if (s === "filled") return "成交";
    if (s === "skipped_limit_entry") return "买跳过";
    if (s === "skipped_limit_exit") return "卖跳过";
    return s;
  }

  function formatFactorWeightsNote(r) {
    if (r && r.factor_weights_note) return String(r.factor_weights_note);
    const fw = (r && r.factor_weights) || {};
    const parts = Object.keys(fw)
      .sort((a, b) => Number(fw[b] || 0) - Number(fw[a] || 0) || a.localeCompare(b))
      .slice(0, 8)
      .map((k) => `${k} ${Number(fw[k]).toFixed(2)}`);
    const body = parts.join(" / ");
    const label = r && r.cluster_label;
    if (label) {
      return `分组 ${label} 因子权重` + (body ? `：${body}` : "");
    }
    return "未入组 · 全局因子权重" + (body ? `：${body}` : "");
  }

  function downloadSimTradesCsv(rows) {
    const showIntent = simTradesIntentDiffers(rows);
    const header = [
      "stock_code",
      "stock_name",
      "signal_date",
      "entry_date",
      ...(showIntent ? ["intent_price"] : []),
      "entry_price",
      "exit_date",
      "exit_price",
      "return_pct",
      "score",
      "cluster_label",
      "factor_weights_note",
      "score_formula",
      "status",
      "port_return_pct",
      "sector",
    ];
    const lines = [header.join(",")];
    for (const r of rows || []) {
      const code = String(r.stock_code || "").trim();
      const name = watchingNameByCode[code] || "";
      const cells = [
        code,
        name,
        r.signal_date || "",
        r.entry_date || "",
        ...(showIntent ? [r.intent_price ?? ""] : []),
        r.entry_price ?? "",
        r.exit_date || "",
        r.exit_price ?? "",
        r.return_pct ?? "",
        r.score ?? "",
        r.cluster_label || "",
        formatFactorWeightsNote(r),
        r.score_formula || "",
        r.status || "",
        r.port_return_pct ?? "",
        r.sector || "",
      ].map((v) => {
        const s = String(v);
        return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
      });
      lines.push(cells.join(","));
    }
    const blob = new Blob(["\ufeff" + lines.join("\n")], {
      type: "text/csv;charset=utf-8",
    });
    downloadBlob(blob, `topk_sim_trades_${new Date().toISOString().slice(0, 10)}.csv`);
  }

  function renderBtTradesTable(dataOrTrades) {
    if (!quantBtTrades) return;
    // 已并入记账表：清空旧「信号–成交对照」区
    const fillEl = document.getElementById("quant-signal-fill");
    if (fillEl) fillEl.innerHTML = "";
    let legs = [];
    if (Array.isArray(dataOrTrades)) {
      legs = dataOrTrades;
    } else if (dataOrTrades && typeof dataOrTrades === "object") {
      if (Array.isArray(dataOrTrades.sim_trades) && dataOrTrades.sim_trades.length) {
        legs = dataOrTrades.sim_trades;
      } else if (Array.isArray(dataOrTrades.trades_sample)) {
        legs = flattenTradesToSimLegs(dataOrTrades.trades_sample);
      } else if (Array.isArray(dataOrTrades.signal_fill_sample)) {
        // 兼容旧响应：无 sim_trades 时用对照样本
        legs = dataOrTrades.signal_fill_sample;
      }
    }
    if (!legs.length) {
      setBtTradesCaption("暂无模拟成交记录 · 请先跑 Top-K 回测");
      return;
    }
    lastSimTrades = legs;
    const filled = legs.filter((r) => (r.status || "filled") === "filled").length;
    const skipped = legs.length - filled;
    const showIntent = simTradesIntentDiffers(legs);
    const rows = legs
      .slice()
      .sort((a, b) => {
        const ae = String(a.entry_date || a.signal_date || "");
        const be = String(b.entry_date || b.signal_date || "");
        if (ae !== be) return be.localeCompare(ae);
        const as = String(a.signal_date || "");
        const bs = String(b.signal_date || "");
        if (as !== bs) return bs.localeCompare(as);
        return String(b.stock_code || "").localeCompare(String(a.stock_code || ""), "zh-CN");
      })
      .map((r, i) => {
        const code = String(r.stock_code || "").trim();
        const fullName = watchingNameByCode[code] || code;
        const ret = r.return_pct;
        const st = r.status || "filled";
        const reasons = Array.isArray(r.score_reasons) ? r.score_reasons.slice(0, 8) : [];
        reasons.push("截面中性化后相对分（中心约 50；公式为合成项，末尾含原始→中性化对照）");
        if (r.sector) reasons.push(`行业 ${r.sector}`);
        if (r.port_return_pct != null) reasons.push(`本期组合收益 ${r.port_return_pct}%`);
        const scoreDetail = JSON.stringify({
          formula: r.score_formula || "",
          reasons,
          hard_reject: false,
          reject_reason: "",
          weight_source: r.score_weight_source || r.weight_source || "",
          cluster_label: r.cluster_label || "",
          cluster_mode: r.cluster_mode || "",
          cluster_version: r.cluster_version,
          score_global: r.score_global,
          score_cluster: r.score_cluster,
          factor_weights: r.factor_weights || {},
        });
        return {
          code: `sim-${i}-${code}-${r.entry_date || ""}-${r.exit_date || ""}-${st}`,
          stock_code: code,
          name: fullName,
          signal: r.signal_date || "—",
          entry: r.entry_date || "—",
          exit: r.exit_date || "—",
          intentNum: Number.isFinite(Number(r.intent_price)) ? Number(r.intent_price) : null,
          intentText: r.intent_price != null ? String(r.intent_price) : "—",
          buyNum: Number.isFinite(Number(r.entry_price)) ? Number(r.entry_price) : null,
          buyText: r.entry_price != null ? String(r.entry_price) : "—",
          sellNum: Number.isFinite(Number(r.exit_price)) ? Number(r.exit_price) : null,
          sellText: r.exit_price != null ? String(r.exit_price) : "—",
          retNum: Number.isFinite(Number(ret)) ? Number(ret) : null,
          retText: ret != null ? fmtPct(ret) : "—",
          retCls: st !== "filled" ? "down" : metricClass(ret),
          scoreNum: Number.isFinite(Number(r.score)) ? Number(r.score) : null,
          scoreText: r.score != null ? String(r.score) : "—",
          scoreDetail,
          status: formatSimStatus(st),
        };
      });

    quantBtTrades.innerHTML =
      `<p class="quant-trades-caption">` +
      `模拟成交账（含涨跌停跳过）· ${rows.length} 笔` +
      `（成交 ${filled}` +
      (skipped ? ` · 跳过 ${skipped}` : "") +
      `）· 按买入日新→旧 · 信号日→次日买入` +
      (showIntent ? "" : " · 意图价=买入价已省略") +
      `<button type="button" id="quant-bt-trades-csv" class="dialog-btn secondary quant-bt-trades-csv">下载 CSV</button>` +
      `</p>` +
      `<div class="quant-bt-trades-host"></div>`;
    const csvBtn = document.getElementById("quant-bt-trades-csv");
    if (csvBtn) {
      csvBtn.addEventListener("click", () => downloadSimTradesCsv(lastSimTrades));
    }
    const host = quantBtTrades.querySelector(".quant-bt-trades-host");
    btTradesTableApi = mountVirtualTable(host, {
      columns: btSimTradeColumns(showIntent),
      emptyText: "暂无模拟成交记录",
      rowHeight: 38,
      rootClass: "watching-react-grid",
      bodyClass: "quant-bt-trades-body",
      compare: (id, a, b) => {
        const numKeys = {
          ret: "retNum",
          buy: "buyNum",
          sell: "sellNum",
          intent: "intentNum",
          score: "scoreNum",
        };
        const nk = numKeys[id];
        if (nk) {
          const av = a[nk];
          const bv = b[nk];
          if (av == null && bv == null) return 0;
          if (av == null) return -1;
          if (bv == null) return 1;
          return av - bv;
        }
        return String(a[id] ?? "").localeCompare(String(b[id] ?? ""), "zh-CN", {
          numeric: true,
        });
      },
      cellHtml: (col, d) => {
        if (col.id === "name") {
          return (
            `<div class="watching-stock" title="${escapeHtml(d.name + " " + d.stock_code)}">` +
            watchingNameSpanHtml(d.name) +
            `<span class="watching-code-sub">${escapeHtml(d.stock_code)}</span></div>`
          );
        }
        if (col.id === "ret") {
          return `<span class="bt-trade-ret ${d.retCls || ""}">${escapeHtml(d.retText)}</span>`;
        }
        if (col.id === "score") {
          return (
            `<span class="bt-trade-score paper-hold-score has-tip" ` +
            `data-score-detail="${escapeHtml(d.scoreDetail)}" ` +
            `title="悬停查看评分与权重来源">${escapeHtml(d.scoreText)}</span>`
          );
        }
        if (col.id === "intent") return escapeHtml(d.intentText);
        if (col.id === "buy") return escapeHtml(d.buyText);
        if (col.id === "sell") return escapeHtml(d.sellText);
        return escapeHtml(d[col.id] ?? "—");
      },
    });
    btTradesTableApi.setRows(rows);
    if (quantBtTrades.dataset.scoreTipWired !== "1") {
      btSimScoreTips.bindHost(quantBtTrades, {
        scoreSelector: ".bt-trade-score[data-score-detail], .paper-hold-score[data-score-detail]",
      });
    }
  }

  function renderMetricCards(host, items) {
    if (!host) return;
    if (!items || !items.length) {
      host.innerHTML = "";
      return;
    }
    host.innerHTML = items
      .map(
        (it) =>
          `<div class="quant-metric"><span class="label">${escapeHtml(it.label)}</span>` +
          `<span class="val ${it.cls || ""}">${it.value}</span></div>`
      )
      .join("");
  }

  function renderBtScopeNote(text, { warn = false } = {}) {
    const el = document.getElementById("quant-bt-scope-note");
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("down");
      return;
    }
    el.hidden = false;
    el.textContent = text;
    if (warn) el.classList.add("down");
    else el.classList.remove("down");
  }

  function renderFitGapPanel(data) {
    const el = document.getElementById("quant-fit-gap");
    if (!el) return;
    if (!data || !data.ok) {
      el.innerHTML = "";
      return;
    }
    const hints = data.hints || [];
    el.innerHTML =
      `<p class="quant-trades-caption">回测–纸面落差归因（启发式 · warn ${
        data.warn_count ?? 0
      }）</p>` +
      researchGridHtml(
        [
          { id: "level", label: "级别", widthPct: 14, center: true },
          { id: "code", label: "码", widthPct: 22 },
          { id: "message", label: "说明", flex: true },
        ],
        hints.map((h) => ({
          level: h.level || "info",
          code: h.code || "",
          message: h.message || "",
          isWarn: (h.level || "") === "warn",
        })),
        (col, d) => escapeHtml(d[col.id] ?? "—"),
        {
          emptyText: "无归因项",
          rowClass: (d) => (d.isWarn ? "down" : ""),
        }
      ) +
      `<p class="quant-sub">${escapeHtml(data.note || "")} · 拟合 KPI 见 <a href="/platform">平台北极星</a></p>`;
  }

  async function loadFitGapForBacktest(bt) {
    const { ok, data } = await apiFetch("/api/ops/fit-gap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result: bt || {} }),
    });
    if (ok) renderFitGapPanel(data);
    else renderFitGapPanel(null);
  }

  function formatSnapshotAt(iso) {
    if (!iso) return "—";
    const s = String(iso);
    return s.length > 19 ? s.slice(0, 19).replace("T", " ") : s.replace("T", " ");
  }

  function renderPortfolioBacktestResult(data) {
    if (!data || !data.success) {
      renderMetricCards(quantBtMetrics, []);
      renderWfSlices(null);
      renderCostAssumptions(null);
      renderAttributionTables(null);
      renderRegimeBuckets(null);
      renderSignalFillTable(null);
      renderUniversePanel(null);
      renderScoreIc(null);
      renderIcEquityAlign(null);
      renderQuantileTable(null);
      renderFitGapPanel(null);
      const expBtn = document.getElementById("quant-backtest-report-export");
      if (expBtn) expBtn.disabled = true;
      clearBtTradesTable();
      return;
    }
    if (neutralCompareSource === "frozen") {
      renderBtScopeNote(
        BT_SCOPE_LIVE + " · 下方中性化对照仍为日报冻结，请点「中性化对照」刷新。",
        { warn: true }
      );
    } else {
      const bench = data.benchmark || {};
      const excessWarn =
        bench.warn_abs_pos_excess_neg
          ? " · ⚠ 绝对收益为正但超额为负（可能只是 beta/池涨）"
          : "";
      renderBtScopeNote(BT_SCOPE_LIVE + excessWarn, {
        warn: !!bench.warn_abs_pos_excess_neg,
      });
    }
    const m = data.metrics || {};
    ctx.lastBacktestMetrics = {
      max_drawdown_pct: m.max_drawdown_pct,
      win_rate_pct: m.win_rate_pct,
      total_return_pct: m.total_return_pct,
    };
    const params = data.params || {};
    const costModel = data.cost_model || params.cost_model || (params.apply_costs ? "simple_cn" : "zero");
    const costMode = (data.cost_assumptions || {}).cost_mode || params.cost_mode;
    const costLabel =
      costModel === "zero"
        ? "零成本"
        : costMode === "turnover"
          ? "A股简化·换手"
          : costModel === "simple_cn"
            ? "A股简化"
            : String(costModel);
    const oos = data.oos_summary || {};
    const regime = data.regime_summary || {};
    const oosFailed = oos.ok === false || oos.failed === true;
    const oosLabel = oos.ok
      ? oos.failed
        ? `失败 · 内 ${oos.is_return_pct ?? "—"}% / 外 ${oos.oos_return_pct ?? "—"}%`
        : `样本内 ${oos.is_return_pct ?? "—"}% / 外 ${oos.oos_return_pct ?? "—"}%`
      : oos.reason || "—";
    const regimeLabel =
      regime.regime ||
      (regime.ok === false ? regime.reason || "—" : "—");
    const dq = data.data_quality || {};
    const fb = Number(dq.fallback_count || 0);
    const gated = Number(dq.gated_count || 0);
    const dqLabel =
      fb > 0 || gated > 0
        ? `降级 ${fb}${gated > 0 ? ` · 门禁 ${gated}` : ""}`
        : dq.count != null
          ? `正常 ${dq.count}`
          : "—";
    const dropN = Number(params.dropped_thin_count || (data.dropped_stocks || []).length || 0);
    const cc = data.cost_compare || {};
    const cards = [
      { label: "累计收益", value: fmtPct(m.total_return_pct), cls: metricClass(m.total_return_pct) },
      { label: "胜率", value: fmtPct(m.win_rate_pct) },
      { label: "交易次数", value: escapeHtml(String(m.trade_count ?? data.trade_count ?? "—")) },
      { label: "最大回撤", value: fmtPct(m.max_drawdown_pct), cls: metricClass(-(Number(m.max_drawdown_pct) || 0)) },
      { label: "平均收益", value: fmtPct(m.avg_return_pct), cls: metricClass(m.avg_return_pct) },
      { label: "Sharpe≈", value: escapeHtml(String(m.sharpe_approx ?? "—")) },
      { label: "共同交易日", value: escapeHtml(String(params.common_dates ?? "—")) },
      { label: "标的数", value: escapeHtml(String((data.loaded_stocks || []).length || params.stock_count || "—")) },
      {
        label: "排除短序列",
        value: escapeHtml(String(dropN)),
        cls: dropN > 0 ? "down" : "",
      },
      { label: "成本模型", value: escapeHtml(costLabel) },
      {
        label: "权重模式",
        value: escapeHtml(
          params.weight_mode === "score_budget"
            ? "分数预算"
            : params.weight_mode === "risk_parity_lite"
              ? "风险平价"
              : params.weight_mode === "equal" || !params.weight_mode
                ? "等权"
                : String(params.weight_mode)
        ),
      },
      { label: "OOS", value: escapeHtml(String(oosLabel)), cls: oosFailed ? "down" : "" },
      { label: "Regime", value: escapeHtml(String(regimeLabel)) },
      { label: "数据质量", value: escapeHtml(dqLabel), cls: fb > 0 || gated > 0 ? "down" : "" },
      { label: "结果来源", value: "当次回测" },
    ];
    const bench = data.benchmark || {};
    if (bench.ok) {
      cards.push(
        {
          label: `超额·${bench.benchmark_label || "基准"}`,
          value: fmtPct(bench.excess_pct),
          cls: metricClass(bench.excess_pct),
        },
        {
          label: "基准收益",
          value: fmtPct(bench.benchmark_return_pct),
          cls: metricClass(bench.benchmark_return_pct),
        }
      );
      if (bench.ann_excess_pct != null) {
        cards.push({
          label: "年化超额",
          value: fmtPct(bench.ann_excess_pct),
          cls: metricClass(bench.ann_excess_pct),
        });
      }
      if (bench.ann_ir != null || bench.ir != null) {
        cards.push({
          label: "超额IR",
          value: escapeHtml(
            String(bench.ann_ir != null ? bench.ann_ir : bench.ir)
          ),
        });
      }
      if (bench.warn_abs_pos_excess_neg) {
        cards.push({
          label: "超额警示",
          value: "绝对+超额−",
          cls: "down",
        });
      }
    }
    const sic = data.score_ic || {};
    if (sic.ok) {
      cards.push(
        { label: "IC均值", value: escapeHtml(String(sic.ic_mean ?? "—")) },
        { label: "ICIR", value: escapeHtml(String(sic.icir ?? "—")) }
      );
      if (sic.positive_ic_ratio != null) {
        cards.push({
          label: "正IC占比",
          value: `${(Number(sic.positive_ic_ratio) * 100).toFixed(0)}%`,
          cls: Number(sic.positive_ic_ratio) >= 0.55 ? "" : "down",
        });
      }
    }
    const align = data.ic_equity_align || {};
    if (align.ok && align.avg_return_spread_pp != null) {
      cards.push({
        label: "IC窗收益差",
        value: `${Number(align.avg_return_spread_pp) >= 0 ? "+" : ""}${align.avg_return_spread_pp}pp`,
        cls: align.aligned_favor_pos_ic === false ? "down" : metricClass(align.avg_return_spread_pp),
      });
    }
    if (cc.ok) {
      const gap = cc.return_gap_pp;
      const zRet = (cc.zero || {}).total_return_pct;
      const cRet = (cc.simple_cn || {}).total_return_pct;
      cards.push(
        {
          label: "成本对照Δ",
          value:
            gap != null
              ? `${Number(gap) >= 0 ? "+" : ""}${Number(gap).toFixed(2)}pp`
              : "—",
          cls: gap != null && Number(gap) < 0 ? "down" : "",
        },
        {
          label: "零成本收益",
          value: zRet != null ? `${Number(zRet).toFixed(2)}%` : "—",
          cls: metricClass(zRet),
        },
        {
          label: "含成本收益",
          value: cRet != null ? `${Number(cRet).toFixed(2)}%` : "—",
          cls: metricClass(cRet),
        }
      );
      if (cc.avg_impact_bps != null && Number(cc.avg_impact_bps) > 0) {
        cards.push({
          label: "均冲击",
          value: `${Number(cc.avg_impact_bps).toFixed(1)} bps`,
        });
      }
    }
    const pitR = data.pit_report || {};
    const fundPit = pitR.fundamentals || {};
    if (pitR.bars_pit) {
      cards.push({
        label: "财务PIT",
        value: pitR.fundamentals_pit
          ? "是"
          : fundPit.missing_as_of
            ? `缺 ${fundPit.missing_as_of}`
            : fundPit.resolved_ok
              ? `部分 ${fundPit.resolved_ok}`
              : "快照/无",
        cls: pitR.fundamentals_pit ? "" : "down",
      });
    }
    const sa = data.source_audit || {};
    if (sa.status && sa.status !== "empty") {
      cards.push({
        label: "源审计",
        value:
          Number(sa.fallback_count || 0) > 0
            ? `fallback ${sa.fallback_count}`
            : sa.status === "ok"
              ? "一致"
              : String(sa.status),
        cls: Number(sa.fallback_count || 0) > 0 || sa.status === "bad" ? "down" : "",
      });
    }
    const attr = data.attribution || {};
    if (attr.ok) {
      const topSec = (attr.by_sector || [])[0];
      cards.push(
        {
          label: "选股超额",
          value:
            attr.selection_excess_pct != null
              ? `${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
              : "—",
          cls: metricClass(attr.selection_excess_pct),
        },
        {
          label: "主贡献行业",
          value: topSec
            ? `${topSec.sector} ${Number(topSec.avg_return_pct) >= 0 ? "+" : ""}${topSec.avg_return_pct}%`
            : "—",
        }
      );
    }
    if (data.pit_report && data.pit_report.bars_pit) {
      cards.push({
        label: "PIT日线",
        value: escapeHtml(String(params.execution_mode || "as_of")),
      });
    }
    renderMetricCards(quantBtMetrics, cards);
    renderUniversePanel(data.universe);
    renderWfSlices(data.wf_slices);
    renderCostAssumptions(data.cost_assumptions);
    renderAttributionTables(data.attribution);
    renderRegimeBuckets(data.regime_buckets);
    // 信号–成交已并入模拟成交账
    renderSignalFillTable(null);
    renderScoreIc(data.score_ic);
    renderIcEquityAlign(data.ic_equity_align);
    renderQuantileTable(data.quantile_backtest);
    lastBacktestPack = {
      kind: "portfolio_backtest",
      exported_at: new Date().toISOString(),
      result: data,
    };
    cachePromoteHintsFromBacktest(data);
    refreshParamGridApplyGate();
    const expBtn = document.getElementById("quant-backtest-report-export");
    if (expBtn) expBtn.disabled = false;

    renderBtTradesTable(data);
  }

  const PROMOTE_HINTS_KEY = "investment_promote_hints_v1";
  const PROMOTE_HARD_GATE_KEY = "investment_promote_hard_gate_v1";
  const PROMOTE_HINTS_TTL_HOURS_KEY = "investment_promote_hints_ttl_h_v1";
  const PROMOTE_EXPIRE_HARD_KEY = "investment_promote_expire_hard_v1";
  const PROMOTE_HINTS_TTL_HOURS_DEFAULT = 24;

  function readPromoteTtlHours() {
    const el = document.getElementById("quant-promote-ttl-hours");
    let h = PROMOTE_HINTS_TTL_HOURS_DEFAULT;
    if (el && el.value !== "") {
      const n = Number(el.value);
      if (Number.isFinite(n)) h = n;
    } else {
      try {
        const saved = Number(sessionStorage.getItem(PROMOTE_HINTS_TTL_HOURS_KEY));
        if (Number.isFinite(saved) && saved > 0) h = saved;
      } catch (_) {
        /* ignore */
      }
    }
    return Math.max(1, Math.min(168, Math.round(h)));
  }

  function promoteHintsTtlMs() {
    return readPromoteTtlHours() * 60 * 60 * 1000;
  }

  function persistPromoteTtlHours(h) {
    try {
      sessionStorage.setItem(PROMOTE_HINTS_TTL_HOURS_KEY, String(h));
    } catch (_) {
      /* ignore */
    }
  }

  function readPromoteExpireHard() {
    const el = document.getElementById("strategy-promote-expire-hard");
    if (el) return !!el.checked;
    try {
      return sessionStorage.getItem(PROMOTE_EXPIRE_HARD_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function persistPromoteExpireHard(on) {
    try {
      sessionStorage.setItem(PROMOTE_EXPIRE_HARD_KEY, on ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
  }

  function readPromoteHardGate() {
    const el = document.getElementById("quant-promote-hard-gate");
    if (el) return !!el.checked;
    try {
      return sessionStorage.getItem(PROMOTE_HARD_GATE_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function persistPromoteHardGate(on) {
    try {
      sessionStorage.setItem(PROMOTE_HARD_GATE_KEY, on ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
  }

  function isPromoteHintsExpired(pack, now = Date.now()) {
    if (!pack || !pack.at) return true;
    const ttl =
      Number(pack.ttl_ms) > 0 ? Number(pack.ttl_ms) : promoteHintsTtlMs();
    return now - Number(pack.at) > ttl;
  }

  function cachePromoteHintsFromBacktest(data) {
    if (!data || !data.success) return;
    const req = data.request || {};
    const ttlMs = promoteHintsTtlMs();
    const pack = {
      at: Date.now(),
      ttl_ms: ttlMs,
      lookback: req.lookback,
      top_k: req.top_k,
      hints: Array.isArray(data.promote_hints) ? data.promote_hints : [],
      ic_equity_align: data.ic_equity_align || null,
      total_return_pct: (data.metrics || {}).total_return_pct,
      excess_pct: (data.benchmark || {}).excess_pct,
      hard_gate_at_cache: readPromoteHardGate(),
    };
    try {
      localStorage.setItem(PROMOTE_HINTS_KEY, JSON.stringify(pack));
    } catch (_) {
      /* ignore */
    }
    renderPromoteHintsPanel(pack, "quant-promote-hints");
    renderPromoteHintsPanel(pack, "strategy-promote-hints");
  }

  function loadCachedPromoteHints() {
    try {
      const raw = localStorage.getItem(PROMOTE_HINTS_KEY);
      if (!raw) return null;
      const pack = JSON.parse(raw);
      if (isPromoteHintsExpired(pack)) {
        localStorage.removeItem(PROMOTE_HINTS_KEY);
        return {
          expired: true,
          at: pack.at,
          ttl_ms: pack.ttl_ms || promoteHintsTtlMs(),
        };
      }
      return pack;
    } catch (_) {
      return null;
    }
  }

  function renderPromoteHintsPanel(pack, elId) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!pack) {
      el.innerHTML = "";
      return;
    }
    const ttlFallback = promoteHintsTtlMs();
    if (pack.expired) {
      const when = pack.at
        ? new Date(pack.at).toISOString().slice(0, 19).replace("T", " ")
        : "—";
      el.innerHTML =
        `<p class="quant-trades-caption down">Promote 提示已过期</p>` +
        `<p class="sub">缓存于 ${escapeHtml(when)}，TTL ${Math.round(
          (pack.ttl_ms || ttlFallback) / 3600000
        )}h。请回 <a href="/replay">历史回测</a> 重跑 Top-K。</p>`;
      return;
    }
    const hints = pack.hints || [];
    const align = pack.ic_equity_align || {};
    const when = pack.at
      ? new Date(pack.at).toISOString().slice(0, 19).replace("T", " ")
      : "—";
    const ageH =
      pack.at != null
        ? Math.max(0, (Date.now() - Number(pack.at)) / 3600000).toFixed(1)
        : null;
    const ttlH = Math.round((pack.ttl_ms || ttlFallback) / 3600000);
    const head =
      `<p class="quant-trades-caption${hints.length ? " down" : ""}">Promote 提示` +
      ` · lookback=${pack.lookback ?? "—"} / top_k=${pack.top_k ?? "—"}` +
      ` · ${escapeHtml(when)}` +
      (ageH != null ? ` · ${ageH}h/${ttlH}h` : "") +
      (pack.total_return_pct != null ? ` · 累计 ${pack.total_return_pct}%` : "") +
      (pack.excess_pct != null ? ` · 超额 ${pack.excess_pct}%` : "") +
      `</p>`;
    if (!hints.length && !(align && align.ok)) {
      el.innerHTML =
        head +
        `<p class="sub">最近 Top-K 无警示。来源：<a href="/replay">历史回测</a>。</p>`;
      return;
    }
    const hintRows = [];
    hints.forEach((h) => {
      hintRows.push({
        level: String(h.level || "warn"),
        code: String(h.code || "—"),
        text: String(h.text || ""),
      });
    });
    if (
      align &&
      align.ok &&
      align.aligned_favor_pos_ic === false &&
      !hints.some((h) => h.code === "ic_align_mismatch")
    ) {
      hintRows.push({
        level: "warn",
        code: "ic_align_mismatch",
        text: `正IC窗均收益未高于非正（差 ${align.avg_return_spread_pp}pp）`,
      });
    }
    el.innerHTML =
      head +
      (hintRows.length
        ? researchGridHtml(
            [
              { id: "level", label: "级别", widthPct: 14, center: true },
              { id: "code", label: "码", widthPct: 26 },
              { id: "text", label: "说明", flex: true },
            ],
            hintRows,
            (col, d) =>
              col.id === "code"
                ? `<code>${escapeHtml(d.code)}</code>`
                : escapeHtml(d[col.id] ?? "—"),
            { emptyText: "无 warn 项" }
          )
        : `<p class="sub">无 warn 项。</p>`) +
      `<p class="sub">研究警示，默认不硬拦 promote；回测页可开「IC硬闸」；策略页可开「过期硬拦晋升」。提示 TTL ${ttlH}h（可改）。</p>`;
  }

  function paramGridApplyGate() {
    const best = lastParamGrid && lastParamGrid.best;
    if (!best) {
      return { ok: false, reason: "无最优单元" };
    }
    const bt = lastBacktestPack && lastBacktestPack.result;
    if (!bt || !bt.success) {
      return {
        ok: false,
        reason: "请先用该 lookback/top_k 跑一次「Top-K 回测」",
      };
    }
    const req = bt.request || {};
    const lb = Number(req.lookback);
    const tk = Number(req.top_k);
    if (lb !== Number(best.lookback) || tk !== Number(best.top_k)) {
      return {
        ok: false,
        reason: `当次回测为 lookback=${lb || "—"}/top_k=${tk || "—"}，与最优 ${best.lookback}/${best.top_k} 不一致`,
      };
    }
    const oos = bt.oos_summary || {};
    if (!oos.ok || oos.failed) {
      return {
        ok: false,
        reason: `OOS 未通过（${oos.reason || "失败"}）；禁止仅凭样本内最优应用`,
      };
    }
    const align = bt.ic_equity_align || {};
    const hints = Array.isArray(bt.promote_hints) ? bt.promote_hints : [];
    const alignWarn =
      (align.ok && align.aligned_favor_pos_ic === false) ||
      hints.some((h) => h && h.code === "ic_align_mismatch");
    if (alignWarn && readPromoteHardGate()) {
      return {
        ok: false,
        reason:
          "IC硬闸已开：正IC窗未优于非正，禁止应用最优（可关闭硬闸后仅二次确认）",
      };
    }
    if (alignWarn) {
      return {
        ok: true,
        warn: true,
        reason:
          "OOS 已过，但正IC窗未优于非正（打分与 Top-K 时段可能不同向）；应用前请确认",
      };
    }
    return { ok: true, reason: "已对齐最优参数且 OOS 通过" };
  }

  function refreshParamGridApplyGate() {
    const applyBestBtn = document.getElementById("quant-param-grid-apply-best");
    const meta = document.getElementById("param-grid-meta");
    if (!applyBestBtn) return;
    const gate = paramGridApplyGate();
    applyBestBtn.disabled = !gate.ok;
    if (meta && lastParamGrid && lastParamGrid.best) {
      const best = lastParamGrid.best;
      const base = `最优(样本内) lookback=${best.lookback} · top_k=${best.top_k} · 收益 ${best.total_return_pct}% · ${lastParamGrid.cell_count} 格`;
      if (!gate.ok) {
        meta.textContent = `${base} · 不可应用：${gate.reason}`;
      } else if (gate.warn) {
        meta.textContent = `${base} · ⚠可应用但需确认：${gate.reason}`;
      } else {
        meta.textContent = `${base} · 可应用（OOS 已核对）`;
      }
    }
  }

  function renderT0BacktestResult(data) {
    if (!data || !data.success) {
      renderMetricCards(quantT0Metrics, []);
      if (quantT0Days) quantT0Days.innerHTML = "";
      return;
    }
    const opt = data.optimistic_compare || {};
    const deltaRatio = data.optimistic_delta_ratio_pct ?? opt.delta_pnl_ratio_pct;
    renderMetricCards(quantT0Metrics, [
      {
        label: "含敞口净 PnL",
        value: escapeHtml(String(data.t0_pnl_with_exposure ?? "—")),
        cls: metricClass(data.t0_pnl_with_exposure),
      },
      { label: "完成往返率", value: fmtPct(data.cover_rate_pct) },
      {
        label: "日均 PnL",
        value: escapeHtml(String(data.avg_pnl_per_trade_day ?? "—")),
        cls: metricClass(data.avg_pnl_per_trade_day),
      },
      { label: "参与率", value: fmtPct(data.participate_rate_pct) },
      { label: "乐观Δ占比", value: fmtPct(deltaRatio) },
      { label: "相对底仓%", value: fmtPct(data.pnl_vs_hold_mv_pct) },
      { label: "做T天数", value: escapeHtml(String(data.t0_trade_days ?? "—")) },
      {
        label: "累计 PnL",
        value: escapeHtml(String(data.t0_pnl_total ?? "—")),
        cls: metricClass(data.t0_pnl_total),
      },
      {
        label: "敞口 PnL",
        value: escapeHtml(String(data.exposure_pnl_total ?? "—")),
        cls: metricClass(data.exposure_pnl_total),
      },
      {
        label: "正T PnL",
        value: escapeHtml(String(data.long_t_pnl ?? "—")),
        cls: metricClass(data.long_t_pnl),
      },
      {
        label: "反T PnL",
        value: escapeHtml(String(data.reverse_t_pnl ?? "—")),
        cls: metricClass(data.reverse_t_pnl),
      },
      {
        label: "正/反日",
        value: escapeHtml(`${data.long_t_days ?? 0}/${data.reverse_t_days ?? 0}`),
      },
      { label: "跳过日", value: escapeHtml(String(data.skip_days ?? "—")) },
      { label: "信号跳过", value: escapeHtml(String(data.signal_skip_days ?? "—")) },
      { label: "分钟路径日", value: escapeHtml(String(data.minute_path_days ?? "—")) },
      {
        label: "相对日线Δ",
        value: escapeHtml(
          String(
            (data.daily_compare && data.daily_compare.delta_pnl != null
              ? data.daily_compare.delta_pnl
              : "—")
          )
        ),
        cls: metricClass(data.daily_compare && data.daily_compare.delta_pnl),
      },
    ]);

    const days = (data.days || []).filter(
      (d) =>
        Number(d.sold_qty) > 0 ||
        Number(d.bought_qty) > 0 ||
        Number(d.pnl) !== 0 ||
        Number(d.exposure_pnl) !== 0
    );
    if (!quantT0Days) return;
    if (!days.length) {
      quantT0Days.innerHTML = `<p class="quant-trades-caption">区间内无做 T 成交日</p>`;
      return;
    }
    const rows = days
      .slice(-20)
      .reverse()
      .map((d) => {
        const cls = metricClass(d.pnl);
        return (
          `<tr><td>${escapeHtml(d.date || "")}</td>` +
          `<td class="num">${escapeHtml(String(d.sold_qty ?? 0))}</td>` +
          `<td class="num">${escapeHtml(String(d.covered_qty ?? 0))}</td>` +
          `<td class="num">${escapeHtml(String(d.uncovered_qty ?? 0))}</td>` +
          `<td class="num ${cls}">${escapeHtml(String(d.pnl ?? 0))}</td></tr>`
        );
      })
      .join("");
    quantT0Days.innerHTML =
      `<p class="quant-trades-caption">做 T 日明细（最多 20 条，新→旧）</p>` +
      `<table class="quant-weight-table"><thead><tr>` +
      `<th>日期</th><th>卖出</th><th>买回</th><th>未回补</th><th>PnL</th>` +
      `</tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function loadLastBacktestSnapshot() {
    try {
      const res = await fetch("/api/quant/last");
      const data = await res.json();
      if (!data || data.empty) return;
      if (data.portfolio_backtest_summary && data.portfolio_backtest_summary.success) {
        const ps = data.portfolio_backtest_summary;
        const nc = data.portfolio_neutral_compare_summary;
        const meta = data.snapshot_meta || {};
        const at = formatSnapshotAt(meta.generated_at || data.generated_at);
        let summary = `日报冻结摘要 · ${at} · 累计 ${ps.total_return_pct ?? "—"}% · 胜率 ${ps.win_rate_pct ?? "—"}% · 交易 ${ps.trade_count ?? "—"}`;
        if (nc && nc.success) {
          summary += ` · 中性化 Δ${nc.delta?.total_return_pct ?? "—"}% (${nc.winner})`;
        }
        quantPortfolioSummary.textContent = summary;
        renderBtScopeNote(`${BT_SCOPE_FROZEN} · 冻结于 ${at}`, { warn: true });
        renderMetricCards(quantBtMetrics, [
          { label: "累计收益", value: fmtPct(ps.total_return_pct), cls: metricClass(ps.total_return_pct) },
          { label: "胜率", value: fmtPct(ps.win_rate_pct) },
          { label: "交易次数", value: escapeHtml(String(ps.trade_count ?? "—")) },
          { label: "结果来源", value: "日报冻结" },
          { label: "冻结时间", value: escapeHtml(at) },
        ]);
        if (nc && nc.success) {
          neutralCompareSource = "frozen";
          renderNeutralCompareTable(nc, null, {
            frozen: true,
            frozenAt: at,
          });
        } else {
          renderNeutralCompareTable(null);
        }
        paintPortfolioChart(ps.equity_curve_tail, "无组合摘要曲线");
        setBtTradesCaption("日报仅含摘要曲线；点击「Top-K 回测」加载完整模拟成交账");
      }
    } catch (_) {
      /* ignore */
    }
  }

  const PRESET_FLAG_LABELS = {
    paper_run: "纸面观察池",
    paper_buy: "纸面模拟买入",
    eval_mock: "golden mock",
    eval_agent: "Agent 回归",
    quant_report: "量化日报",
    watching_refresh: "刷新 watching",
    cross_section: "横截面",
    sync_paper_watchlist: "模拟建仓同步",
    paper_rebalance: "纸面调仓",
    export_quant_report: "导出 MD/HTML",
    portfolio_neutral_compare: "中性化对照",
  };

  function renderPresetFlags(presetName) {
    if (!quantOpsPresetFlags) return;
    const preset = dailyPresetsCache.find((p) => p.name === presetName);
    if (!preset || !preset.flags) {
      quantOpsPresetFlags.textContent = "";
      return;
    }
    const labels = Object.entries(PRESET_FLAG_LABELS)
      .filter(([key]) => preset.flags[key])
      .map(([, label]) => label);
    const head = preset.description ? `${preset.description} · ` : "";
    quantOpsPresetFlags.textContent = labels.length
      ? `${head}将跑：${labels.join(" · ")}`
      : head.trim() || "无启用任务";
  }

  function renderNeutralCompareTable(source, targetEl, opts = {}) {
    const host = targetEl || quantNeutralCompareTable;
    if (!host) return;
    if (!source) {
      host.innerHTML = "";
      return;
    }

    let winner;
    let nRet;
    let aRet;
    let nWin;
    let aWin;
    let dRet;
    let dWin;
    let interp;
    let nEx;
    let aEx;
    let dEx;
    let benchLabel;

    if (source.neutralized && source.absolute) {
      const nm = (source.neutralized.metrics || {});
      const am = (source.absolute.metrics || {});
      const delta = source.delta || {};
      winner = source.winner;
      nRet = nm.total_return_pct;
      aRet = am.total_return_pct;
      nWin = nm.win_rate_pct;
      aWin = am.win_rate_pct;
      dRet = delta.total_return_pct;
      dWin = delta.win_rate_pct;
      interp = source.note;
      const bc = source.benchmark_compare || {};
      if (bc.ok) {
        nEx = bc.neutralized_excess_pct;
        aEx = bc.absolute_excess_pct;
        dEx = bc.delta_excess_pct;
        benchLabel = bc.benchmark_label || "基准";
      }
    } else if (source.success) {
      winner = source.winner;
      nRet = source.neutralized_total_return_pct;
      aRet = source.absolute_total_return_pct;
      nWin = source.neutralized_win_rate_pct;
      aWin = source.absolute_win_rate_pct;
      dRet = (source.delta || {}).total_return_pct;
      dWin = (source.delta || {}).win_rate_pct;
      interp = source.interpretation;
    } else {
      host.innerHTML = "";
      return;
    }

    const winnerLabel =
      winner === "neutralized" ? "中性化" : winner === "absolute" ? "绝对分" : "接近";
    const fmtDelta = (v) => (v == null || v === "" ? "—" : `${v}%`);
    const frozen = !!opts.frozen;
    const srcLabel = frozen
      ? `日报冻结${opts.frozenAt ? ` · ${opts.frozenAt}` : ""}`
      : "当次对照";
    const caption =
      `<p class="quant-trades-caption${frozen ? " down" : ""}">中性化对照 · ${escapeHtml(
        srcLabel
      )} · 更优：${escapeHtml(winnerLabel)}</p>`;
    const table =
      researchGridHtml(
        [
          { id: "dim", label: "维度", flex: true },
          { id: "n", label: "中性化", widthPct: 22, num: true },
          { id: "a", label: "绝对分", widthPct: 22, num: true },
          { id: "d", label: "Δ", widthPct: 18, num: true },
        ],
        [
          {
            dim: "累计收益",
            n: nRet != null ? `${nRet}%` : "—",
            a: aRet != null ? `${aRet}%` : "—",
            d: fmtDelta(dRet),
            dCls: metricClass(dRet),
          },
          {
            dim: "胜率",
            n: nWin != null ? `${nWin}%` : "—",
            a: aWin != null ? `${aWin}%` : "—",
            d: fmtDelta(dWin),
            dCls: metricClass(dWin),
          },
          ...(benchLabel
            ? [
                {
                  dim: `超额·${benchLabel}`,
                  n: nEx != null ? `${nEx}%` : "—",
                  a: aEx != null ? `${aEx}%` : "—",
                  d: fmtDelta(dEx),
                  dCls: metricClass(dEx),
                },
              ]
            : []),
        ],
        (col, d) => {
          if (col.id === "d") return metricCell(escapeHtml(d.d), d.dCls);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      (interp ? `<p class="sub">${escapeHtml(interp)}</p>` : "") +
      (frozen
        ? `<p class="sub down">非当次回测结果；点「中性化对照」按当前 lookback/top_k 重算，或点「Top-K 回测」清掉本块。</p>`
        : "");
    // 日报冻结默认折叠，避免「绝对分…个百分点」长期挡在净值曲线上方
    if (frozen) {
      host.innerHTML =
        `<details class="quant-fold quant-neutral-frozen">` +
        `<summary>日报冻结 · 中性化对照（${escapeHtml(winnerLabel)}）· 展开查看</summary>` +
        `<div class="quant-section">${caption}${table}</div>` +
        `</details>`;
      return;
    }
    host.innerHTML = caption + table;
  }

  function renderExportPreviewToc(toc) {
    if (!quantExportPreviewToc) return;
    const entries = (toc && toc.entries) || [];
    if (!entries.length) {
      quantExportPreviewToc.innerHTML = "";
      return;
    }
    quantExportPreviewToc.innerHTML = `<ul>${entries
      .map(
        (e) =>
          `<li><a href="#${e.anchor}" data-export-anchor="${e.anchor}">${e.title}</a></li>`
      )
      .join("")}</ul>`;
  }

  function applyExportPreviewHeadingIds(root, toc) {
    if (!root) return;
    const entries = (toc && toc.entries) || [];
    if (!entries.length) return;
    const used = new Set();
    for (const h of root.querySelectorAll("h1, h2, h3, h4")) {
      const title = (h.textContent || "").trim();
      if (!title) continue;
      const hit =
        entries.find((e) => e.title === title) ||
        entries.find((e) => title.includes(String(e.title || "")));
      if (!hit || !hit.anchor || used.has(hit.anchor)) continue;
      h.id = hit.anchor;
      used.add(hit.anchor);
    }
  }

  function renderExportMarkdownPreview(content, toc) {
    if (!quantExportPreviewBody) return;
    const raw = content || "";
    let html;
    if (typeof marked !== "undefined" && marked.parse) {
      html = marked.parse(raw, { gfm: true, breaks: true });
    } else {
      html = escapeHtml(raw).replace(/\n/g, "<br/>");
    }
    quantExportPreviewBody.innerHTML = `<div class="quant-export-preview-md">${html}</div>`;
    applyExportPreviewHeadingIds(
      quantExportPreviewBody.querySelector(".quant-export-preview-md"),
      toc
    );
  }

  async function previewQuantExport(format) {
    const fmt = format === "html" ? "html" : "markdown";
    setBusyText(quantExportPreviewMeta, `${fmt.toUpperCase()} 预览加载中…`, { busy: true });
    if (quantExportPreviewBody) quantExportPreviewBody.innerHTML = "";
    if (quantExportPreviewToc) quantExportPreviewToc.innerHTML = "";

    const res = await fetch(`/api/quant/export?format=${encodeURIComponent(fmt)}&use_saved=true`);
    const data = await res.json();
    if (!res.ok) {
      setBusyText(
        quantExportPreviewMeta,
        data.detail || data.error || "预览失败",
        { busy: false }
      );
      return data;
    }

    renderExportPreviewToc(data.export_toc);
    setBusyText(
      quantExportPreviewMeta,
      `${data.filename || fmt} · ${
        (data.export_toc && data.export_toc.entries && data.export_toc.entries.length) || 0
      } 个目录项`,
      { busy: false }
    );

    if (!quantExportPreviewBody) return data;

    if (fmt === "html") {
      const iframe = document.createElement("iframe");
      iframe.title = "量化日报 HTML 预览";
      iframe.srcdoc = data.content || "";
      quantExportPreviewBody.innerHTML = "";
      quantExportPreviewBody.appendChild(iframe);
    } else {
      renderExportMarkdownPreview(data.content || "", data.export_toc);
    }
    return data;
  }

  if (quantExportPreviewToc) {
    quantExportPreviewToc.addEventListener("click", (e) => {
      const link = e.target.closest("[data-export-anchor]");
      if (!link) return;
      e.preventDefault();
      const anchor = link.getAttribute("data-export-anchor");
      if (!anchor || !quantExportPreviewBody) return;
      const iframe = quantExportPreviewBody.querySelector("iframe");
      if (iframe) {
        try {
          const doc = iframe.contentDocument;
          const target = doc && doc.getElementById(anchor);
          if (target) {
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            return;
          }
        } catch (_err) {
          /* ignore cross-origin */
        }
      }
      const target = quantExportPreviewBody.querySelector(`#${CSS.escape(anchor)}`);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      /* 兜底：按标题文本定位 */
      for (const h of quantExportPreviewBody.querySelectorAll("h1, h2, h3, h4")) {
        if ((h.textContent || "").includes(link.textContent.trim())) {
          h.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        }
      }
    });
  }

  function renderThresholdTable(suggest) {
    if (!quantThresholdTable) return;
    if (!suggest || !suggest.success) {
      quantThresholdTable.innerHTML =
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
    quantThresholdTable.innerHTML = researchGridHtml(
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

  function describeWatchingSource(src, index) {
    const typ = String(src.type || "").toLowerCase() || "—";
    const label = `S${index + 1}`;
    const typLabel = typ === "static" ? "静态" : typ === "screen" ? "筛选" : typ;
    if (typ === "static") {
      const codes = (src.codes || []).map((c) => String(c));
      return {
        label,
        typ,
        typLabel,
        title: label,
        detail: codes.length ? codes.join("、") : "（空）",
        codes,
      };
    }
    if (typ === "screen") {
      const bits = [];
      if (src.sector) bits.push(String(src.sector));
      if (src.pe_min != null) bits.push(`PE≥${src.pe_min}`);
      if (src.pe_max != null) bits.push(`PE≤${src.pe_max}`);
      if (src.pb_max != null) bits.push(`PB≤${src.pb_max}`);
      if (src.change_min != null) bits.push(`涨跌≥${src.change_min}%`);
      if (src.change_max != null) bits.push(`涨跌≤${src.change_max}%`);
      if (src.limit != null) bits.push(`取${src.limit}`);
      return {
        label,
        typ,
        typLabel,
        title: label,
        detail: bits.join(" · ") || "筛选",
        codes: [],
      };
    }
    return {
      label,
      typ,
      typLabel: typ,
      title: label,
      detail: JSON.stringify(src),
      codes: [],
    };
  }

  function shortOriginLabel(raw) {
    const text = String(raw || "").trim();
    if (!text) return "—";
    const m = text.match(/^S(\d+)/i);
    if (m) return `S${m[1]}`;
    if (text.includes("筛选") || text.includes("screen") || text.includes("合并")) {
      return "筛选";
    }
    return text;
  }

  function matchWatchlistSource(code, sourceDescs) {
    const raw = String(code || "");
    for (const s of sourceDescs) {
      if (s.typ !== "static") continue;
      for (const c of s.codes) {
        if (c === raw || raw.includes(c) || c.includes(raw)) {
          return s.label;
        }
      }
    }
    return "筛选";
  }

  async function removeWatchingWatchItem(code) {
    const c = String(code || "").trim();
    if (!c) return;
    setWatchingRefreshStatus(`正在移除「${c}」…`);
    try {
      const res = await fetch("/api/watching/watchlist/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: c, sync_paper: false }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      await loadWatchingPanel();
      setWatchingRefreshStatus(data.message || "已移除");
    } catch (err) {
      const msg = String(err.message || err);
      setWatchingRefreshStatus(msg, { error: true });
    }
  }

  async function removeSelectedWatchingItems() {
    const codes = getSelectedWatchingCodes();
    if (!codes.length) {
      setWatchingRefreshStatus("请先勾选要移除的股票", { error: true });
      return;
    }
    const ok = window.confirm(
      codes.length === 1
        ? `确定从观察名单移除「${codes[0]}」？\n（不影响模拟持仓）`
        : `确定从观察名单移除已勾选的 ${codes.length} 只？\n（不影响模拟持仓）`
    );
    if (!ok) return;
    setWatchingRefreshStatus(`正在移除 ${codes.length} 只…`);
    let done = 0;
    const errors = [];
    for (const c of codes) {
      try {
        const res = await fetch("/api/watching/watchlist/remove", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: c, sync_paper: false }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        done += 1;
      } catch (err) {
        errors.push(`${c}: ${String(err.message || err)}`);
      }
    }
    await loadWatchingPanel();
    if (errors.length) {
      setWatchingRefreshStatus(
        `已移除 ${done} 只 · 失败 ${errors.length}：${errors[0]}`,
        { error: true }
      );
    } else {
      setWatchingRefreshStatus(`已从观察名单移除 ${done} 只`);
    }
  }

  function watchingCodeKey(code) {
    const c = String(code || "").trim();
    if (!c) return "";
    if (/^\d{1,5}$/.test(c)) return c.padStart(5, "0"); // 港股常见
    if (/^\d{6}$/.test(c)) return c;
    return c;
  }

  function indexByWatchingCode(items) {
    const byCode = {};
    for (const it of items || []) {
      const c = watchingCodeKey(it.stock_code || it.code);
      if (c) byCode[c] = it;
    }
    return byCode;
  }

  async function fillWatchingQuotes() {
    if (watchingGrid && watchingGridReady) {
      const codes = (watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
      if (!codes.length) return;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 20000);
      setWatchingRefreshStatus("正在拉取行情…");
      try {
        const res = await fetch(
          `/api/watching/quotes?codes=${encodeURIComponent(codes.join(","))}`,
          { signal: ctrl.signal }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || res.statusText);
        const byCode = indexByWatchingCode(data.items || []);
        codes.forEach((code) => {
          const it = byCode[watchingCodeKey(code)] || {};
          const row = watchingGrid.getRow(code);
          if (!row) return;
          const chgNum =
            it.ok && it.change_percent != null && !Number.isNaN(Number(it.change_percent))
              ? Number(it.change_percent)
              : null;
          const chgTxt =
            chgNum != null
              ? `${chgNum >= 0 ? "+" : ""}${chgNum.toFixed(2)}%`
              : "—";
          const chgCls =
            chgNum == null || chgNum === 0 ? "" : chgNum > 0 ? "is-up" : "is-down";
          const volNum = it.ok ? parseWatchingVolume(it.volume) : NaN;
          const market = String(it.market || "").toUpperCase();
          row.update({
            price: it.ok && it.price != null ? String(it.price) : "—",
            chg: chgTxt,
            chgCls,
            vol: it.ok && it.volume != null ? String(it.volume) : "—",
            volNum: Number.isFinite(volNum) ? volNum : null,
            market: market === "CN" ? "A股" : market === "HK" ? "港股" : market === "US" ? "美股" : "",
            name: it.ok && it.stock_name ? String(it.stock_name) : row.getData().name,
          });
        });
        const okN = (data.items || []).filter((x) => x.ok).length;
        setWatchingRefreshStatus(`行情已更新 · ${okN}/${codes.length}`);
        sortWatchingTableRows();
      } catch (err) {
        const msg =
          err && err.name === "AbortError"
            ? "行情拉取超时"
            : String((err && err.message) || err);
        setWatchingRefreshStatus(msg, { error: true });
      } finally {
        clearTimeout(timer);
      }
      return;
    }
    // 原生表回退路径
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const trs = watchTable.querySelectorAll("tr[data-code]");
    if (!trs.length) return;
    const codes = Array.from(trs).map((r) => r.dataset.code).filter(Boolean);
    try {
      const res = await fetch(`/api/watching/quotes?codes=${encodeURIComponent(codes.join(","))}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      trs.forEach((tr) => {
        const it = byCode[watchingCodeKey(tr.dataset.code)] || {};
        const setTxt = (key, val) => {
          const el = tr.querySelector(`[data-q='${key}']`);
          if (el) el.textContent = val != null && val !== "" ? String(val) : "—";
        };
        setTxt("price", it.ok ? it.price : null);
        setTxt("vol", it.ok ? it.volume : null);
        const chgEl = tr.querySelector(`[data-q='chg']`);
        if (chgEl) {
          const chgNum =
            it.ok && it.change_percent != null && !Number.isNaN(Number(it.change_percent))
              ? Number(it.change_percent)
              : null;
          chgEl.textContent =
            chgNum != null
              ? `${chgNum >= 0 ? "+" : ""}${chgNum.toFixed(2)}%`
              : "—";
          chgEl.classList.remove("is-up", "is-down");
          if (chgNum != null && chgNum !== 0) {
            chgEl.classList.add(chgNum > 0 ? "is-up" : "is-down");
          }
        }
        if (it.ok && it.stock_name) {
          applyWatchingNameEl(tr.querySelector(".watching-name-text"), it.stock_name);
        }
      });
      setWatchingRefreshStatus(`行情已更新 · ${(data.items || []).filter((x) => x.ok).length}/${codes.length}`);
    } catch (err) {
      setWatchingRefreshStatus(String(err.message || err), { error: true });
    }
  }

  const watchingScoreTips = createScoreTooltipController();

  function watchingScoreDetail(it) {
    return JSON.stringify({
      formula: (it && it.score_formula) || "",
      reasons: (it && it.score_reasons) || [],
      hard_reject: !!(it && it.hard_reject),
      reject_reason: (it && it.reject_reason) || "",
      weight_source: (it && it.weight_source) || "",
      cluster_label: (it && it.cluster_label) || "",
      cluster_mode: (it && it.cluster_mode) || "",
      cluster_version: it && it.cluster_version,
      score_global: it && it.score_global,
      score_cluster: it && it.score_cluster,
      min_score: it && it.min_score,
      below_min_score: !!(it && it.below_min_score),
    });
  }

  async function fillWatchingInsights() {
    const useGrid = !!(watchingGrid && watchingGridReady);
    const watchTable = document.getElementById("watching-watchlist-table");
    const codes = useGrid
      ? (watchingGrid.getData() || []).map((r) => r.code).filter(Boolean)
      : Array.from(watchTable?.querySelectorAll("tr[data-code]") || [])
          .map((r) => r.dataset.code)
          .filter(Boolean);
    if (!codes.length) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 100000);
    try {
      const res = await fetch(
        `/api/watching/insights?codes=${encodeURIComponent(codes.join(","))}`,
        { signal: ctrl.signal }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      codes.forEach((code) => {
        const it = byCode[watchingCodeKey(code)] || {};
        let excess = null;
        if (it.excess_return_pct != null && !Number.isNaN(Number(it.excess_return_pct))) {
          excess = `${Number(it.excess_return_pct) >= 0 ? "+" : ""}${Number(it.excess_return_pct).toFixed(1)}%${
            it.excess_label ? ` ${it.excess_label}` : ""
          }`;
        } else if (it.excess_label) {
          excess = String(it.excess_label);
        }
        const scoreNum = it.score != null && !Number.isNaN(Number(it.score)) ? Number(it.score) : null;
        const belowMin = !!it.below_min_score;
        const scoreDetail = watchingScoreDetail(it);
        const scoreText =
          scoreNum == null
            ? "—"
            : belowMin
              ? `${scoreNum.toFixed(0)}↓`
              : scoreNum.toFixed(0);
        const scoreTitle = belowMin
          ? `低于选股门槛 ${it.min_score ?? "—"}（仍显示分数）`
          : "悬停查看评分与权重来源";
        const volNum = it.volume != null ? parseWatchingVolume(it.volume) : NaN;
        if (useGrid) {
          const row = watchingGrid.getRow(code);
          if (!row) return;
          row.update({
            score: scoreText,
            scoreNum,
            scoreDetail,
            scoreTitle,
            scoreBelowMin: belowMin,
            stance: it.stance_short || "—",
            excess: excess || "—",
            excessNum:
              it.excess_return_pct != null && !Number.isNaN(Number(it.excess_return_pct))
                ? Number(it.excess_return_pct)
                : null,
            vol: it.volume != null ? String(it.volume) : row.getData().vol,
            volNum: Number.isFinite(volNum) ? volNum : row.getData().volNum,
            volr:
              it.volume_ratio != null && !Number.isNaN(Number(it.volume_ratio))
                ? Number(it.volume_ratio).toFixed(2)
                : "—",
            pe: it.pe != null && !Number.isNaN(Number(it.pe)) ? Number(it.pe).toFixed(1) : "—",
            pb: it.pb != null && !Number.isNaN(Number(it.pb)) ? Number(it.pb).toFixed(2) : "—",
            isHardReject: !!it.hard_reject,
          });
        } else {
          const tr = watchTable?.querySelector(
            `tr[data-code="${String(code).replace(/"/g, "")}"]`
          );
          if (!tr) return;
          const setTxt = (key, val) => {
            const el = tr.querySelector(`[data-q='${key}']`);
            if (el) el.textContent = val != null && val !== "" ? String(val) : "—";
          };
          const scoreEl = tr.querySelector(`[data-q='score']`);
          if (scoreEl) {
            scoreEl.innerHTML =
              `<span class="watching-score-cell paper-hold-score has-tip${
                belowMin ? " score-below-min" : ""
              }" ` +
              `data-score-detail="${escapeHtml(scoreDetail)}" title="${escapeHtml(scoreTitle)}">` +
              `${escapeHtml(scoreText)}</span>`;
          }
          setTxt("stance", it.stance_short || null);
          setTxt("excess", excess);
          if (it.volume) setTxt("vol", it.volume);
          setTxt(
            "volr",
            it.volume_ratio != null && !Number.isNaN(Number(it.volume_ratio))
              ? Number(it.volume_ratio).toFixed(2)
              : null
          );
          setTxt("pe", it.pe != null && !Number.isNaN(Number(it.pe)) ? Number(it.pe).toFixed(1) : null);
          setTxt("pb", it.pb != null && !Number.isNaN(Number(it.pb)) ? Number(it.pb).toFixed(2) : null);
        }
      });
      const okN = (data.items || []).filter((x) => x.ok).length;
      const items = data.items || [];
      const srcSample = items.find((x) => x && x.weight_source) || {};
      const mode = srcSample.cluster_mode || "";
      const wsrc = String(srcSample.weight_source || "");
      let scoreMode = "全局权";
      if (wsrc.startsWith("cluster:")) scoreMode = `组权 · ${wsrc.slice("cluster:".length) || "组"}`;
      else if (wsrc === "global+shadow") scoreMode = "对照中 · 主分全局";
      else if (wsrc === "global_fallback") scoreMode = "全局回退（未映射）";
      else if (mode === "active") scoreMode = "active · 组权优先";
      else if (mode === "shadow") scoreMode = "shadow · 主分全局";
      setWatchingRefreshStatus(
        `摘要已更新 · ${okN}/${codes.length} · score ${scoreMode}（与交易执行同源）`
      );
      if (useGrid) sortWatchingTableRows();
    } catch (err) {
      if (useGrid) {
        codes.forEach((code) => {
          const row = watchingGrid.getRow(code);
          if (!row) return;
          const d = row.getData() || {};
          row.update({
            score: d.score === "…" ? "—" : d.score,
            stance: d.stance === "…" ? "—" : d.stance,
            excess: d.excess === "…" ? "—" : d.excess,
            volr: d.volr === "…" ? "—" : d.volr,
            pe: d.pe === "…" ? "—" : d.pe,
            pb: d.pb === "…" ? "—" : d.pb,
          });
        });
      }
      const msg =
        err && err.name === "AbortError"
          ? "摘要超时（页面仍可用）"
          : `摘要失败：${String((err && err.message) || err)}`;
      setWatchingRefreshStatus(msg, { error: true });
    } finally {
      clearTimeout(timer);
    }
  }

  function getSelectedWatchingCodes() {
    if (watchingGrid && watchingGridReady) {
      return (watchingGrid.getData() || [])
        .filter((r) => r && r.picked)
        .map((r) => String(r.code || "").trim())
        .filter(Boolean);
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return [];
    return Array.from(watchTable.querySelectorAll(".watching-pick:checked"))
      .map((el) => String(el.value || el.dataset.code || "").trim())
      .filter(Boolean);
  }

  function updateWatchingPickCount() {
    const el = document.getElementById("watching-pick-count");
    const n = getSelectedWatchingCodes().length;
    if (el) {
      el.textContent = n ? `已勾选 ${n} 只` : "未勾选";
      el.classList.toggle("is-active", n > 0);
    }
    const removeBtn = document.getElementById("quant-watching-remove");
    const syncBtn = document.getElementById("quant-watching-sync");
    if (removeBtn) removeBtn.disabled = n === 0;
    if (syncBtn) syncBtn.disabled = n === 0;
  }

  function syncWatchingSelectAllState() {
    const master = document.getElementById("watching-select-all");
    updateWatchingPickCount();
    if (!master) return;
    if (watchingGrid && watchingGridReady) {
      const rows = watchingGrid.getData() || [];
      if (!rows.length) {
        master.checked = false;
        master.indeterminate = false;
        return;
      }
      const n = rows.filter((r) => r && r.picked).length;
      master.checked = n === rows.length;
      master.indeterminate = n > 0 && n < rows.length;
      return;
    }
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const boxes = Array.from(watchTable.querySelectorAll(".watching-pick"));
    if (!boxes.length) {
      master.checked = false;
      master.indeterminate = false;
      return;
    }
    const n = boxes.filter((b) => b.checked).length;
    master.checked = n === boxes.length;
    master.indeterminate = n > 0 && n < boxes.length;
  }

  function truncateText(s, n) {
    const t = String(s || "").trim();
    if (!t) return "";
    if (t.length <= n) return t;
    return `${t.slice(0, Math.max(0, n - 1))}…`;
  }

  function sentimentLabelZh(label) {
    const m = {
      bullish: "多",
      bearish: "空",
      mixed: "杂",
      neutral: "中",
    };
    return m[label] || "中";
  }

  function sentimentBadgeHtml(sent, code) {
    const s = sent || {};
    const label = String(s.label || "neutral");
    const cls =
      label === "bullish"
        ? "is-bull"
        : label === "bearish"
          ? "is-bear"
          : label === "mixed"
            ? "is-mixed"
            : "is-neutral";
    const hits = []
      .concat(s.hit_pos || [])
      .concat(s.hit_neg || [])
      .slice(0, 6);
    const scorePart =
      s.score != null && !Number.isNaN(Number(s.score))
        ? ` · 风险 ${Number(s.score).toFixed(2)}`
        : "";
    const title = [
      s.note || "规则关键词，非模型",
      hits.length ? `命中：${hits.join("、")}` : "无关键词命中",
      scorePart.trim(),
    ]
      .filter(Boolean)
      .join(" · ");
    return (
      `<span class="watching-sent-badge ${cls}" data-code="${escapeHtml(code || "")}" title="${escapeHtml(title)}">` +
      `${sentimentLabelZh(label)}</span>`
    );
  }

  let watchingAlertCodes = new Set();
  let watchingSentimentByCode = {};
  const WATCHING_SORT_STORAGE = "watching_table_sort_v1";
  let watchingSortKey = null; // score | vol | null
  let watchingSortDir = "desc"; // asc | desc
  let watchingGrid = null;
  let watchingGridReady = false;
  try {
    const saved = JSON.parse(sessionStorage.getItem(WATCHING_SORT_STORAGE) || "null");
    if (saved && (saved.key === "score" || saved.key === "vol")) {
      watchingSortKey = saved.key;
      watchingSortDir = saved.dir === "asc" ? "asc" : "desc";
    }
  } catch (_) {
    /* ignore */
  }

  function persistWatchingSort() {
    try {
      if (!watchingSortKey) {
        sessionStorage.removeItem(WATCHING_SORT_STORAGE);
        return;
      }
      sessionStorage.setItem(
        WATCHING_SORT_STORAGE,
        JSON.stringify({ key: watchingSortKey, dir: watchingSortDir })
      );
    } catch (_) {
      /* ignore */
    }
  }

  function parseWatchingVolume(raw) {
    if (raw == null || raw === "") return NaN;
    if (typeof raw === "number") return Number.isFinite(raw) ? raw : NaN;
    let s = String(raw).trim().replace(/,/g, "").replace(/\s/g, "");
    if (!s || s === "—" || s === "…") return NaN;
    s = s.replace(/手$/u, "");
    let mult = 1;
    if (/亿$/u.test(s)) {
      mult = 1e8;
      s = s.replace(/亿$/u, "");
    } else if (/万$/u.test(s)) {
      mult = 1e4;
      s = s.replace(/万$/u, "");
    }
    const n = Number(s);
    return Number.isFinite(n) ? n * mult : NaN;
  }

  function refreshWatchingSortHeaders() {
    // 虚拟表表头自带排序状态
  }

  function sortWatchingTableRows() {
    if (!watchingGrid || !watchingGridReady) return;
    if (!watchingSortKey) {
      watchingGrid.clearSort();
      return;
    }
    watchingGrid.setSort([{ column: watchingSortKey, dir: watchingSortDir }]);
  }

  function hideWatchingNewsDetail() {
    const panel = document.getElementById("watching-news-detail");
    if (panel) panel.hidden = true;
  }

  // ---- 观察页日线图 ----
  function niceTicks(min, max, count) {
    if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
      return [min];
    }
    const range = max - min;
    const step = Math.pow(10, Math.floor(Math.log10(range / count)));
    const err = (range / count) / step;
    let mult = 1;
    if (err >= 7.5) mult = 10;
    else if (err >= 3.5) mult = 5;
    else if (err >= 1.5) mult = 2;
    const tickStep = mult * step;
    const start = Math.ceil(min / tickStep) * tickStep;
    const ticks = [];
    for (let v = start; v <= max + tickStep * 0.01; v += tickStep) {
      ticks.push(Math.round(v / tickStep) * tickStep);
    }
    return ticks;
  }

  function fmtAxisY(v) {
    if (Math.abs(v) >= 100) return v.toFixed(0);
    if (Math.abs(v) >= 10) return v.toFixed(1);
    return v.toFixed(2);
  }

  function fmtAxisX(s) {
    if (!s) return "";
    return String(s).slice(5);
  }

  async function paintPortfolioChart(curve, emptyText, benchCurve, benchLabel, icAlign) {
    const host = quantPortfolioChart;
    if (!host) return;
    const legendEl = document.getElementById("quant-portfolio-legend");
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity ?? p.equity_norm ?? p.value),
        };
      });
    const markers = buildIcAlignMarkers(icAlign);
    const pts = toPts(curve);
    const bpts = toPts(benchCurve);
    if (bpts.length >= 2 && pts.length >= 2) {
      if (legendEl) {
        legendEl.textContent =
          `Top-K 净值（蓝）vs ${benchLabel || "基准"}（绿）。` +
          (markers.length
            ? "标记：绿点=正IC窗结束 · 灰点=非正IC窗。"
            : "起点 100；横轴为调仓期结束日。") +
          "超额看指标卡，勿只看绝对累计。";
      }
      await renderDualLineChart(host, pts, bpts, {
        emptyText,
        disableZoom: true,
        markers,
      });
      return;
    }
    if (legendEl) {
      legendEl.textContent =
        "研究用 Top-K 回测净值（非纸面账本）。起点 100。" +
        (markers.length
          ? " 标记：绿点=正IC窗结束 · 灰点=非正IC窗。"
          : " 单线=当次回测；对照时蓝=中性化、绿=绝对分。");
    }
    await renderLineChart(host, pts, { emptyText, disableZoom: true, markers });
  }

  function buildIcAlignMarkers(align) {
    const periods = (align && align.ok && align.periods_tail) || [];
    const out = [];
    const seen = new Set();
    periods.forEach((p) => {
      const t = String(p.end_date || "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(t) || seen.has(t)) return;
      seen.add(t);
      const pos = p.bucket === "pos";
      out.push({
        time: t,
        position: "belowBar",
        color: pos ? "#059669" : "#9ca3af",
        shape: "circle",
        text: pos ? "+" : "−",
      });
    });
    return out.slice(-40);
  }

  async function paintDualPortfolioChart(seriesA, seriesB, emptyText) {
    const host = quantPortfolioChart;
    if (!host) return;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (legendEl) {
      legendEl.textContent =
        "中性化对照双曲线。起点 100；横轴为持有期结束日。蓝=截面中性化打分 · 绿=绝对分打分（同一观察池与参数）。";
    }
    await renderDualLineChart(host, seriesA, seriesB, { emptyText, disableZoom: true });
  }

  function renderUniversePanel(uni) {
    const el = document.getElementById("quant-universe-panel");
    if (!el) return;
    if (!uni) {
      el.innerHTML = "";
      return;
    }
    const src =
      uni.source === "include_only"
        ? "include_only"
        : uni.source === "watching_minus_exclude"
          ? "watching−exclude"
          : uni.source === "explicit"
            ? "显式 codes"
            : String(uni.source || "—");
    const excl = (uni.excluded || []).slice(0, 8).join("、") || "—";
    const dropped = uni.dropped_thin || [];
    const fail = uni.load_failures || [];
    const dropRows = [];
    dropped.slice(0, 12).forEach((d) => {
      dropRows.push({
        code: String(d.stock_code || "—"),
        reason: "短序列",
        detail: String(d.reason || d.bars || "—"),
      });
    });
    fail.slice(0, 8).forEach((f) => {
      dropRows.push({
        code: String(f),
        reason: "拉日线失败",
        detail: "过短/失败",
      });
    });
    el.innerHTML =
      `<p class="quant-trades-caption">验证宇宙 · ${escapeHtml(src)} · 候选 ${
        uni.candidate_count ?? "—"
      } · 载入 ${uni.loaded_count ?? "—"}` +
      (uni.watching_count != null ? ` · 观察 ${uni.watching_count}` : "") +
      (uni.dropped_thin_count ? ` · 排除短序列 ${uni.dropped_thin_count}` : "") +
      `</p>` +
      `<p class="sub">exclude：${escapeHtml(excl)}</p>` +
      (dropRows.length
        ? researchGridHtml(
            [
              { id: "code", label: "代码", widthPct: 22 },
              { id: "reason", label: "原因", widthPct: 22 },
              { id: "detail", label: "说明", flex: true },
            ],
            dropRows,
            (col, d) => escapeHtml(d[col.id] ?? "—"),
            { emptyText: "无剔除项" }
          )
        : `<p class="sub">${escapeHtml(uni.note || "")}</p>`);
    const filt = uni.filters || {};
    const fd = uni.filter_dropped || [];
    if (filt.exclude_st || filt.min_avg_amount_pctile != null || fd.length) {
      const frows = fd.slice(0, 12).map((d) => ({
        code: String(d.stock_code || "—"),
        reason: "过滤",
        detail: String(d.reason || "—"),
      }));
      el.innerHTML +=
        `<p class="sub">过滤 · 剔ST=${filt.exclude_st ? "是" : "否"}` +
        (filt.min_avg_amount_pctile != null
          ? ` · 成交额≥${filt.min_avg_amount_pctile}%分位`
          : "") +
        ` · 保留 ${filt.kept ?? "—"} · 剔除 ${filt.dropped_count ?? fd.length}</p>` +
        (frows.length
          ? researchGridHtml(
              [
                { id: "code", label: "代码", widthPct: 22 },
                { id: "reason", label: "原因", widthPct: 18 },
                { id: "detail", label: "说明", flex: true },
              ],
              frows,
              (col, d) => escapeHtml(d[col.id] ?? "—")
            )
          : "");
    }
  }

  function renderScoreIc(sic) {
    const el = document.getElementById("quant-score-ic");
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!el) return;
    if (!sic) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    if (!sic.ok) {
      el.innerHTML = `<p class="quant-trades-caption">截面 IC：${escapeHtml(
        sic.reason || "不可用"
      )}</p>`;
      if (chartWrap) chartWrap.hidden = true;
      return;
    }
    const posLabel =
      sic.positive_ic_days != null && sic.day_count
        ? `${sic.positive_ic_days}/${sic.day_count}`
        : "—";
    el.innerHTML =
      `<p class="quant-trades-caption">截面 IC · 日数 ${sic.day_count ?? "—"} · horizon ${
        sic.horizon_days ?? "—"
      }d` +
      (sic.roll_window ? ` · 滚动窗 ${sic.roll_window}` : "") +
      `</p>` +
      researchGridHtml(
        [
          { id: "ic", label: "IC均值", widthPct: 14, num: true },
          { id: "std", label: "IC标准差", widthPct: 14, num: true },
          { id: "icir", label: "ICIR", widthPct: 12, num: true, title: "IC均值/IC标准差；看截面预测力是否稳定" },
          { id: "pos", label: "正IC日", widthPct: 14, num: true },
          { id: "note", label: "说明", flex: true },
        ],
        [
          {
            ic: sic.ic_mean != null ? String(sic.ic_mean) : "—",
            std: sic.ic_std != null ? String(sic.ic_std) : "—",
            icir: sic.icir != null ? String(sic.icir) : "—",
            pos: posLabel,
            note: sic.note || "池内 score vs 远期收益",
          },
        ],
        (col, d) => escapeHtml(d[col.id] ?? "—")
      );
    paintIcChart(sic);
  }

  async function paintIcChart(sic) {
    const chartWrap = document.getElementById("quant-ic-chart-wrap");
    const chartHost = document.getElementById("quant-ic-chart");
    if (!chartHost || !chartWrap) return;
    const daily = sic.ic_series_tail || [];
    const rolling = sic.ic_rolling_tail || [];
    const toPts = (rows, key) =>
      (rows || []).map((p, i) => {
        const t = String(p.date || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p[key] ?? p.ic),
        };
      });
    const dPts = toPts(daily, "ic");
    const rPts = toPts(rolling, "ic");
    if (dPts.length < 2 && rPts.length < 2) {
      chartWrap.hidden = true;
      return;
    }
    chartWrap.hidden = false;
    const series = [];
    if (dPts.length >= 2) {
      series.push({ label: "日度IC", color: "#93c5fd", lineWidth: 1.5, points: dPts });
    }
    if (rPts.length >= 2) {
      series.push({ label: "滚动IC", color: "#059669", lineWidth: 2.5, points: rPts });
    }
    // 零轴参考：用极短水平线不合适；双线足够。若仅一条也画。
    if (series.length === 1) {
      await renderLineChart(chartHost, series[0].points, {
        emptyText: "IC 序列不足",
        disableZoom: true,
      });
      return;
    }
    await renderMultiLineChart(chartHost, series, {
      emptyText: "IC 序列不足",
      disableZoom: true,
    });
  }

  function renderIcEquityAlign(align) {
    const el = document.getElementById("quant-ic-align");
    if (!el) return;
    if (!align) {
      el.innerHTML = "";
      return;
    }
    if (!align.ok) {
      el.innerHTML = `<p class="quant-trades-caption">IC↔净值对齐：${escapeHtml(
        align.reason || "不可用"
      )}</p>`;
      return;
    }
    const pos = align.pos_ic || {};
    const neg = align.neg_ic || {};
    const spread = align.avg_return_spread_pp;
    const favor = align.aligned_favor_pos_ic;
    const headCls = favor === false ? " down" : "";
    const head =
      `<p class="quant-trades-caption${headCls}">IC↔净值对齐 · ${align.period_count ?? "—"} 期` +
      (spread != null
        ? ` · 正IC窗均收益−非正 ${Number(spread) >= 0 ? "+" : ""}${spread}pp`
        : "") +
      (favor === false ? " · ⚠正IC窗未优于非正" : favor ? " · 同向" : "") +
      `</p>`;
    el.innerHTML =
      head +
      researchGridHtml(
        [
          { id: "bucket", label: "分桶", widthPct: 18 },
          { id: "n", label: "期数", widthPct: 12, num: true },
          { id: "avg", label: "均期收益", widthPct: 16, num: true },
          { id: "win", label: "胜率", widthPct: 14, num: true },
          { id: "tot", label: "复利累计", widthPct: 16, num: true },
          { id: "note", label: "", flex: true },
        ],
        [
          {
            bucket: "正IC窗",
            n: String(pos.count ?? "—"),
            avgText: pos.avg_return_pct != null ? `${pos.avg_return_pct}%` : "—",
            avgCls: metricClass(pos.avg_return_pct),
            win: pos.win_rate_pct != null ? `${pos.win_rate_pct}%` : "—",
            totText:
              pos.total_return_compound_pct != null
                ? `${pos.total_return_compound_pct}%`
                : "—",
            totCls: metricClass(pos.total_return_compound_pct),
            note: "",
          },
          {
            bucket: "非正IC窗",
            n: String(neg.count ?? "—"),
            avgText: neg.avg_return_pct != null ? `${neg.avg_return_pct}%` : "—",
            avgCls: metricClass(neg.avg_return_pct),
            win: neg.win_rate_pct != null ? `${neg.win_rate_pct}%` : "—",
            totText:
              neg.total_return_compound_pct != null
                ? `${neg.total_return_compound_pct}%`
                : "—",
            totCls: metricClass(neg.total_return_compound_pct),
            note: "",
          },
        ],
        (col, d) => {
          if (col.id === "avg") return metricCell(d.avgText, d.avgCls);
          if (col.id === "tot") return metricCell(d.totText, d.totCls);
          if (col.id === "bucket") return escapeHtml(d.bucket);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      (align.note ? `<p class="sub">${escapeHtml(align.note)}</p>` : "");
  }

  function renderQuantileTable(qb) {
    const el = document.getElementById("quant-quantile-table");
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!el) return;
    if (!qb) {
      el.innerHTML = "";
      if (chartWrap) chartWrap.hidden = true;
      if (chartHost) chartHost.replaceChildren();
      return;
    }
    if (!qb.ok) {
      el.innerHTML = `<p class="quant-trades-caption">分层回测：${escapeHtml(
        qb.reason || "不可用"
      )}</p>`;
      if (chartWrap) chartWrap.hidden = true;
      return;
    }
    const mono = qb.monotonic_increasing;
    const ls = qb.q_high_minus_q_low_pct;
    const warn = mono === false ? " · 非单调（打分区分度弱或噪声大）" : "";
    const head =
      `<p class="quant-trades-caption${mono === false ? " down" : ""}">分层 Q1–Q${
        qb.n_quantiles || 5
      } · ${qb.fold_count ?? "—"} 期` +
      (ls != null ? ` · Q高−Q低 ${Number(ls) >= 0 ? "+" : ""}${ls}%` : "") +
      (mono === true ? " · 单调↑" : warn) +
      `</p>`;
    const rows = qb.quantiles || [];
    if (!rows.length) {
      el.innerHTML = head;
      if (chartWrap) chartWrap.hidden = true;
      return;
    }
    el.innerHTML =
      head +
      researchGridHtml(
        [
          { id: "label", label: "分层", widthPct: 18 },
          { id: "ret", label: "累计收益", widthPct: 16, num: true },
          { id: "win", label: "胜率", widthPct: 14, num: true },
          { id: "n", label: "期数", widthPct: 12, num: true },
          { id: "eq", label: "终值", widthPct: 14, num: true },
          { id: "note", label: "", flex: true },
        ],
        rows.map((r) => ({
          label: r.label || `Q${r.quantile}`,
          retText:
            r.total_return_pct != null ? `${Number(r.total_return_pct).toFixed(2)}%` : "—",
          retCls: metricClass(r.total_return_pct),
          win: r.win_rate_pct != null ? `${Number(r.win_rate_pct).toFixed(1)}%` : "—",
          n: String(r.trade_count ?? "—"),
          eq: r.final_equity != null ? String(r.final_equity) : "—",
          note: "",
        })),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          if (col.id === "label") return escapeHtml(d.label);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      (qb.note ? `<p class="sub">${escapeHtml(qb.note)}</p>` : "");
    paintQuantileChart(qb);
  }

  const Q_COLORS = ["#9ca3af", "#93c5fd", "#60a5fa", "#34d399", "#059669", "#047857", "#065f46"];

  async function paintQuantileChart(qb) {
    const chartWrap = document.getElementById("quant-quantile-chart-wrap");
    const chartHost = document.getElementById("quant-quantile-chart");
    if (!chartHost || !chartWrap) return;
    const rows = (qb && qb.quantiles) || [];
    const series = rows
      .map((r, i) => {
        const curve = r.equity_curve_tail || r.equity_curve || [];
        if (!curve.length) return null;
        const n = rows.length;
        const isEdge = i === 0 || i === n - 1;
        return {
          label: r.label || `Q${r.quantile}`,
          color: Q_COLORS[Math.min(i, Q_COLORS.length - 1)],
          lineWidth: isEdge ? 2.5 : 1.5,
          points: curve.map((p, j) => {
            const t = String(p.date || "").slice(0, 10);
            return {
              time: /^\d{4}-\d{2}-\d{2}$/.test(t)
                ? t
                : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
              value: Number(p.equity ?? p.value),
            };
          }),
        };
      })
      .filter(Boolean);
    const ls = qb.long_short_equity_curve || [];
    if (ls.length >= 2) {
      series.push({
        label: "Q高−Q低",
        color: "#b45309",
        lineWidth: 2.5,
        points: ls.map((p, j) => {
          const t = String(p.date || "").slice(0, 10);
          return {
            time: /^\d{4}-\d{2}-\d{2}$/.test(t)
              ? t
              : new Date(Date.UTC(2020, 0, 1 + j)).toISOString().slice(0, 10),
            value: Number(p.equity ?? p.value),
          };
        }),
      });
    }
    if (series.length < 2) {
      chartWrap.hidden = true;
      return;
    }
    chartWrap.hidden = false;
    await renderMultiLineChart(chartHost, series, {
      emptyText: "分层曲线不足",
      disableZoom: true,
    });
  }

  function renderCostAssumptions(ca) {
    const el = document.getElementById("quant-cost-assumptions");
    if (!el) return;
    if (!ca || !ca.ok) {
      el.innerHTML = ca && ca.reason
        ? `<p class="quant-attr-note">成本假设不可用：${escapeHtml(ca.reason)}</p>`
        : "";
      return;
    }
    const model =
      ca.model === "zero"
        ? "零成本（教学）"
        : ca.cost_mode === "turnover"
          ? "A股简化·按换手"
          : "A股简化";
    const slipLabel =
      ca.max_slippage_bps != null
        ? `${ca.base_slippage_bps ?? "—"}≤${ca.max_slippage_bps}`
        : String(ca.base_slippage_bps ?? "—");
    const turn =
      ca.turnover_cost_sum_pct != null && ca.cost_mode === "turnover"
        ? `${Number(ca.turnover_cost_sum_pct).toFixed(2)}%`
        : "—";
    el.innerHTML =
      researchGridHtml(
        [
          { id: "model", label: "成本模型", flex: true },
          { id: "commission", label: "佣金bps", widthPct: 12, num: true },
          { id: "stamp", label: "印花税bps(卖)", widthPct: 14, num: true },
          { id: "slip", label: "滑点bps", widthPct: 14, num: true },
          { id: "turn", label: "累计换手成本", widthPct: 14, num: true },
          { id: "rt", label: "示意往返%", widthPct: 12, num: true },
        ],
        [
          {
            model,
            commission: String(ca.commission_bps ?? "—"),
            stamp: String(ca.stamp_duty_bps_sell ?? "—"),
            slip: slipLabel,
            turn,
            rt: String(ca.round_trip_pct_on_100x100 ?? "—"),
          },
        ]
      ) + `<p class="quant-attr-note">${escapeHtml(ca.note || "")}</p>`;
  }

  function renderAttributionTables(attr) {
    const el = document.getElementById("quant-attr-tables");
    if (!el) return;
    if (!attr || !attr.ok) {
      el.innerHTML = "";
      return;
    }
    const byStock = (attr.by_stock || []).slice(0, 6);
    const bySector = (attr.by_sector || []).slice(0, 8);
    const br = attr.brinson || {};
    const fp = attr.factor_proxy || {};
    let html = `<p class="quant-trades-caption">选股超额 ${
      attr.selection_excess_pct != null
        ? `${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
        : "—"
    }</p>`;
    if (br.ok) {
      html +=
        `<p class="quant-trades-caption">Brinson lite · A ${fmtPct(br.allocation_pct)} · S ${fmtPct(
          br.selection_pct
        )} · I ${fmtPct(br.interaction_pct)} · Σ ${fmtPct(br.total_excess_pct)}</p>`;
      const brRows = (br.by_sector || []).slice(0, 8).map((r) => ({
        sector: r.sector || "—",
        weight: r.weight_pct != null ? `${r.weight_pct}%` : "—",
        allocation: fmtPct(r.allocation_pct),
        allocationCls: metricClass(r.allocation_pct),
        selection: fmtPct(r.selection_pct),
        selectionCls: metricClass(r.selection_pct),
        interaction: fmtPct(r.interaction_pct),
        interactionCls: metricClass(r.interaction_pct),
      }));
      if (brRows.length) {
        html += researchGridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "weight", label: "权重", widthPct: 14, num: true },
            { id: "allocation", label: "配置", widthPct: 14, num: true },
            { id: "selection", label: "选股", widthPct: 14, num: true },
            { id: "interaction", label: "交互", widthPct: 14, num: true },
          ],
          brRows,
          (col, d) => {
            if (col.id === "allocation") return metricCell(d.allocation, d.allocationCls);
            if (col.id === "selection") return metricCell(d.selection, d.selectionCls);
            if (col.id === "interaction") return metricCell(d.interaction, d.interactionCls);
            return escapeHtml(d[col.id] ?? "—");
          }
        );
      }
    }
    if (fp && fp.ok) {
      html += `<p class="quant-trades-caption">score 高低半组差 ${fmtPct(fp.score_spread_pct)} · n=${escapeHtml(
        String(fp.n ?? "—")
      )}</p>`;
    }
    if (byStock.length) {
      html += researchGridHtml(
        [
          { id: "name", label: "股票", flex: true },
          { id: "sector", label: "行业", widthPct: 18, center: true },
          { id: "ret", label: "均收益", widthPct: 18, num: true },
          { id: "n", label: "笔数", widthPct: 12, num: true },
        ],
        byStock.map((r) => {
          const ret = r.avg_return_pct ?? r.return_pct;
          const code = String(r.code || r.stock_code || "").trim();
          const fullName = watchingNameByCode[code] || code;
          return {
            code,
            name: fullName,
            sector: r.sector || "—",
            retText: fmtPct(ret),
            retCls: metricClass(ret),
            n: String(r.n ?? "—"),
          };
        }),
        (col, d) => {
          if (col.id === "name") {
            return (
              `<div class="watching-stock" title="${escapeHtml(d.name + " " + d.code)}">` +
              watchingNameSpanHtml(d.name) +
              `<span class="watching-code-sub">${escapeHtml(d.code)}</span></div>`
            );
          }
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          return escapeHtml(d[col.id] ?? "—");
        }
      );
    }
    if (bySector.length) {
      html +=
        `<div style="margin-top:8px">` +
        researchGridHtml(
          [
            { id: "sector", label: "行业", flex: true },
            { id: "count", label: "只数", widthPct: 16, num: true },
            { id: "ret", label: "均收益", widthPct: 20, num: true },
          ],
          bySector.map((r) => ({
            sector: r.sector || "—",
            count: String(r.count ?? r.n ?? "—"),
            retText: fmtPct(r.avg_return_pct),
            retCls: metricClass(r.avg_return_pct),
          })),
          (col, d) => {
            if (col.id === "ret") return metricCell(d.retText, d.retCls);
            return escapeHtml(d[col.id] ?? "—");
          }
        ) +
        `</div>`;
    }
    html += `<p class="quant-attr-note">${escapeHtml(
      attr.methodology || attr.note || "非完整因子暴露归因。"
    )}</p>`;
    el.innerHTML = html;
  }

  function renderRegimeBuckets(rb) {
    const el = document.getElementById("quant-regime-buckets");
    if (!el) return;
    if (!rb || !rb.ok || !(rb.buckets || []).length) {
      el.innerHTML = rb && rb.reason
        ? `<p class="quant-attr-note">Regime 分桶不可用：${escapeHtml(rb.reason)}</p>`
        : "";
      return;
    }
    el.innerHTML =
      `<p class="quant-trades-caption">Regime 分桶 · 已标注 ${escapeHtml(
        String(rb.tagged_trades ?? 0)
      )} 笔</p>` +
      researchGridHtml(
        [
          { id: "regime", label: "Regime", flex: true },
          { id: "n", label: "笔数", widthPct: 16, num: true },
          { id: "ret", label: "均收益", widthPct: 20, num: true },
          { id: "win", label: "胜率", widthPct: 18, num: true },
        ],
        (rb.buckets || []).map((b) => ({
          regime: b.regime || "—",
          n: String(b.trade_count ?? "—"),
          retText: fmtPct(b.avg_return_pct),
          retCls: metricClass(b.avg_return_pct),
          win: fmtPct(b.win_rate_pct),
        })),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          if (col.id === "win") return escapeHtml(d.win);
          return escapeHtml(d[col.id] ?? "—");
        }
      ) +
      `<p class="quant-attr-note">${escapeHtml(rb.note || "")}</p>`;
  }

  /** @deprecated 已并入模拟成交账；保留空实现以免旧调用报错 */
  function renderSignalFillTable(_rows) {
    const el = document.getElementById("quant-signal-fill");
    if (el) el.innerHTML = "";
  }

  function drawWatchingChart(pts) {
    const host = document.getElementById("watching-chart");
    if (!host) return;
    const series = (pts || [])
      .map((p) => ({
        time: String(p.x || p.date || "").slice(0, 10),
        value: Number(p.y ?? p.close),
      }))
      .filter((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.time) && Number.isFinite(p.value));
    renderLineChart(host, series, {
      emptyText: "暂无足够日线数据",
      ma: [5, 10, 20],
      disableZoom: true,
    }).catch(() => {});
  }

  async function showWatchingChart(code, name) {
    if (!code) return;
    watchingFocusCode = String(code).trim();
    watchingFocusName = String(name || watchingNameByCode[watchingFocusCode] || "").trim();
    const section = document.getElementById("watching-chart-section");
    const labelEl = document.getElementById("watching-chart-label");
    if (!section) return;
    section.hidden = false;
    if (typeof window.__investmentEnsureDockSide === "function") {
      try {
        window.__investmentEnsureDockSide();
      } catch (_) {
        /* ignore */
      }
    }
    if (labelEl) labelEl.textContent = `${name || code} · 收盘价`;
    drawWatchingChart([]);

    try {
      const res = await fetch(
        `/api/watching/daily-chart?code=${encodeURIComponent(code)}&lookback=60`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const pts = (data.points || [])
        .map((p) => ({ x: p.date, y: Number(p.close) }))
        .filter((p) => Number.isFinite(p.y));
      if (labelEl) {
        labelEl.textContent = `${data.stock_name || name || code} · 收盘价（${pts.length} 日）`;
      }
      if (data.stock_name) watchingFocusName = String(data.stock_name).trim();
      drawWatchingChart(pts);
      // 高亮当前行
      document.querySelectorAll("#watching-watchlist-table .is-chart-active").forEach((rowEl) => {
        rowEl.classList.remove("is-chart-active");
      });
      const activeRow = document.querySelector(
        `#watching-watchlist-table [data-code="${String(code).replace(/"/g, "")}"]`
      );
      if (activeRow) activeRow.classList.add("is-chart-active");
    } catch (err) {
      if (labelEl) labelEl.textContent = `${name || code} · 加载失败`;
      drawWatchingChart([]);
    }
  }

  function hideWatchingChart() {
    const section = document.getElementById("watching-chart-section");
    if (section) section.hidden = true;
    watchingFocusCode = null;
    watchingFocusName = "";
    document.querySelectorAll("#watching-watchlist-table .is-chart-active").forEach((rowEl) => {
      rowEl.classList.remove("is-chart-active");
    });
  }

  async function openWatchingNewsDetail(code) {
    const panel = document.getElementById("watching-news-detail");
    const titleEl = document.getElementById("watching-news-detail-title");
    const metaEl = document.getElementById("watching-news-detail-meta");
    const listEl = document.getElementById("watching-news-detail-list");
    if (!panel || !listEl) return;
    panel.hidden = false;
    if (titleEl) titleEl.textContent = `资讯 · ${code}`;
    if (metaEl) metaEl.textContent = "加载中…";
    listEl.innerHTML = "";
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(code)}?limit=8`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const name = data.stock_name || code;
      const sent = data.sentiment || {};
      if (titleEl) {
        titleEl.innerHTML =
          `资讯 · ${escapeHtml(name)}` +
          ` <span class="watching-code-sub">${escapeHtml(data.stock_code || code)}</span> ` +
          sentimentBadgeHtml(sent, data.stock_code || code);
      }
      const hitBits = []
        .concat(sent.hit_pos || [])
        .concat(sent.hit_neg || []);
      if (metaEl) {
        metaEl.textContent = [
          data.ok ? `${(data.items || []).length} 条` : data.error || "无资讯",
          sent.note || "规则关键词，非模型",
          hitBits.length ? `命中 ${hitBits.join("、")}` : "",
          data.updated_at ? `更新 ${data.updated_at}` : "",
        ]
          .filter(Boolean)
          .join(" · ");
      }
      const items = data.items || [];
      if (!items.length) {
        listEl.innerHTML = `<li class="watching-news-empty">${escapeHtml(
          data.error || "暂无标题"
        )}</li>`;
      } else {
        listEl.innerHTML = items
          .map((it) => {
            const t = escapeHtml(it.title || "");
            const meta = [it.time, it.source].filter(Boolean).join(" · ");
            const url = String(it.url || "").trim();
            const body = url
              ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${t}</a>`
              : t;
            return (
              `<li>` +
              `<div class="watching-news-title">${body}</div>` +
              (meta
                ? `<div class="watching-news-meta">${escapeHtml(meta)}</div>`
                : "") +
              `</li>`
            );
          })
          .join("");
      }
      watchingSentimentByCode[String(code)] = data;
      applySentimentToRow(code, data);
      
      // 调用 AI 舆情分析
      fetchWatchingNewsAI(code, data.stock_code || code);
    } catch (err) {
      if (metaEl) metaEl.textContent = String(err.message || err);
      listEl.innerHTML = `<li class="watching-news-empty">加载失败</li>`;
    }
  }

  async function fetchWatchingNewsAI(code, stock_code) {
    const aiSection = document.getElementById("watching-news-ai-section");
    const aiContent = document.getElementById("watching-news-ai-content");
    const aiStatus = document.getElementById("watching-news-ai-status");
    if (!aiSection || !aiContent) return;
    
    aiSection.hidden = false;
    aiContent.innerHTML = `
      <div class="watching-news-ai-loading">
        <div class="watching-news-ai-spinner"></div>
        <p>正在分析舆情数据…</p>
      </div>
    `;
    if (aiStatus) aiStatus.textContent = "分析中…";
    
    try {
      const res = await fetch(
        `/api/watching/sentiment/${encodeURIComponent(stock_code || code)}/analysis`
      );
      const data = await res.json().catch(() => ({}));
      
      if (data.ok && data.analysis) {
        let analysisHtml;
        if (typeof marked !== "undefined") {
          // 使用 marked 解析 Markdown
          analysisHtml = marked.parse(data.analysis, {
            gfm: true,
            breaks: true
          });
        } else {
          // 降级：简单替换换行
          analysisHtml = escapeHtml(data.analysis).replace(/\n/g, "<br/>");
        }
        aiContent.innerHTML = `<div class="watching-news-ai-text">${analysisHtml}</div>`;
        if (aiStatus) aiStatus.textContent = "分析完成";
      } else {
        aiContent.innerHTML = `<div class="watching-news-ai-error">${escapeHtml(data.analysis || "分析失败")}</div>`;
        if (aiStatus) aiStatus.textContent = "分析失败";
      }
    } catch (err) {
      aiContent.innerHTML = `<div class="watching-news-ai-error">AI 分析请求失败: ${escapeHtml(String(err.message || err))}</div>`;
      if (aiStatus) aiStatus.textContent = "分析失败";
    }
  }

  function applySentimentToRow(code, row) {
    if (!watchingGrid || !watchingGridReady) return;
    const key = String(code || "").trim();
    if (!key) return;
    const comp = watchingGrid.getRow(key);
    if (!comp) return;
    const alert = watchingAlertCodes.has(key);
    comp.update({
      sentHtml: sentimentBadgeHtml((row && row.sentiment) || {}, key),
      isSentimentAlert: alert,
    });
  }

  async function fillWatchingSentiment() {
    if (!watchingGrid || !watchingGridReady) return;
    const codes = (watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
    if (!codes.length) return;
    try {
      const res = await fetch(
        `/api/watching/sentiment?codes=${encodeURIComponent(codes.join(","))}&limit=3`
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const byCode = indexByWatchingCode(data.items || []);
      watchingSentimentByCode = byCode;
      codes.forEach((code) => {
        applySentimentToRow(code, byCode[watchingCodeKey(code)] || { ok: false, items: [] });
      });
    } catch (_) {
      // 填充失败时保持默认状态
    }
  }

  function applySentimentAlertRows(data) {
    const alerts = (data && data.alerts) || [];
    const isScan = !data || !data.kind || data.kind === "sentiment_scan";
    watchingAlertCodes = new Set(
      isScan
        ? alerts.map((a) => String(a.stock_code || "").trim()).filter(Boolean)
        : []
    );
    if (watchingGrid && watchingGridReady) {
      (watchingGrid.getData() || []).forEach((row) => {
        const code = String((row && row.code) || "");
        const comp = watchingGrid.getRow(code);
        if (comp) comp.update({ isSentimentAlert: watchingAlertCodes.has(code) });
      });
    }
    return alerts;
  }

  async function loadWatchingSentimentAlerts() {
    try {
      const res = await fetch("/api/watching/sentiment/alerts");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return;
      applySentimentAlertRows(data);
    } catch (_) {
      /* ignore */
    }
  }

  let watchingBuildCodes = [];
  let watchingBuildSharesByCode = {};
  let watchingBuildAmountByCode = {};
  let watchingBuildMode = "amount"; // amount | pct | shares
  let watchingBuildSeq = 0;
  let watchingBuildPreviewTimer = null;

  function setWatchingBuildStatus(text, { error = false } = {}) {
    const el = document.getElementById("watching-build-status");
    if (!el) return;
    const msg = String(text || "").trim();
    el.hidden = !msg;
    el.textContent = msg;
    el.classList.toggle("is-error", !!error);
  }

  function closeWatchingBuildLayer() {
    const layer = document.getElementById("watching-build-layer");
    if (layer) layer.hidden = true;
    watchingBuildCodes = [];
    watchingBuildSharesByCode = {};
    watchingBuildAmountByCode = {};
    watchingBuildSeq += 1;
    setWatchingBuildStatus("");
  }

  function syncWatchingBuildModeUI() {
    const input = document.getElementById("watching-build-shares-input");
    const label = document.getElementById("watching-build-default-label");
    const unit = document.getElementById("watching-build-default-unit");
    document.querySelectorAll(".watching-build-mode-btn").forEach((btn) => {
      const on = btn.dataset.mode === watchingBuildMode;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (!input || !label || !unit) return;
    if (watchingBuildMode === "amount") {
      label.textContent = "默认每只";
      unit.textContent = "元";
      input.min = "100";
      input.step = "100";
      if (!Number(input.value) || Number(input.value) < 100) input.value = "20000";
      input.title = "默认金额（元）；清单里可按只改";
    } else if (watchingBuildMode === "pct") {
      label.textContent = "每只占可用现金";
      unit.textContent = "%";
      input.min = "0.1";
      input.step = "0.1";
      const n = Number(input.value);
      if (!Number.isFinite(n) || n <= 0 || n > 100) input.value = "10";
      input.title = "相对可用现金的仓位比例";
    } else {
      label.textContent = "默认每只";
      unit.textContent = "股";
      input.min = "100";
      input.step = "100";
      if (!Number(input.value) || Number(input.value) < 100) input.value = "200";
      input.title = "默认股数；清单里可按只改（100 股为一手）";
    }
  }

  function watchingBuildDefaultValue() {
    const input = document.getElementById("watching-build-shares-input");
    const n = Number((input && input.value) || 0);
    return Number.isFinite(n) ? n : 0;
  }

  function watchingBuildPayload() {
    const raw = watchingBuildDefaultValue();
    if (watchingBuildMode === "amount") {
      if (!(raw > 0)) return null;
      const map = {};
      let anyCustom = false;
      for (const code of watchingBuildCodes) {
        const custom = watchingBuildAmountByCode[code];
        const amt = custom != null ? Number(custom) : raw;
        if (!(amt > 0)) return null;
        map[code] = Math.round(amt * 100) / 100;
        if (custom != null && Math.abs(amt - raw) > 0.01) anyCustom = true;
      }
      return {
        amount_per_code: Math.round(raw * 100) / 100,
        amount_by_code: anyCustom ? map : undefined,
      };
    }
    if (watchingBuildMode === "pct") {
      if (!(raw > 0) || raw > 100) return null;
      return { position_pct: Math.round((raw / 100) * 10000) / 10000 };
    }
    // shares
    const defaults = Math.floor(raw / 100) * 100;
    if (!(defaults >= 100)) return null;
    const map = {};
    let anyCustom = false;
    for (const code of watchingBuildCodes) {
      const custom = watchingBuildSharesByCode[code];
      const n =
        custom != null ? Math.floor(Number(custom) / 100) * 100 : defaults;
      if (!(n >= 100)) return null;
      map[code] = n;
      if (custom != null && n !== defaults) anyCustom = true;
    }
    return {
      shares: defaults,
      shares_by_code: anyCustom ? map : undefined,
    };
  }

  function renderWatchingBuildPlan(plan) {
    const body = document.getElementById("watching-build-body");
    const confirmBtn = document.getElementById("watching-build-confirm");
    if (!body) return;
    const items = (plan && plan.items) || [];
    const skipped = (plan && plan.skipped) || [];
    const defaultVal = watchingBuildDefaultValue();
    const fmtMoney = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "—";
      return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    };
    const editableCodes = watchingBuildCodes.slice();
    const byCode = {};
    items.forEach((it) => {
      byCode[it.stock_code] = it;
    });
    skipped.forEach((s) => {
      if (!byCode[s.stock_code]) {
        byCode[s.stock_code] = {
          stock_code: s.stock_code,
          stock_name: s.stock_name,
          reason: s.reason,
        };
      }
    });
    const headCols =
      watchingBuildMode === "amount"
        ? `<th>股票</th><th>现价</th><th>金额</th><th>股数</th><th>花费</th>`
        : `<th>股票</th><th>现价</th><th>股数</th><th>花费</th>`;
    const table = editableCodes.length
      ? `<table class="quant-weight-table watching-build-table"><thead><tr>` +
        headCols +
        `</tr></thead><tbody>` +
        editableCodes
          .map((code) => {
            const it = byCode[code] || { stock_code: code };
            let midCells;
            if (watchingBuildMode === "amount") {
              const amt =
                watchingBuildAmountByCode[code] != null
                  ? watchingBuildAmountByCode[code]
                  : defaultVal;
              midCells =
                `<td class="num"><input type="number" class="watching-build-row-amount" data-code="${escapeHtml(
                  code
                )}" min="100" step="100" value="${escapeHtml(String(amt))}" title="该只买入金额（元）" /></td>` +
                `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
            } else if (watchingBuildMode === "shares") {
              const shares =
                watchingBuildSharesByCode[code] != null
                  ? watchingBuildSharesByCode[code]
                  : it.shares != null
                    ? it.shares
                    : Math.floor(defaultVal / 100) * 100;
              midCells =
                `<td class="num"><input type="number" class="watching-build-row-shares" data-code="${escapeHtml(
                  code
                )}" min="100" step="100" value="${escapeHtml(String(shares))}" title="该只买入股数" /></td>`;
            } else {
              midCells =
                `<td class="num">${it.shares != null ? escapeHtml(String(it.shares)) : "—"}</td>`;
            }
            const amountCell =
              it.amount != null
                ? fmtMoney(it.amount)
                : it.reason
                  ? `<span class="watching-build-skip-reason">${escapeHtml(it.reason)}</span>`
                  : "—";
            return (
              `<tr data-code="${escapeHtml(code)}"><td class="watching-build-name">` +
              watchingNameSpanHtml(it.stock_name || code) +
              `<span class="watching-code-sub">${escapeHtml(code)}</span></td>` +
              `<td class="num">${escapeHtml(String(it.price ?? "—"))}</td>` +
              midCells +
              `<td class="num">${amountCell}</td></tr>`
            );
          })
          .join("") +
        `</tbody></table>`
      : `<p class="watching-table-empty">按当前定量没有可建仓的股票</p>`;
    const costNote =
      plan && plan.cost_model === "zero"
        ? `<div class="watching-build-cost-chip">零成本假设</div>`
        : "";
    const totals =
      costNote +
      `<dl class="watching-build-totals">` +
      `<div><dt>买入</dt><dd>${items.length} 只</dd></div>` +
      `<div><dt>合计花费</dt><dd>${fmtMoney(plan && plan.total_amount)} 元</dd></div>` +
      `<div><dt>可用现金</dt><dd>${fmtMoney(plan && plan.cash)} 元</dd></div>` +
      `<div><dt>建仓后现金</dt><dd>${fmtMoney(plan && plan.cash_after)} 元</dd></div>` +
      `</dl>`;
    body.innerHTML = table + totals;
    if (confirmBtn) confirmBtn.disabled = !items.length;
  }

  async function refreshWatchingBuildPreview() {
    const body = document.getElementById("watching-build-body");
    const confirmBtn = document.getElementById("watching-build-confirm");
    if (!watchingBuildCodes.length || !body) return;
    const payload = watchingBuildPayload();
    if (!payload) {
      body.innerHTML = "";
      const tip =
        watchingBuildMode === "pct"
          ? "仓位比例须在 0–100%"
          : watchingBuildMode === "amount"
            ? "金额须大于 0"
            : "股数须为 100 的整数倍";
      setWatchingBuildStatus(tip, { error: true });
      if (confirmBtn) confirmBtn.disabled = true;
      return;
    }
    const seq = ++watchingBuildSeq;
    setWatchingBuildStatus("按现价试算中…");
    if (confirmBtn) confirmBtn.disabled = true;
    try {
      const res = await fetch("/api/watching/sync-paper/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codes: watchingBuildCodes,
          ...payload,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== watchingBuildSeq) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderWatchingBuildPlan(data);
      setWatchingBuildStatus("");
    } catch (err) {
      if (seq !== watchingBuildSeq) return;
      body.innerHTML = "";
      setWatchingBuildStatus(String(err.message || err), { error: true });
    }
  }

  function scheduleWatchingBuildPreview() {
    if (watchingBuildPreviewTimer) clearTimeout(watchingBuildPreviewTimer);
    watchingBuildPreviewTimer = setTimeout(() => {
      refreshWatchingBuildPreview().catch(() => {});
    }, 300);
  }

  function openWatchingBuildLayer(codes, label) {
    const list = Array.from(
      new Set((codes || []).map((c) => String(c || "").trim()).filter(Boolean))
    );
    if (!list.length) {
      setWatchingRefreshStatus("请先勾选要建仓的股票", { error: true });
      return;
    }
    const layer = document.getElementById("watching-build-layer");
    if (!layer) return;
    watchingBuildCodes = list;
    watchingBuildSharesByCode = {};
    watchingBuildAmountByCode = {};
    watchingBuildMode = "amount";
    syncWatchingBuildModeUI();
    const titleEl = document.getElementById("watching-build-title");
    if (titleEl) {
      titleEl.textContent = label ? `建仓 · ${label}` : `加入纸面 · ${list.length} 只`;
    }
    const body = document.getElementById("watching-build-body");
    if (body) body.innerHTML = "";
    layer.hidden = false;
    const input = document.getElementById("watching-build-shares-input");
    if (input) input.focus();
    refreshWatchingBuildPreview().catch(() => {});
  }

  async function confirmWatchingBuild() {
    if (!watchingBuildCodes.length) return;
    const payload = watchingBuildPayload();
    if (!payload) {
      setWatchingBuildStatus("请检查定量参数", { error: true });
      return;
    }
    const codes = watchingBuildCodes.slice();
    const confirmBtn = document.getElementById("watching-build-confirm");
    if (confirmBtn) confirmBtn.disabled = true;
    setWatchingBuildStatus("建仓中…");
    try {
      const res = await fetch("/api/watching/sync-paper", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codes,
          ...payload,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const sync = data.paper_sync || {};
      const summary = sync.message || `已买入 ${sync.bought_count || 0} 只`;
      closeWatchingBuildLayer();
      await loadWatchingPanel();
      setPoolMeta(summary);
      setWatchingRefreshStatus(summary, { error: !sync.bought_count });
      await loadFollowCard();
      if (typeof ctx.reloadPaper === "function") {
        try {
          await ctx.reloadPaper();
        } catch (_) {}
      }
    } catch (err) {
      setWatchingBuildStatus(String(err.message || err), { error: true });
      if (confirmBtn) confirmBtn.disabled = false;
    }
  }

  function gotoFollowPage(code) {
    const c = String(code || "").trim();
    if (c && typeof ctx.focusPaperHolding === "function") {
      ctx.focusPaperHolding(c);
    }
    if (typeof ctx.showResultsTab === "function") {
      ctx.showResultsTab("follow");
      return;
    }
    const q = c ? `?code=${encodeURIComponent(c)}` : "";
    window.location.href = `/follow${q}`;
  }

  async function renderWatchingWatchTable(wl, names, paperCodes, scores) {
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    if (watchingGrid && typeof watchingGrid.destroy === "function") {
      try {
        watchingGrid.destroy();
      } catch (_) {
        /* ignore */
      }
    }
    watchingGrid = null;
    watchingGridReady = false;
    if (!wl.length) {
      watchTable.innerHTML = `<p class="watching-table-empty">暂无观察 · 上方搜索加入</p>`;
      hideWatchingNewsDetail();
      updateWatchingPickCount();
      return;
    }
    const inPaper =
      paperCodes instanceof Map
        ? paperCodes
        : new Map(Array.from(paperCodes || []).map((c) => [String(c), null]));
    const rowByCode = new Map();
    for (let i = 0; i < wl.length; i++) {
      const code = String(wl[i] || "").trim();
      if (!code) continue;
      const name = (names[i] && String(names[i]).trim()) || "—";
      const scoreRaw = scores && scores[code];
      const scoreNum = scoreRaw == null || Number.isNaN(Number(scoreRaw)) ? null : Number(scoreRaw);
      const onPaper = inPaper.has(code);
      const heldShares = onPaper ? inPaper.get(code) : null;
      rowByCode.set(code, {
        code,
        name,
        picked: false,
        market: "",
        paper: onPaper ? (heldShares != null ? `${heldShares} 股` : "已持") : "建仓",
        onPaper,
        sentHtml: `<span class="watching-sent-badge is-neutral" data-code="${escapeHtml(code)}" title="加载中">…</span>`,
        price: "—",
        chg: "—",
        chgCls: "",
        score: scoreNum == null ? "…" : `${Math.round(scoreNum)}`,
        scoreNum,
        stance: "…",
        ndBias: "—",
        ndBiasKey: "",
        ndBiasTitle: "加载中（收盘→次日方向）",
        excess: "…",
        excessNum: null,
        vol: "…",
        volNum: null,
        volr: "…",
        pe: "…",
        pb: "…",
        isHardReject: false,
        isSentimentAlert: watchingAlertCodes.has(code),
      });
    }
    const rows = Array.from(rowByCode.values());
    try {
      const V =
        (typeof window !== "undefined" && window.__ASSET_V__) || "p315";
      const mod = await import(`./watching_table_island.js?v=${V}`);
      watchTable.innerHTML =
        `<div id="watching-react-root" class="watching-react-grid-host"></div>`;
      const host = document.getElementById("watching-react-root");
      if (!host) throw new Error("watching host missing");
      const grid = await mod.mountWatchingTableIsland(host, {
        initialSort: watchingSortKey
          ? [{ column: watchingSortKey, dir: watchingSortDir === "asc" ? "asc" : "desc" }]
          : [],
      });
      grid.on("sortChanged", (sorters) => {
        const s = Array.isArray(sorters) && sorters.length ? sorters[0] : null;
        const key = s && s.field;
        if (key === "name" || key === "score" || key === "excess" || key === "vol" || key === "ndBias") {
          watchingSortKey = key;
          watchingSortDir = s.dir === "asc" ? "asc" : "desc";
          persistWatchingSort();
        }
      });
      grid.setRows(rows);
      watchingGrid = grid;
      watchingGridReady = true;
      updateWatchingPickCount();
      syncWatchingSelectAllState();
    } catch (err) {
      console.warn("[watching] 虚拟表挂载失败，回退原生表", err);
      watchingGrid = null;
      watchingGridReady = false;
      renderWatchingWatchTableFallback(rows);
    }
  }

  /** CDN/React 不可用时的原生表回退，避免整页空白 */
  function renderWatchingWatchTableFallback(rows) {
    const watchTable = document.getElementById("watching-watchlist-table");
    if (!watchTable) return;
    const body = (rows || [])
      .map((d) => {
        const code = escapeHtml(d.code || "");
        const name = d.name || d.code || "—";
        const alertCls = d.isSentimentAlert ? " is-sentiment-alert" : "";
        return (
          `<tr data-code="${code}" class="watching-watch-row${alertCls}">` +
          `<td class="watching-pick-cell"><input type="checkbox" class="watching-pick" value="${code}" data-code="${code}" /></td>` +
          `<td class="watching-stock" title="${escapeHtml(name)} ${code}">` +
          watchingNameSpanHtml(name) +
          `<span class="watching-code-sub">${code}<span class="watching-mkt"></span></span></td>` +
          `<td class="watching-paper-cell">` +
          (d.onPaper
            ? `<button type="button" class="watching-held-btn" data-code="${code}">${escapeHtml(d.paper || "已持")}</button>`
            : `<button type="button" class="watching-build-btn" data-code="${code}">建仓</button>`) +
          `</td>` +
          `<td class="watching-sent-cell">${d.sentHtml || "—"}</td>` +
          `<td class="num watching-col-num" data-q="price">${escapeHtml(String(d.price ?? "—"))}</td>` +
          `<td class="num watching-col-num watching-chg${d.chgCls ? " " + escapeHtml(d.chgCls) : ""}" data-q="chg">${escapeHtml(String(d.chg ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="score">${escapeHtml(String(d.score ?? "—"))}</td>` +
          `<td data-q="stance">${escapeHtml(String(d.stance ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="excess">${escapeHtml(String(d.excess ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="vol">${escapeHtml(String(d.vol ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="volr">${escapeHtml(String(d.volr ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="pe">${escapeHtml(String(d.pe ?? "—"))}</td>` +
          `<td class="num watching-col-num" data-q="pb">${escapeHtml(String(d.pb ?? "—"))}</td>` +
          `</tr>`
        );
      })
      .join("");
    watchTable.innerHTML =
      `<div class="watching-table-scroll"><table class="quant-weight-table watching-result-table"><thead><tr>` +
      `<th class="watching-pick-cell"><input type="checkbox" id="watching-select-all" /></th>` +
      `<th>股票</th><th>仓位</th><th>情绪</th><th class="watching-col-num">现价</th>` +
      `<th class="watching-col-num">涨跌</th><th class="watching-col-num">评分</th><th>倾向</th>` +
      `<th class="watching-col-num">超额</th><th class="watching-col-num">量</th>` +
      `<th class="watching-col-num">量比</th><th class="watching-col-num">PE</th><th class="watching-col-num">PB</th>` +
      `</tr></thead><tbody>${body}</tbody></table></div>`;
    updateWatchingPickCount();
  }

  function renderWatchingHoldings(holdingsMap, names, buildLogs) {
    const section = document.getElementById("watching-holdings-section");
    const tableEl = document.getElementById("watching-holdings-table");
    if (!section || !tableEl) return;

    const fmtMoney = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "";
      const abs = Math.abs(n);
      if (abs >= 10000) {
        const w = n / 10000;
        return `${w.toFixed(Math.abs(w) >= 100 || Number.isInteger(w * 10) ? 1 : 2)}万`;
      }
      return n.toLocaleString("zh-CN", {
        maximumFractionDigits: 2,
        minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
      });
    };
    const fmtPrice = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "";
      return n.toLocaleString("zh-CN", {
        maximumFractionDigits: n >= 100 ? 2 : 3,
      });
    };
    const fmtTs = (ts) => {
      if (!ts) return { date: "—", time: "", title: "" };
      try {
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) {
          const raw = String(ts);
          return {
            date: raw.slice(0, 10) || "—",
            time: raw.slice(11, 16) || "",
            title: raw,
          };
        }
        const pad = (n) => String(n).padStart(2, "0");
        const y = d.getFullYear();
        const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
        const today = new Date();
        return {
          date: y === today.getFullYear() ? md : `${y}-${md}`,
          time: hm,
          title: `${y}-${md} ${hm}`,
        };
      } catch (_) {
        const raw = String(ts);
        return { date: raw.slice(0, 10) || "—", time: raw.slice(11, 16) || "", title: raw };
      }
    };
    const dayKey = (ts) => {
      try {
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) return "未知日期";
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      } catch (_) {
        return "未知日期";
      }
    };
    const dayLabel = (key, n) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return key;
      const today = new Date();
      const pad = (x) => String(x).padStart(2, "0");
      const todayKey = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
      const y = new Date(today);
      y.setDate(y.getDate() - 1);
      const yKey = `${y.getFullYear()}-${pad(y.getMonth() + 1)}-${pad(y.getDate())}`;
      let base = key.slice(5);
      if (key === todayKey) base = `今天 ${base}`;
      else if (key === yKey) base = `昨天 ${base}`;
      return n ? `${base} · ${n} 笔` : base;
    };

    let records = Array.isArray(buildLogs) ? buildLogs.slice() : [];
    if (!records.length) {
      // 无 operation_log 时，用当前持仓合成「已持」记录
      const nameByCode = {};
      (names || []).forEach((n, i) => {
        /* names 与 code 不一定对齐；仅作兜底 */
        if (n && typeof n === "string") nameByCode[String(n).trim()] = String(n).trim();
      });
      Object.entries(holdingsMap || {}).forEach(([code, h]) => {
        if (!h) return;
        records.push({
          type: "sync_paper",
          type_label: "建仓",
          ts: h.bought_at || h.added_at || "",
          detail: "",
          meta: {
            stock_code: code,
            stock_name: h.stock_name || nameByCode[code] || "",
            shares: h.shares,
            price: h.cost != null ? h.cost : h.price,
            amount:
              h.actual_cost != null
                ? h.actual_cost
                : Number(h.shares) && Number(h.cost)
                  ? Number(h.shares) * Number(h.cost)
                  : h.market_value,
            origin: "manual",
          },
        });
      });
    }

    if (!records.length) {
      section.hidden = true;
      tableEl.innerHTML = "";
      return;
    }
    section.hidden = false;

    try {
      const sorted = records
        .slice()
        .sort((a, b) => {
          const ta = new Date(String(a.ts || 0)).getTime() || 0;
          const tb = new Date(String(b.ts || 0)).getTime() || 0;
          return tb - ta;
        })
        .slice(0, 40);

      const groups = [];
      let cur = null;
      sorted.forEach((l) => {
        const key = dayKey(l.ts);
        if (!cur || cur.key !== key) {
          cur = { key, items: [] };
          groups.push(cur);
        }
        cur.items.push(l);
      });

      tableEl.innerHTML = groups
        .map((g) => {
          const items = g.items
            .map((l) => {
              const meta = l.meta || {};
              const code = String(meta.stock_code || "").trim();
              const name = String(meta.stock_name || "").trim();
              const shares = Number(meta.shares);
              const price = Number(meta.price);
              const amount = Number(
                meta.amount != null ? meta.amount : meta.actual_cost
              );
              const bits = [];
              bits.push(name || code || "—");
              if (Number.isFinite(shares)) bits.push(`${shares}股`);
              if (Number.isFinite(price)) bits.push(`@${fmtPrice(price)}`);
              if (Number.isFinite(amount)) bits.push(`${fmtMoney(amount)}元`);
              const secondary = [code && name ? code : "", "观察建仓"]
                .filter(Boolean)
                .join(" · ");
              const when = fmtTs(l.ts);
              return (
                `<div class="paper-log-item is-sync" title="${escapeHtml(when.title || "")}">` +
                `<span class="paper-log-type">${escapeHtml(l.type_label || "建仓")}</span>` +
                `<div class="paper-log-body">` +
                `<div class="paper-log-primary">${escapeHtml(bits.join(" · "))}</div>` +
                (secondary
                  ? `<div class="paper-log-secondary">${escapeHtml(secondary)}</div>`
                  : "") +
                `</div>` +
                `<span class="paper-log-ts">` +
                `<span class="paper-log-date">${escapeHtml(when.date)}</span>` +
                `<span class="paper-log-time">${escapeHtml(when.time)}</span>` +
                `</span>` +
                `</div>`
              );
            })
            .join("");
          return (
            `<div class="paper-log-day">` +
            `<div class="paper-log-day-label">${escapeHtml(dayLabel(g.key, g.items.length))}</div>` +
            items +
            `</div>`
          );
        })
        .join("");
    } catch (err) {
      console.error("renderWatchingHoldings error:", err);
      tableEl.innerHTML = `<p class="watching-table-empty">记录加载失败</p>`;
    }
  }

  /** 观察页：已持仓映射 + 加入纸面流水（lite，不触发盯市/打分）。 */
  async function fetchPaperWatchContext() {
    const held = new Map();
    let buildLogs = [];
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8000);
      let res;
      try {
        res = await fetch("/api/paper?lite=1", { signal: ctrl.signal });
      } finally {
        clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.initialized) return { held, buildLogs };
      for (const h of (data.summary && data.summary.holdings) || data.holdings || []) {
        const code = String((h && h.stock_code) || "").trim();
        if (!code) continue;
        const shares = Number(h.shares);
        held.set(code, Number.isFinite(shares) ? shares : null);
      }
      const logs = Array.isArray(data.operation_log) ? data.operation_log : [];
      buildLogs = logs.filter((l) => l && l.type === "sync_paper");
      return { held, buildLogs };
    } catch (_) {
      return { held, buildLogs };
    }
  }

  /** 已持仓映射 code -> 股数，供观察页显示仓位并区分建仓/已持。 */
  async function fetchPaperHeldMap() {
    const ctx = await fetchPaperWatchContext();
    return ctx.held;
  }

  async function loadWatchingPanel() {
    const emptyEl = document.getElementById("watching-empty");
    const gridEl = document.getElementById("watching-align-grid");
    const mainActions = document.getElementById("watching-main-actions");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    let uniRes;
    let paperCtx = { held: new Map(), buildLogs: [] };
    try {
      // 名单与纸面上下文解耦：纸面失败/超时不挡主表
      const watchingP = fetch("/api/watching", { signal: ctrl.signal }).then((r) =>
        r.json()
      );
      const paperP = fetchPaperWatchContext().catch(() => ({
        held: new Map(),
        buildLogs: [],
      }));
      uniRes = await watchingP;
      paperCtx = await paperP;
    } catch (err) {
      const msg =
        err && err.name === "AbortError"
          ? "观察名单加载超时，请刷新"
          : String((err && err.message) || err);
      setPoolMeta(msg);
      throw err;
    } finally {
      clearTimeout(timer);
    }
    const paperCodes = paperCtx.held || new Map();
    const data = uniRes;
    if (!data.exists) {
      setPoolMeta("尚未创建");
      if (emptyEl) emptyEl.hidden = false;
      if (gridEl) gridEl.hidden = true;
      if (mainActions) mainActions.hidden = true;
      const searchWrap = document.getElementById("watching-search-wrap");
      if (searchWrap) searchWrap.hidden = true;
      const watchTable = document.getElementById("watching-watchlist-table");
      if (watchingGrid && typeof watchingGrid.destroy === "function") watchingGrid.destroy();
      watchingGrid = null;
      watchingGridReady = false;
      if (watchTable) watchTable.innerHTML = "";
      if (quantWatchingList) quantWatchingList.innerHTML = "";
      renderWatchingHoldings({}, [], []);
      return data;
    }
    if (emptyEl) emptyEl.hidden = true;
    if (gridEl) gridEl.hidden = false;
    if (mainActions) mainActions.hidden = false;
    const searchWrap = document.getElementById("watching-search-wrap");
    if (searchWrap) searchWrap.hidden = false;
    wireWatchingSearch();
    if (typeof window.__investmentInitResearchDock === "function") {
      try {
        window.__investmentInitResearchDock();
      } catch (_) {
        /* ignore */
      }
    } else {
      import(`./research_dock.js?v=${typeof window !== "undefined" && window.__ASSET_V__ ? window.__ASSET_V__ : "p283"}`)
        .then((m) => {
          window.__investmentInitResearchDock = m.initResearchDock;
          m.initResearchDock();
        })
        .catch(() => {});
    }
    const uni = data.watching || {};
    const wl = uni.watchlist || [];
    const names = Array.isArray(uni.watchlist_names) ? uni.watchlist_names : [];
    watchingNameByCode = {};
    for (let i = 0; i < wl.length; i++) {
      const c = String(wl[i] || "").trim();
      if (!c) continue;
      const nm = names[i] && String(names[i]).trim();
      if (nm) watchingNameByCode[c] = nm;
    }
    const paperN = wl.filter((c) => paperCodes.has(String(c))).length;
    setPoolMeta(
      `观察 ${wl.length} 只 · 已持 ${paperN}/${wl.length} · 上限 ${uni.max_size || "—"}`
    );
    await renderWatchingWatchTable(wl, names, paperCodes, uni.watchlist_scores || {});
    renderWatchingHoldings(
      uni.watchlist_holdings || {},
      names,
      paperCtx.buildLogs || []
    );
    if (quantWatchingList) {
      quantWatchingList.innerHTML = wl.length
        ? wl
            .map((c, i) => {
              const name = (names[i] && String(names[i]).trim()) || "";
              const label = name ? `${c} ${name}` : String(c);
              return `<li>${escapeHtml(label)}</li>`;
            })
            .join("")
        : '<li class="sub">watchlist 为空</li>';
    }
    if (wl.length) {
      // 行情与摘要并行；舆情/次日预判单独跑，避免挡住主表
      fillWatchingQuotes().catch(() => {});
      fillWatchingInsights().catch(() => {});
      fillWatchingSentiment().catch(() => {});
      fillWatchingNextDayTrend().catch(() => {});
    }
    loadWatchingSentimentAlerts().catch(() => {});
    return data;
  }

  function renderSignalConfig(data) {
    if (!data || !data.config) {
      if (quantSignalSummary) quantSignalSummary.textContent = "无法加载 signal_config";
      return;
    }
    const cfg = data.config;
    const weights = cfg.weights || {};
    const thresholds = cfg.stance_thresholds || {};
    const rank = cfg.rank || {};
    if (quantSignalSummary) {
      quantSignalSummary.textContent = [
        data.exists ? "已加载配置文件" : "使用内置默认配置",
        `min_score ${rank.min_score ?? "—"}`,
        `regime ${cfg.regime && cfg.regime.enabled ? "开" : "关"}`,
      ].join(" · ");
    }
    if (quantSignalTable) {
      const weightRows = Object.entries(weights)
        .map(
          ([k, v]) =>
            `<tr><td>${factorNameCellHtml(k)}</td><td class="num">${Number(v).toFixed(3)}</td><td>—</td></tr>`
        )
        .join("");
      const thresholdRows = Object.entries(thresholds)
        .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="num">${v}</td><td>stance</td></tr>`)
        .join("");
      quantSignalTable.innerHTML = `<table class="quant-weight-table">
        <thead><tr><th>项</th><th>值</th><th>类型</th></tr></thead>
        <tbody>${weightRows}${thresholdRows}</tbody>
      </table>`;
    }
    if (quantSignalConfig) {
      const json = JSON.stringify(cfg, null, 2);
      quantSignalConfig.value = json;
      const host = document.getElementById("strategy-monaco-host");
      if (host) {
        mountJsonEditor(host, json, quantSignalConfig, { readOnly: false }).catch(() => {
          setJsonEditorValue(json);
        });
      }
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

  async function loadSignalConfigPanel() {
    try {
      await ensureFactorMeta();
      renderFactorDict();
      const res = await fetch("/api/signal/config/file");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderSignalConfig(data);
      return data;
    } catch (err) {
      if (quantSignalSummary) quantSignalSummary.textContent = String(err.message || err);
      return null;
    }
  }

  async function loadStrategyList() {
    if (!strategyList || !strategyListLoading) return;
    try {
      const res = await fetch("/api/quant/strategies");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderStrategyList(data);
    } catch (err) {
      if (strategyListLoading) strategyListLoading.textContent = String(err.message || err);
      }
  }

  function renderStrategyList(data) {
    if (!data || !data.success || !data.strategies) {
      if (strategyListLoading) strategyListLoading.textContent = "加载失败";
      return;
    }
    if (strategyListLoading) strategyListLoading.hidden = true;
    const strategies = data.strategies;
    const costLabel = (m) =>
      m === "simple_cn" ? "A股简化成本" : m === "zero" ? "零成本" : m || "—";
    const cards = strategies
      .map((s) => {
        const params = s.params || {};
        const risk = s.risk || {};
        const t0 = (((s.execution || {}).overlays || {}).t0) || {};
        const chips = [
          params.horizon_days != null ? `持有 ${params.horizon_days} 日` : null,
          params.min_score != null ? `回测门槛 ${params.min_score}` : null,
          risk.max_positions != null ? `最多 ${risk.max_positions} 只` : null,
          risk.max_position_pct != null ? `单票 ≤${risk.max_position_pct}%` : null,
          risk.max_sector_pct != null ? `行业 ≤${risk.max_sector_pct}%` : null,
          risk.target_drawdown_pct != null || risk.max_drawdown_pct != null
            ? `回撤预警 ${risk.target_drawdown_pct ?? risk.max_drawdown_pct}%`
            : null,
          t0.enabled === false
            ? "做T 关"
            : t0.t0_ratio != null
              ? `做T ${Math.round(Number(t0.t0_ratio) * 100)}%`
              : null,
          s.cost_model ? costLabel(s.cost_model) : null,
        ].filter(Boolean);
        const title = s.label || s.strategy_id || s.name || "未命名策略";
        const sid = s.strategy_id || s.name || "";
        return (
          `<article class="strategy-card" data-strategy-id="${escapeHtml(sid)}">` +
          `<header class="strategy-card-head">` +
          `<div class="strategy-card-titles">` +
          `<h4 class="strategy-card-title">${escapeHtml(title)}</h4>` +
          `<p class="strategy-card-desc">${escapeHtml(s.description || "暂无描述")}</p>` +
          `</div>` +
          `<div class="strategy-card-meta">` +
          `<code class="strategy-card-id" title="策略 ID">${escapeHtml(sid)}</code>` +
          `<span class="strategy-card-ver">v${escapeHtml(String(s.version || "—"))}</span>` +
          `</div>` +
          `</header>` +
          (chips.length
            ? `<ul class="strategy-card-chips">${chips
                .map((c) => `<li>${escapeHtml(c)}</li>`)
                .join("")}</ul>`
            : "") +
          `<div class="strategy-card-actions quant-actions-inline">` +
          `<button type="button" class="dialog-btn secondary strategy-promote-btn" data-strategy="${escapeHtml(sid)}" data-apply="0">晋升快照</button>` +
          `<button type="button" class="dialog-btn strategy-promote-btn" data-strategy="${escapeHtml(sid)}" data-apply="1">晋升并应用到纸面</button>` +
          `</div>` +
          `</article>`
        );
      })
      .join("");
    strategyList.innerHTML = `<div class="strategy-card-list">${cards}</div>`;
    strategyList.querySelectorAll(".strategy-promote-btn").forEach((btn) => {
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
      riskBox.innerHTML =
        `<p class="quant-sub">限额 · 成本 · <strong>Execution（含做T）</strong>已写在上方卡片；` +
        `改参须人审 promote，不静默写盘。` +
        `历史验证 → <a href="/replay">历史回测</a>；纸面落地 → <a href="/follow">交易执行</a>；` +
        `拟合 → <a href="/platform">北极星</a>。</p>`;
    }
    renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
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
      if (quantDiffSummary) quantDiffSummary.textContent = (data && data.error) || "预览失败";
      if (quantDiffTable) quantDiffTable.innerHTML = "";
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
    if (quantDiffSummary) {
      quantDiffSummary.textContent = parts.length
        ? parts.join(" · ")
        : data.note || "无待合并 diff（可先跑「分析」/「阈值」或「每日量化」）";
    }
    if (quantDiffTable) {
      quantDiffTable.innerHTML = rows.length
        ? `<table class="quant-weight-table">
            <thead><tr><th>项</th><th>当前</th><th>建议</th><th>Δ</th></tr></thead>
            <tbody>${rows.join("")}</tbody>
          </table>`
        : "";
    }
  }

  async function loadConfigDiffPreview() {
    if (quantDiffSummary) quantDiffSummary.textContent = "加载 diff 预览…";
    try {
      const res = await fetch("/api/signal/config/diff-preview?use_saved=true");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderConfigDiffPreview(data);
      return data;
    } catch (err) {
      if (quantDiffSummary) quantDiffSummary.textContent = String(err.message || err);
      return null;
    }
  }


  function readPortfolioBtParams() {
    const lbEl = document.getElementById("quant-lookback");
    const tkEl = document.getElementById("quant-top-k");
    const wmEl = document.getElementById("quant-weight-mode");
    const dropEl = document.getElementById("quant-dropout-n");
    const stEl = document.getElementById("quant-exclude-st");
    const amtEl = document.getElementById("quant-min-amount-pctile");
    const benchEl = document.getElementById("quant-benchmark-code");
    let lookback = 120;
    let topK = 3;
    let weightMode = "equal";
    let dropoutN = 0;
    let excludeSt = false;
    let minAvgAmountPctile = null;
    let benchmarkCode = "000300";
    if (lbEl && lbEl.value !== "") {
      const n = Number(lbEl.value);
      if (Number.isFinite(n)) lookback = Math.max(40, Math.min(500, Math.round(n)));
    }
    if (tkEl && tkEl.value !== "") {
      const n = Number(tkEl.value);
      if (Number.isFinite(n)) topK = Math.max(1, Math.min(10, Math.round(n)));
    }
    if (wmEl && wmEl.value) {
      const allowed = new Set(["equal", "score_budget", "risk_parity_lite"]);
      if (allowed.has(String(wmEl.value))) weightMode = String(wmEl.value);
    }
    if (dropEl && dropEl.value !== "") {
      const n = Number(dropEl.value);
      if (Number.isFinite(n)) dropoutN = Math.max(0, Math.min(10, Math.round(n)));
    }
    if (stEl) excludeSt = !!stEl.checked;
    if (amtEl && amtEl.value !== "") {
      const n = Number(amtEl.value);
      if (Number.isFinite(n) && n > 0) {
        minAvgAmountPctile = Math.max(0, Math.min(90, n));
      }
    }
    if (benchEl && benchEl.value) {
      const allowedB = new Set(["000300", "000905", "399006", "pool"]);
      if (allowedB.has(String(benchEl.value))) benchmarkCode = String(benchEl.value);
    }
    return {
      lookback,
      top_k: topK,
      horizon_days: readHorizonDays(),
      weight_mode: weightMode,
      dropout_n: dropoutN,
      exclude_st: excludeSt,
      min_avg_amount_pctile: minAvgAmountPctile,
      benchmark_code: benchmarkCode,
    };
  }

  async function runPortfolioBacktest() {
    setQuantBtBusy(true, "Top-K 回测中（拉日线，可能需数秒）…");
    quantPortfolioSummary.textContent = "回测中…";
    try {
      const {
        lookback,
        top_k,
        horizon_days,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
      } = readPortfolioBtParams();
      const payload = {
        lookback,
        top_k,
        horizon_days,
        min_score: 55,
        apply_costs: true,
        fetch_fundamentals: false,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
        include_score_ic: true,
        include_quantile: true,
        include_benchmark: true,
      };
      const res = await fetch("/api/quant/portfolio-backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        const dropped = (data.dropped_stocks || [])
          .slice(0, 3)
          .map((d) => d.stock_code || d)
          .filter(Boolean);
        const dropNote = dropped.length ? ` · 已排除短序列 ${dropped.join("/")}` : "";
        quantPortfolioSummary.textContent =
          data.error || data.detail || `失败 HTTP ${res.status}` + dropNote;
        quantPortfolioSummary.classList.add("down");
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "回测失败");
        return data;
      }
      quantPortfolioSummary.classList.remove("down");
      const m = data.metrics || {};
      const costModel = data.cost_model || (data.params || {}).cost_model || "simple_cn";
      const oos = data.oos_summary || {};
      const regime = data.regime_summary || {};
      const dq = data.data_quality || {};
      const fundNote =
        data.params?.fundamentals_used && data.params?.fundamentals_count
          ? ` · 基本面 ${data.params.fundamentals_count} 只`
          : "";
      const oosFailed = oos.ok === false || oos.failed === true;
      const oosNote = oos.ok
        ? oosFailed
          ? ` · OOS失败 外${oos.oos_return_pct ?? "—"}%`
          : ` · OOS ${oos.oos_return_pct ?? "—"}%`
        : oos.reason
          ? ` · OOS失败 ${oos.reason}`
          : "";
      const regimeNote = regime.regime ? ` · ${regime.regime}` : "";
      const dqNote =
        Number(dq.fallback_count || 0) > 0 || Number(dq.gated_count || 0) > 0
          ? ` · 数据降级 ${dq.fallback_count || 0}${Number(dq.gated_count || 0) > 0 ? `/门禁${dq.gated_count}` : ""}`
          : "";
      const cc = data.cost_compare || {};
      const costCmpNote =
        cc.ok && cc.return_gap_pp != null
          ? ` · 成本Δ ${Number(cc.return_gap_pp) >= 0 ? "+" : ""}${cc.return_gap_pp}pp` +
            (cc.avg_impact_bps != null && Number(cc.avg_impact_bps) > 0
              ? ` · 冲击≈${Number(cc.avg_impact_bps).toFixed(1)}bps`
              : "")
          : "";
      const wf = data.wf_slices || {};
      const wfNote =
        wf.ok && wf.mean_test_return_pct != null
          ? ` · WF均收益 ${Number(wf.mean_test_return_pct).toFixed(2)}%`
          : wf.reason
            ? ` · WF ${wf.reason}`
            : "";
      const attr = data.attribution || {};
      const attrNote =
        attr.ok && attr.selection_excess_pct != null
          ? ` · 选股超额 ${Number(attr.selection_excess_pct) >= 0 ? "+" : ""}${attr.selection_excess_pct}%`
          : attr.by_sector && attr.by_sector.length
            ? ` · 归因行业 ${attr.by_sector.length}`
            : "";
      const pit = data.pit_report || {};
      const fundPit = pit.fundamentals || {};
      const pitNote = pit.bars_pit
        ? pit.fundamentals_pit
          ? " · PIT日线+财务"
          : fundPit.missing_as_of
            ? ` · PIT日线 · 财务缺${fundPit.missing_as_of}`
            : " · PIT日线"
        : "";
      const sa = data.source_audit || {};
      const auditNote =
        Number(sa.fallback_count || 0) > 0
          ? ` · 源不一致风险 ${sa.fallback_count}`
          : "";
      const matchNote =
        data.params?.execution_mode
          ? ` · 成交 ${data.params.execution_mode === "next_open" ? "次日开" : "收盘"}`
          : "";
      const dropN = Number(data.params?.dropped_thin_count || (data.dropped_stocks || []).length || 0);
      const dropNote = dropN > 0 ? ` · 排除短序列 ${dropN}` : "";
      const doN = Number(data.params?.dropout_n ?? data.request?.dropout_n ?? 0);
      const dropoutNote = doN > 0 ? ` · dropout ${doN}` : "";
      const sic = data.score_ic || {};
      const icNote = sic.ok
        ? ` · IC ${sic.ic_mean ?? "—"}/ICIR ${sic.icir ?? "—"}` +
          (sic.positive_ic_ratio != null
            ? ` · 正IC${(Number(sic.positive_ic_ratio) * 100).toFixed(0)}%`
            : "")
        : sic.reason
          ? ` · IC略 ${sic.reason}`
          : "";
      const qb = data.quantile_backtest || {};
      const qNote = qb.ok
        ? qb.monotonic_increasing
          ? ` · 分层单调↑ Q差${qb.q_high_minus_q_low_pct ?? "—"}%`
          : ` · 分层非单调 Q差${qb.q_high_minus_q_low_pct ?? "—"}%`
        : "";
      const bench = data.benchmark || {};
      const benchNote =
        bench.ok && bench.excess_pct != null
          ? ` · 超额${Number(bench.excess_pct) >= 0 ? "+" : ""}${bench.excess_pct}%(${
              bench.benchmark_label || "基准"
            })` +
            (bench.ann_ir != null ? ` · IR ${bench.ann_ir}` : "")
          : "";
      quantPortfolioSummary.textContent = `标的 ${(data.loaded_stocks || []).length} · 共同日 ${data.params?.common_dates} · 交易 ${m.trade_count} · 累计 ${m.total_return_pct}% · 胜率 ${m.win_rate_pct}% · 成本 ${costModel === "simple_cn" ? "A股简化" : costModel}${data.params?.neutralize ? ` · 中性化 ${data.params?.neutralized_rebalances || 0} 次` : ""}${fundNote}${oosNote}${regimeNote}${dqNote}${costCmpNote}${wfNote}${attrNote}${pitNote}${auditNote}${matchNote}${dropNote}${dropoutNote}${icNote}${qNote}${benchNote}`;
      const qBad = qb.ok && qb.monotonic_increasing === false;
      if (quantPortfolioSummary && (oosFailed || qBad)) {
        quantPortfolioSummary.classList.add("down");
      } else if (quantPortfolioSummary) {
        quantPortfolioSummary.classList.remove("down");
      }
      // Top-K 单曲线与中性化对照无关：清掉日报/上次对照块，避免曲线前残留「绝对分…百分点」
      neutralCompareSource = null;
      renderNeutralCompareTable(null);
      renderPortfolioBacktestResult(data);
      paintPortfolioChart(
        data.equity_curve,
        "回测无足够交易点",
        (data.benchmark && data.benchmark.ok && data.benchmark.equity_curve) || null,
        (data.benchmark && data.benchmark.benchmark_label) || null,
        data.ic_equity_align
      );
      loadFitGapForBacktest(data).catch(() => {});

      // 北极星仪表化（前端缓存）：滚动夏普 / 卡玛 / TTM 等
      try {
        const curve =
          data.equity_curve_tail ||
          (Array.isArray(data.equity_curve) ? data.equity_curve.slice(-120) : []);
        if (Array.isArray(curve) && curve.length) {
          localStorage.setItem(
            "investment_northstar_last_backtest",
            JSON.stringify({ at: Date.now(), curve })
          );
        }
      } catch (_) {
        /* ignore */
      }
      return data;
    } finally {
      setQuantBtBusy(false);
    }
  }

  async function runPortfolioNeutralCompare() {
    setQuantBtBusy(true, "中性化对照回测中（可能需数秒）…");
    quantPortfolioSummary.textContent = "中性化对照回测中…";
    try {
      const {
        lookback,
        top_k,
        horizon_days,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
      } = readPortfolioBtParams();
      const payload = {
        lookback,
        top_k,
        horizon_days,
        min_score: 55,
        apply_costs: true,
        fetch_fundamentals: false,
        weight_mode,
        dropout_n,
        exclude_st,
        min_avg_amount_pctile,
        benchmark_code,
      };
      const res = await fetch("/api/quant/portfolio-neutral-compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.success) {
        quantPortfolioSummary.textContent = data.error || "对照失败";
        renderNeutralCompareTable(null);
        renderPortfolioBacktestResult(null);
        paintPortfolioChart([], "对照失败");
        return data;
      }
      const nm = (data.neutralized && data.neutralized.metrics) || {};
      const am = (data.absolute && data.absolute.metrics) || {};
      const d = data.delta || {};
      const bc = data.benchmark_compare || {};
      const winner =
        data.winner === "neutralized"
          ? "中性化更优"
          : data.winner === "absolute"
            ? "绝对分更优"
            : "接近";
      const excessNote =
        bc.ok && bc.delta_excess_pct != null
          ? ` · Δ超额 ${Number(bc.delta_excess_pct) >= 0 ? "+" : ""}${bc.delta_excess_pct}%(${
              bc.benchmark_label || "基准"
            })`
          : "";
      quantPortfolioSummary.textContent =
        `${winner} · Δ累计 ${d.total_return_pct ?? "—"}% · 中性 ${nm.total_return_pct ?? "—"}% vs 绝对 ${am.total_return_pct ?? "—"}%` +
        excessNote +
        (data.fundamentals_count ? ` · 基本面 ${data.fundamentals_count} 只` : "");
      neutralCompareSource = "live";
      renderNeutralCompareTable(data, null, { frozen: false });
      neutralCompareSource = "live";
      renderBtScopeNote(BT_SCOPE_LIVE);
      if (data.neutralized && data.neutralized.success) {
        renderPortfolioBacktestResult({
          ...data.neutralized,
          loaded_stocks: data.loaded_stocks,
          params: { ...(data.neutralized.params || {}), stock_count: (data.loaded_stocks || []).length },
        });
      }
      await paintNeutralCompareChart(data);
      return data;
    } finally {
      setQuantBtBusy(false);
    }
  }

  async function paintNeutralCompareChart(data) {
    const nCurve = (data.neutralized && data.neutralized.equity_curve) || [];
    const aCurve = (data.absolute && data.absolute.equity_curve) || [];
    const toPts = (series) =>
      (series || []).map((p, i) => {
        const t = String(p.date || p.ts || p.time || "").slice(0, 10);
        return {
          time: /^\d{4}-\d{2}-\d{2}$/.test(t)
            ? t
            : new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10),
          value: Number(p.equity ?? p.equity_norm ?? p.value),
        };
      });
    const nPts = toPts(nCurve);
    const aPts = toPts(aCurve);
    const benchCurve =
      (data.neutralized && data.neutralized.benchmark && data.neutralized.benchmark.equity_curve) ||
      (data.absolute && data.absolute.benchmark && data.absolute.benchmark.equity_curve) ||
      [];
    const bPts = toPts(benchCurve);
    const host = quantPortfolioChart;
    const legendEl = document.getElementById("quant-portfolio-legend");
    if (!host) return;
    const series = [];
    if (nPts.length >= 2) {
      series.push({ label: "中性化", color: "#2563eb", lineWidth: 2, points: nPts });
    }
    if (aPts.length >= 2) {
      series.push({ label: "绝对分", color: "#059669", lineWidth: 2, points: aPts });
    }
    if (bPts.length >= 2) {
      series.push({ label: "基准", color: "#9ca3af", lineWidth: 1.5, points: bPts });
    }
    if (series.length >= 2) {
      if (legendEl) {
        legendEl.textContent =
          "中性化对照：蓝=截面中性化 · 绿=绝对分" +
          (bPts.length >= 2 ? " · 灰=同一基准买持" : "") +
          "。起点 100；超额见对照表。";
      }
      await renderMultiLineChart(host, series, {
        emptyText: "对照曲线不足",
        disableZoom: true,
      });
      return;
    }
    await paintDualPortfolioChart(nCurve, aCurve, "对照曲线不足");
  }

  async function openReadmeViewer(dir) {
    if (!readmeDialog) return;
    if (readmeTitle) readmeTitle.textContent = dir;
    if (readmeMeta) readmeMeta.textContent = "加载中…";
    if (readmeBody) readmeBody.textContent = "";
    if (readmeDocLinks) readmeDocLinks.innerHTML = "";
    readmeDialog.showModal();
    try {
      const res = await fetch(`/api/readme?dir=${encodeURIComponent(dir)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      if (readmeTitle) readmeTitle.textContent = data.dir;
      if (readmeMeta) readmeMeta.textContent = data.path;
      if (readmeDocLinks) {
        readmeDocLinks.innerHTML = (data.doc_links || [])
          .map(
            (link) =>
              `<a href="${link.href}" target="_blank" rel="noopener">${link.label}</a>`
          )
          .join("");
      }
      if (readmeBody) {
        if (window.marked) readmeBody.innerHTML = marked.parse(data.content || "");
        else readmeBody.textContent = data.content || "";
      }
    } catch (err) {
      if (readmeMeta) readmeMeta.textContent = String(err.message || err);
      if (readmeBody) readmeBody.textContent = "";
    }
  }

  function renderQuantPackageTree(pkg, readmeIndex) {
    const modules = pkg.modules || {};
    const moduleLines = (pkg.subpackages || []).map((sp) => {
      const names = modules[sp] || [];
      return `${sp}: ${names.length ? names.join(", ") : "—"}`;
    });
    const readmeCount = pkg.readme_index?.present_count ?? readmeIndex?.present_count ?? "—";
    const readmeTotal = pkg.readme_index?.total_dirs ?? readmeIndex?.total_dirs ?? "—";
    const entries = readmeIndex?.entries || [];
    const treeHtml = renderReadmeLinksHtml(
      { entries },
      { summary: `quant/${pkg.layout || "P28+"} · ${pkg.module_count || 0} 模块 · README ${readmeCount}/${readmeTotal} · ${moduleLines.join(" · ")}` }
    );
    return `<details class="readme-tree">
  <summary>浏览子目录 README（${entries.length || readmeTotal}）</summary>
  ${treeHtml}
  </details>`;
  }

  let quantOpsPackageLoaded = false;

  async function loadOpsPackageTree() {
    if (!quantOpsPackage || quantOpsPackageLoaded) return;
    quantOpsPackage.textContent = "加载包结构…";
    try {
      const [packageRes, readmeIndexRes] = await Promise.all([
        fetch("/api/quant/package"),
        fetch("/api/readme-index"),
      ]);
      if (!packageRes.ok) {
        quantOpsPackage.textContent = "包结构加载失败";
        return;
      }
      const pkg = await packageRes.json();
      const readmeIndex = readmeIndexRes.ok ? await readmeIndexRes.json() : null;
      quantOpsPackage.innerHTML = renderQuantPackageTree(pkg, readmeIndex);
      attachReadmeLinkHandler(quantOpsPackage, ctx);
      quantOpsPackageLoaded = true;
    } catch (err) {
      quantOpsPackage.textContent = String(err.message || err);
    }
  }

  function openDailyFold() {
    const fold = document.getElementById("quant-daily-fold");
    if (fold) fold.open = true;
  }

  function syncDailyFoldSummary(tail) {
    const foldSummary = document.getElementById("quant-daily-fold-summary");
    if (!foldSummary) return;
    const t = String(tail || "").trim();
    foldSummary.textContent = t || "暂无摘要";
  }

  let dailyPreviewRequested = false;

  async function ensureDailyPreview() {
    if (!quantExportPreviewBody || dailyPreviewRequested) return;
    dailyPreviewRequested = true;
    try {
      await previewQuantExport("markdown");
    } catch (err) {
      if (quantExportPreviewMeta) {
        quantExportPreviewMeta.textContent = String(err.message || err);
      }
    }
  }

  async function loadOpsPanel() {
    if (!quantOpsSummary) return;
    setBusyText(quantOpsSummary, "加载中…", { busy: true });
    try {
      const healthRes = await fetch("/api/daily/health");
      const data = await healthRes.json();
      if (!healthRes.ok) throw new Error(data.detail || healthRes.statusText);

      // 研究枢纽只暴露 quant 日报；改仓类 preset 不在此页提供
      if (quantOpsPreset) {
        quantOpsPreset.innerHTML = `<option value="quant" selected>量化研究</option>`;
        quantOpsPreset.value = "quant";
      }
      if (quantOpsPresetFlags) quantOpsPresetFlags.textContent = "";

      const daily = data.daily_last || {};
      const uni = data.watching || {};
      const parts = [];
      let foldTail = "";
      if (daily.empty) {
        parts.push("尚无日报 · 点「生成日报」汇总今日结论");
        foldTail = "尚无日报";
      } else {
        const when = String(daily.finished_at || "")
          .replace("T", " ")
          .slice(0, 16);
        parts.push(`最近日报 ${daily.ok ? "OK" : "FAIL"}${when ? ` · ${when}` : ""}`);
        foldTail = `${daily.ok ? "OK" : "FAIL"}${when ? ` · ${when}` : ""}`;
      }
      if (uni.exists === false) {
        parts.push("研究池未初始化");
      } else if (uni.watchlist_count != null) {
        parts.push(`研究池 ${uni.watchlist_count} 只`);
      }
      try {
        const clRes = await fetch("/api/quant/cluster-live/status");
        const cl = await clRes.json();
        if (clRes.ok && cl && cl.success) {
          const mode = (cl.cluster_scoring || {}).mode || "off";
          if (mode !== "off") {
            const v = (cl.active || {}).version;
            const cov = (cl.health || {}).coverage;
            parts.push(
              `分组 live ${mode}` +
                (v != null ? ` · v${v}` : "") +
                (cov != null ? ` · 覆盖 ${Math.round(cov * 100)}%` : "")
            );
          }
        }
      } catch (_) {
        /* ignore */
      }
      setBusyText(quantOpsSummary, parts.join(" · "), { busy: false });
      syncDailyFoldSummary(foldTail);
    } catch (err) {
      setBusyText(quantOpsSummary, String(err.message || err), { busy: false });
      syncDailyFoldSummary("加载失败");
    }
  }

  const QUANT_EXPORT_PRESETS = new Set(["quant", "quant_paper", "full"]);

  async function runDailyWithPreset(preset, runningLabel) {
    openDailyFold();
    setQuantMeta(runningLabel || "生成日报中…", { busy: true });
    const res = await fetch("/api/daily/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    setQuantMeta(
      data.ok
        ? `日报已生成`
        : `日报部分失败 · ${(data.failures || []).join("；")}`,
      { busy: false, error: !data.ok }
    );
    await loadWatchingPanel();
    await loadOpsPanel();
    await loadConfigDiffPreview();
    if (quantCrossList) {
      await refreshCrossSection().catch(() => {});
    }
    if (data.ok && QUANT_EXPORT_PRESETS.has(preset)) {
      dailyPreviewRequested = true;
      await previewQuantExport("markdown");
    }
    return data;
  }

  function renderWfSlices(wf) {
    const host = document.getElementById("quant-wf-slices");
    if (!host) return;
    if (!wf) {
      host.innerHTML = "";
      return;
    }
    if (!wf.ok && !(wf.folds || []).length) {
      host.innerHTML = `<p class="quant-trades-caption">Walk-forward：${escapeHtml(wf.reason || "不可用")}</p>`;
      return;
    }
    const folds = wf.folds || [];
    const mean = wf.mean_test_return_pct;
    const pos = wf.positive_test_folds;
    const meas = wf.measured_test_folds;
    const head =
      `<p class="quant-trades-caption">Walk-forward ${folds.length} 折` +
      (mean != null ? ` · 测试段均收益 ${Number(mean).toFixed(2)}%` : "") +
      (pos != null && meas != null ? ` · 正窗 ${pos}/${meas}` : "") +
      (wf.fail_folds ? ` · 失败 ${wf.fail_folds}` : "") +
      `</p>`;
    if (!folds.length) {
      host.innerHTML = head;
      return;
    }
    host.innerHTML =
      head +
      researchGridHtml(
        [
          { id: "fold", label: "折", widthPct: 8, num: true, center: true },
          { id: "range", label: "测试段", flex: true },
          { id: "ret", label: "测试收益", widthPct: 14, num: true },
          { id: "dd", label: "窗口回撤", widthPct: 14, num: true },
          { id: "trades", label: "交易", widthPct: 10, num: true },
          { id: "status", label: "状态", widthPct: 14, center: true },
        ],
        folds.map((f) => {
          const ret = f.test_return_pct;
          return {
            fold: String(f.fold ?? "—"),
            range: `${f.test_start_date || "—"} → ${f.test_end_date || "—"}`,
            retText: ret != null ? `${Number(ret).toFixed(2)}%` : "—",
            retCls: metricClass(ret),
            dd:
              f.max_drawdown_pct != null ? `${Number(f.max_drawdown_pct).toFixed(2)}%` : "—",
            trades: String(f.trade_count ?? "—"),
            status: f.ok ? "ok" : f.reason || "失败",
          };
        }),
        (col, d) => {
          if (col.id === "ret") return metricCell(d.retText, d.retCls);
          return escapeHtml(d[col.id] ?? "—");
        }
      );
  }

  function fmtWeightCell(v) {
    if (v == null || v === "") return `<span class="quant-cell-empty">—</span>`;
    const n = Number(v);
    if (!Number.isFinite(n)) return escapeHtml(String(v));
    /* API 权重多为 0～1；panel.weight_pct 已是百分数 */
    if (Math.abs(n) <= 1.5) return `${(n * 100).toFixed(1)}%`;
    return `${n.toFixed(1)}%`;
  }

  function fmtEmptyCell() {
    return `<span class="quant-cell-empty">—</span>`;
  }

  function fmtOlsCell(v) {
    if (v == null || v === "") return fmtEmptyCell();
    const n = Number(v);
    if (!Number.isFinite(n)) return escapeHtml(String(v));
    const text = n.toFixed(4);
    return metricCell(escapeHtml(text), metricClass(n));
  }

  /** 建议权计算逻辑（列头 / 单元格悬停） */
  function weightSuggestLogicTip(suggest, isGroupTsIc) {
    const p = (suggest && suggest.params) || {};
    const maxD = p.max_delta != null ? p.max_delta : 0.03;
    const olsD = p.ols_delta != null ? p.ols_delta : 0.02;
    const minIc = p.min_ic != null ? p.min_ic : 0.03;
    const minIcir = p.min_icir != null ? p.min_icir : 0.25;
    const minN = p.min_samples != null ? p.min_samples : 8;
    const minBeta = p.min_ols_beta != null ? p.min_ols_beta : 0.05;
    const decay = p.weak_ic_decay != null ? p.weak_ic_decay : 0.015;
    const scaleBeta = !!p.ols_scale_by_beta;
    const scaleCap = p.ols_scale_cap != null ? p.ols_scale_cap : 2;
    const preferOls = !!p.prefer_ols;
    // 单票组时序回退：不要求 ICIR；组内截面 / 参数显式要求时才门控
    const requireIcir = isGroupTsIc
      ? false
      : p.require_icir !== false;
    const olsLine =
      `|β|≥${minBeta} → ±${olsD}` +
      (scaleBeta ? `（按 |β| 放大，最多 ${scaleCap}×）` : "") +
      (preferOls ? "；同向强 IC 可再加确认步长" : "");
    const icLine =
      `n≥${minN} 且 |IC|≥${minIc}` +
      (requireIcir
        ? ` 且 |ICIR|≥${minIcir}`
        : isGroupTsIc
          ? "（单票组时序 IC 回退，不要求 ICIR）"
          : "") +
      ` → 符号(IC)×最多 ${maxD}` +
      (requireIcir ? "（可按 |ICIR|/0.5 在 0.5～1.5× 间缩放）" : "");
    const lines = preferOls
      ? [
          "建议权 = 当前权 + Δ，再组内上限压缩并归一化（研究只读，不写 config）",
          `① OLS β 优先：${olsLine}`,
          `② 无可用 β 时 IC 补位：${icLine}`,
          `③ 否则近零/未过门槛 IC → −${decay}`,
          "零权因子冻结；非 raw β→权重占比",
        ]
      : [
          "建议权 = 当前权 + Δ，再组内上限压缩并归一化（研究只读，不写 config）",
          `① 强 IC：${icLine}`,
          `② 否则 OLS：${olsLine}`,
          `③ 否则近零/未过门槛 IC → −${decay}`,
          "零权因子冻结；非 raw β→权重占比",
        ];
    return lines.join("\n");
  }

  function factorWeightSuggestCellTip(name, row, suggest, sourceTipMap) {
    const parts = [];
    const rationale = Array.isArray(suggest && suggest.rationale)
      ? suggest.rationale
      : [];
    const hit = rationale.find(
      (line) =>
        String(line).startsWith(`${name} `) || String(line).startsWith(`${name}\t`)
    );
    if (hit) parts.push(String(hit));
    else if (row && row.deltaSource) {
      parts.push(
        (sourceTipMap && sourceTipMap[row.deltaSource]) || row.deltaSource
      );
    } else {
      parts.push("本因子无 Δ（未过门槛、零权冻结，或建议与当前相同）");
    }
    if (row && row.cur != null && row.sug != null) {
      parts.push(
        `当前 ${Number(row.cur).toFixed(3)} → 建议 ${Number(row.sug).toFixed(3)}`
      );
    }
    if (row && row.delta != null && Number.isFinite(Number(row.delta))) {
      const d = Number(row.delta);
      parts.push(`Δ=${d > 0 ? "+" : ""}${d.toFixed(3)}`);
    }
    if (row && row.ols != null && Number.isFinite(Number(row.ols))) {
      parts.push(`OLS β=${Number(row.ols).toFixed(4)}`);
    }
    return parts.join("\n");
  }

  /** 因子 IC + 权重建议 + OLS 系数合并为一张表 */
  function factorIcWeightMergedHtml(panelOrExp, suggest, ols) {
    const panelRows =
      (panelOrExp && panelOrExp.rows) ||
      (panelOrExp && panelOrExp.panel && panelOrExp.panel.rows) ||
      (panelOrExp && panelOrExp.factors) ||
      [];
    const list = Array.isArray(panelRows) ? panelRows : [];
    const hasSuggest = !!(suggest && (suggest.success || suggest.suggested_weights));
    const currentWeights = (suggest && suggest.current_weights) || {};
    const suggestedWeights = (suggest && suggest.suggested_weights) || {};
    const deltaMap = (suggest && suggest.deltas) || {};
    const deltaSources =
      (suggest && suggest.delta_sources && typeof suggest.delta_sources === "object"
        ? suggest.delta_sources
        : {}) || {};
    const isGroupTsIc = !!(panelOrExp && panelOrExp.mode === "group_ts_ic");
    const isGroupCsIc = !!(panelOrExp && panelOrExp.mode === "group_cs_ic");
    const suggestLogicTip = weightSuggestLogicTip(suggest, isGroupTsIc);
    const SOURCE_TIP = {
      cs_ic: "来源：截面 IC/ICIR 强证据 → 调权",
      ic: "来源：时序/组内 IC 强证据 → 调权",
      ols: "来源：OLS β 回退 → 调权",
      weak_ic_decay: "来源：IC 证据不足 → 略降权",
    };
    const olsCoefs =
      ols && ols.success && ols.coefficients && typeof ols.coefficients === "object"
        ? ols.coefficients
        : {};
    const olsCur =
      ols && ols.success && ols.current_weights && typeof ols.current_weights === "object"
        ? ols.current_weights
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
      // 优先行内 label → 注册表中文 → 英文名
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
      let weight = null;
      if (r.weight != null) weight = Number(r.weight);
      else if (r.weight_pct != null) weight = Number(r.weight_pct) / 100;
      const icReason =
        r.exclusion_reason ||
        r.ic_reason ||
        (ic == null ? icReasonsTop[name] : null) ||
        null;
      byName[name] = { name, label, ic, n, icir, weight, icReason };
    }
    const nameSet = new Set([
      ...Object.keys(byName),
      ...Object.keys(currentWeights),
      ...Object.keys(suggestedWeights),
      ...Object.keys(olsCoefs),
      ...Object.keys(olsCur),
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
        weight: null,
        icReason: icReasonsTop[name] || null,
      };
      // 再次以注册表中文覆盖（避免截面 IC 等只带英文名的结果盖掉 label）
      if (meta.label && (!base.label || base.label === name)) {
        base.label = meta.label;
      }
      const curVal =
        currentWeights[name] != null
          ? currentWeights[name]
          : olsCur[name] != null
            ? olsCur[name]
            : base.weight != null
              ? base.weight
              : null;
      const sugVal = hasSuggest ? suggestedWeights[name] : null;
      const delta =
        !hasSuggest
          ? null
          : deltaMap[name] != null
            ? deltaMap[name]
            : sugVal != null && curVal != null
              ? Number(sugVal) - Number(curVal)
              : null;
      const hasOlsKey = Object.prototype.hasOwnProperty.call(olsCoefs, name);
      const olsVal = hasOlsKey ? olsCoefs[name] : null;
      const olsReason = olsReasons[name] || null;
      let icReason = base.icReason || (base.ic == null ? icReasonsTop[name] : null) || null;
      if (base.ic == null && !icReason && list.length) icReason = "other";
      const absDelta =
        delta != null && Number.isFinite(Number(delta))
          ? Math.abs(Number(delta))
          : 0;
      return {
        ...base,
        cur: curVal,
        sug: sugVal,
        delta,
        deltaSource: deltaSources[name] || null,
        ols: olsVal,
        olsReason,
        icReason,
        scanHot: absDelta >= 0.02,
      };
    });

    return researchGridHtml(
      [
        { id: "factor", label: "因子", flex: true },
        {
          id: "ic",
          label: "IC",
          widthPct: 10,
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
          widthPct: 9,
          num: true,
          title: isGroupCsIc
            ? "组内 IC̄/σ(IC)：日截面 IC 序列稳定性；|ICIR|≥0.25 为强证据门槛之一"
            : isGroupTsIc
              ? "单票组无日截面序列，ICIR 不适用"
              : "IC̄/σ(IC)：截面 IC 稳定性；|ICIR|≥0.25 为强证据门槛之一",
        },
        {
          id: "n",
          label: "n",
          widthPct: 7,
          num: true,
          title: isGroupCsIc
            ? "有效截面日数（每日至少 min_names 只组员）"
            : isGroupTsIc
              ? "单票组该因子有效配对样本数"
              : "有效样本数",
        },
        {
          id: "cur",
          label: "当前",
          widthPct: 10,
          num: true,
          title: "当前生效/对照权重（分组表为该组建模起点，通常来自全局 config）",
        },
        {
          id: "sug",
          label: "建议",
          widthPct: 10,
          num: true,
          title: suggestLogicTip,
        },
        {
          id: "delta",
          label: "Δ",
          widthPct: 10,
          num: true,
          title: "建议 − 当前。\n" + suggestLogicTip,
        },
        {
          id: "ols",
          label: "OLS",
          widthPct: 11,
          num: true,
          title:
            "组内/单票 OLS 系数 β（对前瞻收益）。|β| 过门槛时可驱动建议 Δ；不是权重本身",
        },
      ],
      rows,
      (col, r) => {
        if (col.id === "factor") return factorNameCellHtml(r.name, r.label);
        if (col.id === "ic") {
          if (r.ic != null) {
            const cls = r.ic >= 0.03 ? "up" : r.ic <= -0.03 ? "down" : "";
            const tip = isGroupCsIc
              ? `组内日均截面 IC=${Number(r.ic).toFixed(4)}；强证据常用 |IC|≥0.03`
              : isGroupTsIc
                ? `单票组时序 IC=${Number(r.ic).toFixed(4)}：因子值与前瞻收益 Pearson`
                : `IC=${Number(r.ic).toFixed(4)}：与前瞻收益相关；强证据常用 |IC|≥0.03`;
            return `<span title="${escapeHtml(tip)}">${metricCell(
              escapeHtml(Number(r.ic).toFixed(4)),
              cls
            )}</span>`;
          }
          if (r.icReason) {
            const info = IC_REASON[r.icReason] || IC_REASON.other;
            return `<span class="quant-factor-reason" title="${escapeHtml(info.tip)}">${escapeHtml(
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
          const tip = "ICIR = 日截面 IC 均值 / 标准差；越大越稳；强证据常用 |ICIR|≥0.25";
          return `<span title="${escapeHtml(tip)}">${metricCell(
            escapeHtml(v.toFixed(2)),
            cls
          )}</span>`;
        }
        if (col.id === "n")
          return r.n != null ? escapeHtml(String(r.n)) : fmtEmptyCell();
        if (col.id === "cur") {
          const cell = fmtWeightCell(r.cur);
          return r.cur != null
            ? `<span title="当前权重 ${Number(r.cur).toFixed(3)}">${cell}</span>`
            : cell;
        }
        if (col.id === "sug") {
          if (r.sug == null) return fmtEmptyCell();
          const tip = factorWeightSuggestCellTip(r.name, r, suggest, SOURCE_TIP);
          return `<span title="${escapeHtml(tip)}">${fmtWeightCell(r.sug)}</span>`;
        }
        if (col.id === "delta") {
          if (r.delta == null) return fmtEmptyCell();
          const text = `${r.delta > 0 ? "+" : ""}${Number(r.delta).toFixed(3)}`;
          const tip = factorWeightSuggestCellTip(r.name, r, suggest, SOURCE_TIP);
          const cell = metricCell(escapeHtml(text), metricClass(r.delta));
          return `<span title="${escapeHtml(tip)}">${cell}</span>`;
        }
        if (col.id === "ols") {
          if (r.ols != null) {
            const tip = `OLS β=${Number(r.ols).toFixed(4)}：组内池/单票回归系数；过门槛时可作建议 Δ 的回退依据`;
            return `<span title="${escapeHtml(tip)}">${fmtOlsCell(r.ols)}</span>`;
          }
          if (r.olsReason) {
            const info = OLS_REASON[r.olsReason] || OLS_REASON.other;
            return `<span class="quant-factor-reason" title="${escapeHtml(info.tip)}">${escapeHtml(
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

  function weightDiffTableHtml(suggest) {
    return factorIcWeightMergedHtml(null, suggest, lastOlsForMerge);
  }

  function renderWeightDiffTable(suggest) {
    if (!quantFactorList) return;
    lastWeightSuggestForMerge = suggest && suggest.success ? suggest : lastWeightSuggestForMerge;
    quantFactorList.innerHTML =
      factorIcWeightMergedHtml(
        lastFactorPanelForMerge,
        lastWeightSuggestForMerge,
        lastOlsForMerge
      ) || "";
  }

  function icTableHtml(exp) {
    return factorIcWeightMergedHtml(exp, null, lastOlsForMerge);
  }

  let lastFactorPanelForMerge = null;
  let lastWeightSuggestForMerge = null;
  let lastOlsForMerge = null;

  function clusterGroupSuggestShim(cl) {
    const sug = (cl && cl.weight_suggest) || {};
    const diff = (cl && cl.config_diff) || {};
    const suggested =
      sug.suggested_weights || diff.suggested_weights || null;
    if (!suggested || typeof suggested !== "object") {
      return sug.success
        ? sug
        : { success: false, error: sug.error || "无组权建议" };
    }
    const current = sug.current_weights || diff.current_weights || {};
    let deltas = sug.deltas || diff.deltas || null;
    if (!deltas || typeof deltas !== "object") {
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
      success: sug.success !== false,
      suggested_weights: suggested,
      current_weights: current,
      deltas,
      delta_sources: sug.delta_sources || diff.delta_sources || {},
      rationale: sug.rationale || diff.rationale || [],
      params: sug.params || diff.params || {},
      ic_mode: sug.ic_mode || diff.ic_mode || "ols_cluster",
      note: sug.note || diff.note || "",
    };
  }

  function clusterGroupOlsShim(cl) {
    const ols = (cl && cl.ols) || null;
    if (!ols || typeof ols !== "object") return null;
    if (ols.success === false && !ols.coefficients) return null;
    return ols;
  }

  /** 组标题副信息：直径 / Δ组β / |β| top（原花名册字段） */
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

  /** β 分组后：因子区按「一组一表」；花名册并入 G 标题 */
  function renderClusterFactorTables() {
    if (!quantFactorList) return false;
    const data = quantLastOlsClusters;
    if (!data || !data.success) return false;
    const clusters = Array.isArray(data.clusters) ? data.clusters : [];
    if (!clusters.length) return false;
    const pref = data.preferred_cluster || null;
    const nameByCode = clusterNameByCodeFromData(data);
    const parts = clusters.map((cl, idx) => {
      const label = cl.label || `G${(cl.cluster_id ?? idx) + 1}`;
      const isPref =
        pref &&
        (pref.cluster_id === cl.cluster_id ||
          (pref.label && pref.label === cl.label));
      const sug = clusterGroupSuggestShim(cl);
      const ols = clusterGroupOlsShim(cl);
      // 优先组内截面 IC 面板（跑分组时随结果下发）；勿用全局 IC（进页已不预跑）
      const panel =
        (cl.factor_ic_panel && cl.factor_ic_panel.success && cl.factor_ic_panel) ||
        lastFactorPanelForMerge;
      const table =
        factorIcWeightMergedHtml(
          panel,
          sug && sug.success ? sug : null,
          ols
        ) ||
        `<p class="watching-table-empty">${escapeHtml(
          (sug && sug.error) || "本组暂无因子建议"
        )}</p>`;
      const canExport = cl.config_diff && cl.config_diff.success;
      const exportBtn = canExport
        ? `<button type="button" class="dialog-btn secondary dialog-btn-keep-case quant-cluster-export" ` +
          `data-cluster-export="${escapeHtml(
            String(idx)
          )}" title="导出本组因子权重 diff">导出本组</button>`
        : "";
      const gate = cl.oos_gate || {};
      const tags = [];
      tags.push(clusterTagHtml(`${cl.member_count ?? 0} 只`));
      if (cl.outlier_singleton) tags.push(clusterTagHtml("离群单票", "warn"));
      else if (cl.singleton) tags.push(clusterTagHtml("单票", "muted"));
      if (isPref) tags.push(clusterTagHtml("导出优先", "accent"));
      if (gate.ok && gate.passed) tags.push(clusterTagHtml("OOS✓", "ok"));
      else if (gate.ok) tags.push(clusterTagHtml("OOS未过", "warn"));
      const metrics =
        clusterMetricHtml("R²", ols && ols.r_squared != null ? ols.r_squared : "—") +
        clusterMetricHtml(
          "n",
          ols && ols.sample_count != null ? ols.sample_count : "—"
        );
      const membersHtml = formatMemberChipsHtml(cl.members, nameByCode);
      const { tight, top } = clusterTightTopLines(cl);
      const diag = [tight, top].filter(Boolean).join(" · ");
      const diagHtml = diag
        ? `<details class="quant-cluster-diag-fold">` +
          `<summary>紧度 / |β|</summary>` +
          `<div class="quant-cluster-diag">${escapeHtml(diag)}</div>` +
          `</details>`
        : "";
      return (
        `<article class="quant-cluster-group-table">` +
        `<details class="quant-fold quant-cluster-group-fold">` +
        `<summary class="quant-cluster-group-head">` +
        `<div class="quant-cluster-group-title-row">` +
        `<div class="quant-cluster-group-title">` +
        `<span class="quant-cluster-gid">${escapeHtml(String(label))}</span>` +
        `<span class="quant-cluster-tags">${tags.join("")}</span>` +
        `<span class="quant-cluster-metrics">${metrics}</span>` +
        `</div>` +
        (exportBtn
          ? `<div class="quant-cluster-group-actions">${exportBtn}</div>`
          : "") +
        `</div>` +
        `<div class="quant-cluster-members" aria-label="分组成员">${membersHtml}</div>` +
        `</summary>` +
        `<div class="quant-cluster-group-body">` +
        diagHtml +
        table +
        `</div>` +
        `</details>` +
        `</article>`
      );
    });
    quantFactorList.innerHTML =
      `<div class="quant-cluster-tables-head">` +
      `<span class="quant-cluster-tables-label">因子建议</span>` +
      `<span class="sub">组内 IC · 当前 / 建议 / Δ · OLS β · |Δ|≥0.02 高亮</span>` +
      `</div>` +
      parts.join("");
    return true;
  }

  function renderProbeFactorTable() {
    const host = quantProbeResult;
    if (!host) return;
    const html = factorIcWeightMergedHtml(
      lastFactorPanelForMerge,
      lastWeightSuggestForMerge,
      lastOlsForMerge
    );
    host.innerHTML =
      html || `<p class="watching-table-empty">暂无探针对照</p>`;
  }

  function normalizeProbeCode(raw) {
    return String(raw || "")
      .trim()
      .replace(/\.(SH|SZ|BJ)$/i, "");
  }

  /** 名称+代码合并展示：中国石化 600028 */
  function formatStockCodeName(code, name) {
    const c = normalizeProbeCode(code);
    const n = String(name || "").trim().replace(/\s+/g, "");
    if (n && c) return `${n} ${c}`;
    return n || c || "—";
  }

  function clusterNameByCodeFromData(data) {
    const nameByCode = { ...(watchingNameByCode || {}) };
    // 分组接口自带报价名（观察池宇宙优先）
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
    // 分组 score / 分池簿里也可能带名
    for (const cl of (data && data.clusters) || []) {
      for (const r of (cl.group_ranking || cl.ranking || []) || []) {
        const c = normalizeProbeCode(r.stock_code || r.code);
        const nm = String(r.stock_name || "").trim();
        if (c && nm) nameByCode[c] = nm;
      }
    }
    // 写回全局缓存，探针下拉等也能用
    for (const [c, nm] of Object.entries(nameByCode)) {
      if (c && nm && !watchingNameByCode[c]) watchingNameByCode[c] = nm;
    }
    return nameByCode;
  }

  function formatMemberList(codes, nameByCode) {
    const map = nameByCode || {};
    const list = (codes || []).map((m) => {
      const c = normalizeProbeCode(m);
      return formatStockCodeName(c, map[c] || watchingNameByCode[c] || "");
    });
    return list.length ? list.join("、") : "—";
  }

  function formatMemberChipsHtml(codes, nameByCode) {
    const map = nameByCode || {};
    const chips = (codes || []).map((m) => {
      const c = normalizeProbeCode(m);
      const n = String(map[c] || watchingNameByCode[c] || "")
        .trim()
        .replace(/\s+/g, "");
      // 有中文名：名称 + 代码；仅代码时不重复刷两遍
      if (n && c && n !== c) {
        return (
          `<span class="quant-cluster-member">` +
          `<span class="quant-cluster-member-name">${escapeHtml(n)}</span>` +
          `<span class="quant-cluster-member-code">${escapeHtml(c)}</span>` +
          `</span>`
        );
      }
      return (
        `<span class="quant-cluster-member">` +
        `<span class="quant-cluster-member-name">${escapeHtml(n || c || "—")}</span>` +
        `</span>`
      );
    });
    return chips.length
      ? chips.join("")
      : `<span class="sub">—</span>`;
  }

  function clusterTagHtml(text, kind) {
    const k = kind ? ` is-${kind}` : "";
    return `<span class="quant-cluster-tag${k}">${escapeHtml(String(text))}</span>`;
  }

  function clusterMetricHtml(key, value) {
    if (value == null || value === "" || value === "—") return "";
    return (
      `<span class="quant-cluster-metric">` +
      `<span class="quant-cluster-metric-k">${escapeHtml(String(key))}</span>` +
      `<span class="quant-cluster-metric-v">${escapeHtml(String(value))}</span>` +
      `</span>`
    );
  }

  function findClusterForProbeCode(codeOrName) {
    const data = quantLastOlsClusters;
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

  function syncProbeCodeOptionsFromClusters() {
    const hidden = document.getElementById("quant-ols-code");
    if (!hidden) return;
    const data = quantLastOlsClusters;
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
        const nm = String(nameByCode[c] || watchingNameByCode[c] || "")
          .trim()
          .replace(/\s+/g, "");
        rows.push({ code: c, name: nm, group: gLabel });
      }
    }
    if (rows.length) fillProbeCodeSelect(rows, hidden.value);
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
    const host = quantProbeResult;
    if (!host) return;
    const singleton = isProbeSingletonCluster(cluster);
    const groupIc = probeIcMapFromGroupPanel(
      cluster && cluster.factor_ic_panel
    );
    // 单票组：IC/β 一律用分组落盘（组模型=该票），禁止二次 /factor-ols 假差异
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
        // 单票实验为时序 IC，无日截面 ICIR
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
    host.innerHTML = researchGridHtml(
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
          return metricCell(escapeHtml(t), metricClass(r.dIc));
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
          return metricCell(escapeHtml(t), metricClass(r.dB));
        }
        if (col.id === "n") {
          const a = r.stockN != null ? String(r.stockN) : "—";
          const b = r.groupN != null ? String(r.groupN) : "—";
          return escapeHtml(`${a}/${b}`);
        }
        return "—";
      },
      {
        emptyText: "无对照因子",
        rowClass: (r) => (r && r.flag ? "is-scan-hot is-hetero" : ""),
      }
    );
    return rows;
  }

  function probeStatusBadge(kind, text) {
    const k = kind ? ` is-${kind}` : "";
    return (
      `<span class="quant-probe-badge${k}">${escapeHtml(String(text))}</span>`
    );
  }

  function markProbeReadyFromClusters(data) {
    if (!quantProbeSummary) return;
    if (data && data.success) {
      const nCl = data.n_clusters != null ? data.n_clusters : "—";
      const nIn = data.stock_count != null ? data.stock_count : "—";
      setBusyText(
        quantProbeSummary,
        `${probeStatusBadge("ok", "就绪")} ${escapeHtml(String(nCl))} 组 · 入组 ${escapeHtml(
          String(nIn)
        )} · 选票后对照`,
        { busy: false, html: true }
      );
      return;
    }
    const err = (data && data.error) || "上方分组未成功";
    setBusyText(
      quantProbeSummary,
      `${probeStatusBadge("warn", "未就绪")} ${escapeHtml(err)} · 请先跑分组`,
      { busy: false, html: true }
    );
  }

  async function waitForClusterHubReady(maxMs) {
    const limit = Math.max(1000, Number(maxMs) || 120000);
    const t0 = Date.now();
    while (Date.now() - t0 < limit) {
      if (quantLastOlsClusters && quantLastOlsClusters.success) return true;
      if (!bootstrapClusterHub._running) {
        // 未在跑且仍无成功结果 → 失败或未触发
        return !!(quantLastOlsClusters && quantLastOlsClusters.success);
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    return !!(quantLastOlsClusters && quantLastOlsClusters.success);
  }

  async function runProbeStockVsGroup() {
    const code = readOlsCode();
    const fold = document.getElementById("quant-probe-fold");
    if (fold) fold.open = true;
    if (!quantLastOlsClusters || !quantLastOlsClusters.success) {
      if (bootstrapClusterHub._running) {
        setBusyText(quantProbeSummary, "上方分组进行中，请稍候…", {
          busy: true,
        });
        const ok = await waitForClusterHubReady(180000);
        if (!ok) {
          markProbeReadyFromClusters(quantLastOlsClusters);
          if (quantProbeResult) {
            quantProbeResult.innerHTML =
              `<p class="watching-table-empty">分组未完成或失败，无法对照</p>`;
          }
          return;
        }
      } else {
        markProbeReadyFromClusters(quantLastOlsClusters);
        if (quantProbeResult) {
          quantProbeResult.innerHTML =
            `<p class="watching-table-empty">尚无分组结果 · 请先点「跑分组」</p>`;
        }
        return;
      }
    }
    setBusyText(
      quantProbeSummary,
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
        quantProbeSummary,
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
        (quantLastOlsClusters && quantLastOlsClusters.horizon_days) ||
          readHorizonDays()
      ) || readHorizonDays();
    const ridgeRaw = Number(
      quantLastOlsClusters && quantLastOlsClusters.ridge_lambda
    );
    const ridge = Number.isFinite(ridgeRaw)
      ? ridgeRaw
      : readRidgeLambda();
    // 与「跑分组」同窗 / 同 ridge，避免假异质
    const clusterLb = Number(
      (quantLastOlsClusters && quantLastOlsClusters.lookback) || 80
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
        quantProbeSummary,
        (exp && exp.error) || (ols && ols.error) || "单票 IC/OLS 失败",
        { busy: false }
      );
      if (quantProbeResult) {
        quantProbeResult.innerHTML = `<p class="watching-table-empty">对照失败</p>`;
      }
      return;
    }
    const probeRows = renderProbeStockVsGroupTable(exp, ols, cl);
    if (!cl) {
      setBusyText(
        quantProbeSummary,
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
        quantProbeSummary,
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
      quantProbeSummary,
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

  function renderMergedFactorTable() {
    // 已有分组：主因子区保持一组一表；探针结果写到探针区
    if (quantLastOlsClusters && quantLastOlsClusters.success) {
      renderClusterFactorTables();
      if (quantProbeResult) renderProbeFactorTable();
      return;
    }
    if (!quantFactorList) return;
    if (renderClusterFactorTables()) return;
    const html = factorIcWeightMergedHtml(
      lastFactorPanelForMerge,
      lastWeightSuggestForMerge,
      lastOlsForMerge
    );
    quantFactorList.innerHTML =
      html ||
      `<p class="watching-table-empty">点「跑分组」生成一组一表</p>`;
  }

  async function bootstrapClusterHub() {
    if (bootstrapClusterHub._running) return;
    bootstrapClusterHub._running = true;
    try {
      // 忙碌只留页顶 meta；摘要行留给结果/错误，避免「进页自动分组中」叠三处
      if (quantOlsSummary) {
        quantOlsSummary.textContent = "";
        quantOlsSummary.classList.remove("is-busy");
      }
      if (quantFactorList) quantFactorList.innerHTML = "";
      setQuantMeta("分组中…", { busy: true });
      await runFactorOlsClustersSuggest();
    } finally {
      bootstrapClusterHub._running = false;
    }
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

  function setStrategyFactorExportEnabled(on) {
    const icBtn = document.getElementById("strategy-ic-export");
    const wBtn = document.getElementById("strategy-weight-diff-export");
    const fbBtn = document.getElementById("strategy-feedback-from-ic");
    if (icBtn) icBtn.disabled = !on;
    if (wBtn) wBtn.disabled = !on;
    if (fbBtn) fbBtn.disabled = !on;
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
        strategyLastIcExport = null;
        strategyLastWeightDiff = null;
        return;
      }
      const exp =
        data.ic_mode === "cs_ic" && data.factor_cs_ic
          ? data.factor_cs_ic
          : data.factor_experiment || {};
      strategyLastIcExport = {
        stock_code: data.stock_code || exp.stock_code || code,
        generated_at: new Date().toISOString(),
        panel: exp.panel || null,
        factors: exp.factors || exp.rows || null,
        ic_mode: data.ic_mode,
        note: "只读 IC 导出；不改写 signal_config",
      };
      strategyLastWeightDiff = data.config_diff || {
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
          `标的 ${strategyLastIcExport.stock_code} · ${data.ic_mode || "single"} · 只读建议，须人审后合并配置`;
      }
      setStrategyFactorExportEnabled(true);
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }


  function renderFactorPanelTable(panel, suggest) {
    if (!quantFactorList) return;
    if (!panel || !panel.success) {
      lastFactorPanelForMerge = null;
      lastWeightSuggestForMerge = null;
      renderMergedFactorTable();
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
    lastFactorPanelForMerge = panel;
    if (suggest && (suggest.success || suggest.suggested_weights)) {
      lastWeightSuggestForMerge = suggest;
    }
    renderMergedFactorTable();
  }

  async function loadFactorPanel() {
    if (!quantFactorList) return;
    try {
      const res = await fetch("/api/quant/factor-panel");
      const data = await res.json();
      if (!data.success) {
        if (quantMeta) quantMeta.textContent = data.error || "因子面板加载失败";
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
      lastFactorPanelForMerge = data;
      // 不预填 IC：等用户点分析；若已有 OLS 则仍可显示 OLS 列
      if (lastOlsForMerge || lastWeightSuggestForMerge) {
        renderMergedFactorTable();
      }
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
    }
  }

  function renderCrossSection(data) {
    if (!quantCrossSummary && !quantCrossList) return;
    if (!data || !data.success) {
      setBusyText(
        quantCrossSummary,
        (data && data.error) || "排序失败",
        { busy: false }
      );
      if (quantCrossList) {
        quantCrossList.innerHTML =
          `<p class="watching-table-empty">${escapeHtml(
            (data && data.error) || "排序失败"
          )}</p>`;
      }
      return;
    }
    const neut = data.neutralization || {};
    const neutNote = neut.applied
      ? ` · 截面中性化(${neut.method || "zscore"})`
      : "";
    setBusyText(
      quantCrossSummary,
      `Top ${data.ranked_count} / 候选 ${data.candidate_count} · min_score=${data.min_score}${neutNote}`,
      { busy: false }
    );
    const ranking = Array.isArray(data.ranking) ? data.ranking : [];
    if (!quantCrossList) return;
    if (!ranking.length) {
      quantCrossList.innerHTML = `<p class="watching-table-empty">无排序结果</p>`;
      return;
    }
    const rows = ranking.map((r, i) => {
      const code = String(r.stock_code || "").trim();
      const name = r.stock_name || code || "—";
      const score =
        r.score != null && !Number.isNaN(Number(r.score))
          ? String(Math.round(Number(r.score)))
          : "—";
      const raw =
        r.score_raw != null && !Number.isNaN(Number(r.score_raw))
          ? String(Math.round(Number(r.score_raw)))
          : r.score_raw != null
            ? String(r.score_raw)
            : "—";
      return {
        rank: String(i + 1),
        code,
        name,
        score,
        raw,
        source: r.data_source || "—",
      };
    });
    quantCrossList.innerHTML = researchGridHtml(
      [
        {
          id: "rank",
          label: "#",
          widthPct: 8,
          num: true,
          headClass: "watching-col-center",
          cellClass: "watching-col-center",
        },
        { id: "name", label: "股票", flex: true, cellClass: "watching-stock" },
        { id: "score", label: "评分", widthPct: 14, num: true },
        { id: "raw", label: "raw", widthPct: 14, num: true },
        { id: "source", label: "源", widthPct: 22 },
      ],
      rows,
      (col, d) => {
        if (col.id === "name") {
          return (
            `<div class="watching-stock" title="${escapeHtml(
              (d.name || "") + " " + (d.code || "")
            )}">` +
            watchingNameSpanHtml(d.name || d.code) +
            `<span class="watching-code-sub">${escapeHtml(d.code || "")}</span></div>`
          );
        }
        return escapeHtml(d[col.id] ?? "—");
      },
      { emptyText: "无排序结果" }
    );
  }

  async function refreshCrossSection() {
    if (!quantCrossSummary && !quantCrossList) return null;
    setBusyText(quantCrossSummary, "排序中…", { busy: true });
    if (quantCrossList) quantCrossList.innerHTML = "";
    const res = await fetch("/api/quant/cross-section", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit: 10, horizon_days: readHorizonDays() }),
    });
    const data = await res.json();
    renderCrossSection(data);
    return data;
  }

  function renderFactorOls(data) {
    if (!quantOlsSummary && !quantFactorList && !quantProbeResult) return;
    const summaryHost =
      quantLastOlsClusters && quantLastOlsClusters.success && quantProbeSummary
        ? quantProbeSummary
        : quantOlsSummary;
    if (!data || !data.success) {
      lastOlsForMerge = null;
      setBusyText(
        summaryHost,
        (data && data.error) || "OLS 摘要将显示在此",
        { busy: false }
      );
      renderMergedFactorTable();
      return;
    }
    lastOlsForMerge = data;
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
    quantLastWeightDiff = diff;
    const safe = String(diff.cluster_label || label || "cluster").replace(
      /[^\w\u4e00-\u9fff\-]+/g,
      "_"
    );
    downloadJson(diff, `signal_config_weight_diff_${safe}.json`);
    setQuantMeta(`已导出 ${diff.cluster_label || label || "组"} 权重 diff · 不写盘`);
  }

  function renderOlsClusters(data) {
    if (!quantOlsClusters) return;
    quantLastOlsClusters = data && data.success ? data : null;
    markProbeReadyFromClusters(data);
    if (!data || !data.success) {
      quantOlsClusters.innerHTML = `<span class="sub">${escapeHtml(
        (data && data.error) || "β 分组失败"
      )}</span>`;
      renderMergedFactorTable();
      return;
    }
    const clusters = Array.isArray(data.clusters) ? data.clusters : [];

    // 花名册并入下方 G 标题；此处仅摘要 + 未入组提示
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
    const metaLine =
      `<div class="quant-cluster-status">` +
      `<div class="quant-cluster-status-stats">` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(String(nGroups))}</b> 组</span>` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(
        String(nWatchAll || nUni)
      )}</b> 观察</span>` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(
        String(nFitted)
      )}</b> 拟合</span>` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(
        String(nClustered)
      )}</b> 入组</span>` +
      (data.singleton_outlier_count
        ? `<span class="quant-cluster-stat is-warn"><b>${escapeHtml(
            String(data.singleton_outlier_count)
          )}</b> 单票离群</span>`
        : nOutlier
          ? `<span class="quant-cluster-stat is-warn"><b>${escapeHtml(
              String(nOutlier)
            )}</b> β离群</span>`
          : "") +
      (nDataSkip
        ? `<span class="quant-cluster-stat is-warn"><b>${escapeHtml(
            String(nDataSkip)
          )}</b> 数据不足</span>`
        : "") +
      `</div>` +
      `<p class="quant-cluster-method">` +
      `<span class="quant-cluster-method-k">方法</span>` +
      `<span class="quant-cluster-method-v">${escapeHtml(
        methodBits.join(" · ")
      )}</span>` +
      `</p>` +
      `</div>`;

    const rosterNameByCode = clusterNameByCodeFromData(data);
    // 兼容旧字段 β离群未入簇（现多为单票组，一般为空）
    const outlierNote = outliers.length
      ? `<p class="sub">未入组离群：${escapeHtml(
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
    // 宇宙=观察池：成员已在 G 标题；纸面未映射票单独提示（落地用）
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
        `<p class="sub quant-cluster-unmapped">纸面未入组：${escapeHtml(
          umLines
        )}</p>`;
    }

    // 落地主路径 + 次操作折叠
    const landingHost =
      `<div class="quant-cluster-landing" id="quant-cluster-landing" aria-label="分组权落地">` +
      `<p class="quant-fingerprint is-busy">加载落地状态…</p>` +
      `</div>`;

    quantOlsClusters.innerHTML =
      `${metaLine}${outlierNote}${holdHtml}${landingHost}`;
    renderMergedFactorTable();
    syncProbeCodeOptionsFromClusters();
    refreshClusterLiveStatus();
  }

  function clusterLandingHtml(data) {
    const cs = (data && data.cluster_scoring) || {};
    const act = (data && data.active) || {};
    const draft = (data && data.draft) || {};
    const book = (data && data.book) || {};
    const h = (data && data.health) || {};
    const land = (data && data.landing) || {};
    const mode = cs.mode || "off";
    const modeLabel =
      mode === "active"
        ? "已启用组权"
        : mode === "shadow"
          ? "对照中（主分仍全局）"
          : "未接通";
    const cov =
      h.coverage != null ? `${Math.round(Number(h.coverage) * 100)}%` : "—";
    const age = h.age_days != null ? `${h.age_days}d` : "—";
    const alerts = (h.alerts || []).slice(0, 3);
    const canApply = !!land.can_apply || !!draft.exists || !!act.exists;
    const canActivate = !!land.can_activate;
    const readyFollow = !!land.ready_for_follow || mode === "active";
    const nextStep = land.next_step || "";
    const doneResearch = nextStep === "go_follow" || readyFollow;
    const next = land.next_label || "① 对照";
    const nextPrefix = doneResearch ? "状态：" : "下一步：";
    const audit = (data && data.audit_sample) || {};
    const rows = Array.isArray(audit.rows) ? audit.rows : [];
    const ev = (data && data.enable_evidence) || {};
    const evGate = ev.gate || {};
    const evBlockers = Array.isArray(evGate.blockers) ? evGate.blockers : [];
    const evWarns = Array.isArray(evGate.warnings) ? evGate.warnings : [];
    const oos = ev.oos_summary || {};
    const turn = ev.turnover_est || {};
    const exp = ev.exposure_summary || {};
    const topSec = exp.top_sector || null;

    const topN =
      book.top_n_per_group != null
        ? book.top_n_per_group
        : cs.top_n_per_group != null
          ? cs.top_n_per_group
          : ev.top_n_per_group != null
            ? ev.top_n_per_group
            : "—";
    const stats =
      `<div class="quant-cluster-landing-stats">` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(modeLabel)}</b> mode</span>` +
      `<span class="quant-cluster-stat"><b>v${escapeHtml(
        String(act.version != null ? act.version : "—")
      )}</b> 映射</span>` +
      `<span class="quant-cluster-stat" title="每组相对序取前 N，再合并为候选簿"><b>${escapeHtml(
        String(topN)
      )}</b> 组内TopN</span>` +
      `<span class="quant-cluster-stat"><b>${escapeHtml(cov)}</b> 覆盖</span>` +
      `<span class="quant-cluster-stat${h.stale ? " is-warn" : ""}"><b>${escapeHtml(
        age
      )}</b> 龄</span>` +
      `<span class="quant-cluster-stat" title="合并簿只数=账户调仓目标，非单组 Top-N"><b>${escapeHtml(
        String(book.name_count != null ? book.name_count : "—")
      )}</b> 簿</span>` +
      `</div>`;

    const alertHtml = alerts.length
      ? `<p class="quant-cluster-landing-alerts">${escapeHtml(
          alerts.join("；")
        )}</p>`
      : "";

    const evidenceOpen = mode === "shadow" || !!evBlockers.length;
    const evidenceHtml =
      mode === "off" && !act.exists
        ? ""
        : `<details class="quant-cluster-evidence" ${
            evidenceOpen ? "open" : ""
          }>` +
          `<summary>启用证据包 ` +
          `<span class="sub${evGate.ok === false ? " down" : ""}">${
            evGate.ok === false
              ? "未通过"
              : evGate.ok
                ? "可启用"
                : "—"
          }</span></summary>` +
          `<ul class="quant-cluster-evidence-list">` +
          `<li>簿长 ${escapeHtml(
            String(ev.name_count != null ? ev.name_count : book.name_count ?? "—")
          )} · 组内TopN=${escapeHtml(String(topN))} · max=${escapeHtml(
            String(ev.max_names != null ? ev.max_names : "—")
          )}</li>` +
          `<li>OOS 通过 ${escapeHtml(String(oos.pass_count ?? "—"))} / 失败 ${escapeHtml(
            String(oos.fail_count ?? "—")
          )} / 未知 ${escapeHtml(String(oos.unknown_count ?? "—"))}</li>` +
          `<li title="相对纸面 vs 合并簿">换手估计 卖 ${escapeHtml(
            String(turn.would_sell_count ?? "—")
          )} · 买 ${escapeHtml(String(turn.would_buy_count ?? "—"))}</li>` +
          `<li>行业集中 ${
            topSec
              ? escapeHtml(
                  `${topSec.name} ${topSec.weight_pct != null ? topSec.weight_pct + "%" : ""}`
                )
              : "—"
          }</li>` +
          `<li>双分样本 ${escapeHtml(
            String((ev.score_audit_sample || {}).count ?? rows.length)
          )} 只</li>` +
          (evBlockers.length
            ? `<li class="down">拦：${escapeHtml(evBlockers.join("；"))}</li>`
            : "") +
          (evWarns.length
            ? `<li class="is-warn">提示：${escapeHtml(evWarns.join("；"))}</li>`
            : "") +
          `</ul></details>`;

    const actions =
      `<div class="quant-cluster-landing-actions">` +
      `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
      `data-cluster-export="live-apply" ${canApply ? "" : "disabled"} ` +
      `title="晋升映射 → shadow → 刷新簿（更新交易执行打分用的组权）">① 对照</button>` +
      `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
      `data-cluster-export="live-active" ${canActivate ? "" : "disabled"} ` +
      `title="证据包+健康门禁通过后启用组权打分（交易执行页 score 吃组权）">② 启用</button>` +
      `<span class="sub quant-cluster-landing-next${
        doneResearch ? " is-done" : ""
      }">${nextPrefix}${escapeHtml(next)}</span>` +
      `</div>`;

    let auditHtml = "";
    if (mode === "shadow" || mode === "active") {
      const body = rows.length
        ? rows
            .map((r) => {
              const dg = r.delta_vs_global;
              const dCls =
                dg != null && Number(dg) > 0
                  ? "up"
                  : dg != null && Number(dg) < 0
                    ? "down"
                    : "";
              return (
                `<div class="watching-react-grid-row">` +
                `<div class="watching-react-grid-cell" style="flex:1 1 auto;min-width:0">` +
                `${escapeHtml(
                  formatStockCodeName(r.stock_code, r.stock_name || "")
                )}` +
                (r.cluster_label
                  ? ` <span class="sub">${escapeHtml(String(r.cluster_label))}</span>`
                  : "") +
                `</div>` +
                `<div class="watching-react-grid-cell watching-col-num num" style="flex:0 0 14%">` +
                `${escapeHtml(
                  r.score_global != null ? Number(r.score_global).toFixed(2) : "—"
                )}</div>` +
                `<div class="watching-react-grid-cell watching-col-num num" style="flex:0 0 14%">` +
                `${escapeHtml(
                  r.score_cluster != null
                    ? Number(r.score_cluster).toFixed(2)
                    : "—"
                )}</div>` +
                `<div class="watching-react-grid-cell watching-col-num num" style="flex:0 0 12%">` +
                `<span class="bt-trade-ret ${dCls}">${escapeHtml(
                  dg != null
                    ? `${Number(dg) > 0 ? "+" : ""}${Number(dg).toFixed(2)}`
                    : "—"
                )}</span></div>` +
                `</div>`
              );
            })
            .join("")
        : `<p class="watching-table-empty">${escapeHtml(
            audit.error
              ? `双分对照失败：${audit.error}`
              : "双分对照暂无样本（行情/打分未就绪）"
          )}</p>`;
      const sampled =
        audit.sampled_at || audit.version != null
          ? ` · 采样${
              audit.version != null ? ` v${audit.version}` : ""
            }${audit.sampled_at ? ` @ ${String(audit.sampled_at).slice(11, 19)}` : ""}`
          : "";
      auditHtml =
        `<div class="quant-cluster-landing-audit">` +
        `<div class="quant-cluster-tables-head">` +
        `<span class="quant-cluster-tables-label">双分对照</span>` +
        `<span class="sub">全局 / 组权 / Δ${escapeHtml(sampled)} · 不写 config</span>` +
        `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" ` +
        `data-cluster-export="live-audit" title="轮换样本并重新打分">刷新对照</button>` +
        `</div>` +
        (rows.length
          ? `<div class="watching-react-grid quant-research-grid quant-cluster-audit-grid">` +
            `<div class="watching-react-grid-head"><div class="watching-react-grid-row is-head">` +
            `<div class="watching-react-grid-cell" style="flex:1 1 auto">标的</div>` +
            `<div class="watching-react-grid-cell watching-col-num" style="flex:0 0 14%">全局</div>` +
            `<div class="watching-react-grid-cell watching-col-num" style="flex:0 0 14%">组权</div>` +
            `<div class="watching-react-grid-cell watching-col-num" style="flex:0 0 12%">Δ</div>` +
            `</div></div>` +
            `<div class="watching-react-grid-body quant-research-grid-body">${body}</div>` +
            `</div>`
          : body) +
        `</div>`;
    }

    const more =
      `<details class="quant-cluster-more quant-cluster-advanced" id="quant-cluster-live-bar">` +
      `<summary>更多 ` +
      `<span class="sub" id="quant-cluster-live-status">回滚 / 关闭 / 导出</span></summary>` +
      `<div class="quant-cluster-advanced-actions">` +
      `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-off">关闭</button>` +
      `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-rollback">回滚</button>` +
      `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="artifact">导出映射</button>` +
      `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-refresh">刷新簿</button>` +
      `</div></details>`;

    return (
      `<div class="quant-cluster-landing-card">` +
      `<div class="quant-cluster-landing-head">` +
      `<span class="quant-cluster-tables-label">落地</span>` +
      `</div>` +
      stats +
      alertHtml +
      evidenceHtml +
      actions +
      auditHtml +
      more +
      `</div>`
    );
  }

  async function refreshClusterLiveStatus({ auditRotate = false } = {}) {
    try {
      const q = new URLSearchParams();
      if (auditRotate) {
        q.set("audit_rotate", "true");
        q.set("audit_offset", String(Date.now() % 10000000));
      }
      const url =
        "/api/quant/cluster-live/status" +
        (q.toString() ? `?${q.toString()}` : "");
      const res = await fetch(url);
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

  function formatClusterApiError(data, status) {
    const raw = data && (data.error != null ? data.error : data.detail);
    if (typeof raw === "string" && raw.trim()) return raw;
    if (Array.isArray(raw)) {
      const bits = raw
        .map((item) => {
          if (typeof item === "string") return item;
          if (!item || typeof item !== "object") return String(item);
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((x) => x !== "body").join(".")
            : "";
          const msg = item.msg || item.message || "";
          return loc && msg ? `${loc}: ${msg}` : msg || JSON.stringify(item);
        })
        .filter(Boolean);
      if (bits.length) return bits.join("；");
    }
    if (raw && typeof raw === "object") {
      try {
        return JSON.stringify(raw);
      } catch (_) {
        /* ignore */
      }
    }
    if (status === 404) return "接口未找到：请重启 Web";
    return status ? `HTTP ${status}` : "请求失败";
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

  async function runClusterLiveApply() {
    const art =
      quantLastOlsClusters && quantLastOlsClusters.pool_artifact;
    if (
      !window.confirm(
        "一键应用分组？\n将：晋升映射 → 影子模式 → 刷新分池簿。\n不写 signal_config.weights。"
      )
    ) {
      return;
    }
    setQuantMeta("应用分组中…", { busy: true });
    const body = { from_draft: true, mode: "shadow", note: "一键应用" };
    if (art && art.success && art.code_map) {
      body.artifact = art;
      body.from_draft = false;
    }
    const out = await postClusterLive("/api/quant/cluster-live/apply", body);
    if (!out.ok) {
      setQuantMeta(`应用失败 · ${out.error}`, { error: true });
      return;
    }
    const n =
      (out.data.refresh &&
        out.data.refresh.rank &&
        out.data.refresh.rank.name_count) ||
      0;
    setQuantMeta(
      `${out.data.note || "已应用"}` + (n ? ` · 簿 ${n} 只` : "")
    );
    refreshClusterLiveStatus();
  }

  async function runClusterLivePromote() {
    // 兼容旧入口：走一键应用
    return runClusterLiveApply();
  }

  async function runClusterLiveMode(mode) {
    let confirmMsg = `将 cluster_scoring.mode 设为 ${mode}？\n仅改开关，不改全局 weights。`;
    if (mode === "active") {
      try {
        const stRes = await fetch("/api/quant/cluster-live/status");
        const st = await stRes.json();
        const ev = (st && st.enable_evidence) || {};
        const oos = ev.oos_summary || {};
        const turn = ev.turnover_est || {};
        confirmMsg =
          `启用组权前请确认证据包：\n` +
          `· 簿 ${ev.name_count ?? "—"} 只 · TopN=${ev.top_n_per_group ?? "—"}\n` +
          `· OOS 通过 ${oos.pass_count ?? 0} / 失败 ${oos.fail_count ?? 0}\n` +
          `· 相对纸面约卖 ${turn.would_sell_count ?? 0} · 买 ${turn.would_buy_count ?? 0}\n` +
          `· 健康覆盖 ${
            ev.health && ev.health.coverage != null
              ? Math.round(Number(ev.health.coverage) * 100) + "%"
              : "—"
          }\n\n` +
          `确认将 mode 设为 active？（不改全局 weights）`;
        if (ev.gate && ev.gate.ok === false) {
          setQuantMeta(
            `证据包未通过 · ${(ev.gate.blockers || []).join("；") || "见落地卡"}`,
            { error: true }
          );
          refreshClusterLiveStatus();
          return;
        }
      } catch (_) {
        /* 仍走后端门禁 */
      }
    }
    if (!window.confirm(confirmMsg)) {
      return;
    }
    setQuantMeta(`设置 mode=${mode}…`, { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/mode", { mode });
    if (!out.ok) {
      setQuantMeta(`设置失败 · ${out.error}`, { error: true });
      return;
    }
    setQuantMeta(`分组 live mode=${mode}`);
    refreshClusterLiveStatus();
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
    if (!window.confirm("回滚到上一版 live 映射？")) return;
    setQuantMeta("回滚中…", { busy: true });
    const out = await postClusterLive("/api/quant/cluster-live/rollback", {});
    if (!out.ok) {
      setQuantMeta(`回滚失败 · ${out.error}`, { error: true });
      return;
    }
    setQuantMeta(`已回滚 · v${out.data.version ?? "—"}`);
    refreshClusterLiveStatus();
  }

  function exportPoolArtifact() {
    const art =
      quantLastOlsClusters && quantLastOlsClusters.pool_artifact;
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

  function clusterPoolBookAndArtifact() {
    const book =
      quantLastOlsClusters &&
      quantLastOlsClusters.pool_merge &&
      quantLastOlsClusters.pool_merge.book &&
      quantLastOlsClusters.pool_merge.book.book;
    const art =
      quantLastOlsClusters && quantLastOlsClusters.pool_artifact;
    return { book: book || [], artifact: art && art.success ? art : null };
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

  async function runClusterPaperPreview() {
    setQuantMeta("调仓已收口到交易执行页 · 请打开 /follow", { error: true });
  }

  function exportMultiScore() {
    const ms = quantLastOlsClusters && quantLastOlsClusters.multi_score;
    if (!ms || !ms.success) {
      setQuantMeta("无多权打分结果", { error: true });
      return;
    }
    downloadJson(ms, `cluster_multi_score_${Date.now()}.json`);
    setQuantMeta(
      `已导出多权分 · ${ms.scored_count ?? "—"} 只 · 仅组内序`
    );
  }

  async function runClusterMultiRescore() {
    const art =
      quantLastOlsClusters && quantLastOlsClusters.pool_artifact;
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
      if (quantLastOlsClusters) {
        quantLastOlsClusters.multi_score = data;
        renderOlsClusters(quantLastOlsClusters);
      }
      setQuantMeta(
        `多权复打完成 · ${data.scored_count ?? "—"} 只 · 仅组内序 · 不进 live`
      );
      if (quantOlsSummary) {
        setBusyText(
          quantOlsSummary,
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

  function weightSuggestStatusHtml(suggest) {
    if (!suggest || !suggest.success) return "";
    const lines = (suggest.rationale || []).slice(0, 4);
    const gate = suggest.oos_gate || {};
    let gateLine = "";
    if (gate.skipped) {
      gateLine = `<span class="sub">OOS 门禁：已跳过（${escapeHtml(
        String(gate.reason || "—")
      )}）· promote_ready=否</span>`;
    } else if (gate.ok && gate.passed) {
      const d =
        gate.delta_oos_pp != null ? ` · ΔOOS ${gate.delta_oos_pp}pp` : "";
      gateLine = `<span class="sub up">OOS 门禁：通过${escapeHtml(d)} · 仍须人审</span>`;
    } else if (gate.ok) {
      gateLine = `<span class="sub down">OOS 门禁：未过（${escapeHtml(
        String(gate.reason || "—")
      )}）· 不建议 promote</span>`;
    }
    const warns = [
      ...((suggest.constraint_warnings || []).slice(0, 2)),
      ...((suggest.redundancy_warnings || []).slice(0, 1)),
    ];
    const warnHtml = warns.length
      ? `<span class="sub">${warns.map((w) => escapeHtml(String(w))).join(" · ")}</span>`
      : "";
    return (
      `<strong>权重建议</strong>（${escapeHtml(String(suggest.ic_mode || "—"))}` +
      `${suggest.promote_ready ? " · promote_ready" : ""}）<br/>` +
      (lines.length ? lines.map((l) => `${escapeHtml(String(l))}<br/>`).join("") : "") +
      (gateLine ? `${gateLine}<br/>` : "") +
      (warnHtml ? `${warnHtml}<br/>` : "") +
      `<span class="sub">${escapeHtml(
        (suggest.config_diff && suggest.config_diff.apply_note) ||
          "导出 diff 可手动合并；不自动写盘"
      )}</span>`
    );
  }

  function renderFactorExperiment(exp, suggest) {
    if (!exp || !exp.success) {
      setQuantMeta((exp && exp.error) || "分析失败", { error: true });
      lastWeightSuggestForMerge = null;
      renderFactorPanelTable(null);
      if (quantWeightSuggest) quantWeightSuggest.textContent = "";
      quantLastWeightDiff = null;
      return;
    }
    const panel = exp.panel || { success: true, rows: exp.factors || [] };
    if (panel && panel.success == null) panel.success = true;
    const icCount = exp.panel
      ? exp.panel.ic_ready_count
      : (exp.factors || []).filter((f) => f.ic != null).length;
    if (quantMeta) {
      const keepBusy = quantMeta.classList.contains("is-busy");
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
      if (quantWeightSuggest) quantWeightSuggest.innerHTML = weightSuggestStatusHtml(suggest);
      lastWeightSuggestForMerge = suggest;
      renderFactorPanelTable(panel, suggest);
      quantLastWeightDiff = suggest.config_diff || null;
    } else {
      if (quantWeightSuggest) quantWeightSuggest.textContent = "";
      lastWeightSuggestForMerge = null;
      renderFactorPanelTable(panel, null);
      quantLastWeightDiff = null;
    }
  }

  const btnQuant = document.getElementById("btn-quant");
  // 对话工作台顶栏由 results Tab 接管，避免重复加载
  if (btnQuant && btnQuant.tagName === "BUTTON" && !btnQuant.dataset.resultsTab) {
    btnQuant.addEventListener("click", () => openQuantDialog());
  }

  async function onGotoFollow(e) {
    e.preventDefault();
    try {
      await gotoFollowTab();
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
      }
  }
  on("quant-goto-paper", "click", onGotoFollow);
  on("quant-goto-paper-from-compare", "click", onGotoFollow);

  on("quant-follow-refresh", "click", async (e) => {
    e.preventDefault();
    await loadFollowCard();
  });

  on("quant-watching-sync-follow", "click", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/watching/refresh?sync_paper=true", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      await loadWatchingPanel();
      await loadFollowCard();
      const el = document.getElementById("quant-follow-summary");
      if (el) el.textContent = `${el.textContent} · 已同步研究池`;
      } catch (err) {
      const el = document.getElementById("quant-follow-summary");
      if (el) el.textContent = String(err.message || err);
      }
  });

  on("quant-watching-init", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-watching-init");
    if (btn) btn.disabled = true;
    setPoolMeta("正在从模板初始化…");
    try {
      const res = await fetch("/api/watching/init", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      await loadWatchingPanel();
      setWatchingRefreshStatus("已创建观察名单，可用搜索加入股票");
    } catch (err) {
      const msg = String(err.message || err);
      setPoolMeta(msg);
      setWatchingRefreshStatus(msg, { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-watching-remove", "click", async (e) => {
    e.preventDefault();
    await removeSelectedWatchingItems();
  });

  on("quant-watching-sync", "click", (e) => {
    e.preventDefault();
    openWatchingBuildLayer(getSelectedWatchingCodes());
  });

  on("watching-build-close", "click", (e) => {
    e.preventDefault();
    closeWatchingBuildLayer();
  });

  on("watching-build-cancel", "click", (e) => {
    e.preventDefault();
    closeWatchingBuildLayer();
  });

  on("watching-build-confirm", "click", async (e) => {
    e.preventDefault();
    await confirmWatchingBuild();
  });

  on("watching-build-shares-input", "input", () => {
    watchingBuildSharesByCode = {};
    watchingBuildAmountByCode = {};
    scheduleWatchingBuildPreview();
  });

  document.querySelectorAll(".watching-build-mode-btn").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const mode = String(btn.dataset.mode || "amount");
      if (mode === watchingBuildMode) return;
      watchingBuildMode = mode;
      watchingBuildSharesByCode = {};
      watchingBuildAmountByCode = {};
      const input = document.getElementById("watching-build-shares-input");
      if (input) {
        if (mode === "amount") input.value = "20000";
        else if (mode === "pct") input.value = "10";
        else input.value = "200";
      }
      syncWatchingBuildModeUI();
      scheduleWatchingBuildPreview();
    });
  });

  const watchingBuildBodyEl = document.getElementById("watching-build-body");
  if (watchingBuildBodyEl && watchingBuildBodyEl.dataset.sharesWired !== "1") {
    watchingBuildBodyEl.dataset.sharesWired = "1";
    watchingBuildBodyEl.addEventListener("input", (e) => {
      const sharesInput = e.target.closest(".watching-build-row-shares");
      if (sharesInput) {
        const code = String(sharesInput.dataset.code || "").trim();
        if (!code) return;
        const n = Math.floor(Number(sharesInput.value || 0) / 100) * 100;
        if (Number.isFinite(n) && n >= 100) {
          watchingBuildSharesByCode[code] = n;
        } else {
          delete watchingBuildSharesByCode[code];
        }
        scheduleWatchingBuildPreview();
        return;
      }
      const amtInput = e.target.closest(".watching-build-row-amount");
      if (!amtInput) return;
      const code = String(amtInput.dataset.code || "").trim();
      if (!code) return;
      const n = Number(amtInput.value || 0);
      if (Number.isFinite(n) && n > 0) {
        watchingBuildAmountByCode[code] = Math.round(n * 100) / 100;
      } else {
        delete watchingBuildAmountByCode[code];
      }
      scheduleWatchingBuildPreview();
    });
  }

  on("watching-build-layer", "click", (e) => {
    if (e.target && e.target.id === "watching-build-layer") closeWatchingBuildLayer();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const layer = document.getElementById("watching-build-layer");
    if (layer && !layer.hidden) closeWatchingBuildLayer();
  });

  const watchTableEl = document.getElementById("watching-watchlist-table");
  if (watchTableEl) {
    watchingScoreTips.bindHost(watchTableEl, {
      scoreSelector: ".watching-score-cell[data-score-detail], .paper-hold-score[data-score-detail]",
    });
  }
  if (watchTableEl && watchTableEl.dataset.removeWired !== "1") {
    watchTableEl.dataset.removeWired = "1";
    watchTableEl.addEventListener("click", (e) => {
      const buildBtn = e.target.closest(".watching-build-btn");
      if (buildBtn) {
        e.preventDefault();
        const tr = buildBtn.closest("[data-code]");
        const nameEl = tr ? tr.querySelector(".watching-name-text") : null;
        openWatchingBuildLayer(
          [buildBtn.dataset.code],
          watchingNameFromEl(nameEl, buildBtn.dataset.code)
        );
        return;
      }
      const heldBtn = e.target.closest(".watching-held-btn");
      if (heldBtn) {
        e.preventDefault();
        gotoFollowPage(heldBtn.dataset.code);
        return;
      }
      const sentBadge = e.target.closest(".watching-sent-badge");
      if (sentBadge) {
        e.preventDefault();
        const code = sentBadge.dataset.code;
        const tr = sentBadge.closest("[data-code]");
        const nameEl = tr ? tr.querySelector(".watching-name-text") : null;
        const name = watchingNameFromEl(nameEl, tr ? tr.dataset.code : code);
        showWatchingChart(code, name);
        openWatchingNewsDetail(code);
        return;
      }
      // 点击股票名称行时同步显示日线图 + 舆情
      const stockCell = e.target.closest(".watching-stock");
      if (stockCell) {
        const tr = stockCell.closest("[data-code]");
        if (tr && tr.dataset.code) {
          e.preventDefault();
          const nameEl = stockCell.querySelector(".watching-name-text");
          const name = watchingNameFromEl(nameEl, tr.dataset.code);
          showWatchingChart(tr.dataset.code, name);
          openWatchingNewsDetail(tr.dataset.code);
        }
      }
    });
    watchTableEl.addEventListener("change", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLInputElement)) return;
      if (t.id === "watching-select-all") {
        if (watchingGrid && watchingGridReady) {
          (watchingGrid.getData() || []).forEach((rowData) => {
            const code = String((rowData && rowData.code) || "");
            const row = watchingGrid.getRow(code);
            if (row) row.update({ picked: !!t.checked });
          });
        } else {
          watchTableEl.querySelectorAll(".watching-pick").forEach((box) => {
            box.checked = t.checked;
          });
        }
        t.indeterminate = false;
        updateWatchingPickCount();
        return;
      }
      if (t.classList.contains("watching-pick")) {
        const code = String(t.dataset.code || t.value || "").trim();
        if (watchingGrid && watchingGridReady && code) {
          const row = watchingGrid.getRow(code);
          if (row) row.update({ picked: !!t.checked });
        }
        syncWatchingSelectAllState();
      }
    });
  }

  on("watching-news-detail-close", "click", (e) => {
    e.preventDefault();
    hideWatchingNewsDetail();
  });

  on("watching-chart-close", "click", (e) => {
    e.preventDefault();
    hideWatchingChart();
  });

  on("quant-cross-run", "click", async (e) => {
    e.preventDefault();
    try {
      await refreshCrossSection();
    } catch (err) {
      if (quantCrossSummary) quantCrossSummary.textContent = String(err.message || err);
    }
  });

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
      lastFactorPanelForMerge = {
        rows,
        factors: rows,
        mode: "factor_cross_section",
      };
      lastWeightSuggestForMerge = sug;
      if (sug.factor_ols && sug.factor_ols.success) lastOlsForMerge = sug.factor_ols;
      renderMergedFactorTable();
      if (quantWeightSuggest) quantWeightSuggest.innerHTML = weightSuggestStatusHtml(sug);
      quantLastWeightDiff = sug.config_diff || null;
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
      if (quantLastOlsClusters && quantLastOlsClusters.success && quantProbeSummary) {
        setBusyText(quantProbeSummary, icMsg + " · 不冲组表", { busy: false });
        const fold = document.getElementById("quant-probe-fold");
        if (fold) fold.open = true;
      }
      setQuantMeta(icMsg);
      return;
    }
    renderFactorExperiment(exp, sug);
  }

  async function runFactorOlsSuggest() {
    const code = readOlsCode();
    setBusyText(quantOlsSummary, `OLS 实验中… · ${code}`, { busy: true });
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
    renderFactorOls(await res.json());
  }

  async function runFactorOlsPoolSuggest() {
    setBusyText(quantOlsSummary, "研究池 OLS 中…", { busy: true });
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
      setBusyText(quantOlsSummary, detail, { busy: false });
      setQuantMeta(`池内 OLS 失败 · ${detail}`, { error: true });
      return;
    }
    renderFactorOls(data);
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

  async function runFactorOlsClustersSuggest() {
    const started = Date.now();
    const tick = () => {
      const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
      // 进度只走页顶 meta；摘要行空着，算完由状态条接手
      setQuantMeta(`分组中… ${sec}s · 观察池`, { busy: true });
    };
    tick();
    const timer = setInterval(tick, 1000);
    await ensureFactorMeta();
    try {
      const res = await fetch("/api/quant/factor-ols-clusters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lookback: 80,
          horizon_days: readHorizonDays(),
          watching_limit: 12,
          // null → 自动目标 k≈n/5（约 4～10）+ 超大组二分
          n_clusters: null,
          cluster_method: "hierarchical",
          cluster_linkage: "complete",
          within_dist_quantile: 0.75,
          ridge_lambda: readRidgeLambda(),
          pit_fundamentals: false,
          beta_scale: "feature_zscore",
          run_oos_gate: true,
          oos_tol_pp: 1.0,
          run_group_score: true,
          run_pool_merge: true,
          top_n_per_group: 10,
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
        setBusyText(quantOlsSummary, detail, { busy: false });
        setQuantMeta(`分组失败 · ${detail}`, { error: true });
        if (quantOlsClusters) {
          quantOlsClusters.textContent = detail;
        }
        return;
      }
      renderOlsClusters(data);
      if (data && data.success) {
        const nCl = data.n_clusters ?? "—";
        const nUni = data.universe_count ?? data.stock_count ?? "—";
        const nWatch = (data.watching_codes || []).length;
        const nIn = data.stock_count ?? "—";
        const sec = Math.max(1, Math.round((Date.now() - started) / 1000));
        const os = data.oos_summary || {};
        const oosTag = os.run
          ? ` · OOS✓${os.passed ?? 0}/✗${os.failed ?? 0}`
          : "";
        const pmOk = data.pool_merge && data.pool_merge.success ? " · 分池合成" : "";
        // 统计条在 quant-ols-clusters；摘要行留空，避免与 meta / 状态条重复
        if (quantOlsSummary) {
          quantOlsSummary.textContent = "";
          quantOlsSummary.classList.remove("is-busy");
        }
        setQuantMeta(
          `分组 · ${nCl} 组 · 观察 ${nWatch || nUni} · 入组 ${nIn} · ${sec}s${oosTag}${pmOk}`
        );
      } else {
        setBusyText(quantOlsSummary, (data && data.error) || "分组失败", {
          busy: false,
        });
        setQuantMeta((data && data.error) || "分组失败", { error: true });
      }
    } finally {
      clearInterval(timer);
    }
  }

  async function runFactorCsIcSuggest() {
    setQuantMeta("截面 IC 计算中…", { busy: true });
    setBusyText(quantOlsSummary, "截面 IC 中…", { busy: true });
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
      setBusyText(quantOlsSummary, detail, { busy: false });
      setQuantMeta(`截面 IC 失败 · ${detail}`, { error: true });
      return;
    }
    if (!data || !(data.success || data.ok)) {
      setBusyText(quantOlsSummary, (data && data.error) || "截面 IC 失败", { busy: false });
      setQuantMeta((data && data.error) || "截面 IC 失败", { error: true });
      return;
    }
    const rows = (data.factors || []).map((f) => {
      const name = f.factor || f.name || "";
      const meta = factorMetaByName[name] || {};
      return {
        factor: name,
        name,
        label: f.label || meta.label || name,
        ic: f.ic != null ? f.ic : (f.pearson && f.pearson.ic_mean),
        sample_count: f.sample_count != null ? f.sample_count : (f.pearson && f.pearson.day_count),
        exclusion_reason: f.exclusion_reason || null,
        spearman_ic: f.spearman && f.spearman.ic_mean,
        icir: f.icir != null ? f.icir : (f.pearson && f.pearson.icir),
      };
    });
    const panel = {
      rows,
      factors: rows,
      exclusion_reasons: Object.fromEntries(
        rows.filter((r) => r.exclusion_reason).map((r) => [r.factor || r.name, r.exclusion_reason])
      ),
      mode: "factor_cross_section",
    };
    lastFactorPanelForMerge = panel;
    if (quantFactorList) {
      quantFactorList.innerHTML =
        factorIcWeightMergedHtml(panel, lastWeightSuggestForMerge, lastOlsForMerge) || "";
    }
    const scoreP = (data.score_ic && data.score_ic.pearson) || {};
    const nOk = rows.filter((r) => r.ic != null).length;
    setBusyText(
      quantOlsSummary,
      `截面 IC · ${data.stock_count ?? "—"} 只 · 日 ${data.day_count ?? "—"} · 因子有效 ${nOk}/${rows.length}` +
        (scoreP.ic_mean != null ? ` · 综合 IC ${scoreP.ic_mean}` : "") +
        (data.pit_fundamentals ? " · PIT" : ""),
      { busy: false }
    );
    setQuantMeta(
      `截面 IC · ${nOk}/${rows.length} 因子 · horizon ${data.horizon_days ?? 3}` +
        (scoreP.icir != null ? ` · score ICIR ${scoreP.icir}` : "")
    );
  }

  async function runThresholdSuggest({ useWatching = false } = {}) {
    if (!quantThresholdSummary) return;
    setBusyText(
      quantThresholdSummary,
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
    const data = await res.json();
    if (!data.success) {
      setBusyText(quantThresholdSummary, data.error || "失败", { busy: false });
      renderThresholdTable(null);
      return;
    }
    if (useWatching) {
      const agg = data.watching_aggregate || {};
      setBusyText(
        quantThresholdSummary,
        `${(data.rationale || []).slice(0, 1).join("")} · ${agg.stock_count || 0} 只 · 中位 min=${agg.median_best_min_score ?? "—"}`,
        { busy: false }
      );
    } else {
      setBusyText(
        quantThresholdSummary,
        (data.rationale || []).slice(0, 2).join(" · "),
        { busy: false }
      );
    }
    renderThresholdTable(data);
    quantLastThresholdDiff = data.config_diff || null;
  }

  function setQuantMeta(text, { busy = false, error = false } = {}) {
    if (!quantMeta) return;
    quantMeta.textContent = text;
    quantMeta.classList.toggle("is-busy", !!busy && !error);
    quantMeta.classList.toggle("is-error", !!error);
  }

  function setBusyText(el, text, { busy = true, html = false } = {}) {
    if (!el) return;
    if (text != null) {
      if (html) el.innerHTML = text;
      else el.textContent = text;
    }
    el.classList.toggle("is-busy", !!busy);
  }

  async function runAllSuggest() {
    setBusyText(quantOlsSummary, "分析中…", { busy: true });
    setBusyText(quantThresholdSummary, "分析中…", { busy: true });
    // 忙碌态只留在摘要行，避免表内再叠一句「分析中…」
    if (quantFactorList) quantFactorList.innerHTML = "";
    if (quantThresholdTable) quantThresholdTable.innerHTML = "";
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

  on("quant-factor-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runFactorIcSuggest();
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-probe-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-probe-run");
    if (btn) btn.disabled = true;
    try {
      await runProbeStockVsGroup();
    } catch (err) {
      setBusyText(quantProbeSummary, String(err.message || err), { busy: false });
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-ols-code", "change", async () => {
    // 换票后自动对照（需已有分组）
    const btn = document.getElementById("quant-probe-run");
    if (btn) btn.disabled = true;
    try {
      await runProbeStockVsGroup();
    } catch (err) {
      setBusyText(quantProbeSummary, String(err.message || err), { busy: false });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  // 兼容隐藏入口 / 旧深链
  on("quant-ols-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runProbeStockVsGroup();
    } catch (err) {
      setBusyText(quantProbeSummary, String(err.message || err), { busy: false });
    }
  });

  on("quant-ols-pool-run", "click", async (e) => {
    e.preventDefault();
    // 研究池 OLS 已移出探针；隐藏按钮若被触发则提示改用对照验证
    setBusyText(
      quantProbeSummary,
      "研究池 OLS 已从探针移除 · 请用「对照验证」看单票 vs 所在组",
      { busy: false }
    );
  });

  on("quant-cs-ic-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runProbeStockVsGroup();
    } catch (err) {
      setBusyText(quantProbeSummary, String(err.message || err), { busy: false });
    }
  });

  on("quant-ols-clusters-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-ols-clusters-run");
    if (btn) btn.disabled = true;
    try {
      await runFactorOlsClustersSuggest();
    } catch (err) {
      setBusyText(quantOlsSummary, String(err.message || err), { busy: false });
      setQuantMeta(String(err.message || err), { error: true });
      if (quantOlsClusters) {
        quantOlsClusters.hidden = false;
        quantOlsClusters.textContent = String(err.message || err);
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  function onClusterExportClick(e) {
    const btn =
      e.target && e.target.closest
        ? e.target.closest("[data-cluster-export]")
        : null;
    if (!btn || !quantLastOlsClusters) return;
    e.preventDefault();
    e.stopPropagation(); // 勿触发组 details 折叠
    const key = btn.getAttribute("data-cluster-export");
    if (key === "preferred") {
      const pref = quantLastOlsClusters.preferred_cluster;
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
    if (key === "live-audit") {
      setQuantMeta("轮换双分对照样本…", { busy: true });
      refreshClusterLiveStatus({ auditRotate: true }).then(() => {
        setQuantMeta("双分对照已换一批样本");
      });
      return;
    }
    if (key === "live-rollback") {
      runClusterLiveRollback();
      return;
    }
    const idx = Number(key);
    const cl = (quantLastOlsClusters.clusters || [])[idx];
    if (!cl) return;
    exportClusterWeightDiff(cl.config_diff, cl.label);
  }

  if (quantOlsClusters && quantOlsClusters.dataset.exportWired !== "1") {
    quantOlsClusters.dataset.exportWired = "1";
    quantOlsClusters.addEventListener("click", onClusterExportClick);
  }
  if (quantFactorList && quantFactorList.dataset.clusterExportWired !== "1") {
    quantFactorList.dataset.clusterExportWired = "1";
    quantFactorList.addEventListener("click", onClusterExportClick);
  }

  async function fillWatchingNextDayTrend() {
    if (!watchingGrid || !watchingGridReady) return;
    const codes = (watchingGrid.getData() || []).map((r) => r.code).filter(Boolean);
    if (!codes.length) return;
    codes.forEach((code) => {
      const comp = watchingGrid.getRow(code);
      if (comp) {
        comp.update({
          ndBias: "…",
          ndBiasKey: "",
          ndBiasTitle: "计算中…",
        });
      }
    });
    const res = await fetch("/api/quant/next-day-trend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        lookback_eval_days: 60,
        flat_band_pct: 0.5,
        watching_limit: 20,
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
          ? "接口未找到：请重启 Web"
          : `HTTP ${res.status}`);
      codes.forEach((code) => {
        const comp = watchingGrid.getRow(code);
        if (comp) {
          comp.update({
            ndBias: "—",
            ndBiasKey: "",
            ndBiasTitle: detail,
          });
        }
      });
      return;
    }
    if (!data || data.success === false) {
      const err = (data && data.error) || "次日预判失败";
      codes.forEach((code) => {
        const comp = watchingGrid.getRow(code);
        if (comp) {
          comp.update({
            ndBias: "—",
            ndBiasKey: "",
            ndBiasTitle: err,
          });
        }
      });
      return;
    }
    const biasLabel = { up: "偏多", down: "偏空", flat: "中性" };
    const byCode = {};
    (data.latest || []).forEach((row) => {
      const c = String((row && row.code) || "").trim();
      if (c) byCode[c] = row;
    });
    codes.forEach((code) => {
      const row =
        byCode[code] ||
        byCode[watchingCodeKey(code)] ||
        Object.values(byCode).find(
          (r) => watchingCodeKey(r.code) === watchingCodeKey(code)
        );
      const comp = watchingGrid.getRow(code);
      if (!comp) return;
      if (!row) {
        comp.update({
          ndBias: "—",
          ndBiasKey: "",
          ndBiasTitle: "无预判结果",
        });
        return;
      }
      const key = String(row.bias || "");
      const conf =
        row.conf != null ? `${(Number(row.conf) * 100).toFixed(0)}%` : "—";
      const phase = String(row.decision_phase || "");
      // 列名已是「次日」；仅昨收→今日例外标「今·」，避免与现价假对照
      const prefix = phase === "prior_close_for_today" ? "今·" : "";
      const tipBits = [
        `${row.as_of || "—"} → 预判 ${row.target_date || "次日"}`,
        `score ${row.score ?? "—"}`,
        `置信 ${conf}`,
      ];
      if (row.intraday_provisional) tipBits.push("盘中暂估决策日");
      if (phase === "prior_close_for_today") {
        tipBits.push("昨收预判今日（可与涨跌对照，非明日前瞻）");
        if (row.live_vs_pred) tipBits.push(`盘中对照 ${row.live_vs_pred}`);
      } else {
        tipBits.push("勿用今日涨跌评判明日预判");
      }
      const ev = data.eval || {};
      if (ev.hit_rate != null) {
        tipBits.push(`历史命中 ${(Number(ev.hit_rate) * 100).toFixed(1)}%`);
      }
      tipBits.push("非投资建议");
      comp.update({
        ndBias: `${prefix}${biasLabel[key] || key || "—"}`,
        ndBiasKey: key,
        ndBiasTitle: tipBits.join(" · "),
      });
    });
  }

  on("quant-horizon-save-default", "click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    if (btn) btn.disabled = true;
    try {
      const h = await saveHorizonAsDefault();
      const msg = `已存研究默认 horizon=${h}d（不影响数据中心/交易 live）`;
      if (typeof setQuantMeta === "function") setQuantMeta(msg);
      const replayMeta = document.getElementById("replay-meta");
      if (replayMeta) replayMeta.textContent = msg;
    } catch (err) {
      const detail = String(err.message || err);
      if (typeof setQuantMeta === "function") {
        setQuantMeta(`存默认失败 · ${detail}`, { error: true });
      } else {
        window.alert(detail);
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-cs-ic-run", "click", async (e) => {
    e.preventDefault();
    const btn = document.getElementById("quant-cs-ic-run");
    if (btn) btn.disabled = true;
    try {
      await runFactorCsIcSuggest();
    } catch (err) {
      setBusyText(quantOlsSummary, String(err.message || err), { busy: false });
      setQuantMeta(String(err.message || err), { error: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  on("quant-weight-export", "click", (e) => {
    e.preventDefault();
    if (!quantLastWeightDiff || !quantLastWeightDiff.success) {
      if (quantMeta) quantMeta.textContent = "建议尚未就绪，稍后再导出权重 diff";
      return;
    }
    if (quantLastWeightDiff.promote_ready === false) {
      const note = quantLastWeightDiff.apply_note || "OOS 未过或已跳过";
      if (
        !window.confirm(
          `当前建议 promote_ready=否（${note}）。仍导出 diff 仅供对照？`
        )
      ) {
        return;
      }
    }
    downloadJson(quantLastWeightDiff, "signal_config_weight_diff.json");
  });

  on("strategy-weight-suggest-run", "click", (e) => {
    e.preventDefault();
    runStrategyWeightSuggest();
  });
  on("strategy-ic-export", "click", (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!strategyLastIcExport) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    downloadJson(strategyLastIcExport, "factor_ic_export.json");
  });
  on("strategy-weight-diff-export", "click", (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!strategyLastWeightDiff) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    downloadJson(strategyLastWeightDiff, "signal_config_weight_diff.json");
  });

  on("strategy-feedback-from-ic", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-factor-status");
    if (!strategyLastWeightDiff) {
      if (status) status.textContent = "请先「分析 IC / 权重」";
      return;
    }
    if (status) status.textContent = "生成反馈建议中…";
    try {
      const { ok, data, error } = await apiFetch("/api/feedback/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backtest_metrics: {
            win_rate_pct: null,
            max_drawdown_pct: null,
            weight_suggest: strategyLastWeightDiff,
          },
          paper_metrics: {},
          monitor_alerts: [
            {
              level: "info",
              code: "ic_weight_suggest",
              message: "来自策略页 IC/权重分析的人审建议入口",
            },
          ],
        }),
      });
      if (!ok) throw new Error(error || "反馈失败");
      const reasons = (data.reasons || []).filter(Boolean);
      if (status) {
        status.textContent =
          reasons.slice(0, 2).join("；") ||
          data.note ||
          "已生成建议（未写盘）· 编辑规格稿后可人审晋升";
      }
      // 合并反馈 patch；若无 weights 则用 IC 建议权重便于人审
      try {
        const cur = JSON.parse(getJsonEditorValue() || "{}");
        const patch = data.patch && typeof data.patch === "object" ? data.patch : {};
        if (patch.rank && typeof patch.rank === "object") {
          cur.rank = { ...(cur.rank || {}), ...patch.rank };
        }
        if (patch.stance_thresholds && typeof patch.stance_thresholds === "object") {
          cur.stance_thresholds = {
            ...(cur.stance_thresholds || {}),
            ...patch.stance_thresholds,
          };
        }
        if (patch.weights && typeof patch.weights === "object") {
          cur.weights = patch.weights;
        } else if (
          strategyLastWeightDiff &&
          strategyLastWeightDiff.suggested_weights &&
          typeof strategyLastWeightDiff.suggested_weights === "object"
        ) {
          cur.weights = strategyLastWeightDiff.suggested_weights;
        }
        setJsonEditorValue(JSON.stringify(cur, null, 2));
      } catch (_) {
        /* ignore */
      }
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });

  function parseEditorConfig() {
    const raw = getJsonEditorValue();
    try {
      return { ok: true, config: JSON.parse(raw) };
    } catch (err) {
      return { ok: false, error: String(err.message || err) };
    }
  }

  async function postDraft(path, note) {
    const parsed = parseEditorConfig();
    const status = document.getElementById("strategy-draft-status");
    const diffEl = document.getElementById("strategy-draft-diff");
    if (!parsed.ok) {
      if (status) status.textContent = `JSON 无效：${parsed.error}`;
      return null;
    }
    const { ok, data, error } = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: parsed.config, note: note || "" }),
    });
    if (!ok) {
      const detail = (data && (data.detail || data.errors)) || error;
      throw new Error(
        Array.isArray(detail) ? detail.join("; ") : String(detail || "请求失败")
      );
    }
    if (diffEl && data.diff) {
      diffEl.hidden = false;
      diffEl.textContent = JSON.stringify(data.diff.changes || data.diff, null, 2);
    } else if (diffEl && data.changes) {
      diffEl.hidden = false;
      diffEl.textContent = JSON.stringify(data.changes, null, 2);
    }
    return data;
  }

  on("strategy-draft-validate", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-draft-status");
    try {
      if (status) status.textContent = "校验中…";
      const data = await postDraft("/api/signal/config/draft/validate");
      if (!data) return;
      if (status) {
        status.textContent = data.ok
          ? `校验通过 · diff ${data.diff?.change_count ?? 0} 处`
          : `校验失败：${(data.errors || []).join("; ")}`;
      }
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });

  on("strategy-draft-save", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-draft-status");
    try {
      if (status) status.textContent = "保存草稿中…";
      const data = await postDraft("/api/signal/config/draft/save", "R2 UI draft");
      if (!data) return;
      if (status) status.textContent = `草稿已保存 · ${data.path || "signal_config_draft.json"}`;
      } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });

  on("strategy-draft-promote", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-draft-status");
    if (
      !window.confirm(
        "确认将编辑器中的配置写入生产 signal_config.json？将先备份现有文件。"
      )
    ) {
      return;
    }
    try {
      if (status) status.textContent = "人审晋升中…";
      const data = await postDraft(
        "/api/signal/config/draft/promote",
        "R2 UI human promote"
      );
      if (!data) return;
      if (status) {
        status.textContent = `已晋升 · 备份 ${data.backup || "—"} · 请复跑回测验证`;
      }
      await loadSignalConfigPanel();
    } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });

  on("strategy-draft-reload", "click", async (e) => {
    e.preventDefault();
    const status = document.getElementById("strategy-draft-status");
    try {
      await loadSignalConfigPanel();
      if (status) status.textContent = "已重载生产配置";
      } catch (err) {
      if (status) status.textContent = String(err.message || err);
      }
  });

  on("quant-threshold-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runThresholdSuggest({ useWatching: false });
    } catch (err) {
      if (quantThresholdSummary) {
        quantThresholdSummary.textContent = String(err.message || err);
      }
    }
  });

  on("quant-threshold-watching", "click", async (e) => {
    e.preventDefault();
    try {
      await runThresholdSuggest({ useWatching: true });
    } catch (err) {
      if (quantThresholdSummary) {
        quantThresholdSummary.textContent = String(err.message || err);
      }
    }
  });

  on("quant-threshold-export", "click", (e) => {
    e.preventDefault();
    if (!quantLastThresholdDiff || !quantLastThresholdDiff.success) {
      if (quantThresholdSummary) {
        quantThresholdSummary.textContent = "建议尚未就绪，稍后再导出阈值 diff";
      }
      return;
    }
    downloadJson(quantLastThresholdDiff, "signal_config_threshold_diff.json");
  });

  on("quant-portfolio-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runPortfolioBacktest();
    } catch (err) {
      quantPortfolioSummary.textContent = String(err.message || err);
      paintPortfolioChart([], "回测失败");
    }
  });

  on("quant-portfolio-compare", "click", async (e) => {
    e.preventDefault();
    try {
      await runPortfolioNeutralCompare();
    } catch (err) {
      quantPortfolioSummary.textContent = String(err.message || err);
      paintPortfolioChart([], "对照失败");
    }
  });

  function drawParamHeatmap(cells, axes) {
    const canvas = document.getElementById("param-grid-heat");
    if (!canvas) return;
    const g = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    g.clearRect(0, 0, w, h);
    const lookbacks = (axes && axes.lookback) || [];
    const topKs = (axes && axes.top_k) || [];
    if (!lookbacks.length || !topKs.length) {
      g.fillStyle = "#9ca3af";
      g.font = "12px Manrope, sans-serif";
      g.fillText("暂无网格", 16, h / 2);
      return;
    }
    const vals = (cells || [])
      .filter((c) => c.success && c.total_return_pct != null)
      .map((c) => Number(c.total_return_pct));
    const minV = vals.length ? Math.min(...vals) : 0;
    const maxV = vals.length ? Math.max(...vals) : 1;
    const padL = 48;
    const padB = 28;
    const padT = 12;
    const padR = 12;
    const cellW = (w - padL - padR) / topKs.length;
    const cellH = (h - padT - padB) / lookbacks.length;
    const byKey = {};
    (cells || []).forEach((c) => {
      byKey[`${c.lookback}|${c.top_k}`] = c;
    });
    lookbacks.forEach((lb, ri) => {
      topKs.forEach((tk, ci) => {
        const c = byKey[`${lb}|${tk}`];
        const x = padL + ci * cellW;
        const y = padT + ri * cellH;
        let fill = "#e5e7eb";
        if (c && c.success && c.total_return_pct != null) {
          const t = maxV === minV ? 0.5 : (Number(c.total_return_pct) - minV) / (maxV - minV);
          const r = Math.round(255 * t);
          const b = Math.round(255 * (1 - t));
          fill = `rgb(${r},80,${b})`;
        }
        g.fillStyle = fill;
        g.fillRect(x + 2, y + 2, cellW - 4, cellH - 4);
        g.fillStyle = "#111827";
        g.font = "11px IBM Plex Mono, monospace";
        g.textAlign = "center";
        g.textBaseline = "middle";
        const label =
          c && c.success && c.total_return_pct != null
            ? `${Number(c.total_return_pct).toFixed(1)}%`
            : c && c.error
              ? "×"
              : "—";
        g.fillText(label, x + cellW / 2, y + cellH / 2);
      });
    });
    g.fillStyle = "#6b7280";
    g.font = "10px Manrope, sans-serif";
    g.textAlign = "right";
    lookbacks.forEach((lb, ri) => {
      g.fillText(String(lb), padL - 6, padT + ri * cellH + cellH / 2);
    });
    g.textAlign = "center";
    topKs.forEach((tk, ci) => {
      g.fillText(`K=${tk}`, padL + ci * cellW + cellW / 2, h - 10);
    });
  }

  function paramGridDullness(data) {
    const best = data && data.best;
    const cells = (data && data.cells) || [];
    if (!best || best.total_return_pct == null) return null;
    const neighbors = cells.filter((c) => {
      if (!c.success || c.total_return_pct == null) return false;
      const dLb = Math.abs(Number(c.lookback) - Number(best.lookback));
      const dK = Math.abs(Number(c.top_k) - Number(best.top_k));
      if (dLb === 0 && dK === 0) return false;
      return dLb + dK === 1; // 正交邻格
    });
    if (!neighbors.length) return null;
    const bestRet = Number(best.total_return_pct);
    let maxGap = 0;
    neighbors.forEach((c) => {
      const g = Math.abs(bestRet - Number(c.total_return_pct));
      if (g > maxGap) maxGap = g;
    });
    return {
      max_gap_pp: Math.round(maxGap * 100) / 100,
      sharp: maxGap >= 8,
      neighbor_count: neighbors.length,
    };
  }

  function renderParamGridResult(data) {
    const meta = document.getElementById("param-grid-meta");
    const table = document.getElementById("param-grid-table");
    if (!data || !data.success) {
      if (meta) meta.textContent = (data && data.error) || "网格失败";
      return;
    }
    lastParamGrid = data;
    const best = data.best;
    const dull = paramGridDullness(data);
    if (dull) data.dullness = dull;
    if (meta) {
      let t = best
        ? `最优(样本内) lookback=${best.lookback} · top_k=${best.top_k} · 收益 ${best.total_return_pct}% · ${data.cell_count} 格 · 网格跳过 WF/OOS`
        : `完成 ${data.cell_count} 格 · 无成功单元 · 网格为样本内扫描`;
      if (data.multiple_testing_note) {
        t += ` · ${data.multiple_testing_note}`;
      } else if (data.trial_count != null) {
        t += ` · trial_count=${data.trial_count}`;
      }
      if (dull && dull.sharp) {
        t += ` · ⚠邻格Δ最大 ${dull.max_gap_pp}pp（参数过尖，应用前请二次确认）`;
      } else if (dull) {
        t += ` · 邻格Δ ${dull.max_gap_pp}pp`;
      }
      meta.textContent = t;
    }
    drawParamHeatmap(data.cells || [], data.axes || {});
    refreshParamGridApplyGate();
    if (!table) return;
    table.innerHTML = researchGridHtml(
      [
        { id: "lookback", label: "lookback", widthPct: 18, num: true },
        { id: "top_k", label: "top_k", widthPct: 14, num: true },
        { id: "ret", label: "收益", widthPct: 22, num: true },
        { id: "dd", label: "回撤", widthPct: 22, num: true },
        { id: "n", label: "笔数", widthPct: 14, num: true },
      ],
      (data.cells || []).map((c) => {
        const ret = c.total_return_pct;
        const isBest =
          best && c.lookback === best.lookback && c.top_k === best.top_k;
        return {
          lookback: String(c.lookback),
          top_k: String(c.top_k),
          retText: c.success ? fmtPct(ret) : String(c.error || "失败"),
          retCls: c.success ? metricClass(ret) : "down",
          dd: fmtPct(c.max_drawdown_pct),
          n: String(c.trade_count ?? "—"),
          isBest,
        };
      }),
      (col, d) => {
        if (col.id === "ret") return metricCell(escapeHtml(d.retText), d.retCls);
        if (col.id === "dd") return escapeHtml(d.dd);
        return escapeHtml(d[col.id] ?? "—");
      },
      {
        emptyText: "无网格结果",
        rowClass: (d) => (d.isBest ? "is-best" : ""),
      }
    );
  }

  async function runParamGrid() {
    const prog = document.getElementById("param-grid-progress");
    const progText = document.getElementById("param-grid-progress-text");
    const btn = document.getElementById("quant-param-grid-run");
    if (prog) prog.hidden = false;
    if (progText) progText.textContent = "参数扫描中…";
    if (btn) btn.disabled = true;
    try {
      const { ok, data, error } = await apiFetch("/api/quant/param-grid", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          top_k_values: [2, 3, 5],
          lookback_values: [60, 90, 120],
          apply_costs: true,
          max_cells: 9,
        }),
      });
      if (!ok) throw new Error(error || data.detail || "网格失败");
      renderParamGridResult(data);
    } finally {
      if (prog) prog.hidden = true;
      if (btn) btn.disabled = false;
    }
  }

  on("quant-param-grid-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runParamGrid();
    } catch (err) {
      const meta = document.getElementById("param-grid-meta");
      if (meta) meta.textContent = String(err.message || err);
      }
  });

  on("quant-param-grid-apply-best", "click", (e) => {
    e.preventDefault();
    const best = lastParamGrid && lastParamGrid.best;
    const meta = document.getElementById("param-grid-meta");
    const gate = paramGridApplyGate();
    if (!best || !gate.ok) {
      if (meta) meta.textContent = gate.reason || "无最优单元可应用";
      refreshParamGridApplyGate();
      return;
    }
    const dull = lastParamGrid.dullness || paramGridDullness(lastParamGrid);
    if (dull && dull.sharp) {
      const ok = window.confirm(
        `最优格相对邻格收益差达 ${dull.max_gap_pp}pp，可能过拟合尖峰。仍应用 lookback=${best.lookback} / top_k=${best.top_k}？`
      );
      if (!ok) {
        if (meta) meta.textContent = "已取消应用最优（邻格过尖）";
        return;
      }
    }
    if (gate.warn) {
      const okAlign = window.confirm(
        `${gate.reason}\n\n仍将最优 lookback=${best.lookback} / top_k=${best.top_k} 写入回测表单？`
      );
      if (!okAlign) {
        if (meta) meta.textContent = "已取消应用最优（IC对齐警示）";
        return;
      }
    }
    const lb = document.getElementById("quant-lookback");
    const tk = document.getElementById("quant-top-k");
    if (lb) lb.value = String(best.lookback);
    if (tk) tk.value = String(best.top_k);
    if (meta) {
      meta.textContent =
        `已确认 lookback=${best.lookback} · top_k=${best.top_k}（OOS 已核对）；仍勿静默 promote` +
        (dull && dull.sharp ? ` · 邻格Δ ${dull.max_gap_pp}pp` : "");
    }
  });

  function buildResearchCurves(result) {
    if (!result || !result.success) return null;
    const sic = result.score_ic || {};
    const qb = result.quantile_backtest || {};
    const bench = result.benchmark || {};
    const align = result.ic_equity_align || {};
    return {
      equity_curve: result.equity_curve || [],
      benchmark_equity_curve: bench.equity_curve || [],
      ic_series_tail: sic.ic_series_tail || [],
      ic_rolling_tail: sic.ic_rolling_tail || [],
      quantile_curves: (qb.quantiles || []).map((r) => ({
        label: r.label,
        quantile: r.quantile,
        equity_curve_tail: r.equity_curve_tail || [],
      })),
      long_short_equity_curve: qb.long_short_equity_curve || [],
      ic_equity_align: align.ok
        ? {
            avg_return_spread_pp: align.avg_return_spread_pp,
            aligned_favor_pos_ic: align.aligned_favor_pos_ic,
            pos_ic: align.pos_ic,
            neg_ic: align.neg_ic,
            periods_tail: align.periods_tail || [],
          }
        : align,
    };
  }

  function buildResearchPromoteMeta(result) {
    const cached = loadCachedPromoteHints();
    const liveHints = (result && result.promote_hints) || [];
    const ttlH = readPromoteTtlHours();
    return {
      hard_gate: readPromoteHardGate(),
      expire_hard_block: readPromoteExpireHard(),
      hints_ttl_hours: ttlH,
      hints_ttl_ms: ttlH * 3600000,
      live_promote_hints: liveHints,
      cached_promote_hints:
        cached && !cached.expired
          ? {
              at: cached.at,
              ttl_ms: cached.ttl_ms || ttlH * 3600000,
              lookback: cached.lookback,
              top_k: cached.top_k,
              hints: cached.hints || [],
              hard_gate_at_cache: cached.hard_gate_at_cache,
            }
          : cached && cached.expired
            ? { expired: true, at: cached.at, ttl_ms: cached.ttl_ms }
            : null,
      note: "hard_gate=回测应用最优；expire_hard_block=策略过期硬拦；TTL 可在回测页配置。",
    };
  }

  on("quant-research-export", "click", (e) => {
    e.preventDefault();
    const result = lastBacktestPack && lastBacktestPack.result;
    const pack = {
      exported_at: new Date().toISOString(),
      backtest: lastBacktestPack,
      param_grid: lastParamGrid,
      curves: buildResearchCurves(result),
      promote_meta: buildResearchPromoteMeta(result),
      note: "研究包 · 含 IC/分层/基准曲线与 promote_meta（硬闸/提示）；不含实盘指令",
    };
    if (!pack.backtest && !pack.param_grid) {
      if (quantPortfolioSummary) quantPortfolioSummary.textContent = "请先跑回测或参数网格";
      return;
    }
    downloadJson(pack, `research_pack_${Date.now()}.json`);
    if (quantPortfolioSummary && pack.curves) {
      quantPortfolioSummary.textContent =
        (quantPortfolioSummary.textContent || "") + " · 已导出研究包(含曲线/promote_meta)";
    }
  });

  on("quant-backtest-report-export", "click", async (e) => {
    e.preventDefault();
    const result = lastBacktestPack && lastBacktestPack.result;
    if (!result || !result.success) {
      if (quantPortfolioSummary) quantPortfolioSummary.textContent = "请先跑 Top-K 回测";
      return;
    }
    try {
      const { ok, data, error } = await apiFetch("/api/quant/export/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ result, format: "markdown" }),
      });
      if (!ok) throw new Error(error || "导出失败");
      const blob = new Blob([data.content || ""], {
        type: "text/markdown;charset=utf-8",
      });
      downloadBlob(blob, data.filename || `portfolio_backtest_${Date.now()}.md`);
      if (quantPortfolioSummary) {
        quantPortfolioSummary.textContent =
          (quantPortfolioSummary.textContent || "") + " · 已导出回测报告 MD";
      }
    } catch (err) {
      if (quantPortfolioSummary) {
        quantPortfolioSummary.textContent = String(err.message || err);
      }
    }
  });

  document.getElementById("strategy-factor-dict-list")?.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-factor]");
    if (!btn) return;
    e.preventDefault();
    const name = btn.getAttribute("data-factor") || "";
    const tip = btn.getAttribute("title") || name;
    const q = `请解释因子 ${name}：${tip}`;
    if (typeof window.__investmentOpenAi === "function") {
      window.__investmentOpenAi(q);
      setTimeout(() => {
        document.getElementById("ai-drawer-form")?.requestSubmit();
      }, 40);
    }
  });

  on("quant-export-preview-md", "click", async (e) => {
    e.preventDefault();
    try {
      await previewQuantExport("markdown");
    } catch (err) {
      if (quantExportPreviewMeta) quantExportPreviewMeta.textContent = String(err.message || err);
    }
  });

  on("quant-export-preview-html", "click", async (e) => {
    e.preventDefault();
    try {
      await previewQuantExport("html");
    } catch (err) {
      if (quantExportPreviewMeta) quantExportPreviewMeta.textContent = String(err.message || err);
    }
  });

  // 日报默认折叠：展开或深链时再拉 MD 预览
  document.getElementById("quant-daily-fold")?.addEventListener("toggle", (e) => {
    const fold = e.currentTarget;
    if (fold && fold.open) ensureDailyPreview();
  });
  if (String(location.hash || "").replace(/^#/, "") === "quant-daily-fold") {
    openDailyFold();
    ensureDailyPreview();
  }

  on("quant-export-md", "click", async (e) => {
    e.preventDefault();
    openDailyFold();
    try {
      const res = await fetch("/api/quant/export?format=markdown&use_saved=true");
      const data = await res.json();
      if (!res.ok) {
        if (quantMeta) quantMeta.textContent = data.detail || data.error || "导出失败";
        return;
      }
      const blob = new Blob([data.content || ""], { type: "text/markdown;charset=utf-8" });
      downloadBlob(blob, data.filename || "quant_daily.md");
      if (quantMeta) quantMeta.textContent = "Markdown 已下载";
      } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
      }
  });

  on("quant-export-html", "click", async (e) => {
    e.preventDefault();
    openDailyFold();
    try {
      const res = await fetch("/api/quant/export?format=html&use_saved=true");
      const data = await res.json();
      if (!res.ok) {
        if (quantMeta) quantMeta.textContent = data.detail || data.error || "导出失败";
        return;
      }
      const blob = new Blob([data.content || ""], { type: "text/html;charset=utf-8" });
      downloadBlob(blob, data.filename || "quant_daily.html");
      if (quantMeta) quantMeta.textContent = "HTML 已下载";
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
    }
  });

  function showQuantInterpretPanel() {
    if (quantInterpretBody) {
      quantInterpretBody.hidden = false;
      try {
        quantInterpretBody.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
    if (quantInterpretNeutral) quantInterpretNeutral.hidden = false;
  }

  function setQuantInterpretContent(text, { markdown = false, prefix = "" } = {}) {
    if (!quantInterpretBody) return;
    const raw = `${prefix || ""}${text || ""}`.trim() || "—";
    if (!markdown) {
      quantInterpretBody.textContent = raw;
      quantInterpretBody.classList.remove("is-md");
      return;
    }
    quantInterpretBody.classList.add("is-md");
    if (typeof marked !== "undefined" && marked.parse) {
      quantInterpretBody.innerHTML = marked.parse(raw, { gfm: true, breaks: true });
    } else {
      quantInterpretBody.innerHTML = escapeHtml(raw).replace(/\n/g, "<br/>");
    }
  }

  async function resolveLlmAvailable() {
    if (typeof ctx.llmAvailable === "boolean") return ctx.llmAvailable;
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      ctx.llmAvailable = !!data.llm_available;
      return ctx.llmAvailable;
    } catch (_) {
      ctx.llmAvailable = false;
      return false;
    }
  }

  async function runQuantInterpret({ forceOffline = false } = {}) {
    if (!quantInterpretBody) {
      if (quantMeta) quantMeta.textContent = "本页无解读区";
      return;
    }
    openDailyFold();
    const llmOk = forceOffline ? false : await resolveLlmAvailable();
    const useOffline = forceOffline || !llmOk;
    showQuantInterpretPanel();
    setQuantInterpretContent(useOffline ? "规则解读中…" : "AI 解读中…");
    if (quantMeta) {
      quantMeta.textContent = useOffline ? "规则解读中…" : "AI 解读中…";
    }
    if (quantInterpretNeutral) quantInterpretNeutral.innerHTML = "";
    const res = await fetch("/api/quant/interpret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_saved: true, offline: useOffline }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || data.error || "解读失败";
      setQuantInterpretContent(msg);
      if (quantMeta) quantMeta.textContent = msg;
      return data;
    }
    const prefix = data.source === "rule_based" ? "**规则解读**\n\n" : "";
    setQuantInterpretContent(data.interpretation || "—", { markdown: true, prefix });
    if (quantMeta) {
      quantMeta.textContent =
        data.source === "rule_based" ? "规则解读完成" : "AI 解读完成";
    }
    if (data.neutral_compare_summary && data.neutral_compare_summary.success) {
      renderNeutralCompareTable(data.neutral_compare_summary, quantInterpretNeutral);
    } else if (quantInterpretNeutral) {
      quantInterpretNeutral.innerHTML = data.neutral_compare_brief
        ? `<p class="sub">${data.neutral_compare_brief}</p>`
        : "";
    }
    return data;
  }

  on("quant-interpret", "click", async (e) => {
    e.preventDefault();
    const q =
      "请解读上次量化日报（quant_daily / use_saved）：概括因子 IC、权重建议、组合表现与主要风险；" +
      "不要改写 score / stance_label；结论须可核对数据。";
    if (typeof window.__investmentOpenAi === "function") {
      if (quantMeta) quantMeta.textContent = "已打开 AI 助手…";
      window.__investmentOpenAi(q);
      setTimeout(() => {
        document.getElementById("ai-drawer-form")?.requestSubmit();
      }, 40);
      return;
    }
    /* 抽屉不可用时回退本页解读 */
    try {
      await runQuantInterpret({ forceOffline: false });
    } catch (err) {
      showQuantInterpretPanel();
      if (quantInterpretBody) setQuantInterpretContent(String(err.message || err));
      if (quantMeta) quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-interpret-offline", "click", async (e) => {
    e.preventDefault();
    try {
      await runQuantInterpret({ forceOffline: true });
    } catch (err) {
      showQuantInterpretPanel();
      if (quantInterpretBody) setQuantInterpretContent(String(err.message || err));
      if (quantMeta) quantMeta.textContent = String(err.message || err);
    }
  });

  on("quant-feedback-suggest", "click", async (e) => {
    e.preventDefault();
    try {
      if (quantMeta) quantMeta.textContent = "生成配置反馈中…";
      showQuantInterpretPanel();
      if (quantInterpretBody) quantInterpretBody.textContent = "生成配置反馈中…";
      const m = ctx.lastBacktestMetrics || {};
      const { ok, data, error } = await apiFetch("/api/feedback/suggest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backtest_metrics: {
            win_rate_pct: m.win_rate_pct ?? null,
            max_drawdown_pct: m.max_drawdown_pct ?? null,
            ...(quantLastWeightDiff ? { weight_suggest: quantLastWeightDiff } : {}),
          },
          paper_metrics: {},
          monitor_alerts: [
            {
              level: "info",
              code: "quant_hub_feedback",
              message: "来自研究枢纽的配置反馈入口",
            },
          ],
        }),
      });
      if (!ok) throw new Error(error || "反馈失败");
      const reasons = (data.reasons || []).filter(Boolean);
      const note =
        reasons.slice(0, 3).join("；") ||
        data.note ||
        "已生成建议（未写盘）· 可到策略中心人审晋升";
      if (quantMeta) quantMeta.textContent = note;
      if (quantInterpretBody) {
        const patch = data.patch && typeof data.patch === "object" ? data.patch : {};
        const lines = [
          "【配置反馈】未写盘，须人审后合并。",
          note,
          Object.keys(patch).length
            ? `patch keys: ${Object.keys(patch).join(", ")}`
            : "",
        ].filter(Boolean);
        quantInterpretBody.textContent = lines.join("\n");
      }
      if (typeof ctx.showResultsTab === "function" && document.body.dataset.page === "chat") {
        await ctx.showResultsTab("platform", { openMobile: true, load: true });
      }
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
      showQuantInterpretPanel();
      if (quantInterpretBody) quantInterpretBody.textContent = String(err.message || err);
    }
  });

  on("quant-diff-preview", "click", async (e) => {
    e.preventDefault();
    await loadConfigDiffPreview();
  });

  on("quant-diff-export", "click", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/signal/config/diff-export?use_saved=true");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      downloadJson(data, data.filename || "signal_config_diff_bundle.json");
      if (quantDiffSummary) {
        quantDiffSummary.textContent = "diff 包已下载 · 请手动合并 merged_patch";
      }
    } catch (err) {
      if (quantDiffSummary) quantDiffSummary.textContent = String(err.message || err);
      }
  });

  on("quant-daily", "click", async (e) => {
    e.preventDefault();
    try {
      await runDailyWithPreset("quant", "每日量化任务运行中…");
    } catch (err) {
      if (quantMeta) quantMeta.textContent = String(err.message || err);
      }
  });

  on("quant-ops-run-daily", "click", async (e) => {
    e.preventDefault();
    try {
      await runDailyWithPreset("quant", "生成日报中…");
    } catch (err) {
      setQuantMeta(String(err.message || err), { error: true });
    }
  });

  on("quant-ops-refresh", "click", async (e) => {
    e.preventDefault();
    await loadOpsPanel();
  });

  attachReadmeLinkHandler(quantOpsPackage, ctx);

  ctx.openQuantDialog = openQuantDialog;
  ctx.openReadmeViewer = openReadmeViewer;
  ctx.reloadWatching = loadWatchingPanel;

  async function loadWatchingDataQuality() {
    const meta = document.getElementById("watching-dq-meta");
    const table = document.getElementById("watching-dq-table");
    if (!table) return;
    if (meta) meta.textContent = "加载中…";
    try {
      const watching = await apiFetch("/api/watching");
      if (!watching.ok) throw new Error(watching.error || "观察接口失败");
      const wl = watching.data.watchlist || [];
      const paper = await apiFetch("/api/paper");
      const dq =
        (paper.ok && paper.data && paper.data.ops_report && paper.data.ops_report.data_quality) ||
        {};
      const health = await apiFetch("/api/daily/health");
      const warnings =
        (health.ok && (health.data.warnings || [])) || [];
      if (meta) {
        meta.textContent =
          `观察 ${wl.length} 只` +
          (paper.ok ? "" : " · 纸面缓存不可用") +
          (warnings.length ? ` · 告警 ${warnings.length}` : "");
      }
      // 不打开折叠也要能读到一句结论（折叠 summary 可见）
      const foldSummary = document.querySelector(
        "#watching-data-quality-fold > summary"
      );
      if (foldSummary) {
        const fallback = dq && dq.fallback_count != null ? Number(dq.fallback_count) : null;
        const tail =
          warnings.length ? `告警 ${warnings.length} 条` : fallback && fallback > 0 ? `fallback ${fallback}` : "良好";
        foldSummary.textContent = `数据质量 · ${tail}`;
      }
      const warnRows = warnings
        .slice(0, 8)
        .map((w) => `<tr><td colspan="2">${escapeHtml(String(w))}</td></tr>`)
        .join("");
      table.innerHTML =
        `<table class="quant-weight-table"><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>` +
        `<tr><td>样本数</td><td class="num">${escapeHtml(String(dq.count ?? "—"))}</td></tr>` +
        `<tr><td>fallback</td><td class="num">${escapeHtml(String(dq.fallback_count ?? "—"))}</td></tr>` +
        `<tr><td>gated</td><td class="num">${escapeHtml(String(dq.gated_count ?? "—"))}</td></tr>` +
        `<tr><td>复权策略</td><td>${escapeHtml(String(dq.adjust_policy ?? "—"))}</td></tr>` +
        (warnRows
          ? `<tr><td colspan="2"><strong>健康警告</strong></td></tr>${warnRows}`
          : "") +
        `</tbody></table>` +
        `<p class="quant-attr-note">质量摘要来自最近纸面调仓五问；专用逐票质量 API 后续可接。</p>`;
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      const foldSummary = document.querySelector(
        "#watching-data-quality-fold > summary"
      );
      if (foldSummary) foldSummary.textContent = "数据质量 · 加载失败";
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
        const overN = (exposure.over_limit_sectors || []).length;
        meta.textContent =
          `策略 ${ops.strategy_id || data.strategy_id || "—"} · 成本 ${ops.cost_model || data.cost_model || "—"}` +
          (lim.max_sector_pct != null ? ` · 行业≤${lim.max_sector_pct}%` : "") +
          (overN ? ` · 超限行业 ${overN}` : "");
      }
      const foldSummary = document.querySelector(
        "#strategy-risk-audit-fold > summary"
      );
      if (foldSummary) {
        const bN = Array.isArray(blocks) ? blocks.length : Number(rbSum.block_count || 0);
        const over = (exposure.over_limit_sectors || []).length;
        foldSummary.textContent = over
          ? `风控与敞口 · 行业超限 ${over}` + (bN ? ` · 拦截 ${bN}` : "")
          : bN
            ? `风控与敞口 · 拦截 ${bN}`
            : "风控与敞口 · 无超限";
      }

      const sectors = exposure.sectors || [];
      const volNote =
        budget.scale != null
          ? ` · vol_scale ${escapeHtml(String(budget.scale))}`
          : budget.market_vol_scale != null
            ? ` · vol_scale ${escapeHtml(String(budget.market_vol_scale))}`
            : "";
      const sectorCols = [
        { id: "name", label: "行业", flex: true },
        { id: "weight_pct", label: "权重", widthPct: 22, num: true },
        { id: "count", label: "只数", widthPct: 16, num: true },
        { id: "status", label: "状态", widthPct: 18, center: true },
      ];
      expEl.innerHTML =
        `<p class="quant-trades-caption">行业暴露` +
        volNote +
        (lim.max_sector_pct != null
          ? ` · 上限 ${escapeHtml(String(lim.max_sector_pct))}%`
          : "") +
        `</p>` +
        researchGridHtml(
          sectorCols,
          sectors,
          (col, r) => {
            if (col.id === "name") return escapeHtml(r.name || "—");
            if (col.id === "weight_pct")
              return `${escapeHtml(String(r.weight_pct ?? "—"))}%`;
            if (col.id === "count") return escapeHtml(String(r.count ?? "—"));
            if (col.id === "status") {
              return r.over_limit ? `<span class="down">超限</span>` : "—";
            }
            return "—";
          },
          {
            emptyText: "暂无行业敞口（空仓或未计价）",
            rowClass: (r) => (r.over_limit ? "is-over-limit" : ""),
          }
        );

      const styles = exposure.styles || [];
      const sizeBuckets = exposure.size_buckets || [];
      const triCols = [
        { id: "name", label: "名称", flex: true },
        { id: "weight_pct", label: "权重", widthPct: 28, num: true },
        { id: "count", label: "只数", widthPct: 22, num: true },
      ];
      const triCell = (col, r) => {
        if (col.id === "name")
          return escapeHtml(r.name || r.origin_label || r.origin || r.label || "—");
        if (col.id === "weight_pct")
          return `${escapeHtml(String(r.weight_pct ?? r.pct ?? "—"))}%`;
        if (col.id === "count") return escapeHtml(String(r.count ?? "—"));
        return "—";
      };
      if (styleEl) {
        styleEl.innerHTML =
          `<p class="quant-trades-caption">板块风格</p>` +
          researchGridHtml(
            triCols.map((c) => (c.id === "name" ? { ...c, label: "风格" } : c)),
            styles,
            triCell,
            { emptyText: "暂无板块风格" }
          ) +
          (sizeBuckets.length
            ? `<p class="quant-trades-caption">仓位档</p>` +
              researchGridHtml(
                triCols.map((c) => (c.id === "name" ? { ...c, label: "档位" } : c)),
                sizeBuckets,
                triCell
              )
            : "") +
          (origin.length
            ? `<p class="quant-trades-caption">来源暴露</p>` +
              researchGridHtml(
                triCols.map((c) => (c.id === "name" ? { ...c, label: "来源" } : c)),
                origin,
                triCell
              )
            : "");
      }

      if (effEl) {
        const byReason = rbSum.by_reason || (ops.north_star && ops.north_star.risk_by_reason) || {};
        const reasonRows = Object.keys(byReason)
          .sort((a, b) => Number(byReason[b]) - Number(byReason[a]))
          .map((k) => ({ code: k, count: byReason[k] }));
        const dayRows = (rbSum.by_day || []).slice(0, 7);
        const eff =
          rbSum.effectiveness_rate != null
            ? `${(Number(rbSum.effectiveness_rate) * 100).toFixed(0)}%`
            : "—";
        const fp =
          rbSum.false_block_rate != null
            ? `${(Number(rbSum.false_block_rate) * 100).toFixed(0)}%`
            : "—";

        let annotateHtml = "";
        try {
          const br = await apiFetch("/api/paper/risk-blocks?limit=8");
          const blocksAnnotate = (br.ok && br.data && br.data.blocks) || [];
          if (blocksAnnotate.length) {
            annotateHtml =
              `<p class="quant-trades-caption">标注 outcome（抬有效率）</p>` +
              researchGridHtml(
                [
                  { id: "index", label: "#", widthPct: 10, num: true },
                  { id: "detail", label: "说明", flex: true },
                  { id: "outcome", label: "outcome", widthPct: 16, center: true },
                  { id: "act", label: "标注", widthPct: 28, center: true },
                ],
                blocksAnnotate,
                (col, b) => {
                  const idx = b.index;
                  if (col.id === "index") return escapeHtml(String(idx));
                  if (col.id === "detail")
                    return escapeHtml(String(b.detail || "").slice(0, 48));
                  if (col.id === "outcome")
                    return `<code>${escapeHtml(String(b.outcome || "—"))}</code>`;
                  if (col.id === "act") {
                    return (
                      `<button type="button" class="dialog-btn secondary strategy-rb-annotate" data-index="${escapeHtml(
                        String(idx)
                      )}" data-outcome="true_positive">真拦</button> ` +
                      `<button type="button" class="dialog-btn secondary strategy-rb-annotate" data-index="${escapeHtml(
                        String(idx)
                      )}" data-outcome="false_positive">误拦</button>`
                    );
                  }
                  return "—";
                }
              );
          }
        } catch (_) {
          /* ignore */
        }

        effEl.innerHTML =
          `<p class="quant-trades-caption">拦截有效率 · 共 ${escapeHtml(
            String(rbSum.block_count ?? 0)
          )} 条 · 有效 ${escapeHtml(eff)} · 误拦 ${escapeHtml(fp)}` +
          (rbSum.labeled_count != null
            ? ` · 已标注 ${escapeHtml(String(rbSum.labeled_count))}`
            : "") +
          `</p>` +
          (reasonRows.length
            ? researchGridHtml(
                [
                  { id: "code", label: "原因码", flex: true },
                  { id: "count", label: "次数", widthPct: 28, num: true },
                ],
                reasonRows,
                (col, r) =>
                  col.id === "code"
                    ? `<code>${escapeHtml(r.code)}</code>`
                    : escapeHtml(String(r.count))
              )
            : `<p class="watching-table-empty">暂无按码汇总</p>`) +
          (dayRows.length
            ? `<p class="quant-trades-caption">近七日</p>` +
              researchGridHtml(
                [
                  { id: "date", label: "日", flex: true },
                  { id: "count", label: "拦截", widthPct: 28, num: true },
                ],
                dayRows,
                (col, d) =>
                  col.id === "date"
                    ? escapeHtml(d.date || "—")
                    : escapeHtml(String(d.count ?? 0))
              )
            : "") +
          annotateHtml +
          (rbSum.note
            ? `<p class="quant-fingerprint">${escapeHtml(String(rbSum.note))}</p>`
            : "");
      }

      const blkData = (
        blockItems.length
          ? blockItems.map((b) => ({
              code: b.code || "—",
              reason: b.message || b.reason || "—",
              ts: b.sector || b.stock_code || "—",
            }))
          : (Array.isArray(blocks) ? blocks : []).map((b) =>
              typeof b === "string"
                ? { code: "—", reason: b, ts: "—" }
                : {
                    code: b.code || b.stock_code || "—",
                    reason: b.reason || b.message || "—",
                    ts: b.ts || b.time || "—",
                  }
            )
      ).slice(0, 20);
      blkEl.innerHTML =
        `<p class="quant-trades-caption">本次/最近拦截明细</p>` +
        researchGridHtml(
          [
            { id: "code", label: "原因码", widthPct: 22 },
            { id: "reason", label: "说明", flex: true },
            { id: "ts", label: "标的/行业", widthPct: 22 },
          ],
          blkData,
          (col, b) => {
            if (col.id === "code") return `<code>${escapeHtml(b.code)}</code>`;
            if (col.id === "reason") return escapeHtml(b.reason);
            return escapeHtml(b.ts);
          },
          { emptyText: "暂无拦截记录" }
        );

    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      const foldSummary = document.querySelector(
        "#strategy-risk-audit-fold > summary"
      );
      if (foldSummary) foldSummary.textContent = "风控与敞口 · 加载失败";
    }
  }

  document.getElementById("watching-data-quality-fold")?.addEventListener("toggle", (e) => {
    if (e.target.open) loadWatchingDataQuality();
  });
  document.getElementById("strategy-risk-audit-fold")?.addEventListener("toggle", (e) => {
    if (e.target.open) loadStrategyRiskAudit();
  });

  document.getElementById("strategy-risk-audit")?.addEventListener("click", async (e) => {
    const btn = e.target.closest && e.target.closest(".strategy-rb-annotate");
    if (!btn) return;
    e.preventDefault();
    const index = Number(btn.getAttribute("data-index"));
    const outcome = btn.getAttribute("data-outcome") || "";
    const meta = document.getElementById("strategy-risk-meta");
    try {
      const { ok, data, error } = await apiFetch("/api/paper/risk-blocks/annotate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index, outcome }),
      });
      if (!ok) throw new Error(error || "标注失败");
      if (meta) {
        meta.textContent = `已标注 #${index} → ${data.outcome || outcome}`;
      }
      await loadStrategyRiskAudit();
    } catch (err) {
      if (meta) meta.textContent = String(err.message || err);
      }
  });

  // 进页面时先把“折叠 summary 结论”拉出来；同时支持 hash 深链强制打开对应折叠
  const page = document.body.dataset.page || "";
  const hash = String(location.hash || "").replace(/^#/, "");
  // 仅数据中心注册：避免 /follow 上覆盖 paper 的当前持仓 getter
  if (page === "watching") {
    window.__investmentGetCurrentStock = () => {
      if (watchingFocusCode) {
        return {
          code: watchingFocusCode,
          name:
            watchingFocusName ||
            watchingNameByCode[watchingFocusCode] ||
            "",
        };
      }
      let picked = [];
      if (watchingGrid && watchingGridReady && typeof watchingGrid.getData === "function") {
        picked = (watchingGrid.getData() || [])
          .filter((r) => r && r.picked && r.code)
          .map((r) => ({
            code: String(r.code).trim(),
            name: String(r.name || watchingNameByCode[r.code] || "").trim(),
          }));
      } else {
        picked = getSelectedWatchingCodes().map((code) => ({
          code: String(code).trim(),
          name: String(watchingNameByCode[code] || "").trim(),
        }));
      }
      if (picked.length === 1) return picked[0];
      return null;
    };
  }
  if (hash === "watching-data-quality-fold") {
    document.getElementById("watching-data-quality-fold").open = true;
    loadWatchingDataQuality().catch(() => {});
  } else if (page === "watching") {
    loadWatchingDataQuality().catch(() => {});
  }
  if (hash === "strategy-risk-audit-fold") {
    document.getElementById("strategy-risk-audit-fold").open = true;
    loadStrategyRiskAudit().catch(() => {});
  } else if (page === "strategy") {
    loadStrategyRiskAudit().catch(() => {});
    renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
  }

  // T15/T17：IC硬闸 / TTL / 过期硬拦（默认均关或 24h）
  const hardGateEl = document.getElementById("quant-promote-hard-gate");
  if (hardGateEl) {
    try {
      hardGateEl.checked = sessionStorage.getItem(PROMOTE_HARD_GATE_KEY) === "1";
    } catch (_) {
      hardGateEl.checked = false;
    }
    hardGateEl.addEventListener("change", () => {
      persistPromoteHardGate(!!hardGateEl.checked);
      refreshParamGridApplyGate();
    });
  }
  const ttlEl = document.getElementById("quant-promote-ttl-hours");
  if (ttlEl) {
    try {
      const saved = Number(sessionStorage.getItem(PROMOTE_HINTS_TTL_HOURS_KEY));
      if (Number.isFinite(saved) && saved > 0) {
        ttlEl.value = String(Math.max(1, Math.min(168, Math.round(saved))));
      }
    } catch (_) {
      /* ignore */
    }
    ttlEl.addEventListener("change", () => {
      const h = readPromoteTtlHours();
      ttlEl.value = String(h);
      persistPromoteTtlHours(h);
      renderPromoteHintsPanel(loadCachedPromoteHints(), "quant-promote-hints");
      renderPromoteHintsPanel(loadCachedPromoteHints(), "strategy-promote-hints");
    });
  }
  const expireHardEl = document.getElementById("strategy-promote-expire-hard");
  if (expireHardEl) {
    try {
      expireHardEl.checked = sessionStorage.getItem(PROMOTE_EXPIRE_HARD_KEY) === "1";
    } catch (_) {
      expireHardEl.checked = false;
    }
    expireHardEl.addEventListener("change", () => {
      persistPromoteExpireHard(!!expireHardEl.checked);
    });
  }
  if (document.getElementById("quant-promote-hints")) {
    renderPromoteHintsPanel(loadCachedPromoteHints(), "quant-promote-hints");
  }

  ctx.applyQuantArtifact = async function applyQuantArtifact(art) {
    const data = (art && art.data) || {};
    const task = String((art.params && art.params.task) || data.task || "").toLowerCase();
    const summary = (art && art.summary) || "Agent 量化结果";

    if (data.metrics && (data.trades_sample || data.equity_curve || data.equity_curve_tail || data.loaded_stocks)) {
      renderPortfolioBacktestResult(data);
      paintPortfolioChart(
        data.equity_curve_tail || data.equity_curve,
        "无净值曲线"
      );
      if (quantPortfolioSummary) quantPortfolioSummary.textContent = summary;
      return;
    }
    if (data.t0_pnl_total != null || data.t0_trade_days != null || (data.days && task.includes("t0"))) {
      renderT0BacktestResult(data);
      if (quantT0Summary) quantT0Summary.textContent = summary;
      else if (quantMeta) quantMeta.textContent = `${summary} · 详情见纸面 Tab`;
      return;
    }
    if (task === "cross_section" || data.picks || data.cross_section) {
      renderCrossSection(data.cross_section || data);
      return;
    }
    if (task === "factor_ols" || data.coefficients || data.rows) {
      renderFactorOls(data);
      return;
    }
    if (data.portfolio_backtest_summary && data.portfolio_backtest_summary.success) {
      const ps = data.portfolio_backtest_summary;
      if (quantPortfolioSummary) {
        quantPortfolioSummary.textContent = `${summary} · 累计 ${ps.total_return_pct ?? "—"}%`;
      }
      renderMetricCards(quantBtMetrics, [
        { label: "累计收益", value: fmtPct(ps.total_return_pct), cls: metricClass(ps.total_return_pct) },
        { label: "胜率", value: fmtPct(ps.win_rate_pct) },
        { label: "交易次数", value: escapeHtml(String(ps.trade_count ?? "—")) },
        { label: "来源", value: "Agent" },
      ]);
      paintPortfolioChart(ps.equity_curve_tail, "无摘要曲线");
      return;
    }
    await openQuantDialog();
  };

  if (document.body.dataset.page === "quant") {
    const auto = new URLSearchParams(location.search).get("auto");
    openQuantDialog({
      autoBacktest: auto === "backtest",
    });
  }

  // W3：键盘链路自动化（可从全局快捷键触发）
  if (document.body.dataset.page === "replay") {
    const sp = new URLSearchParams(location.search);
    const auto = sp.get("auto_param_grid_chain");
    if (auto === "1") {
      const meta = document.getElementById("param-grid-meta");
      if (meta) meta.textContent = "键盘链路：自动跑网格中…";
      setTimeout(() => {
        runParamGrid()
          .then(() => {
            window.location.href = "/strategy#strategy-spec-editor";
          })
          .catch((err) => {
            if (meta) meta.textContent = String(err.message || err);
      });
      }, 30);
    }
  }
}
