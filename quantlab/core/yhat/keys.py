"""ŷ 特征键常量：signal / research 共用的单一真源。

滞后键定义在此；Z 入模列与 ``core.signal.factors.z_tc`` / ``z_co`` 对齐后在此再导出，
供 dual_score 等避免直 import ``*_panel`` / ``*_ridge``。
"""

from __future__ import annotations

from typing import Tuple

# --- τ / 隔夜滞后（裸名；面板写裸名，Z 前缀在 factors） ---

TAU_LAG_FEATURES: Tuple[str, ...] = ("tau_lag1", "tau_ma5")
TAU_LAG_FEAT_LABELS = {
    "tau_lag1": "昨真实 close/open−1 %",
    "tau_ma5": "近5日真实 close/open−1 均 %",
}

# 隔夜滞后：asof 用 T-1，避开今日 gap_pct（= T-1 日隔夜 = open[T]/close[T-1]-1）
CO_LAG_FEATURES: Tuple[str, ...] = ("yest_gap", "on_ma5")
CO_LAG_FEAT_LABELS = {
    "yest_gap": "昨隔夜缺口 %",
    "on_ma5": "近5日隔夜缺口均 %",
}

# --- 入模 Z（canon）；实现列清单在 factors ---

from core.signal.factors.z_co import (  # noqa: E402
    Z_CO_FIT_FEATURES,
    Z_CO_LEGACY_KEYS,
)
from core.signal.factors.z_tc import Z_TC_FIT_FEATURES  # noqa: E402

# 手造 Z（canon ``z_co_*``）；面板仍写裸名，``stamp_z_co_aliases`` 镜像。
CO_Z_FEATURES = Z_CO_FIT_FEATURES
CO_Z_LEGACY = Z_CO_LEGACY_KEYS

# alpha_τc Z 块（canon ``z_τc_*``）；历史名 TAU_Z_FEATURES
TAU_Z_FEATURES = Z_TC_FIT_FEATURES

__all__ = [
    "CO_LAG_FEATURES",
    "CO_LAG_FEAT_LABELS",
    "CO_Z_FEATURES",
    "CO_Z_LEGACY",
    "TAU_LAG_FEATURES",
    "TAU_LAG_FEAT_LABELS",
    "TAU_Z_FEATURES",
]
