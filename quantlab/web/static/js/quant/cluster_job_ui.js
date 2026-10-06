/** 日线 / 分钟线强更 Job 进度展示（共享） */

export function unwrapJobSnap(data) {
  if (!data || typeof data !== "object") return null;
  if (data.job && typeof data.job === "object") return data.job;
  return data;
}

export function fmtDuration(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m${String(r).padStart(2, "0")}s`;
}

export function fmtAgeSec(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m 前`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return m > 0 ? `${h}h${m}m 前` : `${h}h 前`;
}

export function jobTimings(job, pollStartedAt) {
  const now = Date.now() / 1000;
  const started = Number(job?.started_at);
  const updated = Number(job?.updated_at);
  let elapsed = null;
  if (started > 0) elapsed = now - started;
  else if (pollStartedAt) elapsed = (Date.now() - pollStartedAt) / 1000;
  const heartbeat = updated > 0 ? now - updated : null;
  return { elapsed, heartbeat };
}

function jobEtaSec(job, elapsed) {
  const cur = Number(job?.current);
  const tot = Number(job?.total);
  if (cur > 0 && tot > cur && elapsed != null && elapsed > 2) {
    return (tot - cur) * (elapsed / cur);
  }
  return null;
}

function buildClusterJobFacts(job, { pollStartedAt } = {}) {
  const facts = [];
  const cur = Number(job?.current);
  const tot = Number(job?.total);
  if (cur > 0 && tot > 0) {
    facts.push(`进度 <strong>${cur}/${tot}</strong>`);
  }
  const { elapsed, heartbeat } = jobTimings(job, pollStartedAt);
  if (elapsed != null) facts.push(`已用 <strong>${fmtDuration(elapsed)}</strong>`);
  const eta = jobEtaSec(job, elapsed);
  if (eta != null) facts.push(`预计剩 <strong>${fmtDuration(eta)}</strong>`);
  if (heartbeat != null) {
    const hb = fmtDuration(heartbeat);
    facts.push(
      heartbeat > 90
        ? `心跳 <strong class="quant-job-hb-stale">${hb}</strong>`
        : `心跳 <strong>${hb}</strong>`
    );
  }
  const id = job?.id ? String(job.id).slice(0, 8) : "";
  if (id) facts.push(`Job <strong>${id}</strong>`);
  return facts;
}

export function formatClusterJobFootLine(job) {
  if (!job || !job.status || job.status === "idle") return "—";
  const st = job.status;
  const cur = Number(job.current);
  const tot = Number(job.total);
  const prog = cur > 0 && tot > 0 ? `${cur}/${tot}` : "";
  const pct = Number(job.pct);
  const pctTxt = Number.isFinite(pct) && pct > 0 ? `${Math.round(pct)}%` : "";
  const updated = Number(job.updated_at);
  const age = updated > 0 ? fmtAgeSec(Date.now() / 1000 - updated) : "";
  if (st === "running") {
    return ["进行中", prog, pctTxt, age].filter(Boolean).join(" · ");
  }
  if (st === "failed") {
    const err = String(job.error || job.message || "失败").slice(0, 80);
    return ["失败", prog, err, age].filter(Boolean).join(" · ");
  }
  if (st === "done") {
    const sum = job.result_summary || {};
    const bits = ["完成"];
    if (sum.remote_count != null && sum.total != null) {
      bits.push(`远端 ${sum.remote_count}/${sum.total}`);
    }
    if (sum.warmed != null && sum.total != null) {
      bits.push(`预热 ${sum.warmed}/${sum.total}`);
    }
    if (sum.skipped_ready != null) bits.push(`跳过 Ready ${sum.skipped_ready}`);
    if (age) bits.push(age);
    return bits.join(" · ");
  }
  return String(job.message || st);
}

export function jobStatusBadge(job) {
  if (!job || job.status === "idle") return null;
  const st = job.status;
  if (st === "running") {
    const pct = Number(job.pct);
    return {
      text: Number.isFinite(pct) && pct > 0 ? `更新 ${Math.round(pct)}%` : "更新中",
      cls: "is-neutral",
    };
  }
  if (st === "failed") return { text: "强更失败", cls: "is-warn" };
  if (st === "done") return { text: "强更完成", cls: "is-ok" };
  return null;
}

/**
 * @param {HTMLElement | null} progressEl
 * @param {{
 *   phase: string,
 *   job?: object,
 *   pollStartedAt?: number,
 *   hint?: string,
 *   esc: (s: unknown) => string,
 *   mode?: "running" | "fail" | "done",
 *   errText?: string,
 * }} opts
 */
export function paintClusterJobPanel(progressEl, opts) {
  if (!progressEl) return;
  const {
    phase,
    job = null,
    pollStartedAt,
    hint = "",
    esc,
    mode = "running",
    errText = "",
  } = opts;
  const pctJob = Number(job?.pct);
  const current = Number(job?.current);
  const total = Number(job?.total);
  let pct =
    Number.isFinite(pctJob) && pctJob > 0
      ? pctJob
      : current > 0 && total > 0
        ? Math.round((1000 * current) / total) / 10
        : mode === "done"
          ? 100
          : 0;
  pct = Math.max(0, Math.min(100, pct));
  const msg =
    mode === "fail"
      ? errText || String(job?.error || job?.message || "强更失败")
      : job && (job.message || job.error)
        ? String(job.message || job.error)
        : phase;
  const facts = buildClusterJobFacts(job, { pollStartedAt });
  if (Array.isArray(opts.extraFacts)) facts.push(...opts.extraFacts);
  const isWarn = mode === "fail" || !!opts.isWarn;
  const isDone = mode === "done";
  progressEl.hidden = false;
  progressEl.classList.add("is-active");
  progressEl.classList.toggle("is-warn", isWarn);
  progressEl.classList.toggle("is-done", isDone);
  progressEl.innerHTML = `<div class="quant-bars-progress-head">
      <span class="quant-bars-progress-phase">${esc(phase)}</span>
      <span class="quant-bars-progress-pct">${pct > 0 ? `${Math.round(pct)}%` : isDone ? "100%" : "…"}</span>
    </div>
    <div class="quant-bars-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}">
      <div class="quant-bars-progress-fill" style="width:${pct.toFixed(1)}%"></div>
    </div>
  ${facts.length ? `<div class="quant-bars-progress-facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>` : ""}
    <p class="quant-bars-progress-msg${mode === "fail" ? " quant-bars-progress-msg--err" : ""}">${esc(msg)}</p>
    ${hint ? `<p class="quant-bars-progress-hint">${hint}</p>` : ""}`;
}
