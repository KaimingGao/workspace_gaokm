import { mergeScoringFloors } from "./scoring.js";
import {
  loadAndPaintMacroStrip,
  runMarketContextIngest,
  paintMacroHistoryChart,
  macroHistoryLegendHtml,
} from "../macro_context_ui.js";

/** Quant domain: strategy */
export function installStrategy(q) {
  const { els, state, escapeHtml, apiFetch } = q;

  async function loadSignalConfigPanel() {
    try {
      await loadMarketContextPanel();
      return null;
    } catch (_) {
      return null;
    }
  }

  function regimeLabel(code) {
    const m = {
      bear: "熊市",
      weak: "弱势",
      neutral: "中性",
      strong: "强势",
      bull: "牛市",
    };
    return m[String(code || "").toLowerCase()] || code || "—";
  }

  function regimeShortLabel(code) {
    const m = { bear: "熊", weak: "弱", neutral: "中", strong: "强", bull: "牛" };
    return m[String(code || "").toLowerCase()] || "—";
  }

  function regimeBenchmarkLabel(code) {
    const raw = String(code || "").toLowerCase();
    if (!raw) return "沪深300";
    if (raw.includes("1000") || raw === "csi1000" || raw === "zz1000") return "中证1000";
    if (raw.includes("500") || raw === "csi500" || raw === "zz500") return "中证500";
    if (raw.includes("300") || raw === "hs300" || raw === "csi300" || raw.includes("000300"))
      return "沪深300";
    return String(code);
  }

  function regimePositionScale(code, apply) {
    if (apply === false) return 1;
    const c = String(code || "").toLowerCase();
    if (c === "bear" || c === "weak" || c === "weak_trend" || c === "chop") return 0.8;
    if (c === "neutral") return 0.9;
    if (c === "strong" || c === "bull") return 1;
    return 1;
  }

  function regimeFactorHint(code) {
    const c = String(code || "").toLowerCase();
    if (c === "bear" || c === "weak") return "防守权 · 关动量/量价";
    if (c === "bull" || c === "strong") return "趋势权 · 关估值/质量";
    return "全因子环境";
  }

  function regimeAxisPct(roc) {
    const lo = -10;
    const hi = 10;
    if (roc == null || !Number.isFinite(Number(roc))) return 50;
    const t = (Number(roc) - lo) / (hi - lo);
    return Math.max(3, Math.min(97, t * 100));
  }

  function renderRegimeBoard(reg) {
    const body = document.getElementById("strategy-regime-body");
    const pill = document.getElementById("strategy-mctx-regime-pill");
    if (!body) return;
    const r = reg && typeof reg === "object" ? reg : {};
    const code = String(r.regime || "").toLowerCase();
    const lab = regimeLabel(code);
    const days = Number(r.window_days);
    const windowDays = Number.isFinite(days) && days > 0 ? days : 20;
    const applyScale = r.apply_position_scale !== false;
    const scale = regimePositionScale(code, applyScale);
    const roc = r.index_return_pct;
    const rocTone = toneClass(roc);
    const bench = regimeBenchmarkLabel(r.benchmark);
    const ticks = [
      { k: "bear", lab: "熊" },
      { k: "weak", lab: "弱" },
      { k: "neutral", lab: "中" },
      { k: "strong", lab: "强" },
      { k: "bull", lab: "牛" },
    ];
    const overlay = r.macro_overlay_deferred
      ? "overlay → M"
      : r.macro_overlay_applied
        ? "overlay on"
        : "仓位闸";
    const overlayTip = r.macro_overlay_deferred
      ? "科技拖累已交给跨市场 prior，避免双重惩罚"
      : r.macro_overlay_applied
        ? "Regime 叠加了海外科技拖累"
        : "弱/熊压单票与行业上限；不进 ŷ";
    const reason = r.reason ? String(r.reason) : "";
    const vol =
      r.volatility_pct != null && Number.isFinite(Number(r.volatility_pct))
        ? Number(r.volatility_pct)
        : null;
    const penalty =
      r.score_penalty != null && Number(r.score_penalty) > 0
        ? Number(r.score_penalty)
        : null;
    const known = !!code;
    const envHint = regimeFactorHint(code);
    const envMetric = envHint.split(" · ")[0] || "—";
    const envSub = [
      !applyScale ? "仓位缩放已关" : null,
      penalty != null && penalty >= 0.05 ? `启发式罚 ${penalty.toFixed(1)}` : null,
      vol != null ? `近5日波动 ${vol.toFixed(2)}%` : null,
      r.blend_blended ? "边界软插值" : null,
    ]
      .filter(Boolean)
      .join(" · ");
    if (pill) {
      pill.textContent = known
        ? `Regime · ${regimeShortLabel(code)} · ${bench} ${windowDays}D`
        : "Regime —";
    }
    const axisPct = regimeAxisPct(roc);
    const rocText = fmtPct(roc);
    const needleTitle = roc == null ? "无基准收益" : `${windowDays}D ROC ${rocText}`;
    const needleSide = axisPct < 18 ? "is-start" : axisPct > 82 ? "is-end" : "";
    body.innerHTML =
      `<div class="strategy-regime-axis" data-regime="${escapeHtml(code || "na")}" role="img" aria-label="${escapeHtml(
        needleTitle
      )}">` +
      `<div class="strategy-regime-axis-track">` +
      ticks
        .map(
          (t) =>
            `<span class="strategy-regime-seg ${escapeHtml(t.k)}${
              t.k === code ? " is-current" : ""
            }"></span>`
        )
        .join("") +
      `<span class="strategy-regime-needle ${needleSide}" style="left:${axisPct.toFixed(
        2
      )}%" title="${escapeHtml(needleTitle)}">` +
      `<span class="strategy-regime-needle-val ${rocTone}">${escapeHtml(rocText)}</span>` +
      `</span>` +
      `</div>` +
      `<ol class="strategy-regime-spectrum" aria-label="Regime 五档">` +
      ticks
        .map((t) => {
          const on = t.k === code;
          return (
            `<li class="${escapeHtml(t.k)}${on ? " is-current" : ""}"` +
            (on ? ` aria-current="true"` : "") +
            `>${escapeHtml(t.lab)}</li>`
          );
        })
        .join("") +
      `</ol>` +
      `<div class="strategy-regime-scale" aria-hidden="true">` +
      `<span>−10%</span><span>0</span><span>+10%</span>` +
      `</div>` +
      `</div>` +
      `<p class="strategy-regime-reason${reason ? "" : " is-muted"}" title="${escapeHtml(
        reason || `现价相对 ${windowDays} 日前收盘，不是均线多空`
      )}">${escapeHtml(reason || `现价相对 ${windowDays} 日前收盘，不是均线多空`)}</p>` +
      `<div class="strategy-mctx-strip strategy-regime-metrics">` +
      `<div class="strategy-mctx-cell ${known ? `is-${code}` : ""}">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">状态</span>` +
      `<span class="strategy-mctx-badge ${
        code === "bear" || code === "weak" ? "is-fire" : known ? "is-armed" : "is-idle"
      }">${escapeHtml(known ? lab : "未评估")}</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">${escapeHtml(known ? lab : "—")}</span>` +
      `<span class="strategy-mctx-sub" title="${escapeHtml(overlayTip)}">${escapeHtml(
        [code || "—", overlay].filter(Boolean).join(" · ")
      )}</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">${windowDays}D ROC</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric ${rocTone}">${escapeHtml(rocText)}</span>` +
      `<span class="strategy-mctx-sub">相对${windowDays}日前收盘 · ${escapeHtml(bench)}</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">仓位上限</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">×${scale.toFixed(2)}</span>` +
      `<span class="strategy-mctx-sub">${applyScale ? "单票/行业同乘" : "缩放关闭"} · 不进 ŷ</span>` +
      `</div>` +
      `<div class="strategy-mctx-cell">` +
      `<div class="strategy-mctx-cell-top">` +
      `<span class="strategy-mctx-label">因子环境</span>` +
      `</div>` +
      `<span class="strategy-mctx-metric">${escapeHtml(envMetric)}</span>` +
      `<span class="strategy-mctx-sub" title="${escapeHtml(envHint)}">${escapeHtml(
        envSub || "查表，非模型"
      )}</span>` +
      `</div>` +
      `</div>`;
  }

  function fmtPct(v) {
    if (v == null || !Number.isFinite(Number(v))) return "—";
    const n = Number(v);
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }

  function toneClass(v) {
    if (v == null || !Number.isFinite(Number(v))) return "";
    const n = Number(v);
    if (n > 0.05) return "is-up";
    if (n < -0.05) return "is-down";
    return "is-flat";
  }

  function ageLabel(parts) {
    const ages = ["macro", "market_sentiment", "announcement"]
      .map((k) => (parts && parts[k] && parts[k].age_hours != null ? Number(parts[k].age_hours) : null))
      .filter((x) => x != null && Number.isFinite(x));
    if (!ages.length) return null;
    const h = Math.min(...ages);
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h < 24) return `${h.toFixed(1)}h`;
    return `${(h / 24).toFixed(1)}d`;
  }

  function setStrategyMetaStatus(text, { busy = false } = {}) {
    const meta = document.getElementById("strategy-meta");
    if (!meta) return;
    if (text != null && text !== "") meta.textContent = text;
    meta.classList.toggle("is-busy", !!busy);
    if (busy) meta.setAttribute("aria-busy", "true");
    else meta.removeAttribute("aria-busy");
  }

  async function loadMarketContextPanel() {
    const macroHost = document.getElementById("quant-macro-context-strip");
    if (macroHost) {
      loadAndPaintMacroStrip(macroHost, apiFetch, escapeHtml).catch(() => {});
    }
    try {
      const ctxRes = await apiFetch("/api/dashboard/market-context");
      const ctx = ctxRes.ok !== false && ctxRes.data ? ctxRes.data : null;
      if (!ctx || ctx.ok === false) {
        renderRegimeBoard({});
        paintPriorDesk(null);
        setStrategyMetaStatus("盘前上下文未就绪");
        return;
      }
      const fresh = ctx.freshness || {};
      const age = ageLabel(fresh.parts);
      const bits = [];
      if (fresh.needs_ingest || ctx.macro_degraded) {
        bits.push(ctx.macro_degraded ? "macro 降级" : "需刷新");
      } else if (ctx.data_ready === false) {
        bits.push("部分空");
      } else {
        bits.push(age ? `新鲜 ${age}` : "新鲜");
      }
      bits.push("只读 · 不改 ŷ");
      setStrategyMetaStatus(bits.join(" · "));
      renderRegimeBoard(ctx.regime || {});
      paintPriorDesk(ctx);
    } catch (err) {
      renderRegimeBoard({});
      paintPriorDesk(null);
      setStrategyMetaStatus(String(err.message || err));
    }
  }

  function ageText(h) {
    if (h == null || !Number.isFinite(Number(h))) return "—";
    const n = Number(h);
    if (n < 1) return `${Math.round(n * 60)}m`;
    if (n < 24) return `${n.toFixed(1)}h`;
    return `${(n / 24).toFixed(1)}d`;
  }

  function paintPriorDesk(ctx) {
    const desk = (ctx && ctx.desk) || {};
    const boundary = document.getElementById("strategy-desk-boundary");
    if (boundary) {
      const b = desk.boundary || {};
      boundary.textContent =
        b.note || "非 PIT · 不进 ŷ · 未接调仓；仅盘前人看。";
    }

    const statusLabel = (s) =>
      ({ fresh: "新鲜", warn: "将过期", stale: "过期", missing: "缺失" }[s] || "—");
    const kpiTone = (s) => {
      if (s === "fresh") return "is-quality-good";
      if (s === "warn") return "is-bad";
      if (s === "stale" || s === "missing") return "is-quality-bad";
      return "";
    };
    const paintKpiRow = (bodyId, cells) => {
      const body = document.getElementById(bodyId);
      if (!body) return;
      const list = Array.isArray(cells) ? cells : [];
      if (!list.length) {
        body.innerHTML = `<p class="strategy-desk-empty">—</p>`;
        return;
      }
      body.innerHTML = list
        .map((c) => {
          return (
            `<div class="dashboard-kpi-card quant-pro-kpi-card strategy-kpi-card ${escapeHtml(
              c.tone || ""
            )}">` +
            `<div class="dashboard-kpi-label quant-pro-kpi-label">${escapeHtml(
              c.label || "—"
            )}</div>` +
            `<div class="dashboard-kpi-value quant-pro-kpi-value">${escapeHtml(
              String(c.value ?? "—")
            )}</div>` +
            `<div class="dashboard-kpi-sub quant-pro-kpi-sub" title="${escapeHtml(
              c.title || c.sub || ""
            )}">${escapeHtml(c.sub || "")}</div>` +
            `</div>`
          );
        })
        .join("");
    };

    const trustRows = Array.isArray(desk.trust) ? desk.trust : [];
    if (!trustRows.length) {
      paintKpiRow("strategy-trust-body", []);
      const body = document.getElementById("strategy-trust-body");
      if (body) {
        body.innerHTML = `<p class="strategy-desk-empty">${
          ctx ? "无可信度快照 · 点刷新 ingest" : "盘前上下文未就绪"
        }</p>`;
      }
    } else {
      paintKpiRow(
        "strategy-trust-body",
        trustRows.map((r) => {
          const st = r.status || (r.stale ? "stale" : r.warn ? "warn" : "fresh");
          return {
            label: r.label || r.key || "—",
            value: statusLabel(st),
            tone: kpiTone(st),
            sub: `as-of ${r.as_of || "—"} · ${ageText(r.age_hours)}${
              r.detail ? ` · ${r.detail}` : ""
            }`,
            title: r.source || r.source_short || "",
          };
        })
      );
    }

    const seriesOrder = [
      "sox",
      "ndx",
      "qqq",
      "kweb",
      "nq_fut",
      "es_fut",
      "a50",
      "wti",
      "copper",
      "qvix",
      "cnh",
      "us10y",
    ];
    const seriesLabels = {
      sox: "费城半导体",
      ndx: "纳斯达克",
      qqq: "QQQ",
      kweb: "中概互联",
      nq_fut: "小型纳指",
      es_fut: "小型标普",
      a50: "富时A50",
      wti: "WTI原油",
      copper: "COMEX铜",
      qvix: "50ETF波动",
      cnh: "离岸人民币",
      us10y: "美债10Y",
    };
    const seriesGroups = {
      sox: "美股科技",
      ndx: "美股科技",
      qqq: "美股科技",
      kweb: "中概",
      nq_fut: "外盘期货",
      es_fut: "外盘期货",
      a50: "A 股期货",
      wti: "大宗",
      copper: "大宗",
      qvix: "波动",
      cnh: "汇率",
      us10y: "利率",
    };
    const seriesFromMacro = () => {
      const series = (ctx && ctx.macro && ctx.macro.series) || {};
      if (!series || typeof series !== "object") return [];
      return seriesOrder
        .map((key) => {
          const pack = series[key];
          if (!pack || typeof pack !== "object") return null;
          return {
            key,
            label: seriesLabels[key] || key.toUpperCase(),
            group: seriesGroups[key] || "其他",
            change_1d_pct: pack.change_1d_pct,
            change_5d_pct: pack.change_5d_pct,
            close: pack.close,
            source: pack.source,
            source_short: pack.source,
          };
        })
        .filter(Boolean);
    };
    const mergeSeriesRows = (deskRows, macroRows) => {
      const byKey = new Map();
      (macroRows || []).forEach((r) => {
        if (r && r.key) byKey.set(r.key, r);
      });
      (deskRows || []).forEach((r) => {
        if (!r || !r.key) return;
        const prev = byKey.get(r.key) || {};
        byKey.set(r.key, {
          ...prev,
          ...r,
          label: r.label || prev.label,
          group: r.group || prev.group || seriesGroups[r.key] || "其他",
        });
      });
      const ordered = seriesOrder.map((k) => byKey.get(k)).filter(Boolean);
      if (ordered.length) return ordered;
      return deskRows && deskRows.length ? deskRows : macroRows || [];
    };
    const shortSrc = (r) => {
      const t = String((r && (r.source_short || r.source)) || "").trim();
      if (!t) return "—";
      return t.length > 22 ? `${t.slice(0, 20)}…` : t;
    };
    const fmtClose = (v, key) => {
      if (v == null || !Number.isFinite(Number(v))) return "—";
      const n = Number(v);
      if (key === "us10y") return n.toFixed(3);
      if (n >= 1000) return n.toFixed(0);
      if (n >= 100) return n.toFixed(1);
      return n.toFixed(2);
    };

    const seriesHost = document.getElementById("strategy-series-table");
    const seriesMeta = document.getElementById("strategy-series-meta");
    if (seriesHost) {
      // desk.series_rows 可能是旧进程的短列表；与 macro.series 合并补齐 NQ/ES/油铜/QVIX
      const rows = mergeSeriesRows(
        Array.isArray(desk.series_rows) ? desk.series_rows : [],
        seriesFromMacro()
      );
      if (seriesMeta) {
        seriesMeta.textContent = rows.length ? `${rows.length} 标的 · 1D/5D` : "";
      }
      if (!rows.length) {
        seriesHost.innerHTML = `<p class="strategy-desk-empty">无分项 · 刷新 ingest</p>`;
      } else {
        seriesHost.innerHTML =
          `<table class="strategy-desk-table strategy-desk-table--series"><thead><tr>` +
          `<th>标的</th><th>组别</th><th>1D</th><th>5D</th><th>点位</th><th>源</th>` +
          `</tr></thead><tbody>` +
          rows
            .map((r) => {
              const d1 = r.change_1d_pct;
              const d5 = r.change_5d_pct;
              return (
                `<tr>` +
                `<td class="is-name">${escapeHtml(r.label || r.key || "—")}</td>` +
                `<td class="is-muted">${escapeHtml(r.group || "—")}</td>` +
                `<td class="${toneClass(d1)}">${escapeHtml(fmtPct(d1))}</td>` +
                `<td class="${toneClass(d5)}">${escapeHtml(fmtPct(d5))}</td>` +
                `<td>${escapeHtml(fmtClose(r.close, r.key))}</td>` +
                `<td class="is-src" title="${escapeHtml(r.source || "")}">${escapeHtml(
                  shortSrc(r)
                )}</td>` +
                `</tr>`
              );
            })
            .join("") +
          `</tbody></table>`;
      }
    }

    const llHost = document.getElementById("strategy-leadlag-card");
    if (llHost) {
      const macro = (ctx && ctx.macro) || {};
      const ll = {
        beta: 0.55,
        ...(desk.lead_lag || {}),
      };
      if (ll.expected_gap_pct == null) ll.expected_gap_pct = macro.lead_lag_expected_gap_pct;
      if (ll.a50_1d_pct == null) ll.a50_1d_pct = macro.a50_1d_pct;
      if (ll.overseas_tech_1d_pct == null) {
        ll.overseas_tech_1d_pct = macro.overseas_tech_1d_pct;
      }
      if (ll.beta == null && macro.lead_lag_beta != null) ll.beta = macro.lead_lag_beta;
      const thin = ll.sample_thin || (ll.n != null && Number(ll.n) < 8);
      const hit =
        ll.hit_rate_1pct != null
          ? `${(Number(ll.hit_rate_1pct) * 100).toFixed(0)}%`
          : "—";
      llHost.innerHTML =
        `<p class="strategy-leadlag-reading${thin ? " is-warn" : ""}">${escapeHtml(
          ll.reading || (thin ? "样本偏薄 · 仅供参考" : "—")
        )}</p>` +
        `<div class="strategy-leadlag-grid">` +
        `<div><span class="strategy-desk-fact-k">海外科技</span><strong class="${toneClass(
          ll.overseas_tech_1d_pct
        )}">${escapeHtml(fmtPct(ll.overseas_tech_1d_pct))}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">映射预期</span><strong class="${toneClass(
          ll.expected_today_pct
        )}">${escapeHtml(fmtPct(ll.expected_today_pct))}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">A50 实现</span><strong class="${toneClass(
          ll.a50_1d_pct
        )}">${escapeHtml(fmtPct(ll.a50_1d_pct))}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">今日缺口</span><strong class="${toneClass(
          ll.expected_gap_pct
        )}">${escapeHtml(fmtPct(ll.expected_gap_pct))}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">β</span><strong>${escapeHtml(
          ll.beta != null ? String(ll.beta) : "0.55"
        )}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">近窗 MAE</span><strong>${escapeHtml(
          ll.mae != null ? `${Number(ll.mae).toFixed(2)}%` : "—"
        )}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">均值误差</span><strong>${escapeHtml(
          ll.mean_error != null ? fmtPct(ll.mean_error) : "—"
        )}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">|err|≤1%</span><strong>${escapeHtml(
          hit
        )}</strong></div>` +
        `<div><span class="strategy-desk-fact-k">样本</span><strong class="${
          thin ? "is-warn" : ""
        }">${escapeHtml(ll.n != null ? `${ll.n} 日` : "—")}</strong></div>` +
        `</div>` +
        `<p class="strategy-desk-note">${escapeHtml(
          ll.note || "A50 − 海外科技×β；诊断用，非 PIT。"
        )}</p>`;
    }

    const histHost = document.getElementById("strategy-macro-history");
    const histLegend = document.getElementById("strategy-macro-history-legend");
    if (histHost) {
      const rows = (ctx && ctx.macro_history) || [];
      if (rows.length >= 5) {
        if (histLegend) histLegend.innerHTML = macroHistoryLegendHtml();
        paintMacroHistoryChart(histHost, rows);
      } else {
        try {
          if (histHost.__lwRo) histHost.__lwRo.disconnect();
        } catch (_) {
          /* ignore */
        }
        histHost.__lwRo = null;
        histHost.__lwChart = null;
        histHost.__lwMount = null;
        if (histHost.shadowRoot) histHost.shadowRoot.innerHTML = "";
        histHost.innerHTML = "";
        if (histLegend) {
          histLegend.innerHTML = `<span class="strategy-desk-empty">${
            ctx && ctx.macro_history_rows === 0
              ? "历史未回填 · macro_backfill"
              : "历史不足"
          }</span>`;
        }
      }
    }

    const events = desk.events || {};
    const summary = events.summary || {};
    const drain =
      summary.drain_ratio != null
        ? `${Number(summary.drain_ratio).toFixed(1)}×`
        : "—";
    paintKpiRow("strategy-events-kpi-body", [
      {
        label: "监管命中",
        value: summary.reg_count ?? "—",
        tone: summary.reg_active ? "is-bad" : "",
        sub: summary.reg_active ? "已触发" : "清静",
      },
      {
        label: "今日 IPO",
        value: summary.ipo_today ?? "—",
        tone: summary.extreme_ipo_day ? "is-bad" : "",
        sub: summary.extreme_ipo_day ? "极端日" : "新股只数",
      },
      { label: "虹吸比", value: drain, sub: "概念流动性" },
      {
        label: "最大市值",
        value: summary.max_ipo_yi != null ? `${summary.max_ipo_yi} 亿` : "—",
        sub: "估算首日",
      },
    ]);

    const regMeta = document.getElementById("strategy-reg-meta");
    const regHost = document.getElementById("strategy-reg-events");
    if (regHost) {
      const hits = (events.regulatory_hits || []).slice(0, 10);
      const concepts = (events.penalty_concepts || events.concept_tags || []).slice(0, 4);
      if (regMeta) {
        regMeta.textContent = concepts.length
          ? concepts.join(" · ")
          : hits.length
            ? `${hits.length} 条`
            : "清静";
      }
      if (!hits.length) {
        regHost.innerHTML = `<p class="strategy-desk-empty">无监管命中</p>`;
      } else {
        regHost.innerHTML = hits
          .map((h) => {
            const title = h.title || "公告";
            const code = h.code || "";
            const name = h.name || "";
            const tags = (h.tags || []).slice(0, 2).join(" · ");
            return (
              `<article class="strategy-event-item">` +
              `<div class="strategy-event-head">` +
              (code
                ? `<span class="strategy-event-code">${escapeHtml(code)}</span>`
                : "") +
              (name
                ? `<span class="strategy-event-name">${escapeHtml(name)}</span>`
                : "") +
              (h.date
                ? `<span class="strategy-event-date">${escapeHtml(
                    String(h.date).slice(0, 10)
                  )}</span>`
                : "") +
              `</div>` +
              `<div class="strategy-event-title">${escapeHtml(String(title))}</div>` +
              (tags
                ? `<div class="strategy-event-sub">${escapeHtml(tags)}</div>`
                : "") +
              `</article>`
            );
          })
          .join("");
      }
    }

    const ipoMeta = document.getElementById("strategy-ipo-meta");
    const ipoHost = document.getElementById("strategy-ipo-events");
    if (ipoHost) {
      const ipoEvents = (events.ipo_events || []).slice(0, 10);
      const drainTop = (events.drain_top || []).slice(0, 3);
      if (ipoMeta) {
        ipoMeta.textContent = summary.extreme_ipo_day
          ? "极端日"
          : drainTop.length
            ? drainTop.map((d) => `${d.concept} ${d.ratio}×`).join(" · ")
            : ipoEvents.length
              ? `${ipoEvents.length} 只明细`
              : "无明细";
      }
      if (!ipoEvents.length) {
        const drainHtml = drainTop.length
          ? `<div class="strategy-chip-row">` +
            drainTop
              .map(
                (d) =>
                  `<span class="strategy-chip is-ipo">${escapeHtml(
                    d.concept
                  )} <b>${escapeHtml(String(d.ratio))}×</b></span>`
              )
              .join("") +
            `</div>`
          : "";
        ipoHost.innerHTML =
          drainHtml +
          `<p class="strategy-desk-empty">${
            summary.ipo_today != null
              ? `今日 ${summary.ipo_today} 只 · 明细未解析`
              : "无 IPO 明细"
          }</p>`;
      } else {
        ipoHost.innerHTML = ipoEvents
          .map((e) => {
            const name = e.name || e.code || "新股";
            const mv =
              e.market_cap_yi != null
                ? `${e.market_cap_yi} 亿`
                : e.market_cap_est != null
                  ? `${(Number(e.market_cap_est) / 1e8).toFixed(0)} 亿`
                  : "";
            const tags = (e.concepts || []).slice(0, 2).join(" · ");
            return (
              `<article class="strategy-event-item">` +
              `<div class="strategy-event-head">` +
              (e.code
                ? `<span class="strategy-event-code">${escapeHtml(e.code)}</span>`
                : "") +
              `<span class="strategy-event-name">${escapeHtml(String(name))}</span>` +
              (e.list_date
                ? `<span class="strategy-event-date">${escapeHtml(
                    String(e.list_date).slice(0, 10)
                  )}</span>`
                : "") +
              `</div>` +
              `<div class="strategy-event-sub">${escapeHtml(
                [mv, tags].filter(Boolean).join(" · ") || "—"
              )}</div>` +
              `</article>`
            );
          })
          .join("");
      }
    }

    const exp = desk.exposure || {};
    paintKpiRow("strategy-exposure-kpi-body", [
      { label: "观察池", value: exp.watching_n ?? "—", sub: "名单只数" },
      {
        label: "池命中",
        value: exp.watching_hit_n ?? (exp.watching_hits || []).length,
        sub: "∩ 热点概念",
      },
      { label: "持仓", value: exp.holding_n ?? "—", sub: "纸面只数" },
      {
        label: "仓命中",
        value: exp.holding_hit_n ?? (exp.holding_hits || []).length,
        sub: "∩ 热点概念",
      },
    ]);
    const chipHost = document.getElementById("strategy-exposure-chips");
    if (chipHost) {
      const chips = Array.isArray(exp.concept_chips)
        ? exp.concept_chips
        : (exp.hot_concepts || []).map((c) => ({ concept: c, kind: "reg" }));
      if (!chips.length) {
        chipHost.innerHTML = `<span class="strategy-desk-empty">无热点概念</span>`;
      } else {
        chipHost.innerHTML = chips
          .slice(0, 16)
          .map((c) => {
            const drain =
              c.drain != null ? ` <b>${escapeHtml(String(c.drain))}×</b>` : "";
            return `<span class="strategy-chip is-${escapeHtml(
              c.kind || "reg"
            )}">${escapeHtml(c.concept || "")}${drain}</span>`;
          })
          .join("");
      }
    }

    const heatHost = document.getElementById("strategy-concept-heatmap");
    const heatMeta = document.getElementById("strategy-heatmap-meta");
    const heat = desk.concept_heatmap || {};
    const heatCells = Array.isArray(heat.cells) ? heat.cells : [];
    if (heatMeta) {
      heatMeta.textContent = heat.as_of
        ? `as-of ${heat.as_of} · 净流入亿`
        : heatCells.length
          ? "净流入亿"
          : "";
    }
    if (heatHost) {
      if (!heatCells.length) {
        heatHost.innerHTML = `<p class="strategy-desk-empty">无概念流 · 刷新 ingest</p>`;
      } else {
        heatHost.innerHTML = heatCells
          .slice(0, 16)
          .map((c) => {
            const net = Number(c.net_yi);
            const pct = Math.max(8, Math.round((Number(c.intensity) || 0) * 100));
            const side = (c.side || (net >= 0 ? "in" : "out"));
            const netTxt = Number.isFinite(net)
              ? `${net > 0 ? "+" : ""}${net.toFixed(1)}`
              : "—";
            return (
              `<div class="strategy-heatmap-row is-${escapeHtml(side)}" title="${escapeHtml(
                `${c.concept || ""} · 净额 ${netTxt} 亿` +
                  (c.chg_pct != null ? ` · 涨跌 ${c.chg_pct}%` : "")
              )}">` +
              `<span class="strategy-heatmap-name">${escapeHtml(c.concept || "—")}</span>` +
              `<span class="strategy-heatmap-track"><i style="width:${pct}%"></i></span>` +
              `<span class="strategy-heatmap-val">${escapeHtml(netTxt)}</span>` +
              `</div>`
            );
          })
          .join("");
      }
    }
    const paintHits = (hostId, hits) => {
      const host = document.getElementById(hostId);
      if (!host) return;
      const list = Array.isArray(hits) ? hits : [];
      if (!list.length) {
        host.innerHTML = `<p class="strategy-desk-empty">无命中</p>`;
        return;
      }
      host.innerHTML = list
        .map((h) => {
          return (
            `<article class="strategy-event-item">` +
            `<div class="strategy-event-head">` +
            `<span class="strategy-event-code">${escapeHtml(h.code || "")}</span>` +
            `<span class="strategy-event-name">${escapeHtml(h.name || "")}</span>` +
            (h.via_code ? `<span class="strategy-event-tag">代码</span>` : "") +
            `</div>` +
            `<div class="strategy-event-sub">${escapeHtml(
              (h.concepts || []).join(" · ") || "—"
            )}</div>` +
            `</article>`
          );
        })
        .join("");
    };
    paintHits("strategy-exposure-watching", exp.watching_hits);
    paintHits("strategy-exposure-holdings", exp.holding_hits);

    const limHost = document.getElementById("strategy-regime-limits");
    const limMeta = document.getElementById("strategy-regime-limits-meta");
    const meta = desk.regime_meta || (ctx && ctx.regime) || {};
    if (limMeta) {
      const bits = [];
      if (meta.score_penalty != null) bits.push(`罚分 ${Number(meta.score_penalty).toFixed(2)}`);
      if (meta.volatility_pct != null) bits.push(`波动 ${Number(meta.volatility_pct).toFixed(2)}%`);
      if (meta.overlay_deferred) bits.push("overlay→跨市场");
      limMeta.textContent = bits.join(" · ");
    }
    if (limHost) {
      const rows = Array.isArray(desk.regime_limits) ? desk.regime_limits : [];
      if (!rows.length) {
        limHost.innerHTML = `<p class="strategy-desk-empty">无对照表</p>`;
      } else {
        limHost.innerHTML = rows
          .map((r) => {
            const scale =
              r.position_scale != null ? Number(r.position_scale).toFixed(2) : "—";
            const pct =
              r.position_scale != null
                ? Math.round(Number(r.position_scale) * 100)
                : 0;
            return (
              `<div class="strategy-regime-scale-cell${
                r.current ? " is-current" : ""
              }" data-regime="${escapeHtml(r.regime || "")}">` +
              `<div class="strategy-regime-scale-top">` +
              `<span class="strategy-regime-scale-lab">${escapeHtml(
                r.label || r.regime || "—"
              )}${r.current ? " · 当前" : ""}</span>` +
              `<strong>×${escapeHtml(scale)}</strong>` +
              `</div>` +
              `<div class="strategy-regime-scale-bar" aria-hidden="true">` +
              `<i style="width:${pct}%"></i></div>` +
              `<div class="strategy-regime-scale-note">${escapeHtml(
                r.note || "—"
              )}</div>` +
              `</div>`
            );
          })
          .join("");
      }
    }
  }

  async function refreshMarketContextPanel() {
    const btn = document.getElementById("strategy-mctx-refresh");
    const meta = document.getElementById("strategy-meta");
    const prev = meta ? meta.textContent : "";
    if (btn) btn.disabled = true;
    setStrategyMetaStatus("ingest…", { busy: true });
    try {
      const res = await runMarketContextIngest(apiFetch, { backfill: true });
      if (res && res.ok === false) {
        setStrategyMetaStatus(res.error || "ingest 失败");
        return;
      }
      await loadMarketContextPanel();
    } catch (err) {
      setStrategyMetaStatus(String(err.message || err));
    } finally {
      if (btn) btn.disabled = false;
      const cur = document.getElementById("strategy-meta");
      if (cur && cur.textContent === "ingest…") {
        setStrategyMetaStatus(prev || "盘前看板 · 只读 · 不改 ŷ · 未接调仓");
      }
    }
  }

  async function loadStrategyList() {
    try {
      const res = await fetch("/api/quant/strategies");
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderStrategyList(data);
    } catch (err) {
      const st = document.getElementById("strategy-meta");
      if (st) st.textContent = `策略配置加载失败：${err.message || err}`;
    }
  }

  function renderStrategyList(data) {
    if (!data || !data.success || !data.strategies) return;
    // 观察台直方图只读滞回门槛（本页不再提供编辑）
    const floors = data.scoring_floors || {};
    if (floors.min_predicted_score != null || floors.min_hold_predicted_score != null) {
      state.quantScoringFloors = mergeScoringFloors(state.quantScoringFloors, floors);
      state._scoringFloorsHydrated = true;
    }
  }

  return {
    loadSignalConfigPanel,
    loadStrategyList,
    renderStrategyList,
    loadMarketContextPanel,
    refreshMarketContextPanel,
  };
}
