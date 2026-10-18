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


def names_cover_horizon(cube_or_names: Sequence[str] | object, *, min_frac: float = 0.25) -> bool:
    """Cube.names 或 name 列表是否足够覆盖窗头列。"""
    if cube_or_names is None:
        return False
    if hasattr(cube_or_names, "names"):
        got = {str(n) for n in (getattr(cube_or_names, "names", None) or []) if str(n or "").strip()}
    else:
        got = {str(n) for n in (cube_or_names or []) if str(n or "").strip()}
    if len(got) < 3:
        return False
    need = [str(n) for n in horizon_cube_required_names() if str(n or "").strip()]
    if not need:
        return len(got) >= 3
    try:
        from core.signal.factors.z_th import to_legacy_key
    except Exception:  # noqa: BLE001
        to_legacy_key = lambda k: k  # type: ignore[assignment, misc]
    hit = 0
    for key in need:
        bare = str(to_legacy_key(key) or key)
        if key in got or bare in got:
            hit += 1
    thresh = max(3, int(len(need) * float(min_frac) + 0.999))
    return hit >= thresh


__all__ = [
    "horizon_l1_feature_names",
    "horizon_cube_required_names",
    "names_cover_horizon",
]
