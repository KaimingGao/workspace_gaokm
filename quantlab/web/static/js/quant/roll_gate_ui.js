import { setProStatusChip } from "./factor_corr_ui.js";
import {
  unwrapJobSnap,
  isJobHeartbeatStale,
  fmtDuration,
  jobTimings,
} from "./cluster_job_ui.js";

/** 研究枢纽 · 滚动验证（A 档 walk-forward Top1 闸门，不写模型） */
export function installRollGateUi(q) {
  const { on, apiFetch, escapeHtml } = q;
  const chip = document.getElementById("quant-rg-chip");
  const msgEl = document.getElementById("quant-rg-msg");
  const ranEl = document.getElementById("quant-rg-ran");
  const btn = document.getElementById("quant-rg-run");
  const progressEl = document.getElementById("quant-rg-progress");
  const windowEl = document.getElementById("quant-rg-window");
  const foldHost = document.getElementById("quant-rg-fold-table");
  const foldChart = document.getElementById("quant-rg-fold-chart");
  const dayHost = document.getElementById("quant-rg-day-table");
  const dayStrip = document.getElementById("quant-rg-day-strip");
  const daysHint = document.getElementById("quant-rg-days-hint");
  const section = document.getElementById("quant-section-roll-gate");

  if (!chip || !msgEl || !btn || !section) {
    return { refresh: async () => {}, start: async () => {} };
  }

  let busy = false;
  let pollTimer = null;
  let pollGen = 0;
  let lastDoc = null;
  let dayArm = "oo";
  let pollFailStreak = 0;
  /** 任务槽 started_at；结果文件没有完成时刻时，用它显示上次执行。 */
  let liveStartedIso = "";
  const POLL_FAIL_LIMIT = 8;

  function setStatus(state, shortLabel, detail) {
    setProStatusChip(chip, state, shortLabel);
    msgEl.textContent = detail || shortLabel || "";
  }

  function ranIso(doc) {
    if (!doc || typeof doc !== "object") return "";
    return String(doc.finished_at || doc.updated_at || doc.saved_at || "").trim();
  }

  function noteJobClock(job) {
    const st = String(job?.status || "").toLowerCase();
    const started = Number(job?.started_at);
    if (
      (st === "running" || st === "done" || st === "failed" || st === "error") &&
      Number.isFinite(started) &&
      started > 1e9
    ) {
      liveStartedIso = new Date(started * 1000).toISOString();
      return;
    }
    if (st === "idle" || st === "empty") liveStartedIso = "";
  }

  /** 上次执行：完成时刻优先，否则最近落盘；本地时区，精确到分。 */
  function fmtRanLocal(iso) {
    const raw = String(iso || "").trim();
    if (!raw) return "";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) {
      return raw.replace("T", " ").replace(/\.\d+.*$/, "").slice(0, 16);
    }
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function paintRan(doc, { keep = false } = {}) {
    if (!ranEl) return;
    const iso = ranIso(doc) || liveStartedIso;
    if (!iso) {
      if (keep) return;
      ranEl.textContent = "上次 —";
      ranEl.title = "尚无执行记录";
      return;
    }
    const text = fmtRanLocal(iso);
    ranEl.textContent = `上次 ${text}`;
    ranEl.title = `上次执行 ${text}`;
  }

  function setBusy(on) {
    busy = !!on;
    btn.disabled = busy;
    section.classList.toggle("is-busy", busy);
  }

  function parseRgPhase(message, cur, tot) {
    const msg = String(message || "").trim();
    const low = msg.toLowerCase();
    if (/排队|启动|入队/.test(msg)) {
      return { chip: "QUEUE", label: "排队", stage: "queue", step: 0 };
    }
    if (/拉\s*A\s*档|日线/.test(msg) && !/fold/.test(low)) {
      return { chip: "LOAD", label: "拉日线", stage: "prep", step: 0 };
    }
    if (/建\s*oo|oo\s*日面板/.test(msg)) {
      return { chip: "PANEL", label: "建 oo 面板", stage: "prep", step: 0 };
    }
    if (/建\s*τc|建\s*tc|τc\s*日面板|tc\s*日面板/.test(msg)) {
      return { chip: "PANEL", label: "建 τc 面板", stage: "prep", step: 0 };
    }
    if (/打分|score/.test(msg)) {
      return {
        chip: tot ? `F${cur}/${tot}` : "SCORE",
        label: "打分",
        stage: "score",
        step: 5,
      };
    }
    if (/拟合|fit|lambdarank|ridge/i.test(msg)) {
      let label = "拟合";
      let step = 1;
      if (/OO\s*Ridge|oo\s*ridge/i.test(msg)) {
        label = "拟合 oo Ridge";
        step = 1;
      } else if (/OO\s*Lambda|oo\s*lambda/i.test(msg)) {
        label = "拟合 oo LambdaRank";
        step = 2;
      } else if (/τc\s*Ridge|tc\s*ridge/i.test(msg)) {
        label = "拟合 τc Ridge";
        step = 3;
      } else if (/τc\s*Lambda|tc\s*lambda/i.test(msg)) {
        label = "拟合 τc LambdaRank";
        step = 4;
      }
      return {
        chip: tot ? `F${cur}/${tot}` : "FIT",
        label,
        stage: "fit",
        step,
      };
    }
    if (/完成/.test(msg)) {
      return { chip: "DONE", label: "完成", stage: "done", step: 6 };
    }
    if (cur <= 0) {
      return { chip: "PREP", label: msg || "准备", stage: "prep", step: 0 };
    }
    return {
      chip: tot ? `F${cur}/${tot}` : "RUN",
      label: msg || "运行中",
      stage: "run",
      step: 0,
    };
  }

  function prepPctHint(stage, message) {
    const msg = String(message || "");
    if (stage === "queue") return 4;
    if (/拉\s*A\s*档|日线/.test(msg)) return 10;
    if (/建\s*oo|oo\s*日面板/.test(msg)) return 22;
    if (/建\s*τc|建\s*tc|τc\s*日面板/.test(msg)) return 36;
    if (stage === "prep") return 16;
    return null;
  }

  /** 折内细进度：准备 / 4 段拟合 / 打分 → 落在折区间内，避免长时间钉死在 33/66 */
  function foldAwarePct(cur, tot, phase, rawPct) {
    const t = Math.max(1, tot);
    if (cur <= 0) {
      const hint = prepPctHint(phase.stage, phase.label);
      return Math.max(Number(rawPct) || 0, hint != null ? hint : 8);
    }
    const base = ((cur - 1) / t) * 100;
    const span = 100 / t;
    let frac = 0.08;
    if (phase.stage === "fit") {
      const step = Math.max(1, Math.min(4, Number(phase.step) || 1));
      frac = (step - 0.35) / 5;
    } else if (phase.stage === "score") {
      frac = 0.88;
    } else if (phase.stage === "done") {
      frac = 1;
    }
    const soft = base + span * frac;
    const floor = Number.isFinite(Number(rawPct)) ? Number(rawPct) : 0;
    // 不低于 Job 宏进度，也不越过下一折起点
    return Math.max(floor, Math.min(base + span * 0.97, soft));
  }

  function paintProgress(job) {
    if (!progressEl) return;
    if (!job || String(job.status || "") !== "running") {
      progressEl.hidden = true;
      progressEl.classList.remove("is-warn", "is-prep", "is-fit");
      progressEl.textContent = "";
      return;
    }
    const cur = Math.max(0, Number(job.current) || 0);
    const tot = Math.max(0, Number(job.total) || 0) || 3;
    const msg = String(job.message || "").trim();
    const phase = parseRgPhase(msg, cur, tot);
    const { elapsed, heartbeat } = jobTimings(job);
    const pct = Math.max(
      0,
      Math.min(100, foldAwarePct(cur, tot, phase, job.pct))
    );
    const alive = heartbeat == null || heartbeat < 120;
    const longFit = phase.stage === "fit" && alive;

    const foldPills = Array.from({ length: tot }, (_, i) => {
      const n = i + 1;
      const st = cur > n ? "is-done" : cur === n ? "is-active" : "is-pending";
      const title =
        st === "is-done"
          ? `折 ${n} 完成`
          : st === "is-active"
            ? `折 ${n} · ${phase.label}`
            : `折 ${n} 待跑`;
      return `<span class="quant-rg-fold-pill ${st}" title="${escapeHtml(title)}"><span class="quant-rg-fold-pill-n">${n}</span></span>`;
    }).join("");

    const facts = [];
    if (tot > 0) {
      facts.push(
        cur > 0
          ? `折 <strong>${cur}</strong>/${tot}`
          : `准备 · 共 <strong>${tot}</strong> 折`
      );
    }
    if (elapsed != null) facts.push(`已用 <strong>${fmtDuration(elapsed)}</strong>`);
    if (heartbeat != null) {
      const hbCls = heartbeat >= 180 ? " quant-job-hb-stale" : "";
      facts.push(
        `<span class="${hbCls.trim()}">心跳 <strong>${fmtDuration(heartbeat)}</strong></span>`
      );
    }
    if (longFit) {
      facts.push("<span class=\"quant-rg-alive\">计算中</span>");
    }

    const hint =
      phase.stage === "fit"
        ? "单折 Ridge×2 + LambdaRank×2，常 10–20 分钟；心跳在刷新即未卡死"
        : phase.stage === "prep"
          ? "准备阶段：拉 A 档日线并建面板"
          : "";
    const detail = msg && msg !== phase.label ? msg : "";
    progressEl.hidden = false;
    progressEl.classList.toggle("is-prep", cur <= 0 || phase.stage === "prep");
    progressEl.classList.toggle("is-fit", phase.stage === "fit");
    progressEl.classList.toggle("is-warn", heartbeat != null && heartbeat >= 180);
    progressEl.innerHTML = `
      <div class="quant-bars-progress-head quant-rg-progress-head">
        <span class="quant-bars-progress-phase">${escapeHtml(phase.chip)}</span>
        <span class="quant-rg-progress-label">${escapeHtml(phase.label)}${
          longFit ? '<span class="quant-rg-pulse" aria-hidden="true"></span>' : ""
        }</span>
        <span class="quant-bars-progress-pct">${pct.toFixed(0)}%</span>
      </div>
      <div class="quant-rg-fold-track" aria-label="分折进度">${foldPills}</div>
      <div class="quant-bars-progress-track"><div class="quant-bars-progress-fill" style="width:${pct}%"></div></div>
      <div class="quant-bars-progress-facts">${facts
        .map((f) => `<span>${f}</span>`)
        .join("")}</div>
      ${
        hint
          ? `<div class="quant-bars-progress-hint quant-rg-progress-hint">${escapeHtml(hint)}</div>`
          : ""
      }
      ${
        detail
          ? `<div class="quant-bars-progress-msg quant-rg-progress-msg">${escapeHtml(detail)}</div>`
          : ""
      }`;
  }

  function fmtHit(arm) {
    if (!arm || arm.hit_pct == null || !arm.n) return "—";
    return `${Number(arm.hit_pct).toFixed(1)}%`;
  }

  function fmtHitSub(arm) {
    if (!arm) return "vs 50% 基线";
    const n = Number(arm.n) || 0;
    const hits = Number(arm.hits) || 0;
    if (!n) return "无有仓日";
    const edge = Number(arm.hit_pct) - 50;
    const edgeTxt = Number.isFinite(edge)
      ? `${edge >= 0 ? "+" : ""}${edge.toFixed(1)}pp`
      : "";
    return `${hits}/${n}${edgeTxt ? ` · ${edgeTxt}` : ""}`;
  }

  function fmtEmpty(arm) {
    if (!arm) return "—";
    const empty = Number(arm.empty) || 0;
    const total =
      Number(arm.total_days) || empty + (Number(arm.n) || 0);
    if (!total) return "—";
    return `${empty}/${total}`;
  }

  function fmtEmptySub(arm) {
    if (!arm) return "不计命中分母";
    const empty = Number(arm.empty) || 0;
    const total =
      Number(arm.total_days) || empty + (Number(arm.n) || 0);
    if (!total) return "不计命中分母";
    return `${((100 * empty) / total).toFixed(0)}% 决策日空闸`;
  }

  function fmtRet(arm) {
    if (!arm || arm.mean_ret == null) return "—";
    const v = Number(arm.mean_ret);
    if (!Number.isFinite(v)) return "—";
    const sign = v > 0 ? "+" : "";
    return `${sign}${v.toFixed(3)}%`;
  }

  function hitTone(pct) {
    if (pct == null || !Number.isFinite(Number(pct))) return "";
    return Number(pct) >= 50 ? "is-ok" : "is-danger";
  }

  function retTone(v) {
    if (v == null || !Number.isFinite(Number(v))) return "";
    if (Number(v) > 1e-12) return "is-up";
    if (Number(v) < -1e-12) return "is-down";
    return "";
  }

  function setMeter(id, pct, tone) {
    const meter = document.getElementById(id);
    if (!meter) return;
    const fill = meter.querySelector(".quant-rg-meter-fill");
    const w = pct == null || !Number.isFinite(Number(pct))
      ? 0
      : Math.max(0, Math.min(100, Number(pct)));
    meter.classList.remove("is-ok", "is-danger", "is-empty");
    if (pct == null || !Number.isFinite(Number(pct))) {
      meter.classList.add("is-empty");
    } else if (tone) {
      meter.classList.add(tone);
    }
    if (fill) fill.style.width = `${w}%`;
  }

  function setKpi(id, value, sub, tone, { signed = false } = {}) {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = value;
      el.classList.remove("is-ok", "is-danger", "is-up", "is-down");
      if (tone) el.classList.add(tone);
    }
    const subEl = document.getElementById(`${id}-sub`);
    if (subEl && sub != null) subEl.textContent = sub;
    const card = el?.closest(".quant-kpi-card");
    if (card) {
      card.classList.toggle("is-empty", value === "—" || !value);
      card.classList.remove("is-good", "is-bad", "is-mid");
      if (tone === "is-ok" || (signed && tone === "is-up")) {
        card.classList.add("is-good");
      } else if (tone === "is-danger" || (signed && tone === "is-down")) {
        card.classList.add("is-bad");
      }
    }
  }

  function paintWindow(doc) {
    if (!windowEl) return;
    if (!doc || !doc.window) {
      windowEl.hidden = true;
      windowEl.textContent = "";
      return;
    }
    const win = doc.window;
    const folds = Array.isArray(doc.folds) ? doc.folds : [];
    const params = doc.params || {};
    const bits = [];
    if (win.start && win.end) bits.push(`${win.start} → ${win.end}`);
    if (win.n_test_days != null) bits.push(`${win.n_test_days} 决策日`);
    const uni = String(params.universe || "");
    const nPool = win.n_pool != null ? win.n_pool : win.n_tier_a;
    if (nPool != null) {
      // watching = 改 A 档前的旧落盘；新跑一律 tier_a
      bits.push(uni === "watching" ? `全池(旧) ${nPool}` : `A档 ${nPool}`);
    }
    bits.push(`折 ${folds.length}/${params.folds || 3}`);
    if (doc.partial) bits.push("部分结果");
    windowEl.hidden = !bits.length;
    windowEl.innerHTML = bits
      .map((b) => `<span class="quant-rg-window-chip">${escapeHtml(b)}</span>`)
      .join("");
  }

  function paintKpis(doc) {
    const sum = doc && doc.summary ? doc.summary : null;
    const oo = sum && sum.oo ? sum.oo : null;
    const tc = sum && sum.tc ? sum.tc : null;

    setKpi(
      "quant-rg-kpi-oo-hit",
      fmtHit(oo),
      fmtHitSub(oo),
      hitTone(oo?.hit_pct)
    );
    setMeter("quant-rg-meter-oo", oo?.hit_pct, hitTone(oo?.hit_pct));
    setKpi("quant-rg-kpi-oo-empty", fmtEmpty(oo), fmtEmptySub(oo));
    setKpi(
      "quant-rg-kpi-oo-ret",
      fmtRet(oo),
      "open→open",
      retTone(oo?.mean_ret),
      { signed: true }
    );

    setKpi(
      "quant-rg-kpi-tc-hit",
      fmtHit(tc),
      fmtHitSub(tc),
      hitTone(tc?.hit_pct)
    );
    setMeter("quant-rg-meter-tc", tc?.hit_pct, hitTone(tc?.hit_pct));
    setKpi("quant-rg-kpi-tc-empty", fmtEmpty(tc), fmtEmptySub(tc));
    setKpi(
      "quant-rg-kpi-tc-ret",
      fmtRet(tc),
      "τ→close",
      retTone(tc?.mean_ret),
      { signed: true }
    );
    paintWindow(doc);
  }

  function hitBar(pct, tone) {
    if (pct == null || !Number.isFinite(Number(pct))) {
      return '<span class="quant-rg-hitbar is-empty"><span class="quant-rg-hitbar-fill" style="width:0"></span><span class="quant-rg-hitbar-base"></span></span>';
    }
    const w = Math.max(0, Math.min(100, Number(pct)));
    return `<span class="quant-rg-hitbar ${tone || ""}"><span class="quant-rg-hitbar-fill" style="width:${w}%"></span><span class="quant-rg-hitbar-base" title="50%"></span></span>`;
  }

  function armCell(arm, key) {
    if (!arm) return "—";
    if (key === "hit") {
      if (arm.hit_pct == null || !arm.n) return "—";
      const tone = hitTone(arm.hit_pct);
      return `<div class="quant-rg-hitcell">${hitBar(arm.hit_pct, tone)}<div class="quant-rg-hitcell-txt"><span class="${tone}">${escapeHtml(
        fmtHit(arm)
      )}</span> <span class="muted">${escapeHtml(fmtHitSub(arm))}</span></div></div>`;
    }
    if (key === "empty") return escapeHtml(fmtEmpty(arm));
    if (key === "ret") {
      const tone = retTone(arm.mean_ret);
      return `<span class="${tone}">${escapeHtml(fmtRet(arm))}</span>`;
    }
    return "—";
  }

  function paintFoldChart(doc) {
    if (!foldChart) return;
    const folds = Array.isArray(doc?.folds) ? doc.folds : [];
    if (!folds.length) {
      foldChart.innerHTML = "";
      foldChart.hidden = true;
      return;
    }
    foldChart.hidden = false;
    const rows = folds
      .map((f) => {
        const oo = f.oo && typeof f.oo === "object" ? f.oo : null;
        const tc = f.tc && typeof f.tc === "object" ? f.tc : null;
        const ooPct = oo?.hit_pct;
        const tcPct = tc?.hit_pct;
        const win =
          f.eval_start && f.eval_end
            ? `${String(f.eval_start).slice(5)}–${String(f.eval_end).slice(5)}`
            : "";
        return `<div class="quant-rg-fold-row">
          <div class="quant-rg-fold-lab">F${escapeHtml(String(f.fold ?? ""))}<span class="muted">${escapeHtml(win)}</span></div>
          <div class="quant-rg-fold-bars">
            <div class="quant-rg-fold-barline" title="oo ${escapeHtml(fmtHit(oo))}">
              <span class="quant-rg-fold-arm">oo</span>
              ${hitBar(ooPct, hitTone(ooPct))}
              <span class="quant-rg-fold-pct ${hitTone(ooPct)}">${escapeHtml(fmtHit(oo))}</span>
            </div>
            <div class="quant-rg-fold-barline" title="τc ${escapeHtml(fmtHit(tc))}">
              <span class="quant-rg-fold-arm">τc</span>
              ${hitBar(tcPct, hitTone(tcPct))}
              <span class="quant-rg-fold-pct ${hitTone(tcPct)}">${escapeHtml(fmtHit(tc))}</span>
            </div>
          </div>
        </div>`;
      })
      .join("");
    foldChart.innerHTML = rows;
  }

  function paintFoldTable(doc) {
    if (!foldHost) return;
    paintFoldChart(doc);
    const folds = Array.isArray(doc?.folds) ? doc.folds : [];
    if (!folds.length) {
      foldHost.innerHTML =
        '<p class="quant-empty muted">尚未跑折。点「运行」启动 A 档 walk-forward（LB500 · HO10×3；须先有观察池分档）。</p>';
      return;
    }
    const rows = folds
      .map((f) => {
        const win =
          f.eval_start && f.eval_end
            ? `${f.eval_start}→${f.eval_end}`
            : "—";
        const oo = f.oo && typeof f.oo === "object" ? f.oo : null;
        const tc = f.tc && typeof f.tc === "object" ? f.tc : null;
        const err = f.ok === false ? String(f.error || "fail") : "";
        return `<tr>
          <td class="quant-rg-fold-idx">F${escapeHtml(String(f.fold ?? ""))}${err ? ` <span class="muted">${escapeHtml(err)}</span>` : ""}</td>
          <td class="quant-rg-fold-win">${escapeHtml(win)}</td>
          <td>${armCell(oo, "hit")}</td>
          <td>${armCell(oo, "empty")}</td>
          <td>${armCell(oo, "ret")}</td>
          <td>${armCell(tc, "hit")}</td>
          <td>${armCell(tc, "empty")}</td>
          <td>${armCell(tc, "ret")}</td>
        </tr>`;
      })
      .join("");
    foldHost.innerHTML = `<table class="quant-weight-table quant-rg-fold-table">
      <thead><tr>
        <th>折</th><th>评测窗</th>
        <th>oo 命中</th><th>oo 空/闸</th><th>oo 日均</th>
        <th>τc 命中</th><th>τc 空/闸</th><th>τc 日均</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  const DAY_REASON_LABEL = {
    ok: "—",
    empty: "空闸",
    y_gate: "ŷ 门槛",
    no_day: "无截面",
    bad_day: "截面坏",
    no_rows: "无有效行",
  };

  function dayOutcome(row) {
    if (!row) return { kind: "empty", label: "空闸", reason: "empty" };
    if (row.empty) {
      const reason = String(row.reason || "empty");
      return { kind: "empty", label: "空闸", reason };
    }
    if (row.hit) return { kind: "hit", label: "命中", reason: "" };
    return { kind: "miss", label: "未中", reason: "" };
  }

  function dayNote(row) {
    const out = dayOutcome(row);
    if (out.kind !== "empty") return "—";
    const reason = out.reason || "empty";
    if (reason === "empty") return "—";
    return DAY_REASON_LABEL[reason] || reason;
  }

  function fmtDayDate(raw) {
    const s = String(raw || "");
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(5, 10);
    return s || "—";
  }

  function fmtDayY(v) {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    return Number(v).toFixed(3);
  }

  function fmtDayLabel(v, empty) {
    if (empty || v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(3)}%`;
  }

  function collectDays(doc, arm) {
    const folds = Array.isArray(doc?.folds) ? doc.folds : [];
    const out = [];
    for (const f of folds) {
      const pack = f && f[arm];
      const days = pack && Array.isArray(pack.days) ? pack.days : [];
      for (const d of days) {
        if (d && typeof d === "object") {
          out.push({
            ...d,
            fold: d.fold != null ? d.fold : f.fold,
          });
        }
      }
    }
    return out;
  }

  function paintDayStrip(doc) {
    if (!dayStrip) return;
    const days = collectDays(doc, dayArm);
    if (!days.length) {
      dayStrip.innerHTML = "";
      dayStrip.hidden = true;
      if (daysHint) daysHint.textContent = "有仓 / 空闸序列";
      return;
    }
    dayStrip.hidden = false;
    let hit = 0;
    let miss = 0;
    let empty = 0;
    const cells = days
      .map((d, i) => {
        const out = dayOutcome(d);
        let cls = "is-empty";
        let title = `${d.date || ""} · ${out.label}`;
        if (out.kind === "empty") {
          empty += 1;
        } else if (out.kind === "hit") {
          hit += 1;
          cls = "is-hit";
          title = `${d.date || ""} · 命中 · ${d.code || ""}`;
        } else {
          miss += 1;
          cls = "is-miss";
          title = `${d.date || ""} · 未中 · ${d.code || ""}`;
        }
        return `<button type="button" class="quant-rg-dot ${cls}" data-day-idx="${i}" title="${escapeHtml(
          title
        )}" aria-label="${escapeHtml(title)}"></button>`;
      })
      .join("");
    dayStrip.innerHTML = `<div class="quant-rg-dot-row">${cells}</div>
      <div class="quant-rg-dot-legend">
        <span><i class="quant-rg-dot is-hit"></i>命中 ${hit}</span>
        <span><i class="quant-rg-dot is-miss"></i>未中 ${miss}</span>
        <span><i class="quant-rg-dot is-empty"></i>空闸 ${empty}</span>
      </div>`;
    dayStrip.querySelectorAll(".quant-rg-dot[data-day-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.getAttribute("data-day-idx"));
        const row = dayHost?.querySelector(`tr[data-day-idx="${idx}"]`);
        if (!row) return;
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
        row.classList.add("is-flash");
        window.setTimeout(() => row.classList.remove("is-flash"), 900);
      });
    });
    if (daysHint) {
      daysHint.textContent = `${days.length} 日 · 命中 ${hit} · 空闸 ${empty}`;
    }
  }

  function paintDayTable(doc) {
    if (!dayHost) return;
    const days = collectDays(doc, dayArm);
    if (!days.length) {
      dayHost.innerHTML =
        '<p class="quant-empty muted">无逐日明细（跑完或读上次后展开）。</p>';
      paintDayStrip(doc);
      return;
    }
    let prevFold = null;
    const rows = days
      .map((d, i) => {
        const empty = !!d.empty;
        const out = dayOutcome(d);
        const fold = d.fold != null ? `F${d.fold}` : "—";
        const foldChanged =
          prevFold != null && String(d.fold) !== String(prevFold);
        prevFold = d.fold;
        const code = d.code ? String(d.code) : "—";
        const y = fmtDayY(d.y_hat);
        const lab = fmtDayLabel(d.label, empty);
        const note = dayNote(d);
        const rowCls = [
          out.kind === "hit"
            ? "is-hit"
            : out.kind === "miss"
              ? "is-miss"
              : "is-empty-day",
          foldChanged ? "is-fold-break" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return `<tr class="${rowCls}" data-day-idx="${i}" title="${escapeHtml(
          String(d.date || "")
        )}">
          <td class="quant-rg-day-fold">${escapeHtml(fold)}</td>
          <td class="quant-rg-day-date">${escapeHtml(fmtDayDate(d.date))}</td>
          <td class="quant-rg-day-out"><span class="quant-rg-out quant-rg-out--${out.kind}">${escapeHtml(
            out.label
          )}</span></td>
          <td class="quant-rg-day-code">${escapeHtml(code)}</td>
          <td class="num quant-rg-day-y">${escapeHtml(y)}</td>
          <td class="num quant-rg-day-ret ${retTone(
            empty ? null : d.label
          )}">${escapeHtml(lab)}</td>
          <td class="quant-rg-day-note"><span class="quant-rg-note">${escapeHtml(
            note
          )}</span></td>
        </tr>`;
      })
      .join("");
    dayHost.innerHTML = `<table class="quant-weight-table quant-rg-day-table">
      <thead><tr>
        <th>折</th><th>日期</th><th>结果</th><th>代码</th><th>ŷ</th><th>收益</th><th>备注</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    paintDayStrip(doc);
  }

  function applyDoc(doc, { fromPartial = false } = {}) {
    if (!doc || doc.success === false) {
      if (!fromPartial) {
        lastDoc = null;
        paintKpis(null);
        paintFoldTable(null);
        paintDayTable(null);
        paintRan(null);
      }
      return;
    }
    if (
      fromPartial &&
      lastDoc &&
      Array.isArray(lastDoc.folds) &&
      Array.isArray(doc.folds)
    ) {
      const sameRun =
        String(doc.job_id || "").trim() &&
        String(doc.job_id || "").trim() === String(lastDoc.job_id || "").trim();
      const byFold = new Map(
        lastDoc.folds.map((f) => [Number(f.fold), f])
      );
      doc = {
        ...doc,
        folds: doc.folds.map((f) => {
          const prev = byFold.get(Number(f.fold));
          if (!prev) return f;
          const mergeArm = (arm) => {
            const cur = f[arm];
            const old = prev[arm];
            if (!cur || typeof cur !== "object") return cur;
            // 显式空数组 = 本折无逐日，勿继承上一轮
            if (Array.isArray(cur.days)) {
              if (cur.days.length) return cur;
              return { ...cur, days: [] };
            }
            // progress 剥掉 days 时才补；且必须同 job
            if (
              sameRun &&
              old &&
              Array.isArray(old.days) &&
              old.days.length
            ) {
              return { ...cur, days: old.days };
            }
            return cur;
          };
          return { ...f, oo: mergeArm("oo"), tc: mergeArm("tc") };
        }),
      };
    }
    lastDoc = doc;
    paintKpis(doc);
    paintFoldTable(doc);
    paintDayTable(doc);
    paintRan(doc, { keep: fromPartial && !ranIso(doc) });
  }

  function stopPoll() {
    pollGen += 1;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function fetchLast() {
    const { ok, data } = await apiFetch("/api/quant/roll-gate-top1/last");
    if (!ok || !data || data.exists === false || data.success === false) {
      return null;
    }
    return data;
  }

  async function fetchProgress() {
    const { ok, data } = await apiFetch(
      "/api/quant/roll-gate-top1/progress"
    );
    if (!ok || !data) return null;
    return data;
  }

  function sameRunDoc(doc, job) {
    if (!doc || typeof doc !== "object") return false;
    const docId = String(doc.job_id || "").trim();
    const jobId = String(job?.id || "").trim();
    if (docId && jobId) return docId === jobId;
    // 无 job_id 的旧落盘：仅在非运行态采用；运行中拒绝，避免串到上一轮
    return false;
  }

  async function pollOnce(gen) {
    if (gen !== pollGen) return;
    try {
      const prog = await fetchProgress();
      if (gen !== pollGen) return;
      // 网络空响：勿立刻清 busy；连续失败再放行
      if (!prog) {
        pollFailStreak += 1;
        if (busy) {
          setStatus(
            "warn",
            "进度暂失",
            pollFailStreak >= POLL_FAIL_LIMIT
              ? "多次读进度失败，已解除运行锁；可刷新或再点运行"
              : "网络异常，继续等待任务…"
          );
          if (pollFailStreak >= POLL_FAIL_LIMIT) {
            stopPoll();
            setBusy(false);
            paintProgress(null);
          }
        }
        return;
      }
      pollFailStreak = 0;
      const job = unwrapJobSnap(prog) || prog?.job;
      const status = String(job?.status || prog?.progress?.status || "");
      noteJobClock(job);
      if (!status) {
        if (busy) {
          pollFailStreak += 1;
          setStatus("warn", "进度暂失", "任务槽无状态，继续等待…");
          if (pollFailStreak >= POLL_FAIL_LIMIT) {
            stopPoll();
            setBusy(false);
            paintProgress(null);
          }
          return;
        }
      }
      paintProgress(job);

      const partial = prog?.progress?.partial;
      if (partial && Array.isArray(partial.folds) && partial.folds.length) {
        if (!job?.id || sameRunDoc(partial, job) || partial.partial === true) {
          applyDoc(partial, { fromPartial: true });
        }
      }

      if (status === "running") {
        const msg = String(
          job?.message || prog?.progress?.message || "运行中…"
        );
        const fold = Number(job?.current || prog?.progress?.fold || 0);
        const folds = Number(job?.total || prog?.progress?.folds || 3);
        setBusy(true);
        setStatus(
          "busy",
          folds ? `fold ${fold}/${folds}` : "运行中",
          msg
        );
        if (isJobHeartbeatStale(job, 180)) {
          setStatus("warn", "心跳慢", msg || "任务可能仍在拟合…");
        }
        // progress 折表不含 days；仅当 last 属于本轮 job 时才用磁盘补明细
        if (partial && Array.isArray(partial.folds) && partial.folds.length) {
          try {
            const last = await fetchLast();
            if (gen === pollGen && last && sameRunDoc(last, job)) {
              applyDoc(last, { fromPartial: true });
            }
          } catch (_) {
            /* ignore */
          }
        }
        return;
      }

      stopPoll();
      setBusy(false);
      paintProgress(null);

      if (status === "failed" || status === "error") {
        setStatus(
          "error",
          "失败",
          String(job?.error || job?.message || "滚动验证失败")
        );
        return;
      }

      const last = await fetchLast();
      if (last) {
        applyDoc(last);
        const note = last.partial
          ? "部分结果 · 空仓不计命中分母 · 不入库"
          : "每折重训 · 空仓不计命中分母 · 不入库";
        setStatus("ok", last.partial ? "部分完成" : "完成", note);
      } else if (status === "done") {
        setStatus("ok", "完成", "结果已写入，刷新可读上次");
      } else {
        setStatus("idle", "空闲", "点「运行」开始 walk-forward");
      }
    } catch (err) {
      if (gen !== pollGen) return;
      pollFailStreak += 1;
      setStatus(
        "warn",
        "轮询异常",
        String((err && err.message) || err || "progress 失败")
      );
      if (pollFailStreak >= POLL_FAIL_LIMIT) {
        stopPoll();
        setBusy(false);
        paintProgress(null);
        setStatus(
          "error",
          "轮询中断",
          "连续失败已解除运行锁；请刷新或再点运行"
        );
      }
    }
  }

  function startPoll() {
    stopPoll();
    pollFailStreak = 0;
    const gen = pollGen;
    pollTimer = setInterval(() => {
      void pollOnce(gen);
    }, 2500);
    void pollOnce(gen);
  }

  async function refresh() {
    if (!document.getElementById("quant-section-roll-gate")) return null;
    try {
      const prog = await fetchProgress();
      const job = unwrapJobSnap(prog) || prog?.job;
      const status = String(job?.status || "");
      noteJobClock(job);
      if (status === "running") {
        setBusy(true);
        setStatus("busy", "运行中", String(job?.message || "恢复进度…"));
        paintProgress(job);
        startPoll();
        const partial = prog?.progress?.partial;
        if (partial && Array.isArray(partial.folds) && partial.folds.length) {
          if (!job?.id || sameRunDoc(partial, job) || partial.partial === true) {
            applyDoc(partial, { fromPartial: true });
          }
        }
        const last = await fetchLast();
        if (last && sameRunDoc(last, job)) applyDoc(last, { fromPartial: true });
        else paintRan(lastDoc);
        return last && sameRunDoc(last, job) ? last : null;
      }
      const last = await fetchLast();
      if (last) {
        applyDoc(last);
        setStatus(
          "ok",
          last.partial ? "上次（部分）" : "上次结果",
          last.note || "每折重训 · 空仓不计命中分母 · 不入库"
        );
      } else {
        applyDoc(null);
        setStatus("idle", "未跑", "Walk-forward · Top1 闸门 · 不入库");
      }
      return last;
    } catch (err) {
      setStatus(
        "warn",
        "读取失败",
        String((err && err.message) || err || "")
      );
      return null;
    }
  }

  async function start() {
    if (busy) {
      setStatus("warn", "运行中", "请等待当前任务结束");
      return null;
    }
    setBusy(true);
    pollFailStreak = 0;
    lastDoc = null;
    paintKpis(null);
    paintFoldTable(null);
    paintDayTable(null);
    setStatus("busy", "启动中", "入队 A 档 walk-forward…");
    paintProgress({
      status: "running",
      pct: 0,
      current: 0,
      total: 3,
      message: "排队中…",
    });
    try {
      const { ok, data, error } = await apiFetch(
        "/api/quant/roll-gate-top1",
        { method: "POST" }
      );
      if (!ok || !data || data.success === false) {
        setBusy(false);
        paintProgress(null);
        setStatus(
          "error",
          "启动失败",
          String(error || data?.error || data?.detail || "无法启动")
        );
        return null;
      }
      if (data.reused) {
        setStatus("busy", "已有任务", "复用运行中的滚动验证");
      } else {
        setStatus("busy", "fold 0/3", "拉 A 档日线…");
      }
      startPoll();
      return data;
    } catch (err) {
      setBusy(false);
      paintProgress(null);
      setStatus(
        "error",
        "启动失败",
        String((err && err.message) || err || "")
      );
      return null;
    }
  }

  on("quant-rg-run", "click", (e) => {
    e.preventDefault();
    void start();
  });

  document.querySelectorAll(".quant-rg-day-tab").forEach((tab) => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();
      const arm = String(tab.getAttribute("data-arm") || "oo");
      dayArm = arm === "tc" ? "tc" : "oo";
      document.querySelectorAll(".quant-rg-day-tab").forEach((t) => {
        const onTab = t === tab;
        t.classList.toggle("is-active", onTab);
        t.setAttribute("aria-selected", onTab ? "true" : "false");
      });
      paintDayTable(lastDoc);
    });
  });

  void refresh();

  return { refresh, start };
}
