import { setProStatusChip } from "./factor_corr_ui.js";
import {
  fmtDuration,
  fmtAgeSec,
  jobTimings,
  paintClusterJobPanel,
  formatClusterJobFootLine,
  jobStatusBadge,
  unwrapJobSnap,
} from "./cluster_job_ui.js";

/** 研究枢纽 · 观察池日 K 覆盖 + 「增量补齐」/「强更日 K」 */
export function installClusterBarsUi(q) {
  const { on, apiFetch, readWatchingLimit, escapeHtml } = q;
  const chip = document.getElementById("quant-cluster-bars-chip");
  const msgEl = document.getElementById("quant-cluster-bars-msg");
  const btn = document.getElementById("quant-cluster-bars-refresh");
  const topupBtn = document.getElementById("quant-cluster-bars-topup");
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
  const kpiHost = document.getElementById("quant-bars-kpis");
  const barsCard = document.querySelector(".quant-bars-card");
  const barsBody = document.querySelector(".quant-bars-body");
  const barsStrip = document.getElementById("quant-cluster-bars-strip");

  if (!chip || !msgEl || !btn) {
    return { refreshStatus: async () => {}, startRefresh: async () => {}, startTopup: async () => {} };
  }

  function setBarsButtonsDisabled(disabled) {
    btn.disabled = !!disabled;
    if (topupBtn) topupBtn.disabled = !!disabled;
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
      return modeOrSum === "topup" ? "增量补齐" : "强更日 K";
    }
    return isTopupSummary(modeOrSum) ? "增量补齐" : "强更日 K";
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
      elapsedSec: null,
      remainSec: null,
      workers: null,
      capSec: null,
      timedOut: /超时/.test(msg),
    };
    if (/排队|提交/.test(msg)) out.phase = "queue";
    else if (/指数/.test(msg)) out.phase = "index";
    else if (/超时收尾/.test(msg)) out.phase = "timeout";
    else if (/完成|已对齐|Coverage/.test(msg) && !/拉日线/.test(msg)) out.phase = "done";
    else if (/拉日线\s*0\//.test(msg) && /上限|并发/.test(msg)) out.phase = "start";

    const frac = msg.match(/(\d+)\s*\/\s*(\d+)/);
    if (frac) {
      out.done = Number(frac[1]);
      out.total = Number(frac[2]);
    }
    const remote = msg.match(/远端\s*(\d+)/);
    if (remote) out.remote = Number(remote[1]);
    const cache = msg.match(/缓存\s*(\d+)/);
    if (cache) out.cache = Number(cache[1]);
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
        : remote != null && done != null
          ? Math.max(0, done - remote)
          : null;
    const facts = [];
    if (remote != null) facts.push(`远端 <strong>${remote}</strong>`);
    // 0/N 启动瞬间 done=0 会算出「缓存命中 0」，像没吃到本地仓；有完成票再报
    if (cache != null && Number(done) > 0) {
      facts.push(`缓存命中 <strong>${cache}</strong>`);
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
    paintClusterJobPanel(progressEl, {
      phase: "更新失败",
      job,
      esc,
      mode: "fail",
      errText,
      hint: "可再次点击「增量补齐」或「强更日 K」重试",
    });
  }

  function paintProgressDone(job) {
    const sum = job?.result_summary || lastPolledJob?.result?.bars_refresh || {};
    const bits = [];
    if (sum.remote_count != null) bits.push(`远端 ${sum.remote_count}/${sum.total ?? "—"}`);
    if (sum.cache_count != null) bits.push(`缓存 ${sum.cache_count}`);
    const topup = isTopupSummary(sum);
    paintClusterJobPanel(progressEl, {
      phase: topup ? "增量完成" : "强更完成",
      job,
      esc,
      mode: "done",
      hint: bits.length
        ? bits.join(" · ")
        : topup
          ? "缺口增量 merge · 4 路并发 · 到批上限会超时收尾"
          : "整窗重拉 · 4 路并发 · 到批上限会超时收尾",
    });
  }

  function applyJobSuccess(job) {
    jobSuccess = { job: job || null };
    jobFailure = null;
    setProStatusChip(chip, "ok", "DONE");
    const sum = job?.result_summary || job?.result?.bars_refresh || {};
    const head = [isTopupSummary(sum) ? "增量完成" : "强更完成"];
    if (sum.remote_count != null) head.push(`远端 ${sum.remote_count}/${sum.total ?? "—"}`);
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
    const defaultMsg = topup ? "增量补齐日 K…" : "强更日 K…";
    const msg = job && (job.message || job.error) ? String(job.message || job.error) : defaultMsg;
    const parsed = parseBarsProgressMessage(msg);
    const warn =
      parsed.timedOut ||
      (parsed.remainSec != null && parsed.remainSec <= 30 && parsed.done < parsed.total);
    paintClusterJobPanel(progressEl, {
      phase: phaseLabel(parsed.phase),
      job,
      pollStartedAt,
      esc,
      mode: "running",
      isWarn: warn,
      hint: topup
        ? "已齐 as-of 走本地 · 缺口增量 merge · 约 4 并发"
        : "整窗重拉 · 仓坏/复权兜底 · 约 4 并发",
      extraFacts: barsParsedFacts(parsed, pollStartedAt),
    });
  }

  function setBarsBusy(busy) {
    const on = !!busy;
    barsCard?.classList.toggle("is-busy", on);
    barsStrip?.classList.toggle("is-busy", on);
    barsBody?.classList.toggle("is-busy", on);
    btn?.classList.toggle("is-busy", on);
    topupBtn?.classList.toggle("is-busy", on);
    if (!on && !jobFailure && !jobSuccess) clearProgressPanel();
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
    if (distMeta) {
      if (span <= 0 && expected) {
        distMeta.textContent = `齐至 ${expected} · 柱长=占比`;
      } else if (span > 0) {
        distMeta.textContent = `Δ${span}d · Top 6 · 柱长=占比`;
      } else {
        distMeta.textContent = "Top · 柱长=占比";
      }
    }

    const rows = [...(data.date_distribution || [])].map((row) => {
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
      head: barRowsHead("Last bar", "Share", "N"),
    });
  }

  function renderFoot(data) {
    if (!footEl) return;
    if (!data || !data.success) {
      footEl.innerHTML = "";
      return;
    }
    const backend = backendLabel(data.bars_backend);
    const fields = (data.bar_fields || ["open", "high", "low", "close", "volume"]).join("/");
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
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Write</span><span class="quant-bars-foot-v">增量补齐 / 强更日 K · ≠ 刷新名单 · ≠ 5m</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Fields</span><span class="quant-bars-foot-v">${esc(fields)} · 下游 ŷ_EOD / OLS</span></div>
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
    if (!skipHead && !jobFailure && !jobSuccess) {
      const { state, chip: chipText, msg } = formatStatus(data);
      setProStatusChip(chip, state, chipText);
      msgEl.textContent = msg;
    }
    if (!data || !data.success) {
      renderContextStrip(null);
      renderCoverageBar({ universe_count: 0, at_expected: 0, stale: 0, missing: 0 });
      renderDistribution({ date_distribution: [], missing: 0 });
      if (footEl) footEl.innerHTML = "";
      return;
    }
    renderContextStrip(data);
    renderKpis(data);
    renderCoverageBar(data);
    renderDistribution(data);
    renderFoot(data);
  }

  async function refreshStatus() {
    const limit = typeof readWatchingLimit === "function" ? readWatchingLimit() : 100;
    const { ok, data, error } = await apiFetch(
      `/api/quant/cluster-bars/status?watching_limit=${encodeURIComponent(limit)}`
    );
    if (!ok) {
      paint(null);
      msgEl.textContent = error || "读取日 K 状态失败";
      setProStatusChip(chip, "error", "ERR");
      if (!inflight) setBarsBusy(false);
      return null;
    }
    paint(data, { skipHead: !!jobFailure || !!jobSuccess });
    if (jobFailure) applyJobFailure({ message: jobFailure.errText }, jobFailure.job);
    else if (jobSuccess) applyJobSuccess(jobSuccess.job);
    else if (data.refresh_job) handleRefreshJob(data.refresh_job);
    const jobSt = data.refresh_job && data.refresh_job.status;
    if (!inflight && !jobFailure && !jobSuccess && jobSt !== "running") {
      setBarsBusy(false);
    }
    return data;
  }

  async function syncJobSlot() {
    const { ok, data } = await apiFetch("/api/jobs/cluster-bars-refresh?progress=1");
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
            const result = await waitClusterBarsJob(job.id);
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

  async function waitClusterBarsJob(jobId, mode = activeMode) {
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
        "/api/jobs/cluster-bars-refresh?progress=1"
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
        const limit = typeof readWatchingLimit === "function" ? readWatchingLimit() : 100;
        const { ok, data, error } = await apiFetch("/api/quant/cluster-bars/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lookback: 80, watching_limit: limit, mode: modeS }),
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
          result = await waitClusterBarsJob(data.job.id, modeS);
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

  on("quant-cluster-bars-refresh", "click", (e) => {
    e.preventDefault();
    startRefresh("full").catch(() => {});
  });
  if (topupBtn) {
    on("quant-cluster-bars-topup", "click", (e) => {
      e.preventDefault();
      startTopup().catch(() => {});
    });
  }

  (async () => {
    try {
      await refreshStatus();
      await syncJobSlot();
    } catch (_) {
      /* ignore */
    }
  })();

  return { refreshStatus, startRefresh, startTopup, syncJobSlot };
}
