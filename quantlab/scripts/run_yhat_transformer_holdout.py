"""离线 Holdout：四头 ŷ_* Scheme LT 双臂对照（默认 head=oo；研究轨，不入库）。

臂（固定 L+T）：
  L = 因子 + LightGBM（硬闸基线；回归走 live ``fit_*_lgb_matrix`` + L2 立方 CS-z）
  T = 双支路晚融合（序列塔 ∥ 因子支；因子支与 L 同用 L2 立方截面 CS-z）

Arm·L 固定全量，对齐 live。``--full-aoo`` / ``--atc-core`` 只改 Arm·T 因子列，不改 CS-z。
用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_yhat_transformer_holdout.py --head oo
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# torch + lightgbm 在 macOS 上易撞双份 libomp
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")


def _parse_codes(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    out: List[str] = []
    seen = set()
    for part in str(raw).replace(";", ",").split(","):
        s = part.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out or None


def _rss_mb() -> Optional[float]:
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)
    except Exception:
        return None


def _load_bars(codes: Sequence[str], *, lookback: int) -> Dict[str, List[dict]]:
    from core.research.portfolio_bars import load_portfolio_stock_bars

    n = len(codes)
    print(f"load_bars start n={n} lookback={int(lookback)}", flush=True)
    t0 = time.time()
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        list(codes),
        lookback=int(lookback),
        fetch_fundamentals=False,
        offline_ok=True,
    )
    if failures:
        print(f"日线失败 {len(failures)}：{failures[:6]}", flush=True)
    out = {str(k): list(v) for k, v in (stock_bars or {}).items() if v}
    print(
        f"load_bars done ok={len(out)}/{n} {time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )
    return out


def _eval_pack(preds, ys, metas) -> Dict[str, Any]:
    from core.research.yhat_transformer.metrics import (
        daily_cs_ic_report,
        pooled_pearson,
        sign_hit,
    )

    out = daily_cs_ic_report(preds, ys, metas)
    out["pooled_ic"] = pooled_pearson(preds, ys)
    out["sign_hit"] = sign_hit(preds, ys)
    out["n"] = len(ys)
    return out


def _metas(codes: Sequence[str], dates: Sequence[str]) -> List[dict]:
    return [
        {"code": c, "date": d, "decision_date": d}
        for c, d in zip(codes, dates)
    ]


def _arm_l_max_train_rows() -> int:
    """显式环境变量才截断训练行；默认 0=不截（对齐 live 全样本）。"""
    for key in ("YHAT_ARM_L_MAX_TRAIN",):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            n = int(raw)
        except ValueError:
            continue
        # 0 与未设置相同：不截断。正数才生效，并守住过小的上限。
        if n <= 0:
            return 0
        return max(5000, n)
    return 0


def _arm_t_max_train_rows(n_train: int, args: Any) -> int:
    """Arm·T 训练行上限：``t_sample_frac``（缺省 0.1）；返回 0=不截断。"""
    n_train = max(0, int(n_train or 0))
    frac_raw = getattr(args, "t_sample_frac", None)
    try:
        frac = 0.1 if frac_raw is None else float(frac_raw)
    except (TypeError, ValueError):
        frac = 0.1
    if frac != frac or frac <= 0:
        frac = 0.1
    if frac >= 1.0 - 1e-12:
        return 0
    if n_train <= 0:
        return 0
    return max(1, int(round(n_train * min(1.0, frac))))


def _arm_l_maybe_subsample_train(
    train_idx: Sequence[int], *, max_tr: int, n_features: int
) -> List[int]:
    tr = [int(i) for i in train_idx]
    if max_tr <= 0 or len(tr) <= max_tr:
        return tr
    rng = np.random.RandomState(42)
    pick = rng.choice(len(tr), size=int(max_tr), replace=False)
    out = [tr[int(i)] for i in np.asarray(pick, dtype=np.int64).tolist()]
    print(
        f"  ArmL subsample train {len(tr)}→{len(out)} "
        f"(cap={max_tr} F={n_features}; env override, not live default)",
        flush=True,
    )
    return out


def _arml_boost_log_cb(period: int = 25):
    """LightGBM 迭代进度 → fit.log（verbose=-1 时默认几乎无输出）。

    时钟从第一次回调起算。回调在 ``fit_*_lgb_matrix`` 之前就建好，
    若用创建时间，step=1 的 elapsed 会把截面标准化算进去。
    """
    t_ref: Optional[float] = None

    def _cb(env) -> None:
        nonlocal t_ref
        try:
            it = int(env.iteration) + 1
            end = int(getattr(env, "end_iteration", 0) or 0)
            if end <= 0:
                end = it
            if it != 1 and it % max(1, int(period)) != 0 and it < end:
                return
            now = time.time()
            if t_ref is None:
                t_ref = now
            print(
                f"  ArmL step={it}/{end} "
                f"elapsed={now - t_ref:.0f}s rss={_rss_mb()}",
                flush=True,
            )
        except Exception:
            return

    return _cb


def _run_arm_l_regression(
    fac,
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    *,
    head_id: str,
    t0: float,
) -> Dict[str, Any]:
    """回归 Arm·L：复用 live ``fit_*_lgb_matrix`` / ``predict_*_lgb_matrix``（L2 立方 CS-z）。"""
    import gc

    from core.research.materialize.consumer import MaterializeRequired

    hid = str(head_id or "oo").strip() or "oo"
    names = [str(n) for n in fac.feature_names]
    dates = [str(d)[:10] for d in fac.dates]
    X = np.asarray(fac.X, dtype=np.float64)
    y = np.asarray(fac.y, dtype=np.float64)
    max_tr = _arm_l_max_train_rows()
    tr = _arm_l_maybe_subsample_train(
        train_idx, max_tr=max_tr, n_features=int(X.shape[1])
    )
    te = [int(i) for i in test_idx]
    from core.research.horizon_lgb import HORIZON_LGB

    rounds = int(HORIZON_LGB["n_estimators"])
    step_cb = [_arml_boost_log_cb(period=max(10, rounds // 10))]
    print(
        f"  ArmL live-fit start head={hid} n_train={len(tr)} n_test={len(te)} "
        f"F={int(X.shape[1])} rounds={rounds} rss={_rss_mb()}",
        flush=True,
    )
    try:
        if hid in ("tc", "tc_rank"):
            from core.research.tc_lgb import fit_tc_lgb_matrix, predict_tc_lgb_matrix

            blob, report = fit_tc_lgb_matrix(
                X,
                y,
                names,
                row_idx=tr,
                row_dates=dates,
                cross_section_zscore=True,
                clock="open",
                callbacks=step_cb,
            )
            predict_fn = predict_tc_lgb_matrix
        else:
            from core.research.oo_lgb import fit_oo_lgb_matrix, predict_oo_lgb_matrix

            blob, report = fit_oo_lgb_matrix(
                X,
                y,
                names,
                row_idx=tr,
                row_dates=dates,
                cross_section_zscore=True,
                callbacks=step_cb,
            )
            predict_fn = predict_oo_lgb_matrix
    except MaterializeRequired as exc:
        return {
            "arm": "L_lightgbm",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "note": "live fit_*_lgb_matrix + L2 cube CS-z",
        }
    if blob is None:
        return {
            "arm": "L_lightgbm",
            "success": False,
            "error": (report or {}).get("error") or "LightGBM 拟合失败",
            "report": report,
            "elapsed_sec": round(time.time() - t0, 2),
            "note": "live fit_*_lgb_matrix + L2 cube CS-z",
        }
    n_train = int((report or {}).get("sample_count") or blob.get("sample_count") or len(tr))
    print(
        f"  ArmL live-fit done n_train_fit={n_train} "
        f"{time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )
    print(f"  ArmL predict test={len(te)} rss={_rss_mb()}", flush=True)
    preds_raw = predict_fn(blob, X, names, te, row_dates=dates)
    ys_all = [float(y[i]) for i in te]
    metas_all = _metas([fac.codes[i] for i in te], [fac.dates[i] for i in te])
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    n_miss = 0
    for p, yy, m in zip(preds_raw, ys_all, metas_all):
        if p is None:
            n_miss += 1
            continue
        try:
            fv = float(p)
        except (TypeError, ValueError):
            n_miss += 1
            continue
        if not np.isfinite(fv):
            n_miss += 1
            continue
        preds.append(fv)
        ys.append(float(yy))
        metas.append(m)
    if not preds:
        return {
            "arm": "L_lightgbm",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "n_train": n_train,
            "n_test": len(te),
            "elapsed_sec": round(time.time() - t0, 2),
            "note": "live fit_*_lgb_matrix + L2 cube CS-z",
        }
    metrics = _eval_pack(preds, ys, metas)
    metrics.update(
        {
            "arm": "L_lightgbm",
            "success": True,
            "n_features": int(len(blob.get("feature_names") or names)),
            "n_train": n_train,
            "n_test": len(te),
            "n_eval": len(preds),
            "n_pred_miss": int(n_miss),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": "live fit_*_lgb_matrix + L2 cube CS-z",
            "max_train_cap": int(max_tr),
            "live_aligned": True,
        }
    )
    del blob, X, y
    gc.collect()
    return metrics


def _run_arm_l_rank(
    fac,
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    *,
    head_id: str = "oo_rank",
    t0: float,
) -> Dict[str, Any]:
    """排序 Arm·L：L2 立方 CS-z + live LambdaRank 超参（矩阵路径；非 day-pack 全链路）。

    oo_rank / tc_rank 共用 ``oo_rank_lambdarank`` 超参；面板列已按头部分开（aoo/atc）。
    """
    import gc

    import lightgbm as lgb

    from core.research.horizon_lgb import resolve_lgb_num_threads
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.materialize.cube_z import cs_z_matrix_prefer_cube
    from core.research.oo_rank_lambdarank import (
        LAMBDARANK_COLSAMPLE,
        LAMBDARANK_L2,
        LAMBDARANK_LEARNING_RATE,
        LAMBDARANK_MAX_DEPTH,
        LAMBDARANK_MIN_CHILD_SAMPLES,
        LAMBDARANK_N_ESTIMATORS,
        LAMBDARANK_NUM_LEAVES,
        LAMBDARANK_SUBSAMPLE,
        LAMBDARANK_TRUNCATION_LEVEL,
        LAMBDARANK_Z_CLIP,
        _rank_labels,
    )

    hid = str(head_id or "oo_rank").strip() or "oo_rank"
    names = [str(n) for n in fac.feature_names]
    dates = [str(d)[:10] for d in fac.dates]
    X = np.asarray(fac.X, dtype=np.float64)
    y = np.asarray(fac.y, dtype=np.float64)
    te = np.asarray(list(test_idx), dtype=np.int64)
    max_tr = _arm_l_max_train_rows()
    print(
        f"  ArmL cube CS-z start head={hid} N={int(X.shape[0])} F={int(X.shape[1])} "
        f"rss={_rss_mb()}",
        flush=True,
    )
    try:
        Xz = cs_z_matrix_prefer_cube(X, names, dates, clock="open")
    except MaterializeRequired as exc:
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": "L2 cube CS-z + live LambdaRank hypers",
        }
    z_clip = float(LAMBDARANK_Z_CLIP)
    if z_clip > 0:
        Xz = np.clip(Xz, -z_clip, z_clip)
    Xz = np.where(np.isfinite(Xz), Xz, 0.0)
    print(
        f"  ArmL cube CS-z done {time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )

    tr_sorted = sorted(
        list(train_idx),
        key=lambda i: (str(fac.dates[i])[:10], str(fac.codes[i])),
    )
    X_tr: List[np.ndarray] = []
    y_tr: List[int] = []
    groups: List[int] = []
    by: Dict[str, List[int]] = {}
    for i in tr_sorted:
        by.setdefault(str(fac.dates[i])[:10], []).append(int(i))
    for rows in by.values():
        if len(rows) < 4:
            continue
        labs = _rank_labels([float(y[i]) for i in rows])
        if len(set(labs)) < 2:
            continue
        X_tr.append(Xz[np.asarray(rows, dtype=np.int64)])
        y_tr.extend(labs)
        groups.append(len(rows))
    if not X_tr:
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": "no_rank_groups",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
        }
    X_fit = np.concatenate(X_tr, axis=0)
    y_fit = np.asarray(y_tr, dtype=np.float64)
    y_max = int(y_fit.max()) if y_fit.size else 0
    if max_tr > 0 and int(X_fit.shape[0]) > max_tr:
        rng = np.random.RandomState(42)
        order = list(range(len(groups)))
        rng.shuffle(order)
        keep_x: List[np.ndarray] = []
        keep_y: List[float] = []
        keep_g: List[int] = []
        n_kept = 0
        cursor = 0
        parts = []
        for g in groups:
            parts.append((cursor, cursor + int(g)))
            cursor += int(g)
        for gi in order:
            a, b = parts[int(gi)]
            if n_kept and n_kept + (b - a) > max_tr:
                continue
            keep_x.append(X_fit[a:b])
            keep_y.extend(y_fit[a:b].tolist())
            keep_g.append(int(b - a))
            n_kept += b - a
        if keep_x:
            print(
                f"  ArmL rank subsample rows {int(X_fit.shape[0])}→{n_kept} "
                f"(cap={max_tr}; env override, not live default)",
                flush=True,
            )
            X_fit = np.concatenate(keep_x, axis=0)
            y_fit = np.asarray(keep_y, dtype=np.float64)
            groups = keep_g
            y_max = int(y_fit.max()) if y_fit.size else 0
    rounds = int(LAMBDARANK_N_ESTIMATORS)
    trunc = int(LAMBDARANK_TRUNCATION_LEVEL)
    params = {
        "objective": "lambdarank",
        "metric": ["ndcg"],
        "label_gain": [float(i) for i in range(y_max + 1)],
        "learning_rate": float(LAMBDARANK_LEARNING_RATE),
        "num_leaves": int(LAMBDARANK_NUM_LEAVES),
        "max_depth": int(LAMBDARANK_MAX_DEPTH),
        "min_data_in_leaf": int(LAMBDARANK_MIN_CHILD_SAMPLES),
        "subsample": float(LAMBDARANK_SUBSAMPLE),
        "colsample_bytree": float(LAMBDARANK_COLSAMPLE),
        "lambda_l2": float(LAMBDARANK_L2),
        "verbose": -1,
        "num_threads": int(resolve_lgb_num_threads(None)),
        "seed": 42,
    }
    if trunc > 0:
        params["lambdarank_truncation_level"] = trunc
    print(
        f"  ArmL fit start head={hid} objective=lambdarank rounds={rounds} "
        f"n_train={int(X_fit.shape[0])} groups={len(groups)} "
        f"F={int(X_fit.shape[1])} lr={params['learning_rate']} "
        f"leaves={params['num_leaves']} rss={_rss_mb()}",
        flush=True,
    )
    dtrain = lgb.Dataset(
        X_fit, label=y_fit, group=np.asarray(groups, dtype=np.int32)
    )
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=rounds,
        callbacks=[_arml_boost_log_cb(period=max(10, rounds // 10))],
    )
    n_train = int(X_fit.shape[0])
    del X_fit, y_fit
    print(f"  ArmL predict test={int(te.size)} rss={_rss_mb()}", flush=True)
    preds = booster.predict(Xz[te])
    metrics = _eval_pack(
        list(np.asarray(preds, dtype=np.float64)),
        [float(y[i]) for i in te.tolist()],
        _metas(
            [fac.codes[i] for i in te.tolist()],
            [fac.dates[i] for i in te.tolist()],
        ),
    )
    metrics.update(
        {
            "arm": "L_lambdarank",
            "success": True,
            "head": hid,
            "n_features": int(Xz.shape[1]),
            "n_train": n_train,
            "n_test": int(te.size),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": (
                "L2 cube CS-z + shared LambdaRank hypers (matrix path; "
                f"head={hid}; oo_rank/tc_rank 同超参)"
            ),
            "max_train_cap": int(max_tr),
            # 超参与 live 排序头同源；非 day-pack 全链路，矩阵近似
            "live_aligned": True,
        }
    )
    del Xz, y, booster, dtrain
    gc.collect()
    return metrics


def _run_arm_l(
    fac,
    train_idx,
    test_idx,
    *,
    head_id: str = "oo",
    rank: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    n_feat = int(np.asarray(fac.X).shape[1]) if fac is not None else 0
    print(
        f"  ArmL begin head={head_id} rank={int(bool(rank))} "
        f"N={int(getattr(fac, 'n', 0) or 0)} F={n_feat} rss={_rss_mb()}",
        flush=True,
    )
    if rank:
        return _run_arm_l_rank(fac, train_idx, test_idx, head_id=head_id, t0=t0)
    return _run_arm_l_regression(
        fac, train_idx, test_idx, head_id=head_id, t0=t0
    )


def _run_arm_t(seq, fac, train_idx, test_idx, args) -> Dict[str, Any]:
    """Arm·T：序列 Transformer ∥ 因子支路；固定 seed=42 只训一轮。

    因子支路与 Arm·L 同：全面板 ``cs_z_matrix_prefer_cube``（L2 截面 CS-z）；
    缺立方显式失败，不做列标准化回退。
    """
    import gc

    from core.research.materialize.consumer import MaterializeRequired
    from core.research.materialize.cube_z import cs_z_matrix_prefer_cube
    from core.research.yhat_transformer.train import (
        predict_seq_factor_transformer,
        train_seq_factor_transformer,
    )

    seed = 42
    t0 = time.time()
    te_ii = np.asarray(list(test_idx), dtype=np.int64)
    te_y = np.asarray(seq.y, dtype=np.float32)[te_ii].tolist()
    te_metas = [
        {
            "code": str(seq.codes[int(i)]),
            "date": str(seq.dates[int(i)])[:10],
            "decision_date": str(seq.dates[int(i)])[:10],
        }
        for i in te_ii.tolist()
    ]
    names = [str(n) for n in fac.feature_names]
    dates = [str(d)[:10] for d in fac.dates]
    # 与 L 同：先 float64 走立方 CS-z，再压成 float32 给 Transformer
    fac_X = np.array(fac.X, dtype=np.float64, copy=True)
    n_features_factor = int(fac_X.shape[1])
    try:
        fac.X = np.empty((0, 0), dtype=np.float32)
    except Exception:
        pass
    gc.collect()

    print(
        f"  ArmT L2 CS-z start F={n_features_factor} N={int(fac_X.shape[0])} "
        f"clock=open rss={_rss_mb()}",
        flush=True,
    )
    try:
        fac_X = cs_z_matrix_prefer_cube(fac_X, names, dates, clock="open")
    except MaterializeRequired as exc:
        return {
            "arm": "T_transformer",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "n_features_factor": n_features_factor,
            "note": "factor branch L2 cube CS-z (aligned with Arm·L)",
        }
    # 排序头与 Arm·L LambdaRank 同裁 ±Z_CLIP；回归头不裁（对齐 live LGB）
    head_id = str(getattr(args, "head", "") or "").strip().lower()
    if head_id in ("oo_rank", "tc_rank"):
        from core.research.oo_rank_lambdarank import LAMBDARANK_Z_CLIP

        z_clip = float(LAMBDARANK_Z_CLIP)
        if z_clip > 0:
            fac_X = np.clip(fac_X, -z_clip, z_clip)
    fac_X = np.where(np.isfinite(fac_X), fac_X, 0.0)
    fac_X = np.asarray(fac_X, dtype=np.float32)
    if not fac_X.flags["C_CONTIGUOUS"]:
        fac_X = np.ascontiguousarray(fac_X)
    print(
        f"  ArmT L2 CS-z done {time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )

    n_pool = len(train_idx)
    max_tr = _arm_t_max_train_rows(int(n_pool), args)
    frac = getattr(args, "t_sample_frac", None)
    if frac is not None:
        print(
            f"  ArmT subsample frac={float(frac):.4g} "
            f"n_train_pool={int(n_pool)} → train_cap={max_tr or 'none'} "
            f"rss={_rss_mb()}",
            flush=True,
        )
    state, report = train_seq_factor_transformer(
        seq,
        fac_X,
        train_idx,
        d_model=args.d_model,
        nhead=args.nhead,
        nlayers=args.nlayers,
        factor_hidden=args.mlp_hidden,
        dropout=args.dropout,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=float(getattr(args, "lr", 1e-3) or 1e-3),
        seed=seed,
        device=args.device,
        patience=args.patience,
        max_train_rows=max_tr,
        loss=getattr(args, "loss", "smooth_l1"),
    )
    Xseq = report.pop("Xseq")
    gap = report.pop("gap")
    Xf = report.pop("Xf")
    report.pop("y", None)
    report.pop("dates", None)
    fac_X = None
    gc.collect()
    preds = predict_seq_factor_transformer(
        state,
        Xseq,
        gap,
        Xf,
        test_idx,
        d_model=args.d_model,
        nhead=args.nhead,
        nlayers=args.nlayers,
        factor_hidden=args.mlp_hidden,
        device=args.device,
    )
    m = _eval_pack(preds.tolist(), te_y, te_metas)
    m["seed"] = seed
    m["elapsed_sec"] = round(time.time() - t0, 2)
    m["n_params"] = report.get("n_params")
    print(
        f"  ArmT seed={seed} {_arm_done_bits(m)} "
        f"{m['elapsed_sec']}s rss={_rss_mb()}",
        flush=True,
    )
    return {
        "arm": "T_transformer",
        "success": True,
        "n_features_factor": n_features_factor,
        "note": "factor branch L2 cube CS-z (aligned with Arm·L)",
        **m,
    }


def _fmt_metric(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "nan"
    if x != x:
        return "nan"
    return f"{x:.4f}"


def _arm_done_bits(m: Dict[str, Any]) -> str:
    """完成行带上命中率和 TopY，页面不用等整次 Holdout 结束。"""
    return (
        f"rankIC={_fmt_metric(m.get('cs_rank_ic'))} "
        f"ICIR={_fmt_metric(m.get('cs_rank_icir'))} "
        f"sign_hit={_fmt_metric(m.get('sign_hit'))} "
        f"topY={_fmt_metric(m.get('cs_top_mean_y'))}"
    )


def _metric_row(tag: str, pack: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "arm": tag,
        "cs_rank_ic": pack.get("cs_rank_ic"),
        "cs_rank_icir": pack.get("cs_rank_icir"),
        "cs_ic": pack.get("cs_ic"),
        "cs_icir": pack.get("cs_icir"),
        "sign_hit": pack.get("sign_hit"),
        "cs_top_mean_y": pack.get("cs_top_mean_y"),
        "n": pack.get("n"),
        "success": pack.get("success"),
        "error": pack.get("error"),
    }


def _desk_mark_failed(head: str, error: str) -> None:
    try:
        from core.research.yhat_tf_desk import mark_fit_failed

        mark_fit_failed(head, error)
        print(f"desk 已标记失败 head={head}: {error}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"desk 标记失败异常: {exc}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="四头 ŷ_* Scheme LT Holdout（L+T；默认 head=oo；研究轨）")
    p.add_argument("--codes", default="")
    p.add_argument("--watching-limit", type=int, default=800)
    p.add_argument("--lookback", type=int, default=900)
    p.add_argument("--horizon-days", type=int, default=1)
    p.add_argument("--holdout-trading-days", type=int, default=60)
    p.add_argument("--seq-len", type=int, default=20)
    p.add_argument(
        "--seq-feat-set",
        type=str,
        default="seq16",
        help="序列特征集 seq8|seq16|seq32|seq64（默认 seq16）",
    )
    p.add_argument("--d-model", type=int, default=64)
    p.add_argument("--nhead", type=int, default=4)
    p.add_argument("--nlayers", type=int, default=3)
    p.add_argument(
        "--mlp-hidden",
        type=int,
        default=None,
        help="因子支路 hidden；默认对齐 --d-model",
    )
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3, help="Arm·T 学习率")
    p.add_argument("--patience", type=int, default=6)
    p.add_argument("--device", default="cpu")
    p.add_argument(
        "--t-sample-frac",
        type=float,
        default=None,
        help="Arm·T 相对 Holdout 训练段 subsample 比例 (0,1]；默认 0.1=10%%；1=不截断",
    )
    p.add_argument(
        "--update-desk",
        action="store_true",
        default=True,
        help="回写 live/yhat_tf/{head}_last.json metrics（默认开）",
    )
    p.add_argument(
        "--no-update-desk",
        action="store_false",
        dest="update_desk",
        help="不回写模型中心 last 槽",
    )
    p.add_argument(
        "--head",
        type=str,
        default="oo",
        help="oo|tc|oo_rank|tc_rank；排序头用 listwise / Arm·L=LambdaRank",
    )
    p.add_argument(
        "--tau-end",
        type=str,
        default="09:30",
        help="ŷ_τc / ŷ_τc_rank 前缀；本期仅支持 09:30（开盘）",
    )
    p.add_argument(
        "--full-aoo",
        action="store_true",
        help="Arm·T 用全量 raw_aoo（仅 oo；Arm·L 始终全量）",
    )
    p.add_argument(
        "--atc-core",
        action="store_true",
        help="Arm·T 只留 GAP+YK+LAG（仅 τc；Arm·L 始终全量）",
    )
    p.add_argument("--out", default="")
    args = p.parse_args()
    head_id = str(args.head or "oo").strip() or "oo"
    try:
        return _run_holdout(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        if bool(getattr(args, "update_desk", True)):
            _desk_mark_failed(head_id, f"{type(exc).__name__}: {exc}")
        return 1


def _install_stop_handlers() -> None:
    """SIGTERM/SIGINT 走 SystemExit，让面板的 with 能关掉进程池。"""

    def _stop(signum, _frame) -> None:
        name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(
            int(signum), str(signum)
        )
        print(f"holdout 收到 {name}，中途退出（未回写 metrics）", flush=True)
        raise SystemExit(128 + int(signum))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (OSError, ValueError):
            continue


def _run_holdout(args: argparse.Namespace) -> int:
    _install_stop_handlers()
    from core.research.yhat_transformer.heads import assert_trainable, get_head

    head_spec = get_head(args.head)
    try:
        assert_trainable(head_spec)
    except NotImplementedError as exc:
        raise SystemExit(str(exc)) from exc
    args.loss = head_spec.loss
    args.task_type = head_spec.task_type
    # Scheme LT：固定双臂（不再接受 --arms）
    arms = ["L", "T"]
    need_fac = True
    need_seq = True
    # mlp_hidden 无配置时对齐 d_model
    if getattr(args, "mlp_hidden", None) is None:
        args.mlp_hidden = int(getattr(args, "d_model", 64) or 64)
    # CLI 直跑也可能绕过 desk harden：此处再钳一次 nhead|d_model
    dm = max(1, int(getattr(args, "d_model", 64) or 64))
    nh = max(1, int(getattr(args, "nhead", 4) or 4))
    if dm % nh != 0:
        nh0 = nh
        while nh > 1 and dm % nh != 0:
            nh -= 1
        print(f"nhead: {nh0}→{nh}（须整除 d_model={dm}）", flush=True)
        args.nhead = nh
    args.d_model = dm
    if head_spec.id in ("tc", "tc_rank") and str(args.tau_end or "09:30").strip() not in (
        "09:30",
        "0930",
        "open",
    ):
        raise SystemExit(f"本期 head={head_spec.id} 仅支持 --tau-end 09:30（开盘）")
    print(
        f"head={head_spec.id} ({head_spec.label}) package=yhat_transformer "
        f"loss={args.loss} live_allowed=False tau_end={args.tau_end}",
        flush=True,
    )

    from core.research.holdout import resolve_ridge_split
    from core.research_universe import resolve_model_fit_codes

    explicit = _parse_codes(args.codes)
    resolved = resolve_model_fit_codes(
        watching_limit=args.watching_limit, codes=explicit
    )
    codes = list(resolved.get("codes") or [])
    if len(codes) < 2:
        print("标的不足 2 只", flush=True)
        return 2

    frac = getattr(args, "t_sample_frac", None)
    frac_s = f"{float(frac):.4g}" if frac is not None else "—"
    print(
        f"臂={arms} 池≤{len(codes)} lookback={args.lookback} L={args.seq_len} "
        f"seq_feat={getattr(args, 'seq_feat_set', 'seq16')} "
        f"holdout={args.holdout_trading_days} ArmT_sample_frac={frac_s} "
        f"rss={_rss_mb()}",
        flush=True,
    )
    stock_bars = _load_bars(codes, lookback=args.lookback)
    if len(stock_bars) < 2:
        print("有效日线不足", flush=True)
        return 2
    print(f"有效日线 {len(stock_bars)} 只 rss={_rss_mb()}", flush=True)

    summary: Dict[str, Any] = {
        "role": "research_yhat_multiarm_holdout",
        "package": "yhat_transformer",
        "head": head_spec.id,
        "live_allowed": False,
        "arms": arms,
        "codes_n": len(stock_bars),
        "args": vars(args),
        "rss_mb_start": _rss_mb(),
    }

    seq = None
    fac = None
    if need_seq:
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        t0 = time.time()
        seq = build_seq_panel(
            stock_bars,
            head=head_spec,
            seq_len=args.seq_len,
            horizon_days=args.horizon_days,
            feat_set=getattr(args, "seq_feat_set", None),
        )
        print(
            f"序列面板 head={head_spec.id} N={seq.n} L={seq.seq_len} "
            f"F={seq.X.shape[-1]} feat={getattr(args, 'seq_feat_set', 'seq16')} "
            f"{time.time()-t0:.1f}s rss={_rss_mb()}",
            flush=True,
        )
    if need_fac:
        from core.research.yhat_transformer.factor_panel import build_factor_panel

        t0 = time.time()
        # 先建全量：Arm·L 对齐 live。core / full 只在 Arm·T 开跑前裁列。
        if head_spec.id in ("tc", "tc_rank"):
            pack_name = "atc_full"
        else:
            pack_name = "aoo_full"
        print(
            f"构建因子面板 head={head_spec.id} pack={pack_name}（Arm·L 全量）…",
            flush=True,
        )
        fac = build_factor_panel(
            stock_bars,
            head=head_spec,
            horizon_days=args.horizon_days,
            progress_every=20,
        )
        print(
            f"因子面板 N={fac.n} F={fac.X.shape[1]} "
            f"{time.time()-t0:.1f}s rss={_rss_mb()}",
            flush=True,
        )

    seq_c, fac_c = None, None
    shared_keys: set = set()
    # 对齐后共用交易日历，避免 fac/seq 日域不同 → L/T eval_start 错位、Gate 比错窗
    shared_cal: List[str] = []
    fac_full = fac  # L 训练用全量；T 裁列对齐后不销毁直到 L 跑完

    def _split_for(
        dates: Sequence[str], *, calendar_dates: Optional[Sequence[str]] = None
    ):
        src = calendar_dates if calendar_dates is not None else dates
        cal = sorted({str(d)[:10] for d in src if str(d)[:10]})
        return resolve_ridge_split(
            list(dates),
            holdout_trading_days=args.holdout_trading_days,
            label_horizon_days=args.horizon_days,
            calendar_dates=cal,
        )

    # ----- 先对齐 T 面板，得到共享 (code,date)，Gate/OOS 用交集 -----
    if "T" in arms:
        import gc

        seq_n = int(getattr(seq, "n", 0) or 0) if seq is not None else 0
        fac_n = int(getattr(fac_full, "n", 0) or 0) if fac_full is not None else 0
        if seq is None or fac_full is None or seq_n <= 0 or fac_n <= 0:
            summary["arm_T"] = {
                "success": False,
                "error": "序列或因子面板为空，Arm·T 未启动",
            }
            print(f"  ArmT 失败：{summary['arm_T']['error']}", flush=True)
        else:
            try:
                from core.research.yhat_transformer.factor_panel import (
                    align_seq_and_factor,
                    require_factor_panel,
                    slice_arm_t_panel,
                )

                require_factor_panel(fac_full)
                atc_core = bool(getattr(args, "atc_core", False))
                aoo_core = (
                    head_spec.id not in ("tc", "tc_rank") and not bool(args.full_aoo)
                )
                sliced = slice_arm_t_panel(
                    fac_full,
                    head_spec,
                    aoo_core_only=aoo_core,
                    atc_core_only=atc_core and head_spec.id in ("tc", "tc_rank"),
                )
                seq_c, fac_c = align_seq_and_factor(seq, sliced)
                if fac_c.n <= 0:
                    raise ValueError("对齐后没有共同样本")
                shared_keys = {
                    (str(c), str(d)[:10])
                    for c, d in zip(fac_c.codes, fac_c.dates)
                }
                shared_cal = sorted(
                    {str(d)[:10] for d in seq_c.dates if str(d)[:10]}
                )
                print(
                    f"ArmT 因子 F={fac_c.X.shape[1]} 对齐后 N={seq_c.n} "
                    f"（原 seq={seq.n} fac={sliced.n} shared_keys={len(shared_keys)} "
                    f"shared_cal={len(shared_cal)}）",
                    flush=True,
                )
                if sliced is not fac_full:
                    del sliced
                    gc.collect()
            except Exception as exc:  # noqa: BLE001
                summary["arm_T"] = {
                    "success": False,
                    "error": f"Arm·T 因子准备失败：{type(exc).__name__}: {exc}",
                }
                print(f"  ArmT 失败：{summary['arm_T']['error']}", flush=True)
                seq_c, fac_c = None, None
                shared_keys = set()
                shared_cal = []

    # ----- Arm·L：全量训练；OOS/Gate 评测缩到共享键 -----
    # 共享日历只锁定 eval 窗；训练仍吃 fac 上 eval 前的全部日（含 seq 尚未覆盖的早期日）
    if "L" in arms and fac_full is not None and fac_full.n > 0:
        if shared_cal:
            _, _, meta = _split_for(shared_cal, calendar_dates=shared_cal)
            test_days = {str(d)[:10] for d in (meta.get("test_days") or [])}
            embargo_days = {str(d)[:10] for d in (meta.get("embargo_days") or [])}
            eval_start = str(meta.get("eval_start") or "")[:10] or None
            tr = []
            te_all: List[int] = []
            te_shared: List[int] = []
            for i, (c, d) in enumerate(zip(fac_full.codes, fac_full.dates)):
                ds = str(d)[:10]
                if ds in test_days:
                    te_all.append(int(i))
                    if (str(c), ds) in shared_keys:
                        te_shared.append(int(i))
                elif ds in embargo_days:
                    continue
                elif eval_start and ds >= eval_start:
                    # 晚于共享日历末端的 fac 日：不进训不进测
                    continue
                else:
                    tr.append(int(i))
            n_l_test = len(te_all)
        else:
            tr, te, meta = _split_for(fac_full.dates)
            n_l_test = len(te)
            te_shared = list(te)
        summary["split_L"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "n_train": len(tr),
            "n_test": len(te_shared),
            "n_test_full": n_l_test,
        }
        print(
            f"ArmL 切分 train={len(tr)} test_shared={len(te_shared)}"
            f"（full_holdout={n_l_test}"
            f"{' shared_cal+pretrain' if shared_cal else ''}）",
            flush=True,
        )
        if te_shared:
            try:
                from core.research.yhat_transformer.factor_panel import (
                    require_factor_panel,
                )

                require_factor_panel(fac_full)
                print(
                    f"  ArmL begin F={int(fac_full.X.shape[1])} rss={_rss_mb()}",
                    flush=True,
                )
                summary["arm_L"] = _run_arm_l(
                    fac_full,
                    tr,
                    te_shared,
                    head_id=head_spec.id,
                    rank=(head_spec.task_type == "rank"),
                )
                a0 = summary["arm_L"]
                if a0.get("success"):
                    print(
                        f"  ArmL {_arm_done_bits(a0)} {a0.get('elapsed_sec')}s",
                        flush=True,
                    )
                else:
                    print(
                        f"  ArmL 失败：{a0.get('error') or '无指标'}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                summary["arm_L"] = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"  ArmL 失败：{summary['arm_L']['error']}", flush=True)
        else:
            summary["arm_L"] = {
                "success": False,
                "error": "empty holdout (shared test)",
            }

    # ----- Arm·T 训练 -----
    if "T" in arms and seq_c is not None and fac_c is not None and seq_c.n > 0:
        import gc

        try:
            del stock_bars
        except Exception:
            pass
        if seq is not None and seq_c is not seq:
            try:
                del seq
            except Exception:
                pass
        if fac_full is not None and fac_c is not fac_full:
            try:
                del fac_full
            except Exception:
                pass
        fac = None
        gc.collect()
        tr, te, meta = _split_for(
            seq_c.dates,
            calendar_dates=shared_cal if shared_cal else None,
        )
        summary["split_T"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "n_train": len(tr),
            "n_test": len(te),
        }
        print(
            f"ArmT 切分 train={len(tr)} test={len(te)} "
            f"eval_start={meta.get('eval_start')} rss={_rss_mb()}",
            flush=True,
        )
        if te:
            try:
                summary["arm_T"] = _run_arm_t(seq_c, fac_c, tr, te, args)
            except Exception as exc:  # noqa: BLE001
                summary["arm_T"] = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"  ArmT 失败：{summary['arm_T']['error']}", flush=True)
        else:
            summary["arm_T"] = {"success": False, "error": "empty holdout"}

    n_l_test_u = int((summary.get("split_L") or {}).get("n_test_full") or 0)
    n_l_shared = int((summary.get("split_L") or {}).get("n_test") or 0)
    n_t_test = int((summary.get("split_T") or {}).get("n_test") or 0)
    eval_l = (summary.get("split_L") or {}).get("eval_start")
    eval_t = (summary.get("split_T") or {}).get("eval_start")
    # 须共享键 + 两侧均有 OOS + 同一 eval_start（同日历）
    eval_shared = (
        bool(shared_keys)
        and n_l_shared > 0
        and n_t_test > 0
        and bool(eval_l)
        and eval_l == eval_t
    )
    summary["eval_universe"] = {
        "n_l_test": n_l_test_u,
        "n_t_test": n_t_test,
        "n_shared": n_l_shared,
        "eval_shared": eval_shared,
        "eval_start": eval_l if eval_shared else None,
    }
    # desk 形 parity（与 ingest 一致）
    try:
        from core.research.yhat_tf_desk import compute_parity

        frac_raw = getattr(args, "t_sample_frac", None)
        try:
            frac_v = 0.1 if frac_raw is None else float(frac_raw)
        except (TypeError, ValueError):
            frac_v = 0.1
        if frac_v != frac_v or frac_v <= 0:
            frac_v = 0.1
        desk_params = {
            "full_aoo": bool(getattr(args, "full_aoo", False)),
            "full_atc": not bool(getattr(args, "atc_core", False)),
            "t_sample_pct": float(frac_v) * 100.0,
        }
        summary["parity"] = compute_parity(
            head_spec.id, desk_params, eval_shared=eval_shared
        )
    except Exception:  # noqa: BLE001
        frac_raw = getattr(args, "t_sample_frac", None)
        try:
            frac_v = 0.0 if frac_raw is None else float(frac_raw)
        except (TypeError, ValueError):
            frac_v = 0.0
        summary["parity"] = {
            "factor_width_match": bool(getattr(args, "full_aoo", False))
            if head_spec.id not in ("tc", "tc_rank")
            else not bool(getattr(args, "atc_core", False)),
            "train_sample_full": float(frac_v) >= 0.999,
            "eval_shared": eval_shared,
            "formal": False,
        }

    # 对比表 + 硬门（相对 Arm·L）；排序头 gate=gate_lift
    from core.research.yhat_transformer.metrics import (
        gate_lift_vs_baseline,
        hard_gate_vs_baseline,
    )

    gate_fn = (
        gate_lift_vs_baseline
        if head_spec.gate == "gate_lift"
        else hard_gate_vs_baseline
    )
    arm_l_tag = (
        "L_lambdarank"
        if head_spec.task_type == "rank"
        else "L_lightgbm"
    )
    table = []
    for key, tag in (
        ("arm_L", arm_l_tag),
        ("arm_T", "T_transformer"),
    ):
        if key in summary:
            table.append(_metric_row(tag, summary[key]))
    summary["comparison"] = table
    base = summary.get("arm_L") if (summary.get("arm_L") or {}).get("success") else None
    pack_t = summary.get("arm_T") if isinstance(summary.get("arm_T"), dict) else {}
    # 显式 gate：禁止空 {}；L/T 任一失败都写 pass=false + reasons
    if base and pack_t.get("success"):
        gates = {"T": gate_fn(base, pack_t)}
    elif not base and pack_t.get("success"):
        gates = {
            "T": {
                "pass": False,
                "lift": None,
                "reasons": ["baseline missing: Arm·L 失败或无指标"],
            }
        }
    elif base and not pack_t.get("success"):
        gates = {
            "T": {
                "pass": False,
                "lift": None,
                "reasons": [
                    f"Arm·T 失败：{pack_t.get('error') or '无指标'}"
                    if pack_t
                    else "Arm·T 未产出"
                ],
            }
        }
    else:
        gates = {
            "T": {
                "pass": False,
                "lift": None,
                "reasons": ["Arm·L 与 Arm·T 均无成功指标"],
            }
        }
    summary["hard_gate_vs_arm_L"] = gates
    summary["gate"] = head_spec.gate
    summary["rss_mb_end"] = _rss_mb()

    print("\n=== 对比 ===", flush=True)
    print(
        f"{'arm':<12} {'rankIC':>8} {'ICIR':>8} {'hit':>8} {'topY':>8}",
        flush=True,
    )
    for r in table:
        if r.get("success") is False:
            print(f"{r['arm']} 失败：{r.get('error') or '无指标'}", flush=True)
            continue
        print(
            f"{r['arm']:<12} "
            f"{(r.get('cs_rank_ic') if r.get('cs_rank_ic') is not None else float('nan')):>8.4f} "
            f"{(r.get('cs_rank_icir') if r.get('cs_rank_icir') is not None else float('nan')):>8.3f} "
            f"{(r.get('sign_hit') if r.get('sign_hit') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_top_mean_y') if r.get('cs_top_mean_y') is not None else float('nan')):>8.3f}",
            flush=True,
        )
    for k, g in gates.items():
        print(f"硬门 vs ArmL · {k}: pass={g.get('pass')} lift={g.get('lift')} {g.get('reasons')}", flush=True)

    out = args.out.strip()
    if not out:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(ROOT, "data", "experiments", "yhat_transformer", ts)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "summary.json")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"写入 {out}", flush=True)
    if bool(getattr(args, "update_desk", True)):
        try:
            from core.research.yhat_tf_desk import ingest_holdout_summary

            desk_out = ingest_holdout_summary(head_spec.id, summary, out_path=out)
            if desk_out.get("success"):
                print(
                    f"desk last 已回写 head={head_spec.id} metrics={desk_out.get('metrics')}",
                    flush=True,
                )
            else:
                print(f"desk 回写失败: {desk_out.get('error')}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"desk 回写异常: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
