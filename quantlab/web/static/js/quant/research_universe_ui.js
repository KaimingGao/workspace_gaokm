/**
 * 观察池分档：Holdout 前 60 日四头猜涨 lift 等权（oo / oo_rank / τc / τc_rank）。
 * 表内：四头 lift / Lift_Score。档标题只留 Holdout Lift_Score 均值。
 * 面板按 lookback 全窗（优先 Feature Lake）；live 档位跟随历史回测「分档」勾选。
 */
import { createScoreTooltipController } from "../score_tooltip.js";
import { syncOverviewUniverse } from "./factor_corr_ui.js";
import {
  markWatchingPoolLimitReady,
  notifyWatchingPoolLimitChanged,
  readWatchingPoolLimit,
} from "./params.js";

export function installResearchUniverseUi(q) {
  const { on, apiFetch, escapeHtml, readFitLookbackDays } = q;
  let lastTiers = null;
  let liveStatus = null;
  const f1Tips = createScoreTooltipController();

  const A_COUNT_STORAGE_KEY = "quant.researchUniverse.aCount";
  const B_COUNT_STORAGE_KEY = "quant.researchUniverse.bCount";
  // v2：与报告解耦后换键，避免旧版把 last.holdout_n=20 写进 localStorage 后卡住
  const HOLDOUT_STORAGE_KEY = "quant.researchUniverse.holdout.v2";
  const A_COUNT_DEFAULT = 100;
  const B_COUNT_DEFAULT = 300;
  const HOLDOUT_DEFAULT = 60;
  const A_COUNT_MIN = 1;
  const A_COUNT_MAX = 2000;
  const B_COUNT_MIN = 0;
  const B_COUNT_MAX = 5000;
  const HOLDOUT_MIN = 2;
  const HOLDOUT_MAX = 90;

  const el = {
    chip: () => document.getElementById("quant-ru-chip"),
    msg: () => document.getElementById("quant-ru-msg"),
    strip: () => document.getElementById("quant-ru-strip"),
    tierTable: () => document.getElementById("quant-ru-tier-table"),
    holdout: () => document.getElementById("quant-ru-holdout"),
    aCount: () => document.getElementById("quant-ru-a-count"),
    bCount: () => document.getElementById("quant-ru-b-count"),
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

  function clampHoldout(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return HOLDOUT_DEFAULT;
    return Math.max(HOLDOUT_MIN, Math.min(HOLDOUT_MAX, Math.round(n)));
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

  /** @returns {{ aCount: number, bCount: number }} */
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
    const aInput = el.aCount();
    const bInput = el.bCount();
    if (aInput && String(aInput.value) !== String(aCount)) aInput.value = String(aCount);
    if (bInput && String(bInput.value) !== String(bCount)) bInput.value = String(bCount);
    writeStoredCount(A_COUNT_STORAGE_KEY, aCount);
    writeStoredCount(B_COUNT_STORAGE_KEY, bCount);
    return { aCount, bCount };
  }

  function paintCountFromReport(rep) {
    const thr = rep && rep.thresholds;
    if (thr) {
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
    }
    // Holdout 是下次定档控件，不跟 last 报告回写；不一致由 holdoutMismatch 提示重跑
  }

  function tierMetaFor(aCount, bCount) {
    return {
      A: { label: "强", tip: `猜涨 lift 降序 top ${aCount}` },
      B: { label: "中", tip: `未进 A 后接着 ${bCount}` },
      C: { label: "弱", tip: "其余或说涨不足 8 次" },
    };
  }

  /** 分档专用 Holdout（与 ŷ_oo 拟合卡独立；默认 60）。 */
  function holdoutN() {
    const input = el.holdout();
    const raw =
      input && input.value !== "" && input.value != null
        ? input.value
        : readStoredCount(HOLDOUT_STORAGE_KEY, HOLDOUT_DEFAULT);
    const n = clampHoldout(raw);
    if (input && String(input.value) !== String(n)) input.value = String(n);
    writeStoredCount(HOLDOUT_STORAGE_KEY, n);
    return n;
  }

  function ooLookbackN() {
    if (typeof readFitLookbackDays === "function") {
      return Math.max(100, Math.min(1000, Number(readFitLookbackDays("oo")) || 120));
    }
    return 120;
  }

  function setStatus(state, chip, message, title) {
    const c = el.chip();
    const m = el.msg();
    const s = el.strip();
    if (c) {
      c.dataset.state = state || "idle";
      c.textContent = chip || "—";
    }
    if (m) {
      m.textContent = message || "";
      m.title = title || message || "";
    }
    if (s) s.classList.toggle("is-busy", state === "busy");
  }

  function ranIso(rep) {
    if (!rep || typeof rep !== "object") return "";
    return String(rep.finished_at || rep.updated_at || rep.saved_at || "").trim();
  }

  /** 上次更新：写入时刻优先，否则结果文件 mtime；本地时区，精确到分。 */
  function fmtRanLocal(iso) {
    const raw = String(iso || "").trim();
    if (!raw) return "";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) {
      return raw.replace("T", " ").replace(/\.\d+.*$/, "").slice(0, 16);
    }
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function paintRan(rep, { keep = false } = {}) {
    const node = document.getElementById("quant-ru-ran");
    if (!node) return;
    const iso = ranIso(rep);
    if (!iso) {
      if (keep) return;
      node.textContent = "上次 —";
      node.title = "尚无更新记录";
      return;
    }
    const text = fmtRanLocal(iso);
    node.textContent = `上次 ${text}`;
    node.title = `上次更新 ${text}`;
  }

  function ymd(v) {
    const s = String(v || "").trim().slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "";
  }

  function tierHeadBit(rep) {
    const thr = (rep && rep.thresholds) || {};
    const extra = (rep && rep.tier_lift) || {};
    const heads = ["oo_rank"];
    if (thr.used_oo || extra.used_oo) heads.push("oo");
    if (thr.used_tc_rank || extra.used_tc_rank) heads.push("τc_rank");
    if (thr.used_tc || extra.used_tc) heads.push("τc");
    const why = [extra.oo_note, extra.tc_rank_note, extra.tc_note]
      .map((s) => String(s || "").trim())
      .filter(Boolean)
      .join("；");
    const short = why.length > 36 ? `${why.slice(0, 36)}…` : why;
    const core = `定档猜涨 ${heads.join(" + ")}`;
    return short ? `${core}（${short}）` : core;
  }

  function tierSpanBit(rep) {
    const thr = (rep && rep.thresholds) || {};
    const dates = Array.isArray(rep && rep.tier_dates) ? rep.tier_dates : [];
    const start = ymd(dates[0]);
    const end = ymd(rep && rep.as_of_tier_last) || ymd(dates[dates.length - 1]);
    const n = rep && rep.tier_n != null ? rep.tier_n : dates.length || thr.tier_window_days || 60;
    const emb = thr.embargo_days != null ? thr.embargo_days : 1;
    const when = start && end ? `${start}…${end}` : "Holdout 前";
    const split = (rep && rep.holdout_split) || {};
    const fitN = split.fit_n != null ? ` · 定档模型拟合 ${split.fit_n} 日` : "";
    return `定档窗 ${when}（${n} 个交易日，交界空 ${emb} 日${fitN}）`;
  }

  function holdoutSpanBit(rep) {
    const split = (rep && rep.holdout_split) || {};
    const evalDates = Array.isArray(split.eval_dates) ? split.eval_dates : [];
    const start = ymd(evalDates[0]);
    const end = ymd(rep && rep.as_of_eval_last) || ymd(evalDates[evalDates.length - 1]);
    const n = rep && rep.holdout_n != null ? rep.holdout_n : evalDates.length || "—";
    const when = start && end ? `${start}…${end}` : `${n} 日`;
    const src =
      rep && rep.source === "oo_research_model"
        ? "标题指标读研究套"
        : rep && rep.source === "oo_pre_holdout_refit"
          ? "无研究套，标题指标用 Holdout 前现训"
          : "标题指标读 ŷ_oo";
    return `Holdout ${when}（${n} 日）只评测，研究套 ŷ_oo / ŷ_τc 未改写 · ${src}`;
  }

  function tierStatusShort(rep) {
    const c = (rep && rep.counts) || {};
    const dates = Array.isArray(rep && rep.tier_dates) ? rep.tier_dates : [];
    const start = ymd(dates[0]);
    const end = ymd(rep && rep.as_of_tier_last) || ymd(dates[dates.length - 1]);
    const when = start && end ? `${start.slice(5)}…${end.slice(5)}` : "";
    return [`A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0}`, when].filter(Boolean).join(" · ");
  }

  function tierStatusLine(rep) {
    const c = (rep && rep.counts) || {};
    const thr = (rep && rep.thresholds) || {};
    const minCalls = thr.min_up_calls != null ? thr.min_up_calls : thr.min_days != null ? thr.min_days : 8;
    return [
      `A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0}`,
      tierSpanBit(rep),
      `猜涨 lift = 说涨精度 − 上涨日比例 · 至少 ${minCalls} 次说涨`,
      tierHeadBit(rep),
      holdoutSpanBit(rep),
      liveBit(),
    ].join(" · ");
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
    const n = Number(v);
    const body = Math.abs(n).toFixed(3);
    if (n > 1e-12) return `+${body}`;
    if (n < -1e-12) return `-${body}`;
    return body;
  }

  function csPackOf(rep, tier) {
    if (!rep || !tier) return null;
    const by = rep.cs_by_tier || {};
    return by[tier] || null;
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

  function tierMeanRet(rep, tier) {
    const pack = csPackOf(rep, tier);
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

  /** Lift_Score / lift 列悬浮卡。 */
  function metricTipHtml(pack) {
    const p = pack && typeof pack === "object" ? pack : {};
    const eyebrow = p.eyebrow != null ? String(p.eyebrow) : "";
    const hero = p.hero != null ? String(p.hero) : "—";
    const badge = p.badge != null ? String(p.badge) : "仅展示";
    const badgeCls = p.ok === true ? "is-mid" : "is-empty";
    const foot = p.foot != null ? String(p.foot) : "定档窗展示，不参与定档";
    const reason = p.reason ? String(p.reason) : "";
    const defs = Array.isArray(p.defs) ? p.defs : [];
    const defsHtml = defs.length
      ? `<dl class="quant-ru-f1-tip-defs">` +
        defs
          .map(
            ([dt, dd]) =>
              `<div><dt>${escapeHtml(String(dt || ""))}</dt>` +
              `<dd>${escapeHtml(String(dd || ""))}</dd></div>`
          )
          .join("") +
        `</dl>`
      : reason
        ? `<p class="quant-ru-f1-tip-reason">${escapeHtml(reason)}</p>`
        : "";
    return (
      `<div class="quant-ru-f1-tip-inner">` +
      `<div class="quant-ru-f1-tip-head">` +
      `<div class="quant-ru-f1-tip-head-main">` +
      `<div class="quant-ru-f1-tip-eyebrow">${escapeHtml(eyebrow)}</div>` +
      `<div class="quant-ru-f1-tip-hero is-rel">${escapeHtml(hero)}</div>` +
      `</div>` +
      `<span class="quant-ru-f1-tip-badge ${badgeCls}">${escapeHtml(badge)}</span>` +
      `</div>` +
      defsHtml +
      `<p class="quant-ru-f1-tip-foot">${escapeHtml(foot)}</p>` +
      `</div>`
    );
  }

  function fmtScore(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(3)}`;
  }

  function scoreCell(r) {
    const sc = r && r.score;
    const missing = sc == null || Number.isNaN(Number(sc));
    const hero = fmtScore(sc);
    const parts = ["oo", "oo_rank", "tc", "tc_rank"]
      .filter((p) => r && r[`lift_${p}`] != null && !Number.isNaN(Number(r[`lift_${p}`])))
      .map((p) => fmtScore(r[`lift_${p}`]));
    const defs = missing
      ? []
      : [
          ["含义", "有样本头（精度 − 基准）等权"],
          parts.length ? ["分头", parts.join(" · ")] : null,
        ].filter(Boolean);
    const tipPack = {
      ok: !missing,
      eyebrow: "Lift_Score · 定档窗",
      hero,
      badge: missing ? "无分数" : "定档用",
      defs,
      reason: missing ? "说涨不足 8 次，或各头都没有 lift" : "",
      foot: missing ? "无分数 → C" : "按此分降序取 A / B",
    };
    return (
      `<td class="num quant-ru-score-cell" data-metric-tip="${escapeHtml(
        JSON.stringify(tipPack)
      )}">` +
      `${hero}` +
      `</td>`
    );
  }

  /** 单头定档窗 lift 列；tip 含精度 / 基准 / 说涨次数。 */
  function headLiftCell(r, prefix, label) {
    const liftVal = r && r[`lift_${prefix}`];
    const missing = liftVal == null || Number.isNaN(Number(liftVal));
    const hero = missing ? "—" : fmtScore(liftVal);
    const n = r && r[`n_up_${prefix}`];
    const isRankHead = prefix === "oo_rank" || prefix === "tc_rank";
    const callTip = isRankHead
      ? "说涨 = 预估高于当日截面中位数"
      : "说涨 = 预估 > 0.05 个百分点";
    const defs = missing
      ? []
      : [
          ["lift", "说涨精度 − 上涨日比例"],
          ["口径", callTip],
          ["精度", fmtHit(r[`precision_${prefix}`])],
          ["基准", fmtHit(r[`base_${prefix}`])],
          [
            "说涨",
            n != null && !Number.isNaN(Number(n))
              ? `${Math.round(Number(n))} 次`
              : "—",
          ],
        ];
    const tipPack = {
      ok: !missing,
      eyebrow: `${label} · 定档窗 lift`,
      hero,
      badge: missing ? "未入分" : "进 Score",
      defs,
      reason: missing ? "说涨不足 8 次，或该头未拟合/软跳过" : "",
      foot: "有样本头等权进 Lift_Score",
    };
    return (
      `<td class="num quant-ru-score-cell" data-metric-tip="${escapeHtml(
        JSON.stringify(tipPack)
      )}">` +
      `${hero}` +
      `</td>`
    );
  }

  function wireMetricTips(host) {
    if (!host) return;
    f1Tips.bindAttrTip(host, {
      selector: "[data-metric-tip]",
      className: "score-tooltip quant-ru-f1-tip",
      wireKey: "ru-metric-tip",
      buildHtml: (node) => {
        try {
          return metricTipHtml(JSON.parse(node.getAttribute("data-metric-tip") || "{}"));
        } catch (_) {
          return "";
        }
      },
    });
  }

  /** 档内有 Holdout Lift_Score 的票，简单平均。无分数的不进分母。 */
  function tierMeanHoldoutScore(rows) {
    let sum = 0;
    let n = 0;
    let seen = false;
    for (const r of rows || []) {
      if (!r || !Object.prototype.hasOwnProperty.call(r, "score_holdout")) continue;
      seen = true;
      const s = r.score_holdout;
      if (s == null || Number.isNaN(Number(s))) continue;
      sum += Number(s);
      n += 1;
    }
    return { mean: n ? sum / n : null, n, seen };
  }

  function tierScoreTitleHtml(rows) {
    const { mean, n, seen } = tierMeanHoldoutScore(rows);
    const total = (rows || []).length;
    const tip = seen
      ? `Holdout Lift_Score 均值（有分数 ${n}/${total}）。` +
        "定档仍用表内 Lift_Score。猜涨 lift = 说涨精度 − 上涨日比例。" +
        "说涨少于 8 次没有 Holdout 分数。收益大小不进分数"
      : "这份报告没有 Holdout Lift_Score。再跑一次「观察池分档」后，这里是 Holdout 猜涨 lift 均值。定档仍用表内 Lift_Score";
    return (
      `<span class="quant-ru-tier-score" title="${escapeHtml(tip)}">` +
      `<span class="quant-ru-tier-cs-lab">Lift_Score</span>` +
      `<span class="quant-ru-tier-cs-val${csValCls(mean)}">${fmtIc(mean)}</span>` +
      `</span>`
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
    const aCount =
      thr.a_count != null && Number.isFinite(Number(thr.a_count))
        ? Math.round(Number(thr.a_count))
        : A_COUNT_DEFAULT;
    const bCount =
      thr.b_count != null && Number.isFinite(Number(thr.b_count))
        ? Math.round(Number(thr.b_count))
        : B_COUNT_DEFAULT;
    const tierMeta = tierMetaFor(aCount, bCount);

    const byTier = { A: [], B: [], C: [] };
    for (const r of rep.rows) {
      const t = String(r.tier || "C");
      if (byTier[t]) byTier[t].push(r);
      else byTier.C.push(r);
    }
    const counts = rep.counts || {
      A: byTier.A.length,
      B: byTier.B.length,
      C: byTier.C.length,
    };
    const nSample = rep.n_with_sample != null ? rep.n_with_sample : "—";
    const nPool =
      rep.pool_count != null ? rep.pool_count : (rep.rows || []).length;
    const maxShow = 120;
    const sections = ["A", "B", "C"]
      .map((tier) => {
        const meta = tierMeta[tier] || { label: tier, tip: "" };
        const rows = byTier[tier].slice().sort((a, b) => {
          const sa =
            a && a.score != null && !Number.isNaN(Number(a.score))
              ? Number(a.score)
              : Number.NEGATIVE_INFINITY;
          const sb =
            b && b.score != null && !Number.isNaN(Number(b.score))
              ? Number(b.score)
              : Number.NEGATIVE_INFINITY;
          if (sb !== sa) return sb - sa;
          return String((a && a.code) || "").localeCompare(String((b && b.code) || ""));
        });
        // A 档名额可到 2000，列表不截断；B/C 仍只展开前 maxShow
        const slice = tier === "A" ? rows : rows.slice(0, maxShow);
        const trs = slice
          .map(
            (r) =>
              `<tr data-tier="${tier}">` +
              stockCell(r) +
              headLiftCell(r, "oo", "oo") +
              headLiftCell(r, "oo_rank", "oo_rank") +
              headLiftCell(r, "tc", "τc") +
              headLiftCell(r, "tc_rank", "τc_rank") +
              scoreCell(r) +
              `</tr>`
          )
          .join("");
        const more =
          tier !== "A" && rows.length > maxShow
            ? `<p class="quant-ru-list-more">已显示 ${maxShow} / ${rows.length}</p>`
            : "";
        const body = rows.length
          ? `<table class="quant-ru-tier-grid">
              <thead><tr>
                <th class="quant-ru-col-stock">标的</th>
                <th class="num" title="定档窗 oo：说涨 = 预估 > 0.05pp；lift = 说涨精度 − 上涨日比例；进 Lift_Score 等权">oo</th>
                <th class="num" title="定档窗 oo_rank：说涨 = 预估高于当日截面中位数；lift = 说涨精度 − 上涨日比例；进 Lift_Score 等权">oo_rank</th>
                <th class="num" title="定档窗 τc：说涨 = 预估 > 0.05pp（与 τc_rank 同预估、不同口径）；进 Lift_Score 等权">τc</th>
                <th class="num" title="定档窗 τc_rank：说涨 = 预估高于当日截面中位数；进 Lift_Score 等权">τc_rank</th>
                <th class="num" title="Lift_Score = 有样本头 lift 等权。收益大小不进分数；按此分降序取 A / B">Lift_Score</th>
              </tr></thead>
              <tbody>${trs}</tbody>
            </table>${more}`
          : `<p class="quant-ru-list-empty">无</p>`;
        return `<details class="quant-ru-tier-fold" data-tier="${tier}">
          <summary class="quant-ru-tier-fold-sum" title="${escapeHtml(meta.tip)}">
            <span class="quant-ru-tier-badge is-${tier}">${tier}</span>
            <span class="quant-ru-tier-label">${escapeHtml(meta.label)}</span>
            ${tierScoreTitleHtml(rows)}
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
      `<div class="quant-ru-tier-folds">${sections}</div>` +
      `</div>`;
    wireMetricTips(host);
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
    if (lastTiers) {
      paintCountFromReport(lastTiers);
      paintRan(lastTiers);
    }
    renderTierTable(lastTiers);
    if (lastTiers) {
      const stale =
        String(lastTiers.protocol || "").trim() !== "tier60_quad_uplift_holdout_eval";
      const mm = holdoutMismatch(lastTiers);
      const missingHold =
        !stale &&
        (lastTiers.holdout_n == null ||
          !Number.isFinite(Number(lastTiers.holdout_n)));
      if (stale || mm || missingHold) {
        const bits = [];
        if (stale) {
          bits.push(
            "这份报告不是四头猜涨 lift 定档。请再点「观察池分档」：当场拟合 oo / oo_rank / τc / τc_rank（不改研究套 ŷ_oo / ŷ_τc），有样本头等权，用说涨精度减去上涨日比例重写 A/B/C"
          );
        }
        if (missingHold) {
          bits.push("报告缺 Holdout 元数据，请重跑「观察池分档」");
        }
        if (mm) {
          bits.push(
            `分档 Holdout=${mm.cur} 与这份报告的 Holdout=${mm.last} 不一致，请重跑「观察池分档」`
          );
        }
        if (!stale) bits.push(tierStatusShort(lastTiers));
        setStatus(
          "error",
          stale ? "需重跑" : missingHold ? "缺 Holdout" : "Holdout 不一致",
          bits.join("。"),
          tierStatusLine(lastTiers)
        );
        return;
      }
      setStatus(
        "ok",
        liveStatus && liveStatus.enabled ? "live" : "已分档",
        tierStatusShort(lastTiers),
        tierStatusLine(lastTiers)
      );
    } else if (liveStatus && liveStatus.enabled) {
      setStatus("ok", "live", `${liveBit()} · 可先「观察池分档」刷新`);
    }
  }

  let tierProgressTimer = null;
  let tierProgressGen = 0;

  function stopTierProgress() {
    tierProgressGen += 1;
    if (tierProgressTimer) {
      clearInterval(tierProgressTimer);
      tierProgressTimer = null;
    }
  }

  function startTierProgress() {
    stopTierProgress();
    const gen = tierProgressGen;
    const pull = async () => {
      if (gen !== tierProgressGen) return;
      try {
        const { ok, data } = await apiFetch(
          "/api/quant/research-universe/predictability-tiers/progress"
        );
        if (gen !== tierProgressGen) return;
        const phase = ok && data ? String(data.phase || "").trim() : "";
        if (!data || !data.running || !phase) return;
        setStatus("busy", "分档中", phase, data.detail || phase);
      } catch (_err) {
        /* 进度接口暂时没有时，保留当前短句 */
      }
    };
    tierProgressTimer = setInterval(pull, 1200);
    void pull();
  }

  async function loadTiers({ useLast = false, quiet = false } = {}) {
    if (!document.getElementById("quant-section-research-universe")) return null;
    const h = holdoutN();
    const lb = ooLookbackN();
    const { aCount, bCount } = readTierCounts();
    if (!quiet) {
      setStatus(
        "busy",
        useLast ? "读取中" : "分档中",
        useLast ? "读取上次分档" : "读取日线",
        useLast ? "" : "不改研究套 ŷ_oo / ŷ_τc"
      );
      if (!useLast) startTierProgress();
    }
    const url = useLast
      ? "/api/quant/research-universe/predictability-tiers/last"
      : `/api/quant/research-universe/predictability-tiers?holdout=${encodeURIComponent(
          h
        )}&lookback=${encodeURIComponent(lb)}&a_count=${encodeURIComponent(
          aCount
        )}&b_count=${encodeURIComponent(
          bCount
        )}&head=oo&pool=watching`;
    try {
      const { ok, data, error } = await apiFetch(url);
      if (!ok || !data || data.success === false) {
        if (data && data.live) applyLive(data.live);
        setStatus(
          "error",
          useLast ? "无上次" : "失败",
          String(error || data?.note || data?.detail || data?.error || "分档失败")
        );
        if (!useLast) applyTiers(null);
        else {
          paintRan(null);
          if (data && data.live) applyTiers(null);
        }
        return null;
      }
      applyTiers(data);
      return data;
    } catch (err) {
      setStatus(
        "error",
        "失败",
        String((err && err.message) || err || "分档渲染失败")
      );
      return null;
    } finally {
      stopTierProgress();
    }
  }

  on("quant-ru-tiers", "click", (e) => {
    e.preventDefault();
    void loadTiers({ useLast: false });
  });
  function wireHoldoutClamp() {
    const input = el.holdout();
    if (!input || input.dataset.rgHoldoutWired === "1") return;
    input.dataset.rgHoldoutWired = "1";
    const sync = () => {
      holdoutN();
      if (lastTiers) applyTiers(lastTiers);
    };
    input.addEventListener("change", sync);
    input.addEventListener("blur", sync);
    // 粘贴/滚轮出界后立刻夹紧，避免脏值入请求
    input.addEventListener("input", () => {
      const raw = input.value;
      if (raw === "" || raw == null) return;
      const n = Number(raw);
      if (!Number.isFinite(n)) return;
      const clamped = clampHoldout(n);
      if (String(input.value) !== String(clamped) && (n < HOLDOUT_MIN || n > HOLDOUT_MAX)) {
        input.value = String(clamped);
      }
    });
  }
  // 旧 on("quant-ru-holdout") 会重复绑；改走 wire
  wireHoldoutClamp();
  on("quant-ru-a-count", "change", () => {
    readTierCounts();
  });
  on("quant-ru-b-count", "change", () => {
    readTierCounts();
  });
  holdoutN();
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
