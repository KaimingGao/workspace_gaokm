"""策略健康 / 衰减监控（N5）：只告警与建议，不自动改权。"""


import logging

logger = logging.getLogger(__name__)
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
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


def estimate_yhat_ic(
    bars: List[dict],
    *,
    stock_code: Optional[str] = None,
    horizon_days: int = 3,
    min_history: int = 20,
    max_points: int = 40,
) -> Dict[str, Any]:
    """组/全局 ReturnScoreModel ŷ 相对前瞻收益的 Pearson IC；无模型则跳过。"""
    from core.signal.scorer import score_bars

    horizon_days = max(1, min(int(horizon_days or 3), 5))
    n = len(bars or [])
    xs: List[float] = []
    ys: List[float] = []
    if n < min_history + horizon_days:
        return {
            "ok": False,
            "ic": None,
            "sample_count": 0,
            "note": "bars 不足",
            "source": "yhat",
        }

    model = None
    if stock_code:
        try:
            from core.signal.cluster.live import lookup_code_return_model

            model = lookup_code_return_model(str(stock_code))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
            model = None

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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
            continue
        if out.get("hard_reject"):
            continue
        pred = None
        if model is not None:
            try:
                pred = model.predict(out.get("sub_scores") or {})
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
                pred = None
        if pred is None:
            # 无组模型：跳过（不混 heuristic 以免污染 ŷ IC）
            continue
        c0 = bars[i].get("close")
        c1 = bars[i + horizon_days].get("close")
        if not c0:
            continue
        xs.append(float(pred))
        ys.append((float(c1) / float(c0) - 1.0) * 100.0)

    ic = pearson_ic(xs, ys)
    return {
        "ok": ic is not None,
        "ic": ic,
        "sample_count": len(xs),
        "horizon_days": horizon_days,
        "source": "yhat",
        "has_model": model is not None,
        "note": "ŷ（组 return_model）滚动 IC；只告警，不改权。",
    }


def estimate_rolling_yhat_ic_for_codes(
    codes: Sequence[str],
    *,
    limit: int = 3,
    lookback: int = 90,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """多标的 ŷ IC 均值。"""
    from core.data.facade import get_bars

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
            one = estimate_yhat_ic(
                bars, stock_code=code, horizon_days=horizon_days
            )
            details.append({"stock_code": code, **one})
            if one.get("ic") is not None:
                ics.append(float(one["ic"]))
        except Exception as e:
            logger.exception('unexpected error in estimate_rolling_yhat_ic_for_codes')
            details.append(
                {"stock_code": code, "ok": False, "ic": None, "error": str(e)}
            )

    avg = round(sum(ics) / len(ics), 4) if ics else None
    return {
        "ok": avg is not None,
        "rolling_ic": avg,
        "sample_codes": len(ics),
        "details": details,
        "note": "多标的 ŷ IC 均值；无组模型的票跳过。",
    }


def estimate_rolling_ic_for_codes(
    codes: Sequence[str],
    *,
    limit: int = 3,
    lookback: int = 90,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """对持仓/观察池前几只估滚动 IC，取均值（失败则跳过）。"""
    from core.data.facade import get_bars

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
            logger.exception('unexpected error in estimate_rolling_ic_for_codes')
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
    """行业 map 显式覆盖率；未收录 → 未分类（DS-R2，不再当板别冒充行业）。"""
    from core.portfolio_optimize import _board_for, _sector_for, load_sector_map, sector_map_coverage

    smap = load_sector_map()
    cov = sector_map_coverage(list(codes or []), sector_map=smap)
    sample = {}
    for c in (list(codes or [])[:12]):
        code = str(c or "").strip()
        if code:
            sample[code] = {
                "sector": _sector_for(code, smap),
                "board": _board_for(code),
            }
    return {
        "total": cov["total"],
        "mapped": cov["mapped"],
        "heuristic": cov["unmapped"],  # 兼容旧字段名
        "unmapped": cov["unmapped"],
        "coverage": cov["coverage"],
        "unmapped_codes": cov["unmapped_codes"][:12],
        "sectors": {k: v["sector"] for k, v in sample.items()},
        "boards": {k: v["board"] for k, v in sample.items()},
        "map_size": len(smap),
        "note": "未映射码行业=未分类；板别见 boards。",
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

    sid = paper.get("strategy_id") or "short_conservative"
    try:
        spec_risk = risk or (get_strategy_spec(str(sid)).get("risk") or {})
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
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

    # Y1.1：ŷ 滚动 IC（与启发式合成分分开）
    yhat_ic_pack: Optional[Dict[str, Any]] = None
    yhat_ic_val = None
    if compute_rolling_ic and code_list:
        try:
            yhat_ic_pack = estimate_rolling_yhat_ic_for_codes(code_list)
            yhat_ic_val = yhat_ic_pack.get("rolling_ic")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in strategy_monitor.py", exc_info=True)
            yhat_ic_pack = {"ok": False, "rolling_ic": None}
    if yhat_ic_val is not None:
        try:
            yic = float(yhat_ic_val)
        except (TypeError, ValueError):
            yic = None
        if yic is not None and yic < 0.0:
            alerts.append(
                {
                    "level": "warn",
                    "code": "yhat_ic_decay",
                    "message": f"滚动 ŷ IC={yic:.3f} 偏低，疑似组 β 衰减",
                }
            )
            suggestions.append("研究枢纽跑分组→对照重拟合；勿静默改 weights")

    coverage = sector_coverage_report(code_list)
    cov_r = coverage.get("coverage")
    if coverage.get("total") and cov_r is not None and float(cov_r) < 0.5:
        alerts.append(
            {
                "level": "info",
                "code": "sector_map_thin",
                "message": (
                    f"行业 map 显式覆盖 {coverage['mapped']}/{coverage['total']}"
                    f"（{float(cov_r):.0%}），限额精度偏弱"
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
            "yhat_rolling_ic": yhat_ic_val,
            "equity": equity,
            "sector_coverage": coverage,
        },
        "rolling_ic_detail": ic_pack,
        "yhat_ic_detail": yhat_ic_pack,
        "note": "N5 监控只告警；ŷ IC 与合成分 IC 分列；不自动改权、不代客下单。",
    }
