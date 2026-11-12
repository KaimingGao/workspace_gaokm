"""SeqTransformer 训练 / 推理（Holdout 锁定，只用 train 内验证；四头共用）。"""

from __future__ import annotations

import logging
import math
import os
import random
import time as _time
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


def _group_row_lists(
    dates: Sequence[str],
    indices: Sequence[int],
    *,
    min_group: int = 2,
) -> List[List[int]]:
    """按决策日把行索引收成 listwise 组（不足 ``min_group`` 丢弃）。"""
    by: Dict[str, List[int]] = {}
    for i in indices:
        d = str(dates[int(i)])[:10]
        if not d:
            continue
        by.setdefault(d, []).append(int(i))
    out = [rows for rows in by.values() if len(rows) >= int(min_group)]
    out.sort(key=lambda rows: str(dates[rows[0]])[:10])
    return out


def _iter_group_batches(
    groups: Sequence[Sequence[int]],
    *,
    max_rows: int,
    shuffle: bool,
    rng: np.random.RandomState,
):
    """把日组打包成不超过 ``max_rows`` 的 batch（整组不拆）。"""
    order = list(range(len(groups)))
    if shuffle:
        rng.shuffle(order)
    cap = max(8, int(max_rows or 128))
    bucket: List[int] = []
    bucket_n = 0
    for gi in order:
        g = list(groups[int(gi)])
        if not g:
            continue
        if bucket and bucket_n + len(g) > cap:
            yield bucket
            bucket = []
            bucket_n = 0
        if len(g) > cap and not bucket:
            # 超大截面：仍整组交出（listwise 不可拆）
            yield g
            continue
        bucket.extend(g)
        bucket_n += len(g)
    if bucket:
        yield bucket


def _listnet_loss(pred, yb):
    """ListNet：日截面上 Softmax(y) 对 LogSoftmax(ŷ) 的交叉熵。"""
    torch = _require_torch()
    # 按组分别算；此处假定 yb/pred 已是同一批内若干完整日组拼接，
    # 调用方对单组或同日拼接组调用。多日拼接时需外部分组——见 _listwise_batch_loss。
    p = torch.nn.functional.log_softmax(pred, dim=0)
    t = torch.nn.functional.softmax(yb, dim=0)
    return -(t * p).sum()


def _listwise_batch_loss(pred, yb, dates_batch: Sequence[str]):
    """多日拼 batch：按日期拆开后对各日 ListNet 取均值。"""
    torch = _require_torch()
    if pred.numel() <= 1:
        return pred.sum() * 0.0
    # 单日或日期全同 → 一次
    uniq = []
    seen = set()
    for d in dates_batch:
        ds = str(d)[:10]
        if ds not in seen:
            seen.add(ds)
            uniq.append(ds)
    if len(uniq) <= 1:
        return _listnet_loss(pred, yb)
    losses = []
    for d in uniq:
        mask = [i for i, x in enumerate(dates_batch) if str(x)[:10] == d]
        if len(mask) < 2:
            continue
        idx = torch.as_tensor(mask, device=pred.device, dtype=torch.long)
        losses.append(_listnet_loss(pred.index_select(0, idx), yb.index_select(0, idx)))
    if not losses:
        return pred.sum() * 0.0
    return torch.stack(losses).mean()


def _resolve_loss(loss: Optional[str]):
    """返回 (name, pointwise_fn_or_None)；listwise 走专用路径。"""
    name = str(loss or "smooth_l1").strip().lower()
    if name in ("listwise", "listnet", "rank"):
        return "listwise", None
    torch = _require_torch()
    if name in ("smooth_l1", "huber", "regression"):
        return "smooth_l1", torch.nn.SmoothL1Loss(beta=1.0)
    if name in ("mse", "l2"):
        return "mse", torch.nn.MSELoss()
    return "smooth_l1", torch.nn.SmoothL1Loss(beta=1.0)


def _configure_torch_threads() -> None:
    """CPU 训练：提高算子内并行（OpenMP 已用 OMP_NUM_THREADS 时可并存）。"""
    torch = _require_torch()
    try:
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


def train_yhat_transformer(
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
    loss: str = "smooth_l1",
) -> Tuple[Any, Dict[str, Any]]:
    """返回 ``(state_dict_cpu, report)``；模型权重在 CPU。

    ``loss=listwise``：按决策日 ListNet；其余为点损失。
    """
    torch = _require_torch()
    from core.research.yhat_transformer.model import (
        SeqTransformer,
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
    dates = [str(d)[:10] for d in panel.dates]
    loss_name, loss_fn = _resolve_loss(loss)
    listwise = loss_name == "listwise"
    if tr:
        tr_np = np.asarray(tr, dtype=np.int64)
        if tr_np.size > 20000:
            rng_clip = np.random.RandomState(int(seed) + 7)
            tr_np = tr_np[rng_clip.choice(tr_np.size, size=20000, replace=False)]
        sample = X[tr_np]
        flat = sample.reshape(-1, sample.shape[-1])
        lo = np.nanpercentile(flat, 1.0, axis=0).astype(np.float32)
        hi = np.nanpercentile(flat, 99.0, axis=0).astype(np.float32)
        X = np.clip(X, lo, hi)
        g_tr = gap[tr_np]
        gap = np.clip(
            gap,
            float(np.nanpercentile(g_tr, 1.0)),
            float(np.nanpercentile(g_tr, 99.0)),
        ).astype(np.float32)

    d_in = int(X.shape[-1])
    model = SeqTransformer(
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
            if listwise:
                groups = _group_row_lists(dates, indices, min_group=2)
                if not groups:
                    return float("nan")
                row_iter = _iter_group_batches(
                    groups, max_rows=batch_size, shuffle=train, rng=rng
                )
            else:
                row_iter = (
                    [indices[int(j)] for j in bi.tolist()]
                    for bi in _batches(len(indices), batch_size, shuffle=train, rng=rng)
                )
            for rows in row_iter:
                xb = torch.as_tensor(X[rows], device=dev)
                gb = torch.as_tensor(gap[rows], device=dev)
                yb = torch.as_tensor(y[rows], device=dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                pred = model(xb, gb)
                if listwise:
                    loss = _listwise_batch_loss(
                        pred, yb, [dates[int(r)] for r in rows]
                    )
                else:
                    loss = loss_fn(pred, yb)  # type: ignore[misc]
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
        "loss": loss_name,
    }
    return best_state, report


def predict_yhat_transformer(
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
    from core.research.yhat_transformer.model import SeqTransformer, pick_device

    idx = list(range(panel.n)) if row_idx is None else [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    X = np.asarray(panel.X[idx], dtype=np.float32)
    gap = np.asarray(panel.gap[idx], dtype=np.float32)
    d_in = int(X.shape[-1])
    dev = pick_device(device)
    model = SeqTransformer(
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
    loss: str = "smooth_l1",
) -> Tuple[Any, Dict[str, Any]]:
    torch = _require_torch()
    from core.research.yhat_transformer.model import FactorMLP, count_parameters, pick_device
    from core.research.yhat_transformer.factor_panel import column_standardize_train

    _seed_all(int(seed))
    dev = pick_device(device)
    tr0 = _maybe_subsample(train_idx, max_train_rows=max_train_rows, seed=seed)
    tr, va = split_train_val_indices(dates, tr0, val_frac=0.2)
    Xz, _mu, _sd = column_standardize_train(X, tr)
    y = np.asarray(y, dtype=np.float32)
    day_s = [str(d)[:10] for d in dates]
    loss_name, loss_fn = _resolve_loss(loss)
    listwise = loss_name == "listwise"
    model = FactorMLP(d_in=int(Xz.shape[1]), hidden=hidden, dropout=dropout).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=float(lr), weight_decay=1e-4)
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
            if listwise:
                groups = _group_row_lists(day_s, indices, min_group=2)
                if not groups:
                    return float("nan")
                row_iter = _iter_group_batches(
                    groups, max_rows=batch_size, shuffle=train, rng=rng
                )
            else:
                row_iter = (
                    [indices[int(j)] for j in bi.tolist()]
                    for bi in _batches(len(indices), batch_size, shuffle=train, rng=rng)
                )
            for rows in row_iter:
                xb = torch.as_tensor(Xz[rows], device=dev)
                yb = torch.as_tensor(y[rows], device=dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                pred = model(xb)
                if listwise:
                    loss_t = _listwise_batch_loss(
                        pred, yb, [day_s[int(r)] for r in rows]
                    )
                else:
                    loss_t = loss_fn(pred, yb)  # type: ignore[misc]
                if train:
                    loss_t.backward()
                    opt.step()
                bs = int(yb.shape[0])
                total += float(loss_t.detach().cpu()) * bs
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
        "loss": loss_name,
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
    from core.research.yhat_transformer.model import FactorMLP, pick_device

    idx = [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    dev = pick_device(device)
    model = FactorMLP(d_in=int(d_in), hidden=hidden, dropout=0.0)
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
    loss: str = "smooth_l1",
) -> Tuple[Any, Dict[str, Any]]:
    torch = _require_torch()
    from core.research.yhat_transformer.model import (
        SeqFactorFusion,
        count_parameters,
        pick_device,
    )
    from core.research.yhat_transformer.factor_panel import column_standardize_train

    _seed_all(int(seed))
    _configure_torch_threads()
    dev = pick_device(device)
    tr0 = _maybe_subsample(train_idx, max_train_rows=max_train_rows, seed=seed)
    tr, va = split_train_val_indices(seq_panel.dates, tr0, val_frac=0.2)
    Xseq = np.asarray(seq_panel.X, dtype=np.float32)
    gap = np.asarray(seq_panel.gap, dtype=np.float32)
    y = np.asarray(seq_panel.y, dtype=np.float32)
    day_s = [str(d)[:10] for d in seq_panel.dates]
    loss_name, loss_fn = _resolve_loss(loss)
    listwise = loss_name == "listwise"
    Xf, _mu, _sd = column_standardize_train(fac_X, tr)
    if tr:
        # 勿对 train×L 全量 reshape 再 percentile（80 万×20 会爆内存/换页假死）
        tr_np = np.asarray(tr, dtype=np.int64)
        if tr_np.size > 20000:
            rng_clip = np.random.RandomState(int(seed) + 7)
            tr_np = tr_np[rng_clip.choice(tr_np.size, size=20000, replace=False)]
        sample = Xseq[tr_np]  # (n, L, F)
        flat = sample.reshape(-1, sample.shape[-1])
        lo = np.nanpercentile(flat, 1.0, axis=0).astype(np.float32)
        hi = np.nanpercentile(flat, 99.0, axis=0).astype(np.float32)
        Xseq = np.clip(Xseq, lo, hi)
        g_s = gap[tr_np]
        gap = np.clip(
            gap,
            float(np.nanpercentile(g_s, 1.0)),
            float(np.nanpercentile(g_s, 99.0)),
        ).astype(np.float32)

    print(
        f"    fusion build d_model={d_model} layers={nlayers} "
        f"n_train={len(tr)} batch={batch_size} device={dev}",
        flush=True,
    )
    model = SeqFactorFusion(
        d_seq_in=int(Xseq.shape[-1]),
        d_factor=int(Xf.shape[1]),
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        factor_hidden=int(factor_hidden),
        dropout=float(dropout),
    ).to(dev)
    # 与 numpy 共享存储（勿 ascontiguousarray 强制拷贝）；推理仍回传同一数组
    if not Xseq.flags["C_CONTIGUOUS"]:
        Xseq = np.ascontiguousarray(Xseq)
    if not gap.flags["C_CONTIGUOUS"]:
        gap = np.ascontiguousarray(gap)
    if not Xf.flags["C_CONTIGUOUS"]:
        Xf = np.ascontiguousarray(Xf)
    if not y.flags["C_CONTIGUOUS"]:
        y = np.ascontiguousarray(y)
    Xseq_t = torch.from_numpy(Xseq)
    gap_t = torch.from_numpy(gap)
    Xf_t = torch.from_numpy(Xf)
    y_t = torch.from_numpy(y)
    print(
        f"    fusion tensors ready n_params={count_parameters(model)} "
        f"Xseq={Xseq.nbytes/1e6:.0f}MB Xf={Xf.nbytes/1e6:.0f}MB",
        flush=True,
    )

    opt = torch.optim.AdamW(model.parameters(), lr=float(lr), weight_decay=1e-4)
    rng = np.random.RandomState(int(seed))
    best_state = None
    best_val = float("inf")
    bad = 0
    global_step = 0
    tr_arr = np.asarray(tr, dtype=np.int64)
    va_arr = np.asarray(va, dtype=np.int64) if va else np.zeros((0,), dtype=np.int64)
    bs = max(16, int(batch_size or 128))
    tr_groups = (
        _group_row_lists(day_s, tr_arr.tolist(), min_group=2) if listwise else []
    )
    if listwise and tr_groups:
        steps_per_ep = max(
            1,
            sum(
                1
                for _ in _iter_group_batches(
                    tr_groups, max_rows=bs, shuffle=False, rng=np.random.RandomState(0)
                )
            ),
        )
    else:
        steps_per_ep = max(1, int(math.ceil(tr_arr.size / float(bs))))
    # 步日志：约每 10% epoch；FUSION_LOG_EVERY=N 可覆盖
    try:
        log_every = int(os.environ.get("FUSION_LOG_EVERY") or 0)
    except ValueError:
        log_every = 0
    if log_every <= 0:
        log_every = max(50, min(500, max(1, steps_per_ep // 10)))
    print(
        f"    fusion fit start loss={loss_name} lr={float(lr):.2e} batch={bs} "
        f"steps/ep={steps_per_ep} log_every={log_every} "
        f"epochs={epochs} patience={patience} "
        f"n_tr={tr_arr.size} n_va={va_arr.size}",
        flush=True,
    )

    def _lr_now() -> float:
        try:
            return float(opt.param_groups[0].get("lr") or lr)
        except Exception:
            return float(lr)

    def _epoch(row_ids: np.ndarray, *, train: bool, ep: int) -> Tuple[float, float]:
        """返回 (mean_loss, last_grad_norm)；val 时 grad_norm=nan。"""
        nonlocal global_step
        if row_ids.size <= 0:
            return float("nan"), float("nan")
        model.train(train)
        total = 0.0
        n_seen = 0
        last_gn = float("nan")
        if listwise:
            groups = _group_row_lists(day_s, row_ids.tolist(), min_group=2)
            if not groups:
                return float("nan"), float("nan")
            batches = list(
                _iter_group_batches(groups, max_rows=bs, shuffle=train, rng=rng)
            )
        else:
            order = row_ids.copy()
            if train:
                rng.shuffle(order)
            batches = [
                order[start : start + bs].tolist()
                for start in range(0, int(order.size), bs)
            ]
        n_steps = max(1, len(batches))
        t0 = _time.time()
        ctx = torch.enable_grad() if train else torch.no_grad()
        with ctx:
            for si, rows in enumerate(batches):
                rows_np = np.asarray(rows, dtype=np.int64)
                idx = torch.from_numpy(rows_np)
                xs = Xseq_t[idx].to(dev)
                gg = gap_t[idx].to(dev)
                xf = Xf_t[idx].to(dev)
                yb = y_t[idx].to(dev)
                if train:
                    opt.zero_grad(set_to_none=True)
                pred = model(xs, gg, xf)
                if listwise:
                    loss = _listwise_batch_loss(
                        pred, yb, [day_s[int(r)] for r in rows]
                    )
                else:
                    loss = loss_fn(pred, yb)  # type: ignore[misc]
                loss_v = float(loss.detach().item())
                nbs = int(yb.shape[0])
                total += loss_v * nbs
                n_seen += nbs
                if train:
                    loss.backward()
                    gn = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    try:
                        last_gn = float(
                            gn.detach().item() if hasattr(gn, "detach") else gn
                        )
                    except Exception:
                        last_gn = float(gn) if gn == gn else float("nan")
                    opt.step()
                    global_step += 1
                    if (si + 1) % log_every == 0 or (si + 1) == n_steps:
                        done = si + 1
                        elapsed = _time.time() - t0
                        speed = done / max(elapsed, 1e-6)
                        eta = (n_steps - done) / max(speed, 1e-6)
                        print(
                            f"    fusion step={global_step} "
                            f"ep={ep}/{epochs} [{done}/{n_steps}] "
                            f"loss={loss_v:.4f} avg={total / max(n_seen, 1):.4f} "
                            f"lr={_lr_now():.2e} grad={last_gn:.3f} "
                            f"{speed:.1f}it/s eta={eta:.0f}s",
                            flush=True,
                        )
                del xs, gg, xf, yb, loss
        return total / float(max(1, n_seen)), last_gn

    t_fit0 = _time.time()
    for ep in range(1, max(1, int(epochs)) + 1):
        t_ep = _time.time()
        tr_loss, grad_n = _epoch(tr_arr, train=True, ep=ep)
        va_loss, _ = (
            _epoch(va_arr, train=False, ep=ep)
            if va_arr.size
            else (tr_loss, float("nan"))
        )
        if va_loss < best_val - 1e-5:
            best_val = float(va_loss)
            bad = 0
            best_state = {
                k: v.detach().cpu().clone() for k, v in model.state_dict().items()
            }
            tag = "*best"
        else:
            bad += 1
            tag = f"patience={bad}/{patience}"
        ep_sec = _time.time() - t_ep
        print(
            f"    fusion ep={ep}/{epochs} done "
            f"train_loss={tr_loss:.4f} val_loss={va_loss:.4f} best={best_val:.4f} "
            f"lr={_lr_now():.2e} grad={grad_n:.3f} step={global_step} "
            f"n_tr={tr_arr.size} n_va={va_arr.size} "
            f"{ep_sec:.1f}s {tag}",
            flush=True,
        )
        if bad >= max(2, int(patience)):
            print(
                f"    fusion early stop at ep={ep} step={global_step} "
                f"best_val={best_val:.4f} elapsed={_time.time()-t_fit0:.0f}s",
                flush=True,
            )
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
        "lr": float(lr),
        "loss": loss_name,
        "global_step": int(global_step),
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
    from core.research.yhat_transformer.model import SeqFactorFusion, pick_device

    idx = [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    dev = pick_device(device)
    model = SeqFactorFusion(
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
    "train_yhat_transformer",
    "predict_yhat_transformer",
    "train_factor_mlp",
    "predict_factor_mlp",
    "train_seq_factor_fusion",
    "predict_seq_factor_fusion",
]
