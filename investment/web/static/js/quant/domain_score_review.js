/**
 * 双层 ŷ 复盘：ŷ_EOD 方向 vs 前瞻收益；副轴 ŷ_τ 验收。
 */
import { paintYhatScatter, paintHitSparkline } from "./yhat_viz.js";
import { syncOverviewFromScoreReview, setProStatusChip, factorCN } from "./factor_corr_ui.js";

export function installScoreReview(ctx) {
  const { els, escapeHtml, setQuantMeta, setBusyText, on } = ctx;
  const esc = escapeHtml;
  let panelLoadToken = 0;
  /** 当前复盘决策日（由冻结 chip 选择） */
  let selectedAsOf = null;

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
      `<span>IC <b>${esc(ic)}</b> (n=${esc(n)})</span>` +
      `<span>命中 <b>${esc(hit)}</b></span>` +
      `<span>影子重叠 Jaccard <b>${esc(j)}</b> · ${esc(ov)}</span>` +
      (tau.shadow_exists
        ? `<span>影子簿 ${esc(String(tau.shadow_n ?? "—"))} 只</span>`
        : `<span class="quant-attr-note">无 τ 影子成员快照</span>`) +
      `</div>` +
      `<p class="quant-attr-note">标签 ${esc(
        String(tau.y_spec_tau || "close[T]/open[T]-1")
      )} · 不替代 EOD 复盘</p>`;
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
    row.innerHTML = entries
      .map((e) => {
        const asOf = String(e.as_of || "").trim();
        if (!asOf) return "";
        const n = e.n_rows != null ? Number(e.n_rows) : null;
        const filled = e.outcomes_filled != null ? Number(e.outcomes_filled) : 0;
        const useful = filled > 0;
        const immature = !!e.immature;
        const active = asOf === cur ? " is-active" : "";
        const ocCls = useful
          ? " has-outcomes"
          : immature
            ? " is-immature"
            : " no-outcomes";
        const ocMark = useful
          ? ` · 已回填 ${filled}`
          : immature
            ? " · 未到期"
            : " · 未回填";
        const nTxt = n != null && Number.isFinite(n) ? `${n}只` : "—";
        const title = immature
          ? `${asOf} · ${nTxt} · 会话日账本，h 未到期，复盘请选更早决策日`
          : `${asOf} · ${nTxt}${ocMark} · 点击切换决策日`;
        return (
          `<div class="quant-score-ledger-chip-wrap${active}${immature ? " is-immature" : ""}" role="listitem">` +
          `<button type="button" class="quant-score-ledger-chip${active}${ocCls}"` +
          ` data-asof="${esc(asOf)}" title="${esc(title)}">` +
          `<span class="chip-date">${esc(shortDate(asOf))}</span>` +
          `<span class="chip-meta">${esc(nTxt)}${useful ? " ✓" : immature ? " …" : ""}</span>` +
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
    await runReview({ autofill: true });
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
        const table = document.getElementById("quant-score-review-table");
        if (table) table.innerHTML = "";
        const scatter = document.getElementById("quant-score-review-scatter-wrap");
        if (scatter) scatter.hidden = true;
      }
    } else {
      setStatus(`已删除 ${d}`, { ok: true });
    }
    return data;
  }

  async function runReview({ autofill = true } = {}) {
    await ensureDefaultAsOf();
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
      if (!res.ok) throw new Error(data.detail || res.statusText);
      // 后端可能因薄样本自动降到 h=1；同步控件
      if (data.horizon_days != null) syncHorizonSelect(data.horizon_days);
      const usedH = Number(data.horizon_days) || horizon;
      renderSummary(data);
      renderScatter(data);
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
      const statusLine = data.empty
        ? data.note || "无账本"
        : s.hit_rate != null
          ? `as_of ${data.as_of} · 命中 ${hit} · 错票 ${s.wrong ?? 0}${fbNote}`
          : thin > 0
            ? `as_of ${data.as_of} · 薄样本 ${thin}/${data.n_ledger ?? "—"} · 日线未覆盖 h=${usedH}`
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
              ? `双层 ŷ 复盘 · 薄样本 ${thin} · as_of+h 日线未到，请选更早决策日`
              : `双层 ŷ 复盘 · ${s.blame_line || "样本不足"}`;
        setQuantMeta(metaLine, {
          busy: false,
          error: !!data.empty,
        });
      }
      return data;
    } catch (err) {
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

  async function freezeLedger() {
    setStatus("冻结打分中…", { busy: true });
    // as_of=null：后端按因子截止日解析，避免会话日空标签
    const res = await fetch("/api/quant/score-ledger/freeze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: null }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    const freezeAsOf = data.as_of || "";
    const resolveNote =
      (data.resolve && data.resolve.note) || data.note || "";
    let reviewAsOf = null;
    try {
      const pack = await loadLedgerIndex();
      reviewAsOf = pack.default_as_of || pack.safe_as_of || null;
    } catch (_) {
      /* ignore */
    }
    if (reviewAsOf) setAsOf(reviewAsOf);
    setStatus(
      `已冻结 ${data.n_rows ?? 0} 只（决策日 ${freezeAsOf || "—"}）` +
        (resolveNote ? ` · ${resolveNote}` : "") +
        (reviewAsOf && reviewAsOf !== freezeAsOf
          ? ` · 复盘改用 ${reviewAsOf}`
          : ""),
      { ok: true }
    );
    const out = await runReview({ autofill: true });
    if (setQuantMeta && freezeAsOf) {
      const s = (out && out.summary) || {};
      if (s.hit_rate != null) {
        setQuantMeta(
          `已冻结 ${freezeAsOf} · 复盘 ${out.as_of || reviewAsOf || ""} · 命中 ${(Number(s.hit_rate) * 100).toFixed(0)}%`,
          { busy: false, error: false }
        );
      } else if (Number(s.data_thin || 0) > 0) {
        setQuantMeta(
          `已冻结 ${freezeAsOf}（${data.n_rows ?? 0} 只）· 复盘日线未覆盖 as_of+h，请选更早决策日`,
          { busy: false, error: false }
        );
      }
    }
    return out;
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
  };
}
