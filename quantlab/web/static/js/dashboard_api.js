/** 仪表盘只组合已有 API（A4：禁止在此复制研究台逻辑）。 */

import { apiFetch } from "./api_client.js";

function unwrap(res, fallback = {}) {
  if (!res || res.ok === false) {
    if (res && res.data && typeof res.data === "object")
      return { ...fallback, ...res.data, ok: false };
    return { ok: false, ...fallback };
  }
  if (res.data && typeof res.data === "object") return res.data;
  return res;
}

/**
 * @param {string} url
 * @param {object} [fallback]
 */
export function fetchDash(url, fallback = {}) {
  return apiFetch(url)
    .then((res) => unwrap(res, fallback))
    .catch(() => ({ ok: false, ...fallback }));
}

/**
 * 并行拉取仪表盘标准块（只组合 API，不写研究台逻辑）。
 * @param {{ range?: string, benchmark?: string }} opts
 */
export function fetchDashboardBundle(opts = {}) {
  const range = opts.range || "30";
  const benchParam = opts.benchmark || "none";
  return Promise.all([
    fetchDash(`/api/dashboard/market-overview`),
    fetchDash(`/api/dashboard/market-context`),
    fetchDash(`/api/dashboard/kpis`),
    fetchDash(`/api/dashboard/risk-metrics`),
    fetchDash(`/api/dashboard/nav-curve?range=${range}&benchmark=${benchParam}`, {
      points: [],
      benchmark_points: [],
    }),
    fetchDash(`/api/dashboard/sector-heatmap`, { sectors: [] }),
    fetchDash(`/api/dashboard/signals`, { signals: [] }),
    fetchDash(`/api/dashboard/allocation`, { sectors: [], total_value: 0 }),
    fetchDash(`/api/dashboard/factor-exposure`),
    fetchDash(`/api/dashboard/drawdown?range=${range}`, { points: [] }),
    fetchDash(`/api/dashboard/var-historical?range=${range}`),
  ]);
}
