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
        return None


def build_live_config_manifest(*, note: str = "") -> Dict[str, Any]:
    """扫描 live 相关落盘，返回清单 + 一致性 alerts（不写盘）。

    分组 OO 指针/权重已退役（``cluster_retired``）；仍指纹 signal_config /
    return_model / paper 等非分组真源。
    """
    from core.paths import (
        LIVE_DIR,
        PAPER_PATH,
        PREDICTABILITY_TIERS_ACTIVE_PATH,
        PREDICTABILITY_TIERS_LAST_PATH,
        RETURN_SCORE_MODEL_ACTIVE_PATH,
        SIGNAL_CONFIG_PATH,
    )
    from core.signal.config import get_signal_config_path

    promoted_path = os.path.join(
        os.path.dirname(SIGNAL_CONFIG_PATH), "strategy_promoted.json"
    )
    signal_path = get_signal_config_path()

    _retired_art = {
        "path": None,
        "exists": False,
        "sha1": None,
        "mtime": None,
        "bytes": None,
        "retired": True,
        "error": "cluster_retired",
    }
    artifacts = {
        "signal_config": _file_fingerprint(signal_path),
        "cluster_pointer": dict(_retired_art),
        "cluster_weights_active": dict(_retired_art),
        "cluster_weights_mirror": dict(_retired_art),
        "cluster_book_active": dict(_retired_art),
        "return_score_model_active": _file_fingerprint(RETURN_SCORE_MODEL_ACTIVE_PATH),
        "predictability_tiers_last": _file_fingerprint(PREDICTABILITY_TIERS_LAST_PATH),
        "predictability_tiers_active": _file_fingerprint(PREDICTABILITY_TIERS_ACTIVE_PATH),
        "strategy_promoted": _file_fingerprint(promoted_path),
        "paper": _file_fingerprint(PAPER_PATH),
    }

    cfg = _read_json(signal_path) or {}
    cs = cfg.get("cluster_scoring") or {}
    mode = "off"
    scoring = cfg.get("scoring") or {}
    paper = _read_json(PAPER_PATH) or {}
    paper_rules = paper.get("rules") if isinstance(paper.get("rules"), dict) else {}

    alerts: List[str] = ["cluster_retired"]
    if bool(cs.get("enabled")) or str(cs.get("mode") or "off").strip().lower() not in (
        "",
        "off",
    ):
        alerts.append("signal_config.cluster_scoring 仍非 off（分组已退役，可清理）")
    if bool(paper_rules.get("cluster_mode")):
        alerts.append("paper.rules.cluster_mode 已无调仓含义（分组已退役）")

    return {
        "success": True,
        "schema_version": 1,
        "built_at": now_iso_utc(),
        "note": str(note or "")[:300],
        "cluster_retired": True,
        "cluster_scoring_mode": mode,
        "cluster_scoring_enabled": False,
        "scoring": {
            "rank_mode": scoring.get("rank_mode") or "predicted_score",
            "min_predicted_score": scoring.get("min_predicted_score"),
            "min_hold_predicted_score": scoring.get("min_hold_predicted_score"),
        },
        "cluster_weights_version": None,
        "cluster_weights_promoted_at": None,
        "n_mapped_codes": None,
        "cluster_book_names": None,
        "paper_cluster_mode": bool(paper_rules.get("cluster_mode")),
        "artifacts": artifacts,
        "alerts": alerts,
        "consistent": False,
        "live_dir": LIVE_DIR,
    }


def live_config_manifest_path() -> str:
    """清单路径跟随当时的 ``LIVE_DIR``（测试可 patch），不使用导入时绑定的常量。"""
    from core.paths import LIVE_CONFIG_MANIFEST_PATH, LIVE_DIR

    return os.path.join(LIVE_DIR, os.path.basename(LIVE_CONFIG_MANIFEST_PATH))


def write_live_config_manifest(*, note: str = "") -> Dict[str, Any]:
    """构建并写入 ``data/live/live_config_manifest.json``。"""
    from core.io_atomic import atomic_write_json
    from core.paths import LIVE_DIR

    manifest = build_live_config_manifest(note=note)
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = live_config_manifest_path()
    atomic_write_json(path, manifest)
    manifest["path"] = path
    return manifest


def load_live_config_manifest() -> Optional[Dict[str, Any]]:
    return _read_json(live_config_manifest_path())
