/**
 * 观察池建仓层：payload / 模式 UI 配置 / 文案（纯函数）。
 */

export function watchingBuildInvalidTip(mode) {
  if (mode === "pct") return "仓位比例须在 0–100%";
  if (mode === "amount") return "金额须大于 0";
  return "股数须为 100 的整数倍";
}

export function watchingBuildTitleText(label, count) {
  return label ? `建仓 · ${label}` : `加入纸面 · ${count} 只`;
}

/**
 * @param {"amount"|"pct"|"shares"|string} mode
 * @param {number} raw default input value
 * @param {string[]} codes
 * @param {Record<string, number>} [amountByCode]
 * @param {Record<string, number>} [sharesByCode]
 * @returns {object|null}
 */
export function buildWatchingBuildPayload(mode, raw, codes, amountByCode, sharesByCode) {
  const list = Array.isArray(codes) ? codes : [];
  if (mode === "amount") {
    if (!(raw > 0)) return null;
    const map = {};
    let anyCustom = false;
    for (const code of list) {
      const custom = amountByCode && amountByCode[code];
      const amt = custom != null ? Number(custom) : raw;
      if (!(amt > 0)) return null;
      map[code] = Math.round(amt * 100) / 100;
      if (custom != null && Math.abs(amt - raw) > 0.01) anyCustom = true;
    }
    return {
      amount_per_code: Math.round(raw * 100) / 100,
      amount_by_code: anyCustom ? map : undefined,
    };
  }
  if (mode === "pct") {
    if (!(raw > 0) || raw > 100) return null;
    return { position_pct: Math.round((raw / 100) * 10000) / 10000 };
  }
  const defaults = Math.floor(raw / 100) * 100;
  if (!(defaults >= 100)) return null;
  const map = {};
  let anyCustom = false;
  for (const code of list) {
    const custom = sharesByCode && sharesByCode[code];
    const n = custom != null ? Math.floor(Number(custom) / 100) * 100 : defaults;
    if (!(n >= 100)) return null;
    map[code] = n;
    if (custom != null && n !== defaults) anyCustom = true;
  }
  return {
    shares: defaults,
    shares_by_code: anyCustom ? map : undefined,
  };
}

/** @returns {{ label: string, unit: string, min: string, step: string, defaultValue: string, title: string, shouldReset: (n: number) => boolean }} */
export function watchingBuildModeUiConfig(mode) {
  if (mode === "amount") {
    return {
      label: "默认每只",
      unit: "元",
      min: "100",
      step: "100",
      defaultValue: "20000",
      title: "默认金额（元）；清单里可按只改",
      shouldReset: (n) => !n || n < 100,
    };
  }
  if (mode === "pct") {
    return {
      label: "每只占可用现金",
      unit: "%",
      min: "0.1",
      step: "0.1",
      defaultValue: "10",
      title: "相对可用现金的仓位比例",
      shouldReset: (n) => !Number.isFinite(n) || n <= 0 || n > 100,
    };
  }
  return {
    label: "默认每只",
    unit: "股",
    min: "100",
    step: "100",
    defaultValue: "200",
    title: "默认股数；清单里可按只改（100 股为一手）",
    shouldReset: (n) => !n || n < 100,
  };
}
