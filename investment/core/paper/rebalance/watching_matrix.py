"""观察池实时算分 + path_matrix 预演/落账。

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


def _watching_codes(*, include_held: Sequence[str] = ()) -> Tuple[List[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"source": "watching", "n_watch": 0, "n_held_extra": 0}
    try:
        from core.watching.store import read_watching, refresh_watchlist

        uni = read_watching()
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        if not codes:
            refreshed = refresh_watchlist(uni)
            codes = [str(c).strip() for c in (refreshed.get("watchlist") or []) if str(c).strip()]
        meta["n_watch"] = len(codes)
        meta["watching_name"] = uni.get("name")
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
    return codes, meta


def _score_pool(codes: Sequence[str], *, horizon_days: int = 1) -> Tuple[List[dict], List[dict]]:
    """观察池逐票 score_stock（含 dual_score / path）；失败进 rejected。"""
    from core.signal.score_stock import score_stock

    scored: List[dict] = []
    rejected: List[dict] = []
    # 预演上限：与 watching max 对齐，避免一次打爆
    for raw in list(codes)[:120]:
        code = str(raw or "").strip()
        if not code:
            continue
        try:
            result = score_stock(
                code,
                horizon_days=max(1, int(horizon_days or 1)),
                skip_sentiment=True,
                quote_timeout=6.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("score_stock failed for %s", code, exc_info=True)
            rejected.append({"stock_code": code, "reason": str(exc)[:120]})
            continue
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
        # 对齐 y_* 字段
        try:
            from core.signal.dual_score import align_trade_score_fields

            align_trade_score_fields(item, write_score=False, refresh_window=False)
        except Exception:  # noqa: BLE001
            logger.debug("align_trade_score_fields failed", exc_info=True)
        if item.get("stock_code") is None:
            item["stock_code"] = code
        scored.append(item)
    return scored, rejected


def _y_trade_of(item: dict) -> Optional[float]:
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item

    return scores_from_rebalance_item(item).get("y_trade")


def _current_weights(paper: dict, equity: float) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if equity <= 0:
        return out
    for h in paper.get("holdings") or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        sh = _f(h.get("shares")) or 0.0
        px = _f(h.get("last_price")) or _f(h.get("cost")) or 0.0
        if sh <= 0 or px <= 0:
            continue
        out[code] = float(sh) * float(px) / float(equity)
    return out


def _target_weights_for_topk(
    scored: Sequence[dict],
    *,
    top_k: int,
    buy_floor: float,
    max_position_pct: float,
) -> Dict[str, float]:
    """按 y_trade 选 Top-K，score_budget 分配目标仓（0–1）。"""
    rows: List[dict] = []
    for it in scored:
        yt = _y_trade_of(it)
        if yt is None or float(yt) < float(buy_floor):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "stock_code": code,
                "score": float(yt),
                "sector": it.get("sector") or "未知",
            }
        )
    rows.sort(key=lambda r: (-float(r["score"]), str(r["stock_code"])))
    rows = rows[: max(1, int(top_k))]
    if not rows:
        return {}
    try:
        from core.risk.budget import score_budget_weights

        weights_pct, _, _ = score_budget_weights(
            rows,
            max_position_pct=float(max_position_pct),
            max_sector_pct=min(100.0, float(max_position_pct) * 3),
            max_positions=len(rows),
        )
        return {
            str(k): float(v) / 100.0
            for k, v in (weights_pct or {}).items()
            if v is not None
        }
    except Exception:  # noqa: BLE001
        logger.debug("score_budget_weights failed, equal weight", exc_info=True)
        w = 1.0 / float(len(rows))
        cap = float(max_position_pct) / 100.0
        w = min(w, cap) if cap > 0 else w
        return {str(r["stock_code"]): w for r in rows}


def _quote_px(code: str) -> Optional[float]:
    try:
        from core.data.facade import get_quote

        q = get_quote(code) or {}
        px = _f(q.get("price_raw") if q.get("price_raw") is not None else q.get("price"))
        if px is None:
            px = _f(q.get("close"))
        return float(px) if px is not None and px > 0 else None
    except Exception:  # noqa: BLE001
        logger.debug("quote failed for %s", code, exc_info=True)
        return None


def _lot_shares(amount: float, price: float) -> int:
    if amount <= 0 or price <= 0:
        return 0
    return int(amount // price // 100) * 100


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
    # 矩阵决议分数优先（已对齐 dual_y）
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
    return out


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
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        pnl_pct = round((fill_px / cost - 1.0) * 100.0, 2) if cost else None
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": name,
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
                "score": leg.get("y_trade") if leg.get("y_trade") is not None else leg.get("score"),
                "origin": ORIGIN_STRATEGY,
                "note": f"矩阵调仓 · {note}",
                "matrix_action": leg.get("matrix_action"),
                "y_trade": leg.get("y_trade"),
                "y_path": leg.get("y_path"),
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
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": round(fill_px, 4),
            "amount": amount,
            "score": leg.get("y_trade") if leg.get("y_trade") is not None else leg.get("score"),
            "origin": ORIGIN_STRATEGY,
            "note": f"矩阵调仓 · {note}",
            "matrix_action": leg.get("matrix_action"),
            "y_trade": leg.get("y_trade"),
            "y_path": leg.get("y_path"),
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    from core.paper.tplus1 import add_buy_lot, stamp_new_holding

    if existing:
        old_sh = float(existing.get("shares") or 0)
        old_cost = float(existing.get("cost") or 0)
        new_sh = old_sh + buy_shares
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        add_buy_lot(existing, buy_shares, ts=trade["ts"])
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
        stamp_new_holding(row, ts=trade["ts"])
        holdings.append(row)
        paper["holdings"] = holdings
    paper["updated_at"] = trade["ts"]
    return trade, None


def _apply_matrix_trades(
    paper: dict,
    sell_trades: Sequence[dict],
    buy_trades: Sequence[dict],
) -> Dict[str, Any]:
    """先卖后买落地；失败腿进 apply_skips，不中断其余。"""
    applied_sells: List[dict] = []
    applied_buys: List[dict] = []
    skips: List[dict] = []
    for leg in sell_trades:
        trade, err = _apply_one_leg(paper, leg)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "side": "sell",
                    "reason": err or "apply_failed",
                    "path_matrix": True,
                }
            )
            continue
        applied_sells.append(trade)
    for leg in buy_trades:
        trade, err = _apply_one_leg(paper, leg)
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
    return {
        "sell_trades": applied_sells,
        "buy_trades": applied_buys,
        "apply_skips": skips,
    }


def simulate_watching_matrix_preview(
    paper: dict,
    *,
    top_k: Optional[int] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """观察池实时算分 + L1 path_matrix。dry_run 不写仓；False 则原地改 paper。"""
    from core.paper.ledger import mark_to_market
    from core.paper.rebalance.path_matrix import (
        ACTION_ADD,
        ACTION_EXIT,
        ACTION_OPEN,
        ACTION_PENDING_EXIT,
        ACTION_REDUCE,
        ACTION_SKIP,
        get_path_matrix_cfg,
        resolve_rebalance_action_from_item,
    )
    from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

    rules = paper.get("rules") if isinstance(paper.get("rules"), dict) else {}
    max_pos = max(1, int(rules.get("max_positions") or 15))
    k = max(1, min(int(top_k or max_pos), 80))
    bf = float(buy_floor) if buy_floor is not None else float(
        resolve_buy_floor(paper, heuristic_default=0.01)
    )
    # predicted 门槛常为 ŷ%；若拿到 heuristic 大数则回退
    if bf > 20:
        bf = 0.01
    hf = float(hold_floor) if hold_floor is not None else float(
        resolve_hold_floor(paper, heuristic_default=0.01)
    )
    if hf > 20:
        hf = min(bf, 0.01)
    if hf > bf:
        hf = bf
    raw_pos = _f(rules.get("position_pct"))
    if raw_pos is None:
        max_pos_pct = 15.0
    elif raw_pos > 1.0:
        max_pos_pct = float(raw_pos)
    else:
        max_pos_pct = float(raw_pos) * 100.0
    max_pos_pct = max(5.0, min(max_pos_pct, 40.0))

    held_codes = [
        str(h.get("stock_code") or "").strip()
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict) and float(h.get("shares") or 0) > 0
    ]
    codes, pool_meta = _watching_codes(include_held=held_codes)
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

    scored, rejected = _score_pool(codes, horizon_days=1)
    summary = mark_to_market(paper) or {}
    equity = float(summary.get("equity") or 0) or 0.0
    cash_before = float(summary.get("cash") or paper.get("cash") or 0)
    cash = cash_before
    w_now = _current_weights(paper, equity if equity > 0 else 1.0)
    w_star = _target_weights_for_topk(
        scored,
        top_k=k,
        buy_floor=bf,
        max_position_pct=max_pos_pct,
    )
    top_codes = list(w_star.keys())

    # 预演/落账始终启用矩阵；模式读账户配置（path | linear）
    pm_cfg = get_path_matrix_cfg(paper=paper)
    pm_cfg["enabled"] = True
    mode = str(pm_cfg.get("mode") or "path").strip().lower()
    if mode not in ("path", "linear"):
        mode = "path"
    pm_cfg["mode"] = mode

    item_by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in scored
        if str(it.get("stock_code") or "").strip()
    }
    # 持仓但不在 scored：补空壳以便清仓决议
    for code in held_codes:
        if code not in item_by_code:
            item_by_code[code] = {"stock_code": code, "hard_reject": False}

    decisions: List[dict] = []
    buy_trades: List[dict] = []
    sell_trades: List[dict] = []
    report: List[dict] = []
    skips: List[dict] = []

    universe = sorted(set(list(w_star.keys()) + list(w_now.keys()) + list(item_by_code.keys())))
    for code in universe:
        item = item_by_code.get(code) or {"stock_code": code}
        w = float(w_now.get(code) or 0.0)
        ws = float(w_star.get(code) or 0.0)
        # 已持有但未进 Top-K：目标 0（由 hold 门槛在 resolve 内再判）
        if code in w_now and code not in w_star:
            yt = _y_trade_of(item)
            if yt is not None and float(yt) >= hf:
                # 滞回：未破 hold 则维持现仓为目标（本窗可不加）
                ws = w
            else:
                ws = 0.0
        dec = resolve_rebalance_action_from_item(
            item,
            w=w,
            w_star_day=ws,
            in_topk=(code in w_star) or (w > 1e-9),
            cfg=pm_cfg,
            buy_floor=bf,
            hold_floor=hf,
            mode=mode,
        )
        decisions.append({"stock_code": code, **dec})
        act = str(dec.get("action") or "")
        yt = (dec.get("scores") or {}).get("y_trade")
        yp = (dec.get("scores") or {}).get("y_path")
        yn = (dec.get("scores") or {}).get("y_nowcast")
        yo = (dec.get("scores") or {}).get("y_on")
        name = item.get("stock_name")
        score_payload = _score_fields_for_report(
            item if isinstance(item, dict) else {},
            {"y_trade": yt, "y_path": yp, "y_nowcast": yn, "y_on": yo},
        )
        row = {
            "stock_code": code,
            "stock_name": name,
            "action": act,
            "reason": dec.get("reason"),
            "w": w,
            "w_star": dec.get("w_star"),
            "w_close": dec.get("w_close"),
            "lambda": dec.get("lambda"),
            "delta_w": dec.get("delta_w"),
            "execute": dec.get("execute"),
            "matrix_mode": True,
            **score_payload,
        }

        if act in (ACTION_SKIP, ACTION_PENDING_EXIT) or not dec.get("execute"):
            if act in (ACTION_SKIP, ACTION_PENDING_EXIT, "hold"):
                skips.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "reason": dec.get("reason") or act,
                        "path_matrix": True,
                        "path_matrix_action": act,
                        "score": yt,
                    }
                )
            continue

        px = _quote_px(code)
        if px is None or px <= 0:
            skips.append(
                {
                    "stock_code": code,
                    "reason": "无有效报价",
                    "path_matrix": True,
                    "score": yt,
                }
            )
            continue

        delta = float(dec.get("delta_w") or 0.0)
        held_sh = 0.0
        for h in paper.get("holdings") or []:
            if str(h.get("stock_code") or "").strip() == code:
                held_sh = float(h.get("shares") or 0)
                break

        if act in (ACTION_OPEN, ACTION_ADD) and delta > 0 and equity > 0:
            amount = float(equity) * abs(delta)
            shares = _lot_shares(amount, px)
            if shares <= 0:
                skips.append(
                    {
                        "stock_code": code,
                        "reason": "金额不足一手",
                        "path_matrix": True,
                        "score": yt,
                    }
                )
                continue
            buy_trades.append(
                {
                    "side": "buy",
                    "stock_code": code,
                    "stock_name": name,
                    "shares": shares,
                    "price": px,
                    "amount": round(shares * px, 2),
                    "reason": dec.get("reason"),
                    "matrix_action": act,
                    "y_trade": yt,
                    "y_path": yp,
                    "dry_run": bool(dry_run),
                }
            )
            row.update(
                {
                    "side": "buy",
                    "shares": shares,
                    "price": px,
                    "decision": "买入" if act == ACTION_OPEN else "加仓",
                    "old_shares": held_sh,
                    "new_shares": held_sh + shares,
                    "shares_change": shares,
                }
            )
            report.append(row)
        elif act in (ACTION_EXIT, ACTION_REDUCE) and delta < 0:
            if held_sh <= 0:
                continue
            frac = min(1.0, abs(delta) / max(w, 1e-9)) if w > 1e-9 else 1.0
            if act == ACTION_EXIT and float(dec.get("lambda") or 1) >= 1.0 - 1e-9:
                frac = 1.0
            sell_sh = int(held_sh * frac // 100) * 100
            if sell_sh <= 0 and act == ACTION_EXIT:
                sell_sh = int(held_sh)
            if sell_sh <= 0:
                continue
            sell_trades.append(
                {
                    "side": "sell",
                    "stock_code": code,
                    "stock_name": name,
                    "shares": sell_sh,
                    "price": px,
                    "amount": round(sell_sh * px, 2),
                    "reason": dec.get("reason"),
                    "matrix_action": act,
                    "y_trade": yt,
                    "y_path": yp,
                    "dry_run": bool(dry_run),
                }
            )
            row.update(
                {
                    "side": "sell",
                    "shares": sell_sh,
                    "price": px,
                    "decision": "卖出" if act == ACTION_EXIT else "减仓",
                    "old_shares": held_sh,
                    "new_shares": max(0.0, held_sh - sell_sh),
                    "shares_change": -sell_sh,
                }
            )
            report.append(row)

    apply_skips: List[dict] = []
    if not dry_run and (buy_trades or sell_trades):
        applied = _apply_matrix_trades(paper, sell_trades, buy_trades)
        sell_trades = list(applied.get("sell_trades") or [])
        buy_trades = list(applied.get("buy_trades") or [])
        apply_skips = list(applied.get("apply_skips") or [])
        if apply_skips:
            skips.extend(apply_skips)
        # 落账后刷新净值摘要
        summary = mark_to_market(paper) or summary
        cash = float(summary.get("cash") or paper.get("cash") or cash)

    buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades)
    sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
    empty = None
    if not buy_trades and not sell_trades:
        empty = "no_executable_changes"

    by_action: Dict[str, int] = {}
    for d in decisions:
        a = str(d.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1

    note = (
        "观察池 + path_matrix 预演（未写账）"
        if dry_run
        else "观察池 + path_matrix 落账"
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
        "top_k": k,
        "min_score": bf,
        "min_hold_score": hf,
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
        "summary": summary,
        "cash_impact": {
            "buy_amount": round(buy_amt, 2),
            "sell_amount": round(sell_amt, 2),
            "net_cash": round(sell_amt - buy_amt, 2),
            "cash_before": cash_before,
            "cash_after": float(paper.get("cash") or cash) if not dry_run else None,
            "turnover_pct": round(
                (buy_amt + sell_amt) / equity * 100.0, 2
            )
            if equity > 0
            else None,
        },
        "empty_reason": empty,
        "path_matrix": {
            "enabled": True,
            "mode": mode,
            "cfg": {
                k2: pm_cfg.get(k2)
                for k2 in (
                    "mode",
                    "path_enter",
                    "path_half",
                    "path_full",
                    "y_on_allow",
                    "require_nowcast_for_open",
                )
            },
            "by_action": by_action,
        },
        "target_weights": {c: round(w * 100.0, 4) for c, w in w_star.items()},
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
            "matrix_mode": mode,
        },
        "confirm_supported": True,
    }


__all__ = ["simulate_watching_matrix_preview"]
