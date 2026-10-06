/** 模拟页北极星条（A4：从 paper.js 下沉）。 */

/**
 * @param {HTMLElement|null} host
 * @param {object|null} ns
 */
export function renderFollowNorthStar(host, ns) {
  if (!host) return;
  const data = ns && typeof ns === "object" ? ns : {};
  const pr = data.paper_risk || {};
  const rz = data.realization || {};
  const ttm = data.ttm || {};
  const isFull = !!data.paper_risk;
  const sharpe = isFull ? pr.rolling_sharpe : data.rolling_sharpe;
  const calmar = isFull ? pr.calmar : data.calmar;
  const corr = isFull ? rz.corr : data.corr;
  const te = isFull ? rz.tracking_error_pct : data.tracking_error_pct;
  const bex = data.benchmark_excess || {};
  const legs = data.alpha_beta_legs || {};
  const excess = bex.ok ? bex.total_excess_approx_pct : legs.alpha_leg_approx_pct;
  const annIr = bex.ok ? bex.ann_ir : null;
  const ttmHours = ttm.median_idea_to_paper_hours;
  // 始终画出 7 张质量卡，与调资凑满 4×2，避免第二行空一格
  host.hidden = false;
  const fmt = (v, d) =>
    v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d);
  const fmtTtm = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    if (n >= 24) return `${(n / 24).toFixed(1)}d`;
    return `${n.toFixed(1)}h`;
  };
  const items = [
    ["夏普", fmt(sharpe, 2), "滚动纸面夏普", "sharpe"],
    ["卡玛", fmt(calmar, 2), "纸面卡玛", "calmar"],
    ["TTM", fmtTtm(ttmHours), "Idea→纸面中位", "ttm"],
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
