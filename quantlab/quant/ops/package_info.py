"""Quant 包结构 introspection（P32，运维/文档用）。"""

import os
from typing import Any, Dict, List

from core.paths import ROOT_DIR
from core.readme_index import build_readme_index

QUANT_ROOT = os.path.join(ROOT_DIR, "quant")

SUBPACKAGES = ("services", "ops", "research", "skill")

REMOVED_SHIM_PATHS = [
    "advisor/",
    "services/quant_service.py",
    "services/quant_interpret.py",
    "services/quant_report_export.py",
    "services/quant_report_index.py",
    "services/signal_config_preview.py",
    "services/portfolio_quant_bridge.py",
    "services/daily_presets.py",
    "services/daily_health.py",
    "services/eval_routing_map.py",
    "research/factor_report.py",
    "skills/quant/engine.py",
    "skills/quant/handler.py",
]

CANONICAL_IMPORTS = {
    "QuantService": "quant.services.quant_service.QuantService",
    "build_daily_health": "quant.ops.daily_health.build_daily_health",
    "build_eval_routing_map": "quant.ops.eval_routing_map.build_eval_routing_map",
    "compute_factor_ic_report": "quant.research.factor_report.compute_factor_ic_report",
    "summarize_portfolio_backtest": "quant.research.portfolio_data.summarize_portfolio_backtest",
    "QuantEngine": "quant.skill.engine.QuantEngine",
    "QuantHandler": "quant.skill.handler.QuantHandler",
}


def _list_py_modules(subpkg: str) -> List[str]:
    path = os.path.join(QUANT_ROOT, subpkg)
    if not os.path.isdir(path):
        return []
    out: List[str] = []
    for name in sorted(os.listdir(path)):
        if name.endswith(".py") and not name.startswith("_"):
            out.append(name[:-3])
    return out


def build_quant_package_info() -> Dict[str, Any]:
    modules = {sp: _list_py_modules(sp) for sp in SUBPACKAGES}
    readme = build_readme_index()
    return {
        "success": True,
        "package": "quant",
        "layout": "P28+",
        "subpackages": list(SUBPACKAGES),
        "modules": modules,
        "module_count": sum(len(v) for v in modules.values()),
        "canonical_imports": CANONICAL_IMPORTS,
        "shim_paths": [],
        "shims_removed": True,
        "removed_shim_paths": REMOVED_SHIM_PATHS,
        "readme_index": {
            "total_dirs": readme["total_dirs"],
            "present_count": readme["present_count"],
            "coverage_ok": readme["coverage_ok"],
        },
        "shared_core": ["core/signal", "core/backtest"],
        "note": "P41+ 仅 import quant.*；各子目录见 README.md 与 GET /api/readme-index。",
    }
