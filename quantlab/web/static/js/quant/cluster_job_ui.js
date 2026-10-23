/** 日线 / 分钟线更新 Job 进度与状态条（共享文案） */

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
  try {
    const now = Date.now() / 1000;
    const started = Number(job?.started_at);
    const updated = Number(job?.updated_at);
    let elapsed = null;
    if (Number.isFinite(started) && started > 0) elapsed = now - started;
    else if (pollStartedAt) elapsed = (Date.now() - Number(pollStartedAt)) / 1000;
    const heartbeat =
      Number.isFinite(updated) && updated > 0 ? now - updated : null;
    return {
      elapsed: Number.isFinite(elapsed) ? elapsed : null,
      heartbeat: Number.isFinite(heartbeat) ? heartbeat : null,
    };
  } catch (_) {
    return { elapsed: null, heartbeat: null };
  }
}

/** 距上次心跳秒数；缺字段时返回 null，勿直接解构未定义变量。 */
export function jobHeartbeatAge(job) {
  return jobTimings(job).heartbeat;
}

/** 软超时用：无心跳或心跳过久视为 stale。 */
export function isJobHeartbeatStale(job, freshSec = 90) {
  const hb = jobHeartbeatAge(job);
  const fresh = Math.max(1, Number(freshSec) || 90);
  return hb == null || hb >= fresh;
}

function jobEtaSec(job, elapsed) {
  const cur = Number(job?.current);
  const tot = Number(job?.total);
  if (cur > 0 && tot > cur && elapsed != null && elapsed > 2) {
    return (tot - cur) * (elapsed / cur);
  }
  return null;
}

function finestCount(message) {
  const re = /(\d+)\s*\/\s*(\d+)/g;
  let best = null;
  const s = String(message || "");
  let match;
  while ((match = re.exec(s))) {
    const done = Number(match[1]);
    const tot = Number(match[2]);
    if (!(tot > 0)) continue;
    if (!best || tot > best.tot) best = { done, tot };
  }
  return best;
}

function buildClusterJobFacts(job, { pollStartedAt } = {}) {
  const facts = [];
  const cur = Number(job?.current);
  const tot = Number(job?.total);
  const counted = finestCount(job?.message);
  // 宏步骤 current 在 L1 网格里停在 0；票数写在 message 里，进度格要显示它
  if (counted && counted.tot > 8) {
    facts.push(`进度 <strong>${counted.done}/${counted.tot}</strong>`);
  } else if (cur > 0 && tot > 0) {
    facts.push(`进度 <strong>${cur}/${tot}</strong>`);
  } else {
    const pct = Number(job?.pct);
    if (Number.isFinite(pct) && pct > 0) {
      facts.push(`进度 <strong>${Math.round(pct)}%</strong>`);
    }
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

/** 日线 / 分钟线进度句：拉日线|拉5m N/M（远端 · 已齐|跳过 · 失败 · …） */
export function parseL0ProgressMessage(raw) {
  const msg = String(raw || "");
  const out = {
    raw: msg,
    phase: "fetch",
    done: null,
    total: null,
    remote: null,
    cache: null,
    cacheLabel: "",
    todaySkip: null,
    alignedSkip: null,
    skipCount: null,
    failed: null,
    timedOutCount: null,
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
  else if (/完成|已对齐|Coverage/.test(msg) && !/拉日线|拉5m/.test(msg)) out.phase = "done";
  else if (/拉日线缺口/.test(msg)) out.phase = "gap";
  else if (/(?:拉日线|拉5m)\s*0\//.test(msg) && /上限|并发/.test(msg)) out.phase = "start";

  const frac = msg.match(/(\d+)\s*\/\s*(\d+)/);
  if (frac) {
    out.done = Number(frac[1]);
    out.total = Number(frac[2]);
  }
  const remote = msg.match(/远端\s*(\d+)/);
  if (remote) out.remote = Number(remote[1]);
  const aligned = msg.match(/已齐\s*(\d+)/);
  const cacheHit = msg.match(/缓存\s*(\d+)/);
  if (aligned && !/已齐 as-of/.test(msg)) {
    out.cache = Number(aligned[1]);
    out.cacheLabel = "已齐";
  } else if (cacheHit) {
    out.cache = Number(cacheHit[1]);
    out.cacheLabel = "缓存命中";
  }
  const today = msg.match(/今日跳过\s*[×x]?\s*(\d+)/);
  const alignedSkip = msg.match(/对齐跳过\s*[×x]?\s*(\d+)/);
  let rest = msg;
  if (today) {
    out.todaySkip = Number(today[1]);
    rest = rest.replace(today[0], " ");
  }
  if (alignedSkip) {
    out.alignedSkip = Number(alignedSkip[1]);
    rest = rest.replace(alignedSkip[0], " ");
  }
  const genericSkip = rest.match(/跳过\s*[×x]?\s*(\d+)/);
  if (genericSkip) out.skipCount = Number(genericSkip[1]);
  const failed = msg.match(/失败\s*(\d+)/);
  if (failed) out.failed = Number(failed[1]);
  const timeoutN = msg.match(/超时\s+(\d+)/);
  if (timeoutN) out.timedOutCount = Number(timeoutN[1]);
  // 上限 180s / 剩 12s 不是已用时间
  const elapsedSrc = msg.replace(/上限\s*\d+s/g, " ").replace(/剩\s*\d+s/g, " ");
  const elapsed2 = elapsedSrc.match(/(\d+)s\s*·/);
  const elapsed = elapsedSrc.match(/(\d+)\s*s(?!\s*·)/) || elapsedSrc.match(/·\s*(\d+)s/);
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

export function l0PhaseChip(phase) {
  const map = {
    queue: "QUEUE",
    index: "INDEX",
    start: "START",
    gap: "GAP",
    timeout: "WRAP",
    fetch: "SYNC",
    done: "DONE",
  };
  return map[phase] || "SYNC";
}

export function l0PhaseLabel(phase, kind) {
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
      return kind === "minute" ? "拉5m" : "拉日线";
  }
}

export function l0ParsedFacts(parsed) {
  const done = parsed?.done;
  const remote = parsed?.remote;
  let cache = parsed?.cache;
  let cacheLabel = parsed?.cacheLabel || "";
  if (
    cache == null &&
    parsed?.skipCount == null &&
    parsed?.todaySkip == null &&
    parsed?.alignedSkip == null &&
    remote != null &&
    done != null &&
    parsed?.phase !== "gap"
  ) {
    cache = Math.max(0, Number(done) - Number(remote));
    cacheLabel = "缓存命中";
  }
  const facts = [];
  if (remote != null) facts.push(`远端 <strong>${remote}</strong>`);
  if (parsed?.todaySkip != null && (Number(parsed.todaySkip) > 0 || Number(done) > 0)) {
    facts.push(`今日跳过 <strong>${parsed.todaySkip}</strong>`);
  }
  if (parsed?.alignedSkip != null && (Number(parsed.alignedSkip) > 0 || Number(done) > 0)) {
    facts.push(`对齐跳过 <strong>${parsed.alignedSkip}</strong>`);
  }
  if (parsed?.skipCount != null && Number(done) > 0) {
    facts.push(`跳过 <strong>${parsed.skipCount}</strong>`);
  }
  // 0/N 启动瞬间 done=0 会算出「缓存命中 0」，像没吃到本地仓；有完成票再报
  if (cache != null && Number(done) > 0) {
    const label = cacheLabel || (parsed?.phase === "gap" ? "已齐" : "缓存命中");
    facts.push(`${label} <strong>${cache}</strong>`);
  }
  if (parsed?.failed != null && Number(parsed.failed) > 0) {
    facts.push(`失败 <strong>${parsed.failed}</strong>`);
  }
  if (parsed?.timedOutCount != null && Number(parsed.timedOutCount) > 0) {
    facts.push(`超时 <strong>${parsed.timedOutCount}</strong>`);
  }
  if (parsed?.remainSec != null && parsed?.phase !== "timeout") {
    facts.push(`批上限剩 <strong>${fmtDuration(parsed.remainSec)}</strong>`);
  }
  if (parsed?.workers != null) facts.push(`并发 <strong>${parsed.workers}</strong>`);
  if (parsed?.capSec != null) facts.push(`批上限 <strong>${fmtDuration(parsed.capSec)}</strong>`);
  return facts;
}

export function l0RunningHead(job, { pollStartedAt } = {}) {
  try {
    const parsed = parseL0ProgressMessage(job?.message || job?.error || "");
    const line = String(parsed?.raw || "").trim();
    const headBits = [];
    if (line) headBits.push(line);
    const pct = Number(job?.pct);
    if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
    const { elapsed, heartbeat } = jobTimings(job, pollStartedAt) || {};
    const elapsedSec = parsed?.elapsedSec != null ? parsed.elapsedSec : elapsed;
    if (elapsedSec != null && Number.isFinite(Number(elapsedSec))) {
      headBits.push(fmtDuration(elapsedSec));
    }
    if (heartbeat != null && Number.isFinite(Number(heartbeat))) {
      headBits.push(`心跳 ${fmtDuration(heartbeat)}`);
    }
    if (parsed?.remainSec != null) headBits.push(`剩 ${fmtDuration(parsed.remainSec)}`);
    return {
      chip: l0PhaseChip(parsed?.phase),
      text: headBits.join(" · ") || "更新中",
      parsed,
    };
  } catch (_) {
    return { chip: "SYNC", text: "更新中", parsed: null };
  }
}

export function fmtCoverageTime(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  try {
    let d = null;
    if (/^\d+(\.\d+)?$/.test(s)) {
      const n = Number(s);
      d = new Date(n > 1e12 ? n : n * 1000);
    } else {
      // 本地仓 ISO 常无时区；当本地墙钟解析，避免 UTC 偏移把「上次更新」挪一天
      d = new Date(s.includes("T") ? s : s.replace(" ", "T"));
    }
    if (!d || Number.isNaN(d.getTime())) return s.replace("T", " ").slice(0, 16);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch (_) {
    return s.replace("T", " ").slice(0, 16);
  }
}

function coverageDay(raw) {
  const s = String(raw || "").trim().slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "";
}

/** 日线 / 分钟线覆盖条：数据落到哪一天、仓上次写入时间。目标日只在和末 bar 不一致时出现。 */
export function l0CoverageFacts(data) {
  try {
    if (!data || typeof data !== "object") return [];
    const bits = [];
    const hi = coverageDay(data.last_bar_max);
    const lo = coverageDay(data.last_bar_min);
    if (hi && lo && lo !== hi) bits.push(`更新至 ${lo}→${hi}`);
    else if (hi) bits.push(`更新至 ${hi}`);
    const when = fmtCoverageTime(data.last_fetched_at);
    if (when) bits.push(`上次更新 ${when}`);
    const target = coverageDay(data.expected_latest_bar);
    if (target && target !== hi) bits.push(`目标 ${target}`);
    return bits;
  } catch (_) {
    return [];
  }
}

export function l0DoneTitle(sum) {
  const topup =
    String(sum?.mode || "") === "topup" ||
    String(sum?.kind || "").includes("topup") ||
    sum?.force_latest === true ||
    sum?.topped != null ||
    (sum?.full_window !== true && String(sum?.note || "").includes("增量"));
  return topup ? "增量完成" : "强更完成";
}

/** 完成态计数，日线与分钟线同一顺序：完成量 · 跳过/已齐 · 失败/超时 · 差异。 */
export function l0DoneBits(sum, kind) {
  const bits = [];
  if (kind === "minute") {
    if (sum?.warmed != null) bits.push(`完成 ${sum.warmed}/${sum.total ?? "—"}`);
    if (Number(sum?.skipped_today) > 0) bits.push(`今日跳过 ${sum.skipped_today}`);
    if (sum?.skipped_aligned != null) bits.push(`对齐跳过 ${sum.skipped_aligned}`);
    else if (sum?.skipped_ready != null) bits.push(`跳过 ${sum.skipped_ready}`);
    if (sum?.topped != null) bits.push(`补拉 ${sum.topped}`);
    if (Number(sum?.bootstrapped) > 0) bits.push(`全窗 ${sum.bootstrapped}`);
    if (Number(sum?.timed_out) > 0) bits.push(`超时 ${sum.timed_out}`);
    return bits;
  }
  if (sum?.remote_count != null) bits.push(`远端 ${sum.remote_count}/${sum.total ?? "—"}`);
  if (sum?.gap_count != null) bits.push(`缺口 ${sum.gap_count}`);
  if (sum?.cache_count != null) bits.push(`已齐 ${sum.cache_count}`);
  if (Number(sum?.failed_count) > 0) bits.push(`失败 ${sum.failed_count}`);
  const diff = sum?.diff;
  if (diff && diff.price_count != null) {
    bits.push(`价变 ${diff.price_count}`);
    bits.push(`量纲 ${diff.volume_count ?? 0}`);
    bits.push(`只补尾 ${diff.tail_count ?? 0}`);
  }
  return bits;
}

/** 完成态状态条：标题 · 计数 · 更新至 · 上次更新。没有写入时间时才退回相对年龄。 */
export function l0DoneHead(sum, kind, coverage, job) {
  try {
    const head = [
      l0DoneTitle(sum),
      ...l0DoneBits(sum || {}, kind),
      ...l0CoverageFacts(coverage),
    ];
    const hasWhen = head.some((bit) => String(bit).startsWith("上次更新"));
    const updated = Number(job?.updated_at);
    if (!hasWhen && Number.isFinite(updated) && updated > 0) {
      head.push(fmtAgeSec(Date.now() / 1000 - updated));
    }
    return head.filter(Boolean);
  } catch (_) {
    return [l0DoneTitle(sum)];
  }
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
  if (st === "failed") return { text: "更新失败", cls: "is-bad" };
  if (st === "done") {
    const sum = job.result_summary || job.result?.bars_refresh || job.result?.minute_warmup || {};
    return { text: l0DoneTitle(sum), cls: "is-ok" };
  }
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
 *   tone?: "primary" | "warn",
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
      ? errText || String(job?.error || job?.message || "更新失败")
      : job && (job.message || job.error)
        ? String(job.message || job.error)
        : phase;
  const facts = buildClusterJobFacts(job, { pollStartedAt });
  if (Array.isArray(opts.extraFacts)) facts.push(...opts.extraFacts);
  // tone 显式优先；默认 primary。fail 不再暗绑 warn——否则增量/强更失败刷新后整块退回黄色
  const tone =
    opts.tone === "warn" || opts.tone === "primary"
      ? opts.tone
      : opts.isWarn
        ? "warn"
        : "primary";
  const isWarn = tone === "warn";
  const isDone = mode === "done";
  progressEl.hidden = false;
  progressEl.classList.add("is-active");
  progressEl.classList.toggle("is-warn", isWarn);
  progressEl.classList.toggle("is-done", isDone);
  progressEl.dataset.tone = tone;
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

/** 日线/分钟线状态条色调：primary=蓝（增量/强更），warn=黄（物化）。 */
export function setDeskStatusTone(stripOrCard, tone) {
  const els = Array.isArray(stripOrCard)
    ? stripOrCard
    : stripOrCard
      ? [stripOrCard]
      : [];
  for (const el of els) {
    if (!el) continue;
    if (tone === "primary" || tone === "warn") el.dataset.tone = tone;
    else delete el.dataset.tone;
  }
}
