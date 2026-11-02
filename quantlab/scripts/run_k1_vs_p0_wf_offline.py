"""离线 walk-forward：训练窗与测试窗同步滚动，live_K1 vs new_P0（不入库）。

相对 ``run_k1_vs_p0_offline.py``（冻当前 research 权重）：
  - 测试窗最近 ``--days`` 个可对齐决策日
  - 每 ``--refit-every`` 日重训一次（扩展窗：只用决策日之前、且按 h=1 embargo 的行）
  - ŷ_oo / ŷ_τc / oo_rank 均用 Ridge+日截面 z（现网 oo_rank 为 LambdaRank，此处为可滚动代理）
  - 另打一份 frozen 对照（当前盘内 research 权重，同窗不重训）

用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_k1_vs_p0_wf_offline.py --days 60 --refit-every 5
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def _rank_map(codes: Sequence[str], preds: Sequence[Any]) -> Dict[str, dict]:
    rows: List[Tuple[str, float]] = []
    for code, pred in zip(codes, preds):
        try:
            score = float(pred)
        except (TypeError, ValueError):
            continue
        if score != score:
            continue
        rows.append((str(code), score))
    rows.sort(key=lambda it: (-it[1], it[0]))
    return {c: {"score": s, "rank": i + 1} for i, (c, s) in enumerate(rows)}


def _cfg() -> Dict[str, Any]:
    cfg = {
        "fusion_w_oo": 0.4,
        "fusion_w_oc": 0.6,
        "fuse_enter": 0.002,
        "fuse_strong": 0.003,
        "oo_rank_max": 6,
        "oo_rank_strong_max": 12,
        "lot_base_amount": 50_000.0,
        "lot_strong_amount": 100_000.0,
        "cash": 200_000.0,
    }
    path = os.path.join(ROOT, "data", "paper.json")
    if os.path.isfile(path):
        try:
            paper = json.load(open(path, encoding="utf-8"))
            blob = (
                ((paper.get("rules") or {}).get("execution") or {})
                .get("rebalance_timing", {})
                .get("rank_lots")
            )
            if isinstance(blob, dict):
                for k in list(cfg):
                    if k in blob and blob[k] is not None:
                        cfg[k] = blob[k]
        except Exception:  # noqa: BLE001
            pass
    return cfg


def _stack_days(
    days: Sequence[dict],
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str], List[str]]:
    xs: List[np.ndarray] = []
    ys: List[float] = []
    dates: List[str] = []
    codes: List[str] = []
    names: List[str] = []
    for day in days:
        x = day.get("X")
        if not isinstance(x, np.ndarray) or x.ndim != 2:
            continue
        n = int(x.shape[0])
        day_names = [str(n_) for n_ in (day.get("names") or [])]
        day_codes = [str(c) for c in (day.get("codes") or [])]
        day_ys = list(day.get("ys") or [])
        date = str(day.get("date") or "")[:10]
        if n == 0 or len(day_names) != int(x.shape[1]) or len(day_codes) != n:
            continue
        if len(day_ys) != n or len(date) < 10:
            continue
        if not names:
            names = day_names
        elif day_names != names:
            # 列不一致则跳过该日
            continue
        xs.append(np.asarray(x, dtype=np.float64))
        for y in day_ys:
            try:
                ys.append(float(y))
            except (TypeError, ValueError):
                ys.append(float("nan"))
        dates.extend([date] * n)
        codes.extend(day_codes)
    if not xs:
        return (
            np.zeros((0, 0), dtype=np.float64),
            np.zeros(0, dtype=np.float64),
            [],
            [],
            [],
        )
    return np.vstack(xs), np.asarray(ys, dtype=np.float64), names, dates, codes


def _train_cutoff(uniq: Sequence[str], eval_start: str, *, embargo: int = 1) -> str:
    """返回 train 末日（含）：eval_start 往前 embargo 个交易日再退一格。"""
    days = [str(d)[:10] for d in uniq if len(str(d)[:10]) >= 10]
    if eval_start not in days:
        raise KeyError(eval_start)
    i = days.index(eval_start)
    cut = i - max(0, int(embargo))
    if cut <= 0:
        return ""
    # train 含至 cut-1（embargo 日与 eval 都不进训）
    return days[cut - 1]


def _fit_ridge_on_days(
    days: Sequence[dict],
    *,
    fit_end: str,
    ridge_lambda: float = 1.0,
) -> Optional[Any]:
    """扩展窗 Ridge：date <= fit_end。返回 ReturnScoreModel。"""
    from core.research.oo_ridge_compact import fit_oo_ridge_matrix

    use = [d for d in days if str(d.get("date") or "")[:10] <= fit_end]
    if len(use) < 8:
        return None
    x, y, names, dates, _codes = _stack_days(use)
    if x.shape[0] < 40 or not names:
        return None
    model, report = fit_oo_ridge_matrix(
        x,
        y,
        names,
        ridge_lambda=float(ridge_lambda),
        horizon_days=1,
        min_samples=40,
        fitted_as_of=fit_end,
        row_dates=dates,
        cross_section_zscore=True,
    )
    if model is None or not (isinstance(report, dict) and report.get("success")):
        return None
    return model


def _fit_rank_ridge_on_days(
    days: Sequence[dict],
    *,
    fit_end: str,
    ridge_lambda: float = 1.0,
) -> Optional[dict]:
    from core.research.feature_standardize import stamp_cross_section_zscore
    from core.research.oo_rank_lambdarank import _fit_ridge_baseline

    use = [d for d in days if str(d.get("date") or "")[:10] <= fit_end]
    if len(use) < 8:
        return None
    names = list((use[-1].get("names") or []))
    if not names:
        return None
    report = _fit_ridge_baseline(
        use,
        feature_names=names,
        ridge_lambda=float(ridge_lambda),
        cross_section_zscore=True,
    )
    if not isinstance(report, dict) or not report.get("success"):
        return None
    model = report.get("return_model") or report.get("model")
    if not isinstance(model, dict):
        # fit_oo_ridge_matrix 可能把系数放在 report 顶层
        coefs = report.get("coefficients")
        if not isinstance(coefs, dict):
            return None
        model = {
            "coefficients": coefs,
            "intercept": float(report.get("intercept") or 0.0),
            "feature_zscore": True,
            "solver": "ridge",
        }
    stamp_cross_section_zscore(model)
    model["solver"] = "ridge"
    model["backend"] = "ridge"
    model["fitted_as_of"] = fit_end
    return model


def _model_to_fit_dict(model: Any) -> Optional[dict]:
    """ReturnScoreModel → 与 _predict_ridge_matrix 兼容的 dict。"""
    if model is None:
        return None
    if isinstance(model, dict):
        return model
    coefs = getattr(model, "coefficients", None)
    if not isinstance(coefs, dict) or not coefs:
        return None
    out = {
        "coefficients": dict(coefs),
        "intercept": float(getattr(model, "intercept", 0.0) or 0.0),
        "feature_zscore": bool(getattr(model, "feature_zscore", True)),
        "zscore_scope": str(getattr(model, "zscore_scope", "") or ""),
        "z_means": dict(getattr(model, "z_means", None) or {}),
        "z_stds": dict(getattr(model, "z_stds", None) or {}),
        "solver": "ridge",
    }
    from core.research.feature_standardize import (
        ZSCORE_SCOPE_CROSS_SECTION,
        stamp_cross_section_zscore,
    )

    if out["zscore_scope"] == ZSCORE_SCOPE_CROSS_SECTION or getattr(
        model, "zscore_scope", ""
    ) == ZSCORE_SCOPE_CROSS_SECTION:
        stamp_cross_section_zscore(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--lookback", type=int, default=220)
    ap.add_argument("--refit-every", type=int, default=5)
    ap.add_argument("--ridge-lambda", type=float, default=1.0)
    args = ap.parse_args()

    mean_m = _load(
        os.path.join(ROOT, "scripts", "run_fuse_a_mean_offline.py"),
        "fuse_a_mean_offline",
    )
    k1_m = _load(
        os.path.join(ROOT, "scripts", "run_k1_vs_p0_offline.py"),
        "k1_vs_p0_offline",
    )
    _log, _mean = mean_m._log, mean_m._mean
    _cfg_fn = k1_m._cfg if hasattr(k1_m, "_cfg") else _cfg

    a_codes, a_meta = mean_m._load_a_codes()
    _log(f"A 档 {a_meta.get('n_a')} as_of={a_meta.get('as_of')}")
    cfg = _cfg_fn()
    w_oo = float(cfg["fusion_w_oo"])
    w_tc = float(cfg["fusion_w_oc"])
    s = w_oo + w_tc
    if s > 1e-12:
        w_oo, w_tc = w_oo / s, w_tc / s
    fuse_enter = float(cfg["fuse_enter"])
    fuse_strong = float(cfg["fuse_strong"])
    oo_max = int(cfg["oo_rank_max"] or 0)
    oo_strong_max = int(cfg["oo_rank_strong_max"] or 0)
    lot_base = float(cfg["lot_base_amount"])
    lot_strong = float(cfg["lot_strong_amount"])
    cash0 = float(cfg["cash"])
    refit_every = max(1, int(args.refit_every))
    _log(
        f"cfg enter={fuse_enter*100:.2f}% strong={fuse_strong*100:.2f}% "
        f"w={w_oo:.2f}/{w_tc:.2f} oo_rank_max={oo_max} "
        f"refit_every={refit_every}"
    )

    from core.research.holdout import MODEL_ROLE_RESEARCH
    from core.research.oo_rank_lambdarank import (
        _predict_oo_rank_matrix,
        _predict_ridge_matrix,
        load_oo_rank_model,
    )
    from core.research.oo_rank_panel import build_oo_rank_day_panels, group_day_matrices
    from core.research.oo_ridge import load_return_model
    from core.research.oo_ridge_compact import predict_oo_matrix
    from core.research.panel_matrix import collect_tau_compact
    from core.research.tc_ridge import load_tau_model
    from core.research.tc_ridge_compact import TAU_Z_FEATURES
    from core.signal.yhat_windows import fuse_pct, oc_with_co

    stock_bars = mean_m._load_pool(int(args.lookback), a_codes)
    if len(stock_bars) < 20:
        _log("有效标的不足")
        return 1

    t0 = time.time()
    oo_days = build_oo_rank_day_panels(
        stock_bars, horizon_days=1, excess_mode="none"
    )
    x, col_names, ys, dates, metas, _n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_Z_FEATURES),
        min_history=12,
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc",
        cache_head="tc_wf_offline",
    )
    codes = [
        str((metas[i] or {}).get("stock_code") or "") if i < len(metas) else ""
        for i in range(len(ys))
    ]
    tc_days = group_day_matrices(
        x, np.asarray(ys, dtype=np.float64), col_names, dates, codes, min_names=8
    )
    _log(
        f"面板 oo_days={len(oo_days)} tc_days={len(tc_days)} "
        f"耗时 {time.time()-t0:.1f}s"
    )

    oo_by = {str(d.get("date") or "")[:10]: d for d in oo_days}
    tc_by = {str(d.get("date") or "")[:10]: d for d in tc_days}
    common = sorted(set(oo_by) & set(tc_by))
    if len(common) < max(30, int(args.days) + 20):
        _log(f"可对齐日不足 common={len(common)}")
        return 1
    chosen = common[-max(5, int(args.days)) :]
    uniq = list(common)
    _log(f"WF 窗 {chosen[0]}→{chosen[-1]} n={len(chosen)}")

    # ---- frozen 对照（当前 research）----
    oo_frozen, info = load_return_model(prefer_research=True)
    if oo_frozen is None:
        _log(str(info))
        return 1
    tau_doc = load_tau_model(role=MODEL_ROLE_RESEARCH)
    tau_frozen = dict(tau_doc["return_model"])
    rank_frozen = _fit_of(load_oo_rank_model(prefer_research=True))
    if rank_frozen is None:
        _log("缺 oo_rank frozen")
        return 1

    def _score_pack(oo_model, tau_fit, rank_fit, day_dates: Sequence[str]):
        oo_books: Dict[str, Dict[str, Tuple[float, float]]] = {}
        tc_pred: Dict[str, Dict[str, float]] = {}
        oo_rank_by: Dict[str, Dict[str, dict]] = {}
        tc_rank_by: Dict[str, Dict[str, dict]] = {}
        for date in day_dates:
            od = oo_by.get(date)
            td = tc_by.get(date)
            if od is None or td is None:
                continue
            # ŷ_oo
            if hasattr(oo_model, "coefficients") or (
                isinstance(oo_model, dict) and oo_model.get("coefficients")
            ):
                if isinstance(oo_model, dict):
                    preds = _predict_ridge_matrix(oo_model, od)
                else:
                    xx = od.get("X")
                    names = list(od.get("names") or [])
                    n = int(xx.shape[0]) if isinstance(xx, np.ndarray) else 0
                    preds = predict_oo_matrix(
                        oo_model, xx, names, list(range(n)), row_dates=[date] * n
                    )
            else:
                continue
            ys_d = list(od.get("ys") or [])
            codes_d = list(od.get("codes") or [])
            bucket: Dict[str, Tuple[float, float]] = {}
            for code, pred, y in zip(codes_d, preds, ys_d):
                try:
                    p = float(pred)
                    r = float(y)
                except (TypeError, ValueError):
                    continue
                if p != p or r != r:
                    continue
                bucket[str(code)] = (p, r)
            if bucket:
                oo_books[date] = bucket

            # ŷ_τc
            tpreds = _predict_ridge_matrix(tau_fit, td)
            tcodes = list(td.get("codes") or [])
            tb: Dict[str, float] = {}
            for code, pred in zip(tcodes, tpreds):
                try:
                    p = float(pred)
                except (TypeError, ValueError):
                    continue
                if p != p:
                    continue
                tb[str(code)] = p
            if tb:
                tc_pred[date] = tb

            # ranks（A 档内）
            def _a_rank(fit, day):
                ranked = _rank_map(
                    list(day.get("codes") or []),
                    _predict_oo_rank_matrix(fit, day)
                    if fit.get("booster_b64")
                    else _predict_ridge_matrix(fit, day),
                )
                rows = [(c, inf) for c, inf in ranked.items() if c in a_codes]
                rows.sort(key=lambda it: (-it[1]["score"], it[0]))
                return {
                    c: {"score": inf["score"], "rank_a": i + 1}
                    for i, (c, inf) in enumerate(rows)
                }

            oo_rank_by[date] = _a_rank(rank_fit, od)
            tc_rank_by[date] = _a_rank(tau_fit, td)
        return oo_books, tc_pred, oo_rank_by, tc_rank_by

    def _build_day_rows(oo_books, tc_pred, oo_rank_by, tc_rank_by, day_dates):
        out: Dict[str, List[dict]] = {}
        for date in day_dates:
            if date not in oo_books or date not in tc_pred or date not in oo_rank_by:
                continue
            rows = []
            for code in a_codes:
                if code not in oo_books[date] or code not in tc_pred[date]:
                    continue
                if code not in oo_rank_by[date]:
                    continue
                y_oo, y_real = oo_books[date][code]
                y_tc = float(tc_pred[date][code])
                fuse = fuse_pct(
                    y_oo, oc_with_co(y_tc, None, 0.0), w_left=w_oo, w_right=w_tc
                )
                if fuse is None:
                    continue
                oo_r = int(oo_rank_by[date][code]["rank_a"])
                tc_r = (
                    int(tc_rank_by[date][code]["rank_a"])
                    if date in tc_rank_by and code in tc_rank_by[date]
                    else oo_r
                )
                rows.append(
                    {
                        "code": code,
                        "y_oo": float(y_oo),
                        "y_tc": y_tc,
                        "fuse": float(fuse),
                        "fuse_net": float(fuse) / 100.0,
                        "label": float(y_real),
                        "oo_rank": oo_r,
                        "eff_rank": max(oo_r, tc_r),
                    }
                )
            out[date] = rows
        return out

    def _pass_live(r: dict, *, need_tc: bool, need_rank_max: bool) -> bool:
        if r["fuse_net"] <= fuse_enter:
            return False
        if r["y_oo"] <= 0:
            return False
        if need_tc and r["y_tc"] <= 0:
            return False
        if need_rank_max:
            strong = r["fuse_net"] > fuse_strong
            lim = oo_strong_max if strong else oo_max
            if lim > 0 and r["eff_rank"] >= lim:
                return False
        return True

    def _pick_k1(rows, *, need_tc: bool, need_rank_max: bool):
        cands = [
            r
            for r in rows
            if _pass_live(r, need_tc=need_tc, need_rank_max=need_rank_max)
        ]
        if not cands:
            return None
        return min(cands, key=lambda r: (r["oo_rank"], r["code"]))

    def _pick_p0(rows):
        cands = [r for r in rows if r["fuse_net"] > fuse_enter and r["y_oo"] > 0]
        if not cands:
            return None
        return min(cands, key=lambda r: (r["oo_rank"], r["code"]))

    def _sim_lots(rows) -> Dict[str, Any]:
        cands = []
        for r in rows:
            if not _pass_live(r, need_tc=True, need_rank_max=True):
                continue
            strong = r["fuse_net"] > fuse_strong
            cands.append({**r, "lot": lot_strong if strong else lot_base, "strong": strong})
        cands.sort(key=lambda r: (r["oo_rank"], r["code"]))
        cash = cash0
        buys = []
        for r in cands:
            lot = float(r["lot"])
            if lot > cash + 1e-6:
                if r["strong"] and lot_base <= cash + 1e-6:
                    lot = lot_base
                else:
                    continue
            buys.append({**r, "notional": lot})
            cash -= lot
            if cash < lot_base * 0.5:
                break
        if not buys:
            return {"n": 0, "hit": None, "ret": None}
        w = [b["notional"] for b in buys]
        lab = [b["label"] for b in buys]
        ret = sum(a * b for a, b in zip(w, lab)) / sum(w)
        return {"n": len(buys), "hit": 1 if ret > 1e-12 else 0, "ret": ret}

    def _run_arms(tag: str, day_rows: Dict[str, List[dict]], day_dates: Sequence[str]):
        arms = {
            f"{tag}_K1": [],
            f"{tag}_P0": [],
            f"{tag}_lots": [],
        }
        _log("")
        _log(f"[{tag}] date        K1   P0  K1码     P0码    同? lots_n lots_ret")
        _log("-" * 78)
        for date in day_dates:
            rows = day_rows.get(date) or []
            p_k1 = _pick_k1(rows, need_tc=True, need_rank_max=True)
            p_p0 = _pick_p0(rows)
            lots = _sim_lots(rows)

            def _rec(name, p):
                if p is None:
                    arms[name].append({"empty": True, "hit": None, "ret": None})
                    return "—"
                hit = 1 if p["label"] > 1e-12 else 0
                arms[name].append(
                    {
                        "empty": False,
                        "hit": hit,
                        "ret": p["label"],
                        "code": p["code"],
                    }
                )
                return "Y" if hit else "N"

            h1 = _rec(f"{tag}_K1", p_k1)
            h4 = _rec(f"{tag}_P0", p_p0)
            if lots["n"]:
                arms[f"{tag}_lots"].append(
                    {
                        "empty": False,
                        "hit": lots["hit"],
                        "ret": lots["ret"],
                        "n": lots["n"],
                    }
                )
                ls = f"{lots['n']:>6} {lots['ret']:>+8.3f}"
            else:
                arms[f"{tag}_lots"].append(
                    {"empty": True, "hit": None, "ret": None}
                )
                ls = f"{0:>6} {'—':>8}"
            same = (
                "Y"
                if p_k1 and p_p0 and p_k1["code"] == p_p0["code"]
                else ("—" if not p_k1 or not p_p0 else "N")
            )
            _log(
                f"{date} {h1:>3} {h4:>3} "
                f"{(p_k1 or {}).get('code') or '—':<8} "
                f"{(p_p0 or {}).get('code') or '—':<8} "
                f"{same:>3} {ls}"
            )
        return arms

    def _summ(arms: Dict[str, list], name: str, *, is_lots: bool = False) -> None:
        xs = arms[name]
        active = [x for x in xs if not x.get("empty")]
        empty = len(xs) - len(active)
        if not active:
            _log(f"  {name:<18} 全日空仓 {empty}/{len(xs)}")
            return
        hits = sum(int(x["hit"]) for x in active)
        n = len(active)
        ret = _mean([float(x["ret"]) for x in active])
        extra = ""
        if is_lots:
            eq = 1.0
            for x in active:
                eq *= 1.0 + float(x["ret"]) / 100.0
            extra = f"  复利 {(eq - 1) * 100:+.2f}%"
        _log(
            f"  {name:<18} 命中/日+ {hits}/{n}={100.0 * hits / n:.1f}%  "
            f"空仓 {empty}/{len(xs)}  日均 {ret:+.3f}%{extra}"
        )

    # frozen score whole window
    _log("打分 frozen research…")
    fr = _score_pack(oo_frozen, tau_frozen, rank_frozen, chosen)
    frozen_rows = _build_day_rows(*fr, chosen)
    frozen_arms = _run_arms("frozen", frozen_rows, chosen)

    # walk-forward segments
    _log("")
    _log(
        f"WF 重训：扩展窗 + embargo=1，每 {refit_every} 日；"
        "oo/τc/rank 皆 Ridge（rank≠现网 LambdaRank）"
    )
    wf_day_rows: Dict[str, List[dict]] = {}
    n_refit = 0
    fit_ends: List[str] = []
    for seg_i in range(0, len(chosen), refit_every):
        seg = chosen[seg_i : seg_i + refit_every]
        eval_start = seg[0]
        try:
            fit_end = _train_cutoff(uniq, eval_start, embargo=1)
        except KeyError:
            _log(f"skip {eval_start}: not in calendar")
            continue
        if not fit_end:
            _log(f"skip {eval_start}: train too short")
            continue
        t1 = time.time()
        oo_m = _fit_ridge_on_days(
            oo_days, fit_end=fit_end, ridge_lambda=float(args.ridge_lambda)
        )
        tc_m = _fit_ridge_on_days(
            tc_days, fit_end=fit_end, ridge_lambda=float(args.ridge_lambda)
        )
        rk_m = _fit_rank_ridge_on_days(
            oo_days, fit_end=fit_end, ridge_lambda=float(args.ridge_lambda)
        )
        tau_fit = _model_to_fit_dict(tc_m)
        if oo_m is None or tau_fit is None or rk_m is None:
            _log(
                f"  refit@{eval_start} fit_end={fit_end} FAILED "
                f"oo={oo_m is not None} tc={tau_fit is not None} rk={rk_m is not None}"
            )
            continue
        n_refit += 1
        fit_ends.append(fit_end)
        pack = _score_pack(oo_m, tau_fit, rk_m, seg)
        part = _build_day_rows(*pack, seg)
        wf_day_rows.update(part)
        _log(
            f"  refit#{n_refit} eval={eval_start}..{seg[-1]} "
            f"fit_end={fit_end} days={len(seg)} {time.time()-t1:.1f}s"
        )

    wf_dates = [d for d in chosen if d in wf_day_rows]
    wf_arms = _run_arms("wf", wf_day_rows, wf_dates)

    _log("")
    _log("汇总（不入库 · 60 日级 walk-forward）")
    _log("-- frozen（当前 research，不重训）--")
    _summ(frozen_arms, "frozen_K1")
    _summ(frozen_arms, "frozen_P0")
    _summ(frozen_arms, "frozen_lots", is_lots=True)
    _log("-- wf（扩展窗滚动 Ridge）--")
    _summ(wf_arms, "wf_K1")
    _summ(wf_arms, "wf_P0")
    _summ(wf_arms, "wf_lots", is_lots=True)
    _log(
        f"重训次数={n_refit}  fit_end 例: "
        f"{fit_ends[0] if fit_ends else '—'} … {fit_ends[-1] if fit_ends else '—'}"
    )
    _log(
        "说明：wf 的 oo_rank 为 Ridge 代理；frozen 的 oo_rank 为现网 LambdaRank。"
        "未扣费、未入库。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
