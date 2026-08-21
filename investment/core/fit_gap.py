"""回测–纸面拟合落差启发式归因（V1.3 · 运营加深）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence


def build_curve_day_diff(
    paper_snapshots: Optional[Sequence[dict]] = None,
    backtest_curve: Optional[Sequence[dict]] = None,
    *,
    window: int = 60,
    sample_limit: int = 16,
) -> Dict[str, Any]:
    """同窗日集合 Diff：交集 / 纸面独有 / 回测独有 + 对齐日收益差样本。"""
    from core.backtest_curve_store import _curve_points, _paper_daily_equities
    from core.risk_metrics import period_returns

    paper_pts = _paper_daily_equities(paper_snapshots or [])
    bt_pts = _curve_points(backtest_curve or [])
    w = max(5, int(window or 60))
    if len(paper_pts) > w:
        paper_pts = paper_pts[-w:]
    if len(bt_pts) > w * 2:
        bt_pts = bt_pts[-(w * 2) :]

    paper_map = {d: e for d, e in paper_pts}
    bt_map = {d: e for d, e in bt_pts}
    paper_dates = set(paper_map)
    bt_dates = set(bt_map)
    common = sorted(paper_dates & bt_dates)
    paper_only = sorted(paper_dates - bt_dates)
    bt_only = sorted(bt_dates - paper_dates)

    day_gaps: List[Dict[str, Any]] = []
    day_series: List[Dict[str, Any]] = []
    if len(common) >= 2:
        p_eq = [paper_map[d] for d in common]
        b_eq = [bt_map[d] for d in common]
        p0, b0 = p_eq[0], b_eq[0]
        if p0 and b0 and p0 > 0 and b0 > 0:
            p_n = [e / p0 for e in p_eq]
            b_n = [e / b0 for e in b_eq]
            p_rets = period_returns(p_n)
            b_rets = period_returns(b_n)
            for i, d in enumerate(common[1:]):
                if i >= len(p_rets) or i >= len(b_rets):
                    break
                pr = float(p_rets[i])
                br = float(b_rets[i])
                row = {
                    "date": d,
                    "paper_ret_pct": round(pr * 100.0, 4),
                    "bt_ret_pct": round(br * 100.0, 4),
                    "gap_pp": round((pr - br) * 100.0, 4),
                }
                day_series.append(row)
            # 表：|Δ| 最大的若干日；图：用完整时序 day_series
            day_gaps = sorted(
                day_series, key=lambda x: -abs(float(x.get("gap_pp") or 0))
            )

    lim = max(3, min(int(sample_limit or 16), 40))
    return {
        "ok": True,
        "window": w,
        "paper_days": len(paper_pts),
        "backtest_days": len(bt_pts),
        "aligned_days": len(common),
        "paper_only_days": len(paper_only),
        "bt_only_days": len(bt_only),
        "paper_only_sample": paper_only[-lim:],
        "bt_only_sample": bt_only[-lim:],
        "common_first": common[0] if common else None,
        "common_last": common[-1] if common else None,
        "day_series": day_series,
        "day_gaps": day_gaps[:lim],
        "note": "同窗对齐后的日收益差；正=纸面当日相对回测更高。非因果证明。",
    }


def _safe_float(value: Any) -> Optional[float]:
    """容错数值转换：None→None；非法→None。"""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    """容错整数转换：失败回退 default。"""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _append_scope_hint(hints: List[Dict[str, str]]) -> None:
    """基线口径提示：回测 vs live 信号域差异。"""
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


def _append_quality_policy_hint(hints: List[Dict[str, str]]) -> "Optional[Dict[str, Any]]":
    """附加 X 政策快照提示；返回 quality_policy 供最终汇总。"""
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
        return qp
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in fit_gap.py", exc_info=True)
        return None


def _append_factor_health_hint(hints: List[Dict[str, str]]) -> None:
    """伪因子 blocker 提示（best-effort）。"""
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in fit_gap.py", exc_info=True)


def _append_realization_hints(hints: List[Dict[str, str]], rz: Dict[str, Any]) -> None:
    """realization 状态提示：不可用 / 低 Corr / 高 TE。"""
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
        corr_f = _safe_float(rz.get("corr"))
        if corr_f is not None and corr_f < 0.3:
            hints.append(
                {
                    "code": "low_corr",
                    "level": "warn",
                    "message": f"Corr≈{corr_f:.3f} 偏低；检查宇宙、调仓日与成本假设是否一致",
                }
            )
        te_f = _safe_float(rz.get("tracking_error_pct"))
        if te_f is not None and te_f >= 3.0:
            hints.append(
                {
                    "code": "high_te",
                    "level": "info",
                    "message": f"跟踪误差 TE≈{te_f:.2f}%；路径分化大时优先对齐持有期与换手",
                }
            )


def _append_day_diff_hint(hints: List[Dict[str, str]], dd: Dict[str, Any]) -> None:
    """同窗日集合 Diff 提示。"""
    po = _safe_int(dd.get("paper_only_days"))
    bo = _safe_int(dd.get("bt_only_days"))
    al = _safe_int(dd.get("aligned_days"))
    if po or bo:
        level = "warn" if (po >= 5 or bo >= 5 or al < 10) else "info"
        hints.append(
            {
                "code": "date_set_diff",
                "level": level,
                "message": (
                    f"同窗日集合：对齐 {al} · 纸面独有 {po} · 回测独有 {bo}；"
                    "缺交集时 Corr 不可用或偏脆"
                ),
            }
        )


def _append_cost_compare_hints(hints: List[Dict[str, str]], cc: Dict[str, Any]) -> None:
    """成本比较：收益差与冲击成本提示。"""
    gap_f = _safe_float(cc.get("return_gap_pp"))
    if gap_f is not None and abs(gap_f) >= 1.0:
        hints.append(
            {
                "code": "cost_gap",
                "level": "info",
                "message": f"零成本 vs 含成本收益差约 {gap_f:.2f}pp；纸面若用 simple_cn 更接近含成本",
            }
        )
    impact_f = _safe_float(cc.get("avg_impact_bps"))
    if impact_f is not None and impact_f >= 8:
        hints.append(
            {
                "code": "impact",
                "level": "info",
                "message": f"均冲击约 {impact_f:.1f} bps；小盘/高换手时纸面滑点可能更大",
            }
        )


def _append_source_audit_hint(hints: List[Dict[str, str]], sa: Dict[str, Any]) -> None:
    """源审计 fallback 提示。"""
    fb = _safe_int(sa.get("fallback_count"))
    if fb > 0:
        hints.append(
            {
                "code": "source_fallback",
                "level": "warn",
                "message": f"源审计 fallback {fb} 只；live 与回测源分裂会拉低拟合",
            }
        )


def _append_cost_model_mismatch_hint(
    hints: List[Dict[str, str]],
    ops: Dict[str, Any],
    params: Dict[str, Any],
    cc: Dict[str, Any],
) -> None:
    """成本假设不一致提示。"""
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


def _append_weight_mode_mismatch_hint(
    hints: List[Dict[str, str]],
    ops: Dict[str, Any],
    params: Dict[str, Any],
) -> None:
    """权重模式不一致提示。"""
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


def _append_horizon_and_y_spec_hints(
    hints: List[Dict[str, str]], params: Dict[str, Any]
) -> None:
    """horizon 一致性 + y_spec 说明（best-effort）。"""
    try:
        from core.research.beta_accuracy import build_y_spec
        from core.signal.config import load_signal_config

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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in fit_gap.py", exc_info=True)


def _append_rebalance_freq_hint(
    hints: List[Dict[str, str]], params: Dict[str, Any]
) -> None:
    """调仓/持有周期分化提示。"""
    if params.get("rebalance_days") or params.get("horizon_days"):
        hints.append(
            {
                "code": "rebalance_freq",
                "level": "info",
                "message": "调仓/持有周期不同会导致路径分化；对齐 horizon 与纸面日更频率再比 Corr",
            }
        )


def _append_universe_hints(
    hints: List[Dict[str, str]],
    uni: Dict[str, Any],
    params: Dict[str, Any],
) -> None:
    """宇宙规模与短序列剔除提示。"""
    cand = uni.get("candidate_count") or params.get("stock_count")
    dropped = uni.get("dropped_thin_count") or params.get("dropped_thin_count")
    cand_n: Optional[int]
    try:
        cand_n = int(cand) if cand is not None else None
    except (TypeError, ValueError):
        cand_n = None
    drop_n = _safe_int(dropped) if dropped is not None else 0
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


def _finalize_hints_output(
    hints: List[Dict[str, str]],
    quality_policy: Optional[Dict[str, Any]],
    dd: Dict[str, Any],
    rz: Dict[str, Any],
) -> Dict[str, Any]:
    """汇总最终输出：无实质提示时补 ok 提示，附 quality_policy/realization 摘要。"""
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
    out: Dict[str, Any] = {
        "ok": True,
        "hints": hints,
        "count": len(hints),
        "warn_count": warn_n,
        "quality_policy": quality_policy,
        "note": "启发式归因，非因果证明。",
    }
    if dd:
        out["day_diff"] = dd
    if rz:
        out["realization"] = {
            "status": rz.get("status"),
            "corr": rz.get("corr"),
            "tracking_error_pct": rz.get("tracking_error_pct"),
            "aligned_days": rz.get("aligned_days"),
            "first_date": rz.get("first_date"),
            "last_date": rz.get("last_date"),
            "note": rz.get("note"),
        }
    return out


def fit_gap_hints(
    *,
    realization: Optional[dict] = None,
    cost_compare: Optional[dict] = None,
    source_audit: Optional[dict] = None,
    paper_ops: Optional[dict] = None,
    backtest_params: Optional[dict] = None,
    universe: Optional[dict] = None,
    day_diff: Optional[dict] = None,
) -> Dict[str, Any]:
    """规则启发式：解释 Corr/TE unavailable 或偏弱的可能原因。"""
    rz = realization or {}
    cc = cost_compare or {}
    sa = source_audit or {}
    ops = paper_ops or {}
    params = backtest_params or {}
    uni = universe or {}
    dd = day_diff or {}

    hints: List[Dict[str, str]] = []
    _append_scope_hint(hints)
    # quality_policy 快照最终汇总用，这里同步收集提示
    quality_policy: Optional[Dict[str, Any]] = _append_quality_policy_hint(hints)
    _append_factor_health_hint(hints)
    _append_realization_hints(hints, rz)
    _append_day_diff_hint(hints, dd)
    _append_cost_compare_hints(hints, cc)
    _append_source_audit_hint(hints, sa)
    _append_cost_model_mismatch_hint(hints, ops, params, cc)
    _append_weight_mode_mismatch_hint(hints, ops, params)
    _append_horizon_and_y_spec_hints(hints, params)
    _append_rebalance_freq_hint(hints, params)
    _append_universe_hints(hints, uni, params)

    return _finalize_hints_output(hints, quality_policy, dd, rz)
