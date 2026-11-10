"""离线对照：ŷ_τc_rank LambdaRank 树深/叶数网格。

一次建 tc_rank 日面板、同窗切 Holdout；各臂只改 max_depth/num_leaves，
其余鲁棒默认不动（稠密名次、truncation=100、z_clip=3、min_leaf=50、
L2=5、n_estimators=100）。不写模型、不入库。

默认三臂（depth, leaves 对齐 2^depth）: (4,16) · (5,32) · (6,64)。

用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_tc_rank_depth_offline.py

环境变量:
  TC_RANK_DEPTH_LOOKBACK   默认 1000
  TC_RANK_DEPTH_LIMIT      观察池截断；0=全池（默认 0）
  TC_RANK_DEPTH_HOLDOUT    默认 30
  TC_RANK_DEPTH_TOPK       Gate/NDCG@K（默认 10）
  TC_RANK_DEPTH_TIERS      训练档，逗号分隔；默认 A；空=整池损失
  TC_RANK_DEPTH_TAU_END    前缀终点 09:30…11:00（默认 09:30；与 ŷ_τc 同族）
  TC_RANK_DEPTH_ARMS       如 ``4:16,5:32,6:64``（默认即此）

结果: data/tc_rank_depth_offline.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _f(v: Any, digits: int = 4) -> str:
    if v is None:
        return "   N/A "
    try:
        return f"{float(v):+.{digits}f}"
    except (TypeError, ValueError):
        return "   N/A "


def _pick(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "n_days": m.get("n_days"),
        "n_rows": m.get("n_rows"),
        "gate_lift": m.get("gate_lift"),
        "gate_mean_y": m.get("gate_mean_y"),
        "gate_n": m.get("gate_n"),
        "gate_strong_lift": m.get("gate_strong_lift"),
        "spearman": m.get("spearman"),
        "cs_ic": m.get("cs_ic"),
        "cs_icir": m.get("cs_icir"),
        "ndcg_at_k": m.get("ndcg_at_k"),
        "topk_overlap": m.get("topk_overlap"),
        "topk_mean_y": m.get("topk_mean_y_oo"),
    }


def _delta(a: Dict[str, Any], b: Dict[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in keys:
        if a.get(k) is None or b.get(k) is None:
            out[k] = None
        else:
            out[k] = round(float(b[k]) - float(a[k]), 4)
    return out


def _parse_arms(raw: str) -> List[Tuple[str, int, int]]:
    """``4:16,5:32,6:64`` → [(d4_l16, 4, 16), ...]。"""
    text = str(raw or "").strip() or "4:16,5:32,6:64"
    out: List[Tuple[str, int, int]] = []
    for part in text.split(","):
        bit = part.strip()
        if not bit:
            continue
        if ":" not in bit:
            raise ValueError(f"bad arm={bit!r}；期望 depth:leaves")
        d_s, l_s = bit.split(":", 1)
        depth = int(d_s.strip())
        leaves = int(l_s.strip())
        out.append((f"d{depth}_l{leaves}", depth, leaves))
    if not out:
        raise ValueError("TC_RANK_DEPTH_ARMS empty")
    return out


def _fit_arm(
    *,
    name: str,
    days_tr: Sequence[Dict[str, Any]],
    days_te: Sequence[Dict[str, Any]],
    feat_names: Sequence[str],
    train_codes: Optional[Set[str]],
    eval_codes: Optional[Set[str]],
    topk: int,
    use_cs: bool,
    max_depth: int,
    num_leaves: int,
) -> Dict[str, Any]:
    from core.research.oo_rank_lambdarank import (
        LAMBDARANK_L2,
        LAMBDARANK_MAX_DEPTH,
        LAMBDARANK_MIN_CHILD_SAMPLES,
        LAMBDARANK_N_ESTIMATORS,
        LAMBDARANK_TRUNCATION_LEVEL,
        LAMBDARANK_Z_CLIP,
        _oos_day_metrics,
        fit_lambdarank,
    )

    t0 = time.time()
    _log(
        f"  [{name}] fit depth={max_depth} leaves={num_leaves} "
        f"train_codes={len(train_codes) if train_codes else 'pool'}"
    )
    fit = fit_lambdarank(
        days_tr,
        feature_names=feat_names,
        max_depth=int(max_depth),
        num_leaves=int(num_leaves),
        l2=float(LAMBDARANK_L2),
        n_estimators=int(LAMBDARANK_N_ESTIMATORS),
        min_child_samples=int(LAMBDARANK_MIN_CHILD_SAMPLES),
        truncation_level=int(LAMBDARANK_TRUNCATION_LEVEL),
        z_clip=float(LAMBDARANK_Z_CLIP),
        cross_section_zscore=use_cs,
        train_codes=train_codes,
    )
    if not fit.get("success"):
        return {
            "arm": name,
            "success": False,
            "error": fit.get("error") or "lambdarank_failed",
            "max_depth": max_depth,
            "num_leaves": num_leaves,
            "detail": {k: fit.get(k) for k in ("error", "n_groups", "n_rank_rows_dropped")},
            "elapsed_sec": round(time.time() - t0, 1),
        }

    oos_train_u = _oos_day_metrics(
        days_te, fit, topk_track=topk, ndcg_k=topk, train_codes=train_codes
    )
    oos_pool = _oos_day_metrics(
        days_te, fit, topk_track=topk, ndcg_k=topk, train_codes=None
    )
    oos_eval: Dict[str, Any] = {}
    if eval_codes is not None:
        oos_eval = _oos_day_metrics(
            days_te, fit, topk_track=topk, ndcg_k=topk, train_codes=eval_codes
        )

    elapsed = round(time.time() - t0, 1)
    _log(
        f"  [{name}] done {elapsed}s · "
        f"train_u Gate={_f(oos_train_u.get('gate_lift'))} "
        f"NDCG={_f(oos_train_u.get('ndcg_at_k'))} · "
        f"pool Gate={_f(oos_pool.get('gate_lift'))}"
    )
    return {
        "arm": name,
        "success": True,
        "elapsed_sec": elapsed,
        "max_depth": max_depth,
        "num_leaves": num_leaves,
        "baseline_depth": int(LAMBDARANK_MAX_DEPTH),
        "sample_count": fit.get("sample_count"),
        "n_groups": fit.get("n_groups"),
        "n_dense_labels": fit.get("n_dense_labels"),
        "lambdarank_truncation_level": fit.get("lambdarank_truncation_level"),
        "oos": {
            "train_universe": _pick(oos_train_u),
            "pool": _pick(oos_pool),
            "eval_subset": _pick(oos_eval) if oos_eval else None,
        },
    }


def main() -> None:
    from core.research.holdout import (
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )
    from core.research.oo_rank_lambdarank import _collect_feature_names
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.research.predictability_tiers import (
        load_predictability_tiers_last,
        tier_code_set,
    )
    from core.research.rank_day_panels import build_rank_day_panels
    from core.watching.store import read_watching

    lookback = int(os.environ.get("TC_RANK_DEPTH_LOOKBACK", "1000"))
    limit = int(os.environ.get("TC_RANK_DEPTH_LIMIT", "0"))
    holdout = int(os.environ.get("TC_RANK_DEPTH_HOLDOUT", "30"))
    topk = int(os.environ.get("TC_RANK_DEPTH_TOPK", "10"))
    tiers_raw = os.environ.get("TC_RANK_DEPTH_TIERS", "A").strip()
    use_cs = True
    tau_end_hm = str(os.environ.get("TC_RANK_DEPTH_TAU_END", "09:30") or "09:30").strip()[
        :5
    ]
    try:
        arms_spec = _parse_arms(os.environ.get("TC_RANK_DEPTH_ARMS", "4:16,5:32,6:64"))
    except ValueError as exc:
        _log(f"ERROR: {exc}")
        sys.exit(1)

    from core.signal.dual_score import get_dual_score_cfg
    from core.signal.minute_tau_grid import resolve_tau_train_prefix

    ds = get_dual_score_cfg()
    prefix = resolve_tau_train_prefix(
        tau_end_hm=tau_end_hm,
        enable_minute_tau=bool(ds.get("enable_minute_tau")),
    )
    if prefix.get("error"):
        _log(f"ERROR: 前缀 {prefix['error']}")
        sys.exit(1)
    tau_key = str(prefix.get("tau_hm") or "open")
    tau_grid = prefix.get("tau_grid")
    resolved_end = str(prefix.get("tau_end_hm") or tau_end_hm)[:5]

    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    if limit > 0:
        pool = pool[:limit]

    train_codes: Optional[Set[str]] = None
    eval_codes: Optional[Set[str]] = None
    tier_labels: List[str] = []
    if tiers_raw:
        tier_labels = [t.strip().upper() for t in tiers_raw.split(",") if t.strip()]
        tier_rep = load_predictability_tiers_last()
        if not tier_rep or not tier_rep.get("success"):
            _log("ERROR: 无 predictability_tiers_last；请先跑观察池分档，或 TC_RANK_DEPTH_TIERS=")
            sys.exit(1)
        train_codes = set(tier_code_set(tier_rep, tier_labels))
        eval_codes = set(tier_code_set(tier_rep, ["A"]))
        if len(train_codes) < 8:
            _log(f"ERROR: 训练档 {tier_labels} 不足 8（{len(train_codes)}）")
            sys.exit(1)

    _log(
        f"池={len(pool)} lookback={lookback} holdout={holdout} "
        f"tiers={tier_labels or ['pool']} "
        f"train_codes={len(train_codes) if train_codes else 'pool'} cs_z={int(use_cs)} "
        f"tau_end={resolved_end}"
    )

    loaded, _fail, _fund = load_portfolio_stock_bars(
        pool,
        lookback=lookback,
        fetch_fundamentals=False,
        offline_ok=True,
    )
    stock_bars: List[Dict[str, Any]] = []
    for code in pool:
        bars = loaded.get(str(code)) or []
        if len(bars) < 20:
            continue
        stock_bars.append({"code": code, "bars": bars})
    _log(f"有效加载: {len(stock_bars)}")
    if len(stock_bars) < 8:
        _log("ERROR: 有效股票不足")
        sys.exit(1)

    if train_codes is not None:
        train_codes = {c for c in train_codes if any(r["code"] == c for r in stock_bars)}
        _log(f"训练档∩有效: {len(train_codes)}")
    if eval_codes is not None:
        eval_codes = {c for c in eval_codes if any(r["code"] == c for r in stock_bars)}

    calendar = calendar_dates_from_stock_bars(stock_bars)
    _pre_tr, _pre_te, split_pre = resolve_ridge_split(
        calendar,
        holdout_trading_days=holdout,
        label_horizon_days=0,
        calendar_dates=calendar,
    )
    fit_dates = sorted(split_pre.get("train_days") or [])

    _log(f"建 tc_rank 日面板（仅一次；前缀={resolved_end}）...")
    t_panel = time.time()
    days = build_rank_day_panels(
        stock_bars,
        family="tc_rank",
        fit_dates=fit_dates,
        min_history=12,
        min_names=8,
        tau_hm=tau_key,
        tau_grid=tau_grid,
    )
    _log(f"截面日: {len(days)}（{time.time() - t_panel:.0f}s）")
    if len(days) < 8:
        _log("ERROR: 截面日不足")
        sys.exit(1)

    date_rows = [str(d.get("date") or "")[:10] for d in days]
    train_idx, test_idx, split_meta = resolve_ridge_split(
        date_rows,
        holdout_trading_days=holdout,
        label_horizon_days=0,
        calendar_dates=calendar,
    )
    days_tr = [days[i] for i in train_idx]
    days_te = [days[i] for i in test_idx]
    feat_names = list(days_tr[0].get("names") or []) or _collect_feature_names(days_tr)
    _log(
        f"split train={len(days_tr)} test={len(days_te)} features={len(feat_names)} "
        f"holdout={split_meta.get('holdout_trading_days')}"
    )

    rows: List[Dict[str, Any]] = []
    for name, depth, leaves in arms_spec:
        rows.append(
            _fit_arm(
                name=name,
                days_tr=days_tr,
                days_te=days_te,
                feat_names=feat_names,
                train_codes=train_codes,
                eval_codes=eval_codes,
                topk=topk,
                use_cs=use_cs,
                max_depth=depth,
                num_leaves=leaves,
            )
        )

    keys = (
        "gate_lift",
        "gate_mean_y",
        "gate_strong_lift",
        "ndcg_at_k",
        "topk_mean_y",
        "spearman",
        "cs_ic",
    )
    base_name = arms_spec[0][0]
    by = {r["arm"]: r for r in rows if r.get("success")}
    base = ((by.get(base_name) or {}).get("oos") or {}).get("train_universe") or {}
    deltas_vs_base: Dict[str, Dict[str, Any]] = {}
    for name, _d, _l in arms_spec[1:]:
        cur = ((by.get(name) or {}).get("oos") or {}).get("train_universe") or {}
        if base and cur:
            deltas_vs_base[name] = _delta(base, cur, keys)

    _log(f"\n=== Holdout · 训练宇宙（主评；Δ vs {base_name}）===")
    hdr = (
        f"{'arm':12s}  {'depth':>5s}  {'leaves':>6s}  {'GateL':>8s}  "
        f"{'GateY':>8s}  {'NDCG':>8s}  {'TopKy':>8s}  {'RankIC':>8s}"
    )
    _log(hdr)
    for row in rows:
        if not row.get("success"):
            _log(f"{row.get('arm'):12s}  FAIL {row.get('error')}")
            continue
        m = ((row.get("oos") or {}).get("train_universe") or {})
        _log(
            f"{row['arm']:12s}  {int(row['max_depth']):>5d}  {int(row['num_leaves']):>6d}  "
            f"{_f(m.get('gate_lift')):>8s}  {_f(m.get('gate_mean_y')):>8s}  "
            f"{_f(m.get('ndcg_at_k')):>8s}  {_f(m.get('topk_mean_y')):>8s}  "
            f"{_f(m.get('spearman')):>8s}"
        )
    for name, deltas in deltas_vs_base.items():
        _log(
            f"{'Δ '+name:12s}  {'':>5s}  {'':>6s}  "
            f"{_f(deltas.get('gate_lift')):>8s}  {_f(deltas.get('gate_mean_y')):>8s}  "
            f"{_f(deltas.get('ndcg_at_k')):>8s}  {_f(deltas.get('topk_mean_y')):>8s}  "
            f"{_f(deltas.get('spearman')):>8s}"
        )

    out = {
        "success": all(r.get("success") for r in rows),
        "task": "tc_rank_depth_offline",
        "n_stocks": len(stock_bars),
        "n_train_codes": len(train_codes) if train_codes is not None else None,
        "train_universe_tiers": tier_labels or None,
        "lookback": lookback,
        "holdout_trading_days": holdout,
        "topk": topk,
        "ndcg_k": topk,
        "cross_section_zscore": use_cs,
        "tau_end_hm": resolved_end,
        "tau_hm": tau_key,
        "tau_grid": list(tau_grid) if tau_grid else None,
        "zero_prefix": bool(prefix.get("zero_prefix")),
        "n_panel_days": len(days),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "n_features": len(feat_names),
        "split": split_meta,
        "arms_spec": [{"arm": n, "max_depth": d, "num_leaves": l} for n, d, l in arms_spec],
        "arms": rows,
        "baseline_arm": base_name,
        "delta_vs_baseline_train_universe": deltas_vs_base,
        "note": (
            "同窗同面板；仅改 max_depth/num_leaves。"
            "主评=训练宇宙 Holdout Gate lift；不落盘。"
        ),
    }
    path = os.path.join(ROOT, "data", "tc_rank_depth_offline.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _log(f"\n写入 {path}")
    if not out["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
