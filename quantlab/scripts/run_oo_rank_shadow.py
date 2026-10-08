"""跑一轮 oo_rank LambdaRank OOS：LambdaRank vs Ridge ŷ_oo。

特征与 ŷ_oo 同口径：原始 sub_score。
标签: excess_mode=index（扣指数后的超额收益）

用法: python scripts/run_oo_rank_shadow.py
"""

from __future__ import annotations

import json
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.data.facade import bars_and_source, index_bars_and_source
from core.ports.market import default_benchmark
from core.watching.store import read_watching
from core.research.oo_rank_lambdarank import compare_oo_rank_shadow_track
from core.research.oo_rank_panel import build_oo_rank_day_panels


def load_pool(lookback: int = 250):
    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    print(f"观察池: {len(pool)} 只")

    stock_bars = []
    skipped = 0
    for i, code in enumerate(pool):
        bars, _ = bars_and_source(code, limit=lookback + 40)
        if not bars or len(bars) < 20:
            skipped += 1
            continue
        stock_bars.append({"code": code, "bars": bars})
        if (i + 1) % 40 == 0:
            print(f"  已加载 {i+1}/{len(pool)} ...")

    print(f"有效加载: {len(stock_bars)} 只, 跳过: {skipped} 只")
    return stock_bars


def load_index(lookback: int = 250):
    try:
        idx_code = default_benchmark("CN")
        idx_bars, _ = index_bars_and_source(idx_code, limit=lookback + 40)
        print(f"指数基准: {idx_code}, bars={len(idx_bars or [])}")
        return idx_bars
    except Exception as e:
        print(f"指数加载失败: {e}，将使用 excess_mode=none")
        return None


def run_one(stock_bars, index_bars, day_panels, excess_mode: str = "index"):
    print(f"\n{'=' * 70}")
    print(f"  excess_mode={excess_mode}")
    print(f"{'=' * 70}")

    track = compare_oo_rank_shadow_track(
        stock_bars,
        holdout_trading_days=20,
        topk_track=10,
        ndcg_k=10,
        l2=1.0,
        index_bars=index_bars,
        excess_mode=excess_mode,
        day_panels=day_panels,
    )

    if not track.get("success"):
        print(f"  FAILED: {track.get('error')}")
        if track.get("detail"):
            print(f"  detail: {track.get('detail')}")
        return None

    oos = track.get("oos") or {}
    rank_m = oos.get("oo_rank") or {}
    ridge_m = oos.get("ridge_oo_baseline") or {}

    def _f(val, fmt="+", w=4):
        if val is None:
            return "  N/A  "
        try:
            return f"{float(val):{fmt}.{w}f}"
        except (TypeError, ValueError):
            return "  N/A  "

    print(f"  训练天数: {track.get('n_train_days')}, 验证天数: {track.get('n_test_days')}")
    print()
    print(f"  {'':28s}  {'LambdaRank':>10s}  {'Ridge':>10s}  {'Δ':>10s}")
    print(f"  {'Spearman IC':28s}  {_f(rank_m.get('spearman')):>10s}  {_f(ridge_m.get('spearman')):>10s}  {_f(track.get('delta_spearman')):>10s}")
    print(f"  {'NDCG@10':28s}  {_f(rank_m.get('ndcg_at_k'), fmt='', w=4):>10s}  {_f(ridge_m.get('ndcg_at_k'), fmt='', w=4):>10s}  {_f(track.get('delta_ndcg_at_k'), fmt='', w=4):>10s}")
    print(f"  {'Top-10 Overlap':28s}  {_f(rank_m.get('topk_overlap'), fmt='', w=4):>10s}  {_f(ridge_m.get('topk_overlap'), fmt='', w=4):>10s}  {_f(track.get('delta_topk_overlap'), fmt='', w=4):>10s}")
    print(f"  {'Top-10 均值收益(%)':28s}  {_f(rank_m.get('topk_mean_y_oo')):>9s}%  {_f(ridge_m.get('topk_mean_y_oo')):>9s}%  {_f(track.get('delta_topk_mean_y_oo')):>9s}%")

    return track


def main():
    lookback = 250
    stock_bars = load_pool(lookback)
    if len(stock_bars) < 8:
        print("ERROR: 有效股票不足 8 只")
        return

    index_bars = load_index(lookback)
    excess_mode = "index" if index_bars else "none"

    print("\n构建日截面面板（raw，一次复用）...")
    day_panels = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=12,
        min_names=8,
        index_bars=index_bars,
        excess_mode=excess_mode,
    )
    print(f"  面板天数: {len(day_panels)}")
    if day_panels:
        names = list(day_panels[0].get("names") or [])
        n_feats = len(names)
        print(f"  每票特征数(raw): {n_feats}")

    track = run_one(stock_bars, index_bars, day_panels, excess_mode)

    if track is None:
        return

    # 汇总对比
    print(f"\n{'=' * 70}")
    print("  汇总: LambdaRank Top-10 均值收益(%)")
    print(f"{'=' * 70}")
    oos = track.get("oos") or {}
    rank_m = oos.get("oo_rank") or {}
    ridge_m = oos.get("ridge_oo_baseline") or {}
    rv = rank_m.get("topk_mean_y_oo")
    iv = ridge_m.get("topk_mean_y_oo")
    if rv is not None and iv is not None:
        print(f"  LambdaRank={rv:+.4f}%  Ridge={iv:+.4f}%  Δ={(rv-iv):+.4f}%")
    else:
        print("  N/A")

    # 保存
    out_path = os.path.join(ROOT, "data", "oo_rank_shadow_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(track, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  结果已保存: {out_path}")


if __name__ == "__main__":
    main()
