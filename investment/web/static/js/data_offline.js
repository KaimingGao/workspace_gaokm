/**
 * 数据中心 / 交易执行共用：是否禁止拉远端日线/分钟补数（影响 ŷ）。
 * localStorage ``investment.data_offline_only``：缺省 ``1``（仅本地仓，与策略调仓默认对齐）。
 *
 * 范围：ŷ / 日K·5m·指数。不含：观察名单维护、现价/浮盈行情、做T Worker 盯盘。
 */

import {
  OFFLINE_TOGGLE_TITLE_OFF,
  OFFLINE_TOGGLE_TITLE_ON,
} from "./data_policy.js";

export const DATA_OFFLINE_KEY = "investment.data_offline_only";

/** @returns {boolean} true=仅本地仓（不拉远端） */
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
 * @param {HTMLInputElement|null} input
 * @param {{ onChange?: (offlineOnly: boolean) => void }} [opts]
 */
export function installDataOfflineToggle(input, opts = {}) {
  if (!input) return { get: getDataOfflineOnly, set: setDataOfflineOnly };
  const syncUi = () => {
    const on = getDataOfflineOnly();
    input.checked = on;
    input.setAttribute("aria-checked", on ? "true" : "false");
    const label = input.closest("label");
    const text = label?.querySelector(".data-offline-toggle-text");
    if (text) text.textContent = on ? "仅本地仓" : "可拉远端";
    label?.setAttribute(
      "title",
      on ? OFFLINE_TOGGLE_TITLE_ON : OFFLINE_TOGGLE_TITLE_OFF
    );
  };
  syncUi();
  input.addEventListener("change", () => {
    const on = !!input.checked;
    setDataOfflineOnly(on);
    syncUi();
    if (typeof opts.onChange === "function") opts.onChange(on);
  });
  return { get: getDataOfflineOnly, set: setDataOfflineOnly, syncUi };
}

/** 拼到 query：``offline_only=1|0`` */
export function offlineOnlyQuery() {
  return `offline_only=${getDataOfflineOnly() ? "1" : "0"}`;
}
