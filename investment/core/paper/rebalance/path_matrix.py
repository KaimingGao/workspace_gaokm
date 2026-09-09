"""策略调仓配置（rank_lots）。

Follow / 历史回测 / 自动调仓都走 ``rank_lots``。本模块只提供
``rank_enter`` / ``rank_strong`` / ``cash_floor`` / ``holdings_mv_cap`` /
融合权重 / ``y_on_alpha``，以及 ``scores_from_rebalance_item``。

配置键优先 ``rebalance_timing.rank_lots``，仍认旧键 ``path_matrix``。
λ / 同号闸 / 横截面 TopK 已删除。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH_MATRIX: Dict[str, Any] = {
    "enabled": True,
    "mode": "rank_lots",
    "rank_enter": 0.012,
    "rank_strong": 0.012,
    "cash_floor": 500_000.0,
    "holdings_mv_cap": 150_000.0,
    "fusion_w_trade": 0.5,
    "fusion_w_nowcast": 0.5,
    "y_on_alpha": 0.0,
}


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _pick_raw_cfg(d: Optional[dict]) -> Optional[dict]:
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("rank_lots"), dict):
        return d["rank_lots"]
    if isinstance(d.get("path_matrix"), dict):
        return d["path_matrix"]
    if any(k in d for k in DEFAULT_PATH_MATRIX):
        return d
    return None


def get_path_matrix_cfg(
    timing_or_cfg: Optional[dict] = None,
    *,
    paper: Optional[dict] = None,
) -> Dict[str, Any]:
    """读 rank_lots 配置：``rank_lots`` 优先，否则旧键 ``path_matrix``，再否则默认。"""
    out = dict(DEFAULT_PATH_MATRIX)
    raw = _pick_raw_cfg(timing_or_cfg)
    if raw is None and isinstance(paper, dict):
        try:
            from core.paper.open_fill import get_rebalance_timing

            timing = get_rebalance_timing(paper) or {}
            raw = _pick_raw_cfg(timing)
        except Exception:  # noqa: BLE001
            logger.debug("get_path_matrix_cfg timing failed", exc_info=True)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and v is not None:
                out[k] = v
    out["mode"] = "rank_lots"
    out["enabled"] = bool(out.get("enabled"))
    for key, default, lo, hi in (
        ("rank_enter", 0.012, 0.0, 10.0),
        ("rank_strong", 0.012, 0.0, 10.0),
        ("cash_floor", 500_000.0, 0.0, 1.0e8),
        ("holdings_mv_cap", 150_000.0, 0.0, 1.0e8),
        ("fusion_w_trade", 0.5, 0.0, 1.0),
        ("fusion_w_nowcast", 0.5, 0.0, 1.0),
        ("y_on_alpha", 0.0, 0.0, 10.0),
    ):
        try:
            out[key] = max(lo, min(float(out.get(key, default)), hi))
        except (TypeError, ValueError):
            out[key] = float(default)
    for rk in ("rank_enter", "rank_strong"):
        v = float(out[rk])
        if v >= 0.5:
            if abs(v - 1.0) < 1e-9:
                v = 0.01
            elif abs(v - 1.002) < 1e-6:
                v = 0.02
            else:
                v = max(0.0, v - 1.0)
        elif abs(v - 0.20) < 1e-6:
            v = 0.02
        out[rk] = v
    wt = float(out["fusion_w_trade"])
    wn = float(out["fusion_w_nowcast"])
    s = wt + wn
    if s <= 1e-12:
        out["fusion_w_trade"], out["fusion_w_nowcast"] = 0.5, 0.5
    else:
        out["fusion_w_trade"], out["fusion_w_nowcast"] = wt / s, wn / s
    if float(out["rank_strong"]) < float(out["rank_enter"]):
        out["rank_strong"] = float(out["rank_enter"])
    return out


def fuse_trade_nowcast(
    y_trade: Optional[float],
    y_nowcast: Optional[float],
    *,
    w_trade: float = 0.5,
    w_nowcast: float = 0.5,
) -> Optional[float]:
    """加权融合；缺一侧则用另一侧；都缺则 None。"""
    wt = max(0.0, float(w_trade))
    wn = max(0.0, float(w_nowcast))
    t = _f(y_trade)
    n = _f(y_nowcast)
    if t is None and n is None:
        return None
    if t is None:
        return float(n)
    if n is None:
        return float(t)
    s = wt + wn
    if s <= 1e-12:
        return 0.5 * float(t) + 0.5 * float(n)
    return (wt * float(t) + wn * float(n)) / s


def scores_from_rebalance_item(item: Optional[dict]) -> Dict[str, Optional[float]]:
    """从调仓行 / signal_item 抽四分数（字段与 dual_y 对齐）。"""
    if not isinstance(item, dict):
        return {"y_trade": None, "y_path": None, "y_nowcast": None, "y_on": None}

    y_trade = _f(item.get("y_trade"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score_blend"))
    if y_trade is None:
        y_trade = _f(item.get("decision_score"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score"))
    if y_trade is not None and abs(y_trade) > 20.0:
        y_trade = None

    y_path = _f(item.get("y_path"))
    if y_path is None:
        y_path = _f(item.get("predicted_score_path"))

    y_nowcast = _f(item.get("y_nowcast"))
    if y_nowcast is None:
        y_nowcast = _f(item.get("predicted_score_nowcast"))

    y_on = _f(item.get("y_on"))
    if y_on is None:
        y_on = _f(item.get("predicted_score_on"))

    return {
        "y_trade": y_trade,
        "y_path": y_path,
        "y_nowcast": y_nowcast,
        "y_on": y_on,
    }


__all__ = [
    "DEFAULT_PATH_MATRIX",
    "fuse_trade_nowcast",
    "get_path_matrix_cfg",
    "scores_from_rebalance_item",
]
