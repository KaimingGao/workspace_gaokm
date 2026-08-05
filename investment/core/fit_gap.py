"""回测–纸面拟合落差启发式归因（V1.3 · 运营加深）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def fit_gap_hints(
    *,
    realization: Optional[dict] = None,
    cost_compare: Optional[dict] = None,
    source_audit: Optional[dict] = None,
    paper_ops: Optional[dict] = None,
    backtest_params: Optional[dict] = None,
    universe: Optional[dict] = None,
) -> Dict[str, Any]:
    """规则启发式：解释 Corr/TE unavailable 或偏弱的可能原因。"""
    rz = realization or {}
    cc = cost_compare or {}
    sa = source_audit or {}
    ops = paper_ops or {}
    params = backtest_params or {}
    uni = universe or {}
    hints: List[Dict[str, str]] = []

    # 口径脚注：live 与回测边界（常驻 info，避免误以为已对齐舆情）
    hints.append(
        {
            "code": "live_vs_bt_scope",
            "level": "info",
            "message": (
                "口径：回测日线 PIT + 可选财务；不含舆情加减分；"
                "live 质量门禁/标题情绪不会进历史 TopK——拟合差时先查此项"
            ),
        }
    )

    # X4：特征同构 / 质量门政策
    try:
        from core.signal.live_features import build_quality_policy_snapshot

        qp = build_quality_policy_snapshot()
        hints.append(
            {
                "code": "quality_policy",
                "level": "info",
                "message": (
                    "X 政策：live 有质量门+财务PIT+index；"
                    f"回测 quality_gate={qp.get('backtest', {}).get('quality_gate')}；"
                    "研究面板默认绕开 regime（OOS/分组默认 respect_regime=True）"
                ),
            }
        )
    except Exception:
        pass
    try:
        from core.signal.factor_health import assess_factor_health

        fh = assess_factor_health()
        if fh.get("blockers"):
            hints.append(
                {
                    "code": "x_factor_proxy",
                    "level": "warn",
                    "message": "伪因子权重：" + "; ".join(fh.get("blockers") or [])[:200],
                }
            )
    except Exception:
        pass

    status = str(rz.get("status") or "")
    if status == "unavailable":
        reason = str(rz.get("reason") or "unavailable")
        note = str(rz.get("note") or "")
        hints.append(
            {
                "code": "realization_unavailable",
                "level": "warn",
                "message": f"拟合不可用：{reason}"
                + (f"。{note}" if note else "（需对齐日更多日净值与回测曲线）"),
            }
        )
    elif status in ("ok", "partial"):
        corr = rz.get("corr")
        try:
            corr_f = float(corr) if corr is not None else None
        except (TypeError, ValueError):
            corr_f = None
        if corr_f is not None and corr_f < 0.3:
            hints.append(
                {
                    "code": "low_corr",
                    "level": "warn",
                    "message": f"Corr≈{corr_f:.3f} 偏低；检查宇宙、调仓日与成本假设是否一致",
                }
            )
        te = rz.get("tracking_error_pct")
        try:
            te_f = float(te) if te is not None else None
        except (TypeError, ValueError):
            te_f = None
        if te_f is not None and te_f >= 3.0:
            hints.append(
                {
                    "code": "high_te",
                    "level": "info",
                    "message": f"跟踪误差 TE≈{te_f:.2f}%；路径分化大时优先对齐持有期与换手",
                }
            )

    gap = cc.get("return_gap_pp")
    try:
        gap_f = float(gap) if gap is not None else None
    except (TypeError, ValueError):
        gap_f = None
    if gap_f is not None and abs(gap_f) >= 1.0:
        hints.append(
            {
                "code": "cost_gap",
                "level": "info",
                "message": f"零成本 vs 含成本收益差约 {gap_f:.2f}pp；纸面若用 simple_cn 更接近含成本",
            }
        )

    impact = cc.get("avg_impact_bps")
    try:
        impact_f = float(impact) if impact is not None else None
    except (TypeError, ValueError):
        impact_f = None
    if impact_f is not None and impact_f >= 8:
        hints.append(
            {
                "code": "impact",
                "level": "info",
                "message": f"均冲击约 {impact_f:.1f} bps；小盘/高换手时纸面滑点可能更大",
            }
        )

    fb = int(sa.get("fallback_count") or 0)
    if fb > 0:
        hints.append(
            {
                "code": "source_fallback",
                "level": "warn",
                "message": f"源审计 fallback {fb} 只；live 与回测源分裂会拉低拟合",
            }
        )

    paper_cost = str(ops.get("cost_model") or "")
    bt_cost = str(params.get("cost_model") or "")
    if not bt_cost and cc.get("simple_cn"):
        bt_cost = "simple_cn"
    if not bt_cost and params.get("apply_costs"):
        bt_cost = "simple_cn"
    if paper_cost and bt_cost and paper_cost not in ("—", "") and paper_cost != bt_cost:
        hints.append(
            {
                "code": "cost_model_mismatch",
                "level": "warn",
                "message": f"纸面成本假设 {paper_cost} ≠ 回测 {bt_cost}",
            }
        )

    paper_wm = str(ops.get("weight_mode") or ops.get("last_weight_mode") or "")
    bt_wm = str(params.get("weight_mode") or "")
    if paper_wm and bt_wm and paper_wm != bt_wm:
        hints.append(
            {
                "code": "weight_mode_mismatch",
                "level": "info",
                "message": f"纸面权重模式 {paper_wm} ≠ 回测 {bt_wm}；对照时请选同一模式",
            }
        )

    # B2：horizon / y_spec 与配置对齐提示
    try:
        from core.signal.config import load_signal_config
        from core.research.beta_accuracy import build_y_spec

        cfg = load_signal_config() or {}
        scoring = cfg.get("scoring") or {}
        cfg_h = scoring.get("horizon_days")
        if cfg_h is None:
            cfg_h = 3
        bt_h = params.get("horizon_days")
        if bt_h is not None:
            try:
                if int(bt_h) != int(cfg_h):
                    hints.append(
                        {
                            "code": "horizon_mismatch",
                            "level": "warn",
                            "message": (
                                f"回测/对照 horizon={bt_h} ≠ scoring.horizon_days={cfg_h}；"
                                "y 契约不一致会扭曲拟合与 OOS"
                            ),
                        }
                    )
            except (TypeError, ValueError):
                pass
        y_spec = build_y_spec(horizon_days=int(cfg_h))
        hints.append(
            {
                "code": "y_spec",
                "level": "info",
                "message": (
                    f"y_spec：horizon={y_spec.get('horizon_days')} · "
                    f"成本={'含' if y_spec.get('include_cost') else '不含'} · "
                    f"{y_spec.get('formula')}"
                ),
            }
        )
    except Exception:
        pass

    if params.get("rebalance_days") or params.get("horizon_days"):
        hints.append(
            {
                "code": "rebalance_freq",
                "level": "info",
                "message": "调仓/持有周期不同会导致路径分化；对齐 horizon 与纸面日更频率再比 Corr",
            }
        )

    cand = uni.get("candidate_count") or params.get("stock_count")
    dropped = uni.get("dropped_thin_count") or params.get("dropped_thin_count")
    try:
        cand_n = int(cand) if cand is not None else None
    except (TypeError, ValueError):
        cand_n = None
    try:
        drop_n = int(dropped) if dropped is not None else 0
    except (TypeError, ValueError):
        drop_n = 0
    if cand_n is not None and cand_n < 8:
        hints.append(
            {
                "code": "small_universe",
                "level": "warn",
                "message": f"验证宇宙仅 {cand_n} 只候选；小样本外推与拟合都偏脆",
            }
        )
    if drop_n > 0:
        hints.append(
            {
                "code": "dropped_thin",
                "level": "info",
                "message": f"短序列剔除 {drop_n} 只；共同交易日被裁短会改变路径形状",
            }
        )

    # 去掉「仅有口径脚注」时的空洞 ok；若除 live_vs_bt_scope 外无其它，补一条汇总
    substantive = [h for h in hints if h.get("code") != "live_vs_bt_scope"]
    if not substantive:
        hints.append(
            {
                "code": "ok",
                "level": "info",
                "message": "未发现明显结构落差信号；若 Corr 仍低，检查宇宙与调仓日对齐",
            }
        )

    warn_n = sum(1 for h in hints if h.get("level") == "warn")
    quality_policy = None
    try:
        from core.signal.live_features import build_quality_policy_snapshot

        quality_policy = build_quality_policy_snapshot()
    except Exception:
        quality_policy = None
    return {
        "ok": True,
        "hints": hints,
        "count": len(hints),
        "warn_count": warn_n,
        "quality_policy": quality_policy,
        "note": "启发式归因，非因果证明。",
    }
