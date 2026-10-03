/**
 * 本地仓显式写入口：日 K + 5m「增量补齐」（与研究枢纽同源 API）。
 *
 * 「可拉远端」= 允许触发本模块；ŷ 现算始终只读本地仓（见 data_offline.offlineOnlyQuery）。
 */

import { apiFetch } from "./api_client.js";
import { getDataOfflineOnly } from "./data_offline.js";
import { unwrapJobSnap } from "./quant/cluster_job_ui.js";
import { BARS_WATCHING_LIMIT, clampBarsWatchingLimit } from "./quant/params.js";

/** 同会话内默认 30 分钟内不重复整池 topup（force 可强制）。 */
export const WAREHOUSE_TOPUP_TTL_MS = 30 * 60 * 1000;

let _inflight = null;
let _lastOkAt = 0;

function _sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * @param {string} pollPath e.g. /api/jobs/cluster-bars-refresh
 * @param {string|null} jobId
 * @param {{ label?: string, timeoutMs?: number, onStatus?: (msg: string) => void, fetchImpl?: typeof apiFetch }} [opts]
 */
async function waitRefreshJob(pollPath, jobId, opts = {}) {
  const label = opts.label || "增量补齐";
  const timeoutMs = opts.timeoutMs || 20 * 60 * 1000;
  const onStatus = opts.onStatus;
  const fetchImpl = opts.fetchImpl || apiFetch;
  const started = Date.now();
  let sawOwn = false;
  while (Date.now() - started < timeoutMs) {
    const { ok, data } = await fetchImpl(`${pollPath}?progress=1`);
    const job = unwrapJobSnap(data);
    if (!ok || !job) {
      await _sleep(800);
      continue;
    }
    if (jobId && job.id && job.id !== jobId) {
      if (sawOwn) break;
      if (typeof onStatus === "function") onStatus(`${label} · 等待本任务…`);
      await _sleep(600);
      continue;
    }
    if (jobId && job.id === jobId) sawOwn = true;
    const st = job.status;
    if (st === "done") return job.result || { success: true };
    if (st === "failed") {
      throw new Error(job.error || job.message || `${label}失败`);
    }
    if (st === "idle" && sawOwn) {
      throw new Error(`${label}已结束但未返回结果`);
    }
    if (typeof onStatus === "function") {
      const pct = Number(job.pct);
      const bits = [job.message || label];
      if (Number.isFinite(pct) && pct > 0) bits.push(`${Math.round(pct)}%`);
      onStatus(bits.join(" · "));
    }
    await _sleep(500);
  }
  throw new Error(`${label}超时`);
}

/**
 * 提交并等待一次 refresh Job（支持 background / sync）。
 * @param {string} postUrl
 * @param {object} body
 * @param {string} pollPath
 * @param {{ label?: string, onStatus?: (msg: string) => void, fetchImpl?: typeof apiFetch }} [opts]
 */
async function runOneTopup(postUrl, body, pollPath, opts = {}) {
  const label = opts.label || "增量补齐";
  const onStatus = opts.onStatus;
  const fetchImpl = opts.fetchImpl || apiFetch;
  if (typeof onStatus === "function") onStatus(`提交${label}…`);
  const { ok, data, error } = await fetchImpl(postUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!ok) throw new Error(error || `${label}提交失败`);
  if (data && data.background && data.job) {
    const jid = data.job.id || null;
    if (data.reused && typeof onStatus === "function") {
      onStatus(data.job.message || `复用进行中的${label}…`);
    }
    return waitRefreshJob(pollPath, jid, {
      label,
      onStatus,
      fetchImpl,
    });
  }
  return data;
}

/**
 * 「可拉远端」时：观察池日 K + 5m 增量补齐（显式写仓）。
 * 「仅本地仓」时跳过。
 *
 * @param {{
 *   force?: boolean,
 *   watchingLimit?: number,
 *   onStatus?: (msg: string) => void,
 *   fetchImpl?: typeof apiFetch,
 *   ttlMs?: number,
 * }} [opts]
 */
export async function ensureWarehouseTopup(opts = {}) {
  const force = !!opts.force;
  const watchingLimit = clampBarsWatchingLimit(
    opts.watchingLimit,
    BARS_WATCHING_LIMIT
  );
  const onStatus = opts.onStatus;
  const fetchImpl = opts.fetchImpl || apiFetch;
  const ttlMs =
    opts.ttlMs != null ? Number(opts.ttlMs) : WAREHOUSE_TOPUP_TTL_MS;

  if (getDataOfflineOnly()) {
    return { ok: true, skipped: true, reason: "offline_only" };
  }
  if (!force && _inflight) return _inflight;
  if (!force && _lastOkAt && Date.now() - _lastOkAt < ttlMs) {
    return { ok: true, skipped: true, reason: "recent" };
  }

  _inflight = (async () => {
    const barsBody = {
      lookback: 600,
      watching_limit: watchingLimit,
      mode: "topup",
    };
    const minuteBody = {
      lookback_days: 120,
      watching_limit: watchingLimit,
      period: "5",
      min_span_days: 40,
      mode: "topup",
      topup_lookback_days: 5,
    };

    // 日 K / 5m 分 Job，可并行提交并各自轮询
    if (typeof onStatus === "function") onStatus("增量补齐日 K · 5m…");
    const [bars, minute] = await Promise.all([
      runOneTopup(
        "/api/quant/cluster-bars/refresh",
        barsBody,
        "/api/jobs/cluster-bars-refresh",
        { label: "增量补齐日 K", onStatus, fetchImpl }
      ),
      runOneTopup(
        "/api/quant/cluster-minute/refresh",
        minuteBody,
        "/api/jobs/cluster-minute-refresh",
        { label: "增量补齐 5m", onStatus, fetchImpl }
      ),
    ]);

    const barsOk = !(bars && bars.success === false);
    if (!barsOk) {
      throw new Error((bars && bars.error) || "增量补齐日 K 失败");
    }
    if (minute && minute.success === false) {
      throw new Error((minute && minute.error) || "增量补齐 5m 失败");
    }
    _lastOkAt = Date.now();
    return {
      ok: true,
      skipped: false,
      bars,
      minute,
      watching_limit: watchingLimit,
    };
  })().finally(() => {
    _inflight = null;
  });

  return _inflight;
}

/** 测试 / 调试：清 TTL 与 inflight */
export function resetWarehouseTopupState() {
  _inflight = null;
  _lastOkAt = 0;
}
