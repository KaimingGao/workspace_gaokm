"""检测 quant 兼容 shim 的非法 import（P39，非破坏性守卫）。"""

import ast
import os
from typing import Any, Dict, Iterable, List, Optional, Set

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEGACY_MODULES = frozenset(
    {
        "advisor",
        "services.quant_service",
        "services.quant_interpret",
        "services.quant_report_export",
        "services.quant_report_index",
        "services.signal_config_preview",
        "services.portfolio_quant_bridge",
        "services.daily_presets",
        "services.daily_health",
        "services.eval_routing_map",
        "research.factor_report",
        "research.paper_vs_backtest",
        "research.paper_vs_portfolio",
    }
)

ALLOWLIST_SUFFIXES = (
    "/quant/ops/shim_audit.py",
)


def _is_allowlisted(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(norm.endswith(suffix) for suffix in ALLOWLIST_SUFFIXES)


def _iter_py_files(root: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git", "data", "store"}]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _imports_in_file(path: str) -> Set[str]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
    return found


def audit_quant_shim_imports(*, root: Optional[str] = None) -> Dict[str, Any]:
    base = root or ROOT_DIR
    offenders: List[Dict[str, str]] = []
    scanned = 0

    for path in _iter_py_files(base):
        if _is_allowlisted(path):
            continue
        scanned += 1
        rel = os.path.relpath(path, base)
        for mod in sorted(_imports_in_file(path)):
            if mod in LEGACY_MODULES or any(
                mod.startswith(prefix + ".") for prefix in LEGACY_MODULES
            ):
                offenders.append({"file": rel, "module": mod})

    return {
        "success": True,
        "ok": not offenders,
        "scanned_files": scanned,
        "offender_count": len(offenders),
        "offenders": offenders,
        "legacy_modules": sorted(LEGACY_MODULES),
        "note": "P41+ shim 已删除（含 advisor/）；新代码请 import quant.* / agent.*，legacy import 一律禁止。",
    }


def main(argv: Optional[List[str]] = None) -> int:
    del argv
    out = audit_quant_shim_imports()
    if not out["ok"]:
        print(f"quant shim import audit FAIL: {out['offender_count']} issue(s)")
        for row in out["offenders"]:
            print(f"  - {row['file']}: {row['module']}")
        return 1
    print(f"quant shim import audit OK ({out['scanned_files']} files scanned)")
    return 0

if __name__ == "__main__":
    import sys

    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    raise SystemExit(main())
