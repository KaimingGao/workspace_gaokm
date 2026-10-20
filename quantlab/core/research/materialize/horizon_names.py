"""ŷ_τ* / 按钟 L1 列清单：τc ∪ 窗头（H 轨契约）。"""

from __future__ import annotations

from typing import List, Sequence, Tuple


def horizon_l1_feature_names() -> List[str]:
    """物化 ``tc_HHMM`` 时写入的特征列（去重保序）。"""
    from core.signal.factors.z_th import Z_TH_FIT_FEATURES
    from core.yhat.keys import TAU_Z_FEATURES

    out: List[str] = []
    seen = set()
    for n in list(TAU_Z_FEATURES) + list(Z_TH_FIT_FEATURES):
        key = str(n or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    try:
        from core.signal.factors.alpha_th import alpha_th_fit_features_for_head

        for head in ("t30", "t45", "t60", "t75", "t90"):
            for n in alpha_th_fit_features_for_head(head):
                key = str(n or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
    except Exception:  # noqa: BLE001
        pass
    return out


def horizon_cube_required_names() -> Tuple[str, ...]:
    """按钟 L2 至少应覆盖的窗头入模列（用于拒 slim）。"""
    from core.signal.factors.z_th import Z_TH_FIT_DROP_ALIASES, Z_TH_FIT_FEATURES

    return tuple(k for k in Z_TH_FIT_FEATURES if k not in Z_TH_FIT_DROP_ALIASES)


def open_cube_required_names() -> Tuple[str, ...]:
    """日线 open 立方必须盖住的入模列：oo、开盘 τc、co。不含分钟键。"""
    from core.signal.factors.alpha_oo import (
        ALPHA_OO_FIELD_NAMES,
        ALPHA_OO_RAW_PREFIX,
        Z_OO_DROP_WHEN_AOO_CORE,
    )
    from core.signal.factors.z_co import Z_CO_FIT_FEATURES
    from core.signal.factors.z_oo import Z_OO_SKIP_PACKS, to_z_oo_key, z_oo_legacy_keys
    from core.signal.factors.z_tc import Z_TC_FIT_FEATURES, Z_TC_MINUTE_KEYS

    need: List[str] = []
    seen = set()

    def _add(name: str) -> None:
        key = str(name or "").strip()
        if key and key not in seen:
            seen.add(key)
            need.append(key)

    for name in z_oo_legacy_keys():
        if name in Z_OO_DROP_WHEN_AOO_CORE:
            continue
        canon = to_z_oo_key(name)
        if not canon or canon in Z_OO_SKIP_PACKS:
            continue
        _add(canon)
    for key in ALPHA_OO_FIELD_NAMES:
        _add(f"{ALPHA_OO_RAW_PREFIX}{key}")
    minute = set(Z_TC_MINUTE_KEYS)
    for key in Z_TC_FIT_FEATURES:
        if key not in minute:
            _add(key)
    for key in Z_CO_FIT_FEATURES:
        _add(key)
    return tuple(need)


def minute_horizon_required_names() -> Tuple[str, ...]:
    """分钟立方入模列：窗头 Z 列加上五个头的 raw_ath_*。"""
    need: List[str] = []
    seen = set()
    for n in horizon_cube_required_names():
        key = str(n or "").strip()
        if key and key not in seen:
            seen.add(key)
            need.append(key)
    try:
        from core.signal.factors.alpha_th import alpha_th_fit_features_for_head

        for head in ("t30", "t45", "t60", "t75", "t90"):
            for n in alpha_th_fit_features_for_head(head):
                key = str(n or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    need.append(key)
    except Exception:  # noqa: BLE001
        pass
    return tuple(need)


def _name_list(cube_or_names: Sequence[str] | object) -> List[str]:
    if cube_or_names is None:
        return []
    named = getattr(cube_or_names, "names", None)
    if named is not None and not callable(named):
        try:
            return [str(n) for n in named if str(n or "").strip()]
        except TypeError:
            return []
    if isinstance(cube_or_names, (list, tuple, set)):
        return [str(n) for n in cube_or_names if str(n or "").strip()]
    return []


# 早盘钟 raw_ath_* / 部分 z_τh_* 常为 NaN（不足窗长），立方组矩阵会丢掉全空列。
# 验收用半数覆盖，避免「L1 有列名、L2 全被 slim_reject」假失败。
HORIZON_COVER_FRAC = 0.5


def names_cover_horizon(
    cube_or_names: Sequence[str] | object,
    *,
    min_frac: float = HORIZON_COVER_FRAC,
    need: Sequence[str] | None = None,
) -> bool:
    """Cube.names 或 name 列表是否足够覆盖窗头列。"""
    if cube_or_names is None:
        return False
    got = set(_name_list(cube_or_names))
    if len(got) < 3:
        return False
    required = (
        horizon_cube_required_names()
        if need is None
        else [str(n) for n in need if str(n or "").strip()]
    )
    if not required:
        return len(got) >= 3
    try:
        from core.signal.factors.z_th import to_legacy_key
    except Exception:  # noqa: BLE001
        to_legacy_key = lambda k: k  # type: ignore[assignment, misc]
    hit = 0
    for key in required:
        bare = str(to_legacy_key(key) or key)
        if key in got or bare in got:
            hit += 1
    thresh = max(3, int(len(required) * float(min_frac) + 0.999))
    return hit >= thresh


__all__ = [
    "HORIZON_COVER_FRAC",
    "horizon_l1_feature_names",
    "horizon_cube_required_names",
    "minute_horizon_required_names",
    "open_cube_required_names",
    "names_cover_horizon",
]
