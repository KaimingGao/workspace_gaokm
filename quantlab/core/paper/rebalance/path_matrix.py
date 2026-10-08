"""策略调仓配置（rank_lots）。

Follow / 历史回测 / 自动调仓都走 ``rank_lots``。
ranking = w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)。w_co 默认 1。
ŷ_trade / ŷ_nowcast / y_fuse / y_tau 已下线（τ 头只写 y_τc）。

配置键优先 ``rebalance_timing.rank_lots``，仍认旧键 ``path_matrix``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from core.signal.yhat_windows import (
    fusion_w_co_from_cfg,
    fusion_weights_from_cfg,
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
    "y_oo_gt0": False,
    "y_τc_gt0": False,
    # 历史回测成交钟（/replay）；不驱动 live 自动调仓。
    "fill_clock": "09:30",
    # Live 自动调仓 / 手动预演窗口起点；止于 10:00。与回测 fill_clock 分立。
    "live_fill_clock": "09:30",
    "score_backend": "ridge",
    # 手数：按金额/价换算整手，不够一手则买一手；保存规则写入交易执行。缺省 live 1万/2万。
    "lot_base_amount": 10_000.0,
    "lot_strong_amount": 20_000.0,
}


def _clamp_lot_amount(raw: Any, default: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v:
        v = float(default)
    v = max(1_000.0, min(v, 1_000_000.0))
    return float(int(round(v / 100.0)) * 100)


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
    from core.t0.config import coerce_cfg_bool

    out["y_enter_enabled"] = coerce_cfg_bool(out.get("y_enter_enabled"), True)
    out["y_enter_alt_enabled"] = coerce_cfg_bool(out.get("y_enter_alt_enabled"), True)
    out["y_oo_gt0"] = coerce_cfg_bool(out.get("y_oo_gt0"), False)
    out["y_τc_gt0"] = coerce_cfg_bool(out.get("y_τc_gt0"), False)
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
    w_oo, w_τc = fusion_weights_from_cfg(out)
    out["fusion_w_oo"], out["fusion_w_oc"] = w_oo, w_τc
    w_co = fusion_w_co_from_cfg(out)
    out["fusion_w_co"] = w_co
    if float(out["rank_strong"]) < float(out["rank_enter"]):
        out["rank_strong"] = float(out["rank_enter"])
    out["cash_floor"] = 0.0
    if isinstance(raw, dict):
        if raw.get("rank_enter_alt") in (None, ""):
            out["rank_enter_alt"] = float(out["rank_enter"])
    try:
        from core.backtest.paper_replay import (
            LIVE_FILL_CLOCK,
            REPLAY_FILL_CLOCK,
            clamp_live_fill_clock,
            clamp_replay_fill_clock,
        )

        out["fill_clock"] = clamp_replay_fill_clock(
            out.get("fill_clock"), REPLAY_FILL_CLOCK
        )
        # 缺省独立；不回退 fill_clock，避免回测改钟拖动 live。可含 09:25。
        raw_live = out.get("live_fill_clock")
        if raw_live in (None, ""):
            out["live_fill_clock"] = LIVE_FILL_CLOCK
        else:
            out["live_fill_clock"] = clamp_live_fill_clock(raw_live, LIVE_FILL_CLOCK)
    except Exception:  # noqa: BLE001
        logger.debug("clamp fill_clock failed", exc_info=True)
        out["fill_clock"] = "09:30"
        out["live_fill_clock"] = "09:30"
    try:
        from core.research.return_tree import normalize_rebalance_score_backend

        out["score_backend"] = normalize_rebalance_score_backend(out.get("score_backend"))
    except Exception:  # noqa: BLE001
        logger.debug("clamp score_backend failed", exc_info=True)
        out["score_backend"] = "ridge"
    base = _clamp_lot_amount(out.get("lot_base_amount"), 10_000.0)
    strong = _clamp_lot_amount(out.get("lot_strong_amount"), 20_000.0)
    if strong < base:
        strong = base
    out["lot_base_amount"] = base
    out["lot_strong_amount"] = strong
    return out


def scores_from_rebalance_item(
    item: Optional[dict],
    cfg: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    """从调仓行抽出 ŷ_oo / ŷ_oc / ŷ_τc / ŷ_co 与 ranking / residual。"""
    return stamp_window_scores(item, cfg)


__all__ = [
    "DEFAULT_PATH_MATRIX",
    "get_path_matrix_cfg",
    "scores_from_rebalance_item",
]
