"""运行清单（Q3）：每次回测 / 调仓写出可复现指纹。"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
from core.numbers import now_iso_local as _now_iso

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from core.paths import DATA_DIR
from core.io_atomic import atomic_write_json

MANIFEST_DIR = os.path.join(DATA_DIR, "run_manifests")


def _hash_obj(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_run_manifest(
    *,
    kind: str,
    strategy_id: Optional[str] = None,
    strategy_version: Optional[str] = None,
    cost_model: Optional[str] = None,
    rules: Optional[dict] = None,
    extra: Optional[dict] = None,
    data_quality: Optional[dict] = None,
    adjust_policy: Optional[str] = None,
) -> Dict[str, Any]:
    from core.data_service import DEFAULT_ADJUST_POLICY

    dq = data_quality or (extra or {}).get("data_quality") or {}
    policy = adjust_policy or dq.get("adjust_policy") or DEFAULT_ADJUST_POLICY
    body = {
        "kind": kind,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "cost_model": cost_model or "zero",
        "adjust_policy": policy,
        "rules": rules or {},
        "data_quality": dq,
        "extra": extra or {},
    }
    return {
        "ts": _now_iso(),
        "kind": kind,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "cost_model": body["cost_model"],
        "adjust_policy": policy,
        "fingerprint": _hash_obj(body),
        "rules_fingerprint": _hash_obj(rules or {}),
        "data_quality": dq,
        "extra": extra or {},
    }


def write_run_manifest(manifest: Dict[str, Any], *, dir_path: Optional[str] = None) -> str:
    d = dir_path or MANIFEST_DIR
    os.makedirs(d, exist_ok=True)
    kind = str(manifest.get("kind") or "run")
    fp = str(manifest.get("fingerprint") or "na")
    ts = str(manifest.get("ts") or _now_iso()).replace(":", "").replace("-", "")
    path = os.path.join(d, f"{kind}_{ts}_{fp}.json")
    atomic_write_json(path, manifest)
    return path
