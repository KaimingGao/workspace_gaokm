/**
 * 数据产物对照（UI 文案唯一口径）。
 *
 * 四类产物互不替代：
 *   名单 · 日 K 仓 · 5m 仓 · 分数(ŷ)
 * 「刷新」必须写清产物；现价/浮盈另属行情层，不受页头仓写开关约束。
 *
 * 写仓收敛：数据中心「增量补齐（L0）」日常 /「强更日 K（L0）」「强更 5m（L0）」兜底；
 * 交易执行页头「可拉远端」= 触发同源增量补齐，ŷ 仍只读本地。观察台没有这只开关。
 */

/** @typedef {{ id: string, name: string, write: string, read: string }} DataArtifact */

/** @type {DataArtifact[]} */
export const DATA_ARTIFACTS = [
  {
    id: "watchlist",
    name: "观察名单",
    write: "观察台 · 搜票加入 / 同步名单（不写 K 线）",
    read: "观察表 · 调仓宇宙 · 研究枢纽覆盖统计",
  },
  {
    id: "day_bars",
    name: "日 K 仓",
    write: "数据中心「增量补齐（L0）」/「强更日 K（L0）」；页头「可拉远端」同源增量补齐",
    read: "ŷ_oo · IC/OOS · 仅本地仓时的现价回退",
  },
  {
    id: "minute_bars",
    name: "5m 仓",
    write: "数据中心「增量补齐（L0）」/「强更 5m（L0）」；页头「可拉远端」同源增量补齐",
    read: "ŷ_τc 因果前缀≤10:00 分钟小包 · 做T回测/预演路径 · 涨跌 tip",
  },
  {
    id: "scores",
    name: "分数 ŷ",
    write: "数据中心「物化特征（L1/L2）」；缺覆盖时提示先物化",
    read: "观察台 insights · 持仓分 · 预演调仓 · 做T选向（会话快照）",
  },
  {
    id: "quotes",
    name: "现价行情",
    write: "行情接口 / 调度 spot（与仓写开关无关）",
    read: "涨跌% · 浮盈 · 调仓落账成交价 · 做T tick 盯盘",
  },
];

export const OFFLINE_TOGGLE_TITLE_ON =
  "关：不触发增量补齐；ŷ 读已物化特征（名单与现价/浮盈不受此开关）";
export const OFFLINE_TOGGLE_TITLE_OFF =
  "开：先对观察池跑「增量补齐（L0）」写日K/5m；ŷ 读已物化特征；现价仍走行情";

export function offlineToggleTitle(on) {
  return on ? OFFLINE_TOGGLE_TITLE_ON : OFFLINE_TOGGLE_TITLE_OFF;
}

/** 策略调仓区脚注 */
export function rebalanceDataFoot(offlineOnly = true) {
  const score = offlineOnly
    ? "ŷ 读已物化特征（页头「仅本地仓」）"
    : "ŷ 先增量补齐（L0）观察池，再读物化特征（页头「可拉远端」）";
  return (
    `${score} · 缺特征去数据中心物化 · 缺仓/仓坏用「强更日 K（L0）」/「强更 5m（L0）」` +
    ` · 落账成交价走行情 · ≠ 做T 选向`
  );
}

/** 做T区脚注 */
export function t0DataFoot() {
  return (
    "选向ŷ：回测/预演用各日会话快照（≠持仓实时分）" +
    " · 路径5m：数据中心仓 / 页头可拉远端增量补齐（L0）" +
    " · Worker/落账可拉最新5m · 现价盯盘走行情"
  );
}

/** 数据中心分钟区补充 */
export function minuteHubFoot() {
  return "下游：ŷ_τc 因果前缀≤10:00 · 预演调仓 · 做T回测 · 涨跌 tip；Ready≠消费端 as-of 齐";
}

/** 数据中心日线区补充 */
export function dayBarsHubFoot() {
  return "下游：ŷ_oo / 分组 OLS · ≠ 5m；日常增量补齐（L0） · 仓坏用强更日 K（L0）；本区不改名单";
}
