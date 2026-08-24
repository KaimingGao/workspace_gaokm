#!/usr/bin/env python3
"""Refit h=1 clusters after partition_loss retune; compare vs active."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _members(cl: dict) -> list:
    mem = cl.get("members") or cl.get("codes") or cl.get("stock_codes") or []
    if isinstance(mem, list) and mem and isinstance(mem[0], dict):
        return [x.get("stock_code") or x.get("code") for x in mem]
    return list(mem or [])


def _focus(clusters: list, code: str) -> dict | None:
    for cl in clusters:
        mem = _members(cl)
        if code not in mem:
            continue
        gate = cl.get("oos_gate") or {}
        coefs = (cl.get("return_model") or {}).get("coefficients") or {}
        return {
            "label": cl.get("label") or cl.get("cluster_label"),
            "oos_passed": gate.get("passed"),
            "oos_reason": gate.get("reason"),
            "rs": coefs.get("relative_strength"),
            "mom": coefs.get("momentum"),
            "n": len(mem),
            "members": mem,
        }
    return None


def _oos_fail(clusters: list) -> list:
    out = []
    for cl in clusters:
        gate = cl.get("oos_gate") or {}
        if gate.get("passed") is False:
            out.append(
                {
                    "label": cl.get("label") or cl.get("cluster_label"),
                    "reason": gate.get("reason"),
                    "n": len(_members(cl)),
                }
            )
    return out


def main() -> int:
    from quant.services.quant_service import QuantService
    from core.signal.cluster.live import load_active_cluster_weights

    try:
        qs = QuantService()
    except TypeError:
        qs = QuantService.__new__(QuantService)

    active = load_active_cluster_weights() or {}
    active_clusters = active.get("clusters") or []
    print(
        f"active v{active.get('version')} n_cl={len(active_clusters)} "
        f"oos_fail={len(_oos_fail(active_clusters))}",
        flush=True,
    )

    print("start cluster h=1 watching_limit=100 refresh_bars=False use_cache=False", flush=True)
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
    if not rep.get("success"):
        print("FAILED", rep.get("error"), flush=True)
        return 1

    clusters = rep.get("clusters") or []
    focus_codes = ("000938", "603019")
    draft_focus = {c: _focus(clusters, c) for c in focus_codes}
    active_focus = {c: _focus(active_clusters, c) for c in focus_codes}
    for c in focus_codes:
        d, a = draft_focus.get(c), active_focus.get(c)
        print(
            f"{c} ACTIVE label={a and a.get('label')} oos={a and a.get('oos_passed')} "
            f"rs={a and a.get('rs')} | DRAFT label={d and d.get('label')} "
            f"oos={d and d.get('oos_passed')} rs={d and d.get('rs')}",
            flush=True,
        )

    draft_fail = _oos_fail(clusters)
    active_fail = _oos_fail(active_clusters)
    print(
        f"OOS fail draft {len(draft_fail)}/{len(clusters)} "
        f"active {len(active_fail)}/{len(active_clusters)}",
        flush=True,
    )
    print("draft_fail", draft_fail, flush=True)

    from core.signal.cluster.oos_labels import compare_partition_vs_active
    from core.signal.cluster.live import get_cluster_scoring_cfg

    ccfg = get_cluster_scoring_cfg()
    max_rate = ccfg.get("max_oos_fail_rate")
    preflight = compare_partition_vs_active(
        rep,
        active,
        focus_codes=focus_codes,
        max_oos_fail_rate=float(max_rate) if max_rate is not None else None,
        allow_worse_than_active=bool(
            ccfg.get("promote_allow_worse_oos_than_active", True)
        ),
    )
    print(
        "promote_preflight",
        {
            "promote_ready": preflight.get("promote_ready"),
            "blockers": preflight.get("blockers"),
            "warnings": preflight.get("warnings"),
            "delta": preflight.get("delta"),
            "greedy": (preflight.get("draft") or {}).get("greedy_refine"),
        },
        flush=True,
    )

    ksel = rep.get("k_selection") or {}
    print(
        "k_selection",
        {
            k: ksel.get(k)
            for k in (
                "selected_k",
                "best_k",
                "n_candidates",
                "score_key",
                "ok",
                "note",
            )
            if k in ksel or True
        },
        flush=True,
    )
    # trim k_selection dump
    print(
        "k_sel_keys",
        list(ksel.keys())[:20],
        "selected",
        ksel.get("selected_k") or ksel.get("best_k") or ksel.get("k"),
        "greedy",
        (ksel.get("greedy_refine") or {}).get("mode"),
        (ksel.get("greedy_refine") or {}).get("n_swaps"),
        flush=True,
    )

    summary = {
        "note": "partition_loss retune (ic_ref_scale/imbalance/singleton) — draft only",
        "success": True,
        "horizon_days": 1,
        "stock_count": rep.get("stock_count"),
        "n_clusters": rep.get("n_clusters") or len(clusters),
        "active_version": active.get("version"),
        "active_oos_fail_n": len(active_fail),
        "active_n_clusters": len(active_clusters),
        "draft_oos_fail_n": len(draft_fail),
        "draft_oos_fail": draft_fail,
        "focus_active": active_focus,
        "focus_draft": draft_focus,
        "promote_preflight": {
            "promote_ready": preflight.get("promote_ready"),
            "blockers": preflight.get("blockers"),
            "warnings": preflight.get("warnings"),
            "delta": preflight.get("delta"),
        },
        "k_selection_selected": ksel.get("selected_k")
        or ksel.get("best_k")
        or ksel.get("k"),
        "greedy_refine": ksel.get("greedy_refine"),
        "partition_loss_defaults": {
            "ic_ref_scale": 0.05,
            "w_ic": 1.0,
            "imbalance_mult": 1.5,
            "lambda_singleton": 0.75,
        },
    }
    out = ROOT / "data" / "live" / "cluster_refit_after_loss_tune.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
