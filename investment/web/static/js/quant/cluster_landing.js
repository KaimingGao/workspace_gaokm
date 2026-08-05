/**
 * 分组权落地卡片 HTML（纯字符串）。
 */
import { escapeHtml } from "../shared.js";

export function clusterLandingHtml(data) {
  const cs = (data && data.cluster_scoring) || {};
  const act = (data && data.active) || {};
  const draft = (data && data.draft) || {};
  const book = (data && data.book) || {};
  const h = (data && data.health) || {};
  const land = (data && data.landing) || {};
  const mode = cs.mode || "off";
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
    `<span class="quant-cluster-stat" title="组ŷ 打分后全局按 score 排序，再按 min_score / max 截断"><b>全局</b> 排序</span>` +
    `<span class="quant-cluster-stat"><b>${escapeHtml(cov)}</b> 覆盖</span>` +
    `<span class="quant-cluster-stat${h.stale ? " is-warn" : ""}"><b>${escapeHtml(
      age
    )}</b> 龄</span>` +
    `<span class="quant-cluster-stat" title="合并簿只数=账户调仓目标"><b>${escapeHtml(
      String(book.name_count != null ? book.name_count : "—")
    )}</b> 簿</span>` +
    `</div>`;

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
        `<li title="组ŷ→全局排序→min_score 过滤→max 截断">簿长 ${escapeHtml(
          String(ev.name_count != null ? ev.name_count : book.name_count ?? "—")
        )} · 全局排序 · min_score=${escapeHtml(
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
          "基线=heuristic（人工加权）；研究臂=predicted_score（ŷ）。过门≠自动 promote。"
        )}">OOS：heuristic 基线 vs ŷ 研究臂 · 过门≠自动 promote</li>` +
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

  const actions =
    `<div class="quant-cluster-landing-actions">` +
    `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
    `data-cluster-export="live-apply" ${canApply ? "" : "disabled"} ` +
    `title="晋升分组映射 → 进入对照（shadow）→ 刷新目标簿。此步不切换交易执行选股真源。">① 对照</button>` +
    `<button type="button" class="dialog-btn dialog-btn-keep-case" ` +
    `data-cluster-export="live-active" ${canActivate ? "" : "disabled"} ` +
    `title="证据包与健康门禁通过后，将交易执行选股切换为组ŷ。不改写 signal_config.weights。">② 启用</button>` +
    `<span class="sub quant-cluster-landing-next${
      doneResearch ? " is-done" : ""
    }">${nextPrefix}${escapeHtml(next)}</span>` +
    `</div>`;

  const more =
    `<details class="quant-cluster-more quant-cluster-advanced" id="quant-cluster-live-bar">` +
    `<summary>更多 ` +
    `<span class="sub" id="quant-cluster-live-status">回滚 / 关闭 / 导出</span></summary>` +
    `<div class="quant-cluster-advanced-actions">` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-off">关闭</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-rollback">回滚</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="artifact">导出映射</button>` +
    `<button type="button" class="dialog-btn secondary dialog-btn-keep-case" data-cluster-export="live-refresh">刷新簿</button>` +
    `</div></details>`;

  return (
    `<div class="quant-cluster-landing-card">` +
    `<div class="quant-cluster-landing-head">` +
    `<span class="quant-cluster-tables-label">落地</span>` +
    `</div>` +
    stats +
    alertHtml +
    evidenceHtml +
    actions +
    more +
    `</div>`
  );
}
