#!/usr/bin/env python3
"""Try restore cluster mode=active after v54 promote; report blockers if any."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.signal.cluster_live import (
        assess_cluster_live_health,
        build_cluster_enable_evidence,
        set_cluster_scoring_mode,
        get_cluster_scoring_cfg,
    )
    from core.signal.cluster_pointer import active_enable_blockers
    from core.signal.score_stock import score_stock

    health = assess_cluster_live_health()
    evidence = build_cluster_enable_evidence(health=health, include_rolling_ic=True)
    blockers = active_enable_blockers(health=health, evidence=evidence)
    print("blockers", blockers, flush=True)
    gate = (evidence or {}).get("gate") or {}
    print("gate", gate, flush=True)
    print(
        "evidence summary",
        {
            k: evidence.get(k)
            for k in ("ok", "skipped", "note", "oos_fail_rate", "max_oos_fail_rate")
            if k in (evidence or {})
        },
        flush=True,
    )

    if blockers:
        print("KEEP shadow; not forcing active", flush=True)
        mode_out = {"success": False, "kept": "shadow", "blockers": blockers}
    else:
        mode_out = set_cluster_scoring_mode("active", force=False)
        print(
            "set_active",
            {
                "success": mode_out.get("success"),
                "error": mode_out.get("error"),
                "mode": (mode_out.get("cluster_scoring") or {}).get("mode"),
            },
            flush=True,
        )

    # Focus scores under new map
    focus_scores = {}
    for code, name in (("000938", "紫光股份"), ("603019", "中科曙光")):
        try:
            s = score_stock(code, use_cluster=True)
        except Exception as e:
            focus_scores[code] = {"error": str(e)}
            continue
        focus_scores[code] = {
            "name": name,
            "predicted_score": s.get("predicted_score"),
            "cluster_label": s.get("cluster_label") or (s.get("cluster") or {}).get("label"),
            "weight_source": s.get("weight_source"),
            "score_rem": s.get("score_rem"),
            "event_prior": s.get("event_prior"),
            "gap_pct": s.get("gap_pct"),
        }
        print(code, focus_scores[code], flush=True)

    out = {
        "mode": get_cluster_scoring_cfg().get("mode"),
        "blockers": blockers,
        "set_active": {
            "success": mode_out.get("success"),
            "error": mode_out.get("error"),
            "mode": (mode_out.get("cluster_scoring") or get_cluster_scoring_cfg()).get("mode")
            if isinstance(mode_out.get("cluster_scoring"), dict)
            else get_cluster_scoring_cfg().get("mode"),
        },
        "focus_scores": focus_scores,
        "note": "book thin because min_predicted_score=1.0; only 688012 cleared floor under v54",
    }
    path = ROOT / "data" / "live" / "cluster_promote_h1_summary.json"
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev.update(out)
    path.write_text(json.dumps(prev, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
