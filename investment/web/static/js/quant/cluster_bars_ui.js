import { setProStatusChip } from "./factor_corr_ui.js";

/** 研究枢纽 · 观察池日 K 覆盖状态 + 可视化 + 「强更日 K」 */
export function installClusterBarsUi(q) {
  const { on, apiFetch, readWatchingLimit, escapeHtml } = q;
  const chip = document.getElementById("quant-cluster-bars-chip");
  const msgEl = document.getElementById("quant-cluster-bars-msg");
  const btn = document.getElementById("quant-cluster-bars-refresh");
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
    return { refreshStatus: async () => {}, startRefresh: async () => {} };
  }

  const esc = typeof escapeHtml === "function" ? escapeHtml : (s) => String(s ?? "");

  let inflight = null;

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

  function fmtDuration(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}m${String(r).padStart(2, "0")}s`;
  }

  function clearProgressPanel() {
    if (!progressEl) return;
    progressEl.hidden = true;
    progressEl.innerHTML = "";
    progressEl.classList.remove("is-active", "is-warn");
  }

  function paintProgressPanel(job, { pollStartedAt } = {}) {
    if (!progressEl) return;
    const msg = job && (job.message || job.error) ? String(job.message || job.error) : "强更日 K…";
    const parsed = parseBarsProgressMessage(msg);
    const pctJob = Number(job && job.pct);
    const done = parsed.done;
    const total = parsed.total;
    let pct =
      Number.isFinite(pctJob) && pctJob > 0
        ? pctJob
        : done != null && total > 0
          ? Math.round((1000 * done) / total) / 10
          : 0;
    pct = Math.max(0, Math.min(100, pct));

    const wallSec = pollStartedAt ? (Date.now() - pollStartedAt) / 1000 : parsed.elapsedSec;
    const elapsed = parsed.elapsedSec != null ? parsed.elapsedSec : wallSec;
    let etaSec = null;
    if (done > 0 && total > done && elapsed > 2) {
      const rate = done / elapsed;
      etaSec = (total - done) / rate;
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
    if (done != null && total != null) facts.push(`进度 <strong>${done}/${total}</strong>`);
    if (remote != null) facts.push(`远端 <strong>${remote}</strong>`);
    if (cache != null) facts.push(`缓存命中 <strong>${cache}</strong>`);
    if (elapsed != null) facts.push(`已用 <strong>${fmtDuration(elapsed)}</strong>`);
    if (etaSec != null && parsed.phase !== "timeout") {
      facts.push(`ETA <strong>~${fmtDuration(etaSec)}</strong>`);
    }
    if (parsed.remainSec != null) facts.push(`批上限剩 <strong>${fmtDuration(parsed.remainSec)}</strong>`);
    if (parsed.workers != null) facts.push(`并发 <strong>${parsed.workers}</strong>`);
    if (parsed.capSec != null) facts.push(`批上限 <strong>${fmtDuration(parsed.capSec)}</strong>`);

    const warn = parsed.timedOut || (parsed.remainSec != null && parsed.remainSec <= 30 && done < total);
    progressEl.hidden = false;
    progressEl.classList.toggle("is-active", true);
    progressEl.classList.toggle("is-warn", !!warn);
    progressEl.innerHTML = `<div class="quant-bars-progress-head">
        <span class="quant-bars-progress-phase">${esc(phaseLabel(parsed.phase))}</span>
        <span class="quant-bars-progress-pct">${pct > 0 ? `${Math.round(pct)}%` : "…"}</span>
      </div>
      <div class="quant-bars-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}">
        <div class="quant-bars-progress-fill" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <div class="quant-bars-progress-facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
      <p class="quant-bars-progress-msg">${esc(msg)}</p>
      <p class="quant-bars-progress-hint">强更跳过 36h 复用 · 几乎每只打远端 · 4 路并发防限流 · 到批上限会超时收尾</p>`;
  }

  function setBarsBusy(busy) {
    const on = !!busy;
    barsCard?.classList.toggle("is-busy", on);
    barsStrip?.classList.toggle("is-busy", on);
    barsBody?.classList.toggle("is-busy", on);
    btn?.classList.toggle("is-busy", on);
    if (!on) clearProgressPanel();
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
    footEl.innerHTML = `<div class="quant-bars-foot-grid">
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Store</span><span class="quant-bars-foot-v">${esc(backend)} · OHLCV</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Scope</span><span class="quant-bars-foot-v">watching · Limit ${esc(data.watching_limit ?? "—")}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Rule</span><span class="quant-bars-foot-v">交易日 15:05 前 as-of → 上一交易日</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Force</span><span class="quant-bars-foot-v">${esc(remote)}${marker.saved_at ? ` · ${esc(fmtUtcShort(marker.saved_at))} UTC` : ""}</span></div>
      <div class="quant-bars-foot-item"><span class="quant-bars-foot-k">Fields</span><span class="quant-bars-foot-v">${esc(fields)} · 下游 ŷ_EOD / IC / OOS</span></div>
    </div>`;
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

  function paint(data) {
    const { state, chip: chipText, msg } = formatStatus(data);
    setProStatusChip(chip, state, chipText);
    msgEl.textContent = msg;
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
    paintProgressPanel(
      { message: "提交强更日 K…（排队后台 Job）", pct: 0 },
      { pollStartedAt: started }
    );
    while (Date.now() - started < absoluteCapMs) {
      const { ok, data: job } = await apiFetch(
        "/api/jobs/cluster-bars-refresh?progress=1"
      );
      if (!ok || !job) {
        paintProgressPanel(
          { message: "等待 Job 心跳…", pct: 0 },
          { pollStartedAt: started }
        );
        await new Promise((r) => setTimeout(r, 800));
        continue;
      }
      if (jobId && job.id && job.id !== jobId) {
        if (sawOwnJob) break;
        paintProgressPanel(
          { message: "等待本任务接管…", pct: 0 },
          { pollStartedAt: started }
        );
        await new Promise((r) => setTimeout(r, 600));
        continue;
      }
      if (jobId && job.id === jobId) sawOwnJob = true;
      const st = job.status;
      if (st === "done") {
        paintProgressPanel(
          {
            message: job.message || "强更完成，刷新覆盖…",
            pct: 100,
          },
          { pollStartedAt: started }
        );
        return job.result || { success: true };
      }
      if (st === "failed") {
        throw new Error(job.error || job.message || "日 K 强更失败");
      }
      if (st === "idle" && sawOwnJob) {
        throw new Error("日 K 任务已结束但未返回结果");
      }
      const pct = Number(job.pct);
      const line = job.message || "强更日 K…";
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
      paintProgressPanel(job, { pollStartedAt: started });
      const headBits = [line];
      if (Number.isFinite(pct) && pct > 0) headBits.push(`${Math.round(pct)}%`);
      if (parsed.elapsedSec != null) headBits.push(fmtDuration(parsed.elapsedSec));
      if (parsed.remainSec != null) headBits.push(`剩 ${fmtDuration(parsed.remainSec)}`);
      msgEl.textContent = headBits.join(" · ");
      const updatedAt = Number(job.updated_at || 0);
      const ageSec = updatedAt ? Date.now() / 1000 - updatedAt : 999;
      if (Date.now() - started > softCapMs && ageSec > heartbeatFreshSec) {
        throw new Error("日 K 强更超时（长时间无进度）");
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("日 K 强更超时");
  }

  async function startRefresh() {
    if (inflight) return inflight;
    inflight = (async () => {
      btn.disabled = true;
      setBarsBusy(true);
      setProStatusChip(chip, "busy", "QUEUE");
      msgEl.textContent = "提交强更日 K…";
      paintProgressPanel(
        { message: "提交强更日 K…", pct: 0 },
        { pollStartedAt: Date.now() }
      );
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
          if (data.reused) {
            paintProgressPanel(
              {
                message: data.job.message || "复用进行中的强更 Job…",
                pct: data.job.pct,
              },
              { pollStartedAt: Date.now() }
            );
          }
          result = await waitClusterBarsJob(data.job.id);
        }
        if (result && result.status) {
          paint(result.status);
        } else {
          await refreshStatus();
        }
        if (result && result.success === false) {
          throw new Error(result.error || "日 K 强更失败");
        }
        const br = (result && result.bars_refresh) || {};
        if (br.remote_count != null) {
          msgEl.textContent = `强更完成 · 远端 ${br.remote_count}/${br.total ?? "—"} · 缓存 ${br.cache_count ?? "—"}`;
        }
      } catch (err) {
        setProStatusChip(chip, "error", "FAIL");
        msgEl.textContent = String((err && err.message) || err || "日 K 强更失败");
        if (progressEl) {
          progressEl.hidden = false;
          progressEl.classList.add("is-warn", "is-active");
          progressEl.innerHTML = `<p class="quant-bars-progress-msg">${esc(
            String((err && err.message) || err || "日 K 强更失败")
          )}</p>`;
        }
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
