/** Platform panel: memory / decisions / feedback / schedule / prefill / audit (D2–D6 + P2 + W2.5). */
import { apiFetch } from "./api_client.js";

export function initPlatform(ctx) {
  const meta = document.getElementById("platform-meta");
  if (!meta && !document.getElementById("memory-risk")) return;

  const riskEl = document.getElementById("memory-risk");
  const horizonDisplay = document.getElementById("memory-horizon-display");
  const notesEl = document.getElementById("memory-notes");
  const decisionList = document.getElementById("decision-list");
  const feedbackOut = document.getElementById("feedback-out");
  const prefillOut = document.getElementById("prefill-out");
  const scheduleOut = document.getElementById("schedule-last-out");
  const alertList = document.getElementById("schedule-alert-list");

  let lastMonitorAlerts = [];
  let lastPaperMetrics = null;
  /** 研究默认 horizon（只读展示；编辑入口在研究枢纽） */
  let cachedHorizonDays = 3;

  function setMeta(text) {
    if (meta) meta.textContent = text;
  }

  function renderAlerts(alerts) {
    lastMonitorAlerts = Array.isArray(alerts) ? alerts : [];
    if (!alertList) return;
    if (!lastMonitorAlerts.length) {
      alertList.innerHTML =
        '<li class="platform-item"><span class="sub">暂无监控告警</span></li>';
      return;
    }
    alertList.innerHTML = lastMonitorAlerts
      .slice(0, 12)
      .map((a) => {
        const msg =
          typeof a === "string" ? a : a.message || a.code || JSON.stringify(a);
        const lvl = typeof a === "object" && a.level ? a.level : "info";
        return `<li class="platform-item"><div class="name">${msg}</div><div class="sub">${lvl}</div></li>`;
      })
      .join("");
  }

  function clampHorizonDays(v, fallback = 1) {
    const n = Number(v);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(1, Math.min(10, Math.round(n)));
  }

  async function loadMemory() {
    const res = await fetch("/api/memory");
    const data = await res.json();
    const prefs = (data.effective || data.preferences) || {};
    if (riskEl && prefs.risk_style) riskEl.value = prefs.risk_style;
    cachedHorizonDays = clampHorizonDays(prefs.horizon_days, 1);
    if (horizonDisplay) horizonDisplay.textContent = String(cachedHorizonDays);
    if (notesEl) notesEl.value = prefs.notes || "";
    setMeta(
      data.exists
        ? `已加载偏好 · 研究默认 horizon=${cachedHorizonDays}d（只读；改在研究枢纽）`
        : "尚无 memory.json · 可保存风险风格/备注"
    );
    return data;
  }

  async function saveMemory() {
    const preferences = {
      risk_style: riskEl ? riskEl.value : "balanced",
      // 保留既有研究默认，避免平台保存时把 horizon 冲掉
      horizon_days: cachedHorizonDays,
      notes: notesEl ? notesEl.value : "",
    };
    const res = await fetch("/api/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    const eff = data.effective || data.preferences || preferences;
    cachedHorizonDays = clampHorizonDays(eff.horizon_days, cachedHorizonDays);
    if (horizonDisplay) horizonDisplay.textContent = String(cachedHorizonDays);
    setMeta(
      `偏好已保存 · 研究默认 horizon=${cachedHorizonDays}d 仍只影响研究/回测`
    );
    return data;
  }

  async function loadDecisions() {
    const res = await fetch("/api/decisions?limit=20");
    const data = await res.json();
    if (!decisionList) return data;
    const items = data.items || [];
    if (!items.length) {
      decisionList.innerHTML = '<li class="platform-item"><span class="sub">暂无决策记录</span></li>';
      return data;
    }
    decisionList.innerHTML = items
      .map((d) => {
        const title = `${d.stock_name || d.stock_code || "—"} · ${d.stance_label || "—"}`;
        const sub = `${d.source || ""} · ${d.id || ""}`;
        return `<li class="platform-item"><div class="name">${title}</div><div class="sub">${sub}</div></li>`;
      })
      .join("");
    return data;
  }

  async function loadScheduleLast() {
    const res = await fetch("/api/schedule/last");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    const last = data.last || null;
    if (scheduleOut) {
      if (data.empty || !last) {
        scheduleOut.textContent = "尚无 schedule_last_run.json";
      } else {
        const slim = {
          kind: last.kind,
          ts: last.ts,
          strategy_id: last.strategy_id,
          cost_model: last.cost_model,
          data_quality: last.data_quality,
          risk_blocks: last.risk_blocks,
          monitor_alerts: last.monitor_alerts,
          buys_blocked: last.buys_blocked,
          note: last.note,
          error: last.error,
        };
        scheduleOut.textContent = JSON.stringify(slim, null, 2);
      }
    }
    const alerts =
      (last && (last.monitor_alerts || (last.ops_report && last.ops_report.monitor_alerts))) ||
      [];
    renderAlerts(alerts);
    if (last && last.summary) {
      lastPaperMetrics = {
        max_drawdown_pct: last.summary.max_drawdown_pct,
        equity: last.summary.equity,
      };
    }
    setMeta(data.empty ? "尚无上次调度" : `上次调度 · ${last.kind || "—"}`);
    return data;
  }

  async function runPaperDaily() {
    const stratEl = document.getElementById("schedule-paper-strategy");
    const buyEl = document.getElementById("schedule-paper-buy");
    setMeta("纸面日更运行中…");
    const res = await fetch("/api/schedule/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "paper_daily",
        strategy: stratEl ? stratEl.value : "short_conservative",
        simulate_buy: !!(buyEl && buyEl.checked),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    renderAlerts(data.monitor_alerts || []);
    if (data.summary) {
      lastPaperMetrics = {
        max_drawdown_pct: data.summary.max_drawdown_pct,
        equity: data.summary.equity,
      };
    }
    if (scheduleOut) {
      scheduleOut.textContent = JSON.stringify(
        {
          kind: data.kind,
          strategy_id: data.strategy_id,
          cost_model: data.cost_model,
          data_quality: data.data_quality,
          risk_blocks: data.risk_blocks,
          monitor_alerts: data.monitor_alerts,
          buys_blocked: data.buys_blocked,
          note: data.note,
        },
        null,
        2
      );
    }
    setMeta(
      `纸面日更完成 · 告警 ${(data.monitor_alerts || []).length} · 策略 ${data.strategy_id || "—"}`
    );
    await loadDecisions().catch(() => {});
    return data;
  }

  async function runFeedback(fromAlerts) {
    const dd = document.getElementById("feedback-dd");
    const win = document.getElementById("feedback-win");
    const backtest_metrics = {};
    if (dd && dd.value !== "") backtest_metrics.max_drawdown_pct = Number(dd.value);
    if (win && win.value !== "") backtest_metrics.win_rate_pct = Number(win.value);
    const body = {
      backtest_metrics,
      paper_metrics: lastPaperMetrics || undefined,
      monitor_alerts: fromAlerts ? lastMonitorAlerts : undefined,
    };
    const res = await fetch("/api/feedback/suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (feedbackOut) feedbackOut.textContent = JSON.stringify(data, null, 2);
    setMeta(
      fromAlerts
        ? "已从监控告警生成配置建议（未写盘 · 人审后到策略页 promote）"
        : "已生成配置反馈建议（未写盘）"
    );
    return data;
  }

  async function runPrefill(fmt) {
    const res = await fetch(`/api/orders/prefill?limit=20&fmt=${encodeURIComponent(fmt)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (prefillOut) {
      prefillOut.textContent =
        fmt === "csv" ? data.csv || JSON.stringify(data, null, 2) : JSON.stringify(data, null, 2);
    }
    setMeta("已生成非交易预填（须券商 App 人工确认）");
    return data;
  }

  async function loadAuditTimeline() {
    const list = document.getElementById("audit-timeline-list");
    if (!list) return null;
    const { ok, data, error } = await apiFetch("/api/audit/timeline?limit=40");
    if (!ok) {
      list.innerHTML = `<li class="platform-item"><span class="sub">${error || "加载失败"}</span></li>`;
      return data;
    }
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = '<li class="platform-item"><span class="sub">暂无审计事件</span></li>';
      return data;
    }
    list.innerHTML = items
      .map((ev) => {
        const kind = ev.kind || "event";
        const title = ev.title || "—";
        const sub = [ev.ts, kind, ev.detail].filter(Boolean).join(" · ");
        const href = ev.href || "/platform";
        return (
          `<li class="platform-item">` +
          `<div class="name"><a href="${href}">${title}</a></div>` +
          `<div class="sub">${sub}</div></li>`
        );
      })
      .join("");
    return data;
  }

  function fmtKpi(v, digits) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(digits != null ? digits : 2) : "—";
  }

  function escapeAttr(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function setNorthStarActionStatus(text) {
    const el = document.getElementById("north-star-action-status");
    if (el) el.textContent = text || "";
    setMeta(text || "");
  }

  /** 拟合不可用时的人话说明 + 下一步（平台页横幅） */
  function renderFitGuide(rz) {
    const banner = document.getElementById("north-star-fit-banner");
    if (!banner) return;
    if (!rz || rz.status !== "unavailable") {
      banner.hidden = true;
      banner.innerHTML = "";
      return;
    }
    const reason = String(rz.reason || "unavailable");
    const paperDays = rz.paper_days != null ? Number(rz.paper_days) : null;
    const btDays = rz.backtest_days != null ? Number(rz.backtest_days) : null;
    const need = rz.need_days != null ? Number(rz.need_days) : 6;
    let title = "拟合仍空";
    let why = rz.note || reason;
    let steps = [];

    if (reason === "curves_too_short") {
      title = "拟合仍空：纸面净值天数不够";
      why =
        `当前纸面按日净值 ${paperDays ?? "—"} 日、回测曲线 ${btDays ?? "—"} 日；` +
        `至少各需 ${need} 日才能算 Corr/TE。`;
      steps = [
        "本页下方「纸面日更 · P2」点「运行纸面日更」（每个交易日一次）",
        "连续积累到 ≥6 个净值日（越多越好）",
        "再到 <a href=\"/replay\">历史回测</a> 跑一次 Top-K（落盘回测曲线）",
        "回来点「刷新北极星」看拟合是否出现数字",
      ];
    } else if (reason === "no_date_overlap") {
      title = "拟合仍空：纸面与回测日期对不齐";
      why =
        `同日交集仅 ${rz.aligned_days ?? 0} 日（需≥${need}）。` +
        (rz.paper_span ? ` 纸面 ${rz.paper_span}；` : "") +
        (rz.backtest_span ? `回测 ${rz.backtest_span}。` : "");
      steps = [
        "拉长历史回测 lookback，使曲线覆盖纸面日期",
        "继续每日纸面日更",
        "再点「刷新北极星」",
      ];
    } else {
      title = `拟合仍空（${escapeAttr(reason)}）`;
      steps = [
        "查看样本覆盖里的纸面快照天数",
        "运行纸面日更并跑历史回测 Top-K",
        "可点「落差归因」看启发式原因",
      ];
    }

    banner.hidden = false;
    banner.innerHTML =
      `<p class="platform-fit-banner-title">${escapeAttr(title)}</p>` +
      `<p class="platform-fit-banner-why">${escapeAttr(why)}</p>` +
      `<p class="platform-hint">下一步</p>` +
      `<ol class="platform-fit-banner-steps">` +
      steps.map((s) => `<li>${s}</li>`).join("") +
      `</ol>` +
      `<p class="platform-hint">反复点「刷新北极星」不会增加天数；同一自然日多次日更通常仍是 1 个点。</p>`;
  }

  function realizationStatusTip(rz) {
    if (!rz || rz.status !== "unavailable") {
      if (rz && rz.corr != null) return `Corr=${rz.corr}`;
      return "已刷新";
    }
    const reason = rz.reason || "unavailable";
    if (reason === "curves_too_short") {
      const p = rz.paper_days != null ? rz.paper_days : "?";
      const n = rz.need_days != null ? rz.need_days : 6;
      return `拟合仍空：纸面仅 ${p} 日（需≥${n}）· 请跑纸面日更`;
    }
    if (reason === "no_date_overlap") {
      return `拟合仍空：日期无交集 · 对齐回测 lookback 与日更`;
    }
    return `拟合仍空（${reason}）`;
  }

  async function withButtonBusy(btnId, labelBusy, fn) {
    const btn = document.getElementById(btnId);
    const prev = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = labelBusy;
    }
    try {
      return await fn();
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev;
      }
    }
  }

  function renderNorthStar(ns) {
    const host = document.getElementById("north-star-kpi");
    const noteEl = document.getElementById("north-star-note");
    if (!host) return;
    const pr = (ns && ns.paper_risk) || {};
    const prs = (ns && ns.paper_risk_strategy) || {};
    const rz = (ns && ns.realization) || {};
    const ttm = (ns && ns.ttm) || {};
    const rb = (ns && ns.risk_blocks) || {};
    const items = [
      ["滚动夏普", fmtKpi(pr.rolling_sharpe, 2), pr.status === "unavailable" ? "样本不足" : "全账户 snapshots"],
      [
        "策略夏普",
        fmtKpi(prs.rolling_sharpe, 2),
        prs.status === "unavailable"
          ? prs.reason || "无策略仓序列"
          : "origin=strategy 归因净值",
      ],
      ["卡玛", fmtKpi(pr.calmar, 2), pr.status === "unavailable" ? "样本不足" : "CAGR/最大回撤"],
      ["拟合 Corr", fmtKpi(rz.corr, 3), rz.status === "unavailable" ? rz.reason || "未对齐" : `对齐 ${rz.aligned_days || 0} 日`],
      ["跟踪误差", rz.tracking_error_pct != null ? `${fmtKpi(rz.tracking_error_pct, 2)}%` : "—",
        rz.status === "unavailable" ? (rz.reason || "未对齐") : "年化 TE"],
      [
        "TTM(中位h)",
        fmtKpi(ttm.median_idea_to_paper_hours, 1),
        ttm.status === "unavailable" ? "未度量" : "Idea→纸面规则",
      ],
      ["风控拦截", rb.block_count != null ? String(rb.block_count) : "—",
        rb.effectiveness_rate != null
          ? `有效率 ${(Number(rb.effectiveness_rate) * 100).toFixed(0)}%`
          : "流水按码汇总；标注 outcome 后算有效率"],
    ];
    host.innerHTML = items
      .map(
        ([label, val, title]) =>
          `<div class="paper-stat follow-stat" title="${escapeAttr(title)}">` +
          `<span class="label">${label}</span>` +
          `<span class="val">${val}</span></div>`
      )
      .join("");
    host.classList.remove("north-star-flash");
    // force reflow for flash animation
    void host.offsetWidth;
    host.classList.add("north-star-flash");
    if (noteEl) {
      const rzNote =
        rz.status === "unavailable"
          ? `拟合不可用：${rz.reason || "unknown"}` +
            (rz.paper_days != null || rz.backtest_days != null
              ? ` · 纸面${rz.paper_days ?? "—"}日 / 回测${rz.backtest_days ?? "—"}日`
              : "") +
            (rz.aligned_days != null ? ` · 对齐日=${rz.aligned_days}` : "") +
            (rz.paper_span || rz.backtest_span
              ? ` · span 纸面 ${rz.paper_span || "—"} / 回测 ${rz.backtest_span || "—"}`
              : "")
          : rz.status === "ok"
            ? `拟合 OK · 对齐 ${rz.aligned_days || 0} 日（${rz.first_date || "?"}→${rz.last_date || "?"}）`
            : "";
      const stratNote =
        prs.status === "ok"
          ? `策略夏普 ${fmtKpi(prs.rolling_sharpe, 2)}（n=${prs.sample_count ?? "—"}）`
          : prs.reason
            ? `策略 scope：${prs.reason}`
            : "";
      noteEl.textContent =
        [rzNote, stratNote, (ns && ns.note) || ""]
          .filter(Boolean)
          .join(" · ") ||
        "缺样本显示 —；回测成功落盘曲线后拟合可算；promote / 反馈建议会打 TTM 点。";
    }
    renderFitGuide(rz);
  }

  async function loadSampleStatus() {
    const body = document.getElementById("sample-status-body");
    const fold = document.getElementById("sample-status-fold");
    if (!body) return null;
    if (fold) fold.open = true;
    setNorthStarActionStatus("正在刷新样本覆盖…");
    const { ok, data, error } = await apiFetch("/api/ops/sample-status");
    if (!ok) {
      body.textContent = error || "样本覆盖加载失败";
      setNorthStarActionStatus(error || "样本覆盖加载失败");
      return data;
    }
    const ttm = data.ttm || {};
    const fund = data.fundamentals_history || {};
    const snap = data.paper_snapshots || {};
    const rb = data.risk_blocks || {};
    const ou = data.outcome_unlabeled || {};
    const disc = data.discipline || {};
    const hints = (data.hints || []).map((h) => `<li>${escapeAttr(h)}</li>`).join("");
    const warnings = (disc.warnings || []).map((w) => `<li class="is-warn">${escapeAttr(w)}</li>`).join("");
    const ouItems = (ou.items || [])
      .slice(0, 5)
      .map((it) => {
        const codes = (it.codes || []).join(",") || "—";
        return `<li>待标注 #${it.index ?? "?"} · ${escapeAttr(codes)} · ${escapeAttr(
          String(it.reason || it.detail || "").slice(0, 60)
        )}</li>`;
      })
      .join("");
    body.innerHTML =
      `<p class="platform-hint"><strong>演示样本 ≠ 验证结论</strong></p>` +
      `<ul class="platform-list compact">` +
      `<li>TTM：${escapeAttr(ttm.status || "—")} · 事件 ${ttm.sample_count ?? "—"}（真实 ${
        ttm.real_events ?? "—"
      } / seeded ${ttm.seeded_events ?? "—"}）· 中位 ${
        ttm.median_idea_to_paper_hours != null ? ttm.median_idea_to_paper_hours + "h" : "—"
      }</li>` +
      `<li>财务 history：有点 ${fund.with_history ?? 0}/${fund.total ?? 0} · 真实多点 ${
        fund.real_multi_point ?? 0
      } · demo多点 ${fund.synthetic_multi_point ?? 0} · 空 ${fund.empty ?? 0}` +
      (fund.real_multi_coverage != null
        ? ` · 真实多点覆盖 ${(Number(fund.real_multi_coverage) * 100).toFixed(0)}%`
        : "") +
      (fund.universe_source
        ? ` · 宇宙 ${escapeAttr(String(fund.universe_source))}`
        : "") +
      `</li>` +
      (fund.empty_codes && fund.empty_codes.length
        ? `<li>空码：${escapeAttr(fund.empty_codes.slice(0, 12).join(", "))}${
            fund.empty_codes.length > 12 ? "…" : ""
          }</li>`
        : "") +
      (fund.ann_missing_code_ratio != null
        ? `<li class="${
            Number(fund.ann_missing_code_ratio) > 0.3 ? "is-warn" : ""
          }">ann_missing：码占比 ${(Number(fund.ann_missing_code_ratio) * 100).toFixed(
            0
          )}% · ${fund.ann_missing_codes ?? 0} 只 · points ${
            fund.ann_missing_points ?? "—"
          }</li>`
        : "") +
      (fund.ann_missing_top && fund.ann_missing_top.length
        ? `<li>缺 ann Top：${escapeAttr(
            fund.ann_missing_top
              .slice(0, 8)
              .map((r) => `${r.code}(${r.ann_missing_points})`)
              .join(", ")
          )}${fund.ann_missing_top.length > 8 ? "…" : ""}</li>`
        : "") +
      (fund.ingest_hint
        ? `<li class="is-warn">催办：${escapeAttr(String(fund.ingest_hint))}</li>`
        : "") +
      (Number(fund.store_orphan_count || 0) > 0
        ? `<li class="is-warn">store 孤儿 ${fund.store_orphan_count}（不计入覆盖）：${escapeAttr(
            (fund.store_orphan_codes || []).slice(0, 8).join(", ")
          )}${
            (fund.store_orphan_codes || []).length > 8 ? "…" : ""
          }</li>`
        : "") +
      `<li>纸面快照：${snap.count ?? 0}（真实 ${snap.real_count ?? "—"} / densified ${
        snap.densified_count ?? 0
      }）${snap.enough_for_sharpe ? " · 够算夏普" : " · 偏少"}</li>` +
      `<li>拦截：${rb.block_count ?? 0} · 已标注 ${rb.labeled_count ?? 0}` +
      (rb.effectiveness_rate != null
        ? ` · 有效率 ${(Number(rb.effectiveness_rate) * 100).toFixed(0)}%`
        : rb.note
          ? ` · ${escapeAttr(rb.note)}`
          : "") +
      `</li>` +
      (Number(ou.unlabeled_count || 0) > 0
        ? `<li class="is-warn">待标注 outcome ${ou.unlabeled_count} 条 → 策略中心「拦截流水」点真拦/误拦</li>`
        : "") +
      `</ul>` +
      (ouItems
        ? `<p class="platform-hint">待标注摘要</p><ul class="platform-list compact">${ouItems}</ul>`
        : "") +
      (warnings ? `<p class="platform-hint">纪律</p><ul class="platform-list compact">${warnings}</ul>` : "") +
      (hints ? `<p class="platform-hint">建议</p><ul class="platform-list compact">${hints}</ul>` : "");
    const cov =
      fund.real_multi_coverage != null
        ? `${(Number(fund.real_multi_coverage) * 100).toFixed(0)}%`
        : "—";
    setNorthStarActionStatus(
      `样本覆盖已刷新 · 真实多点覆盖 ${cov} · 快照 ${snap.count ?? 0}` +
        (Number(ou.unlabeled_count || 0) > 0 ? ` · 待标注 ${ou.unlabeled_count}` : "")
    );
    try {
      body.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (_) {
      /* ignore */
    }
    return data;
  }

  async function loadNorthStar() {
    setNorthStarActionStatus("正在刷新北极星…");
    const { ok, data, error } = await apiFetch("/api/north-star?refresh=1");
    if (!ok) {
      renderNorthStar(null);
      setNorthStarActionStatus(error || "北极星加载失败");
      return data;
    }
    renderNorthStar(data.north_star || null);
    const rz = (data.north_star && data.north_star.realization) || {};
    setNorthStarActionStatus(
      (data.cached ? "北极星（缓存）" : "北极星已刷新") + " · " + realizationStatusTip(rz)
    );
    return data;
  }

  async function loadClusterLiveHint() {
    try {
      const res = await fetch("/api/quant/cluster-live/status");
      const data = await res.json();
      if (!res.ok || !data || !data.success) return;
      const mode = (data.cluster_scoring || {}).mode || "off";
      if (mode === "off") return;
      const v = (data.active || {}).version;
      const cov = (data.health || {}).coverage;
      const tip =
        `分组 live · ${mode}` +
        (v != null ? ` · v${v}` : "") +
        (cov != null ? ` · 覆盖 ${Math.round(Number(cov) * 100)}%` : "");
      if (meta && meta.textContent) {
        meta.textContent = `${meta.textContent} · ${tip}`;
      } else {
        setMeta(tip);
      }
    } catch (_) {
      /* ignore */
    }
  }

  async function openPlatformPanel() {
    await Promise.all([
      loadMemory().catch((e) => setMeta(String(e.message || e))),
      loadDecisions().catch(() => {}),
      loadScheduleLast().catch(() => {}),
      loadAuditTimeline().catch(() => {}),
      loadNorthStar().catch(() => {}),
      loadSampleStatus().catch(() => {}),
    ]);
    await loadClusterLiveHint().catch(() => {});
  }

  const on = (id, type, fn) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(type, (e) => {
      e.preventDefault();
      fn().catch((err) => setMeta(String(err.message || err)));
    });
  };

  on("memory-save", "click", saveMemory);
  on("memory-refresh", "click", loadMemory);
  on("decision-refresh", "click", loadDecisions);
  on("audit-timeline-refresh", "click", loadAuditTimeline);
  on("north-star-refresh", "click", () =>
    withButtonBusy("north-star-refresh", "刷新中…", loadNorthStar)
  );
  on("sample-status-refresh", "click", () =>
    withButtonBusy("sample-status-refresh", "刷新中…", loadSampleStatus)
  );

  async function loadDataQuality() {
    const body = document.getElementById("data-quality-body");
    const fold = document.getElementById("data-quality-fold");
    if (!body) return null;
    if (fold) fold.open = true;
    setNorthStarActionStatus("正在加载数据质量…");
    const { ok, data, error } = await apiFetch("/api/ops/data-quality");
    if (!ok) {
      body.textContent = error || "数据质量加载失败";
      setNorthStarActionStatus(error || "数据质量加载失败");
      return data;
    }
    const cov = data.bars_coverage || {};
    const fund = data.fundamentals_history || {};
    const audit = data.source_audit || {};
    const cal = data.calendar || {};
    const warns = (data.warnings || []).map((w) => `<li class="is-warn">${escapeAttr(w)}</li>`).join("");
    body.innerHTML =
      `<p class="platform-hint"><strong>状态</strong> ${escapeAttr(data.status || "—")} · ${escapeAttr(
        data.track || ""
      )}</p>` +
      `<ul class="platform-list compact">` +
      `<li>日线覆盖：mapped ${cov.mapped ?? cov.total ?? "—"} · good/thin/empty 见 coverage</li>` +
      `<li>财务多期：真实多点 ${fund.real_multi_point ?? "—"} · 覆盖 ${
        fund.real_multi_coverage != null
          ? (Number(fund.real_multi_coverage) * 100).toFixed(0) + "%"
          : "—"
      } · demo ${fund.synthetic_multi_point ?? "—"}</li>` +
      (fund.ann_missing_code_ratio != null
        ? `<li class="${
            Number(fund.ann_missing_code_ratio) > 0.3 ? "is-warn" : ""
          }">ann_missing 占比 ${(Number(fund.ann_missing_code_ratio) * 100).toFixed(0)}%` +
          (fund.ann_missing_top && fund.ann_missing_top.length
            ? ` · Top ${escapeAttr(
                fund.ann_missing_top
                  .slice(0, 6)
                  .map((r) => r.code)
                  .join(", ")
              )}`
            : "") +
          `</li>`
        : "") +
      `<li>源审计：${escapeAttr(audit.status || "—")} · fallback ${
        (audit.fallback_codes || []).length
      } · thin ${(audit.thin_codes || []).length}</li>` +
      `<li>日历：节假日 ${cal.holiday_count ?? 0} · ${escapeAttr(cal.note || "")}</li>` +
      `</ul>` +
      (warns ? `<ul class="platform-list compact">${warns}</ul>` : "") +
      `<p class="platform-hint">${escapeAttr(data.note || "")}</p>`;
    setNorthStarActionStatus(`数据质量 · ${data.status || "ok"}`);
    return data;
  }

  on("data-quality-refresh", "click", () =>
    withButtonBusy("data-quality-refresh", "加载中…", loadDataQuality)
  );

  on("maturity-gate-refresh", "click", async () => {
    const el = document.getElementById("maturity-gate-body");
    if (!el) return;
    el.hidden = false;
    setNorthStarActionStatus("正在加载成熟闸门…");
    await withButtonBusy("maturity-gate-refresh", "加载中…", async () => {
      const { ok, data, error } = await apiFetch("/api/ops/maturity-gate");
      if (!ok) {
        el.textContent = error || "闸门加载失败";
        setNorthStarActionStatus(error || "闸门加载失败");
        return;
      }
      const sectionLabel = {
        data: "数据",
        fit: "拟合",
        ops: "运营",
        risk: "风控",
        eng: "工程",
      };
      const rows = (data.items || [])
        .map((i) => {
          const mark = i.ok ? "✓" : i.severity === "soft" ? "！" : "✗";
          const cls = i.ok ? "" : i.severity === "soft" ? "platform-gate-soft" : "down";
          const sec = sectionLabel[i.section] || i.section;
          const act = !i.ok && i.action ? ` → ${i.action}` : "";
          return (
            `<li class="${cls}">${mark} <strong>[${sec}]</strong> ${escapeAttr(i.id)}：` +
            `${escapeAttr(i.detail || "")}${escapeAttr(act)}</li>`
          );
        })
        .join("");
      const next = (data.next_actions || [])
        .slice(0, 4)
        .map((a) => `<li>${escapeAttr(a)}</li>`)
        .join("");
      el.innerHTML =
        `<p><strong>硬项 ${data.hard_passed ?? "—"}/${data.hard_total ?? "—"}</strong>` +
        ` · 软项 ${data.soft_passed ?? "—"}/${data.soft_total ?? "—"}` +
        ` · ready_for_n6_review=<strong>${data.ready_for_n6_review}</strong></p>` +
        `<p class="platform-hint">${escapeAttr(data.note || "")}</p>` +
        `<ul class="platform-list compact">${rows}</ul>` +
        (next
          ? `<p class="platform-hint">下一步</p><ul class="platform-list compact">${next}</ul>`
          : "");
      setNorthStarActionStatus(
        `成熟闸门硬项 ${data.hard_passed}/${data.hard_total} · ready=${data.ready_for_n6_review}`
      );
    });
  });

  on("empty-fundamentals-refresh", "click", async () => {
    const el = document.getElementById("ops-aux-body");
    if (!el) return;
    el.hidden = false;
    setNorthStarActionStatus("加载空财务列表…");
    await withButtonBusy("empty-fundamentals-refresh", "加载中…", async () => {
      const { ok, data, error } = await apiFetch("/api/ops/empty-fundamentals");
      if (!ok) {
        el.textContent = error || "失败";
        return;
      }
      const codes = data.empty_codes || data.codes || [];
      el.textContent =
        `空财务 ${codes.length} 只\n` +
        (data.note || "") +
        "\n" +
        (codes.length ? codes.join("\n") : "（无）");
      setNorthStarActionStatus(`空财务 ${codes.length} 只`);
    });
  });

  on("fundamentals-warmup-run", "click", async () => {
    const el = document.getElementById("ops-aux-body");
    if (el) {
      el.hidden = false;
      el.textContent = "预热财务多期中（可能需数分钟）…";
    }
    setNorthStarActionStatus("预热财务多期…");
    await withButtonBusy("fundamentals-warmup-run", "预热中…", async () => {
      const { ok, data, error } = await apiFetch("/api/schedule/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "fundamentals_warmup",
          limit: 20,
          ingest_history: true,
          ingest_max_points: 8,
        }),
      });
      if (el) {
        el.textContent = ok
          ? JSON.stringify(data, null, 2)
          : error || data?.detail || "预热失败";
      }
      setNorthStarActionStatus(
        ok ? "财务预热已启动/完成 · 请再点「刷新样本覆盖」" : error || "预热失败"
      );
      if (ok) await loadSampleStatus();
    });
  });

  on("fit-gap-refresh", "click", async () => {
    const el = document.getElementById("ops-aux-body");
    if (!el) return;
    el.hidden = false;
    setNorthStarActionStatus("加载落差归因…");
    await withButtonBusy("fit-gap-refresh", "加载中…", async () => {
      const { ok, data, error } = await apiFetch("/api/ops/fit-gap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!ok) {
        el.textContent = error || "失败";
        return;
      }
      const lines = (data.hints || []).map(
        (h) => `[${h.level}] ${h.code}: ${h.message}`
      );
      el.textContent =
        `warn=${data.warn_count ?? 0} · ${data.note || ""}\n` + lines.join("\n");
      setNorthStarActionStatus(`落差归因 ${data.count || 0} 条 · warn ${data.warn_count || 0}`);
    });
  });
  on("feedback-run", "click", () => runFeedback(false));
  on("feedback-from-alerts", "click", () => runFeedback(true));
  on("schedule-paper-run", "click", runPaperDaily);
  on("schedule-last-refresh", "click", loadScheduleLast);
  on("prefill-json", "click", () => runPrefill("json"));
  on("prefill-csv", "click", () => runPrefill("csv"));

  ctx.openPlatformPanel = openPlatformPanel;
  ctx.reloadPlatform = openPlatformPanel;
}
