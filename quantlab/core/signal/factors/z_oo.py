"""ŷ_oo 注册日线因子的统一前缀 ``z_oo_*``。

训练与新模型系数键用 canon（``z_oo_momentum`` 等）；面板/live 仍写裸名。
``raw_aoo_*`` 不改前缀。``feature_get`` / ``stamp_z_oo_aliases`` 双向别名，
旧 return_score 模型（裸名 β）可继续打分。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

Z_OO_PREFIX = "z_oo_"

# 因子包本身不是日线 Z，不进 z_oo_* 重命名
Z_OO_SKIP_PACKS = frozenset({"alpha_oo", "alpha_tc"})


def is_z_oo_key(name: str) -> bool:
    return str(name or "").startswith(Z_OO_PREFIX)


def to_z_oo_key(legacy_or_canon: str) -> str:
    k = str(legacy_or_canon or "").strip()
    if not k:
        return k
    if is_z_oo_key(k):
        return k
    if k in Z_OO_SKIP_PACKS:
        return k
    # raw_aoo_* / raw_atc_* 自有前缀，不套 z_oo_
    if k.startswith("raw_aoo_") or k.startswith("raw_atc_") or k.startswith(
        ("z_τc_", "z_co_")
    ):
        return k
    return f"{Z_OO_PREFIX}{k}"


def to_legacy_key(canon_or_legacy: str) -> str:
    k = str(canon_or_legacy or "").strip()
    if k.startswith(Z_OO_PREFIX):
        return k[len(Z_OO_PREFIX) :]
    return k


def z_oo_legacy_keys() -> Tuple[str, ...]:
    """注册表日线裸名（排除因子包）。"""
    from core.signal.factors.meta.registry import registered_factor_names

    out: List[str] = []
    seen = set()
    for name in registered_factor_names():
        k = str(name or "").strip()
        if not k or k in Z_OO_SKIP_PACKS or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return tuple(out)


def z_oo_fit_features() -> Tuple[str, ...]:
    return tuple(to_z_oo_key(k) for k in z_oo_legacy_keys())


def stamp_z_oo_aliases(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """就地：裸名 ↔ ``z_oo_*`` 镜像。"""
    out = row if isinstance(row, dict) else {}
    for leg in z_oo_legacy_keys():
        canon = f"{Z_OO_PREFIX}{leg}"
        lv = out.get(leg)
        cv = out.get(canon)
        if lv is not None and lv != "" and (cv is None or cv == ""):
            out[canon] = lv
        elif cv is not None and cv != "" and (lv is None or lv == ""):
            out[leg] = cv
    return out


def stamp_z_oo_rows(rows: Sequence[dict]) -> None:
    for row in rows or []:
        if isinstance(row, dict):
            stamp_z_oo_aliases(row)


def feature_get(row: Optional[Dict[str, Any]], name: str) -> Any:
    """预测查找：先原名，再别名。"""
    src = row if isinstance(row, dict) else {}
    key = str(name or "")
    if not key:
        return None
    v = src.get(key)
    if v is not None and v != "":
        return v
    alt = to_legacy_key(key) if is_z_oo_key(key) else to_z_oo_key(key)
    if alt and alt != key and alt not in Z_OO_SKIP_PACKS:
        v2 = src.get(alt)
        if v2 is not None and v2 != "":
            return v2
    return None
