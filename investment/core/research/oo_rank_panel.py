"""ŷ_oo_rank 截面面板：按决策日聚合观察池 (X, y_oo)。

标签与 ŷ_oo 同源：open[T+h]/open[T]−1（默认 h=1，百分点）。
特征：原始 sub_score + 横截面 cs_rank / cs_zscore；``feature_mode`` 消融选列。
仅供 pairwise LTR 研究 / 影子跑路；不进 live ranking。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FEATURE_MODES = ("raw", "cs_rank", "cs_z", "raw_cs")
DEFAULT_FEATURE_MODE = "raw"
_CS_RANK_SUFFIX = "_cs_rank"
_CS_Z_SUFFIX = "_cs_zscore"


def _is_cs_key(name: str) -> bool:
    n = str(name or "")
    return n.endswith(_CS_RANK_SUFFIX) or n.endswith(_CS_Z_SUFFIX)


def _raw_feature_keys(xs: Sequence[dict]) -> List[str]:
    keys: List[str] = []
    seen = set()
    for row in xs or []:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if k in seen or _is_cs_key(str(k)):
                continue
            if isinstance(v, (int, float)) and not (
                isinstance(v, float) and math.isnan(v)
            ):
                seen.add(k)
                keys.append(str(k))
    return keys


def add_cross_sectional_features(
    bucket: Dict[str, Any],
    *,
    feature_keys: Optional[List[str]] = None,
) -> None:
    """对每个原始特征列，添加当天池内横截面 rank (0~1) 和 zscore。

    修改 bucket["xs"] in-place。跳过已有 ``*_cs_*`` 列，避免递归扩维。
    """
    xs = bucket.get("xs") or []
    n = len(xs)
    if n < 2:
        return

    if feature_keys is None:
        feature_keys = _raw_feature_keys(xs)
    else:
        feature_keys = [k for k in feature_keys if not _is_cs_key(str(k))]
    if not feature_keys:
        return

    for feat in feature_keys:
        vals: List[float] = []
        for x in xs:
            v = (x or {}).get(feat) if isinstance(x, dict) else None
            if isinstance(v, (int, float)) and not (
                isinstance(v, float) and math.isnan(v)
            ):
                vals.append(float(v))
            else:
                vals.append(0.0)

        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = math.sqrt(var) if var > 0 else 1.0

        sorted_idx = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        for rank_pos, idx in enumerate(sorted_idx):
            ranks[idx] = rank_pos / max(1, n - 1)

        for i, x in enumerate(xs):
            if not isinstance(x, dict):
                continue
            x[f"{feat}{_CS_RANK_SUFFIX}"] = round(ranks[i], 4)
            x[f"{feat}{_CS_Z_SUFFIX}"] = round((vals[i] - mean) / std, 4)


# 兼容旧名
_add_cross_sectional_features = add_cross_sectional_features


def normalize_feature_mode(mode: Optional[str]) -> str:
    m = str(mode or DEFAULT_FEATURE_MODE).strip().lower()
    if m in ("cs_zscore", "csz", "z"):
        m = "cs_z"
    if m not in FEATURE_MODES:
        return DEFAULT_FEATURE_MODE
    return m


def apply_feature_mode_to_row(row: dict, feature_mode: str) -> dict:
    """按 feature_mode 过滤单行特征列（返回新 dict）。"""
    mode = normalize_feature_mode(feature_mode)
    src = row if isinstance(row, dict) else {}
    if mode == "raw":
        return {k: v for k, v in src.items() if not _is_cs_key(str(k))}
    if mode == "cs_rank":
        return {k: v for k, v in src.items() if str(k).endswith(_CS_RANK_SUFFIX)}
    if mode == "cs_z":
        return {k: v for k, v in src.items() if str(k).endswith(_CS_Z_SUFFIX)}
    # raw_cs
    return dict(src)


def apply_feature_mode_to_xs(
    xs: Sequence[dict],
    feature_mode: str,
) -> List[dict]:
    """就地过滤并返回 xs 列表。"""
    mode = normalize_feature_mode(feature_mode)
    out: List[dict] = []
    for row in xs or []:
        if not isinstance(row, dict):
            out.append({})
            continue
        filtered = apply_feature_mode_to_row(row, mode)
        row.clear()
        row.update(filtered)
        out.append(row)
    return out


def enrich_day_panel_features(
    day: Dict[str, Any],
    *,
    feature_mode: str = DEFAULT_FEATURE_MODE,
) -> Dict[str, Any]:
    """日截面：补 cs_* 再按 feature_mode 选列。"""
    if not isinstance(day, dict):
        return day
    add_cross_sectional_features(day)
    apply_feature_mode_to_xs(day.get("xs") or [], feature_mode)
    return day


def enrich_day_panels_features(
    days: Sequence[Dict[str, Any]],
    *,
    feature_mode: str = DEFAULT_FEATURE_MODE,
) -> List[Dict[str, Any]]:
    mode = normalize_feature_mode(feature_mode)
    out: List[Dict[str, Any]] = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        bucket = {
            "date": day.get("date"),
            "codes": list(day.get("codes") or []),
            "xs": [dict(x) if isinstance(x, dict) else {} for x in (day.get("xs") or [])],
            "ys": list(day.get("ys") or []),
        }
        enrich_day_panel_features(bucket, feature_mode=mode)
        out.append(bucket)
    return out


def attach_cs_features_to_rows(rows: Sequence[dict]) -> List[dict]:
    """批内（同日观察池）挂 cs_*；返回新行列表，不改调用方原 dict 引用语义。"""
    xs = [dict(r) if isinstance(r, dict) else {} for r in (rows or [])]
    bucket = {"xs": xs}
    add_cross_sectional_features(bucket)
    return xs


def build_oo_rank_day_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
    min_names: int = 8,
    index_bars: Optional[List[dict]] = None,
    excess_mode: str = "none",
    respect_regime: bool = False,
    config: Optional[dict] = None,
    feature_mode: str = DEFAULT_FEATURE_MODE,
) -> List[Dict[str, Any]]:
    """返回按日截面列表。

    每项::
        {
          "date": "YYYY-MM-DD",
          "codes": [str, ...],
          "xs": [dict, ...],   # 按 feature_mode 选列后的特征
          "ys": [float, ...],  # y_oo %
        }
    """
    from core.research.panel import collect_subscore_forward_panel

    mode = normalize_feature_mode(feature_mode)
    by_date: Dict[str, Dict[str, Any]] = {}
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if not code or len(bars) < int(min_history) + int(horizon_days) + 1:
            continue
        idx = index_bars if index_bars is not None else item.get("index_bars")
        try:
            xs, ys, dates = collect_subscore_forward_panel(
                bars,
                horizon_days=int(horizon_days),
                min_history=int(min_history),
                index_bars=idx if isinstance(idx, list) else None,
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                pit_fundamentals=True,
                respect_regime=bool(respect_regime),
                config=config,
                excess_mode=str(excess_mode or "none"),
            )
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank panel collect failed for %s", code, exc_info=True)
            continue
        for x, y, d in zip(xs, ys, dates):
            day = str(d or "")[:10]
            if len(day) < 10:
                continue
            bucket = by_date.get(day)
            if bucket is None:
                bucket = {"date": day, "codes": [], "xs": [], "ys": []}
                by_date[day] = bucket
            bucket["codes"].append(code)
            bucket["xs"].append(dict(x) if isinstance(x, dict) else {})
            try:
                bucket["ys"].append(float(y))
            except (TypeError, ValueError):
                bucket["codes"].pop()
                bucket["xs"].pop()

    out: List[Dict[str, Any]] = []
    min_n = max(4, int(min_names or 8))
    for day in sorted(by_date.keys()):
        bucket = by_date[day]
        n = len(bucket["ys"])
        if n < min_n:
            continue
        enrich_day_panel_features(bucket, feature_mode=mode)
        out.append(bucket)
    return out


def stack_day_panels(
    days: Sequence[Dict[str, Any]],
) -> Tuple[List[dict], List[float], List[str], List[str]]:
    """展平为 pointwise 行（供 Ridge 对照臂）。"""
    xs: List[dict] = []
    ys: List[float] = []
    dates: List[str] = []
    codes: List[str] = []
    for day in days or []:
        d = str(day.get("date") or "")[:10]
        for code, x, y in zip(day.get("codes") or [], day.get("xs") or [], day.get("ys") or []):
            xs.append(dict(x) if isinstance(x, dict) else {})
            ys.append(float(y))
            dates.append(d)
            codes.append(str(code))
    return xs, ys, dates, codes


def feature_mode_meta(
    feature_names: Sequence[str],
    coefficients: Optional[Dict[str, Any]] = None,
    *,
    feature_mode: str = DEFAULT_FEATURE_MODE,
) -> Dict[str, Any]:
    """报告用：列数与 cs_* 系数占比。"""
    names = [str(n) for n in (feature_names or [])]
    n_cs_rank = sum(1 for n in names if n.endswith(_CS_RANK_SUFFIX))
    n_cs_z = sum(1 for n in names if n.endswith(_CS_Z_SUFFIX))
    n_raw = sum(1 for n in names if not _is_cs_key(n))
    coefs = coefficients if isinstance(coefficients, dict) else {}
    abs_sum = 0.0
    abs_cs = 0.0
    for k, v in coefs.items():
        try:
            av = abs(float(v))
        except (TypeError, ValueError):
            continue
        abs_sum += av
        if _is_cs_key(str(k)):
            abs_cs += av
    cs_coef_share = round(abs_cs / abs_sum, 4) if abs_sum > 1e-12 else None
    return {
        "feature_mode": normalize_feature_mode(feature_mode),
        "n_features": len(names),
        "n_raw_features": n_raw,
        "n_cs_rank_features": n_cs_rank,
        "n_cs_z_features": n_cs_z,
        "cs_coef_share": cs_coef_share,
    }


__all__ = [
    "DEFAULT_FEATURE_MODE",
    "FEATURE_MODES",
    "add_cross_sectional_features",
    "apply_feature_mode_to_row",
    "apply_feature_mode_to_xs",
    "attach_cs_features_to_rows",
    "build_oo_rank_day_panels",
    "enrich_day_panel_features",
    "enrich_day_panels_features",
    "feature_mode_meta",
    "normalize_feature_mode",
    "stack_day_panels",
]
