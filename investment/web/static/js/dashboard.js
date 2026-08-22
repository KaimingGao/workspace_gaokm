import { apiFetch } from "./api_client.js";
import { renderLineChart, renderDualLineChart } from "./lw_charts.js";
import { escapeHtml } from "./shared.js";
import { macroHistoryLegendHtml, paintMacroHistoryChart, runMarketContextIngest } from "./macro_context_ui.js";
import { fetchDash, fetchDashboardBundle } from "./dashboard_api.js?v=p1234";

const V = (typeof window !== "undefined" && window.__ASSET_V__) || "p803";

let _initiated = false;
let _navChart = null;
let _drawdownChart = null;
let _varChart = null;

function cssToken(name, fallback) {
  const root =
    document.querySelector(".dashboard-page") || document.documentElement;
  const v = getComputedStyle(root).getPropertyValue(name).trim();
  return v || fallback;
}

function chartPrimary() {
  return cssToken("--d-chart-primary", cssToken("--accent", "#1890ff"));
}

function chartBench() {
  return cssToken("--d-chart-bench", cssToken("--ok", "#059669"));
}

function fmtPct(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return sign + v.toFixed(decimals) + "%";
}

function fmtNum(v, decimals = 2) {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(decimals);
}

/** 收益类：A 股涨跌色 */
function clsReturn(v, threshold = 0) {
  if (v == null || !isFinite(v)) return "";
  return v >= threshold ? "is-good" : "is-bad";
}

/** 质量类：ok / danger，非涨跌 */
function clsQuality(v, threshold = 0) {
  if (v == null || !isFinite(v)) return "";
  return v >= threshold ? "is-quality-good" : "is-quality-bad";
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

function regimeCls(code) {
  const c = String(code || "").toLowerCase();
  if (c === "bear" || c === "weak") return "is-bad";
  if (c === "bull" || c === "strong") return "is-good";
  return "";
}

function fmtMoney(v) {
  if (v == null || !isFinite(v)) return "—";
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(2);
}

function readBenchmarkFlag() {
  const el = document.getElementById("dashboard-benchmark-toggle");
  return el ? !!el.checked : true;
}

function readActiveRange() {
  const active = document.querySelector("#dashboard-nav-range [data-range].is-active");
  return active ? active.dataset.range : "30";
}

function setRangeTabState(rangeTabs, activeBtn) {
  if (!rangeTabs || !activeBtn) return;
  rangeTabs.querySelectorAll("[role='tab']").forEach((b) => {
    const on = b === activeBtn;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
}

function kpiCardHtml(item) {
  const val = item.value;
  const sub = item.sub || "";
  const direction =
    item.semantic === "quality"
      ? clsQuality(item.direction, 0)
      : clsReturn(item.direction, 0);
  const sparkline = item.sparkline
    ? `<canvas class="dashboard-kpi-spark" data-spark='${JSON.stringify(item.sparkline)}' width="80" height="24"></canvas>`
    : "";
  return `
    <div class="dashboard-kpi-card ${direction}" data-kpi="${item.key}">
      <span class="dashboard-kpi-label">${escapeHtml(item.label)}</span>
      <span class="dashboard-kpi-value">${item.formatted || escapeHtml(String(val))}</span>
      <span class="dashboard-kpi-sub">${escapeHtml(sub)}</span>
      ${sparkline}
    </div>
  `;
}

function renderSparklines() {
  document.querySelectorAll(".dashboard-kpi-spark").forEach((canvas) => {
    const data = JSON.parse(canvas.dataset.spark || "[]");
    if (!data.length) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const step = w / (data.length - 1 || 1);
    const colorUp = cssToken("--color-up", "#f5222d");
    const colorDown = cssToken("--color-down", "#52c41a");
    const color = data[data.length - 1] >= data[0] ? colorUp : colorDown;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    // hex → soft fill fallback
    ctx.globalAlpha = 0.08;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 1;
  });
}

function renderMarketOverview(market) {
  const host = document.getElementById("dashboard-market-cards");
  if (!host) return;

  const indices = market?.indices || [];
  const breadth = market?.breadth || {};
  const turnover = market?.turnover || {};

  const cards = [];

  for (const idx of indices) {
    const pct = idx.change_pct;
    const cls = pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
    const closeTxt =
      idx.close != null ? `vs 昨收 · ${fmtNum(idx.close, 0)}` : "vs 昨收";
    cards.push(`
      <div class="dashboard-market-card dashboard-market-card--index ${cls}" title="涨跌幅相对昨收">
        <span class="dashboard-market-label">${escapeHtml(idx.name)}</span>
        <span class="dashboard-market-value">${idx.change_pct != null ? fmtPct(idx.change_pct) : "—"}</span>
        <span class="dashboard-market-sub">${escapeHtml(closeTxt)}</span>
      </div>
    `);
  }

  if (breadth.total_holdings || breadth.up_count || breadth.down_count || breadth.flat_count) {
    const total = breadth.total_holdings || 1;
    const upPct = ((breadth.up_count / total) * 100).toFixed(0);
    const downPct = ((breadth.down_count / total) * 100).toFixed(0);
    cards.push(`
      <div class="dashboard-market-card is-breadth">
        <span class="dashboard-market-label">涨跌家数</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${breadth.up_count || 0}<span class="dashboard-market-sep">/</span>${breadth.down_count || 0}</span>
          <span class="dashboard-market-change">涨${upPct}% · 跌${downPct}% · 平${breadth.flat_count || 0}</span>
        </div>
      </div>
    `);
  }

  if (turnover.total_value != null || turnover.holding_count) {
    cards.push(`
      <div class="dashboard-market-card">
        <span class="dashboard-market-label">持仓市值</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${fmtMoney(turnover.total_value)}</span>
          <span class="dashboard-market-change">${turnover.holding_count || 0} 只</span>
        </div>
      </div>
    `);
  }

  if (breadth.limit_up || breadth.limit_down) {
    cards.push(`
      <div class="dashboard-market-card is-limit">
        <span class="dashboard-market-label">涨停/跌停</span>
        <div class="dashboard-market-metrics">
          <span class="dashboard-market-value">${breadth.limit_up || 0}<span class="dashboard-market-sep">/</span>${breadth.limit_down || 0}</span>
          <span class="dashboard-market-change">涨停 / 跌停</span>
        </div>
      </div>
    `);
  }

  if (cards.length === 0) {
    cards.push(`<div class="dashboard-empty dashboard-empty--span">暂无市场数据</div>`);
  }

  host.innerHTML = cards.join("");
}

function renderMarketContext(ctx) {
  const host = document.getElementById("dashboard-market-context");
  if (!host) return;

  if (!ctx || ctx.ok === false) {
    host.innerHTML =
      `<div class="dashboard-market-context-empty">盘前上下文未就绪 · 点击「刷新 ingest」拉取 macro/情绪/公告</div>` +
      `<div class="dashboard-market-context-toolbar"><button type="button" class="dialog-btn secondary dashboard-market-context-refresh" id="dashboard-context-refresh">刷新 ingest</button></div>`;
    wireMarketContextRefresh(host);
    return;
  }

  const fresh = ctx.freshness || {};
  const macro = ctx.macro || {};
  const sent = ctx.market_sentiment || {};
  const macroDegraded = !!ctx.macro_degraded;
  const dataReady = ctx.data_ready !== false;
  const ann = ctx.announcement || {};
  const regulatory = ann.regulatory || {};
  const ipo = ann.ipo || {};
  const flags = ctx.prior_flags || {};
  const regimeSnap = ctx.regime || {};
  const priorActiveN = [
    flags.cross_market,
    flags.market_sentiment,
    flags.regulatory,
    flags.ipo_drain,
  ].filter(Boolean).length;
  const priorActive = flags.any_active
    ? priorActiveN > 0
      ? `激活 · ${priorActiveN}/4`
      : "激活"
    : "待机";
  const penaltySub =
    (ann.penalty_concepts || []).slice(0, 2).join(" · ") ||
    (regulatory.concept_tags || []).slice(0, 2).join(" · ") ||
    "无热点概念";
  const needHistory =
    (ctx.macro_history || []).length < 5 && (ctx.macro_history_rows || 0) === 0;
  const showIngestBtn =
    !!fresh.needs_ingest || needHistory || macroDegraded || !dataReady;
  const stale = fresh.needs_ingest || macroDegraded || !dataReady ? "is-stale" : "is-fresh";
  const freshLabel = fresh.needs_ingest
    ? "需刷新"
    : macroDegraded
      ? "macro 降级"
      : !dataReady
        ? "部分空"
        : "新鲜";
  const macroErrHint = macroDegraded
    ? (ctx.macro_errors || macro.errors || []).slice(0, 3).join(" · ") || "海外数据源未返回"
    : "";

  const cards = [
    {
      label: "海外科技",
      value: macro.overseas_tech_1d_pct != null ? fmtPct(macro.overseas_tech_1d_pct) : "—",
      sub:
        macroErrHint ||
        (macro.a50_1d_pct != null ? `A50 ${fmtPct(macro.a50_1d_pct)}` : "隔夜 · 待 ingest"),
      cls: clsReturn(macro.overseas_tech_1d_pct, 0),
      tip:
        "SOX/NDX/QQQ/KWEB 等隔夜均涨跌。越负越利空 A 股科技；下方 A50 为富时 A50 期指近端。不改 ŷ，供跨市场 M prior 触发。",
    },
    {
      label: "Lead-Lag",
      value:
        macro.lead_lag_expected_gap_pct != null
          ? fmtPct(macro.lead_lag_expected_gap_pct)
          : "—",
      sub: "预期缺口",
      cls: clsReturn(macro.lead_lag_expected_gap_pct, 0),
      tip:
        "海外科技 × 经验 β（约 0.55）得到的开盘缺口粗估，不是收盘涨跌预测。用于跨市场风险提示。",
    },
    {
      label: "情绪周期",
      value: sent.sentiment_cycle_score != null ? String(sent.sentiment_cycle_score) : "—",
      sub:
        sent.broken_limit_rate != null
          ? `炸板 ${(sent.broken_limit_rate * 100).toFixed(0)}%`
          : "涨停溢价",
      cls: clsQuality(sent.sentiment_cycle_score, 50),
      tip:
        "市场情绪温度（周期分）。炸板率=涨停后开板比例，偏高表示跟风脆弱。对应 market_sentiment_prior，不改 ŷ。",
    },
    {
      label: "监管降温",
      value: regulatory.active ? `${regulatory.count || 0} 条` : "—",
      sub: penaltySub,
      cls: regulatory.active || flags.regulatory ? "is-bad" : "",
      tip:
        "盘前抓取的监管/处罚类公告条数；副文案为命中概念。对应 regulatory_prior，可在调仓时缩仓或警告。",
    },
    {
      label: "Regime",
      value: regimeLabel(regimeSnap.regime),
      sub:
        regimeSnap.index_return_pct != null
          ? `基准 ${fmtPct(regimeSnap.index_return_pct)}${
              regimeSnap.macro_overlay_deferred ? " · overlay→M" : ""
            }`
          : (regimeSnap.reason || "—").slice(0, 28),
      cls: regimeCls(regimeSnap.regime),
      tip:
        "按基准近端涨跌划分熊/弱/中/强/牛，影响因子权重环境。overlay→M 表示科技拖累已交给跨市场 prior，避免与 Regime 双重惩罚。",
    },
    {
      label: "Prior",
      value: priorActive,
      sub: (ctx.prior_warnings || []).slice(0, 1).join("") || "M 层闸",
      cls: flags.any_active ? "is-bad" : "",
      tip:
        "M 层 prior（跨市场/情绪/监管/IPO）是否触发。激活时调仓可缩仓或禁买；ŷ 排名轴不变。副文案为当前警告摘要。",
    },
    {
      label: "IPO 虹吸",
      value:
        ipo.liquidity_drain_ratio != null
          ? `${Number(ipo.liquidity_drain_ratio).toFixed(1)}x`
          : ipo.extreme_ipo_day
            ? "极端"
            : "—",
      sub: ipo.ipo_today_count ? `${ipo.ipo_today_count} 只新股` : "今日",
      cls: ipo.extreme_ipo_day ? "is-bad" : "",
      tip:
        "今日新股对流动性的虹吸压力。比值或「极端」偏高时，ipo_drain_prior 可能在调仓时缩仓。不改 ŷ。",
    },
    {
      label: "快照",
      value: freshLabel,
      sub: ctx.computed_at ? ctx.computed_at.slice(0, 16) : "—",
      cls: stale,
      tip:
        "盘前上下文最近一次计算/展示时间。需刷新时请点「刷新 ingest」或等 paper_daily 自动拉取（过期约 18h）。非盘中 tick。",
    },
  ];

  host.innerHTML = `
    <div class="dashboard-market-context-band ${stale}">
      <div class="dashboard-market-context-head">
        <span class="dashboard-market-context-title" title="盘前 macro / 情绪 / 公告快照。用于 M prior 与 Regime 环境判断；不预测当日大盘收盘，不改 ŷ。">盘前上下文</span>
        ${
          (ctx.macro_sparkline || []).length >= 2
            ? `<canvas class="dashboard-mctx-spark" id="dashboard-mctx-spark" width="72" height="22" aria-label="海外科技近14日" title="海外科技近14日走势"></canvas>`
            : ""
        }
        ${
          showIngestBtn
            ? `<button type="button" class="dialog-btn secondary dashboard-market-context-refresh" id="dashboard-context-refresh" title="重新拉取 macro / 情绪 / 公告快照">刷新 ingest</button>`
            : ""
        }
      </div>
      <div class="dashboard-market-context-cards">
        ${cards
          .map(
            (c) => `
          <div class="dashboard-market-context-card ${c.cls || ""}" title="${escapeHtml(c.tip || "")}">
            <span class="dashboard-market-context-label">${escapeHtml(c.label)}</span>
            <span class="dashboard-market-context-value">${escapeHtml(String(c.value))}</span>
            <span class="dashboard-market-context-sub">${escapeHtml(c.sub || "")}</span>
          </div>`
          )
          .join("")}
      </div>
      ${
        macroDegraded
          ? `<p class="dashboard-mctx-degraded-hint">跨市场 macro 未拉到（${escapeHtml(
              macroErrHint || "akshare/网络"
            )}）· 情绪/公告/Regime 仍可用</p>`
          : !dataReady
            ? `<p class="dashboard-mctx-degraded-hint">尚未 ingest · 点「刷新 ingest」</p>`
            : ""
      }
      ${
        (ctx.macro_history || []).length >= 5
          ? `<div class="dashboard-mctx-history" aria-label="宏观历史近30日" title="海外科技 / 富时A50 近30日日涨跌%（灰线=0）">
              <span class="dashboard-mctx-history-label">宏观近30日</span>
              <canvas class="dashboard-mctx-history-canvas" id="dashboard-mctx-history" width="520" height="56" aria-label="海外科技与A50近30日日涨跌"></canvas>
              ${macroHistoryLegendHtml()}
            </div>`
          : ctx.macro_history_rows === 0
            ? `<p class="dashboard-mctx-history-hint">宏观历史未回填 · 可运行 macro_backfill</p>`
            : ""
      }
    </div>`;

  paintMacroHistoryChart(
    document.getElementById("dashboard-mctx-history"),
    ctx.macro_history || [],
    { cssToken }
  );

  const spark = document.getElementById("dashboard-mctx-spark");
  const sparkData = ctx.macro_sparkline || [];
  if (spark && sparkData.length >= 2) {
    const sctx = spark.getContext("2d");
    if (sctx) {
      const w = spark.width;
      const h = spark.height;
      const min = Math.min(...sparkData);
      const max = Math.max(...sparkData);
      const range = max - min || 1;
      const step = w / (sparkData.length - 1);
      sctx.clearRect(0, 0, w, h);
      sctx.beginPath();
      sparkData.forEach((v, i) => {
        const x = i * step;
        const y = h - ((v - min) / range) * (h - 4) - 2;
        if (i === 0) sctx.moveTo(x, y);
        else sctx.lineTo(x, y);
      });
      sctx.strokeStyle =
        sparkData[sparkData.length - 1] >= 0
          ? cssToken("--color-up", "#f5222d")
          : cssToken("--color-down", "#52c41a");
      sctx.lineWidth = 1.5;
      sctx.stroke();
    }
  }

  const refreshBtn = document.getElementById("dashboard-context-refresh");
  wireMarketContextRefresh(host, refreshBtn);
}

function wireMarketContextRefresh(host, btnEl) {
  const refreshBtn =
    btnEl || (host && host.querySelector("#dashboard-context-refresh"));
  if (!refreshBtn || refreshBtn.dataset.bound === "1") return;
  refreshBtn.dataset.bound = "1";
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "ingest…";
    try {
      const res = await runMarketContextIngest(apiFetch, { backfill: true });
      if (res && res.ok !== false) {
        refreshBtn.textContent = "完成";
        loadDashboardData(readActiveRange(), readBenchmarkFlag());
      } else {
        refreshBtn.textContent = res?.error || "失败";
      }
    } catch (_) {
      refreshBtn.textContent = "失败";
    } finally {
      setTimeout(() => {
        refreshBtn.disabled = false;
        if (refreshBtn.textContent === "完成" || refreshBtn.textContent === "失败") {
          refreshBtn.textContent = "刷新 ingest";
        }
      }, 1200);
    }
  });
}

function renderSectorHeatmap(sectors) {
  const grid = document.getElementById("dashboard-sector-grid");
  const sub = document.getElementById("dashboard-sector-sub");
  if (!grid) return;
  if (!sectors || !sectors.length) {
    grid.innerHTML = `<div class="dashboard-empty">暂无板块数据</div>`;
    if (sub) sub.textContent = "持仓行业";
    return;
  }
  if (sub) sub.textContent = `${sectors.length} 个板块`;
  grid.innerHTML = sectors
    .map((s) => {
      const pct = s.change_pct ?? 0;
      const cls = pct > 0 ? "is-up" : pct < 0 ? "is-down" : "";
      const upRatio = s.up_ratio || 0;
      const title = `${s.name}\n涨跌: ${fmtPct(pct)}\n市值: ${fmtMoney(s.value)}\n个股: ${s.count}\n上涨比例: ${upRatio}%`;
      return `
      <div class="dashboard-sector-cell ${cls}" data-sector="${escapeHtml(s.name)}" title="${escapeHtml(title)}">
        <span class="dashboard-sector-name">${escapeHtml(s.name)}</span>
        <span class="dashboard-sector-pct">${fmtPct(pct)}</span>
        <span class="dashboard-sector-sub">${s.count}只 · ${upRatio}%涨</span>
      </div>
    `;
    })
    .join("");
}

function renderLeaderboard(strategies) {
  const body = document.getElementById("dashboard-leaderboard");
  if (!body) return;
  if (!strategies || !strategies.length) {
    body.innerHTML = `<div class="dashboard-empty">暂无策略数据</div>`;
    return;
  }
  const rows = strategies
    .map(
      (s, i) => `
    <tr>
      <td class="col-rank">${i + 1}</td>
      <td class="col-name">${escapeHtml(s.name || s.strategy || "—")}</td>
      <td class="col-ret ${s.return_pct > 0 ? "up" : s.return_pct < 0 ? "down" : ""}">${fmtPct(s.return_pct)}</td>
      <td class="col-sharpe">${fmtNum(s.sharpe, 2)}</td>
    </tr>
  `
    )
    .join("");
  body.innerHTML = `
    <table class="dashboard-leaderboard-table">
      <thead>
        <tr>
          <th>#</th>
          <th>策略</th>
          <th class="num">收益率</th>
          <th class="num">夏普</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderSignals(signals) {
  const list = document.getElementById("dashboard-signals-list");
  const countEl = document.getElementById("dashboard-signals-count");
  if (!list) return;
  if (countEl) countEl.textContent = signals ? `${signals.length} 条` : "—";
  if (!signals || !signals.length) {
    list.innerHTML = `<div class="dashboard-empty">暂无信号</div>`;
    return;
  }
  list.innerHTML = signals
    .slice(0, 20)
    .map((s) => {
      const rawDir = String(s.direction || "").toLowerCase();
      const dir =
        rawDir === "long" ||
        rawDir === "buy" ||
        rawDir === "bullish" ||
        s.direction === "多"
          ? "is-long"
          : rawDir === "short" ||
              rawDir === "sell" ||
              rawDir === "bearish" ||
              s.direction === "空"
            ? "is-short"
            : "";
      const dirLabel =
        dir === "is-long" ? "多" : dir === "is-short" ? "空" : String(s.direction || "—");
      const ts = String(s.time || s.ts || "");
      const timeShort = ts.includes("T")
        ? ts.slice(5, 16).replace("T", " ")
        : ts.slice(0, 16);
      const code = s.code || s.stock_code || "—";
      const name = s.name || s.stock_name || "";
      return `
      <div class="dashboard-signal-item ${dir}">
        <span class="dashboard-signal-code">${escapeHtml(String(code))}</span>
        <span class="dashboard-signal-name" title="${escapeHtml(name || String(code))}">${escapeHtml(name || "—")}</span>
        <span class="dashboard-signal-dir">${escapeHtml(dirLabel)}</span>
        <span class="dashboard-signal-score num">${fmtNum(s.score ?? s.predicted_score, 2)}</span>
        <span class="dashboard-signal-time">${escapeHtml(timeShort)}</span>
      </div>
    `;
    })
    .join("");
}

function renderRiskStrip(risk, exposure, varData, kpis, drawdownData) {
  const host = document.getElementById("dashboard-risk-strip");
  if (!host) return;
  const var95 =
    (risk && risk.var_95 != null ? risk.var_95 : null) ??
    (varData && varData.var_95 != null ? varData.var_95 : null);
  const cvar95 =
    (risk && risk.cvar_95 != null ? risk.cvar_95 : null) ??
    (varData && varData.cvar_95 != null ? varData.cvar_95 : null);
  const hhi = exposure && exposure.hhi != null ? exposure.hhi : null;
  const conc = exposure && exposure.concentration ? exposure.concentration : null;
  const concMap = { low: "分散", medium: "中等", high: "集中" };
  const overN = Array.isArray(exposure?.over_limit_sectors)
    ? exposure.over_limit_sectors.length
    : 0;
  const holdingsN = exposure?.holdings_count ?? exposure?.sector_count ?? null;
  const sharpe =
    risk?.sharpe != null
      ? risk.sharpe
      : kpis?.sharpe != null
        ? kpis.sharpe
        : null;
  const maxDd =
    risk?.max_drawdown != null
      ? risk.max_drawdown
      : drawdownData?.max_drawdown != null
        ? drawdownData.max_drawdown
        : kpis?.max_drawdown != null
          ? kpis.max_drawdown
          : null;

  const items = [
    {
      k: "VaR95",
      v: var95 != null ? fmtPct(var95) : "—",
      cls: var95 != null && var95 > 2 ? "is-quality-bad" : "",
    },
    {
      k: "CVaR95",
      v: cvar95 != null ? fmtPct(cvar95) : "—",
      cls: cvar95 != null && cvar95 > 3 ? "is-quality-bad" : "",
    },
    {
      k: "Sharpe",
      v: sharpe != null ? fmtNum(sharpe, 2) : "—",
      cls:
        sharpe != null && sharpe < 0
          ? "is-quality-bad"
          : sharpe != null && sharpe >= 1
            ? "is-quality-good"
            : "",
    },
    {
      k: "最大回撤",
      v: maxDd != null ? fmtPct(maxDd) : "—",
      cls: maxDd != null && maxDd > 5 ? "is-quality-bad" : "",
    },
    {
      k: "集中度",
      v: conc
        ? `${concMap[conc] || conc}${hhi != null ? ` · HHI ${fmtNum(hhi, 3)}` : ""}`
        : "—",
      cls: conc === "high" ? "is-quality-bad" : "",
    },
    {
      k: "超限板块",
      v: String(overN),
      cls: overN > 0 ? "is-quality-bad" : "is-quality-good",
    },
    {
      k: "持仓",
      v: holdingsN != null ? `${holdingsN}` : "—",
      cls: "",
    },
  ];
  host.innerHTML = items
    .map(
      (it) =>
        `<div class="dashboard-risk-strip-item ${it.cls}">` +
        `<span class="dashboard-risk-strip-k">${escapeHtml(it.k)}</span>` +
        `<span class="dashboard-risk-strip-v">${escapeHtml(it.v)}</span>` +
        `</div>`
    )
    .join("");
}

function renderRiskMetrics(risk) {
  const host = document.getElementById("dashboard-risk-cards");
  if (!host) return;
  if (!risk || risk.ok === false) {
    host.innerHTML = `<div class="dashboard-empty dashboard-empty--span">${risk?.message || "暂无风险数据"}</div>`;
    return;
  }

  const items = [
    {
      key: "ann_return",
      label: "年化收益",
      formatted: fmtPct(risk.ann_return),
      direction: risk.ann_return,
      semantic: "return",
    },
    {
      key: "ann_vol",
      label: "年化波动",
      formatted: fmtPct(risk.ann_volatility),
      direction: -risk.ann_volatility,
      semantic: "quality",
    },
    {
      key: "sortino",
      label: "Sortino",
      formatted: fmtNum(risk.sortino, 2),
      direction: risk.sortino - 1,
      semantic: "quality",
    },
    {
      key: "calmar",
      label: "Calmar",
      formatted: fmtNum(risk.calmar, 2),
      direction: risk.calmar - 1,
      semantic: "quality",
    },
  ];

  host.innerHTML = items
    .map((item) => {
      const direction =
        item.semantic === "quality"
          ? clsQuality(item.direction, 0)
          : clsReturn(item.direction, 0);
      return `
      <div class="dashboard-risk-card ${direction}" data-risk="${item.key}">
        <span class="dashboard-risk-label">${escapeHtml(item.label)}</span>
        <span class="dashboard-risk-value">${item.formatted}</span>
      </div>
    `;
    })
    .join("");
}

function renderFactorExposure(exposure) {
  const host = document.getElementById("dashboard-exposure-body");
  const concEl = document.getElementById("dashboard-exposure-concentration");
  if (!host) return;
  if (!exposure || !exposure.ok) {
    host.innerHTML = `<div class="dashboard-empty">${exposure?.message || "暂无暴露数据"}</div>`;
    if (concEl) concEl.textContent = "—";
    return;
  }

  if (concEl) {
    const concMap = { low: "分散", medium: "中等", high: "集中" };
    concEl.textContent = `${concMap[exposure.concentration] || "—"} · HHI ${fmtNum(exposure.hhi, 3)}`;
  }

  const sectors = exposure.sectors || [];
  if (!sectors.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无板块暴露</div>`;
    return;
  }

  const maxPct = Math.max(...sectors.map((s) => s.pct), 1);
  host.innerHTML = `
    <div class="dashboard-exposure-list">
      ${sectors
        .slice(0, 8)
        .map(
          (s) => `
        <div class="dashboard-exposure-row">
          <span class="dashboard-exposure-name">${escapeHtml(s.name)}</span>
          <div class="dashboard-exposure-bar-track">
            <div class="dashboard-exposure-bar" style="width:${((s.pct / maxPct) * 100).toFixed(1)}%"></div>
          </div>
          <span class="dashboard-exposure-pct num">${s.pct.toFixed(1)}%</span>
        </div>
      `
        )
        .join("")}
    </div>
  `;
}

function allocColor(i) {
  const fallback = [
    "#1890ff",
    "#059669",
    "#b45309",
    "#b42318",
    "#0369a1",
    "#4d7c0f",
    "#0f766e",
    "#475569",
  ];
  return cssToken(`--d-alloc-${(i % 8) + 1}`, fallback[i % 8]);
}

/** 资产配置：横向条带（非饼图） */
function renderAllocation(allocation) {
  const host = document.getElementById("dashboard-allocation-legend");
  const sub = document.getElementById("dashboard-allocation-sub");
  if (!host) return;
  if (!allocation || !allocation.sectors || !allocation.sectors.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无配置数据</div>`;
    if (sub) sub.textContent = "板块市值";
    return;
  }
  const total =
    allocation.total_value ||
    allocation.sectors.reduce((s, x) => s + (Number(x.value) || 0), 0) ||
    1;
  if (sub) sub.textContent = `总市值 ${fmtMoney(total)}`;

  const maxPct = Math.max(
    ...allocation.sectors.map((s) =>
      s.pct != null ? Number(s.pct) : ((Number(s.value) || 0) / total) * 100
    ),
    1
  );

  host.innerHTML = `
    <div class="dashboard-allocation-list">
      ${allocation.sectors
        .map((s, i) => {
          const pct =
            s.pct != null
              ? Number(s.pct)
              : ((Number(s.value) || 0) / total) * 100;
          const color = allocColor(i);
          const w = Math.max(2, (pct / maxPct) * 100);
          return `
        <div class="dashboard-allocation-row">
          <span class="dashboard-allocation-swatch" style="background:${color}"></span>
          <span class="dashboard-allocation-name">${escapeHtml(s.name)}</span>
          <div class="dashboard-allocation-bar-track">
            <div class="dashboard-allocation-bar" style="width:${w.toFixed(1)}%;background:${color}"></div>
          </div>
          <span class="dashboard-allocation-pct num">${pct.toFixed(1)}%</span>
        </div>`;
        })
        .join("")}
    </div>
  `;
}

function renderNavChart(points, range, benchmarkPoints) {
  const host = document.getElementById("dashboard-nav-chart");
  const statsEl = document.getElementById("dashboard-nav-stats");
  if (!host) return;
  let filtered = points || [];
  let filteredBenchmark = benchmarkPoints || [];
  if (range && range !== "all") {
    const now = new Date();
    let cutoff;
    if (range === "30") cutoff = new Date(now.getTime() - 30 * 86400000);
    else if (range === "90") cutoff = new Date(now.getTime() - 90 * 86400000);
    else if (range === "ytd") cutoff = new Date(now.getFullYear(), 0, 1);
    if (cutoff) {
      filtered = points.filter((p) => new Date(p.time || p.date) >= cutoff);
      filteredBenchmark = filteredBenchmark.filter(
        (p) => new Date(p.time || p.date) >= cutoff
      );
    }
  }
  const pts = filtered.map((p) => ({
    time: p.time || p.date,
    value: p.value ?? p.equity ?? p.nav,
  }));
  const benchPts = filteredBenchmark.map((p) => ({
    time: p.time || p.date,
    value: p.value ?? p.equity ?? p.nav,
  }));

  const lastLive = !!(pts.length && (filtered[filtered.length - 1] || {}).live);
  const subEl = document.querySelector(".dashboard-nav-card .dashboard-card-sub");
  if (subEl) {
    subEl.textContent = lastLive ? "起点 100 · 末点现价盯市" : "起点 100";
  }

  if (statsEl && pts.length > 1) {
    const vals = pts.map((p) => p.value).filter((v) => v != null && isFinite(v));
    if (vals.length >= 2) {
      const first = vals[0];
      const last = vals[vals.length - 1];
      const ret = (last / first - 1) * 100;
      const peak = Math.max(...vals);
      const dd = ((peak - last) / peak) * 100;

      let benchRet = null;
      let excessRet = null;
      if (benchPts.length >= 2) {
        const bFirst = benchPts[0].value;
        const bLast = benchPts[benchPts.length - 1].value;
        if (isFinite(bFirst) && isFinite(bLast) && bFirst !== 0) {
          benchRet = (bLast / bFirst - 1) * 100;
          excessRet = ret - benchRet;
        }
      }

      const stat = (label, value, cls = "") =>
        `<span class="dashboard-nav-stat"><span class="nav-stat-label">${label}</span>` +
        `<span class="nav-stat-value ${cls}">${value}</span></span>`;
      let statsHtml =
        stat("区间", fmtPct(ret), ret >= 0 ? "up" : "down") +
        stat("峰值", fmtNum(peak, 0)) +
        stat("回撤", fmtPct(-dd), dd > 0 ? "down" : "");
      if (benchRet !== null) {
        statsHtml +=
          stat("基准", fmtPct(benchRet), benchRet >= 0 ? "up" : "down") +
          stat("超额", fmtPct(excessRet), excessRet >= 0 ? "up" : "down");
      }
      statsHtml += stat("n", String(vals.length));
      statsEl.innerHTML = statsHtml;
    }
  }

  const primary = chartPrimary();
  const bench = chartBench();
  if (benchPts.length >= 2) {
    renderDualLineChart(host, pts, benchPts, {
      labelA: "策略净值",
      labelB: "沪深300",
      colorA: primary,
      colorB: bench,
      emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
      disableZoom: false,
    }).then((chart) => {
      _navChart = chart;
    });
  } else {
    renderLineChart(host, pts, {
      color: primary,
      emptyText: "暂无净值曲线 · 请先在交易执行页运行纸面",
      disableZoom: false,
    }).then((chart) => {
      _navChart = chart;
    });
  }
}

function renderDrawdownChart(drawdownPoints) {
  const host = document.getElementById("dashboard-drawdown-chart");
  const statsEl = document.getElementById("dashboard-drawdown-stats");
  if (!host) return null;
  if (!drawdownPoints || !drawdownPoints.length) {
    if (statsEl) statsEl.textContent = "—";
    host.innerHTML = `<div class="dashboard-empty">暂无回撤数据</div>`;
    return null;
  }
  const pts = drawdownPoints
    .map((p) => ({
      time: p.time || p.date,
      value: Number(p.value),
    }))
    .filter((p) => p.time && Number.isFinite(p.value));
  if (!pts.length) {
    host.innerHTML = `<div class="dashboard-empty">暂无回撤数据</div>`;
    return null;
  }
  const minVal = Math.min(...pts.map((p) => p.value));
  if (statsEl) statsEl.textContent = `最大回撤 ${fmtPct(minVal)} · ${pts.length} 点`;
  return renderLineChart(host, pts, {
    color: chartPrimary(),
    emptyText: "暂无回撤数据",
    disableZoom: false,
    zeroLine: true,
  }).then((chart) => {
    _drawdownChart = chart;
    return chart;
  });
}

function renderVarHistogram(histData) {
  const host = document.getElementById("dashboard-var-chart");
  const statsEl = document.getElementById("dashboard-var-stats");
  if (!host) return null;
  const buckets = (histData && (histData.histogram || histData.buckets)) || [];
  if (!histData || histData.ok === false || !buckets.length) {
    if (statsEl) statsEl.textContent = histData?.message || "—";
    host.innerHTML = `<div class="dashboard-empty">${histData?.message || "暂无VaR历史数据"}</div>`;
    return null;
  }
  const maxCount = Math.max(...buckets.map((b) => Number(b.count) || 0), 1);
  const var95 = histData.var_95;
  const var99 = histData.var_99;
  const cvar95 = histData.cvar_95;
  if (statsEl) {
    statsEl.textContent = `VaR95 ${fmtPct(var95)} · CVaR95 ${fmtPct(cvar95)} · n=${histData.sample_count ?? buckets.length}`;
  }
  const bars = buckets
    .map((b) => {
      const count = Number(b.count) || 0;
      const h = Math.max(2, (count / maxCount) * 100);
      const mid = ((Number(b.range_low) || 0) + (Number(b.range_high) || 0)) / 2;
      const isTail95 = var95 != null && mid <= -Math.abs(Number(var95));
      const isTail99 = var99 != null && mid <= -Math.abs(Number(var99));
      const cls = isTail99 ? "is-tail99" : isTail95 ? "is-tail95" : "";
      const title = `${b.label || `${b.range_low}% ~ ${b.range_high}%`} · ${count}`;
      return `<div class="dashboard-var-bar ${cls}" style="height:${h}%" title="${escapeHtml(title)}"></div>`;
    })
    .join("");
  host.innerHTML =
    `<div class="dashboard-var-hist" aria-label="收益直方图">${bars}</div>` +
    `<p class="dashboard-var-footnote">橙/红柱≈VaR 尾部 · 样本 ${escapeHtml(String(histData.sample_count ?? "—"))}</p>`;
  _varChart = { host };
  return _varChart;
}

async function loadDashboardData(range = "30", showBenchmark = readBenchmarkFlag()) {
  const meta = document.getElementById("dashboard-meta");
  const updated = document.getElementById("dashboard-updated");
  if (meta) meta.setAttribute("aria-busy", "true");
  if (updated) {
    updated.dataset.state = "loading";
    updated.textContent = "加载中";
    updated.setAttribute("aria-busy", "true");
  }

  // A4: fetchDash / unwrap 见 dashboard_api.js

  try {
    const benchParam = showBenchmark ? "hs300" : "none";
    const [
      marketData,
      marketContext,
      kpis,
      riskData,
      navData,
      sectors,
      signals,
      allocation,
      exposure,
      drawdownData,
      varData,
    ] = await fetchDashboardBundle({ range, benchmark: benchParam });

    try {
      renderMarketOverview(marketData);
    } catch (e) {
      console.warn("[Dashboard] market overview", e);
    }
    try {
      renderMarketContext(marketContext);
    } catch (e) {
      console.warn("[Dashboard] market context", e);
      const host = document.getElementById("dashboard-market-context");
      if (host) {
        host.innerHTML = `<div class="dashboard-market-context-empty">盘前上下文渲染失败 · ${escapeHtml(
          String(e.message || e)
        )}</div>`;
      }
    }

    const kpiHost = document.getElementById("dashboard-kpi-cards");
    if (kpiHost && kpis.ok !== false) {
      const items = [
        {
          key: "today_return",
          label: "今日收益",
          value: kpis.today_return,
          formatted: fmtPct(kpis.today_return),
          sub:
            kpis.today_return_basis === "reset"
              ? "自回零"
              : kpis.today_return_basis === "prev_nav"
                ? "vs 昨收账本"
                : "vs 昨收",
          sparkline: kpis.sparkline_today,
          direction: kpis.today_return,
          semantic: "return",
        },
        {
          key: "total_return",
          label: "累计收益",
          value: kpis.total_return,
          formatted: fmtPct(kpis.total_return),
          sub: kpis.period || "全部",
          sparkline: kpis.sparkline_total,
          direction: kpis.total_return,
          semantic: "return",
        },
        {
          key: "sharpe",
          label: "Sharpe",
          value: kpis.sharpe,
          formatted: fmtNum(kpis.sharpe, 2),
          sub: "年化",
          sparkline: kpis.sparkline_sharpe,
          direction: kpis.sharpe - 1,
          semantic: "quality",
        },
        {
          key: "max_dd",
          label: "最大回撤",
          value: kpis.max_drawdown,
          formatted: fmtPct(kpis.max_drawdown),
          sub: "历史极值",
          sparkline: kpis.sparkline_dd,
          direction:
            kpis.max_drawdown != null ? -Math.abs(kpis.max_drawdown) : null,
          semantic: "return",
        },
        {
          key: "win_rate",
          label: "胜率",
          value: kpis.win_rate,
          formatted: fmtPct(kpis.win_rate),
          sub: (kpis.trade_count || 0) + " 笔",
          sparkline: kpis.sparkline_winrate,
          direction: kpis.win_rate - 50,
          semantic: "quality",
        },
      ];
      kpiHost.innerHTML = items.map(kpiCardHtml).join("");
      renderSparklines();
    } else if (kpiHost) {
      kpiHost.innerHTML = `<div class="dashboard-empty dashboard-empty--span">暂无 KPI 数据 · 请先在交易执行页运行纸面</div>`;
    }

    try {
      renderRiskMetrics(riskData);
    } catch (e) {
      console.warn("[Dashboard] risk", e);
    }
    try {
      renderRiskStrip(riskData, exposure, varData, kpis, drawdownData);
    } catch (e) {
      console.warn("[Dashboard] risk-strip", e);
    }

    try {
      if (navData && navData.points) {
        renderNavChart(
          navData.points,
          range,
          showBenchmark ? navData.benchmark || navData.benchmark_points || [] : []
        );
      }
    } catch (e) {
      console.warn("[Dashboard] nav", e);
    }

    try {
      if (drawdownData && drawdownData.points) {
        renderDrawdownChart(drawdownData.points);
      }
    } catch (e) {
      console.warn("[Dashboard] drawdown", e);
    }

    try {
      if (varData && varData.ok !== false) {
        renderVarHistogram(varData);
      } else {
        const host = document.getElementById("dashboard-var-chart");
        const statsEl = document.getElementById("dashboard-var-stats");
        if (statsEl) statsEl.textContent = "—";
        if (host) {
          host.innerHTML = `<div class="dashboard-empty">${varData?.message || "暂无VaR历史数据"}</div>`;
        }
      }
    } catch (e) {
      console.warn("[Dashboard] var", e);
    }

    try {
      renderSectorHeatmap(sectors?.sectors || []);
    } catch (e) {
      console.warn("[Dashboard] sector", e);
    }
    try {
      renderLeaderboard(kpis?.strategies || []);
    } catch (e) {
      console.warn("[Dashboard] leaderboard", e);
    }
    try {
      renderSignals(signals?.signals || []);
    } catch (e) {
      console.warn("[Dashboard] signals", e);
    }
    try {
      renderAllocation(allocation);
    } catch (e) {
      console.warn("[Dashboard] allocation", e);
    }
    try {
      renderFactorExposure(exposure);
    } catch (e) {
      console.warn("[Dashboard] exposure", e);
    }

    const t = new Date().toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    if (meta) {
      meta.textContent = "纸面组合 · 市场 · 风险 · 信号";
      meta.setAttribute("aria-busy", "false");
    }
    if (updated) {
      updated.dataset.state = "ready";
      updated.textContent = t;
      updated.setAttribute("aria-busy", "false");
    }
  } catch (err) {
    console.error("[Dashboard] load failed", err);
    if (meta) {
      meta.textContent = "加载失败: " + (err.message || err);
      meta.setAttribute("aria-busy", "false");
    }
    if (updated) {
      updated.dataset.state = "error";
      updated.textContent = "失败";
      updated.setAttribute("aria-busy", "false");
    }
  }
}

function bindEvents() {
  const refreshBtn = document.getElementById("dashboard-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      loadDashboardData(readActiveRange(), readBenchmarkFlag());
    });
  }

  const rangeTabs = document.getElementById("dashboard-nav-range");
  if (rangeTabs) {
    const tabs = () => [...rangeTabs.querySelectorAll("[role='tab']")];
    rangeTabs.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-range]");
      if (!btn) return;
      setRangeTabState(rangeTabs, btn);
      loadDashboardData(btn.dataset.range, readBenchmarkFlag());
    });
    rangeTabs.addEventListener("keydown", (e) => {
      const list = tabs();
      if (!list.length) return;
      const i = list.findIndex((b) => b.classList.contains("is-active"));
      let next = -1;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % list.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
        next = (i - 1 + list.length) % list.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = list.length - 1;
      if (next < 0) return;
      e.preventDefault();
      setRangeTabState(rangeTabs, list[next]);
      list[next].focus();
      loadDashboardData(list[next].dataset.range, readBenchmarkFlag());
    });
  }

  const benchToggle = document.getElementById("dashboard-benchmark-toggle");
  if (benchToggle) {
    benchToggle.addEventListener("change", () => {
      loadDashboardData(readActiveRange(), readBenchmarkFlag());
    });
  }
}

export function initDashboard(ctx) {
  if (_initiated) return;
  _initiated = true;
  void V;
  void ctx;
  const rangeTabs = document.getElementById("dashboard-nav-range");
  if (rangeTabs) {
    const active =
      rangeTabs.querySelector("[data-range].is-active") ||
      rangeTabs.querySelector("[data-range]");
    if (active) setRangeTabState(rangeTabs, active);
  }
  bindEvents();
  loadDashboardData("30", readBenchmarkFlag());

  setInterval(() => {
    loadDashboardData(readActiveRange(), readBenchmarkFlag());
  }, 90000);
}
