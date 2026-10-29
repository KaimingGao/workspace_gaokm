"""离线对照：oo_rank 训练宇宙 pool / tier_a。

一次建面板，两臂同窗拟合 LambdaRank（+ Ridge），Holdout **主评整池**，
另报 A 子集。tier_a = 损失缩到观察池∩A。

用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_oo_rank_a_boost_offline.py

环境变量:
  OO_RANK_ABOOST_LOOKBACK  训练窗交易日（默认 200）
  OO_RANK_ABOOST_LIMIT     观察池截断（0=全池，默认 200）
  OO_RANK_ABOOST_HOLDOUT   Holdout 日（默认 20）
  OO_RANK_ABOOST_WEIGHT    A 权重（默认 2.0）
  OO_RANK_ABOOST_TOPK      TopK / NDCG@K（默认 10）
  OO_RANK_ABOOST_CS_Z      1=日截面 Z（默认），0=全局 μ/σ

结果: data/oo_rank_a_boost_offline.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set

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


def _pick_metrics(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "n_days": m.get("n_days"),
        "n_rows": m.get("n_rows"),
        "spearman": m.get("spearman"),
        "cs_ic": m.get("cs_ic"),
        "cs_icir": m.get("cs_icir"),
        "ndcg_at_k": m.get("ndcg_at_k"),
        "topk_overlap": m.get("topk_overlap"),
        "topk_mean_y_oo": m.get("topk_mean_y_oo"),
    }


def _fit_arm(
    *,
    name: str,
    days_tr: Sequence[Dict[str, Any]],
    days_te: Sequence[Dict[str, Any]],
    feat_names: Sequence[str],
    a_codes: Set[str],
    topk: int,
    ndcg_k: int,
    use_cs: bool,
    train_codes: Optional[Set[str]],
) -> Dict[str, Any]:
    from core.research.oo_rank_lambdarank import (
        _fit_ridge_baseline,
        _oos_day_metrics,
        fit_lambdarank,
    )

    t0 = time.time()
    _log(
        f"  [{name}] fit LambdaRank "
        f"train_codes={len(train_codes) if train_codes else 0}"
    )
    fit = fit_lambdarank(
        days_tr,
        feature_names=feat_names,
        cross_section_zscore=use_cs,
        train_codes=train_codes,
    )
    if not fit.get("success"):
        return {
            "arm": name,
            "success": False,
            "error": fit.get("error") or "lambdarank_failed",
            "detail": fit,
            "elapsed_sec": round(time.time() - t0, 1),
        }
    ridge = _fit_ridge_baseline(
        days_tr,
        feature_names=feat_names,
        cross_section_zscore=use_cs,
        train_codes=train_codes,
    )
    oos_pool = _oos_day_metrics(
        days_te, fit, topk_track=topk, ndcg_k=ndcg_k, train_codes=None
    )
    oos_a = _oos_day_metrics(
        days_te, fit, topk_track=topk, ndcg_k=ndcg_k, train_codes=a_codes
    )
    oos_ridge_pool: Dict[str, Any] = {"success": bool(ridge.get("success"))}
    oos_ridge_a: Dict[str, Any] = {"success": bool(ridge.get("success"))}
    if ridge.get("success"):
        oos_ridge_pool.update(
            _oos_day_metrics(
                days_te, ridge, topk_track=topk, ndcg_k=ndcg_k, train_codes=None
            )
        )
        oos_ridge_a.update(
            _oos_day_metrics(
                days_te, ridge, topk_track=topk, ndcg_k=ndcg_k, train_codes=a_codes
            )
        )
    elapsed = round(time.time() - t0, 1)
    _log(
        f"  [{name}] done {elapsed}s · pool NDCG={_f(oos_pool.get('ndcg_at_k'))} "
        f"RankIC={_f(oos_pool.get('spearman'))} · "
        f"A NDCG={_f(oos_a.get('ndcg_at_k'))}"
    )
    return {
        "arm": name,
        "success": True,
        "elapsed_sec": elapsed,
        "sample_count": fit.get("sample_count"),
        "n_groups": fit.get("n_groups"),
        "lambdarank": {
            "pool": _pick_metrics(oos_pool),
            "tier_a": _pick_metrics(oos_a),
        },
        "ridge": {
            "pool": _pick_metrics(oos_ridge_pool),
            "tier_a": _pick_metrics(oos_ridge_a),
        },
    }


def main() -> None:
    from core.data.facade import bars_and_source
    from core.research.holdout import (
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )
    from core.research.oo_rank_lambdarank import _collect_feature_names
    from core.research.oo_rank_panel import build_oo_rank_day_panels
    from core.research.predictability_tiers import (
        load_predictability_tiers_last,
        tier_code_set,
    )
    from core.watching.store import read_watching

    lookback = int(os.environ.get("OO_RANK_ABOOST_LOOKBACK", "200"))
    limit = int(os.environ.get("OO_RANK_ABOOST_LIMIT", "200"))
    holdout = int(os.environ.get("OO_RANK_ABOOST_HOLDOUT", "20"))
    topk = int(os.environ.get("OO_RANK_ABOOST_TOPK", "10"))
    use_cs = os.environ.get("OO_RANK_ABOOST_CS_Z", "1").strip() not in {
        "0",
        "false",
        "False",
        "no",
    }

    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    tier_rep = load_predictability_tiers_last()
    if not tier_rep or not tier_rep.get("success"):
        _log("ERROR: 无 predictability_tiers_last；请先跑观察池分档")
        sys.exit(1)
    a_codes = set(tier_code_set(tier_rep, ["A"]))
    if len(a_codes) < 8:
        _log(f"ERROR: A 档不足 8（{len(a_codes)}）")
        sys.exit(1)

    if limit > 0:
        pool = pool[:limit]
    inter_a = [c for c in pool if c in a_codes]
    _log(
        f"观察池用 {len(pool)} · A∩池 {len(inter_a)}/{len(a_codes)} · "
        f"lookback={lookback} holdout={holdout} cs_z={int(use_cs)}"
    )
    if len(inter_a) < 8:
        _log("ERROR: 截断后 A∩池 < 8，请加大 LIMIT 或降低截断")
        sys.exit(1)

    stock_bars: List[Dict[str, Any]] = []
    for i, code in enumerate(pool):
        bars, _src = bars_and_source(code, limit=lookback + 40)
        if not bars or len(bars) < 20:
            continue
        stock_bars.append({"code": code, "bars": bars})
        if (i + 1) % 40 == 0:
            _log(f"  已加载 {i + 1}/{len(pool)} ...")
    _log(f"有效加载: {len(stock_bars)}")
    if len(stock_bars) < 8:
        _log("ERROR: 有效股票不足")
        sys.exit(1)

    a_in_bars = {str(r["code"]) for r in stock_bars if str(r["code"]) in a_codes}
    _log(f"A∩有效加载: {len(a_in_bars)}")

    _log("建 oo_rank 日面板（仅一次）...")
    t_panel = time.time()
    days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=12,
        min_names=8,
    )
    _log(f"截面日: {len(days)}（{time.time() - t_panel:.0f}s）")
    if len(days) < 8:
        _log("ERROR: 截面日不足")
        sys.exit(1)

    date_rows = [str(d["date"]) for d in days]
    train_idx, test_idx, split_meta = resolve_ridge_split(
        date_rows,
        holdout_trading_days=holdout,
        label_horizon_days=1,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    days_tr = [days[i] for i in train_idx]
    days_te = [days[i] for i in test_idx]
    feat_names = _collect_feature_names(days_tr)
    _log(
        f"split train={len(days_tr)} test={len(days_te)} features={len(feat_names)} "
        f"meta={split_meta.get('holdout_trading_days')}"
    )

    arms = [
        ("pool", None),
        ("tier_a", a_in_bars),
    ]
    rows: List[Dict[str, Any]] = []
    for name, train_c in arms:
        rows.append(
            _fit_arm(
                name=name,
                days_tr=days_tr,
                days_te=days_te,
                feat_names=feat_names,
                a_codes=a_in_bars,
                topk=topk,
                ndcg_k=topk,
                use_cs=use_cs,
                train_codes=train_c,
            )
        )

    def _print_table(scope: str) -> None:
        _log(f"\n=== Holdout · {scope}（主评={'整池' if scope == 'pool' else 'A 子集'}）===")
        hdr = (
            f"{'arm':10s}  {'NDCG':>8s}  {'RankIC':>8s}  {'IC':>8s}  "
            f"{'ICIR':>8s}  {'TopKov':>8s}  {'TopKy':>8s}"
        )
        _log(hdr)
        for row in rows:
            if not row.get("success"):
                _log(f"{row.get('arm'):10s}  FAIL {row.get('error')}")
                continue
            m = ((row.get("lambdarank") or {}).get(scope) or {})
            _log(
                f"{row['arm']:10s}  {_f(m.get('ndcg_at_k')):>8s}  "
                f"{_f(m.get('spearman')):>8s}  {_f(m.get('cs_ic')):>8s}  "
                f"{_f(m.get('cs_icir')):>8s}  {_f(m.get('topk_overlap')):>8s}  "
                f"{_f(m.get('topk_mean_y_oo')):>8s}"
            )

    _print_table("pool")
    _print_table("tier_a")

    # 相对 pool 的 Δ（LambdaRank · 整池）
    by = {r["arm"]: r for r in rows if r.get("success")}
    base = ((by.get("pool") or {}).get("lambdarank") or {}).get("pool") or {}
    _log("\n=== Δ vs pool（LambdaRank · 整池 Holdout）===")
    for arm in ("tier_a",):
        m = ((by.get(arm) or {}).get("lambdarank") or {}).get("pool") or {}
        if not base or not m:
            continue
        deltas = {}
        for k in ("ndcg_at_k", "spearman", "cs_ic", "cs_icir", "topk_overlap", "topk_mean_y_oo"):
            if base.get(k) is None or m.get(k) is None:
                deltas[k] = None
            else:
                deltas[k] = round(float(m[k]) - float(base[k]), 4)
        _log(
            f"{arm:10s}  ΔNDCG={_f(deltas.get('ndcg_at_k'))}  "
            f"ΔRankIC={_f(deltas.get('spearman'))}  "
            f"ΔTopKy={_f(deltas.get('topk_mean_y_oo'))}"
        )

    out = {
        "success": all(r.get("success") for r in rows),
        "task": "oo_rank_a_boost_offline",
        "n_stocks": len(stock_bars),
        "n_a": len(a_in_bars),
        "lookback": lookback,
        "holdout_trading_days": holdout,
        "topk": topk,
        "ndcg_k": topk,
        "cross_section_zscore": use_cs,
        "n_panel_days": len(days),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "n_features": len(feat_names),
        "split": split_meta,
        "tier_note": (tier_rep or {}).get("note"),
        "rows": rows,
        "note": (
            "三臂同窗；Holdout 主表看整池（训推一致）；"
            "tier_a=损失缩 A；不改推理宇宙。"
        ),
    }
    path = os.path.join(ROOT, "data", "oo_rank_a_boost_offline.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _log(f"\n写入 {path}")
    if not out["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
