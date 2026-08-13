#!/usr/bin/env python3
"""Promote full-pool h=1 cluster draft → active + refresh book (keep mode=active)."""
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

    print("promote+apply from draft mode=active", flush=True)
    out = qs.apply_cluster_live_shortcut(
        from_draft=True,
        note="h=1 full watching refit 2026-08-13; fix G5 neg-RS for 000938; OOS-fail groups excluded from book",
        mode="active",
        force=False,
    )
    promo = out.get("promote") or {}
    refresh = out.get("refresh") or {}
    rank = (refresh.get("rank") if isinstance(refresh, dict) else None) or {}
    print(
        {
            "success": out.get("success"),
            "error": out.get("error") or promo.get("error"),
            "version": out.get("version") or promo.get("version"),
            "mode": ((out.get("mode") or {}).get("cluster_scoring") or {}).get("mode"),
            "book_n": rank.get("name_count"),
            "note": out.get("note"),
        },
        flush=True,
    )

    # Focus check on active after promote
    from core.signal.cluster_live import load_active_cluster_weights

    active = load_active_cluster_weights() or {}
    cm = active.get("code_map") or {}
    focus = {}
    for code in ("000938", "603019"):
        m = cm.get(code) or {}
        rm = m.get("return_model") or {}
        coefs = rm.get("coefficients") or {}
        focus[code] = {
            "label": m.get("cluster_label"),
            "oos_passed": m.get("oos_passed"),
            "rs": coefs.get("relative_strength"),
            "mom": coefs.get("momentum"),
        }
    print("focus", json.dumps(focus, ensure_ascii=False), flush=True)

    # Book: are focus names in / excluded?
    book_path = ROOT / "data" / "live" / "cluster_book_active.json"
    book_doc = json.loads(book_path.read_text(encoding="utf-8")) if book_path.exists() else {}
    book = book_doc.get("book") or book_doc if isinstance(book_doc, list) else book_doc.get("book") or []
    codes_in_book = {
        str(x.get("stock_code") or x.get("code") or "")
        for x in (book or [])
        if isinstance(x, dict)
    }
    print(
        "in_book",
        {c: c in codes_in_book for c in ("000938", "603019")},
        "book_n",
        len(codes_in_book),
        flush=True,
    )

    summary = {
        "apply": {
            "success": out.get("success"),
            "version": out.get("version") or promo.get("version"),
            "error": out.get("error") or promo.get("error"),
            "mode": ((out.get("mode") or {}).get("cluster_scoring") or {}).get("mode"),
            "book_n": rank.get("name_count"),
            "note": out.get("note"),
        },
        "focus": focus,
        "in_book": {c: c in codes_in_book for c in ("000938", "603019")},
    }
    out_path = ROOT / "data" / "live" / "cluster_promote_h1_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out_path, flush=True)
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
