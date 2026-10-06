"""日频截面 IC（对齐 Qlib SigAnaRecord）。

Qlib:
  每日截面 Pearson → mean → IC
  每日截面 Spearman → mean → Rank IC
  ICIR = mean / std（日序列）

与 Holdout 拼样本 Pearson（chrono）不同；见 ``core.signal.ic_contract``。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest.factor_cs_ic import _agg_ic_series, _spearman
from core.backtest.pool_ic import _pearson
from core.signal.ic_contract import annotate_ic_block


def _meta_date(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    for key in ("date", "decision_date", "asof", "trade_date"):
        v = meta.get(key)
        if v not in (None, ""):
            return str(v)[:10]
    return ""


def _section_key(meta: Any) -> str:
    """截面键：决策日；ŷ_τ 多钟时按 date|τ，避免同票多 τ 灌进同一截面。"""
    d = _meta_date(meta)
    if not d:
        return ""
    if not isinstance(meta, dict):
        return d
    tau = str(meta.get("tau") or meta.get("tau_hm") or "").strip()
    if tau and tau.lower() not in ("", "open"):
        return f"{d}|{tau}"
    return d


def _stock_key(meta: Any, fallback: int) -> str:
    if isinstance(meta, dict):
        for key in ("code", "stock_code", "symbol"):
            v = meta.get(key)
            if v not in (None, ""):
                return str(v)
    return f"_{fallback}"


def _group_by_section(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[Any],
) -> Dict[str, Tuple[List[float], List[float]]]:
    buckets: Dict[str, Tuple[List[float], List[float]]] = {}
    seen: Dict[str, set] = {}
    n = min(len(preds), len(ys), len(metas))
    for i in range(n):
        p = preds[i]
        if p is None:
            continue
        try:
            pf = float(p)
            yf = float(ys[i])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pf) or not math.isfinite(yf):
            continue
        meta = metas[i]
        key = _section_key(meta)
        if not key:
            continue
        sk = _stock_key(meta, i)
        used = seen.setdefault(key, set())
        if sk in used:
            continue
        used.add(sk)
        xs, zs = buckets.setdefault(key, ([], []))
        xs.append(pf)
        zs.append(yf)
    return buckets


def summarize_daily_cs_ic(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[Any],
    *,
    min_names: int = 5,
) -> Dict[str, Any]:
    """按决策日截面相关，再对日平均（Qlib IC / Rank IC）。

    返回扁平字段，便于写入 ``oos`` 与实验 tracker::

        cs_ic, cs_icir, cs_rank_ic, cs_rank_icir, cs_day_count, ...
    """
    min_names = max(2, int(min_names or 5))
    by_sec = _group_by_section(preds, ys, metas)
    pearson_daily: List[float] = []
    spearman_daily: List[float] = []
    daily_rows: List[Dict[str, Any]] = []
    for sec in sorted(by_sec):
        xs, zs = by_sec[sec]
        if len(xs) < min_names:
            continue
        p = _pearson(xs, zs)
        s = _spearman(xs, zs)
        if p is not None:
            pearson_daily.append(float(p))
        if s is not None:
            spearman_daily.append(float(s))
        if p is not None or s is not None:
            daily_rows.append(
                {
                    "date": sec.split("|", 1)[0],
                    "section": sec,
                    "n_names": len(xs),
                    "ic": round(p, 4) if p is not None else None,
                    "rank_ic": round(s, 4) if s is not None else None,
                }
            )

    pear = _agg_ic_series(pearson_daily)
    spear = _agg_ic_series(spearman_daily)
    pear_ann = annotate_ic_block(pear, kind="cs_pearson", primary=False)
    spear_ann = annotate_ic_block(spear, kind="cs_spearman", primary=True)
    ok = pear.get("ic_mean") is not None or spear.get("ic_mean") is not None
    return {
        "ok": ok,
        "cs_ic": pear.get("ic_mean"),
        "cs_ic_std": pear.get("ic_std"),
        "cs_icir": pear.get("icir"),
        "cs_rank_ic": spear.get("ic_mean"),
        "cs_rank_ic_std": spear.get("ic_std"),
        "cs_rank_icir": spear.get("icir"),
        "cs_day_count": max(int(pear.get("day_count") or 0), int(spear.get("day_count") or 0)),
        "cs_positive_ic_ratio": pear.get("positive_ic_ratio"),
        "cs_positive_rank_ic_ratio": spear.get("positive_ic_ratio"),
        "cs_min_names": min_names,
        "cs_ic_kind": "cs_pearson",
        "cs_rank_ic_kind": "cs_spearman",
        "cs_align": "qlib_sigana",
        "cs_note": (
            "对齐 Qlib SigAnaRecord：日频截面 Pearson 均值=IC，"
            "Spearman 均值=Rank IC；ICIR=mean/std。"
        ),
        "cs_pearson": pear_ann,
        "cs_spearman": spear_ann,
        "cs_daily_tail": daily_rows[-40:],
    }


def attach_daily_cs_ic(
    oos: Dict[str, Any],
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[Any],
    *,
    min_names: int = 5,
) -> Dict[str, Any]:
    """把日频截面 IC 写入 oos；标注既有 ``ic`` 为 chrono_pearson。"""
    out = oos if isinstance(oos, dict) else {}
    if out.get("ic") is not None and out.get("ic_kind") is None:
        annotated = annotate_ic_block(
            {"ic": out.get("ic")},
            kind="chrono_pearson",
            primary=False,
        )
        out["ic_kind"] = annotated.get("ic_kind")
        out["ic_label"] = annotated.get("ic_label")
        out["ic_role"] = annotated.get("ic_role")
        out["ic_note"] = annotated.get("ic_note")
        out["is_primary_ic"] = False
    pack = summarize_daily_cs_ic(preds, ys, metas, min_names=min_names)
    for key, val in pack.items():
        if key in ("cs_pearson", "cs_spearman", "cs_daily_tail"):
            out[key] = val
        elif key.startswith("cs_") or key == "ok":
            if key == "ok":
                out["cs_ok"] = val
            else:
                out[key] = val
    return out
