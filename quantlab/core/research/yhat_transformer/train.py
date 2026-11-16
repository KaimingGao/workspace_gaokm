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


def _cosine_warmup_lr_mult(
    step: int,
    *,
    warmup: int = 100,
    total: int,
    min_ratio: float = 0.01,
) -> float:
    """线性 warmup → cosine 衰减到 ``min_ratio * base_lr``。``step`` 为已完成步数（≥1）。"""
    s = max(0, int(step))
    w = max(0, int(warmup))
    t = max(1, int(total))
    lo = max(0.0, min(1.0, float(min_ratio)))
    if w > 0 and s <= w:
        return float(s) / float(w)
    denom = max(1, t - w)
    progress = min(1.0, max(0.0, float(s - w) / float(denom)))
    cos = 0.5 * (1.0 + math.cos(math.pi * progress))
    return float(lo + (1.0 - lo) * cos)


def _owned_f32(arr: np.ndarray) -> np.ndarray:
    """可写 float32 拷贝（避免与面板共享存储后 clip 再翻倍）。"""
    out = np.array(arr, dtype=np.float32, copy=True)
    if not out.flags.writeable:
        out = np.array(out, dtype=np.float32, copy=True)
    return out


def _clip_inplace_rows(
    X: np.ndarray,
    gap: np.ndarray,
    row_ids: Sequence[int],
    *,
    seed: int,
) -> None:
    """按 train 行估计分位，原地 clip X/gap（勿再分配全量副本）。"""
    if not row_ids:
        return
    tr_np = np.asarray(row_ids, dtype=np.int64)
    if tr_np.size > 20000:
        rng_clip = np.random.RandomState(int(seed) + 7)
        tr_np = tr_np[rng_clip.choice(tr_np.size, size=20000, replace=False)]
    sample = X[tr_np]
    flat = sample.reshape(-1, sample.shape[-1])
    lo = np.nanpercentile(flat, 1.0, axis=0).astype(np.float32)
    hi = np.nanpercentile(flat, 99.0, axis=0).astype(np.float32)
    np.clip(X, lo, hi, out=X)
    g_s = gap[tr_np]
    np.clip(
        gap,
        float(np.nanpercentile(g_s, 1.0)),
        float(np.nanpercentile(g_s, 99.0)),
        out=gap,
    )


# 早停只用训练日历末尾这段；下限 10%，把其余样本留给训练。
_VAL_DAY_FRAC = 0.1


def split_train_val_indices(
    dates: Sequence[str],
    train_idx: Sequence[int],
    *,
    val_frac: float = _VAL_DAY_FRAC,
) -> Tuple[List[int], List[int]]:
    """按日历从训练集末尾切 val（不动 Holdout）。

    ``val_frac`` 钳在 0.1–0.4，默认取下限。至少 2 个验证日，训练日至少留 3 天。
    """
    row_days = [str(dates[i])[:10] for i in train_idx]
    uniq = sorted({d for d in row_days if d})
    if len(uniq) < 8:
        return list(train_idx), []
    frac = min(0.4, max(0.1, float(val_frac if val_frac else _VAL_DAY_FRAC)))
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
    """按决策日把行索引收成整日组（不足 ``min_group`` 丢弃）。"""
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
            # 超大截面：仍整组交出（排序损失按日计算，不可拆）
            yield g
            continue
        bucket.extend(g)
        bucket_n += len(g)
    if bucket:
        yield bucket


# 与 Arm·L ``LAMBDARANK_TRUNCATION_LEVEL`` 对齐：当前分数前 100 名。
_LAMBDARANK_TRUNCATION = 100


def _lambdarank_group_loss(pred, yb, *, truncation: int = _LAMBDARANK_TRUNCATION):
    """一天一组的 LambdaRank 成对损失（对计入的对取平均）。

    标签是稠密名次 ``0..n-1``（收益高 = 名次高）。``w_ij`` 用当前分数的名次断开梯度，
    只沿 ``ŷ_i - ŷ_j`` 反传。一对里至少有一个落在当前前 ``truncation`` 名才计入；
    外层从高到低扫这些位置，只和更靠后的位置配对，每对只算一次。

    鲁棒性对齐 LightGBM ``lambdarank_norm``（默认开）：权重再除以
    ``0.01+|分数差|``，分数已经拉开的对少推动；整天 λ 质量再乘
    ``log2(1+S)/S``。IDCG 只用前 ``truncation`` 名，尾部不稀释头部。
    """
    torch = _require_torch()
    finite = torch.isfinite(pred) & torch.isfinite(yb)
    if int(finite.sum().item()) < 2:
        return pred.sum() * 0.0
    if not bool(finite.all().item()):
        pred = pred[finite]
        yb = yb[finite]
    n = int(pred.shape[0])
    k = max(1, min(int(truncation), n))
    # 稠密名次：argsort 升序，收益最高的名次是 n-1。平局按原序相邻排开，与 Arm·L 一致。
    order_y = torch.argsort(yb.detach(), stable=True)
    ranks = torch.empty(n, dtype=pred.dtype, device=pred.device)
    ranks[order_y] = torch.arange(n, dtype=pred.dtype, device=pred.device)
    order = torch.argsort(pred.detach(), descending=True, stable=True)
    pos = torch.arange(n, dtype=pred.dtype, device=pred.device)
    discount = 1.0 / torch.log2(pos + 2.0)
    # LightGBM CalMaxDCGAtK(truncation)：理想名次只累加前 k 个位置。
    ideal_gain = torch.arange(n - 1, n - 1 - k, -1, dtype=pred.dtype, device=pred.device)
    idcg = (ideal_gain * discount[:k]).sum().clamp_min(1e-6)
    s_i = pred[order[:k]].unsqueeze(1)
    s_j = pred[order].unsqueeze(0)
    r_i = ranks[order[:k]].unsqueeze(1)
    r_j = ranks[order].unsqueeze(0)
    d_i = discount[:k].unsqueeze(1)
    d_j = discount.unsqueeze(0)
    sign = torch.sign(r_i - r_j)
    oriented = sign * (s_i - s_j)
    gap = oriented.detach().abs()
    weight = ((r_i - r_j).abs() * (d_i - d_j).abs() / idcg).detach()
    weight = weight / (0.01 + gap)
    pair = torch.nn.functional.softplus(-oriented) * weight
    j_pos = pos.unsqueeze(0)
    i_pos = pos[:k].unsqueeze(1)
    keep = j_pos > i_pos
    pair = pair.masked_fill(~keep, 0.0)
    # sum |λ| 的对数压缩。分母断开，不把归一化本身再求导。
    sig = torch.sigmoid(-oriented.detach())
    mass = (weight * sig).masked_fill(~keep, 0.0).sum() * 2.0
    if float(mass.detach()) > 0.0:
        pair = pair * (torch.log2(1.0 + mass) / mass).detach()
    n_pairs = float(k * (2 * n - k - 1) / 2)
    return pair.sum() / max(n_pairs, 1.0)


def _rank_batch_loss(pred, yb, dates_batch: Sequence[str]):
    """多日拼 batch：按日期拆开，各日 LambdaRank 再取均值。"""
    torch = _require_torch()
    if pred.numel() <= 1:
        return pred.sum() * 0.0
    uniq = []
    seen = set()
    for d in dates_batch:
        ds = str(d)[:10]
        if ds not in seen:
            seen.add(ds)
            uniq.append(ds)
    if len(uniq) <= 1:
        return _lambdarank_group_loss(pred, yb)
    losses = []
    for d in uniq:
        mask = [i for i, x in enumerate(dates_batch) if str(x)[:10] == d]
        if len(mask) < 2:
            continue
        idx = torch.as_tensor(mask, device=pred.device, dtype=torch.long)
        losses.append(
            _lambdarank_group_loss(pred.index_select(0, idx), yb.index_select(0, idx))
        )
    if not losses:
        return pred.sum() * 0.0
    return torch.stack(losses).mean()


def _resolve_loss(loss: Optional[str]):
    """返回 (name, pointwise_fn_or_None)；排序头走 LambdaRank 成对路径。"""
    name = str(loss or "smooth_l1").strip().lower()
    if name == "lambdarank":
        return "lambdarank", None
    torch = _require_torch()
    if name in ("smooth_l1", "huber", "regression"):
        return "smooth_l1", torch.nn.SmoothL1Loss(beta=1.0)
    if name in ("mse", "l2"):
        return "mse", torch.nn.MSELoss()
    return "smooth_l1", torch.nn.SmoothL1Loss(beta=1.0)


def _configure_torch_threads() -> int:
    """Arm·T 的 intra-op 线程。默认 4。

    BLAS / OpenMP 仍由启动环境保持 1，LightGBM 不跟着放大。
    全局面板的 PyTorch 高级索引已改成小 batch 的 NumPy 切片。
    """
    torch = _require_torch()
    try:
        n = int(os.environ.get("TORCH_NUM_THREADS") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = 4
    n = max(1, n)
    try:
        torch.set_num_threads(n)
        try:
            torch.set_num_interop_threads(1)
        except Exception:
            pass
    except Exception:
        return 1
    return n


def _batch_to_device(*arrays: np.ndarray, rows: np.ndarray, device) -> tuple:
    """按行从 numpy 取小 batch 再 as_tensor，避免 X_t[idx] OpenMP 高级索引崩溃。"""
    torch = _require_torch()
    out = []
    for arr in arrays:
        sl = np.ascontiguousarray(arr[rows])
        t = torch.as_tensor(sl, device=device)
        out.append(t)
    return tuple(out)


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


def _subsample_whole_groups(
    dates: Sequence[str],
    indices: Sequence[int],
    *,
    max_rows: Optional[int],
    seed: int,
    latest: bool = False,
    min_group: int = 2,
) -> List[int]:
    """按决策日整组抽样。一组放不进剩余名额就停，不再改收更小的日期。"""
    groups = _group_row_lists(dates, indices, min_group=min_group)
    if not groups:
        return []
    cap = int(max_rows or 0)
    total = sum(len(g) for g in groups)
    if cap <= 0 or total <= cap:
        return [int(i) for i in indices]
    order = list(range(len(groups)))
    if latest:
        order.reverse()
    else:
        rng = np.random.RandomState(int(seed))
        rng.shuffle(order)
    kept: List[int] = []
    n = 0
    for gi in order:
        g = groups[int(gi)]
        if n + len(g) > cap:
            if n == 0:
                return list(g)
            # 放不进就停。再往后挑更小的日期会偏向股票少的截面。
            break
        kept.extend(g)
        n += len(g)
    return kept


def _prepare_rank_indices(
    dates: Sequence[str],
    train_idx: Sequence[int],
    *,
    max_train_rows: Optional[int],
    seed: int,
    val_idx: Optional[Sequence[int]] = None,
) -> Tuple[List[int], List[int]]:
    """先按日历切验证日；只对 **train 日组** 做 ``max_train_rows`` 抽样，val 保持完整日。"""
    if val_idx is None:
        tr_full, va_full = split_train_val_indices(dates, list(train_idx))
    else:
        tr_full = list(train_idx)
        va_full = list(val_idx)
    cap = int(max_train_rows or 0)
    tr = _subsample_whole_groups(
        dates, tr_full, max_rows=cap, seed=seed, latest=False
    )
    va = list(va_full)
    return tr, va


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
    patience: int = 6,
    max_train_rows: Optional[int] = None,
    loss: str = "smooth_l1",
) -> Tuple[Any, Dict[str, Any]]:
    """返回 ``(state_dict_cpu, report)``；模型权重在 CPU。

    ``loss=lambdarank``：按决策日做 LambdaRank 成对损失；其余为点损失。
    """
    torch = _require_torch()
    from core.research.yhat_transformer.model import (
        SeqTransformer,
        count_parameters,
        pick_device,
    )

    _seed_all(int(seed))
    dev = pick_device(device)
    loss_name, loss_fn = _resolve_loss(loss)
    grouped = loss_name == "lambdarank"
    dates = [str(d)[:10] for d in panel.dates]
    if grouped:
        tr, va = _prepare_rank_indices(
            dates,
            train_idx,
            max_train_rows=max_train_rows,
            seed=seed,
            val_idx=val_idx,
        )
    else:
        # 先切验证日，再只抽训练日，避免抽样把日历末尾的验证日抽掉。
        if val_idx is None:
            tr_full, va = split_train_val_indices(panel.dates, list(train_idx))
        else:
            tr_full = list(train_idx)
            va = list(val_idx)
        tr = _maybe_subsample(tr_full, max_train_rows=max_train_rows, seed=seed)

    X = _owned_f32(panel.X)
    gap = _owned_f32(panel.gap)
    y = _owned_f32(panel.y)
    if tr:
        _clip_inplace_rows(X, gap, tr, seed=int(seed))

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
            if grouped:
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
                if grouped:
                    loss = _rank_batch_loss(
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
            if bad >= max(1, int(patience)):
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


def train_seq_factor_transformer(
    seq_panel: Any,
    fac_X: Optional[np.ndarray],
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
    patience: int = 6,
    max_train_rows: Optional[int] = None,
    loss: str = "smooth_l1",
    holdout_idx: Optional[Sequence[int]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Arm·T 训练（固定调用方 seed，默认 42）。

    ``fac_X`` 须已由调用方做完与 Arm·L 对齐的 L2 立方截面 CS-z；
    此处不再做训练段列标准化。
    ``holdout_idx`` 只在每个 epoch 结束后打指标，不参与早停，也不决定留下哪一版权重。
    """
    torch = _require_torch()
    from core.research.yhat_transformer.model import (
        SeqFactorTransformer,
        count_parameters,
        pick_device,
    )

    import gc

    _seed_all(int(seed))
    n_threads = _configure_torch_threads()
    dev = pick_device(device)
    loss_name, loss_fn = _resolve_loss(loss)
    grouped = loss_name == "lambdarank"
    if grouped:
        tr, va = _prepare_rank_indices(
            seq_panel.dates,
            train_idx,
            max_train_rows=max_train_rows,
            seed=seed,
        )
    else:
        # 先切验证日，再只抽训练日（与排序头一致，验证日保持完整）。
        tr_full, va = split_train_val_indices(seq_panel.dates, list(train_idx))
        tr = _maybe_subsample(tr_full, max_train_rows=max_train_rows, seed=seed)

    # 先拆出独立缓冲，再清空面板大数组，避免 clip 时与面板双份并存
    Xseq = _owned_f32(seq_panel.X)
    gap = _owned_f32(seq_panel.gap)
    y = _owned_f32(seq_panel.y)
    day_s = [str(d)[:10] for d in seq_panel.dates]
    code_s = [str(c) for c in list(getattr(seq_panel, "codes", []) or [])]
    try:
        seq_panel.X = np.empty((0, 0, 0), dtype=np.float32)
        seq_panel.gap = np.empty((0,), dtype=np.float32)
        seq_panel.y = np.empty((0,), dtype=np.float32)
    except Exception:
        pass
    # 因子已 CS-z：只保证独立 float32 缓冲，不再列标准化
    fac_owned = np.asarray(fac_X, dtype=np.float32)
    if (
        fac_owned.dtype != np.float32
        or not fac_owned.flags.writeable
        or not getattr(fac_owned.flags, "owndata", True)
    ):
        fac_owned = _owned_f32(fac_X)
    del fac_X
    gc.collect()
    Xf = fac_owned
    if int(Xf.shape[0]) != int(Xseq.shape[0]):
        raise ValueError(
            f"Arm·T 行数不对齐：Xseq N={int(Xseq.shape[0])} Xf N={int(Xf.shape[0])}；"
            "须先 align_seq_and_factor"
        )
    if int(d_model) % max(1, int(nhead)) != 0:
        raise ValueError(
            f"d_model={int(d_model)} 须能被 nhead={int(nhead)} 整除"
        )
    if tr:
        # 勿对 train×L 全量 reshape 再 percentile（80 万×20 会爆内存/换页假死）
        _clip_inplace_rows(Xseq, gap, tr, seed=int(seed))
    gc.collect()

    def _count_days(rows: Sequence[int]) -> int:
        seen = set()
        n_days = len(day_s)
        for i in rows:
            ii = int(i)
            if 0 <= ii < n_days and day_s[ii]:
                seen.add(day_s[ii])
        return len(seen)

    n_tr_days = _count_days(tr)
    n_va_days = _count_days(va)
    print(
        f"    transformer build d_model={d_model} layers={nlayers} "
        f"n_train={len(tr)} n_val={len(va)} "
        f"val_days={n_va_days}/{n_tr_days + n_va_days} "
        f"batch={batch_size} device={dev} "
        f"Xseq={Xseq.nbytes/1e6:.0f}MB Xf={Xf.nbytes/1e6:.0f}MB",
        flush=True,
    )
    model = SeqFactorTransformer(
        d_seq_in=int(Xseq.shape[-1]),
        d_factor=int(Xf.shape[1]),
        d_model=int(d_model),
        nhead=int(nhead),
        nlayers=int(nlayers),
        factor_hidden=int(factor_hidden),
        dropout=float(dropout),
    ).to(dev)
    print(
        f"    transformer model ready n_params={count_parameters(model)}",
        flush=True,
    )
    # 保持 numpy 面板；按 batch as_tensor，勿对全量 X_t[idx]（libtorch OpenMP 曾 SIGSEGV）
    if not Xseq.flags["C_CONTIGUOUS"]:
        Xseq = np.ascontiguousarray(Xseq)
    if not gap.flags["C_CONTIGUOUS"]:
        gap = np.ascontiguousarray(gap)
    if not Xf.flags["C_CONTIGUOUS"]:
        Xf = np.ascontiguousarray(Xf)
    if not y.flags["C_CONTIGUOUS"]:
        y = np.ascontiguousarray(y)
    print(
        f"    transformer buffers ready n_params={count_parameters(model)} "
        f"Xseq={Xseq.nbytes/1e6:.0f}MB Xf={Xf.nbytes/1e6:.0f}MB "
        f"(batch via numpy, no full torch index)",
        flush=True,
    )

    base_lr = float(lr)
    opt = torch.optim.AdamW(model.parameters(), lr=base_lr, weight_decay=1e-4)
    rng = np.random.RandomState(int(seed))
    best_state = None
    best_val = float("inf")
    bad = 0
    global_step = 0
    tr_arr = np.asarray(tr, dtype=np.int64)
    # va / holdout 可能是 ndarray，不能写 `if x` / `x or []`
    va_arr = np.asarray([] if va is None else va, dtype=np.int64)
    ho_src = [] if holdout_idx is None else holdout_idx
    ho_arr = np.asarray(ho_src, dtype=np.int64)
    if ho_arr.size:
        ho_arr = ho_arr[(ho_arr >= 0) & (ho_arr < int(y.shape[0]))]
    bs = max(16, int(batch_size or 128))
    tr_groups = (
        _group_row_lists(day_s, tr_arr.tolist(), min_group=2) if grouped else []
    )
    if grouped and tr_groups:
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
    try:
        warmup_steps = int(os.environ.get("TRANSFORMER_WARMUP_STEPS") or 100)
    except ValueError:
        warmup_steps = 100
    warmup_steps = max(0, warmup_steps)
    total_steps = max(1, int(steps_per_ep) * max(1, int(epochs)))
    # 步日志：约每 10% epoch；TRANSFORMER_LOG_EVERY=N 可覆盖
    try:
        log_every = int(os.environ.get("TRANSFORMER_LOG_EVERY") or 0)
    except ValueError:
        log_every = 0
    if log_every <= 0:
        log_every = max(50, min(500, max(1, steps_per_ep // 10)))
    print(
        f"    transformer fit start loss={loss_name} lr={base_lr:.2e} "
        f"sched=cosine warmup={warmup_steps} total_steps={total_steps} "
        f"threads={n_threads} batch={bs} steps/ep={steps_per_ep} log_every={log_every} "
        f"epochs={epochs} patience={patience} "
        f"n_tr={tr_arr.size} n_va={va_arr.size} "
        f"val_days={n_va_days}/{n_tr_days + n_va_days}"
        + (
            f" n_groups={len(tr_groups)} "
            f"group_med={sorted(len(g) for g in tr_groups)[len(tr_groups) // 2]}"
            if grouped and tr_groups
            else ""
        ),
        flush=True,
    )

    def _set_lr(step: int) -> float:
        mult = _cosine_warmup_lr_mult(
            step, warmup=warmup_steps, total=total_steps, min_ratio=0.01
        )
        cur = base_lr * mult
        for pg in opt.param_groups:
            pg["lr"] = cur
        return cur

    def _lr_now() -> float:
        try:
            return float(opt.param_groups[0].get("lr") or base_lr)
        except Exception:
            return base_lr

    # warmup 约定 step≥1；若从 0 起则首步 lr=0，等于白跑一步
    _set_lr(1)

    def _epoch(row_ids: np.ndarray, *, train: bool, ep: int) -> Tuple[float, float]:
        """返回 (mean_loss, last_grad_norm)；val 时 grad_norm=nan。"""
        nonlocal global_step
        if row_ids.size <= 0:
            return float("nan"), float("nan")
        model.train(train)
        total = 0.0
        n_seen = 0
        last_gn = float("nan")
        if grouped:
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
                xs, gg, xf, yb = _batch_to_device(
                    Xseq, gap, Xf, y, rows=rows_np, device=dev
                )
                if train:
                    opt.zero_grad(set_to_none=True)
                pred = model(xs, gg, xf)
                if grouped:
                    loss = _rank_batch_loss(
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
                    _set_lr(global_step + 1)  # 下一步（仍按「即将执行的步号」）
                    if (si + 1) % log_every == 0 or (si + 1) == n_steps:
                        done = si + 1
                        elapsed = _time.time() - t0
                        speed = done / max(elapsed, 1e-6)
                        eta = (n_steps - done) / max(speed, 1e-6)
                        print(
                            f"    transformer step={global_step} "
                            f"ep={ep}/{epochs} [{done}/{n_steps}] "
                            f"loss={loss_v:.4f} avg={total / max(n_seen, 1):.4f} "
                            f"lr={_lr_now():.2e} grad={last_gn:.3f} "
                            f"{speed:.1f}it/s eta={eta:.0f}s",
                            flush=True,
                        )
                del xs, gg, xf, yb, loss
        return total / float(max(1, n_seen)), last_gn

    def _fmt4(v: Any) -> str:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return "nan"
        if x != x:
            return "nan"
        return f"{x:.4f}"

    def _score_holdout() -> Optional[Dict[str, Any]]:
        """当前权重在 Holdout 行上的截面指标。不写回 best，也不改 patience。"""
        if ho_arr.size <= 0:
            return None
        from core.research.yhat_transformer.metrics import daily_cs_ic_report, sign_hit

        was_training = bool(model.training)
        model.eval()
        outs: List[np.ndarray] = []
        try:
            with torch.no_grad():
                for start in range(0, int(ho_arr.size), bs):
                    rows = ho_arr[start : start + bs]
                    xs, gg, xf = _batch_to_device(
                        Xseq, gap, Xf, rows=rows, device=dev
                    )
                    pred = model(xs, gg, xf)
                    outs.append(pred.detach().cpu().numpy().astype(np.float64))
                    del xs, gg, xf, pred
        finally:
            if was_training:
                model.train(True)
        preds = (
            np.concatenate(outs, axis=0)
            if outs
            else np.zeros((0,), dtype=np.float64)
        )
        ys = np.asarray(y[ho_arr], dtype=np.float64)
        metas = [
            {
                "code": code_s[int(i)] if int(i) < len(code_s) else "",
                "date": day_s[int(i)] if int(i) < len(day_s) else "",
                "decision_date": day_s[int(i)] if int(i) < len(day_s) else "",
            }
            for i in ho_arr.tolist()
        ]
        pack = daily_cs_ic_report(preds.tolist(), ys.tolist(), metas)
        pack["sign_hit"] = sign_hit(preds.tolist(), ys.tolist())
        pack["n"] = int(ys.shape[0])
        if grouped:
            from core.research.yhat_transformer.metrics import mean_daily_ndcg

            pack["cs_ndcg_100"] = mean_daily_ndcg(
                preds.tolist(), ys.tolist(), metas, k=_LAMBDARANK_TRUNCATION
            )
        return pack

    holdout_by_epoch: List[Dict[str, Any]] = []
    t_fit0 = _time.time()
    for ep in range(1, max(1, int(epochs)) + 1):
        t_ep = _time.time()
        tr_loss, grad_n = _epoch(tr_arr, train=True, ep=ep)
        va_loss, _ = (
            _epoch(va_arr, train=False, ep=ep)
            if va_arr.size
            else (tr_loss, float("nan"))
        )
        # 排序日组不足 2 只 → va_loss=nan；回退 train_loss，避免永不落盘 best
        score = va_loss if math.isfinite(va_loss) else tr_loss
        if math.isfinite(score) and score < best_val - 1e-5:
            best_val = float(score)
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
            f"    transformer ep={ep}/{epochs} done "
            f"train_loss={tr_loss:.4f} val_loss={va_loss:.4f} best={best_val:.4f} "
            f"lr={_lr_now():.2e} grad={grad_n:.3f} step={global_step} "
            f"n_tr={tr_arr.size} n_va={va_arr.size} "
            f"{ep_sec:.1f}s {tag}",
            flush=True,
        )
        if ho_arr.size:
            try:
                ho_pack = _score_holdout()
            except Exception as exc:  # noqa: BLE001
                ho_pack = None
                print(
                    f"    transformer ep={ep}/{epochs} holdout 失败："
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            if ho_pack is not None:
                row: Dict[str, Any] = {"epoch": int(ep)}
                ho_keys = [
                    "cs_rank_ic",
                    "cs_rank_icir",
                    "sign_hit",
                    "cs_top_mean_y",
                ]
                if grouped:
                    ho_keys.append("cs_ndcg_100")
                for key in ho_keys:
                    raw = ho_pack.get(key)
                    try:
                        num = float(raw)
                    except (TypeError, ValueError):
                        num = float("nan")
                    row[key] = num if num == num else None
                row["n"] = int(ho_pack.get("n") or 0)
                holdout_by_epoch.append(row)
                ndcg_bit = ""
                if grouped:
                    ndcg_bit = f" ndcg100={_fmt4(ho_pack.get('cs_ndcg_100'))}"
                print(
                    f"    transformer ep={ep}/{epochs} holdout "
                    f"rankIC={_fmt4(ho_pack.get('cs_rank_ic'))} "
                    f"ICIR={_fmt4(ho_pack.get('cs_icir'))} "
                    f"sign_hit={_fmt4(ho_pack.get('sign_hit'))} "
                    f"topY={_fmt4(ho_pack.get('cs_top_mean_y'))}"
                    f"{ndcg_bit} "
                    f"n={ho_pack.get('n')}",
                    flush=True,
                )
        if bad >= max(1, int(patience)):
            print(
                f"    transformer early stop at ep={ep} step={global_step} "
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
        "lr": float(base_lr),
        "lr_sched": "cosine",
        "warmup_steps": int(warmup_steps),
        "loss": loss_name,
        "global_step": int(global_step),
        "holdout_by_epoch": holdout_by_epoch,
        "Xf": Xf,
        "Xseq": Xseq,
        "gap": gap,
        "y": y,
        "dates": day_s,
    }


def predict_seq_factor_transformer(
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
    from core.research.yhat_transformer.model import SeqFactorTransformer, pick_device

    idx = [int(i) for i in row_idx]
    if not idx:
        return np.zeros((0,), dtype=np.float64)
    dev = pick_device(device)
    model = SeqFactorTransformer(
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
    Xseq_np = np.ascontiguousarray(np.asarray(Xseq, dtype=np.float32))
    gap_np = np.ascontiguousarray(np.asarray(gap, dtype=np.float32))
    Xf_np = np.ascontiguousarray(np.asarray(Xf, dtype=np.float32))
    outs: List[np.ndarray] = []
    bs = max(32, int(batch_size))
    order = np.asarray(idx, dtype=np.int64)
    with torch.no_grad():
        for start in range(0, int(order.size), bs):
            rows = order[start : start + bs]
            xs, gg, xf = _batch_to_device(
                Xseq_np, gap_np, Xf_np, rows=rows, device=dev
            )
            pred = model(xs, gg, xf)
            outs.append(pred.detach().cpu().numpy().astype(np.float64))
    return np.concatenate(outs, axis=0) if outs else np.zeros((0,), dtype=np.float64)


__all__ = [
    "split_train_val_indices",
    "train_yhat_transformer",
    "predict_yhat_transformer",
    "train_seq_factor_transformer",
    "predict_seq_factor_transformer",
]
