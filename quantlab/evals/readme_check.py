"""子目录 README 覆盖校验（P44，CI / evals 共用）。"""

from __future__ import annotations

from typing import Any, Dict, List

from core.readme_index import (
    ARCHITECTURE_SECTION_ANCHOR,
    REPO_README_DIRS,
    build_readme_index,
    read_repo_readme,
)


def check_readme_coverage() -> Dict[str, Any]:
    idx = build_readme_index()
    failures: List[str] = list(idx.get("missing") or [])
    marker = f"architecture.md#{ARCHITECTURE_SECTION_ANCHOR}"

    for rel in REPO_README_DIRS:
        if rel in (idx.get("missing") or []):
            continue
        out = read_repo_readme(rel)
        if not out.get("success"):
            failures.append(f"{rel}: README not readable")
            continue
        if marker not in out.get("content", ""):
            failures.append(f"{rel}: missing architecture backlink")

    return {
        "success": True,
        "ok": not failures,
        "total_dirs": idx["total_dirs"],
        "present_count": idx["present_count"],
        "missing_count": len(failures),
        "failures": failures,
        "entries": idx.get("entries") or [],
        "coverage_ok": idx.get("coverage_ok"),
    }
