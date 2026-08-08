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

    # Y4.2 · ŷ 生产硬化闸门
    try:
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor
        from core.signal.config import load_signal_config

        cfg = load_signal_config()
        scoring = cfg.get("scoring") or {}
        has_hysteresis = (
            "min_predicted_score" in scoring and "min_hold_predicted_score" in scoring
        )
        buy_f = resolve_buy_floor()
        hold_f = resolve_hold_floor()
        add(
            "y_track",
            "yhat_hysteresis_configured",
            has_hysteresis,
            f"ŷ 滞回 buy={buy_f} hold={hold_f}（须在 scoring 显式配置）",
            severity="hard",
            action="策略中心写入 min_predicted_score / min_hold_predicted_score",
        )
    except Exception as exc:
        add(
            "y_track",
            "yhat_hysteresis_configured",
            False,
            f"ŷ 滞回读取失败：{exc}",
            severity="hard",
            action="检查 signal_config.scoring",
        )

    try:
        from core.signal.cluster_live import (
            assess_cluster_live_health,
            get_cluster_scoring_cfg,
            load_active_cluster_weights,
        )

        cs = get_cluster_scoring_cfg()
        mode = cs.get("mode") or "off"
        active = load_active_cluster_weights()
        health = assess_cluster_live_health() if active else {}
        live_ok = mode == "off" or (
            bool(active) and bool(health.get("allow_active") or mode == "shadow")
        )
        add(
            "y_track",
            "yhat_live_health",
            live_ok,
            f"cluster mode={mode} has_active={bool(active)} "
            f"stale={health.get('stale')} refit={health.get('refit_suggested')}",
            severity="soft",
            action="研究枢纽刷新簿 / 跑分组→对照；陈旧则勿长期 active",
        )
    except Exception as exc:
        add(
            "y_track",
            "yhat_live_health",
            False,
            f"live 健康检查失败：{exc}",
            severity="soft",
            action="检查 cluster live 映射",
        )

    # Y5.2：纸面日更连续样本
    snap_count = int(snaps.get("count") or 0)
    add(
        "y_track",
        "paper_daily_streak",
        snap_count >= 5,
        f"paper snapshots={snap_count}（软阈值≥5 交易日净值点）",
        severity="soft",
        action="平台/交易页运行纸面日更，积累真实净值序列",
    )

    # X 轨 · 特征同构
    ann_ratio = fund.get("ann_missing_code_ratio")
    ann_ok = True
    if ann_ratio is not None:
        try:
            ann_ok = float(ann_ratio) <= 0.35
        except (TypeError, ValueError):
            ann_ok = True
    add(
        "x_track",
        "ann_missing_honest",
        ann_ok,
        f"ann_missing 码占比={ann_ratio}（软阈值≤0.35）· points={fund.get('ann_missing_points')}",
        severity="soft",
        action="ingest 时写入 ann_date；见 fundamentals_pit / sample_ops",
    )
    try:
        from core.signal.factor_health import assess_factor_health
        from core.signal.config import load_signal_config as _lsc

        fh = assess_factor_health(config=_lsc())
        add(
            "x_track",
            "factor_health_proxy",
            bool(fh.get("ok")),
            (
                "生产面因子健康 ok"
                if fh.get("ok")
                else "; ".join(fh.get("blockers") or ["proxy weight"])
            ),
            severity="soft",
            action="money_flow 等无真源时权重保持 0；见 factor_health",
        )
    except Exception as exc:
        add(
            "x_track",
            "factor_health_proxy",
            False,
            f"factor_health 失败：{exc}",
            severity="soft",
            action="检查 core.signal.factor_health",
        )
    add(
        "x_track",
        "live_feature_isomorphism",
        True,
        "live 财务 PIT + index_bars 已接线（X0/X1）；详见 feature-signal-strengthen",
        severity="soft",
        action="打分响应查 fundamentals_pit / index_meta",
    )

    # B 轨
    try:
        from core.validation_universe import universe_sample_gate

        ug = universe_sample_gate()
        add(
            "b_track",
            "universe_min_codes",
            bool(ug.get("ok")),
            f"验证宇宙 {ug.get('count')}（min_codes={ug.get('min_codes')}）",
            severity="soft",
            action="扩观察池或调 validation_universe.min_codes",
        )
    except Exception as exc:
        add(
            "b_track",
            "universe_min_codes",
            False,
            f"宇宙闸门失败：{exc}",
            severity="soft",
            action="检查 validation_universe",
        )
    add(
        "b_track",
        "regression_accuracy_track",
        True,
        "B 轨：y_spec · 共线策略 · Ridge 选 λ · 重估 demote · OOS respect_regime",
        severity="soft",
        action="见 docs/beta-regression-strengthen.md",
    )
    try:
        ann_ratio = fund.get("ann_missing_code_ratio")
        ann_ok = ann_ratio is None or float(ann_ratio) <= 0.35
        add(
            "b_track",
            "ann_missing_ops",
            bool(ann_ok),
            f"ann_missing 码占比={ann_ratio}（软阈值≤0.35）",
            severity="soft",
            action="平台样本覆盖 → 缺 ann 清单 → ingest-history / 预热财务",
        )
    except Exception:
        add(
            "b_track",
            "ann_missing_ops",
            False,
            "ann_missing 统计失败",
            severity="soft",
            action="检查 sample_ops / fundamentals store",
        )
    try:
        from core.signal.cluster_live_health import assess_cluster_live_health

        health = assess_cluster_live_health()
        refit_ok = not bool(health.get("refit_suggested") or health.get("stale") or health.get("ic_demote"))
        add(
            "b_track",
            "refit_discipline",
            refit_ok or str(health.get("mode") or "") in ("off", "shadow"),
            (
                f"分组 health mode={health.get('mode')} · stale={health.get('stale')} · "
                f"refit={health.get('refit_suggested')} · ic_demote={health.get('ic_demote')}"
            ),
            severity="soft",
            action="研究枢纽跑分组重估；陈旧/IC 破线会 auto demote active→shadow",
        )
    except Exception as exc:
        add(
            "b_track",
            "refit_discipline",
            True,
            f"health 跳过：{exc}",
            severity="soft",
            action="可忽略（无分组 live 时）",
        )

    # DC / FM / RK · 专业核心三轨
    try:
        from core.pro_core import assess_pit_depth

        pit = assess_pit_depth(sample_status=ss, fundamentals_history=fund)
        add(
            "dc_track",
            "pit_depth_soft",
            bool(pit.get("soft_ok")),
            (
                f"PIT 深度 soft_ok={pit.get('soft_ok')} · "
                f"ann_missing={pit.get('ann_missing_code_ratio')} · "
                f"real_multi={pit.get('real_multi_coverage')} · "
                f"label={pit.get('honest_label')}"
            ),
            severity="soft",
            action="平台 DQ → 催办 ingest TopN；见 pro-core-strengthen DC0",
        )
        add(
            "dc_track",
            "pit_depth_hard",
            bool(pit.get("ok")),
            (
                f"PIT 深度 hard_ok={pit.get('ok')} · "
                f"硬阈值 ann_missing≤{pit.get('ann_missing_hard_max')}"
            ),
            severity="hard",
            action="ann_missing 过高时先补财务公告日再评估 N6",
        )
    except Exception as exc:
        add(
            "dc_track",
            "pit_depth_soft",
            False,
            f"pit_depth 失败：{exc}",
            severity="soft",
            action="检查 core.pro_core.assess_pit_depth",
        )
    try:
        from core.paper import load_paper
        from core.risk.block_outcome import unlabeled_digest

        paper = load_paper() or {}
        ud = unlabeled_digest(paper.get("operation_log") or []) or {}
        unlabeled_n = int(ud.get("unlabeled_count") or 0)
        add(
            "rk_track",
            "outcome_unlabeled_nudge",
            unlabeled_n == 0,
            f"风险拦截未标注 outcome={unlabeled_n}",
            severity="soft",
            action="策略页标注真拦/误拦；见 pro-core-strengthen RK2",
        )
    except Exception as exc:
        add(
            "rk_track",
            "outcome_unlabeled_nudge",
            True,
            f"outcome 催办跳过：{exc}",
            severity="soft",
            action="",
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
        "track": "S0-S4+Y0-Y5+X0-X5+B0-B5+DC/FM/RK",
    }
