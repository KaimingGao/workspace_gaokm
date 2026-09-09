/** 模拟页北极星条（A4：从 paper.js 下沉）。 */

/**
 * @param {HTMLElement|null} host
 * @param {object|null} ns
 */
export function renderFollowNorthStar(host, ns) {
  if (!host) return;
  if (!ns || typeof ns !== "object") {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  const pr = ns.paper_risk || {};
  const rz = ns.realization || {};
  const isFull = !!ns.paper_risk;
  const sharpe = isFull ? pr.rolling_sharpe : ns.rolling_sharpe;
  const calmar = isFull ? pr.calmar : ns.calmar;
  const corr = isFull ? rz.corr : ns.corr;
  const te = isFull ? rz.tracking_error_pct : ns.tracking_error_pct;
  const bex = ns.benchmark_excess || {};
  const legs = ns.alpha_beta_legs || {};
  const excess = bex.ok ? bex.total_excess_approx_pct : legs.alpha_leg_approx_pct;
  const annIr = bex.ok ? bex.ann_ir : null;
  if (
    sharpe == null &&
    calmar == null &&
    corr == null &&
    te == null &&
    excess == null
  ) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  const fmt = (v, d) =>
    v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d);
  const items = [
    ["夏普", fmt(sharpe, 2), "滚动纸面夏普", "sharpe"],
    ["卡玛", fmt(calmar, 2), "纸面卡玛", "calmar"],
    ["拟合", fmt(corr, 3), "回测–纸面相关", "fit"],
    ["TE", te != null ? `${fmt(te, 2)}%` : "—", "跟踪误差", "te"],
    [
      "超额",
      excess != null ? `${fmt(excess, 2)}%` : "—",
      "相对指数累计超额近似（α 腿）",
      "excess",
    ],
    ["IR", fmt(annIr, 2), "年化信息比率（超额/波动）", "ir"],
  ];
  host.innerHTML = items
    .map(([label, val, sub, key]) => {
      const empty = val === "—";
      return (
        `<div class="dashboard-kpi-card quant-pro-kpi-card follow-kpi-card follow-north-star-item${empty ? " is-empty" : ""}" role="listitem" data-kpi="${key}" title="${sub}">` +
        `<div class="dashboard-kpi-label quant-pro-kpi-label follow-kpi-label follow-north-star-label">${label}</div>` +
        `<div class="dashboard-kpi-value quant-pro-kpi-value follow-kpi-value follow-north-star-val">${val}</div>` +
        `<div class="dashboard-kpi-sub quant-pro-kpi-sub follow-kpi-sub follow-north-star-sub">${sub}</div>` +
        `</div>`
      );
    })
    .join("");
}
