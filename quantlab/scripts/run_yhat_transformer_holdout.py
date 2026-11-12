"""离线 Holdout：四头 ŷ_* 多臂对照（默认 head=oo；研究轨，不入库）。

臂：
  0 = 因子 + LightGBM（轻量 CS-z，不强制 L2 cube）
  A = 因子 + MLP
  B = 价量序列 Transformer（默认 L=20, F=8）
  C = 序列 Transformer ∥ 因子 MLP 融合

默认因子集 aoo_core（``--full-aoo`` 开全量 raw_aoo）。
用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_yhat_transformer_holdout.py --arms 0,A,B,C
"""

from __future__ import annotations

import argparse
import json
import os
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


def _parse_arms(raw: str) -> List[str]:
    allowed = {"0", "A", "B", "C"}
    out: List[str] = []
    for p in str(raw or "0,A,B,C").replace(";", ",").split(","):
        s = p.strip().upper()
        if s == "0" or s == "ARM0" or s == "LGB":
            s = "0"
        if s in allowed and s not in out:
            out.append(s)
    return out or ["B"]


def _rss_mb() -> Optional[float]:
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)
    except Exception:
        return None


def _load_bars(codes: Sequence[str], *, lookback: int) -> Dict[str, List[dict]]:
    from core.research.portfolio_bars import load_portfolio_stock_bars

    stock_bars, failures, _fund = load_portfolio_stock_bars(
        list(codes),
        lookback=int(lookback),
        fetch_fundamentals=False,
        offline_ok=True,
    )
    if failures:
        print(f"日线失败 {len(failures)}：{failures[:6]}", flush=True)
    return {str(k): list(v) for k, v in (stock_bars or {}).items() if v}


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


def _cs_z_by_date(X: np.ndarray, dates: Sequence[str]) -> np.ndarray:
    """简易日截面 z（研究对照；缺测已填 0）。"""
    X = np.asarray(X, dtype=np.float64)
    out = np.zeros_like(X, dtype=np.float64)
    by: Dict[str, List[int]] = {}
    for i, d in enumerate(dates):
        by.setdefault(str(d)[:10], []).append(i)
    for rows in by.values():
        idx = np.asarray(rows, dtype=np.int64)
        block = X[idx]
        mu = np.nanmean(block, axis=0)
        sd = np.nanstd(block, axis=0)
        sd = np.where(sd > 1e-8, sd, 1.0)
        z = (block - mu) / sd
        out[idx] = np.where(np.isfinite(z), z, 0.0)
    return out


def _run_arm0_lgb(fac, train_idx, test_idx, *, rank: bool = False) -> Dict[str, Any]:
    import lightgbm as lgb

    t0 = time.time()
    Xz = _cs_z_by_date(fac.X, fac.dates)
    y = np.asarray(fac.y, dtype=np.float64)
    te = np.asarray(list(test_idx), dtype=np.int64)
    if rank:
        from core.research.oo_rank_lambdarank import _rank_labels

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
                "arm": "0_lambdarank",
                "success": False,
                "error": "no_rank_groups",
                "elapsed_sec": round(time.time() - t0, 2),
            }
        X_fit = np.concatenate(X_tr, axis=0)
        y_fit = np.asarray(y_tr, dtype=np.float64)
        y_max = int(y_fit.max()) if y_fit.size else 0
        params = {
            "objective": "lambdarank",
            "metric": ["ndcg"],
            "label_gain": [float(i) for i in range(y_max + 1)],
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 20,
            "lambda_l2": 1.0,
            "verbose": -1,
            "num_threads": 1,
            "seed": 42,
        }
        dtrain = lgb.Dataset(X_fit, label=y_fit, group=np.asarray(groups, dtype=np.int32))
        booster = lgb.train(params, dtrain, num_boost_round=200)
        note = "factor CS-z + LightGBM LambdaRank (research arm0)"
        arm = "0_lambdarank"
        n_train = int(X_fit.shape[0])
    else:
        from core.research.horizon_lgb import HORIZON_LGB

        tr = np.asarray(list(train_idx), dtype=np.int64)
        params = dict(HORIZON_LGB)
        params["verbosity"] = -1
        dtrain = lgb.Dataset(Xz[tr], label=y[tr], free_raw_data=True)
        booster = lgb.train(
            params, dtrain, num_boost_round=int(params.get("n_estimators") or 300)
        )
        note = "factor CS-z + LightGBM (research, not cube path)"
        arm = "0_lgb"
        n_train = int(tr.size)
    preds = booster.predict(Xz[te])
    metrics = _eval_pack(
        list(np.asarray(preds, dtype=np.float64)),
        [float(y[i]) for i in te.tolist()],
        _metas([fac.codes[i] for i in te.tolist()], [fac.dates[i] for i in te.tolist()]),
    )
    metrics.update(
        {
            "arm": arm,
            "success": True,
            "n_features": int(Xz.shape[1]),
            "n_train": n_train,
            "n_test": int(te.size),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": note,
        }
    )
    return metrics


def _run_arm_a(fac, train_idx, test_idx, args) -> Dict[str, Any]:
    from core.research.yhat_transformer.train import predict_factor_mlp, train_factor_mlp

    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    rows = []
    pred_sum = None
    for seed in seeds:
        t0 = time.time()
        state, report = train_factor_mlp(
            fac.X,
            fac.y,
            fac.dates,
            train_idx,
            hidden=args.mlp_hidden,
            dropout=args.dropout,
            epochs=args.epochs,
            batch_size=max(128, args.batch_size),
            seed=seed,
            device=args.device,
            patience=args.patience,
            max_train_rows=args.max_train_rows,
            loss=getattr(args, "loss", "smooth_l1"),
        )
        Xz = report.pop("Xz")
        preds = predict_factor_mlp(
            state,
            Xz,
            test_idx,
            d_in=int(report["d_in"]),
            hidden=args.mlp_hidden,
            device=args.device,
        )
        te = list(test_idx)
        m = _eval_pack(
            preds.tolist(),
            [float(fac.y[i]) for i in te],
            _metas([fac.codes[i] for i in te], [fac.dates[i] for i in te]),
        )
        m["seed"] = seed
        m["elapsed_sec"] = round(time.time() - t0, 2)
        m["n_params"] = report.get("n_params")
        rows.append(m)
        pred_sum = preds if pred_sum is None else pred_sum + preds
        print(
            f"  ArmA seed={seed} rankIC={m.get('cs_rank_ic')} "
            f"{m['elapsed_sec']}s rss={_rss_mb()}",
            flush=True,
        )
    pred_mean = pred_sum / float(len(seeds))
    te = list(test_idx)
    mean_m = _eval_pack(
        pred_mean.tolist(),
        [float(fac.y[i]) for i in te],
        _metas([fac.codes[i] for i in te], [fac.dates[i] for i in te]),
    )
    ics = [r.get("cs_rank_ic") for r in rows if r.get("cs_rank_ic") is not None]
    return {
        "arm": "A_factor_mlp",
        "success": True,
        "seeds": rows,
        "mean_over_seeds": mean_m,
        "rank_ic_seed_mean": float(np.mean(ics)) if ics else None,
        "rank_ic_seed_std": float(np.std(ics)) if len(ics) > 1 else 0.0,
        "n_features": int(fac.X.shape[1]),
    }


def _run_arm_b(panel, train_idx, test_idx, args) -> Dict[str, Any]:
    from core.research.yhat_transformer.seq_dataset import panel_metas
    from core.research.yhat_transformer.train import (
        predict_yhat_transformer,
        train_yhat_transformer,
    )

    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    rows = []
    pred_sum = None
    for seed in seeds:
        t0 = time.time()
        state, report = train_yhat_transformer(
            panel,
            train_idx,
            d_model=args.d_model,
            nhead=args.nhead,
            nlayers=args.nlayers,
            dropout=args.dropout,
            epochs=args.epochs,
            batch_size=args.batch_size,
            seed=seed,
            device=args.device,
            patience=args.patience,
            max_train_rows=args.max_train_rows,
            loss=getattr(args, "loss", "smooth_l1"),
        )
        preds = predict_yhat_transformer(
            state,
            panel,
            test_idx,
            d_model=args.d_model,
            nhead=args.nhead,
            nlayers=args.nlayers,
            device=args.device,
        )
        te = panel.subset(test_idx)
        m = _eval_pack(preds.tolist(), te.y.tolist(), panel_metas(te))
        m["seed"] = seed
        m["elapsed_sec"] = round(time.time() - t0, 2)
        m["n_params"] = report.get("n_params")
        m["epochs_ran"] = report.get("epochs_ran")
        rows.append(m)
        pred_sum = preds if pred_sum is None else pred_sum + preds
        print(
            f"  ArmB seed={seed} rankIC={m.get('cs_rank_ic')} "
            f"epochs={report.get('epochs_ran')} {m['elapsed_sec']}s rss={_rss_mb()}",
            flush=True,
        )
    pred_mean = pred_sum / float(len(seeds))
    te = panel.subset(test_idx)
    mean_m = _eval_pack(pred_mean.tolist(), te.y.tolist(), panel_metas(te))
    ics = [r.get("cs_rank_ic") for r in rows if r.get("cs_rank_ic") is not None]
    return {
        "arm": "B_transformer",
        "success": True,
        "seeds": rows,
        "mean_over_seeds": mean_m,
        "rank_ic_seed_mean": float(np.mean(ics)) if ics else None,
        "rank_ic_seed_std": float(np.std(ics)) if len(ics) > 1 else 0.0,
    }


def _run_arm_c(seq, fac, train_idx, test_idx, args) -> Dict[str, Any]:
    from core.research.yhat_transformer.seq_dataset import panel_metas
    from core.research.yhat_transformer.train import (
        predict_seq_factor_fusion,
        train_seq_factor_fusion,
    )

    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    rows = []
    pred_sum = None
    for seed in seeds:
        t0 = time.time()
        state, report = train_seq_factor_fusion(
            seq,
            fac.X,
            train_idx,
            d_model=args.d_model,
            nhead=args.nhead,
            nlayers=args.nlayers,
            factor_hidden=args.mlp_hidden,
            dropout=args.dropout,
            epochs=args.epochs,
            batch_size=args.batch_size,
            seed=seed,
            device=args.device,
            patience=args.patience,
            max_train_rows=args.max_train_rows,
            loss=getattr(args, "loss", "smooth_l1"),
        )
        Xseq = report.pop("Xseq")
        gap = report.pop("gap")
        Xf = report.pop("Xf")
        preds = predict_seq_factor_fusion(
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
        te = seq.subset(test_idx)
        m = _eval_pack(preds.tolist(), te.y.tolist(), panel_metas(te))
        m["seed"] = seed
        m["elapsed_sec"] = round(time.time() - t0, 2)
        m["n_params"] = report.get("n_params")
        rows.append(m)
        pred_sum = preds if pred_sum is None else pred_sum + preds
        print(
            f"  ArmC seed={seed} rankIC={m.get('cs_rank_ic')} "
            f"{m['elapsed_sec']}s rss={_rss_mb()}",
            flush=True,
        )
    pred_mean = pred_sum / float(len(seeds))
    te = seq.subset(test_idx)
    mean_m = _eval_pack(pred_mean.tolist(), te.y.tolist(), panel_metas(te))
    ics = [r.get("cs_rank_ic") for r in rows if r.get("cs_rank_ic") is not None]
    return {
        "arm": "C_seq_factor_fusion",
        "success": True,
        "seeds": rows,
        "mean_over_seeds": mean_m,
        "rank_ic_seed_mean": float(np.mean(ics)) if ics else None,
        "rank_ic_seed_std": float(np.std(ics)) if len(ics) > 1 else 0.0,
        "n_features_factor": int(fac.X.shape[1]),
    }


def _metric_row(tag: str, pack: Dict[str, Any]) -> Dict[str, Any]:
    src = pack.get("mean_over_seeds") if isinstance(pack.get("mean_over_seeds"), dict) else pack
    return {
        "arm": tag,
        "cs_rank_ic": src.get("cs_rank_ic"),
        "cs_rank_icir": src.get("cs_rank_icir"),
        "cs_ic": src.get("cs_ic"),
        "cs_icir": src.get("cs_icir"),
        "sign_hit": src.get("sign_hit"),
        "cs_top_mean_y": src.get("cs_top_mean_y"),
        "n": src.get("n"),
        "rank_ic_seed_mean": pack.get("rank_ic_seed_mean"),
        "rank_ic_seed_std": pack.get("rank_ic_seed_std"),
        "success": pack.get("success"),
        "error": pack.get("error"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="四头 ŷ_* 多臂 Holdout（默认 head=oo；研究轨）")
    p.add_argument("--codes", default="")
    p.add_argument("--watching-limit", type=int, default=120)
    p.add_argument("--lookback", type=int, default=400)
    p.add_argument("--horizon-days", type=int, default=1)
    p.add_argument("--holdout-trading-days", type=int, default=20)
    p.add_argument("--seq-len", type=int, default=20)
    p.add_argument("--d-model", type=int, default=32)
    p.add_argument("--nhead", type=int, default=4)
    p.add_argument("--nlayers", type=int, default=2)
    p.add_argument("--mlp-hidden", type=int, default=64)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--patience", type=int, default=4)
    p.add_argument("--seeds", default="42,43")
    p.add_argument("--device", default="cpu")
    p.add_argument("--max-train-rows", type=int, default=40000)
    p.add_argument(
        "--head",
        type=str,
        default="oo",
        help="oo|tc|oo_rank|tc_rank；排序头用 listwise / Arm0=LambdaRank",
    )
    p.add_argument(
        "--tau-end",
        type=str,
        default="09:30",
        help="ŷ_τc / ŷ_τc_rank 前缀；本期仅支持 09:30（开盘）",
    )
    p.add_argument("--arms", default="0,A,B,C")
    p.add_argument("--full-aoo", action="store_true", help="因子臂用全量 raw_aoo（更慢；仅 oo）")
    p.add_argument("--out", default="")
    args = p.parse_args()
    from core.research.yhat_transformer.heads import assert_trainable, get_head

    head_spec = get_head(args.head)
    try:
        assert_trainable(head_spec)
    except NotImplementedError as exc:
        raise SystemExit(str(exc)) from exc
    args.loss = head_spec.loss
    args.task_type = head_spec.task_type
    if head_spec.id in ("tc", "tc_rank") and str(args.tau_end or "09:30").strip() not in (
        "09:30",
        "0930",
        "open",
    ):
        raise SystemExit(f"本期 head={head_spec.id} 仅支持 --tau-end 09:30（开盘）")
    if head_spec.task_type == "rank" and not str(args.arms or "").strip():
        args.arms = "0,C"
    print(
        f"head={head_spec.id} ({head_spec.label}) package=yhat_transformer "
        f"loss={args.loss} live_allowed=False tau_end={args.tau_end}",
        flush=True,
    )
    arms = _parse_arms(args.arms)
    need_fac = any(a in arms for a in ("0", "A", "C"))
    need_seq = any(a in arms for a in ("B", "C"))

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

    print(
        f"臂={arms} 池≤{len(codes)} lookback={args.lookback} L={args.seq_len} "
        f"holdout={args.holdout_trading_days} max_train={args.max_train_rows} "
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
        )
        print(
            f"序列面板 head={head_spec.id} N={seq.n} L={seq.seq_len} "
            f"F={seq.X.shape[-1]} {time.time()-t0:.1f}s rss={_rss_mb()}",
            flush=True,
        )
    if need_fac:
        from core.research.yhat_transformer.factor_panel import build_factor_panel

        t0 = time.time()
        print(f"构建因子面板 head={head_spec.id} pack={head_spec.factor_pack}…", flush=True)
        fac = build_factor_panel(
            stock_bars,
            head=head_spec,
            horizon_days=args.horizon_days,
            ridge_core_only=not bool(args.full_aoo),
            progress_every=20,
        )
        print(
            f"因子面板 N={fac.n} F={fac.X.shape[1]} "
            f"{time.time()-t0:.1f}s rss={_rss_mb()}",
            flush=True,
        )

    # Arm C / 公平切分：有 fac+seq 则对齐后共用索引
    if "C" in arms and seq is not None and fac is not None:
        from core.research.yhat_transformer.factor_panel import align_seq_and_factor

        seq_c, fac_c = align_seq_and_factor(seq, fac)
        print(f"对齐后 N={seq_c.n}（原 seq={seq.n} fac={fac.n}）", flush=True)
    else:
        seq_c, fac_c = seq, fac

    def _split_for(dates: Sequence[str]):
        cal = sorted({str(d)[:10] for d in dates if str(d)[:10]})
        return resolve_ridge_split(
            list(dates),
            holdout_trading_days=args.holdout_trading_days,
            label_horizon_days=args.horizon_days,
            calendar_dates=cal,
        )

    # ----- run arms -----
    if "B" in arms and seq is not None and seq.n > 0:
        tr, te, meta = _split_for(seq.dates)
        summary["split_B"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "n_train": len(tr),
            "n_test": len(te),
        }
        print(f"ArmB 切分 train={len(tr)} test={len(te)} eval={meta.get('eval_start')}", flush=True)
        if te:
            summary["arm_B"] = _run_arm_b(seq, tr, te, args)
        else:
            summary["arm_B"] = {"success": False, "error": "empty holdout"}

    if "0" in arms and fac is not None and fac.n > 0:
        tr, te, meta = _split_for(fac.dates)
        summary["split_0"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "n_train": len(tr),
            "n_test": len(te),
        }
        print(f"Arm0 切分 train={len(tr)} test={len(te)}", flush=True)
        if te:
            summary["arm_0"] = _run_arm0_lgb(
                fac, tr, te, rank=(head_spec.task_type == "rank")
            )
            a0 = summary["arm_0"]
            print(
                f"  Arm0 rankIC={a0.get('cs_rank_ic')} ICIR={a0.get('cs_rank_icir')} "
                f"{a0.get('elapsed_sec')}s",
                flush=True,
            )
        else:
            summary["arm_0"] = {"success": False, "error": "empty holdout"}

    if "A" in arms and fac is not None and fac.n > 0:
        tr, te, meta = _split_for(fac.dates)
        print(f"ArmA 切分 train={len(tr)} test={len(te)}", flush=True)
        if te:
            summary["arm_A"] = _run_arm_a(fac, tr, te, args)
        else:
            summary["arm_A"] = {"success": False, "error": "empty holdout"}

    if "C" in arms and seq_c is not None and fac_c is not None and seq_c.n > 0:
        import gc

        # O/A 已算完：丢掉原始大面板，只留对齐后的 seq_c/fac_c，压峰值
        try:
            del stock_bars
        except Exception:
            pass
        if seq is not None and seq_c is not seq:
            try:
                del seq
            except Exception:
                pass
        if fac is not None and fac_c is not fac:
            try:
                del fac
            except Exception:
                pass
        gc.collect()
        tr, te, meta = _split_for(seq_c.dates)
        summary["split_C"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "n_train": len(tr),
            "n_test": len(te),
        }
        print(
            f"ArmC 切分 train={len(tr)} test={len(te)} rss={_rss_mb()}",
            flush=True,
        )
        if te:
            try:
                summary["arm_C"] = _run_arm_c(seq_c, fac_c, tr, te, args)
            except Exception as exc:  # noqa: BLE001
                summary["arm_C"] = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"  ArmC 失败：{summary['arm_C']['error']}", flush=True)
        else:
            summary["arm_C"] = {"success": False, "error": "empty holdout"}

    # 对比表 + 硬门（相对 Arm0）；排序头 gate=gate_lift
    from core.research.yhat_transformer.metrics import (
        gate_lift_vs_baseline,
        hard_gate_vs_baseline,
    )

    gate_fn = (
        gate_lift_vs_baseline
        if head_spec.gate == "gate_lift"
        else hard_gate_vs_baseline
    )
    arm0_tag = (
        "0_lambdarank"
        if head_spec.task_type == "rank"
        else "0_lgb"
    )
    table = []
    for key, tag in (
        ("arm_0", arm0_tag),
        ("arm_A", "A_mlp"),
        ("arm_B", "B_seq_tf"),
        ("arm_C", "C_fusion"),
    ):
        if key in summary:
            table.append(_metric_row(tag, summary[key]))
    summary["comparison"] = table
    base = summary.get("arm_0") if (summary.get("arm_0") or {}).get("success") else None
    gates = {}
    if base:
        for key, tag in (("arm_A", "A"), ("arm_B", "B"), ("arm_C", "C")):
            pack = summary.get(key) or {}
            if not pack.get("success"):
                continue
            cand = pack.get("mean_over_seeds") or pack
            gates[tag] = gate_fn(base, cand)
    summary["hard_gate_vs_arm0"] = gates
    summary["gate"] = head_spec.gate
    summary["rss_mb_end"] = _rss_mb()

    print("\n=== 对比 ===", flush=True)
    print(
        f"{'arm':<12} {'rankIC':>8} {'ICIR':>8} {'hit':>8} {'topY':>8}",
        flush=True,
    )
    for r in table:
        print(
            f"{r['arm']:<12} "
            f"{(r.get('cs_rank_ic') if r.get('cs_rank_ic') is not None else float('nan')):>8.4f} "
            f"{(r.get('cs_rank_icir') if r.get('cs_rank_icir') is not None else float('nan')):>8.3f} "
            f"{(r.get('sign_hit') if r.get('sign_hit') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_top_mean_y') if r.get('cs_top_mean_y') is not None else float('nan')):>8.3f}",
            flush=True,
        )
    for k, g in gates.items():
        print(f"硬门 vs Arm0 · {k}: pass={g.get('pass')} lift={g.get('lift')} {g.get('reasons')}", flush=True)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
