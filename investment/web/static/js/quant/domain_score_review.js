/**
 * 双层 ŷ 复盘：ŷ_EOD 方向 vs 前瞻收益；副轴 ŷ_τ 验收。
 */
import { paintYhatScatter, paintHitSparkline, mountCalibrationCurve } from "./yhat_viz.js";
import {
  renderYCheckBucketBars,
  paintYDisagreeScatter,
} from "../y_path_viz.js?v=p1169";
import { syncOverviewFromScoreReview, setProStatusChip, factorCN } from "./factor_corr_ui.js";

export function installScoreReview(ctx) {
  const { els, escapeHtml, setQuantMeta, setBusyText, on } = ctx;
  const esc = escapeHtml;
  let panelLoadToken = 0;
  /** 当前复盘决策日（由冻结 chip 选择） */
  let selectedAsOf = null;
  let reviewGen = 0;

  function tagLabel(tag) {
    const map = {
      hit: "命中",
      factor_fade: "因子失效",
      idiosyncratic: "个股特异",
      model_tilt: "模型偏置",
      data_thin: "数据不足",
      no_direction: "无方向",
    };
    return map[tag] || tag || "—";
  }

  function readAsOf() {
    const v = selectedAsOf != null ? String(selectedAsOf).trim() : "";
    return v || null;
  }

  function setAsOf(asOf) {
    const d = String(asOf || "").trim();
    selectedAsOf = d || null;
    markActiveLedgerChip(selectedAsOf || "");
    return selectedAsOf;
  }

  function readHorizon() {
    const el = document.getElementById("quant-score-review-horizon");
    const n = Number(el && el.value);
    return Number.isFinite(n) && n >= 1 ? n : 3;
  }

  function setStatus(text, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("quant-score-review-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok);
  }

  function setCalStatus(text, { busy = false, error = false, ok = false } = {}) {
    const el = document.getElementById("quant-score-cal-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
    el.classList.toggle("is-error", !!error);
    el.classList.toggle("is-ok", !!ok);
  }

  function calPanelEl() {
    return document.getElementById("quant-score-cal-panel");
  }

  function syncHorizonSelect(horizon) {
    const el = document.getElementById("quant-score-review-horizon");
    if (!el || horizon == null) return;
    const v = String(horizon);
    if (el.value !== v) el.value = v;
  }

  function renderSummary(data) {
    const box = document.getElementById("quant-score-review-summary");
    if (!box) return;
    if (data.empty) {
      box.innerHTML = `<p class="quant-attr-note">${esc(
        data.note || "无账本"
      )}</p>`;
      syncOverviewFromScoreReview(data);
      setProStatusChip("quant-pro-review-status", "warn", "无账本");
      renderTauShadow(data && data.tau_shadow);
      renderNowcastShadow(data && data.nowcast_shadow);
      return;
    }
    const s = data.summary || {};
    const hit =
      s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
    const fb =
      data.horizon_fallback_from != null
        ? `<p class="quant-attr-note">h=${esc(
            String(data.horizon_fallback_from)
          )} 实现收益未齐 · 已改用 h=${esc(
            String(data.horizon_days ?? 1)
          )}</p>`
        : "";
    const thinNote =
      s.hit_rate == null && Number(s.data_thin || 0) > 0
        ? `<p class="quant-attr-note">${esc(
            s.blame_line ||
              "薄样本：日线未覆盖前瞻收益。可改小 Horizon / 更早决策日 / 刷新日线后回填"
          )}</p>`
        : "";
    box.innerHTML =
      `<div class="quant-metric-strip">` +
      `<span>as_of <b>${esc(data.as_of || "—")}</b></span>` +
      `<span>h=${esc(String(data.horizon_days ?? "—"))}</span>` +
      `<span>命中 <b>${esc(hit)}</b> (${esc(String(s.hits ?? 0))}/${esc(
        String(s.n_scored ?? 0)
      )})</span>` +
      `<span>错多 ${esc(String(s.wrong_long ?? 0))}</span>` +
      `<span>错空 ${esc(String(s.wrong_short ?? 0))}</span>` +
      `<span>账本 ${esc(String(data.n_ledger ?? 0))} 只</span>` +
      (Number(s.data_thin || 0) > 0
        ? `<span>薄样本 ${esc(String(s.data_thin))}</span>`
        : "") +
      `</div>` +
      renderYCheckStrip(s.by_y_check) +
      fb +
      thinNote +
      (s.blame_line && s.hit_rate != null
        ? `<p class="quant-attr-note">${esc(s.blame_line)}</p>`
        : "") +
      (data.refit_hint
        ? `<p class="quant-attr-note">${esc(data.refit_hint)}</p>`
        : "") +
      (data.note && s.hit_rate == null
        ? `<p class="quant-attr-note">${esc(String(data.note).slice(0, 160))}</p>`
        : "");
    renderTauShadow(data.tau_shadow);
    renderNowcastShadow(data.nowcast_shadow);
    syncOverviewFromScoreReview(data);
    if (s.hit_rate != null && Number.isFinite(Number(s.hit_rate))) {
      const pct = Number(s.hit_rate) * 100;
      setProStatusChip(
        "quant-pro-review-status",
        pct >= 55 ? "ok" : "warn",
        `命中 ${pct.toFixed(0)}%`
      );
    } else {
      const thin = Number(s.data_thin || 0);
      setProStatusChip(
        "quant-pro-review-status",
        "warn",
        thin > 0 ? `薄样本 ${thin}` : s.blame_line || "样本不足"
      );
    }
  }

  function renderYCheckStrip(rows) {
    if (!Array.isArray(rows) || !rows.length) return "";
    const label = {
      ok: "校验通过",
      conflict: "双头分歧",
      low_conf: "低置信",
      missing_tau: "缺τ",
      single_head: "单头",
    };
    const parts = rows
      .slice(0, 6)
      .map((r) => {
        const ck = String(r.check || "");
        const hit =
          r.hit_rate != null
            ? `${(Number(r.hit_rate) * 100).toFixed(0)}%`
            : "—";
        return `<span title="Y·EOD 校验分桶 · EOD 方向命中">${esc(
          label[ck] || ck
        )} <b>${esc(hit)}</b> (${esc(String(r.hits ?? 0))}/${esc(
          String(r.n ?? 0)
        )})</span>`;
      })
      .join("");
    return (
      `<div class="quant-metric-strip quant-y-check-strip">` +
      `<span class="quant-y-check-strip-label">Y校验</span>` +
      parts +
      `</div>` +
      renderYCheckBucketBars(rows)
    );
  }

  function renderYDisagreeViz(data) {
    const wrap = document.getElementById("quant-score-review-ydisagree-wrap");
    const canvas = document.getElementById("quant-score-review-ydisagree");
    if (!wrap || !canvas) return;
    const pts = (data.scored_rows || []).filter(
      (p) => p.y_disagree != null && Number.isFinite(Number(p.y_disagree))
    );
    wrap.hidden = false;
    if (data.empty || pts.length < 2) {
      canvas.hidden = true;
      setVizMeta(
        "quant-score-review-ydisagree-meta",
        data.empty ? "无账本样本" : "无 |Δ| 字段或样本不足"
      );
      return;
    }
    canvas.hidden = false;
    requestAnimationFrame(() => {
      const pack = paintYDisagreeScatter(canvas, pts, {
        onPoint: (p) => {
          if (p && p.code) openStockPanel(p.code, p.name || "");
        },
      });
      if (pack && pack.n) {
        setVizMeta(
          "quant-score-review-ydisagree-meta",
          `双头分歧 |Δ| vs 实现 · n=${pack.n} · 色=校验态 · 点击打开单票`
        );
      }
    });
  }

  function renderTauShadow(tau) {
    const box = document.getElementById("quant-score-review-tau");
    if (!box) return;
    if (!tau || tau.success === false) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    const ic =
      tau.tau_ic_spearman != null
        ? Number(tau.tau_ic_spearman).toFixed(2)
        : "—";
    const hit =
      tau.tau_sign_hit_rate != null
        ? `${(Number(tau.tau_sign_hit_rate) * 100).toFixed(0)}%`
        : "—";
    const vs = tau.vs_eod || {};
    const j =
      vs.jaccard != null ? Number(vs.jaccard).toFixed(2) : "—";
    const ov =
      vs.overlap != null
        ? `${vs.overlap}/${vs.n_a ?? "—"}∩${vs.n_b ?? "—"}`
        : "—";
    const n = tau.tau_n != null ? String(tau.tau_n) : "—";
    box.hidden = false;
    box.innerHTML =
      `<div class="quant-metric-strip quant-tau-strip" title="A2：ŷ_τ vs open→close；影子簿 vs EOD 重叠">` +
      `<span><b>ŷ_τ</b> 验收</span>` +
      `<span>τ <b>${esc(String(tau.as_of_tau || tau.tau || "open"))}</b></span>` +
      `<span>IC <b>${esc(ic)}</b> (n=${esc(n)})</span>` +
      `<span>命中 <b>${esc(hit)}</b></span>` +
      `<span>影子重叠 Jaccard <b>${esc(j)}</b> · ${esc(ov)}</span>` +
      (tau.shadow_exists
        ? `<span>影子簿 ${esc(String(tau.shadow_n ?? "—"))} 只</span>`
        : `<span class="quant-attr-note">无 τ 影子成员快照</span>`) +
      `</div>` +
      `<p class="quant-attr-note">标签 ${esc(
        String(tau.y_spec_tau || "close[T]/open[T]-1")
      )} · 与 EOD 分栏对账 · 禁止混用全日相对昨收验收分钟头</p>`;
  }

  function renderNowcastShadow(nc) {
    const box = document.getElementById("quant-score-review-nowcast");
    if (!box) return;
    if (!nc || nc.success === false) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    const ic =
      nc.nowcast_ic_spearman != null
        ? Number(nc.nowcast_ic_spearman).toFixed(2)
        : "—";
    const hit =
      nc.nowcast_sign_hit_rate != null
        ? `${(Number(nc.nowcast_sign_hit_rate) * 100).toFixed(0)}%`
        : "—";
    const nord =
      nc.nordhaus_revision_slope != null &&
      Number.isFinite(Number(nc.nordhaus_revision_slope))
        ? Number(nc.nordhaus_revision_slope).toFixed(2)
        : "—";
    const vs = nc.vs_eod || {};
    const j =
      vs.jaccard != null ? Number(vs.jaccard).toFixed(2) : "—";
    const ov =
      vs.overlap != null
        ? `${vs.overlap}/${vs.n_a ?? "—"}∩${vs.n_b ?? "—"}`
        : "";
    const n = nc.nowcast_n != null ? String(nc.nowcast_n) : "—";
    const shadowNote = nc.shadow_exists
      ? `<span>影子簿 ${esc(String(nc.shadow_n ?? "—"))} 只</span>`
      : vs.jaccard != null
        ? `<span class="quant-attr-note">无成员快照 · 重叠按当日账本 Top-K 估</span>`
        : `<span class="quant-attr-note">无 nowcast 影子成员快照</span>`;
    box.hidden = false;
    box.innerHTML =
      `<div class="quant-metric-strip quant-tau-strip" title="N3：ŷ_nowcast vs 涨跌（昨收口径，同 ŷ_trade）；Nordhaus≈0 才考虑升主排序">` +
      `<span><b>ŷ_nowcast</b> 验收</span>` +
      `<span>IC <b>${esc(ic)}</b> (n=${esc(n)})</span>` +
      `<span>命中 <b>${esc(hit)}</b></span>` +
      `<span>Nordhaus <b>${esc(nord)}</b></span>` +
      `<span>影子重叠 Jaccard <b>${esc(j)}</b>${ov ? ` · ${esc(ov)}` : ""}</span>` +
      shadowNote +
      `</div>` +
      `<p class="quant-attr-note">标签 ${esc(
        String(nc.y_spec_nowcast || "close[T]/close[T-1]−1（与 ŷ_trade / 涨跌同一口径）")
      )} · 影子对照 · 默认不改主排序</p>`;
  }

  function setVizMeta(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text || "";
  }

  function renderScatter(data) {
    const wrap = document.getElementById("quant-score-review-scatter-wrap");
    const canvas = document.getElementById("quant-score-review-scatter");
    if (!wrap || !canvas) return;
    const pts = data.scored_rows || [];
    wrap.hidden = false;
    if (data.empty || pts.length < 2) {
      canvas.hidden = true;
      setVizMeta(
        "quant-score-review-scatter-meta",
        data.empty ? "无账本样本" : "样本不足（需 ≥2 点）"
      );
      return;
    }
    canvas.hidden = false;
    requestAnimationFrame(() => {
      const pack = paintYhatScatter(canvas, pts, {
        tagLabel,
        onPoint: (p) => {
          if (p && p.code) openStockPanel(p.code, p.name || "");
        },
      });
      if (pack && pack.n) {
        const hit =
          pack.hit_rate != null
            ? `${(pack.hit_rate * 100).toFixed(0)}%`
            : "—";
        setVizMeta(
          "quant-score-review-scatter-meta",
          `每点一只 · n=${pack.n} · 命中 ${hit} · 绿对红错 · 悬停看明细`
        );
      }
    });
  }

  async function renderHitSparkline(horizon) {
    const wrap = document.getElementById("quant-score-review-hit-wrap");
    const canvas = document.getElementById("quant-score-review-hit-spark");
    if (!wrap || !canvas) return;
    wrap.hidden = false;
    try {
      const q = new URLSearchParams({
        horizon_days: String(horizon || 3),
        limit: "20",
        autofill: "false",
      });
      const res = await fetch(`/api/quant/score-review/hit-series?${q}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        canvas.hidden = true;
        setVizMeta("quant-score-review-hit-meta", "暂无命中率序列");
        return;
      }
      const pts = data.points || [];
      if (pts.length < 2) {
        canvas.hidden = true;
        setVizMeta("quant-score-review-hit-meta", "样本不足（需 ≥2 日）");
        return;
      }
      canvas.hidden = false;
      requestAnimationFrame(() => {
        const pack = paintHitSparkline(canvas, pts);
        if (pack) {
          const last =
            pack.last != null ? `${(pack.last * 100).toFixed(0)}%` : "—";
          const mean =
            pack.mean != null ? `${(pack.mean * 100).toFixed(0)}%` : "—";
          setVizMeta(
            "quant-score-review-hit-meta",
            `${pack.n} 日 · 近 ${last} · μ ${mean} · 虚线 50% / μ`
          );
        }
      });
    } catch (_) {
      canvas.hidden = true;
      setVizMeta("quant-score-review-hit-meta", "命中率加载失败");
    }
  }

  function facLabel(r) {
    return (
      r.dominant_factor_label ||
      r.factor_label ||
      factorCN(r.dominant_factor || r.factor) ||
      "—"
    );
  }

  function renderWrongTable(data) {
    const box = document.getElementById("quant-score-review-table");
    if (!box) return;
    const scored = data.scored_rows || [];
    if (data.empty || !scored.length) {
      box.innerHTML = data.empty
        ? ""
        : `<p class="quant-attr-note">无已回填样本（或收益尚未回填）。</p>`;
      return;
    }
    const icByCode = {};
    for (const r of data.wrong_rows || []) {
      const c = String(r.code || "").trim();
      if (c && r.factor_ic_day != null) icByCode[c] = r.factor_ic_day;
    }
    const nHit = scored.filter((r) => r.hit === true).length;
    const nMiss = scored.filter((r) => r.hit === false).length;
    const rows = scored.slice().sort((a, b) => {
      const ah = a.hit === true ? 1 : a.hit === false ? 0 : 2;
      const bh = b.hit === true ? 1 : b.hit === false ? 0 : 2;
      if (ah !== bh) return ah - bh; // 错票优先，便于对照
      return Math.abs(Number(b.abs_err) || 0) - Math.abs(Number(a.abs_err) || 0);
    });
    const body = rows
      .slice(0, 80)
      .map((r) => {
        const y = r.yhat != null ? Number(r.yhat).toFixed(2) : "—";
        const yTau =
          r.yhat_tau != null ? Number(r.yhat_tau).toFixed(2) : "—";
        const ret = r.realized_h != null ? Number(r.realized_h).toFixed(2) : "—";
        const err = r.abs_err != null ? Number(r.abs_err).toFixed(2) : "—";
        const fac = facLabel(r);
        const code = String(r.code || "").trim();
        const icRaw = r.factor_ic_day != null ? r.factor_ic_day : icByCode[code];
        const ic = icRaw != null ? Number(icRaw).toFixed(3) : "—";
        const hitCls =
          r.hit === true ? " is-hit" : r.hit === false ? " is-miss" : "";
        const result =
          r.hit === true ? "命中" : r.hit === false ? "失手" : tagLabel(r.tag);
        const tauTip =
          r.hit_tau === true
            ? "τ命中"
            : r.hit_tau === false
              ? "τ失手"
              : "";
        return (
          `<tr class="quant-score-review-row is-clickable${hitCls}" data-code="${esc(code)}" data-name="${esc(
            r.name || ""
          )}" title="点击查看收盘 / 涨跌 / 冻结ŷ" role="button" tabindex="0">` +
          `<td class="col-code">${esc(code || "—")}</td>` +
          `<td class="col-name" title="${esc(r.name || "")}">${esc(r.name || "")}</td>` +
          `<td class="col-sector" title="${esc(r.sector || "")}">${esc(r.sector || "—")}</td>` +
          `<td class="col-group">${esc(r.cluster_label || "—")}</td>` +
          `<td class="num" title="ŷ_EOD">${esc(y)}</td>` +
          `<td class="num" title="ŷ_τ · ${esc(tauTip || "—")}">${esc(yTau)}</td>` +
          `<td class="num">${esc(ret)}</td>` +
          `<td class="num">${esc(err)}</td>` +
          `<td class="col-factor" title="${esc(r.dominant_factor || "")}">${esc(fac)}</td>` +
          `<td class="num">${esc(ic)}</td>` +
          `<td class="col-tag">${esc(result)}</td>` +
          `</tr>`
        );
      })
      .join("");
    box.innerHTML =
      `<p class="quant-trades-caption">复盘样本 · EOD 命中 ${esc(String(nHit))} / 失手 ${esc(
        String(nMiss)
      )} · 列 ŷ / ŷ_τ 分轴 · 点击行打开单票时间线</p>` +
      `<div class="quant-score-review-table-scroll">` +
      `<table class="quant-mini-table quant-score-review-mini quant-score-review-mini--wrong">` +
      `<thead><tr>` +
      `<th class="col-code">代码</th><th class="col-name">名称</th>` +
      `<th class="col-sector">行业</th><th class="col-group">组</th>` +
      `<th class="num" title="主轴 predicted_score">ŷ%</th>` +
      `<th class="num" title="副轴 predicted_score_tau">ŷ_τ%</th>` +
      `<th class="num">实现%</th><th class="num">|误差|</th>` +
      `<th class="col-factor">主导因子</th><th class="num">因子相关</th><th class="col-tag">结果</th>` +
      `</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  async function openStockPanel(code, name) {
    const wrap = document.getElementById("quant-score-review-panel-wrap");
    const host = document.getElementById("quant-score-review-panel-chart");
    const noteEl = document.getElementById("quant-score-review-panel-note");
    if (!wrap || !host) return;
    const c = String(code || "").trim();
    if (!c) return;
    wrap.hidden = false;
    wrap.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setVizMeta(
      "quant-score-review-panel-meta",
      `${name || c} · 加载中…`
    );
    const token = ++panelLoadToken;
    try {
      const q = new URLSearchParams({ code: c, lookback: "10" });
      const res = await fetch(`/api/quant/score-ledger/stock-panel?${q}`);
      const data = await res.json().catch(() => ({}));
      if (token !== panelLoadToken) return;
      if (!res.ok || !data.success) {
        setVizMeta(
          "quant-score-review-panel-meta",
          `${c} · ${data.detail || data.error || "加载失败"}`
        );
        return;
      }
      if (noteEl && data.note) {
        noteEl.textContent =
          `${data.note} · 左轴%（涨跌/ŷ）· 右轴收盘价。`;
      }
      const title = `${data.stock_name || data.name || name || c} · ${c}`;
      setVizMeta(
        "quant-score-review-panel-meta",
        `${title} · 收盘 ${data.n_close ?? 0} · 涨跌 ${data.n_change ?? 0} · ŷ ${data.n_yhat ?? 0} 日`
      );
      const V =
        (typeof window !== "undefined" && window.__ASSET_V__) || "dev";
      const { renderDualScaleOverlayChart } = await import(
        `../lw_charts.js?v=${encodeURIComponent(V)}`
      );
      await renderDualScaleOverlayChart(host, {
        price: {
          points: data.close_points || [],
          color: "#0f766e",
          label: "收盘",
        },
        pctSeries: [
          {
            points: data.change_points || [],
            color: "#c2410c",
            label: "日涨跌%",
            zeroLine: true,
          },
          {
            points: data.yhat_points || [],
            color: "#2563eb",
            label: "冻结ŷ%",
            zeroLine: true,
          },
        ],
        emptyText: "暂无近 10 日曲线",
        disableZoom: true,
      });
      document
        .querySelectorAll("#quant-score-review-table tr.is-active")
        .forEach((tr) => tr.classList.remove("is-active"));
      const active = document.querySelector(
        `#quant-score-review-table tr[data-code="${CSS.escape(c)}"]`
      );
      if (active) active.classList.add("is-active");
    } catch (err) {
      if (token !== panelLoadToken) return;
      setVizMeta(
        "quant-score-review-panel-meta",
        `${c} · ${String(err.message || err)}`
      );
    }
  }

  async function ensureDefaultAsOf() {
    if (readAsOf()) return;
    try {
      const pack = await loadLedgerIndex();
      setAsOf(pack.default_as_of || (pack.dates || [])[0] || "");
    } catch (_) {
      /* ignore */
    }
  }

  function shortDate(asOf) {
    const s = String(asOf || "");
    return s.length >= 10 ? s.slice(5) : s || "—";
  }

  async function loadLedgerIndex() {
    const res = await fetch("/api/quant/score-review/dates?limit=20");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    renderLedgerChips(data);
    return data;
  }

  function renderLedgerChips(data) {
    const wrap = document.getElementById("quant-score-review-ledgers");
    const row = document.getElementById("quant-score-review-ledger-chips");
    if (!wrap || !row) return;
    const entries = Array.isArray(data.entries)
      ? data.entries
      : (data.dates || []).map((d) => ({
          as_of: d,
          n_rows: null,
          has_outcomes: false,
        }));
    if (!entries.length) {
      wrap.hidden = true;
      row.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    const cur = readAsOf() || data.default_as_of || "";
    if (!readAsOf() && cur) selectedAsOf = cur;
    const freezeBtn = document.getElementById("quant-score-ledger-freeze");
    if (freezeBtn) {
      const cal = String(data.calendar_as_of || "").trim();
      const dates = Array.isArray(data.dates) ? data.dates.map(String) : [];
      freezeBtn.title =
        cal && dates.length && !dates.includes(cal)
          ? `按因子截止日冻结。上一交易日 ${cal} 尚无账本；日线未齐时会覆盖已有截止日，看起来像没变化`
          : "按因子截止日冻结分池簿 ŷ（通常为上一交易日；收盘后日线齐才可能是今日）";
    }
    row.innerHTML = entries
      .map((e) => {
        const asOf = String(e.as_of || "").trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return "";
        const n = e.n_rows != null ? Number(e.n_rows) : null;
        const filled = e.outcomes_filled != null ? Number(e.outcomes_filled) : 0;
        const useful = filled > 0;
        const immature = !!e.immature;
        const pending = !!e.pending_close && !useful;
        const needBar = String(e.need_bar_date || "").trim();
        const active = asOf === cur ? " is-active" : "";
        const ocCls = useful
          ? " has-outcomes"
          : immature || pending
            ? " is-immature"
            : " no-outcomes";
        const ocMark = useful
          ? ` · 已回填 ${filled}`
          : pending && needBar
            ? ` · 待 ${needBar.slice(5)} 收盘`
            : immature
              ? " · 未到期"
              : " · 未回填";
        const nTxt = n != null && Number.isFinite(n) ? `${n}只` : "—";
        const title = pending && needBar
          ? `${asOf} · ${nTxt} · h=1 需 ${needBar} 收盘后对账（盘中日线通常未入库）`
          : immature
            ? `${asOf} · ${nTxt} · 会话日账本，h 未到期，复盘请选更早决策日`
            : `${asOf} · ${nTxt}${ocMark} · 点击切换决策日`;
        return (
          `<div class="quant-score-ledger-chip-wrap${active}${
            immature || pending ? " is-immature" : ""
          }" role="listitem">` +
          `<button type="button" class="quant-score-ledger-chip${active}${ocCls}"` +
          ` data-asof="${esc(asOf)}" title="${esc(title)}">` +
          `<span class="chip-date">${esc(shortDate(asOf))}</span>` +
          `<span class="chip-meta">${esc(nTxt)}${
            useful ? " ✓" : pending ? " 待收盘" : immature ? " …" : ""
          }</span>` +
          `</button>` +
          `<button type="button" class="quant-score-ledger-chip-del"` +
          ` data-asof="${esc(asOf)}" data-nrows="${esc(String(n ?? ""))}"` +
          ` title="删除 ${esc(asOf)} 账本" aria-label="删除 ${esc(asOf)} 账本">×</button>` +
          `</div>`
        );
      })
      .join("");
  }

  function markActiveLedgerChip(asOf) {
    const row = document.getElementById("quant-score-review-ledger-chips");
    if (!row) return;
    const cur = String(asOf || "").trim();
    row.querySelectorAll(".quant-score-ledger-chip-wrap").forEach((wrap) => {
      const btn = wrap.querySelector(".quant-score-ledger-chip");
      const on = btn && btn.getAttribute("data-asof") === cur;
      wrap.classList.toggle("is-active", !!on);
      if (btn) btn.classList.toggle("is-active", !!on);
    });
  }

  async function selectLedgerAsOf(asOf) {
    const d = String(asOf || "").trim();
    if (!d) return;
    setAsOf(d);
    // 用户点选的决策日必须留下；薄样本只提示，不偷偷切回已回填日
    await runReview({ autofill: true, allowThinFallback: false });
  }

  async function deleteLedgerAsOf(asOf, { nRows } = {}) {
    const d = String(asOf || "").trim();
    if (!d) return;
    const nHint =
      nRows != null && String(nRows) !== "" ? `（约 ${nRows} 只）` : "";
    const ok = window.confirm(
      `确认删除 ${d} 的冻结账本${nHint}？\n将同时删除该日回填 outcomes，不可恢复。`
    );
    if (!ok) return;
    setStatus(`正在删除 ${d}…`, { busy: true });
    const res = await fetch("/api/quant/score-ledger/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: d, include_outcomes: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
    const pack = await loadLedgerIndex().catch(() => ({ dates: [], default_as_of: null }));
    const cur = readAsOf();
    if (cur === d) {
      const next =
        pack.default_as_of ||
        (pack.dates || []).find((x) => x && x !== d) ||
        "";
      setAsOf(next);
      if (next) {
        setStatus(`已删除 ${d} · 切到 ${next}`, { ok: true });
        await runReview({ autofill: true });
      } else {
        setStatus(`已删除 ${d} · 无剩余账本`, { ok: true });
        renderSummary({ empty: true, note: "无该日账本。请先冻结打分。" });
        const tauBox = document.getElementById("quant-score-review-tau");
        if (tauBox) {
          tauBox.hidden = true;
          tauBox.innerHTML = "";
        }
        const ncBox = document.getElementById("quant-score-review-nowcast");
        if (ncBox) {
          ncBox.hidden = true;
          ncBox.innerHTML = "";
        }
        const table = document.getElementById("quant-score-review-table");
        if (table) table.innerHTML = "";
        const scatter = document.getElementById("quant-score-review-scatter-wrap");
        if (scatter) scatter.hidden = true;
        const ydis = document.getElementById("quant-score-review-ydisagree-wrap");
        if (ydis) ydis.hidden = true;
      }
    } else {
      setStatus(`已删除 ${d}`, { ok: true });
    }
    return data;
  }

  async function runReview({ autofill = true, allowThinFallback = true } = {}) {
    const gen = ++reviewGen;
    await ensureDefaultAsOf();
    if (gen !== reviewGen) return;
    const asOf = readAsOf();
    const horizon = readHorizon();
    setStatus("复盘计算中…", { busy: true });
    try {
      const q = new URLSearchParams();
      if (asOf) q.set("as_of", asOf);
      q.set("horizon_days", String(horizon));
      q.set("autofill", autofill ? "true" : "false");
      const res = await fetch(`/api/quant/score-review?${q.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (gen !== reviewGen) return;
      if (!res.ok) throw new Error(data.detail || res.statusText);
      // 后端可能因薄样本自动降到 h=1；同步控件
      if (data.horizon_days != null) syncHorizonSelect(data.horizon_days);
      const usedH = Number(data.horizon_days) || horizon;
      const s0 = data.summary || {};
      const thin0 = Number(s0.data_thin || 0);
      // 仅自动加载时：as_of+h 未到则改用最近已回填日。手动点 chip 不回跳
      if (
        allowThinFallback &&
        !data.empty &&
        s0.hit_rate == null &&
        thin0 > 0
      ) {
        const pack = await loadLedgerIndex().catch(() => null);
        if (gen !== reviewGen) return;
        const next =
          (pack && pack.default_as_of) ||
          ((pack && pack.entries) || []).find(
            (e) =>
              /^\d{4}-\d{2}-\d{2}$/.test(String(e.as_of || "")) &&
              Number(e.outcomes_filled || 0) > 0
          )?.as_of ||
          "";
        if (next && next !== asOf) {
          setAsOf(next);
          setStatus(`as_of+h 日线未到，已改用更早决策日 ${next}`, { ok: true });
          return runReview({ autofill, allowThinFallback: false });
        }
      }
      if (gen !== reviewGen) return;
      renderSummary(data);
      renderScatter(data);
      renderYDisagreeViz(data);
      renderHitSparkline(usedH).catch(() => {});
      renderWrongTable(data);
      markActiveLedgerChip(data.as_of || asOf);
      const s = data.summary || {};
      const hit =
        s.hit_rate != null ? `${(Number(s.hit_rate) * 100).toFixed(0)}%` : "—";
      const thin = Number(s.data_thin || 0);
      const fbNote =
        data.horizon_fallback_from != null
          ? ` · 自 h=${data.horizon_fallback_from} 降级`
          : "";
      const needBar = s.need_bar_date ? String(s.need_bar_date) : "";
      const thinNeed = needBar
        ? `需 ${needBar} 收盘后回填`
        : `日线未覆盖 h=${usedH}`;
      const statusLine = data.empty
        ? data.note || "无账本"
        : s.hit_rate != null
          ? `as_of ${data.as_of} · 命中 ${hit} · 错票 ${s.wrong ?? 0}${fbNote}`
          : thin > 0
            ? `as_of ${data.as_of} · 薄样本 ${thin}/${data.n_ledger ?? "—"} · ${thinNeed}`
            : `as_of ${data.as_of} · ${s.blame_line || "样本不足"}`;
      setStatus(statusLine, {
        ok: !data.empty && s.hit_rate != null,
        error: !!data.empty,
      });
      if (setQuantMeta) {
        const metaLine = data.empty
          ? "双层 ŷ 复盘：无账本"
          : s.hit_rate != null
            ? `双层 ŷ 复盘 · EOD 命中 ${hit}${fbNote}`
            : thin > 0
              ? `双层 ŷ 复盘 · 薄样本 ${thin} · ${thinNeed}`
              : `双层 ŷ 复盘 · ${s.blame_line || "样本不足"}`;
        setQuantMeta(metaLine, {
          busy: false,
          error: !!data.empty,
        });
      }
      return data;
    } catch (err) {
      if (gen !== reviewGen) return;
      setStatus(`复盘失败：${String(err.message || err)}`, { error: true });
      if (setQuantMeta) {
        setQuantMeta(`双层 ŷ 复盘失败：${String(err.message || err)}`, {
          busy: false,
          error: true,
        });
      }
      throw err;
    }
  }

  async function fillOutcomes() {
    await ensureDefaultAsOf();
    const asOf = readAsOf();
    const horizon = readHorizon();
    setStatus("回填收益中…", { busy: true });
    const res = await fetch("/api/quant/score-outcomes/fill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: asOf, horizon_days: horizon }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    setStatus(
      `已回填 ${data.filled ?? 0} 只（缺 ${data.missing ?? 0}）`,
      { ok: true }
    );
    try {
      await loadLedgerIndex();
    } catch (_) {
      /* ignore */
    }
    return runReview({ autofill: false });
  }

  function freezeSkippedNote(data) {
    const freezeAsOf = String((data && data.as_of) || "").trim();
    const resolve = (data && data.resolve) || {};
    const skipped = String(
      (data && data.skipped_newer) || resolve.prev_trading_day || ""
    ).trim();
    const feature = String(resolve.feature_as_of || freezeAsOf || "").trim();
    if (skipped && freezeAsOf && skipped > freezeAsOf) {
      return `${skipped} 未冻（日线未齐，因子截止 ${feature || freezeAsOf}）`;
    }
    return "";
  }

  async function freezeLedger() {
    const freezeBtn = document.getElementById("quant-score-ledger-freeze");
    if (freezeBtn) {
      freezeBtn.disabled = true;
      freezeBtn.setAttribute("aria-busy", "true");
    }
    setStatus("冻结打分中…", { busy: true });
    try {
      // as_of=null：后端按因子截止日解析，避免会话日空标签
      const res = await fetch("/api/quant/score-ledger/freeze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ as_of: null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      const freezeAsOf = String(data.as_of || "").trim();
      const resolveNote =
        (data.resolve && data.resolve.note) || data.note || "";
      const skippedNote = freezeSkippedNote(data);
      // 停在实际冻到的决策日，不要跳回「已回填默认日」（会看起来像没冻）
      if (freezeAsOf) setAsOf(freezeAsOf);
      try {
        await loadLedgerIndex();
      } catch (_) {
        /* ignore */
      }
      const freezeHead =
        `已冻结 ${data.n_rows ?? 0} 只 · 决策日 ${freezeAsOf || "—"}` +
        (skippedNote
          ? ` · ${skippedNote}`
          : resolveNote
            ? ` · ${resolveNote}`
            : "");
      setStatus(freezeHead, { ok: true, error: !!skippedNote });
      if (setQuantMeta) {
        setQuantMeta(freezeHead, { busy: true, error: !!skippedNote });
      }
      // 刚冻的日子可能还没 outcomes；禁止 thin-fallback 偷偷切走
      const out = await runReview({
        autofill: true,
        allowThinFallback: false,
      });
      const reviewLine =
        document.getElementById("quant-score-review-status")?.textContent ||
        "";
      const keepFreeze =
        reviewLine && !reviewLine.startsWith("已冻结")
          ? `${freezeHead} · ${reviewLine}`
          : freezeHead;
      setStatus(keepFreeze, {
        ok: !skippedNote && !!(out && out.summary && out.summary.hit_rate != null),
        error: !!skippedNote || !!(out && out.empty),
      });
      if (setQuantMeta) {
        setQuantMeta(keepFreeze, {
          busy: false,
          error: !!skippedNote || !!(out && out.empty),
        });
      }
      return out;
    } finally {
      if (freezeBtn) {
        freezeBtn.disabled = false;
        freezeBtn.removeAttribute("aria-busy");
      }
    }
  }

  function jumpRefit() {
    const runBtn = document.getElementById("quant-ols-clusters-run");
    if (runBtn) {
      runBtn.scrollIntoView({ block: "nearest", behavior: "smooth" });
      // 进度挂分组卡头，不占页顶主标题
      if (setBusyText && els.quantOlsSummary) {
        setBusyText(els.quantOlsSummary, "复盘 → 跑分组重估中…", { busy: true });
      }
      runBtn.click();
      setStatus("已触发「跑分组」重估", { busy: true });
    } else {
      setStatus("请到「分组」点「跑分组」重估组 β", { error: true });
    }
  }

  on("quant-score-review-run", "click", async (e) => {
    e.preventDefault();
    try {
      await runReview({ autofill: true });
    } catch (_) {
      /* status already set */
    }
  });
  on("quant-score-review-fill", "click", async (e) => {
    e.preventDefault();
    try {
      await fillOutcomes();
    } catch (err) {
      setStatus(`回填失败：${String(err.message || err)}`, { error: true });
    }
  });
  on("quant-score-ledger-freeze", "click", async (e) => {
    e.preventDefault();
    try {
      await freezeLedger();
    } catch (err) {
      setStatus(`冻结失败：${String(err.message || err)}`, { error: true });
    }
  });

  function fmtCalMae(v) {
    return v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(3) : "—";
  }
  function fmtCalIc(v) {
    return v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(2) : "—";
  }
  function summarizeCalHeads(heads) {
    const parts = [];
    for (const [name, h] of Object.entries(heads || {})) {
      if (!h || !h.success) {
        parts.push(`${name}失败`);
        continue;
      }
      const ho = h.holdout_metrics || {};
      const src = h.sample_source || (h.pair_resolve && h.pair_resolve.used) || "";
      const srcTag = src ? ` · ${src}` : "";
      parts.push(
        `${name} n=${h.n ?? "—"} hold MAE ${fmtCalMae(ho.mae_raw)}→${fmtCalMae(
          ho.mae_cal
        )} IC ${fmtCalIc(ho.ic_raw)}→${fmtCalIc(ho.ic_cal)}${srcTag}`
      );
    }
    return parts.join(" · ");
  }

  /** 最近一次拟合（未 promote）快照，供面板对照 */
  let lastFitSnapshot = null;
  /** 当前面板用于重绘曲线的 head 文档 */
  let lastCalVizHeads = null;

  function pickCalHeads(doc) {
    if (!doc || typeof doc !== "object") return {};
    const heads = doc.heads;
    return heads && typeof heads === "object" ? heads : {};
  }

  function metricDeltaClass(raw, cal, { higherBetter = false } = {}) {
    const a = Number(raw);
    const b = Number(cal);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return "";
    if (Math.abs(a - b) < 1e-9) return "";
    const better = higherBetter ? b > a : b < a;
    return better ? "is-better" : "is-worse";
  }

  function headDocForViz(name, liveHeads, reportHeads, preferLive) {
    if (preferLive && liveHeads[name]) return liveHeads[name];
    return reportHeads[name] || liveHeads[name] || null;
  }

  function fmtDelta(raw, cal, digits = 3) {
    const a = Number(raw);
    const b = Number(cal);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return "—";
    const d = b - a;
    const t = Math.abs(d).toFixed(digits);
    return d > 0 ? `+${t}` : d < 0 ? `−${t}` : "0";
  }

  function kpiChipHtml(label, raw, cal, { higherBetter = false, digits = 3 } = {}) {
    const cls = metricDeltaClass(raw, cal, { higherBetter });
    const rawTxt = higherBetter ? fmtCalIc(raw) : fmtCalMae(raw);
    const calTxt = higherBetter ? fmtCalIc(cal) : fmtCalMae(cal);
    const dTxt = fmtDelta(raw, cal, digits);
    return (
      `<div class="quant-cal-kpi ${cls}">` +
      `<span class="quant-cal-kpi-lab">${esc(label)}</span>` +
      `<span class="quant-cal-kpi-vals">` +
      `<em title="raw">${esc(rawTxt)}</em>` +
      `<span class="quant-cal-kpi-arrow" aria-hidden="true">→</span>` +
      `<strong title="calibrated">${esc(calTxt)}</strong>` +
      `</span>` +
      `<span class="quant-cal-kpi-delta" title="cal − raw">${esc(dTxt)}</span>` +
      `</div>`
    );
  }

  function paintCalCharts(headsByName) {
    lastCalVizHeads = headsByName || null;
    if (!headsByName) return;
    const colors = { eod: "#0f766e", tau: "#0369a1" };
    for (const [name, doc] of Object.entries(headsByName)) {
      if (!doc) continue;
      const canvas = document.querySelector(
        `#quant-score-cal-panel canvas[data-cal-head="${String(name).replace(/"/g, "")}"]`
      );
      if (!canvas) continue;
      mountCalibrationCurve(canvas, doc, {
        label: name,
        color: colors[name] || "#0f766e",
      });
    }
  }

  function renderCalibrationPanel(pack) {
    const box = calPanelEl();
    if (!box) return;
    const live = (pack && pack.live) || null;
    const lastReport =
      lastFitSnapshot || (pack && pack.last_report) || null;
    const promoteOk =
      pack && pack.promote_ok != null
        ? !!pack.promote_ok
        : lastReport && lastReport.promote_ok != null
          ? !!lastReport.promote_ok
          : true;
    const promoteBlock =
      (pack && pack.promote_block_reason) ||
      (lastReport && lastReport.promote_block_reason) ||
      "";
    // 「将跳过：…」仍可写入 live（只挂安全头）
    const promoteHardBlock =
      !promoteOk ||
      (promoteBlock && !String(promoteBlock).startsWith("将跳过"));
    const liveHeads = pickCalHeads(live);
    const reportHeads = pickCalHeads(lastReport);
    const headNames = Array.from(
      new Set([...Object.keys(liveHeads), ...Object.keys(reportHeads)])
    ).sort();
    const hasLive = !!(pack && pack.live_present && Object.keys(liveHeads).length);
    const hasDraft = !!(lastReport && Object.keys(reportHeads).length);
    let state = "off";
    let badge = "未拟合";
    if (hasLive) {
      state = promoteHardBlock ? "warn" : "on";
      badge = promoteHardBlock ? "已上线·映射偏弱" : "已上线";
    } else if (hasDraft) {
      state = promoteHardBlock ? "warn" : "draft";
      badge = promoteHardBlock ? "可写入·映射偏弱" : "已拟合·待写入";
    }
    const promoted =
      (live && (live.promoted_at || live.fitted_at)) ||
      (lastReport && (lastReport.fitted_at || lastReport.promoted_at)) ||
      "";
    const lookback =
      (live && live.lookback_dates) ||
      (lastReport && lastReport.lookback_dates) ||
      "—";
    const note =
      (live && live.note) ||
      (lastReport && lastReport.note) ||
      "";

    const vizHeads = {};
    headNames.forEach((name) => {
      // 有 draft 时优先看报告；已上线且无新拟合则看 live
      const preferLive = hasLive && !hasDraft;
      const doc = headDocForViz(name, liveHeads, reportHeads, preferLive);
      if (doc) vizHeads[name] = doc;
    });

    const rows = headNames
      .map((name) => {
        const src = vizHeads[name] || {};
        const ho = src.holdout_metrics || {};
        const maeCls = metricDeltaClass(ho.mae_raw, ho.mae_cal, {
          higherBetter: false,
        });
        const icCls = metricDeltaClass(ho.ic_raw, ho.ic_cal, {
          higherBetter: true,
        });
        const ok =
          src.success !== false &&
          ((src.knots_x && src.knots_x.length >= 2) || src.n != null);
        const srcKind =
          src.sample_source ||
          (src.pair_resolve && src.pair_resolve.used) ||
          "";
        return (
          `<tr>` +
          `<td>${esc(name)}</td>` +
          `<td class="num">${esc(String(src.n ?? "—"))}</td>` +
          `<td>${esc(srcKind || "—")}</td>` +
          `<td class="num ${maeCls}">${esc(
            `${fmtCalMae(ho.mae_raw)}→${fmtCalMae(ho.mae_cal)}`
          )}</td>` +
          `<td class="num ${icCls}">${esc(
            `${fmtCalIc(ho.ic_raw)}→${fmtCalIc(ho.ic_cal)}`
          )}</td>` +
          `<td>${esc(ok ? "ok" : "缺")}</td>` +
          `</tr>`
        );
      })
      .join("");

    const table =
      headNames.length > 0
        ? `<table class="quant-cal-table">` +
          `<thead><tr>` +
          `<th>头</th><th class="num">n</th><th>样本</th><th class="num">hold MAE</th>` +
          `<th class="num">hold IC</th><th>knots</th>` +
          `</tr></thead><tbody>${rows}</tbody></table>`
        : `<p class="quant-cal-note">尚无校准映射。先点顶部「拟合校准」，看 holdout 后再「写入 live」（校准列/tip 可读，不进决策）。</p>`;

    const headTitle = { eod: "ŷ_EOD", tau: "ŷ_τ" };
    const vizCells = headNames
      .map((name) => {
        const src = vizHeads[name] || {};
        const ho = src.holdout_metrics || {};
        const nKnots = Math.min(
          (src.knots_x || []).length,
          (src.knots_y || []).length
        );
        if (nKnots < 2) return "";
        const nSamp = src.n_holdout ?? src.n ?? "—";
        const title = headTitle[name] || name;
        return (
          `<article class="quant-cal-viz-cell">` +
          `<header class="quant-cal-viz-head">` +
          `<div>` +
          `<div class="quant-cal-viz-title">${esc(title)} 校准映射</div>` +
          `<div class="quant-cal-viz-sub">isotonic · ${esc(
            String(nKnots)
          )} knots · holdout n=${esc(String(nSamp))}</div>` +
          `</div>` +
          `<div class="quant-cal-kpi-row">` +
          kpiChipHtml("MAE", ho.mae_raw, ho.mae_cal, {
            higherBetter: false,
            digits: 3,
          }) +
          kpiChipHtml("IC", ho.ic_raw, ho.ic_cal, {
            higherBetter: true,
            digits: 2,
          }) +
          `</div>` +
          `</header>` +
          `<div class="dashboard-chart-host quant-cal-curve-host">` +
          `<canvas class="quant-cal-curve" data-cal-head="${esc(
            name
          )}" width="480" height="188" aria-label="${esc(
            title
          )} 校准曲线"></canvas>` +
          `</div>` +
          `<p class="quant-cal-viz-hint">青带=抬高 · 琥珀带=压低 · 悬停 knot 读 Δ</p>` +
          `</article>`
        );
      })
      .filter(Boolean)
      .join("");

    const vizBlock = vizCells
      ? `<div class="quant-cal-viz" id="quant-cal-viz">` +
        `<div class="quant-cal-viz-grid">${vizCells}</div>` +
        `</div>`
      : "";

    box.innerHTML =
      `<div class="quant-cal-panel is-${state}">` +
      `<div class="quant-cal-head">` +
      `<span class="quant-cal-badge">${esc(badge)}</span>` +
      `<span class="quant-cal-title">校准 g(ŷ)</span>` +
      `<span class="quant-cal-meta">lookback ${esc(String(lookback))} · ${esc(
        promoted ? String(promoted).slice(0, 19).replace("T", " ") : "—"
      )}</span>` +
      `</div>` +
      table +
      vizBlock +
      `<p class="quant-cal-note">` +
      (hasLive
        ? "tip / 复盘可读 g(ŷ) 对照；排序与买卖/入簿闸仍用原始 ŷ。不改 Ridge β。"
        : promoteHardBlock
          ? `映射偏弱（软警告）：${esc(
              String(promoteBlock || "g(门槛)偏低")
            )} · 仍可顶部「写入 live」供 tip，不进决策`
          : promoteBlock
            ? `${esc(String(promoteBlock))} · 顶部「写入 live」后 tip 出现 ⑤`
            : "顶部「写入 live」后 tip 出现 ⑤；排序与闸仍用 raw ŷ；主 predicted_score 保留原值。") +
      (note ? ` · ${esc(String(note).slice(0, 80))}` : "") +
      `</p>` +
      `</div>`;

    // 等布局后再画，避免 clientWidth=0
    requestAnimationFrame(() => paintCalCharts(vizHeads));
  }

  async function refreshCalibrationPanel() {
    const box = calPanelEl();
    try {
      const res = await fetch("/api/quant/score-calibration/model");
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        if (box) {
          box.innerHTML = `<p class="quant-attr-note">校准状态读取失败</p>`;
        }
        setCalStatus("校准状态读取失败", { error: true });
        return null;
      }
      renderCalibrationPanel(data);
      const liveHeads =
        data.live && data.live.heads && typeof data.live.heads === "object"
          ? Object.keys(data.live.heads).length
          : 0;
      const badge = liveHeads
        ? "已上线"
        : data.last_report
          ? "已拟合·待写入"
          : "未拟合";
      setCalStatus(`校准 · ${badge}`);
      return data;
    } catch (_) {
      if (box) {
        box.innerHTML = `<p class="quant-attr-note">校准状态读取失败</p>`;
      }
      setCalStatus("校准状态读取失败", { error: true });
      return null;
    }
  }

  async function fitCalibration() {
    setCalStatus("拟合校准 g(ŷ)…", { busy: true });
    setQuantMeta("拟合校准中…", { busy: true });
    const res = await fetch("/api/quant/score-calibration/fit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback_dates: 90,
        train_frac: 0.75,
        sample_source: "panel",
        lookback_bars: 80,
        watching_limit: 100,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setCalStatus(`校准拟合失败：${err}`, { error: true });
      setQuantMeta(`校准拟合失败 · ${err}`, { error: true });
      return;
    }
    lastFitSnapshot = data;
    const msg = summarizeCalHeads(data.heads);
    if (data.promote_ok === false) {
      setCalStatus(
        `校准已拟合 · 可写入 live（软警告：${data.promote_block_reason || msg}）`,
        { ok: true }
      );
      setQuantMeta(
        `校准已拟合 · 可写入 tip 对照 · ${data.promote_block_reason || msg}`
      );
    } else if (
      data.promote_block_reason &&
      String(data.promote_block_reason).startsWith("将跳过")
    ) {
      setCalStatus(`校准已拟合 · ${data.promote_block_reason} · ${msg}`, {
        ok: true,
      });
      setQuantMeta(`校准可写入（部分头告警）· ${msg}`);
    } else {
      setCalStatus(`校准已拟合（未写盘）· ${msg}`, { ok: true });
      setQuantMeta(`校准已拟合 · 人审后点「写入 live」· ${msg}`);
    }
    await refreshCalibrationPanel();
  }

  async function persistCalibration() {
    if (
      !window.confirm(
        [
          "写入 ŷ 校准对照层到 live？",
          "",
          "将写入 live/score_calibration.json；tip/复盘可读 g(ŷ)。",
          "排序 / 买卖闸 / 入簿门槛仍用原始 ŷ（不进决策）。",
          "不改 Ridge β / signal_config.weights。",
        ].join("\n")
      )
    ) {
      return;
    }
    setCalStatus("写入 live…", { busy: true });
    const res = await fetch("/api/quant/score-calibration/persist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        note: "ui score calibration promote",
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setCalStatus(`校准写入失败：${err}`, { error: true });
      return;
    }
    lastFitSnapshot = null;
    setCalStatus(
      `校准已写入 live · heads ${(data.heads || []).join(",") || "—"} · ${
        data.promoted_at || ""
      }`,
      { ok: true }
    );
    setQuantMeta("校准已上线 · tip 可读 g(ŷ)；排序/闸仍用 raw");
    await refreshCalibrationPanel();
  }

  on("quant-score-cal-fit", "click", async (e) => {
    e.preventDefault();
    try {
      await fitCalibration();
    } catch (err) {
      setCalStatus(`校准拟合失败：${String(err.message || err)}`, { error: true });
    }
  });
  on("quant-score-cal-persist", "click", async (e) => {
    e.preventDefault();
    try {
      await persistCalibration();
    } catch (err) {
      setCalStatus(`校准写入失败：${String(err.message || err)}`, { error: true });
    }
  });

  const calBox = calPanelEl();
  if (calBox && calBox.dataset.calWired !== "1") {
    calBox.dataset.calWired = "1";
    if (typeof ResizeObserver !== "undefined") {
      let t = 0;
      const ro = new ResizeObserver(() => {
        window.clearTimeout(t);
        t = window.setTimeout(() => {
          if (lastCalVizHeads) paintCalCharts(lastCalVizHeads);
        }, 80);
      });
      ro.observe(calBox);
    }
  }
  async function runYhatResidualShadow() {
    setStatus("ŷ 残差对照中…", { busy: true });
    setQuantMeta("ŷ 行业残差 on/off…", { busy: true });
    const res = await fetch("/api/quant/yhat-residual/shadow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        watching_limit: 36,
        top_k: 10,
        prefer_cluster_book: true,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !(data.success || data.ok)) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setStatus(`ŷ残差对照失败：${err}`, { error: true });
      setQuantMeta(`ŷ残差对照失败 · ${err}`, { error: true });
      return;
    }
    const cmp = data.compare || {};
    const jac =
      cmp.jaccard_topk != null ? Number(cmp.jaccard_topk).toFixed(2) : "—";
    const sp =
      cmp.spearman_topk_ranks != null
        ? Number(cmp.spearman_topk_ranks).toFixed(2)
        : "—";
    const win = data.winner || "—";
    setStatus(
      `ŷ残差对照 · Jaccard ${jac} · 秩相关 ${sp} · ${win} · 源 ${data.source || "—"}`,
      { ok: true }
    );
    setQuantMeta(
      `ŷ残差对照完成 · ${win} · ${data.note || ""} · ${data.promote_hint || "不写盘"}`
    );
  }

  on("quant-yhat-residual-shadow", "click", async (e) => {
    e.preventDefault();
    try {
      await runYhatResidualShadow();
    } catch (err) {
      setStatus(`ŷ残差对照失败：${String(err.message || err)}`, { error: true });
    }
  });

  async function runExcessModeShadow() {
    setStatus("超额标签对照中…", { busy: true });
    setQuantMeta("绝对 y vs 指数超额 y…", { busy: true });
    const res = await fetch("/api/quant/excess-mode/shadow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lookback: 120,
        watching_limit: 36,
        horizon_days: 1,
        ridge_lambda: 1.0,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !(data.success || data.ok)) {
      const err = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      setStatus(`超额标签对照失败：${err}`, { error: true });
      setQuantMeta(`超额标签对照失败 · ${err}`, { error: true });
      return;
    }
    const arms = data.arms || {};
    const a = arms.none || {};
    const b = arms.index || {};
    const fmt = (arm) => {
      const o = (arm && arm.oos) || {};
      const ic = o.ic != null ? Number(o.ic).toFixed(2) : "—";
      const hit =
        o.sign_hit != null ? `${(Number(o.sign_hit) * 100).toFixed(0)}%` : "—";
      return `IC ${ic} · 命中 ${hit} · n=${o.n ?? arm.n ?? "—"}`;
    };
    const win = data.winner || "—";
    setStatus(
      `超额标签 · 绝对 ${fmt(a)} · 超额 ${fmt(b)} · 胜者 ${win}`,
      { ok: true }
    );
    setQuantMeta(
      `超额标签对照完成 · 胜者 ${win} · ${data.note || ""} · ${data.promote_hint || "不写盘"}`
    );
  }

  on("quant-excess-mode-shadow", "click", async (e) => {
    e.preventDefault();
    try {
      await runExcessModeShadow();
    } catch (err) {
      setStatus(`超额标签对照失败：${String(err.message || err)}`, { error: true });
    }
  });

  on("quant-score-review-refit", "click", (e) => {
    e.preventDefault();
    jumpRefit();
  });

  const reviewTable = document.getElementById("quant-score-review-table");
  if (reviewTable && reviewTable.dataset.panelWired !== "1") {
    reviewTable.dataset.panelWired = "1";
    reviewTable.addEventListener("click", (e) => {
      const tr = e.target && e.target.closest ? e.target.closest("tr[data-code]") : null;
      if (!tr || !reviewTable.contains(tr)) return;
      const code = String(tr.getAttribute("data-code") || "").trim();
      if (!code) return;
      openStockPanel(code, tr.getAttribute("data-name") || "");
    });
    reviewTable.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const tr = e.target && e.target.closest ? e.target.closest("tr[data-code]") : null;
      if (!tr || !reviewTable.contains(tr)) return;
      e.preventDefault();
      const code = String(tr.getAttribute("data-code") || "").trim();
      if (!code) return;
      openStockPanel(code, tr.getAttribute("data-name") || "");
    });
  }

  const chipRow = document.getElementById("quant-score-review-ledger-chips");
  if (chipRow && chipRow.dataset.chipWired !== "1") {
    chipRow.dataset.chipWired = "1";
    chipRow.addEventListener("click", (e) => {
      const delBtn =
        e.target && e.target.closest
          ? e.target.closest("button.quant-score-ledger-chip-del[data-asof]")
          : null;
      if (delBtn && chipRow.contains(delBtn)) {
        e.preventDefault();
        e.stopPropagation();
        const asOf = String(delBtn.getAttribute("data-asof") || "").trim();
        if (!asOf) return;
        deleteLedgerAsOf(asOf, {
          nRows: delBtn.getAttribute("data-nrows") || "",
        }).catch((err) => {
          setStatus(`删除失败：${String(err.message || err)}`, { error: true });
        });
        return;
      }
      const btn =
        e.target && e.target.closest
          ? e.target.closest("button.quant-score-ledger-chip[data-asof]")
          : null;
      if (!btn || !chipRow.contains(btn)) return;
      e.preventDefault();
      const asOf = String(btn.getAttribute("data-asof") || "").trim();
      if (!asOf) return;
      selectLedgerAsOf(asOf).catch((err) => {
        setStatus(`切换账本失败：${String(err.message || err)}`, { error: true });
      });
    });
  }

  // 仅研究枢纽进页自动加载，避免 /follow 等页抢带宽拖慢纸面主链路
  if (document.body?.dataset?.page === "quant") {
    refreshCalibrationPanel().catch(() => {});
    loadLedgerIndex()
      .then((pack) => {
        setAsOf(pack.default_as_of || (pack.dates || [])[0] || "");
      })
      .catch(() => {})
      .then(() => runReview({ autofill: true }))
      .catch(() => {});
  }

  return {
    runReview,
    fillOutcomes,
    freezeLedger,
    ensureDefaultAsOf,
    jumpRefit,
    loadLedgerIndex,
    refreshCalibrationPanel,
  };
}
