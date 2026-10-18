"""ŷ_co 单票/截面 Z 的统一前缀 ``z_co_*``。

训练与新模型系数键用 canon；面板/live 仍可写旧裸名（``gap_pct`` / ``ret_oc`` 等）。
``feature_get`` / ``stamp_z_co_aliases`` 负责双向别名，旧 ``co_ridge_v1`` 可继续打分。
``raw_aoo_*`` 不改前缀。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

Z_CO_PREFIX = "z_co_"

# 与 yhat.keys.CO_Z_LEGACY / 历史裸名对齐（含隔夜滞后）
Z_CO_LEGACY_KEYS: Tuple[str, ...] = (
    "ret_oc",
    "gap_pct",
    "ret_cc",
    "y_on_today",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "yest_close_loc",
    "yest_vol_ratio",
    "dist_to_up_limit",
    "yest_gap",
    "on_ma5",
)
Z_CO_LEGACY_SET = frozenset(Z_CO_LEGACY_KEYS)

Z_CO_FIT_FEATURES: Tuple[str, ...] = tuple(
    f"{Z_CO_PREFIX}{k}" for k in Z_CO_LEGACY_KEYS
)


def is_z_co_key(name: str) -> bool:
    return str(name or "").startswith(Z_CO_PREFIX)


def to_z_co_key(legacy_or_canon: str) -> str:
    k = str(legacy_or_canon or "").strip()
    if not k:
        return k
    if is_z_co_key(k):
        return k
    return f"{Z_CO_PREFIX}{k}"


def to_legacy_key(canon_or_legacy: str) -> str:
    k = str(canon_or_legacy or "").strip()
    if k.startswith(Z_CO_PREFIX):
        return k[len(Z_CO_PREFIX) :]
    return k


def stamp_z_co_aliases(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """就地：裸名 ↔ ``z_co_*``。

    优先级：裸名 > z_co_*。选定后强制写回两侧；须在截面 Z 之前调用。
    """
    out = row if isinstance(row, dict) else {}
    for leg in Z_CO_LEGACY_KEYS:
        canon = f"{Z_CO_PREFIX}{leg}"
        lv = out.get(leg)
        cv = out.get(canon)
        src = None
        if lv is not None and lv != "":
            src = lv
        elif cv is not None and cv != "":
            src = cv
        if src is None:
            continue
        out[canon] = src
        out[leg] = src
    return out


def stamp_z_co_rows(rows: Sequence[dict]) -> None:
    for row in rows or []:
        if isinstance(row, dict):
            stamp_z_co_aliases(row)


def feature_get(row: Optional[Dict[str, Any]], name: str) -> Any:
    """预测查找：先原名，再别名。"""
    src = row if isinstance(row, dict) else {}
    key = str(name or "")
    if not key:
        return None
    v = src.get(key)
    if v is not None and v != "":
        return v
    alt = to_legacy_key(key) if is_z_co_key(key) else to_z_co_key(key)
    if alt and alt != key:
        v2 = src.get(alt)
        if v2 is not None and v2 != "":
            return v2
    return None


def z_co_feat_labels(base_labels: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """旧中文标签挂到 ``z_co_*`` 键上。"""
    out: Dict[str, str] = {}
    for k, lab in (base_labels or {}).items():
        sk = str(k)
        out[sk] = lab
        if sk in Z_CO_LEGACY_SET:
            out[to_z_co_key(sk)] = lab
        elif is_z_co_key(sk):
            leg = to_legacy_key(sk)
            if leg and leg not in out:
                out[leg] = lab
    return out
