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
  if (/τ\s*网格|L1\s*τ|τL1/i.test(s)) return { chip: "τL1", phase: "物化 τL1" };
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

function resultHeadBits(result, { tau = false } = {}) {
  const bits = [tau ? "物化完成（τ）" : "物化完成"];
  if (result?.elapsed_s != null) bits.push(`${result.elapsed_s}s`);
  const l1 = result?.l1 || {};
  if (l1.elapsed_s != null) bits.push(`L1 ${l1.elapsed_s}s`);
  const l1tau = result?.l1_tau_grid || {};
  if (l1tau.elapsed_s != null) {
    bits.push(`τL1 ${l1tau.clocks_ok || 0}/${(l1tau.clocks || []).length}`);
  }
  const l2bit = summarizeL2((result?.l2 || {}).by_clock);
  if (l2bit) bits.push(l2bit);
  return bits;
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

  function paintRunning(job, { pollStartedAt } = {}) {
    const msg = job?.message || `${label}进行中…`;
    const { phase } = phaseFromMessage(msg);
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase,
      job,
      pollStartedAt,
      esc,
      mode: "running",
      tone: "warn",
      hint: tau
        ? "训练网格 tc_HHMM L1 + 按钟 L2 · 须 5m Ready"
        : "open L1（oo/tc/co）+ open L2 DayCube",
    });
  }

  function paintDone(job, result) {
    const bits = resultHeadBits(result, { tau });
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase: tau ? "物化完成（τ）" : "物化完成",
      job,
      esc,
      mode: "done",
      tone: "warn",
      hint: bits.slice(1).join(" · ") || (tau ? "τ 网格 L1 + 按钟 Cube" : "open L1 + open Cube"),
    });
  }

  function paintFail(job, errText) {
    setDeskStatusTone([strip, card], "warn");
    paintClusterJobPanel(progressEl, {
      phase: "物化失败",
      job,
      esc,
      mode: "fail",
      tone: "warn",
      errText,
      hint: tau
        ? "可再次点击「物化 L1/L2」（须分钟 Ready）"
        : "可再次点击「物化 L1/L2」重试",
    });
  }

  function applyReadyGate() {
    if (!tau || running) return;
    const st = isReadyFn() || {};
    const ok = !!st.ok;
    btn.disabled = !ok;
    btn.title = ok
      ? "物化 L1/L2：训练网格 tc_HHMM + 按钟 Cube（须 5m）。不训模型、不出 ŷ。"
      : String(st.reason || "须先分钟 Ready（增量补齐 / 强更 5m）");
  }

  async function waitJob(jobId, { pollStartedAt } = {}) {
    const started = pollStartedAt || Date.now();
    const timeoutMs = 45 * 60 * 1000;
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
        if (sawOwn) break;
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
      if (status === "failed" || status === "error" || job.error) {
        return { ok: false, job, error: job.error || "物化失败" };
      }
      if (status === "idle" && sawOwn) {
        return { ok: false, job, error: "物化任务已结束但未返回结果" };
      }

      const line = job.message || `${label}进行中…`;
      const { chip: chipText } = phaseFromMessage(line);
      setProStatusChip(chip, "busy", chipText);
      setBusy(true);
      paintRunning(job, { pollStartedAt: started });
      const pct = Number(job.pct);
      const { heartbeat } = jobTimings(job, started);
      const headBits = [line];
      if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
      if (job.current > 0 && job.total > 0) {
        headBits.push(`${job.current}/${job.total}`);
      }
      headBits.push(fmtDuration((Date.now() - started) / 1000));
      if (heartbeat != null) headBits.push(`心跳 ${fmtDuration(heartbeat)}`);
      msgEl.textContent = headBits.join(" · ");
      await sleep(500);
    }
    return { ok: false, error: "物化超时" };
  }

  async function postAndWait({ clocks, doL1, doL1TauGrid, doL2 }) {
    const body = {
      families: ["oo", "tc", "co"],
      clocks: Array.isArray(clocks) && clocks.length ? clocks : ["open"],
      do_l1: !!doL1,
      do_l1_tau_grid: !!doL1TauGrid,
      do_l2: !!doL2,
      sync: false,
    };
    let res;
    try {
      res = await fetchData(apiFetch, "/api/quant/materialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      const data = (e && e.data) || {};
      return {
        ok: false,
        error: (data && data.error) || (e && e.message) || "启动物化失败",
      };
    }
    if (!res || res.success === false) {
      return { ok: false, error: (res && res.error) || "启动物化失败", job: res?.job };
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

  async function run() {
    if (tau) applyReadyGate();
    if (btn.disabled) return;
    lastPolledJob = null;
    if (onStart) onStart();
    running = true;
    setActionDisabled(true);
    setBusy(true);
    setProStatusChip(chip, "busy", "QUEUE");
    msgEl.textContent = `提交${label}…`;
    paintRunning(
      { message: `提交${label}…`, pct: 0 },
      { pollStartedAt: Date.now() }
    );
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
      if (!done.ok) {
        const errText = String(done.error || "物化失败");
        setProStatusChip(chip, "error", "FAIL");
        const head = ["物化失败"];
        const j = done.job || lastPolledJob;
        if (j && j.current > 0 && j.total > 0) head.push(`${j.current}/${j.total}`);
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
    } catch (e) {
      const errText = String((e && e.message) || e || "物化异常");
      setProStatusChip(chip, "error", "FAIL");
      msgEl.textContent = ["物化失败", errText].join(" · ");
      paintFail(lastPolledJob, errText);
    } finally {
      running = false;
      setBusy(false);
      for (const b of siblingBtns) b.disabled = false;
      if (tau) applyReadyGate();
      else btn.disabled = false;
      if (onSettled) onSettled();
      // 失败/完成保留进度面板（对齐日 K / 5m）
    }
  }

  btn.addEventListener("click", () => {
    void run();
  });
  if (tau) applyReadyGate();

  const gate = () => {
    if (tau) applyReadyGate();
  };
  return { refresh: gate, setReady: gate, run };
}

/** 日线区：oo/tc/co + open L2。 */
export function installMaterializeUi(q) {
  return installMaterializeRunner(q, {
    btnId: "quant-materialize-run",
    chipId: "quant-bars-chip",
    msgId: "quant-bars-msg",
    progressId: "quant-bars-progress",
    stripId: "quant-bars-strip",
    cardSel: ".quant-bars-card:not(.quant-minute-card)",
    siblingBtnIds: ["quant-bars-topup", "quant-bars-refresh"],
    tau: false,
  });
}

/**
 * 分钟线区：τ 网格 L1 + 按钟 L2。
 * ``opts.isReady()`` → { ok, reason? }；Ready 不足时禁用按钮。
 */
export function installMinuteMaterializeUi(q, opts = {}) {
  return installMaterializeRunner(q, {
    btnId: "quant-minute-materialize-run",
    chipId: "quant-minute-chip",
    msgId: "quant-minute-msg",
    progressId: "quant-minute-progress",
    stripId: "quant-minute-strip",
    cardSel: ".quant-minute-card",
    siblingBtnIds: ["quant-minute-topup", "quant-minute-refresh"],
    tau: true,
    isReady: opts.isReady,
    onStart: opts.onStart,
    onBusy: opts.onBusy,
    onSettled: opts.onSettled,
  });
}
