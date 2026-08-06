"""策略验证宇宙（V0.5）：可配置纳入/排除；空财务码治理。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core.paths import DATA_DIR
from core.io_atomic import atomic_write_json

UNIVERSE_PATH = os.path.join(DATA_DIR, "validation_universe.json")


def default_universe() -> Dict[str, Any]:
    return {
        "version": 1,
        "exclude_codes": [],
        "include_only": [],
        "min_codes": 5,
        "min_trading_days": 40,
        "note": "include_only 非空时仅用该列表；否则 watching − exclude。B1：min_codes/min_trading_days 供闸门。",
    }


def load_validation_universe(*, path: Optional[str] = None) -> Dict[str, Any]:
    p = path or UNIVERSE_PATH
    if not os.path.isfile(p):
        return default_universe()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_universe()
    out = default_universe()
    if isinstance(data, dict):
        out.update(data)
    out["exclude_codes"] = [str(c).strip() for c in (out.get("exclude_codes") or []) if str(c).strip()]
    out["include_only"] = [str(c).strip() for c in (out.get("include_only") or []) if str(c).strip()]
    return out


def save_validation_universe(
    payload: dict, *, path: Optional[str] = None
) -> Dict[str, Any]:
    p = path or UNIVERSE_PATH
    merged = default_universe()
    if isinstance(payload, dict):
        merged.update(payload)
    merged["exclude_codes"] = [
        str(c).strip() for c in (merged.get("exclude_codes") or []) if str(c).strip()
    ]
    merged["include_only"] = [
        str(c).strip() for c in (merged.get("include_only") or []) if str(c).strip()
    ]
    atomic_write_json(p, merged)
    return {"ok": True, "path": p, "universe": merged}


def resolve_validation_codes(
    *,
    watching_codes: Optional[Sequence[str]] = None,
    universe: Optional[dict] = None,
) -> Dict[str, Any]:
    """解析验证用股票宇宙。"""
    uni = universe or load_validation_universe()
    include = list(uni.get("include_only") or [])
    exclude = set(uni.get("exclude_codes") or [])
    if include:
        codes = [c for c in include if c not in exclude]
        source = "include_only"
    else:
        if watching_codes is None:
            try:
                from core.data_coverage import universe_codes

                watching_codes = universe_codes()
            except Exception:
                watching_codes = []
        codes = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
        codes = [c for c in codes if c not in exclude]
        source = "watching_minus_exclude"
    return {
        "ok": True,
        "codes": codes,
        "count": len(codes),
        "source": source,
        "excluded": sorted(exclude),
        "universe": uni,
        "min_codes": int(uni.get("min_codes") or 5),
        "min_trading_days": int(uni.get("min_trading_days") or 40),
        "universe_ok": len(codes) >= int(uni.get("min_codes") or 5),
    }


def universe_sample_gate(*, universe: Optional[dict] = None) -> Dict[str, Any]:
    """B1：验证宇宙码数门槛（供 promote / maturity）。"""
    resolved = resolve_validation_codes(universe=universe)
    min_c = int(resolved.get("min_codes") or 5)
    ok = bool(resolved.get("universe_ok"))
    blockers = []
    if not ok:
        blockers.append(
            f"验证宇宙 {resolved.get('count')} < min_codes={min_c}"
        )
    return {
        "ok": ok,
        "count": resolved.get("count"),
        "min_codes": min_c,
        "min_trading_days": resolved.get("min_trading_days"),
        "blockers": blockers,
        "track": "B1",
    }


def empty_fundamentals_report(
    *,
    codes: Optional[Sequence[str]] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """列出空财务码，供补拉或移出验证宇宙。"""
    from core.sample_ops import fundamentals_history_coverage

    if codes is None:
        resolved = resolve_validation_codes()
        codes = resolved.get("codes") or []
        meta = {"universe_source": resolved.get("source")}
    else:
        meta = {"universe_source": "explicit"}
    cov = fundamentals_history_coverage(store_dir=store_dir, codes=codes)
    empty = list(cov.get("empty_codes") or [])
    return {
        "ok": True,
        "empty_codes": empty,
        "empty_count": len(empty),
        "total": cov.get("total"),
        "real_multi_point": cov.get("real_multi_point"),
        "real_multi_coverage": cov.get("real_multi_coverage"),
        **meta,
        "note": "可将 empty_codes 写入 validation_universe.exclude_codes，或跑 fundamentals_warmup。",
    }
