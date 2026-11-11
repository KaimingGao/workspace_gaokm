"""离线 WF：ŷ_oo>0.2 且 A 档 oo_rank=1 的命中率（不入库）。

研究枢纽「滚动验证」走 ``core.research.roll_gate``（观察池∩A 档 · LB500 · HO10×3 ·
ŷ>0.5% ∧ rank 相对分>0.5 · top_n=10 等权，oo/τc 两臂）。本脚本是另一套离线实验：
训练仍全池、只评 A 档、LB900、10 折、仍取 rank=1，勿混用。

设定：
  - 训练：全观察池；lookback=900 交易日（定长滚动）
  - 预测/选票：仅 A 档；条件 ŷ_oo>0.2（百分点）且 A 内 LambdaRank 名次=1
  - holdout=10 日 × 连续 10 折（共 100 OOS 日）
  - ŷ_oo = Ridge+日截面 z；oo_rank = LambdaRank（非 Ridge 代理）
  - embargo=1（h=1）；不写模型、不入库

用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_oo_rank1_gate_wf_offline.py
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_full_pool(lookback: int) -> List[dict]:
    from core.data.facade import get_bars_batch
    from core.watching.store import read_watching

    uni = read_watching()
    watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    print(f"拉日线 全池={len(watch)} lookback={lookback}", flush=True)
    packs = get_bars_batch(watch, limit=int(lookback))
    out: List[dict] = []
    skip = 0
    for pack in packs:
        if not isinstance(pack, dict):
            skip += 1
            continue
        code = str(pack.get("stock_code") or pack.get("code") or "").strip()
        bars = list(pack.get("bars") or [])
        if not code or len(bars) < 40:
            skip += 1
            continue
        out.append({"code": code, "bars": bars})
    print(f"有效 {len(out)} 只，跳过 {skip}", flush=True)
    return out


def _train_cutoff(uniq: Sequence[str], eval_start: str, *, embargo: int = 1) -> str:
    days = [str(d)[:10] for d in uniq if len(str(d)[:10]) >= 10]
    i = days.index(eval_start)
    cut = i - max(0, int(embargo))
    if cut <= 0:
        return ""
    return days[cut - 1]


def _rolling_train_days(
    uniq: Sequence[str], fit_end: str, lookback: int
) -> Set[str]:
    days = [str(d)[:10] for d in uniq if len(str(d)[:10]) >= 10]
    if fit_end not in days:
        return set()
    j = days.index(fit_end)
    start = max(0, j - int(lookback) + 1)
    return set(days[start : j + 1])


def _fit_oo_ridge(
    days: Sequence[dict],
    train_dates: Set[str],
    *,
    ridge_lambda: float,
    fit_end: str,
) -> Optional[Any]:
    from core.research.oo_model_compact import fit_oo_ridge_matrix

    use = [d for d in days if str(d.get("date") or "")[:10] in train_dates]
    if len(use) < 20:
        return None
    xs, ys, dates, names = [], [], [], None
    for day in use:
        x = day.get("X")
        if not isinstance(x, np.ndarray) or x.ndim != 2:
            continue
        n = int(x.shape[0])
        nm = [str(n_) for n_ in (day.get("names") or [])]
        yrow = list(day.get("ys") or [])
        date = str(day.get("date") or "")[:10]
        if n == 0 or len(nm) != x.shape[1] or len(yrow) != n:
            continue
        if names is None:
            names = nm
        elif nm != names:
            continue
        xs.append(np.asarray(x, dtype=np.float64))
        for y in yrow:
            try:
                ys.append(float(y))
            except (TypeError, ValueError):
                ys.append(float("nan"))
        dates.extend([date] * n)
    if not xs or not names:
        return None
    x = np.vstack(xs)
    y = np.asarray(ys, dtype=np.float64)
    model, report = fit_oo_ridge_matrix(
        x,
        y,
        names,
        ridge_lambda=float(ridge_lambda),
        horizon_days=1,
        min_samples=80,
        fitted_as_of=fit_end,
        row_dates=dates,
        cross_section_zscore=True,
    )
    if model is None or not (isinstance(report, dict) and report.get("success")):
        return None
    return model


def _fit_oo_rank_lambda(
    days: Sequence[dict],
    train_dates: Set[str],
) -> Optional[dict]:
    from core.research.oo_rank_lambdarank import _collect_feature_names, fit_lambdarank

    use = [d for d in days if str(d.get("date") or "")[:10] in train_dates]
    if len(use) < 20:
        return None
    names = _collect_feature_names(use)
    if not names:
        return None
    fit = fit_lambdarank(
        use,
        feature_names=names,
        cross_section_zscore=True,
        train_codes=None,  # 全池损失
    )
    if not isinstance(fit, dict) or not fit.get("success"):
        return None
    return fit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=900)
    ap.add_argument("--holdout", type=int, default=10)
    ap.add_argument("--folds", type=int, default=10)
    ap.add_argument("--y-oo-min", type=float, default=0.2, help="ŷ_oo 门槛（百分点）")
    ap.add_argument("--ridge-lambda", type=float, default=1.0)
    ap.add_argument(
        "--panel-lookback",
        type=int,
        default=0,
        help="拉日线条数；0=lookback+holdout*folds+40",
    )
    args = ap.parse_args()

    mean_m = _load(
        os.path.join(ROOT, "scripts", "run_fuse_a_mean_offline.py"),
        "fuse_a_mean_offline",
    )
    a_codes, a_meta = mean_m._load_a_codes()
    print(f"A 档 {a_meta.get('n_a')} as_of={a_meta.get('as_of')}", flush=True)

    need = int(args.lookback) + int(args.holdout) * int(args.folds) + 40
    panel_lb = int(args.panel_lookback) or need
    stock_bars = _load_full_pool(panel_lb)
    if len(stock_bars) < 80:
        print("全池有效标的不足", flush=True)
        return 1

    from core.research.oo_rank_lambdarank import _predict_oo_rank_matrix
    from core.research.oo_model_compact import predict_oo_matrix
    from core.research.rank_day_panels import build_rank_day_panels

    t0 = time.time()
    print("建 oo 日面板（全池）…", flush=True)
    oo_days = build_rank_day_panels(
        stock_bars, family="oo_rank", horizon_days=1, excess_mode="none"
    )
    print(f"oo_days={len(oo_days)} 耗时 {time.time()-t0:.1f}s", flush=True)
    if len(oo_days) < int(args.lookback) + 20:
        print(
            f"面板日数不足 {len(oo_days)} < lookback+20={int(args.lookback)+20}",
            flush=True,
        )
        return 1

    uniq = [str(d.get("date") or "")[:10] for d in oo_days]
    uniq = [d for d in uniq if len(d) >= 10]
    # 去重保序
    seen = set()
    cal: List[str] = []
    for d in uniq:
        if d in seen:
            continue
        seen.add(d)
        cal.append(d)
    hold_n = max(1, int(args.holdout))
    folds = max(1, int(args.folds))
    need_test = hold_n * folds
    if len(cal) < int(args.lookback) + need_test + 2:
        print(
            f"日历不够 cal={len(cal)} need≈{int(args.lookback)+need_test+2}",
            flush=True,
        )
        return 1
    test_days = cal[-need_test:]
    print(
        f"OOS {test_days[0]}→{test_days[-1]} n={len(test_days)} "
        f"lookback={args.lookback} holdout={hold_n}×{folds} "
        f"gate ŷ_oo>{args.y_oo_min} & A-rank=1 LambdaRank",
        flush=True,
    )

    day_by = {str(d.get("date") or "")[:10]: d for d in oo_days}
    y_min = float(args.y_oo_min)

    def _print_day_row(r: dict) -> None:
        if r.get("empty"):
            y_oo_s = f"{float(r['y_oo']):+.3f}" if "y_oo" in r else "—"
            print(
                f"{r['date']:<12} {r['fold']:>4} {'—':>4} "
                f"{(r.get('code') or '—'):<8} "
                f"{y_oo_s:>8} {'—':>8} {r.get('reason') or 'empty'}",
                flush=True,
            )
            return
        print(
            f"{r['date']:<12} {r['fold']:>4} "
            f"{'Y' if r['hit'] else 'N':>4} "
            f"{r['code']:<8} {r['y_oo']:>+8.3f} {r['label']:>+8.3f}",
            flush=True,
        )

    def _fold_stats(fold_rows: Sequence[dict]) -> Dict[str, Any]:
        active = [r for r in fold_rows if not r.get("empty")]
        empty = len(fold_rows) - len(active)
        if not active:
            return {
                "n": 0,
                "hits": 0,
                "empty": empty,
                "mean_ret": None,
                "hit_pct": None,
            }
        hits = sum(int(r["hit"]) for r in active)
        n = len(active)
        mean_ret = sum(float(r["label"]) for r in active) / n
        return {
            "n": n,
            "hits": hits,
            "empty": empty,
            "mean_ret": mean_ret,
            "hit_pct": 100.0 * hits / n,
        }

    rows_out: List[dict] = []
    fold_summaries: List[dict] = []
    n_refit = 0
    hdr = (
        f"{'date':<12} {'fold':>4} {'hit':>4} {'code':<8} "
        f"{'y_oo':>8} {'label':>8} note"
    )

    for fi in range(folds):
        seg = test_days[fi * hold_n : (fi + 1) * hold_n]
        if not seg:
            continue
        eval_start = seg[0]
        try:
            fit_end = _train_cutoff(cal, eval_start, embargo=1)
        except ValueError:
            print(f"fold{fi+1} skip: {eval_start} not in cal", flush=True)
            continue
        if not fit_end:
            print(f"fold{fi+1} skip: train too short", flush=True)
            continue
        train_dates = _rolling_train_days(cal, fit_end, int(args.lookback))
        if len(train_dates) < max(60, int(args.lookback) // 2):
            print(
                f"fold{fi+1} skip: train_dates={len(train_dates)} fit_end={fit_end}",
                flush=True,
            )
            continue

        t1 = time.time()
        print("", flush=True)
        print(
            f"===== fold{fi+1}/{folds} eval={seg[0]}..{seg[-1]} "
            f"fit_end={fit_end} train_days={len(train_dates)} =====",
            flush=True,
        )
        oo_m = _fit_oo_ridge(
            oo_days,
            train_dates,
            ridge_lambda=float(args.ridge_lambda),
            fit_end=fit_end,
        )
        rk_m = _fit_oo_rank_lambda(oo_days, train_dates)
        if oo_m is None or rk_m is None:
            print(
                f"  FAIL oo={oo_m is not None} rank={rk_m is not None}",
                flush=True,
            )
            continue
        n_refit += 1
        print(f"  fitted in {time.time()-t1:.1f}s  scoring…", flush=True)

        fold_rows: List[dict] = []
        for date in seg:
            day = day_by.get(date)
            if day is None:
                fold_rows.append(
                    {
                        "date": date,
                        "fold": fi + 1,
                        "empty": True,
                        "reason": "no_day",
                    }
                )
                continue
            x = day.get("X")
            names = list(day.get("names") or [])
            codes = [str(c) for c in (day.get("codes") or [])]
            ys = list(day.get("ys") or [])
            n = int(x.shape[0]) if isinstance(x, np.ndarray) else 0
            if n == 0 or len(codes) != n or len(ys) != n:
                fold_rows.append(
                    {
                        "date": date,
                        "fold": fi + 1,
                        "empty": True,
                        "reason": "bad_day",
                    }
                )
                continue
            preds = predict_oo_matrix(
                oo_m, x, names, list(range(n)), row_dates=[date] * n
            )
            ranks = _predict_oo_rank_matrix(rk_m, day)
            # A 档内按 LambdaRank 分排序
            a_rows: List[Tuple[str, float, float, float]] = []
            for code, p_oo, p_rk, y in zip(codes, preds, ranks, ys):
                if code not in a_codes:
                    continue
                try:
                    y_oo = float(p_oo)
                    score = float(p_rk)
                    lab = float(y)
                except (TypeError, ValueError):
                    continue
                if y_oo != y_oo or score != score or lab != lab:
                    continue
                a_rows.append((code, y_oo, score, lab))
            if not a_rows:
                fold_rows.append(
                    {
                        "date": date,
                        "fold": fi + 1,
                        "empty": True,
                        "reason": "no_a",
                    }
                )
                continue
            a_rows.sort(key=lambda t: (-t[2], t[0]))
            code, y_oo, score, lab = a_rows[0]
            if y_oo <= y_min:
                fold_rows.append(
                    {
                        "date": date,
                        "fold": fi + 1,
                        "empty": True,
                        "reason": "y_oo_gate",
                        "code": code,
                        "y_oo": y_oo,
                        "rank_score": score,
                    }
                )
                continue
            hit = 1 if lab > 1e-12 else 0
            fold_rows.append(
                {
                    "date": date,
                    "fold": fi + 1,
                    "empty": False,
                    "code": code,
                    "y_oo": y_oo,
                    "rank_score": score,
                    "label": lab,
                    "hit": hit,
                }
            )

        # 本折结果立刻输出
        print(hdr, flush=True)
        print("-" * 64, flush=True)
        for r in fold_rows:
            _print_day_row(r)
        st = _fold_stats(fold_rows)
        fold_summaries.append({"fold": fi + 1, "seg": seg, **st})
        rows_out.extend(fold_rows)
        print("-" * 64, flush=True)
        if st["n"] <= 0:
            print(
                f"[fold{fi+1} 汇总] 有仓 0  空/闸 {st['empty']}/{len(fold_rows)}  "
                f"耗时 {time.time()-t1:.1f}s",
                flush=True,
            )
        else:
            print(
                f"[fold{fi+1} 汇总] 命中 {st['hits']}/{st['n']}="
                f"{st['hit_pct']:.1f}%  空/闸 {st['empty']}/{len(fold_rows)}  "
                f"日均 {st['mean_ret']:+.3f}%  耗时 {time.time()-t1:.1f}s",
                flush=True,
            )

    # 综合
    active = [r for r in rows_out if not r.get("empty")]
    empty = len(rows_out) - len(active)
    print("", flush=True)
    print("=" * 64, flush=True)
    print("综合汇总（不入库）", flush=True)
    print(
        f"  设定: 训全池 lookback={args.lookback} · 预测 A 档 · "
        f"LambdaRank · ŷ_oo>{y_min} & rank_a=1 · "
        f"holdout={hold_n}×{folds}",
        flush=True,
    )
    print(f"  重训折数={n_refit}/{folds}", flush=True)
    for fs in fold_summaries:
        if fs["n"] <= 0:
            print(
                f"  fold{fs['fold']}: 空/闸 {fs['empty']}  "
                f"({fs['seg'][0]}..{fs['seg'][-1]})",
                flush=True,
            )
        else:
            print(
                f"  fold{fs['fold']}: 命中 {fs['hits']}/{fs['n']}="
                f"{fs['hit_pct']:.1f}%  空/闸 {fs['empty']}  "
                f"日均 {fs['mean_ret']:+.3f}%  "
                f"({fs['seg'][0]}..{fs['seg'][-1]})",
                flush=True,
            )
    if not active:
        print(f"  全日有仓命中 —  空/闸 {empty}/{len(rows_out)}", flush=True)
    else:
        hits = sum(int(r["hit"]) for r in active)
        n = len(active)
        mean_ret = sum(float(r["label"]) for r in active) / n
        print(
            f"  全日有仓命中 {hits}/{n}={100.0*hits/n:.1f}%  "
            f"空/闸 {empty}/{len(rows_out)}  日均 {mean_ret:+.3f}%",
            flush=True,
        )
    print(
        "说明：A 内按 LambdaRank 分取 Top1；全池截面 z；未扣费、不入库。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
