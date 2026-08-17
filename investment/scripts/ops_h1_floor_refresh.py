#!/usr/bin/env python3
"""h=1 ops: lower ŷ floor, sync sector_map, refresh book, probe rem attach."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.signal.scoring_floors import save_scoring_floors
    from core.sector_map_sync import sync_sector_map_from_watching, coverage_report
    from core.signal.cluster_live import refresh_cluster_book_daily, get_cluster_scoring_cfg
    from core.signal.service import get_default_signal_service

    svc = get_default_signal_service()

    # 1) h=1 floor: +1% was calibrated for multi-day; daily ŷ rarely clears 1.0
    #    Use 0.35 (~stance wait/probe band) so OOS-passed groups can form a usable book.
    floor = save_scoring_floors(
        min_predicted_score=0.35,
        note="h=1 align: daily ŷ floor 0.35 (was 1.0 for longer-horizon habit)",
    )
    print("FLOOR", {k: floor.get(k) for k in ("success", "changed", "error", "note")}, flush=True)

    # 2) sector_map for event breadth / active evidence
    sync = sync_sector_map_from_watching(write=True)
    cov = coverage_report()
    print(
        "SECTOR",
        {
            "added": sync.get("added_count"),
            "mapped": sync.get("mapped"),
            "written": sync.get("written"),
            "coverage": cov.get("coverage"),
            "missing_n": len(cov.get("missing") or []),
        },
        flush=True,
    )

    # 3) refresh book under new floor
    print("refresh book…", flush=True)
    refresh = refresh_cluster_book_daily(light=True)
    rank = refresh.get("rank") or {}
    book_n = rank.get("name_count")
    print(
        "BOOK",
        {
            "success": refresh.get("success"),
            "book_n": book_n,
            "mode": get_cluster_scoring_cfg().get("mode"),
        },
        flush=True,
    )

    book_path = ROOT / "data" / "live" / "cluster_book_active.json"
    book_doc = json.loads(book_path.read_text(encoding="utf-8"))
    book = book_doc.get("book") or []
    meta = book_doc.get("meta") or {}
    print(
        "BOOK_ROWS",
        [
            {
                "code": r.get("stock_code"),
                "yhat": r.get("predicted_score"),
                "label": r.get("cluster_label"),
            }
            for r in book[:12]
        ],
        flush=True,
    )
    print("meta_min_score", meta.get("min_score"), "oos_blocked", meta.get("oos_blocked_labels"), flush=True)

    # 4) rem attach probe
    rem_probe = {}
    for code in ("000938", "603019", "688012"):
        scored = svc.score_one(code, cluster_mode="active", skip_sentiment=True)
        si = scored.item or {}
        rem_probe[code] = {
            "predicted_score": scored.predicted_score or si.get("predicted_score"),
            "score_rem": si.get("score_rem"),
            "predicted_score_tau": scored.predicted_score_tau,
            "production_ok": scored.production_ok,
            "gap_pct": si.get("gap_pct"),
            "cluster_label": si.get("cluster_label"),
            "in_book": any(r.get("stock_code") == code for r in book),
            "event_actions": (si.get("event_prior") or {}).get("actions"),
        }
        print(code, rem_probe[code], flush=True)

    out = {
        "floor": floor.get("changed"),
        "sector_coverage": cov.get("coverage"),
        "sector_added": sync.get("added_count"),
        "book_n": len(book),
        "book": [
            {
                "code": r.get("stock_code"),
                "name": r.get("stock_name"),
                "yhat": r.get("predicted_score"),
                "label": r.get("cluster_label"),
            }
            for r in book
        ],
        "meta_min_score": meta.get("min_score"),
        "rem_probe": rem_probe,
        "mode": get_cluster_scoring_cfg().get("mode"),
    }
    path = ROOT / "data" / "live" / "ops_h1_floor_refresh.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", path, flush=True)
    return 0 if refresh.get("success") and floor.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
