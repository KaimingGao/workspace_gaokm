"""策略调仓配置（rank_lots）。

Follow / 历史回测 / 自动调仓都走 ``rank_lots``。
rank = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)。w_co 默认 1。
ŷ_trade / ŷ_nowcast / y_fuse 已下线。

配置键优先 ``rebalance_timing.rank_lots``，仍认旧键 ``path_matrix``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from core.signal.yhat_windows import (  # noqa: F401
    fuse_pct,
    fusion_w_co_from_cfg,
    fusion_weights_from_cfg,
    pick_y_co,
    pick_y_oc,
    pick_y_oo,
    pick_y_τc,
    ranking_pct,
    residual_pct,
    stamp_window_scores,
)

DEFAULT_PATH_MATRIX: Dict[str, Any] = {
    "enabled": True,
    "mode": "rank_lots",
    "rank_enter": 0.001,
    "rank_strong": 0.001,
    "rank_enter_alt": 0.001,
    "cash_floor": 0.0,
    "holdings_mv_cap": 150_000.0,
    "fusion_w_oo": 0.6,
    "fusion_w_oc": 0.4,
    "fusion_w_co": 1.0,
    "fusion_w_pc": 0.5,
    "residual_w_oc": 0.5,
    "y_enter_enabled": True,
    "y_enter_alt_enabled": True,
    "y_hl_enabled": True,
    "y_oo_enter": 0.1,
    "y_oc_enter": 0.1,
    "y_hl_enter": 0.1,
    "y_oo_enter_alt": 0.1,
    "y_oc_enter_alt": 0.1,
    "y_hl_enter_alt": 0.1,
    # 调仓成交钟：自动调仓 / 手动预演窗口起点；止于 10:00。与历史回测 fill_clock 同源。
    "fill_clock": "09:30",
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
        if raw.get("fusion_w_oo") is None and raw.get("fusion_w_trade") is not None:
            out["fusion_w_oo"] = raw.get("fusion_w_trade")
        if raw.get("fusion_w_oc") is None and raw.get("fusion_w_nowcast") is not None:
            out["fusion_w_oc"] = raw.get("fusion_w_nowcast")
        if raw.get("fusion_w_pc") is None and raw.get("residual_w_pc") is not None:
            out["fusion_w_pc"] = raw.get("residual_w_pc")
        if raw.get("fusion_w_co") is None and raw.get("y_on_alpha") is not None:
            out["fusion_w_co"] = raw.get("y_on_alpha")
    out["mode"] = "rank_lots"
    out["enabled"] = bool(out.get("enabled"))
    from core.t0.config import coerce_cfg_bool

    out["y_enter_enabled"] = coerce_cfg_bool(out.get("y_enter_enabled"), True)
    out["y_enter_alt_enabled"] = coerce_cfg_bool(out.get("y_enter_alt_enabled"), True)
    out["y_hl_enabled"] = coerce_cfg_bool(out.get("y_hl_enabled"), True)
    for key, default, lo, hi in (
        ("rank_enter", 0.001, 0.0, 10.0),
        ("rank_strong", 0.001, 0.0, 10.0),
        ("rank_enter_alt", 0.001, 0.0, 10.0),
        ("cash_floor", 0.0, 0.0, 1.0e8),
        ("holdings_mv_cap", 150_000.0, 0.0, 1.0e8),
        ("fusion_w_oo", 0.6, 0.0, 1.0),
        ("fusion_w_oc", 0.4, 0.0, 1.0),
        ("fusion_w_co", 1.0, 0.0, 10.0),
        ("fusion_w_pc", 0.5, 0.0, 1.0),
        ("residual_w_oc", 0.5, 0.0, 1.0),
        ("y_oo_enter", 0.1, 0.0, 100.0),
        ("y_oc_enter", 0.1, 0.0, 100.0),
        ("y_hl_enter", 0.1, 0.0, 100.0),
        ("y_oo_enter_alt", 0.1, 0.0, 100.0),
        ("y_oc_enter_alt", 0.1, 0.0, 100.0),
        ("y_hl_enter_alt", 0.1, 0.0, 100.0),
    ):
        try:
            out[key] = max(lo, min(float(out.get(key, default)), hi))
        except (TypeError, ValueError):
            out[key] = float(default)
    for rk in ("rank_enter", "rank_strong", "rank_enter_alt"):
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
    w_oo, w_oc = fusion_weights_from_cfg(out)
    out["fusion_w_oo"], out["fusion_w_oc"] = w_oo, w_oc
    w_co = fusion_w_co_from_cfg(out)
    out["fusion_w_co"] = w_co
    out["y_on_alpha"] = w_co
    # 旧键镜像，避免未改的 UI 读空
    out["fusion_w_trade"] = w_oo
    out["fusion_w_nowcast"] = w_oc
    if float(out["rank_strong"]) < float(out["rank_enter"]):
        out["rank_strong"] = float(out["rank_enter"])
    out["cash_floor"] = 0.0
    if isinstance(raw, dict):
        if raw.get("rank_enter_alt") in (None, ""):
            out["rank_enter_alt"] = float(out["rank_enter"])
        for src, alt in (
            ("y_oo_enter", "y_oo_enter_alt"),
            ("y_oc_enter", "y_oc_enter_alt"),
            ("y_hl_enter", "y_hl_enter_alt"),
        ):
            if raw.get(alt) in (None, ""):
                out[alt] = float(out[src])
    try:
        from core.backtest.paper_replay import REPLAY_FILL_CLOCK, clamp_replay_fill_clock

        out["fill_clock"] = clamp_replay_fill_clock(
            out.get("fill_clock"), REPLAY_FILL_CLOCK
        )
    except Exception:  # noqa: BLE001
        logger.debug("clamp fill_clock failed", exc_info=True)
        out["fill_clock"] = "09:30"
    return out


def scores_from_rebalance_item(
    item: Optional[dict],
    cfg: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    """从调仓行抽出 ŷ_oo / ŷ_oc / ŷ_τc / ŷ_co 与 ranking / residual。"""
    stamped = stamp_window_scores(item, cfg)
    y_path = None
    if isinstance(item, dict):
        from core.research.path_panel import pick_y_hl, write_y_hl

        y_path = pick_y_hl(item)
        if y_path is not None:
            write_y_hl(stamped, y_path)
        else:
            stamped["y_hl"] = None
    else:
        stamped["y_hl"] = None
    stamped["y_tau"] = stamped.get("y_oc")
    if stamped.get("ranking") is not None:
        stamped["y_fuse"] = stamped.get("ranking")
    return stamped


__all__ = [
    "DEFAULT_PATH_MATRIX",
    "fuse_pct",
    "fusion_w_co_from_cfg",
    "get_path_matrix_cfg",
    "pick_y_co",
    "pick_y_oc",
    "pick_y_oo",
    "pick_y_τc",
    "ranking_pct",
    "residual_pct",
    "scores_from_rebalance_item",
    "stamp_window_scores",
]
