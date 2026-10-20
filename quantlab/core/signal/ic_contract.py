"""IC 契约标签（P1）：主 IC = 日频截面 Spearman；其它为辅并打 kind。

禁止把时序 Pearson、全样本回放 IC 与主 IC 混称为同一个「IC」。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

# 主验收口径
PRIMARY_IC_KIND = "cs_spearman"
PRIMARY_IC_LABEL = "日频截面 Spearman"

IC_KIND_META: Dict[str, Dict[str, str]] = {
    "cs_spearman": {
        "label": "日频截面 Spearman",
        "role": "primary",
        "note": "每日横截面秩相关再对日平均；对齐 Qlib Rank IC；选股排序主验收。",
    },
    "cs_pearson": {
        "label": "日频截面 Pearson",
        "role": "aux",
        "note": "每日横截面线性相关再对日平均；对齐 Qlib IC；辅看，易受极端值影响。",
    },
    "chrono_pearson": {
        "label": "时序拼样本 Pearson",
        "role": "aux",
        "note": "Holdout ŷ vs y 拼样本；易被共同日冲击灌水，≠ Qlib / 截面选股 IC。",
    },
    "chrono_spearman": {
        "label": "时序 Spearman",
        "role": "aux",
        "note": "单票/拼样本秩相关；辅看。",
    },
    "full_sample_replay": {
        "label": "全样本回放",
        "role": "display_only",
        "note": "概览展示用；不是 OOS，不可做人审 promote 唯一依据。",
    },
    "tau_spearman": {
        "label": "τ Spearman",
        "role": "aux_tau",
        "note": "ŷ_τ 头 IC；与 EOD 主 IC 分轨，勿并排成同一数字。",
    },
}


def annotate_ic_block(
    block: Optional[dict],
    *,
    kind: str,
    primary: bool = False,
) -> Dict[str, Any]:
    """给已有 IC 字典打上 kind / role / label。"""
    meta = IC_KIND_META.get(kind) or {
        "label": kind,
        "role": "unknown",
        "note": "",
    }
    out = dict(block or {})
    out["ic_kind"] = kind
    out["ic_label"] = meta["label"]
    out["ic_role"] = "primary" if primary else meta["role"]
    out["ic_note"] = meta.get("note") or ""
    out["is_primary_ic"] = bool(primary)
    return out


def primary_ic_from_cs_factor_row(row: dict) -> Dict[str, Any]:
    """从 factor_cs_ic 单行提取主 IC（优先 spearman）。"""
    spear = (row or {}).get("spearman") or {}
    pear = (row or {}).get("pearson") or {}
    ic = spear.get("ic_mean")
    icir = spear.get("icir")
    src = "spearman"
    if ic is None:
        ic = pear.get("ic_mean")
        icir = pear.get("icir")
        src = "pearson"
    kind = "cs_spearman" if src == "spearman" else "cs_pearson"
    return annotate_ic_block(
        {
            "ic": ic,
            "icir": icir,
            "day_count": spear.get("day_count") or pear.get("day_count"),
            "source_agg": src,
        },
        kind=kind,
        primary=(kind == PRIMARY_IC_KIND),
    )


def ic_contract_banner() -> Dict[str, Any]:
    return {
        "primary_ic_kind": PRIMARY_IC_KIND,
        "primary_ic_label": PRIMARY_IC_LABEL,
        "kinds": dict(IC_KIND_META),
        "note": "人审/选模看 primary；chrono_* 与 full_sample_replay 不得单独宣称模型有效。",
    }


# Holdout ŷ 探针（原 quant.research.partition_loss；分组 OLS 已退役）
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


def metrics_from_pred_act(
    preds: Sequence[float],
    acts: Sequence[float],
    *,
    holdout_ratio: float,
    n_full: int,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Holdout ŷ vs y：chrono Pearson IC + RMSE / sign_hit（辅口径，非主 IC）。"""
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
    _ic_ann = annotate_ic_block(
        {"ic": None if ic is None else round(float(ic), 4)},
        kind="chrono_pearson",
        primary=False,
    )
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
