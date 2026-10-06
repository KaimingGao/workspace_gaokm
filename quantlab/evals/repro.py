"""确定性引擎回归：同输入两次运行，指纹须一致（不经过 LLM）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple


def stable_fingerprint(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compare_runs(first: Any, second: Any) -> Tuple[bool, str]:
    fp1 = stable_fingerprint(first)
    fp2 = stable_fingerprint(second)
    return fp1 == fp2, fp1 if fp1 == fp2 else f"{fp1} != {fp2}"


def run_score_bars_case(case: dict) -> Dict[str, Any]:
    from core.signal.scorer import score_bars

    bars = case.get("bars") or []
    params = case.get("params") or {}
    quote = case.get("quote")
    a = score_bars(
        bars,
        horizon_days=int(params.get("horizon_days") or 3),
        quote=quote,
    )
    b = score_bars(
        bars,
        horizon_days=int(params.get("horizon_days") or 3),
        quote=quote,
    )
    ok, fp = compare_runs(a, b)
    return {"ok": ok, "fingerprint": fp, "sample": a}


def run_backtest_case(case: dict) -> Dict[str, Any]:
    from core.backtest.engine import backtest_signal_on_bars

    bars = case.get("bars") or []
    params = case.get("params") or {}
    kwargs = {
        "horizon_days": int(params.get("horizon_days") or 3),
        "min_score": float(params.get("min_score") or 55.0),
        "min_history": int(params.get("min_history") or 12),
    }
    a = backtest_signal_on_bars(bars, **kwargs)
    b = backtest_signal_on_bars(bars, **kwargs)
    ok, fp = compare_runs(a, b)
    return {"ok": ok, "fingerprint": fp, "trade_count": (a.get("metrics") or {}).get("trade_count")}


def run_handler_case(case: dict) -> Dict[str, Any]:
    """Handler 在 mock 行情下双跑，比对 JSON 指纹。"""
    from unittest.mock import patch

    from agent.registry import create_handlers

    name = case.get("handler") or case.get("skill")
    params = case.get("parameters") or {}
    patches = case.get("patches") or {}

    handler = create_handlers().get(name)
    if handler is None:
        return {"ok": False, "error": f"未知 handler: {name}"}

    call = {"name": name, "parameters": params}
    patch_targets = {}
    for key, val in patches.items():
        if key == "fetch_daily_bars":
            patch_targets["adapters.market.history.fetch_daily_bars"] = val
        elif key == "StockAPI.query":
            patch_targets["adapters.market.quote_api.StockAPI.query"] = val

    from contextlib import ExitStack

    with ExitStack() as stack:
        for attr, ret in patches.items():
            if attr == "quote":
                stack.enter_context(
                    patch("adapters.market.quote_api.StockAPI.query", return_value=ret)
                )
            elif attr == "bars":
                stack.enter_context(
                    patch(
                        "adapters.market.history.fetch_daily_bars",
                        return_value=(ret, "fixture"),
                    )
                )
        raw_a = handler.execute(call)
        raw_b = handler.execute(call)

    try:
        a = json.loads(raw_a)
        b = json.loads(raw_b)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": str(e)}

    ok, fp = compare_runs(a, b)
    return {"ok": ok, "fingerprint": fp, "success": a.get("success")}


ENGINES = {
    "score_bars": run_score_bars_case,
    "backtest_signal": run_backtest_case,
    "handler": run_handler_case,
}


MANIFEST_REQUIRED_KEYS = (
    "kind",
    "cost_model",
    "fingerprint",
    "data_quality",
)


def check_manifest_contract(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """N4 repro checklist：Run Manifest 须含成本与数据质量键。"""
    missing = [k for k in MANIFEST_REQUIRED_KEYS if k not in (manifest or {})]
    return {
        "ok": not missing,
        "missing": missing,
        "cost_model": (manifest or {}).get("cost_model"),
        "has_data_quality": "data_quality" in (manifest or {}),
    }


def run_repro_case(case: dict) -> Dict[str, Any]:
    engine = case.get("engine") or "score_bars"
    fn = ENGINES.get(engine)
    if not fn:
        return {"ok": False, "error": f"未知 engine: {engine}"}
    out = fn(case)
    out["id"] = case.get("id")
    out["engine"] = engine
    return out
