"""分组 Job 结果水合：从 last_report / 指纹缓存补全 clusters（抗热重载）。

属 core 能力：job_progress 不得反向依赖 quant.services。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, Optional

from core.paths import CLUSTER_LAST_REPORT_PATH, CLUSTER_REPORT_CACHE_PATH

logger = logging.getLogger(__name__)


def _unpack_cluster_report(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, TypeError):
        logger.debug("cluster report read failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict):
        return None
    report = doc.get("report") if isinstance(doc.get("report"), dict) else doc
    if not isinstance(report, dict) or not report.get("success"):
        return None
    if not isinstance(report.get("clusters"), list) or not report.get("clusters"):
        return None
    try:
        out = copy.deepcopy(report)
    except Exception:
        out = dict(report)
    out["cache_created_at"] = (
        doc.get("saved_at")
        or doc.get("cache_created_at")
        or doc.get("created_at")
        or report.get("cache_created_at")
    )
    out["hydrated_from_cache"] = True
    try:
        from core.signal.factor_taxonomy import strip_removed_factors_from_cluster_report

        strip_removed_factors_from_cluster_report(out)
    except Exception:
        logger.debug("strip removed factors failed", exc_info=True)
    return out


def load_latest_cluster_report() -> Optional[Dict[str, Any]]:
    """读最近一次成功分组：last_report → 指纹缓存。"""
    last = _unpack_cluster_report(CLUSTER_LAST_REPORT_PATH)
    if last is not None:
        last["restored_from"] = "last_report"
        return last
    cached = _unpack_cluster_report(CLUSTER_REPORT_CACHE_PATH)
    if cached is not None:
        cached["restored_from"] = "fingerprint_cache"
        return cached
    return None


def hydrate_ols_clusters_job_result(
    result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Job 落盘摘要缺 ``clusters`` 时，从最近报告补全。"""
    if not isinstance(result, dict):
        return result
    clusters = result.get("clusters")
    if isinstance(clusters, list) and len(clusters) > 0:
        return result
    if not (
        result.get("persisted_artifact")
        or result.get("success")
        or result.get("ok")
    ):
        return result
    cached = load_latest_cluster_report()
    if not cached:
        return result
    cached = dict(cached)
    cached["hydrated_from_job_stub"] = True
    cached["job_stub_n_clusters"] = result.get("n_clusters")
    for k in ("cache_hit", "cache_age_hours", "oos_summary"):
        if result.get(k) is not None and cached.get(k) is None:
            cached[k] = result.get(k)
    return cached
