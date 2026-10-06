/**
 * 观察池分档：研究套 ŷ_oo · Holdout 前半 OOS 打档 → last；
 * live 档位跟随历史回测「分档」勾选（见 domain_backtest live-sync）。
 */
import { syncOverviewUniverse } from "./factor_corr_ui.js";

export function installResearchUniverseUi(q) {
  const { on, apiFetch, escapeHtml, readHoldoutTradingDays, readFitLookbackDays } = q;
  let lastTiers = null;
  let liveStatus = null;

  const TIER_META = {
    A: { label: "强", tip: "命中过门且有效日够" },
    B: { label: "中", tip: "方向尚可或样本偏薄" },
    C: { label: "弱", tip: "命中不足或无样本" },
  };

  const el = {
    chip: () => document.getElementById("quant-ru-chip"),
    msg: () => document.getElementById("quant-ru-msg"),
    strip: () => document.getElementById("quant-ru-strip"),
    tierTable: () => document.getElementById("quant-ru-tier-table"),
    maxSize: () => document.getElementById("quant-watching-max-size"),
    poolVal: () => document.getElementById("quant-watching-pool-val"),
    poolStatus: () => document.getElementById("quant-watching-pool-status"),
  };

  function holdoutN() {
    if (typeof readHoldoutTradingDays === "function") {
      return Math.max(2, Math.min(90, Number(readHoldoutTradingDays("oo")) || 20));
    }
    return 20;
  }

  function ooLookbackN() {
    if (typeof readFitLookbackDays === "function") {
      return Math.max(40, Math.min(700, Number(readFitLookbackDays("oo")) || 120));
    }
    return 120;
  }

  function setStatus(state, chip, message) {
    const c = el.chip();
    const m = el.msg();
    const s = el.strip();
    if (c) {
      c.dataset.state = state || "idle";
      c.textContent = chip || "—";
    }
    if (m) m.textContent = message || "";
    if (s) s.classList.toggle("is-busy", state === "busy");
  }

  function liveBit() {
    if (!liveStatus || !liveStatus.enabled) return "live 随回测（未开）";
    const allow = (liveStatus.allowed_tiers || ["A", "B"]).join("") || "AB";
    return `live 随回测 ${allow}`;
  }

  function fmtHit(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return `${Math.round(Number(v) * 1000) / 10}%`;
  }

  function fmtIc(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    return Number(v).toFixed(3);
  }

  function hitBarHtml(hitRate, aHit, bHit) {
    if (hitRate == null || Number.isNaN(Number(hitRate))) {
      return `<span class="quant-ru-hit-bar is-empty" title="无有效命中"><span class="quant-ru-hit-fill" style="width:0%"></span></span>`;
    }
    const pct = Math.max(0, Math.min(100, Number(hitRate) * 100));
    const a = (aHit ?? 0.6) * 100;
    const b = (bHit ?? 0.5) * 100;
    let tone = "is-c";
    if (pct + 1e-9 >= a) tone = "is-a";
    else if (pct + 1e-9 >= b) tone = "is-b";
    return (
      `<span class="quant-ru-hit-bar ${tone}" title="命中 ${pct.toFixed(1)}%">` +
      `<span class="quant-ru-hit-fill" style="width:${pct.toFixed(1)}%"></span>` +
      `</span>`
    );
  }

  function stockCell(r) {
    const code = escapeHtml(r.code || "");
    const name = escapeHtml(String(r.name || "").trim() || "—");
    const title = r.name
      ? `${escapeHtml(r.name)} ${code}`
      : code;
    return (
      `<td class="quant-ru-stock" title="${title}">` +
      `<span class="quant-ru-stock-name">${name}</span>` +
      `<span class="quant-ru-stock-code">${code}</span>` +
      `</td>`
    );
  }

  function mixBarHtml(counts) {
    const a = Number(counts.A || 0);
    const b = Number(counts.B || 0);
    const c = Number(counts.C || 0);
    const tot = Math.max(1, a + b + c);
    return (
      `<div class="quant-ru-mix" role="img" aria-label="档位构成 A${a} B${b} C${c}">` +
      `<span class="quant-ru-mix-seg is-A" style="flex:${a}" title="A ${a}"></span>` +
      `<span class="quant-ru-mix-seg is-B" style="flex:${b}" title="B ${b}"></span>` +
      `<span class="quant-ru-mix-seg is-C" style="flex:${c}" title="C ${c}"></span>` +
      `<span class="quant-ru-mix-meta">${Math.round((a / tot) * 100)}% A · ${Math.round(
        (b / tot) * 100
      )}% B · ${Math.round((c / tot) * 100)}% C</span>` +
      `</div>`
    );
  }

  function renderTierTable(rep) {
    const host = el.tierTable();
    if (!host) return;
    if (!rep || !rep.success || !Array.isArray(rep.rows) || !rep.rows.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    const thr = rep.thresholds || {};
    const hold = rep.holdout_n != null ? rep.holdout_n : "—";
    const tierN = rep.tier_n != null ? rep.tier_n : rep.n_ledger_dates ?? "—";
    const aHit = thr.a_hit ?? 0.6;
    const aMinN = thr.a_min_n != null ? thr.a_min_n : 6;
    const bHit = thr.b_hit ?? 0.5;
    const counts = rep.counts || {};
    const nSample = rep.n_with_sample != null ? rep.n_with_sample : "—";
    const nPool = rep.pool_count != null ? rep.pool_count : (rep.rows || []).length;

    const headNote = [
      `ŷ_oo Holdout OOS`,
      `Holdout${hold} · 前半${tierN}日`,
      `A 命中≥${Math.round(aHit * 100)}% · IC>0 · N≥${aMinN}`,
      `B≥${Math.round(bHit * 100)}%`,
      liveBit(),
    ].join(" · ");

    const byTier = { A: [], B: [], C: [] };
    for (const r of rep.rows) {
      const t = String(r.tier || "C");
      if (byTier[t]) byTier[t].push(r);
      else byTier.C.push(r);
    }
    const maxShow = 120;
    const sections = ["A", "B", "C"]
      .map((tier) => {
        const meta = TIER_META[tier] || { label: tier, tip: "" };
        const rows = byTier[tier];
        const slice = rows.slice(0, maxShow);
        const trs = slice
          .map(
            (r) => `<tr data-tier="${tier}">
            ${stockCell(r)}
            <td class="num quant-ru-hit-cell">
              <span class="quant-ru-hit-val">${fmtHit(r.hit_rate)}</span>
              ${hitBarHtml(r.hit_rate, aHit, bHit)}
            </td>
            <td class="num" title="有效日 / 窗内日">${r.n_valid ?? 0}<span class="quant-ru-n-sub">/${
              r.n_days ?? 0
            }</span></td>
            <td class="num">${fmtIc(r.ic)}</td>
          </tr>`
          )
          .join("");
        const more =
          rows.length > maxShow
            ? `<p class="quant-ru-list-more">已显示 ${maxShow} / ${rows.length}</p>`
            : "";
        const body = rows.length
          ? `<table class="quant-ru-tier-grid">
              <thead><tr>
                <th class="quant-ru-col-stock">标的</th>
                <th class="num" title="ŷ_oo 符号命中率">命中</th>
                <th class="num" title="有效命中日 / 窗内出现日">n</th>
                <th class="num" title="票内 Spearman IC">IC</th>
              </tr></thead>
              <tbody>${trs}</tbody>
            </table>${more}`
          : `<p class="quant-ru-list-empty">无</p>`;
        return `<details class="quant-ru-tier-fold" data-tier="${tier}">
          <summary class="quant-ru-tier-fold-sum" title="${escapeHtml(meta.tip)}">
            <span class="quant-ru-tier-badge is-${tier}">${tier}</span>
            <span class="quant-ru-tier-label">${escapeHtml(meta.label)}</span>
            <span class="quant-ru-tier-fold-count">${rows.length}</span>
          </summary>
          <div class="quant-ru-tier-fold-body">${body}</div>
        </details>`;
      })
      .join("");

    host.hidden = false;
    host.innerHTML =
      `<div class="quant-ru-desk">` +
      `<div class="quant-ru-kpis" aria-label="分档 KPI">` +
      `<div class="quant-ru-kpi is-A"><span class="quant-ru-kpi-lab">A 强</span><span class="quant-ru-kpi-val">${
        counts.A ?? 0
      }</span></div>` +
      `<div class="quant-ru-kpi is-B"><span class="quant-ru-kpi-lab">B 中</span><span class="quant-ru-kpi-val">${
        counts.B ?? 0
      }</span></div>` +
      `<div class="quant-ru-kpi is-C"><span class="quant-ru-kpi-lab">C 弱</span><span class="quant-ru-kpi-val">${
        counts.C ?? 0
      }</span></div>` +
      `<div class="quant-ru-kpi"><span class="quant-ru-kpi-lab">有样本</span><span class="quant-ru-kpi-val">${nSample}<span class="quant-ru-kpi-sub">/${nPool}</span></span></div>` +
      `</div>` +
      mixBarHtml(counts) +
      `<p class="quant-ru-list-note">${escapeHtml(headNote)}</p>` +
      `<div class="quant-ru-tier-folds">${sections}</div>` +
      `</div>`;
  }

  function applyLive(live) {
    liveStatus = live && typeof live === "object" ? live : null;
  }

  function applyTiers(rep) {
    lastTiers = rep && rep.success ? rep : null;
    if (rep && rep.live) applyLive(rep.live);
    renderTierTable(lastTiers);
    if (lastTiers) {
      const c = lastTiers.counts || {};
      const hold = lastTiers.holdout_n != null ? lastTiers.holdout_n : "—";
      const lb = lastTiers.lookback != null ? lastTiers.lookback : "—";
      const src =
        lastTiers.source === "oo_research_model"
          ? "研究套"
          : lastTiers.source === "oo_holdout_oos_refit"
            ? "现训"
            : "ŷ_oo";
      setStatus(
        "ok",
        liveStatus && liveStatus.enabled ? "live" : "已分档",
        `A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0} · ${src} · 窗${lb} · Holdout${hold} · ${liveBit()}`
      );
    } else if (liveStatus && liveStatus.enabled) {
      setStatus("ok", "live", `${liveBit()} · 可先「观察池分档」刷新`);
    }
  }

  async function loadTiers({ useLast = false, quiet = false } = {}) {
    if (!document.getElementById("quant-section-research-universe")) return null;
    const h = holdoutN();
    const lb = ooLookbackN();
    if (!quiet) {
      setStatus(
        "busy",
        useLast ? "读取中" : "分档中",
        useLast
          ? "上次 Holdout OOS 分档…"
          : `研究套 ŷ_oo · 窗 ${lb} · Holdout ${h} 日前半（整池拉面板约需数分钟，勿刷新）…`
      );
    }
    const url = useLast
      ? "/api/quant/research-universe/predictability-tiers/last"
      : `/api/quant/research-universe/predictability-tiers?holdout=${encodeURIComponent(
          h
        )}&lookback=${encodeURIComponent(lb)}&min_n=20&head=oo&pool=watching`;
    const { ok, data, error } = await apiFetch(url);
    if (!ok || !data || data.success === false) {
      if (data && data.live) applyLive(data.live);
      if (!quiet) {
        setStatus(
          "error",
          useLast ? "无上次" : "失败",
          String(error || data?.note || data?.detail || data?.error || "分档失败")
        );
      }
      if (!useLast) applyTiers(null);
      else if (data && data.live) applyTiers(null);
      return null;
    }
    applyTiers(data);
    return data;
  }

  on("quant-ru-tiers", "click", (e) => {
    e.preventDefault();
    void loadTiers({ useLast: false });
  });

  function clampWatchingMaxSize(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 300;
    return Math.max(200, Math.min(1000, Math.round(n)));
  }

  function paintPoolSize(cap) {
    const n = clampWatchingMaxSize(cap);
    const input = el.maxSize();
    const out = el.poolVal();
    if (input) {
      input.value = String(n);
      input.setAttribute("aria-valuenow", String(n));
      const pct = ((n - 200) / 800) * 100;
      const host = input.closest(".quant-watching-pool-slider");
      if (host) host.style.setProperty("--pool-pct", `${pct}%`);
    }
    if (out) out.value = String(n);
    return n;
  }

  function setPoolStatus(message, isError) {
    const st = el.poolStatus();
    if (!st) return;
    st.textContent = message || "";
    st.classList.toggle("is-error", !!isError);
  }

  function syncPoolOverview(n) {
    const count = Number(n);
    if (!Number.isFinite(count) || count < 0) return;
    try {
      syncOverviewUniverse(count);
    } catch (_) {
      /* overview optional */
    }
  }

  async function hydrateWatchingMaxSize() {
    const input = el.maxSize();
    if (!input) return;
    try {
      const { ok, data } = await apiFetch("/api/watching/file");
      const watching = data && data.watching;
      const cap = Number((watching && watching.max_size) || (data && data.max_size));
      if (ok && Number.isFinite(cap)) paintPoolSize(cap);
      const wl = watching && watching.watchlist;
      if (ok && Array.isArray(wl)) {
        syncPoolOverview(wl.filter((c) => String(c || "").trim()).length);
      }
    } catch (_) {
      paintPoolSize(input.value || 300);
    }
  }

  async function saveWatchingMaxSize() {
    const input = el.maxSize();
    if (!input) return;
    const cap = paintPoolSize(input.value);
    const { ok, data, error } = await apiFetch("/api/watching/max-size", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_size: cap }),
    });
    if (!ok || (data && data.ok === false)) {
      const fail = (data && (data.detail || data.error)) || error || "保存失败";
      const msg = /not found/i.test(String(fail))
        ? "保存接口未加载，请重启 Web"
        : String(fail);
      setPoolStatus(msg, true);
      return;
    }
    paintPoolSize(data && data.max_size != null ? data.max_size : cap);
    setPoolStatus("", false);
    syncPoolOverview(data && data.count);
  }

  on("quant-watching-max-size", "input", () => {
    const input = el.maxSize();
    if (input) paintPoolSize(input.value);
  });
  on("quant-watching-max-size", "change", () => {
    void saveWatchingMaxSize();
  });

  if (el.maxSize()) {
    void hydrateWatchingMaxSize().catch(() => {});
  }
  if (document.getElementById("quant-section-research-universe")) {
    void loadTiers({ useLast: true, quiet: true }).catch(() => {});
  }

  return { loadTiers, applyTiers };
}
