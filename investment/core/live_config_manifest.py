"""Live 配置清单：多 JSON 真源的指纹与一致性检查（H3）。

不替代各 artifact 落盘；在 promote / 改 mode / 刷新簿后写入
``data/live/live_config_manifest.json``，便于发现「有权重但 mode=off」等半成功态。
"""

import logging

logger = logging.getLogger(__name__)
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from core.numbers import now_iso_utc


def _file_fingerprint(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {"path": path, "exists": False, "sha1": None, "mtime": None, "bytes": None}
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
    except Exception as e:
        logger.exception('unexpected error in _file_fingerprint')
        return {"path": path, "exists": True, "sha1": None, "error": str(e)[:120]}


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in live_config_manifest.py", exc_info=True)
        return None


def build_live_config_manifest(*, note: str = "") -> Dict[str, Any]:
    """扫描 live 相关落盘，返回清单 + 一致性 alerts（不写盘）。"""
    from core.paths import (
        CLUSTER_BOOK_ACTIVE_PATH,
        CLUSTER_POINTER_PATH,
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        LIVE_DIR,
        PAPER_PATH,
        RETURN_SCORE_MODEL_ACTIVE_PATH,
        SIGNAL_CONFIG_PATH,
    )
    from core.signal.cluster.pointer import load_cluster_pointer, resolve_cluster_weights_path
    from core.signal.config import get_signal_config_path

    promoted_path = os.path.join(
        os.path.dirname(SIGNAL_CONFIG_PATH), "strategy_promoted.json"
    )
    signal_path = get_signal_config_path()
    resolved_weights = resolve_cluster_weights_path()

    artifacts = {
        "signal_config": _file_fingerprint(signal_path),
        "cluster_pointer": _file_fingerprint(CLUSTER_POINTER_PATH),
        "cluster_weights_active": _file_fingerprint(
            resolved_weights or CLUSTER_WEIGHTS_ACTIVE_PATH
        ),
        "cluster_weights_mirror": _file_fingerprint(CLUSTER_WEIGHTS_ACTIVE_PATH),
        "cluster_book_active": _file_fingerprint(CLUSTER_BOOK_ACTIVE_PATH),
        "return_score_model_active": _file_fingerprint(RETURN_SCORE_MODEL_ACTIVE_PATH),
        "strategy_promoted": _file_fingerprint(promoted_path),
        "paper": _file_fingerprint(PAPER_PATH),
    }

    cfg = _read_json(signal_path) or {}
    cs = cfg.get("cluster_scoring") or {}
    mode = str(cs.get("mode") or "off").strip().lower()
    scoring = cfg.get("scoring") or {}

    weights = _read_json(resolved_weights) if resolved_weights else {}
    if not weights:
        weights = _read_json(CLUSTER_WEIGHTS_ACTIVE_PATH) or {}
    book = _read_json(CLUSTER_BOOK_ACTIVE_PATH) or {}
    paper = _read_json(PAPER_PATH) or {}
    paper_rules = paper.get("rules") if isinstance(paper.get("rules"), dict) else {}
    ptr = load_cluster_pointer()

    alerts: List[str] = []
    has_weights = bool(resolved_weights) or bool(
        artifacts["cluster_weights_mirror"].get("exists")
    )
    if mode in ("shadow", "active") and not has_weights:
        alerts.append(f"cluster_scoring.mode={mode} 但无 cluster_weights")
    if has_weights and mode == "off":
        alerts.append("有 cluster_weights 但 mode=off（半晋升：须人审设 shadow/active）")
    if bool(paper_rules.get("cluster_mode")) and mode == "off":
        alerts.append("paper.rules.cluster_mode 已无调仓含义；组 ŷ 看 cluster_scoring.mode")
    if os.path.isfile(CLUSTER_POINTER_PATH) and not resolved_weights:
        alerts.append("cluster_pointer 存在但 artifact 不可读（指针残缺）")
    if ptr and resolved_weights:
        art = str(ptr.get("artifact") or "")
        if art and os.path.basename(resolved_weights) not in (
            os.path.basename(art),
            art,
        ) and os.path.abspath(resolved_weights) != os.path.abspath(art):
            # 指针与解析路径不一致（罕见）
            if os.path.basename(art) not in os.path.basename(resolved_weights):
                alerts.append("cluster_pointer.artifact 与解析路径不一致")
    n_mapped = weights.get("n_mapped_codes")
    book_n = len(book.get("book") or []) if isinstance(book.get("book"), list) else None

    return {
        "success": True,
        "schema_version": 1,
        "built_at": now_iso_utc(),
        "note": str(note or "")[:300],
        "cluster_scoring_mode": mode,
        "cluster_scoring_enabled": bool(cs.get("enabled", mode != "off")),
        "scoring": {
            "rank_mode": scoring.get("rank_mode") or "predicted_score",
            "min_predicted_score": scoring.get("min_predicted_score"),
            "min_hold_predicted_score": scoring.get("min_hold_predicted_score"),
        },
        "cluster_weights_version": weights.get("version"),
        "cluster_weights_promoted_at": weights.get("promoted_at"),
        "n_mapped_codes": n_mapped,
        "cluster_book_names": book_n,
        "paper_cluster_mode": bool(paper_rules.get("cluster_mode")),
        "artifacts": artifacts,
        "alerts": alerts,
        "consistent": len(alerts) == 0,
        "live_dir": LIVE_DIR,
    }


def write_live_config_manifest(*, note: str = "") -> Dict[str, Any]:
    """构建并写入 ``data/live/live_config_manifest.json``。"""
    from core.io_atomic import atomic_write_json
    from core.paths import LIVE_CONFIG_MANIFEST_PATH, LIVE_DIR

    manifest = build_live_config_manifest(note=note)
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = LIVE_CONFIG_MANIFEST_PATH
    atomic_write_json(path, manifest)
    manifest["path"] = path
    return manifest


def load_live_config_manifest() -> Optional[Dict[str, Any]]:
    from core.paths import LIVE_DIR

    path = os.path.join(LIVE_DIR, "live_config_manifest.json")
    return _read_json(path)
