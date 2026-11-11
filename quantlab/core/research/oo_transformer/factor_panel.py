"""决策日因子面板（研究轨）：z_oo_* ∪ raw_aoo_*，带 code/date 便于与序列对齐。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OoFactorPanel:
    X: np.ndarray  # (N, F) float32
    y: np.ndarray
    dates: List[str]
    codes: List[str]
    feature_names: List[str]

    @property
    def n(self) -> int:
        return int(self.y.shape[0])

    def subset(self, idx: Sequence[int]) -> "OoFactorPanel":
        ii = np.asarray(list(idx), dtype=np.int64)
        return OoFactorPanel(
            X=self.X[ii],
            y=self.y[ii],
            dates=[self.dates[int(i)] for i in ii.tolist()],
            codes=[self.codes[int(i)] for i in ii.tolist()],
            feature_names=list(self.feature_names),
        )


def build_oo_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    horizon_days: int = 1,
    ridge_core_only: bool = True,
    progress_every: int = 25,
) -> OoFactorPanel:
    """逐票 ``collect_oo_panel`` 再堆叠（研究用；默认 aoo_core 以控内存/耗时）。"""
    from core.research.oo_model_compact import OoPanelMatrix
    from core.research.oo_panel import collect_oo_panel

    panel = OoPanelMatrix(ridge_core_only=bool(ridge_core_only))
    codes_all: List[str] = []
    items = list(stock_bars.items())
    for i, (code, bars) in enumerate(items):
        code_s = str(code).strip()
        if not code_s or not bars:
            continue
        try:
            rows, y_list, d_list = collect_oo_panel(
                list(bars),
                horizon_days=horizon_days,
                stock_code=code_s,
                pit_fundamentals=False,
            )
        except Exception:  # noqa: BLE001
            logger.debug("factor panel collect failed %s", code_s, exc_info=True)
            continue
        n_before = len(panel.dates)
        panel.add_stock_rows(rows, y_list, d_list)
        n_after = len(panel.dates)
        codes_all.extend([code_s] * max(0, n_after - n_before))
        if progress_every and (i + 1) % int(progress_every) == 0:
            print(
                f"  因子面板 {i+1}/{len(items)} 累计行={len(panel.dates)}",
                flush=True,
            )

    x64, y64, names, dates = panel.finalize()
    if x64.shape[0] == 0:
        return OoFactorPanel(
            X=np.zeros((0, len(names)), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=list(names),
        )
    if len(codes_all) != int(x64.shape[0]):
        # 防御：长度不一致时用占位 code，避免静默错位
        logger.warning(
            "factor panel code align mismatch %s vs %s",
            len(codes_all),
            x64.shape[0],
        )
        codes_all = [f"row{i}" for i in range(int(x64.shape[0]))]
    X = np.asarray(x64, dtype=np.float32)
    X = np.where(np.isfinite(X), X, 0.0)
    y = np.asarray(y64, dtype=np.float32)
    # 丢掉无标签行
    keep = np.isfinite(y)
    if not bool(np.all(keep)):
        idx = np.where(keep)[0]
        X = X[idx]
        y = y[idx]
        dates = [dates[int(i)] for i in idx.tolist()]
        codes_all = [codes_all[int(i)] for i in idx.tolist()]
    return OoFactorPanel(
        X=X,
        y=y,
        dates=[str(d)[:10] for d in dates],
        codes=codes_all,
        feature_names=[str(n) for n in names],
    )


def align_seq_and_factor(
    seq: Any,
    fac: OoFactorPanel,
) -> Tuple[Any, OoFactorPanel]:
    """按 (code, date) 内连接。"""
    from core.research.oo_transformer.seq_dataset import OoSeqPanel

    fac_map: Dict[Tuple[str, str], int] = {}
    for i, (c, d) in enumerate(zip(fac.codes, fac.dates)):
        fac_map[(str(c), str(d)[:10])] = i
    seq_keep: List[int] = []
    fac_keep: List[int] = []
    for i, (c, d) in enumerate(zip(seq.codes, seq.dates)):
        j = fac_map.get((str(c), str(d)[:10]))
        if j is None:
            continue
        seq_keep.append(i)
        fac_keep.append(j)
    if not seq_keep:
        empty_s = OoSeqPanel(
            X=np.zeros((0, seq.seq_len, seq.X.shape[-1]), dtype=np.float32),
            gap=np.zeros((0,), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            seq_len=seq.seq_len,
        )
        empty_f = OoFactorPanel(
            X=np.zeros((0, max(1, fac.X.shape[-1])), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=list(fac.feature_names),
        )
        return empty_s, empty_f
    return seq.subset(seq_keep), fac.subset(fac_keep)


def column_standardize_train(
    X: np.ndarray,
    train_idx: Sequence[int],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Train 列 μ/σ；返回 (Xz, mu, sd)。"""
    X = np.asarray(X, dtype=np.float32)
    tr = np.asarray(list(train_idx), dtype=np.int64)
    mu = np.nanmean(X[tr], axis=0)
    sd = np.nanstd(X[tr], axis=0)
    sd = np.where(sd > 1e-6, sd, 1.0).astype(np.float32)
    mu = np.where(np.isfinite(mu), mu, 0.0).astype(np.float32)
    Xz = (X - mu) / sd
    Xz = np.where(np.isfinite(Xz), Xz, 0.0).astype(np.float32)
    return Xz, mu, sd


__all__ = [
    "OoFactorPanel",
    "build_oo_factor_panel",
    "align_seq_and_factor",
    "column_standardize_train",
]
