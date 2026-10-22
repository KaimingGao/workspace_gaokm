import { setProStatusChip, syncOverviewBarsCoverage } from "./factor_corr_ui.js";
import {
  fmtDuration,
  fmtAgeSec,
  jobTimings,
  paintClusterJobPanel,
  formatClusterJobFootLine,
  jobStatusBadge,
  unwrapJobSnap,
  setDeskStatusTone,
} from "./cluster_job_ui.js";
import {
  BARS_DAILY_LOOKBACK,
  BARS_WATCHING_LIMIT,
  readWatchingPoolLimit,
  awaitWatchingPoolLimitReady,
  WATCHING_POOL_LIMIT_CHANGED,
} from "./params.js";
/** 研究枢纽 · 观察池日 K 覆盖 + 「增量补齐（L0）」/「强更日 K（L0）」 */
export function installBarsUi(q) {
  const { on, apiFetch, escapeHtml } = q;
  const chip = document.getElementById("quant-bars-chip");
  const msgEl = document.getElementById("quant-bars-msg");
  const btn = document.getElementById("quant-bars-refresh");
  const topupBtn = document.getElementById("quant-bars-topup");
  const kpiAlign = document.getElementById("quant-bars-kpi-align");
  const kpiAlignSub = document.getElementById("quant-bars-kpi-align-sub");
  const kpiExpected = document.getElementById("quant-bars-kpi-expected");
  const kpiExpectedSub = document.getElementById("quant-bars-kpi-expected-sub");
  const kpiStale = document.getElementById("quant-bars-kpi-stale");
  const kpiMissing = document.getElementById("quant-bars-kpi-missing");
  const covPct = document.getElementById("quant-bars-cov-pct");
  const covBody = document.getElementById("quant-bars-cov-body");
  const distHost = document.getElementById("quant-bars-dist");
  const distMeta = document.getElementById("quant-bars-dist-meta");
  const contextStrip = document.getElementById("quant-bars-context-strip");
  const progressEl = document.getElementById("quant-bars-progress");
  const footEl = document.getElementById("quant-bars-foot");
  const diffMeta = document.getElementById("quant-bars-diff-meta");
  const diffPrice = document.getElementById("quant-bars-diff-price");
  const diffVolume = document.getElementById("quant-bars-diff-volume");
  const diffTail = document.getElementById("quant-bars-diff-tail");
  const diffSame = document.getElementById("quant-bars-diff-same");
  const diffRows = document.getElementById("quant-bars-diff-rows");
  const diffNote = document.getElementById("quant-bars-diff-note");
  const diffKpis = document.getElementById("quant-bars-diff-kpis");
  const kpiHost = document.getElementById("quant-bars-kpis");
  const barsCard =
    document.querySelector("#quant-section-bars .quant-bars-panel-l0") ||
    document.querySelector(".quant-bars-card:not(.quant-minute-card)");
  const barsBody = barsCard?.querySelector(".quant-bars-body");
  const barsStrip = document.getElementById("quant-bars-strip");
  const matCard = document.querySelector(".quant-bars-mat-card");
  const matBody = matCard?.querySelector(".quant-bars-mat-body");
  const matStrip = document.getElementById("quant-materialize-strip");

  if (!chip || !msgEl || !btn) {
    return { refreshStatus: async () => {}, startRefresh: async () => {}, startTopup: async () => {} };
  }

  const matBtn = document.getElementById("quant-materialize-run");
  let matBusy = false;
  const installMat =
    typeof q.installMaterializeUi === "function" ? q.installMaterializeUi : null;
  const matUi = installMat
    ? installMat(q, {
        onStart: () => {
          jobFailure = null;
          jobSuccess = null;
          setDeskStatusTone([matStrip, matCard], "warn");
        },
        onBusy: (on) => {
          matBusy = !!on;
          if (on) {
            setDeskStatusTone([matStrip, matCard], "warn");
            matCard?.classList.add("is-busy");
            matStrip?.classList.add("is-busy");
            matBody?.classList.add("is-busy");
          }
        },
        onSettled: () => {
          matBusy = false;
          setDeskStatusTone([matStrip, matCard], "warn");
          matCard?.classList.remove("is-busy");
          matStrip?.classList.remove("is-busy");
          matBody?.classList.remove("is-busy");
        },
      })
    : { refresh: () => {}, run: async () => {}, resumeIfRunning: async () => false };

  function setBarsButtonsDisabled(disabled) {
    btn.disabled = !!disabled;
    if (topupBtn) topupBtn.disabled = !!disabled;
    if (disabled) {
      if (matBtn) matBtn.disabled = true;
    } else if (!matBusy && matBtn) {
      matBtn.disabled = false;
    }
  }

  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s ?? "");

  let inflight = null;
  let jobFailure = null;
  let jobSuccess = null;
  let lastPolledJob = null;
  let lastStatusJob = null;
  let activeMode = "topup";

  function clearJobPanels() {
    jobFailure = null;
    jobSuccess = null;
  }

  function isTopupSummary(sum) {
    return (
      String(sum?.mode || "") === "topup" ||
      sum?.force_latest === true ||
      (sum?.full_window !== true && String(sum?.note || "").includes("增量"))
    );
  }

  function modeLabel(modeOrSum) {
    if (typeof modeOrSum === "string") {
      return modeOrSum === "topup" ? "增量补齐（L0）" : "强更日 K（L0）";
    }
    return isTopupSummary(modeOrSum) ? "增量补齐（L0）" : "强更日 K（L0）";
  }

  function parseBarsProgressMessage(raw) {
    const msg = String(raw || "");
    const out = {
      raw: msg,
      phase: "fetch",
      done: null,
      total: null,
      remote: null,
      cache: null,
      failed: null,
      elapsedSec: null,
      remainSec: null,
      workers: null,
      capSec: null,
      timedOut: /超时/.test(msg),
    };
    if (/排队|提交/.test(msg)) out.phase = "queue";
    else if (/指数/.test(msg)) out.phase = "index";
    else if (/超时收尾/.test(msg)) out.phase = "timeout";
    else if (/已齐 as-of/.test(msg)) out.phase = "done";
    else if (/完成|已对齐|Coverage/.test(msg) && !/拉日线/.test(msg)) out.phase = "done";
    else if (/拉日线缺口/.test(msg)) out.phase = "gap";
    else if (/拉日线\s*0\//.test(msg) && /上限|并发/.test(msg)) out.phase = "start";

    const frac = msg.match(/(\d+)\s*\/\s*(\d+)/);
    if (frac) {
      out.done = Number(frac[1]);
      out.total = Number(frac[2]);
    }
    const remote = msg.match(/远端\s*(\d+)/);
    if (remote) out.remote = Number(remote[1]);
    const cache = msg.match(/(?:缓存|已齐)\s*(\d+)/);
    if (cache) out.cache = Number(cache[1]);
    const failed = msg.match(/失败\s*(\d+)/);
    if (failed) out.failed = Number(failed[1]);
    const elapsed = msg.match(/(\d+)\s*s(?!\s*·)/) || msg.match(/·\s*(\d+)s/);
    const elapsed2 = msg.match(/(\d+)s\s*·/);
    if (elapsed2) out.elapsedSec = Number(elapsed2[1]);
    else if (elapsed) out.elapsedSec = Number(elapsed[1]);
    const remain = msg.match(/剩\s*(\d+)s/);
    if (remain) out.remainSec = Number(remain[1]);
    const workers = msg.match(/(\d+)\s*并发/);
    if (workers) out.workers = Number(workers[1]);
    const cap = msg.match(/上限\s*(\d+)s/);
    if (cap) out.capSec = Number(cap[1]);
    return out;
  }

  function phaseLabel(phase) {
    switch (phase) {
      case "queue":
        return "排队";
      case "index":
        return "拉指数";
      case "start":
        return "启动";
      case "gap":
        return "补缺口";
      case "timeout":
        return "超时收尾";
      case "done":
        return "完成";
      default:
        return "拉日线";
    }
  }

  function clearProgressPanel() {
    if (!progressEl) return;
    progressEl.hidden = true;
    progressEl.innerHTML = "";
    progressEl.classList.remove("is-active", "is-warn", "is-done");
    delete progressEl.dataset.tone;
  }

  function barsParsedFacts(parsed, pollStartedAt) {
    const wallSec = pollStartedAt ? (Date.now() - pollStartedAt) / 1000 : parsed.elapsedSec;
    const elapsed = parsed.elapsedSec != null ? parsed.elapsedSec : wallSec;
    let etaSec = null;
    const done = parsed.done;
    const total = parsed.total;
    if (done > 0 && total > done && elapsed > 2) {
      etaSec = (total - done) * (elapsed / done);
    } else if (parsed.remainSec != null) {
      etaSec = parsed.remainSec;
    }
    const remote = parsed.remote;
    const cache =
      parsed.cache != null
        ? parsed.cache
        : remote != null && done != null && parsed.phase !== "gap"
          ? Math.max(0, done - remote)
          : null;
    const facts = [];
    if (remote != null) facts.push(`远端 <strong>${remote}</strong>`);
    // 0/N 启动瞬间 done=0 会算出「缓存命中 0」，像没吃到本地仓；有完成票再报
    if (cache != null && Number(done) > 0) {
      facts.push(
        parsed.phase === "gap"
          ? `已齐 <strong>${cache}</strong>`
          : `缓存命中 <strong>${cache}</strong>`
      );
    }
    if (parsed.failed != null && Number(parsed.failed) > 0) {
      facts.push(`失败 <strong>${parsed.failed}</strong>`);
    }
    if (etaSec != null && parsed.phase !== "timeout") {
      facts.push(`ETA <strong>~${fmtDuration(etaSec)}</strong>`);
    }
    if (parsed.remainSec != null) facts.push(`批上限剩 <strong>${fmtDuration(parsed.remainSec)}</strong>`);
    if (parsed.workers != null) facts.push(`并发 <strong>${parsed.workers}</strong>`);
    if (parsed.capSec != null) facts.push(`批上限 <strong>${fmtDuration(parsed.capSec)}</strong>`);
    return facts;
  }

  function paintProgressFailed(job, errText) {
    setDeskStatusTone([barsStrip, barsCard], "primary");
    paintClusterJobPanel(progressEl, {
      phase: "更新失败",
      job,
      esc,
      mode: "fail",
      tone: "primary",
      errText,
      hint: "可再次点击「增量补齐（L0）」或「强更日 K（L0）」重试",
    });
  }

  function paintProgressDone(job) {
    const sum = job?.result_summary || lastPolledJob?.result?.bars_refresh || {};
    const bits = [];
    if (sum.remote_count != null) bits.push(`远端 ${sum.remote_count}/${sum.total ?? "—"}`);
    if (sum.gap_count != null) bits.push(`缺口 ${sum.gap_count}`);
    if (sum.cache_count != null) bits.push(`已齐 ${sum.cache_count}`);
    if (Number(sum.failed_count) > 0) bits.push(`失败 ${sum.failed_count}`);
    const topup = isTopupSummary(sum);
    setDeskStatusTone([barsStrip, barsCard], "primary");
    paintClusterJobPanel(progressEl, {
      phase: topup ? "增量完成" : "强更完成",
      job,
      esc,
      mode: "done",
      tone: "primary",
      hint: bits.length
        ? bits.join(" · ")
        : topup
          ? "只拉未齐 as-of · 进程池并行 · 单票 60s"
          : "整窗替换 · 进程池并行 · 单票 60s",
    });
  }

  function applyJobSuccess(job) {
    jobSuccess = { job: job || null };
    jobFailure = null;
    setDeskStatusTone([barsStrip, barsCard], "primary");
    setProStatusChip(chip, "ok", "DONE");
    const sum = job?.result_summary || job?.result?.bars_refresh || {};
    const head = [isTopupSummary(sum) ? "增量完成" : "强更完成"];
    if (sum.remote_count != null) head.push(`远端 ${sum.remote_count}/${sum.total ?? "—"}`);
    const diff = sum.diff;
    if (diff && diff.price_count != null) {
      head.push(`价变 ${diff.price_count}`);
      head.push(`量纲 ${diff.volume_count ?? 0}`);
      head.push(`只补尾 ${diff.tail_count ?? 0}`);
    }
    const updated = Number(job?.updated_at);
    if (updated > 0) head.push(fmtAgeSec(Date.now() / 1000 - updated));
    msgEl.textContent = head.join(" · ");
    paintProgressDone(job);
    barsCard?.classList.remove("is-busy");
    barsStrip?.classList.remove("is-busy");
    barsBody?.classList.remove("is-busy");
    btn?.classList.remove("is-busy");
    topupBtn?.classList.remove("is-busy");
  }

  function applyJobFailure(err, job) {
    jobSuccess = null;
    const errText = String((err && err.message) || err || "日 K 更新失败");
    jobFailure = { errText, job: job || null };
    setDeskStatusTone([barsStrip, barsCard], "primary");
    setProStatusChip(chip, "error", "FAIL");
    const head = ["更新失败"];
    const j = jobFailure.job;
    if (j && j.current > 0 && j.total > 0) head.push(`${j.current}/${j.total}`);
    head.push(errText);
    msgEl.textContent = head.join(" · ");
    paintProgressFailed(j, errText);
    barsCard?.classList.remove("is-busy");
    barsStrip?.classList.remove("is-busy");
    barsBody?.classList.remove("is-busy");
    btn?.classList.remove("is-busy");
    topupBtn?.classList.remove("is-busy");
  }

  function paintProgressPanel(job, { pollStartedAt, mode } = {}) {
    if (!progressEl) return;
    const topup =
      mode === "topup" ||
      isTopupSummary(job?.result_summary || job?.result?.bars_refresh);
    const defaultMsg = topup ? "增量补齐（L0）…" : "强更日 K（L0）…";
    const msg = job && (job.message || job.error) ? String(job.message || job.error) : defaultMsg;
    const parsed = parseBarsProgressMessage(msg);
    setDeskStatusTone([barsStrip, barsCard], "primary");
    paintClusterJobPanel(progressEl, {
      phase: phaseLabel(parsed.phase),
      job,
      pollStartedAt,
      esc,
      mode: "running",
      tone: "primary",
      hint: topup
        ? "已齐 as-of 走本地 · 缺口进程池并行 · 单票 60s"
        : "整窗替换 · 进程池并行 · 单票 60s",
      extraFacts: barsParsedFacts(parsed, pollStartedAt),
    });
  }

  function setBarsBusy(busy) {
    const on = !!busy;
    // 物化已独立板块：L0 busy 只跟日 K 任务，不被 matBusy 连带
    barsCard?.classList.toggle("is-busy", on);
    barsStrip?.classList.toggle("is-busy", on);
    barsBody?.classList.toggle("is-busy", on);
    btn?.classList.toggle("is-busy", on);
    topupBtn?.classList.toggle("is-busy", on);
    if (on) setDeskStatusTone([barsStrip, barsCard], "primary");
    if (!on && !jobFailure && !jobSuccess) {
      clearProgressPanel();
      setDeskStatusTone([barsStrip, barsCard], null);
    }
  }

  function setKpiCard(key, state) {
    if (!kpiHost) return;
    const card = kpiHost.querySelector(`[data-bars-kpi="${key}"]`);
    if (!card) return;
    card.classList.remove("is-good", "is-bad", "is-mid", "is-empty");
    if (state) card.classList.add(state);
  }

  function fmtUtcShort(iso) {
    if (!iso) return "";
    return String(iso).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
  }

  function backendLabel(raw) {
    const b = String(raw || "").trim().toLowerCase();
    if (b === "sqlite") return "SQLite WAL";
    if (b === "json") return "JSON files";
    return b || "—";
  }

  function formatStatus(data) {
    if (!data || !data.success) {
      return { state: "error", chip: "ERR", msg: "无法读取日 K 覆盖状态" };
    }
    const total = data.universe_count ?? 0;
    const at = data.at_expected ?? 0;
    const stale = data.stale ?? 0;
    const missing = data.missing ?? 0;
    const expected = data.expected_latest_bar || "—";
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;
    const parts = [`Coverage ${pct}% (${at}/${total})`, `as-of ${expected}`];
    if (stale > 0) parts.push(`Stale ${stale}`);
    if (missing > 0) parts.push(`Missing ${missing}`);
    const marker = data.forced_marker || {};
    if (marker.session_date && marker.saved_at) {
      parts.push(`Force sync ${fmtUtcShort(marker.saved_at)} UTC`);
    } else if (data.needs_force_latest_bars) {
      parts.push("本会话未强更");
    }
    let state = "ok";
    let chipText = "SYNCED";
    if (total <= 0) {
      state = "warn";
      chipText = "EMPTY";
    } else if (!data.coverage_ok || data.needs_force_latest_bars) {
      state = "warn";
      chipText = stale || missing ? "GAP" : "PENDING";
    }
    return { state, chip: chipText, msg: parts.join(" · "), pct, total, at, stale, missing, expected };
  }

  function barRowsHead(left, mid, right) {
    return `<div class="quant-bars-bar-head" aria-hidden="true">
      <span>${esc(left)}</span><span>${esc(mid)}</span><span>${esc(right)}</span>
    </div>`;
  }

  function renderBarRows(host, rows, { emptyText = "—", head = null } = {}) {
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = `${head || ""}<p class="quant-bars-viz-empty">${esc(emptyText)}</p>`;
      return;
    }
    // 柱长按观察池占比（universe），不是相对最高柱——52% / 48% 视觉上就对半
    const uniFromRow = Math.max(
      0,
      ...rows.map((r) => Number(r.universeTotal) || 0)
    );
    const sumCounts = rows.reduce((s, r) => s + (Number(r.count) || 0), 0);
    const denom = uniFromRow > 0 ? uniFromRow : Math.max(sumCounts, 1);
    const body = rows
      .map((row) => {
        const n = Number(row.count) || 0;
        const share = denom > 0 ? (100 * n) / denom : 0;
        // 极小占比仍可见一点，但不扭曲大块比例（≥8% 严格按比例）
        const w = n <= 0 ? 0 : share >= 8 ? share : Math.max(2.5, share);
        const label = String(row.label || "—");
        const state = row.state ? ` ${row.state}` : "";
        const pctOfUni =
          denom > 0 ? `${Math.round((1000 * n) / denom) / 10}%` : "";
        const tip = `${row.title || label} · ${n}/${denom} (${pctOfUni})`;
        return `<div class="quant-bars-bar-row${state}" title="${esc(tip)}">
          <span class="quant-bars-bar-label">${esc(label)}</span>
          <span class="quant-bars-bar-wrap" aria-hidden="true">
            <span class="quant-bars-bar-fill" style="width:${w.toFixed(2)}%"></span>
          </span>
          <span class="quant-bars-bar-n">${n}${pctOfUni ? `<span class="quant-bars-bar-pct">${esc(pctOfUni)}</span>` : ""}</span>
        </div>`;
      })
      .join("");
    host.innerHTML = `${head || ""}${body}`;
  }

  function renderContextStrip(data) {
    if (!contextStrip) return;
    if (!data || !data.success) {
      contextStrip.innerHTML = "";
      contextStrip.className = "quant-bars-context-strip is-empty";
      return;
    }
    const total = Number(data.universe_count) || 0;
    const expected = data.expected_latest_bar || "—";
    const session = data.session_date || "—";
    const limit = data.watching_limit ?? "—";
    const backend = backendLabel(data.bars_backend);
    const span = Number(data.last_bar_span_days) || 0;
    const min = data.last_bar_min || "";
    const max = data.last_bar_max || "";
    let spanText = "—";
    if (min && max) {
      spanText = min === max ? min : `${min} → ${max}`;
      if (span > 0) spanText += ` · Δ${span}d`;
    }

    const badges = [];
    if (total <= 0) {
      badges.push('<span class="quant-bars-badge is-warn">UNIVERSE EMPTY</span>');
    } else if (data.coverage_ok && data.last_bar_aligned) {
      badges.push('<span class="quant-bars-badge is-ok">AS-OF OK</span>');
    } else {
      badges.push('<span class="quant-bars-badge is-warn">AS-OF GAP</span>');
    }
    const marker = data.forced_marker || {};
    if (marker.saved_at) {
      badges.push(
        `<span class="quant-bars-badge is-mid" title="远端 ${esc(marker.remote_count ?? "—")}/${esc(marker.total ?? "—")}">FORCE ${esc(fmtUtcShort(marker.saved_at))}</span>`
      );
    } else if (data.needs_force_latest_bars) {
      badges.push('<span class="quant-bars-badge is-warn">NO FORCE SYNC</span>');
    }
    badges.push(`<span class="quant-bars-badge is-neutral">${esc(backend)}</span>`);
    const jobBadge = jobStatusBadge(lastStatusJob);
    if (jobBadge) {
      badges.push(`<span class="quant-bars-badge ${jobBadge.cls}">${esc(jobBadge.text)}</span>`);
    }

    contextStrip.className = `quant-bars-context-strip${
      data.coverage_ok && data.last_bar_aligned ? " is-synced" : " is-gap"
    }`;
    contextStrip.innerHTML = `<div class="quant-bars-strip">
      <div class="quant-bars-strip-brand">
        <span class="quant-bars-strip-eyebrow">Daily bars</span>
        <span class="quant-bars-strip-title">日 K 仓</span>
      </div>
      <dl class="quant-bars-strip-facts">
        <div><dt>Universe</dt><dd>watching · Limit ${esc(limit)} · ${total} 只</dd></div>
        <div><dt>As-of</dt><dd>${esc(expected)} · 会话 ${esc(session)}</dd></div>
        <div><dt>Span</dt><dd>${esc(spanText)}</dd></div>
      </dl>
      <div class="quant-bars-strip-badges">${badges.join("")}</div>
    </div>`;
  }

  function renderCoverageBar(data) {
    const total = Math.max(0, Number(data.universe_count) || 0);
    const at = Number(data.at_expected) || 0;
    const stale = Number(data.stale) || 0;
    const missing = Number(data.missing) || 0;
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;
    if (covPct) covPct.textContent = total > 0 ? `${pct}% aligned` : "—";

    const head = barRowsHead("Status", "Share", "N");
    if (total <= 0) {
      renderBarRows(covBody, [], { emptyText: "观察池为空", head });
      return;
    }

    const rows = [
      {
        label: "Aligned",
        count: at,
        state: "is-ok",
        title: `Aligned ${at}`,
        universeTotal: total,
      },
      {
        label: "Stale",
        count: stale,
        state: "is-warn",
        title: `Stale ${stale}`,
        universeTotal: total,
      },
      {
        label: "Missing",
        count: missing,
        state: "is-bad",
        title: `Missing ${missing}`,
        universeTotal: total,
      },
    ].filter((row) => row.count > 0);
    renderBarRows(covBody, rows, { emptyText: "无覆盖数据", head });
  }

  function renderDistribution(data) {
    if (!distHost) return;
    const expected = data.expected_latest_bar || "";
    const total = Number(data.universe_count) || 0;
    const span = Number(data.last_bar_span_days) || 0;
    const lags = Array.isArray(data.lag_distribution) ? data.lag_distribution : [];
    if (distMeta) {
      const lagBits = lags
        .filter((r) => Number(r.count) > 0 && Number(r.lag) !== 0)
        .map((r) => `${r.label || `缺${r.lag}`} ${r.count}`)
        .slice(0, 4);
      if (lagBits.length) {
        distMeta.textContent = `as-of ${expected || "—"} · ${lagBits.join(" · ")}`;
      } else if (span <= 0 && expected) {
        distMeta.textContent = `齐至 ${expected} · 柱长=占比`;
      } else if (span > 0) {
        distMeta.textContent = `Δ${span}d · Top 6 · 柱长=占比`;
      } else {
        distMeta.textContent = "Top · 柱长=占比";
      }
    }

    // 优先画交易日缺口分桶（缺1 / 缺2），否则回退末 bar 日期分布
    let rows;
    if (lags.length) {
      rows = lags.map((row) => {
        const lag = Number(row.lag);
        const label = String(row.label || (lag < 0 ? "Missing" : lag === 0 ? "齐" : `缺${lag}`));
        let state = "";
        if (lag < 0) state = "is-bad";
        else if (lag === 0) state = "is-ok";
        else state = "is-warn";
        return {
          label,
          count: Number(row.count) || 0,
          state,
          title: lag > 0 ? `落后 ${lag} 个交易日` : label,
          universeTotal: total,
        };
      });
    } else {
      rows = [...(data.date_distribution || [])].map((row) => {
        const missing = !!row.missing;
        const date = missing ? "Missing" : String(row.date || "—");
        let state = "";
        if (missing) state = "is-bad";
        else if (expected && date === expected) state = "is-ok";
        else if (expected && date < expected) state = "is-warn";
        return {
          label: date,
          count: Number(row.count) || 0,
          state,
          title: missing ? "Missing cache" : date,
          universeTotal: total,
        };
      });
    }
    if (Number(data.missing) > 0 && !rows.some((r) => r.label === "Missing")) {
      rows.push({
        label: "Missing",
        count: Number(data.missing) || 0,
        state: "is-bad",
        title: "Missing cache",
        universeTotal: total,
      });
    }
    renderBarRows(distHost, rows, {
      emptyText: "暂无分布（观察池为空或未拉取）",
      head: barRowsHead(lags.length ? "Lag" : "Last bar", "Share", "N"),
    });
  }

  function renderFoot(data) {
    if (!footEl) return;
    if (!data || !data.success) {
      footEl.innerHTML = "";
      return;
    }
    const backend = backendLabel(data.bars_backend);
    const marker = data.forced_marker || {};
    const remote =
      marker.remote_count != null && marker.total != null
        ? `${marker.remote_count}/${marker.total} 远端`
        : "—";
    const jobLine = formatClusterJobFootLine(data.refresh_job || lastStatusJob);
    footEl.innerHTML = `<div class="quant-bars-foot-grid">
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Store</span><span class="quant-bars-foot-v">${esc(backend)} · OHLCV</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Scope</span><span class="quant-bars-foot-v">watching · Limit ${esc(data.watching_limit ?? "—")}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">强更 Job</span><span class="quant-bars-foot-v">${esc(jobLine)}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Rule</span><span class="quant-bars-foot-v">交易日 15:05 前 as-of → 上一交易日</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Force</span><span class="quant-bars-foot-v">${esc(remote)}${marker.saved_at ? ` · ${esc(fmtUtcShort(marker.saved_at))} UTC` : ""}</span></div>
    </div>`;
  }

  function handleRefreshJob(job) {
    if (!job || !job.status || job.status === "idle") {
      lastStatusJob = null;
      return;
    }
    lastStatusJob = job;
    if (job.status === "running") {
      if (!inflight) syncJobSlot();
      return;
    }
    if (job.status === "failed") {
      applyJobFailure(new Error(job.error || job.message || "日 K 强更失败"), job);
      return;
    }
    if (job.status === "done") {
      applyJobSuccess(job);
    }
  }

  const DIFF_TAGS = {
    price: "价变",
    volume: "量纲",
    tail: "只补尾",
    unchanged: "未改",
    new: "新写入",
    gone: "缺失",
  };

  function fmtCloseRel(rel) {
    const n = Number(rel);
    if (!Number.isFinite(n)) return "—";
    const pct = n * 100;
    if (pct >= 1) return `${pct.toFixed(2)}%`;
    return `${pct.toFixed(3)}%`;
  }

  function fmtVolRatio(ratio) {
    const n = Number(ratio);
    if (!Number.isFinite(n) || n <= 0) return "—";
    if (n >= 10) return `×${Math.round(n)}`;
    if (n <= 0.1) return `×${n.toFixed(2)}`;
    return `×${n.toFixed(2)}`;
  }

  function closeRelBarPct(rel, scale) {
    const n = Number(rel);
    if (!Number.isFinite(n) || n <= 0 || !(scale > 0)) return 0;
    return Math.max(3, Math.min(100, (100 * n) / scale));
  }

  function volRatioBarPct(ratio) {
    const n = Number(ratio);
    if (!Number.isFinite(n) || n <= 0) return 0;
    // 相对 1 的偏离：1→约 8%，10×→~55%，100×→满格
    const dist = Math.abs(Math.log10(n));
    return Math.max(n > 0.67 && n < 1.5 ? 8 : 14, Math.min(100, 8 + dist * 46));
  }

  function setDiffKpi(key, state) {
    if (!diffKpis) return;
    const card = diffKpis.querySelector(`[data-bars-diff="${key}"]`);
    if (!card) return;
    card.classList.remove("is-bad", "is-mid", "is-empty");
    card.classList.add(state || "is-empty");
  }

  function renderBarsDiff(diff) {
    const emptyNote = "点增量补齐或强更后，这里对比写入前后的收盘和成交量。";
    if (!diff || !diff.headline) {
      if (diffMeta) diffMeta.textContent = "还没有对比";
      if (diffPrice) diffPrice.textContent = "—";
      if (diffVolume) diffVolume.textContent = "—";
      if (diffTail) diffTail.textContent = "—";
      if (diffSame) diffSame.textContent = "—";
      setDiffKpi("price", "is-empty");
      setDiffKpi("volume", "is-empty");
      setDiffKpi("tail", "is-empty");
      setDiffKpi("same", "is-empty");
      if (diffRows) diffRows.innerHTML = "";
      if (diffNote) diffNote.textContent = emptyNote;
      return;
    }
    const priceN = Number(diff.price_count) || 0;
    const volumeN = Number(diff.volume_count) || 0;
    const tailN = Number(diff.tail_count) || 0;
    const sameN = Number(diff.unchanged_count) || 0;
    const modeLabel = String(diff.mode) === "full" ? "强更" : "增量";
    const when = diff.saved_at ? String(diff.saved_at).replace("T", " ").slice(0, 16) : "";
    const compared = Number(diff.compared) || 0;
    if (diffMeta) {
      const parts = [modeLabel];
      if (compared > 0) parts.push(`比较 ${compared} 只`);
      if (when) parts.push(when);
      diffMeta.textContent = parts.join(" · ");
    }
    if (diffPrice) diffPrice.textContent = String(priceN);
    if (diffVolume) diffVolume.textContent = String(volumeN);
    if (diffTail) diffTail.textContent = String(tailN);
    if (diffSame) diffSame.textContent = String(sameN);
    setDiffKpi("price", priceN > 0 ? "is-bad" : "is-mid");
    setDiffKpi("volume", volumeN > 0 ? "is-bad" : "is-mid");
    setDiffKpi("tail", tailN > 0 ? "is-mid" : "is-empty");
    setDiffKpi("same", sameN > 0 ? "is-mid" : "is-empty");
    if (diffNote) diffNote.textContent = diff.note || emptyNote;
    if (!diffRows) return;
    const rows = Array.isArray(diff.rows) ? diff.rows : [];
    if (!rows.length) {
      diffRows.innerHTML =
        `<div class="quant-bars-viz-empty">无逐票差异（重叠日价和量都在阈值内）</div>`;
      return;
    }
    const scale = Math.max(
      Number(diff.worst_close_rel) || 0,
      ...rows.map((row) => Number(row.max_close_rel) || 0),
      0.01
    );
    const head =
      `<div class="quant-bars-diff-row is-head" role="row">` +
      `<span>代码</span><span>重叠日</span><span>收盘最大相对差</span><span>成交量比</span><span>标记</span>` +
      `</div>`;
    const body = rows
      .map((row) => {
        const tagsList = Array.isArray(row.tags) ? row.tags : [];
        const alert = tagsList.includes("price") || tagsList.includes("volume") || tagsList.includes("gone");
        const rel = Number(row.max_close_rel) || 0;
        const ratio = Number(row.vol_ratio);
        const volOff = Number.isFinite(ratio) && ratio > 0 && (ratio >= 1.5 || ratio <= 1 / 1.5);
        const tags = tagsList
          .map((tag) => {
            const label = DIFF_TAGS[tag] || tag;
            const tone = tag === "price" || tag === "volume" || tag === "gone" ? "is-bad" : "is-warn";
            return `<span class="quant-bars-badge ${tone}">${esc(label)}</span>`;
          })
          .join("");
        const relW = closeRelBarPct(rel, scale).toFixed(1);
        const volW = volRatioBarPct(ratio).toFixed(1);
        const volCls = volOff ? "is-vol" : "is-ok";
        const volValCls = volOff ? "" : " is-muted";
        return (
          `<div class="quant-bars-diff-row${alert ? " is-alert" : ""}" role="row">` +
          `<span class="quant-bars-diff-code">${esc(row.code || "—")}</span>` +
          `<span>${esc(row.overlap_days ?? "—")}</span>` +
          `<span class="quant-bars-diff-metric" title="相对本表最大差缩放">` +
          `<span class="quant-bars-diff-meter is-price"><i style="width:${relW}%"></i></span>` +
          `<span class="quant-bars-diff-val">${esc(fmtCloseRel(rel))}</span>` +
          `</span>` +
          `<span class="quant-bars-diff-metric" title="相对 1 的对数偏离">` +
          `<span class="quant-bars-diff-meter ${volCls}"><i style="width:${volW}%"></i></span>` +
          `<span class="quant-bars-diff-val${volValCls}">${esc(fmtVolRatio(ratio))}</span>` +
          `</span>` +
          `<span class="quant-bars-diff-tags">${tags}</span>` +
          `</div>`
        );
      })
      .join("");
    diffRows.innerHTML = head + body;
  }

  function renderKpis(data) {
    const total = Number(data.universe_count) || 0;
    const at = Number(data.at_expected) || 0;
    const stale = Number(data.stale) || 0;
    const missing = Number(data.missing) || 0;
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;
    const span = Number(data.last_bar_span_days) || 0;

    if (kpiAlign) kpiAlign.textContent = total > 0 ? `${pct}%` : "—";
    if (kpiAlignSub) kpiAlignSub.textContent = total > 0 ? `${at} / ${total} aligned` : "— / — aligned";
    if (kpiExpected) kpiExpected.textContent = data.expected_latest_bar || "—";
    if (kpiExpectedSub) {
      if (span > 0 && data.last_bar_min && data.last_bar_max) {
        kpiExpectedSub.textContent = `跨度 ${data.last_bar_min}→${data.last_bar_max}`;
      } else {
        kpiExpectedSub.textContent = "15:05 规则 · 齐";
      }
    }
    if (kpiStale) kpiStale.textContent = String(stale);
    if (kpiMissing) kpiMissing.textContent = String(missing);

    setKpiCard("align", total <= 0 ? "is-empty" : data.coverage_ok ? "is-good" : "is-mid");
    setKpiCard("expected", data.expected_latest_bar ? "is-mid" : "is-empty");
    setKpiCard("stale", stale > 0 ? "is-bad" : total > 0 ? "is-good" : "is-empty");
    setKpiCard("missing", missing > 0 ? "is-bad" : total > 0 ? "is-good" : "is-empty");
  }

  function paint(data, { skipHead = false } = {}) {
    // 物化不占用日 K 状态条：覆盖文案只避开本卡日 K 任务结果
    if (!skipHead && !jobFailure && !jobSuccess) {
      const { state, chip: chipText, msg } = formatStatus(data);
      setProStatusChip(chip, state, chipText);
      msgEl.textContent = msg;
    }
    if (!data || !data.success) {
      renderContextStrip(null);
      renderCoverageBar({ universe_count: 0, at_expected: 0, stale: 0, missing: 0 });
      renderDistribution({ date_distribution: [], missing: 0 });
      renderBarsDiff(null);
      if (footEl) footEl.innerHTML = "";
      return;
    }
    renderContextStrip(data);
    renderKpis(data);
    try {
      syncOverviewBarsCoverage(data);
    } catch (_) {
      /* overview optional */
    }
    renderCoverageBar(data);
    renderDistribution(data);
    renderBarsDiff(data.bars_diff);
    renderFoot(data);
  }

  async function refreshStatus() {
    const limit = Math.min(BARS_WATCHING_LIMIT, readWatchingPoolLimit());
    const { ok, data, error } = await apiFetch(
      `/api/quant/bars/status?watching_limit=${encodeURIComponent(limit)}`
    );
    if (!ok) {
      if (!jobFailure && !jobSuccess) {
        paint(null);
        msgEl.textContent = error || "读取日 K 状态失败";
        setProStatusChip(chip, "error", "ERR");
      }
      if (!inflight) setBarsBusy(false);
      return null;
    }
    paint(data, {
      skipHead: !!jobFailure || !!jobSuccess,
    });
    if (jobFailure) applyJobFailure({ message: jobFailure.errText }, jobFailure.job);
    else if (jobSuccess) applyJobSuccess(jobSuccess.job);
    else if (data.refresh_job) {
      handleRefreshJob(data.refresh_job);
    }
    const jobSt = data.refresh_job && data.refresh_job.status;
    if (
      !inflight &&
      !jobFailure &&
      !jobSuccess &&
      jobSt !== "running"
    ) {
      setBarsBusy(false);
    }
    return data;
  }

  async function syncJobSlot() {
    const { ok, data } = await apiFetch("/api/jobs/bars-refresh?progress=1");
    const job = unwrapJobSnap(data);
    if (!ok || !job) return null;
    lastPolledJob = job;
    lastStatusJob = job.status && job.status !== "idle" ? job : lastStatusJob;
    const st = job.status;
    if (st === "running") {
      clearJobPanels();
      if (!inflight) {
        setBarsButtonsDisabled(true);
        inflight = (async () => {
          try {
            const result = await waitBarsJob(job.id);
            clearJobPanels();
            applyJobSuccess({ ...job, result_summary: result?.bars_refresh, result });
            setBarsBusy(false);
            await refreshStatus();
          } catch (err) {
            applyJobFailure(err, lastPolledJob);
          } finally {
            setBarsButtonsDisabled(false);
            inflight = null;
          }
        })();
      }
      return job;
    }
    if (st === "failed") {
      applyJobFailure(new Error(job.error || job.message || "日 K 更新失败"), job);
      return job;
    }
    if (st === "done") {
      applyJobSuccess(job);
      return job;
    }
    return job;
  }

  async function waitBarsJob(jobId, mode = activeMode) {
    const started = Date.now();
    const softCapMs = 12 * 60 * 1000;
    const absoluteCapMs = 45 * 60 * 1000;
    const heartbeatFreshSec = 90;
    let sawOwnJob = false;
    const waitLabel = modeLabel(mode);
    paintProgressPanel(
      { message: `提交${waitLabel}…（排队后台 Job）`, pct: 0 },
      { pollStartedAt: started, mode }
    );
    while (Date.now() - started < absoluteCapMs) {
      const { ok, data } = await apiFetch(
        "/api/jobs/bars-refresh?progress=1"
      );
      const job = unwrapJobSnap(data);
      if (!ok || !job) {
        paintProgressPanel(
          { message: "等待 Job 心跳…", pct: 0 },
          { pollStartedAt: started, mode }
        );
        await new Promise((r) => setTimeout(r, 800));
        continue;
      }
      if (jobId && job.id && job.id !== jobId) {
        if (sawOwnJob) break;
        paintProgressPanel(
          { message: "等待本任务接管…", pct: 0 },
          { pollStartedAt: started, mode }
        );
        await new Promise((r) => setTimeout(r, 600));
        continue;
      }
      if (jobId && job.id === jobId) sawOwnJob = true;
      lastPolledJob = job;
      const st = job.status;
      if (st === "done") {
        paintProgressDone(job);
        return job.result || { success: true };
      }
      if (st === "failed") {
        throw new Error(job.error || job.message || `${waitLabel}失败`);
      }
      if (st === "idle" && sawOwnJob) {
        throw new Error("日 K 任务已结束但未返回结果");
      }
      const pct = Number(job.pct);
      const line = job.message || `${waitLabel}…`;
      const parsed = parseBarsProgressMessage(line);
      const chipMap = {
        queue: "QUEUE",
        index: "INDEX",
        start: "START",
        timeout: "WRAP",
        fetch: "SYNC",
        done: "DONE",
      };
      setProStatusChip(chip, "busy", chipMap[parsed.phase] || "SYNC");
      setBarsBusy(true);
      paintProgressPanel(job, { pollStartedAt: started, mode });
      const headBits = [line];
      if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
      if (parsed.elapsedSec != null) headBits.push(fmtDuration(parsed.elapsedSec));
      else headBits.push(fmtDuration((Date.now() - started) / 1000));
      const { heartbeat } = jobTimings(job, started);
      if (heartbeat != null) headBits.push(`心跳 ${fmtDuration(heartbeat)}`);
      if (parsed.remainSec != null) headBits.push(`剩 ${fmtDuration(parsed.remainSec)}`);
      msgEl.textContent = headBits.join(" · ");
      const updatedAt = Number(job.updated_at || 0);
      const ageSec = updatedAt ? Date.now() / 1000 - updatedAt : 999;
      if (Date.now() - started > softCapMs && ageSec > heartbeatFreshSec) {
        throw new Error(`${waitLabel}超时（长时间无进度）`);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(`${waitLabel}超时`);
  }

  async function startRefresh(mode = "full") {
    if (inflight) return inflight;
    const modeS = mode === "topup" ? "topup" : "full";
    activeMode = modeS;
    const label = modeLabel(modeS);
    inflight = (async () => {
      clearJobPanels();
      setBarsButtonsDisabled(true);
      setBarsBusy(true);
      setProStatusChip(chip, "busy", "QUEUE");
      msgEl.textContent = `提交${label}…`;
      paintProgressPanel(
        { message: `提交${label}…`, pct: 0 },
        { pollStartedAt: Date.now(), mode: modeS }
      );
      try {
        const limit = Math.min(BARS_WATCHING_LIMIT, readWatchingPoolLimit());
        const { ok, data, error } = await apiFetch("/api/quant/bars/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            lookback: BARS_DAILY_LOOKBACK,
            watching_limit: limit,
            mode: modeS,
          }),
        });
        if (!ok) throw new Error(error || "提交失败");
        let result = data;
        if (data && data.background && data.job) {
          if (data.reused) {
            paintProgressPanel(
              {
                message: data.job.message || "复用进行中的日 K Job…",
                pct: data.job.pct,
              },
              { pollStartedAt: Date.now(), mode: modeS }
            );
          }
          result = await waitBarsJob(data.job.id, modeS);
        }
        if (result && result.status) {
          paint(result.status);
        } else {
          await refreshStatus();
        }
        if (result && result.success === false) {
          throw new Error(result.error || `${label}失败`);
        }
        const br = (result && result.bars_refresh) || {};
        if (br.remote_count != null) {
          const doneLabel = isTopupSummary(br) ? "增量完成" : "强更完成";
          msgEl.textContent = `${doneLabel} · 远端 ${br.remote_count}/${br.total ?? "—"} · 缓存 ${br.cache_count ?? "—"}`;
        }
        clearJobPanels();
        applyJobSuccess(lastPolledJob);
      } catch (err) {
        applyJobFailure(err, lastPolledJob);
        throw err;
      } finally {
        setBarsButtonsDisabled(false);
        inflight = null;
        if (!jobFailure && !jobSuccess) setBarsBusy(false);
        try {
          await refreshStatus();
        } catch (_) {
          /* ignore */
        }
      }
    })();
    return inflight;
  }

  async function startTopup() {
    return startRefresh("topup");
  }

  on("quant-bars-refresh", "click", (e) => {
    e.preventDefault();
    startRefresh("full").catch(() => {});
  });
  if (topupBtn) {
    on("quant-bars-topup", "click", (e) => {
      e.preventDefault();
      startTopup().catch(() => {});
    });
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener(WATCHING_POOL_LIMIT_CHANGED, () => {
      void refreshStatus();
    });
  }

  (async () => {
    try {
      await awaitWatchingPoolLimitReady();
      // 物化与日 K 状态条已分立；先 resume 物化进度，再刷覆盖
      const matResumed =
        typeof matUi.resumeIfRunning === "function"
          ? await matUi.resumeIfRunning()
          : false;
      await refreshStatus();
      if (!matResumed) await syncJobSlot();
    } catch (_) {
      /* ignore */
    }
  })();

  return { refreshStatus, startRefresh, startTopup, syncJobSlot };
}
