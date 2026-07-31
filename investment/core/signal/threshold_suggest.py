"""基于 OOS 回测的 stance 阈值微调建议（P13.3）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.config import get_stance_thresholds, load_signal_config

STANCE_KEYS = ("avoid", "wait", "probe")


def suggest_stance_thresholds_from_oos(
    oos_result: Dict[str, Any],
    *,
    current_thresholds: Optional[Dict[str, float]] = None,
    max_delta: float = 3.0,
    min_test_trades: int = 3,
) -> Dict[str, Any]:
    """根据 OOS test 指标与 train 最优 min_score 给出阈值微调建议（不自动写配置）。"""
    if not oos_result.get("success"):
        return {"success": False, "error": oos_result.get("error") or "OOS 扫描失败"}

    cfg = load_signal_config()
    base = dict(current_thresholds or get_stance_thresholds(cfg))
    if not base:
        return {"success": False, "error": "无 stance 阈值配置"}

    test_metrics = ((oos_result.get("test") or {}).get("metrics") or {})
    test_trades = int(test_metrics.get("trade_count") or 0)
    test_win = test_metrics.get("win_rate_pct")
    best_min = (oos_result.get("best_params") or {}).get("min_score")

    rationale: List[str] = []
    deltas = {k: 0.0 for k in STANCE_KEYS}
    suggested = {k: round(float(base[k]), 1) for k in STANCE_KEYS}

    if best_min is not None and test_trades >= min_test_trades:
        wait_cur = float(base["wait"])
        target_delta = float(best_min) - wait_cur
        target_delta = max(-max_delta, min(max_delta, target_delta))
        if abs(target_delta) >= 1.0:
            deltas["wait"] = round(target_delta, 1)
            suggested["wait"] = round(wait_cur + target_delta, 1)
            suggested["avoid"] = round(min(suggested["wait"] - 8.0, float(base["avoid"]) + target_delta * 0.5), 1)
            suggested["probe"] = round(max(suggested["wait"] + 10.0, float(base["probe"])), 1)
            rationale.append(
                f"OOS 最优 min_score={best_min}，test 胜率={test_win}%（n={test_trades}）"
                f" → 建议 wait {wait_cur}→{suggested['wait']}"
            )
        elif test_win is not None and test_win < 45 and test_trades >= min_test_trades:
            deltas["wait"] = max_delta
            suggested["wait"] = round(float(base["wait"]) + max_delta, 1)
            suggested["probe"] = round(max(suggested["wait"] + 10.0, float(base["probe"])), 1)
            rationale.append(
                f"test 胜率偏低（{test_win}%）→ 建议提高 wait +{max_delta} 以收紧买入门槛"
            )

    # 保持 avoid < wait < probe
    if suggested["avoid"] >= suggested["wait"]:
        suggested["avoid"] = round(suggested["wait"] - 8.0, 1)
    if suggested["probe"] <= suggested["wait"]:
        suggested["probe"] = round(suggested["wait"] + 10.0, 1)

    return {
        "success": True,
        "current_thresholds": {k: round(float(base[k]), 1) for k in STANCE_KEYS},
        "suggested_thresholds": suggested,
        "deltas": {k: round(deltas.get(k, 0.0), 1) for k in STANCE_KEYS},
        "oos": {
            "best_params": oos_result.get("best_params"),
            "test_metrics": test_metrics,
            "split": oos_result.get("split"),
        },
        "rationale": rationale or ["当前 OOS 指标与阈值大致匹配，暂不建议调整"],
        "params": {"max_delta": max_delta, "min_test_trades": min_test_trades},
        "note": "建议仅供研究；改 stance_thresholds 前须确认 test 样本外表现，勿直接用于投顾结论。",
    }


def format_threshold_config_diff(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """生成可下载的 stance_thresholds diff（不自动写盘）。"""
    from datetime import datetime

    if not suggestion.get("success"):
        return {"success": False, "error": suggestion.get("error") or "无效阈值建议"}

    current = suggestion.get("current_thresholds") or {}
    suggested = suggestion.get("suggested_thresholds") or {}
    changes: Dict[str, dict] = {}
    for key in STANCE_KEYS:
        c = float(current.get(key, 0))
        s = float(suggested.get(key, c))
        delta = round(s - c, 1)
        if abs(delta) >= 0.5:
            changes[key] = {"from": round(c, 1), "to": round(s, 1), "delta": delta}

    return {
        "success": True,
        "target_file": "data/signal_config.json",
        "patch": {"stance_thresholds": suggested},
        "changes": changes,
        "deltas": suggestion.get("deltas") or {},
        "rationale": suggestion.get("rationale") or [],
        "params": suggestion.get("params") or {},
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "apply_note": "请手动合并 patch.stance_thresholds；须以 OOS test 指标为准。",
    }


def suggest_stance_thresholds_from_watching_oos(
    codes: List[str],
    *,
    lookback: int = 120,
    max_stocks: int = 5,
    max_delta: float = 3.0,
) -> Dict[str, Any]:
    """对 watching 多票分别 OOS 扫描，用中位最优 min_score 聚合阈值建议。"""
    from core.backtest.engine import scan_signal_parameters_oos
    from core.ports.market import fetch_daily_bars, query_quote

    if not codes:
        return {"success": False, "error": "候选列表为空"}

    best_mins: List[float] = []
    win_rates: List[float] = []
    per_stock: List[dict] = []
    failures: List[str] = []

    for raw in codes[: max(1, min(int(max_stocks or 5), 10))]:
        quote = query_quote(str(raw))
        sym = quote.get("stock_code") if quote.get("success") else str(raw)
        bars, src = fetch_daily_bars(raw, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = fetch_daily_bars(sym, limit=lookback + 35)
        if not bars or len(bars) < 40:
            failures.append(str(raw))
            continue

        oos = scan_signal_parameters_oos(
            bars,
            min_scores=[45, 50, 55, 60, 65],
            horizon_days_list=[2, 3],
        )
        if not oos.get("success"):
            failures.append(sym)
            continue

        best_min = (oos.get("best_params") or {}).get("min_score")
        test_m = ((oos.get("test") or {}).get("metrics") or {})
        if best_min is None:
            continue
        best_mins.append(float(best_min))
        if test_m.get("win_rate_pct") is not None:
            win_rates.append(float(test_m["win_rate_pct"]))
        per_stock.append(
            {
                "stock_code": sym,
                "data_source": src,
                "best_min_score": best_min,
                "test_win_rate_pct": test_m.get("win_rate_pct"),
                "test_trade_count": test_m.get("trade_count"),
            }
        )

    if not best_mins:
        return {
            "success": False,
            "error": "watching OOS 无有效结果",
            "failures": failures,
        }

    best_mins.sort()
    median_best = best_mins[len(best_mins) // 2]
    avg_win = sum(win_rates) / len(win_rates) if win_rates else None
    synthetic_oos = {
        "success": True,
        "best_params": {"min_score": median_best, "horizon_days": 3},
        "test": {
            "metrics": {
                "trade_count": max(3, len(best_mins) * 2),
                "win_rate_pct": avg_win,
            }
        },
    }
    suggestion = suggest_stance_thresholds_from_oos(
        synthetic_oos,
        max_delta=max_delta,
    )
    suggestion["watching_aggregate"] = {
        "stock_count": len(best_mins),
        "median_best_min_score": median_best,
        "best_min_scores": best_mins,
        "per_stock": per_stock,
        "failures": failures,
    }
    suggestion["mode"] = "watching_oos_aggregate"
    return suggestion
