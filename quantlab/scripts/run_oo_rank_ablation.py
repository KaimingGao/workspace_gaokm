"""ŷ_oo_rank 单段 raw holdout 验证（单头 LambdaRank）。

一次建面板，再拟合 LambdaRank。

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

from core.research.oo_rank_lambdarank import compare_oo_rank_shadow_track
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research.rank_day_panels import build_rank_day_panels
from core.watching.store import read_watching


def _log(msg: str) -> None:
    print(msg, flush=True)


def _gate_row(rank_m: Dict[str, Any]) -> Dict[str, Any]:
    nd = rank_m.get("ndcg_at_k")
    gl = rank_m.get("gate_lift")
    topk = rank_m.get("topk_mean_y_oo")
    checks = {
        "ndcg_ge_055": nd is not None and float(nd) >= 0.55,
        "gate_lift_positive": gl is not None and float(gl) > 0,
        "topk_finite": topk is not None,
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

    loaded, _fail, _fund = load_portfolio_stock_bars(
        pool[:limit],
        lookback=lookback,
        fetch_fundamentals=False,
        offline_ok=True,
    )
    stock_bars: List[Dict[str, Any]] = []
    for code in pool[:limit]:
        bars = loaded.get(str(code)) or []
        if len(bars) < 20:
            continue
        stock_bars.append({"code": code, "bars": bars})

    _log(f"有效加载: {len(stock_bars)}")
    if len(stock_bars) < 8:
        _log("ERROR: 有效股票不足 8 只")
        sys.exit(1)

    _log("建 raw 日面板（仅一次）...")
    base_days = build_rank_day_panels(
        stock_bars,
        family="oo_rank",
        horizon_days=1,
        min_history=12,
        min_names=8,
    )
    _log(f"截面日: {len(base_days)}")
    if len(base_days) < 8:
        _log("ERROR: 截面日不足")
        sys.exit(1)

    _log("\n>>> LambdaRank")
    track = compare_oo_rank_shadow_track(
        stock_bars,
        holdout_trading_days=20,
        topk_track=10,
        ndcg_k=10,
        day_panels=base_days,
    )
    rows: List[Dict[str, Any]] = []
    if not track.get("success"):
        rows.append({"success": False, "error": track.get("error")})
        _log(f"  FAIL: {track.get('error')}")
    else:
        oos = track.get("oos") or {}
        rank_m = oos.get("oo_rank") or {}
        gate = _gate_row(rank_m)
        row = {
            "success": True,
            "n_days": track.get("n_days"),
            "n_train_days": track.get("n_train_days"),
            "n_test_days": track.get("n_test_days"),
            "n_features": track.get("n_features"),
            "rank": {
                "gate_lift": rank_m.get("gate_lift"),
                "gate_mean_y": rank_m.get("gate_mean_y"),
                "spearman": rank_m.get("spearman"),
                "ndcg_at_k": rank_m.get("ndcg_at_k"),
                "topk_overlap": rank_m.get("topk_overlap"),
                "topk_mean_y_oo": rank_m.get("topk_mean_y_oo"),
            },
            "gate": gate,
        }
        rows.append(row)
        _log(
            f"  gate_lift={rank_m.get('gate_lift')} "
            f"ndcg={rank_m.get('ndcg_at_k')} pass={gate.get('pass')}"
        )

    out = {
        "success": True,
        "task": "oo_rank_ablation_matrix",
        "n_stocks": len(stock_bars),
        "lookback": lookback,
        "holdout_trading_days": 20,
        "ndcg_k": 10,
        "n_panel_days": len(base_days),
        "rows": rows,
        "any_gate_pass": any(
            (r.get("gate") or {}).get("pass") for r in rows if r.get("success")
        ),
        "note": (
            "单段 holdout · 单头 LambdaRank；正式推进需 ≥2 段互不重叠 holdout 均过闸。"
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
