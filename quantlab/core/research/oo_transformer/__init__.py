"""ŷ_oo 研究轨：日线短序列 Transformer（L4，不进生产 score）。

对照现网 LightGBM；Holdout 日频截面 rank IC 为硬门。
"""

from __future__ import annotations

from core.research.oo_transformer.seq_dataset import (
    SEQ_FEATURE_NAMES,
    OoSeqPanel,
    build_oo_seq_panel,
)

__all__ = [
    "SEQ_FEATURE_NAMES",
    "OoSeqPanel",
    "build_oo_seq_panel",
]
