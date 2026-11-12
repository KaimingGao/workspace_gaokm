"""按 HeadSpec 构建 Holdout 面板（研究轨；不写生产 score）。"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from core.research.yhat_transformer.factor_panel import (
    FactorPanel,
    align_seq_and_factor,
    build_factor_panel,
)
from core.research.yhat_transformer.heads import HeadSpec, assert_trainable, get_head
from core.research.yhat_transformer.seq_dataset import SeqPanel, build_seq_panel


def build_holdout_panels(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    head: Any = "oo",
    seq_len: int = 20,
    horizon_days: int = 1,
    need_seq: bool = True,
    need_fac: bool = True,
    ridge_core_only: bool = True,
    progress_every: int = 20,
    use_cache: bool = True,
) -> Tuple[Optional[SeqPanel], Optional[FactorPanel], HeadSpec]:
    """返回 ``(seq, fac, spec)``；未请求的一侧为 None。"""
    spec = get_head(getattr(head, "id", head))
    assert_trainable(spec)
    seq: Optional[SeqPanel] = None
    fac: Optional[FactorPanel] = None
    if need_seq:
        seq = build_seq_panel(
            stock_bars,
            head=spec,
            seq_len=seq_len,
            horizon_days=horizon_days,
        )
    if need_fac:
        fac = build_factor_panel(
            stock_bars,
            head=spec,
            horizon_days=horizon_days,
            ridge_core_only=ridge_core_only,
            progress_every=progress_every,
            use_cache=use_cache,
        )
    if seq is not None and fac is not None and seq.n > 0 and fac.n > 0:
        seq, fac = align_seq_and_factor(seq, fac)
    return seq, fac, spec


__all__ = ["build_holdout_panels"]
