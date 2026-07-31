"""Daily preset 离线校验（P21.3）。"""

from __future__ import annotations

from typing import Any, Dict, List

from quant.ops.daily_presets import resolve_daily_preset

PRESET_EXPECTATIONS: Dict[str, Dict[str, bool]] = {
    "advisor": {
        "paper_run": True,
        "eval_mock": True,
        "quant_report": False,
        "paper_rebalance": False,
    },
    "quant": {
        "quant_report": True,
        "watching_refresh": True,
        "cross_section": True,
        "export_quant_report": True,
        "portfolio_neutral_compare": True,
        "paper_rebalance": False,
    },
    "quant_paper": {
        "quant_report": True,
        "paper_rebalance": True,
        "export_quant_report": True,
        "portfolio_neutral_compare": True,
    },
    "full": {
        "paper_run": True,
        "eval_mock": True,
        "quant_report": True,
        "portfolio_neutral_compare": True,
        "paper_rebalance": False,
    },
}


def check_daily_presets() -> Dict[str, Any]:
    failures: List[str] = []
    checked: List[str] = []

    for name, expect in PRESET_EXPECTATIONS.items():
        flags = resolve_daily_preset(name)["flags"]
        checked.append(name)
        for key, want in expect.items():
            got = bool(flags.get(key))
            if got != want:
                failures.append(f"{name}.{key}={got} expected={want}")

    return {
        "success": True,
        "ok": not failures,
        "checked": checked,
        "failures": failures,
    }
