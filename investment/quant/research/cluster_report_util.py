"""分组 OLS 报告辅助（从 factor_ols_clusters 按用例拆出 · A3）。"""

from __future__ import annotations

from typing import Any, Dict


def clamp_n_clusters(value: Any, default: int = 3) -> int:
    """目标组数下限 2；不再硬顶 12（上限由调用方 ``min(k, n)`` 按宇宙规模收束）。"""
    try:
        k = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(2, k)


def clamp_watching_limit(value: Any, default: int = 8) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return int(default)
    # 上限对齐观察池 WATCHING_MAX_SIZE（分组 Limit / 日K/5m / ŷ_* 拟合默认满池 200）
    from core.watching.store import WATCHING_MAX_SIZE

    return max(3, min(n, int(WATCHING_MAX_SIZE)))


def cluster_speed_policy(panel_count: int) -> Dict[str, Any]:
    """大宇宙（≥40）加速：末日财务快照 + 跳过 Ridge 选 λ。"""
    n = int(panel_count or 0)
    large = n >= 40
    return {
        "large_universe": large,
        "daily_pit": not large,
        "select_ridge": not large,
        "note": (
            f"宇宙 {n}≥40：财务用末日快照（非逐日 PIT）· 跳过 Ridge 选 λ · 选区跳过组权 OOS 辅门禁"
            if large
            else None
        ),
    }
