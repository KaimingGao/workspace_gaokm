/**
 * 分组权落地卡片 HTML（纯字符串）。
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

function clockStat(iso, label, title) {
  const txt = fmtClusterTs(iso);
  if (!txt) return "";
  return (
    `<span class="quant-cluster-stat" title="${escapeHtml(title || "")}">` +
    `<b>${escapeHtml(txt)}</b> ${escapeHtml(label)}</span>`
  );
}

function clusterClockStats(act, h) {
  const fitIso = (act && act.source_created_at) || (h && h.fitted_as_of) || "";
  const promoIso = (act && act.promoted_at) || (h && h.promoted_at) || "";
  const fitTxt = fmtClusterTs(fitIso);
  const promoTxt = fmtClusterTs(promoIso);
  if (fitTxt && promoTxt && fitTxt === promoTxt) {
    return clockStat(
      promoIso,
      "拟合/晋升",
      `组 β 拟合并写入 live · ${promoIso}`
    );
  }
  return (
    clockStat(fitIso, "拟合", `组 β 产物时刻 source_created_at · ${fitIso}`) +
    clockStat(promoIso, "晋升", `写入 live 映射 promoted_at · ${promoIso}`)
  );
}

function shadowBookStat(sh, label) {
  const lab = String(label || "影");
  if (!sh || !sh.exists) {
    return (
      `<span class="quant-cluster-stat" title="${escapeHtml(lab)} 影子簿尚未写出（刷簿后出现；不驱动买入）">` +
      `<b>—</b> ${escapeHtml(lab)}影</span>`
    );
  }
  const vs = sh.vs_eod || {};
  const j =
    vs.jaccard != null && Number.isFinite(Number(vs.jaccard))
      ? Number(vs.jaccard).toFixed(2)
      : "—";
  const n = sh.name_count != null ? sh.name_count : "—";
  const nord = (sh.meta || {}).nordhaus_revision_slope;
  const nordTxt =
    nord != null && Number.isFinite(Number(nord))
      ? ` · Nordhaus=${Number(nord).toFixed(2)}`
      : "";
  return (
    `<span class="quant-cluster-stat" title="${escapeHtml(
      lab
    )} 影子簿 vs EOD 重叠 Jaccard=${escapeHtml(String(j))}${escapeHtml(
      nordTxt
    )}${
      sh.updated_at ? ` · 落盘 ${fmtClusterTs(sh.updated_at)}` : ""
    } · 不驱动 execution">` +
    `<b>${escapeHtml(String(n))}</b> ${escapeHtml(lab)}影 · J=${escapeHtml(
      String(j)
    )}</span>`
  );
}

export function clusterLandingHtml(data) {
  const cs = (data && data.cluster_scoring) || {};
  const act = (data && data.active) || {};
  const draft = (data && data.draft) || {};
  const book = (data && data.book) || {};
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
    mode === "active"
      ? "已启用组ŷ"
      : mode === "shadow"
        ? "对照中（映射已就绪）"
        : "未接通";
  const cov =
    h.coverage != null ? `${Math.round(Number(h.coverage) * 100)}%` : "—";
  const age = h.age_days != null ? `${h.age_days}d` : "—";
  const alerts = (h.alerts || []).slice(0, 3);
  const canApply = !!land.can_apply || !!draft.exists || !!act.exists;
  const canActivate = !!land.can_activate;
  const readyFollow = !!land.ready_for_follow || mode === "active";
  const nextStep = land.next_step || "";
  const doneResearch = nextStep === "go_follow" || readyFollow;
  const next = land.next_label || "① 对照";
  const nextPrefix = doneResearch ? "状态：" : "下一步：";
  const ev = (data && data.enable_evidence) || {};
  const evGate = ev.gate || {};
  const evBlockers = Array.isArray(evGate.blockers) ? evGate.blockers : [];
  const evWarns = Array.isArray(evGate.warnings) ? evGate.warnings : [];
  const oos = ev.oos_summary || {};
  const turn = ev.turnover_est || {};
  const exp = ev.exposure_summary || {};
  const topSec = exp.top_sector || null;
  // OOS 失败组固定剔主分/主簿（遗留开关 exclude_oos_failed_groups 已退役）
  const excludeOos = true;

  const maxNames =
    book.max_names != null
      ? book.max_names
      : cs.max_names != null
        ? cs.max_names
        : ev.max_names != null
          ? ev.max_names
          : "—";
  const minScore =
    book.min_score != null
      ? book.min_score
      : ev.min_score != null
        ? ev.min_score
        : "—";
  const stats =
    `<div class="quant-cluster-landing-stats">` +
    `<span class="quant-cluster-stat"><b>${escapeHtml(modeLabel)}</b> mode</span>` +
    `<span class="quant-cluster-stat"><b>v${escapeHtml(
      String(act.version != null ? act.version : "—")
    )}</b> 映射</span>` +
    (researchN != null && liveN != null && researchN !== liveN
      ? `<span class="quant-cluster-stat is-warn" title="「跑分组」只更新研究区；须点「对照」才把 live 映射换成新组数"><b>研究 ${escapeHtml(
          String(researchN)
        )} / live ${escapeHtml(String(liveN))}</b> 组</span>`
      : liveN != null
        ? `<span class="quant-cluster-stat"><b>${escapeHtml(
            String(liveN)
          )}</b> live组</span>`
        : "") +
    `<span class="quant-cluster-stat" title="组ŷ 打分后全局按 score 排序，再按 ŷ 门槛 / max 截断"><b>全局</b> 排序</span>` +
    `<span class="quant-cluster-stat"><b>${escapeHtml(cov)}</b> 覆盖</span>` +
    clusterClockStats(act, h) +
    `<span class="quant-cluster-stat${h.stale ? " is-warn" : ""}"><b>${escapeHtml(
      age
    )}</b> 龄</span>` +
    (h.refit_suggested
      ? `<span class="quant-cluster-stat is-warn"><b>重估</b> 建议</span>`
      : "") +
    (h.ic_demote
      ? `<span class="quant-cluster-stat is-warn"><b>IC</b> 破线</span>`
      : "") +
    `<span class="quant-cluster-stat" title="合并簿只数=账户调仓目标${
      book.updated_at ? ` · 落盘 ${book.updated_at}` : ""
    }"><b>${escapeHtml(
      String(book.name_count != null ? book.name_count : "—")
    )}</b> 簿</span>` +
    clockStat(
      book.updated_at,
      "刷簿",
      `目标簿落盘 updated_at · ${book.updated_at || ""}`
    ) +
    shadowBookStat(data && data.tau_shadow_book, "τ") +
    shadowBookStat(data && data.nowcast_shadow_book, "ŷ_nowcast") +
    `</div>`;

  const pf = (data && data.promote_preflight) || null;
  let preflightHtml = "";
  if (pf && draft.exists) {
    const ready = pf.promote_ready === true;
    const dOos = pf.draft_oos || {};
    const aOos = pf.active_oos || {};
    const delta = pf.delta || {};
    const greedy = pf.greedy || {};
    const blockers = Array.isArray(pf.blockers) ? pf.blockers : [];
    const warns = Array.isArray(pf.warnings) ? pf.warnings : [];
    const failLine =
      dOos.fail_rate != null
        ? `draft失败率 ${Math.round(Number(dOos.fail_rate) * 100)}%` +
          (aOos.fail_rate != null
            ? ` · active ${Math.round(Number(aOos.fail_rate) * 100)}%`
            : "") +
          (delta.oos_fail_rate != null
            ? ` · Δ${Number(delta.oos_fail_rate) >= 0 ? "+" : ""}${Math.round(
                Number(delta.oos_fail_rate) * 100
              )}pp`
            : "")
        : "OOS —";
    const greedyLine =
      greedy && greedy.mode
        ? ` · greedy ${escapeHtml(String(greedy.mode))}${
            greedy.n_swaps != null ? `×${escapeHtml(String(greedy.n_swaps))}` : ""
          }`
        : "";
    preflightHtml =
      `<div class="quant-cluster-preflight${ready ? "" : " is-warn"}" ` +
      `title="B3 draft vs active；promote_ready 仅看 OOS 闸">` +
      `<span class="quant-cluster-stat"><b>${
        ready ? "可晋升" : "暂不可晋升"
      }</b> 预检</span>` +
      `<span class="quant-cluster-stat">${escapeHtml(failLine)}${greedyLine}</span>` +
      (blockers.length
        ? `<p class="quant-cluster-landing-alerts down">${escapeHtml(
            blockers.join("；")
          )}</p>`
        : "") +
      (warns.length
        ? `<p class="quant-cluster-landing-alerts">${escapeHtml(
            warns.join("；")
          )}</p>`
        : "") +
      (() => {
        const cl = Array.isArray(pf.checklist) ? pf.checklist : [];
        if (!cl.length) return "";
        const bits = cl
          .map((c) => {
            const ok = c && c.ok !== false;
            const lab = (c && (c.label || c.id)) || "?";
            return `${ok ? "✓" : "✗"}${lab}`;
          })
          .join(" · ");
        return `<p class="sub quant-cluster-checklist" title="promote 硬清单（OOS 硬闸；其余多为软项）">${escapeHtml(
          bits
        )}</p>`;
      })() +
      `</div>`;
  }

  const alertHtml = alerts.length
    ? `<p class="quant-cluster-landing-alerts">${escapeHtml(
        alerts.join("；")
      )}</p>`
    : "";

  const evidenceOpen = mode === "shadow" || !!evBlockers.length;
  const evidenceHtml =
    mode === "off" && !act.exists
      ? ""
      : `<details class="quant-cluster-evidence" ${
          evidenceOpen ? "open" : ""
        }>` +
        `<summary>启用证据包 ` +
        `<span class="sub${evGate.ok === false ? " down" : ""}">${
          evGate.ok === false
            ? "未通过"
            : evGate.ok
              ? "可启用"
              : "—"
        }</span></summary>` +
        `<ul class="quant-cluster-evidence-list">` +
        `<li title="组ŷ→全局排序→ŷ 门槛过滤→max 截断">簿长 ${escapeHtml(
          String(ev.name_count != null ? ev.name_count : book.name_count ?? "—")
        )} · 全局排序 · ŷ门槛=${escapeHtml(
          String(minScore)
        )} · max=${escapeHtml(String(maxNames))}</li>` +
        `<li title="${escapeHtml(
          "汇总各组 oos_gate：通过/失败/跳过/未知。悬停各组「OOS✓/未过」标签可见原因。"
        )}">OOS 通过 ${escapeHtml(String(oos.pass_count ?? "—"))} / 失败 ${escapeHtml(
          String(oos.fail_count ?? "—")
        )} / 跳过 ${escapeHtml(String(oos.skip_count ?? "—"))} / 未知 ${escapeHtml(
          String(oos.unknown_count ?? "—")
        )}${
          oos.note ? ` · ${escapeHtml(String(oos.note))}` : ""
        }</li>` +
        `<li class="quant-oos-semantics" title="${escapeHtml(
          excludeOos
            ? "开：失败组不进簿；主分=全局ŷ/heuristic；组ŷ仅 tip 对照。基线=heuristic；研究臂=ŷ。过门≠自动 promote。"
            : "关：失败组可进簿且主分可用组 β。基线=heuristic；研究臂=ŷ。过门≠自动 promote。"
        )}">OOS：heuristic 基线 vs ŷ 研究臂 · ${
          excludeOos ? "失败组剔簿+主分降级" : "失败组可进簿"
        }</li>` +
        `<li title="相对纸面 vs 合并簿">换手估计 卖 ${escapeHtml(
          String(turn.would_sell_count ?? "—")
        )} · 买 ${escapeHtml(String(turn.would_buy_count ?? "—"))}</li>` +
        `<li>行业集中 ${
          topSec
            ? escapeHtml(
                `${topSec.name} ${topSec.weight_pct != null ? topSec.weight_pct + "%" : ""}`
              )
            : "—"
        }</li>` +
        (evBlockers.length
          ? `<li class="down">拦：${escapeHtml(evBlockers.join("；"))}</li>`
          : "") +
        (evWarns.length
          ? `<li class="is-warn">提示：${escapeHtml(evWarns.join("；"))}</li>`
          : "") +
        `</ul></details>`;

  const needDemote =
    nextStep === "demote_shadow" ||
    (mode === "active" && (!canActivate || h.suggest_demote || h.ic_demote));
  const actions =
    `<div class="quant-cluster-landing-actions">` +
    (needDemote
      ? `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
        `data-cluster-export="live-shadow" ` +
        `title="ŷ IC / 健康未过：先降为对照（shadow），交易执行不再用组ŷ；再跑分组重估。">降为对照</button>`
      : `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
        `data-cluster-export="live-apply" ${canApply ? "" : "disabled"} ` +
        `title="晋升分组映射 → 进入对照（shadow）→ 刷新目标簿。此步不切换交易执行选股真源。">① 对照</button>`) +
    `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
    `data-cluster-export="live-active" ${canActivate ? "" : "disabled"} ` +
    `title="证据包与健康门禁通过后，将交易执行选股切换为组ŷ。不改写 signal_config.weights。">② 启用</button>` +
    `<span class="sub quant-cluster-landing-next${
      doneResearch && !needDemote ? " is-done" : ""
    }">${nextPrefix}${escapeHtml(next)}</span>` +
    `</div>`;

  const more =
    `<div class="quant-cluster-more quant-cluster-advanced" id="quant-cluster-live-bar">` +
    `<div class="quant-cluster-advanced-actions">` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-off">关闭</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-rollback">回滚</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="artifact">导出映射</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-refit" ` +
    `title="跳到研究枢纽「跑分组」重估组 β（人审后对照/启用）。">建议重估</button>` +
    `</div></div>`;

  return (
    `<div class="quant-cluster-landing-card">` +
    `<div class="quant-cluster-landing-head">` +
    `<span class="quant-cluster-tables-label">落地</span>` +
    `</div>` +
    stats +
    preflightHtml +
    alertHtml +
    evidenceHtml +
    actions +
    more +
    `</div>`
  );
}
