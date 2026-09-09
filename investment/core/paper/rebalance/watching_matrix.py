"""观察池实时算分 + rank_lots 预演/落账。

手动路径：dry_run 预演 → 确认后同算法落账（改 paper 持仓/现金）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _ymd(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().replace("Z", "")
    d = s[:10]
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return None


def _open_date_of(
    holding: Optional[dict],
    fallback: Optional[str] = None,
) -> Optional[str]:
    """持仓最早开日（lots.bought_date 优先，避免 ts=墙钟把开日写成今天）。"""
    dates: List[str] = []
    if isinstance(holding, dict):
        try:
            from core.paper.tplus1 import ensure_lots

            ensure_lots(holding)
        except Exception:  # noqa: BLE001
            logger.debug("ensure_lots failed in _open_date_of", exc_info=True)
        lots = holding.get("lots") if isinstance(holding.get("lots"), list) else []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            d = _ymd(lot.get("bought_date")) or _ymd(lot.get("bought_at"))
            if d:
                dates.append(d)
        if not dates:
            d = _ymd(holding.get("bought_date")) or _ymd(holding.get("bought_at"))
            if d:
                dates.append(d)
    if dates:
        return min(dates)
    return _ymd(fallback)


def _watching_codes(*, include_held: Sequence[str] = ()) -> Tuple[List[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"source": "watching", "n_watch": 0, "n_held_extra": 0}
    name_by_code: Dict[str, str] = {}
    try:
        from core.watching.store import read_watching, refresh_watchlist, watchlist_names_for

        uni = read_watching()
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        if not codes:
            refreshed = refresh_watchlist(uni)
            codes = [str(c).strip() for c in (refreshed.get("watchlist") or []) if str(c).strip()]
            uni = refreshed
        meta["n_watch"] = len(codes)
        meta["watching_name"] = uni.get("name")
        try:
            names = watchlist_names_for(uni)
            for c, n in zip(codes, names):
                nn = str(n or "").strip()
                if c and nn and nn != c:
                    name_by_code[c] = nn
        except Exception:  # noqa: BLE001
            logger.debug("watchlist names map skipped", exc_info=True)
    except FileNotFoundError:
        return [], {"error": "watching.json 不存在", "source": "watching"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("read watching failed: %s", exc, exc_info=True)
        return [], {"error": str(exc), "source": "watching"}

    seen = set(codes)
    extra = 0
    for raw in include_held or []:
        c = str(raw or "").strip()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)
            extra += 1
    meta["n_held_extra"] = extra
    meta["n_total"] = len(codes)
    meta["name_by_code"] = name_by_code
    return codes, meta


def _resolve_report_name(
    code: str,
    *,
    item: Optional[dict] = None,
    paper: Optional[dict] = None,
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    """报告行股票名：signal → 持仓 → 观察池 → a_code_name。"""
    c = str(code or "").strip()
    fallback = ""
    if isinstance(item, dict):
        fallback = str(item.get("stock_name") or "").strip()
    if paper and isinstance(paper.get("holdings"), list):
        for h in paper["holdings"]:
            if not isinstance(h, dict):
                continue
            if str(h.get("stock_code") or "").strip() != c:
                continue
            hn = str(h.get("stock_name") or "").strip()
            if hn and hn != c:
                return hn
            break
    try:
        from core.t0.intraday import resolve_stock_name

        return resolve_stock_name(
            c, fallback=fallback, name_by_code=name_by_code or {}
        ) or fallback or c
    except Exception:  # noqa: BLE001
        logger.debug("resolve_stock_name failed for %s", c, exc_info=True)
        if fallback and fallback != c:
            return fallback
        mapped = (name_by_code or {}).get(c) or ""
        return mapped or fallback or c


def _score_pool(
    codes: Sequence[str],
    *,
    horizon_days: int = 1,
    offline_only: bool = True,
) -> Tuple[List[dict], List[dict]]:
    """观察池并行 score_stock。默认仅本地缓存；``offline_only=False`` 可补远端。"""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.signal.score_stock import score_stock

    scored: List[dict] = []
    rejected: List[dict] = []
    pool = [str(c or "").strip() for c in list(codes)[:120] if str(c or "").strip()]
    if not pool:
        return scored, rejected

    use_offline = bool(offline_only)
    t0 = time.perf_counter()
    try:
        from core.signal.live_features import fetch_live_index_bars

        fetch_live_index_bars(market="CN", limit=75, offline_only=use_offline)
    except Exception:  # noqa: BLE001
        logger.debug("warm index bars failed", exc_info=True)

    def _score_one(code: str) -> Tuple[str, Any]:
        try:
            result = score_stock(
                code,
                horizon_days=max(1, int(horizon_days or 1)),
                skip_fundamentals=True,
                skip_sentiment=True,
                offline_only=use_offline,
                quote_timeout=5.0 if use_offline else 8.0,
            )
        except Exception as exc:  # noqa: BLE001
            return code, {"success": False, "stock_code": code, "error": str(exc)[:120]}
        return code, result

    workers = max(1, min(8, len(pool)))
    by_code: Dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_score_one, c): c for c in pool}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                _, result = fut.result()
                by_code[code] = result
            except Exception as exc:  # noqa: BLE001
                logger.debug("score future failed for %s", code, exc_info=True)
                by_code[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": str(exc)[:120],
                }

    for code in pool:
        result = by_code.get(code)
        if not isinstance(result, dict) or result.get("success") is False:
            rejected.append(
                {
                    "stock_code": code,
                    "reason": (result or {}).get("error") or "score_stock 失败",
                }
            )
            continue
        item = result.get("signal_item") if isinstance(result.get("signal_item"), dict) else result
        if not isinstance(item, dict):
            rejected.append({"stock_code": code, "reason": "无 signal_item"})
            continue
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code") or code,
                    "stock_name": item.get("stock_name"),
                    "reason": item.get("reject_reason") or "hard_reject",
                }
            )
            continue
        try:
            from core.signal.dual_score import align_trade_score_fields

            align_trade_score_fields(item, write_score=False, refresh_window=False)
        except Exception:  # noqa: BLE001
            logger.debug("align_trade_score_fields failed", exc_info=True)
        if item.get("stock_code") is None:
            item["stock_code"] = code
        scored.append(item)

    logger.info(
        "watching_matrix score_pool n=%s scored=%s rejected=%s workers=%s elapsed=%.1fs offline_only=%s",
        len(pool),
        len(scored),
        len(rejected),
        workers,
        time.perf_counter() - t0,
        int(use_offline),
    )
    return scored, rejected


def _quote_px(code: str, *, offline_only: bool = True) -> Optional[float]:
    """预演/落账定价：默认本地日线末收；``offline_only=False`` 可打实时行情。"""
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            code,
            limit=5,
            offline_only=bool(offline_only),
            reject_quote_fallback=True,
        ) or {}
        bars = list(pack.get("bars") or [])
        if bars:
            px = _f((bars[-1] or {}).get("close"))
            if px is not None and px > 0:
                return float(px)
    except Exception:  # noqa: BLE001
        logger.debug("cached bar price failed for %s", code, exc_info=True)
    if not offline_only:
        try:
            from core.data.facade import get_quote

            q = get_quote(code) or {}
            px = _f(q.get("price_raw") if isinstance(q, dict) else None)
            if px is None and isinstance(q, dict):
                px = _f(q.get("price"))
            if px is not None and px > 0:
                return float(px)
        except Exception:  # noqa: BLE001
            logger.debug("live quote price failed for %s", code, exc_info=True)
    return None


_SCORE_PASSTHROUGH_KEYS = (
    "predicted_score",
    "predicted_score_eod",
    "predicted_score_eod_rem",
    "predicted_score_tau",
    "predicted_score_rem",
    "score_rem",
    "predicted_score_blend",
    "predicted_score_path",
    "predicted_score_nowcast",
    "predicted_score_on",
    "decision_score",
    "y_trade",
    "y_path",
    "y_nowcast",
    "y_nc",
    "y_on",
    "y_tau",
    "y_fuse",
    "ranking_score",
    "dual_score_window",
    "dual_score_head",
    "dual_score_weights",
    "nowcast_K",
    "nowcast_vs",
    "predicted_score_blend_vs",
    "gap_pct",
    "open_gap_pct",
    "change_pct",
    "prev_close",
    "last_price",
    "hard_reject",
    "reject_reason",
    "sector",
)


def _score_fields_for_report(item: dict, scores: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """透传 signal_item 打分字段，供前端 resolve*Score 解析表列。"""
    out: Dict[str, Any] = {}
    if isinstance(item, dict):
        for k in _SCORE_PASSTHROUGH_KEYS:
            if k in item and item.get(k) is not None:
                out[k] = item.get(k)
    # 分项 ŷ 覆盖同名键；ŷ_EOD / ŷ_τ 仍走 item 透传
    yt = scores.get("y_trade")
    yp = scores.get("y_path")
    yn = scores.get("y_nowcast")
    yo = scores.get("y_on")
    if yt is not None:
        out["y_trade"] = yt
        out["predicted_score_blend"] = yt
        out["decision_score"] = yt
        out["score"] = yt
    if yp is not None:
        out["y_path"] = yp
        out.setdefault("predicted_score_path", yp)
    if yn is not None:
        out["y_nowcast"] = yn
        out["y_nc"] = yn
        out.setdefault("predicted_score_nowcast", yn)
    if yo is not None:
        out["y_on"] = yo
        out.setdefault("predicted_score_on", yo)
    ytau = scores.get("y_tau")
    if ytau is not None:
        out["y_tau"] = ytau
        out.setdefault("predicted_score_tau", ytau)
    return out


def _row_score_payload(item: Optional[dict], src: Optional[dict] = None) -> Dict[str, Any]:
    """表列用分项 ŷ，不把 y_fuse 塞进 y_trade。"""
    from core.paper.rebalance.rank_lots import y_on_of, y_tau_of
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item

    item = item if isinstance(item, dict) else {}
    src = src if isinstance(src, dict) else {}
    sc = scores_from_rebalance_item(item)
    yt = _f(src.get("y_trade"))
    if yt is None:
        yt = sc.get("y_trade")
    yn = _f(src.get("y_nowcast") if src.get("y_nowcast") is not None else src.get("y_nc"))
    if yn is None:
        yn = sc.get("y_nowcast")
    yp = _f(src.get("y_path") if src.get("y_path") is not None else src.get("predicted_score_path"))
    if yp is None:
        yp = _f(item.get("y_path") if item.get("y_path") is not None else item.get("predicted_score_path"))
    yo = _f(src.get("y_on"))
    if yo is None:
        yo = y_on_of(item)
    ytau = _f(src.get("y_tau") if src.get("y_tau") is not None else src.get("predicted_score_tau"))
    if ytau is None:
        ytau = y_tau_of(item)
    payload = _score_fields_for_report(
        item,
        {"y_trade": yt, "y_path": yp, "y_nowcast": yn, "y_on": yo, "y_tau": ytau},
    )
    yf = src.get("y_fuse")
    if yf is not None:
        payload["y_fuse"] = yf
    rs = src.get("ranking_score")
    if rs is not None:
        payload["ranking_score"] = rs
    return payload


def _apply_one_leg(
    paper: dict,
    leg: dict,
    *,
    as_of: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """按计划价（含成本模型滑点）落地一笔买卖。先卖后买由调用方保证。"""
    from core.paper.costs import (
        annotate_trade,
        apply_fill_price,
        calc_trade_fees,
        cost_params,
        resolve_cost_model,
    )
    from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, merge_origin
    from core.paper.sizing import _lot_shares as lot_sh

    side = str(leg.get("side") or "").strip().lower()
    code = str(leg.get("stock_code") or "").strip()
    try:
        shares = float(leg.get("shares") or 0)
    except (TypeError, ValueError):
        shares = 0.0
    raw_px = _f(leg.get("price"))
    if side not in ("buy", "sell") or not code or shares <= 0 or not raw_px or raw_px <= 0:
        return None, "invalid_leg"

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    fill_px = apply_fill_price(side, float(raw_px), model=model, params=params)
    cash = float(paper.get("cash") or 0)
    holdings = list(paper.get("holdings") or [])
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = leg.get("stock_name") or (existing or {}).get("stock_name")
    note = leg.get("reason") or leg.get("matrix_action") or "watching_matrix"
    rank_fields = {
        k: leg.get(k)
        for k in (
            "y_fuse",
            "y_on",
            "y_tau",
            "predicted_score_tau",
            "ranking_score",
            "reason",
            "y_trade",
            "y_nowcast",
            "rank_i",
            "rank_n",
            "lot_kind",
            "action",
        )
        if leg.get(k) is not None
    }
    if as_of:
        rank_fields["as_of"] = str(as_of)[:10]

    if side == "sell":
        have = float((existing or {}).get("shares") or 0)
        from core.paper.tplus1 import TPLUS1_LOCK_REASON, clip_sell_shares, consume_sell_lots

        sell_shares, t1_meta = clip_sell_shares(
            existing or {}, min(shares, have), as_of=as_of
        )
        sell_shares = float(lot_sh(sell_shares) or sell_shares)
        if sell_shares <= 0 or not existing:
            return None, (t1_meta.get("reason") or TPLUS1_LOCK_REASON) if have > 0 else "no_position"
        cost = float(existing.get("cost") or 0)
        have_sh = float(existing.get("shares") or 0)
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        pnl_pct = round((fill_px / cost - 1.0) * 100.0, 2) if cost else None
        cum_cost = round(cost * have_sh, 2) if cost and have_sh else None
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": name,
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "cost_price": round(cost, 4) if cost else None,
                "cum_cost": cum_cost,
                "open_date": _open_date_of(existing, as_of),
                "pnl_pct": pnl_pct,
                "score": leg.get("y_trade") if leg.get("y_trade") is not None else leg.get("score"),
                "origin": ORIGIN_STRATEGY,
                "note": f"矩阵调仓 · {note}",
                "matrix_action": leg.get("matrix_action"),
                "y_trade": leg.get("y_trade"),
                "y_path": leg.get("y_path"),
                **rank_fields,
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        paper["cash"] = round(cash + float(fee_info.get("net_cash_delta") or 0), 2)
        consume_sell_lots(existing, sell_shares)
        if float(existing.get("shares") or 0) <= 1e-6:
            paper["holdings"] = [h for h in holdings if str(h.get("stock_code")) != code]
        paper["updated_at"] = trade["ts"]
        return trade, None

    buy_shares = float(lot_sh(shares) or shares)
    if buy_shares <= 0:
        return None, "lot"
    amount = round(buy_shares * fill_px, 2)
    fee_info = calc_trade_fees("buy", amount, model=model, params=params)
    need = amount + float(fee_info.get("fees") or 0)
    if need > cash + 1e-6:
        return None, "cash"
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        floor = float(get_rank_lot_cfg(paper).get("cash_floor") or 0.0)
    except Exception:  # noqa: BLE001
        logger.debug("cash_floor resolve failed", exc_info=True)
        floor = 0.0
    if cash - need < floor - 1e-6:
        return None, "cash_floor"
    fill_rounded = round(fill_px, 4)
    old_sh = float(existing.get("shares") or 0) if existing else 0.0
    old_cost = float(existing.get("cost") or 0) if existing else 0.0
    cum_cost = round(old_cost * old_sh + fill_px * buy_shares, 2)
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": fill_rounded,
            "amount": amount,
            "cost_price": fill_rounded,
            "cum_cost": cum_cost,
            "open_date": _open_date_of(existing, as_of),
            "score": leg.get("y_trade") if leg.get("y_trade") is not None else leg.get("score"),
            "origin": ORIGIN_STRATEGY,
            "note": f"矩阵调仓 · {note}",
            "matrix_action": leg.get("matrix_action"),
            "y_trade": leg.get("y_trade"),
            "y_path": leg.get("y_path"),
            **rank_fields,
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    from core.paper.tplus1 import add_buy_lot, stamp_new_holding

    if existing:
        new_sh = old_sh + buy_shares
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        add_buy_lot(existing, buy_shares, ts=trade["ts"], as_of=as_of)
        existing["origin"] = merge_origin(existing.get("origin"), ORIGIN_STRATEGY)
        if name and not existing.get("stock_name"):
            existing["stock_name"] = name
    else:
        row = {
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "cost": round(fill_px, 4),
            "bought_at": trade["ts"],
            "origin": ORIGIN_STRATEGY,
        }
        stamp_new_holding(row, ts=trade["ts"], as_of=as_of)
        holdings.append(row)
        paper["holdings"] = holdings
    paper["updated_at"] = trade["ts"]
    return trade, None


def _apply_matrix_trades(
    paper: dict,
    sell_trades: Sequence[dict],
    buy_trades: Sequence[dict],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """先卖后买落地；失败腿进 apply_skips，不中断其余。"""
    applied_sells: List[dict] = []
    applied_buys: List[dict] = []
    skips: List[dict] = []
    for leg in sell_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "side": "sell",
                    "reason": err or "apply_failed",
                    "path_matrix": True,
                    "tplus1": bool(err and "T+1" in str(err)),
                }
            )
            continue
        applied_sells.append(trade)
    for leg in buy_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "side": "buy",
                    "reason": err or "apply_failed",
                    "path_matrix": True,
                }
            )
            continue
        applied_buys.append(trade)
    sell_match_skips = [
        s for s in skips if str(s.get("side") or "") == "sell"
    ]
    return {
        "sell_trades": applied_sells,
        "buy_trades": applied_buys,
        "apply_skips": skips,
        "sell_match_skips": sell_match_skips,
    }


def simulate_watching_matrix_preview(
    paper: dict,
    *,
    top_k: Optional[int] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    dry_run: bool = True,
    offline_only: bool = True,
) -> Dict[str, Any]:
    """观察池算分 + rank_lots（y_fuse/y_on · 200/500 股）。默认 offline；dry_run 不写仓。"""
    from core.paper.ledger import mark_to_market
    from core.paper.rebalance.rank_lots import (
        ACTION_ADD,
        ACTION_EXIT,
        ACTION_OPEN,
        get_rank_lot_cfg,
        plan_rank_lot_day,
    )

    _ = buy_floor, hold_floor
    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    k = int(rl_cfg["top_k"])

    held_codes = [
        str(h.get("stock_code") or "").strip()
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict) and float(h.get("shares") or 0) > 0
    ]
    codes, pool_meta = _watching_codes(include_held=held_codes)
    name_by_code = (
        pool_meta.get("name_by_code")
        if isinstance(pool_meta.get("name_by_code"), dict)
        else {}
    )
    if pool_meta.get("error"):
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": pool_meta.get("error"),
            "empty_reason": "empty_ranking",
            "confirm_supported": True,
        }
    if not codes:
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": "观察池为空",
            "empty_reason": "empty_ranking",
            "confirm_supported": True,
        }

    # 与持仓表 / 数据中心同源：signal_config.scoring.horizon_days
    try:
        from core.signal.config import get_scoring_horizon_days

        horizon = int(get_scoring_horizon_days())
    except Exception:  # noqa: BLE001
        logger.debug("get_scoring_horizon_days failed", exc_info=True)
        horizon = 1
    use_offline = bool(offline_only)
    scored, rejected = _score_pool(
        codes, horizon_days=horizon, offline_only=use_offline
    )
    summary = mark_to_market(paper) or {}
    equity = float(summary.get("equity") or 0) or 0.0
    cash_before = float(summary.get("cash") or paper.get("cash") or 0)
    cash = cash_before

    item_by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in scored
        if str(it.get("stock_code") or "").strip()
    }
    for code in held_codes:
        if code not in item_by_code:
            item_by_code[code] = {"stock_code": code, "hard_reject": False}
        name = _resolve_report_name(
            code,
            item=item_by_code.get(code),
            paper=paper,
            name_by_code=name_by_code,
        )
        if name:
            item_by_code[code]["stock_name"] = name

    prices: Dict[str, float] = {}
    for code in item_by_code:
        px = _quote_px(code, offline_only=use_offline)
        if px is not None and px > 0:
            prices[code] = float(px)

    plan = plan_rank_lot_day(
        scored=list(item_by_code.values()),
        holdings=list(paper.get("holdings") or []),
        cash=cash_before,
        prices=prices,
        cfg=rl_cfg,
    )
    oos_excluded = sum(
        1
        for s in (plan.get("skips") or [])
        if "OOS" in str(s.get("reason") or "")
    )

    decisions: List[dict] = []
    buy_trades: List[dict] = []
    sell_trades: List[dict] = []
    report: List[dict] = []
    skips: List[dict] = list(plan.get("skips") or [])

    held_sh_of = {
        str(h.get("stock_code") or "").strip(): float(h.get("shares") or 0)
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict)
    }

    def _base_row(code: str, src: Optional[dict] = None) -> dict:
        item = item_by_code.get(code) or {}
        name = _resolve_report_name(
            code, item=item, paper=paper, name_by_code=name_by_code
        )
        px = prices.get(code)
        return {
            "stock_code": code,
            "stock_name": name,
            "matrix_mode": True,
            "price": px,
            **_row_score_payload(item, src),
        }

    def _leg_row(leg: dict, *, side: str) -> dict:
        code = str(leg.get("stock_code") or "")
        act = str(leg.get("action") or "")
        held_sh = float(held_sh_of.get(code) or 0)
        shares = float(leg.get("shares") or 0)
        px = prices.get(code)
        row = {
            **_base_row(code, leg),
            "action": act,
            "reason": leg.get("reason"),
            "execute": True,
            "side": side,
            "shares": shares,
            "amount": round(shares * float(px), 2) if px else None,
        }
        if side == "buy":
            row["decision"] = "买入" if act == ACTION_OPEN else "加仓"
            row["old_shares"] = held_sh
            row["new_shares"] = held_sh + shares
            row["shares_change"] = shares
        else:
            row["decision"] = "卖出"
            row["old_shares"] = held_sh
            row["new_shares"] = max(0.0, held_sh - shares)
            row["shares_change"] = -shares
        return row

    def _status_row(
        src: dict,
        *,
        decision: str,
        held_sh: float,
        new_sh: Optional[float] = None,
    ) -> dict:
        code = str(src.get("stock_code") or "")
        ns = float(held_sh if new_sh is None else new_sh)
        return {
            **_base_row(code, src),
            "action": src.get("action"),
            "reason": src.get("reason"),
            "execute": False,
            "decision": decision,
            "old_shares": held_sh,
            "new_shares": ns,
            "shares_change": ns - held_sh,
            "shares": held_sh,
        }

    for leg in plan.get("sells") or []:
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        sell_trades.append(trade)
        report.append(_leg_row(leg, side="sell"))
        decisions.append({"stock_code": code, "action": ACTION_EXIT, **leg})

    for leg in plan.get("buys") or []:
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        buy_trades.append(trade)
        report.append(_leg_row(leg, side="buy"))
        decisions.append({"stock_code": code, "action": leg.get("action"), **leg})

    reported = {
        str(r.get("stock_code") or "").strip()
        for r in report
        if str(r.get("stock_code") or "").strip()
    }
    skip_by_code = {
        str(s.get("stock_code") or "").strip(): s
        for s in skips
        if str(s.get("stock_code") or "").strip()
    }
    for hrow in plan.get("holds") or []:
        decisions.append(hrow)
        code = str(hrow.get("stock_code") or "").strip()
        if not code or code in reported:
            continue
        held_sh = float(held_sh_of.get(code) or 0)
        reason = str(hrow.get("reason") or "ranking≥0% 持有")
        sk = skip_by_code.get(code) or {}
        sk_reason = str(sk.get("reason") or "")
        if sk_reason and "地板" in sk_reason and sk_reason not in reason:
            reason = f"{reason} · {sk_reason}"
        src = {**sk, **hrow, "reason": reason}
        report.append(_status_row(src, decision="持有", held_sh=held_sh))
        reported.add(code)

    for sk in skips:
        code = str(sk.get("stock_code") or "").strip()
        if not code or code in reported:
            continue
        held_sh = float(held_sh_of.get(code) or 0)
        report.append(_status_row(sk, decision="跳过", held_sh=held_sh))
        reported.add(code)

    report.sort(
        key=lambda r: (
            0 if str(r.get("side") or "") == "sell" else 1,
            str(r.get("stock_code") or ""),
        )
    )

    apply_skips: List[dict] = []
    sell_match_skips: List[dict] = []
    if not dry_run and (buy_trades or sell_trades):
        applied = _apply_matrix_trades(paper, sell_trades, buy_trades)
        sell_trades = list(applied.get("sell_trades") or [])
        buy_trades = list(applied.get("buy_trades") or [])
        apply_skips = list(applied.get("apply_skips") or [])
        sell_match_skips = list(applied.get("sell_match_skips") or [])
        if apply_skips:
            skips.extend(apply_skips)
        # 落账后刷新净值摘要
        summary = mark_to_market(paper) or summary
        cash = float(summary.get("cash") or paper.get("cash") or cash)

    buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades)
    sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
    empty = None
    empty_detail = None
    floor_val = _f(rl_cfg.get("cash_floor")) or 0.0
    cash_skips = [
        s
        for s in skips
        if "地板" in str(s.get("reason") or "")
    ]
    if not buy_trades and not sell_trades:
        if cash_skips or (floor_val > 0 and cash_before + 1e-6 < floor_val):
            empty = "cash_below_floor"
            empty_detail = (
                f"现金 {cash_before:,.0f} < 地板 {floor_val:,.0f}"
            )
        elif not scored and not held_codes:
            empty = "empty_ranking"
        else:
            empty = "no_executable_changes"

    by_action: Dict[str, int] = {}
    for d in decisions:
        a = str(d.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1

    note = (
        "观察池 + rank_lots 预演（未写账）"
        if dry_run
        else "观察池 + rank_lots 落账"
    )
    strategy_id = paper.get("strategy_id")
    strategy_label = None
    if strategy_id:
        try:
            from core.strategy import get_strategy_spec

            strategy_label = get_strategy_spec(str(strategy_id)).get("label")
        except Exception:  # noqa: BLE001
            logger.debug("strategy_label resolve failed", exc_info=True)
    return {
        "success": True,
        "ok": True,
        "mode": "watching_matrix",
        "dry_run": bool(dry_run),
        "matrix_mode": True,
        "cluster_mode": False,
        "offline_only": use_offline,
        "top_k": k,
        "min_score": rl_cfg.get("rank_enter"),
        "rank_enter": rl_cfg.get("rank_enter"),
        "rank_enter_pct": round(float(rl_cfg.get("rank_enter") or 0) * 100.0, 4),
        "min_hold_score": 0.0,
        "observation_pool_count": len(codes),
        "scored_count": len(scored),
        "rejected": rejected[:40],
        "rejected_count": len(rejected),
        "pool_meta": pool_meta,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "rebalance_report": report,
        "risk_budget_skips": skips,
        "apply_skips": apply_skips,
        "sell_match_skips": sell_match_skips,
        "summary": summary,
        "cash_impact": {
            "buy_amount": round(buy_amt, 2),
            "sell_amount": round(sell_amt, 2),
            "net_cash": round(sell_amt - buy_amt, 2),
            "cash_before": cash_before,
            "cash_after": float(paper.get("cash") or cash) if not dry_run else None,
            "cash_floor": rl_cfg.get("cash_floor"),
            "cash_floor_configured": rl_cfg.get("cash_floor_configured"),
            "cash_floor_scaled": bool(rl_cfg.get("cash_floor_scaled")),
            "turnover_pct": round(
                (buy_amt + sell_amt) / equity * 100.0, 2
            )
            if equity > 0
            else None,
        },
        "empty_reason": empty,
        "empty_detail": empty_detail,
        "path_matrix": {
            "enabled": True,
            "mode": "rank_lots",
            "oos_excluded": oos_excluded,
            "cfg": {
                "rank_enter": rl_cfg.get("rank_enter"),
                "rank_strong": rl_cfg.get("rank_strong"),
                "cash_floor": rl_cfg.get("cash_floor"),
                "cash_floor_configured": rl_cfg.get("cash_floor_configured"),
                "cash_floor_scaled": bool(rl_cfg.get("cash_floor_scaled")),
                "fusion_w_trade": rl_cfg.get("fusion_w_trade"),
                "fusion_w_nowcast": rl_cfg.get("fusion_w_nowcast"),
                "y_on_alpha": rl_cfg.get("y_on_alpha"),
            },
            "by_action": by_action,
        },
        "target_weights": {},
        "note": note,
        "strategy_id": strategy_id,
        "strategy_label": strategy_label,
        "ops_report": {
            "strategy_id": strategy_id,
            "strategy_label": strategy_label,
            "note": (
                "watching_matrix 预演"
                if dry_run
                else "watching_matrix 落账"
            ),
            "matrix_by_action": by_action,
            "matrix_mode": "rank_lots",
        },
        "confirm_supported": True,
    }


__all__ = ["simulate_watching_matrix_preview"]
