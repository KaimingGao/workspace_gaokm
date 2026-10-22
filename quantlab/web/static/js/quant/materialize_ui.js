/** 物化 L1/L2：日线区 open；分钟线区 τ 网格 / 按钟。状态条对齐日 K / 5m 强更。 */

import { setProStatusChip } from "./factor_corr_ui.js";
import {
  fmtDuration,
  fmtAgeSec,
  jobTimings,
  paintClusterJobPanel,
  unwrapJobSnap,
  setDeskStatusTone,
} from "./cluster_job_ui.js";
import { BARS_DAILY_LOOKBACK } from "./params.js";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/** apiFetch → 解包 data。 */
async function fetchData(apiFetch, url, opts) {
  const { ok, data, error } = await apiFetch(url, opts);
  if (!ok) {
    const err = new Error(error || "请求失败");
    err.data = data;
    throw err;
  }
  return data || {};
}

function phaseFromMessage(msg) {
  const s = String(msg || "");
  if (/排队|提交/.test(s)) return { chip: "QUEUE", phase: "排队" };
  // 「ŷ_τc 网格 12/800」里 τ 与「网格」不相邻，不能写成 τ\s*网格
  if (/网格|L1\s*τ|τL1/i.test(s)) return { chip: "τL1", phase: "物化 τL1" };
  if (/\bL1\b|物化 L1/.test(s)) return { chip: "L1", phase: "物化 L1" };
  if (/\bL2\b|物化 L2|Cube/i.test(s)) return { chip: "L2", phase: "物化 L2" };
  if (/完成/.test(s)) return { chip: "DONE", phase: "物化完成" };
  return { chip: "SYNC", phase: "物化中" };
}

function summarizeL2(byClock) {
  if (!byClock || typeof byClock !== "object") return "";
  const keys = Object.keys(byClock);
  if (!keys.length) return "";
  if (keys.length === 1 && keys[0] === "open") {
    const o = byClock.open || {};
    if (o.cubes_hit == null) return "";
    return `L2 hit ${o.cubes_hit}/built ${o.cubes_built || 0}`;
  }
  let hit = 0;
  let built = 0;
  let n = 0;
  for (const k of keys) {
    const c = byClock[k] || {};
    if (c.cubes_hit != null) hit += Number(c.cubes_hit) || 0;
    if (c.cubes_built != null) built += Number(c.cubes_built) || 0;
    n += 1;
  }
  return `L2 ${n}钟 hit ${hit}/built ${built}`;
}

/** 优先用后端 track.headline；旧 result 无 track 时回退拼句。 */
function resultHeadBits(result, { tau = false } = {}) {
  const track = result?.track;
  if (track && typeof track === "object") {
    const bits = [];
    if (track.headline) bits.push(String(track.headline));
    else bits.push(tau ? "物化完成（τ）" : "物化完成");
    if (track.steps_ok) bits.push(`步骤 ${track.steps_ok}`);
    if (track.schema != null) bits.push(`schema v${track.schema}`);
    if (track.codes_n != null) bits.push(`${track.codes_n} 只`);
    return bits;
  }
  const bits = [tau ? "物化完成（τ）" : "物化完成"];
  if (result?.elapsed_s != null) bits.push(`${result.elapsed_s}s`);
  if (tau) {
    const l1tau = result?.l1_tau_grid || {};
    if (l1tau.elapsed_s != null || (l1tau.clocks || []).length) {
      bits.push(`τL1 ${l1tau.clocks_ok || 0}/${(l1tau.clocks || []).length}`);
    }
  } else {
    const l1 = result?.l1 || {};
    if (l1.elapsed_s != null) bits.push(`L1 ${l1.elapsed_s}s`);
  }
  const l2 = result?.l2 || {};
  // 分钟物化：明示 5m 够格窗，避免误以为已覆盖拟合 lookback 全长
  if (tau && l2.minute_span) {
    const n = l2.minute_days != null ? `·${l2.minute_days}日` : "";
    bits.push(`5m窗 ${l2.minute_span}${n}`);
  }
  const l2bit = summarizeL2(l2.by_clock);
  if (l2bit) bits.push(l2bit);
  return bits;
}

function trackStageHint(result, { tau = false } = {}) {
  const stages = result?.track?.stages;
  if (!Array.isArray(stages) || !stages.length) {
    return tau ? "τ 网格 L1 + 按钟 Cube" : "open L1 + open Cube";
  }
  return stages
    .filter((s) => s && s.status !== "skip")
    .map((s) => {
      const id = String(s.id || "");
      const st = String(s.status || "");
      const el = s.elapsed_s != null ? `${s.elapsed_s}s` : "";
      const mark = st === "fail" ? "FAIL" : st === "ok" ? "ok" : st;
      return [id, mark, el].filter(Boolean).join(" ");
    })
    .join(" → ");
}

async function resolveSuggestedTauClocks(apiFetch) {
  if (typeof apiFetch !== "function") return [];
  try {
    const cat = await fetchData(apiFetch, "/api/quant/materialize/catalog");
    const sug = (cat && cat.suggested_tau_clocks) || [];
    const out = [];
    for (const c of sug) {
      const s = String(c || "").trim();
      if (s && !out.includes(s)) out.push(s);
    }
    return out;
  } catch (_) {
    return [];
  }
}

/** open 区只认 materialize_open；分钟区只认 materialize_tau（互不抢状态条）。 */
function kindMatchesRunner(kind, tau) {
  const k = String(kind || "");
  if (tau) return k === "materialize_tau";
  return k === "materialize_open";
}

function kindLabelZh(kind) {
  const k = String(kind || "");
  if (k === "materialize_open") return "日线物化";
  if (k === "materialize_tau") return "分钟 τ 物化";
  if (k === "materialize_l1l2") return "混合物化";
  return "物化";
}

function isMatKindConflict(res) {
  if (!res || res.success !== false) return false;
  if (res.conflict) return true;
  return /冲突|共用同一任务槽/.test(String(res.error || ""));
}

/** 宏步骤 total≤8；票级 336/800 等微进度不进状态句/进度条 facts。 */
function macroJobView(job) {
  if (!job || typeof job !== "object") return job;
  const t = Number(job.total);
  if (!(t > 8)) return job;
  return { ...job, current: 0, total: 0, pct: 0 };
}

function jobFracText(job) {
  const j = macroJobView(job) || {};
  const c = Number(j.current);
  const t = Number(j.total);
  if (c > 0 && t > 0) return `${c}/${t}`;
  return "";
}

/**
 * @param {object} q
 * @param {{
 *   btnId: string,
 *   chipId: string,
 *   msgId: string,
 *   progressId: string,
 *   stripId: string,
 *   cardSel: string,
 *   siblingBtnIds?: string[],
 *   tau?: boolean,
 *   isReady?: () => { ok?: boolean, reason?: string },
 *   onStart?: () => void,
 *   onBusy?: (on: boolean) => void,
 *   onSettled?: () => void,
 * }} cfg
 */
function installMaterializeRunner(q, cfg) {
  const btn = document.getElementById(cfg.btnId);
  const chip = document.getElementById(cfg.chipId);
  const msgEl = document.getElementById(cfg.msgId);
  const progressEl = document.getElementById(cfg.progressId);
  const strip = document.getElementById(cfg.stripId);
  const card = document.querySelector(cfg.cardSel);
  const body = card?.querySelector(".quant-bars-body") || null;
  const apiFetch = q && q.apiFetch;
  const esc =
    typeof q?.escapeHtml === "function"
      ? q.escapeHtml
      : (s) =>
          String(s ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");

  if (!btn || !chip || !msgEl || typeof apiFetch !== "function") {
    return { refresh: () => {}, setReady: () => {}, run: async () => {} };
  }

  const siblingBtns = (cfg.siblingBtnIds || [])
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  const isReadyFn =
    typeof cfg.isReady === "function" ? cfg.isReady : () => ({ ok: true });
  const onStart = typeof cfg.onStart === "function" ? cfg.onStart : null;
  const onBusy = typeof cfg.onBusy === "function" ? cfg.onBusy : null;
  const onSettled = typeof cfg.onSettled === "function" ? cfg.onSettled : null;
  const tau = !!cfg.tau;
  const label = "物化";
  let lastPolledJob = null;
  let running = false;

  function setBusy(on) {
    card?.classList.toggle("is-busy", !!on);
    strip?.classList.toggle("is-busy", !!on);
    body?.classList.toggle("is-busy", !!on);
    btn.classList.toggle("is-busy", !!on);
    if (on) setDeskStatusTone([strip, card], "warn");
    if (onBusy) onBusy(!!on);
  }

  function setActionDisabled(disabled) {
    btn.disabled = !!disabled;
    for (const b of siblingBtns) b.disabled = !!disabled;
  }

  function paintRunning(job, { pollStartedAt, waitPeer = false } = {}) {
    const view = macroJobView(job);
    const msg = view?.message || `${label}进行中…`;
    // 等对侧时勿用 phaseFromMessage（「DayCube」会误标成本面板 L2）
    const phase = waitPeer
      ? `等待${kindLabelZh(view?.peer_kind || (tau ? "materialize_open" : "materialize_tau"))}`
      : phaseFromMessage(msg).phase;
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase,
      job: view,
      pollStartedAt,
      esc,
      mode: "running",
      tone: "warn",
      hint: waitPeer
        ? "日线 / 分钟物化共用任务槽；对侧结束后将自动继续"
        : tau
          ? "训练网格 tc_HHMM L1 + 按钟 L2 · 依赖本地 5m"
          : "open L1（oo/tc/co）+ open L2 DayCube",
    });
  }

  function paintDone(job, result) {
    const bits = resultHeadBits(result, { tau });
    const stageHint = trackStageHint(result, { tau });
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase: tau ? "物化完成（τ）" : "物化完成",
      job: macroJobView(job),
      esc,
      mode: "done",
      tone: "warn",
      hint: [stageHint, bits.slice(1).join(" · ")].filter(Boolean).join(" · "),
    });
  }

  function paintFail(job, errText) {
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase: "物化失败",
      job: macroJobView(job),
      esc,
      mode: "fail",
      tone: "warn",
      errText,
      hint: tau
        ? "可再次点击「物化 L1/L2」"
        : "可再次点击「物化 L1/L2」重试",
    });
  }

  function applyReadyGate() {
    if (!tau || running) return;
    const st = isReadyFn() || {};
    const ok = !!st.ok;
    btn.disabled = !ok;
    const warn = String(st.reason || "").trim();
    btn.title = ok
      ? warn ||
        "物化 L1/L2：训练网格 tc_HHMM + 按钟 Cube（依赖本地 5m）。不训模型、不出 ŷ。"
      : warn || "观察池不足，无法物化 τ";
  }

  async function waitJob(jobId, { pollStartedAt } = {}) {
    const started = pollStartedAt || Date.now();
    // τ 网格 18 钟 L1+L2 常 >45min；对齐槽 stale 5400s，τ 再放宽
    const timeoutMs = (tau ? 150 : 90) * 60 * 1000;
    let sawOwn = false;
    while (Date.now() - started < timeoutMs) {
      let data;
      try {
        data = await fetchData(apiFetch, "/api/jobs/materialize-l1l2?progress=1");
      } catch (_) {
        paintRunning(
          { message: "等待 Job 心跳…", pct: 0 },
          { pollStartedAt: started }
        );
        await sleep(800);
        continue;
      }
      const job = unwrapJobSnap(data) || {};
      if (jobId && job.id && job.id !== jobId) {
        if (sawOwn) {
          return {
            ok: false,
            job,
            error: "物化任务已被新任务替换",
          };
        }
        paintRunning(
          { message: "等待本任务接管…", pct: 0 },
          { pollStartedAt: started }
        );
        await sleep(600);
        continue;
      }
      if (jobId && job.id === jobId) sawOwn = true;
      lastPolledJob = job;
      const status = String(job.status || "").toLowerCase();
      if (status === "done" || status === "success" || status === "finished") {
        return { ok: true, job };
      }
      // 仅认 status；勿用 job.error（落盘残留会误杀 running）
      if (status === "failed" || status === "error") {
        return { ok: false, job, error: job.error || "物化失败" };
      }
      if (status === "idle" && sawOwn) {
        return { ok: false, job, error: "物化任务已结束但未返回结果" };
      }

      const view = macroJobView(job) || job;
      const line = view.message || `${label}进行中…`;
      const { chip: chipText } = phaseFromMessage(line);
      setProStatusChip(chip, "busy", chipText);
      setBusy(true);
      paintRunning(job, { pollStartedAt: started });
      const pct = Number(view.pct);
      const { heartbeat } = jobTimings(job, started);
      const headBits = [line];
      if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
      const frac = jobFracText(job);
      if (frac) headBits.push(frac);
      headBits.push(fmtDuration((Date.now() - started) / 1000));
      if (heartbeat != null) headBits.push(`心跳 ${fmtDuration(heartbeat)}`);
      msgEl.textContent = headBits.join(" · ");
      await sleep(500);
    }
    // 前端等满时后台可能仍在跑：勿误报「失败」，提示刷新接管
    try {
      const data = await fetchData(apiFetch, "/api/jobs/materialize-l1l2?progress=1");
      const job = unwrapJobSnap(data) || {};
      const st = String(job.status || "").toLowerCase();
      if (
        jobId &&
        job.id === jobId &&
        (st === "running" || st === "done" || st === "success" || st === "finished")
      ) {
        if (st === "done" || st === "success" || st === "finished") {
          return { ok: true, job };
        }
        lastPolledJob = job;
        return {
          ok: false,
          job,
          stillRunning: true,
          error: "前端等待已满，任务仍在后台运行；请刷新页面接管进度（勿重复开跑）",
        };
      }
    } catch (_) {
      /* ignore */
    }
    return { ok: false, error: "物化超时" };
  }

  async function waitPeerMaterialize(peerJob, { pollStartedAt } = {}) {
    const peerId = peerJob && peerJob.id;
    const peerKind = peerJob && peerJob.kind;
    const peerLabel = kindLabelZh(peerKind);
    const started = pollStartedAt || Date.now();
    const timeoutMs = (tau ? 150 : 90) * 60 * 1000;
    while (Date.now() - started < timeoutMs) {
      let data;
      try {
        data = await fetchData(apiFetch, "/api/jobs/materialize-l1l2?progress=1");
      } catch (_) {
        paintRunning(
          {
            message: `等待${peerLabel}结束…`,
            pct: 0,
            peer_kind: peerKind,
          },
          { pollStartedAt: started, waitPeer: true }
        );
        await sleep(800);
        continue;
      }
      const job = unwrapJobSnap(data) || {};
      const st = String(job.status || "").toLowerCase();
      if (!peerId || job.id !== peerId || st !== "running") {
        return { ok: true, job };
      }
      // 状态条只写「等待对侧」；对侧内部进度（DayCube 等）放 hint，避免分钟区看起来像在跑日线物化
      const line = `等待${peerLabel}结束…`;
      const peerMsg = String(job.message || "").trim();
      setProStatusChip(chip, "busy", "WAIT");
      setBusy(true);
      paintRunning(
        {
          message: line,
          pct: 0,
          current: 0,
          total: 0,
          id: job.id,
          updated_at: job.updated_at,
          started_at: job.started_at,
          peer_kind: peerKind,
        },
        { pollStartedAt: started, waitPeer: true }
      );
      if (progressEl) {
        const hintEl = progressEl.querySelector(".quant-bars-progress-hint");
        if (hintEl && peerMsg) {
          hintEl.textContent = `对侧：${peerMsg}`;
        }
      }
      const headBits = [line];
      if (peerMsg) headBits.push(peerMsg);
      headBits.push(fmtDuration((Date.now() - started) / 1000));
      msgEl.textContent = headBits.join(" · ");
      await sleep(800);
    }
    return { ok: false, error: `等待${peerLabel}超时` };
  }

  async function postMaterializeOnce(body) {
    try {
      return await fetchData(apiFetch, "/api/quant/materialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      const data = (e && e.data) || {};
      return {
        success: false,
        ok: false,
        conflict: !!(data && data.conflict),
        error: (data && data.error) || (e && e.message) || "启动物化失败",
        job: data && data.job,
      };
    }
  }

  async function postAndWait({ clocks, doL1, doL1TauGrid, doL2 }) {
    // 唯一 lookback：日线 L1+open L2（与日 K 仓同常量）；分钟钟短窗由后端内部常量。
    const body = {
      families: ["oo", "tc", "co"],
      clocks: Array.isArray(clocks) && clocks.length ? clocks : ["open"],
      do_l1: !!doL1,
      do_l1_tau_grid: !!doL1TauGrid,
      do_l2: !!doL2,
      lookback: BARS_DAILY_LOOKBACK,
      sync: false,
    };
    let res = await postMaterializeOnce(body);
    // 日线 / τ 共用一槽：对侧进行中则等待结束后自动再开一次
    if (isMatKindConflict(res) && res.job) {
      const waited = await waitPeerMaterialize(res.job, {
        pollStartedAt: Date.now(),
      });
      if (!waited.ok) return waited;
      res = await postMaterializeOnce(body);
    }
    if (!res || res.success === false) {
      return {
        ok: false,
        error: (res && res.error) || "启动物化失败",
        job: res?.job,
      };
    }
    const started = Date.now();
    if (res.reused && res.job) {
      paintRunning(
        {
          message: res.job.message || "复用进行中的物化 Job…",
          pct: res.job.pct,
          current: res.job.current,
          total: res.job.total,
          id: res.job.id,
          updated_at: res.job.updated_at,
          started_at: res.job.started_at,
        },
        { pollStartedAt: started }
      );
    }
    const done = await waitJob(res.job?.id, { pollStartedAt: started });
    if (!done.ok) return done;
    const result = (done.job && done.job.result) || {};
    if (
      result &&
      typeof result.do_l1_tau_grid === "boolean" &&
      !!result.do_l1_tau_grid !== !!body.do_l1_tau_grid
    ) {
      return {
        ok: false,
        error: "物化结果与请求模式不一致（可能复用了其它物化 Job）",
        job: done.job,
      };
    }
    return { ...done, clocks: body.clocks, do_l1_tau_grid: body.do_l1_tau_grid };
  }

  function applyDoneUi(done) {
    if (!done.ok) {
      const errText = String(done.error || "物化失败");
      const j = done.job || lastPolledJob;
      // 前端等满但槽位仍 running：黄条提示接管，勿标 FAIL
      if (done.stillRunning) {
        setProStatusChip(chip, "busy", "WAIT");
        msgEl.textContent = errText;
        paintRunning(j || { message: errText }, { pollStartedAt: Date.now() });
        return;
      }
      setProStatusChip(chip, "error", "FAIL");
      const head = ["物化失败"];
      const frac = jobFracText(j);
      if (frac) head.push(frac);
      head.push(errText);
      msgEl.textContent = head.join(" · ");
      paintFail(j, errText);
      return;
    }
    const result = (done.job && done.job.result) || {};
    const bits = resultHeadBits(result, { tau });
    const updated = Number(done.job?.updated_at);
    if (updated > 0) bits.push(fmtAgeSec(Date.now() / 1000 - updated));
    setProStatusChip(chip, "ok", "DONE");
    msgEl.textContent = bits.join(" · ");
    paintDone(done.job, result);
  }

  function beginRunUi(seedMsg) {
    if (onStart) onStart();
    running = true;
    setActionDisabled(true);
    setBusy(true);
    setProStatusChip(chip, "busy", "QUEUE");
    msgEl.textContent = seedMsg;
    paintRunning({ message: seedMsg, pct: 0 }, { pollStartedAt: Date.now() });
  }

  function endRunUi() {
    running = false;
    setBusy(false);
    for (const b of siblingBtns) b.disabled = false;
    if (tau) applyReadyGate();
    else btn.disabled = false;
    if (onSettled) onSettled();
  }

  async function run() {
    if (tau) applyReadyGate();
    if (btn.disabled || running) return;
    lastPolledJob = null;
    beginRunUi(`提交${label}…`);
    try {
      let clocks = ["open"];
      let doL1 = true;
      let doL1TauGrid = false;
      let doL2 = true;
      if (tau) {
        clocks = await resolveSuggestedTauClocks(apiFetch);
        if (!clocks.length) {
          throw new Error("catalog 无 suggested_tau_clocks");
        }
        doL1 = false;
        doL1TauGrid = true;
        msgEl.textContent = `物化进行中（τ 网格 L1 + ${clocks.length}钟 L2）…`;
      } else {
        msgEl.textContent = "物化进行中（open）…";
      }
      const done = await postAndWait({ clocks, doL1, doL1TauGrid, doL2 });
      applyDoneUi(done);
    } catch (e) {
      const errText = String((e && e.message) || e || "物化异常");
      setProStatusChip(chip, "error", "FAIL");
      msgEl.textContent = ["物化失败", errText].join(" · ");
      paintFail(lastPolledJob, errText);
    } finally {
      endRunUi();
    }
  }

  /** 刷新后接管 in-flight；近期失败也画出来（勿被日 K/5m done 盖掉）。 */
  async function resumeIfRunning() {
    if (running || typeof apiFetch !== "function") return false;
    let data;
    try {
      data = await fetchData(apiFetch, "/api/jobs/materialize-l1l2?progress=1");
    } catch (_) {
      return false;
    }
    const job = unwrapJobSnap(data) || {};
    const st = String(job.status || "").toLowerCase();
    if (!kindMatchesRunner(job.kind, tau)) return false;

    if (st === "failed" || st === "error") {
      const updated = Number(job.updated_at || 0);
      const ageSec = updated > 0 ? Date.now() / 1000 - updated : 1e9;
      // 超过 6h 的旧失败不抢状态条
      if (ageSec > 6 * 3600) return false;
      lastPolledJob = job;
      if (onStart) onStart();
      const errText = String(job.error || job.message || "物化失败");
      setProStatusChip(chip, "error", "FAIL");
      const head = ["物化失败"];
      const frac = jobFracText(job);
      if (frac) head.push(frac);
      head.push(errText);
      msgEl.textContent = head.join(" · ");
      paintFail(job, errText);
      if (onSettled) onSettled();
      return true;
    }

    if (st !== "running") return false;
    lastPolledJob = job;
    beginRunUi(job.message || `接管${label}…`);
    const { chip: chipText } = phaseFromMessage(job.message);
    setProStatusChip(chip, "busy", chipText);
    paintRunning(job, { pollStartedAt: Date.now() });
    // 勿阻塞页加载；后台挂 waitJob
    void (async () => {
      try {
        const done = await waitJob(job.id, { pollStartedAt: Date.now() });
        applyDoneUi(done);
      } catch (e) {
        const errText = String((e && e.message) || e || "物化异常");
        setProStatusChip(chip, "error", "FAIL");
        msgEl.textContent = ["物化失败", errText].join(" · ");
        paintFail(lastPolledJob, errText);
      } finally {
        endRunUi();
      }
    })();
    return true;
  }

  btn.addEventListener("click", () => {
    void run();
  });
  if (tau) applyReadyGate();
  // resumeIfRunning 由 bars/minute 在 bootstrap 里先调（未接管再 syncJobSlot）

  const gate = () => {
    if (tau) applyReadyGate();
  };
  return { refresh: gate, setReady: gate, run, resumeIfRunning };
}

/** 日线区：oo/tc/co + open L2（独立板块）。 */
export function installMaterializeUi(q, opts = {}) {
  return installMaterializeRunner(q, {
    btnId: "quant-materialize-run",
    chipId: "quant-materialize-chip",
    msgId: "quant-materialize-msg",
    progressId: "quant-materialize-progress",
    stripId: "quant-materialize-strip",
    cardSel: ".quant-bars-mat-card",
    siblingBtnIds: ["quant-bars-topup", "quant-bars-refresh"],
    tau: false,
    onStart: opts.onStart,
    onBusy: opts.onBusy,
    onSettled: opts.onSettled,
  });
}

/**
 * 分钟线区：τ 网格 L1 + 按钟 L2（独立板块）。
 * ``opts.isReady()`` → { ok, reason? }；仅观察池不足时禁用（不再卡 Ready%）。
 */
export function installMinuteMaterializeUi(q, opts = {}) {
  return installMaterializeRunner(q, {
    btnId: "quant-minute-materialize-run",
    chipId: "quant-minute-materialize-chip",
    msgId: "quant-minute-materialize-msg",
    progressId: "quant-minute-materialize-progress",
    stripId: "quant-minute-materialize-strip",
    cardSel: ".quant-minute-mat-card",
    siblingBtnIds: ["quant-minute-topup", "quant-minute-refresh"],
    tau: true,
    isReady: opts.isReady,
    onStart: opts.onStart,
    onBusy: opts.onBusy,
    onSettled: opts.onSettled,
  });
}
