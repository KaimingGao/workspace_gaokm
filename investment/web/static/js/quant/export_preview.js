/**
 * 量化日报导出预览 HTML 渲染。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

/**
 * @param {HTMLElement|null} tocHost
 * @param {{ entries?: Array<{ anchor: string, title: string }> }|null} toc
 */
export function renderExportPreviewToc(tocHost, toc) {
  if (!tocHost) return;
  const entries = (toc && toc.entries) || [];
  if (!entries.length) {
    tocHost.innerHTML = "";
    return;
  }
  tocHost.innerHTML = `<ul>${entries
    .map(
      (e) =>
        `<li><a href="#${e.anchor}" data-export-anchor="${e.anchor}">${e.title}</a></li>`
    )
    .join("")}</ul>`;
}

/**
 * @param {HTMLElement|null} root
 * @param {{ entries?: Array<{ anchor: string, title: string }> }|null} toc
 */
export function applyExportPreviewHeadingIds(root, toc) {
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

/**
 * @param {HTMLElement|null} bodyHost
 * @param {string} content
 * @param {{ entries?: Array<{ anchor: string, title: string }> }|null} toc
 * @param {{ escapeHtml?: typeof defaultEscapeHtml, marked?: { parse?: Function } }} [opts]
 */
export function renderExportMarkdownPreview(bodyHost, content, toc, opts = {}) {
  if (!bodyHost) return;
  const esc = opts.escapeHtml || defaultEscapeHtml;
  const raw = content || "";
  let html;
  const markedLib =
    opts.marked ||
    (typeof window !== "undefined" && window.marked ? window.marked : null);
  if (markedLib && markedLib.parse) {
    html = markedLib.parse(raw, { gfm: true, breaks: true });
  } else {
    html = esc(raw).replace(/\n/g, "<br/>");
  }
  bodyHost.innerHTML = `<div class="quant-export-preview-md">${html}</div>`;
  applyExportPreviewHeadingIds(
    bodyHost.querySelector(".quant-export-preview-md"),
    toc
  );
}
