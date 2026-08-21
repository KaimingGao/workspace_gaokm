"""纸面组合简化归因（P2 · 非完整 Brinson / 非 Barra）。

基于当前 MTM 持仓：市值权重 × 持仓盈亏，做选股 / 配置 / 残差分解，
并给出个股贡献 Top-N。研究回测归因见 ``core.backtest.attribution``。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def _sector_for(code: str, sector: Optional[str] = None) -> str:
    if sector:
        return str(sector)
    try:
        from core.portfolio_optimize import _sector_for as _sf
        from core.portfolio_optimize import load_sector_map

        return _sf(code, load_sector_map())
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_attribution.py", exc_info=True)
        return "其他"


def _period_return_pct(paper: Optional[dict], equity_now: float) -> Optional[float]:
    """相对上一快照的净值变动 %。"""
    snaps = list((paper or {}).get("snapshots") or [])
    if not snaps or equity_now <= 0:
        return None
    prev = snaps[-1] if isinstance(snaps[-1], dict) else None
    if not prev:
        return None
    try:
        eq0 = float(prev.get("equity") or 0)
    except (TypeError, ValueError):
        return None
    if eq0 <= 0:
        return None
    return round((equity_now / eq0 - 1.0) * 100.0, 3)


def build_paper_attribution_lite(
    paper: Optional[dict] = None,
    summary: Optional[dict] = None,
    *,
    top_n: int = 5,
) -> Dict[str, Any]:
    """
    持仓 MTM 一页归因。

    - 配置（allocation）：行业权重相对等权行业基准 × 基准收益
    - 选股（selection）：基准行业权重 ×（行业收益 − 基准收益）
    - 残差（residual）：interaction + 未解释部分
    """
    paper = paper or {}
    sm = summary or {}
    holdings = list(sm.get("holdings") or paper.get("holdings") or [])
    equity = float(sm.get("equity") or 0)
    stock_value = float(sm.get("stock_value") or 0)
    if stock_value <= 0:
        stock_value = sum(float(h.get("market_value") or 0) for h in holdings)
    if equity <= 0:
        equity = float(paper.get("cash") or 0) + stock_value

    legs: List[Dict[str, Any]] = []
    for h in holdings:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        mv = float(h.get("market_value") or 0)
        if mv <= 0:
            mv = float(h.get("cost") or 0) * float(h.get("shares") or 0)
        if mv <= 0 or stock_value <= 0:
            continue
        try:
            ret = float(h.get("pnl_pct"))
        except (TypeError, ValueError):
            cost = float(h.get("cost") or 0)
            px = float(h.get("price") or 0)
            if cost > 0 and px > 0:
                ret = (px / cost - 1.0) * 100.0
            else:
                continue
        w = mv / stock_value
        sec = _sector_for(code, h.get("sector"))
        contrib = w * ret
        legs.append(
            {
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "sector": sec,
                "weight_pct": round(w * 100.0, 2),
                "return_pct": round(ret, 3),
                "contrib_pct": round(contrib, 3),
                "market_value": round(mv, 2),
            }
        )

    if not legs:
        return {
            "ok": False,
            "mode": "holdings_mtm",
            "reason": "no_holdings",
            "note": "无持仓市值，无法归因。",
        }

    port_ret = sum(float(l["weight_pct"]) / 100.0 * float(l["return_pct"]) for l in legs)
    port_ret = round(port_ret, 3)

    by_sec: Dict[str, Dict[str, float]] = {}
    for l in legs:
        sec = str(l["sector"])
        b = by_sec.setdefault(sec, {"w": 0.0, "wr": 0.0})
        w = float(l["weight_pct"]) / 100.0
        b["w"] += w
        b["wr"] += w * float(l["return_pct"])

    s_count = len(by_sec)
    bench_w = 1.0 / s_count if s_count else 0.0
    # 无外部行业指数：行业基准收益用组合加权收益
    bench_r = port_ret

    allocation = 0.0
    selection = 0.0
    interaction = 0.0
    sector_rows: List[Dict[str, Any]] = []
    for sec, b in by_sec.items():
        w_p = float(b["w"])
        r_p = float(b["wr"]) / w_p if w_p > 1e-12 else 0.0
        w_b = bench_w
        r_b = bench_r
        a = (w_p - w_b) * r_b
        s = w_b * (r_p - r_b)
        i = (w_p - w_b) * (r_p - r_b)
        allocation += a
        selection += s
        interaction += i
        sector_rows.append(
            {
                "sector": sec,
                "weight_pct": round(w_p * 100.0, 2),
                "bench_weight_pct": round(w_b * 100.0, 2),
                "avg_return_pct": round(r_p, 3),
                "allocation_pct": round(a, 3),
                "selection_pct": round(s, 3),
                "residual_pct": round(i, 3),
                "contrib_pct": round(w_p * r_p, 3),
            }
        )
    sector_rows.sort(key=lambda r: -abs(float(r.get("contrib_pct") or 0)))

    top = sorted(legs, key=lambda x: -abs(float(x.get("contrib_pct") or 0)))[
        : max(1, int(top_n))
    ]
    residual = round(interaction, 3)
    total_excess = round(allocation + selection + interaction, 3)

    period = _period_return_pct(paper, equity)

    return {
        "ok": True,
        "mode": "holdings_mtm",
        "selection_pct": round(selection, 3),
        "allocation_pct": round(allocation, 3),
        "residual_pct": residual,
        "interaction_pct": residual,
        "total_excess_pct": total_excess,
        "portfolio_return_pct": port_ret,
        "period_return_pct": period,
        "by_sector": sector_rows[:8],
        "top_contributors": [
            {
                "stock_code": t["stock_code"],
                "stock_name": t.get("stock_name"),
                "sector": t.get("sector"),
                "weight_pct": t.get("weight_pct"),
                "return_pct": t.get("return_pct"),
                "contrib_pct": t.get("contrib_pct"),
            }
            for t in top
        ],
        "name_count": len(legs),
        "methodology": (
            "纸面 Brinson lite（市值权重）：配置=行业超配×基准收益；"
            "选股=基准行业权×行业超额；残差=interaction。"
            "基准行业收益=组合加权收益（无外部行业指数）。非完整因子归因。"
        ),
        "note": "一页简化归因 · 持仓 MTM · 非完整 Brinson/Barra",
    }
