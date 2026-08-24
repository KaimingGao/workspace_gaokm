import { setProStatusChip } from "./factor_corr_ui.js";

/** 研究枢纽 · 观察池日 K 覆盖状态 + 可视化 + 「更新日线」 */
export function installClusterBarsUi(q) {
  const { on, apiFetch, readWatchingLimit, escapeHtml } = q;
  const chip = document.getElementById("quant-cluster-bars-chip");
  const msgEl = document.getElementById("quant-cluster-bars-msg");
  const btn = document.getElementById("quant-cluster-bars-refresh");
  const kpiAlign = document.getElementById("quant-bars-kpi-align");
  const kpiAlignSub = document.getElementById("quant-bars-kpi-align-sub");
  const kpiExpected = document.getElementById("quant-bars-kpi-expected");
  const kpiStale = document.getElementById("quant-bars-kpi-stale");
  const kpiMissing = document.getElementById("quant-bars-kpi-missing");
  const covPct = document.getElementById("quant-bars-cov-pct");
  const covBody = document.getElementById("quant-bars-cov-body");
  const distHost = document.getElementById("quant-bars-dist");
  const metaEl = document.getElementById("quant-bars-meta");
  const kpiHost = document.getElementById("quant-bars-kpis");
  const barsCard = document.querySelector(".quant-bars-card");
  const barsBody = document.querySelector(".quant-bars-body");
  const barsStrip = document.getElementById("quant-cluster-bars-strip");

  if (!chip || !msgEl || !btn) {
    return { refreshStatus: async () => {}, startRefresh: async () => {} };
  }

  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s ?? "");

  let inflight = null;

  function setBarsBusy(busy) {
    const on = !!busy;
    barsCard?.classList.toggle("is-busy", on);
    barsStrip?.classList.toggle("is-busy", on);
    barsBody?.classList.toggle("is-busy", on);
    btn?.classList.toggle("is-busy", on);
  }

  function setKpiCard(key, state) {
    if (!kpiHost) return;
    const card = kpiHost.querySelector(`[data-bars-kpi="${key}"]`);
    if (!card) return;
    card.classList.remove("is-good", "is-bad", "is-mid", "is-empty");
    if (state) card.classList.add(state);
  }

  function formatStatus(data) {
    if (!data || !data.success) {
      return { state: "error", chip: "异常", msg: "无法读取日 K 状态" };
    }
    const total = data.universe_count ?? 0;
    const at = data.at_expected ?? 0;
    const stale = data.stale ?? 0;
    const missing = data.missing ?? 0;
    const expected = data.expected_latest_bar || "—";
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;
    const parts = [`${at}/${total} 对齐 · ${pct}%`, `目标 ${expected}`];
    if (stale > 0) parts.push(`滞后 ${stale}`);
    if (missing > 0) parts.push(`缺缓存 ${missing}`);
    const marker = data.forced_marker || {};
    if (marker.session_date && marker.saved_at) {
      const when = String(marker.saved_at).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
      parts.push(`本会话已强更 ${when} UTC`);
    } else if (data.needs_force_latest_bars) {
      parts.push("本会话尚未强更");
    }
    let state = "ok";
    let chipText = "已对齐";
    if (total <= 0) {
      state = "warn";
      chipText = "空池";
    } else if (!data.coverage_ok || data.needs_force_latest_bars) {
      state = "warn";
      chipText = stale || missing ? "待补齐" : "待更新";
    }
    return { state, chip: chipText, msg: parts.join(" · "), pct, total, at, stale, missing, expected };
  }

  function renderBarRows(host, rows, { emptyText = "—" } = {}) {
    if (!host) return;
    if (!rows.length) {
      host.innerHTML = `<p class="quant-bars-viz-empty">${esc(emptyText)}</p>`;
      return;
    }
    const max = Math.max(...rows.map((r) => Number(r.count) || 0), 1);
    host.innerHTML = rows
      .map((row) => {
        const n = Number(row.count) || 0;
        const w = Math.max(4, Math.round((100 * n) / max));
        const label = String(row.label || "—");
        const state = row.state ? ` ${row.state}` : "";
        return `<div class="quant-bars-bar-row${state}" title="${esc(row.title || label)}">
          <span class="quant-bars-bar-label">${esc(label)}</span>
          <span class="quant-bars-bar-wrap" aria-hidden="true">
            <span class="quant-bars-bar-fill" style="width:${w}%"></span>
          </span>
          <span class="quant-bars-bar-n">${n}</span>
        </div>`;
      })
      .join("");
  }

  function renderCoverageBar(data) {
    const total = Math.max(0, Number(data.universe_count) || 0);
    const at = Number(data.at_expected) || 0;
    const stale = Number(data.stale) || 0;
    const missing = Number(data.missing) || 0;
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;
    if (covPct) covPct.textContent = total > 0 ? `${pct}% 对齐` : "—";

    if (total <= 0) {
      renderBarRows(covBody, [], { emptyText: "观察池为空" });
      return;
    }

    const rows = [
      { label: "已对齐", count: at, state: "is-ok", title: `已对齐 ${at}` },
      { label: "滞后", count: stale, state: "is-warn", title: `滞后 ${stale}` },
      { label: "缺缓存", count: missing, state: "is-bad", title: `缺缓存 ${missing}` },
    ].filter((row) => row.count > 0);
    renderBarRows(covBody, rows, { emptyText: "暂无覆盖数据" });
  }

  function renderDistribution(data) {
    if (!distHost) return;
    const expected = data.expected_latest_bar || "";
    const rows = [...(data.date_distribution || [])].map((row) => {
      const missing = !!row.missing;
      const date = missing ? "缺缓存" : String(row.date || "—");
      let state = "";
      if (missing) state = "is-bad";
      else if (expected && date === expected) state = "is-ok";
      else if (expected && date < expected) state = "is-warn";
      return {
        label: date,
        count: Number(row.count) || 0,
        state,
        title: missing ? "缺缓存" : date,
      };
    });
    if (Number(data.missing) > 0 && !rows.some((r) => r.label === "缺缓存")) {
      rows.push({
        label: "缺缓存",
        count: Number(data.missing) || 0,
        state: "is-bad",
        title: "缺缓存",
      });
    }
    renderBarRows(distHost, rows, {
      emptyText: "暂无分布（观察池为空或未拉取）",
    });
  }

  function renderMeta(data) {
    if (!metaEl) return;
    const bits = [];
    bits.push(`Limit ${data.watching_limit ?? "—"}`);
    bits.push(`会话 ${data.session_date || "—"}`);
    if (data.last_bar_min && data.last_bar_max) {
      bits.push(`末 bar 区间 ${data.last_bar_min} ~ ${data.last_bar_max}`);
    }
    const marker = data.forced_marker || {};
    if (marker.remote_count != null && marker.total != null) {
      bits.push(`末次强更拉网 ${marker.remote_count}/${marker.total}`);
    }
    bits.push("字段 open/high/low/close/volume · 本地 JSON 缓存");
    metaEl.textContent = bits.join(" · ");
  }

  function renderKpis(data) {
    const total = Number(data.universe_count) || 0;
    const at = Number(data.at_expected) || 0;
    const stale = Number(data.stale) || 0;
    const missing = Number(data.missing) || 0;
    const pct = total > 0 ? Math.round((1000 * at) / total) / 10 : 0;

    if (kpiAlign) kpiAlign.textContent = total > 0 ? `${pct}%` : "—";
    if (kpiAlignSub) kpiAlignSub.textContent = total > 0 ? `${at} / ${total} 只` : "— / — 只";
    if (kpiExpected) kpiExpected.textContent = data.expected_latest_bar || "—";
    if (kpiStale) kpiStale.textContent = String(stale);
    if (kpiMissing) kpiMissing.textContent = String(missing);

    setKpiCard("align", total <= 0 ? "is-empty" : data.coverage_ok ? "is-good" : "is-mid");
    setKpiCard("expected", data.expected_latest_bar ? "is-mid" : "is-empty");
    setKpiCard("stale", stale > 0 ? "is-bad" : total > 0 ? "is-good" : "is-empty");
    setKpiCard("missing", missing > 0 ? "is-bad" : total > 0 ? "is-good" : "is-empty");
  }

  function paint(data) {
    const { state, chip: chipText, msg } = formatStatus(data);
    setProStatusChip(chip, state, chipText);
    msgEl.textContent = msg;
    if (!data || !data.success) {
      renderCoverageBar({ universe_count: 0, at_expected: 0, stale: 0, missing: 0 });
      renderDistribution({ date_distribution: [], missing: 0 });
      if (metaEl) metaEl.textContent = "—";
      return;
    }
    renderKpis(data);
    renderCoverageBar(data);
    renderDistribution(data);
    renderMeta(data);
  }

  async function refreshStatus() {
    const limit = typeof readWatchingLimit === "function" ? readWatchingLimit() : 100;
    const { ok, data, error } = await apiFetch(
      `/api/quant/cluster-bars/status?watching_limit=${encodeURIComponent(limit)}`
    );
    if (!ok) {
      paint(null);
      msgEl.textContent = error || "读取日 K 状态失败";
      setProStatusChip(chip, "error", "异常");
      return null;
    }
    paint(data);
    return data;
  }

  async function waitClusterBarsJob(jobId) {
    const started = Date.now();
    const softCapMs = 12 * 60 * 1000;
    const absoluteCapMs = 45 * 60 * 1000;
    const heartbeatFreshSec = 90;
    let sawOwnJob = false;
    while (Date.now() - started < absoluteCapMs) {
      const { ok, data: job } = await apiFetch(
        "/api/jobs/cluster-bars-refresh?progress=1"
      );
      if (!ok || !job) {
        await new Promise((r) => setTimeout(r, 800));
        continue;
      }
      if (jobId && job.id && job.id !== jobId) {
        if (sawOwnJob) break;
        await new Promise((r) => setTimeout(r, 600));
        continue;
      }
      if (jobId && job.id === jobId) sawOwnJob = true;
      const st = job.status;
      if (st === "done") return job.result || { success: true };
      if (st === "failed") {
        throw new Error(job.error || job.message || "日线更新失败");
      }
      if (st === "idle" && sawOwnJob) {
        throw new Error("日线任务已结束但未返回结果");
      }
      const pct = Number(job.pct);
      const line = job.message || "更新日线…";
      setProStatusChip(chip, "busy", "更新中");
      setBarsBusy(true);
      msgEl.textContent = `${line}${
        Number.isFinite(pct) && pct > 0 ? ` · ${Math.round(pct)}%` : ""
      }`;
      const updatedAt = Number(job.updated_at || 0);
      const ageSec = updatedAt ? Date.now() / 1000 - updatedAt : 999;
      if (Date.now() - started > softCapMs && ageSec > heartbeatFreshSec) {
        throw new Error("日线更新超时（长时间无进度）");
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("日线更新超时");
  }

  async function startRefresh() {
    if (inflight) return inflight;
    inflight = (async () => {
      btn.disabled = true;
      setBarsBusy(true);
      setProStatusChip(chip, "busy", "排队");
      msgEl.textContent = "更新日线…";
      try {
        const limit = typeof readWatchingLimit === "function" ? readWatchingLimit() : 100;
        const { ok, data, error } = await apiFetch("/api/quant/cluster-bars/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lookback: 80, watching_limit: limit }),
        });
        if (!ok) throw new Error(error || "提交失败");
        let result = data;
        if (data && data.background && data.job) {
          result = await waitClusterBarsJob(data.job.id);
        }
        if (result && result.status) {
          paint(result.status);
        } else {
          await refreshStatus();
        }
        if (result && result.success === false) {
          throw new Error(result.error || "日线更新失败");
        }
        setProStatusChip(chip, "ok", "完成");
      } catch (err) {
        setProStatusChip(chip, "error", "失败");
        msgEl.textContent = String((err && err.message) || err || "日线更新失败");
        throw err;
      } finally {
        setBarsBusy(false);
        btn.disabled = false;
        inflight = null;
        try {
          await refreshStatus();
        } catch (_) {
          /* ignore */
        }
      }
    })();
    return inflight;
  }

  on("quant-cluster-bars-refresh", "click", (e) => {
    e.preventDefault();
    startRefresh().catch(() => {});
  });

  refreshStatus().catch(() => {});

  return { refreshStatus, startRefresh };
}
