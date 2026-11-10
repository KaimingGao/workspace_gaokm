"""离线：最近 N 日 A 档 y_oo_fuse 预估日均 vs 真实 fuse 日均。

口径（开盘 τ=open，rot=0）：
  预估 fuse = w_oo·ŷ_oo + w_τc·ŷ_τc
  真实 fuse = open[T+1]/open[T]−1  （= 面板 y_oo；与 y_oo_fuse_realized 同）

只读研究套 / 分档落盘；打印结果，不写库、不改配置。

用法（quantlab 目录）:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_fuse_a_mean_offline.py
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_fuse_a_mean_offline.py --days 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _mean(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 5 or n != len(ys):
        return None
    mx = sum(xs) / float(n)
    my = sum(ys) / float(n)
    num = dx = dy = 0.0
    for a, b in zip(xs, ys):
        da = float(a) - mx
        db = float(b) - my
        num += da * db
        dx += da * da
        dy += db * db
    if dx < 1e-18 or dy < 1e-18:
        return None
    return num / ((dx ** 0.5) * (dy ** 0.5))


def _load_a_codes() -> Tuple[Set[str], Dict[str, Any]]:
    from core.paths import LIVE_DIR

    meta: Dict[str, Any] = {"source": None, "n_a": 0}
    last_path = os.path.join(LIVE_DIR, "predictability_tiers_last.json")
    active_path = os.path.join(LIVE_DIR, "predictability_tiers_active.json")
    rows: List[dict] = []
    if os.path.isfile(last_path):
        doc = json.load(open(last_path, encoding="utf-8"))
        rows = list(doc.get("rows") or [])
        meta["source"] = last_path
        meta["as_of"] = doc.get("as_of_eval_last") or doc.get("as_of_tier_last")
        meta["tier_n"] = doc.get("tier_n")
    elif os.path.isfile(active_path):
        doc = json.load(open(active_path, encoding="utf-8"))
        report = doc.get("report") if isinstance(doc.get("report"), dict) else {}
        rows = list(report.get("rows") or [])
        meta["source"] = active_path
        meta["allowed"] = doc.get("allowed_tiers")
    codes = {
        str(r.get("code") or "").strip()
        for r in rows
        if isinstance(r, dict) and str(r.get("tier") or "").strip().upper() == "A"
    }
    codes.discard("")
    meta["n_a"] = len(codes)
    return codes, meta


def _fusion_cfg() -> Dict[str, float]:
    """优先 paper 执行快照，否则默认 0.6/0.4。"""
    from core.signal.yhat_windows import fusion_weights_from_cfg

    cfg: Dict[str, Any] = {}
    paper_path = os.path.join(ROOT, "data", "paper.json")
    if os.path.isfile(paper_path):
        try:
            paper = json.load(open(paper_path, encoding="utf-8"))
            # 常见落点：rules / execution / last_rebalance 参数
            for blob in (
                paper.get("rules"),
                paper.get("execution"),
                (paper.get("last_rebalance") or {}).get("params"),
                paper,
            ):
                if not isinstance(blob, dict):
                    continue
                for k in ("fusion_w_oo", "fusion_w_oc"):
                    if blob.get(k) is not None:
                        cfg[k] = blob.get(k)
        except Exception:  # noqa: BLE001
            cfg = {}
    if "fusion_w_oo" not in cfg:
        cfg["fusion_w_oo"] = 0.6
    if "fusion_w_oc" not in cfg:
        cfg["fusion_w_oc"] = 0.4
    w_oo, w_tc = fusion_weights_from_cfg(cfg)
    return {"fusion_w_oo": w_oo, "fusion_w_oc": w_tc}


def _load_pool(lookback: int, a_codes: Set[str]) -> List[dict]:
    """观察池日线；若 A 档与观察池交集够大则只拉交集加速。"""
    from core.data.facade import get_bars_batch
    from core.watching.store import read_watching

    uni = read_watching()
    watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    inter = [c for c in watch if c in a_codes]
    # 截面 μ/σ 用「观察池∩A」近似；A 档本就来自观察池分档
    pool = inter if len(inter) >= 40 else watch
    _log(f"拉日线 pool={len(pool)}（观察池 {len(watch)} · A∩池 {len(inter)}）lookback={lookback}")
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
    _log(f"有效 {len(stock_bars)} 只，跳过 {skipped}")
    return stock_bars


def _score_oo_days(days: Sequence[dict], model: Any) -> Dict[str, Dict[str, Tuple[float, float]]]:
    import numpy as np
    from core.research.oo_model_compact import predict_oo_matrix

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
            p = _f(pred)
            r = _f(y)
            if p is None or r is None:
                continue
            bucket[str(code)] = (p, r)
        if bucket:
            out[date] = bucket
    return out


def _score_ridge_days(
    days: Sequence[dict], fit: dict
) -> Dict[str, Dict[str, float]]:
    from core.research.oo_rank_lambdarank import _predict_ridge_matrix

    out: Dict[str, Dict[str, float]] = {}
    for day in days:
        date = str(day.get("date") or "")[:10]
        if not date:
            continue
        preds = _predict_ridge_matrix(fit, day)
        codes = list(day.get("codes") or [])
        bucket: Dict[str, float] = {}
        for code, pred in zip(codes, preds):
            p = _f(pred)
            if p is None:
                continue
            bucket[str(code)] = p
        if bucket:
            out[date] = bucket
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="A档 fuse 预估均值 vs 真实（离线·不入库）")
    parser.add_argument("--days", type=int, default=20, help="最近 N 个可对齐决策日")
    parser.add_argument("--lookback", type=int, default=160)
    args = parser.parse_args()

    a_codes, a_meta = _load_a_codes()
    if len(a_codes) < 8:
        _log(f"A 档不足：{a_meta}")
        return 1
    _log(
        f"A 档 {a_meta.get('n_a')} 只  source={a_meta.get('source')}  "
        f"as_of={a_meta.get('as_of')}"
    )

    weights = _fusion_cfg()
    w_oo = float(weights["fusion_w_oo"])
    w_tc = float(weights["fusion_w_oc"])
    _log(f"融合权 w_oo={w_oo:.3f}  w_τc={w_tc:.3f}（开盘 rot=0）")

    from core.research.holdout import MODEL_ROLE_RESEARCH
    from core.research.oo_model import load_return_model
    from core.research.tc_model import load_tau_model
    from core.signal.yhat_windows import fuse_pct

    oo_model, oo_info = load_return_model(prefer_research=True)
    if oo_model is None:
        _log(f"缺研究套 ŷ_oo：{oo_info.get('reason')}")
        return 1
    tau_doc = load_tau_model(role=MODEL_ROLE_RESEARCH)
    if not tau_doc or not isinstance(tau_doc.get("return_model"), dict):
        _log("缺研究套 ŷ_τc（tau_lgb_model_research）")
        return 1
    tau_fit = dict(tau_doc["return_model"])
    _log(
        f"ŷ_oo role={oo_info.get('model_role')} path={oo_info.get('path')}\n"
        f"ŷ_τc research ok"
    )

    stock_bars = _load_pool(int(args.lookback), a_codes)
    if len(stock_bars) < 8:
        _log("有效股票不足")
        return 1

    from core.research.rank_day_panels import build_rank_day_panels

    _log("构建 y_oo 面板…")
    oo_days = build_rank_day_panels(
        stock_bars,
        family="oo_rank",
        horizon_days=1,
        excess_mode="none",
    )
    _log(f"  oo 日 {len(oo_days)}")

    _log("构建 y_τc 面板（open）…")
    tau_stats: dict = {}
    tc_days = build_rank_day_panels(
        stock_bars,
        family="tc",
        head="y_tc",
        cache_head="tc_fuse_offline",
        stats_out=tau_stats,
    )
    _log(f"  tc 日 {len(tc_days)} 股票 {tau_stats.get('n_stocks', '?')}")

    _log("研究套打分…")
    oo_books = _score_oo_days(oo_days, oo_model)
    tc_books = _score_ridge_days(tc_days, tau_fit)

    common = sorted(set(oo_books) & set(tc_books))
    days_n = max(5, int(args.days))
    # 只要 T+1 已实现（oo 面板 ys 非空即有次日开）
    usable = [d for d in common if oo_books.get(d)]
    chosen = usable[-days_n:]
    if len(chosen) < 5:
        _log(f"可用决策日不足: {len(chosen)}")
        return 1
    _log(f"对照窗 {chosen[0]} → {chosen[-1]}  共 {len(chosen)} 日")

    daily_rows: List[dict] = []
    pred_all: List[float] = []
    real_all: List[float] = []
    for date in chosen:
        oo_map = oo_books[date]
        tc_map = tc_books[date]
        preds: List[float] = []
        reals: List[float] = []
        for code in a_codes:
            if code not in oo_map or code not in tc_map:
                continue
            y_oo_hat, y_oo_real = oo_map[code]
            y_tc_hat = tc_map[code]
            fuse = fuse_pct(y_oo_hat, y_tc_hat, w_left=w_oo, w_right=w_tc)
            if fuse is None:
                continue
            preds.append(float(fuse))
            reals.append(float(y_oo_real))
        if len(preds) < 5:
            continue
        mp = _mean(preds)
        mr = _mean(reals)
        assert mp is not None and mr is not None
        daily_rows.append(
            {
                "date": date,
                "n": len(preds),
                "mean_pred": mp,
                "mean_real": mr,
                "bias": mp - mr,
            }
        )
        pred_all.extend(preds)
        real_all.extend(reals)

    if not daily_rows:
        _log("无对齐样本")
        return 1

    _log("")
    _log(f"{'date':<12} {'n_A':>5} {'mean_pred%':>11} {'mean_real%':>11} {'bias':>9}")
    _log("-" * 52)
    for row in daily_rows:
        _log(
            f"{row['date']:<12} {row['n']:>5} "
            f"{row['mean_pred']:>+11.4f} {row['mean_real']:>+11.4f} "
            f"{row['bias']:>+9.4f}"
        )
    _log("-" * 52)

    mean_pred_d = _mean([r["mean_pred"] for r in daily_rows])
    mean_real_d = _mean([r["mean_real"] for r in daily_rows])
    mean_bias_d = _mean([r["bias"] for r in daily_rows])
    pooled_pred = _mean(pred_all)
    pooled_real = _mean(real_all)
    corr_day = _pearson(
        [r["mean_pred"] for r in daily_rows],
        [r["mean_real"] for r in daily_rows],
    )
    # 截面：每日 A 档内 pred vs real 相关，再对日平均
    ics: List[float] = []
    for date, row in ((r["date"], r) for r in daily_rows):
        oo_map = oo_books[date]
        tc_map = tc_books[date]
        ps: List[float] = []
        rs: List[float] = []
        for code in a_codes:
            if code not in oo_map or code not in tc_map:
                continue
            y_oo_hat, y_oo_real = oo_map[code]
            fuse = fuse_pct(y_oo_hat, tc_map[code], w_left=w_oo, w_right=w_tc)
            if fuse is None:
                continue
            ps.append(float(fuse))
            rs.append(float(y_oo_real))
        ic = _pearson(ps, rs)
        if ic is not None:
            ics.append(ic)

    _log("")
    _log("汇总（不入库）")
    _log(f"  日数            {len(daily_rows)}")
    _log(f"  日均 n_A        {_mean([float(r['n']) for r in daily_rows]):.1f}")
    _log(f"  日均 mean_pred  {mean_pred_d:+.4f}%")
    _log(f"  日均 mean_real  {mean_real_d:+.4f}%")
    _log(f"  日均 bias       {mean_bias_d:+.4f}%   （预估−真实）")
    _log(f"  池化 mean_pred  {pooled_pred:+.4f}%  (n={len(pred_all)})")
    _log(f"  池化 mean_real  {pooled_real:+.4f}%")
    _log(f"  日序列相关      {corr_day if corr_day is not None else 'n/a'}")
    _log(
        f"  A档内截面IC均   "
        f"{(_mean(ics) if ics else None):+.4f}" if ics else "  A档内截面IC均   n/a"
    )
    _log(
        "说明：开盘钟真实 fuse≡y_oo；这是「A 档等权温度计」对照，"
        "不是市值加权大盘预估。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
