import { setProStatusChip, syncOverviewMinute } from "./factor_corr_ui.js";
import {
  fmtDuration,
  fmtAgeSec,
  jobTimings,
  paintClusterJobPanel,
  formatClusterJobFootLine,
  jobStatusBadge,
  unwrapJobSnap,
} from "./cluster_job_ui.js";
import { BARS_WATCHING_LIMIT } from "./params.js";

/** 研究枢纽 · 观察池 5m 分钟覆盖 + 「增量补齐」/「强更 5m」 */
export function installClusterMinuteUi(q) {
  const { on, apiFetch, escapeHtml } = q;
  const chip = document.getElementById("quant-cluster-minute-chip");
  const msgEl = document.getElementById("quant-cluster-minute-msg");
  const btn = document.getElementById("quant-cluster-minute-refresh");
  const topupBtn = document.getElementById("quant-cluster-minute-topup");
  const kpiOk = document.getElementById("quant-minute-kpi-ok");
  const TOPUP_LOOKBACK_DAYS = 5;
  const kpiOkSub = document.getElementById("quant-minute-kpi-ok-sub");
  const kpiSpan = document.getElementById("quant-minute-kpi-span");
  const kpiSpanSub = document.getElementById("quant-minute-kpi-span-sub");
  const kpiShort = document.getElementById("quant-minute-kpi-short");
  const kpiMissing = document.getElementById("quant-minute-kpi-missing");
  const covPct = document.getElementById("quant-minute-cov-pct");
  const covBody = document.getElementById("quant-minute-cov-body");
  const labelTauMeta = document.getElementById("quant-minute-label-tau-meta");
  const labelTauBody = document.getElementById("quant-minute-label-tau-body");
  const labelPathMeta = document.getElementById("quant-minute-label-path-meta");
  const labelPathBody = document.getElementById("quant-minute-label-path-body");
  const labelJointMeta = document.getElementById("quant-minute-label-joint-meta");
  const labelJointBody = document.getElementById("quant-minute-label-joint-body");
  const contextStrip = document.getElementById("quant-minute-context-strip");
  const progressEl = document.getElementById("quant-minute-progress");
  const footEl = document.getElementById("quant-minute-foot");
  const kpiHost = document.getElementById("quant-minute-kpis");
  const minuteCard = document.querySelector(".quant-minute-card");
  const minuteBody = minuteCard?.querySelector(".quant-bars-body");
  const minuteStrip = document.getElementById("quant-cluster-minute-strip");

  if (!chip || !msgEl || !btn) {
    return { refreshStatus: async () => {}, startRefresh: async () => {}, startTopup: async () => {} };
  }

  function setMinuteButtonsDisabled(disabled) {
    btn.disabled = !!disabled;
    if (topupBtn) topupBtn.disabled = !!disabled;
  }

  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s ?? "");
  const LOOKBACK_DAYS = 30;
  const MIN_SPAN_DAYS = 30;

  let inflight = null;
  let kpiInflight = null;
  let jobFailure = null;
  let jobSuccess = null;
  let lastPolledJob = null;
  let lastStatusJob = null;

  function clearJobPanels() {
    jobFailure = null;
    jobSuccess = null;
  }

  function clearProgressPanel() {
    if (!progressEl) return;
    progressEl.hidden = true;
    progressEl.innerHTML = "";
    progressEl.classList.remove("is-active", "is-warn", "is-done");
  }

  function paintProgressFailed(job, errText) {
    paintClusterJobPanel(progressEl, {
      phase: "强更失败",
      job,
      esc,
      mode: "fail",
      errText,
      hint: "可再次点击「增量补齐」或「强更 5m」重试",
    });
  }

  function isTopupSummary(sum) {
    return (
      String(sum?.mode || "") === "topup" ||
      String(sum?.kind || "").includes("topup") ||
      sum?.topped != null
    );
  }

  function paintProgressDone(job) {
    const sum = job?.result_summary || lastPolledJob?.result?.minute_warmup || {};
    const bits = [];
    if (sum.warmed != null) bits.push(`完成 ${sum.warmed}/${sum.total ?? "—"}`);
    if (sum.skipped_today != null && Number(sum.skipped_today) > 0) {
      bits.push(`今日跳过 ${sum.skipped_today}`);
    }
    if (sum.skipped_aligned != null) bits.push(`对齐跳过 ${sum.skipped_aligned}`);
    else if (sum.skipped_ready != null) bits.push(`跳过 Ready ${sum.skipped_ready}`);
    if (sum.topped != null) bits.push(`topup ${sum.topped}`);
    if (sum.bootstrapped != null && Number(sum.bootstrapped) > 0) {
      bits.push(`全窗 ${sum.bootstrapped}`);
    }
    const topup = isTopupSummary(sum);
    paintClusterJobPanel(progressEl, {
      phase: topup ? "增量完成" : "强更完成",
      job,
      esc,
      mode: "done",
      hint:
        bits.length
          ? bits.join(" · ")
          : topup
            ? `增量补齐 · 近 ${TOPUP_LOOKBACK_DAYS} 日 · 约 4 并发`
            : "逐只串行 · 东财/BaoStock 30 日历日 · 新浪/腾讯有数则跳过 BaoStock · 各源间隔 10s",
    });
  }

  function applyJobSuccess(job) {
    jobSuccess = { job: job || null };
    jobFailure = null;
    setProStatusChip(chip, "ok", "DONE");
    const sum = job?.result_summary || job?.result?.minute_warmup || {};
    const topup = isTopupSummary(sum);
    const head = [topup ? "增量完成" : "强更完成"];
    if (sum.warmed != null) head.push(`${sum.warmed}/${sum.total ?? "—"}`);
    const updated = Number(job?.updated_at);
    if (updated > 0) head.push(fmtAgeSec(Date.now() / 1000 - updated));
    msgEl.textContent = head.join(" · ");
    paintProgressDone(job);
    minuteCard?.classList.remove("is-busy");
    minuteStrip?.classList.remove("is-busy");
    minuteBody?.classList.remove("is-busy");
    btn?.classList.remove("is-busy");
    topupBtn?.classList.remove("is-busy");
  }

  function applyJobFailure(err, job) {
    jobSuccess = null;
    const errText = String((err && err.message) || err || "5m 强更失败");
    jobFailure = { errText, job: job || null };
    setProStatusChip(chip, "error", "FAIL");
    const head = ["强更失败"];
    const j = jobFailure.job;
    if (j && j.current > 0 && j.total > 0) head.push(`${j.current}/${j.total}`);
    head.push(errText);
    msgEl.textContent = head.join(" · ");
    paintProgressFailed(j, errText);
    minuteCard?.classList.remove("is-busy");
    minuteStrip?.classList.remove("is-busy");
    minuteBody?.classList.remove("is-busy");
    btn?.classList.remove("is-busy");
    topupBtn?.classList.remove("is-busy");
  }

  function paintProgressPanel(job, { pollStartedAt, mode } = {}) {
    const topup = mode === "topup" || isTopupSummary(job?.result_summary || job?.result?.minute_warmup);
    paintClusterJobPanel(progressEl, {
      phase: topup ? "增量补齐 5m" : "预热 5m",
      job,
      pollStartedAt,
      esc,
      mode: "running",
      hint: topup
        ? `今日已拉跳过 · 对齐跳过 · 近 ${TOPUP_LOOKBACK_DAYS} 日 merge · 新浪近端 · 约 4 并发 · 缺/短才打东财`
        : "逐只串行 · 东财/BaoStock 30 日历日 · 新浪/腾讯有数则跳过 BaoStock · 各源间隔 10s · Ready% 为 span≥30d 覆盖（与 Job 进度不同步刷新）",
    });
  }

  function setMinuteBusy(busy) {
    const onBusy = !!busy;
    minuteCard?.classList.toggle("is-busy", onBusy);
    minuteStrip?.classList.toggle("is-busy", onBusy);
    minuteBody?.classList.toggle("is-busy", onBusy);
    btn?.classList.toggle("is-busy", onBusy);
    topupBtn?.classList.toggle("is-busy", onBusy);
    if (!onBusy && !jobFailure && !jobSuccess) clearProgressPanel();
  }

  function setKpiCard(key, state) {
    if (!kpiHost) return;
    const card = kpiHost.querySelector(`[data-minute-kpi="${key}"]`);
    if (!card) return;
    card.classList.remove("is-good", "is-bad", "is-mid", "is-empty");
    if (state) card.classList.add(state);
  }

  function backendLabel(raw) {
    const b = String(raw || "").trim().toLowerCase();
    if (b === "sqlite") return "SQLite WAL";
    if (b === "json") return "JSON files";
    return b || "—";
  }

  function formatStatus(data) {
    if (!data || !data.success) {
      return { state: "error", chip: "ERR", msg: "无法读取 5m 覆盖状态" };
    }
    const total = data.universe_count ?? 0;
    const ok = data.cached_ok ?? 0;
    const short = data.short ?? 0;
    const missing = data.missing ?? 0;
    const med = data.minute_span_days_med ?? 0;
    const pct = total > 0 ? Math.round((1000 * ok) / total) / 10 : 0;
    const parts = [`Ready ${pct}% (${ok}/${total})`, `span med ${med}d`];
    if (short > 0) parts.push(`Short ${short}`);
    if (missing > 0) parts.push(`Missing ${missing}`);
    let state = "ok";
    let chipText = "READY";
    if (total <= 0) {
      state = "warn";
      chipText = "EMPTY";
    } else if (!data.coverage_ok) {
      state = "warn";
      chipText = short || missing ? "GAP" : "PENDING";
    }
    return { state, chip: chipText, msg: parts.join(" · "), pct, total, ok, short, missing, med };
  }

  function renderBarRows(host, rows, { emptyText = "—", head = null } = {}) {
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = `${head || ""}<p class="quant-bars-viz-empty">${esc(emptyText)}</p>`;
      return;
    }
    const uniFromRow = Math.max(0, ...rows.map((r) => Number(r.universeTotal) || 0));
    const sumCounts = rows.reduce((s, r) => s + (Number(r.count) || 0), 0);
    const denom = uniFromRow > 0 ? uniFromRow : Math.max(sumCounts, 1);
    const body = rows
      .map((row) => {
        const n = Number(row.count) || 0;
        const share = denom > 0 ? (100 * n) / denom : 0;
        const w = n <= 0 ? 0 : share >= 8 ? share : Math.max(2.5, share);
        const label = String(row.label || "—");
        const state = row.state ? ` ${row.state}` : "";
        const pctOfUni = denom > 0 ? `${Math.round((1000 * n) / denom) / 10}%` : "";
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
    const limit = data.watching_limit ?? "—";
    const period = data.period || "5";
    const lb = data.lookback_days_default ?? LOOKBACK_DAYS;
    const minSpan = data.min_span_days ?? MIN_SPAN_DAYS;
    const backend = backendLabel(data.bars_backend);
    const badges = [];
    if (total <= 0) {
      badges.push('<span class="quant-bars-badge is-warn">UNIVERSE EMPTY</span>');
    } else if (data.coverage_ok) {
      badges.push('<span class="quant-bars-badge is-ok">SPAN OK</span>');
    } else {
      badges.push('<span class="quant-bars-badge is-warn">SPAN GAP</span>');
    }
    badges.push(`<span class="quant-bars-badge is-neutral">${esc(period)}m</span>`);
    badges.push(`<span class="quant-bars-badge is-neutral">${esc(backend)}</span>`);
    const jobBadge = jobStatusBadge(lastStatusJob);
    if (jobBadge) {
      badges.push(`<span class="quant-bars-badge ${jobBadge.cls}">${esc(jobBadge.text)}</span>`);
    }

    contextStrip.className = `quant-bars-context-strip${data.coverage_ok ? " is-synced" : " is-gap"}`;
    contextStrip.innerHTML = `<div class="quant-bars-strip">
      <div class="quant-bars-strip-brand">
        <span class="quant-bars-strip-eyebrow">Minute bars</span>
        <span class="quant-bars-strip-title">5m 仓</span>
      </div>
      <dl class="quant-bars-strip-facts">
        <div><dt>Universe</dt><dd>watching · Limit ${esc(limit)} · ${total} 只</dd></div>
        <div><dt>Lookback</dt><dd>增量 ${esc(TOPUP_LOOKBACK_DAYS)}d · 强更 ${esc(lb)}d · Ready ≥ ${esc(minSpan)}d</dd></div>
        <div><dt>Span</dt><dd>med ${esc(data.minute_span_days_med ?? "—")}d · ${esc(data.minute_span_days_min ?? "—")}→${esc(data.minute_span_days_max ?? "—")}</dd></div>
      </dl>
      <div class="quant-bars-strip-badges">${badges.join("")}</div>
    </div>`;
  }

  function renderCoverageBar(data) {
    const total = Math.max(0, Number(data.universe_count) || 0);
    const ok = Number(data.cached_ok) || 0;
    const pct = total > 0 ? Math.round((1000 * ok) / total) / 10 : 0;
    if (covPct) covPct.textContent = total > 0 ? `${pct}% ready` : "—";

    const head = `<div class="quant-bars-bar-head" aria-hidden="true"><span>Bucket</span><span>Share</span><span>N</span></div>`;
    if (total <= 0) {
      renderBarRows(covBody, [], { emptyText: "观察池为空", head });
      return;
    }

    const dist = Array.isArray(data.span_distribution) ? data.span_distribution : [];
    const fineOrder = ["<90d", "<60d", "<30d"];
    const shortBucketState = (bucket) => {
      if (bucket === "<30d") return "is-bad";
      if (bucket === "<60d") return "is-warn";
      return "is-warn";
    };
    const byBucket = new Map(fineOrder.map((k) => [k, 0]));
    dist
      .filter((row) => String(row.bucket || "").startsWith("<"))
      .forEach((row) => {
        const label = String(row.bucket || "");
        if (byBucket.has(label)) byBucket.set(label, Number(row.count) || 0);
      });
    const rows = fineOrder.map((label) => ({
      label,
      count: byBucket.get(label) || 0,
      state: shortBucketState(label),
      title: label,
      universeTotal: total,
    }));
    const hasShort = rows.some((r) => (Number(r.count) || 0) > 0);
    renderBarRows(covBody, hasShort ? rows : [], {
      emptyText: "无 <90d",
      head,
    });
  }

  function labelSignRows(pack, denom) {
    const pos = Number(pack?.pos) || 0;
    const neg = Number(pack?.neg) || 0;
    const zero = Number(pack?.zero) || 0;
    const missing = Number(pack?.missing) || 0;
    const n = denom > 0 ? denom : pos + neg + zero + missing;
    return [
      { label: "+ pos", count: pos, state: "is-good", title: "正标签", universeTotal: n },
      { label: "− neg", count: neg, state: "is-bad", title: "负标签", universeTotal: n },
      { label: "0 flat", count: zero, state: "is-mid", title: "近零/无振幅", universeTotal: n },
      { label: "missing", count: missing, state: "is-warn", title: "缺标签", universeTotal: n },
    ].filter((r) => r.count > 0);
  }

  function renderLabelPortrait(data) {
    const head = `<div class="quant-bars-bar-head" aria-hidden="true"><span>Bucket</span><span>Share</span><span>N</span></div>`;
    const lp = data && data.label_portrait;
    if (!lp || lp.success === false) {
      if (labelTauMeta) labelTauMeta.textContent = "开→收";
      if (labelPathMeta) labelPathMeta.textContent = "极值序";
      if (labelJointMeta) labelJointMeta.textContent = "τ↔path";
      renderBarRows(labelTauBody, [], { emptyText: "暂无标签画像", head });
      renderBarRows(labelPathBody, [], { emptyText: "暂无标签画像", head });
      renderBarRows(labelJointBody, [], { emptyText: "暂无同号统计", head });
      return;
    }
    const days = Number(lp.days_scanned) || 0;
    const tau = lp.tau || {};
    const path = lp.path || {};
    const joint = lp.joint || {};
    const tauShare =
      tau.pos_share != null && Number.isFinite(Number(tau.pos_share))
        ? `${Math.round(Number(tau.pos_share) * 1000) / 10}%+`
        : "—";
    const pathShare =
      path.pos_share != null && Number.isFinite(Number(path.pos_share))
        ? `${Math.round(Number(path.pos_share) * 1000) / 10}%+`
        : "—";
    const sameRate =
      joint.same_sign_rate != null && Number.isFinite(Number(joint.same_sign_rate))
        ? `${Math.round(Number(joint.same_sign_rate) * 1000) / 10}%`
        : "—";
    if (labelTauMeta) labelTauMeta.textContent = `n=${days} · ${tauShare}`;
    if (labelPathMeta) labelPathMeta.textContent = `n=${days} · ${pathShare}`;
    if (labelJointMeta) {
      labelJointMeta.textContent = `同号 ${sameRate}${lp.cached ? " · cache" : ""}`;
    }
    renderBarRows(labelTauBody, labelSignRows(tau, days), {
      emptyText: "无 τ 标签日",
      head,
    });
    renderBarRows(labelPathBody, labelSignRows(path, days), {
      emptyText: "无 path 标签日",
      head,
    });
    const signedN =
      Number(joint.signed_n) ||
      Number(joint.same_sign || 0) + Number(joint.opposite_sign || 0);
    const jointRows = [
      {
        label: "same",
        count: Number(joint.same_sign) || 0,
        state: "is-good",
        title: "τ 与 path 标签同号",
        universeTotal: signedN || days,
      },
      {
        label: "opp",
        count: Number(joint.opposite_sign) || 0,
        state: "is-bad",
        title: "τ 与 path 标签异号",
        universeTotal: signedN || days,
      },
      {
        label: "flat",
        count: Number(joint.flat) || 0,
        state: "is-mid",
        title: "任一侧近零/缺失",
        universeTotal: days,
      },
    ].filter((r) => r.count > 0);
    renderBarRows(labelJointBody, jointRows, {
      emptyText: "无双标签日",
      head,
    });
  }

  function renderFoot(data) {
    if (!footEl) return;
    if (!data || !data.success) {
      footEl.innerHTML = "";
      return;
    }
    const backend = backendLabel(data.bars_backend);
    const jobLine = formatClusterJobFootLine(data.refresh_job || lastStatusJob);
    footEl.innerHTML = `<div class="quant-bars-foot-grid">
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Store</span><span class="quant-bars-foot-v">${esc(backend)} · OHLCV 5m</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Scope</span><span class="quant-bars-foot-v">watching · Limit ${esc(data.watching_limit ?? "—")}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">强更 Job</span><span class="quant-bars-foot-v">${esc(jobLine)}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Gate</span><span class="quant-bars-foot-v">Ready ≥ ${esc(data.min_span_days ?? MIN_SPAN_DAYS)} 交易日 · ŷ_path 软闸</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Write</span><span class="quant-bars-foot-v">增量补齐 / 强更 5m · ≠ 日K · ≠ 现算 ŷ</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Labels</span><span class="quant-bars-foot-v">τ=分钟开→收% · path=极值序% · 每票≤${esc(data.label_portrait?.max_days_per_code ?? 120)}d</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Downstream</span><span class="quant-bars-foot-v">ŷ_τ@10:30 · ŷ_path · 调仓 · 做T · tip</span></div>
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
      applyJobFailure(new Error(job.error || job.message || "5m 强更失败"), job);
      return;
    }
    if (job.status === "done") {
      applyJobSuccess(job);
    }
  }

  function renderKpis(data) {
    const total = Number(data.universe_count) || 0;
    const ok = Number(data.cached_ok) || 0;
    const short = Number(data.short) || 0;
    const missing = Number(data.missing) || 0;
    const med = Number(data.minute_span_days_med) || 0;
    const pct = total > 0 ? Math.round((1000 * ok) / total) / 10 : 0;
    const minSpan = Number(data.min_span_days) || MIN_SPAN_DAYS;

    if (kpiOk) kpiOk.textContent = total > 0 ? `${pct}%` : "—";
    if (kpiOkSub) kpiOkSub.textContent = total > 0 ? `≥${minSpan}d · ${ok} / ${total}` : `≥${minSpan}d · — / —`;
    if (kpiSpan) kpiSpan.textContent = med > 0 ? `${med}d` : "—";
    if (kpiSpanSub) {
      kpiSpanSub.textContent =
        data.minute_span_days_min && data.minute_span_days_max
          ? `${data.minute_span_days_min}→${data.minute_span_days_max}`
          : "交易日";
    }
    if (kpiShort) kpiShort.textContent = String(short);
    if (kpiMissing) kpiMissing.textContent = String(missing);

    setKpiCard("ok", total <= 0 ? "is-empty" : data.coverage_ok ? "is-good" : "is-mid");
    setKpiCard("span", med >= minSpan ? "is-good" : med > 0 ? "is-mid" : "is-empty");
    setKpiCard("short", short > 0 ? "is-bad" : total > 0 ? "is-good" : "is-empty");
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
      renderCoverageBar({ universe_count: 0, cached_ok: 0, short: 0, missing: 0, span_distribution: [] });
      renderLabelPortrait(null);
      if (footEl) footEl.innerHTML = "";
      return;
    }
    renderContextStrip(data);
    renderKpis(data);
    try {
      syncOverviewMinute(data);
    } catch (_) {
      /* overview optional */
    }
    renderCoverageBar(data);
    renderLabelPortrait(data);
    renderFoot(data);
  }

  async function refreshCoverageKpis() {
    if (kpiInflight) return kpiInflight;
    const limit = BARS_WATCHING_LIMIT;
    kpiInflight = (async () => {
      const { ok, data } = await apiFetch(
        `/api/quant/cluster-minute/status?watching_limit=${encodeURIComponent(limit)}&min_span_days=${MIN_SPAN_DAYS}&include_label_portrait=0`
      );
      if (!ok || !data?.success) return null;
      paint(data, { skipHead: true });
      return data;
    })().finally(() => {
      kpiInflight = null;
    });
    return kpiInflight;
  }

  async function refreshStatus() {
    const limit = BARS_WATCHING_LIMIT;
    const { ok, data, error } = await apiFetch(
      `/api/quant/cluster-minute/status?watching_limit=${encodeURIComponent(limit)}&min_span_days=${MIN_SPAN_DAYS}`
    );
    if (!ok) {
      paint(null);
      msgEl.textContent = error || "读取 5m 状态失败";
      setProStatusChip(chip, "error", "ERR");
      if (!inflight) setMinuteBusy(false);
      return null;
    }
    paint(data, { skipHead: !!jobFailure || !!jobSuccess });
    if (jobFailure) applyJobFailure({ message: jobFailure.errText }, jobFailure.job);
    else if (jobSuccess) applyJobSuccess(jobSuccess.job);
    else if (data.refresh_job) handleRefreshJob(data.refresh_job);
    const jobSt = data.refresh_job && data.refresh_job.status;
    if (!inflight && !jobFailure && !jobSuccess && jobSt !== "running") {
      setMinuteBusy(false);
    }
    return data;
  }

  async function syncJobSlot() {
    const { ok, data } = await apiFetch("/api/jobs/cluster-minute-refresh?progress=1");
    const job = unwrapJobSnap(data);
    if (!ok || !job) return null;
    lastPolledJob = job;
    lastStatusJob = job.status && job.status !== "idle" ? job : lastStatusJob;
    const st = job.status;
    if (st === "running") {
      clearJobPanels();
      if (!inflight) {
        setMinuteButtonsDisabled(true);
        inflight = (async () => {
          try {
            const result = await waitClusterMinuteJob(job.id);
            clearJobPanels();
            applyJobSuccess({ ...job, result_summary: result?.minute_warmup, result });
            setMinuteBusy(false);
            await refreshStatus();
          } catch (err) {
            applyJobFailure(err, lastPolledJob);
          } finally {
            setMinuteButtonsDisabled(false);
            inflight = null;
          }
        })();
      }
      return job;
    }
    if (st === "failed") {
      applyJobFailure(new Error(job.error || job.message || "5m 强更失败"), job);
      return job;
    }
    if (st === "done") {
      applyJobSuccess(job);
      return job;
    }
    return job;
  }

  async function waitClusterMinuteJob(jobId, mode = "full") {
    const started = Date.now();
    const absoluteCapMs = 30 * 60 * 1000;
    let sawOwnJob = false;
    let pollN = 0;
    const waitLabel = mode === "topup" ? "增量补齐" : "强更 5m";
    paintProgressPanel({ message: `提交${waitLabel}…（排队后台 Job）`, pct: 0 }, { pollStartedAt: started, mode });
    while (Date.now() - started < absoluteCapMs) {
      const { ok, data } = await apiFetch("/api/jobs/cluster-minute-refresh?progress=1");
      const job = unwrapJobSnap(data);
      if (!ok || !job) {
        paintProgressPanel({ message: "等待 Job 心跳…", pct: 0 }, { pollStartedAt: started });
        await new Promise((r) => setTimeout(r, 800));
        continue;
      }
      if (jobId && job.id && job.id !== jobId) {
        if (sawOwnJob) break;
        paintProgressPanel({ message: "等待本任务接管…", pct: 0 }, { pollStartedAt: started });
        await new Promise((r) => setTimeout(r, 600));
        continue;
      }
      if (jobId && job.id === jobId) sawOwnJob = true;
      lastPolledJob = job;
      if (job.status && job.status !== "idle") lastStatusJob = job;
      const st = job.status;
      if (st === "done") {
        paintProgressDone(job);
        return job.result || { success: true };
      }
      if (st === "failed") {
        throw new Error(job.error || job.message || "5m 强更失败");
      }
      if (st === "idle" && sawOwnJob) {
        throw new Error("5m 任务已结束但未返回结果");
      }
      const badge = jobStatusBadge(job);
      setProStatusChip(chip, "busy", badge?.text || "SYNC");
      setMinuteBusy(true);
      paintProgressPanel(job, { pollStartedAt: started });
      const pct = Number(job.pct);
      const line = job.message || "强更 5m…";
      const { heartbeat } = jobTimings(job, started);
      const headBits = [line];
      if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
      headBits.push(fmtDuration((Date.now() - started) / 1000));
      if (heartbeat != null) headBits.push(`心跳 ${fmtDuration(heartbeat)}`);
      msgEl.textContent = headBits.join(" · ");
      pollN += 1;
      if (pollN % 6 === 0) refreshCoverageKpis().catch(() => {});
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("5m 强更超时");
  }

  async function startRefresh(mode = "full") {
    if (inflight) return inflight;
    const modeS = mode === "topup" ? "topup" : "full";
    const label = modeS === "topup" ? "增量补齐" : "强更 5m";
    inflight = (async () => {
      clearJobPanels();
      setMinuteButtonsDisabled(true);
      setMinuteBusy(true);
      setProStatusChip(chip, "busy", "QUEUE");
      msgEl.textContent = `提交${label}…`;
      paintProgressPanel(
        { message: `提交${label}…`, pct: 0 },
        { pollStartedAt: Date.now(), mode: modeS }
      );
      try {
        const limit = BARS_WATCHING_LIMIT;
        const body = {
          lookback_days: LOOKBACK_DAYS,
          watching_limit: limit,
          period: "5",
          min_span_days: MIN_SPAN_DAYS,
          mode: modeS,
        };
        if (modeS === "topup") body.topup_lookback_days = TOPUP_LOOKBACK_DAYS;
        const { ok, data, error } = await apiFetch("/api/quant/cluster-minute/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!ok) throw new Error(error || "提交失败");
        let result = data;
        if (data && data.background && data.job) {
          if (data.reused) {
            paintProgressPanel(
              {
                message: data.job.message || `复用进行中的 5m Job…`,
                pct: data.job.pct,
                current: data.job.current,
                total: data.job.total,
              },
              { pollStartedAt: Date.now(), mode: modeS }
            );
          }
          result = await waitClusterMinuteJob(data.job.id, modeS);
        }
        if (result && result.status) {
          paint(result.status);
        } else {
          await refreshStatus();
        }
        if (result && result.success === false) {
          throw new Error(result.error || `${label}失败`);
        }
        const mw = (result && result.minute_warmup) || {};
        if (mw.warmed != null) {
          const bits = [`${label}完成`, `${mw.warmed}/${mw.total ?? "—"}`];
          if (mw.skipped_today != null && Number(mw.skipped_today) > 0) {
            bits.push(`今日 ${mw.skipped_today}`);
          }
          if (mw.skipped_aligned != null) bits.push(`跳过 ${mw.skipped_aligned}`);
          if (mw.topped != null) bits.push(`topup ${mw.topped}`);
          msgEl.textContent = bits.join(" · ");
        }
        clearJobPanels();
        applyJobSuccess(lastPolledJob || { result_summary: mw, result });
      } catch (err) {
        applyJobFailure(err, lastPolledJob);
        throw err;
      } finally {
        setMinuteButtonsDisabled(false);
        inflight = null;
        if (!jobFailure && !jobSuccess) setMinuteBusy(false);
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

  on("quant-cluster-minute-refresh", "click", (e) => {
    e.preventDefault();
    startRefresh("full").catch(() => {});
  });
  if (topupBtn) {
    on("quant-cluster-minute-topup", "click", (e) => {
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
