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
    """从 active 集群书冻结 ŷ；决策日对齐因子截止（见 resolve_freeze_as_of）。

    优先冻 ``scored_all``（打分宇宙，供校准 g(ŷ) 全轴拟合）；无则回退 ``book``。
    行上打 ``in_book``：复盘 UI / 命中率仍默认只看簿内。

    ``auto=True``：刷簿附带冻结，盘中跳过。
    ``force=True``：绕过收盘闸（测试 / 显式重建）。
    """
    from core.signal.cluster.live import load_active_cluster_book

    # 自动路径：收盘前直接跳过（不解析、不写盘）
    if auto and not force:
        gate0 = session_allows_ledger_freeze(auto=True, force=False)
        if not gate0.get("ok"):
            resolved = resolve_freeze_as_of(
                as_of, book_doc=book_doc if isinstance(book_doc, dict) else None
            )
            return {
                "success": False,
                "skipped": True,
                "error": gate0.get("reason") or "盘中跳过冻结",
                "as_of": resolved.get("as_of"),
                "n_rows": 0,
                "resolve": resolved,
                "gate": gate0,
            }

    doc = book_doc if isinstance(book_doc, dict) else load_active_cluster_book()
    if not doc:
        resolved = resolve_freeze_as_of(as_of, book_doc=None)
        return {
            "success": False,
            "error": "无集群书",
            "as_of": resolved.get("as_of"),
            "n_rows": 0,
            "resolve": resolved,
        }
    book = list(doc.get("book") or [])
    scored_all = list(doc.get("scored_all") or [])
    book_codes = {
        str(r.get("stock_code") or r.get("code") or "").strip()
        for r in book
        if isinstance(r, dict) and str(r.get("stock_code") or r.get("code") or "").strip()
    }
    universe = scored_all if scored_all else book
    freeze_rows: List[dict] = []
    for item in universe:
        if not isinstance(item, dict):
            continue
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if not code:
            continue
        row = dict(item)
        row["in_book"] = (code in book_codes) if book_codes else True
        freeze_rows.append(row)
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    resolved = resolve_freeze_as_of(as_of, book_doc=doc)
    d = date_key(resolved.get("as_of"))
    if not d:
        return {
            "success": False,
            "error": "无法解析冻结决策日",
            "n_rows": 0,
            "resolve": resolved,
        }
    # 显式/日更：按解析后的决策日再闸（禁止盘中冻「当日」半日 K）
    gate = session_allows_ledger_freeze(as_of=d, auto=False, force=force)
    if not gate.get("ok"):
        return {
            "success": False,
            "skipped": True,
            "error": gate.get("reason") or "盘中跳过冻结",
            "as_of": d,
            "n_rows": 0,
            "resolve": resolved,
            "gate": gate,
        }
    src = "cluster_scored_all" if scored_all else "cluster_book"
    out = _lio.upsert_ledger_rows(
        d,
        freeze_rows,
        source=src,
        meta={
            "cluster_version": meta.get("version") or doc.get("version"),
            "book_updated_at": doc.get("updated_at"),
            "feature_as_of": resolved.get("feature_as_of"),
            "session_date": resolved.get("session_date"),
            "freeze_remapped": bool(resolved.get("remapped")),
            "freeze_note": resolved.get("note"),
            "freeze_universe": "scored_all" if scored_all else "book",
            "n_book": len(book_codes),
            "n_universe": len(freeze_rows),
            "freeze_gate": gate.get("reason"),
        },
    )
    out["resolve"] = resolved
    out["gate"] = gate
    if resolved.get("note"):
        out["note"] = resolved.get("note")
    prev = date_key(resolved.get("prev_trading_day"))
    out["skipped_newer"] = prev if prev and d and prev > d else None
    # 同步冻结 τ 影子簿成员（失败不影响主账本）
    try:
        shadow_out = freeze_from_tau_shadow_book(as_of=d, auto=False, force=force)
        out["tau_shadow"] = {
            "success": shadow_out.get("success"),
            "skipped": shadow_out.get("skipped"),
            "n_rows": shadow_out.get("n_rows"),
            "path": shadow_out.get("path"),
            "error": shadow_out.get("error"),
        }
    except Exception as exc:
        logger.exception('unexpected error in freeze_from_cluster_book')
        out["tau_shadow"] = {"success": False, "error": str(exc)}
    try:
        nc_out = freeze_from_nowcast_shadow_book(as_of=d, auto=False, force=force)
        out["nowcast_shadow"] = {
            "success": nc_out.get("success"),
            "skipped": nc_out.get("skipped"),
            "n_rows": nc_out.get("n_rows"),
            "path": nc_out.get("path"),
            "error": nc_out.get("error"),
        }
    except Exception as exc:
        logger.exception('unexpected error in freeze_from_cluster_book')
        out["nowcast_shadow"] = {"success": False, "error": str(exc)}
    return out


def freeze_from_tau_shadow_book(
    *,
    as_of: Optional[str] = None,
    shadow_doc: Optional[dict] = None,
    auto: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """冻结 A2 τ 影子簿成员与 ŷ_τ（独立文件，不覆盖 EOD 账本）。"""
    from core.signal.cluster.live import load_tau_shadow_cluster_book

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

    doc = shadow_doc if isinstance(shadow_doc, dict) else load_tau_shadow_cluster_book()
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
        y_eod = _lio._to_float(item.get("predicted_score_eod"))
        if y_eod is None:
            y_eod = _lio._to_float(item.get("predicted_score"))
        if y_eod is None:
            y_eod = _lio._to_float(item.get("score"))
        rows.append(
            {
                "as_of": d,
                "code": code.zfill(6) if code.isdigit() else code,
                "name": item.get("stock_name") or item.get("name"),
                "rank": item.get("rank") or (i + 1),
                "rank_key": "predicted_score_tau",
                "yhat_tau": round(y_tau, 6) if y_tau is not None else None,
                "yhat_eod": round(y_eod, 6) if y_eod is not None else None,
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
            "note": "A2 影子成员快照；不对账单一 y_EOD",
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
    """冻结 N3 nowcast 影子簿成员与 ŷ_nowcast（独立文件）。"""
    from core.signal.cluster.live import load_nowcast_shadow_cluster_book

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

    doc = shadow_doc if isinstance(shadow_doc, dict) else load_nowcast_shadow_cluster_book()
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
        y_eod = _lio._to_float(item.get("predicted_score_eod"))
        if y_eod is None:
            y_eod = _lio._to_float(item.get("predicted_score"))
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
                "yhat_eod": round(y_eod, 6) if y_eod is not None else None,
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
    """从日报里的 book_top / cross_section 补写账本。"""
    d_hint = date_key(as_of)
    if not d_hint:
        gen = str((report or {}).get("generated_at") or "")
        d_hint = date_key(gen) or None
    rows: List[dict] = []
    cl = report.get("cluster_live") if isinstance(report.get("cluster_live"), dict) else {}
    for r in cl.get("book_top") or []:
        if isinstance(r, dict):
            rows.append(r)
    cs = report.get("cross_section") if isinstance(report.get("cross_section"), dict) else {}
    for r in cs.get("items") or cs.get("ranked") or []:
        if isinstance(r, dict):
            rows.append(r)
    if not rows:
        # 仍尝试刷书（走因子截止解析）
        return freeze_from_cluster_book(as_of=d_hint)
    codes = [
        str(r.get("stock_code") or r.get("code") or "").strip()
        for r in rows
        if isinstance(r, dict)
    ]
    resolved = resolve_freeze_as_of(d_hint, codes=codes)
    d = date_key(resolved.get("as_of"))
    if not d:
        return {
            "success": False,
            "error": "无法解析冻结决策日",
            "n_rows": 0,
            "resolve": resolved,
        }
    out = _lio.upsert_ledger_rows(
        d,
        rows,
        source="daily_report",
        meta={
            "from_daily": True,
            "feature_as_of": resolved.get("feature_as_of"),
            "session_date": resolved.get("session_date"),
            "freeze_remapped": bool(resolved.get("remapped")),
            "freeze_note": resolved.get("note"),
        },
    )
    out["resolve"] = resolved
    if resolved.get("note"):
        out["note"] = resolved.get("note")
    return out

