"""ŷ_τ* 单票/截面 Z 的统一前缀 ``z_τh_*``。

训练与新模型系数键用 canon；面板/live 仍可写裸名或 ``z_τc_*``。
``feature_get`` / ``stamp_z_th_aliases``：``z_τh_*`` ↔ 裸名 ↔ ``z_τc_*``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from core.signal.factors.z_tc import (
    Z_TC_LEGACY_OPEN,
    Z_TC_PREFIX,
    _TAU_LAG_FEATURES,
)
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS

Z_TH_PREFIX = "z_τh_"

# 与 tc_ridge_compact.TAU_HORIZON_DROP_OC_SHAPE 对齐（硬编码避免 research↔factors 环）
_OC_SHAPE_DROP = frozenset(
    {
        "range_pct",
        "loc_hl",
        "up_extent",
        "down_extent",
        "path_sign",
        "pullback_from_high",
        "bounce_from_low",
        "realized_vol",
        "vol_last3_vs_avg",
        "tau_elapsed_min",
        "t_hi_frac",
        "t_lo_frac",
        "t_hi_minus_lo",
        "room_to_high",
        "room_to_low",
        "mom_accel_5_15",
        "mom_accel_5_30",
        "vol_down_up",
        "range_efficiency",
        "vp_confirm",
        "vol_up_share",
        "pullback_x_vol",
    }
)


def _ath_promoted_sources() -> frozenset:
    """已被 raw_ath_* 升格覆盖的源裸名（勿再进 z_τh_*）。"""
    from core.signal.factors.alpha_th import _FEAT_TO_ATH, _HEAD_PROMOTE

    keys = set(_FEAT_TO_ATH.keys())
    for m in _HEAD_PROMOTE.values():
        keys.update(str(v) for v in m.values())
    return frozenset(keys)


def _kept_legacy() -> Tuple[str, ...]:
    promoted = _ath_promoted_sources()
    open_keys = tuple(
        k
        for k in Z_TC_LEGACY_OPEN
        if k != "open_gap" and k not in _OC_SHAPE_DROP and k not in promoted
    )
    minute_keys = tuple(
        k
        for k in MINUTE_TAU_ALL_KEYS
        if k not in _OC_SHAPE_DROP and k not in promoted
    )
    lag_keys = tuple(k for k in _TAU_LAG_FEATURES if k not in promoted)
    return open_keys + minute_keys + lag_keys


# 镜像用全量候选（含 open_gap，拟合时另丢）
Z_TH_LEGACY_KEYS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        list(Z_TC_LEGACY_OPEN)
        + list(MINUTE_TAU_ALL_KEYS)
        + list(_TAU_LAG_FEATURES)
    )
)
Z_TH_LEGACY_SET = frozenset(Z_TH_LEGACY_KEYS)

Z_TH_KEPT_LEGACY: Tuple[str, ...] = _kept_legacy()

Z_TH_FIT_FEATURES: Tuple[str, ...] = tuple(
    f"{Z_TH_PREFIX}{k}" for k in Z_TH_KEPT_LEGACY
)

Z_TH_FIT_DROP_ALIASES = frozenset({f"{Z_TH_PREFIX}open_gap", "open_gap"})


def is_z_th_key(name: str) -> bool:
    return str(name or "").startswith(Z_TH_PREFIX)


def to_z_th_key(legacy_or_canon: str) -> str:
    k = str(legacy_or_canon or "").strip()
    if not k:
        return k
    if is_z_th_key(k):
        return k
    # raw_ath_* / raw_atc_* 等不是 Z 块，勿包 z_τh_
    if k.startswith("raw_"):
        return k
    if k.startswith(Z_TC_PREFIX):
        k = k[len(Z_TC_PREFIX) :]
    return f"{Z_TH_PREFIX}{k}"


def to_legacy_key(canon_or_legacy: str) -> str:
    k = str(canon_or_legacy or "").strip()
    if k.startswith(Z_TH_PREFIX):
        return k[len(Z_TH_PREFIX) :]
    if k.startswith(Z_TC_PREFIX):
        return k[len(Z_TC_PREFIX) :]
    return k


def stamp_z_th_aliases(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """就地：裸名 / z_τc_* ↔ z_τh_*。

    优先级：裸名 > z_τc_* > z_τh_*。选定后**强制**写回裸名与 z_τh_*，
    避免两侧并存时预测读到陈旧 z_τh_*。
    """
    out = row if isinstance(row, dict) else {}
    for leg in Z_TH_LEGACY_KEYS:
        th = f"{Z_TH_PREFIX}{leg}"
        tc = f"{Z_TC_PREFIX}{leg}"
        lv = out.get(leg)
        thv = out.get(th)
        tcv = out.get(tc)
        src = None
        if lv is not None and lv != "":
            src = lv
        elif tcv is not None and tcv != "":
            src = tcv
        elif thv is not None and thv != "":
            src = thv
        if src is None:
            continue
        out[th] = src
        out[leg] = src
    return out


def stamp_z_th_rows(rows: Sequence[dict]) -> None:
    for row in rows or []:
        if isinstance(row, dict):
            stamp_z_th_aliases(row)


def feature_get(row: Optional[Dict[str, Any]], name: str) -> Any:
    """预测查找：z_τh_* → 裸名 → z_τc_*。"""
    src = row if isinstance(row, dict) else {}
    key = str(name or "")
    if not key:
        return None
    v = src.get(key)
    if v is not None and v != "":
        return v
    leg = to_legacy_key(key)
    if leg and leg != key:
        v2 = src.get(leg)
        if v2 is not None and v2 != "":
            return v2
    if key.startswith("raw_"):
        return None
    if is_z_th_key(key) or (leg and not key.startswith(Z_TC_PREFIX)):
        tc = f"{Z_TC_PREFIX}{leg}"
        v3 = src.get(tc)
        if v3 is not None and v3 != "":
            return v3
    if not is_z_th_key(key):
        th = to_z_th_key(key)
        if th and th != key:
            v4 = src.get(th)
            if v4 is not None and v4 != "":
                return v4
    return None


def z_th_feat_labels(base_labels: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """中文标签挂到 ``z_τh_*`` 键上。"""
    out: Dict[str, str] = {}
    for k, lab in (base_labels or {}).items():
        sk = str(k)
        out[sk] = lab
        leg = to_legacy_key(sk)
        th = to_z_th_key(sk)
        if th not in out:
            out[th] = lab
        if leg and leg not in out:
            out[leg] = lab
    return out
