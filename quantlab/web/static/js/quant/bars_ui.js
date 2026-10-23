import { setProStatusChip, syncOverviewBarsCoverage } from "./factor_corr_ui.js";
import {
  paintClusterJobPanel,
  jobStatusBadge,
  unwrapJobSnap,
  setDeskStatusTone,
  parseL0ProgressMessage,
  l0PhaseLabel,
  l0ParsedFacts,
  l0RunningHead,
  l0DoneHead,
  l0CoverageFacts,
  isJobHeartbeatStale,
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
  const diffPrice = document.getElementById("quant-bars-diff-price");
  const diffVolume = document.getElementById("quant-bars-diff-volume");
  const diffTail = document.getElementById("quant-bars-diff-tail");
  const diffSame = document.getElementById("quant-bars-diff-same");
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
  let lastCoverage = null;
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

  function clearProgressPanel() {
    if (!progressEl) return;
    progressEl.hidden = true;
    progressEl.innerHTML = "";
    progressEl.classList.remove("is-active", "is-warn", "is-done");
    delete progressEl.dataset.tone;
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
    const head = l0DoneHead(sum, "bars", lastCoverage, job);
    const topup = isTopupSummary(sum);
    setDeskStatusTone([barsStrip, barsCard], "primary");
    paintClusterJobPanel(progressEl, {
      phase: head[0],
      job,
      esc,
      mode: "done",
      tone: "primary",
      hint: head.length > 1
        ? head.slice(1).join(" · ")
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
    msgEl.textContent = l0DoneHead(sum, "bars", lastCoverage, job).join(" · ");
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
    const j = (err && err.job) || job || lastPolledJob || null;
    // 前端等满但槽位仍 running：黄条提示接管，勿标 FAIL
    if (err && err.stillRunning) {
      jobFailure = null;
      setDeskStatusTone([barsStrip, barsCard], "primary");
      setProStatusChip(chip, "busy", "WAIT");
      msgEl.textContent = errText;
      setBarsBusy(true);
      paintProgressPanel(j || { message: errText }, { pollStartedAt: Date.now() });
      return;
    }
    jobFailure = { errText, job: j };
    setDeskStatusTone([barsStrip, barsCard], "primary");
    setProStatusChip(chip, "error", "FAIL");
    const head = ["更新失败"];
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
    const parsed = parseL0ProgressMessage(msg);
    setDeskStatusTone([barsStrip, barsCard], "primary");
    paintClusterJobPanel(progressEl, {
      phase: l0PhaseLabel(parsed.phase, "bars"),
      job,
      pollStartedAt,
      esc,
      mode: "running",
      tone: "primary",
      hint: topup
        ? "已齐 as-of 走本地 · 缺口进程池并行 · 单票 60s"
        : "整窗替换 · 进程池并行 · 单票 60s",
      extraFacts: l0ParsedFacts(parsed),
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
    const parts = [`Coverage ${pct}% (${at}/${total})`, ...l0CoverageFacts(data)];
    if (stale > 0) parts.push(`Stale ${stale}`);
    if (missing > 0) parts.push(`Missing ${missing}`);
    if (data.needs_force_latest_bars && !(data.forced_marker || {}).saved_at) {
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
        <div><dt>Updated</dt><dd>${l0CoverageFacts(data).map((bit) => esc(bit)).join(" · ") || "—"}</dd></div>
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
      applyJobFailure(new Error(job.error || job.message || "日 K 更新失败"), job);
      return;
    }
    if (job.status === "done") {
      applyJobSuccess(job);
    }
  }

  function setDiffKpi(key, state) {
    if (!diffKpis) return;
    const card = diffKpis.querySelector(`[data-bars-diff="${key}"]`);
    if (!card) return;
    card.classList.remove("is-bad", "is-mid", "is-empty");
    card.classList.add(state || "is-empty");
  }

  function renderBarsDiff(diff) {
    if (!diff || !diff.headline) {
      if (diffPrice) diffPrice.textContent = "—";
      if (diffVolume) diffVolume.textContent = "—";
      if (diffTail) diffTail.textContent = "—";
      if (diffSame) diffSame.textContent = "—";
      setDiffKpi("price", "is-empty");
      setDiffKpi("volume", "is-empty");
      setDiffKpi("tail", "is-empty");
      setDiffKpi("same", "is-empty");
      return;
    }
    const priceN = Number(diff.price_count) || 0;
    const volumeN = Number(diff.volume_count) || 0;
    const tailN = Number(diff.tail_count) || 0;
    const sameN = Number(diff.unchanged_count) || 0;
    if (diffPrice) diffPrice.textContent = String(priceN);
    if (diffVolume) diffVolume.textContent = String(volumeN);
    if (diffTail) diffTail.textContent = String(tailN);
    if (diffSame) diffSame.textContent = String(sameN);
    setDiffKpi("price", priceN > 0 ? "is-bad" : "is-mid");
    setDiffKpi("volume", volumeN > 0 ? "is-bad" : "is-mid");
    setDiffKpi("tail", tailN > 0 ? "is-mid" : "is-empty");
    setDiffKpi("same", sameN > 0 ? "is-mid" : "is-empty");
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
    if (data && data.success) lastCoverage = data;
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
    // 软上限：心跳仍新鲜则继续；心跳停了才退出。硬上限 45min。
    // 前端等满时勿直接 FAIL——后台可能还在跑，提示刷新接管。
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
      let ok = false;
      let data = null;
      try {
        ({ ok, data } = await apiFetch("/api/jobs/bars-refresh?progress=1"));
      } catch (_) {
        paintProgressPanel(
          { message: "网络中断，重试 Job 心跳…", pct: 0 },
          { pollStartedAt: started, mode }
        );
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
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
      const view = l0RunningHead(job, { pollStartedAt: started });
      setProStatusChip(chip, "busy", view.chip);
      setBarsBusy(true);
      paintProgressPanel(job, { pollStartedAt: started, mode });
      msgEl.textContent = view.text;
      const pastSoft = Date.now() - started >= softCapMs;
      if (pastSoft && isJobHeartbeatStale(job, heartbeatFreshSec)) break;
      await new Promise((r) => setTimeout(r, 500));
    }
    // 前端等满时后台可能仍在跑：勿误报「失败」
    try {
      const { ok, data } = await apiFetch("/api/jobs/bars-refresh?progress=1");
      const job = unwrapJobSnap(data) || {};
      const st = String(job.status || "").toLowerCase();
      if (
        jobId &&
        job.id === jobId &&
        (st === "running" || st === "done" || st === "success" || st === "finished")
      ) {
        if (st === "done" || st === "success" || st === "finished") {
          paintProgressDone(job);
          return job.result || { success: true };
        }
        lastPolledJob = job;
        const err = new Error(
          "前端等待已满，任务仍在后台运行；请刷新页面接管进度（勿重复开跑）"
        );
        err.stillRunning = true;
        err.job = job;
        throw err;
      }
    } catch (e) {
      if (e && e.stillRunning) throw e;
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
        const jobForDone =
          lastPolledJob && (lastPolledJob.result || lastPolledJob.result_summary)
            ? lastPolledJob
            : { result_summary: br, result: result || {}, updated_at: Date.now() / 1000 };
        clearJobPanels();
        applyJobSuccess(jobForDone);
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
