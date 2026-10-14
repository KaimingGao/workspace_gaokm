/**
 * 观察池分档：研究套 ŷ_oo（+ 有则 ŷ_τc）· Score=1/rank(Y_oo)+1/rank(Y_τc) · A 档另过 Y_oo / Y_τc 命中率 · 分档看 Holdout 前半，指标与等权买持看整段 → last。
 * 有研究套时只拉 Holdout 附近本地日线建面板；live 档位跟随历史回测「分档」勾选。
 */
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

  const A_COUNT_STORAGE_KEY = "quant.researchUniverse.aCount";
  const B_COUNT_STORAGE_KEY = "quant.researchUniverse.bCount";
  const A_HIT_STORAGE_KEY = "quant.researchUniverse.aHit";
  const TAU_A_HIT_STORAGE_KEY = "quant.researchUniverse.tauAHit";
  const A_COUNT_DEFAULT = 100;
  const B_COUNT_DEFAULT = 300;
  const A_HIT_DEFAULT = 50;
  const TAU_A_HIT_DEFAULT = 50;
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
    aHit: () => document.getElementById("quant-ru-a-hit"),
    tauAHit: () => document.getElementById("quant-ru-tau-a-hit"),
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

  /** @returns {{ aCount: number, bCount: number, aHit: number, tauAHit: number }} */
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
    const aHit = clampHitPct(
      el.aHit()?.value !== "" && el.aHit()?.value != null
        ? el.aHit().value
        : readStoredCount(A_HIT_STORAGE_KEY, A_HIT_DEFAULT),
      A_HIT_DEFAULT
    );
    const tauAHit = clampHitPct(
      el.tauAHit()?.value !== "" && el.tauAHit()?.value != null
        ? el.tauAHit().value
        : readStoredCount(TAU_A_HIT_STORAGE_KEY, TAU_A_HIT_DEFAULT),
      TAU_A_HIT_DEFAULT
    );
    const aInput = el.aCount();
    const bInput = el.bCount();
    const aHitInput = el.aHit();
    const tauHitInput = el.tauAHit();
    if (aInput && String(aInput.value) !== String(aCount)) aInput.value = String(aCount);
    if (bInput && String(bInput.value) !== String(bCount)) bInput.value = String(bCount);
    if (aHitInput && String(aHitInput.value) !== String(aHit)) aHitInput.value = String(aHit);
    if (tauHitInput && String(tauHitInput.value) !== String(tauAHit)) {
      tauHitInput.value = String(tauAHit);
    }
    writeStoredCount(A_COUNT_STORAGE_KEY, aCount);
    writeStoredCount(B_COUNT_STORAGE_KEY, bCount);
    writeStoredCount(A_HIT_STORAGE_KEY, aHit);
    writeStoredCount(TAU_A_HIT_STORAGE_KEY, tauAHit);
    return { aCount, bCount, aHit, tauAHit };
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
    if (thr.a_hit != null && Number.isFinite(Number(thr.a_hit))) {
      const aHit = hitPctFromRatio(thr.a_hit, A_HIT_DEFAULT);
      const input = el.aHit();
      if (input) input.value = String(aHit);
      writeStoredCount(A_HIT_STORAGE_KEY, aHit);
    }
    if (thr.tau_a_hit != null && Number.isFinite(Number(thr.tau_a_hit))) {
      const tauAHit = hitPctFromRatio(thr.tau_a_hit, TAU_A_HIT_DEFAULT);
      const input = el.tauAHit();
      if (input) input.value = String(tauAHit);
      writeStoredCount(TAU_A_HIT_STORAGE_KEY, tauAHit);
    }
  }

  function tierMetaFor(aCount, bCount, aHit, tauAHit, requireTau) {
    const gate = requireTau
      ? `Y_oo≥${aHit}% 且 Y_τc≥${tauAHit}%`
      : `Y_oo≥${aHit}%`;
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

  function hitTone(hitRate) {
    if (hitRate == null || Number.isNaN(Number(hitRate))) return "is-empty";
    const pct = Number(hitRate) * 100;
    if (pct + 1e-9 >= 60) return "is-a";
    if (pct + 1e-9 >= 50) return "is-b";
    return "is-c";
  }

  function hitBarHtml(hitRate) {
    const tone = hitTone(hitRate);
    const pct =
      tone === "is-empty"
        ? 0
        : Math.max(0, Math.min(100, Number(hitRate) * 100));
    const tip =
      tone === "is-empty" ? "无有效命中" : `命中 ${pct.toFixed(1)}%`;
    return (
      `<span class="quant-ru-hit-meter ${tone}" title="${escapeHtml(tip)}">` +
      `<span class="quant-ru-hit-track">` +
      `<span class="quant-ru-hit-fill" style="width:${pct.toFixed(1)}%"></span>` +
      `</span>` +
      `</span>`
    );
  }

  function hitNCell(hitRate, nValid, title, headCls) {
    const tone = hitTone(hitRate);
    const n = nValid == null || Number.isNaN(Number(nValid)) ? 0 : Number(nValid);
    return (
      `<td class="num quant-ru-hit-cell ${escapeHtml(headCls || "")}" title="${escapeHtml(
        title || ""
      )}">` +
      `<span class="quant-ru-hit-row">` +
      `<span class="quant-ru-hit-val ${tone}">${fmtHit(hitRate)}</span>` +
      `<span class="quant-ru-hit-n">${n}</span>` +
      `</span>` +
      hitBarHtml(hitRate) +
      `</td>`
    );
  }

  function fmtScore(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(3);
  }

  function scoreCell(r) {
    const sc = r && r.score;
    const tipParts = [
      sc != null && !Number.isNaN(Number(sc)) ? `Score ${fmtScore(sc)}` : "无 Score",
      r && r.rank_oo != null ? `rank(Y_oo)=${r.rank_oo}` : "",
      r && r.rank_tau != null ? `rank(Y_τc)=${r.rank_tau}` : "",
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

  function condRetCell(r, side) {
    const isPos = side === "pos";
    const v = r && (isPos ? r.mean_y_pos : r.mean_y_neg);
    const n = v == null ? NaN : Number(v);
    const nSide = Number((r && (isPos ? r.n_y_pos : r.n_y_neg)) || 0);
    const nValid = Number((r && r.n_valid) || 0);
    const label = isPos ? "r⁺" : "r⁻";
    const formula = isPos
      ? "mean(y_oo|ŷ_oo>ε)"
      : "mean(y_oo|ŷ_oo<-ε)";
    const emptyLab = isPos ? "无看多" : "无看空";
    let tip;
    let text;
    let cls = "";
    if (Number.isFinite(n)) {
      tip =
        `Holdout 前半 ${label}=${formula} ${fmtRetPct(n)}` +
        (nSide > 0 ? ` · ${nSide} 日` : "");
      text = fmtRetPct(n);
      cls = csValCls(n);
    } else if (nValid > 0) {
      tip = isPos
        ? `Holdout 前半无 ŷ_oo>ε 日（${nValid} 个有效命中日均为看空）· 无法算 ${label}`
        : `Holdout 前半无 ŷ_oo<-ε 日（${nValid} 个有效命中日均为看多）· 无法算 ${label}`;
      text = emptyLab;
      cls = " is-muted";
    } else {
      tip = `Holdout 前半无有效 ŷ_oo 样本 · 无法算 ${label}`;
      text = "—";
    }
    return (
      `<td class="num quant-ru-ret-cell${cls}" title="${escapeHtml(tip)}">` +
      `${text}` +
      `</td>`
    );
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
    const aHit =
      thr.a_hit != null && Number.isFinite(Number(thr.a_hit))
        ? hitPctFromRatio(thr.a_hit, A_HIT_DEFAULT)
        : A_HIT_DEFAULT;
    const tauAHit =
      thr.tau_a_hit != null && Number.isFinite(Number(thr.tau_a_hit))
        ? hitPctFromRatio(thr.tau_a_hit, TAU_A_HIT_DEFAULT)
        : TAU_A_HIT_DEFAULT;
    const requireTau = !!thr.require_tau;
    const counts = rep.counts || {};
    const nSample = rep.n_with_sample != null ? rep.n_with_sample : "—";
    const nPool = rep.pool_count != null ? rep.pool_count : (rep.rows || []).length;
    const tierMeta = tierMetaFor(aCount, bCount, aHit, tauAHit, requireTau);
    const gateLab = requireTau
      ? `A档 Y_oo≥${aHit}% · Y_τc≥${tauAHit}%`
      : `A档 Y_oo≥${aHit}%`;
    const scoreLab = requireTau
      ? "Score=1/rank(Y_oo)+1/rank(Y_τc)"
      : "Score=1/rank(Y_oo)";

    const headNote = [
      `ŷ_oo Holdout OOS`,
      `Holdout${hold}日前半` +
        (tierN != null && Number.isFinite(Number(tierN)) ? ` · 窗${tierN}日` : "") +
        ` · r⁺/r⁻看前半 · 指标与等权买持看整段`,
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
        const trs = slice
          .map(
            (r) =>
              `<tr data-tier="${tier}">` +
              stockCell(r) +
              retCell(r) +
              condRetCell(r, "pos") +
              condRetCell(r, "neg") +
              scoreCell(r) +
              hitNCell(
                r.hit_rate,
                r.n_valid,
                "Y_oo 命中率 · 有效样本日",
                "is-oo"
              ) +
              hitNCell(
                r.hit_rate_tau,
                r.n_valid_tau,
                "Y_τc 命中率 · 有效样本日",
                "is-tau"
              ) +
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
                <th class="num" title="Holdout 前半 r⁺=mean(y_oo|ŷ_oo&gt;ε)；模型看多日的平均实现收益">r⁺</th>
                <th class="num" title="Holdout 前半 r⁻=mean(y_oo|ŷ_oo&lt;-ε)；模型看空日的平均实现收益">r⁻</th>
                <th class="num" title="Score = 1/rank(Y_oo) + 1/rank(Y_τc)">Score</th>
                <th class="num" title="Y_oo 命中率 · 有效样本日">Y_oo</th>
                <th class="num" title="Y_τc 命中率 · 有效样本日">Y_τc</th>
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
    const { aCount, bCount, aHit, tauAHit } = readTierCounts();
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
        )}&a_hit=${encodeURIComponent(
          (aHit / 100).toFixed(4)
        )}&tau_a_hit=${encodeURIComponent(
          (tauAHit / 100).toFixed(4)
        )}&min_n=20&head=oo&pool=watching`;
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
  on("quant-ru-a-hit", "change", () => {
    readTierCounts();
  });
  on("quant-ru-tau-a-hit", "change", () => {
    readTierCounts();
  });
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
