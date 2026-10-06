/**
 * 数据中心 / 交易执行共用：是否允许显式增量补齐写本地仓。
 *
 * localStorage ``quantlab.data_offline_only``：缺省 ``1``（仅本地仓）。
 * - 开（仅本地仓）：ŷ 只读本地；不触发增量补齐
 * - 关（可拉远端）：先走研究枢纽同源「增量补齐」写日K/5m，再只读现算 ŷ
 *
 * ŷ 现算永远 ``offline_only=1``（见 offlineOnlyQuery）；禁止评分旁路拉网落盘。
 * 不含：观察名单维护、现价/浮盈行情、做T Worker 盯盘。
 */

import {
  OFFLINE_TOGGLE_TITLE_OFF,
  OFFLINE_TOGGLE_TITLE_ON,
} from "./data_policy.js?v=p1736";

export const DATA_OFFLINE_KEY = "quantlab.data_offline_only";

/** @returns {boolean} true=仅本地仓（不触发增量补齐） */
export function getDataOfflineOnly() {
  try {
    const raw = localStorage.getItem(DATA_OFFLINE_KEY);
    if (raw == null || raw === "") return true;
    const s = String(raw).trim().toLowerCase();
    if (s === "0" || s === "false" || s === "no" || s === "off") return false;
    return true;
  } catch (_) {
    return true;
  }
}

/** @param {boolean} on */
export function setDataOfflineOnly(on) {
  try {
    localStorage.setItem(DATA_OFFLINE_KEY, on ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

/**
 * 绑定 checkbox[role=switch]；变更时写 localStorage，并可选回调。
 * UI：亮色 checked =「可拉远端」；暗色 unchecked =「仅本地仓」。
 * @param {HTMLInputElement|null} input
 * @param {{ onChange?: (offlineOnly: boolean) => void }} [opts]
 */
export function installDataOfflineToggle(input, opts = {}) {
  if (!input) return { get: getDataOfflineOnly, set: setDataOfflineOnly };
  const syncUi = () => {
    const offlineOnly = getDataOfflineOnly();
    // 亮色 = 可拉远端（允许增量补齐）
    const remoteOn = !offlineOnly;
    input.checked = remoteOn;
    input.setAttribute("aria-checked", remoteOn ? "true" : "false");
    const label = input.closest("label");
    const text = label?.querySelector(".data-offline-toggle-text");
    if (text) text.textContent = offlineOnly ? "仅本地仓" : "可拉远端";
    label?.setAttribute(
      "title",
      offlineOnly ? OFFLINE_TOGGLE_TITLE_ON : OFFLINE_TOGGLE_TITLE_OFF
    );
  };
  syncUi();
  input.addEventListener("change", () => {
    const offlineOnly = !input.checked;
    setDataOfflineOnly(offlineOnly);
    syncUi();
    if (typeof opts.onChange === "function") opts.onChange(offlineOnly);
  });
  return { get: getDataOfflineOnly, set: setDataOfflineOnly, syncUi };
}

/**
 * ŷ / insights / 持仓分 / 预演调仓：永远只读本地仓。
 * 写仓请走 ``ensureWarehouseTopup``（可拉远端时）。
 */
export function offlineOnlyQuery() {
  return "offline_only=1";
}
