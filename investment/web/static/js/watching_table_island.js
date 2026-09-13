/**
 * 数据中心主表 · 纯 DOM 虚拟滚动（共享 virtual_table 内核）。
 */

function numSortKey(row, key) {
  const n = Number(row?.[key]);
  return Number.isFinite(n) ? n : -Infinity;
}

function compare(id, a, b) {
  if (id === "score") return numSortKey(a, "scoreNum") - numSortKey(b, "scoreNum");
  if (id === "score_eod") return numSortKey(a, "scoreEodNum") - numSortKey(b, "scoreEodNum");
  if (id === "score_tau") return numSortKey(a, "scoreTauNum") - numSortKey(b, "scoreTauNum");
  if (id === "score_on") return numSortKey(a, "scoreOnNum") - numSortKey(b, "scoreOnNum");
  if (id === "score_nowcast") return numSortKey(a, "scoreNowcastNum") - numSortKey(b, "scoreNowcastNum");
  if (id === "vol") return numSortKey(a, "volNum") - numSortKey(b, "volNum");
  if (id === "excess") return numSortKey(a, "excessNum") - numSortKey(b, "excessNum");
  if (id === "chg") return numSortKey(a, "chgNum") - numSortKey(b, "chgNum");
  if (id === "open") return numSortKey(a, "openNum") - numSortKey(b, "openNum");
  if (id === "name") {
    return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN");
  }
  return String(a[id] ?? "").localeCompare(String(b[id] ?? ""), "zh-CN", { numeric: true });
}

/** 数据中心主表列轨：除「股票」外固定 px，避免 fr 权重把间距拉散。 */
const COLS = [
  {
    id: "picked",
    label: "",
    width: 40,
    headClass: "watching-pick-cell",
    cellClass: "watching-pick-cell",
    title: "勾选后可加入模拟持仓",
  },
  {
    id: "name",
    label: "股票",
    flex: true,
    flexMin: "10.5rem",
    flexFr: 1,
    sortable: true,
    cellClass: "watching-stock",
    title: "股票名称与代码",
  },
  {
    id: "paper",
    label: "仓位",
    width: 60,
    headClass: "watching-col-center",
    cellClass: "watching-col-center",
    title: "是否已在模拟持仓",
  },
  {
    id: "sent",
    label: "情绪",
    width: 36,
    headClass: "watching-col-center",
    cellClass: "watching-col-center",
    title: "标题情绪摘要",
  },
  { id: "prev_close", label: "昨收", width: 78, num: true, title: "上一交易日收盘价" },
  { id: "open", label: "今开", width: 78, num: true, title: "今日开盘价" },
  { id: "price", label: "现价", width: 78, num: true, title: "最新成交价" },
  {
    id: "chg",
    label: "涨跌",
    width: 68,
    num: true,
    sortable: true,
    title: "相对昨收的涨跌幅 %",
  },
  {
    id: "score_eod",
    label: "y_oo",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: "ŷ_oo · open[T]→open[T+1]（%）",
  },
  {
    id: "score_tau",
    label: "y_oc",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: "ŷ_oc · open[T]→close[T]（拟合原值；τ 闸同源）",
  },
  {
    id: "score_on",
    label: "y_co",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: "ŷ_co · close[T]→open[T+1]（对照）",
  },
  {
    id: "score",
    label: "ranking",
    width: 94,
    num: true,
    sortable: true,
    headClass: "watching-col-y watching-col-y-trade",
    cellClass: "watching-col-y watching-col-y-trade",
    title: "ranking = w·ŷ_oo + w·(ŷ_oc∘w_co·ŷ_co) · 排序/卖门槛",
  },
  {
    id: "score_nowcast",
    label: "y_τc",
    width: 82,
    num: true,
    sortable: true,
    headClass: "watching-col-y",
    cellClass: "watching-col-y",
    title: "ŷ_τc · close[T]/price(τ)−1 · 与 remaining(ŷ_oc) 融合成 R̂_τ / ĉ",
  },
  {
    id: "stance",
    label: "倾向",
    width: 48,
    headClass: "watching-col-center",
    cellClass: "watching-col-center",
    title: "规则倾向（买入 / 观望等），不是 ŷ 本身",
  },
  { id: "excess", label: "超额", width: 62, num: true, sortable: true, title: "相对基准（指数）的超额收益" },
  { id: "vol", label: "量", width: 88, num: true, sortable: true, title: "成交量" },
  { id: "volr", label: "量比", width: 56, num: true, title: "近期成交量 / 均量" },
  { id: "pe", label: "PE", width: 56, num: true, title: "市盈率" },
  { id: "pb", label: "PB", width: 56, num: true, title: "市净率" },
];

/**
 * @param {HTMLElement} host
 * @param {{ initialSort?: Array<{column:string, dir:string}> }} [options]
 */
export async function mountWatchingTableIsland(host, options = {}) {
  // 嵌套模块必须带 ASSET_V，否则 virtual_table 会被浏览器缓存成旧版导致表头/表体错位
  const V = (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
  const { mountVirtualTable, escapeHtml, truncateName } = await import(
    `./virtual_table.js?v=${encodeURIComponent(V)}`
  );
  const { fitTierBadgeForCode, ensureFitTierMap } = await import(
    `./quant/fit_tier_ui.js?v=${encodeURIComponent(V)}`
  );
  await ensureFitTierMap();

  const api = mountVirtualTable(host, {
    columns: COLS,
    emptyText: "暂无观察",
    rowHeight: 50,
    initialSort: options.initialSort,
    compare,
    rowClass: (d) =>
      [
        "watching-watch-row",
        d.isSentimentAlert ? "is-sentiment-alert" : "",
        d.isHardReject ? "is-hard-reject" : "",
        d.yhatHistHit ? "is-yhat-hist-hit" : "",
        d.yhatHistDim ? "is-yhat-hist-dim" : "",
        d.oosFailed ? "is-oos-failed" : "",
      ]
        .filter(Boolean)
        .join(" "),
    rowAttrs: (d) => ({ "data-code": d.code || "" }),
    headHtml: (col, ctx) => {
      if (col.id !== "picked") {
        const s = ctx.sortState[0];
        const sorted = s && s.id === col.id;
        const arrow = sorted ? (s.desc ? " ↓" : " ↑") : "";
        return `${escapeHtml(col.label || "")}${arrow}`;
      }
      const rows = ctx.rows || [];
      const allPicked = rows.length > 0 && rows.every((r) => !!r.picked);
      return (
        `<input type="checkbox" id="watching-select-all" title="全选" aria-label="全选"` +
        `${allPicked ? " checked" : ""} />`
      );
    },
    cellHtml: (col, d) => {
      if (col.id === "picked") {
        return (
          `<input type="checkbox" class="watching-pick" value="${escapeHtml(d.code || "")}" ` +
          `data-code="${escapeHtml(d.code || "")}" aria-label="选择 ${escapeHtml(
            d.name || d.code || ""
          )}"${d.picked ? " checked" : ""} />`
        );
      }
      if (col.id === "name") {
        const oosBadge = d.oosFailed
          ? `<span class="watching-oos-badge" title="OOS 失败组 · 禁止新买 · 表列 ŷ 仅对照">OOS</span>`
          : "";
        const fitBadge = fitTierBadgeForCode(d.code, { escapeHtml });
        return (
          `<div class="watching-stock" title="${escapeHtml((d.name || "") + " " + (d.code || ""))}">` +
          `<span class="watching-name-row">` +
          `<span class="watching-name-text" title="${escapeHtml(d.name || "")}" data-full-name="${escapeHtml(
            d.name || ""
          )}">${escapeHtml(truncateName(d.name || d.code))}</span>` +
          fitBadge +
          oosBadge +
          `</span>` +
          `<span class="watching-code-sub">${escapeHtml(d.code || "")}` +
          `<span class="watching-mkt">${escapeHtml(d.market || "")}</span></span></div>`
        );
      }
      if (col.id === "paper") {
        return d.onPaper
          ? `<button type="button" class="watching-held-btn" data-code="${escapeHtml(
              d.code || ""
            )}" title="已在模拟持仓，点击到模拟页管理">${escapeHtml(d.paper || "已持")}</button>`
          : `<button type="button" class="watching-build-btn" data-code="${escapeHtml(
              d.code || ""
            )}" title="按金额确认后，现价假买进模拟账户">建仓</button>`;
      }
      if (col.id === "sent") return d.sentHtml || "—";
      if (col.id === "chg") {
        const text = d.chg != null && d.chg !== "" ? String(d.chg) : "—";
        const cls = d.chgCls ? ` ${escapeHtml(d.chgCls)}` : "";
        return `<span class="watching-chg${cls}">${escapeHtml(text)}</span>`;
      }
      if (col.id === "score") {
        const text = d.score != null && d.score !== "" ? String(d.score) : "—";
        const detail = d.scoreDetail || "";
        const below = !!d.scoreBelowMin;
        const singleHead = !!d.scoreSingleHead;
        const head = d.dualScoreHead || "";
        const headTitle =
          head === "single_tau"
            ? "ŷ_trade 单头降级：仅 ŷ_oc（缺 ŷ_oo）· 与双头票不同量纲"
            : head === "single_eod"
              ? "ŷ_trade 单头降级：仅 ŷ_oo（缺 ŷ_oc）· 与双头票不同量纲"
              : "ŷ_trade 单头降级 · 与双头票不同量纲";
        const badges = [];
        if (singleHead) {
          badges.push(
            `<span class="watching-single-head-badge" title="${escapeHtml(
              headTitle
            )}">单</span>`
          );
        }
        const yCheck = d.yCheck || "";
        if (yCheck && yCheck !== "ok") {
          const yMap = {
            conflict: ["歧", "Y·EOD 校验：双头分歧 · 降低今日执行信任"],
            low_conf: ["弱", "Y·EOD 校验：低置信"],
            missing_tau: ["缺τ", "Y·EOD 校验：缺 ŷ_oc"],
            single_head: ["单", "Y·EOD 校验：单头降级"],
          };
          const [t, tip] = yMap[yCheck] || ["校", `Y·EOD 校验：${yCheck}`];
          badges.push(
            `<span class="watching-y-check-badge is-${escapeHtml(
              yCheck
            )}" title="${escapeHtml(tip)}">${escapeHtml(t)}</span>`
          );
        }
        const title = d.scoreTitle || "悬停查看收益分与因子系数";
        const signCls = d.scoreCls ? ` ${escapeHtml(String(d.scoreCls))}` : "";
        if (!detail) {
          return `<span class="watching-score-cell paper-hold-score${signCls}${
            singleHead ? " score-single-head" : ""
          }">${escapeHtml(text)}${badges.join("")}</span>`;
        }
        return (
          `<span class="watching-score-cell paper-hold-score has-tip${signCls}${
            below ? " score-below-min" : ""
          }${singleHead ? " score-single-head" : ""}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="trade" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}${badges.join("")}</span>`
        );
      }
      if (col.id === "score_eod" || col.id === "score_tau" || col.id === "score_on" || col.id === "score_nowcast") {
        const tipMap = { score_eod: "eod", score_tau: "tau", score_on: "on", score_nowcast: "r" };
        const skinMap = { score_eod: "eod", score_tau: "tau", score_on: "on", score_nowcast: "nowcast" };
        const textKey =
          col.id === "score_eod"
            ? "scoreEod"
            : col.id === "score_tau"
              ? "scoreTau"
              : col.id === "score_on"
                ? "scoreOn"
                : "scoreNowcast";
        const clsKey =
          col.id === "score_eod"
            ? "scoreEodCls"
            : col.id === "score_tau"
              ? "scoreTauCls"
              : col.id === "score_on"
                ? "scoreOnCls"
                : "scoreNowcastCls";
        const titleKey =
          col.id === "score_eod"
            ? "scoreEodTitle"
            : col.id === "score_tau"
              ? "scoreTauTitle"
              : col.id === "score_on"
                ? "scoreOnTitle"
                : "scoreNowcastTitle";
        const text = d[textKey] != null && d[textKey] !== "" ? String(d[textKey]) : "—";
        const detail = d.scoreDetail || "";
        const title = d[titleKey] || "";
        const signCls = d[clsKey] ? ` ${escapeHtml(String(d[clsKey]))}` : "";
        const skin = skinMap[col.id];
        if (!detail) {
          return `<span class="watching-score-cell watching-score-${skin} paper-hold-score${signCls}">${escapeHtml(
            text
          )}</span>`;
        }
        return (
          `<span class="watching-score-cell watching-score-${skin} paper-hold-score has-tip${signCls}" ` +
          `data-score-detail="${escapeHtml(detail)}" data-score-tip="${tipMap[col.id]}" title="${escapeHtml(title)}">` +
          `${escapeHtml(text)}</span>`
        );
      }
      const v = d[col.id];
      if (v == null || v === "") return "—";
      let text = String(v);
      let tip = text;
      // 超额：单元格只留 ±x.x%，强弱进 title（兼容旧 patch 仍带「强/弱/平」）
      if (col.id === "excess") {
        const m = text.match(/^([+-]?\d+(?:\.\d+)?%)/);
        if (m) text = m[1];
        tip = d.excessTitle || String(v);
      } else if (col.id === "price" || col.id === "prev_close" || col.id === "open") {
        // 「元」占宽，窄列易被裁成「…」
        text = text.replace(/元$/u, "");
        tip = String(v);
      } else if (d[`${col.id}Title`]) {
        tip = String(d[`${col.id}Title`]);
      }
      return `<span title="${escapeHtml(tip)}">${escapeHtml(text)}</span>`;
    },
  });

  // 勾选变更：同步到行数据（事件由外层 #watching-watchlist-table 委托也会处理）
  host.addEventListener("change", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    if (t.id === "watching-select-all") {
      const checked = !!t.checked;
      (api.getData() || []).forEach((row) => {
        const r = api.getRow(row.code);
        if (r) r.update({ picked: checked });
      });
      return;
    }
    if (t.classList.contains("watching-pick")) {
      const code = String(t.dataset.code || t.value || "").trim();
      const r = code ? api.getRow(code) : null;
      if (r) r.update({ picked: !!t.checked });
    }
  });

  return api;
}
