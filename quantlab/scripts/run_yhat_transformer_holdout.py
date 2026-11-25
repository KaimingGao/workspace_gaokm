"""离线 Holdout：四头 ŷ_* 工厂单臂 / 可选双臂（默认 head=oo；研究轨，不入库）。

``--factory``：
  L = 仅 Arm·L（因子 + LightGBM / LambdaRank；不建序列面板）
  R = 仅 Arm·R（因子 + Ridge；不建序列面板）
  T = 仅 Arm·T（序列塔 ∥ 因子支；L2 立方 CS-z）
  LT = 旧 Scheme 双臂对照（页已拆分；CLI 保留）

Arm·L 固定全量因子。``--full-aoo`` / ``--atc-core`` 只改 Arm·T 因子列，不改 CS-z。
用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_yhat_transformer_holdout.py --head oo --factory T
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

# 排序 Arm·L：truncation=30，120 棵，深度 5，叶子 32，subsample=0.85，colsample=0.9，L2=1。
_ARM_L_RANK_N_ESTIMATORS = 120
_ARM_L_RANK_MAX_DEPTH = 5
_ARM_L_RANK_NUM_LEAVES = 32
_ARM_L_RANK_SUBSAMPLE = 0.85
_ARM_L_RANK_COLSAMPLE = 0.9
_ARM_L_RANK_L2 = 1.0
_ARM_L_RANK_TRUNCATION = 30


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


def _holdout_is_full_sample(holdout_trading_days: Any) -> bool:
    """Holdout 0 是执行套全样本，评估窗为空也要训练。"""
    try:
        return int(holdout_trading_days) == 0
    except (TypeError, ValueError):
        return False


def _train_despite_empty_holdout(
    n_train: int, n_test: int, holdout_trading_days: Any
) -> bool:
    """Holdout 0 且训练行还在：没有评估行也要训练。"""
    try:
        train_n = int(n_train)
        test_n = int(n_test)
    except (TypeError, ValueError):
        return False
    return _holdout_is_full_sample(holdout_trading_days) and train_n > 0 and test_n <= 0


def _eval_pack(preds, ys, metas, *, level: bool = False) -> Dict[str, Any]:
    from core.research.yhat_transformer.metrics import (
        daily_cs_ic_report,
        mean_daily_ndcg,
        pooled_pearson,
        holdout_mse,
        sign_hit,
    )

    out = daily_cs_ic_report(preds, ys, metas)
    out["pooled_ic"] = pooled_pearson(preds, ys)
    out["sign_hit"] = sign_hit(preds, ys)
    out["cs_ndcg_30"] = mean_daily_ndcg(preds, ys, metas, k=30)
    out["n"] = len(ys)
    if level:
        out["mse"] = holdout_mse(preds, ys)
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
    """Arm·T 训练行上限：``t_sample_frac``（缺省 1，不截断）；返回 0=不截断。"""
    n_train = max(0, int(n_train or 0))
    frac_raw = getattr(args, "t_sample_frac", None)
    try:
        frac = 1.0 if frac_raw is None else float(frac_raw)
    except (TypeError, ValueError):
        frac = 1.0
    if frac != frac or frac <= 0:
        frac = 1.0
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


def _theme_weights_from_panel(X: np.ndarray, names: Sequence[str]) -> Optional[np.ndarray]:
    """主题日权重与枢纽同口径（开盘可知，boost 1.5）。

    读 ``collect_tau_compact`` 写入的 ``z_τc_theme_day``。没有这一列，或列上没有主题日，则等权。
    """
    arr = np.asarray(X)
    name_list = [str(n) for n in names]
    index = {n: i for i, n in enumerate(name_list)}
    col_j = index.get("z_τc_theme_day")
    if (
        col_j is None
        or arr.ndim != 2
        or int(arr.shape[0]) == 0
        or int(arr.shape[1]) != len(name_list)
    ):
        return None
    col = np.asarray(arr[:, col_j], dtype=np.float64)
    theme = np.isfinite(col) & (np.rint(col) == 1.0)
    if not bool(theme.any()):
        return None
    return np.where(theme, 1.5, 1.0).astype(np.float64, copy=False)


def _tc_hub_columns(
    X: np.ndarray,
    names: Sequence[str],
    train_idx: Sequence[int],
) -> Tuple[np.ndarray, List[str]]:
    """τ=open 入模列与 ``fit_tau_model_matrices`` 同：丢掉分钟列和别名，留下有限 raw_atc。"""
    from core.research.panel_matrix import named_columns, raw_atc_finite
    from core.research.tc_model_compact import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    arr = np.asarray(X)
    name_list = [str(n) for n in names]
    if arr.ndim != 2 or int(arr.shape[1]) != len(name_list):
        return np.zeros((int(arr.shape[0]) if arr.ndim == 2 else 0, 0), dtype=np.float64), []
    drop = set(Z_TC_MINUTE_KEYS) | set(TAU_FIT_DROP_ALIASES)
    wanted: List[str] = [k for k in TAU_Z_FEATURES if k not in drop]
    seen = set(wanted)
    safe_idx: List[int] = []
    n_rows = int(arr.shape[0])
    for raw in train_idx or []:
        try:
            ii = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= ii < n_rows:
            safe_idx.append(ii)
    for key in raw_atc_finite(arr, name_list, safe_idx):
        if key not in seen:
            seen.add(key)
            wanted.append(key)
    return named_columns(arr, name_list, wanted)


def _stamp_open_clock(days: Sequence[dict]) -> List[dict]:
    out: List[dict] = []
    for day in days:
        row = dict(day)
        row.setdefault("clock", "open")
        out.append(row)
    return out


def _cap_rank_days(days: Sequence[dict], max_tr: int) -> List[dict]:
    """整日丢弃，凑到训练行上限。默认不截断（max_tr<=0）。"""
    rows = [d for d in days if d]
    if max_tr <= 0:
        return rows
    total = sum(len(d.get("ys") or []) for d in rows)
    if total <= max_tr:
        return rows
    rng = np.random.RandomState(42)
    order = list(range(len(rows)))
    rng.shuffle(order)
    kept: List[dict] = []
    n_kept = 0
    for gi in order:
        day = rows[int(gi)]
        n = len(day.get("ys") or [])
        if n_kept and n_kept + n > max_tr:
            continue
        kept.append(day)
        n_kept += n
    return kept


def _blank_fac_matrix(fac: Any) -> None:
    """入模矩阵已经单独拷出后，放开面板宽表。"""
    try:
        fac.X = np.empty((0, 0), dtype=np.float32)
    except Exception:
        return


def _arml_memory_stop(panel_bytes: int) -> str:
    """超水位就停，避免系统把拟合进程杀掉、桌面只能看到中途消失。"""
    from core.research.horizon_l1 import (
        confirm_fit_memory_stop,
        physical_ram_bytes,
        process_rss_bytes,
    )

    return (
        confirm_fit_memory_stop(
            panel_bytes=max(0, int(panel_bytes or 0)),
            rss_bytes=process_rss_bytes(),
            ram_bytes=physical_ram_bytes(),
            reread_rss=process_rss_bytes,
        )
        or ""
    )


def _arml_oom_error() -> str:
    return (
        "内存保护：分配失败，已停止以免进程被系统杀掉。"
        "请缩小池，或缩短 lookback / Holdout。"
    )


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
    # 全表保持原 dtype（面板是 float32）。训练行在 fit_* 里收成一份 float32。
    X = fac.X
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
    theme_boost = None
    try:
        if hid in ("tc", "tc_rank"):
            from core.research.horizon_l1 import release_allocator_cache
            from core.research.tc_lgb import fit_tc_lgb_matrix, predict_tc_lgb_matrix

            print(
                f"  ArmL τc columns start N={int(X.shape[0])} "
                f"F={int(X.shape[1])} rss={_rss_mb()}",
                flush=True,
            )
            # 截面广度已在 collect_tau_compact 写进面板，这里只做入模列过滤。
            weights = _theme_weights_from_panel(X, names)
            wide = X
            X_fit, feat_names = _tc_hub_columns(wide, names, tr)
            if not feat_names:
                return {
                    "arm": "L_lightgbm",
                    "success": False,
                    "error": "τc 入模列为空（枢纽列过滤后没有 z_τc / raw_atc）",
                    "elapsed_sec": round(time.time() - t0, 2),
                    "note": "live fit_tc_lgb_matrix + theme weights + hub columns",
                }
            if getattr(fac, "X", None) is not X_fit:
                _blank_fac_matrix(fac)
            X = X_fit
            names = feat_names
            del wide
            gc.collect()
            release_allocator_cache()
            if weights is not None:
                theme_boost = 1.5
            print(
                f"  ArmL τc columns F={len(feat_names)} theme_boost={theme_boost} "
                f"rss={_rss_mb()}",
                flush=True,
            )
            stop = _arml_memory_stop(int(getattr(X_fit, "nbytes", 0) or 0))
            if stop:
                return {
                    "arm": "L_lightgbm",
                    "success": False,
                    "error": stop,
                    "elapsed_sec": round(time.time() - t0, 2),
                    "note": "live fit_tc_lgb_matrix + theme weights + hub columns",
                }
            blob, report = fit_tc_lgb_matrix(
                X,
                y,
                names,
                row_idx=tr,
                row_dates=dates,
                sample_weights=weights,
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
    except MemoryError:
        return {
            "arm": "L_lightgbm",
            "success": False,
            "error": _arml_oom_error(),
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
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    n_miss = 0
    if te:
        print(f"  ArmL predict test={len(te)} rss={_rss_mb()}", flush=True)
        preds_raw = predict_fn(blob, X, names, te, row_dates=dates)
        ys_all = [float(y[i]) for i in te]
        metas_all = _metas([fac.codes[i] for i in te], [fac.dates[i] for i in te])
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
    else:
        print("  ArmL Holdout=0 全样本，跳过评估", flush=True)
    if not preds and te:
        return {
            "arm": "L_lightgbm",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "n_train": n_train,
            "n_test": len(te),
            "elapsed_sec": round(time.time() - t0, 2),
            "note": "live fit_*_lgb_matrix + L2 cube CS-z",
        }
    metrics = _eval_pack(preds, ys, metas, level=True) if preds else {}
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
            "theme_boost": theme_boost,
            "live_aligned": True,
            "full_sample": not bool(te),
        }
    )
    span = _eval_span([str(fac.dates[i]) for i in te])
    feat_names = [str(n) for n in (blob.get("feature_names") or names)]
    _try_save_lightgbm(
        blob,
        {
            "head": hid,
            "factor_pack": _factor_pack(hid, None),
            "factor_names": feat_names,
            "solver": str(blob.get("solver") or "lightgbm"),
            "seed": None,
            **span,
        },
    )
    del blob, X, y
    gc.collect()
    return metrics


def _rank_day_packs(
    fac,
    idx: Sequence[int],
    X: np.ndarray,
    names: Sequence[str],
) -> List[dict]:
    """训练/测试行 → 枢纽日包（按日 ≥8 名，clock=open）。"""
    from core.research.oo_rank_panel import group_day_matrices

    ii = [int(i) for i in idx]
    if not ii:
        return []
    pos = np.asarray(ii, dtype=np.int64)
    days = group_day_matrices(
        np.asarray(X)[pos],
        np.asarray(fac.y, dtype=np.float64)[pos],
        list(names),
        [fac.dates[i] for i in ii],
        [fac.codes[i] for i in ii],
        min_names=8,
    )
    return _stamp_open_clock(days)


def _run_arm_l_rank(
    fac,
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    *,
    head_id: str = "oo_rank",
    t0: float,
) -> Dict[str, Any]:
    """排序 Arm·L：日包 ``fit_lambdarank``。

    truncation=30，120 棵，深度 5，叶子 32，subsample=0.85，colsample=0.9，L2=1。
    threads=1，至少 5 个交易日，特征走截面立方 z。

    ŷ_τc_rank 的截面列已在 ``collect_tau_compact`` 里。这里只按枢纽列过滤
    （丢掉分钟列和别名，留下有限 raw_atc）。不把 booster 写入研究枢纽槽。
    """
    import gc

    from core.research.materialize.consumer import MaterializeRequired
    from core.research.oo_rank_lambdarank import (
        _predict_oo_rank_matrix,
        fit_lambdarank,
    )

    hid = str(head_id or "oo_rank").strip() or "oo_rank"
    names = [str(n) for n in fac.feature_names]
    max_tr = _arm_l_max_train_rows()
    X = fac.X
    if hid == "tc_rank":
        wide = X
        X, names = _tc_hub_columns(wide, names, train_idx)
        if not names:
            return {
                "arm": "L_lambdarank",
                "success": False,
                "error": "τc_rank 入模列为空（枢纽列过滤后没有 z_τc / raw_atc）",
                "elapsed_sec": round(time.time() - t0, 2),
                "head": hid,
            }
        if getattr(fac, "X", None) is not X:
            _blank_fac_matrix(fac)
        del wide
        gc.collect()
    print(
        f"  ArmL rank day-pack start head={hid} N={int(np.asarray(X).shape[0])} "
        f"F={len(names)} rss={_rss_mb()}",
        flush=True,
    )
    days_tr = _cap_rank_days(_rank_day_packs(fac, train_idx, X, names), max_tr)
    if max_tr > 0:
        print(
            f"  ArmL rank day cap groups={len(days_tr)} cap={max_tr} "
            "(env override, not live default)",
            flush=True,
        )
    print(
        f"  ArmL fit start head={hid} objective=lambdarank "
        f"truncation={_ARM_L_RANK_TRUNCATION} trees={_ARM_L_RANK_N_ESTIMATORS} "
        f"depth={_ARM_L_RANK_MAX_DEPTH} leaves={_ARM_L_RANK_NUM_LEAVES} "
        f"subsample={_ARM_L_RANK_SUBSAMPLE} colsample={_ARM_L_RANK_COLSAMPLE} "
        f"l2={_ARM_L_RANK_L2:g} "
        f"groups={len(days_tr)} F={len(names)} threads=1 rss={_rss_mb()}",
        flush=True,
    )
    try:
        fit = fit_lambdarank(
            days_tr,
            feature_names=names,
            n_estimators=_ARM_L_RANK_N_ESTIMATORS,
            max_depth=_ARM_L_RANK_MAX_DEPTH,
            num_leaves=_ARM_L_RANK_NUM_LEAVES,
            subsample=_ARM_L_RANK_SUBSAMPLE,
            colsample_bytree=_ARM_L_RANK_COLSAMPLE,
            l2=_ARM_L_RANK_L2,
            truncation_level=_ARM_L_RANK_TRUNCATION,
        )
    except MemoryError:
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": _arml_oom_error(),
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": "live fit_lambdarank day-pack",
        }
    except MaterializeRequired as exc:
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": "live fit_lambdarank day-pack",
        }
    if not isinstance(fit, dict) or not fit.get("success"):
        err = (fit or {}).get("error") if isinstance(fit, dict) else "lambdarank_fit_failed"
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": err or "lambdarank_fit_failed",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_groups": (fit or {}).get("n_groups") if isinstance(fit, dict) else None,
            "note": "live fit_lambdarank day-pack",
        }
    days_te = _rank_day_packs(fac, test_idx, X, names)
    print(
        f"  ArmL predict test_days={len(days_te)} rss={_rss_mb()}",
        flush=True,
    )
    preds: List[float] = []
    ys: List[float] = []
    codes: List[str] = []
    dates_te: List[str] = []
    n_miss = 0
    for day in days_te:
        scored = _predict_oo_rank_matrix(fit, day)
        day_ys = list(day.get("ys") or [])
        day_codes = list(day.get("codes") or [])
        day_date = str(day.get("date") or "")[:10]
        for i, yy in enumerate(day_ys):
            pv = scored[i] if i < len(scored) else None
            try:
                fv = float(pv) if pv is not None else float("nan")
            except (TypeError, ValueError):
                fv = float("nan")
            if fv != fv:
                n_miss += 1
                continue
            preds.append(fv)
            ys.append(float(yy))
            codes.append(str(day_codes[i]) if i < len(day_codes) else "")
            dates_te.append(day_date)
    if not preds and (days_te or list(test_idx)):
        return {
            "arm": "L_lambdarank",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_groups": fit.get("n_groups"),
            "note": "live fit_lambdarank day-pack",
        }
    if not preds and not days_te:
        print("  ArmL Holdout=0 全样本，跳过评估", flush=True)
    metrics = (
        _eval_pack(preds, ys, _metas(codes, dates_te)) if preds else {}
    )
    n_train = int(fit.get("sample_count") or 0)
    metrics.update(
        {
            "arm": "L_lambdarank",
            "success": True,
            "head": hid,
            "n_features": int(len(names)),
            "n_train": n_train,
            "n_test": len(preds),
            "n_pred_miss": int(n_miss),
            "n_groups": fit.get("n_groups"),
            "full_sample": not bool(preds) and not bool(days_te),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": (
                "live fit_lambdarank day-pack · num_threads=1 · cube CS-z "
                f"truncation={_ARM_L_RANK_TRUNCATION} "
                f"trees={_ARM_L_RANK_N_ESTIMATORS} "
                f"depth={_ARM_L_RANK_MAX_DEPTH} "
                f"leaves={_ARM_L_RANK_NUM_LEAVES} "
                f"subsample={_ARM_L_RANK_SUBSAMPLE} "
                f"colsample={_ARM_L_RANK_COLSAMPLE} "
                f"l2={_ARM_L_RANK_L2:g} "
                f"(head={hid})"
            ),
            "lambdarank_truncation_level": int(_ARM_L_RANK_TRUNCATION),
            "n_estimators": int(_ARM_L_RANK_N_ESTIMATORS),
            "max_depth": int(_ARM_L_RANK_MAX_DEPTH),
            "num_leaves": int(_ARM_L_RANK_NUM_LEAVES),
            "subsample": float(_ARM_L_RANK_SUBSAMPLE),
            "colsample_bytree": float(_ARM_L_RANK_COLSAMPLE),
            "l2": float(_ARM_L_RANK_L2),
            "max_train_cap": int(max_tr),
            "live_aligned": True,
        }
    )
    span = _eval_span(dates_te)
    _try_save_lightgbm(
        fit,
        {
            "head": hid,
            "factor_pack": _factor_pack(hid, None),
            "factor_names": list(names),
            "solver": str(fit.get("solver") or "lambdarank"),
            "z_clip": fit.get("z_clip"),
            "lambdarank_truncation_level": int(_ARM_L_RANK_TRUNCATION),
            "n_estimators": int(_ARM_L_RANK_N_ESTIMATORS),
            "max_depth": int(_ARM_L_RANK_MAX_DEPTH),
            "num_leaves": int(_ARM_L_RANK_NUM_LEAVES),
            "subsample": float(_ARM_L_RANK_SUBSAMPLE),
            "colsample_bytree": float(_ARM_L_RANK_COLSAMPLE),
            "l2": float(_ARM_L_RANK_L2),
            "seed": None,
            **span,
        },
    )
    del fit, X
    gc.collect()
    return metrics


def _fit_arm_r_like_hub(
    fac,
    X,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    tr_pos: Sequence[int],
    te_pos: Sequence[int],
    *,
    hid: str,
    ridge_alpha: float,
    t0: float,
) -> Dict[str, Any]:
    """回归头与 main 的 ``fit_oo_ridge_matrix`` 同口径。

    先丢稀疏列、真常数和覆盖不够的列，再做趋势族共线剔除。只留每一列都有数的行。
    这些行对齐 L2 后做截面 z。Holdout 打分缺测不加项，整行都缺则不进指标。
    """
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.oo_model_compact import fit_oo_ridge_matrix, predict_oo_matrix

    note = "Ridge · 枢纽同口径 · 完整行 · 缺测跳过"
    try:
        model, report = fit_oo_ridge_matrix(
            np.asarray(X),
            y,
            names,
            row_idx=list(tr_pos),
            horizon_days=1,
            ridge_lambda=float(ridge_alpha),
            min_samples=24,
            row_dates=list(dates),
            cross_section_zscore=True,
        )
    except MaterializeRequired as exc:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": note,
        }
    report = report if isinstance(report, dict) else {}
    if model is None or not report.get("success"):
        return {
            "arm": "R_ridge",
            "success": False,
            "error": str(report.get("error") or "Ridge 求解失败"),
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_train": int(report.get("sample_count") or 0),
            "note": note,
        }
    coef_map = {
        str(k): float(v) for k, v in dict(getattr(model, "coefficients", {}) or {}).items()
    }
    active = [str(n) for n in (report.get("active_features") or []) if str(n) in coef_map]
    if not active:
        active = list(coef_map.keys())
    coef = [coef_map[n] for n in active]
    if not coef:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": "Ridge 入模列为空",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": note,
        }
    intercept = float(getattr(model, "intercept", 0.0) or 0.0)
    n_train = int(report.get("sample_count") or 0)
    excluded = [str(n) for n in (report.get("excluded_features") or [])]
    print(
        f"  ArmR fit done n_train_fit={n_train} F={len(active)} "
        f"excluded={len(excluded)} {time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    n_miss = 0
    if te_pos:
        try:
            raw_preds = predict_oo_matrix(
                model,
                np.asarray(X),
                names,
                list(te_pos),
                row_dates=list(dates),
            )
        except MaterializeRequired as exc:
            return {
                "arm": "R_ridge",
                "success": False,
                "error": f"MaterializeRequired: {exc}",
                "elapsed_sec": round(time.time() - t0, 2),
                "head": hid,
                "n_train": n_train,
                "note": note,
            }
        for i, fv in zip(te_pos, raw_preds):
            if fv is None:
                n_miss += 1
                continue
            yy = float(y[i])
            val = float(fv)
            if not np.isfinite(val) or not np.isfinite(yy):
                n_miss += 1
                continue
            preds.append(val)
            ys.append(yy)
            metas.append(
                {
                    "code": str(fac.codes[i]),
                    "date": str(fac.dates[i])[:10],
                    "decision_date": str(fac.dates[i])[:10],
                }
            )
    else:
        print("  ArmR Holdout=0 全样本，跳过评估", flush=True)
    if not preds and te_pos:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "n_train": n_train,
            "n_test": len(te_pos),
            "elapsed_sec": round(time.time() - t0, 2),
            "note": note,
        }
    metrics = _eval_pack(preds, ys, metas, level=True) if preds else {}
    metrics.update(
        {
            "arm": "R_ridge",
            "success": True,
            "n_features": len(active),
            "n_excluded": len(excluded),
            "n_train": n_train,
            "n_test": len(te_pos),
            "n_eval": len(preds),
            "n_pred_miss": int(n_miss),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": note,
            "ridge_alpha": float(ridge_alpha),
            "live_aligned": False,
            "full_sample": not bool(te_pos),
        }
    )
    span = _eval_span([str(fac.dates[i]) for i in te_pos])
    _try_save_ridge(
        {
            "alpha": float(ridge_alpha),
            "intercept": intercept,
            "coef": coef,
            "feature_names": list(active),
        },
        {
            "head": hid,
            "factor_pack": _factor_pack(hid, None),
            "factor_names": list(active),
            "solver": "ridge",
            "alpha": float(ridge_alpha),
            "intercept": intercept,
            "z_clip": None,
            "missing": "skip",
            "task": "regression",
            "seed": None,
            **span,
        },
    )
    return metrics


def _fit_arm_r_tc(
    fac,
    X,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    tr_pos: Sequence[int],
    te_pos: Sequence[int],
    *,
    hid: str,
    ridge_alpha: float,
    t0: float,
) -> Dict[str, Any]:
    """ŷ_τc 与 main 的 ``fit_keepall_ridge_matrix`` 同口径。

    共线列保留。主题日样本权 1.5，其余 1。Holdout 上任一入模列缺测，整行不进指标。
    """
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.panel_matrix import fit_keepall_ridge_matrix, predict_ridge_matrix
    from core.signal.factors.sum_identity import filter_predict_active_names

    note = "Ridge · τc 枢纽同口径 · 共线保留 · 主题日×1.5 · 缺列整行不算"
    tr_ii = np.asarray(list(tr_pos), dtype=np.int64)
    te_ii = np.asarray(list(te_pos), dtype=np.int64)
    x_arr = np.asarray(X, dtype=np.float64)
    y_tr = np.asarray(y, dtype=np.float64)[tr_ii]
    dates_tr = [str(dates[i])[:10] for i in tr_pos]
    theme_all = _theme_weights_from_panel(x_arr, names)
    weights = None
    if theme_all is not None and int(theme_all.shape[0]) == int(x_arr.shape[0]):
        weights = [float(v) for v in np.asarray(theme_all, dtype=np.float64)[tr_ii].tolist()]
    try:
        fit = fit_keepall_ridge_matrix(
            x_arr[tr_ii],
            y_tr,
            names,
            ridge_lambda=float(ridge_alpha),
            sample_weights=weights,
            row_dates=dates_tr,
            cross_section_zscore=True,
        )
    except MaterializeRequired as exc:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": note,
        }
    fit = fit if isinstance(fit, dict) else {}
    if not fit.get("success"):
        return {
            "arm": "R_ridge",
            "success": False,
            "error": str(fit.get("error") or "Ridge 求解失败"),
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_train": int(fit.get("sample_count") or 0),
            "note": note,
        }
    coef_map = {
        str(k): float(v)
        for k, v in dict(fit.get("coefficients") or {}).items()
        if v is not None
    }
    active = [
        str(n)
        for n in filter_predict_active_names(
            fit.get("active_features") or list(coef_map.keys()),
            coef_map,
        )
    ]
    coef = [coef_map[n] for n in active if n in coef_map]
    active = [n for n in active if n in coef_map]
    if not coef:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": "Ridge 入模列为空",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": note,
        }
    intercept = float(fit.get("intercept") or 0.0)
    n_train = int(fit.get("sample_count") or 0)
    excluded = [str(n) for n in (fit.get("excluded_features") or [])]
    print(
        f"  ArmR fit done n_train_fit={n_train} F={len(active)} "
        f"excluded={len(excluded)} theme_weight={int(weights is not None)} "
        f"{time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )
    try:
        raw_preds = (
            predict_ridge_matrix(
                fit,
                x_arr[te_ii],
                names,
                row_dates=[str(dates[i])[:10] for i in te_pos],
            )
            if len(te_pos)
            else []
        )
    except MaterializeRequired as exc:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_train": n_train,
            "note": note,
        }
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    n_miss = 0
    for i, fv in zip(te_pos, raw_preds):
        if fv is None:
            n_miss += 1
            continue
        yy = float(y[i])
        val = float(fv)
        if not np.isfinite(val) or not np.isfinite(yy):
            n_miss += 1
            continue
        preds.append(val)
        ys.append(yy)
        metas.append(
            {
                "code": str(fac.codes[i]),
                "date": str(fac.dates[i])[:10],
                "decision_date": str(fac.dates[i])[:10],
            }
        )
    if not preds and te_pos:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "n_train": n_train,
            "n_test": len(te_pos),
            "elapsed_sec": round(time.time() - t0, 2),
            "note": note,
        }
    if not preds:
        print("  ArmR Holdout=0 全样本，跳过评估", flush=True)
    metrics = _eval_pack(preds, ys, metas, level=True) if preds else {}
    metrics.update(
        {
            "arm": "R_ridge",
            "success": True,
            "n_features": len(active),
            "n_excluded": len(excluded),
            "n_train": n_train,
            "n_test": len(te_pos),
            "n_eval": len(preds),
            "n_pred_miss": int(n_miss),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": note,
            "ridge_alpha": float(ridge_alpha),
            "live_aligned": False,
            "full_sample": not bool(te_pos),
        }
    )
    span = _eval_span([str(fac.dates[i]) for i in te_pos])
    _try_save_ridge(
        {
            "alpha": float(ridge_alpha),
            "intercept": intercept,
            "coef": coef,
            "feature_names": list(active),
        },
        {
            "head": hid,
            "factor_pack": _factor_pack(hid, None),
            "factor_names": list(active),
            "solver": "ridge",
            "alpha": float(ridge_alpha),
            "intercept": intercept,
            "z_clip": None,
            "missing": "complete",
            "task": "regression",
            "seed": None,
            **span,
        },
    )
    return metrics


def _run_arm_r(
    fac,
    train_idx,
    test_idx,
    *,
    head_id: str = "oo",
    rank: bool = False,
    ridge_alpha: float = 1.0,
) -> Dict[str, Any]:
    """Arm·R。ŷ_oo 对齐枢纽隔夜 Ridge，ŷ_τc 对齐 main 的 τc Ridge。

    训练窗在调用前已收到 open 立方起点。回归头只把训练行送进对应拟合，
    垫根不参与。排序头只给训练行和 Holdout 行打 z，这两段里的缺日仍
    ``MaterializeRequired``。排序头按 LambdaRank 同样裁 ±Z，评测仍对照实现收益。
    """
    import gc

    from core.research.factor_ols_fit import clamp_ridge_lambda
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.yhat_ridge import dense_rank_targets, fit_ridge

    t0 = time.time()
    ridge_alpha = clamp_ridge_lambda(ridge_alpha, 1.0)
    hid = str(head_id or "oo").strip() or "oo"
    names = [str(n) for n in fac.feature_names]
    dates = [str(d)[:10] for d in fac.dates]
    X = fac.X
    y = np.asarray(fac.y, dtype=np.float64)
    note = "Ridge · L2 cube CS-z"
    if rank:
        note = "Ridge · L2 cube CS-z · 训练标签=日内稠密名次"
    tr = [int(i) for i in train_idx]
    te = [int(i) for i in test_idx]
    print(
        f"  ArmR begin head={hid} rank={int(bool(rank))} "
        f"N={int(getattr(fac, 'n', 0) or 0)} F={len(names)} "
        f"alpha={float(ridge_alpha):.6g} rss={_rss_mb()}",
        flush=True,
    )
    if hid in ("tc", "tc_rank"):
        wide = X
        X, names = _tc_hub_columns(wide, names, tr)
        if not names:
            return {
                "arm": "R_ridge",
                "success": False,
                "error": "τc 入模列为空（枢纽列过滤后没有 z_τc / raw_atc）",
                "elapsed_sec": round(time.time() - t0, 2),
                "head": hid,
                "note": note,
            }
        if getattr(fac, "X", None) is not X:
            _blank_fac_matrix(fac)
        del wide
        gc.collect()
    n_features = int(np.asarray(X).shape[1]) if np.asarray(X).ndim == 2 else 0
    n_rows = int(y.shape[0])
    tr_pos = [i for i in tr if 0 <= i < n_rows]
    te_pos = [i for i in te if 0 <= i < n_rows]
    if not rank:
        fit_head = _fit_arm_r_tc if hid == "tc" else _fit_arm_r_like_hub
        return fit_head(
            fac,
            X,
            y,
            names,
            dates,
            tr_pos,
            te_pos,
            hid=hid,
            ridge_alpha=float(ridge_alpha),
            t0=t0,
        )
    try:
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        tr_ii = np.asarray(tr_pos, dtype=np.int64)
        X_tr = np.array(np.asarray(X)[tr_ii], dtype=np.float64, copy=True)
        y_tr = np.asarray(y[tr_ii], dtype=np.float64)
        dates_tr = [dates[i] for i in tr_pos]
        if int(X_tr.shape[0]) == 0:
            Xz_tr = X_tr
        else:
            Xz_tr = cs_z_matrix_prefer_cube(
                X_tr, names, dates_tr, clock="open", inplace=True
            )
        if te_pos:
            te_ii = np.asarray(te_pos, dtype=np.int64)
            X_te = np.array(np.asarray(X)[te_ii], dtype=np.float64, copy=True)
            dates_te = [dates[i] for i in te_pos]
            Xz_te = cs_z_matrix_prefer_cube(
                X_te, names, dates_te, clock="open", inplace=True
            )
        else:
            Xz_te = np.zeros((0, n_features), dtype=np.float64)
    except MaterializeRequired as exc:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "note": note,
        }
    z_clip = None
    if rank:
        from core.research.oo_rank_lambdarank import LAMBDARANK_Z_CLIP

        z_clip = float(LAMBDARANK_Z_CLIP)
        if z_clip > 0:
            Xz_tr = np.clip(Xz_tr, -z_clip, z_clip)
            Xz_te = np.clip(Xz_te, -z_clip, z_clip)
    Xz_tr = np.where(np.isfinite(Xz_tr), Xz_tr, 0.0)
    Xz_te = np.where(np.isfinite(Xz_te), Xz_te, 0.0)
    local = list(range(int(np.asarray(y_tr).shape[0])))
    if rank:
        y_fit, fit_rows = dense_rank_targets(y_tr, dates_tr, local, min_names=8)
        if len(fit_rows) < 24:
            return {
                "arm": "R_ridge",
                "success": False,
                "error": "排序日不足（每日至少 8 名，且训练行不足 24）",
                "elapsed_sec": round(time.time() - t0, 2),
                "head": hid,
                "note": note,
            }
    else:
        y_fit = y_tr
        fit_rows = [i for i in local if np.isfinite(y_tr[i])]
        if len(fit_rows) < 24:
            return {
                "arm": "R_ridge",
                "success": False,
                "error": f"训练样本不足（{len(fit_rows)} < 24）",
                "elapsed_sec": round(time.time() - t0, 2),
                "head": hid,
                "note": note,
            }
    fit_ii = np.asarray(fit_rows, dtype=np.int64)
    solved = fit_ridge(Xz_tr[fit_ii], y_fit[fit_ii], ridge_alpha)
    if solved is None:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": "Ridge 求解失败",
            "elapsed_sec": round(time.time() - t0, 2),
            "head": hid,
            "n_train": len(fit_rows),
            "note": note,
        }
    intercept, coef = solved
    print(
        f"  ArmR fit done n_train_fit={len(fit_rows)} F={n_features} "
        f"{time.time() - t0:.1f}s rss={_rss_mb()}",
        flush=True,
    )
    pred = intercept + Xz_te @ coef
    preds: List[float] = []
    ys: List[float] = []
    metas: List[dict] = []
    n_miss = 0
    for i, fv in zip(te_pos, pred.tolist()):
        yy = float(y[i])
        if not np.isfinite(fv) or not np.isfinite(yy):
            n_miss += 1
            continue
        preds.append(float(fv))
        ys.append(yy)
        metas.append(
            {
                "code": str(fac.codes[i]),
                "date": str(fac.dates[i])[:10],
                "decision_date": str(fac.dates[i])[:10],
            }
        )
    if not preds and te_pos:
        return {
            "arm": "R_ridge",
            "success": False,
            "error": f"holdout preds empty (soft_miss={n_miss})",
            "n_train": len(fit_rows),
            "n_test": len(te),
            "elapsed_sec": round(time.time() - t0, 2),
            "note": note,
        }
    if not preds:
        print("  ArmR Holdout=0 全样本，跳过评估", flush=True)
    metrics = _eval_pack(preds, ys, metas) if preds else {}
    metrics.update(
        {
            "arm": "R_ridge",
            "success": True,
            "n_features": n_features,
            "n_train": len(fit_rows),
            "n_test": len(te),
            "n_eval": len(preds),
            "n_pred_miss": int(n_miss),
            "elapsed_sec": round(time.time() - t0, 2),
            "rss_mb": _rss_mb(),
            "note": note,
            "ridge_alpha": float(ridge_alpha),
            "live_aligned": False,
            "full_sample": not bool(te_pos),
        }
    )
    span = _eval_span([str(fac.dates[i]) for i in te_pos])
    _try_save_ridge(
        {
            "alpha": float(ridge_alpha),
            "intercept": float(intercept),
            "coef": [float(v) for v in coef.tolist()],
            "feature_names": list(names),
        },
        {
            "head": hid,
            "factor_pack": _factor_pack(hid, None),
            "factor_names": list(names),
            "solver": "ridge",
            "alpha": float(ridge_alpha),
            "intercept": float(intercept),
            "z_clip": z_clip,
            "task": "rank" if rank else "regression",
            "seed": None,
            **span,
        },
    )
    del Xz_tr, Xz_te, coef
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


def _drop_aligned_rows_without_cube(seq, fac):
    """对齐后丢掉早于第一份可读 open 立方的行。

    起点与截面标准化同一读法：文件能列出 μ/σ 才算。跨度开头读失败的文件跳过。
    起点当天及之后仍缺的日期留下，截面标准化照旧失败。一份可读立方都没有时原样返回。
    日期全部早于起点时清空面板，避免带着必败的缺日进入截面标准化。
    """
    from core.research.materialize.cube_z import first_readable_cube_day

    n = int(getattr(fac, "n", 0) or 0)
    if n <= 0 or int(getattr(seq, "n", 0) or 0) != n:
        return seq, fac
    first, skipped = first_readable_cube_day("open")
    if len(first) < 10:
        if skipped:
            print(
                f"ArmT 本地立方文件读不出 μ/σ（{skipped} 个），保留全部 N={n}",
                flush=True,
            )
        else:
            print(f"ArmT 本地无可用 L2，保留全部 N={n}", flush=True)
        return seq, fac
    if skipped:
        print(
            f"ArmT 跳过读不出 μ/σ 的立方前缀 {skipped} 个，起点 {first}",
            flush=True,
        )
    early_example: List[str] = []
    keep: List[int] = []
    for i, raw in enumerate(fac.dates):
        day = str(raw)[:10]
        if len(day) >= 10 and day >= first:
            keep.append(i)
            continue
        if len(early_example) < 4 and len(day) >= 10 and day not in early_example:
            early_example.append(day)
    if len(keep) == n:
        return seq, fac
    if not keep:
        print(
            f"ArmT 对齐日期都早于立方起点 {first}，面板清空 N={n} 例如 {early_example}",
            flush=True,
        )
        return seq.subset([]), fac.subset([])
    n_drop = n - len(keep)
    print(
        f"ArmT 丢掉早于立方起点 {first} 的历史行 {n_drop}/{n} 例如 {early_example}",
        flush=True,
    )
    return seq.subset(keep), fac.subset(keep)


def _factor_pack(head_id: str, args: Any) -> str:
    """``args is None`` 表示 Arm·L：因子始终全量。"""
    hid = str(head_id or "")
    if hid in ("tc", "tc_rank"):
        if args is None:
            return "atc_full"
        return "atc_core" if bool(getattr(args, "atc_core", False)) else "atc_full"
    if hid in ("oo", "oo_rank"):
        if args is None:
            return "aoo_full"
        return "aoo_full" if bool(getattr(args, "full_aoo", False)) else "aoo_core"
    return ""


def _eval_span(dates: Sequence[str]) -> Dict[str, Optional[str]]:
    days = sorted({str(d)[:10] for d in dates if str(d or "")[:10]})
    if not days:
        return {"eval_start": None, "eval_end": None}
    return {"eval_start": days[0], "eval_end": days[-1]}


def _try_save_lightgbm(blob: Dict[str, Any], meta: Dict[str, Any]) -> None:
    try:
        from core.research.yhat_factory_weights import (
            lightgbm_text_from_blob,
            save_lightgbm_pending,
        )

        from core.research.yhat_tf_desk import factory_scope

        text = lightgbm_text_from_blob(blob)
        if not text:
            print("  权重未保存：LightGBM 模型文本为空", flush=True)
            return
        # 归档按当前工厂找临时目录。不进 L 作用域时会写到 Arm-T 目录，L 版本就挂不上权重。
        with factory_scope("L"):
            saved = save_lightgbm_pending(text, meta)
        if not saved:
            print("  权重未保存：无 YHAT_FACTORY_RUN_ID（非模型中心拉起）", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  权重未保存：{exc}", flush=True)


def _try_save_ridge(blob: Dict[str, Any], meta: Dict[str, Any]) -> None:
    try:
        from core.research.yhat_factory_weights import save_ridge_pending
        from core.research.yhat_tf_desk import factory_scope

        with factory_scope("R"):
            saved = save_ridge_pending(blob, meta)
        if not saved:
            print("  权重未保存：无 YHAT_FACTORY_RUN_ID（非模型中心拉起）", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  权重未保存：{exc}", flush=True)


def _try_save_transformer(state: Dict[str, Any], meta: Dict[str, Any]) -> None:
    try:
        from core.research.yhat_factory_weights import save_transformer_pending
        from core.research.yhat_tf_desk import factory_scope

        with factory_scope("T"):
            saved = save_transformer_pending(state, meta)
        if not saved:
            print("  ArmT 权重未保存：无 YHAT_FACTORY_RUN_ID（非模型中心拉起）", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  ArmT 权重未保存：{exc}", flush=True)


def _run_arm_t(seq, fac, train_idx, test_idx, args) -> Dict[str, Any]:
    """Arm·T。固定 seed=42，跑满 epochs。

    因子面板只负责对齐样本。序列里的因子列走开盘立方截面 z。
    早于立方的行已在对齐后丢掉；序列缺立方则显式失败，不做列标准化回退。
    """
    import gc

    from core.research.materialize.consumer import MaterializeRequired
    from core.research.yhat_transformer.train import (
        predict_arm_t,
        train_arm_t,
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
    n_features_factor = int(getattr(fac.X, "shape", (0, 0))[1] or 0)
    try:
        fac.X = np.empty((0, 0), dtype=np.float32)
    except Exception:
        pass
    gc.collect()

    # 排序头与 Arm·L LambdaRank 同裁 ±Z_CLIP；回归头不裁
    head_id = str(getattr(args, "head", "") or "").strip().lower()
    z_clip = None
    if head_id in ("oo_rank", "tc_rank"):
        from core.research.oo_rank_lambdarank import LAMBDARANK_Z_CLIP

        z_clip = float(LAMBDARANK_Z_CLIP)
    from core.research.yhat_transformer.factor_panel import cs_z_factor_sequence

    print(
        f"  ArmT 序列截面 z start N={int(getattr(seq, 'n', 0) or 0)} "
        f"clock=open rss={_rss_mb()}",
        flush=True,
    )
    try:
        cs_z_factor_sequence(seq, z_clip=z_clip)
    except MaterializeRequired as exc:
        return {
            "arm": "T_transformer",
            "success": False,
            "error": f"MaterializeRequired: {exc}",
            "elapsed_sec": round(time.time() - t0, 2),
            "n_features_factor": n_features_factor,
            "note": "sequence-only; open-cube CS-z on the sequence",
        }
    seq_cs_z = bool(getattr(getattr(seq, "X", None), "cs_z", False))
    if seq_cs_z:
        print(
            f"  ArmT 序列截面 z F={int(seq.X.values.shape[1])} "
            f"z_clip={z_clip if z_clip else '—'} rss={_rss_mb()}",
            flush=True,
        )
    print(
        f"  ArmT 序列截面 z done {time.time() - t0:.1f}s rss={_rss_mb()}",
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
    init_state = None
    if getattr(args, "finetune", False):
        from core.research.yhat_factory_versions import latest_transformer_pretrain

        loaded = latest_transformer_pretrain(head_id or str(getattr(args, "head", "") or ""))
        if not loaded.get("success"):
            return {
                "arm": "T_transformer",
                "success": False,
                "error": str(loaded.get("error") or "还没有大池预训练权重"),
                "elapsed_sec": round(time.time() - t0, 2),
            }
        pre_meta = loaded.get("meta") if isinstance(loaded.get("meta"), dict) else {}
        if int(pre_meta.get("arch") or 3) < 3:
            return {
                "arm": "T_transformer",
                "success": False,
                "error": "预训练权重是旧结构，不能微调",
                "elapsed_sec": round(time.time() - t0, 2),
            }
        if not pre_meta.get("seq_through_decision"):
            return {
                "arm": "T_transformer",
                "success": False,
                "error": "预训练权重的因子序列停在 T-1，不能微调进含决策日的窗口",
                "elapsed_sec": round(time.time() - t0, 2),
            }
        init_state = loaded.get("state_dict")
        print(
            f"  ArmT finetune from {loaded.get('pretrain_run_id')}",
            flush=True,
        )
    try:
        state, report = train_arm_t(
            seq,
            train_idx,
            d_model=args.d_model,
            nhead=args.nhead,
            nlayers=args.nlayers,
            dropout=args.dropout,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=float(args.lr),
            seed=seed,
            device=args.device,
            max_train_rows=max_tr,
            loss=getattr(args, "loss", "mse"),
            holdout_idx=list(test_idx),
            init_state=init_state,
        )
    except ValueError as exc:
        return {
            "arm": "T_transformer",
            "success": False,
            "error": str(exc),
            "elapsed_sec": round(time.time() - t0, 2),
        }
    Xseq = report.pop("Xseq")
    gap = report.pop("gap")
    report.pop("y", None)
    report.pop("dates", None)
    gc.collect()
    preds = predict_arm_t(
        state,
        Xseq,
        gap,
        test_idx,
        d_model=args.d_model,
        nhead=args.nhead,
        nlayers=args.nlayers,
        device=args.device,
    )
    level = str(getattr(args, "head", "") or "") in ("oo", "tc")
    m = _eval_pack(preds.tolist(), te_y, te_metas, level=level)
    m["seed"] = seed
    m["elapsed_sec"] = round(time.time() - t0, 2)
    m["n_params"] = report.get("n_params")
    m["holdout_by_epoch"] = list(report.get("holdout_by_epoch") or [])
    print(
        f"  ArmT seed={seed} {_arm_done_bits(m)} "
        f"{m['elapsed_sec']}s rss={_rss_mb()}",
        flush=True,
    )
    span = _eval_span([row["date"] for row in te_metas])
    weight_meta: Dict[str, Any] = {
        "head": head_id,
        "factor_pack": _factor_pack(head_id, args),
        "factor_names": [],
        "d_seq_in": int(Xseq.shape[-1]),
        "d_model": int(args.d_model),
        "nhead": int(args.nhead),
        "nlayers": int(args.nlayers),
        "dropout_infer": 0.0,
        "loss": str(getattr(args, "loss", "") or ""),
        "seed": int(seed),
        "position_encoding": "sin",
        "seq_clip": report.get("seq_clip"),
        "seq_cs_z": bool(seq_cs_z),
        "seq_through_decision": True,
        "z_clip": z_clip,
        **span,
    }
    if report.get("lambdarank_truncation") is not None:
        weight_meta["lambdarank_truncation"] = int(report["lambdarank_truncation"])
    _try_save_transformer(state, weight_meta)
    return {
        "arm": "T_transformer",
        "success": True,
        "n_features_factor": n_features_factor,
        "note": "sequence-only; open-cube CS-z on the sequence",
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
    """完成行。回归头在公共指标后写 mse。"""
    ndcg = ""
    if m.get("cs_ndcg_30") is not None:
        ndcg = f" ndcg30={_fmt_metric(m.get('cs_ndcg_30'))}"
    level = ""
    if m.get("mse") is not None:
        level = f" mse={_fmt_metric(m.get('mse'))}"
    return (
        f"rankIC={_fmt_metric(m.get('cs_rank_ic'))} "
        f"ICIR={_fmt_metric(m.get('cs_icir'))} "
        f"rankICIR={_fmt_metric(m.get('cs_rank_icir'))} "
        f"sign_hit={_fmt_metric(m.get('sign_hit'))} "
        f"topY={_fmt_metric(m.get('cs_top_mean_y'))}"
        f"{level}"
        f"{ndcg}"
    )


def _metric_row(tag: str, pack: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "arm": tag,
        "cs_rank_ic": pack.get("cs_rank_ic"),
        "cs_rank_icir": pack.get("cs_rank_icir"),
        "cs_ic": pack.get("cs_ic"),
        "cs_icir": pack.get("cs_icir"),
        "sign_hit": pack.get("sign_hit"),
        "cs_ndcg_30": pack.get("cs_ndcg_30"),
        "cs_top_mean_y": pack.get("cs_top_mean_y"),
        "mse": pack.get("mse"),
        "n": pack.get("n"),
        "success": pack.get("success"),
        "error": pack.get("error"),
    }


def _relative_hard_gates(
    summary: Dict[str, Any],
    gate_fn,
    *,
    factory: str,
    formal: bool = True,
) -> Optional[Dict[str, Any]]:
    """LT 双臂才写相对硬闸。单臂工厂返回 None。非正式对照不写 pass=true。"""
    if factory != "LT":
        return None
    if not formal:
        return {
            "T": {
                "pass": False,
                "applicable": False,
                "lift": None,
                "reasons": ["非正式对照：需要全量因子、采样 100%、共享评估窗"],
            }
        }
    base = summary.get("arm_L") if (summary.get("arm_L") or {}).get("success") else None
    pack_t = summary.get("arm_T") if isinstance(summary.get("arm_T"), dict) else {}
    if base and pack_t.get("success"):
        return {"T": gate_fn(base, pack_t)}
    if not base and pack_t.get("success"):
        return {
            "T": {
                "pass": False,
                "applicable": True,
                "lift": None,
                "reasons": ["baseline missing: Arm·L 失败或无指标"],
            }
        }
    if base and not pack_t.get("success"):
        return {
            "T": {
                "pass": False,
                "applicable": True,
                "lift": None,
                "reasons": [
                    f"Arm·T 失败：{pack_t.get('error') or '无指标'}"
                    if pack_t
                    else "Arm·T 未产出"
                ],
            }
        }
    return {
        "T": {
            "pass": False,
            "applicable": True,
            "lift": None,
            "reasons": ["Arm·L 与 Arm·T 均无成功指标"],
        }
    }


def clip_train_to_open_cube(
    train_idx: Sequence[int],
    dates: Sequence[str],
    *,
    train_start: Optional[str],
    eval_start: Optional[str],
    cube_start: str,
) -> Tuple[List[int], Optional[str], int]:
    """训练窗起点早于 open 立方时收到立方日，并丢掉更早的训练行。

    立方起点落在 Holdout（``eval_start``）当天或之后时不裁，留给截面标准化报缺日。
    返回 ``(train_idx, train_start, n_drop)``。
    """
    cube = str(cube_start or "")[:10]
    start = str(train_start or "")[:10]
    ev = str(eval_start or "")[:10]
    idx = [int(i) for i in train_idx]
    if len(cube) < 10 or (ev and cube >= ev):
        return idx, train_start, 0
    kept = [
        i
        for i in idx
        if 0 <= i < len(dates) and str(dates[i])[:10] >= cube
    ]
    n_drop = len(idx) - len(kept)
    if start and start >= cube:
        return kept, train_start, n_drop
    return kept, cube, n_drop


def arml_fit_calendar(bar_dates: Sequence[str], lookback: Optional[int]) -> List[str]:
    """与枢纽 ``clip_calendar_lookback`` 同口径。

    拉日线会在 lookback 前垫特征根。这些日子可以留在因子面板里，
    不进 Arm·L 训练集。缺省或非正 lookback 时不裁。
    """
    from core.research.holdout import clip_calendar_lookback

    return clip_calendar_lookback(list(bar_dates or []), lookback)


def arml_keep_train_day(
    day: str,
    *,
    test_days: Sequence[str],
    embargo_days: Sequence[str],
    eval_start: Optional[str],
    train_start: Optional[str],
) -> bool:
    """共享日历锁定 eval 窗时，训练日还须落在 lookback 窗内。

    序列尚未覆盖、但已在训练窗内的早期因子日仍保留。
    早于 ``train_start`` 的垫根丢掉。
    """
    ds = str(day or "")[:10]
    if len(ds) < 10:
        return False
    if ds in test_days or ds in embargo_days:
        return False
    ev = str(eval_start or "")[:10]
    if ev and ds >= ev:
        return False
    start = str(train_start or "")[:10]
    if start and ds < start:
        return False
    return True


def label_horizon_days_for_head(head_id: str, horizon_days: int) -> int:
    """τc 标签当天收盘实现，隔离 0 日；隔夜头用 horizon（至少 1）。

    ``--horizon-days`` 默认 1 是给 ŷ_oo 的。τc / τc_rank 若照搬，
    会把评测窗前一个交易日空出训练集。
    """
    hid = str(head_id or "").strip().lower()
    if hid in ("tc", "tc_rank"):
        return 0
    try:
        return max(1, int(horizon_days or 1))
    except (TypeError, ValueError):
        return 1


def _desk_mark_failed(head: str, error: str, *, factory: str = "T") -> None:
    try:
        from core.research.yhat_tf_desk import factory_scope, mark_fit_failed

        with factory_scope(factory):
            mark_fit_failed(head, error, factory=factory)
        print(f"desk 已标记失败 head={head} factory={factory}: {error}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"desk 标记失败异常: {exc}", flush=True)


def _run_pretrain_then_finetune(args: argparse.Namespace) -> int:
    """大池预训练归档后，同一进程接着在沪深 300 上微调。"""
    original = str(os.environ.get("YHAT_FACTORY_RUN_ID") or "")
    pre_id = f"{original}-pre" if original else ""
    head_id = str(args.head or "oo").strip() or "oo"
    fac = str(getattr(args, "factory", "T") or "T")
    if pre_id:
        os.environ["YHAT_FACTORY_RUN_ID"] = pre_id
    os.environ["QUANTLAB_FEATURE_SET"] = "pool"
    args.update_desk = False
    args.finetune = False
    print("==== 大池预训练 ====", flush=True)
    try:
        code = _run_holdout(args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if original:
            os.environ["YHAT_FACTORY_RUN_ID"] = original
        _desk_mark_failed(head_id, "大池预训练未完成，未开始沪深300微调", factory=fac)
        return int(code) if isinstance(code, int) else 1
    if code != 0:
        if original:
            os.environ["YHAT_FACTORY_RUN_ID"] = original
        _desk_mark_failed(head_id, "大池预训练未完成，未开始沪深300微调", factory=fac)
        return int(code)
    sealed = False
    try:
        from core.research.yhat_tf_desk import factory_scope, seal_chain_pretrain

        summary = getattr(args, "holdout_summary", None)
        with factory_scope("T"):
            sealed = bool(
                seal_chain_pretrain(
                    head_id,
                    run_id=pre_id or original,
                    factory="T",
                    summary=summary if isinstance(summary, dict) else None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        print(f"预训练权重未归档：{exc}", flush=True)
        sealed = False
    if original:
        os.environ["YHAT_FACTORY_RUN_ID"] = original
    if not sealed:
        _desk_mark_failed(head_id, "大池预训练权重未保存，未开始沪深300微调", factory=fac)
        return 1
    from core.universe.feature_sets import codes_for_feature_set

    hs = codes_for_feature_set("hs300", fetch=False)
    if len(hs) < 2:
        _desk_mark_failed(head_id, "沪深300成分未就绪，微调未开始", factory=fac)
        return 2
    os.environ["QUANTLAB_FEATURE_SET"] = "hs300"
    args.codes = ",".join(hs)
    args.watching_limit = len(hs)
    args.finetune = True
    args.chain_finetune = False
    args.update_desk = True
    print("==== 沪深300微调 ====", flush=True)
    return _run_holdout(args)


def main() -> int:
    from core.research.yhat_tf_desk import ARM_T_POOL_LR
    from core.research.yhat_transformer.heads import (
        ARM_T_BATCH,
        ARM_T_BATCH_MAX,
        ARM_T_BATCH_MIN,
        ARM_T_D_MODEL,
        ARM_T_EPOCHS,
        ARM_T_HOLDOUT,
        ARM_T_LOOKBACK,
        ARM_T_NLAYERS,
        ARM_T_SEQ_LEN,
    )

    p = argparse.ArgumentParser(
        description="四头 ŷ_* 工厂 Holdout（--factory L|R|T|LT；默认 head=oo；研究轨）"
    )
    p.add_argument("--codes", default="")
    p.add_argument("--finetune", action="store_true")
    p.add_argument(
        "--chain-finetune",
        action="store_true",
        help="Arm·T：先按 --codes 大池预训练，再在沪深300上微调",
    )
    p.add_argument("--watching-limit", type=int, default=800)
    p.add_argument("--lookback", type=int, default=ARM_T_LOOKBACK)
    p.add_argument("--horizon-days", type=int, default=1)
    p.add_argument("--holdout-trading-days", type=int, default=ARM_T_HOLDOUT)
    p.add_argument(
        "--factory",
        type=str,
        default="T",
        help="L=仅 LightGBM 臂；R=仅 Ridge 臂；T=仅 Transformer 臂；LT=双臂对照（遗留）",
    )
    p.add_argument("--seq-len", type=int, default=ARM_T_SEQ_LEN)
    p.add_argument(
        "--seq-feat-set",
        type=str,
        default="seq_core",
        help="序列特征集 seq_core|seq_full（oo 65/222，开盘 τc 61/218）",
    )
    p.add_argument("--d-model", type=int, default=ARM_T_D_MODEL)
    p.add_argument("--nhead", type=int, default=4)
    p.add_argument("--nlayers", type=int, default=ARM_T_NLAYERS)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--epochs", type=int, default=ARM_T_EPOCHS)
    p.add_argument(
        "--batch-size",
        type=int,
        default=ARM_T_BATCH,
        help=f"Arm·T 训练 batch，范围 {ARM_T_BATCH_MIN}–{ARM_T_BATCH_MAX}",
    )
    p.add_argument(
        "--lr",
        type=float,
        default=ARM_T_POOL_LR,
        help="Arm·T 初始学习率。大池默认 1e-3；沪深300和微调段在开跑前改成 1e-4",
    )
    p.add_argument("--device", default="cpu")
    p.add_argument(
        "--t-sample-frac",
        type=float,
        default=1.0,
        help="Arm·T 相对 Holdout 训练段 subsample 比例 (0,1]；默认 1=不截断",
    )
    p.add_argument(
        "--update-desk",
        action="store_true",
        default=True,
        help="回写 live/yhat_{l|t}/{head}_last.json metrics（默认开）",
    )
    p.add_argument(
        "--no-update-desk",
        action="store_false",
        dest="update_desk",
        help="不回写模型中心 last 槽",
    )
    p.add_argument(
        "--ridge-alpha",
        type=float,
        default=1.0,
        help="Arm·R 岭回归 λ；截距不惩罚；范围 [0, 100]",
    )
    p.add_argument(
        "--head",
        type=str,
        default="oo",
        help="oo|tc|oo_rank|tc_rank；Arm·T 回归/排序默认再融 ListNet（见 train.py）",
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
    fac = str(getattr(args, "factory", "T") or "T")
    try:
        if bool(getattr(args, "chain_finetune", False)) and not bool(
            getattr(args, "finetune", False)
        ):
            return _run_pretrain_then_finetune(args)
        return _run_holdout(args)
    except SystemExit as exc:
        # SIGTERM/SIGINT → SystemExit(128+n)；勿把成功码 0 标成失败
        code = exc.code
        ok_exit = code in (0, None, "0")
        if bool(getattr(args, "update_desk", True)) and not ok_exit:
            if isinstance(code, int) and code >= 128:
                sig = code - 128
                name = {15: "SIGTERM", 2: "SIGINT"}.get(sig, f"signal {sig}")
                msg = f"拟合进程被终止（{name}）；未回写 metrics，请重新拟合"
            else:
                msg = str(code) if code not in (None, "") else "拟合中途退出"
            _desk_mark_failed(head_id, msg, factory=fac)
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        if bool(getattr(args, "update_desk", True)):
            _desk_mark_failed(
                head_id, f"{type(exc).__name__}: {exc}", factory=fac
            )
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


def _factor_panel_build_line(
    head_id: str,
    arms: Sequence[str],
    *,
    full_aoo: bool = False,
    atc_core: bool = False,
) -> str:
    """因子面板先建全量。日志写明谁用全量、Arm·T 裁成哪一包。"""
    hid = str(head_id or "oo").strip().lower() or "oo"
    tc = hid in ("tc", "tc_rank")
    source = "atc_full" if tc else "aoo_full"
    if tc:
        t_pack = "atc_core" if atc_core else "atc_full"
    else:
        t_pack = "aoo_full" if full_aoo else "aoo_core"
    has_l = "L" in arms
    has_r = "R" in arms
    has_t = "T" in arms
    if has_r and not has_l and not has_t:
        note = f"Arm·R 用 {source}"
    elif has_l and has_t:
        if t_pack == source:
            note = f"Arm·L 与 Arm·T 都用 {source}"
        else:
            note = f"Arm·L 用 {source}；Arm·T 开跑前裁成 {t_pack}"
    elif has_l:
        note = f"Arm·L 用 {source}"
    elif t_pack == source:
        note = f"Arm·T 用 {source}"
    else:
        note = f"先建 {source}，Arm·T 开跑前裁成 {t_pack}"
    return f"构建因子面板 head={hid}：{note}…"


def _run_holdout(args: argparse.Namespace) -> int:
    _install_stop_handlers()
    from core.research.yhat_transformer.heads import assert_trainable, get_head

    head_spec = get_head(args.head)
    try:
        assert_trainable(head_spec)
    except NotImplementedError as exc:
        raise SystemExit(str(exc)) from exc
    args.task_type = head_spec.task_type
    factory = str(getattr(args, "factory", "T") or "T").strip().upper()
    if factory in ("L", "ARM-L", "ARM_L", "LGB"):
        factory = "L"
        arms = ["L"]
    elif factory in ("R", "ARM-R", "ARM_R", "RIDGE"):
        factory = "R"
        arms = ["R"]
    elif factory in ("T", "ARM-T", "ARM_T", "TF"):
        factory = "T"
        arms = ["T"]
    elif factory in ("LT", "L+T", "BOTH", "ALL"):
        factory = "LT"
        arms = ["L", "T"]
    else:
        raise SystemExit(f"unknown --factory {factory!r}; expect L|R|T|LT")
    args.factory = factory
    from core.research.yhat_tf_desk import loss_for_factory

    # 日志和 Arm·T 训练都读这一份。L=l2/lambdarank，R=ridge；T 锁名 mse/lambdarank，训练内再融 ListNet。
    args.loss = loss_for_factory(head_spec, "T" if factory == "LT" else factory)
    if "T" in arms:
        from core.research.yhat_tf_desk import resolve_arm_t_lr

        args.lr = resolve_arm_t_lr(
            getattr(args, "lr", None),
            feature_set=os.environ.get("QUANTLAB_FEATURE_SET") or "",
            finetune=bool(getattr(args, "finetune", False)),
        )
    need_fac = True
    need_seq = "T" in arms
    if need_seq:
        from core.research.yhat_transformer.seq_dataset import resolve_seq_feat_set

        args.seq_feat_set = resolve_seq_feat_set(getattr(args, "seq_feat_set", None))
    # CLI 直跑也可能绕过 desk harden：此处再钳一次 nhead|d_model
    from core.research.yhat_transformer.heads import ARM_T_D_MODEL

    dm = max(1, int(getattr(args, "d_model", ARM_T_D_MODEL) or ARM_T_D_MODEL))
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
    package = {"L": "yhat_l", "R": "yhat_r", "T": "yhat_t"}.get(factory, "yhat_transformer")
    print(
        f"head={head_spec.id} ({head_spec.label}) factory={factory} "
        f"package={package} loss={args.loss} live_allowed=False "
        f"tau_end={args.tau_end}",
        flush=True,
    )

    from core.research.holdout import resolve_ridge_split
    from core.research_universe import resolve_model_fit_codes

    explicit = _parse_codes(args.codes)
    resolved = resolve_model_fit_codes(
        watching_limit=args.watching_limit,
        codes=explicit or None,
        file_cap=not explicit,
    )
    codes = list(resolved.get("codes") or [])
    if len(codes) < 2:
        print("标的不足 2 只", flush=True)
        return 2

    frac = getattr(args, "t_sample_frac", None)
    frac_s = f"{float(frac):.4g}" if frac is not None else "—"
    if factory == "R":
        try:
            alpha_s = f"{float(getattr(args, 'ridge_alpha', 1.0) or 1.0):.6g}"
        except (TypeError, ValueError):
            alpha_s = "1"
        print(
            f"臂={arms} 池≤{len(codes)} lookback={args.lookback} "
            f"holdout={args.holdout_trading_days} alpha={alpha_s} "
            f"rss={_rss_mb()}",
            flush=True,
        )
    else:
        print(
            f"臂={arms} 池≤{len(codes)} lookback={args.lookback} L={args.seq_len} "
            f"seq_feat={getattr(args, 'seq_feat_set', 'seq_core')} "
            f"holdout={args.holdout_trading_days} ArmT_sample_frac={frac_s} "
            f"lr={float(args.lr):.6g} rss={_rss_mb()}",
            flush=True,
        )
    stock_bars = _load_bars(codes, lookback=args.lookback)
    if len(stock_bars) < 2:
        print("有效日线不足", flush=True)
        return 2
    print(f"有效日线 {len(stock_bars)} 只 rss={_rss_mb()}", flush=True)

    recorded_args = dict(vars(args))
    if factory == "R":
        for key in (
            "seq_len",
            "seq_feat_set",
            "d_model",
            "nhead",
            "nlayers",
            "dropout",
            "epochs",
            "batch_size",
            "lr",
            "device",
            "t_sample_frac",
        ):
            recorded_args.pop(key, None)
        if head_spec.id in ("oo", "oo_rank"):
            recorded_args["full_aoo"] = True
    summary: Dict[str, Any] = {
        "role": "research_yhat_factory_holdout",
        "package": package,
        "factory": factory,
        "head": head_spec.id,
        "live_allowed": False,
        "arms": arms,
        "codes_n": len(stock_bars),
        "args": recorded_args,
        "rss_mb_start": _rss_mb(),
    }

    seq = None
    fac = None
    from core.research.yhat_transformer.seq_dataset import is_factor_seq

    factor_seq = bool(need_seq and is_factor_seq(getattr(args, "seq_feat_set", None)))
    if need_seq and not factor_seq:
        raise ValueError("价量序列已下线，只用 seq_core / seq_full")
    if need_fac:
        from core.research.yhat_transformer.factor_panel import build_factor_panel

        t0 = time.time()
        # 先建全量：Arm·L 对齐 live。core / full 只在 Arm·T 开跑前裁列。
        print(
            _factor_panel_build_line(
                head_spec.id,
                arms,
                full_aoo=bool(getattr(args, "full_aoo", False)),
                atc_core=bool(getattr(args, "atc_core", False)),
            ),
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

    if factor_seq and fac is not None and int(getattr(fac, "n", 0) or 0) > 0:
        from core.research.yhat_transformer.factor_panel import (
            sequence_from_factor_panel,
            slice_arm_t_panel,
        )
        from core.research.yhat_transformer.heads import ARM_T_SEQ_LEN

        t0 = time.time()
        tc_head = head_spec.id in ("tc", "tc_rank")
        seq_src = fac
        if str(getattr(args, "seq_feat_set", "") or "") == "seq_core":
            seq_src = slice_arm_t_panel(
                fac,
                head_spec,
                aoo_core_only=not tc_head,
                atc_core_only=tc_head,
            )
        seq = sequence_from_factor_panel(
            seq_src, stock_bars, seq_len=int(args.seq_len or ARM_T_SEQ_LEN)
        )
        print(
            f"序列面板 head={head_spec.id} N={seq.n} L={seq.seq_len} "
            f"含决策日 "
            f"F={seq.X.shape[-1]} feat={getattr(args, 'seq_feat_set', 'seq_core')} "
            f"window={getattr(seq.X, 'nbytes', 0)/1e6:.0f}MB "
            f"对齐因子列 {time.time()-t0:.1f}s rss={_rss_mb()}",
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
            label_horizon_days=label_horizon_days_for_head(
                head_spec.id, args.horizon_days
            ),
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
                seq_c, fac_c = _drop_aligned_rows_without_cube(seq_c, fac_c)
                if fac_c.n <= 0:
                    raise ValueError("对齐后没有落在 open 立方起点之后的样本")
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

    # ----- Arm·L / Arm·R：全量因子；OOS 评测缩到共享键 -----
    # 共享日历只锁定 eval 窗。训练仍吃 lookback 内、eval 前的因子日
    # （含序列尚未覆盖的早期日），lookback 之前的特征垫根不进训练。
    # 序列副本和日线在对齐之后不再参与因子臂。先丢掉，避免和 τc 宽因子矩阵叠在一起。
    for arm_tag in ("L", "R"):
        if arm_tag not in arms or fac_full is None or fac_full.n <= 0:
            continue
        import gc
        label = "ArmL" if arm_tag == "L" else "ArmR"

        # 与枢纽 fit_watching_return_model 一致：Holdout 用日线交易日，
        # 再裁到最近 lookback 日。垫根只供特征，不进训练。
        bar_cal = sorted(
            {
                str(bar.get("date") or bar.get("trade_date") or "")[:10]
                for bars in (stock_bars or {}).values()
                for bar in (bars or [])
                if isinstance(bar, dict)
                and len(str(bar.get("date") or bar.get("trade_date") or "")[:10])
                >= 10
            }
        )
        fit_cal = arml_fit_calendar(bar_cal, getattr(args, "lookback", None))
        train_start = fit_cal[0] if fit_cal else None
        stock_bars = None
        if seq is not None and seq is not seq_c:
            seq = None
        gc.collect()
        freed = "释放日线" if arm_tag == "R" else "释放序列/日线"
        print(f"  {label} {freed} rss={_rss_mb()}", flush=True)
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
                elif arml_keep_train_day(
                    ds,
                    test_days=test_days,
                    embargo_days=embargo_days,
                    eval_start=eval_start,
                    train_start=train_start,
                ):
                    tr.append(int(i))
            n_l_test = len(te_all)
        else:
            tr, te, meta = _split_for(
                fac_full.dates,
                calendar_dates=fit_cal or None,
            )
            n_l_test = len(te)
            te_shared = list(te)
        from core.research.materialize.cube_z import first_readable_cube_day

        cube_start, _skipped = first_readable_cube_day("open")
        tr, train_start, n_cube_drop = clip_train_to_open_cube(
            tr,
            fac_full.dates,
            train_start=train_start,
            eval_start=meta.get("eval_start"),
            cube_start=str(cube_start or ""),
        )
        if n_cube_drop:
            print(
                f"  {label} 训练窗起点收到 open 立方 {train_start}，"
                f"丢掉 {n_cube_drop} 行",
                flush=True,
            )
        # shared：LT 双臂 Gate 用交集；full：与枢纽 LGB Holdout 同窗（厂 L 默认）。
        oos_index = "shared" if shared_cal else "full"
        summary[f"split_{arm_tag}"] = {
            "fit_end": meta.get("fit_end"),
            "eval_start": meta.get("eval_start"),
            "train_start": train_start,
            "n_train": len(tr),
            "n_test": len(te_shared),
            "n_test_full": n_l_test,
            "oos_index": oos_index,
        }
        cal_note = (
            " shared_cal+pretrain"
            if shared_cal
            else (" bar_cal" if bar_cal else "")
        )
        print(
            f"{label} 切分 train={len(tr)} test_shared={len(te_shared)}"
            f"（full_holdout={n_l_test}{cal_note} oos={oos_index}"
            f" train_start={train_start or '—'} fit_end={meta.get('fit_end')}"
            f" eval_start={meta.get('eval_start')}）",
            flush=True,
        )
        if not tr and n_cube_drop:
            summary[f"arm_{arm_tag}"] = {
                "success": False,
                "error": f"训练日都早于 open 立方起点 {train_start}",
            }
            print(
                f"  {label} 失败：{summary[f'arm_{arm_tag}']['error']}",
                flush=True,
            )
            continue
        full_sample = _train_despite_empty_holdout(
            len(tr),
            len(te_shared),
            getattr(args, "holdout_trading_days", None),
        )
        if te_shared or full_sample:
            if full_sample:
                print(f"  {label} Holdout=0 全样本训练，无评估窗", flush=True)
            try:
                from core.research.yhat_transformer.factor_panel import (
                    require_factor_panel,
                )

                require_factor_panel(fac_full)
                print(
                    f"  {label} begin F={int(fac_full.X.shape[1])} rss={_rss_mb()}",
                    flush=True,
                )
                if arm_tag == "R":
                    try:
                        ridge_alpha = float(getattr(args, "ridge_alpha", 1.0) or 1.0)
                    except (TypeError, ValueError):
                        ridge_alpha = 1.0
                    summary["arm_R"] = _run_arm_r(
                        fac_full,
                        tr,
                        te_shared,
                        head_id=head_spec.id,
                        rank=(head_spec.task_type == "rank"),
                        ridge_alpha=ridge_alpha,
                    )
                else:
                    summary["arm_L"] = _run_arm_l(
                        fac_full,
                        tr,
                        te_shared,
                        head_id=head_spec.id,
                        rank=(head_spec.task_type == "rank"),
                    )
                a0 = summary[f"arm_{arm_tag}"]
                if a0.get("success"):
                    print(
                        f"  {label} {_arm_done_bits(a0)} {a0.get('elapsed_sec')}s",
                        flush=True,
                    )
                else:
                    print(
                        f"  {label} 失败：{a0.get('error') or '无指标'}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                import traceback

                summary[f"arm_{arm_tag}"] = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"  {label} 失败：{summary[f'arm_{arm_tag}']['error']}", flush=True)
                traceback.print_exc(file=sys.stdout)
                sys.stdout.flush()
        else:
            summary[f"arm_{arm_tag}"] = {
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
        full_sample = _holdout_is_full_sample(
            getattr(args, "holdout_trading_days", None)
        )
        if te or (full_sample and tr):
            if full_sample and not te:
                print("ArmT Holdout=0 全样本训练，无评估窗", flush=True)
            try:
                summary["arm_T"] = _run_arm_t(seq_c, fac_c, tr, te, args)
            except Exception as exc:  # noqa: BLE001
                import traceback

                summary["arm_T"] = {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"  ArmT 失败：{summary['arm_T']['error']}", flush=True)
                traceback.print_exc(file=sys.stdout)
                sys.stdout.flush()
        else:
            summary["arm_T"] = {"success": False, "error": "empty holdout"}

    n_l_test_u = int((summary.get("split_L") or {}).get("n_test_full") or 0)
    n_l_shared = int((summary.get("split_L") or {}).get("n_test") or 0)
    n_r_shared = int((summary.get("split_R") or {}).get("n_test") or 0)
    n_t_test = int((summary.get("split_T") or {}).get("n_test") or 0)
    eval_l = (summary.get("split_L") or {}).get("eval_start")
    eval_r = (summary.get("split_R") or {}).get("eval_start")
    eval_t = (summary.get("split_T") or {}).get("eval_start")
    if factory == "LT":
        # 双臂：须共享键 + 两侧均有 OOS + 同一 eval_start
        eval_shared = (
            bool(shared_keys)
            and n_l_shared > 0
            and n_t_test > 0
            and bool(eval_l)
            and eval_l == eval_t
        )
        eval_start = eval_l if eval_shared else None
    elif factory == "L":
        eval_shared = n_l_shared > 0
        eval_start = eval_l if eval_shared else None
    elif factory == "R":
        eval_shared = n_r_shared > 0
        eval_start = eval_r if eval_shared else None
    else:
        eval_shared = n_t_test > 0
        eval_start = eval_t if eval_shared else None
    summary["eval_universe"] = {
        "n_l_test": n_l_test_u,
        "n_t_test": n_t_test,
        "n_shared": (
            n_l_shared
            if factory == "LT"
            else (n_l_shared or n_r_shared or n_t_test)
        ),
        "eval_shared": eval_shared,
        "eval_start": eval_start,
        "factory": factory,
    }
    # desk 形 parity（与 ingest 一致）
    try:
        from core.research.yhat_tf_desk import compute_parity

        frac_raw = getattr(args, "t_sample_frac", None)
        try:
            frac_v = 1.0 if frac_raw is None else float(frac_raw)
        except (TypeError, ValueError):
            frac_v = 1.0
        if frac_v != frac_v or frac_v <= 0:
            frac_v = 1.0
        desk_params = {
            "full_aoo": True if factory in ("L", "R") and head_spec.id in ("oo", "oo_rank") else bool(getattr(args, "full_aoo", False)),
            "full_atc": True if factory in ("L", "R") and head_spec.id in ("tc", "tc_rank") else not bool(getattr(args, "atc_core", False)),
            "t_sample_pct": 100.0 if factory in ("L", "R") else float(frac_v) * 100.0,
        }
        summary["parity"] = compute_parity(
            head_spec.id, desk_params, eval_shared=eval_shared
        )
    except Exception:  # noqa: BLE001
        frac_raw = getattr(args, "t_sample_frac", None)
        try:
            frac_v = 1.0 if frac_raw is None else float(frac_raw)
        except (TypeError, ValueError):
            frac_v = 1.0
        factor_full = (
            factory in ("L", "R")
            or (
                bool(getattr(args, "full_aoo", False))
                if head_spec.id not in ("tc", "tc_rank")
                else not bool(getattr(args, "atc_core", False))
            )
        )
        sample_full = factory in ("L", "R") or float(frac_v) >= 0.999
        summary["parity"] = {
            "factor_width_match": factor_full,
            "train_sample_full": sample_full,
            "eval_shared": eval_shared,
            "formal": False,
        }

    # 对比表；相对硬闸仅 LT 双臂
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
        ("arm_R", "R_ridge"),
        ("arm_T", "T_transformer"),
    ):
        if key in summary:
            table.append(_metric_row(tag, summary[key]))
    summary["comparison"] = table
    # 单臂工厂无页内相对闸；gates 必须在此赋值，后面的打印不能碰未绑定局部变量
    formal = bool((summary.get("parity") or {}).get("formal"))
    gates = _relative_hard_gates(summary, gate_fn, factory=factory, formal=formal)
    summary["hard_gate_vs_arm_L"] = gates
    summary["gate"] = head_spec.gate if factory == "LT" else None
    summary["rss_mb_end"] = _rss_mb()

    print("\n=== 对比 ===", flush=True)
    print(
        f"{'arm':<12} {'Rank IC':>8} {'RankICIR':>8} {'Pearson':>8} "
        f"{'命中':>8} {'NDCG@30':>8} {'Top10% y':>8}",
        flush=True,
    )
    for r in table:
        if r.get("success") is False:
            print(f"{r['arm']} 失败：{r.get('error') or '无指标'}", flush=True)
            continue
        print(
            f"{r['arm']:<12} "
            f"{(r.get('cs_rank_ic') if r.get('cs_rank_ic') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_rank_icir') if r.get('cs_rank_icir') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_icir') if r.get('cs_icir') is not None else float('nan')):>8.3f} "
            f"{(r.get('sign_hit') if r.get('sign_hit') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_ndcg_30') if r.get('cs_ndcg_30') is not None else float('nan')):>8.3f} "
            f"{(r.get('cs_top_mean_y') if r.get('cs_top_mean_y') is not None else float('nan')):>8.3f}",
            flush=True,
        )
    for k, g in (gates or {}).items():
        if not isinstance(g, dict):
            continue
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
            from core.research.yhat_tf_desk import factory_scope, ingest_holdout_summary

            with factory_scope(factory):
                desk_out = ingest_holdout_summary(
                    head_spec.id, summary, out_path=out, factory=factory
                )
            if desk_out.get("success"):
                print(
                    f"desk last 已回写 head={head_spec.id} metrics={desk_out.get('metrics')}",
                    flush=True,
                )
            elif desk_out.get("skipped"):
                print(f"desk 回写跳过: {desk_out.get('error')}", flush=True)
            else:
                last = desk_out.get("last") if isinstance(desk_out.get("last"), dict) else {}
                print(
                    f"desk 已回写失败态: {desk_out.get('error') or last.get('message') or '无可用指标'}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"desk 回写异常: {exc}", flush=True)
    args.holdout_summary = summary
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
