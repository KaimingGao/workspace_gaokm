/**
 * 观察台与交易执行的 ŷ 读取固定 offline_only=1。
 * 日 K / 5m 补齐只在数据中心。禁止评分旁路拉网落盘。
 */

export function offlineOnlyQuery() {
  return "offline_only=1";
}
