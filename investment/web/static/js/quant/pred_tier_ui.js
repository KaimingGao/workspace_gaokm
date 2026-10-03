/**
 * 观察池可预测性分档（A/B/C），供数据中心主表。
 * 读上次「观察池分档」影子报告，表内不重算。
 */
import { escapeHtml as defaultEscapeHtml } from "../shared.js";

const TIER_RANK = { A: 0, B: 1, C: 2 };
const TIER_TIP = {
  A: "A 强：命中率>60%、IC>0、有效日 N>6",
  B: "B 中：方向尚可，或样本偏薄未升 A",
  C: "C 弱：命中不足或无有效样本",
};

let _byCode = {};
let _loaded = false;
let _pending = null;
let _note = "";

export function predTierRank(tier) {
  const t = String(tier || "").trim().toUpperCase();
  return Object.prototype.hasOwnProperty.call(TIER_RANK, t) ? TIER_RANK[t] : 9;
}

export function getPredTier(code) {
  const c = String(code || "").trim();
  return c && _byCode[c] ? _byCode[c] : null;
}

function fmtHit(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(1)}%`;
}

export function predTierBadgeHtml(code, escapeHtml) {
  const esc = escapeHtml || defaultEscapeHtml;
  const info = getPredTier(code);
  const t = info ? String(info.tier || "").trim().toUpperCase() : "";
  if (t !== "A" && t !== "B" && t !== "C") {
    const why = _loaded
      ? _note || "尚无分档。请在研究枢纽跑「观察池分档」"
      : "分档加载中";
    return `<span class="watching-tier-empty" title="${esc(why)}">—</span>`;
  }
  const icBit =
    info.ic != null && !Number.isNaN(Number(info.ic))
      ? `IC ${Number(info.ic).toFixed(3)}`
      : "";
  const tip = [
    TIER_TIP[t],
    `命中 ${fmtHit(info.hit_rate)}`,
    `有效日 ${info.n_valid ?? 0}/${info.n_days ?? 0}`,
    icBit,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    `<span class="watching-fit-tier-badge is-${t}" data-tier="${t}" title="${esc(tip)}">${esc(
      t
    )}</span>`
  );
}

export function ingestPredTierReport(data) {
  const next = {};
  const rows = data && Array.isArray(data.rows) ? data.rows : [];
  rows.forEach((r) => {
    const code = String((r && r.code) || "").trim();
    const tier = String((r && r.tier) || "").trim().toUpperCase();
    if (!code || (tier !== "A" && tier !== "B" && tier !== "C")) return;
    next[code] = {
      tier,
      hit_rate: r.hit_rate,
      n_valid: r.n_valid,
      n_days: r.n_days,
      ic: r.ic,
    };
  });
  _byCode = next;
  _loaded = true;
  if (data && data.success === false) {
    _note = String(data.note || "尚无分档报告；请先在研究枢纽跑「观察池分档」");
  } else if (!rows.length) {
    _note = "尚无分档报告；请先在研究枢纽跑「观察池分档」";
  } else {
    _note = "";
  }
  return _byCode;
}

export async function ensurePredTierMap() {
  if (_loaded) return _byCode;
  if (_pending) return _pending;
  _pending = fetch("/api/quant/research-universe/predictability-tiers/last?slim=1")
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => ingestPredTierReport(data || {}))
    .catch(() => ingestPredTierReport({}))
    .finally(() => {
      _pending = null;
    });
  return _pending;
}
