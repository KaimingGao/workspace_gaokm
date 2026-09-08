"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.research.portfolio_bars import (
    DAILY_PORTFOLIO_MAX_NAMES,
    load_portfolio_stock_bars,
    should_fetch_backtest_fundamentals,
)

# `/quant` 生成日报表单默认（≠ 纸面 max_positions；cron/CLI 不传仍走 resolve 纸面对齐）
DAILY_BT_UI_TOP_K = 3
DAILY_BT_UI_HORIZON_DAYS = 1
DAILY_BT_UI_LOOKBACK = 30

__all__ = [
    "DAILY_BT_UI_TOP_K",
    "DAILY_BT_UI_HORIZON_DAYS",
    "DAILY_BT_UI_LOOKBACK",
    "should_fetch_backtest_fundamentals",
    "load_portfolio_stock_bars",
    "resolve_daily_topk_backtest_kwargs",
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


def daily_bt_option_defaults() -> Dict[str, Any]:
    """`/quant` 生成日报表单默认（K=3 / h=1）；纸面值在 paper_*，供「跟纸面」标签。"""
    d = resolve_daily_topk_backtest_kwargs()
    return {
        "top_k": DAILY_BT_UI_TOP_K,
        "horizon_days": DAILY_BT_UI_HORIZON_DAYS,
        "paper_max_positions": d["paper_max_positions"],
        "paper_horizon_days": d["paper_horizon_days"],
        "yhat_horizon_days": d["yhat_horizon_days"],
        "min_predicted_score": d["min_predicted_score"],
        "lookback": DAILY_BT_UI_LOOKBACK,
        "apply_costs": True,
        "note": (
            "表单默认 K=3 · 持有 1 日 · lookback 30；跟纸面用 paper_*；"
            "cron/CLI 不传则仍跟纸面；ŷ 标签窗口仍是 scoring.horizon_days；不写 signal_config；"
            "h=1 日频换仓为研究探路口径，≠纸面可实现（见 paper_replay）"
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
) -> Dict[str, Any]:
    """每日报告用的轻量 TopK 回测摘要（ŷ 排序；规则分 min_score 不再默认 55）。"""
    from core.backtest.topk_backtest import backtest_topk_equal_weight
    from core.watching.store import read_watching

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
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        candidates,
        lookback=lookback,
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

    resolved = resolve_daily_topk_backtest_kwargs(
        top_k=top_k,
        horizon_days=horizon_days,
        min_predicted_score=min_predicted_score,
        apply_costs=apply_costs,
    )
    sector_sync = _watching_sector_overlay()
    bt_kwargs: Dict[str, Any] = {
        "top_k": resolved["top_k"],
        "horizon_days": resolved["horizon_days"],
        "rank_mode": "predicted_score",
        "min_predicted_score": resolved["min_predicted_score"],
        "apply_costs": resolved["apply_costs"],
        "weight_mode": resolved["weight_mode"],
        "max_position_pct": resolved["max_position_pct"],
        "max_sector_pct": resolved["max_sector_pct"],
    }
    if isinstance(sector_sync.get("mapping"), dict) and sector_sync["mapping"]:
        bt_kwargs["sector_map"] = sector_sync["mapping"]
    # 仅显式传入时才带规则分门槛，避免日报 params 残留 min_score=55
    if min_score is not None:
        bt_kwargs["min_score"] = float(min_score)

    precomputed_ranks: Dict[str, Any] = {}
    bt = backtest_topk_equal_weight(
        stock_bars,
        **bt_kwargs,
        # 日报只有日线、无可靠分钟 τ；开 ŷ_τ 闸会把调仓打成 0 笔
        apply_tau_buy_gate=False,
        precomputed_ranks=precomputed_ranks,
    )
    if not bt.get("success"):
        return bt

    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    attr = bt.get("attribution") or {}
    oos = bt.get("oos_summary") or {}
    regime = bt.get("regime_summary") or {}
    rb = bt.get("regime_buckets") or {}
    pit = bt.get("pit_report") or {}
    params = dict(bt.get("params") or {})
    if params.get("rank_mode") == "predicted_score":
        # 规则分 0–100 门槛不适用；保留 min_predicted_score
        params["min_score"] = None
    params["apply_tau_buy_gate"] = False
    params["rank_key"] = "predicted_score_eod"
    params["yhat_horizon_days"] = resolved["yhat_horizon_days"]
    params["paper_horizon_days"] = resolved["paper_horizon_days"]
    params["paper_max_positions"] = resolved.get("paper_max_positions")
    params["lookback"] = int(lookback)
    note_bits = []
    if n_all > DAILY_PORTFOLIO_MAX_NAMES:
        note_bits.append(
            f"日报轻量回测截断观察池 {n_all}→{len(stock_bars)}（缓存优先，上限 {DAILY_PORTFOLIO_MAX_NAMES}），"
            "基本面仅本地缓存（避免串行远端挂死）"
        )
    note_bits.append("选股键=ŷ_EOD · 关 τ 闸（日线无可靠分钟 τ；≠ live ŷ_trade）")
    note_bits.append("引擎=topk_research（独立腿聚合；≠纸面可实现）")
    paper_k = resolved.get("paper_max_positions")
    if paper_k is not None and int(paper_k) != int(resolved["top_k"]):
        k_note = f"K={resolved['top_k']}（≠纸面 max_positions={paper_k}）"
    else:
        k_note = f"K={resolved['top_k']}（纸面 max_positions）"
    paper_h = resolved.get("paper_horizon_days")
    if paper_h is not None and int(paper_h) != int(resolved["horizon_days"]):
        h_note = f"h={resolved['horizon_days']}（≠纸面持有 {paper_h}）"
    else:
        h_note = f"h={resolved['horizon_days']}（纸面持有）"
    note_bits.append(
        f"{k_note}· {h_note}"
        f"· 成本={'开' if resolved['apply_costs'] else '关'} · 权重含现金"
        f"· 止损={params.get('stop_pct') or '关'}"
        + (
            f"（已进主路径 · 触及 {params.get('stop_legs_clipped') or 0} 腿）"
            if params.get("stop_applied")
            else ""
        )
    )
    if resolved["yhat_horizon_days"] != resolved["paper_horizon_days"]:
        note_bits.append(
            f"ŷ 标签仍为 {resolved['yhat_horizon_days']} 日；未改 scoring.horizon_days"
        )
    cov = params.get("sector_coverage") or {}
    if cov.get("coverage") is not None:
        note_bits.append(f"行业映射 {cov.get('mapped')}/{cov.get('total')}")
    note_bits.append(
        "ŷ残差/超额标签对照未自动打开（研究枢纽影子 API）"
    )
    note = " · ".join(note_bits)
    params["engine"] = "topk_research"

    paper_replay_summary: Optional[Dict[str, Any]] = None
    try:
        from core.backtest.paper_replay import (
            rankings_from_topk_precomputed,
            summarize_paper_replay_for_daily,
        )

        yhat_ranks = rankings_from_topk_precomputed(
            precomputed_ranks, top_k=int(resolved["top_k"])
        )
        paper_replay_summary = summarize_paper_replay_for_daily(
            stock_bars,
            top_k=int(resolved["top_k"]),
            lookback=int(lookback),
            rankings_by_date=yhat_ranks if yhat_ranks else None,
            rank_source="topk_precomputed" if yhat_ranks else "momentum_close",
        )
    except Exception as exc:  # noqa: BLE001 — 纸面回放失败不挡研究摘要
        logger.warning("paper_replay daily summary failed: %s", exc, exc_info=True)
        paper_replay_summary = {
            "success": False,
            "engine": "paper_replay",
            "error": str(exc),
            "note": "纸面回放不可用；研究 Top-K 摘要仍有效",
        }

    return {
        "success": True,
        "engine": "topk_research",
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
        "equity_curve_tail": curve[-12:],
        "failures": failures,
        "note": note,
        # R4.4：日报导出可读诊断块（与页内块对齐的子集）
        "metrics": metrics,
        "attribution": {
            "ok": attr.get("ok"),
            "selection_excess_pct": attr.get("selection_excess_pct"),
            "brinson": attr.get("brinson"),
            "factor_proxy": attr.get("factor_proxy"),
            "by_sector": (attr.get("by_sector") or [])[:6],
        }
        if attr
        else None,
        "oos_summary": oos,
        "regime_summary": regime,
        "regime_buckets": rb,
        "pit_report": pit,
        "stop_shadow": bt.get("stop_shadow"),
        "signal_fill_sample": (bt.get("signal_fill_sample") or [])[-12:],
        "cost_model": bt.get("cost_model"),
        "paper_replay": paper_replay_summary,
        "research_next": [
            "人审 ŷ 残差对照 POST /api/quant/yhat-residual/shadow",
            "人审 超额标签对照 POST /api/quant/excess-mode/shadow",
            "过门后再开 cross_section.yhat_residual / y_spec.excess_mode=index",
            "可交易验证看 paper_replay（同摘要内嵌）",
        ],
        "sector_sync": {
            "ok": sector_sync.get("ok"),
            "coverage": sector_sync.get("coverage"),
            "added_count": sector_sync.get("added_count"),
            "written": False,
        },
    }
