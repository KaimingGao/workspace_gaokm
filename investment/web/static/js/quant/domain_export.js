import { escapeHtml, renderReadmeLinksHtml, attachReadmeLinkHandler, downloadBlob, downloadJson } from "../shared.js";
import { apiFetch } from "../api_client.js";
import { renderExportPreviewToc as renderExportPreviewTocHtml, renderExportMarkdownPreview as renderExportMarkdownPreviewHtml, applyExportPreviewHeadingIds as applyExportPreviewHeadingIdsHtml } from "./export_preview.js";
import { renderNeutralCompareTable as renderNeutralCompareTableHtml, buildNeutralCompareBriefHtml } from "./neutral_compare.js";
import { buildResearchCurves } from "./bt_result.js";
import { buildResearchPromoteMeta } from "./promote_cache.js";

/** Quant domain: export */
export function installExportInterpret(q) {
  const { on, els, state, ctx, escapeHtml, apiFetch, setQuantMeta, setBusyText } = q;
  const { QUANT_EXPORT_PRESETS, PRESET_FLAG_LABELS, attachReadmeLinkHandler, renderReadmeLinksHtml, buildResearchCurves, buildResearchPromoteMeta } = q;
  const { renderNeutralCompareTable } = q;


  async function ensureDailyPreview() {
    if (!els.quantExportPreviewBody || state.dailyPreviewRequested) return;
    state.dailyPreviewRequested = true;
    try {
      await previewQuantExport("markdown");
    } catch (err) {
      if (els.quantExportPreviewMeta) {
        els.quantExportPreviewMeta.textContent = String(err.message || err);
      }
    }
  }

  async function loadOpsPackageTree() {
    if (!els.quantOpsPackage || state.quantOpsPackageLoaded) return;
    els.quantOpsPackage.textContent = "加载包结构…";
    try {
      const [packageRes, readmeIndexRes] = await Promise.all([
        fetch("/api/quant/package"),
        fetch("/api/readme-index"),
      ]);
      if (!packageRes.ok) {
        els.quantOpsPackage.textContent = "包结构加载失败";
        return;
      }
      const pkg = await packageRes.json();
      const readmeIndex = readmeIndexRes.ok ? await readmeIndexRes.json() : null;
      els.quantOpsPackage.innerHTML = renderQuantPackageTree(pkg, readmeIndex);
      attachReadmeLinkHandler(els.quantOpsPackage, ctx);
      state.quantOpsPackageLoaded = true;
    } catch (err) {
      els.quantOpsPackage.textContent = String(err.message || err);
    }
  }

  async function loadOpsPanel() {
    if (!els.quantOpsSummary) return;
    setBusyText(els.quantOpsSummary, "加载中…", { busy: true });
    try {
      const healthRes = await fetch("/api/daily/health");
      const data = await healthRes.json();
      if (!healthRes.ok) throw new Error(data.detail || healthRes.statusText);

      // 研究枢纽只暴露 quant 日报；改仓类 preset 不在此页提供
      if (els.quantOpsPreset) {
        els.quantOpsPreset.innerHTML = `<option value="quant" selected>量化研究</option>`;
        els.quantOpsPreset.value = "quant";
      }
      if (els.quantOpsPresetFlags) els.quantOpsPresetFlags.textContent = "";

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
        const clRes = await fetch("/api/quant/cluster-live/status?light=1");
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
      setBusyText(els.quantOpsSummary, parts.join(" · "), { busy: false });
      syncDailyFoldSummary(foldTail);
    } catch (err) {
      setBusyText(els.quantOpsSummary, String(err.message || err), { busy: false });
      syncDailyFoldSummary("加载失败");
    }
  }

  function openDailyFold() {
    const sec = document.getElementById("quant-daily-fold");
    const fold = sec?.querySelector?.("details.quant-secondary-fold");
    if (fold) fold.open = true;
    sec?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
    });
  }

  async function openReadmeViewer(dir) {
    if (!els.readmeDialog) return;
    if (els.readmeTitle) els.readmeTitle.textContent = dir;
    if (els.readmeMeta) els.readmeMeta.textContent = "加载中…";
    if (els.readmeBody) els.readmeBody.textContent = "";
    if (els.readmeDocLinks) els.readmeDocLinks.innerHTML = "";
    els.readmeDialog.showModal();
    try {
      const res = await fetch(`/api/readme?dir=${encodeURIComponent(dir)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      if (els.readmeTitle) els.readmeTitle.textContent = data.dir;
      if (els.readmeMeta) els.readmeMeta.textContent = data.path;
      if (els.readmeDocLinks) {
        els.readmeDocLinks.innerHTML = (data.doc_links || [])
          .map(
            (link) =>
              `<a href="${link.href}" target="_blank" rel="noopener">${link.label}</a>`
          )
          .join("");
      }
      if (els.readmeBody) {
        if (window.marked) els.readmeBody.innerHTML = marked.parse(data.content || "");
        else els.readmeBody.textContent = data.content || "";
      }
    } catch (err) {
      if (els.readmeMeta) els.readmeMeta.textContent = String(err.message || err);
      if (els.readmeBody) els.readmeBody.textContent = "";
    }
  }

  async function previewQuantExport(format) {
    const fmt = format === "html" ? "html" : "markdown";
    setBusyText(els.quantExportPreviewMeta, `${fmt.toUpperCase()} 预览加载中…`, { busy: true });
    if (els.quantExportPreviewBody) els.quantExportPreviewBody.innerHTML = "";
    if (els.quantExportPreviewToc) els.quantExportPreviewToc.innerHTML = "";

    const res = await fetch(`/api/quant/export?format=${encodeURIComponent(fmt)}&use_saved=true`);
    const data = await res.json();
    if (!res.ok) {
      setBusyText(
        els.quantExportPreviewMeta,
        data.detail || data.error || "预览失败",
        { busy: false }
      );
      return data;
    }

    renderExportPreviewToc(data.export_toc);
    setBusyText(
      els.quantExportPreviewMeta,
      `${data.filename || fmt} · ${
        (data.export_toc && data.export_toc.entries && data.export_toc.entries.length) || 0
      } 个目录项`,
      { busy: false }
    );

    if (!els.quantExportPreviewBody) return data;

    if (fmt === "html") {
      const iframe = document.createElement("iframe");
      iframe.title = "量化日报 HTML 预览";
      iframe.srcdoc = data.content || "";
      els.quantExportPreviewBody.innerHTML = "";
      els.quantExportPreviewBody.appendChild(iframe);
    } else {
      renderExportMarkdownPreview(data.content || "", data.export_toc);
    }
    return data;
  }

  function renderExportMarkdownPreview(content, toc) {
    renderExportMarkdownPreviewHtml(els.quantExportPreviewBody, content, toc, {
      escapeHtml,
      marked: typeof marked !== "undefined" ? marked : null,
    });
  }

  function renderExportPreviewToc(toc) {
    renderExportPreviewTocHtml(els.quantExportPreviewToc, toc);
  }

  function renderPresetFlags(presetName) {
    if (!els.quantOpsPresetFlags) return;
    const preset = state.dailyPresetsCache.find((p) => p.name === presetName);
    if (!preset || !preset.flags) {
      els.quantOpsPresetFlags.textContent = "";
      return;
    }
    const labels = Object.entries(PRESET_FLAG_LABELS)
      .filter(([key]) => preset.flags[key])
      .map(([, label]) => label);
    const head = preset.description ? `${preset.description} · ` : "";
    els.quantOpsPresetFlags.textContent = labels.length
      ? `${head}将跑：${labels.join(" · ")}`
      : head.trim() || "无启用任务";
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

  async function runDailyWithPreset(preset, runningLabel) {
    openDailyFold();
    const busyLine = runningLabel || "生成日报中…";
    setBusyText(els.quantOpsSummary, busyLine, { busy: true });
    const res = await fetch("/api/daily/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    });
    const data = await res.json();
    if (!res.ok) {
      const detail = data.detail || res.statusText;
      setBusyText(els.quantOpsSummary, `日报失败 · ${detail}`, { busy: false });
      throw new Error(detail);
    }
    const doneLine = data.ok
      ? "日报已生成"
      : `日报部分失败 · ${(data.failures || []).join("；")}`;
    setBusyText(els.quantOpsSummary, doneLine, { busy: false });
    await q.watching.loadWatchingPanel();
    await loadOpsPanel();
    await q.strategy.loadConfigDiffPreview();
    if (els.quantCrossList) {
      await q.cluster.refreshCrossSection().catch(() => {});
    }
    if (data.ok && QUANT_EXPORT_PRESETS.has(preset)) {
      state.dailyPreviewRequested = true;
      await previewQuantExport("markdown");
    }
    return data;
  }

  async function runQuantInterpret({ forceOffline = false } = {}) {
    if (!els.quantInterpretBody) {
      if (els.quantMeta) els.quantMeta.textContent = "本页无解读区";
      return;
    }
    openDailyFold();
    const llmOk = forceOffline ? false : await resolveLlmAvailable();
    const useOffline = forceOffline || !llmOk;
    showQuantInterpretPanel();
    setQuantInterpretContent(useOffline ? "规则解读中…" : "AI 解读中…");
    if (els.quantMeta) {
      els.quantMeta.textContent = useOffline ? "规则解读中…" : "AI 解读中…";
    }
    if (els.quantInterpretNeutral) els.quantInterpretNeutral.innerHTML = "";
    const res = await fetch("/api/quant/interpret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_saved: true, offline: useOffline }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || data.error || "解读失败";
      setQuantInterpretContent(msg);
      if (els.quantMeta) els.quantMeta.textContent = msg;
      return data;
    }
    const prefix = data.source === "rule_based" ? "**规则解读**\n\n" : "";
    setQuantInterpretContent(data.interpretation || "—", { markdown: true, prefix });
    if (els.quantMeta) {
      els.quantMeta.textContent =
        data.source === "rule_based" ? "规则解读完成" : "AI 解读完成";
    }
    if (data.neutral_compare_summary && data.neutral_compare_summary.success) {
      q.backtest.renderNeutralCompareTable(data.neutral_compare_summary, els.quantInterpretNeutral);
    } else if (els.quantInterpretNeutral) {
      els.quantInterpretNeutral.innerHTML = buildNeutralCompareBriefHtml(
        data.neutral_compare_brief,
        escapeHtml
      );
    }
    return data;
  }

  function setQuantInterpretContent(text, { markdown = false, prefix = "" } = {}) {
    if (!els.quantInterpretBody) return;
    const raw = `${prefix || ""}${text || ""}`.trim() || "—";
    if (!markdown) {
      els.quantInterpretBody.textContent = raw;
      els.quantInterpretBody.classList.remove("is-md");
      return;
    }
    els.quantInterpretBody.classList.add("is-md");
    if (typeof marked !== "undefined" && marked.parse) {
      els.quantInterpretBody.innerHTML = marked.parse(raw, { gfm: true, breaks: true });
    } else {
      els.quantInterpretBody.innerHTML = escapeHtml(raw).replace(/\n/g, "<br/>");
    }
  }

  function showQuantInterpretPanel() {
    if (els.quantInterpretBody) {
      els.quantInterpretBody.hidden = false;
      try {
        els.quantInterpretBody.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (_) {
        /* ignore */
      }
    }
    if (els.quantInterpretNeutral) els.quantInterpretNeutral.hidden = false;
  }

  function syncDailyFoldSummary(tail) {
    const foldSummary = document.getElementById("quant-daily-fold-summary");
    if (!foldSummary) return;
    const t = String(tail || "").trim();
    foldSummary.textContent = t || "暂无摘要";
  }

  return {
    applyExportPreviewHeadingIds: applyExportPreviewHeadingIdsHtml,
    ensureDailyPreview,
    loadOpsPackageTree,
    loadOpsPanel,
    openDailyFold,
    openReadmeViewer,
    previewQuantExport,
    renderExportMarkdownPreview,
    renderExportPreviewToc,
    renderPresetFlags,
    renderQuantPackageTree,
    resolveLlmAvailable,
    runDailyWithPreset,
    runQuantInterpret,
    setQuantInterpretContent,
    showQuantInterpretPanel,
    syncDailyFoldSummary,
  };
}
