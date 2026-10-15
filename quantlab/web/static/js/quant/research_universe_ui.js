/**
 * 观察池分档：研究套 ŷ_oo（+ 有则 ŷ_τc）· Score=0.5*OO_F1+0.5*TC_F1 · A 档另过 OO_F1 / TC_F1（可勾选 OO_R+>0 / TC_R+>0）· 分档看 Holdout 前半，指标与等权买持看整段 → last。
 * 有研究套时只拉 Holdout 附近本地日线建面板；live 档位跟随历史回测「分档」勾选。
 */
import { createScoreTooltipController } from "../score_tooltip.js";
import { syncOverviewUniverse } from "./factor_corr_ui.js";
import {
  markWatchingPoolLimitReady,
  notifyWatchingPoolLimitChanged,
  readWatchingPoolLimit,
} from "./params.js";

export function installResearchUniverseUi(q) {
  const { on, apiFetch, escapeHtml, readHoldoutTradingDays, readFitLookbackDays } = q;
  let lastTiers = null;
  let liveStatus = null;
  const f1Tips = createScoreTooltipController();

  const A_COUNT_STORAGE_KEY = "quant.researchUniverse.aCount";
  const B_COUNT_STORAGE_KEY = "quant.researchUniverse.bCount";
  const A_F1_STORAGE_KEY = "quant.researchUniverse.aF1";
  const TAU_A_F1_STORAGE_KEY = "quant.researchUniverse.tauAF1";
  const REQUIRE_OO_R_POS_STORAGE_KEY = "quant.researchUniverse.requireOoRPos";
  const REQUIRE_TC_R_POS_STORAGE_KEY = "quant.researchUniverse.requireTcRPos";
  const A_COUNT_DEFAULT = 100;
  const B_COUNT_DEFAULT = 300;
  const A_F1_DEFAULT = 20;
  const TAU_A_F1_DEFAULT = 20;
  const REQUIRE_OO_R_POS_DEFAULT = true;
  const REQUIRE_TC_R_POS_DEFAULT = true;
  const A_COUNT_MIN = 1;
  const A_COUNT_MAX = 2000;
  const B_COUNT_MIN = 0;
  const B_COUNT_MAX = 5000;
  const HIT_PCT_MIN = 0;
  const HIT_PCT_MAX = 100;

  const el = {
    chip: () => document.getElementById("quant-ru-chip"),
    msg: () => document.getElementById("quant-ru-msg"),
    strip: () => document.getElementById("quant-ru-strip"),
    tierTable: () => document.getElementById("quant-ru-tier-table"),
    aCount: () => document.getElementById("quant-ru-a-count"),
    bCount: () => document.getElementById("quant-ru-b-count"),
    aF1: () => document.getElementById("quant-ru-a-f1"),
    tauAF1: () => document.getElementById("quant-ru-tau-a-f1"),
    requireOoRPos: () => document.getElementById("quant-ru-require-oo-r-pos"),
    requireTcRPos: () => document.getElementById("quant-ru-require-tc-r-pos"),
    maxSize: () => document.getElementById("quant-watching-max-size"),
    poolVal: () => document.getElementById("quant-watching-pool-val"),
    poolStatus: () => document.getElementById("quant-watching-pool-status"),
  };

  function clampACount(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return A_COUNT_DEFAULT;
    return Math.max(A_COUNT_MIN, Math.min(A_COUNT_MAX, Math.round(n)));
  }

  function clampBCount(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return B_COUNT_DEFAULT;
    return Math.max(B_COUNT_MIN, Math.min(B_COUNT_MAX, Math.round(n)));
  }

  /** 工具栏百分数 0–100。报告里的 0–1 比例由 hitPctFromRatio 先换算。 */
  function clampHitPct(raw, fallback) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(HIT_PCT_MIN, Math.min(HIT_PCT_MAX, Math.round(n)));
  }

  function hitPctFromRatio(raw, fallback) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    return clampHitPct(n <= 1 ? n * 100 : n, fallback);
  }

  function readStoredCount(key, fallback) {
    try {
      const saved = localStorage.getItem(key);
      if (saved != null && saved !== "") {
        const n = Number(saved);
        if (Number.isFinite(n)) return n;
      }
    } catch (_) {
      /* ignore */
    }
    return fallback;
  }

  function writeStoredCount(key, n) {
    try {
      localStorage.setItem(key, String(n));
    } catch (_) {
      /* ignore */
    }
  }

  function readStoredBool(key, fallback) {
    try {
      const saved = localStorage.getItem(key);
      if (saved == null || saved === "") return fallback;
      const s = String(saved).trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(s)) return true;
      if (["0", "false", "no", "off"].includes(s)) return false;
    } catch (_) {
      /* ignore */
    }
    return fallback;
  }

  function writeStoredBool(key, on) {
    try {
      localStorage.setItem(key, on ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
  }

  /** @returns {{ aCount: number, bCount: number, aF1: number, tauAF1: number, requireOoRPos: boolean, requireTcRPos: boolean }} */
  function readTierCounts() {
    const aCount = clampACount(
      el.aCount()?.value !== "" && el.aCount()?.value != null
        ? el.aCount().value
        : readStoredCount(A_COUNT_STORAGE_KEY, A_COUNT_DEFAULT)
    );
    const bCount = clampBCount(
      el.bCount()?.value !== "" && el.bCount()?.value != null
        ? el.bCount().value
        : readStoredCount(B_COUNT_STORAGE_KEY, B_COUNT_DEFAULT)
    );
    const aF1 = clampHitPct(
      el.aF1()?.value !== "" && el.aF1()?.value != null
        ? el.aF1().value
        : readStoredCount(A_F1_STORAGE_KEY, A_F1_DEFAULT),
      A_F1_DEFAULT
    );
    const tauAF1 = clampHitPct(
      el.tauAF1()?.value !== "" && el.tauAF1()?.value != null
        ? el.tauAF1().value
        : readStoredCount(TAU_A_F1_STORAGE_KEY, TAU_A_F1_DEFAULT),
      TAU_A_F1_DEFAULT
    );
    const ooInput = el.requireOoRPos();
    const tcInput = el.requireTcRPos();
    const requireOoRPos = ooInput
      ? !!ooInput.checked
      : readStoredBool(REQUIRE_OO_R_POS_STORAGE_KEY, REQUIRE_OO_R_POS_DEFAULT);
    const requireTcRPos = tcInput
      ? !!tcInput.checked
      : readStoredBool(REQUIRE_TC_R_POS_STORAGE_KEY, REQUIRE_TC_R_POS_DEFAULT);
    const aInput = el.aCount();
    const bInput = el.bCount();
    const aF1Input = el.aF1();
    const tauF1Input = el.tauAF1();
    if (aInput && String(aInput.value) !== String(aCount)) aInput.value = String(aCount);
    if (bInput && String(bInput.value) !== String(bCount)) bInput.value = String(bCount);
    if (aF1Input && String(aF1Input.value) !== String(aF1)) aF1Input.value = String(aF1);
    if (tauF1Input && String(tauF1Input.value) !== String(tauAF1)) {
      tauF1Input.value = String(tauAF1);
    }
    if (ooInput && ooInput.checked !== requireOoRPos) ooInput.checked = requireOoRPos;
    if (tcInput && tcInput.checked !== requireTcRPos) tcInput.checked = requireTcRPos;
    writeStoredCount(A_COUNT_STORAGE_KEY, aCount);
    writeStoredCount(B_COUNT_STORAGE_KEY, bCount);
    writeStoredCount(A_F1_STORAGE_KEY, aF1);
    writeStoredCount(TAU_A_F1_STORAGE_KEY, tauAF1);
    writeStoredBool(REQUIRE_OO_R_POS_STORAGE_KEY, requireOoRPos);
    writeStoredBool(REQUIRE_TC_R_POS_STORAGE_KEY, requireTcRPos);
    return { aCount, bCount, aF1, tauAF1, requireOoRPos, requireTcRPos };
  }

  function paintCountFromReport(rep) {
    const thr = rep && rep.thresholds;
    if (!thr) return;
    if (thr.a_count != null && Number.isFinite(Number(thr.a_count))) {
      const aCount = clampACount(thr.a_count);
      const input = el.aCount();
      if (input) input.value = String(aCount);
      writeStoredCount(A_COUNT_STORAGE_KEY, aCount);
    }
    if (thr.b_count != null && Number.isFinite(Number(thr.b_count))) {
      const bCount = clampBCount(thr.b_count);
      const input = el.bCount();
      if (input) input.value = String(bCount);
      writeStoredCount(B_COUNT_STORAGE_KEY, bCount);
    }
    // 只认 a_f1；旧报告的 a_hit 是命中率门槛，不能灌进 F1 控件
    if (thr.a_f1 != null && Number.isFinite(Number(thr.a_f1))) {
      const aF1 = hitPctFromRatio(thr.a_f1, A_F1_DEFAULT);
      const input = el.aF1();
      if (input) input.value = String(aF1);
      writeStoredCount(A_F1_STORAGE_KEY, aF1);
    }
    if (thr.tau_a_f1 != null && Number.isFinite(Number(thr.tau_a_f1))) {
      const tauAF1 = hitPctFromRatio(thr.tau_a_f1, TAU_A_F1_DEFAULT);
      const input = el.tauAF1();
      if (input) input.value = String(tauAF1);
      writeStoredCount(TAU_A_F1_STORAGE_KEY, tauAF1);
    }
    if (thr.require_oo_r_pos != null) {
      const on = !!thr.require_oo_r_pos;
      const input = el.requireOoRPos();
      if (input) input.checked = on;
      writeStoredBool(REQUIRE_OO_R_POS_STORAGE_KEY, on);
    }
    if (thr.require_tc_r_pos != null) {
      const on = !!thr.require_tc_r_pos;
      const input = el.requireTcRPos();
      if (input) input.checked = on;
      writeStoredBool(REQUIRE_TC_R_POS_STORAGE_KEY, on);
    }
  }

  function tierMetaFor(
    aCount,
    bCount,
    aF1,
    tauAF1,
    requireTau,
    requireOoRPos,
    requireTcRPos
  ) {
    let gate = requireTau
      ? `OO_F1≥${aF1}% 且 TC_F1≥${tauAF1}%`
      : `OO_F1≥${aF1}%`;
    if (requireOoRPos) gate += " 且 OO_R+>0";
    if (requireTcRPos && requireTau) gate += " 且 TC_R+>0";
    return {
      A: { label: "强", tip: `过 ${gate} 的 Score 序 top ${aCount}` },
      B: { label: "中", tip: `未进 A 后接着 ${bCount}` },
      C: { label: "弱", tip: "其余或无样本" },
    };
  }

  function holdoutN() {
    if (typeof readHoldoutTradingDays === "function") {
      return Math.max(2, Math.min(90, Number(readHoldoutTradingDays("oo")) || 20));
    }
    return 20;
  }

  function ooLookbackN() {
    if (typeof readFitLookbackDays === "function") {
      return Math.max(100, Math.min(1000, Number(readFitLookbackDays("oo")) || 120));
    }
    return 120;
  }

  function setStatus(state, chip, message) {
    const c = el.chip();
    const m = el.msg();
    const s = el.strip();
    if (c) {
      c.dataset.state = state || "idle";
      c.textContent = chip || "—";
    }
    if (m) m.textContent = message || "";
    if (s) s.classList.toggle("is-busy", state === "busy");
  }

  function liveBit() {
    if (!liveStatus || !liveStatus.enabled) return "live 随回测（未开）";
    const allow = (liveStatus.allowed_tiers || ["A"]).join("") || "A";
    return `live 随回测 ${allow}`;
  }

  function fmtHit(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return `${Math.round(Number(v) * 1000) / 10}%`;
  }

  function fmtIc(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(3);
  }

  function csPackOf(rep, tier, head) {
    if (!rep) return null;
    const useTau = head === "tau";
    if (tier) {
      const by = useTau ? rep.cs_by_tier_tau || {} : rep.cs_by_tier || {};
      return by[tier] || null;
    }
    if (useTau) {
      return {
        cs_rank_ic: rep.cs_rank_ic_tau,
        cs_ic: rep.cs_ic_tau,
        cs_icir: rep.cs_icir_tau,
      };
    }
    return rep;
  }

  function csValCls(v) {
    if (v == null || Number.isNaN(Number(v))) return "";
    const n = Number(v);
    if (n > 1e-12) return " up";
    if (n < -1e-12) return " down";
    return "";
  }

  function fmtRetPct(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}%`;
  }

  function csMetricsHtml(pack, tip, headLab) {
    const items = [
      ["RankIC", pack && pack.cs_rank_ic, fmtIc],
      ["IC", pack && pack.cs_ic, fmtIc],
      ["ICIR", pack && pack.cs_icir, fmtIc],
    ];
    const html = items
      .map(
        ([lab, raw, fmt]) =>
          `<span class="quant-ru-tier-cs-item">` +
          `<span class="quant-ru-tier-cs-lab">${escapeHtml(lab)}</span>` +
          `<span class="quant-ru-tier-cs-val${csValCls(raw)}">${fmt(raw)}</span>` +
          `</span>`
      )
      .join("");
    const headBit = headLab
      ? `<span class="quant-ru-tier-cs-head">${escapeHtml(headLab)}</span>`
      : "";
    return (
      `<span class="quant-ru-tier-cs" title="${escapeHtml(tip)}" aria-label="档内日截面">` +
      headBit +
      html +
      `</span>`
    );
  }

  function dualCsHtml(rep, tier) {
    const ooTip = `Holdout 整段、${tier} 档内 Y_oo：Rank IC / IC / ICIR`;
    const tauTip =
      `Holdout 整段、${tier} 档内 Y_τc 日截面：Rank IC / IC / ICIR`;
    const requireTau = !!(rep && rep.thresholds && rep.thresholds.require_tau);
    const ooPack = csPackOf(rep, tier, "oo");
    const tauPack = csPackOf(rep, tier, "tau");
    const showTau =
      requireTau ||
      (tauPack &&
        (tauPack.cs_rank_ic != null ||
          tauPack.cs_ic != null ||
          tauPack.cs_icir != null));
    return (
      `<span class="quant-ru-tier-cs-wrap">` +
      csMetricsHtml(ooPack, ooTip, "Y_oo") +
      (showTau ? csMetricsHtml(tauPack, tauTip, "Y_τc") : "") +
      `</span>`
    );
  }

  function tierMeanRet(rep, tier) {
    const pack = csPackOf(rep, tier, "oo");
    if (!pack || pack.mean_realized_pct == null) return null;
    const n = Number(pack.mean_realized_pct);
    return Number.isFinite(n) ? n : null;
  }

  /** 有样本卡：各档等权买持收益按 n_realized（参与票数）加权。 */
  function poolMeanRet(rep) {
    const by = (rep && rep.cs_by_tier) || {};
    let sum = 0;
    let n = 0;
    for (const tier of ["A", "B", "C"]) {
      const pack = by[tier];
      if (!pack || pack.mean_realized_pct == null) continue;
      const pct = Number(pack.mean_realized_pct);
      const w = Number(pack.n_realized);
      if (!Number.isFinite(pct) || !Number.isFinite(w) || w <= 0) continue;
      sum += pct * w;
      n += w;
    }
    return n > 0 ? sum / n : null;
  }

  function kpiRetHtml(ret, tip) {
    const val =
      ret == null
        ? `<span class="quant-ru-kpi-ret-val">—</span>`
        : `<span class="quant-ru-kpi-ret-val${csValCls(ret)}">${fmtRetPct(ret)}</span>`;
    return (
      `<span class="quant-ru-kpi-ret" title="${escapeHtml(tip)}">` +
      `<span class="quant-ru-kpi-ret-lab">平均收益率</span>` +
      val +
      `</span>`
    );
  }

  function kpiCardHtml(tierCls, lab, valHtml, ret, tip) {
    const cls = tierCls ? `quant-ru-kpi ${tierCls}` : "quant-ru-kpi";
    return (
      `<div class="${cls}">` +
      `<span class="quant-ru-kpi-lab">${lab}</span>` +
      `<span class="quant-ru-kpi-val">${valHtml}</span>` +
      kpiRetHtml(ret, tip) +
      `</div>`
    );
  }

  /** ≥50% 红 / <50% 绿；类名与收益列 up/down 对齐。 */
  function hitTone(hitRate) {
    if (hitRate == null || Number.isNaN(Number(hitRate))) return "is-empty";
    return Number(hitRate) * 100 + 1e-9 >= 50 ? "up" : "down";
  }

  /** 比例列：仅百分数（无比例条 / 无样本 N）。 */
  function rateCell(rate, title, headCls) {
    const tone = hitTone(rate);
    return (
      `<td class="num quant-ru-rate-cell ${escapeHtml(headCls || "")}" title="${escapeHtml(
        title || ""
      )}">` +
      `<span class="quant-ru-hit-val ${tone}">${fmtHit(rate)}</span>` +
      `</td>`
    );
  }

  function fmtScore(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(3);
  }

  function scoreCell(r) {
    const sc = r && r.score;
    const ooF1 = r && r.f1_pos;
    const tcF1 = r && r.f1_pos_tau;
    const tipParts = [
      sc != null && !Number.isNaN(Number(sc))
        ? `Rank_Score ${fmtScore(sc)}`
        : "无 Rank_Score",
      "0.5*OO_F1+0.5*TC_F1",
      ooF1 != null && !Number.isNaN(Number(ooF1)) ? `OO_F1 ${fmtHit(ooF1)}` : "OO_F1 —",
      tcF1 != null && !Number.isNaN(Number(tcF1)) ? `TC_F1 ${fmtHit(tcF1)}` : "TC_F1 —",
    ].filter(Boolean);
    return (
      `<td class="num quant-ru-score-cell" title="${escapeHtml(tipParts.join(" · "))}">` +
      `${fmtScore(sc)}` +
      `</td>`
    );
  }

  function retCell(r) {
    const pct = r && r.realized_pct;
    const n = pct == null ? NaN : Number(pct);
    const tip =
      pct == null || !Number.isFinite(n)
        ? "Holdout 整段无买持收益（日线不足）"
        : `Holdout 整段买持 ${fmtRetPct(n)}（close 首尾，对齐档内平均收益率）`;
    return (
      `<td class="num quant-ru-ret-cell${csValCls(Number.isFinite(n) ? n : null)}" title="${escapeHtml(
        tip
      )}">` +
      `${fmtRetPct(Number.isFinite(n) ? n : null)}` +
      `</td>`
    );
  }

  /** kind: "oo" → OO_R+；"tau" → TC_R+ */
  function condRetPosCell(r, kind) {
    const isOo = kind !== "tau";
    const v = r && (isOo ? r.mean_y_pos : r.mean_y_pos_tau);
    const n = v == null ? NaN : Number(v);
    const nSide = Number((r && (isOo ? r.n_y_pos : r.n_y_pos_tau)) || 0);
    const nValid = Number((r && (isOo ? r.n_valid : r.n_valid_tau)) || 0);
    const label = isOo ? "OO_R+" : "TC_R+";
    const yhat = isOo ? "ŷ_oo" : "ŷ_τc";
    const yReal = isOo ? "y_oo" : "y_τc";
    const formula = `mean(${yReal}|${yhat}>ε)`;
    let tip;
    let text;
    let cls = "";
    const thr = lastTiers && lastTiers.thresholds ? lastTiers.thresholds : null;
    const needThis = !!(
      thr && (isOo ? thr.require_oo_r_pos : thr.require_tc_r_pos)
    );
    if (Number.isFinite(n)) {
      tip =
        `Holdout 前半 ${label}=${formula} ${fmtRetPct(n)}` +
        (nSide > 0 ? ` · ${nSide} 日` : "") +
        (needThis ? ` · A 档须 ${label}>0` : "");
      text = fmtRetPct(n);
      cls = csValCls(n);
    } else if (nValid > 0) {
      tip =
        `Holdout 前半无 ${yhat}>ε 日（${nValid} 个有效命中日均为看空）· 无法算 ${label}` +
        (needThis ? " · 不过 A" : "");
      text = "无看多";
      cls = " is-muted";
    } else {
      tip = `Holdout 前半无有效 ${yhat} 样本 · 无法算 ${label}`;
      text = "—";
    }
    return (
      `<td class="num quant-ru-ret-cell${cls}" title="${escapeHtml(tip)}">` +
      `${text}` +
      `</td>`
    );
  }

  /**
   * 档内有效 F1 的 min/max，供相对着色（避免全池高分位票在 A 档「全红」）。
   * key: f1_pos | f1_pos_tau
   * @returns {{ lo: number, hi: number } | null}
   */
  function buildF1RelScale(rows, key) {
    const field = key || "f1_pos";
    let lo = Infinity;
    let hi = -Infinity;
    let n = 0;
    for (const r of rows || []) {
      const v = r && r[field] != null ? Number(r[field]) : NaN;
      if (!Number.isFinite(v)) continue;
      n += 1;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    if (n <= 0) return null;
    return { lo, hi };
  }

  /** 档内 min-max → 0..1；同值无跨度 → 0.5（中性）。 */
  function f1RelPct(f1, scale) {
    const n = Number(f1);
    if (!Number.isFinite(n) || !scale || typeof scale !== "object") return null;
    const lo = Number(scale.lo);
    const hi = Number(scale.hi);
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const span = hi - lo;
    if (span <= 1e-12) return 0.5;
    return Math.max(0, Math.min(1, (n - lo) / span));
  }

  /** 档内相对 → tip 徽章：偏高 / 偏低 / 中位。 */
  function f1RelBand(pct) {
    if (pct == null || !Number.isFinite(Number(pct))) {
      return { tone: "is-empty", badge: "无样本" };
    }
    const p = Number(pct);
    if (p >= 0.8) return { tone: "is-hi", badge: "档内偏高" };
    if (p <= 0.2) return { tone: "is-lo", badge: "档内偏低" };
    return { tone: "is-mid", badge: "档内中位" };
  }

  /**
   * 深浅：中位贴 ink（浅/中性），两端再饱和到红/绿。
   * 避免 color-mix(红,绿) 中间变成脏橄榄、看不出浅色。
   * data-f1-tone=up|down；--f1-sat: 0%..100%（距中位越远越深）。
   */
  function f1RelTone(relPct) {
    const p = Number(relPct);
    if (!Number.isFinite(p)) return { tone: "", sat: "0%" };
    const clamped = Math.max(0, Math.min(1, p));
    return {
      tone: clamped >= 0.5 ? "up" : "down",
      sat: `${Math.round(Math.abs(clamped - 0.5) * 2 * 100)}%`,
    };
  }

  function f1TipHtml(pack) {
    const p = pack && typeof pack === "object" ? pack : {};
    const tone = String(p.tone || "is-empty");
    const badge = p.badge != null ? String(p.badge) : "无样本";
    const badgeCls =
      tone === "is-hi"
        ? "is-hi"
        : tone === "is-lo"
          ? "is-lo"
          : tone === "is-mid"
            ? "is-mid"
            : "is-empty";
    const hero = p.f1_txt != null ? String(p.f1_txt) : "—";
    const reason = p.reason ? String(p.reason) : "";
    const foot = p.foot
      ? String(p.foot)
      : "进 Score（0.5*OO_F1+0.5*TC_F1）· 默认可作 A 档门槛";
    const eyebrow =
      p.eyebrow != null
        ? String(p.eyebrow)
        : "OO_F1 · Holdout 前半 · 正类=上涨";
    const pTxt =
      p.precision != null && Number.isFinite(Number(p.precision))
        ? fmtHit(p.precision)
        : "—";
    const rTxt =
      p.recall != null && Number.isFinite(Number(p.recall))
        ? fmtHit(p.recall)
        : "—";
    const relBit =
      p.rel_pct != null && Number.isFinite(Number(p.rel_pct))
        ? `档内 ${Math.round(Number(p.rel_pct) * 100)}%`
        : "";
    const defs =
      p.ok === true
        ? `<dl class="quant-ru-f1-tip-defs">` +
          `<div><dt>精确率 P⁺</dt><dd><b>${escapeHtml(pTxt)}</b> · 看多且涨 / 看多</dd></div>` +
          `<div><dt>召回</dt><dd><b>${escapeHtml(rTxt)}</b> · 看多且涨 / 实际上涨日</dd></div>` +
          `<div><dt>着色</dt><dd>本档相对：中位 ink · 偏高红 · 偏低绿${relBit ? ` · ${escapeHtml(relBit)}` : ""}</dd></div>` +
          `<div><dt>死区</dt><dd>与命中同 |ŷ|≥ε · |y|&gt;0</dd></div>` +
          `</dl>`
        : reason
          ? `<p class="quant-ru-f1-tip-reason">${escapeHtml(reason)}</p>`
          : `<dl class="quant-ru-f1-tip-defs">` +
            `<div><dt>精确率 P⁺</dt><dd>看多且涨 / 看多</dd></div>` +
            `<div><dt>召回</dt><dd>看多且涨 / 实际上涨日</dd></div>` +
            `<div><dt>死区</dt><dd>与命中同 |ŷ|≥ε · |y|&gt;0</dd></div>` +
            `</dl>`;
    let heroTone = "";
    let heroStyle = "";
    if (p.rel_pct != null && Number.isFinite(Number(p.rel_pct))) {
      const rel = f1RelTone(Number(p.rel_pct));
      heroTone = rel.tone ? ` data-f1-tone="${rel.tone}"` : "";
      heroStyle = ` style="--f1-sat:${rel.sat}"`;
    }
    return (
      `<div class="quant-ru-f1-tip-inner">` +
      `<div class="quant-ru-f1-tip-head">` +
      `<div class="quant-ru-f1-tip-head-main">` +
      `<div class="quant-ru-f1-tip-eyebrow">${escapeHtml(eyebrow)}</div>` +
      `<div class="quant-ru-f1-tip-hero is-rel"${heroTone}${heroStyle}>${escapeHtml(hero)}</div>` +
      `</div>` +
      `<span class="quant-ru-f1-tip-badge ${badgeCls}">${escapeHtml(badge)}</span>` +
      `</div>` +
      defs +
      `<p class="quant-ru-f1-tip-foot">${escapeHtml(foot)}</p>` +
      `</div>`
    );
  }

  /** kind: "oo" → OO_F1；"tau" → TC_F1 */
  function f1PosCell(r, scale, kind) {
    const isOo = kind !== "tau";
    const label = isOo ? "OO_F1" : "TC_F1";
    const yhat = isOo ? "ŷ_oo" : "ŷ_τc";
    const v = r && (isOo ? r.f1_pos : r.f1_pos_tau);
    const n = v == null ? NaN : Number(v);
    const p = r && (isOo ? r.precision_pos : r.precision_pos_tau);
    const rec = r && (isOo ? r.recall_pos : r.recall_pos_tau);
    const nPos = Number((r && (isOo ? r.n_y_pos : r.n_y_pos_tau)) || 0);
    const nValid = Number((r && (isOo ? r.n_valid : r.n_valid_tau)) || 0);
    const eyebrow = `${label} · Holdout 前半 · 正类=上涨`;
    let text;
    /** @type {Record<string, unknown>} */
    let tipPack;
    let relPct = null;
    let band = { tone: "is-empty", badge: "无样本" };
    if (Number.isFinite(n)) {
      text = fmtHit(n);
      relPct = f1RelPct(n, scale);
      band = f1RelBand(relPct);
      tipPack = {
        ok: true,
        tone: band.tone,
        badge: band.badge,
        rel_pct: relPct,
        eyebrow,
        f1: n,
        f1_txt: text,
        precision: p != null && Number.isFinite(Number(p)) ? Number(p) : null,
        recall: rec != null && Number.isFinite(Number(rec)) ? Number(rec) : null,
        foot:
          (nPos > 0 ? `看多 ${nPos} 日 · ` : "无看多样本 · ") +
          (isOo
            ? "进 Score（×0.5 或单独）· 默认可作 A 档门槛"
            : "进 Score ×0.5 · 有 ŷ_τc 时默认可作 A 档门槛"),
      };
    } else if (nValid > 0 && nPos <= 0) {
      text = "无看多";
      tipPack = {
        ok: false,
        tone: "is-empty",
        badge: "无样本",
        eyebrow,
        f1_txt: "—",
        reason: `无 ${yhat}>ε 日（${nValid} 个有效命中日均为看空），无法算 P⁺ / 召回`,
        foot: isOo
          ? "进 Score（×0.5 或单独）· 默认可作 A 档门槛"
          : "进 Score ×0.5 · 有 ŷ_τc 时默认可作 A 档门槛",
      };
    } else if (nValid > 0) {
      text = "—";
      tipPack = {
        ok: false,
        tone: "is-empty",
        badge: "无样本",
        eyebrow,
        f1_txt: "—",
        reason: "无上涨有效日 · P⁺ / 召回缺一",
        foot: isOo
          ? "进 Score（×0.5 或单独）· 默认可作 A 档门槛"
          : "进 Score ×0.5 · 有 ŷ_τc 时默认可作 A 档门槛",
      };
    } else {
      text = "—";
      tipPack = {
        ok: false,
        tone: "is-empty",
        badge: "无样本",
        eyebrow,
        f1_txt: "—",
        reason: `无有效 ${yhat} 样本`,
        foot: isOo
          ? "进 Score（×0.5 或单独）· 默认可作 A 档门槛"
          : "进 Score ×0.5 · 有 ŷ_τc 时默认可作 A 档门槛",
      };
    }
    const hasRel = relPct != null && Number.isFinite(relPct);
    const muted = !hasRel && text !== "—" ? " is-muted" : "";
    const tipAttr = escapeHtml(JSON.stringify(tipPack));
    const rel = hasRel ? f1RelTone(relPct) : null;
    const toneBit = rel && rel.tone ? ` data-f1-tone="${rel.tone}"` : "";
    const styleBit = rel ? ` style="--f1-sat:${rel.sat}"` : "";
    const valCls = hasRel ? "is-rel" : "is-empty";
    return (
      `<td class="num quant-ru-f1-cell ${valCls}${muted}" data-f1-tip="${tipAttr}"${toneBit}${styleBit}>` +
      `<span class="quant-ru-f1-val ${valCls}">${text}</span>` +
      `</td>`
    );
  }

  function wireF1Tips(host) {
    if (!host) return;
    f1Tips.bindAttrTip(host, {
      selector: "[data-f1-tip]",
      className: "score-tooltip quant-ru-f1-tip",
      wireKey: "ru-f1-tip",
      buildHtml: (node) => {
        try {
          return f1TipHtml(JSON.parse(node.getAttribute("data-f1-tip") || "{}"));
        } catch (_) {
          return "";
        }
      },
    });
  }

  function stockCell(r) {
    const code = escapeHtml(r.code || "");
    const name = escapeHtml(String(r.name || "").trim() || "—");
    const title = r.name
      ? `${escapeHtml(r.name)} ${code}`
      : code;
    return (
      `<td class="quant-ru-stock" title="${title}">` +
      `<span class="quant-ru-stock-name">${name}</span>` +
      `<span class="quant-ru-stock-code">${code}</span>` +
      `</td>`
    );
  }

  function mixBarHtml(counts) {
    const a = Number(counts.A || 0);
    const b = Number(counts.B || 0);
    const c = Number(counts.C || 0);
    const tot = Math.max(1, a + b + c);
    return (
      `<div class="quant-ru-mix" role="img" aria-label="档位构成 A${a} B${b} C${c}">` +
      `<span class="quant-ru-mix-seg is-A" style="flex:${a}" title="A ${a}"></span>` +
      `<span class="quant-ru-mix-seg is-B" style="flex:${b}" title="B ${b}"></span>` +
      `<span class="quant-ru-mix-seg is-C" style="flex:${c}" title="C ${c}"></span>` +
      `<span class="quant-ru-mix-meta">${Math.round((a / tot) * 100)}% A · ${Math.round(
        (b / tot) * 100
      )}% B · ${Math.round((c / tot) * 100)}% C</span>` +
      `</div>`
    );
  }

  function renderTierTable(rep) {
    const host = el.tierTable();
    if (!host) return;
    if (!rep || !rep.success || !Array.isArray(rep.rows) || !rep.rows.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    const thr = rep.thresholds || {};
    const hold = rep.holdout_n != null ? rep.holdout_n : "—";
    const tierN = rep.tier_n != null ? rep.tier_n : rep.n_ledger_dates ?? "—";
    const aCount =
      thr.a_count != null && Number.isFinite(Number(thr.a_count))
        ? Math.round(Number(thr.a_count))
        : A_COUNT_DEFAULT;
    const bCount =
      thr.b_count != null && Number.isFinite(Number(thr.b_count))
        ? Math.round(Number(thr.b_count))
        : B_COUNT_DEFAULT;
    // 旧报告只有 a_hit（命中率）时不沿用，避免误写成 OO_F1≥50%
    const aF1 =
      thr.a_f1 != null && Number.isFinite(Number(thr.a_f1))
        ? hitPctFromRatio(thr.a_f1, A_F1_DEFAULT)
        : A_F1_DEFAULT;
    const tauAF1 =
      thr.tau_a_f1 != null && Number.isFinite(Number(thr.tau_a_f1))
        ? hitPctFromRatio(thr.tau_a_f1, TAU_A_F1_DEFAULT)
        : TAU_A_F1_DEFAULT;
    const requireTau = !!thr.require_tau;
    const requireOoRPos =
      thr.require_oo_r_pos != null
        ? !!thr.require_oo_r_pos
        : REQUIRE_OO_R_POS_DEFAULT;
    const requireTcRPos =
      thr.require_tc_r_pos != null
        ? !!thr.require_tc_r_pos
        : REQUIRE_TC_R_POS_DEFAULT;
    const counts = rep.counts || {};
    const nSample = rep.n_with_sample != null ? rep.n_with_sample : "—";
    const nPool = rep.pool_count != null ? rep.pool_count : (rep.rows || []).length;
    const tierMeta = tierMetaFor(
      aCount,
      bCount,
      aF1,
      tauAF1,
      requireTau,
      requireOoRPos,
      requireTcRPos
    );
    let gateLab = requireTau
      ? `A档 OO_F1≥${aF1}% · TC_F1≥${tauAF1}%`
      : `A档 OO_F1≥${aF1}%`;
    if (requireOoRPos) gateLab += " · OO_R+>0";
    if (requireTcRPos && requireTau) gateLab += " · TC_R+>0";
    const scoreLab = requireTau
      ? "Score=0.5*OO_F1+0.5*TC_F1"
      : "Score=OO_F1";

    const headNote = [
      `ŷ_oo Holdout OOS`,
      `Holdout${hold}日前半` +
        (tierN != null && Number.isFinite(Number(tierN)) ? ` · 窗${tierN}日` : "") +
        ` · OO/TC F1·P·R·ACC 看前半 · 指标与等权买持看整段`,
      scoreLab,
      `A档数量 ${aCount} · B档数量 ${bCount} · ${gateLab} · C 其余`,
      liveBit(),
    ]
      .filter(Boolean)
      .join(" · ");

    const byTier = { A: [], B: [], C: [] };
    for (const r of rep.rows) {
      const t = String(r.tier || "C");
      if (byTier[t]) byTier[t].push(r);
      else byTier.C.push(r);
    }
    const maxShow = 120;
    const sections = ["A", "B", "C"]
      .map((tier) => {
        const meta = tierMeta[tier] || { label: tier, tip: "" };
        const rows = byTier[tier];
        const slice = rows.slice(0, maxShow);
        // 档内相对：A 档整体偏高时不再「全红」
        const f1ScaleOo = buildF1RelScale(rows, "f1_pos");
        const f1ScaleTau = buildF1RelScale(rows, "f1_pos_tau");
        const trs = slice
          .map(
            (r) =>
              `<tr data-tier="${tier}">` +
              stockCell(r) +
              retCell(r) +
              condRetPosCell(r, "oo") +
              rateCell(r.hit_rate, "OO_ACC：Holdout 前半 ŷ_oo 符号命中率", "is-oo") +
              rateCell(r.precision_pos, "OO_P：Holdout 前半 ŷ_oo 上涨精确率 P⁺", "is-oo") +
              rateCell(r.recall_pos, "OO_R：Holdout 前半 ŷ_oo 上涨召回", "is-oo") +
              f1PosCell(r, f1ScaleOo, "oo") +
              condRetPosCell(r, "tau") +
              rateCell(r.hit_rate_tau, "TC_ACC：Holdout 前半 ŷ_τc 符号命中率", "is-tau") +
              rateCell(
                r.precision_pos_tau,
                "TC_P：Holdout 前半 ŷ_τc 上涨精确率 P⁺",
                "is-tau"
              ) +
              rateCell(r.recall_pos_tau, "TC_R：Holdout 前半 ŷ_τc 上涨召回", "is-tau") +
              f1PosCell(r, f1ScaleTau, "tau") +
              scoreCell(r) +
              `</tr>`
          )
          .join("");
        const more =
          rows.length > maxShow
            ? `<p class="quant-ru-list-more">已显示 ${maxShow} / ${rows.length}</p>`
            : "";
        const body = rows.length
          ? `<table class="quant-ru-tier-grid">
              <thead><tr>
                <th class="quant-ru-col-stock">标的</th>
                <th class="num" title="Holdout 整段买持收益%（close 首尾，对齐档内平均收益率）">收益</th>
                <th class="num" title="Holdout 前半 OO_R+=mean(y_oo|ŷ_oo&gt;ε)；模型看多日的平均实现收益；勾选 OO_R+&gt;0 时进 A 档门槛">OO_R+</th>
                <th class="num" title="OO_ACC：Holdout 前半 ŷ_oo 符号命中率；仅展示">OO_ACC</th>
                <th class="num" title="OO_P：Holdout 前半 ŷ_oo 上涨精确率 P⁺；仅展示">OO_P</th>
                <th class="num" title="OO_R：Holdout 前半 ŷ_oo 上涨召回；仅展示">OO_R</th>
                <th class="num" title="OO_F1：Holdout 前半 ŷ_oo 上涨正类调和平均；着色=本档 min-max 相对深浅；进 Rank_Score；默认可作 A 档门槛">OO_F1</th>
                <th class="num" title="Holdout 前半 TC_R+=mean(y_τc|ŷ_τc&gt;ε)；勾选 TC_R+&gt;0 且有 ŷ_τc 研究套时进 A 档门槛">TC_R+</th>
                <th class="num" title="TC_ACC：Holdout 前半 ŷ_τc 符号命中率；仅展示">TC_ACC</th>
                <th class="num" title="TC_P：Holdout 前半 ŷ_τc 上涨精确率 P⁺；仅展示">TC_P</th>
                <th class="num" title="TC_R：Holdout 前半 ŷ_τc 上涨召回；仅展示">TC_R</th>
                <th class="num" title="TC_F1：Holdout 前半 ŷ_τc 上涨正类调和平均；进 Rank_Score；有 ŷ_τc 时默认可作 A 档门槛">TC_F1</th>
                <th class="num" title="Rank_Score = 0.5*OO_F1 + 0.5*TC_F1（无 ŷ_τc 研究套时 =OO_F1；缺一侧按 0）">Rank_Score</th>
              </tr></thead>
              <tbody>${trs}</tbody>
            </table>${more}`
          : `<p class="quant-ru-list-empty">无</p>`;
        return `<details class="quant-ru-tier-fold" data-tier="${tier}">
          <summary class="quant-ru-tier-fold-sum" title="${escapeHtml(meta.tip)}">
            <span class="quant-ru-tier-badge is-${tier}">${tier}</span>
            <span class="quant-ru-tier-label">${escapeHtml(meta.label)}</span>
            ${dualCsHtml(rep, tier)}
          </summary>
          <div class="quant-ru-tier-fold-body">${body}</div>
        </details>`;
      })
      .join("");

    host.hidden = false;
    host.innerHTML =
      `<div class="quant-ru-desk">` +
      `<div class="quant-ru-kpis" aria-label="分档 KPI">` +
      kpiCardHtml(
        "is-A",
        "A 强",
        String(counts.A ?? 0),
        tierMeanRet(rep, "A"),
        "Holdout 整段、A 档等权买持（close，对齐回测观察池A档等权）"
      ) +
      kpiCardHtml(
        "is-B",
        "B 中",
        String(counts.B ?? 0),
        tierMeanRet(rep, "B"),
        "Holdout 整段、B 档等权买持（close，对齐回测基线口径）"
      ) +
      kpiCardHtml(
        "is-C",
        "C 弱",
        String(counts.C ?? 0),
        tierMeanRet(rep, "C"),
        "Holdout 整段、C 档等权买持（close，对齐回测基线口径）"
      ) +
      kpiCardHtml(
        "",
        "有样本",
        `${nSample}<span class="quant-ru-kpi-sub">/${nPool}</span>`,
        poolMeanRet(rep),
        "Holdout 整段、有样本等权买持（按档内票数加权）"
      ) +
      `</div>` +
      mixBarHtml(counts) +
      `<p class="quant-ru-list-note">${escapeHtml(headNote)}</p>` +
      `<div class="quant-ru-tier-folds">${sections}</div>` +
      `</div>`;
    wireF1Tips(host);
  }

  function applyLive(live) {
    liveStatus = live && typeof live === "object" ? live : null;
  }

  function holdoutMismatch(rep) {
    if (!rep || rep.holdout_n == null) return null;
    const last = Number(rep.holdout_n);
    if (!Number.isFinite(last)) return null;
    const cur = holdoutN();
    if (Number(cur) === Number(last)) return null;
    return { cur, last };
  }

  function applyTiers(rep) {
    lastTiers = rep && rep.success ? rep : null;
    if (rep && rep.live) applyLive(rep.live);
    if (lastTiers) paintCountFromReport(lastTiers);
    renderTierTable(lastTiers);
    if (lastTiers) {
      const mm = holdoutMismatch(lastTiers);
      if (mm) {
        setStatus(
          "error",
          "Holdout 不一致",
          `ŷ_oo Holdout=${mm.cur} 与分档 Holdout=${mm.last} 不一致；请重跑「观察池分档」后再回测`
        );
        return;
      }
      const c = lastTiers.counts || {};
      const hold = lastTiers.holdout_n != null ? lastTiers.holdout_n : "—";
      const src =
        lastTiers.source === "oo_research_model"
          ? "研究套"
          : lastTiers.source === "oo_holdout_oos_refit"
            ? "现训"
            : "ŷ_oo";
      setStatus(
        "ok",
        liveStatus && liveStatus.enabled ? "live" : "已分档",
        `A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0} · ${src} · Holdout${hold}前半 · ${liveBit()}`
      );
    } else if (liveStatus && liveStatus.enabled) {
      setStatus("ok", "live", `${liveBit()} · 可先「观察池分档」刷新`);
    }
  }

  async function loadTiers({ useLast = false, quiet = false } = {}) {
    if (!document.getElementById("quant-section-research-universe")) return null;
    const h = holdoutN();
    const lb = ooLookbackN();
    const { aCount, bCount, aF1, tauAF1, requireOoRPos, requireTcRPos } =
      readTierCounts();
    if (!quiet) {
      setStatus(
        "busy",
        useLast ? "读取中" : "分档中",
        useLast
          ? "上次 Holdout OOS 分档…"
          : `研究套 ŷ_oo · Holdout ${h} 日前半…`
      );
    }
    const url = useLast
      ? "/api/quant/research-universe/predictability-tiers/last"
      : `/api/quant/research-universe/predictability-tiers?holdout=${encodeURIComponent(
          h
        )}&lookback=${encodeURIComponent(lb)}&a_count=${encodeURIComponent(
          aCount
        )}&b_count=${encodeURIComponent(
          bCount
        )}&a_f1=${encodeURIComponent(
          (aF1 / 100).toFixed(4)
        )}&tau_a_f1=${encodeURIComponent(
          (tauAF1 / 100).toFixed(4)
        )}&require_oo_r_pos=${requireOoRPos ? "true" : "false"}&require_tc_r_pos=${
          requireTcRPos ? "true" : "false"
        }&min_n=20&head=oo&pool=watching`;
    const { ok, data, error } = await apiFetch(url);
    if (!ok || !data || data.success === false) {
      if (data && data.live) applyLive(data.live);
      if (!quiet) {
        setStatus(
          "error",
          useLast ? "无上次" : "失败",
          String(error || data?.note || data?.detail || data?.error || "分档失败")
        );
      }
      if (!useLast) applyTiers(null);
      else if (data && data.live) applyTiers(null);
      return null;
    }
    applyTiers(data);
    return data;
  }

  on("quant-ru-tiers", "click", (e) => {
    e.preventDefault();
    void loadTiers({ useLast: false });
  });
  on("quant-holdout-days", "change", () => {
    if (lastTiers) applyTiers(lastTiers);
  });
  on("quant-ru-a-count", "change", () => {
    readTierCounts();
  });
  on("quant-ru-b-count", "change", () => {
    readTierCounts();
  });
  on("quant-ru-a-f1", "change", () => {
    readTierCounts();
  });
  on("quant-ru-tau-a-f1", "change", () => {
    readTierCounts();
  });
  on("quant-ru-require-oo-r-pos", "change", () => {
    readTierCounts();
  });
  on("quant-ru-require-tc-r-pos", "change", () => {
    readTierCounts();
  });
  // 首次：若本地有勾选记忆则回填；否则保持 HTML 默认勾选
  (() => {
    const oo = el.requireOoRPos();
    const tc = el.requireTcRPos();
    try {
      if (oo && localStorage.getItem(REQUIRE_OO_R_POS_STORAGE_KEY) != null) {
        oo.checked = readStoredBool(
          REQUIRE_OO_R_POS_STORAGE_KEY,
          REQUIRE_OO_R_POS_DEFAULT
        );
      }
      if (tc && localStorage.getItem(REQUIRE_TC_R_POS_STORAGE_KEY) != null) {
        tc.checked = readStoredBool(
          REQUIRE_TC_R_POS_STORAGE_KEY,
          REQUIRE_TC_R_POS_DEFAULT
        );
      }
    } catch (_) {
      /* ignore */
    }
  })();
  readTierCounts();

  function clampWatchingMaxSize(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 300;
    return Math.max(200, Math.min(1000, Math.round(n)));
  }

  function paintPoolSize(cap) {
    const n = clampWatchingMaxSize(cap);
    const input = el.maxSize();
    const out = el.poolVal();
    if (input) {
      input.value = String(n);
      input.setAttribute("aria-valuenow", String(n));
    }
    if (out) out.value = String(n);
    return n;
  }

  function setPoolStatus(message, isError) {
    const st = el.poolStatus();
    if (!st) return;
    st.textContent = message || "";
    st.classList.toggle("is-error", !!isError);
  }

  function syncPoolOverview(n) {
    const count = Number(n);
    if (!Number.isFinite(count) || count < 0) return;
    try {
      syncOverviewUniverse(count);
    } catch (_) {
      /* overview optional */
    }
  }

  async function hydrateWatchingMaxSize() {
    const input = el.maxSize();
    if (!input) {
      markWatchingPoolLimitReady(readWatchingPoolLimit());
      return;
    }
    try {
      const { ok, data } = await apiFetch("/api/watching/file");
      const watching = data && data.watching;
      const cap = Number((watching && watching.max_size) || (data && data.max_size));
      if (ok && Number.isFinite(cap)) paintPoolSize(cap);
      const wl = watching && watching.watchlist;
      if (ok && Array.isArray(wl)) {
        syncPoolOverview(wl.filter((c) => String(c || "").trim()).length);
      }
    } catch (_) {
      paintPoolSize(input.value || 300);
    } finally {
      // 先就绪再通知：日线/5m 首刷 await ready，避免用 HTML 默认 300 截断满池
      markWatchingPoolLimitReady(readWatchingPoolLimit());
    }
  }

  async function saveWatchingMaxSize() {
    const input = el.maxSize();
    if (!input) return;
    const cap = paintPoolSize(input.value);
    const { ok, data, error } = await apiFetch("/api/watching/max-size", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_size: cap }),
    });
    if (!ok || (data && data.ok === false)) {
      const fail = (data && (data.detail || data.error)) || error || "保存失败";
      const msg = /not found/i.test(String(fail))
        ? "保存接口未加载，请重启 Web"
        : String(fail);
      setPoolStatus(msg, true);
      return;
    }
    const saved = paintPoolSize(data && data.max_size != null ? data.max_size : cap);
    setPoolStatus("", false);
    syncPoolOverview(data && data.count);
    notifyWatchingPoolLimitChanged(saved);
  }

  on("quant-watching-max-size", "input", () => {
    const input = el.maxSize();
    if (input) paintPoolSize(input.value);
  });
  on("quant-watching-max-size", "change", () => {
    void saveWatchingMaxSize();
  });

  if (el.maxSize()) {
    void hydrateWatchingMaxSize().catch(() => {
      markWatchingPoolLimitReady(readWatchingPoolLimit());
    });
  } else {
    markWatchingPoolLimitReady(readWatchingPoolLimit());
  }
  if (document.getElementById("quant-section-research-universe")) {
    void loadTiers({ useLast: true, quiet: true }).catch(() => {});
  }

  return { loadTiers, applyTiers };
}
