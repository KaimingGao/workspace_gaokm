"""组合回测 Walk-forward 最小切片（P1）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = {str(b.get("date") or "").strip() for b in (bars or []) if b.get("date")}
        keys.discard("")
        common = keys if common is None else common & keys
    return sorted(common or [])


def _slice_bars_to_dates(
    stock_bars: Dict[str, List[dict]], allowed_dates: set
) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for code, bars in (stock_bars or {}).items():
        sliced = [b for b in (bars or []) if str(b.get("date") or "").strip() in allowed_dates]
        if len(sliced) >= 2:
            out[code] = sliced
    return out


def _test_period_return(equity_curve: List[dict], test_dates: set) -> Optional[float]:
    pts = [p for p in (equity_curve or []) if str(p.get("date") or "") in test_dates]
    if len(pts) < 2:
        return None
    try:
        eq0 = float(pts[0].get("equity"))
        eq1 = float(pts[-1].get("equity"))
        if eq0 <= 0:
            return None
        return round((eq1 / eq0 - 1.0) * 100.0, 2)
    except (TypeError, ValueError):
        return None


def run_portfolio_wf_slices(
    stock_bars: Dict[str, List[dict]],
    *,
    n_splits: int = 3,
    apply_costs: bool = True,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    min_train: int = 40,
    min_test: int = 12,
    weight_mode: str = "equal",
    max_position_pct: float = 40.0,
    max_sector_pct: float = 60.0,
    dropout_n: int = 0,
) -> Dict[str, Any]:
    """对组合横截面回测跑扩展窗 WF；每折用截至 test_end 的日线，报告测试段收益。"""
    from core.backtest.topk_backtest import backtest_topk_equal_weight
    from research.split import rolling_walk_forward_slices

    dates = _common_dates(stock_bars)
    n = len(dates)
    folds = rolling_walk_forward_slices(
        n, n_splits=n_splits, min_train=min_train, min_test=min_test
    )
    if not folds:
        return {
            "ok": False,
            "reason": f"共同交易日不足（{n}），无法切 Walk-forward",
            "common_dates": n,
            "n_splits": n_splits,
            "folds": [],
        }

    common_kw = dict(
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fundamentals_by_code or None,
        weight_mode=weight_mode,
        max_position_pct=max_position_pct,
        max_sector_pct=max_sector_pct,
        dropout_n=dropout_n,
    )
    fold_rows: List[dict] = []
    test_returns: List[float] = []

    for fold in folds:
        allowed = set(dates[: fold.test_end])
        sliced = _slice_bars_to_dates(stock_bars, allowed)
        if len(sliced) < 2:
            fold_rows.append(
                {
                    "fold": fold.fold,
                    "ok": False,
                    "reason": "切片后标的不足",
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "train_end_date": dates[fold.train_end - 1] if fold.train_end else None,
                    "test_start_date": dates[fold.test_start] if fold.test_start < n else None,
                    "test_end_date": dates[fold.test_end - 1] if fold.test_end else None,
                }
            )
            continue
        res = backtest_topk_equal_weight(
            sliced,
            apply_costs=apply_costs,
            **common_kw,
        )
        test_dates = set(dates[fold.test_start : fold.test_end])
        test_ret = _test_period_return(res.get("equity_curve") or [], test_dates)
        m = (res or {}).get("metrics") or {}
        ok = bool(res.get("success"))
        if ok and test_ret is not None:
            test_returns.append(float(test_ret))
        fold_rows.append(
            {
                "fold": fold.fold,
                "ok": ok,
                "reason": None if ok else (res.get("error") or "失败"),
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "train_end_date": dates[fold.train_end - 1] if fold.train_end else None,
                "test_start_date": dates[fold.test_start] if fold.test_start < n else None,
                "test_end_date": dates[fold.test_end - 1] if fold.test_end else None,
                "test_return_pct": test_ret,
                "window_return_pct": m.get("total_return_pct"),
                "max_drawdown_pct": m.get("max_drawdown_pct"),
                "trade_count": m.get("trade_count") or res.get("trade_count"),
            }
        )

    mean_ret = round(sum(test_returns) / len(test_returns), 2) if test_returns else None
    fail_n = sum(1 for r in fold_rows if not r.get("ok") or r.get("test_return_pct") is None)
    pos_n = sum(1 for r in test_returns if r > 0)
    return {
        "ok": bool(fold_rows) and fail_n < len(fold_rows),
        "common_dates": n,
        "n_splits": len(fold_rows),
        "mean_test_return_pct": mean_ret,
        "fail_folds": fail_n,
        "positive_test_folds": pos_n,
        "measured_test_folds": len(test_returns),
        "folds": fold_rows,
        "note": (
            "扩展窗 Walk-forward：每折训练集为测试段之前全部历史；收益取测试段净值变化。"
            f"正窗 {pos_n}/{len(test_returns)}。"
            if test_returns
            else "扩展窗 Walk-forward：每折训练集为测试段之前全部历史；收益取测试段净值变化。"
        ),
    }
