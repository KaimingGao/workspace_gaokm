/** Paper Job 轮询（A4：从 paper.js 编排下沉）。 */

/**
 * @param {object} opts
 * @param {string} [opts.jobId]
 * @param {(pct: number, msg: string) => void} [opts.showProgress]
 * @param {(text: string) => void} [opts.setMeta]
 * @param {number} [opts.hardCapMs]
 * @param {number} [opts.softCapMs]
 * @param {string} [opts.slotUrl]
 */
export async function waitPaperJob(opts = {}) {
  const {
    jobId,
    showProgress,
    setMeta,
    hardCapMs = 25 * 60 * 1000,
    softCapMs = 15 * 60 * 1000,
    slotUrl = "/api/jobs/paper",
  } = opts;
  const started = Date.now();
  let sawOwnJob = false;
  while (Date.now() - started < hardCapMs) {
    const res = await fetch(`${slotUrl}?progress=1`);
    const data = await res.json();
    const job = (data && data.job) || {};
    const sameJob = !jobId || !job.id || job.id === jobId;
    if (sameJob && job.id) sawOwnJob = true;

    if (job.status === "idle" || !job.id) {
      if (sawOwnJob || Date.now() - started > 2500) {
        throw new Error("纸面任务已中断（可能服务重启），请重试确认调仓");
      }
      await new Promise((r) => setTimeout(r, 400));
      continue;
    }
    if (!sameJob) {
      throw new Error("纸面任务已被其它任务覆盖，请重试");
    }

    const pct = Number(job.pct) || 0;
    const msg =
      job.message ||
      (job.total
        ? `${job.current || 0}/${job.total}`
        : job.status === "running"
          ? "运行中…"
          : "");
    if (typeof showProgress === "function") showProgress(pct, msg);
    if (typeof setMeta === "function" && msg) setMeta(msg);
    if (job.status === "done") {
      const fullRes = await fetch(slotUrl);
      const fullData = await fullRes.json();
      return (fullData && fullData.job) || job;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "纸面任务失败");
    }
    const elapsed = Date.now() - started;
    if (elapsed >= softCapMs) {
      const ua = Number(job.updated_at);
      const fresh = Number.isFinite(ua) && Date.now() / 1000 - ua < 90;
      if (!fresh) throw new Error("纸面任务超时");
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("纸面任务超时");
}
