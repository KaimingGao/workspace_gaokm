"""τ 主题日口径：与 ``attach_cross_section_breadth`` 训练侧对齐。"""

from typing import Optional, Sequence


def median_abs_gap(gaps: Optional[Sequence[float]]) -> Optional[float]:
    vals = sorted(abs(float(g)) for g in (gaps or []) if g is not None)
    if not vals:
        return None
    return float(vals[len(vals) // 2])


def resolve_theme_day(
    *,
    gap_pct: Optional[float] = None,
    sector_breadth: Optional[float] = None,
    pool_gaps: Optional[Sequence[float]] = None,
    gap_trigger_pct: float = 2.0,
    breadth_theme_min: float = 0.5,
) -> float:
    """主题日 =1：广度≥门槛，或截面 |gap| 中位≥trigger，或本票 |gap|≥trigger。

    本票大缺口在有截面时也记主题（开盘可得、无泄漏）；避免「池静、个股跳空」永远 theme=0。
    """
    trigger = float(gap_trigger_pct)
    bmin = float(breadth_theme_min)
    try:
        b = float(sector_breadth) if sector_breadth is not None else None
    except (TypeError, ValueError):
        b = None
    med = median_abs_gap(pool_gaps)
    if b is not None and b >= bmin:
        return 1.0
    if med is not None and med >= trigger:
        return 1.0
    if gap_pct is not None:
        try:
            if abs(float(gap_pct)) >= trigger:
                return 1.0
        except (TypeError, ValueError):
            pass
    return 0.0
