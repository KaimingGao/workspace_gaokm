/**
 * 观察池分档：ŷ_oo Holdout 前半 OOS 打档 → last；回测/live 复用档位（回测天数独立）。
 */
export function installResearchUniverseUi(q) {
  const { on, apiFetch, escapeHtml, readHoldoutTradingDays } = q;
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
  };

  function holdoutN() {
    if (typeof readHoldoutTradingDays === "function") {
      return Math.max(2, Math.min(90, Number(readHoldoutTradingDays()) || 20));
    }
    return 20;
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
    if (!liveStatus || !liveStatus.enabled) return "未启用 live";
    const allow = (liveStatus.allowed_tiers || ["A", "B"]).join("") || "AB";
    return `live ${allow}`;
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
      `A 命中>${Math.round(aHit * 100)}% · IC>0 · N>${aMinN}`,
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
      setStatus(
        "ok",
        liveStatus && liveStatus.enabled ? "live" : "已分档",
        `A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0} · ŷ_oo OOS · Holdout${hold} · ${liveBit()}`
      );
    } else if (liveStatus && liveStatus.enabled) {
      setStatus("ok", "live", `${liveBit()} · 可先「观察池分档」刷新`);
    }
  }

  async function loadTiers({ useLast = false, quiet = false } = {}) {
    if (!document.getElementById("quant-section-research-universe")) return null;
    const h = holdoutN();
    if (!quiet) {
      setStatus(
        "busy",
        useLast ? "读取中" : "分档中",
        useLast
          ? "上次 Holdout OOS 分档…"
          : `Holdout ${h} 日前半 · ŷ_oo OOS 打档…`
      );
    }
    const url = useLast
      ? "/api/quant/research-universe/predictability-tiers/last"
      : `/api/quant/research-universe/predictability-tiers?holdout=${encodeURIComponent(
          h
        )}&min_n=20&head=oo&pool=watching`;
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

  async function promoteLive() {
    if (
      !window.confirm(
        "启用 live？将复用当前 ŷ_oo Holdout OOS 前半分档；调仓新开只留 A+B（持仓保留）。"
      )
    ) {
      return;
    }
    setStatus("busy", "启用", "写入 live active…");
    const { ok, data, error } = await apiFetch(
      "/api/quant/research-universe/predictability-tiers/promote",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
    );
    if (!ok || !data || data.ok === false) {
      setStatus(
        "error",
        "失败",
        String(error || data?.detail || data?.error || "启用失败")
      );
      return;
    }
    applyLive(data.live || { enabled: true, allowed_tiers: ["A", "B"] });
    if (lastTiers) applyTiers(lastTiers);
    else setStatus("ok", "live", liveBit());
  }

  async function demoteLive() {
    if (!window.confirm("关闭 live 分档闸？调仓将不再按档过滤。")) return;
    setStatus("busy", "关闭", "清除 live active…");
    const { ok, data, error } = await apiFetch(
      "/api/quant/research-universe/predictability-tiers/demote",
      { method: "POST" }
    );
    if (!ok || !data || data.ok === false) {
      setStatus(
        "error",
        "失败",
        String(error || data?.detail || data?.error || "关闭失败")
      );
      return;
    }
    applyLive(data.live || { enabled: false });
    if (lastTiers) applyTiers(lastTiers);
    else setStatus("ok", "已关", "live 闸已关");
  }

  on("quant-ru-tiers", "click", (e) => {
    e.preventDefault();
    void loadTiers({ useLast: false });
  });
  on("quant-ru-tiers-promote", "click", (e) => {
    e.preventDefault();
    void promoteLive();
  });
  on("quant-ru-tiers-demote", "click", (e) => {
    e.preventDefault();
    void demoteLive();
  });

  if (document.getElementById("quant-section-research-universe")) {
    void loadTiers({ useLast: true, quiet: true }).catch(() => {});
  }

  return { loadTiers, applyTiers };
}
