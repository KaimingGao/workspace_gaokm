"""离线对照 TopkDropout（Qlib）与每日满仓换血。

信号：已启用研究套 y_oo_rank / y_τc_rank，不重训、不写模型、不写回测。
持有收益：决策日 open[T+1]/open[T]−1（y_oo 原始百分点，非超额）。
组合：等权。成本走回测换手费率（佣金 + 过户 + 卖出印花 + 基础滑点），续持不扣。

用法（在 quantlab 目录，用项目 .venv）:
  python scripts/run_topk_dropout_offline.py --signal y_oo
  python scripts/run_topk_dropout_offline.py --signal rank
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

Score = float  # higher is better


def topk_dropout_holdings(
    scores: Dict[str, Score],
    held: Sequence[str],
    *,
    topk: int,
    n_drop: int,
) -> List[str]:
    """Qlib TopkDropoutStrategy（method_buy=top, method_sell=bottom，不滤停牌）。

    当天只把「未持仓里最好的 n_drop + topk - 持仓数」只放进候选，
    再从「持仓 ∪ 候选」的分数尾部卖掉最多 n_drop 只，避免卖掉高分去买更低分。
    当日没有分数的持仓视为离开宇宙，先移出再补满 topk。
    """
    k = max(1, int(topk))
    drop = max(0, int(n_drop))
    universe = {str(c): float(s) for c, s in (scores or {}).items() if c}
    last = [str(c) for c in held if str(c) in universe]
    last_sorted = sorted(last, key=lambda c: (-universe[c], c))
    n_buy_cap = drop + k - len(last_sorted)
    if n_buy_cap < 0:
        n_buy_cap = 0
    unheld = sorted(
        (c for c in universe if c not in set(last_sorted)),
        key=lambda c: (-universe[c], c),
    )
    today = unheld[:n_buy_cap]
    comb = sorted(set(last_sorted) | set(today), key=lambda c: (-universe[c], c))
    tail = comb[-drop:] if drop and comb else []
    tail_set = set(tail)
    sell = [c for c in last_sorted if c in tail_set]
    n_buy = len(sell) + k - len(last_sorted)
    if n_buy < 0:
        n_buy = 0
    buy = today[:n_buy]
    new_held = [c for c in last_sorted if c not in set(sell)] + list(buy)
    if len(new_held) < k:
        have = set(new_held)
        for code in unheld:
            if code in have:
                continue
            new_held.append(code)
            have.add(code)
            if len(new_held) >= k:
                break
    return new_held[:k]


def _self_check() -> None:
    scores = {"a": 5.0, "b": 4.0, "c": 3.0, "d": 1.0}
    assert topk_dropout_holdings(scores, [], topk=2, n_drop=1) == ["a", "b"]
    # 候选比持仓差：不换
    assert topk_dropout_holdings(scores, ["a", "b"], topk=2, n_drop=1) == ["a", "b"]
    # 持仓里的 b 差于未持仓的 c：换掉 b
    swap = {"a": 5.0, "b": 1.0, "c": 4.0, "d": 3.0}
    assert topk_dropout_holdings(swap, ["a", "b"], topk=2, n_drop=1) == ["a", "c"]
    # n_drop=topk 且新分数全面更好：整组换掉
    fresh = {"a": 1.0, "b": 2.0, "c": 5.0, "d": 4.0}
    assert topk_dropout_holdings(fresh, ["a", "b"], topk=2, n_drop=2) == ["c", "d"]


def _fit_of(doc: Optional[dict]) -> Optional[dict]:
    if not isinstance(doc, dict):
        return None
    rm = doc.get("return_model")
    if isinstance(rm, dict) and (
        rm.get("booster_b64") or rm.get("coefficients") or rm.get("solver")
    ):
        return rm
    if doc.get("booster_b64") or doc.get("coefficients"):
        return doc
    return None


def _rank_map(codes: Sequence[str], preds: Sequence[Optional[float]]) -> Dict[str, dict]:
    rows: List[Tuple[str, float]] = []
    for code, pred in zip(codes, preds):
        if pred is None:
            continue
        try:
            score = float(pred)
        except (TypeError, ValueError):
            continue
        if score != score:
            continue
        rows.append((str(code), score))
    rows.sort(key=lambda it: (-it[1], it[0]))
    n = len(rows)
    out: Dict[str, dict] = {}
    for i, (code, score) in enumerate(rows):
        out[code] = {"score": score, "rank": i + 1, "n": n}
    return out


def _score_panels(days: Sequence[dict], fit: dict) -> Dict[str, Dict[str, dict]]:
    from core.research.oo_rank_lambdarank import _predict_oo_rank_matrix

    out: Dict[str, Dict[str, dict]] = {}
    for day in days:
        date = str(day.get("date") or "")[:10]
        if not date:
            continue
        preds = _predict_oo_rank_matrix(fit, day)
        codes = list(day.get("codes") or [])
        ranked = _rank_map(codes, preds)
        if ranked:
            out[date] = ranked
    return out


def _y_by_day(days: Sequence[dict]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for day in days:
        date = str(day.get("date") or "")[:10]
        codes = list(day.get("codes") or [])
        ys = list(day.get("ys") or [])
        bucket: Dict[str, float] = {}
        for code, y in zip(codes, ys):
            try:
                v = float(y)
            except (TypeError, ValueError):
                continue
            if v != v:
                continue
            bucket[str(code)] = v
        if date and bucket:
            out[date] = bucket
    return out


def _composite_scores(
    oo: Dict[str, dict],
    tc: Dict[str, dict],
    y: Dict[str, float],
    *,
    mode: str,
) -> Dict[str, Tuple[float, float]]:
    """返回 code → (排序分, 当日收益百分点)。只保留两边名次和收益都在的票。"""
    out: Dict[str, Tuple[float, float]] = {}
    for code, ret in y.items():
        o = oo.get(code)
        t = tc.get(code)
        if not o or not t:
            continue
        oo_r = int(o["rank"])
        tc_r = int(t["rank"])
        if mode == "oo":
            key = -float(oo_r)
        elif mode == "tc":
            key = -float(tc_r)
        else:
            # 与 rank 入场同一口径：越差的一侧决定排序；名次打平再看两侧之和
            key = -float(max(oo_r, tc_r)) * 1000.0 - float(oo_r + tc_r)
        out[code] = (key, ret)
    return out


def _simulate(
    dates: Sequence[str],
    books: Dict[str, Dict[str, Tuple[float, float]]],
    *,
    topk: int,
    n_drop: int,
    cost_cfg: dict,
) -> dict:
    from core.backtest.costs import rebalance_cost_pct

    held: List[str] = []
    gross: List[float] = []
    costs: List[float] = []
    replaced: List[int] = []
    for date in dates:
        panel = books[date]
        scores = {c: pair[0] for c, pair in panel.items()}
        nxt = topk_dropout_holdings(scores, held, topk=topk, n_drop=n_drop)
        prev = list(held)
        sold = len(set(prev) - set(nxt))
        cost = float(
            rebalance_cost_pct(prev, nxt, config=cost_cfg)
        )
        rets = [panel[c][1] for c in nxt if c in panel]
        if not rets:
            held = nxt
            continue
        gross.append(sum(rets) / float(len(rets)))
        costs.append(cost)
        replaced.append(sold)
        held = nxt
    n = len(gross)
    if n == 0:
        return {"n": 0}
    nets = [g - c for g, c in zip(gross, costs)]

    def _compound(xs: Sequence[float]) -> float:
        eq = 1.0
        for x in xs:
            eq *= 1.0 + float(x) / 100.0
        return (eq - 1.0) * 100.0

    mean = sum(nets) / float(n)
    var = sum((x - mean) ** 2 for x in nets) / float(n)
    std = var ** 0.5
    sharpe = (mean / std * (252.0 ** 0.5)) if std > 1e-12 else None
    return {
        "n": n,
        "gross_pct": _compound(gross),
        "net_pct": _compound(nets),
        "mean_net": mean,
        "sharpe": sharpe,
        "turnover": (sum(replaced) / float(n)) / float(topk),
        "mean_cost": sum(costs) / float(n),
    }


def _fmt(val: Optional[float], nd: int = 2) -> str:
    if val is None:
        return "  n/a"
    return f"{val:{nd+6}.{nd}f}"


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 5 or n != len(ys):
        return None
    mx = sum(xs) / float(n)
    my = sum(ys) / float(n)
    num = 0.0
    dx = 0.0
    dy = 0.0
    for a, b in zip(xs, ys):
        da = float(a) - mx
        db = float(b) - my
        num += da * db
        dx += da * da
        dy += db * db
    if dx < 1e-18 or dy < 1e-18:
        return None
    return num / ((dx ** 0.5) * (dy ** 0.5))


def _score_y_oo_days(days: Sequence[dict], model: Any) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """研究套 ŷ_oo 连续分。返回 date → code → (score, 实现收益百分点)。"""
    import numpy as np

    from core.research.oo_ridge_compact import predict_oo_matrix

    out: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for day in days:
        date = str(day.get("date") or "")[:10]
        x = day.get("X")
        names = list(day.get("names") or [])
        codes = list(day.get("codes") or [])
        ys = list(day.get("ys") or [])
        if not date or not isinstance(x, np.ndarray) or not names:
            continue
        n = int(x.shape[0])
        if n == 0 or len(codes) != n or len(ys) != n:
            continue
        preds = predict_oo_matrix(
            model,
            x,
            names,
            list(range(n)),
            row_dates=[date] * n,
        )
        bucket: Dict[str, Tuple[float, float]] = {}
        for code, pred, y in zip(codes, preds, ys):
            if pred is None:
                continue
            try:
                score = float(pred)
                ret = float(y)
            except (TypeError, ValueError):
                continue
            if score != score or ret != ret:
                continue
            bucket[str(code)] = (score, ret)
        if bucket:
            out[date] = bucket
    return out


def _window_diag(dates: Sequence[str], books: Dict[str, Dict[str, Tuple[float, float]]], topk: int) -> None:
    ics: List[float] = []
    top_means: List[float] = []
    bot_means: List[float] = []
    pool_means: List[float] = []
    for date in dates:
        rows = list(books[date].items())
        if len(rows) < max(20, topk * 2):
            continue
        rows.sort(key=lambda it: (-it[1][0], it[0]))
        scores = [pair[0] for _, pair in rows]
        rets = [pair[1] for _, pair in rows]
        ic = _pearson(scores, rets)
        if ic is not None:
            ics.append(ic)
        k = min(int(topk), len(rows))
        top_means.append(sum(rets[:k]) / float(k))
        bot_means.append(sum(rets[-k:]) / float(k))
        pool_means.append(sum(rets) / float(len(rets)))

    def _avg(xs: Sequence[float]) -> Optional[float]:
        if not xs:
            return None
        return sum(xs) / float(len(xs))

    ic_m = _avg(ics)
    ic_std = None
    if ics and ic_m is not None:
        var = sum((x - ic_m) ** 2 for x in ics) / float(len(ics))
        ic_std = var ** 0.5
    icir = (ic_m / ic_std) if ic_m is not None and ic_std and ic_std > 1e-12 else None
    print(
        f"本窗截面  IC={_fmt(ic_m, 4).strip()}  ICIR={_fmt(icir, 2).strip()}  "
        f"Top{topk}日均={_fmt(_avg(top_means)).strip()}%  "
        f"Bottom{topk}日均={_fmt(_avg(bot_means)).strip()}%  "
        f"池日均={_fmt(_avg(pool_means)).strip()}%"
    )


def load_pool(lookback: int) -> List[dict]:
    from core.data.facade import get_bars_batch
    from core.watching.store import read_watching

    uni = read_watching()
    pool = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    print(f"观察池 {len(pool)} 只，日线 lookback={lookback}", flush=True)
    packs = get_bars_batch(pool, limit=int(lookback))
    stock_bars: List[dict] = []
    skipped = 0
    for pack in packs:
        if not isinstance(pack, dict):
            skipped += 1
            continue
        code = str(pack.get("stock_code") or pack.get("code") or "").strip()
        bars = list(pack.get("bars") or [])
        if not code or len(bars) < 40:
            skipped += 1
            continue
        stock_bars.append({"code": code, "bars": bars})
    print(f"有效 {len(stock_bars)} 只，跳过 {skipped}", flush=True)
    return stock_bars


def _main_y_oo(args: argparse.Namespace, *, cost_cfg: dict) -> int:
    from core.research.oo_rank_panel import build_oo_rank_day_panels
    from core.research.oo_ridge import load_return_model

    model, info = load_return_model(prefer_research=True)
    if model is None:
        print(f"缺少研究套 y_oo：{info.get('reason')}")
        return 1
    fitted = getattr(model, "fitted_as_of", None) or info.get("saved_at") or "?"
    scope = getattr(model, "zscore_scope", "") or "train"
    print(
        f"y_oo  role={info.get('model_role')}  solver={getattr(model, 'solver', '?')}  "
        f"zscore={scope}  fitted={fitted}"
    )
    print(f"路径 {info.get('path')}")
    print("只读研究套。不写模型、不写回测。信号=ŷ_oo 连续分（越高越好）。", flush=True)

    stock_bars = load_pool(int(args.lookback))
    if len(stock_bars) < 8:
        print("有效股票不足 8 只")
        return 1

    print("构建 y_oo 面板（原始 open→次日 open，非超额）...", flush=True)
    oo_days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        excess_mode="none",
    )
    print(f"  oo 截面日 {len(oo_days)}", flush=True)
    print("研究套打分...", flush=True)
    books_all = _score_y_oo_days(oo_days, model)
    usable = sorted(
        date for date, panel in books_all.items() if len(panel) >= int(args.topk)
    )
    days_n = max(5, int(args.days))
    chosen = usable[-days_n:]
    if len(chosen) < 5:
        print(f"可用决策日不足: {len(chosen)}")
        return 1
    books = {date: books_all[date] for date in chosen}
    print(
        f"对照窗 {chosen[0]} → {chosen[-1]}  共 {len(chosen)} 日"
        f"（有 ŷ_oo 且满 topk 的最近 {days_n} 日）",
        flush=True,
    )
    sample_vals = [pair[1] for pair in list(books[chosen[0]].values())[:80]]
    med = sorted(abs(v) for v in sample_vals)[len(sample_vals) // 2]
    print(f"实现 y |中位|≈{med:.3f}（百分点）  当日可打分 {len(books[chosen[-1]])} 只")
    _window_diag(chosen, books, int(args.topk))
    print(
        "成本：换手计费，佣金 2.5bps + 过户 0.1bps + 卖出印花 5bps + 滑点 3bps；"
        "首日从现金建仓会计买入成本。"
    )
    drops: List[int] = []
    for part in str(args.drops).split(","):
        part = part.strip()
        if part:
            drops.append(max(0, int(part)))
    if not drops:
        drops = [1, 2, 5, int(args.topk)]
    print()
    header = (
        f"{'信号':<14} {'n_drop':>6} {'日数':>4} {'毛收益%':>9} {'净收益%':>9} "
        f"{'日均净%':>8} {'夏普':>7} {'单边换手':>8} {'日均成本%':>9}"
    )
    print(header)
    print("-" * len(header))
    for n_drop in drops:
        stat = _simulate(
            chosen,
            books,
            topk=int(args.topk),
            n_drop=n_drop,
            cost_cfg=cost_cfg,
        )
        tag = "满换" if n_drop >= int(args.topk) else "dropout"
        print(
            f"{'y_oo':<14} {n_drop:>6} {stat.get('n', 0):>4} "
            f"{_fmt(stat.get('gross_pct')):>9} {_fmt(stat.get('net_pct')):>9} "
            f"{_fmt(stat.get('mean_net')):>8} {_fmt(stat.get('sharpe')):>7} "
            f"{_fmt(stat.get('turnover'), 3):>8} {_fmt(stat.get('mean_cost'), 4):>9}"
            f"  {tag}"
        )
    print("-" * len(header))
    pool_rets = []
    for date in chosen:
        ys = [pair[1] for pair in books[date].values()]
        if ys:
            pool_rets.append(sum(ys) / float(len(ys)))
    if pool_rets:
        eq = 1.0
        for x in pool_rets:
            eq *= 1.0 + float(x) / 100.0
        print(
            f"对照 观察池等权（当日有 ŷ_oo 的票，不计成本）"
            f"  复利 {(eq - 1.0) * 100.0:.2f}%  日均 {sum(pool_rets) / len(pool_rets):.2f}%"
        )
    print(
        "排序用 ŷ_oo 连续分，越高越好。单边换手 = 日均卖出只数 / topk。"
        "未做涨跌停、停牌、手数和 fuse 门槛。"
    )
    return 0


def main() -> int:
    _self_check()
    parser = argparse.ArgumentParser(description="离线 TopkDropout 对照（不入库）")
    parser.add_argument("--days", type=int, default=20, help="最近 N 个可打分决策日")
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--drops", default="1,2,5,10", help="n_drop 列表；等于 topk 即每日满换")
    parser.add_argument("--lookback", type=int, default=160)
    parser.add_argument(
        "--signal",
        choices=("rank", "y_oo"),
        default="rank",
        help="rank=y_oo_rank 与 y_τc_rank；y_oo=研究套 ŷ_oo 连续分",
    )
    args = parser.parse_args()

    from core.backtest.cost_port import BACKTEST_RESEARCH_DEFAULTS
    if args.signal == "y_oo":
        return _main_y_oo(args, cost_cfg=dict(BACKTEST_RESEARCH_DEFAULTS))

    from core.research.oo_rank_lambdarank import load_oo_rank_model
    from core.research.oo_rank_panel import build_oo_rank_day_panels
    from core.research.panel_matrix import collect_tau_compact
    from core.research.tc_rank import load_tc_rank_model
    from core.research.tc_ridge_compact import TAU_Z_FEATURES
    from core.research.oo_rank_panel import group_day_matrices

    oo_doc = load_oo_rank_model(prefer_research=True)
    tc_doc = load_tc_rank_model(prefer_research=True)
    oo_fit = _fit_of(oo_doc)
    tc_fit = _fit_of(tc_doc)
    if oo_fit is None or tc_fit is None:
        print("缺少研究套 y_oo_rank 或 y_τc_rank，无法离线打分。")
        return 1

    def _meta(doc: dict, fit: dict) -> str:
        backend = fit.get("backend") or fit.get("solver") or "?"
        fitted = (doc or {}).get("fitted_at") or fit.get("fitted_at") or "?"
        return f"backend={backend} fitted_at={fitted}"

    print(f"y_oo_rank  {_meta(oo_doc, oo_fit)}")
    print(f"y_τc_rank  {_meta(tc_doc, tc_fit)}")
    print("只读研究套。不写模型、不写回测。", flush=True)

    stock_bars = load_pool(int(args.lookback))
    if len(stock_bars) < 8:
        print("有效股票不足 8 只")
        return 1

    print("构建 y_oo 面板（原始 open→次日 open，非超额）...", flush=True)
    oo_days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        excess_mode="none",
    )
    print(f"  oo 截面日 {len(oo_days)}", flush=True)

    print("构建 y_τc 面板（开盘钟）...", flush=True)
    import numpy as np

    x, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_Z_FEATURES),
        min_history=12,
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc_rank",
        cache_head="tc_rank",
    )
    codes = [
        str((metas[i] or {}).get("stock_code") or "") if i < len(metas) else ""
        for i in range(len(ys))
    ]
    tc_days = group_day_matrices(
        x,
        np.asarray(ys, dtype=np.float64),
        col_names,
        dates,
        codes,
        min_names=8,
    )
    print(f"  tc 截面日 {len(tc_days)} 股票 {n_stocks}", flush=True)

    print("研究套打分...", flush=True)
    oo_scores = _score_panels(oo_days, oo_fit)
    tc_scores = _score_panels(tc_days, tc_fit)
    y_oo = _y_by_day(oo_days)
    common = sorted(set(oo_scores) & set(tc_scores) & set(y_oo))
    usable: List[str] = []
    for date in common:
        both = set(oo_scores[date]) & set(tc_scores[date]) & set(y_oo[date])
        if len(both) >= int(args.topk):
            usable.append(date)
    days_n = max(5, int(args.days))
    chosen = usable[-days_n:]
    if len(chosen) < 5:
        print(f"可用决策日不足: {len(chosen)}")
        return 1
    print(
        f"对照窗 {chosen[0]} → {chosen[-1]}  共 {len(chosen)} 日"
        f"（两边名次齐全且满 topk 的最近 {days_n} 日）",
        flush=True,
    )

    modes = (
        ("max(oo,τc)", "max"),
        ("oo_rank", "oo"),
        ("τc_rank", "tc"),
    )
    drops = []
    for part in str(args.drops).split(","):
        part = part.strip()
        if not part:
            continue
        drops.append(max(0, int(part)))
    if not drops:
        drops = [1, 2, 5, int(args.topk)]

    books_by_mode: Dict[str, Dict[str, Dict[str, Tuple[float, float]]]] = {}
    for label, mode in modes:
        books: Dict[str, Dict[str, Tuple[float, float]]] = {}
        for date in chosen:
            books[date] = _composite_scores(
                oo_scores[date], tc_scores[date], y_oo[date], mode=mode
            )
        books_by_mode[label] = books

    # 收益量纲自检：y_oo 文档是百分点
    sample_vals = [
        pair[1]
        for date in chosen[:3]
        for pair in list(books_by_mode["oo_rank"][date].values())[:30]
    ]
    med = sorted(abs(v) for v in sample_vals)[len(sample_vals) // 2] if sample_vals else 0.0
    print(f"y_oo |中位|≈{med:.3f}（期望为百分点，约 1 表示 1%）")
    print(
        "成本：换手计费，佣金 2.5bps + 过户 0.1bps + 卖出印花 5bps + 滑点 3bps；"
        "首日从现金建仓会计买入成本。"
    )
    print()
    header = (
        f"{'信号':<14} {'n_drop':>6} {'日数':>4} {'毛收益%':>9} {'净收益%':>9} "
        f"{'日均净%':>8} {'夏普':>7} {'单边换手':>8} {'日均成本%':>9}"
    )
    print(header)
    print("-" * len(header))
    cost_cfg = dict(BACKTEST_RESEARCH_DEFAULTS)
    for label, _mode in modes:
        for n_drop in drops:
            stat = _simulate(
                chosen,
                books_by_mode[label],
                topk=int(args.topk),
                n_drop=n_drop,
                cost_cfg=cost_cfg,
            )
            tag = "满换" if n_drop >= int(args.topk) else "dropout"
            print(
                f"{label:<14} {n_drop:>6} {stat.get('n', 0):>4} "
                f"{_fmt(stat.get('gross_pct')):>9} {_fmt(stat.get('net_pct')):>9} "
                f"{_fmt(stat.get('mean_net')):>8} {_fmt(stat.get('sharpe')):>7} "
                f"{_fmt(stat.get('turnover'), 3):>8} {_fmt(stat.get('mean_cost'), 4):>9}"
                f"  {tag}"
            )
        print("-" * len(header))
    pool_rets: List[float] = []
    for date in chosen:
        ys = [pair[1] for pair in books_by_mode["oo_rank"][date].values()]
        if ys:
            pool_rets.append(sum(ys) / float(len(ys)))
    if pool_rets:
        eq = 1.0
        for x in pool_rets:
            eq *= 1.0 + float(x) / 100.0
        print(
            f"对照 观察池等权（两边名次都在的票，不计成本）"
            f"  复利 {(eq - 1.0) * 100.0:.2f}%  日均 {sum(pool_rets) / len(pool_rets):.2f}%"
        )
    print(
        "排序：1=最好。max(oo,τc) 与「rank 入场」同一侧（两侧都差的排后）。"
        "单边换手 = 日均卖出只数 / topk。净收益已扣换手成本。"
        "未做涨跌停、停牌、手数和 fuse 门槛，不能直接当 /replay。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
