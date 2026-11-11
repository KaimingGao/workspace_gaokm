"""OoSeqTransformer 训练 / 推理（Holdout 锁定，只用 train 内验证）。"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "需要 PyTorch：.venv/bin/python -m pip install torch"
        ) from exc
    return torch


def _seed_all(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch = _require_torch()
    torch.manual_seed(seed)


def split_train_val_indices(
    dates: Sequence[str],
    train_idx: Sequence[int],
    *,
    val_frac: float = 0.2,
) -> Tuple[List[int], List[int]]:
    """按日历从训练集末尾切 val（不动 Holdout）。"""
    row_days = [str(dates[i])[:10] for i in train_idx]
    uniq = sorted({d for d in row_days if d})
    if len(uniq) < 8:
        return list(train_idx), []
    frac = min(0.4, max(0.1, float(val_frac or 0.2)))
    n_val = max(2, int(round(len(uniq) * frac)))
    n_val = min(n_val, len(uniq) - 3)
    val_days = set(uniq[-n_val:])
    tr: List[int] = []
    va: List[int] = []
    for i in train_idx:
        d = str(dates[i])[:10]
        (va if d in val_days else tr).append(int(i))
    if len(tr) < 8:
        return list(train_idx), []
    return tr, va


def _batches(n: int, batch_size: int, *, shuffle: bool, rng: np.random.RandomState):
    idx = np.arange(n, dtype=np.int64)
    if shuffle:
        rng.shuffle(idx)
    bs = max(16, int(batch_size or 128))
    for start in range(0, n, bs):
        yield idx[start : start + bs]


def _configure_torch_threads() -> None:
    """CPU 训练：提高算子内并行（OpenMP 已用 OMP_NUM_THREADS 时可并存）。"""
    torch = _require_torch()
    try:
        import os

        n = int(os.environ.get("TORCH_NUM_THREADS") or 0)
        if n <= 0:
            n = max(1, min(8, int(os.cpu_count() or 4)))
        torch.set_num_threads(n)
    except Exception:
        pass


def _to_device_tensor(arr: np.ndarray, device) -> Any:
    torch = _require_torch()
    t = torch.from_numpy(np.ascontiguousarray(arr))
    if str(device) != "cpu":
        t = t.to(device, non_blocking=True)
    return t


def _maybe_subsample(
    train_idx: Sequence[int],
    *,
    max_train_rows: Optional[int],
    seed: int,
) -> List[int]:
    tr = list(train_idx)
    cap = int(max_train_rows or 0)
    if cap <= 0 or len(tr) <= cap:
        return tr
    rng = np.random.RandomState(int(seed))
    pick = rng.choice(len(tr), size=cap, replace=False)
    return [tr[int(i)] for i in sorted(pick.tolist())]


def train_oo_transformer(
    panel: Any,
    train_idx: Sequence[int],
    *,
    val_idx: Optional[Sequence[int]] = None,
    d_model: int = 32,
    nhead: int = 4,
    nlayers: int = 2,
    dropout: float = 0.1,
    epochs: int = 30,
    batch_size: int = 128,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    seed: int = 42,
    device: Optional[str] = None,
    patience: int = 5,
    max_train_rows: Optional[int] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """返回 ``(state_dict_cpu, report)``；模型权重在 CPU。"""
    torch = _require_torch()
    from core.research.oo_transformer.model import (
        OoSeqTransformer,
        count_parameters,
        pick_device,
    )

    _seed_all(int(seed))
    dev = pick_device(device)
    tr = _maybe_subsample(train_idx, max_train_rows=max_train_rows, seed=seed)
    if val_idx is None:
        tr, va = split_train_val_indices(panel.dates, tr, val_frac=0.2)
    else:
        va = list(val_idx)

    X = np.asarray(panel.X, dtype=np.float32)
    gap = np.asarray(panel.gap, dtype=np.float32)
    y = np.asarray(panel.y, dtype=np.float32)
    if tr:
        flat = X[np.asarray(tr)].reshape(-1, X.shape[-1])
        lo = np.nanpercentile(flat, 1.0, axis=0)
        hi = np.nanpercentile(flat, 99.0, axis=0)
        X = np.clip(X, lo, hi)
        g_tr = gap[np.asarray(tr)]
        glo, ghi = np.nanpercentile(g_tr, 1.0), np.nanpercentile(g_tr, 99.0)
        gap = np.clip(gap, glo, ghi)

    d_in = int(X.shape[-1])
    model = OoSeqTransformer(
        d_in=d_in,
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        dim_feedforward=max(64, int(d_model) * 2),
        dropout=float(dropout),
        use_gap=True,
    ).to(dev)
    opt = torch.optim.AdamW(
        model.parameters(),
        lr=float(lr),
        weight_decay=float(weight_decay),
    )
    loss_fn = torch.nn.SmoothL1Loss(beta=1.0)
    rng = np.random.RandomState(int(seed))

    best_state = None
    best_val = float("inf")
    bad = 0
    history: List[Dict[str, float]] = []

    def _run_epoch(indices: Sequence[int], *, train: bool) -> float:
        if not indices:
            return float("nan")
        if train:
            model.train()
        else:
            model.eval()
        total = 0.0
        n_seen = 0
        ctx = torch.enable_grad() if train else torch.no_grad()
        with ctx:
            for bi in _batches(len(indices), batch_size, shuffle=train, rng=rng):
                rows = [indices[int(j)] for j in bi.tolist()]
                xb = torch.as_tensor(X[rows], device=dev)
                gb = torch.as_tensor(gap[rows], device=dev)
                yb = torch.as_tensor(y[rows], device=dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                pred = model(xb, gb)
                loss = loss_fn(pred, yb)
                if train:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()
                bs = int(yb.shape[0])
                total += float(loss.detach().cpu()) * bs
                n_seen += bs
        return total / float(max(1, n_seen))

    for ep in range(1, max(1, int(epochs)) + 1):
        tr_loss = _run_epoch(tr, train=True)
        va_loss = _run_epoch(va, train=False) if va else tr_loss
        history.append({"epoch": float(ep), "train_loss": tr_loss, "val_loss": va_loss})
        score = va_loss if math.isfinite(va_loss) else tr_loss
        if score < best_val - 1e-5:
            best_val = float(score)
            bad = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= max(2, int(patience)):
                break

    if best_state is None:
        best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    report = {
        "success": True,
        "device": str(dev),
        "seed": int(seed),
        "n_train": len(tr),
        "n_val": len(va),
        "n_params": count_parameters(model),
        "best_val_loss": best_val if math.isfinite(best_val) else None,
        "epochs_ran": len(history),
        "history_tail": history[-8:],
        "d_model": int(d_model),
        "nlayers": int(nlayers),
        "batch_size": int(batch_size),
    }
    return best_state, report


def predict_oo_transformer(
    state_dict: Dict[str, Any],
    panel: Any,
    row_idx: Optional[Sequence[int]] = None,
    *,
    d_model: int = 32,
    nhead: int = 4,
    nlayers: int = 2,
    device: Optional[str] = None,
    batch_size: int = 256,
) -> np.ndarray:
    torch = _require_torch()
    from core.research.oo_transformer.model import OoSeqTransformer, pick_device

    idx = list(range(panel.n)) if row_idx is None else [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    X = np.asarray(panel.X[idx], dtype=np.float32)
    gap = np.asarray(panel.gap[idx], dtype=np.float32)
    d_in = int(X.shape[-1])
    dev = pick_device(device)
    model = OoSeqTransformer(
        d_in=d_in,
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        dim_feedforward=max(64, int(d_model) * 2),
        dropout=0.0,
        use_gap=True,
    )
    model.load_state_dict(state_dict)
    model.to(dev)
    model.eval()
    outs: List[np.ndarray] = []
    n = int(X.shape[0])
    bs = max(32, int(batch_size or 256))
    with torch.no_grad():
        for start in range(0, n, bs):
            sl = slice(start, start + bs)
            xb = torch.as_tensor(X[sl], device=dev)
            gb = torch.as_tensor(gap[sl], device=dev)
            pred = model(xb, gb)
            outs.append(pred.detach().cpu().numpy().astype(np.float64))
    if not outs:
        return np.zeros((0,), dtype=np.float64)
    return np.concatenate(outs, axis=0)


def train_factor_mlp(
    X: np.ndarray,
    y: np.ndarray,
    dates: Sequence[str],
    train_idx: Sequence[int],
    *,
    hidden: int = 64,
    dropout: float = 0.1,
    epochs: int = 25,
    batch_size: int = 256,
    lr: float = 1e-3,
    seed: int = 42,
    device: Optional[str] = None,
    patience: int = 5,
    max_train_rows: Optional[int] = None,
) -> Tuple[Any, Dict[str, Any]]:
    torch = _require_torch()
    from core.research.oo_transformer.model import OoFactorMLP, count_parameters, pick_device
    from core.research.oo_transformer.factor_panel import column_standardize_train

    _seed_all(int(seed))
    dev = pick_device(device)
    tr0 = _maybe_subsample(train_idx, max_train_rows=max_train_rows, seed=seed)
    tr, va = split_train_val_indices(dates, tr0, val_frac=0.2)
    Xz, _mu, _sd = column_standardize_train(X, tr)
    y = np.asarray(y, dtype=np.float32)
    model = OoFactorMLP(d_in=int(Xz.shape[1]), hidden=hidden, dropout=dropout).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=float(lr), weight_decay=1e-4)
    loss_fn = torch.nn.SmoothL1Loss(beta=1.0)
    rng = np.random.RandomState(int(seed))
    best_state = None
    best_val = float("inf")
    bad = 0

    def _epoch(indices: Sequence[int], *, train: bool) -> float:
        if not indices:
            return float("nan")
        model.train(train)
        total = 0.0
        n_seen = 0
        ctx = torch.enable_grad() if train else torch.no_grad()
        with ctx:
            for bi in _batches(len(indices), batch_size, shuffle=train, rng=rng):
                rows = [indices[int(j)] for j in bi.tolist()]
                xb = torch.as_tensor(Xz[rows], device=dev)
                yb = torch.as_tensor(y[rows], device=dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                loss = loss_fn(model(xb), yb)
                if train:
                    loss.backward()
                    opt.step()
                bs = int(yb.shape[0])
                total += float(loss.detach().cpu()) * bs
                n_seen += bs
        return total / float(max(1, n_seen))

    for _ep in range(1, max(1, int(epochs)) + 1):
        _epoch(tr, train=True)
        va_loss = _epoch(va, train=False) if va else _epoch(tr, train=False)
        if va_loss < best_val - 1e-5:
            best_val = float(va_loss)
            bad = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= max(2, int(patience)):
                break
    if best_state is None:
        best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    return best_state, {
        "success": True,
        "n_train": len(tr),
        "n_val": len(va),
        "n_params": count_parameters(model),
        "best_val_loss": best_val if math.isfinite(best_val) else None,
        "d_in": int(Xz.shape[1]),
        "hidden": int(hidden),
        "seed": int(seed),
        "Xz": Xz,  # 推理复用同一标准化
    }


def predict_factor_mlp(
    state_dict: Dict[str, Any],
    Xz: np.ndarray,
    row_idx: Sequence[int],
    *,
    d_in: int,
    hidden: int = 64,
    device: Optional[str] = None,
    batch_size: int = 512,
) -> np.ndarray:
    torch = _require_torch()
    from core.research.oo_transformer.model import OoFactorMLP, pick_device

    idx = [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    dev = pick_device(device)
    model = OoFactorMLP(d_in=int(d_in), hidden=hidden, dropout=0.0)
    model.load_state_dict(state_dict)
    model.to(dev)
    model.eval()
    X = np.asarray(Xz[idx], dtype=np.float32)
    outs: List[np.ndarray] = []
    bs = max(32, int(batch_size))
    with torch.no_grad():
        for start in range(0, len(idx), bs):
            sl = slice(start, start + bs)
            pred = model(torch.as_tensor(X[sl], device=dev))
            outs.append(pred.detach().cpu().numpy().astype(np.float64))
    return np.concatenate(outs, axis=0) if outs else np.zeros((0,), dtype=np.float64)


def train_seq_factor_fusion(
    seq_panel: Any,
    fac_X: np.ndarray,
    train_idx: Sequence[int],
    *,
    d_model: int = 32,
    nhead: int = 4,
    nlayers: int = 2,
    factor_hidden: int = 64,
    dropout: float = 0.1,
    epochs: int = 25,
    batch_size: int = 128,
    lr: float = 1e-3,
    seed: int = 42,
    device: Optional[str] = None,
    patience: int = 5,
    max_train_rows: Optional[int] = None,
) -> Tuple[Any, Dict[str, Any]]:
    torch = _require_torch()
    from core.research.oo_transformer.model import (
        OoSeqFactorFusion,
        count_parameters,
        pick_device,
    )
    from core.research.oo_transformer.factor_panel import column_standardize_train

    _seed_all(int(seed))
    _configure_torch_threads()
    dev = pick_device(device)
    tr0 = _maybe_subsample(train_idx, max_train_rows=max_train_rows, seed=seed)
    tr, va = split_train_val_indices(seq_panel.dates, tr0, val_frac=0.2)
    Xseq = np.asarray(seq_panel.X, dtype=np.float32)
    gap = np.asarray(seq_panel.gap, dtype=np.float32)
    y = np.asarray(seq_panel.y, dtype=np.float32)
    Xf, _mu, _sd = column_standardize_train(fac_X, tr)
    if tr:
        flat = Xseq[np.asarray(tr)].reshape(-1, Xseq.shape[-1])
        lo, hi = np.nanpercentile(flat, 1.0, axis=0), np.nanpercentile(flat, 99.0, axis=0)
        Xseq = np.clip(Xseq, lo, hi)
        gap = np.clip(
            gap,
            np.nanpercentile(gap[np.asarray(tr)], 1.0),
            np.nanpercentile(gap[np.asarray(tr)], 99.0),
        )

    model = OoSeqFactorFusion(
        d_seq_in=int(Xseq.shape[-1]),
        d_factor=int(Xf.shape[1]),
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        factor_hidden=int(factor_hidden),
        dropout=float(dropout),
    ).to(dev)
    # 全表一次上张量，避免每个 batch 反复 numpy→tensor（C 臂主耗时）
    Xseq_t = _to_device_tensor(Xseq, "cpu")
    gap_t = _to_device_tensor(gap, "cpu")
    Xf_t = _to_device_tensor(Xf, "cpu")
    y_t = _to_device_tensor(y, "cpu")
    opt = torch.optim.AdamW(model.parameters(), lr=float(lr), weight_decay=1e-4)
    loss_fn = torch.nn.SmoothL1Loss(beta=1.0)
    rng = np.random.RandomState(int(seed))
    best_state = None
    best_val = float("inf")
    bad = 0
    tr_arr = np.asarray(tr, dtype=np.int64)
    va_arr = np.asarray(va, dtype=np.int64) if va else np.zeros((0,), dtype=np.int64)

    def _epoch(row_ids: np.ndarray, *, train: bool) -> float:
        if row_ids.size <= 0:
            return float("nan")
        model.train(train)
        total = 0.0
        n_seen = 0
        order = row_ids.copy()
        if train:
            rng.shuffle(order)
        bs = max(16, int(batch_size or 128))
        ctx = torch.enable_grad() if train else torch.no_grad()
        with ctx:
            for start in range(0, int(order.size), bs):
                rows = order[start : start + bs]
                idx = torch.from_numpy(rows)
                xs = Xseq_t[idx].to(dev)
                gg = gap_t[idx].to(dev)
                xf = Xf_t[idx].to(dev)
                yb = y_t[idx].to(dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                loss = loss_fn(model(xs, gg, xf), yb)
                if train:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    opt.step()
                nbs = int(yb.shape[0])
                total += float(loss.detach()) * nbs
                n_seen += nbs
        return total / float(max(1, n_seen))

    for _ep in range(1, max(1, int(epochs)) + 1):
        _epoch(tr_arr, train=True)
        va_loss = _epoch(va_arr, train=False) if va_arr.size else _epoch(tr_arr, train=False)
        if va_loss < best_val - 1e-5:
            best_val = float(va_loss)
            bad = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= max(2, int(patience)):
                break
    if best_state is None:
        best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    return best_state, {
        "success": True,
        "n_train": len(tr),
        "n_val": len(va),
        "n_params": count_parameters(model),
        "best_val_loss": best_val if math.isfinite(best_val) else None,
        "seed": int(seed),
        "device": str(dev),
        "batch_size": int(batch_size),
        "Xf": Xf,
        "Xseq": Xseq,
        "gap": gap,
    }


def predict_seq_factor_fusion(
    state_dict: Dict[str, Any],
    Xseq: np.ndarray,
    gap: np.ndarray,
    Xf: np.ndarray,
    row_idx: Sequence[int],
    *,
    d_model: int = 32,
    nhead: int = 4,
    nlayers: int = 2,
    factor_hidden: int = 64,
    device: Optional[str] = None,
    batch_size: int = 256,
) -> np.ndarray:
    torch = _require_torch()
    from core.research.oo_transformer.model import OoSeqFactorFusion, pick_device

    idx = [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    dev = pick_device(device)
    model = OoSeqFactorFusion(
        d_seq_in=int(Xseq.shape[-1]),
        d_factor=int(Xf.shape[1]),
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        factor_hidden=int(factor_hidden),
        dropout=0.0,
    )
    model.load_state_dict(state_dict)
    model.to(dev)
    model.eval()
    Xseq_t = _to_device_tensor(np.asarray(Xseq, dtype=np.float32), "cpu")
    gap_t = _to_device_tensor(np.asarray(gap, dtype=np.float32), "cpu")
    Xf_t = _to_device_tensor(np.asarray(Xf, dtype=np.float32), "cpu")
    outs: List[np.ndarray] = []
    bs = max(32, int(batch_size))
    order = np.asarray(idx, dtype=np.int64)
    with torch.no_grad():
        for start in range(0, int(order.size), bs):
            rows = order[start : start + bs]
            ii = torch.from_numpy(rows)
            pred = model(
                Xseq_t[ii].to(dev),
                gap_t[ii].to(dev),
                Xf_t[ii].to(dev),
            )
            outs.append(pred.detach().cpu().numpy().astype(np.float64))
    return np.concatenate(outs, axis=0) if outs else np.zeros((0,), dtype=np.float64)


__all__ = [
    "split_train_val_indices",
    "train_oo_transformer",
    "predict_oo_transformer",
    "train_factor_mlp",
    "predict_factor_mlp",
    "train_seq_factor_fusion",
    "predict_seq_factor_fusion",
]
