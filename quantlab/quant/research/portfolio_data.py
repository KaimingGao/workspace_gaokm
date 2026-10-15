"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.research.portfolio_bars import (
    DAILY_PORTFOLIO_MAX_NAMES,
    load_portfolio_stock_bars,
    should_fetch_backtest_fundamentals,
)

from core.backtest.paper_replay import REPLAY_FUSE_ENTER, REPLAY_FUSE_STRONG

# `/quant` 生成日报：历史回测对齐 /replay（rank_lots）；lookback 表单默认 30
DAILY_BT_UI_LOOKBACK = 30
DAILY_BT_UI_FUSION_W_CO = 0.0
DAILY_BT_UI_FUSE_ENTER = REPLAY_FUSE_ENTER
DAILY_BT_UI_FUSE_STRONG = REPLAY_FUSE_STRONG

__all__ = [
    "DAILY_BT_UI_LOOKBACK",
    "DAILY_BT_UI_FUSION_W_CO",
    "DAILY_BT_UI_FUSE_ENTER",
    "DAILY_BT_UI_FUSE_STRONG",
    "should_fetch_backtest_fundamentals",
    "load_portfolio_stock_bars",
    "resolve_daily_replay_kwargs",
    "daily_bt_option_defaults",
    "summarize_portfolio_backtest",
    "DAILY_PORTFOLIO_MAX_NAMES",
]


def resolve_daily_replay_kwargs(
    *,
    lookback: Optional[int] = None,
    apply_costs: Optional[bool] = None,
    fusion_w_co: Optional[float] = None,
    fuse_enter: Optional[float] = None,
    fuse_strong: Optional[float] = None,
) -> Dict[str, Any]:
    """日报历史回测：对齐 /replay paper_replay（w_co / fuse入场 / fuse强 / lookback）。"""
    from core.paper.rebalance.rank_lots import (
        clamp_fusion_w_co,
        coerce_fuse_threshold,
    )

    costs = True if apply_costs is None else bool(apply_costs)
    try:
        alpha = float(DAILY_BT_UI_FUSION_W_CO if fusion_w_co is None else fusion_w_co)
    except (TypeError, ValueError):
        alpha = float(DAILY_BT_UI_FUSION_W_CO)
    if alpha != alpha:
        alpha = float(DAILY_BT_UI_FUSION_W_CO)
    alpha = max(0.0, min(1.0, float(clamp_fusion_w_co(alpha))))
    enter = coerce_fuse_threshold(
        DAILY_BT_UI_FUSE_ENTER if fuse_enter is None else fuse_enter,
        DAILY_BT_UI_FUSE_ENTER,
    )
    strong = coerce_fuse_threshold(
        DAILY_BT_UI_FUSE_STRONG if fuse_strong is None else fuse_strong,
        DAILY_BT_UI_FUSE_STRONG,
    )
    enter = max(0.0, min(1.0, float(enter)))
    strong = max(0.0, min(1.0, float(strong)))
    if strong < enter:
        strong = enter
    lb = int(lookback) if lookback is not None else int(DAILY_BT_UI_LOOKBACK)
    lb = max(10, min(lb, 500))
    return {
        "lookback": lb,
        "apply_costs": costs,
        "fusion_w_co": alpha,
        "fuse_enter": enter,
        "fuse_strong": strong,
        "engine": "paper_replay",
    }


def daily_bt_option_defaults() -> Dict[str, Any]:
    """`/quant` 生成日报表单默认：历史回测对齐 /replay。"""
    from core.strategy import backtest_portfolio_defaults

    d = backtest_portfolio_defaults()
    replay = resolve_daily_replay_kwargs()
    return {
        "engine": "paper_replay",
        "lookback": replay["lookback"],
        "fusion_w_co": replay["fusion_w_co"],
        "fuse_enter": replay["fuse_enter"],
        "fuse_strong": replay["fuse_strong"],
        "apply_costs": True,
        "paper_max_positions": int(d.get("max_positions") or d.get("top_k") or 20),
        "paper_horizon_days": int(d.get("horizon_days") or 1),
        "note": (
            "日报历史回测=paper_replay/rank_lots（对齐 /replay · 09:30 · 入场/强档金额）；"
            "模块可改 w_co / Fuse入场 / Fuse强 / lookback；不写 signal_config；成本开"
        ),
    }


def _watching_sector_overlay() -> Dict[str, Any]:
    """watching 真主题叠加到 sector_map（默认不写盘）。"""
    try:
        from core.sector_map_sync import sync_sector_map_from_watching

        sync = sync_sector_map_from_watching(write=False)
    except Exception as exc:
        logger.exception('unexpected error in _watching_sector_overlay')
        return {"ok": False, "error": str(exc), "mapping": None}
    mapping = sync.get("mapping") if isinstance(sync, dict) else None
    if not isinstance(mapping, dict):
        mapping = None
    return {**sync, "mapping": mapping}


def summarize_portfolio_backtest(
    *,
    codes: Optional[List[str]] = None,
    lookback: int = DAILY_BT_UI_LOOKBACK,
    top_k: Optional[int] = None,
    horizon_days: Optional[int] = None,
    min_score: Optional[float] = None,
    min_predicted_score: Optional[float] = None,
    apply_costs: Optional[bool] = None,
    fusion_w_co: Optional[float] = None,
    fuse_enter: Optional[float] = None,
    fuse_strong: Optional[float] = None,
) -> Dict[str, Any]:
    """日报历史回测摘要：与 /replay 同一引擎 paper_replay / rank_lots。

    top_k / horizon_days / min_score 保留兼容签名，主路径不再截 Top-K 独立腿。
    """
    from core.backtest.paper_replay import (
        REPLAY_CASH_FLOOR,
        REPLAY_INITIAL_CASH,
        backtest_paper_replay,
    )
    from core.watching.store import read_watching

    _ = top_k, horizon_days, min_score, min_predicted_score

    candidates = list(codes or [])
    if not candidates:
        try:
            uni = read_watching()
            candidates = uni.get("watchlist") or []
        except FileNotFoundError:
            return {"success": False, "error": "watching.json 不存在且无 codes"}

    if len(candidates) < 2:
        return {"success": False, "error": "候选标的不足"}

    n_all = len(candidates)
    replay = resolve_daily_replay_kwargs(
        lookback=lookback,
        apply_costs=apply_costs,
        fusion_w_co=fusion_w_co,
        fuse_enter=fuse_enter,
        fuse_strong=fuse_strong,
    )
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        candidates,
        lookback=replay["lookback"],
        offline_ok=True,
        max_names=DAILY_PORTFOLIO_MAX_NAMES,
        fundamentals_live=False,
    )
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（{len(stock_bars)}）",
            "failures": failures,
        }

    sector_sync = _watching_sector_overlay()
    universe_n = len(stock_bars)
    bt = backtest_paper_replay(
        stock_bars,
        top_k=universe_n,
        cost_model="simple_cn" if replay["apply_costs"] else "zero",
        yhat_horizon_days=1,
        initial_cash=REPLAY_INITIAL_CASH,
        cash_floor=REPLAY_CASH_FLOOR,
        fusion_w_co=replay["fusion_w_co"],
        fuse_enter=replay["fuse_enter"],
        fuse_strong=replay["fuse_strong"],
        lookback=int(replay["lookback"]),
    )
    if isinstance(bt, dict):
        bt.pop("paper", None)
    if not bt.get("success"):
        return bt

    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    params = dict(bt.get("params") or {})
    params["engine"] = "paper_replay"
    params["apply_costs"] = bool(replay["apply_costs"])
    params["lookback"] = int(replay["lookback"])
    params["fuse_enter"] = replay["fuse_enter"]
    params["fuse_strong"] = replay["fuse_strong"]
    params["fusion_w_co"] = replay["fusion_w_co"]
    params["cost_model"] = "simple_cn" if replay["apply_costs"] else "zero"

    note_bits = []
    if n_all > DAILY_PORTFOLIO_MAX_NAMES:
        note_bits.append(
            f"日报轻量回测截断观察池 {n_all}→{len(stock_bars)}（缓存优先，上限 {DAILY_PORTFOLIO_MAX_NAMES}），"
            "基本面仅本地缓存（避免串行远端挂死）"
        )
    note_bits.append(
        "引擎=paper_replay：每个交易日 09:30 rank_lots"
        f"（w_co={replay['fusion_w_co']:g}；入场={replay['fuse_enter']:g}；"
        f"强={replay['fuse_strong']:g}；宇宙={universe_n} 只）"
    )
    note_bits.append("对齐历史回测页 · 成本=" + ("开" if replay["apply_costs"] else "关"))
    note = str(bt.get("note") or "")
    if note_bits:
        extra = " · ".join(note_bits)
        note = f"{note} · {extra}" if note else extra

    cost_model = params.get("cost_model") or (
        "simple_cn" if replay["apply_costs"] else "zero"
    )
    return {
        "success": True,
        "engine": "paper_replay",
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
        "equity_curve_tail": curve[-12:],
        "failures": failures,
        "note": note,
        "metrics": metrics,
        "constraints_hit": bt.get("constraints_hit"),
        "cost_model": cost_model,
        "research_next": [
            "人审 ŷ 残差对照 POST /api/quant/yhat-residual/shadow",
            "过门后再开 cross_section.yhat_residual / y_spec.excess_mode=index",
            "完整曲线与成交账见 /replay 历史回测",
        ],
        "sector_sync": {
            "ok": sector_sync.get("ok"),
            "coverage": sector_sync.get("coverage"),
            "added_count": sector_sync.get("added_count"),
            "written": False,
        },
    }
