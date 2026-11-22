"""Holdout 指标：日频截面 IC（对齐现网）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence


def daily_cs_ic_report(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[Any],
    *,
    min_names: int = 5,
) -> Dict[str, Any]:
    from core.research.daily_cs_ic import summarize_daily_cs_ic, summarize_cs_quantile_proxy

    ic = summarize_daily_cs_ic(preds, ys, metas, min_names=min_names)
    q = summarize_cs_quantile_proxy(preds, ys, metas, top_frac=0.1, min_names=min_names)
    return {
        "cs_ic": ic.get("cs_ic"),
        "cs_icir": ic.get("cs_icir"),
        "cs_rank_ic": ic.get("cs_rank_ic"),
        "cs_rank_icir": ic.get("cs_rank_icir"),
        "cs_day_count": ic.get("cs_day_count"),
        "cs_top_mean_y": q.get("cs_top_mean_y"),
        "cs_ls_mean_y": q.get("cs_ls_mean_y"),
        "cs_align": ic.get("cs_align"),
    }


def _ndcg_at_k(
    pred: Sequence[float],
    ys: Sequence[float],
    k: int,
) -> Optional[float]:
    """NDCG@k。gain 是收益的升序名次（0=最差），折扣 ``1/log2(position+1)``。"""
    n = len(pred)
    if n < 2 or k < 1:
        return None
    kk = min(int(k), n)
    order_by_ys = sorted(range(n), key=lambda i: (float(ys[i]), i))
    gain = [0.0] * n
    for rank, idx in enumerate(order_by_ys):
        gain[idx] = float(rank)
    pred_order = sorted(range(n), key=lambda i: (-float(pred[i]), i))
    dcg = 0.0
    for i, idx in enumerate(pred_order[:kk]):
        dcg += gain[idx] / math.log2(i + 2)
    ideal = sorted(range(n), key=lambda i: (-float(ys[i]), i))
    idcg = 0.0
    for i, idx in enumerate(ideal[:kk]):
        idcg += gain[idx] / math.log2(i + 2)
    if idcg <= 0.0:
        return None
    return float(dcg / idcg)


def mean_daily_ndcg(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[Any],
    *,
    k: int = 100,
    min_names: int = 2,
) -> Optional[float]:
    """按决策日算 NDCG@k，再对日取平均。不足 ``min_names`` 的截面跳过。"""
    from core.research.daily_cs_ic import _group_by_section

    days = _group_by_section(preds, ys, metas)
    scores: List[float] = []
    for pred_d, y_d in days.values():
        if len(pred_d) < int(min_names):
            continue
        nd = _ndcg_at_k(pred_d, y_d, int(k))
        if nd is None or nd != nd:
            continue
        scores.append(float(nd))
    if not scores:
        return None
    return float(sum(scores) / len(scores))


def pooled_pearson(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    from core.backtest.pool_ic import _pearson

    xs: List[float] = []
    zs: List[float] = []
    for p, y in zip(preds, ys):
        try:
            pf = float(p)
            yf = float(y)
        except (TypeError, ValueError):
            continue
        if math.isfinite(pf) and math.isfinite(yf):
            xs.append(pf)
            zs.append(yf)
    if len(xs) < 8:
        return None
    return _pearson(xs, zs)


def _finite_pair(p: Any, y: Any) -> Optional[tuple]:
    try:
        pf = float(p)
        yf = float(y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pf) or not math.isfinite(yf):
        return None
    return pf, yf


def holdout_mse(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Holdout 逐点均方误差。预测和标签都是百分点，不截断。"""
    acc = 0.0
    n = 0
    for p, y in zip(preds, ys):
        pair = _finite_pair(p, y)
        if pair is None:
            continue
        err = pair[0] - pair[1]
        acc += err * err
        n += 1
    if n <= 0:
        return None
    return round(acc / float(n), 6)


def sign_hit(
    preds: Sequence[float],
    ys: Sequence[float],
    *,
    deadband: float = 0.05,
) -> Optional[float]:
    """方向命中；|ŷ|<deadband 视为无方向（不计）。"""
    ok = 0
    n = 0
    for p, y in zip(preds, ys):
        try:
            pf = float(p)
            yf = float(y)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pf) or not math.isfinite(yf):
            continue
        if abs(pf) < float(deadband):
            continue
        n += 1
        if (pf > 0 and yf > 0) or (pf < 0 and yf < 0):
            ok += 1
    if n <= 0:
        return None
    return round(ok / float(n), 4)


def hard_gate_vs_baseline(
    base: Dict[str, Any],
    cand: Dict[str, Any],
    *,
    min_rank_ic_lift: float = 0.01,
    min_rel_lift: float = 0.20,
    icir_tol: float = 0.10,
) -> Dict[str, Any]:
    """相对基线的硬门（方案 §7）。"""
    b = base.get("cs_rank_ic")
    c = cand.get("cs_rank_ic")
    bi = base.get("cs_rank_icir")
    ci = cand.get("cs_rank_icir")
    reasons: List[str] = []
    if b is None or c is None:
        return {"pass": False, "applicable": True, "reasons": ["missing cs_rank_ic"]}
    try:
        bf = float(b)
        cf = float(c)
    except (TypeError, ValueError):
        return {"pass": False, "applicable": True, "reasons": ["non-finite cs_rank_ic"]}
    if cf <= 0.0:
        reasons.append(f"cand cs_rank_ic {cf:.4f} <= 0")
    abs_ok = (cf - bf) >= float(min_rank_ic_lift)
    rel_ok = False
    if abs(bf) > 1e-9:
        rel_ok = ((cf - bf) / abs(bf)) >= float(min_rel_lift)
    else:
        rel_ok = cf >= float(min_rank_ic_lift)
    if not (abs_ok or rel_ok):
        reasons.append(
            f"rank_ic lift {cf - bf:+.4f} < max(abs={min_rank_ic_lift}, rel={min_rel_lift})"
        )
    if bi is None or ci is None:
        reasons.append("missing cs_rank_icir")
    else:
        try:
            bif = float(bi)
            cif = float(ci)
            if bif > 0 and cif < bif * (1.0 - float(icir_tol)):
                reasons.append(f"rank_icir worsened {cif:.4f} < {bif:.4f}*(1-{icir_tol})")
            elif bif <= 0 and cif <= 0:
                reasons.append(f"rank_icir {cif:.4f} <= 0 while baseline <= 0")
        except (TypeError, ValueError):
            reasons.append("non-finite cs_rank_icir")
    return {
        "pass": not reasons,
        "applicable": True,
        "reasons": reasons,
        "base_cs_rank_ic": bf,
        "cand_cs_rank_ic": cf,
        "lift": round(cf - bf, 6),
    }


def gate_lift_vs_baseline(
    base: Dict[str, Any],
    cand: Dict[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    """排序头硬闸别名（HeadSpec.gate=gate_lift → CS rank IC lift）。"""
    return hard_gate_vs_baseline(base, cand, **kwargs)


__all__ = [
    "daily_cs_ic_report",
    "pooled_pearson",
    "holdout_mse",
    "sign_hit",
    "hard_gate_vs_baseline",
    "gate_lift_vs_baseline",
]
