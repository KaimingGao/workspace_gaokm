"""模型 / 特征指纹辅助（不改 live_config_manifest 的 artifact 键名）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Optional, Sequence


def sha1_short(data: bytes, *, n: int = 16) -> str:
    return hashlib.sha1(data).hexdigest()[: max(1, int(n))]


def file_content_fingerprint(path: str) -> Dict[str, Any]:
    """与 ``live_config_manifest._file_fingerprint`` 同形的文件指纹（独立副本）。"""
    import os

    if not path or not os.path.isfile(path):
        return {
            "path": path,
            "exists": False,
            "sha1": None,
            "mtime": None,
            "bytes": None,
        }
    try:
        st = os.stat(path)
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return {
            "path": path,
            "exists": True,
            "sha1": h.hexdigest()[:16],
            "mtime": int(st.st_mtime),
            "bytes": int(st.st_size),
        }
    except OSError as e:
        return {
            "path": path,
            "exists": True,
            "sha1": None,
            "error": str(e)[:120],
        }


def feature_keys_fingerprint(keys: Iterable[str]) -> str:
    """稳定特征键集合指纹（排序后拼接）。"""
    norm = sorted({str(k).strip() for k in keys if str(k).strip()})
    return sha1_short("|".join(norm).encode("utf-8"))


def model_doc_fingerprint(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """模型 JSON 文档内容指纹（排序键序列化）；空文档返回 None。"""
    if not isinstance(doc, dict) or not doc:
        return None
    try:
        payload = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted(doc.keys()))
    return sha1_short(payload.encode("utf-8"))


def coefficients_fingerprint(
    coefficients: Optional[Dict[str, Any]],
    *,
    intercept: Any = None,
) -> Optional[str]:
    """Ridge β / 截距指纹（忽略无关元数据）。"""
    if not isinstance(coefficients, dict) and intercept is None:
        return None
    body = {
        "coefficients": dict(coefficients or {}),
        "intercept": intercept,
    }
    try:
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    return sha1_short(payload.encode("utf-8"))


def yhat_feature_pack_fingerprint() -> Dict[str, str]:
    """当前包内特征键清单指纹（便于诊断 signal/research 是否同键）。"""
    from core.yhat.keys import (
        CO_LAG_FEATURES,
        CO_Z_FEATURES,
        CO_Z_LEGACY,
        TAU_LAG_FEATURES,
        TAU_Z_FEATURES,
    )

    packs: Dict[str, Sequence[str]] = {
        "TAU_LAG_FEATURES": TAU_LAG_FEATURES,
        "TAU_Z_FEATURES": TAU_Z_FEATURES,
        "CO_LAG_FEATURES": CO_LAG_FEATURES,
        "CO_Z_FEATURES": CO_Z_FEATURES,
        "CO_Z_LEGACY": CO_Z_LEGACY,
    }
    return {name: feature_keys_fingerprint(keys) for name, keys in packs.items()}


__all__ = [
    "coefficients_fingerprint",
    "feature_keys_fingerprint",
    "file_content_fingerprint",
    "model_doc_fingerprint",
    "sha1_short",
    "yhat_feature_pack_fingerprint",
]
