"""Holdout ŷ 指标探针（分组 partition_loss 主体已随 cluster OLS 退役）。

仅保留 `_metrics_from_pred_act`，供 IC 口径单测与研究脚本复用。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

# 与 τ `_sign_hit` 同门槛：|ŷ|<0.05% 无方向，不进开盘命中分母
SIGN_HIT_MIN_ABS = 0.05
SIGN_HIT_MIN_N = 5


def _empty_yhat_metrics(holdout_ratio: float) -> Dict[str, Any]:
    return {
        "ok": False,
        "n": 0,
        "ic": None,
        "rmse": None,
        "mse": None,
        "r2": None,
        "sign_hit": None,
        "n_signed": 0,
        "holdout_ratio": float(holdout_ratio),
    }


def _sign_hit_from_pred_act(
    preds: Sequence[float],
    acts: Sequence[float],
    *,
    min_abs: float = SIGN_HIT_MIN_ABS,
    min_n: int = SIGN_HIT_MIN_N,
) -> Tuple[Optional[float], int]:
    """sign(ŷ)=sign(y)；|ŷ|<min_abs 无方向，跳过。返回 (命中率, 有方向 n)。"""
    hits = 0
    n = 0
    thr = float(min_abs)
    for p, y in zip(preds, acts):
        try:
            pv = float(p)
            yv = float(y)
        except (TypeError, ValueError):
            continue
        if abs(pv) < thr:
            continue
        n += 1
        if (pv > 0 and yv > 0) or (pv < 0 and yv < 0):
            hits += 1
    if n < int(min_n):
        return None, int(n)
    return round(hits / float(n), 4), int(n)


def _metrics_from_pred_act(
    preds: Sequence[float],
    acts: Sequence[float],
    *,
    holdout_ratio: float,
    n_full: int,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    from core.signal.factors.meta.corr import pearson_with_reason

    empty = _empty_yhat_metrics(holdout_ratio)
    p_h = [float(v) for v in preds]
    a_h = [float(v) for v in acts]
    n = min(len(p_h), len(a_h))
    if n < 4:
        return {**empty, "n": n, "n_full": int(n_full), "reason": reason or "too_few_holdout"}
    p_h, a_h = p_h[:n], a_h[:n]
    ic, _reason = pearson_with_reason(p_h, a_h)
    err = [float(p_h[i]) - float(a_h[i]) for i in range(n)]
    mse = float(sum(e * e for e in err) / max(1, len(err)))
    rmse = float(math.sqrt(mse))
    y_bar = float(sum(a_h) / len(a_h))
    ss_tot = sum((float(a_h[i]) - y_bar) ** 2 for i in range(n))
    ss_res = sum(e * e for e in err)
    r2 = None
    if ss_tot > 1e-12:
        r2 = float(max(0.0, min(1.0, 1.0 - ss_res / ss_tot)))
    sign_hit, n_signed = _sign_hit_from_pred_act(p_h, a_h)
    try:
        from core.signal.ic_contract import annotate_ic_block

        _ic_ann = annotate_ic_block(
            {"ic": None if ic is None else round(float(ic), 4)},
            kind="chrono_pearson",
            primary=False,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        _ic_ann = {
            "ic": None if ic is None else round(float(ic), 4),
            "ic_kind": "chrono_pearson",
            "is_primary_ic": False,
        }
    return {
        "ok": True,
        "n": int(n),
        "n_full": int(n_full),
        "ic": _ic_ann.get("ic"),
        "ic_kind": _ic_ann.get("ic_kind"),
        "ic_label": _ic_ann.get("ic_label"),
        "ic_role": _ic_ann.get("ic_role"),
        "is_primary_ic": False,
        "rmse": round(rmse, 4),
        "mse": round(mse, 6),
        "sign_hit": sign_hit,
        "n_signed": int(n_signed),
        "r2": None if r2 is None else round(float(r2), 4),
        "holdout_ratio": float(holdout_ratio),
        "reason": reason,
    }
