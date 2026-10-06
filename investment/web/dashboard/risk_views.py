"""仪表盘：组合风险 / 因子暴露。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from web.dashboard.paper_helpers import (
    _equity_curve_from_paper,
    _holding_market_value,
    _holding_sector,
    _holdings_from_paper,
    _load_raw_paper,
)


def _build_risk_metrics() -> Dict[str, Any]:
    """组合风险指标：Sharpe / Sortino / VaR / 波动率 / 最大回撤。"""
    paper = _load_raw_paper()
    eq_curve = _equity_curve_from_paper(paper)

    if not eq_curve or len(eq_curve) < 2:
        return {
            "ok": False,
            "status": "insufficient_data",
            "message": "净值曲线数据不足（至少2期）",
        }

    equities = [e.get("equity", 0) for e in eq_curve if e.get("equity")]
    if len(equities) < 2:
        return {
            "ok": False,
            "status": "insufficient_data",
            "message": "有效净值数据不足",
        }

    # Period returns（快照间隔可能是分钟级；仍给出可展示指标）
    rets: List[float] = []
    for i in range(1, len(equities)):
        e0, e1 = equities[i - 1], equities[i]
        if e0 and e0 > 0:
            rets.append(e1 / e0 - 1)

    if len(rets) < 1:
        return {"ok": False, "status": "insufficient_data", "message": "收益率数据不足"}

    import math
    import statistics

    n = len(rets)
    mean_ret = statistics.mean(rets)
    std_ret = statistics.stdev(rets) if n > 1 else 0.0
    unique_days = len({e.get("date") for e in eq_curve if e.get("date")})
    # 同日/极短样本：报期内夏普；跨日样本再年化
    ann_factor = 252.0 if unique_days > 1 else 1.0

    # Sharpe
    sharpe = (mean_ret / std_ret * math.sqrt(ann_factor)) if std_ret > 1e-12 else None

    # Sortino (downside deviation)
    downside_rets = [r for r in rets if r < 0]
    downside_std = statistics.stdev(downside_rets) if len(downside_rets) > 1 else (
        abs(downside_rets[0]) if len(downside_rets) == 1 else 0.0
    )
    sortino = (mean_ret / downside_std * math.sqrt(ann_factor)) if downside_std > 1e-12 else None

    # Annualized volatility（同日样本为期内波动）
    vol = std_ret * math.sqrt(ann_factor) * 100 if n > 1 else 0.0

    # Annualized return
    total_ret = equities[-1] / equities[0] - 1
    if unique_days > 1:
        years = max(n / 252.0, 1e-6)
        ann_ret = ((1 + total_ret) ** (1 / years) - 1) * 100 if years > 0 else None
    else:
        ann_ret = total_ret * 100

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = max_dd * 100

    # VaR / CVaR：样本少时用经验分位近似，避免整块空白
    sorted_rets = sorted(rets)
    if n >= 3:
        idx95 = max(0, int(n * 0.05))
        var_95 = -sorted_rets[idx95] * 100
        tail = sorted_rets[: idx95 + 1] or sorted_rets[:1]
        cvar_95 = -statistics.mean(tail) * 100 if tail else None
    else:
        var_95 = -min(rets) * 100
        cvar_95 = var_95

    # Calmar ratio
    calmar = (ann_ret / max_dd_pct) if max_dd_pct > 0 and ann_ret is not None else None

    # Win rate
    win_days = sum(1 for r in rets if r > 0)
    win_rate = win_days / n * 100

    return {
        "ok": True,
        "sample_count": n,
        "ann_return": round(ann_ret, 2) if ann_ret is not None else None,
        "ann_volatility": round(vol, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "max_drawdown": round(max_dd_pct, 2),
        "var_95": round(var_95, 2) if var_95 is not None else None,
        "cvar_95": round(cvar_95, 2) if cvar_95 is not None else None,
        "calmar": round(calmar, 2) if calmar is not None else None,
        "win_rate": round(win_rate, 2),
        "trading_days": n,
        "note": (
            "同日快照 · 期内指标（未年化）"
            if unique_days <= 1
            else ("短样本近似；非完整日频风险估计" if n < 20 else None)
        ),
    }


def _build_factor_exposure() -> Dict[str, Any]:
    """因子暴露分析：基于持仓的板块/风格暴露（含超限清单）。"""
    paper = _load_raw_paper()
    holdings = _holdings_from_paper(paper)

    if not holdings:
        return {"ok": False, "message": "暂无持仓数据"}

    # 优先复用风控暴露矩阵（含行业限额 over_limit）
    try:
        from core.risk.exposure import build_exposure_matrix

        matrix = build_exposure_matrix(paper) or {}
        if isinstance(matrix, dict) and (matrix.get("sectors") or matrix.get("ok")):
            sectors_raw = matrix.get("sectors") or []
            over = [
                s.get("name") or s.get("key")
                for s in sectors_raw
                if isinstance(s, dict) and s.get("over_limit")
            ]
            sectors = [
                {
                    "name": s.get("name") or s.get("key") or "其他",
                    "value": round(float(s.get("market_value") or 0), 2),
                    "pct": round(float(s.get("weight_pct") or 0), 2),
                    "over_limit": bool(s.get("over_limit")),
                }
                for s in sectors_raw
                if isinstance(s, dict)
            ]
            hhi = matrix.get("hhi")
            if hhi is None and sectors:
                hhi = sum((float(s.get("pct") or 0) / 100.0) ** 2 for s in sectors)
            return {
                "ok": True,
                "sectors": sectors,
                "total_value": round(float(matrix.get("equity") or matrix.get("total_value") or 0), 2),
                "sector_count": len(sectors),
                "hhi": round(float(hhi or 0), 4),
                "concentration": (
                    "high"
                    if float(hhi or 0) > 0.25
                    else "medium"
                    if float(hhi or 0) > 0.15
                    else "low"
                ),
                "holdings_count": len(holdings),
                "over_limit_sectors": [x for x in over if x],
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        pass

    # Sector exposure fallback
    sector_values: Dict[str, float] = {}
    total_value = 0.0
    try:
        from core.portfolio_optimize import load_sector_map

        smap = load_sector_map()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        smap = {}

    for h in holdings:
        mv = _holding_market_value(h)
        total_value += mv
        sector = _holding_sector(h, smap)
        sector_values[sector] = sector_values.get(sector, 0) + mv

    hhi = sum((v / max(total_value, 1)) ** 2 for v in sector_values.values())

    sectors = [
        {"name": name, "value": round(val, 2), "pct": round(val / max(total_value, 1) * 100, 2)}
        for name, val in sorted(sector_values.items(), key=lambda x: -x[1])
    ]

    return {
        "ok": True,
        "sectors": sectors,
        "total_value": round(total_value, 2),
        "sector_count": len(sectors),
        "hhi": round(hhi, 4),
        "concentration": "high" if hhi > 0.25 else "medium" if hhi > 0.15 else "low",
        "holdings_count": len(holdings),
        "over_limit_sectors": [],
    }

