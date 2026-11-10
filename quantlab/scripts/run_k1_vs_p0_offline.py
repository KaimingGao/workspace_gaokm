"""离线：现网闸门 K=1 vs new_P0（不入库）。

臂：
  live_K1     = 现网闸(fuse+ŷ_oo>0+ŷ_τc>0+oo_rank_max)后按 oo_rank 只买 1 只
  live_K1_noτc= 同上但关掉 y_τc>0
  live_K1_norank = 现网闸但关掉 oo_rank_max（仍要 ŷ_τc>0）
  new_P0      = fuse+ŷ_oo>0，无 ŷ_τc/名次闸，oo_rank Top1
  live_lots   = 现网多票（对照）

用法:
  PYTHONUNBUFFERED=1 .venv/bin/python scripts/run_k1_vs_p0_offline.py --days 20
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    return {
        c: {"score": s, "rank": i + 1}
        for i, (c, s) in enumerate(rows)
    }


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--lookback", type=int, default=160)
    args = ap.parse_args()

    mean_m = _load(
        os.path.join(ROOT, "scripts", "run_fuse_a_mean_offline.py"),
        "fuse_a_mean_offline",
    )
    _log, _mean = mean_m._log, mean_m._mean

    a_codes, a_meta = mean_m._load_a_codes()
    _log(f"A 档 {a_meta.get('n_a')} as_of={a_meta.get('as_of')}")
    cfg = _cfg()
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
    _log(
        f"cfg enter={fuse_enter*100:.2f}% strong={fuse_strong*100:.2f}% "
        f"w={w_oo:.2f}/{w_tc:.2f} oo_rank_max={oo_max}"
    )

    from core.research.holdout import MODEL_ROLE_RESEARCH
    from core.research.oo_model import load_return_model
    from core.research.tc_model import load_tau_model
    from core.research.oo_rank_lambdarank import (
        _predict_oo_rank_matrix,
        load_oo_rank_model,
    )
    from core.research.tc_rank import load_tc_rank_model
    from core.research.rank_day_panels import build_rank_day_panels
    from core.signal.yhat_windows import fuse_pct

    oo_model, info = load_return_model(prefer_research=True)
    if oo_model is None:
        _log(str(info))
        return 1
    tau_doc = load_tau_model(role=MODEL_ROLE_RESEARCH)
    tau_fit = dict(tau_doc["return_model"])
    oo_rank_fit = _fit_of(load_oo_rank_model(prefer_research=True))
    tc_rank_fit = _fit_of(load_tc_rank_model(prefer_research=True))
    if oo_rank_fit is None:
        _log("缺 oo_rank")
        return 1

    stock_bars = mean_m._load_pool(int(args.lookback), a_codes)
    oo_days = build_rank_day_panels(
        stock_bars, family="oo_rank", horizon_days=1, excess_mode="none"
    )
    tc_days = build_rank_day_panels(
        stock_bars,
        family="tc",
        head="y_tc",
        cache_head="tc_fuse_offline",
    )

    oo_books = mean_m._score_oo_days(oo_days, oo_model)
    tc_pred = mean_m._score_ridge_days(tc_days, tau_fit)

    def _a_ranks(fit, days):
        out = {}
        for day in days:
            date = str(day.get("date") or "")[:10]
            ranked = _rank_map(
                list(day.get("codes") or []), _predict_oo_rank_matrix(fit, day)
            )
            rows = [(c, inf) for c, inf in ranked.items() if c in a_codes]
            rows.sort(key=lambda it: (-it[1]["score"], it[0]))
            out[date] = {
                c: {"score": inf["score"], "rank_a": i + 1}
                for i, (c, inf) in enumerate(rows)
            }
        return out

    oo_rank_by = _a_ranks(oo_rank_fit, oo_days)
    tc_rank_by = _a_ranks(tc_rank_fit, tc_days) if tc_rank_fit else {}

    common = sorted(set(oo_books) & set(tc_pred) & set(oo_rank_by))
    chosen = common[-max(5, int(args.days)) :]
    _log(f"窗 {chosen[0]}→{chosen[-1]} n={len(chosen)}")

    day_rows: Dict[str, List[dict]] = {}
    for date in chosen:
        rows = []
        for code in a_codes:
            if code not in oo_books[date] or code not in tc_pred[date]:
                continue
            if code not in oo_rank_by[date]:
                continue
            y_oo, y_real = oo_books[date][code]
            y_tc = float(tc_pred[date][code])
            fuse = fuse_pct(y_oo, y_tc, w_left=w_oo, w_right=w_tc)
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
        day_rows[date] = rows

    def _pass_live(
        r: dict, *, need_tc: bool, need_rank_max: bool
    ) -> bool:
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

    def _pick_k1(rows: List[dict], *, need_tc: bool, need_rank_max: bool):
        cands = [
            r
            for r in rows
            if _pass_live(r, need_tc=need_tc, need_rank_max=need_rank_max)
        ]
        if not cands:
            return None
        return min(cands, key=lambda r: (r["oo_rank"], r["code"]))

    def _pick_p0(rows: List[dict]):
        cands = [
            r for r in rows if r["fuse_net"] > fuse_enter and r["y_oo"] > 0
        ]
        if not cands:
            return None
        return min(cands, key=lambda r: (r["oo_rank"], r["code"]))

    def _sim_lots(rows: List[dict]) -> Dict[str, Any]:
        cands = []
        for r in rows:
            if not _pass_live(r, need_tc=True, need_rank_max=True):
                continue
            strong = r["fuse_net"] > fuse_strong
            cands.append(
                {
                    **r,
                    "lot": lot_strong if strong else lot_base,
                    "strong": strong,
                }
            )
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

    arms = {
        "live_K1": [],
        "live_K1_noτc": [],
        "live_K1_norank": [],
        "new_P0": [],
        "live_lots": [],
    }

    _log("")
    _log(
        f"{'date':<12} {'K1':>4} {'K1¬τc':>6} {'K1¬rk':>6} {'P0':>4} "
        f"{'K1码':<8} {'P0码':<8} {'同?':>3} {'lots_n':>6} {'lots_ret':>9}"
    )
    _log("-" * 90)

    for date in chosen:
        rows = day_rows[date]
        p_k1 = _pick_k1(rows, need_tc=True, need_rank_max=True)
        p_nt = _pick_k1(rows, need_tc=False, need_rank_max=True)
        p_nr = _pick_k1(rows, need_tc=True, need_rank_max=False)
        p_p0 = _pick_p0(rows)
        lots = _sim_lots(rows)

        def _rec(name, p):
            if p is None:
                arms[name].append({"empty": True, "hit": None, "ret": None})
                return "—"
            hit = 1 if p["label"] > 1e-12 else 0
            arms[name].append(
                {"empty": False, "hit": hit, "ret": p["label"], "code": p["code"]}
            )
            return "Y" if hit else "N"

        h1 = _rec("live_K1", p_k1)
        h2 = _rec("live_K1_noτc", p_nt)
        h3 = _rec("live_K1_norank", p_nr)
        h4 = _rec("new_P0", p_p0)
        if lots["n"]:
            arms["live_lots"].append(
                {"empty": False, "hit": lots["hit"], "ret": lots["ret"], "n": lots["n"]}
            )
            ls = f"{lots['n']:>6} {lots['ret']:>+9.3f}"
        else:
            arms["live_lots"].append({"empty": True, "hit": None, "ret": None})
            ls = f"{0:>6} {'—':>9}"

        same = (
            "Y"
            if p_k1 and p_p0 and p_k1["code"] == p_p0["code"]
            else ("—" if not p_k1 or not p_p0 else "N")
        )
        _log(
            f"{date:<12} {h1:>4} {h2:>6} {h3:>6} {h4:>4} "
            f"{(p_k1 or {}).get('code') or '—':<8} "
            f"{(p_p0 or {}).get('code') or '—':<8} "
            f"{same:>3} {ls}"
        )

    _log("-" * 90)

    def _summ(name: str, is_lots: bool = False) -> None:
        xs = arms[name]
        active = [x for x in xs if not x.get("empty")]
        empty = len(xs) - len(active)
        if not active:
            _log(f"  {name:<16} 全日空仓 {empty}/{len(xs)}")
            return
        hits = sum(int(x["hit"]) for x in active)
        n = len(active)
        ret = _mean([float(x["ret"]) for x in active])
        extra = ""
        if is_lots:
            eq = 1.0
            for x in active:
                eq *= 1.0 + float(x["ret"]) / 100.0
            extra = f"  复利 {(eq-1)*100:+.2f}%"
        _log(
            f"  {name:<16} 命中/日+ {hits}/{n}={100.0*hits/n:.1f}%  "
            f"空仓 {empty}/{len(xs)}  日均 {ret:+.3f}%{extra}"
        )

    _log("")
    _log("汇总（不入库）")
    _summ("live_K1")
    _summ("live_K1_noτc")
    _summ("live_K1_norank")
    _summ("new_P0")
    _summ("live_lots", is_lots=True)

    # 同票率
    both = 0
    same = 0
    for date in chosen:
        rows = day_rows[date]
        a = _pick_k1(rows, need_tc=True, need_rank_max=True)
        b = _pick_p0(rows)
        if a and b:
            both += 1
            if a["code"] == b["code"]:
                same += 1
    _log(
        f"  live_K1 与 new_P0 同日都有票时同码: "
        f"{same}/{both} = {(100.0*same/both if both else 0):.1f}%"
    )
    _log(
        "live_K1=现网闸+只买1只；new_P0=fuse+ŷ_oo>0 后 oo_rank Top1；"
        "¬τc/¬rk 为拆闸对照。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
