"""分组 ŷ_oo Ridge：Alpha158 开/关 OOS 对照。

路径：``compute_factor_ols_cluster_report``（β 聚类 → 组池 Ridge）
+ ``attach_cluster_oos_gates``（组 holdout IC / ΔOOS）。
基线：临时从 registry 移除 ``alpha158``；引入后恢复。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from core.research.portfolio_bars import load_portfolio_stock_bars
from core.signal.factors.meta.registry import _REGISTRY
from core.validation_universe import resolve_validation_codes
from quant.research.cluster_oos import attach_cluster_oos_gates
from quant.research.factor_ols_clusters import compute_factor_ols_cluster_report


def load_stock_panels():
    codes = resolve_validation_codes()["codes"]
    stock_bars_dict, _f, _s = load_portfolio_stock_bars(
        codes, lookback=150, max_names=len(codes)
    )
    return [
        {"code": c, "bars": b}
        for c, b in stock_bars_dict.items()
        if len(b) >= 70
    ]


def _mean(xs):
    vals = [float(x) for x in xs if x is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def summarize(rep: dict) -> dict:
    feats = list(rep.get("feature_names") or [])
    n_a158_feat = sum(1 for f in feats if "alpha158" in str(f).lower())
    clusters = list(rep.get("clusters") or [])
    mses, hits, rmses = [], [], []
    a158_coefs = []
    multi = 0
    for cl in clusters:
        mem = cl.get("members") or cl.get("codes") or []
        if len(mem) >= 2:
            multi += 1
        ya = cl.get("yhat_acc") or {}
        if ya.get("mse") is not None:
            mses.append(ya["mse"])
        if ya.get("rmse") is not None:
            rmses.append(ya["rmse"])
        if ya.get("sign_hit") is not None:
            hits.append(ya["sign_hit"])
        # 组池 OLS active（比单票 β 并集更能反映 alpha158 是否进 ŷ）
        ols = cl.get("ols") or {}
        active = list(ols.get("active_features") or [])
        a158_coefs.append(sum(1 for a in active if "alpha158" in str(a).lower()))
        if not active:
            rm = cl.get("return_model_research") or cl.get("return_model") or {}
            coefs = (rm.get("coefficients") if isinstance(rm, dict) else None) or {}
            a158_coefs[-1] = sum(
                1 for k in coefs if "alpha158" in str(k).lower()
            )
    oos = rep.get("oos_summary") or {}
    return {
        "success": bool(rep.get("success")),
        "error": rep.get("error"),
        "fitted": rep.get("fitted_count"),
        "n_clusters": rep.get("n_clusters"),
        "n_multi": multi,
        "n_features": len(feats),
        "n_a158_feat": n_a158_feat,
        "mean_a158_coefs": _mean(a158_coefs),
        "mean_holdout_mse": _mean(mses),
        "mean_holdout_rmse": _mean(rmses),
        "mean_sign_hit": _mean(hits),
        "oos_passed": oos.get("passed"),
        "oos_failed": oos.get("failed"),
        "mean_yhat_ic": oos.get("mean_yhat_ic"),
        "mean_delta_oos_pp": oos.get("mean_delta_oos_pp"),
        "mean_holdout_r2": oos.get("mean_holdout_r2"),
        "partition_loss": oos.get("partition_loss"),
    }


def run_once(panels, *, label: str) -> dict:
    t0 = time.time()

    def _prog(msg, cur=0, tot=0):
        if tot and (cur in (0, tot) or cur % 25 == 0):
            print(f"  [{label}] {msg}", flush=True)

    print(f"\n=== {label} ===", flush=True)
    rep = compute_factor_ols_cluster_report(
        panels,
        horizon_days=1,
        holdout_trading_days=10,
        ridge_lambda=1.0,
        n_clusters=5,
        pit_fundamentals=False,
        respect_regime=False,  # 避免 regime 白名单丢掉 alpha158
        select_ridge=False,
        collinearity_policy="drop_redundant",
        cluster_method="hierarchical",
        cluster_linkage="complete",
        max_workers=8,
        # 两边同窗：PIT hist 要 ≥61 → max_window≥62；显式 70 公平对照
        min_history=70,
        max_window=70,
        progress_cb=_prog,
    )
    if not rep.get("success"):
        print(f"  FAIL: {rep.get('error')}", flush=True)
        return {"success": False, "error": rep.get("error"), "elapsed": round(time.time() - t0, 1)}

    bars_by_code = {
        str(p.get("code") or p.get("stock_code")): p.get("bars") or []
        for p in panels
        if (p.get("code") or p.get("stock_code"))
    }
    print(f"  [{label}] OOS gates…", flush=True)
    attach_cluster_oos_gates(
        rep,
        lookback=150,
        horizon_days=1,
        oos_tol_pp=1.0,
        run_oos_gate=True,
        bars_by_code=bars_by_code,
    )
    out = summarize(rep)
    out["elapsed"] = round(time.time() - t0, 1)
    print(
        f"  fitted={out['fitted']} k={out['n_clusters']} feats={out['n_features']} "
        f"a158_feat={out['n_a158_feat']} mean_a158_coefs={out['mean_a158_coefs']} "
        f"ŷIC={out['mean_yhat_ic']} ΔOOS={out['mean_delta_oos_pp']} "
        f"sign_hit={out['mean_sign_hit']} mse={out['mean_holdout_mse']} "
        f"({out['elapsed']}s)",
        flush=True,
    )
    return out


def main():
    panels = load_stock_panels()
    print(f"loaded {len(panels)} stocks", flush=True)
    if len(panels) < 10:
        print("ERROR: too few stocks")
        return

    # 基线：registry 临时去掉 alpha158
    entry = _REGISTRY.pop("alpha158", None)
    try:
        base = run_once(panels, label="基线（无 alpha158）")
    finally:
        if entry is not None:
            _REGISTRY["alpha158"] = entry

    full = run_once(panels, label="引入 alpha158")

    print("\n=== 汇总（分组 ŷ_oo Ridge）===")
    hdr = (
        f"{'场景':<14} {'fitted':>6} {'k':>3} {'feats':>6} {'a158':>5} "
        f"{'ŷIC':>8} {'ΔOOS':>8} {'sign':>7} {'mse':>8}"
    )
    print(hdr)

    def row(name, r):
        def f(k):
            v = r.get(k)
            return "—" if v is None else str(v)

        print(
            f"{name:<14} {f('fitted'):>6} {f('n_clusters'):>3} {f('n_features'):>6} "
            f"{f('n_a158_feat'):>5} {f('mean_yhat_ic'):>8} {f('mean_delta_oos_pp'):>8} "
            f"{f('mean_sign_hit'):>7} {f('mean_holdout_mse'):>8}"
        )

    row("基线", base)
    row("引入后", full)


if __name__ == "__main__":
    main()
