"""观察池 → 模拟账户同步。"""

from typing import Any, Dict, List, Optional

from core.numbers import now_iso_local as _now_iso
from core.watching.repository import read_watching
from core.watching.sources import refresh_watchlist


def _resolve_sync_targets(codes: Optional[List[str]] = None) -> tuple:
    """解析加入纸面的目标代码，返回 (target, missing)。"""
    uni = read_watching()
    uni_wl = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    if not uni_wl and codes is None:
        refresh = refresh_watchlist(uni)
        uni_wl = [str(c).strip() for c in (refresh.get("watchlist") or []) if str(c).strip()]

    if codes is None:
        return list(uni_wl), []

    wanted = [str(c).strip() for c in codes if str(c).strip()]
    if not wanted:
        raise ValueError("请勾选要加入纸面的股票")
    allowed = set(uni_wl)
    selected = [c for c in wanted if c in allowed]
    missing = [c for c in wanted if c not in allowed]
    if not selected:
        raise ValueError("所选代码均不在观察名单中")
    return selected, missing


def _sync_sizing_kwargs(
    *,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """建仓定量：默认按金额；显式股数/金额/% 时按传入优先。"""
    from core.paper import DEFAULT_SYNC_AMOUNT, DEFAULT_SYNC_LOT_SHARES

    has_shares = lot_shares is not None or bool(shares_by_code)
    has_amount = (
        amount_per_code is not None
        or bool(amount_by_code)
        or position_pct is not None
    )
    if not has_shares and not has_amount:
        return {
            "lot_shares": 0,
            "shares_by_code": None,
            "amount_per_code": DEFAULT_SYNC_AMOUNT,
            "amount_by_code": None,
            "position_pct": None,
        }
    return {
        "lot_shares": int(lot_shares or DEFAULT_SYNC_LOT_SHARES) if has_shares else 0,
        "shares_by_code": shares_by_code,
        "amount_per_code": amount_per_code,
        "amount_by_code": amount_by_code,
        "position_pct": position_pct,
    }


def plan_sync_to_paper(
    paper_path: Optional[str] = None,
    *,
    codes: Optional[List[str]] = None,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """加入模拟账户预览：每只买多少、合计花多少、剩余现金，不落盘。"""
    from core.paper import load_paper, plan_buy_codes

    target, missing = _resolve_sync_targets(codes)
    paper = load_paper(paper_path)
    plan = plan_buy_codes(
        paper,
        target,
        **_sync_sizing_kwargs(
            lot_shares=lot_shares,
            shares_by_code=shares_by_code,
            amount_per_code=amount_per_code,
            amount_by_code=amount_by_code,
            position_pct=position_pct,
        ),
    )
    plan["success"] = True
    plan["selected"] = codes is not None
    if missing:
        plan["missing"] = missing
    return plan


def sync_paper_watchlist(
    paper_path: Optional[str] = None,
    *,
    codes: Optional[List[str]] = None,
    buy: bool = False,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """观察 → 模拟建仓：buy=True 时按现价假买进持仓。

    产品只维护 watching + holdings；不再写入 paper.watchlist。
    codes 为 None：全部观察；否则只处理所选子集（须在观察名单内）。
    buy=False：空操作（日更可保留该步骤；观察与仓位已分离）。
    """
    from core.paper import (
        append_operation_log,
        buy_codes_direct,
        holding_codes,
        load_paper,
        mark_to_market,
        save_paper,
    )

    target, missing = _resolve_sync_targets(codes)
    sizing = _sync_sizing_kwargs(
        lot_shares=lot_shares,
        shares_by_code=shares_by_code,
        amount_per_code=amount_per_code,
        amount_by_code=amount_by_code,
        position_pct=position_pct,
    )

    paper = load_paper(paper_path)
    trades: List[dict] = []
    skipped_buy: List[dict] = []
    if buy:
        result = buy_codes_direct(paper, target, **sizing)
        trades = result.get("trades") or []
        skipped_buy = [
            s
            for s in (result.get("skipped") or [])
            if s.get("reason") not in ("已持仓",)
        ]
        mark_to_market(paper)
        paper["updated_at"] = _now_iso()
        if trades:
            from core.paper.costs import fee_fields_from_trade

            for t in trades:
                fee_meta = fee_fields_from_trade(t)
                append_operation_log(
                    paper, "sync_paper",
                    detail=(
                        f"加入模拟 {t.get('stock_name') or t.get('stock_code')} "
                        f"{t.get('shares')}股 @ {t.get('price')}"
                    ),
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "manual",
                        **fee_meta,
                    },
                )
    # 写入时剔除历史 paper.watchlist；buy=False 时也保存以完成迁移
    save_paper(paper, paper_path)

    held = holding_codes(paper)
    bought_n = len(trades)
    out: Dict[str, Any] = {
        "success": True,
        "holdings_count": len(held),
        "holdings_codes": held,
        "selected": codes is not None,
        "bought_count": bought_n,
        "new_trades": trades,
        "buy_skipped": skipped_buy,
    }
    parts = []
    if buy:
        if bought_n:
            parts.append(f"已买入 {bought_n} 只进持仓")
        parts.append(f"持仓共 {len(held)} 只")
        if skipped_buy:
            reasons = "；".join(
                f"{s.get('stock_code')}:{s.get('reason')}" for s in skipped_buy[:5]
            )
            parts.append(f"未买入 {len(skipped_buy)} 只（{reasons}）")
        if bought_n == 0 and not any(
            str(h.get("stock_code")) in target for h in (paper.get("holdings") or [])
        ):
            parts.insert(0, "未能加入持仓")
    else:
        parts.append(f"无需同步名单 · 持仓 {len(held)} 只（观察请在观察页维护）")
    if missing:
        out["skipped"] = missing
        parts.append(f"跳过 {len(missing)} 只不在观察中")
    out["message"] = " · ".join(parts)
    return out
