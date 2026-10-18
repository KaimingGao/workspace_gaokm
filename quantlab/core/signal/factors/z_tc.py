"""ŷ_τc 单票/截面 Z 的统一前缀 ``z_τc_*``。

训练与新模型系数键用 canon；面板/live 仍可写旧裸名（``gap_pct`` 等）。
``feature_get`` / ``stamp_z_tc_aliases`` 负责双向别名，旧 ``tau_ridge_v12`` 可继续打分。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS

Z_TC_PREFIX = "z_τc_"

# 与 yhat.keys.TAU_LAG_FEATURES 对齐（此处硬编码，避免 yhat↔factors 循环 import）
_TAU_LAG_FEATURES: Tuple[str, ...] = ("tau_lag1", "tau_ma5")

# 开盘 / 截面 Z（open_gap ≡ gap_pct，拟合时丢掉）
Z_TC_LEGACY_OPEN: Tuple[str, ...] = (
    "gap_pct",
    "open_gap",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
)

Z_TC_LEGACY_KEYS: Tuple[str, ...] = (
    Z_TC_LEGACY_OPEN + MINUTE_TAU_ALL_KEYS + _TAU_LAG_FEATURES
)
Z_TC_LEGACY_SET = frozenset(Z_TC_LEGACY_KEYS)

# 入模默认列（历史 TAU_Z_FEATURES 同成员 + 前缀）
Z_TC_FIT_FEATURES: Tuple[str, ...] = tuple(
    f"{Z_TC_PREFIX}{k}"
    for k in (
        "gap_pct",
        "sector_gap_breadth",
        "theme_day",
        "gap_atr",
        "gap_vs_sector",
        "yclose_loc",
    )
    + MINUTE_TAU_ALL_KEYS
    + _TAU_LAG_FEATURES
)

Z_TC_MINUTE_KEYS: Tuple[str, ...] = tuple(
    f"{Z_TC_PREFIX}{k}" for k in MINUTE_TAU_ALL_KEYS
)

Z_TC_FIT_DROP_ALIASES = frozenset({f"{Z_TC_PREFIX}open_gap", "open_gap"})


def is_z_tc_key(name: str) -> bool:
    return str(name or "").startswith(Z_TC_PREFIX)


def to_z_tc_key(legacy_or_canon: str) -> str:
    k = str(legacy_or_canon or "").strip()
    if not k:
        return k
    if is_z_tc_key(k):
        return k
    return f"{Z_TC_PREFIX}{k}"


def to_legacy_key(canon_or_legacy: str) -> str:
    k = str(canon_or_legacy or "").strip()
    if k.startswith(Z_TC_PREFIX):
        return k[len(Z_TC_PREFIX) :]
    return k


def stamp_z_tc_aliases(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """就地：裸名 ↔ ``z_τc_*``。

    优先级：裸名 > z_τc_*。选定后强制写回两侧，避免并存时预测读到陈旧 canon。
    须在截面 Z 之前调用（Z 后勿再 stamp，以免裸名覆盖已 z-score 的 canon）。
    """
    out = row if isinstance(row, dict) else {}
    for leg in Z_TC_LEGACY_KEYS:
        canon = f"{Z_TC_PREFIX}{leg}"
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


def stamp_z_tc_rows(rows: Sequence[dict]) -> None:
    for row in rows or []:
        if isinstance(row, dict):
            stamp_z_tc_aliases(row)


def feature_get(row: Optional[Dict[str, Any]], name: str) -> Any:
    """预测查找：先原名，再别名。"""
    src = row if isinstance(row, dict) else {}
    key = str(name or "")
    if not key:
        return None
    v = src.get(key)
    if v is not None and v != "":
        return v
    alt = to_legacy_key(key) if is_z_tc_key(key) else to_z_tc_key(key)
    if alt and alt != key:
        v2 = src.get(alt)
        if v2 is not None and v2 != "":
            return v2
    return None


def z_tc_feat_labels(base_labels: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """旧中文标签挂到 ``z_τc_*`` 键上。"""
    out: Dict[str, str] = {}
    for k, lab in (base_labels or {}).items():
        sk = str(k)
        out[sk] = lab
        if sk in Z_TC_LEGACY_SET:
            out[to_z_tc_key(sk)] = lab
        elif is_z_tc_key(sk):
            leg = to_legacy_key(sk)
            if leg and leg not in out:
                out[leg] = lab
    return out
