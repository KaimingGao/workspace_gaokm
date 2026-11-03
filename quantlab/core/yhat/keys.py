"""ŷ 特征键常量：signal / research 共用的单一真源。

滞后键定义在此；Z 入模列与 ``core.signal.factors.z_tc`` 对齐后在此再导出，
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

# --- 入模 Z（canon）；实现列清单在 factors ---

from core.signal.factors.z_tc import Z_TC_FIT_FEATURES  # noqa: E402

# alpha_τc Z 块（canon ``z_τc_*``）；历史名 TAU_Z_FEATURES
TAU_Z_FEATURES = Z_TC_FIT_FEATURES

__all__ = [
    "TAU_LAG_FEATURES",
    "TAU_LAG_FEAT_LABELS",
    "TAU_Z_FEATURES",
]
