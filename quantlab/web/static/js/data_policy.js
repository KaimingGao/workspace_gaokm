/**
 * 数据产物对照（UI 文案唯一口径）。
 *
 * 四类产物互不替代：
 *   名单 · 日 K 仓 · 5m 仓 · 分数(ŷ)
 * 「刷新」必须写清产物；现价/浮盈另属行情层。
 *
 * 写仓收敛：数据中心「增量补齐（L0）」日常 /「强更日 K（L0）」「强更 5m（L0）」兜底。
 * 观察台与交易执行都不在页头拉远端；ŷ 只读已物化特征。
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
    write: "数据中心「增量补齐（L0）」/「强更日 K（L0）」",
    read: "ŷ_oo · IC/OOS · 现价回退",
  },
  {
    id: "minute_bars",
    name: "5m 仓",
    write: "数据中心「增量补齐（L0）」/「强更 5m（L0）」",
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
    write: "行情接口 / 调度 spot",
    read: "涨跌% · 浮盈 · 调仓落账成交价 · 做T tick 盯盘",
  },
];

/** 策略调仓区脚注 */
export function rebalanceDataFoot() {
  return (
    "ŷ 读已物化特征 · 缺特征去数据中心物化 · 缺仓/仓坏用「强更日 K（L0）」/「强更 5m（L0）」" +
    " · 落账成交价走行情 · ≠ 做T 选向"
  );
}

/** 做T区脚注 */
export function t0DataFoot() {
  return (
    "选向ŷ：回测/预演用各日会话快照（≠持仓实时分）" +
    " · 路径5m：数据中心仓" +
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
