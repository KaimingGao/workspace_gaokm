"""策略验证成熟闸门（V5 / §11）只读评估。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def evaluate_maturity_gate(
    *,
    sample_status: Optional[dict] = None,
    north_star: Optional[dict] = None,
    core_paths: Optional[dict] = None,
) -> Dict[str, Any]:
    """根据当前样本与 KPI 勾选闸门项（启发式，可人工豁免）。"""
    ss = sample_status or {}
    ns = north_star or {}
    fund = ss.get("fundamentals_history") or {}
    ttm = ss.get("ttm") or {}
    snaps = ss.get("paper_snapshots") or {}
    rb = ss.get("risk_blocks") or {}
    disc = ss.get("discipline") or {}
    items: List[Dict[str, Any]] = []

    def add(
        section: str,
        id_: str,
        ok: bool,
        detail: str,
        *,
        severity: str = "hard",
        action: str = "",
    ) -> None:
        items.append(
            {
                "section": section,
                "id": id_,
                "ok": bool(ok),
                "detail": detail,
                "severity": severity,  # hard | soft
                "action": action or "",
            }
        )

    real_cov = fund.get("real_multi_coverage")
    add(
        "data",
        "real_pit_coverage",
        real_cov is not None and float(real_cov) >= 0.5,
        f"真实多点覆盖 real_multi_coverage={real_cov}（目标≥0.5）",
        action="平台点「预热财务多期」或 CLI: sample_ops_run.py ingest-history",
    )
    add(
        "data",
        "empty_fundamentals",
        int(fund.get("empty") or 0) == 0
        or int(fund.get("empty") or 0) <= max(2, int(fund.get("total") or 0) * 0.2),
        f"空财务 {fund.get('empty')}/{fund.get('total')}",
        action="查看空财务列表并补拉或移出验证宇宙",
    )
    syn = int(fund.get("synthetic_multi_point") or 0)
    real_m = int(fund.get("real_multi_point") or 0)
    add(
        "data",
        "demo_discipline",
        syn == 0 or real_m >= syn,
        f"real_multi={real_m} synthetic_multi={syn}",
        action="勿用 seed-ladder 宣称已验证；改跑 ingest-history",
    )

    rz = (ns.get("realization") or {}) if isinstance(ns, dict) else {}
    rz_status = str(rz.get("status") or "")
    # unavailable 不得当作「拟合可读」通过（此前几乎恒绿）
    add(
        "fit",
        "realization_readable",
        rz_status in ("ok", "partial"),
        f"realization status={rz_status or '—'} reason={rz.get('reason') or '—'}"
        + (f" corr={rz.get('corr')}" if rz.get("corr") is not None else "")
        + (
            f" TE={rz.get('tracking_error_pct')}"
            if rz.get("tracking_error_pct") is not None
            else ""
        ),
        action="持续 paper_daily + 回溯页 Top-K 落盘曲线，对齐日期后再看 Corr/TE",
    )
    corr = rz.get("corr")
    te = rz.get("tracking_error_pct")
    corr_ok = True
    corr_detail = "拟合数值待样本（status≠ok 时本项随 realization 一并处理）"
    if rz_status in ("ok", "partial") and corr is not None:
        try:
            corr_f = float(corr)
            # soft：相关过低提示，不单独卡死 N6 评审（仍记 ok=False 供 UI 标黄）
            corr_ok = corr_f >= 0.3
            corr_detail = f"corr={corr_f:.3f}（软阈值≥0.3）"
            if te is not None:
                corr_detail += f" · TE={te}"
        except (TypeError, ValueError):
            corr_ok = False
            corr_detail = f"corr 无法解析: {corr}"
    add(
        "fit",
        "realization_strength",
        corr_ok if rz_status in ("ok", "partial") else False,
        corr_detail,
        severity="soft",
        action="历史回测页看落差归因卡；对齐成本模型/宇宙/调仓频率",
    )

    add(
        "ops",
        "paper_snapshots",
        bool(snaps.get("enough_for_sharpe"))
        and int(snaps.get("densified_count") or 0)
        <= int(snaps.get("count") or 0) * 0.5,
        f"snapshots={snaps.get('count')} densified={snaps.get('densified_count')}",
        action="运行纸面日更；必要时 prune-densified",
    )
    add(
        "ops",
        "ttm_not_seed_dominated",
        int(ttm.get("real_events") or 0) >= int(ttm.get("seeded_events") or 0),
        f"real_events={ttm.get('real_events')} seeded={ttm.get('seeded_events')}",
        action="走真实回测/promote 链路，少用 seed-ttm",
    )
    labeled = int(rb.get("labeled_count") or 0)
    blocks = int(rb.get("block_count") or 0)
    outcome_need = min(20, blocks) if blocks > 0 else 0
    add(
        "risk",
        "outcome_sample",
        blocks == 0 or labeled >= outcome_need,
        f"labeled={labeled} blocks={blocks}"
        + ("（无拦截可豁免）" if blocks == 0 else f"（需≥{outcome_need}，少样本时要求全部标注）"),
        action="策略中心「拦截流水」点真拦/误拦",
    )

    cp_ok = True if core_paths is None else bool(core_paths.get("ok"))
    add(
        "eng",
        "core_paths",
        cp_ok,
        "core golden paths ok" if cp_ok else str((core_paths or {}).get("failures")),
        action="python evals/run_core_paths.py",
    )
    add(
        "eng",
        "no_oms",
        True,
        "现行定位策略验证；未接 OMS（阶段约束）",
    )

    # S4.1 · S 轨能力可达性（软项：有代码即可，不依赖运营样本）
    add(
        "s_track",
        "factor_cs_ic_available",
        True,
        "因子截面 IC：core.backtest.factor_cs_ic + POST /api/quant/factor-cs-ic",
        severity="soft",
        action="研究枢纽点「截面 IC」；对照单票 IC",
    )
    add(
        "s_track",
        "validation_pack_shape",
        True,
        "验证包 v2 含 exposure / risk_blocks / ab_compare 槽位",
        severity="soft",
        action="POST /api/ops/validation-pack 导出后检查字段",
    )
    add(
        "s_track",
        "ann_date_pit",
        True,
        "财务可用日（ann_date 优先）已接入 select_point_as_of",
        severity="soft",
        action="ingest 时尽量带 ann_date；见 fundamentals_pit",
    )

    # E0 · 策略 scope 可读（软项）
    prs = (ns.get("paper_risk_strategy") or {}) if isinstance(ns, dict) else {}
    prs_status = str(prs.get("status") or "")
    prs_reason = str(prs.get("reason") or "")
    strategy_scope_ok = prs_status in ("ok", "partial") or prs_reason in (
        "no_strategy_equity",
        "snapshots_too_short",
    )
    add(
        "e_track",
        "strategy_scope_readable",
        strategy_scope_ok,
        f"paper_risk_strategy status={prs_status or '—'} reason={prs_reason or '—'}"
        + (
            f" sharpe={prs.get('rolling_sharpe')}"
            if prs.get("rolling_sharpe") is not None
            else ""
        ),
        severity="soft",
        action="策略调仓写入 origin=strategy；日更快照含 equity_strategy 后可读策略夏普",
    )

    hard_items = [i for i in items if i.get("severity") != "soft"]
    soft_items = [i for i in items if i.get("severity") == "soft"]
    hard_passed = sum(1 for i in hard_items if i["ok"])
    soft_passed = sum(1 for i in soft_items if i["ok"])
    passed = sum(1 for i in items if i["ok"])
    total = len(items)
    # N6 评审就绪：硬项全过；软项失败仍可评审但标注
    ready = hard_passed == len(hard_items)
    return {
        "ok": True,
        "ready_for_n6_review": ready,
        "passed": passed,
        "total": total,
        "hard_passed": hard_passed,
        "hard_total": len(hard_items),
        "soft_passed": soft_passed,
        "soft_total": len(soft_items),
        "items": items,
        "next_actions": [i["action"] for i in items if not i["ok"] and i.get("action")][:6],
        "note": "硬项全部通过仅表示「可评估是否立项 N6」，不自动开通实盘。"
        if ready
        else "硬项未过：继续样本/拟合加深或书面豁免单项；软项为质量提示。",
        "track": "S0-S4",
    }
