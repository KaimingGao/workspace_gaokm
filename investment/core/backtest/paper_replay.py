"""纸面可实现回放：日循环驱动 ``simulate_cross_section_rebalance``。

与 ``topk_research``（独立腿聚合）并列：本引擎跟踪真实持仓、T+1、换手预算、
``min_cash_pct`` 与账户风控，成交价默认 next_open（信号日收盘打分 → 次日开盘调仓）。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ENGINE_ID = "paper_replay"


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def mock_quote_from_bar(
    bar: Optional[dict],
    *,
    prev_bar: Optional[dict] = None,
    px_field: str = "open",
) -> dict:
    """日线 → 调仓/盯市用行情 dict（含 prev_close / change_raw，供涨跌停）。"""
    if not isinstance(bar, dict):
        return {}
    try:
        px = float(bar.get(px_field) or bar.get("close") or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            px = 0.0
    prev = None
    if isinstance(prev_bar, dict):
        try:
            prev = float(prev_bar.get("close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    if prev is None:
        try:
            prev = float(bar.get("pre_close") or bar.get("prev_close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    change = 0.0
    if prev and prev > 0 and px > 0:
        change = (px / prev - 1.0) * 100.0
    open_px = None
    try:
        open_px = float(bar.get("open") or 0) or None
    except (TypeError, ValueError):
        open_px = None
    return {
        "success": True,
        "price_raw": px,
        "price": px,
        "open_raw": open_px if open_px is not None else px,
        "open": open_px if open_px is not None else px,
        "prev_close": prev,
        "pre_close": prev,
        "yesterday_close": prev,
        "change_raw": round(change, 4),
        "change_pct": round(change, 4),
    }


def _make_batch_query(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
    *,
    px_field: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    date_i = {d: i for i, d in enumerate(dates)}
    i = date_i.get(str(day))

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for code in codes or []:
            c = str(code or "").strip()
            if not c:
                continue
            dm = date_maps.get(c) or {}
            bar = dm.get(str(day))
            prev_bar = None
            if i is not None and i > 0:
                prev_bar = dm.get(dates[i - 1])
            q = mock_quote_from_bar(bar, prev_bar=prev_bar, px_field=px_field)
            if q:
                q["stock_code"] = c
            out[c] = q
        return out

    return _batch


def _default_paper(
    *,
    initial_cash: float,
    top_k: int,
    min_cash_pct: float,
    max_turnover_pct: Optional[float],
    cost_model: str,
) -> dict:
    rules: Dict[str, Any] = {
        "max_positions": int(top_k),
        "position_pct": min(0.25, 1.0 / max(1, int(top_k))),
        "min_cash_pct": float(min_cash_pct),
        "horizon_days": 1,
        "execution_mode": "next_open",
    }
    if max_turnover_pct is not None:
        rules["max_turnover_pct"] = float(max_turnover_pct)
    # 回放关闭做 T，避免卖腿读盘中分钟线打网
    rules["t0"] = {"enabled": False}
    cash = float(initial_cash)
    return {
        "cash": cash,
        "initial_cash": cash,
        "holdings": [],
        "trades": [],
        "snapshots": [],
        "operation_log": [],
        "strategy_id": "short",
        "cost_model": cost_model,
        "rules": rules,
    }


def _ranking_from_scores(
    entries: List[dict],
    *,
    top_k: int,
    min_predicted_score: Optional[float],
) -> List[dict]:
    """把打分行排成调仓 ranking（ŷ_EOD 优先；无则 heuristic）。"""
    rows: List[dict] = []
    for it in entries or []:
        if not isinstance(it, dict) or not it.get("stock_code"):
            continue
        row = dict(it)
        eod = row.get("predicted_score_eod")
        if eod is None:
            eod = row.get("predicted_score")
        if eod is None:
            eod = row.get("score")
        try:
            eod_f = float(eod) if eod is not None else None
        except (TypeError, ValueError):
            eod_f = None
        if eod_f is not None:
            row["predicted_score"] = eod_f
            row.setdefault("predicted_score_eod", eod_f)
            row.setdefault("predicted_score_blend", eod_f)
            row.setdefault("score_scale", "predicted_yhat")
        if min_predicted_score is not None and eod_f is not None:
            if eod_f < float(min_predicted_score):
                continue
        rows.append(row)

    def _key(r: dict) -> float:
        for k in ("predicted_score_eod", "predicted_score", "score"):
            v = r.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return float("-inf")

    rows.sort(key=_key, reverse=True)
    return rows[: max(1, int(top_k))]


def _score_day(
    *,
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    signal_i: int,
    max_window: int,
    horizon_days: int,
    cfg: Optional[dict],
) -> List[dict]:
    from core.backtest.engine import _mock_quote_from_bars
    from core.signal.cross_section_batch import score_window_as_item

    entries: List[dict] = []
    for code in stock_bars:
        window = _window_for_code(code, dates, date_maps, signal_i, max_window)
        if len(window) < 2:
            continue
        quote = _mock_quote_from_bars(window, len(window) - 1)
        item = score_window_as_item(
            code,
            window,
            horizon_days=horizon_days,
            quote=quote,
            config=cfg,
        )
        if item:
            entries.append(item)
    return entries


def backtest_paper_replay(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int = 5,
    min_history: int = 12,
    max_window: int = 30,
    initial_cash: float = 1_000_000.0,
    min_cash_pct: float = 0.2,
    max_turnover_pct: Optional[float] = 40.0,
    cost_model: str = "simple_cn",
    min_predicted_score: Optional[float] = None,
    min_score: Optional[float] = None,
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    skip_sentiment_prior: bool = True,
    skip_market_prior: bool = True,
    yhat_horizon_days: int = 1,
    stop_loss_pnl: float = -8.0,
    force_trim_cooldown_days: int = 1,
    progress_cb: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """纸面日频回放：信号日收盘打分 → 次日开盘调仓 →（可选）盘中止损 → 收盘盯市。

    ``rankings_by_date``：可选预计算目标簿（测试/对照）；缺省则按日线 PIT 打分。
    ``stop_loss_pnl``：日线 low 触及成本该百分比则卖（默认 -8；≥0 关闭）。
    ``force_trim_cooldown_days``：膨胀减仓卸簿内后 N 日不买回（0=关）。
    """
    from core.backtest.engine import _trade_metrics
    from core.paper.ledger import mark_to_market
    from core.paper.rebalance import simulate_cross_section_rebalance
    from core.paper.replay_ctx import paper_replay_context

    if not stock_bars or len(stock_bars) < 1:
        return {"success": False, "error": "stock_bars 为空", "params": {"engine": ENGINE_ID}}

    date_maps = {str(c): _bars_by_date(bars) for c, bars in stock_bars.items()}
    dates = _common_dates(stock_bars)
    n = len(dates)
    need = max(2, int(min_history) + 2)
    if n < need:
        return {
            "success": False,
            "error": f"共同交易日不足 {n}<{need}",
            "params": {"engine": ENGINE_ID, "common_dates": n},
        }

    top_k = max(1, min(int(top_k or 1), 30))
    paper = _default_paper(
        initial_cash=initial_cash,
        top_k=top_k,
        min_cash_pct=min_cash_pct,
        max_turnover_pct=max_turnover_pct,
        cost_model=cost_model,
    )
    cfg = None
    try:
        from core.signal.config import load_signal_config

        cfg = load_signal_config()
    except Exception:  # noqa: BLE001 — 打分缺省配置
        logger.debug("load_signal_config failed in paper_replay", exc_info=True)

    equity_curve: List[dict] = []
    day_returns: List[float] = []
    rebalance_logs: List[dict] = []
    constraints: Dict[str, int] = {
        "t1_blocks": 0,
        "limit_skips": 0,
        "turnover_clips": 0,
        "cash_reserve_hits": 0,
        "rebalance_days": 0,
        "stop_exits": 0,
        "trim_cooldown_blocks": 0,
    }
    paper["_force_trim_cooldown"] = {}
    prev_equity = float(initial_cash)
    equity_curve.append({"date": dates[min_history - 1], "equity": prev_equity, "return_pct": 0.0})

    # 信号日 i → 执行日 i+1；最后一日仅盯市不新开
    first_i = max(0, int(min_history) - 1)
    last_signal_i = n - 2
    for signal_i in range(first_i, last_signal_i + 1):
        signal_date = dates[signal_i]
        exec_date = dates[signal_i + 1]
        if progress_cb is not None:
            try:
                progress_cb(
                    f"{signal_date}→{exec_date}",
                    signal_i - first_i + 1,
                    max(1, last_signal_i - first_i + 1),
                )
            except Exception:  # noqa: BLE001 — 进度回调不阻塞
                logger.debug("paper_replay progress_cb failed", exc_info=True)

        if rankings_by_date is not None:
            ranking = list(rankings_by_date.get(signal_date) or [])[:top_k]
        else:
            entries = _score_day(
                stock_bars=stock_bars,
                dates=dates,
                date_maps=date_maps,
                signal_i=signal_i,
                max_window=max_window,
                horizon_days=max(1, int(yhat_horizon_days or 1)),
                cfg=cfg,
            )
            ranking = _ranking_from_scores(
                entries, top_k=top_k, min_predicted_score=min_predicted_score
            )
        before_cool = sum(
            1 for it in ranking if it.get("hard_reject")
        )
        ranking = _apply_force_trim_cooldown_to_ranking(
            ranking, paper, exec_date=exec_date
        )
        after_cool = sum(1 for it in ranking if it.get("hard_reject"))
        if after_cool > before_cool:
            constraints["trim_cooldown_blocks"] += after_cool - before_cool

        open_q = _make_batch_query(date_maps, dates, exec_date, px_field="open")
        with paper_replay_context(as_of=exec_date, batch_query=open_q):
            try:
                result = simulate_cross_section_rebalance(
                    paper,
                    ranking,
                    top_k=top_k,
                    min_score=min_score,
                    respect_max_positions=True,
                    skip_sentiment_prior=skip_sentiment_prior,
                    skip_market_prior=skip_market_prior,
                )
            except Exception as exc:  # noqa: BLE001 — 单日失败记日志继续
                logger.exception("paper_replay rebalance failed on %s", exec_date)
                rebalance_logs.append(
                    {
                        "signal_date": signal_date,
                        "exec_date": exec_date,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                result = {"success": False, "error": str(exc)}

        if result.get("success"):
            constraints["rebalance_days"] += 1
            for skip in result.get("sell_match_skips") or []:
                reason = str(skip.get("reason") or "")
                if "T+1" in reason:
                    constraints["t1_blocks"] += 1
                elif "跌停" in reason or "停牌" in reason:
                    constraints["limit_skips"] += 1
            if result.get("turnover_capped") or result.get("cash_impact", {}).get(
                "turnover_clipped"
            ):
                constraints["turnover_clips"] += 1
            ci = result.get("cash_impact") or {}
            if ci.get("min_cash_pct") is not None:
                constraints["cash_reserve_hits"] += 1
            _stamp_force_trim_cooldown(
                paper,
                result.get("sell_trades") or [],
                exec_date=exec_date,
                dates=dates,
                cooldown_days=force_trim_cooldown_days,
            )
            rebalance_logs.append(
                {
                    "signal_date": signal_date,
                    "exec_date": exec_date,
                    "ok": True,
                    "sell_count": len(result.get("sell_trades") or []),
                    "buy_count": len(result.get("buy_trades") or []),
                    "held": [
                        str(h.get("stock_code"))
                        for h in (paper.get("holdings") or [])
                        if h.get("stock_code")
                    ],
                    "turnover_pct": (result.get("cash_impact") or {}).get("turnover_pct"),
                }
            )

        # 开盘调仓后、收盘盯市前：日线 low 止损近似（需回放时钟 / T+1）
        with paper_replay_context(as_of=exec_date, batch_query=open_q):
            stop_trades = _apply_intraday_stops(
                paper,
                date_maps=date_maps,
                exec_date=exec_date,
                stop_loss_pnl=stop_loss_pnl,
            )
        if stop_trades:
            constraints["stop_exits"] += len(stop_trades)

        close_q = _make_batch_query(date_maps, dates, exec_date, px_field="close")
        with paper_replay_context(as_of=exec_date, batch_query=close_q):
            summary = mark_to_market(paper)
        equity = float(summary.get("equity") or paper.get("cash") or 0)
        ret = (equity / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        day_returns.append(ret)
        equity_curve.append(
            {
                "date": exec_date,
                "equity": round(equity, 2),
                "return_pct": round(ret, 4),
                "cash": round(float(paper.get("cash") or 0), 2),
                "n_holdings": len(paper.get("holdings") or []),
            }
        )
        prev_equity = equity

    metrics = _trade_metrics(day_returns, holding_days=1)
    # 日收益段数 ≠ 成交笔数；补纸面成交笔数
    metrics = dict(metrics)
    metrics["trade_count"] = len(paper.get("trades") or [])
    metrics["rebalance_days"] = constraints["rebalance_days"]
    final_eq = float(equity_curve[-1]["equity"]) if equity_curve else float(initial_cash)
    total_ret = (final_eq / float(initial_cash) - 1.0) * 100.0 if initial_cash else 0.0
    metrics["total_return_pct"] = round(total_ret, 2)

    note = (
        f"引擎={ENGINE_ID}：信号日收盘打分→次日开盘调仓→收盘盯市；"
        f"含 T+1 / min_cash_pct={min_cash_pct:.0%} / 换手预算"
        + (f"≤{max_turnover_pct:g}%" if max_turnover_pct is not None else "关")
        + f"；止损={stop_loss_pnl:g}%"
        + (
            f"；trim冷却={force_trim_cooldown_days}日"
            if force_trim_cooldown_days
            else "；trim冷却关"
        )
        + f"；成本={cost_model}；≠ topk_research 独立腿聚合。"
    )
    return {
        "success": True,
        "strategy": ENGINE_ID,
        "params": {
            "engine": ENGINE_ID,
            "top_k": top_k,
            "min_history": min_history,
            "max_window": max_window,
            "initial_cash": initial_cash,
            "min_cash_pct": min_cash_pct,
            "max_turnover_pct": max_turnover_pct,
            "cost_model": cost_model,
            "execution_mode": "next_open",
            "stock_count": len(stock_bars),
            "common_dates": n,
            "yhat_horizon_days": yhat_horizon_days,
            "rankings_injected": rankings_by_date is not None,
            "skip_sentiment_prior": skip_sentiment_prior,
            "skip_market_prior": skip_market_prior,
            "stop_loss_pnl": stop_loss_pnl,
            "force_trim_cooldown_days": int(force_trim_cooldown_days or 0),
        },
        "metrics": metrics,
        "equity_curve": equity_curve,
        "constraints_hit": constraints,
        "rebalance_logs": rebalance_logs[-40:],
        "trades": list(paper.get("trades") or [])[-80:],
        "holdings_end": copy.deepcopy(paper.get("holdings") or []),
        "cash_end": round(float(paper.get("cash") or 0), 2),
        "paper": paper,
        "note": note,
    }


def _apply_intraday_stops(
    paper: dict,
    *,
    date_maps: Dict[str, Dict[str, dict]],
    exec_date: str,
    stop_loss_pnl: float,
) -> List[dict]:
    """日线近似盘中止损：当日 low 触及成本×(1+stop%) 则按 min(open, stop_px) 卖。

    ``stop_loss_pnl`` 为百分比（如 -8 表示跌 8%）。≤0 或 ≥0 视为关闭。
    """
    try:
        sp = float(stop_loss_pnl)
    except (TypeError, ValueError):
        return []
    if sp >= 0 or sp <= -99:
        return []
    from core.paper.costs import (
        annotate_trade,
        apply_fill_price,
        calc_trade_fees,
        cost_params,
        resolve_cost_model,
    )
    from core.paper.ledger import ORIGIN_STRATEGY, _now_iso
    from core.paper.tplus1 import clip_sell_shares, consume_sell_lots

    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    cash = float(paper.get("cash") or 0)
    kept: List[dict] = []
    trades: List[dict] = []
    floor_mult = 1.0 + sp / 100.0
    for h in list(paper.get("holdings") or []):
        code = str(h.get("stock_code") or "").strip()
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        if not code or shares <= 0 or cost <= 0:
            kept.append(h)
            continue
        bar = (date_maps.get(code) or {}).get(exec_date) or {}
        try:
            low = float(bar.get("low") or 0)
            open_px = float(bar.get("open") or 0)
        except (TypeError, ValueError):
            low, open_px = 0.0, 0.0
        floor_px = cost * floor_mult
        if low <= 0 or low > floor_px:
            kept.append(h)
            continue
        sell_px = floor_px
        if open_px > 0:
            sell_px = min(open_px, floor_px)
        sell_shares, t1_meta = clip_sell_shares(h, shares)
        if sell_shares <= 1e-9:
            kept.append(h)
            continue
        fill_px = apply_fill_price(
            "sell", float(sell_px), model=cost_model, params=fee_params
        )
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=cost_model, params=fee_params)
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": round((fill_px / cost - 1.0) * 100.0, 2),
                "origin": ORIGIN_STRATEGY,
                "note": f"纸面回放盘中止损近似（low≤{sp:g}% · {exec_date}）",
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        trades.append(trade)
        cash = round(cash + float(fee_info["net_cash_delta"]), 2)
        consume_sell_lots(h, sell_shares)
        if float(h.get("shares") or 0) > 1e-6:
            kept.append(h)
    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)
    return trades


def _stamp_force_trim_cooldown(
    paper: dict,
    sell_trades: Sequence[dict],
    *,
    exec_date: str,
    dates: Sequence[str],
    cooldown_days: int,
) -> None:
    """膨胀减仓卸簿内票 → 写入 N 日买回冷却。"""
    n = max(0, int(cooldown_days or 0))
    if n <= 0:
        return
    date_i = {str(d): i for i, d in enumerate(dates)}
    i0 = date_i.get(str(exec_date))
    if i0 is None:
        return
    until_i = min(len(dates) - 1, i0 + n)
    until = str(dates[until_i])
    cool = dict(paper.get("_force_trim_cooldown") or {})
    for t in sell_trades or []:
        note = str(t.get("note") or "")
        if "膨胀" not in note or "簿内" not in note:
            continue
        code = str(t.get("stock_code") or "").strip()
        if code:
            cool[code] = until
    paper["_force_trim_cooldown"] = cool


def _apply_force_trim_cooldown_to_ranking(
    ranking: List[dict],
    paper: dict,
    *,
    exec_date: str,
) -> List[dict]:
    cool = paper.get("_force_trim_cooldown") or {}
    if not cool:
        return ranking
    out: List[dict] = []
    for it in ranking or []:
        row = dict(it)
        code = str(row.get("stock_code") or "").strip()
        until = str(cool.get(code) or "")
        if code and until and exec_date <= until:
            row["hard_reject"] = True
            row["reject_reason"] = f"force_trim_cooldown≤{until}"
        out.append(row)
    # 过期清理
    paper["_force_trim_cooldown"] = {
        c: u for c, u in cool.items() if str(u or "") >= exec_date
    }
    return out


def rankings_from_topk_precomputed(
    precomputed_ranks: Dict[str, Any],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """把 Top-K ``precomputed_ranks`` 转成纸面调仓 ranking 行。"""
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for day, pack in (precomputed_ranks or {}).items():
        if not isinstance(pack, dict):
            continue
        picks = list(pack.get("picks") or [])[:top_k]
        items_by = pack.get("items_by_code") or {}
        rows: List[dict] = []
        for code, score in picks:
            c = str(code or "").strip()
            if not c:
                continue
            base = dict(items_by.get(c) or {})
            base["stock_code"] = c
            try:
                sc = float(score)
            except (TypeError, ValueError):
                sc = None
            if sc is not None:
                base.setdefault("predicted_score", sc)
                base.setdefault("predicted_score_eod", sc)
                base.setdefault("predicted_score_blend", sc)
                base.setdefault("score", sc)
            base.setdefault("score_scale", "predicted_yhat")
            base.setdefault("dual_score_window", "eod_next")
            base["rank_source"] = "topk_precomputed"
            rows.append(base)
        out[str(day)] = rows
    return out


def build_momentum_rankings(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """用昨收到今收涨跌幅做轻量目标簿（日报离线摘要用；≠生产 ŷ）。"""
    date_maps = {str(c): _bars_by_date(bars) for c, bars in (stock_bars or {}).items()}
    dates = _common_dates(stock_bars)
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for i, day in enumerate(dates):
        if i < 1:
            continue
        rows: List[dict] = []
        prev_d = dates[i - 1]
        for code, dm in date_maps.items():
            bar = dm.get(day)
            prev = dm.get(prev_d)
            if not bar or not prev:
                continue
            try:
                c0 = float(prev.get("close") or 0)
                c1 = float(bar.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if c0 <= 0 or c1 <= 0:
                continue
            ret = (c1 / c0 - 1.0) * 100.0
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": code,
                    "score": ret,
                    "predicted_score": ret,
                    "predicted_score_eod": ret,
                    "predicted_score_tau": ret,
                    "score_scale": "predicted_yhat",
                    "dual_score_window": "eod_next",
                    "rank_source": "momentum_close",
                }
            )
        rows.sort(key=lambda r: float(r.get("predicted_score") or 0), reverse=True)
        out[day] = rows[:top_k]
    return out


def summarize_paper_replay_for_daily(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    min_cash_pct: float = 0.2,
    max_turnover_pct: Optional[float] = 40.0,
    min_predicted_score: Optional[float] = None,
    yhat_horizon_days: int = 1,
    lookback: Optional[int] = None,
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    rank_source: Optional[str] = None,
    stop_loss_pnl: float = -8.0,
    force_trim_cooldown_days: int = 1,
) -> Dict[str, Any]:
    """日报用轻量摘要（与 ``summarize_portfolio_backtest`` 字段对齐子集）。

    优先用注入的 Top-K ŷ ranking；否则昨收→今收动量近似。
    """
    del min_predicted_score, yhat_horizon_days
    src = rank_source
    if rankings_by_date is None:
        rankings = build_momentum_rankings(stock_bars, top_k=top_k)
        src = src or "momentum_close"
    else:
        rankings = rankings_by_date
        src = src or "topk_precomputed"
    bt = backtest_paper_replay(
        stock_bars,
        top_k=top_k,
        min_cash_pct=min_cash_pct,
        max_turnover_pct=max_turnover_pct,
        rankings_by_date=rankings,
        min_score=-1e9,
        cost_model="simple_cn",
        skip_sentiment_prior=True,
        skip_market_prior=True,
        stop_loss_pnl=stop_loss_pnl,
        force_trim_cooldown_days=force_trim_cooldown_days,
    )
    if not bt.get("success"):
        return bt
    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    params = dict(bt.get("params") or {})
    params["rank_source"] = src
    if lookback is not None:
        params["lookback"] = int(lookback)
    note = str(bt.get("note") or "")
    if src == "topk_precomputed":
        note += " · 日报目标簿=与 Top-K 同序列 ŷ（precomputed_ranks）"
    else:
        note += " · 日报目标簿=昨收涨跌幅动量近似（≠ ŷ_EOD）"
    return {
        "success": True,
        "engine": ENGINE_ID,
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
        "equity_curve_tail": curve[-12:],
        "constraints_hit": bt.get("constraints_hit"),
        "metrics": metrics,
        "note": note,
    }
