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
/** @type {string[] | null} live 勾选的分档；null = 闸关，不过滤 */
let _selectedTiers = null;

function tierTip(t) {
  const scoreBit = _requireTau
    ? "Score=0.5*OO_F1+0.5*TC_F1"
    : "Score=OO_F1";
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

/** 历史回测「分档」勾选并同步到 live 的档。闸关时返回 null。 */
export function selectedPredTiers() {
  return _selectedTiers ? _selectedTiers.slice() : null;
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
  const fmtCondRet = (v, nSide, label) => {
    if (v == null || Number.isNaN(Number(v))) return "";
    const n = Number(v);
    const days = Number(nSide);
    const dayBit =
      Number.isFinite(days) && days > 0 ? ` · ${Math.round(days)}日` : "";
    return `${label} ${n > 0 ? "+" : ""}${n.toFixed(2)}%${dayBit}`;
  };
  const fmtF1Pos = (f1, precision, recall, label) => {
    if (f1 == null || Number.isNaN(Number(f1))) return "";
    const bits = [`${label} ${fmtHit(f1)}`];
    if (precision != null && !Number.isNaN(Number(precision))) {
      bits.push(`P⁺ ${fmtHit(precision)}`);
    }
    if (recall != null && !Number.isNaN(Number(recall))) {
      bits.push(`召回 ${fmtHit(recall)}`);
    }
    return bits.join(" ");
  };
  const tip = [
    tierTip(t),
    scoreBit,
    fmtCondRet(info.mean_y_pos, info.n_y_pos, "OO_R+"),
    fmtF1Pos(info.f1_pos, info.precision_pos, info.recall_pos, "OO_F1"),
    fmtCondRet(info.mean_y_pos_tau, info.n_y_pos_tau, "TC_R+"),
    fmtF1Pos(
      info.f1_pos_tau,
      info.precision_pos_tau,
      info.recall_pos_tau,
      "TC_F1"
    ),
    info.realized_pct != null && !Number.isNaN(Number(info.realized_pct))
      ? `收益 ${Number(info.realized_pct) > 0 ? "+" : ""}${Number(
          info.realized_pct
        ).toFixed(2)}%`
      : "",
    info.precision_pos != null ? `OO_P ${fmtHit(info.precision_pos)}` : "",
    info.recall_pos != null ? `OO_R ${fmtHit(info.recall_pos)}` : "",
    info.hit_rate != null ? `OO_ACC ${fmtHit(info.hit_rate)}` : "",
    info.precision_pos_tau != null
      ? `TC_P ${fmtHit(info.precision_pos_tau)}`
      : "",
    info.recall_pos_tau != null ? `TC_R ${fmtHit(info.recall_pos_tau)}` : "",
    info.hit_rate_tau != null ? `TC_ACC ${fmtHit(info.hit_rate_tau)}` : "",
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
      hit_rate: r.hit_rate,
      hit_rate_tau: r.hit_rate_tau,
      n_valid: r.n_valid,
      n_valid_tau: r.n_valid_tau,
      n_days: r.n_days,
      mean_y_pos: r.mean_y_pos,
      n_y_pos: r.n_y_pos,
      f1_pos: r.f1_pos,
      precision_pos: r.precision_pos,
      recall_pos: r.recall_pos,
      mean_y_pos_tau: r.mean_y_pos_tau,
      n_y_pos_tau: r.n_y_pos_tau,
      f1_pos_tau: r.f1_pos_tau,
      precision_pos_tau: r.precision_pos_tau,
      recall_pos_tau: r.recall_pos_tau,
      realized_pct: r.realized_pct,
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
  const live = data && data.live;
  const allow = live && Array.isArray(live.allowed_tiers) ? live.allowed_tiers : null;
  if (live && live.enabled && allow && allow.length) {
    _selectedTiers = allow
      .map((t) => String(t || "").trim().toUpperCase())
      .filter((t) => t === "A" || t === "B" || t === "C");
    if (!_selectedTiers.length) _selectedTiers = null;
  } else {
    _selectedTiers = null;
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
