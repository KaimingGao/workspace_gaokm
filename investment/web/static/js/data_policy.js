/**
 * 数据产物对照（UI 文案唯一口径）。
 *
 * 四类产物互不替代：
 *   名单 · 日 K 仓 · 5m 仓 · 分数(ŷ)
 * 「刷新」必须写清产物；现价/浮盈另属行情层，不受页头仓写开关约束。
 *
 * 写仓收敛：研究枢纽「增量补齐」日常 /「强更日 K·5m」兜底；
 * 数据中心/交易执行「可拉远端」= 触发同源增量补齐，ŷ 仍只读本地。
 */

/** @typedef {{ id: string, name: string, write: string, read: string }} DataArtifact */

/** @type {DataArtifact[]} */
export const DATA_ARTIFACTS = [
  {
    id: "watchlist",
    name: "观察名单",
    write: "数据中心 · 搜票加入 / 同步名单（不写 K 线）",
    read: "观察表 · 调仓宇宙 · 研究枢纽覆盖统计",
  },
  {
    id: "day_bars",
    name: "日 K 仓",
    write: "研究枢纽「增量补齐」/「强更日 K」；页头「可拉远端」同源增量补齐",
    read: "ŷ_oo · IC/OOS · 仅本地仓时的现价回退",
  },
  {
    id: "minute_bars",
    name: "5m 仓",
    write: "研究枢纽「增量补齐」/「强更 5m」；页头「可拉远端」同源增量补齐",
    read: "ŷ_oc 因果前缀≤10:00 分钟小包 · 做T回测/预演路径 · 涨跌 tip",
  },
  {
    id: "scores",
    name: "分数 ŷ",
    write: "现算（不落独立仓）；只读日 K±5m±模型 · 不旁路拉网",
    read: "数据中心 insights · 持仓分 · 预演调仓 · 做T选向（会话快照）",
  },
  {
    id: "quotes",
    name: "现价行情",
    write: "行情接口 / 调度 spot（与仓写开关无关）",
    read: "涨跌% · 浮盈 · 调仓落账成交价 · 做T tick 盯盘",
  },
];

export const OFFLINE_TOGGLE_TITLE_ON =
  "关：ŷ 只读本地日K·5m·指数；不触发增量补齐（名单与现价/浮盈不受此开关）";
export const OFFLINE_TOGGLE_TITLE_OFF =
  "开：先对观察池跑「增量补齐」写日K/5m，再只读现算 ŷ；现价仍走行情";

export function offlineToggleTitle(on) {
  return on ? OFFLINE_TOGGLE_TITLE_ON : OFFLINE_TOGGLE_TITLE_OFF;
}

/** 页头一句：当前分数数据政策 */
export function scoresPolicyLine(offlineOnly = true) {
  return offlineOnly
    ? "ŷ：仅本地仓（日K·5m）· 现价/浮盈仍行情"
    : "ŷ：可拉远端→增量补齐写仓后只读 · 现价/浮盈行情";
}

/** 策略调仓区脚注 */
export function rebalanceDataFoot(offlineOnly = true) {
  const score = offlineOnly
    ? "ŷ 读本地日K·5m（页头「仅本地仓」）"
    : "ŷ 先增量补齐观察池再只读（页头「可拉远端」）";
  return (
    `${score} · 缺仓/仓坏可去研究枢纽「强更」` +
    ` · 落账成交价走行情 · ≠ 做T 选向`
  );
}

/** 做T区脚注 */
export function t0DataFoot() {
  return (
    "选向ŷ：回测/预演用各日会话快照（≠持仓实时分）" +
    " · 路径5m：研究枢纽仓 / 页头可拉远端增量补齐" +
    " · Worker/落账可拉最新5m · 现价盯盘走行情"
  );
}

/** 研究枢纽分钟区补充 */
export function minuteHubFoot() {
  return "下游：ŷ_oc 因果前缀≤10:00 · 预演调仓 · 做T回测 · 涨跌 tip；Ready≠消费端 as-of 齐";
}

/** 研究枢纽日线区补充 */
export function dayBarsHubFoot() {
  return "下游：ŷ_oo / 分组 OLS · ≠ 5m；日常增量补齐 · 仓坏用强更日 K；「刷新 watching」只改名单";
}

/** 数据中心页头路径说明 */
export function watchingPathHint() {
  return (
    "名单在本页维护 · 日K/5m：研究枢纽或页头「可拉远端」增量补齐 · ŷ 只读现算 · 行情列独立"
  );
}
