#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.signal.cluster_live import (
        assess_cluster_live_health,
        get_cluster_scoring_cfg,
        load_active_cluster_weights,
        cluster_status_public,
    )

    h = assess_cluster_live_health(compute_ic=False)
    print("HEALTH")
    for k, v in h.items():
        s = str(v)
        if len(s) > 400:
            print(f"  {k}: <{type(v).__name__} len={len(v) if hasattr(v,'__len__') else '?'}>")
        else:
            print(f"  {k}: {v}")

    cs = get_cluster_scoring_cfg()
    print("MODE", cs.get("mode"), "enabled", cs.get("enabled"))

    active = load_active_cluster_weights() or {}
    print("ACTIVE v", active.get("version"), "map", len(active.get("code_map") or {}), "clusters", len(active.get("clusters") or []))

    oos_fail = []
    for cl in active.get("clusters") or []:
        gate = cl.get("oos_gate") or {}
        if gate.get("passed") is False:
            mem = cl.get("members") or cl.get("codes") or []
            if mem and isinstance(mem[0], dict):
                mem = [x.get("stock_code") or x.get("code") for x in mem]
            oos_fail.append((cl.get("label"), gate.get("reason"), len(mem)))
    print("OOS_FAIL", oos_fail)

    book_path = ROOT / "data" / "live" / "cluster_book_active.json"
    book_doc = json.loads(book_path.read_text(encoding="utf-8"))
    print("BOOK_DOC keys", list(book_doc)[:20] if isinstance(book_doc, dict) else type(book_doc))
    if isinstance(book_doc, dict):
        for k in (
            "success",
            "error",
            "cluster_version",
            "name_count",
            "excluded_oos_labels",
            "oos_failed_labels",
            "note",
            "warnings",
        ):
            if k in book_doc:
                print(f"  book.{k}={book_doc.get(k)}")
        b = book_doc.get("book") or []
        print("  book_n", len(b))
        for row in b[:5]:
            if isinstance(row, dict):
                print("   ", {k: row.get(k) for k in ("stock_code", "code", "cluster_label", "predicted_score", "oos_passed")})

    st = cluster_status_public(light=True)
    print("STATUS keys", list(st)[:30])
    for k in ("ok", "warnings", "note", "error"):
        if k in st:
            print(f"  status.{k}={st.get(k)}")
    for nest in ("health", "enable_evidence", "cluster_scoring", "gate"):
        v = st.get(nest)
        if isinstance(v, dict):
            print(f"  {nest}:")
            for kk, vv in v.items():
                ss = str(vv)
                if len(ss) > 300:
                    print(f"    {kk}: <truncated>")
                else:
                    print(f"    {kk}: {vv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
