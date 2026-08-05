/**
 * 因子元数据缓存（/api/quant/factors）。
 */
import { escapeHtml } from "../shared.js";

/**
 * @param {{ fetchImpl?: typeof fetch }} [opts]
 */
export function createFactorMetaCache(opts = {}) {
  const fetchImpl = opts.fetchImpl || fetch;
  /** 稳定对象引用：外层可 factorMetaByName[name]=... 原地写入 */
  const factorMetaByName = /** @type {Record<string, any>} */ ({});
  const factorMetaByLabel = /** @type {Record<string, any>} */ ({});
  let factorMetaPromise = null;

  function rememberFactorMeta(list) {
    Object.keys(factorMetaByName).forEach((k) => {
      delete factorMetaByName[k];
    });
    Object.keys(factorMetaByLabel).forEach((k) => {
      delete factorMetaByLabel[k];
    });
    (list || []).forEach((f) => {
      if (!f || !f.name) return;
      factorMetaByName[f.name] = f;
      if (f.label) factorMetaByLabel[f.label] = f;
    });
  }

  async function ensureFactorMeta() {
    if (Object.keys(factorMetaByName).length) return factorMetaByName;
    if (factorMetaPromise) return factorMetaPromise;
    factorMetaPromise = (async () => {
      try {
        const res = await fetchImpl("/api/quant/factors");
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

  function factorNameCellHtml(name, label) {
    const display = label || name || "—";
    const tip = factorDescription(name, label);
    const text = escapeHtml(String(display));
    if (!tip) return text;
    return `<span class="factor-tip" title="${escapeHtml(tip)}">${text}</span>`;
  }

  return {
    factorMetaByName,
    factorMetaByLabel,
    rememberFactorMeta,
    ensureFactorMeta,
    factorDescription,
    factorNameCellHtml,
  };
}
