/**
 * 观察池可预测性分档（A/B/C），供数据中心主表。
 * 读上次「观察池分档」影子报告，表内不重算。
 */
import { escapeHtml } from "../shared.js";

const TIER_RANK = { A: 0, B: 1, C: 2 };

let _byCode = {};
let _loaded = false;
let _pending = null;
let _note = "";
let _requireTau = false;
let _aCount = 100;
let _bCount = 300;

function tierTip(t) {
  const scoreBit = _requireTau
    ? "Score=1/rank(Y_oo)+1/rank(Y_τc)"
    : "Score=1/rank(Y_oo)";
  if (t === "A") return `A 强：${scoreBit} · top ${_aCount}`;
  if (t === "B") return `B 中：${scoreBit} · 接着 ${_bCount}`;
  if (t === "C") return `C 弱：${scoreBit} · 剩余或无有效样本`;
  return "";
}

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

export function predTierBadgeHtml(code, esc) {
  esc = esc || escapeHtml;
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
  const scoreBit =
    info.score != null && !Number.isNaN(Number(info.score))
      ? `Score ${Number(info.score).toFixed(3)}`
      : "";
  const tip = [
    tierTip(t),
    scoreBit,
    `ŷ_oo ${fmtHit(info.hit_rate)}`,
    info.hit_rate_tau != null ? `ŷ_τc ${fmtHit(info.hit_rate_tau)}` : "",
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

function ingestPredTierReport(data) {
  const next = {};
  const rows = data && Array.isArray(data.rows) ? data.rows : [];
  rows.forEach((r) => {
    const code = String((r && r.code) || "").trim();
    const tier = String((r && r.tier) || "").trim().toUpperCase();
    if (!code || (tier !== "A" && tier !== "B" && tier !== "C")) return;
    next[code] = {
      tier,
      score: r.score,
      rank_oo: r.rank_oo,
      rank_tau: r.rank_tau,
      hit_rate: r.hit_rate,
      hit_rate_tau: r.hit_rate_tau,
      n_valid: r.n_valid,
      n_valid_tau: r.n_valid_tau,
      n_days: r.n_days,
      ic: r.ic,
    };
  });
  _byCode = next;
  _loaded = true;
  const thr = data && data.thresholds;
  if (thr && thr.a_count != null && Number.isFinite(Number(thr.a_count))) {
    _aCount = Math.max(1, Math.min(2000, Math.round(Number(thr.a_count))));
  }
  if (thr && thr.b_count != null && Number.isFinite(Number(thr.b_count))) {
    _bCount = Math.max(0, Math.min(5000, Math.round(Number(thr.b_count))));
  }
  _requireTau = !!(thr && thr.require_tau);
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
