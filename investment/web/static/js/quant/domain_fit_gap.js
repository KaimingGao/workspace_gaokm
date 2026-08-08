/**
 * 研究枢纽 · 纸面拟合（常驻，不依赖刚跑完 TopK）。
 *
 * 用途：对比两条独立收益曲线——历史回测日收益（/replay 的 Top-K 组合回测引擎产出）
 * 与纸面交易实际持仓日收益（/follow 页 paper.json），输出 Corr（相关系数）、
 * TE（跟踪误差）、Δpp（日收益缺口），并标记 |gap_pp|≥1% 的显著缺口日。
 * 用于识别回测过拟合、执行延迟、未建模成本/规则等问题。
 * 轻量诊断工具，非完整回测，聚焦回测与纸面的对齐度。
 */
import { renderMultiLineChart, renderLineChart } from "../lw_charts.js";
import { syncOverviewFromFitGap } from "./factor_corr_ui.js";

export function installFitGapHub(ctx) {
  const { escapeHtml, setQuantMeta, on, researchGridHtml } = ctx;
  const esc = escapeHtml;

  function setStatus(text, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("quant-fit-gap-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok);
  }

  function toChartPts(rows, key) {
    return (rows || [])
      .map((g) => {
        const t = String(g.date || "").slice(0, 10);
        const v = Number(g[key]);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(t) || !Number.isFinite(v)) return null;
        return { time: t, value: v };
      })
      .filter(Boolean);
  }

  async function paintFitGapCharts(dd) {
    const dualHost = document.getElementById("quant-fit-gap-dual");
    const gapHost = document.getElementById("quant-fit-gap-gap");
    const dualWrap = document.getElementById("quant-fit-gap-dual-wrap");
    const gapWrap = document.getElementById("quant-fit-gap-gap-wrap");
    const series = dd && (dd.day_series || []);
    const paper = toChartPts(series, "paper_ret_pct");
    const bt = toChartPts(series, "bt_ret_pct");
    const gap = toChartPts(series, "gap_pp");
    const hasDual = paper.length >= 2 || bt.length >= 2;
    if (dualWrap) dualWrap.hidden = !hasDual;
    if (gapWrap) gapWrap.hidden = gap.length < 2;
    if (hasDual && dualHost) {
      const lines = [];
      if (paper.length >= 2) {
        lines.push({
          label: "纸面日收益%",
          color: "#2563eb",
          lineWidth: 2,
          points: paper,
        });
      }
      if (bt.length >= 2) {
        lines.push({
          label: "回测日收益%",
          color: "#059669",
          lineWidth: 2,
          points: bt,
        });
      }
      await renderMultiLineChart(dualHost, lines, {
        emptyText: "对齐日收益不足",
        disableZoom: true,
        zeroLine: true,
      });
    }
    if (gap.length >= 2 && gapHost) {
      await renderLineChart(gapHost, gap, {
        emptyText: "缺口序列不足",
        disableZoom: true,
        zeroLine: true,
        color: "#c2410c",
        mainLabel: "Δpp",
      });
    }
  }

  function renderDayDiff(dd) {
    if (!dd || !dd.ok) return "";
    const rzBits = [
      `对齐 ${dd.aligned_days ?? 0}`,
      `纸面 ${dd.paper_days ?? 0}`,
      `回测 ${dd.backtest_days ?? 0}`,
      `纸面独有 ${dd.paper_only_days ?? 0}`,
      `回测独有 ${dd.bt_only_days ?? 0}`,
    ];
    if (dd.common_first && dd.common_last) {
      rzBits.push(`${dd.common_first}→${dd.common_last}`);
    }
    let html =
      `<p class="quant-trades-caption">同窗日 Diff · ${esc(rzBits.join(" · "))}</p>`;

    const seriesLen = (dd.day_series || []).length;
    if (seriesLen >= 2) {
      html +=
        `<div class="quant-chart-wrap quant-fit-gap-chart" id="quant-fit-gap-dual-wrap">` +
        `<div class="quant-chart-axis-head">` +
        `<span class="quant-chart-title">纸面 vs 回测 · 日收益%</span>` +
        `<span class="quant-chart-axis-hint">蓝=纸面 · 绿=回测 · 零轴=当日持平</span>` +
        `</div>` +
        `<div id="quant-fit-gap-dual" class="quant-chart-host quant-fit-gap-dual-host" aria-label="拟合双曲线"></div>` +
        `</div>` +
        `<div class="quant-chart-wrap quant-fit-gap-chart" id="quant-fit-gap-gap-wrap">` +
        `<div class="quant-chart-axis-head">` +
        `<span class="quant-chart-title">日缺口 Δpp（纸面−回测）</span>` +
        `<span class="quant-chart-axis-hint">正=纸面当日相对回测更高</span>` +
        `</div>` +
        `<div id="quant-fit-gap-gap" class="quant-chart-host quant-fit-gap-gap-host" aria-label="缺口曲线"></div>` +
        `</div>`;
    }

    const gaps = dd.day_gaps || [];
    if (gaps.length) {
      html +=
        `<p class="quant-trades-caption">|Δ| 最大样本</p>` +
        (researchGridHtml
          ? researchGridHtml(
              [
                { id: "date", label: "日", widthPct: 22 },
                { id: "paper_ret_pct", label: "纸面%", widthPct: 18, center: true },
                { id: "bt_ret_pct", label: "回测%", widthPct: 18, center: true },
                { id: "gap_pp", label: "Δpp", widthPct: 18, center: true },
              ],
              gaps.map((g) => ({
                date: g.date || "—",
                paper_ret_pct:
                  g.paper_ret_pct != null ? Number(g.paper_ret_pct).toFixed(2) : "—",
                bt_ret_pct:
                  g.bt_ret_pct != null ? Number(g.bt_ret_pct).toFixed(2) : "—",
                gap_pp: g.gap_pp != null ? Number(g.gap_pp).toFixed(2) : "—",
                isWarn: Math.abs(Number(g.gap_pp) || 0) >= 1,
              })),
              (col, d) => esc(d[col.id] ?? "—"),
              {
                emptyText: "无日收益差样本",
                rowClass: (d) => (d.isWarn ? "down" : ""),
              }
            )
          : "") +
        `<p class="quant-sub">${esc(dd.note || "")}</p>`;
    } else {
      const po = (dd.paper_only_sample || []).slice(-6).join(", ");
      const bo = (dd.bt_only_sample || []).slice(-6).join(", ");
      html += `<p class="quant-attr-note">纸面独有样例：${esc(po || "—")} · 回测独有样例：${esc(
        bo || "—"
      )}</p>`;
    }
    return html;
  }

  function renderPanel(data) {
    const el = document.getElementById("quant-fit-gap-hub");
    if (!el) return;
    if (!data || !data.ok) {
      el.innerHTML = `<p class="quant-attr-note">${esc(
        (data && data.error) || "无法加载拟合落差"
      )}</p>`;
      syncOverviewFromFitGap(data || { ok: false, error: "无法加载拟合落差" });
      return;
    }
    const hints = data.hints || [];
    const rz = data.realization || {};
    const corr =
      rz.corr != null && Number.isFinite(Number(rz.corr))
        ? Number(rz.corr).toFixed(3)
        : "—";
    const te =
      rz.tracking_error_pct != null
        ? `${Number(rz.tracking_error_pct).toFixed(2)}%`
        : "—";
    syncOverviewFromFitGap(data);
    let html =
      `<div class="quant-metric-strip">` +
      `<span>Corr <b>${esc(corr)}</b></span>` +
      `<span>TE <b>${esc(te)}</b></span>` +
      `<span>状态 ${esc(rz.status || "—")}</span>` +
      `<span>warn ${esc(String(data.warn_count ?? 0))}</span>` +
      `</div>`;

    if (researchGridHtml) {
      html +=
        `<p class="quant-trades-caption">启发式落差</p>` +
        researchGridHtml(
          [
            { id: "level", label: "级别", widthPct: 14, center: true },
            { id: "code", label: "码", widthPct: 22 },
            { id: "message", label: "说明", flex: true },
          ],
          hints.map((h) => ({
            level: h.level || "info",
            code: h.code || "",
            message: h.message || "",
            isWarn: (h.level || "") === "warn",
          })),
          (col, d) => esc(d[col.id] ?? "—"),
          {
            emptyText: "无归因项",
            rowClass: (d) => (d.isWarn ? "down" : ""),
          }
        );
    }

    html += renderDayDiff(data.day_diff);
    html += `<p class="quant-sub">${esc(data.note || "")} · 完整回测见 <a href="/replay">/replay</a></p>`;
    el.innerHTML = html;
    // 图表宿主需在 DOM 内再画
    requestAnimationFrame(() => {
      paintFitGapCharts(data.day_diff || {}).catch(() => {});
    });
  }

  async function refresh() {
    setStatus("拟合计算中…", { busy: true });
    if (setQuantMeta) setQuantMeta("纸面拟合计算中…", { busy: true });
    try {
      const res = await fetch("/api/ops/fit-gap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      renderPanel(data);
      const dd = data.day_diff || {};
      const sum = document.getElementById("quant-fit-gap-fold-summary");
      const rz = data.realization || {};
      const corr =
        rz.corr != null && Number.isFinite(Number(rz.corr))
          ? Number(rz.corr).toFixed(3)
          : "—";
      if (sum) {
        sum.textContent = `Corr ${corr} · 对齐 ${dd.aligned_days ?? 0} · warn ${
          data.warn_count ?? 0
        }`;
      }
      setStatus(
        `Corr ${corr} · 对齐 ${dd.aligned_days ?? 0} · warn ${data.warn_count ?? 0}`,
        { ok: true }
      );
      if (setQuantMeta) {
        setQuantMeta(`拟合 · Corr ${corr}`, { busy: false });
      }
      return data;
    } catch (err) {
      setStatus(`拟合失败：${String(err.message || err)}`, { error: true });
      if (setQuantMeta) {
        setQuantMeta(`拟合失败：${String(err.message || err)}`, {
          busy: false,
          error: true,
        });
      }
      throw err;
    }
  }

  on("quant-fit-gap-refresh", "click", async (e) => {
    e.preventDefault();
    try {
      await refresh();
    } catch (_) {
      /* status set */
    }
  });

  // 进页直接拉一次（不再依赖 details 展开）
  refresh().catch(() => {});

  return { refresh, renderPanel };
}
