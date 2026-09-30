/**
 * 日报折页 UI 已下线。本域仅保留 readme 查看器（策略/文档链仍用）。
 * 导出 / 解读 / 日报生成走 HTTP/API 与脚本，不经研究枢纽按钮。
 */
export function installExportInterpret(q) {
  const { els } = q;

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

  return {
    openReadmeViewer,
    ensureDailyPreview: async () => {},
    loadOpsPanel: async () => {},
    loadOpsPackageTree: async () => {},
    openDailyFold: () => {},
    previewQuantExport: async () => ({ success: false, deprecated: true }),
    runDailyWithPreset: async () => ({ success: false, deprecated: true }),
    runQuantInterpret: async () => ({ success: false, deprecated: true }),
    setQuantInterpretContent: () => {},
    showQuantInterpretPanel: () => {},
    syncDailyFoldSummary: () => {},
    loadDailyArchive: async () => {},
    renderDailyArchive: () => {},
    deleteDailyArchive: async () => {},
    renderPresetFlags: () => {},
    renderQuantPackageTree: () => "",
    resolveLlmAvailable: async () => false,
    applyExportPreviewHeadingIds: () => {},
    renderExportMarkdownPreview: () => {},
    renderExportPreviewToc: () => {},
  };
}
