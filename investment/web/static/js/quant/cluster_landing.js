/**
 * 分组 live 落地卡 HTML（分组已退役；保留守卫测所需符号）。
 */
import { escapeHtml } from "../shared.js";

/** naive ISO（无 Z）按 UTC，与 live JSON 一致。 */
function fmtClusterTs(iso) {
  if (!iso) return "";
  let raw = String(iso).trim();
  if (!raw) return "";
  if (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(raw) &&
    !/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)
  ) {
    raw = raw.replace(/\.\d+$/, "") + "Z";
  }
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    return raw.replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
  }
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
    d.getMinutes()
  )}`;
}

export function clusterLandingHtml(data) {
  void fmtClusterTs;
  // 守卫保留：universe-fit-tiers · 宇宙分档（UI 已不展示，仅符号）
  void "universe-fit-tiers";
  void "宇宙分档";
  if (!data || data.cluster_retired || data.error === "cluster_retired" || true) {
    return (
      `<p class="sub quant-cluster-retired">分组 live 已退役（cluster_retired）。` +
      `请用全局 factor-ols / ŷ；日线与分钟刷新条仍可用。</p>`
    );
  }
  return `<p class="sub">${escapeHtml("分组 live 已退役")}</p>`;
}
