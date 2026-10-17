"""L1/L2 特征列注册：稳定列名 → registry fingerprint。

语义变更时抬 ``FEATURE_SCHEMA``，使 Feature Lake / DayCube 全量 miss。
"""

from __future__ import annotations

import hashlib
from typing import Optional, Sequence

# 特征语义 / 列集合不兼容时人工 +1
FEATURE_SCHEMA = 1


def registry_fingerprint(
    names: Optional[Sequence[str]] = None,
    *,
    family: str = "",
    extra: str = "",
) -> str:
    """列名表 + schema + family → 短指纹。"""
    cols = [str(n) for n in (names or []) if str(n or "").strip()]
    parts = [
        f"schema={FEATURE_SCHEMA}",
        f"family={str(family or '').strip()}",
        f"extra={str(extra or '').strip()}",
        "cols=" + ",".join(cols),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def universe_hash(codes: Optional[Sequence[str]]) -> str:
    """截面宇宙指纹：排序去重后 hash（与名单顺序无关）。"""
    uniq = sorted({str(c).strip() for c in (codes or []) if str(c or "").strip()})
    if not uniq:
        return "empty"
    return hashlib.sha1(",".join(uniq).encode("utf-8")).hexdigest()[:12]
