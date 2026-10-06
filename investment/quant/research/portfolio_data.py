"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.research.portfolio_bars import (
    DAILY_PORTFOLIO_MAX_NAMES,
    load_portfolio_stock_bars,
    should_fetch_backtest_fundamentals,
)

from core.backtest.paper_replay import REPLAY_RANK_ENTER, REPLAY_RANK_STRONG

# `/quant` 生成日报：历史回测对齐 /replay（rank_lots）；lookback 表单默认 30
DAILY_BT_UI_TOP_K = 3  # 仅中性化对照 / 旧 API；主回测不再截 Top-K
DAILY_BT_UI_HORIZON_DAYS = 1
DAILY_BT_UI_LOOKBACK = 30
DAILY_BT_UI_FUSION_W_CO = 0.0
DAILY_BT_UI_RANK_ENTER = REPLAY_RANK_ENTER
DAILY_BT_UI_RANK_STRONG = REPLAY_RANK_STRONG

__all__ = [
    "DAILY_BT_UI_TOP_K",
    "DAILY_BT_UI_HORIZON_DAYS",
    "DAILY_BT_UI_LOOKBACK",
    "DAILY_BT_UI_FUSION_W_CO",
    "DAILY_BT_UI_RANK_ENTER",
    "DAILY_BT_UI_RANK_STRONG",
    "should_fetch_backtest_fundamentals",
    "load_portfolio_stock_bars",
    "resolve_daily_topk_backtest_kwargs",
    "resolve_daily_replay_kwargs",
    "daily_bt_option_defaults",
    "summarize_portfolio_backtest",
    "DAILY_PORTFOLIO_MAX_NAMES",
]


def resolve_daily_topk_backtest_kwargs(
    *,
    top_k: Optional[int] = None,
    horizon_days: Optional[int] = None,
    min_predicted_score: Optional[float] = None,
    apply_costs: Optional[bool] = None,
    weight_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """日报 / 轻量对照共用：K 与持有期对齐纸面；ŷ 标签窗口单独记录；默认含成本。"""
    from core.signal.config import get_scoring_horizon_days
    from core.strategy import backtest_portfolio_defaults

    d = backtest_portfolio_defaults()
    yhat_h = int(get_scoring_horizon_days())
    paper_h = int(d.get("horizon_days") or yhat_h)
    # 研究默认 top_k=3（小组合探路）；日报必须跟纸面 max_positions，否则累计会被
    # 3 槽 + 单票 25% 上限压成半仓，和纸面/历史基线不可比。
    paper_k = int(d.get("max_positions") or d.get("top_k") or 20)
    k = int(top_k) if top_k is not None else paper_k
    # 持有期跟纸面，避免 ŷ(h=1) 迫使日频换仓把费用吃光；ŷ 仍按 scoring.horizon_days 训练
    h = int(horizon_days) if horizon_days is not None else paper_h
    costs = True if apply_costs is None else bool(apply_costs)
    mode = str(weight_mode or d.get("weight_mode") or "score_budget")
    pred = min_predicted_score
    if pred is None:
        pred = d.get("min_predicted_score")
    return {
        "top_k": max(1, k),
        "horizon_days": max(1, min(h, 10)),
        "apply_costs": costs,
        "weight_mode": mode,
        "max_position_pct": float(d.get("max_position_pct") or 25.0),
        "max_sector_pct": float(d.get("max_sector_pct") or 40.0),
        "min_predicted_score": pred,
        "yhat_horizon_days": yhat_h,
        "paper_horizon_days": paper_h,
        "paper_max_positions": paper_k,
        "lookback": DAILY_BT_UI_LOOKBACK,
        "rank_mode": "predicted_score",
    }


def resolve_daily_replay_kwargs(
    *,
    lookback: Optional[int] = None,
    apply_costs: Optional[bool] = None,
    fusion_w_co: Optional[float] = None,
    rank_enter: Optional[float] = None,
    rank_strong: Optional[float] = None,
) -> Dict[str, Any]:
    """日报历史回测：对齐 /replay paper_replay（w_co / rank入场 / rank强 / lookback）。"""
    from core.paper.rebalance.rank_lots import (
        clamp_fusion_w_co,
        coerce_rank_threshold,
    )

    costs = True if apply_costs is None else bool(apply_costs)
    try:
        alpha = float(DAILY_BT_UI_FUSION_W_CO if fusion_w_co is None else fusion_w_co)
    except (TypeError, ValueError):
        alpha = float(DAILY_BT_UI_FUSION_W_CO)
    if alpha != alpha:
        alpha = float(DAILY_BT_UI_FUSION_W_CO)
    alpha = max(0.0, min(1.0, float(clamp_fusion_w_co(alpha))))
    enter = coerce_rank_threshold(
        DAILY_BT_UI_RANK_ENTER if rank_enter is None else rank_enter,
        DAILY_BT_UI_RANK_ENTER,
    )
    strong = coerce_rank_threshold(
        DAILY_BT_UI_RANK_STRONG if rank_strong is None else rank_strong,
        DAILY_BT_UI_RANK_STRONG,
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
        "rank_enter": enter,
        "rank_strong": strong,
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
        "rank_enter": replay["rank_enter"],
        "rank_strong": replay["rank_strong"],
        "apply_costs": True,
        "paper_max_positions": int(d.get("max_positions") or d.get("top_k") or 20),
        "paper_horizon_days": int(d.get("horizon_days") or 1),
        "top_k": DAILY_BT_UI_TOP_K,
        "horizon_days": DAILY_BT_UI_HORIZON_DAYS,
        "note": (
            "日报历史回测=paper_replay/rank_lots（对齐 /replay · 09:30 · 入场/强档金额）；"
            "模块可改 w_co / Rank入场 / Rank强 / lookback；不写 signal_config；成本开"
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
    rank_enter: Optional[float] = None,
    rank_strong: Optional[float] = None,
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
        rank_enter=rank_enter,
        rank_strong=rank_strong,
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
        rank_enter=replay["rank_enter"],
        rank_strong=replay["rank_strong"],
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
    params["rank_enter"] = replay["rank_enter"]
    params["rank_strong"] = replay["rank_strong"]
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
        f"（w_co={replay['fusion_w_co']:g}；入场={replay['rank_enter']:g}；"
        f"强={replay['rank_strong']:g}；宇宙={universe_n} 只）"
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
