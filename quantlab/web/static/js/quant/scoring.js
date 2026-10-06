/**
 * ŷ 滞回门槛与回测选股门 payload。
 */

/** @typedef {{ min_predicted_score?: number|null, min_hold_predicted_score?: number|null }} ScoringFloors */

/** @returns {ScoringFloors} */
export function defaultScoringFloors() {
  // null=未从配置水合；payload 应省略，让后端用 signal_config
  return { min_predicted_score: null, min_hold_predicted_score: null };
}

/**
 * Top-K / 中性化对照回测：predicted 模式发 min_predicted_score，勿硬编码 55。
 * 未水合时不发字段（勿默认 1.0，会盖掉配置 0.4）。
 * @param {ScoringFloors} floors
 * @param {string} [rankMode]
 * @returns {{ min_predicted_score: number } | { min_score: number } | {}}
 */
export function portfolioBtScoreFloorPayload(floors, rankMode) {
  const rm = rankMode || "predicted_score";
  if (rm === "predicted_score") {
    const mp = floors && floors.min_predicted_score;
    if (mp == null || !Number.isFinite(Number(mp))) return {};
    return { min_predicted_score: Number(mp) };
  }
  return { min_score: 55 };
}

/**
 * @param {ScoringFloors} base
 * @param {Partial<ScoringFloors>|null|undefined} patch
 * @returns {ScoringFloors}
 */
export function mergeScoringFloors(base, patch) {
  return { ...(base || defaultScoringFloors()), ...(patch || {}) };
}
