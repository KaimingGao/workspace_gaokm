/** 盘中补齐会话日官方 open L2（POST /api/quant/materialize · do_session_open_l2）。 */

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * @param {object} data API 错误体
 * @returns {boolean}
 */
export function isSessionOpenMaterializeRequired(data) {
  if (!data || typeof data !== "object") return false;
  const code = String(data.error_code || "").trim();
  const req = data.request || {};
  if (req && req.do_session_open_l2) return true;
  if (code !== "materialize_required") return false;
  const hint = String(data.hint || "");
  const err = String(data.error || data.detail || data.score_error || "");
  return (
    hint.includes("开盘 L2") ||
    err.includes("开盘 L2") ||
    err.includes("open_session_unavailable")
  );
}

/**
 * @param {object} data
 * @returns {object}
 */
export function sessionOpenMaterializeBody(data) {
  const req = (data && data.request) || {};
  const day = String(
    req.cycle_day || (data && data.cycle_day) || ""
  ).slice(0, 10);
  return {
    do_session_open_l2: true,
    do_l1: false,
    do_l1_tau_grid: false,
    do_l2: true,
    clocks: ["open"],
    families: ["oo", "tc"],
    cycle_day: day.length >= 10 ? day : undefined,
    sync: false,
  };
}

/**
 * @param {object} opts
 * @param {(pct:number, msg:string)=>void} [opts.onProgress]
 * @param {object} [opts.fromError] MaterializeRequired 响应
 * @returns {Promise<{ok:boolean, error?:string, result?:object}>}
 */
export async function ensureSessionOpenL2(opts = {}) {
  const onProgress =
    typeof opts.onProgress === "function" ? opts.onProgress : () => {};
  const body = sessionOpenMaterializeBody(opts.fromError || {});
  onProgress(2, "补齐当日开盘 L2…");
  let start;
  try {
    const res = await fetch("/api/quant/materialize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    start = await res.json().catch(() => ({}));
    if (!res.ok && !(start && start.job)) {
      return {
        ok: false,
        error: String(
          (start && (start.error || start.detail)) || res.statusText || "启动物化失败"
        ),
      };
    }
  } catch (e) {
    return {
      ok: false,
      error: String((e && e.message) || e || "启动物化失败"),
    };
  }
  if (start && start.sync) {
    const ok = !!(start.ok || start.success);
    return {
      ok,
      error: ok ? undefined : String(start.error || "补齐开盘 L2 失败"),
      result: start,
    };
  }
  const jobId = start && start.job && start.job.id;
  const t0 = Date.now();
  while (Date.now() - t0 < 10 * 60 * 1000) {
    await sleep(1200);
    let prog;
    try {
      const res = await fetch("/api/jobs/materialize-l1l2?progress=1");
      prog = await res.json().catch(() => ({}));
    } catch (e) {
      return {
        ok: false,
        error: String((e && e.message) || e || "轮询物化失败"),
      };
    }
    const job = (prog && prog.job) || prog || {};
    if (jobId && job.id && String(job.id) !== String(jobId) && job.status === "running") {
      // 槽被其它任务占用时继续等
    }
    const pct = Number(job.pct);
    const msg = String(job.message || "补齐开盘 L2…");
    onProgress(
      Number.isFinite(pct) ? Math.max(2, Math.min(99, pct)) : 5,
      msg
    );
    const st = String(job.status || "").toLowerCase();
    if (st === "done" || st === "success" || st === "completed") {
      const result = job.result || {};
      const ok = !!(result.ok || result.success || job.ok);
      return {
        ok,
        error: ok
          ? undefined
          : String(result.error || job.error || "补齐开盘 L2 失败"),
        result,
      };
    }
    if (st === "error" || st === "failed" || st === "cancelled") {
      return {
        ok: false,
        error: String(job.error || (job.result && job.result.error) || "补齐开盘 L2 失败"),
        result: job.result,
      };
    }
  }
  return { ok: false, error: "补齐开盘 L2 超时" };
}
