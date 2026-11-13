"""四头 ŷ_* L4 Transformer 研究轨（不进生产 score）。

公共包：按 HeadSpec 服务 ŷ_oo / ŷ_τc / ŷ_oo_rank / ŷ_τc_rank。
一次 holdout run 只训一个 head；硬闸对照各头 Arm·L（LightGBM）。
"""

from __future__ import annotations

from core.research.yhat_transformer.heads import (
    HeadSpec,
    assert_trainable,
    get_head,
    head_ids,
    iter_heads,
)
from core.research.yhat_transformer.seq_dataset import (
    DEFAULT_SEQ_FEAT_SET,
    SEQ16_FEATURE_NAMES,
    SEQ32_FEATURE_NAMES,
    SEQ64_FEATURE_NAMES,
    SEQ8_FEATURE_NAMES,
    SEQ_FEATURE_NAMES,
    SeqPanel,
    build_seq_panel,
    resolve_seq_feat_set,
    seq_feature_names,
)

__all__ = [
    "HeadSpec",
    "get_head",
    "iter_heads",
    "head_ids",
    "assert_trainable",
    "DEFAULT_SEQ_FEAT_SET",
    "SEQ8_FEATURE_NAMES",
    "SEQ16_FEATURE_NAMES",
    "SEQ32_FEATURE_NAMES",
    "SEQ64_FEATURE_NAMES",
    "SEQ_FEATURE_NAMES",
    "resolve_seq_feat_set",
    "seq_feature_names",
    "SeqPanel",
    "build_seq_panel",
]
