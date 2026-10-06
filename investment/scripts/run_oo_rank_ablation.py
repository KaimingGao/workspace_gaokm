"""ŷ_oo_rank 消融矩阵：feature_mode × pair_preset，固定观察池 + holdout=20。

一次建面板，再按 mode/preset 拟合（避免 8 次重扫日线）。

用法: PYTHONUNBUFFERED=1 python scripts/run_oo_rank_ablation.py
结果: data/oo_rank_ablation_matrix.json
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.data.facade import bars_and_source
from core.research.oo_rank_panel import FEATURE_MODES, build_oo_rank_day_panels
from core.research.oo_rank_pairwise import (
    PAIR_PRESETS,
    compare_oo_rank_shadow_track,
)
from core.watching.store import read_watching


def _log(msg: str) -> None:
    print(msg, flush=True)


def _gate_row(rank_m: Dict[str, Any], ridge_m: Dict[str, Any], deltas: Dict[str, Any]) -> Dict[str, Any]:
    pa = rank_m.get("pair_accuracy")
    sp = rank_m.get("spearman")
    sp_r = ridge_m.get("spearman")
    topk = rank_m.get("topk_mean_y_oo")
    topk_r = ridge_m.get("topk_mean_y_oo")
    d_sp = deltas.get("delta_spearman")
    checks = {
        "pair_acc_ge_052": pa is not None and float(pa) >= 0.52,
        "spearman_beats_ridge": (
            sp is not None
            and sp_r is not None
            and float(sp) > float(sp_r)
            and (float(sp) > 0 or (d_sp is not None and float(d_sp) >= 0.02))
        ),
        "topk_not_worse": (
            topk is not None
            and topk_r is not None
            and float(topk) >= float(topk_r) - 1e-9
        ),
    }
    checks["pass"] = all(checks.values())
    return checks


def main() -> None:
    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    lookback = int(os.environ.get("OO_RANK_ABLATION_LOOKBACK", "120"))
    limit = int(os.environ.get("OO_RANK_ABLATION_LIMIT", "60"))
    limit = max(8, min(limit, len(pool)))
    _log(f"观察池: {len(pool)} · 用前 {limit} · lookback={lookback}")

    stock_bars: List[Dict[str, Any]] = []
    for i, code in enumerate(pool[:limit]):
        bars, _src = bars_and_source(code, limit=lookback + 40)
        if not bars or len(bars) < 20:
            continue
        stock_bars.append({"code": code, "bars": bars})
        if (i + 1) % 20 == 0:
            _log(f"  已加载 {i + 1}/{limit} ...")

    _log(f"有效加载: {len(stock_bars)}")
    if len(stock_bars) < 8:
        _log("ERROR: 有效股票不足 8 只")
        sys.exit(1)

    _log("建 raw_cs 日面板（仅一次）...")
    base_days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=12,
        min_names=8,
        feature_mode="raw_cs",
    )
    _log(f"截面日: {len(base_days)}")
    if len(base_days) < 8:
        _log("ERROR: 截面日不足")
        sys.exit(1)

    modes = list(FEATURE_MODES)
    presets = list(PAIR_PRESETS.keys())
    rows: List[Dict[str, Any]] = []

    for mode in modes:
        for preset in presets:
            _log(f"\n>>> feature_mode={mode} pair_preset={preset}")
            track = compare_oo_rank_shadow_track(
                stock_bars,
                holdout_trading_days=20,
                feature_mode=mode,
                pair_preset=preset,
                topk_track=10,
                l2=1.0,
                day_panels=base_days,
            )
            if not track.get("success"):
                rows.append(
                    {
                        "feature_mode": mode,
                        "pair_preset": preset,
                        "success": False,
                        "error": track.get("error"),
                    }
                )
                _log(f"  FAIL: {track.get('error')}")
                continue
            oos = track.get("oos") or {}
            rank_m = oos.get("oo_rank") or {}
            ridge_m = oos.get("ridge_oo_baseline") or {}
            deltas = {
                "delta_spearman": track.get("delta_spearman"),
                "delta_topk_overlap": track.get("delta_topk_overlap"),
                "delta_topk_mean_y_oo": track.get("delta_topk_mean_y_oo"),
            }
            gate = _gate_row(rank_m, ridge_m, deltas)
            row = {
                "feature_mode": mode,
                "pair_preset": preset,
                "success": True,
                "n_days": track.get("n_days"),
                "n_train_days": track.get("n_train_days"),
                "n_test_days": track.get("n_test_days"),
                "feature_meta": track.get("feature_meta"),
                "rank": {
                    "spearman": rank_m.get("spearman"),
                    "pair_accuracy": rank_m.get("pair_accuracy"),
                    "topk_overlap": rank_m.get("topk_overlap"),
                    "topk_mean_y_oo": rank_m.get("topk_mean_y_oo"),
                },
                "ridge": {
                    "spearman": ridge_m.get("spearman"),
                    "pair_accuracy": ridge_m.get("pair_accuracy"),
                    "topk_overlap": ridge_m.get("topk_overlap"),
                    "topk_mean_y_oo": ridge_m.get("topk_mean_y_oo"),
                },
                "delta": deltas,
                "gate": gate,
            }
            rows.append(row)
            _log(
                f"  ρ={rank_m.get('spearman')} pair={rank_m.get('pair_accuracy')} "
                f"Δρ={deltas.get('delta_spearman')} gate={gate.get('pass')}"
            )

    out = {
        "success": True,
        "task": "oo_rank_ablation_matrix",
        "n_stocks": len(stock_bars),
        "lookback": lookback,
        "holdout_trading_days": 20,
        "n_panel_days": len(base_days),
        "rows": rows,
        "any_gate_pass": any(
            (r.get("gate") or {}).get("pass") for r in rows if r.get("success")
        ),
        "note": (
            "单段 holdout；正式推进需 ≥2 段互不重叠 holdout 均过闸。"
            "any_gate_pass=false → 维持影子，不上中性标签/LGBM。"
        ),
    }
    path = os.path.join(ROOT, "data", "oo_rank_ablation_matrix.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _log(f"\n写入 {path}")
    _log(f"any_gate_pass={out['any_gate_pass']}")


if __name__ == "__main__":
    main()
