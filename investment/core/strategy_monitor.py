"""策略健康 / 衰减监控（N5）：只告警与建议，不自动改权。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence


def pearson_ic(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x <= 1e-12 or den_y <= 1e-12:
        return None
    return round(num / (den_x * den_y), 4)


def estimate_composite_ic(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 20,
    max_points: int = 40,
) -> Dict[str, Any]:
    """合成分（score_bars）相对前瞻收益的 Pearson IC；离线、不写配置。"""
    from core.signal.scorer import score_bars

    horizon_days = max(1, min(int(horizon_days or 3), 5))
    n = len(bars or [])
    xs: List[float] = []
    ys: List[float] = []
    if n < min_history + horizon_days:
        return {"ok": False, "ic": None, "sample_count": 0, "note": "bars 不足"}

    start_i = max(min_history - 1, n - horizon_days - max_points)
    for i in range(start_i, n - horizon_days):
        window = bars[: i + 1]
        quote = {"change_raw": 0.0, "price_raw": bars[i]["close"]}
        if i >= 1 and bars[i - 1].get("close"):
            quote["change_raw"] = round(
                (bars[i]["close"] / bars[i - 1]["close"] - 1.0) * 100.0, 4
            )
        try:
            out = score_bars(window, quote=quote, fundamentals=None, sentiment=None)
        except Exception:
            continue
        if out.get("hard_reject") or out.get("score") is None:
            continue
        c0 = bars[i].get("close")
        c1 = bars[i + horizon_days].get("close")
        if not c0:
            continue
        xs.append(float(out["score"]))
        ys.append((float(c1) / float(c0) - 1.0) * 100.0)

    ic = pearson_ic(xs, ys)
    return {
        "ok": ic is not None,
        "ic": ic,
        "sample_count": len(xs),
        "horizon_days": horizon_days,
        "note": "合成 score 滚动 IC；只告警，不改权。",
    }


def estimate_rolling_ic_for_codes(
    codes: Sequence[str],
    *,
    limit: int = 3,
    lookback: int = 90,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """对持仓/观察池前几只估滚动 IC，取均值（失败则跳过）。"""
    from core.data_service import get_bars

    ics: List[float] = []
    details: List[Dict[str, Any]] = []
    seen = set()
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if len(seen) > max(1, int(limit or 3)):
            break
        try:
            pack = get_bars(code, limit=lookback)
            bars = (pack or {}).get("bars") or []
            one = estimate_composite_ic(bars, horizon_days=horizon_days)
            details.append({"stock_code": code, **one})
            if one.get("ic") is not None:
                ics.append(float(one["ic"]))
        except Exception as e:
            details.append({"stock_code": code, "ok": False, "ic": None, "error": str(e)})

    avg = round(sum(ics) / len(ics), 4) if ics else None
    return {
        "ok": avg is not None,
        "rolling_ic": avg,
        "sample_codes": len(ics),
        "details": details,
        "note": "多标的合成分 IC 均值；网络/缓存失败时跳过。",
    }


def sector_coverage_report(codes: Sequence[str]) -> Dict[str, Any]:
    """行业 map 显式覆盖率；未收录的走启发式板块（限额精度弱）。"""
    from core.portfolio_optimize import _sector_for, load_sector_map

    smap = load_sector_map()
    mapped: List[str] = []
    heuristic: List[str] = []
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        if code in smap:
            mapped.append(code)
        else:
            heuristic.append(code)
    total = len(mapped) + len(heuristic)
    coverage = round(len(mapped) / total, 3) if total else 1.0
    sample_sectors = {c: _sector_for(c, smap) for c in (mapped + heuristic)[:12]}
    return {
        "total": total,
        "mapped": len(mapped),
        "heuristic": len(heuristic),
        "coverage": coverage,
        "unmapped_codes": heuristic[:12],
        "sectors": sample_sectors,
        "map_size": len(smap),
    }


def _codes_from_paper(paper: Optional[dict]) -> List[str]:
    paper = paper or {}
    out: List[str] = []
    seen = set()
    for h in paper.get("holdings") or []:
        c = str(h.get("stock_code") or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def assess_strategy_health(
    paper: Optional[dict] = None,
    *,
    summary: Optional[dict] = None,
    risk: Optional[dict] = None,
    rolling_ic: Optional[float] = None,
    compute_rolling_ic: bool = False,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """根据纸面回撤、持仓、可选滚动 IC、行业覆盖产出告警。"""
    from core.strategy import get_strategy_spec

    paper = paper or {}
    sm = summary or {}
    alerts: List[Dict[str, str]] = []
    suggestions: List[str] = []

    sid = paper.get("strategy_id") or "short"
    try:
        spec_risk = risk or (get_strategy_spec(str(sid)).get("risk") or {})
    except Exception:
        spec_risk = risk or {}

    max_dd = float(spec_risk.get("max_drawdown_pct") or 20.0)
    target_dd = float(spec_risk.get("target_drawdown_pct") or max_dd * 0.6)
    dd = sm.get("max_drawdown_pct")
    try:
        dd_f = float(dd) if dd is not None else None
    except (TypeError, ValueError):
        dd_f = None

    if dd_f is not None:
        if dd_f >= max_dd:
            alerts.append(
                {
                    "level": "block",
                    "code": "drawdown_limit",
                    "message": f"回撤 {dd_f}% 已达熔断限额 {max_dd}%",
                }
            )
            suggestions.append("暂停加仓并复盘 StrategySpec.risk / 因子权重（人审）")
        elif dd_f >= target_dd:
            alerts.append(
                {
                    "level": "warn",
                    "code": "drawdown_target",
                    "message": f"回撤 {dd_f}% 触及目标预警 {target_dd}%",
                }
            )
            suggestions.append("检查行业集中度与单票仓位；可跑 feedback/suggest")

    code_list = list(codes) if codes is not None else _codes_from_paper(paper)
    ic_pack: Optional[Dict[str, Any]] = None
    ic_val = rolling_ic
    if ic_val is None and compute_rolling_ic and code_list:
        try:
            ic_pack = estimate_rolling_ic_for_codes(code_list)
            ic_val = ic_pack.get("rolling_ic")
        except Exception:
            ic_pack = {"ok": False, "rolling_ic": None}

    if ic_val is not None:
        try:
            ic = float(ic_val)
        except (TypeError, ValueError):
            ic = None
        if ic is not None and ic < 0.02:
            alerts.append(
                {
                    "level": "warn",
                    "code": "ic_decay",
                    "message": f"滚动 IC={ic:.3f} 偏低，疑似因子衰减",
                }
            )
            suggestions.append("导出 weight_suggest diff，人审后 promote；勿自动改 signal_config")

    coverage = sector_coverage_report(code_list)
    if coverage["total"] > 0 and coverage["coverage"] < 0.5:
        alerts.append(
            {
                "level": "info",
                "code": "sector_map_thin",
                "message": (
                    f"行业 map 显式覆盖 {coverage['mapped']}/{coverage['total']}"
                    f"（{coverage['coverage']:.0%}），限额精度偏弱"
                ),
            }
        )
        suggestions.append("补全 data/sector_map.json 后行业集中度拦截更准")

    equity = sm.get("equity")
    cash = paper.get("cash")
    try:
        if equity is not None and cash is not None and float(equity) > 0:
            cash_pct = float(cash) / float(equity) * 100.0
            if cash_pct > 80:
                alerts.append(
                    {
                        "level": "info",
                        "code": "high_cash",
                        "message": f"现金占比 {cash_pct:.0f}%，组合偏空仓",
                    }
                )
    except (TypeError, ValueError):
        pass

    level = "ok"
    if any(a["level"] == "block" for a in alerts):
        level = "block"
    elif any(a["level"] == "warn" for a in alerts):
        level = "warn"
    elif alerts:
        level = "info"

    return {
        "ok": level != "block",
        "level": level,
        "alerts": alerts,
        "suggestions": suggestions,
        "metrics": {
            "max_drawdown_pct": dd_f,
            "target_drawdown_pct": target_dd,
            "max_drawdown_limit_pct": max_dd,
            "rolling_ic": ic_val,
            "equity": equity,
            "sector_coverage": coverage,
        },
        "rolling_ic_detail": ic_pack,
        "note": "N5 监控只告警；不自动改权、不代客下单。",
    }
