"""分池合成：各组组内 Top-N → 合并候选簿；与全局 Top-K 对照回测（研究探针）。

不写 signal_config，不进纸面/live；供人审分池调仓前预览。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _suggested_weights(cluster: Dict[str, Any]) -> Optional[Dict[str, float]]:
    sug = cluster.get("weight_suggest") or {}
    if not sug.get("success"):
        return None
    w = sug.get("suggested_weights") or {}
    if not isinstance(w, dict) or not w:
        return None
    out: Dict[str, float] = {}
    for k, v in w.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


def _cluster_specs(
    clusters: Sequence[Dict[str, Any]],
    *,
    only_oos_passed: bool = False,
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for cl in clusters or []:
        weights = _suggested_weights(cl)
        if not weights:
            continue
        gate = cl.get("oos_gate") or {}
        oos_passed = bool(gate.get("ok") and gate.get("passed"))
        if only_oos_passed and not oos_passed:
            continue
        members = [str(m).strip() for m in (cl.get("members") or []) if str(m).strip()]
        if not members:
            continue
        specs.append(
            {
                "cluster_id": cl.get("cluster_id"),
                "label": cl.get("label") or f"G{(cl.get('cluster_id') or 0) + 1}",
                "members": members,
                "weights": weights,
                "oos_passed": oos_passed,
            }
        )
    return specs


def build_merged_book(
    group_scores: Dict[str, Any],
    *,
    top_n_per_group: int = 10,
    max_names: int = 40,
    clusters: Optional[Sequence[Dict[str, Any]]] = None,
    only_oos_passed: bool = False,
) -> Dict[str, Any]:
    """从最新 ``group_scores`` 各组榜取 Top-N，合成候选簿（等权提示）。"""
    top_n = max(1, min(int(top_n_per_group or 10), 10))
    max_names = max(1, min(int(max_names or 40), 80))
    groups = list((group_scores or {}).get("groups") or [])
    oos_ok: Optional[set] = None
    if only_oos_passed and clusters is not None:
        oos_ok = {
            str(s["label"])
            for s in _cluster_specs(clusters, only_oos_passed=True)
        }

    picks: List[Dict[str, Any]] = []
    seen: set = set()
    for g in groups:
        if g.get("skipped"):
            continue
        label = str(g.get("label") or "")
        if oos_ok is not None and label not in oos_ok:
            continue
        ranking = list(g.get("ranking") or [])
        taken = 0
        for r in ranking:
            if taken >= top_n:
                break
            code = str(r.get("stock_code") or "").strip()
            if not code or code in seen:
                continue
            if r.get("hard_reject"):
                continue
            seen.add(code)
            taken += 1
            picks.append(
                {
                    "stock_code": code,
                    "stock_name": r.get("stock_name") or code,
                    "cluster_label": label,
                    "cluster_id": g.get("cluster_id"),
                    "rank_in_group": r.get("rank_in_group"),
                    "score": r.get("score"),
                    "global_rank": r.get("global_rank"),
                    "score_mode": "group_weights",
                }
            )
            if len(picks) >= max_names:
                break
        if len(picks) >= max_names:
            break

    n = len(picks)
    weight_pct = round(100.0 / n, 4) if n else 0.0
    for p in picks:
        p["weight_pct"] = weight_pct

    global_top = list((group_scores or {}).get("global_ranking") or [])[: max(n, 1)]
    global_codes = {str(r.get("stock_code")) for r in global_top if r.get("stock_code")}
    pool_codes = {p["stock_code"] for p in picks}
    overlap = sorted(pool_codes & global_codes)
    jaccard = (
        round(len(overlap) / len(pool_codes | global_codes), 3)
        if (pool_codes or global_codes)
        else None
    )

    return {
        "success": True,
        "task": "cluster_pool_merge_book",
        "top_n_per_group": top_n,
        "max_names": max_names,
        "only_oos_passed": bool(only_oos_passed),
        "name_count": n,
        "book": picks,
        "vs_global_top": {
            "global_top_k": len(global_top),
            "overlap_codes": overlap,
            "overlap_count": len(overlap),
            "jaccard": jaccard,
        },
        "note": (
            "各组组内 Top-N 合并为候选簿（等权提示）；"
            "对照为全局权统一排名前 K。研究预览，不写纸面。"
        ),
    }


def _hold_return_close(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    entry_date: str,
    exit_date: str,
) -> Optional[float]:
    dm = date_maps.get(code) or {}
    a = dm.get(entry_date)
    b = dm.get(exit_date)
    if not a or not b:
        return None
    try:
        pa = float(a.get("close"))
        pb = float(b.get("close"))
    except (TypeError, ValueError):
        return None
    if pa <= 0:
        return None
    return (pb / pa - 1.0) * 100.0


def backtest_cluster_pools(
    clusters: Sequence[Dict[str, Any]],
    bars_by_code: Dict[str, List[dict]],
    *,
    top_n_per_group: int = 10,
    max_names: int = 40,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_history: int = 12,
    max_window: int = 30,
    only_oos_passed: bool = False,
) -> Dict[str, Any]:
    """分池调仓回测：每组用组权打分取 Top-N，合并等权持有；对照全局权 Top-K。

    执行简化为信号日收盘进、持有 horizon 后收盘出（研究对照，非纸面撮合）。
    """
    from core.backtest.engine import _mock_quote_from_bars, _trade_metrics
    from core.backtest.oos_report import split_oos_summary
    from core.backtest.topk_backtest import (
        _bars_by_date,
        _common_dates,
        _filter_stock_bars_for_calendar,
        _window_for_code,
        backtest_topk_equal_weight,
    )
    from core.signal.config import load_signal_config, signal_config_overlay
    from core.signal.cross_section_batch import score_window_as_item

    top_n = max(1, min(int(top_n_per_group or 10), 10))
    max_names = max(1, min(int(max_names or 40), 80))
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    from core.signal.score_display import resolve_optimize_score_floor

    min_score = resolve_optimize_score_floor(min_score)
    min_history = max(5, int(min_history or 12))

    specs = _cluster_specs(clusters, only_oos_passed=only_oos_passed)
    if len(specs) < 1:
        return {
            "success": False,
            "error": "无可用分组权（或 OOS 过滤后为空）",
            "task": "cluster_pool_backtest",
        }

    member_codes = sorted({c for s in specs for c in s["members"]})
    stock_bars = {
        c: bars_by_code[c] for c in member_codes if c in (bars_by_code or {})
    }
    need = min_history + horizon_days
    stock_bars, dropped = _filter_stock_bars_for_calendar(stock_bars, min_bars=need)
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（{len(stock_bars)}）",
            "dropped_stocks": dropped,
            "task": "cluster_pool_backtest",
        }

    # 对齐成员到过滤后宇宙
    for s in specs:
        s["members"] = [c for c in s["members"] if c in stock_bars]
    specs = [s for s in specs if s["members"]]
    if not specs:
        return {
            "success": False,
            "error": "过滤后无组员",
            "task": "cluster_pool_backtest",
        }

    date_maps = {code: _bars_by_date(bars) for code, bars in stock_bars.items()}
    dates = _common_dates(stock_bars)
    n_dates = len(dates)
    if n_dates < need:
        return {
            "success": False,
            "error": f"共同交易日不足（{n_dates}）",
            "common_dates": n_dates,
            "task": "cluster_pool_backtest",
        }

    cfg_base = load_signal_config()
    returns: List[float] = []
    equity_curve: List[dict] = []
    equity = 100.0
    rebalance_count = 0
    last_i = n_dates - horizon_days - 1
    i = min_history - 1
    if dates:
        equity_curve.append({"date": dates[i], "equity": equity, "return_pct": 0.0})

    while i <= last_i:
        selected: List[Tuple[str, float, str]] = []  # code, score, label
        seen: set = set()
        for spec in specs:
            overlay = dict(cfg_base)
            overlay["weights"] = dict(spec["weights"])
            scored: List[Tuple[str, float]] = []
            with signal_config_overlay({"weights": spec["weights"]}):
                for code in spec["members"]:
                    window = _window_for_code(
                        code, dates, date_maps, i, max_window
                    )
                    if len(window) < 2:
                        continue
                    quote = _mock_quote_from_bars(window, len(window) - 1)
                    item = score_window_as_item(
                        code,
                        window,
                        horizon_days=horizon_days,
                        quote=quote,
                        config=overlay,
                    )
                    if not item:
                        continue
                    sc = float(item.get("score") or 0.0)
                    if sc < min_score:
                        continue
                    scored.append((code, sc))
            scored.sort(key=lambda t: t[1], reverse=True)
            for code, sc in scored[:top_n]:
                if code in seen:
                    continue
                seen.add(code)
                selected.append((code, sc, str(spec["label"])))
                if len(selected) >= max_names:
                    break
            if len(selected) >= max_names:
                break

        if not selected:
            i += 1
            continue

        entry_date = dates[i]
        exit_date = dates[i + horizon_days]
        leg_rets: List[float] = []
        for code, _sc, _lab in selected:
            r = _hold_return_close(date_maps, code, entry_date, exit_date)
            if r is not None:
                leg_rets.append(r)
        if not leg_rets:
            i += 1
            continue
        period = sum(leg_rets) / len(leg_rets)
        returns.append(period)
        equity *= 1.0 + period / 100.0
        rebalance_count += 1
        equity_curve.append(
            {
                "date": exit_date,
                "equity": round(equity, 4),
                "return_pct": round(period, 4),
                "n_names": len(selected),
            }
        )
        i += horizon_days

    metrics = _trade_metrics(returns)
    oos = split_oos_summary(equity_curve)

    # 全局权对照：同一宇宙、Top-K ≈ 合成名数
    # 必须用 heuristic + 关闭 live 组模型；默认 predicted 会拖成数十分钟并像「卡死」
    top_k_global = max(1, min(max_names, max(1, len(specs) * top_n)))
    global_bt: Dict[str, Any]
    try:
        with signal_config_overlay({"weights": dict(cfg_base.get("weights") or {})}):
            global_bt = backtest_topk_equal_weight(
                stock_bars,
                top_k=top_k_global,
                horizon_days=horizon_days,
                min_score=float(min_score),
                apply_costs=False,
                execution_mode="close",
                respect_limit=False,
                weight_mode="equal",
                rank_mode="heuristic_score",
                allow_heuristic_baseline=True,
                use_live_cluster_models=False,
                apply_tau_buy_gate=False,
            )
    except Exception as exc:
        global_bt = {"success": False, "error": str(exc)}

    global_oos = None
    g_metrics = global_bt.get("metrics") or {} if global_bt.get("success") else {}
    if global_bt.get("success"):
        global_oos = split_oos_summary(global_bt.get("equity_curve") or [])

    def _oos_ret(summary: Optional[Dict[str, Any]]) -> Optional[float]:
        if not summary or not summary.get("ok"):
            return None
        v = summary.get("oos_return_pct")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    pool_oos = _oos_ret(oos)
    g_oos = _oos_ret(global_oos)
    delta = None
    if pool_oos is not None and g_oos is not None:
        delta = round(pool_oos - g_oos, 3)

    return {
        "success": True,
        "task": "cluster_pool_backtest",
        "mode": "per_group_topn_merge_equal",
        "execution": "close_hold_horizon",
        "top_n_per_group": top_n,
        "max_names": max_names,
        "top_k_global": top_k_global,
        "horizon_days": horizon_days,
        "only_oos_passed": bool(only_oos_passed),
        "groups_used": len(specs),
        "stock_count": len(stock_bars),
        "rebalance_count": rebalance_count,
        "metrics": {
            "total_return_pct": metrics.get("total_return_pct"),
            "win_rate_pct": metrics.get("win_rate_pct"),
            "avg_return_pct": metrics.get("avg_return_pct"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "trade_count": metrics.get("trade_count"),
        },
        "oos": oos,
        "global_baseline": {
            "success": bool(global_bt.get("success")),
            "top_k": top_k_global,
            "total_return_pct": g_metrics.get("total_return_pct"),
            "max_drawdown_pct": g_metrics.get("max_drawdown_pct"),
            "oos": global_oos,
            "error": global_bt.get("error") if not global_bt.get("success") else None,
        },
        "compare": {
            "delta_oos_pp": delta,
            "pool_better_oos": (
                bool(delta is not None and delta >= 0) if delta is not None else None
            ),
        },
        "equity_curve_tail": equity_curve[-5:],
        "note": (
            "分池：各组组权打分取 Top-N 后合并等权；"
            "对照为同宇宙全局权 Top-K（close 执行）。不写盘。"
        ),
    }


def attach_cluster_pool_merge(
    report: Dict[str, Any],
    bars_by_code: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    top_n_per_group: int = 10,
    max_names: int = 40,
    run_pool_merge: bool = True,
    run_backtest: bool = True,
) -> Dict[str, Any]:
    """挂最新合成簿 + 可选分池对照回测。"""
    if not run_pool_merge or not report.get("success"):
        report["pool_merge"] = {
            "success": False,
            "skipped": True,
            "reason": "gate_disabled" if not run_pool_merge else "report_failed",
        }
        return report

    gs = report.get("group_scores") or {}
    if not gs.get("success"):
        report["pool_merge"] = {
            "success": False,
            "skipped": True,
            "reason": "group_scores_missing",
        }
        return report

    book = build_merged_book(
        gs,
        top_n_per_group=top_n_per_group,
        max_names=max_names,
        clusters=report.get("clusters") or [],
        only_oos_passed=False,
    )
    out: Dict[str, Any] = {
        "success": True,
        "task": "cluster_pool_merge",
        "book": book,
    }
    if run_backtest:
        bt = backtest_cluster_pools(
            report.get("clusters") or [],
            bars_by_code,
            top_n_per_group=top_n_per_group,
            max_names=max_names,
            horizon_days=horizon_days,
        )
        out["backtest"] = bt
    else:
        out["backtest"] = {"success": False, "skipped": True, "reason": "disabled"}

    report["pool_merge"] = out
    note = str(report.get("note") or "")
    if "分池合成" not in note:
        report["note"] = note + " 已附分池合成候选簿 / 对照回测。"
    return report
