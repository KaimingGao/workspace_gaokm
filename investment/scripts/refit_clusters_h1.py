#!/usr/bin/env python3
"""Offline cluster refit h=1 using local daily cache (refresh_bars=False)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from quant.services.quant_service import QuantService

    try:
        qs = QuantService()
    except TypeError:
        qs = QuantService.__new__(QuantService)

    # Full watching pool (cap 100) so late-list names like 000938/603019 enter code_map.
    print("start cluster h=1 watching_limit=100 refresh_bars=False", flush=True)
    rep = qs.run_factor_ols_cluster_experiment(
        lookback=80,
        horizon_days=1,
        watching_limit=100,
        ridge_lambda=1.0,
        refresh_bars=False,
        use_cache=False,
        run_oos_gate=True,
        respect_regime=True,
    )
    print(
        {
            k: rep.get(k)
            for k in ("success", "error", "n_clusters", "stock_count", "horizon_days")
        },
        flush=True,
    )
    clusters = rep.get("clusters") or []
    code_map = rep.get("code_map") or {}

    def _members(cl: dict) -> list:
        mem = cl.get("members") or cl.get("codes") or cl.get("stock_codes") or []
        if isinstance(mem, list) and mem and isinstance(mem[0], dict):
            return [x.get("stock_code") or x.get("code") for x in mem]
        return list(mem or [])

    def _focus_from_clusters(code: str):
        m = code_map.get(code) or {}
        for cl in clusters:
            mem = _members(cl)
            if code not in mem:
                continue
            gate = cl.get("oos_gate") or {}
            coefs = (cl.get("return_model") or {}).get("coefficients") or {}
            return {
                "label": cl.get("label") or cl.get("cluster_label"),
                "cluster_id": cl.get("cluster_id") or m.get("cluster_id"),
                "oos_passed": gate.get("passed"),
                "oos_reason": gate.get("reason"),
                "rs": coefs.get("relative_strength"),
                "mom": coefs.get("momentum"),
                "members": mem,
                "top": sorted(coefs.items(), key=lambda kv: -abs(float(kv[1] or 0)))[:5],
            }
        return None

    focus = {c: _focus_from_clusters(c) for c in ("000938", "603019")}
    for c, info in focus.items():
        if not info:
            print(c, "label None id None", flush=True)
            continue
        print(
            c,
            "label",
            info.get("label"),
            "id",
            info.get("cluster_id"),
            "oos",
            info.get("oos_passed"),
            "rs",
            info.get("rs"),
            "mom",
            info.get("mom"),
            flush=True,
        )

    for cl in clusters:
        lab = cl.get("label") or cl.get("cluster_label")
        mem = _members(cl)
        gate = cl.get("oos_gate") or {}
        coefs = (cl.get("return_model") or {}).get("coefficients") or {}
        if "000938" in mem or "603019" in mem or gate.get("passed") is False:
            top = sorted(coefs.items(), key=lambda kv: -abs(float(kv[1] or 0)))[:5]
            print(
                f"G {lab} n={len(mem)} oos_passed={gate.get('passed')} "
                f"reason={gate.get('reason')} mem={mem[:12]} topβ={top}",
                flush=True,
            )

    out = ROOT / "data" / "live" / "cluster_refit_h1_summary.json"
    out.write_text(
        json.dumps(
            {
                "success": rep.get("success"),
                "error": rep.get("error"),
                "n_clusters": rep.get("n_clusters") or len(clusters),
                "horizon_days": rep.get("horizon_days") or 1,
                "stock_count": rep.get("stock_count"),
                "focus": focus,
                "oos_fail": [
                    {
                        "label": cl.get("label") or cl.get("cluster_label"),
                        "passed": (cl.get("oos_gate") or {}).get("passed"),
                        "reason": (cl.get("oos_gate") or {}).get("reason"),
                        "n": len(_members(cl)),
                    }
                    for cl in clusters
                    if (cl.get("oos_gate") or {}).get("passed") is False
                ],
                "note": "draft only — do not auto-promote; human review required",
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("wrote", out, flush=True)
    return 0 if rep.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
