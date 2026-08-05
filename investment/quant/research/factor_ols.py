"""因子面板 OLS（研究用，不自动写 signal_config）。

- 单票 walk-forward：``compute_factor_ols_report``
- 研究池堆叠时序：``compute_factor_ols_pooled_report``（非逐日截面 Fama–MacBeth）

研究路径与生产 ``score_bars`` 解耦：全量注册因子、不走 regime 择时白名单；
拟合前对入模列做样本内 z-score，β 表示 1σ 偏效应（勿与 config 权重同量级对比）。
求解：默认 QR 最小二乘；``ridge_lambda>0`` 时用 Ridge（截距不惩罚），缓解共线与过拟合。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.research.factor_ols_fit import (
    _exclusion_reasons_map,
    clamp_ridge_lambda,
    fit_factor_ols_from_panel,
)
from core.research.panel import collect_subscore_forward_panel

# Backward-compatible re-exports (implementation lives in core.research).
__all__ = [
    "clamp_ridge_lambda",
    "collect_subscore_forward_panel",
    "fit_factor_ols_from_panel",
    "compute_factor_ols_report",
    "compute_factor_ols_pooled_report",
]


def compute_factor_ols_report(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
    ridge_lambda: float = 0.0,
) -> Dict[str, Any]:
    """对 sub_scores 拟合 forward return 的 OLS/Ridge（研究用，不产出生产权重 patch）。"""
    xs, ys = collect_subscore_forward_panel(
        bars,
        horizon_days=horizon_days,
        min_history=min_history,
        max_window=max_window,
        index_bars=index_bars,
        fundamentals=fundamentals,
        stock_code=stock_code,
        pit_fundamentals=pit_fundamentals,
    )
    out = fit_factor_ols_from_panel(
        xs,
        ys,
        horizon_days=horizon_days,
        fundamentals_used=bool(pit_fundamentals) or fundamentals is not None,
        pit_fundamentals=pit_fundamentals,
        mode="single",
        ridge_lambda=ridge_lambda,
    )
    if stock_code:
        out["stock_code"] = stock_code
    return out


def compute_factor_ols_pooled_report(
    stock_panels: List[Dict[str, Any]],
    *,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
) -> Dict[str, Any]:
    """堆叠多票时序面板后拟合 OLS/Ridge（研究池探针，非逐日截面回归）。

    每项需含 ``code`` 与 ``bars``；可选 ``index_bars`` / ``fundamentals``。
    """
    all_xs: List[Dict[str, Optional[float]]] = []
    all_ys: List[float] = []
    loaded: List[str] = []
    skipped: List[Dict[str, str]] = []
    any_fundamentals = False

    for item in stock_panels or []:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code or not bars:
            if code:
                skipped.append({"code": code, "reason": "无日线"})
            continue
        xs, ys = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            pit_fundamentals=True,
        )
        if not ys:
            skipped.append({"code": code, "reason": "面板为空"})
            continue
        all_xs.extend(xs)
        all_ys.extend(ys)
        loaded.append(code)
        any_fundamentals = True

    if len(loaded) < 2:
        return {
            "success": False,
            "error": "研究池有效样本不足 2 只，无法做池内 OLS",
            "task": "factor_ols_pool",
            "mode": "watching_pooled",
            "stock_codes": loaded,
            "stock_count": len(loaded),
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": clamp_ridge_lambda(ridge_lambda, 0.0),
        }

    out = fit_factor_ols_from_panel(
        all_xs,
        all_ys,
        horizon_days=horizon_days,
        fundamentals_used=any_fundamentals,
        pit_fundamentals=True,
        mode="watching_pooled",
        stock_codes=loaded,
        ridge_lambda=ridge_lambda,
    )
    out["skipped"] = skipped
    out["raw_row_count"] = len(all_ys)
    return out
