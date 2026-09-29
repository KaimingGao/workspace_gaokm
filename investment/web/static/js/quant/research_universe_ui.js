/**
 * 观察池分档：Holdout 前半打档 → last；回测/live 复用档位过滤宇宙（回测天数独立）。
 */
export function installResearchUniverseUi(q) {
  const { on, apiFetch, escapeHtml, readHoldoutTradingDays } = q;
  let lastTiers = null;
  let liveStatus = null;

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

  function renderTierTable(rep) {
    const host = el.tierTable();
    if (!host) return;
    if (!rep || !rep.success || !Array.isArray(rep.rows) || !rep.rows.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    const thr = rep.thresholds || {};
    const minNEff = thr.min_n_effective != null ? thr.min_n_effective : thr.min_n ?? 20;
    const minNReq = thr.min_n != null ? thr.min_n : 20;
    const nBit =
      thr.min_n_adapted || minNEff !== minNReq
        ? `n≥${minNEff}(账本短·配${minNReq})`
        : `n≥${minNEff}`;
    const hold = rep.holdout_n != null ? rep.holdout_n : "—";
    const tierN = rep.tier_n != null ? rep.tier_n : rep.n_ledger_dates ?? "—";
    const head = String(rep.head || "oo").toLowerCase();
    const headBit =
      head === "oo" || head === "y_oo" || head === "ŷ_oo"
        ? "y_oo 分档"
        : `${escapeHtml(rep.head_label || rep.head || "y_oo")} 分档`;
    const headNote = [
      headBit,
      `Holdout${hold} · 前半${tierN}日`,
      `A≥${Math.round((thr.a_hit ?? 0.55) * 100)}%`,
      `B≥${Math.round((thr.b_hit ?? 0.5) * 100)}%`,
      nBit,
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
        const rows = byTier[tier];
        const open = tier === "A" ? " open" : "";
        const slice = rows.slice(0, maxShow);
        const trs = slice
          .map(
            (r) => `<tr>
            <td>${escapeHtml(r.code || "")}</td>
            <td>${escapeHtml(r.name || "—")}</td>
            <td>${fmtHit(r.hit_rate)}</td>
            <td>${r.n_valid ?? 0}</td>
            <td>${fmtIc(r.ic)}</td>
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
                <th>代码</th><th>名称</th><th title="y_oo 符号命中"><span class="quant-ru-token">y_oo</span> 命中</th><th>n</th><th>IC</th>
              </tr></thead>
              <tbody>${trs}</tbody>
            </table>${more}`
          : `<p class="quant-ru-list-empty">无</p>`;
        return `<details class="quant-ru-tier-fold" data-tier="${tier}"${open}>
          <summary class="quant-ru-tier-fold-sum">
            <span class="quant-ru-tier-badge is-${tier}">${tier}</span>
            <span class="quant-ru-tier-fold-count">${rows.length}</span>
          </summary>
          <div class="quant-ru-tier-fold-body">${body}</div>
        </details>`;
      })
      .join("");

    host.hidden = false;
    host.innerHTML = `<p class="quant-ru-list-note">${headNote}</p>
      <div class="quant-ru-tier-folds">${sections}</div>`;
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
        `A${c.A ?? 0} · B${c.B ?? 0} · C${c.C ?? 0} · y_oo 分档 · Holdout${hold} · ${liveBit()}`
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
          ? "上次 Holdout 分档…"
          : `Holdout ${h} 日前半 · y_oo 打档…`
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
        "启用 live？将复用当前 Holdout 前半分档；调仓新开只留 A+B（持仓保留）。"
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
