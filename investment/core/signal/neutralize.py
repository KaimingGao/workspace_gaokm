"""横截面因子中性化（P47）：对 sub_scores 做 z-score / rank 后再加权。"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


def _zscore_to_scores(values: List[float], *, scale: float = 10.0) -> List[float]:
    n = len(values)
    if n < 2:
        return [50.0] * n
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    std = math.sqrt(var)
    if std < 1e-9:
        return [50.0] * n
    out: List[float] = []
    for x in values:
        z = (x - mean) / std
        out.append(max(10.0, min(95.0, 50.0 + z * scale)))
    return out


def _rank_to_scores(values: List[float]) -> List[float]:
    n = len(values)
    if n < 2:
        return [50.0] * n
    order = sorted(range(n), key=lambda i: values[i])
    out = [50.0] * n
    for rank, idx in enumerate(order):
        out[idx] = max(10.0, min(95.0, (rank + 0.5) / n * 100.0))
    return out


def _convert(values: List[float], *, method: str, zscore_scale: float) -> List[float]:
    if method == "rank":
        return _rank_to_scores(values)
    return _zscore_to_scores(values, scale=zscore_scale)


def _group_residual(
    items: List[dict],
    *,
    group_keys: List[str],
    factor_names: List[str],
) -> List[dict]:
    """按 group_keys 分桶，组内对 sub_scores 减均值后映回 50 附近。"""
    by_group: Dict[str, List[int]] = defaultdict(list)
    for i, it in enumerate(items):
        by_group[group_keys[i]].append(i)

    adjusted = [dict(it) for it in items]
    for idxs in by_group.values():
        if len(idxs) < 2:
            continue
        for fac in factor_names:
            vals: List[Optional[float]] = []
            for i in idxs:
                subs = items[i].get("sub_scores") or {}
                if fac in subs:
                    vals.append(float(subs[fac]))
                else:
                    vals.append(None)
            present = [v for v in vals if v is not None]
            if len(present) < 2:
                continue
            mean = sum(present) / len(present)
            for j, i in enumerate(idxs):
                if vals[j] is None:
                    continue
                subs = dict(adjusted[i].get("sub_scores") or {})
                subs[fac] = round(vals[j] - mean + 50.0, 4)
                adjusted[i]["sub_scores"] = subs
    return adjusted


def _size_bucket_keys(
    items: List[dict],
    *,
    n_buckets: int = 3,
) -> Tuple[Optional[List[str]], Dict[str, Any]]:
    """按 log(market_cap) 分位分桶；有效市值不足时返回 None（跳过 size residual）。"""
    caps: List[Tuple[int, float]] = []
    for i, it in enumerate(items):
        raw = it.get("market_cap")
        try:
            cap = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            cap = None
        if cap is not None and cap > 0:
            caps.append((i, math.log(cap)))

    meta: Dict[str, Any] = {
        "size_cap_count": len(caps),
        "size_buckets": int(n_buckets),
    }
    if len(caps) < max(3, int(n_buckets)):
        meta["size_residual_skipped"] = "insufficient_market_cap"
        return None, meta

    caps_sorted = sorted(caps, key=lambda x: x[1])
    n = len(caps_sorted)
    buckets = max(2, min(int(n_buckets or 3), n))
    keys = ["unknown"] * len(items)
    for rank, (idx, _log_cap) in enumerate(caps_sorted):
        # 等分位桶
        bucket = min(buckets - 1, int(rank * buckets / n))
        keys[idx] = f"size_{bucket}"
    meta["size_residual_applied"] = True
    return keys, meta


def apply_cross_section_neutralization(
    items: List[dict],
    *,
    weights: Optional[Dict[str, float]] = None,
    method: str = "zscore",
    min_samples: int = 3,
    zscore_scale: float = 10.0,
    industry_residual: bool = False,
    size_residual: bool = False,
    size_buckets: int = 3,
) -> Dict[str, Any]:
    """
    对一批 signal_item 的 sub_scores 做截面中性化，并重算 score / factor_contrib。
    industry_residual=True：先按 sector 减组内均值。
    size_residual=True：再按 log(market_cap) 分位桶减组内均值；市值不足则跳过。
    样本不足时不改动，返回 applied=False。
    """
    wmap = weights or {}
    if not items:
        return {"applied": False, "reason": "empty", "items": items}
    if len(items) < max(2, int(min_samples or 3)):
        return {
            "applied": False,
            "reason": f"样本不足({len(items)}<{min_samples})",
            "sample_count": len(items),
            "items": items,
        }

    method = (method or "zscore").strip().lower()
    if method not in ("zscore", "rank"):
        method = "zscore"

    factor_names = list(wmap.keys())
    work_items = items
    size_meta: Dict[str, Any] = {}

    if industry_residual:
        sec_keys = [
            str(it.get("sector") or it.get("industry") or "unknown") for it in items
        ]
        work_items = _group_residual(
            work_items, group_keys=sec_keys, factor_names=factor_names
        )

    if size_residual:
        bucket_keys, size_meta = _size_bucket_keys(
            items, n_buckets=int(size_buckets or 3)
        )
        if bucket_keys is not None:
            work_items = _group_residual(
                work_items, group_keys=bucket_keys, factor_names=factor_names
            )

    neutralized: List[Dict[str, float]] = [{} for _ in work_items]

    for fac in factor_names:
        vals: List[float] = []
        idxs: List[int] = []
        for i, item in enumerate(work_items):
            subs = item.get("sub_scores") or {}
            if fac not in subs:
                continue
            vals.append(float(subs[fac]))
            idxs.append(i)
        if len(vals) < 2:
            for i in idxs:
                neutralized[i][fac] = round(
                    float((work_items[i].get("sub_scores") or {}).get(fac, 50.0)), 1
                )
            continue
        converted = _convert(vals, method=method, zscore_scale=zscore_scale)
        for j, i in enumerate(idxs):
            neutralized[i][fac] = round(converted[j], 1)

    out_items: List[dict] = []
    for i, item in enumerate(work_items):
        patched = dict(item)
        raw_subs = dict(items[i].get("sub_scores") or {})
        patched["score_raw"] = items[i].get("score")
        patched["sub_scores_raw"] = raw_subs

        new_subs = dict(raw_subs)
        new_subs.update(neutralized[i])
        for fac in factor_names:
            if fac not in new_subs:
                new_subs[fac] = round(float(raw_subs.get(fac, 50.0)), 1)
        patched["sub_scores"] = new_subs

        contrib: Dict[str, float] = {}
        for fac, weight in wmap.items():
            if fac in new_subs:
                contrib[fac] = round(float(weight) * new_subs[fac], 2)
        total = sum(contrib.values())

        regime = item.get("regime") or {}
        penalty = float(regime.get("score_penalty") or 0)
        if penalty > 0:
            total -= penalty
            contrib["regime_penalty"] = round(-penalty, 2)

        patched["factor_contrib"] = contrib
        patched["score"] = round(max(0.0, min(100.0, total)), 1)
        patched["neutralization"] = {
            "applied": True,
            "method": method,
            "industry_residual": bool(industry_residual),
            "size_residual": bool(size_residual and size_meta.get("size_residual_applied")),
            "sample_count": len(items),
        }
        out_items.append(patched)

    return {
        "applied": True,
        "method": method,
        "industry_residual": bool(industry_residual),
        "size_residual": bool(size_residual and size_meta.get("size_residual_applied")),
        "size_residual_meta": size_meta,
        "sample_count": len(items),
        "factor_count": len(factor_names),
        "items": out_items,
    }
