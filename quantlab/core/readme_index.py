"""仓库子目录 README 覆盖索引（P42）与内容读取（P43）。"""


import os
from typing import Any, Dict, List, Optional

from core.paths import ROOT_DIR

ARCHITECTURE_SECTION_ANCHOR = "子目录-readme-索引"

# 需要 README.md 的代码/数据目录（相对 quantlab/ 根）
# 仅保留顶层入口 + 少数有独立对外价值的目录；子级详情统一见 docs/architecture.md
REPO_README_DIRS: tuple[str, ...] = (
    "agent",
    "core",
    "data",
    "data/reports",
    "docs",
    "evals",
    "quant",
    "research",
    "scripts",
    "services",
    "skills",
    "tests",
    "web",
)

_README_DIR_SET = frozenset(REPO_README_DIRS)


def docs_relative_prefix(rel_dir: str) -> str:
    depth = rel_dir.count("/") + 1
    return "../" * depth


def architecture_doc_href(rel_dir: str) -> str:
    return f"{docs_relative_prefix(rel_dir)}docs/architecture.md#{ARCHITECTURE_SECTION_ANCHOR}"


def architecture_link_line(rel_dir: str) -> str:
    return f"- [架构总览 · 子目录索引]({architecture_doc_href(rel_dir)})"


def normalize_readme_dir(rel_dir: str) -> str:
    norm = (rel_dir or "").strip().strip("/").replace("\\", "/")
    if not norm or norm not in _README_DIR_SET:
        raise ValueError(f"unknown readme dir: {rel_dir!r}")
    return norm


def readme_doc_links(rel_dir: str) -> List[Dict[str, str]]:
    prefix = docs_relative_prefix(rel_dir)
    return [
        {
            "label": "架构总览 · 子目录索引",
            "href": f"{prefix}docs/architecture.md#{ARCHITECTURE_SECTION_ANCHOR}",
        },
        {"label": "文档索引", "href": f"{prefix}docs/README.md"},
    ]


def read_repo_readme(rel_dir: str, *, root: Optional[str] = None) -> Dict[str, Any]:
    base = root or ROOT_DIR
    norm = normalize_readme_dir(rel_dir)
    path = os.path.join(base, norm, "README.md")
    if not os.path.isfile(path):
        return {"success": False, "dir": norm, "error": "README not found"}
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return {
        "success": True,
        "dir": norm,
        "path": f"{norm}/README.md",
        "content": content,
        "doc_links": readme_doc_links(norm),
        "api_url": f"/api/readme?dir={norm}",
    }


def build_readme_index(*, root: Optional[str] = None) -> Dict[str, Any]:
    base = root or ROOT_DIR
    present: List[str] = []
    missing: List[str] = []
    entries: List[Dict[str, Any]] = []

    for rel in REPO_README_DIRS:
        readme = os.path.join(base, rel, "README.md")
        ok = os.path.isfile(readme)
        if ok:
            present.append(rel)
        else:
            missing.append(rel)
        entries.append(
            {
                "dir": rel,
                "readme_path": f"{rel}/README.md",
                "api_url": f"/api/readme?dir={rel}",
                "present": ok,
            }
        )

    total = len(REPO_README_DIRS)
    return {
        "success": True,
        "total_dirs": total,
        "present_count": len(present),
        "missing_count": len(missing),
        "coverage_ok": not missing,
        "present": present,
        "missing": missing,
        "entries": entries,
        "architecture_anchor": ARCHITECTURE_SECTION_ANCHOR,
        "note": "各子目录 README 说明职责与入口；Web 运维区可点击浏览。",
    }
