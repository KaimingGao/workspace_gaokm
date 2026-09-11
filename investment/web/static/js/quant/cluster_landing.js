/**
 * 分组摘要 live 段 HTML（纯字符串）。
 * 卡网格与「因子摘要」同壳：name + state + desc + 三度量。
 */
import { escapeHtml } from "../shared.js";

/** naive ISO（无 Z）按 UTC，与 live JSON 一致。 */
function fmtClusterTs(iso) {
  if (!iso) return "";
  let raw = String(iso).trim();
  if (!raw) return "";
  if (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(raw) &&
    !/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)
  ) {
    raw = raw.replace(/\.\d+$/, "") + "Z";
  }
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) {
    return raw.replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 16);
  }
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
    d.getMinutes()
  )}`;
}

function splitTs(txt) {
  const m = String(txt || "").match(/^(\d{2}-\d{2})\s+(\d{2}:\d{2})/);
  if (!m) return { date: txt || "—", time: "—" };
  return { date: m[1], time: m[2] };
}

function metric(label, value, extraCls = "", title = "") {
  const raw = value == null || value === "" ? "—" : String(value);
  return (
    `<div class="quant-pro-factor-metric quant-cluster-kpi" title="${escapeHtml(
      title
    )}">` +
    `<span class="quant-pro-factor-metric-label">${escapeHtml(label)}</span>` +
    `<span class="quant-pro-factor-metric-value${
      extraCls ? ` ${extraCls}` : ""
    }">${escapeHtml(raw)}</span>` +
    `</div>`
  );
}

function summaryCard({ name, state, stateLabel, desc, metricsHtml, title, extraClass }) {
  return (
    `<div class="quant-pro-factor-card" data-state="${escapeHtml(state)}" title="${escapeHtml(title || "")}">` +
    `<div class="quant-pro-factor-card-head">` +
    `<span class="quant-pro-factor-card-name">${escapeHtml(name)}</span>` +
    `<span class="quant-pro-factor-card-state${
      extraClass ? ` ${extraClass}` : ""
    }">${escapeHtml(stateLabel)}</span>` +
    `</div>` +
    `<div class="quant-pro-factor-card-desc">${escapeHtml(desc || "—")}</div>` +
    `<div class="quant-pro-factor-card-metrics">${metricsHtml}</div>` +
    `</div>`
  );
}

function universeFitTiersHtml(cs) {
  const raw = Array.isArray(cs.universe_fit_tiers)
    ? cs.universe_fit_tiers
    : ["A", "B", "C"];
  const set = new Set(
    raw.map((t) => String(t || "").toUpperCase()).filter((t) => t === "A" || t === "B" || t === "C")
  );
  if (!set.size) {
    set.add("A");
    set.add("B");
    set.add("C");
  }
  const row = (id, label) =>
    `<label class="quant-cluster-universe-tier">` +
    `<input type="checkbox" id="quant-universe-tier-live-${id.toLowerCase()}" value="${id}" ${
      set.has(id) ? "checked" : ""
    } /> ${escapeHtml(label)}</label>`;
  return (
    `<fieldset class="quant-cluster-universe-tiers" title="观察池按拟合档限制新开/加仓宇宙；已持仓仍可卖/持。C 档即使入选，OOS 失败仍拦新买。回测页可另选对照，不改这里。">` +
    `<legend>宇宙分档</legend>` +
    row("A", "A 强") +
    row("B", "B 中") +
    row("C", "C 弱") +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="universe-fit-tiers" title="写入 cluster_scoring.universe_fit_tiers，影响 live 观察池新开/加。">保存宇宙</button>` +
    `</fieldset>`
  );
}

export function clusterLandingHtml(data) {
  const cs = (data && data.cluster_scoring) || {};
  const act = (data && data.active) || {};
  const draft = (data && data.draft) || {};
  const h = (data && data.health) || {};
  const land = (data && data.landing) || {};
  const mode = cs.mode || "off";
  const liveN =
    act.n_clusters != null
      ? Number(act.n_clusters)
      : Array.isArray(act.clusters)
        ? act.clusters.length
        : null;
  const researchN =
    data.research_n_clusters != null
      ? Number(data.research_n_clusters)
      : null;
  const modeLabel =
    mode === "active" ? "已启用" : mode === "shadow" ? "对照中" : "未接通";
  const modeState =
    mode === "active" ? "active" : mode === "shadow" ? "testing" : "drop";
  const modeDesc =
    mode === "active"
      ? "组ŷ 选股真源"
      : mode === "shadow"
        ? "对照 · 不改交易执行"
        : "live 映射未接通";
  const cov =
    h.coverage != null ? `${Math.round(Number(h.coverage) * 100)}%` : "—";
  const age = h.age_days != null ? `${h.age_days}d` : "";
  const alerts = (h.alerts || []).slice(0, 3);
  const canApply = !!land.can_apply || !!draft.exists || !!act.exists;
  const canActivate = !!land.can_activate;
  const readyFollow = !!land.ready_for_follow || mode === "active";
  const nextStep = land.next_step || "";
  const doneResearch = nextStep === "go_follow" || readyFollow;
  const next = land.next_label || "① 对照";
  const nextPrefix = doneResearch ? "" : "下一步 ";
  const ev = (data && data.enable_evidence) || {};
  const evGate = ev.gate || {};
  const evBlockers = Array.isArray(evGate.blockers) ? evGate.blockers : [];
  const evWarns = Array.isArray(evGate.warnings) ? evGate.warnings : [];
  const oos = ev.oos_summary || {};
  const turn = ev.turnover_est || {};
  const exp = ev.exposure_summary || {};
  const topSec = exp.top_sector || null;
  // OOS 失败组固定剔主分（遗留开关 exclude_oos_failed_groups 已退役）
  const excludeOos = true;

  const maxNames =
    cs.max_names != null
      ? cs.max_names
      : ev.max_names != null
        ? ev.max_names
        : "—";
  const minScore = ev.min_score != null ? ev.min_score : "—";
  const ver =
    act.version != null && act.version !== "" ? `v${act.version}` : "";
  const groupMismatch =
    researchN != null && liveN != null && researchN !== liveN;

  const fitIso = act.source_created_at || h.fitted_as_of || "";
  const promoIso = act.promoted_at || h.promoted_at || "";
  const fitTxt = fmtClusterTs(fitIso);
  const promoTxt = fmtClusterTs(promoIso);
  const fitParts = splitTs(fitTxt);
  const promoParts = splitTs(promoTxt);

  const cards = [];

  cards.push(
    summaryCard({
      name: "状态",
      state: modeState,
      stateLabel: modeLabel,
      desc: [ver, modeDesc].filter(Boolean).join(" · ") || modeDesc,
      extraClass: "quant-cluster-mode",
      title: "全局排序：组ŷ 打分后按 score 排序，再按 ŷ 门槛 / max 截断",
      metricsHtml:
        metric("研究", researchN != null ? String(researchN) : "—", "", "研究区组数（跑分组）") +
        metric(
          "live",
          liveN != null ? String(liveN) : "—",
          groupMismatch ? "is-warn" : "",
          groupMismatch
            ? "「跑分组」只更新研究区；须点「对照」才把 live 映射换成新组数"
            : "live 映射组数"
        ) +
        metric("覆盖", cov, "", "观察池落入 live 映射"),
    })
  );

  const ageState = !age ? "drop" : h.stale ? "warn" : "mid";
  const ageBadge = !age ? "—" : h.stale ? "偏旧" : "新鲜";
  cards.push(
    summaryCard({
      name: "龄",
      state: ageState,
      stateLabel: ageBadge,
      desc: h.stale ? "映射偏旧，建议重估" : "距拟合 / 晋升",
      title: h.stale ? "映射偏旧，建议重估" : "距拟合/晋升的天数",
      metricsHtml:
        metric("天数", age || "—", h.stale ? "is-warn" : "is-accent") +
        metric(
          "重估",
          h.refit_suggested ? "建议" : "—",
          h.refit_suggested ? "is-warn" : "",
          "健康建议重估组 β"
        ) +
        metric(
          "IC",
          h.ic_demote ? "破线" : "—",
          h.ic_demote ? "is-warn" : "",
          "ŷ IC 破线，建议降为对照"
        ),
    })
  );

  cards.push(
    summaryCard({
      name: "拟合",
      state: fitTxt ? "mid" : "drop",
      stateLabel: fitTxt ? "时刻" : "—",
      desc: "组 β 产物时刻",
      title: `组 β 产物时刻 source_created_at · ${fitIso}`,
      metricsHtml:
        metric("日期", fitParts.date) +
        metric("时间", fitParts.time, "is-accent") +
        metric("产物", fitTxt ? "β" : "—"),
    })
  );

  cards.push(
    summaryCard({
      name: "晋升",
      state: promoTxt ? "mid" : "drop",
      stateLabel: promoTxt ? "时刻" : "—",
      desc: "写入 live 映射",
      title: `写入 live 映射 promoted_at · ${promoIso}`,
      metricsHtml:
        metric("日期", promoParts.date) +
        metric("时间", promoParts.time, "is-accent") +
        metric("写入", promoTxt ? "live" : "—"),
    })
  );

  const pf = (data && data.promote_preflight) || null;
  let preflightExtra = "";
  if (pf && draft.exists) {
    const ready = pf.promote_ready === true;
    const dOos = pf.draft_oos || {};
    const aOos = pf.active_oos || {};
    const delta = pf.delta || {};
    const greedy = pf.greedy || {};
    const blockers = Array.isArray(pf.blockers) ? pf.blockers : [];
    const warns = Array.isArray(pf.warnings) ? pf.warnings : [];
    const dPct =
      dOos.fail_rate != null ? Math.round(Number(dOos.fail_rate) * 100) : null;
    const aPct =
      aOos.fail_rate != null ? Math.round(Number(aOos.fail_rate) * 100) : null;
    const dPp =
      delta.oos_fail_rate != null
        ? Math.round(Number(delta.oos_fail_rate) * 100)
        : null;
    const greedyLine =
      greedy && greedy.mode
        ? `greedy ${String(greedy.mode)}${
            greedy.n_swaps != null ? `×${String(greedy.n_swaps)}` : ""
          }`
        : "";
    const checks = Array.isArray(pf.checklist) ? pf.checklist : [];
    const failChecks = checks.filter((c) => c && c.ok === false);
    const checkTip = checks
      .map((c) => {
        const ok = c && c.ok !== false;
        const lab = (c && (c.label || c.id)) || "?";
        return `${ok ? "✓" : "✗"}${lab}`;
      })
      .join(" · ");
    const pfDesc = greedyLine || "draft vs live · 仅看 OOS 闸";
    const deltaTxt =
      dPp == null ? "—" : `${dPp >= 0 ? "+" : ""}${dPp}pp`;
    const deltaCls =
      dPp == null ? "" : dPp > 0 ? "is-neg" : dPp < 0 ? "is-pos" : "";
    cards.push(
      summaryCard({
        name: "预检",
        state: ready ? "strong" : "warn",
        stateLabel: ready ? "过门" : "暂不可",
        desc: pfDesc,
        title: `B3 draft vs active；promote_ready 仅看 OOS 闸${
          checkTip ? ` · ${checkTip}` : ""
        }`,
        metricsHtml:
          metric("draft", dPct != null ? `${dPct}%` : "—", "", "draft OOS 失败率") +
          metric("live", aPct != null ? `${aPct}%` : "—", "", "active OOS 失败率") +
          metric("Δ", deltaTxt, deltaCls, "draft − active · pp"),
      })
    );
    const checkHtml = failChecks.length
      ? failChecks
          .map((c) => {
            const lab = (c && (c.label || c.id)) || "?";
            return (
              `<span class="quant-cluster-check is-fail">✗${escapeHtml(lab)}</span>`
            );
          })
          .join("")
      : "";
    let warnHtml = "";
    if (blockers.length) {
      warnHtml += `<span class="quant-cluster-landing-alerts down">${escapeHtml(
        blockers.join("；")
      )}</span>`;
    } else if (warns.length) {
      const softOos =
        ready && dPct != null && aPct != null && dPct > aPct
          ? `软提示 ${dPct}%>${aPct}%`
          : "";
      warnHtml += `<span class="quant-cluster-landing-alerts" title="${escapeHtml(
        warns.join("；")
      )}">${escapeHtml(softOos || warns[0])}</span>`;
    }
    if (checkHtml || warnHtml) {
      preflightExtra =
        `<div class="quant-cluster-preflight${ready ? "" : " is-warn"}">` +
        (checkHtml ? `<span class="quant-cluster-checks">${checkHtml}</span>` : "") +
        warnHtml +
        `</div>`;
    }
  }

  const cols = Math.min(Math.max(cards.length, 1), 5);
  const grid =
    `<div class="quant-cluster-summary-grid" style="--cluster-summary-cols:${cols}">` +
    cards.join("") +
    `</div>`;

  const alertHtml = alerts.length
    ? `<p class="quant-cluster-landing-alerts">${escapeHtml(
        alerts.join("；")
      )}</p>`
    : "";

  const evidenceBits = [];
  if (minScore !== "—" || maxNames !== "—") {
    evidenceBits.push(
      `<span title="组ŷ 打分后全局排序，再按 ŷ 门槛 / max 截断">ŷ门槛=${escapeHtml(
        String(minScore)
      )} · max=${escapeHtml(String(maxNames))}</span>`
    );
  }
  const oosHas =
    oos.pass_count != null ||
    oos.fail_count != null ||
    oos.skip_count != null ||
    oos.unknown_count != null ||
    oos.note;
  if (oosHas) {
    evidenceBits.push(
      `<span title="${escapeHtml(
        "汇总各组 oos_gate：通过/失败/跳过/未知。悬停各组「OOS✓/未过」标签可见原因。"
      )}">OOS ${escapeHtml(String(oos.pass_count ?? "—"))}/${escapeHtml(
        String(oos.fail_count ?? "—")
      )}/${escapeHtml(String(oos.skip_count ?? "—"))}/${escapeHtml(
        String(oos.unknown_count ?? "—")
      )}${oos.note ? ` · ${escapeHtml(String(oos.note))}` : ""}</span>`
    );
  }
  evidenceBits.push(
    `<span class="quant-oos-semantics" title="${escapeHtml(
      excludeOos
        ? "开：失败组主分降为 heuristic；组ŷ仅 tip 对照。基线=heuristic；研究臂=ŷ。过门≠自动 promote。"
        : "关：失败组主分仍可用组 β。基线=heuristic；研究臂=ŷ。过门≠自动 promote。"
    )}">OOS 失败组${excludeOos ? "主分降级" : "主分可用组β"}</span>`
  );
  if (!turn.deprecated && (turn.would_sell_count || turn.would_buy_count)) {
    evidenceBits.push(
      `<span title="相对纸面 vs 观察池 rank_lots">换手 卖 ${escapeHtml(
        String(turn.would_sell_count ?? "—")
      )} · 买 ${escapeHtml(String(turn.would_buy_count ?? "—"))}</span>`
    );
  }
  if (topSec) {
    evidenceBits.push(
      `<span>行业 ${escapeHtml(
        `${topSec.name}${topSec.weight_pct != null ? " " + topSec.weight_pct + "%" : ""}`
      )}</span>`
    );
  }
  if (evBlockers.length) {
    evidenceBits.push(
      `<span class="down">拦：${escapeHtml(evBlockers.join("；"))}</span>`
    );
  }
  if (evWarns.length) {
    evidenceBits.push(
      `<span class="is-warn">提示：${escapeHtml(evWarns.join("；"))}</span>`
    );
  }
  const evidenceOpen = mode === "shadow" || !!evBlockers.length;
  const showEvidence = evidenceOpen || evGate.ok === false;
  const evidenceHtml =
    mode === "off" && !act.exists
      ? ""
      : showEvidence
        ? `<details class="quant-cluster-evidence" ${
            evidenceOpen ? "open" : ""
          }>` +
          `<summary>启用证据 ` +
          `<span class="sub${evGate.ok === false ? " down" : ""}">${
            evGate.ok === false ? "未通过" : evGate.ok ? "可启用" : "—"
          }</span></summary>` +
          `<div class="quant-cluster-evidence-list">${evidenceBits.join(
            `<span class="quant-cluster-evidence-sep">·</span>`
          )}</div></details>`
        : "";

  const needDemote =
    nextStep === "demote_shadow" ||
    (mode === "active" && (!canActivate || h.suggest_demote || h.ic_demote));
  const actions =
    `<div class="quant-cluster-landing-actions" id="quant-cluster-live-bar">` +
    (needDemote
      ? `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
        `data-cluster-export="live-shadow" ` +
        `title="ŷ IC / 健康未过：先降为对照（shadow），交易执行不再用组ŷ；再跑分组重估。">降为对照</button>`
      : `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
        `data-cluster-export="live-apply" ${canApply ? "" : "disabled"} ` +
        `title="晋升分组映射 → 进入对照（shadow）。此步不切换交易执行选股真源。">① 对照</button>`) +
    `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
    `data-cluster-export="live-active" ${canActivate ? "" : "disabled"} ` +
    `title="证据包与健康门禁通过后，将交易执行选股切换为组ŷ。不改写 signal_config.weights。">② 启用</button>` +
    (doneResearch && !needDemote
      ? ""
      : `<span class="sub quant-cluster-landing-next">${nextPrefix}${escapeHtml(
          next
        )}</span>`) +
    `<span class="quant-cluster-advanced-actions">` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-off">关闭</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-rollback">回滚</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="artifact">导出映射</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-refit" ` +
    `title="跳到研究枢纽「跑分组」重估组 β（人审后对照/启用）。">建议重估</button>` +
    `</span></div>`;

  const midBits = [universeFitTiersHtml(cs), preflightExtra, evidenceHtml].filter(Boolean);
  const mid = midBits.length
    ? `<div class="quant-cluster-landing-mid">${midBits.join("")}</div>`
    : "";

  return (
    `<div class="quant-cluster-landing-card">` +
    grid +
    mid +
    alertHtml +
    actions +
    `</div>`
  );
}
