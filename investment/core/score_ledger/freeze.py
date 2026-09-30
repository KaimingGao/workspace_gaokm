"""打分账本：从分池簿 / 影子簿 / 日报冻结 ŷ。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.score_ledger import io as _lio
from core.io_atomic import atomic_write_json
from core.numbers import date_key
from core.score_ledger.asof import (
    resolve_freeze_as_of,
    session_allows_ledger_freeze,
)


def freeze_from_cluster_book(
    *,
    as_of: Optional[str] = None,
    book_doc: Optional[dict] = None,
    auto: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """分池簿已停用：不再从集群书冻结 ŷ。"""
    _ = as_of, book_doc, auto, force
    return {
        "success": False,
        "deprecated": True,
        "error": "分池簿已停用；账本冻结请改用其他数据源",
        "n_rows": 0,
    }


def freeze_from_tau_shadow_book(
    *,
    as_of: Optional[str] = None,
    shadow_doc: Optional[dict] = None,
    auto: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """冻结 A2 τ 影子簿成员与 ŷ_τ（独立文件，不覆盖 EOD 账本）。

    分组 τ 影子簿加载已退役；仅接受显式 ``shadow_doc``。
    """
    gate = session_allows_ledger_freeze(as_of=as_of, auto=auto, force=force)
    if not gate.get("ok"):
        return {
            "success": False,
            "skipped": True,
            "error": gate.get("reason") or "盘中跳过冻结",
            "as_of": date_key(as_of) if as_of else None,
            "n_rows": 0,
            "gate": gate,
        }

    doc = shadow_doc if isinstance(shadow_doc, dict) else None
    if not doc:
        return {
            "success": False,
            "error": "无 τ 影子簿",
            "as_of": date_key(as_of) if as_of else None,
            "n_rows": 0,
        }
    book = list(doc.get("book") or [])
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    # 与 EOD 书共用 resolve：优先显式 as_of，否则用影子 meta / 书时间
    resolved = resolve_freeze_as_of(as_of, book_doc={"book": book, "meta": meta, **doc})
    d = date_key(resolved.get("as_of") or as_of)
    if not d:
        return {
            "success": False,
            "error": "无法解析冻结决策日",
            "n_rows": 0,
            "resolve": resolved,
        }
    rows: List[Dict[str, Any]] = []
    for i, item in enumerate(book):
        if not isinstance(item, dict):
            continue
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if not code:
            continue
        y_tau = _lio._to_float(item.get("predicted_score_tau"))
        if y_tau is None:
            y_tau = _lio._to_float(item.get("score_rem"))
        if y_tau is None:
            y_tau = _lio._to_float(item.get("predicted_score"))
        y_oo = _lio._to_float(item.get("predicted_score_eod"))
        if y_oo is None:
            y_oo = _lio._to_float(item.get("predicted_score"))
        if y_oo is None:
            y_oo = _lio._to_float(item.get("score"))
        rows.append(
            {
                "as_of": d,
                "code": code.zfill(6) if code.isdigit() else code,
                "name": item.get("stock_name") or item.get("name"),
                "rank": item.get("rank") or (i + 1),
                "rank_key": "predicted_score_tau",
                "yhat_tau": round(y_tau, 6) if y_tau is not None else None,
                "yhat_eod": round(y_oo, 6) if y_oo is not None else None,
                "cluster_label": item.get("cluster_label"),
                "sector": item.get("sector"),
            }
        )
    path = _lio.tau_shadow_membership_path(d)
    os.makedirs(_lio.ledger_dir(), exist_ok=True)
    payload = {
        "success": True,
        "as_of": d,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": len(rows),
        "rows": rows,
        "meta": {
            "source": "tau_shadow_book",
            "cluster_version": meta.get("version"),
            "vs_eod_book": meta.get("vs_eod_book"),
            "book_updated_at": doc.get("updated_at"),
            "feature_as_of": resolved.get("feature_as_of"),
            "freeze_note": resolved.get("note"),
            "note": "A2 影子成员快照；不对账单一 y_oo",
        },
    }
    atomic_write_json(path, payload)
    return {
        "success": True,
        "as_of": d,
        "n_rows": len(rows),
        "path": path,
        "resolve": resolved,
        "vs_eod_book": meta.get("vs_eod_book"),
    }


def freeze_from_nowcast_shadow_book(
    *,
    as_of: Optional[str] = None,
    shadow_doc: Optional[dict] = None,
    auto: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """冻结 N3 nowcast 影子簿成员与 ŷ_nowcast（独立文件）。

    分组 nowcast 影子簿加载已退役；仅接受显式 ``shadow_doc``。
    """
    gate = session_allows_ledger_freeze(as_of=as_of, auto=auto, force=force)
    if not gate.get("ok"):
        return {
            "success": False,
            "skipped": True,
            "error": gate.get("reason") or "盘中跳过冻结",
            "as_of": date_key(as_of) if as_of else None,
            "n_rows": 0,
            "gate": gate,
        }

    doc = shadow_doc if isinstance(shadow_doc, dict) else None
    if not doc:
        return {
            "success": False,
            "error": "无 nowcast 影子簿",
            "as_of": date_key(as_of) if as_of else None,
            "n_rows": 0,
        }
    book = list(doc.get("book") or [])
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    resolved = resolve_freeze_as_of(as_of, book_doc={"book": book, "meta": meta, **doc})
    d = date_key(resolved.get("as_of") or as_of)
    if not d:
        return {
            "success": False,
            "error": "无法解析冻结决策日",
            "n_rows": 0,
            "resolve": resolved,
        }
    rows: List[Dict[str, Any]] = []
    for i, item in enumerate(book):
        if not isinstance(item, dict):
            continue
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if not code:
            continue
        y_n = _lio._to_float(item.get("predicted_score_nowcast"))
        y_tau = _lio._to_float(item.get("predicted_score_tau"))
        if y_tau is None:
            y_tau = _lio._to_float(item.get("score_rem"))
        y_oo = _lio._to_float(item.get("predicted_score_eod"))
        if y_oo is None:
            y_oo = _lio._to_float(item.get("predicted_score"))
        rows.append(
            {
                "as_of": d,
                "code": code.zfill(6) if code.isdigit() else code,
                "name": item.get("stock_name") or item.get("name"),
                "rank": item.get("rank") or (i + 1),
                "rank_key": "predicted_score_nowcast",
                "yhat_nowcast": round(y_n, 6) if y_n is not None else None,
                "nowcast_as_of": item.get("nowcast_as_of"),
                "nowcast_K": _lio._to_float(item.get("nowcast_K")),
                "nowcast_q": _lio._to_float(item.get("nowcast_q")),
                "nowcast_x_prior": _lio._to_float(item.get("nowcast_x_prior")),
                "yhat_tau": round(y_tau, 6) if y_tau is not None else None,
                "yhat_eod": round(y_oo, 6) if y_oo is not None else None,
                "cluster_label": item.get("cluster_label"),
                "sector": item.get("sector"),
            }
        )
    path = _lio.nowcast_shadow_membership_path(d)
    os.makedirs(_lio.ledger_dir(), exist_ok=True)
    payload = {
        "success": True,
        "as_of": d,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": len(rows),
        "rows": rows,
        "meta": {
            "source": "nowcast_shadow_book",
            "cluster_version": meta.get("version"),
            "vs_eod_book": meta.get("vs_eod_book"),
            "nordhaus_revision_slope": meta.get("nordhaus_revision_slope"),
            "book_updated_at": doc.get("updated_at"),
            "feature_as_of": resolved.get("feature_as_of"),
            "freeze_note": resolved.get("note"),
            "note": "N3 nowcast 影子成员快照；对账 ŷ_nowcast↔涨跌（昨收口径），不进决策",
        },
    }
    atomic_write_json(path, payload)
    return {
        "success": True,
        "as_of": d,
        "n_rows": len(rows),
        "path": path,
        "resolve": resolved,
        "vs_eod_book": meta.get("vs_eod_book"),
    }


def load_tau_shadow_membership(as_of: str) -> Dict[str, Any]:
    path = _lio.tau_shadow_membership_path(as_of)
    if not os.path.isfile(path):
        return {
            "success": True,
            "empty": True,
            "as_of": date_key(as_of),
            "rows": [],
            "path": path,
        }
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "success": False,
            "error": str(e),
            "as_of": date_key(as_of),
            "rows": [],
            "path": path,
        }
    rows = list(data.get("rows") or []) if isinstance(data, dict) else []
    return {
        "success": True,
        "empty": not bool(rows),
        "as_of": date_key(as_of) or (data.get("as_of") if isinstance(data, dict) else None),
        "rows": rows,
        "meta": (data.get("meta") if isinstance(data, dict) else None) or {},
        "path": path,
        "updated_at": data.get("updated_at") if isinstance(data, dict) else None,
    }


def load_nowcast_shadow_membership(as_of: str) -> Dict[str, Any]:
    path = _lio.nowcast_shadow_membership_path(as_of)
    if not os.path.isfile(path):
        return {
            "success": True,
            "empty": True,
            "as_of": date_key(as_of),
            "rows": [],
            "path": path,
        }
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "success": False,
            "error": str(e),
            "as_of": date_key(as_of),
            "rows": [],
            "path": path,
        }
    rows = list(data.get("rows") or []) if isinstance(data, dict) else []
    return {
        "success": True,
        "empty": not bool(rows),
        "as_of": date_key(as_of) or (data.get("as_of") if isinstance(data, dict) else None),
        "rows": rows,
        "meta": (data.get("meta") if isinstance(data, dict) else None) or {},
        "path": path,
        "updated_at": data.get("updated_at") if isinstance(data, dict) else None,
    }


def freeze_from_daily_report(
    report: dict,
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """日报写账本已停用（分档改 ŷ_oo Holdout OOS）。"""
    _ = report, as_of
    return {
        "success": False,
        "deprecated": True,
        "n_rows": 0,
        "error": "score_ledger 冻结已停用；日报不再写账本",
    }

