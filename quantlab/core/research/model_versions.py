"""ŷ 四头研究/执行套：按拟合时间归档版本，供卡头下拉切回。

每头每槽保留最近 ``KEEP`` 份（默认 10）；落盘成功后归档；切版本 = 覆盖当前槽文件。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.io_atomic import atomic_write_json
from core.paths import LIVE_DIR
from core.research.holdout import (
    MODEL_ROLE_LIVE,
    MODEL_ROLE_RESEARCH,
    model_fit_id,
    normalize_model_role,
    research_model_path,
)

logger = logging.getLogger(__name__)

SUITE_HEADS = ("oo", "tau", "oo_rank", "tc_rank")
KEEP = 10
# 残缺包（冒烟/误拟合）不进版本库、不晋级槽；满池通常 ≥50 万行
MIN_ARCHIVE_SAMPLES = 500
MIN_ARCHIVE_FEATURES = 3
MODEL_VERSIONS_DIR = os.path.join(LIVE_DIR, "model_versions")

_SAFE_VID = re.compile(r"[^0-9A-Za-z._+-]+")


def _doc_sample_count(doc: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(doc, dict):
        return None
    for raw in (
        doc.get("sample_count"),
        ((doc.get("return_model") or {}) if isinstance(doc.get("return_model"), dict) else {}).get(
            "sample_count"
        ),
        ((doc.get("model") or {}) if isinstance(doc.get("model"), dict) else {}).get(
            "sample_count"
        ),
    ):
        try:
            if raw is None:
                continue
            n = int(raw)
            if n >= 0:
                return n
        except (TypeError, ValueError):
            continue
    return None


def _doc_n_features(doc: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(doc, dict):
        return None
    for blob in (doc.get("return_model"), doc.get("model"), doc):
        if not isinstance(blob, dict):
            continue
        names = blob.get("feature_names")
        if isinstance(names, (list, tuple)) and names:
            return len(names)
        fi = blob.get("feature_importance")
        if isinstance(fi, list) and fi:
            return len(fi)
        coefs = blob.get("coefficients")
        if isinstance(coefs, dict) and coefs:
            return len(coefs)
    return None


_TOY_BOOSTER_B64 = frozenset(
    {
        "dGVzdA==",  # base64("test")
        "dGVzdA",
        "test",
        "TEST",
    }
)


def _doc_looks_toy_booster(doc: Optional[Dict[str, Any]]) -> bool:
    """单测/冒烟留下的伪 booster（如 ``dGVzdA==``）。"""
    if not isinstance(doc, dict):
        return False
    blobs = [doc.get("return_model"), doc.get("model"), doc]
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        candidates = [blob.get("booster_b64"), blob.get("payload_b64")]
        booster = blob.get("booster")
        if isinstance(booster, dict):
            candidates.extend(
                [booster.get("payload_b64"), booster.get("booster_b64")]
            )
        for raw in candidates:
            if isinstance(raw, str) and raw.strip() in _TOY_BOOSTER_B64:
                return True
    return False


def suite_doc_incomplete_reason(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """残缺模型原因；完整则 ``None``。"""
    if not isinstance(doc, dict):
        return "not a model doc"
    n = _doc_sample_count(doc)
    if n is None:
        return "missing sample_count"
    if n < int(MIN_ARCHIVE_SAMPLES):
        return f"sample_count={n} < {MIN_ARCHIVE_SAMPLES}"
    n_feat = _doc_n_features(doc)
    if n_feat is not None and n_feat < int(MIN_ARCHIVE_FEATURES):
        return f"n_features={n_feat} < {MIN_ARCHIVE_FEATURES}"
    if _doc_looks_toy_booster(doc):
        return "toy booster payload"
    return None


def is_suite_doc_complete(doc: Optional[Dict[str, Any]]) -> bool:
    return suite_doc_incomplete_reason(doc) is None


def refuse_incomplete_suite_doc(
    doc: Optional[Dict[str, Any]],
    *,
    sink: str = "模型套",
) -> Optional[Dict[str, Any]]:
    """残缺包拒写研究/执行槽；完整则 ``None``。"""
    why = suite_doc_incomplete_reason(doc)
    if not why:
        return None
    return {
        "success": False,
        "error": f"残缺模型不写入{sink}（{why}）；请重新拟合",
        "incomplete": True,
    }


def normalize_suite_head(head: Any) -> str:
    h = str(head or "").strip().lower()
    if h in ("oo", "return", "return_score", "ŷ_oo", "y_oo"):
        return "oo"
    if h in ("tau", "tc", "τc", "y_tc", "ŷ_τc", "tau_lgb"):
        return "tau"
    if h in ("oo_rank", "oorank", "ŷ_oo_rank", "y_oo_rank"):
        return "oo_rank"
    if h in ("tc_rank", "tau_rank", "tcrank", "ŷ_τc_rank", "y_tc_rank"):
        return "tc_rank"
    return h


def _role(role: Any) -> str:
    return normalize_model_role(role)


def suite_slot_path(head: str, role: str) -> str:
    """当前槽位盘路径。"""
    h = normalize_suite_head(head)
    r = _role(role)
    if h == "oo":
        from core.paths import (
            RETURN_SCORE_MODEL_ACTIVE_PATH,
            RETURN_SCORE_MODEL_RESEARCH_PATH,
        )

        return (
            RETURN_SCORE_MODEL_RESEARCH_PATH
            if r == MODEL_ROLE_RESEARCH
            else RETURN_SCORE_MODEL_ACTIVE_PATH
        )
    if h == "tau":
        from core.research.tc_model import tau_model_path

        live = tau_model_path()
        return research_model_path(live) if r == MODEL_ROLE_RESEARCH else live
    if h == "oo_rank":
        from core.research.oo_rank_lambdarank import oo_rank_model_path

        live = oo_rank_model_path()
        return research_model_path(live) if r == MODEL_ROLE_RESEARCH else live
    if h == "tc_rank":
        from core.research.tc_rank import tc_rank_model_path

        live = tc_rank_model_path()
        return research_model_path(live) if r == MODEL_ROLE_RESEARCH else live
    raise ValueError(f"unknown suite head: {head!r}")


def versions_dir(head: str, role: str) -> str:
    return os.path.join(MODEL_VERSIONS_DIR, normalize_suite_head(head), _role(role))


def _index_path(head: str, role: str) -> str:
    return os.path.join(versions_dir(head, role), "index.json")


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_index(head: str, role: str) -> List[Dict[str, Any]]:
    raw = _load_json(_index_path(head, role))
    items = (raw or {}).get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(dict(it))
    return out


def _write_index(head: str, role: str, items: List[Dict[str, Any]]) -> None:
    d = versions_dir(head, role)
    os.makedirs(d, exist_ok=True)
    atomic_write_json(
        _index_path(head, role),
        {"version": 1, "head": normalize_suite_head(head), "role": _role(role), "items": items},
    )


def version_id_for_doc(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """用拟合时间作版本 id（下拉展示同一时间戳）。同秒重拟合会覆盖旧档。"""
    if not isinstance(doc, dict):
        return None
    stamp = model_fit_id(doc) or doc.get("promoted_at")
    if not stamp:
        return None
    base = _SAFE_VID.sub("_", str(stamp)).strip("._") or "unknown"
    return base[:96]


def _meta_from_doc(doc: Dict[str, Any], version_id: str) -> Dict[str, Any]:
    stamp = model_fit_id(doc)
    return {
        "id": version_id,
        "fitted_at": stamp,
        "promoted_at": doc.get("promoted_at"),
        "backend": doc.get("backend")
        or doc.get("recommended_backend")
        or ((doc.get("return_model") or {}) if isinstance(doc.get("return_model"), dict) else {}).get(
            "backend"
        ),
        "sample_count": doc.get("sample_count")
        or ((doc.get("return_model") or {}) if isinstance(doc.get("return_model"), dict) else {}).get(
            "sample_count"
        )
        or ((doc.get("model") or {}) if isinstance(doc.get("model"), dict) else {}).get(
            "sample_count"
        ),
    }


def archive_suite_version(
    head: str,
    role: str,
    doc: Optional[Dict[str, Any]],
    *,
    keep: int = KEEP,
    allow_incomplete: bool = False,
) -> Optional[str]:
    """把刚落盘的槽文档归档；同 id 覆盖。返回 version id。

    默认跳过残缺包（样本过少 / 特征过少），避免冒烟拟合污染版本下拉。
    """
    if not isinstance(doc, dict):
        return None
    h = normalize_suite_head(head)
    if h not in SUITE_HEADS:
        return None
    if not allow_incomplete:
        why = suite_doc_incomplete_reason(doc)
        if why:
            logger.info(
                "skip archive incomplete head=%s role=%s reason=%s",
                h,
                _role(role),
                why,
            )
            return None
    r = _role(role)
    vid = version_id_for_doc(doc)
    if not vid:
        return None
    d = versions_dir(h, r)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{vid}.json")
    atomic_write_json(path, doc)
    items = [it for it in _read_index(h, r) if it.get("id") != vid]
    items.insert(0, _meta_from_doc(doc, vid))
    # 裁剪
    drop = items[keep:]
    items = items[:keep]
    for old in drop:
        oid = str(old.get("id") or "")
        if not oid:
            continue
        try:
            os.remove(os.path.join(d, f"{oid}.json"))
        except OSError:
            pass
    _write_index(h, r, items)
    return vid


def ensure_current_archived(head: str, role: str) -> Optional[str]:
    """若当前槽有模型但未进版本列表，补归档。"""
    h = normalize_suite_head(head)
    r = _role(role)
    path = suite_slot_path(h, r)
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return None
    # 残缺槽不补归档
    if suite_doc_incomplete_reason(doc):
        return None
    vid = version_id_for_doc(doc)
    if not vid:
        return None
    items = _read_index(h, r)
    if any(it.get("id") == vid for it in items):
        return vid
    # 宽松：同 fitted_at 已在列表则跳过（避免重复指纹）
    stamp = model_fit_id(doc)
    if stamp and any(it.get("fitted_at") == stamp for it in items):
        return str(next(it["id"] for it in items if it.get("fitted_at") == stamp))
    return archive_suite_version(h, r, doc)


def delete_suite_version(head: str, role: str, version_id: str) -> Dict[str, Any]:
    """从版本库删除指定 id（不改当前槽文件）。

    保护：当前槽版本不可删；列表至少保留 1 个。
    """
    h = normalize_suite_head(head)
    r = _role(role)
    if h not in SUITE_HEADS:
        return {"success": False, "error": f"unknown head: {head!r}"}
    vid = str(version_id or "").strip()
    if not vid or ".." in vid or "/" in vid or "\\" in vid:
        return {"success": False, "error": "invalid version_id"}
    items_before = _read_index(h, r)
    if not any(str(it.get("id") or "") == vid for it in items_before):
        return {"success": False, "error": "版本不存在或已清理", "version_id": vid}
    if len(items_before) <= 1:
        return {
            "success": False,
            "error": "至少保留一个版本，不能删空列表",
            "version_id": vid,
        }
    slot_doc = _load_json(suite_slot_path(h, r))
    cur_id = version_id_for_doc(slot_doc) if isinstance(slot_doc, dict) else None
    if cur_id and str(cur_id) == vid:
        return {
            "success": False,
            "error": "当前槽版本不可删；请先启用其它版本",
            "version_id": vid,
            "current": True,
        }
    d = versions_dir(h, r)
    path = os.path.join(d, f"{vid}.json")
    items = [it for it in items_before if str(it.get("id") or "") != vid]
    removed = False
    if os.path.isfile(path):
        try:
            os.remove(path)
            removed = True
        except OSError as exc:
            return {"success": False, "error": str(exc), "version_id": vid}
    _write_index(h, r, items)
    return {
        "success": True,
        "head": h,
        "role": r,
        "version_id": vid,
        "removed_file": removed,
        "items_left": len(items),
    }


def purge_incomplete_versions(head: str, role: str) -> Dict[str, Any]:
    """扫版本库，删掉残缺包（样本/特征过少）。

    残缺包不受「至少保留 1 个 / 当前槽不可删」保护：直接去文件并移出索引，
    避免 delete 失败后索引已脏、磁盘仍留孤儿 JSON。
    """
    h = normalize_suite_head(head)
    r = _role(role)
    if h not in SUITE_HEADS:
        return {"success": False, "error": f"unknown head: {head!r}", "removed": []}
    removed: List[str] = []
    kept: List[Dict[str, Any]] = []
    d = versions_dir(h, r)
    for it in _read_index(h, r):
        vid = str(it.get("id") or "")
        if not vid:
            continue
        doc = load_version_doc(h, r, vid)
        why = suite_doc_incomplete_reason(doc if isinstance(doc, dict) else None)
        if why:
            path = os.path.join(d, f"{vid}.json")
            try:
                if os.path.isfile(path):
                    os.remove(path)
                removed.append(vid)
            except OSError:
                # 删不掉则仍留在索引，下次再试
                kept.append(it)
            continue
        kept.append(it)
    _write_index(h, r, kept)
    return {
        "success": True,
        "head": h,
        "role": r,
        "removed": removed,
        "kept": len(kept),
    }


def list_suite_versions(head: str, role: str) -> Dict[str, Any]:
    h = normalize_suite_head(head)
    r = _role(role)
    if h not in SUITE_HEADS:
        return {"success": False, "error": f"unknown head: {head!r}", "items": []}
    # 列表前清掉历史残缺包，避免下拉再冒烟
    purged = purge_incomplete_versions(h, r)
    current_id = ensure_current_archived(h, r)
    items = _read_index(h, r)
    slot = suite_slot_path(h, r)
    cur_doc = _load_json(slot)
    cur_fit = model_fit_id(cur_doc) if cur_doc else None
    slot_incomplete = suite_doc_incomplete_reason(cur_doc) if cur_doc else None
    if current_id is None and cur_doc and not slot_incomplete:
        current_id = version_id_for_doc(cur_doc)
    return {
        "success": True,
        "head": h,
        "role": r,
        "current_id": current_id,
        "current_fitted_at": cur_fit,
        "slot_path": slot,
        "slot_incomplete": slot_incomplete,
        "purged_incomplete": purged.get("removed") or [],
        "items": items,
    }


def load_version_doc(head: str, role: str, version_id: str) -> Optional[Dict[str, Any]]:
    vid = str(version_id or "").strip()
    if not vid or ".." in vid or "/" in vid or "\\" in vid:
        return None
    path = os.path.join(versions_dir(head, role), f"{vid}.json")
    return _load_json(path)


_HEAVY_MODEL_KEYS = frozenset(
    {
        "booster",
        "booster_b64",
        "model_dump",
        "lgb_model",
        "tree_dump",
        "trees",
        "payload_b64",
    }
)


def _slim_model_blob(blob: Any) -> Any:
    """面板预览用：去掉 booster 等大字段，保留因子表 / OOS 所需元数据。"""
    if not isinstance(blob, dict):
        return blob
    out = {k: v for k, v in blob.items() if k not in _HEAVY_MODEL_KEYS}
    # nested booster 偶发挂在子 dict
    for nest in ("return_model", "model", "lightgbm", "lambdarank"):
        if isinstance(out.get(nest), dict):
            out[nest] = _slim_model_blob(out[nest])
    return out


def preview_suite_version(head: str, role: str, version_id: str) -> Dict[str, Any]:
    """读历史版本（不写槽）；返回瘦身后的面板预览包。"""
    h = normalize_suite_head(head)
    r = _role(role)
    if h not in SUITE_HEADS:
        return {"success": False, "error": f"unknown head: {head!r}"}
    vid = str(version_id or "").strip()
    doc = load_version_doc(h, r, vid)
    if not isinstance(doc, dict):
        return {"success": False, "error": "版本不存在或已清理", "version_id": vid}
    why = suite_doc_incomplete_reason(doc)
    if why:
        return {
            "success": False,
            "error": f"残缺版本不可预览（{why}）；请删除或重新拟合",
            "version_id": vid,
            "incomplete": True,
        }
    rm = doc.get("return_model") or doc.get("model")
    fi = None
    if isinstance(rm, dict) and isinstance(rm.get("feature_importance"), list):
        fi = rm.get("feature_importance")
    elif isinstance(doc.get("feature_importance"), list):
        fi = doc.get("feature_importance")
    backends = doc.get("backends")
    slim_backends = None
    if isinstance(backends, dict):
        slim_backends = {
            str(k): _slim_model_blob(v) if isinstance(v, dict) else v
            for k, v in backends.items()
        }
        if fi is None:
            for side in slim_backends.values():
                if not isinstance(side, dict):
                    continue
                cand = side.get("feature_importance") or (
                    (side.get("return_model") or {}).get("feature_importance")
                    if isinstance(side.get("return_model"), dict)
                    else None
                )
                if isinstance(cand, list) and cand:
                    fi = cand
                    break
    slim_rm = _slim_model_blob(rm) if isinstance(rm, dict) else None
    if isinstance(slim_rm, dict) and fi is not None and "feature_importance" not in slim_rm:
        slim_rm = dict(slim_rm)
        slim_rm["feature_importance"] = fi
    y_spec = None
    if isinstance(doc.get("y_spec"), dict):
        y_spec = dict(doc["y_spec"])
    elif isinstance(slim_rm, dict) and isinstance(slim_rm.get("y_spec"), dict):
        y_spec = dict(slim_rm["y_spec"])
    return {
        "success": True,
        "head": h,
        "role": r,
        "version_id": vid,
        "fitted_at": model_fit_id(doc),
        "promoted_at": doc.get("promoted_at"),
        "backend": doc.get("backend") or doc.get("recommended_backend"),
        "sample_count": doc.get("sample_count")
        or (slim_rm.get("sample_count") if isinstance(slim_rm, dict) else None),
        "oos": doc.get("oos") if isinstance(doc.get("oos"), dict) else None,
        "return_model": slim_rm,
        "feature_importance": fi,
        "backends": slim_backends,
        "y_spec": y_spec,
        "n_train_days": doc.get("n_train_days"),
        "n_test_days": doc.get("n_test_days"),
        "holdout_trading_days": doc.get("holdout_trading_days"),
        "tau_end_hm": doc.get("tau_end_hm"),
        "preview": True,
    }


def restore_suite_version(head: str, role: str, version_id: str) -> Dict[str, Any]:
    """把历史版本写回当前槽（覆盖）；先归档当前槽。"""
    h = normalize_suite_head(head)
    r = _role(role)
    if h not in SUITE_HEADS:
        return {"success": False, "error": f"unknown head: {head!r}"}
    vid = str(version_id or "").strip()
    doc = load_version_doc(h, r, vid)
    if not isinstance(doc, dict):
        return {"success": False, "error": "版本不存在或已清理", "version_id": vid}
    why = suite_doc_incomplete_reason(doc)
    if why:
        return {
            "success": False,
            "error": f"残缺版本不可启用（{why}）；请删除或重新拟合",
            "version_id": vid,
            "incomplete": True,
        }
    slot = suite_slot_path(h, r)
    # 先把当前槽收进历史，避免切走丢失（残缺槽会跳过归档）
    cur = _load_json(slot)
    if isinstance(cur, dict):
        archive_suite_version(h, r, cur)
    os.makedirs(os.path.dirname(slot) or ".", exist_ok=True)
    atomic_write_json(slot, doc)
    _invalidate_suite_model_caches(slot)
    archive_suite_version(h, r, doc)
    return {
        "success": True,
        "head": h,
        "role": r,
        "version_id": vid,
        "path": slot,
        "fitted_at": model_fit_id(doc),
        "promoted_at": doc.get("promoted_at"),
    }


def _invalidate_suite_model_caches(slot_path: str) -> None:
    """版本写回 / 落盘后失效各头进程内模型缓存（缺模块则跳过）。"""
    path = str(slot_path or "")
    if not path:
        return
    try:
        from core.research.tc_model import _JSON_MODEL_CACHE

        _JSON_MODEL_CACHE.pop(path, None)
    except Exception:
        logger.debug("tau model cache invalidate skipped", exc_info=True)
    # 预留：其它头若日后加路径缓存，在此统一 pop
    for mod_name in (
        "core.research.oo_model",
        "core.research.oo_rank_lambdarank",
        "core.research.tc_rank",
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            cache = getattr(mod, "_JSON_MODEL_CACHE", None)
            if isinstance(cache, dict):
                cache.pop(path, None)
        except Exception:
            logger.debug("%s cache invalidate skipped", mod_name, exc_info=True)


def try_archive_after_persist(
    head: str,
    role: str,
    persist_out: Optional[Dict[str, Any]],
) -> Optional[str]:
    """persist_* 成功后：读刚写的槽文件并归档。"""
    if not isinstance(persist_out, dict) or not persist_out.get("success"):
        return None
    h = normalize_suite_head(head)
    r = _role(role or persist_out.get("model_role") or persist_out.get("role"))
    path = str(persist_out.get("path") or suite_slot_path(h, r))
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return None
    _invalidate_suite_model_caches(path)
    return archive_suite_version(h, r, doc)


def archive_persist_safe(
    head: str,
    role: str,
    persist_out: Optional[Dict[str, Any]],
) -> Optional[str]:
    """落盘归档；失败只打日志，不挡主路径。"""
    try:
        return try_archive_after_persist(head, role, persist_out)
    except Exception:
        logger.exception("model version archive failed head=%s role=%s", head, role)
        return None
