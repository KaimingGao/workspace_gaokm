"""打分账本：路径、读写、upsert、列举与删除。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.numbers import date_key

_YHAT_EPS = 0.05  # |ŷ| < ε → 无方向
_LEDGER_DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")

def ledger_dir() -> str:
    from core.paths import SCORE_LEDGER_DIR

    return SCORE_LEDGER_DIR


def ledger_path(as_of: str) -> str:
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.json")


def outcomes_path(as_of: str) -> str:
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.outcomes.json")


def tau_shadow_membership_path(as_of: str) -> str:
    """A2：ŷ_τ 影子簿成员快照（与 EOD 账本分文件，避免覆盖）。"""
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.tau_shadow.json")


def nowcast_shadow_membership_path(as_of: str) -> str:
    """N3：Kalman nowcast 影子簿成员快照。"""
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.nowcast_shadow.json")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _terms_top(raw: Any, *, limit: int = 5) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        terms = list(raw.get("terms") or [])
    elif isinstance(raw, list):
        terms = list(raw)
    else:
        terms = []
    out: List[Dict[str, Any]] = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        key = str(t.get("key") or t.get("factor") or "").strip()
        if not key:
            continue
        out.append(
            {
                "key": key,
                "label": t.get("label") or key,
                "beta": _to_float(t.get("beta")),
                "z": _to_float(t.get("z")),
                "contrib": _to_float(t.get("contrib")),
            }
        )
    out.sort(key=lambda x: -abs(float(x.get("contrib") or 0.0)))
    return out[: max(1, int(limit))]


def row_from_scored_item(
    item: dict,
    *,
    as_of: str,
    source: str = "scored",
) -> Optional[Dict[str, Any]]:
    """从 signal_item / 书行 / 横截面行抽出账本行。"""
    if not isinstance(item, dict):
        return None
    code = str(item.get("stock_code") or item.get("code") or "").strip()
    if not code:
        return None
    yhat = _to_float(item.get("predicted_score"))
    if yhat is None:
        yhat = _to_float(item.get("yhat"))
    if yhat is None:
        yhat = _to_float(item.get("predicted_score_blend"))
    if yhat is None:
        yhat = _to_float(item.get("score_cluster"))
    if yhat is None:
        cand = _to_float(item.get("score"))
        if cand is not None and abs(cand) <= 20.0:
            yhat = cand
    # 无收益分 ŷ 时，允许 heuristic 0–100 进 yhat（展示/对照），但不得进 yhat_eod
    heuristic_as_yhat = False
    if yhat is not None and abs(float(yhat)) > 20.0:
        # 脏 heuristic 误入 predicted_score → 丢弃
        yhat = None
    if yhat is None:
        hs = _to_float(item.get("heuristic_score"))
        if hs is None:
            hs = _to_float(item.get("score"))
        if hs is not None and abs(float(hs)) > 20.0:
            yhat = float(hs)
            heuristic_as_yhat = True
    if yhat is None:
        return None
    # ŷ_EOD 必须是收益分口径（%），禁止用 heuristic 0–100 填
    yhat_eod = None
    if not heuristic_as_yhat:
        yhat_eod = _to_float(item.get("predicted_score_eod"))
        if yhat_eod is None:
            pred = _to_float(item.get("predicted_score"))
            if pred is not None and abs(pred) <= 20.0:
                yhat_eod = pred
        if yhat_eod is not None and abs(float(yhat_eod)) > 20.0:
            yhat_eod = None
    yhat_tau = _to_float(item.get("predicted_score_tau"))
    if yhat_tau is None:
        yhat_tau = _to_float(item.get("score_rem"))
    if yhat_tau is None:
        yhat_tau = _to_float(item.get("yhat_tau"))
    if yhat_tau is not None and abs(float(yhat_tau)) > 20.0:
        yhat_tau = None
    yhat_eod_rem = _to_float(item.get("predicted_score_eod_rem"))
    if yhat_eod_rem is not None and abs(float(yhat_eod_rem)) > 20.0:
        yhat_eod_rem = None
    y_check = item.get("y_check")
    if y_check is not None:
        y_check = str(y_check)
    y_disagree = _to_float(item.get("y_disagree"))
    eod_trust = _to_float(item.get("eod_trust"))
    if not y_check:
        try:
            from core.signal.y_state import build_y_state

            st = build_y_state(item)
            y_check = st.get("check")
            if y_disagree is None:
                y_disagree = _to_float(st.get("disagree"))
            if eod_trust is None:
                eod_trust = _to_float(st.get("eod_trust"))
            if yhat_eod_rem is None:
                yhat_eod_rem = _to_float((st.get("heads") or {}).get("eod_rem"))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            pass
    yhat_nowcast = _to_float(item.get("predicted_score_nowcast"))
    if yhat_nowcast is None:
        yhat_nowcast = _to_float(item.get("yhat_nowcast"))
    if yhat_nowcast is not None and abs(float(yhat_nowcast)) > 20.0:
        yhat_nowcast = None
    yhat_path = _to_float(item.get("predicted_score_path"))
    if yhat_path is None:
        yhat_path = _to_float(item.get("yhat_path"))
    if yhat_path is None:
        yhat_path = _to_float(item.get("y_path"))
    # path 头标签 ±100，勿用 EOD 的 >20 守卫
    if yhat_path is not None and abs(float(yhat_path)) > 120.0:
        yhat_path = None
    terms = _terms_top(
        item.get("score_formula_terms") or item.get("formula_terms_top")
    )
    sector = (
        item.get("sector")
        or item.get("industry")
        or item.get("theme_sector")
        or None
    )
    if sector is not None:
        sector = str(sector).strip() or None
    if not sector:
        try:
            from core.portfolio_optimize import _sector_for, load_sector_map

            sector = _sector_for(code, load_sector_map()) or None
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            sector = None
    heuristic = _to_float(item.get("heuristic_score"))
    if heuristic is None:
        sc_legacy = _to_float(item.get("score"))
        if sc_legacy is not None and abs(sc_legacy) > 20.0:
            heuristic = sc_legacy
    return {
        "as_of": date_key(as_of),
        "code": code.zfill(6) if code.isdigit() else code,
        "name": item.get("stock_name") or item.get("name"),
        "yhat": round(yhat, 6),
        "yhat_eod": round(yhat_eod, 6) if yhat_eod is not None else None,
        "yhat_eod_rem": round(yhat_eod_rem, 6) if yhat_eod_rem is not None else None,
        "yhat_tau": round(yhat_tau, 6) if yhat_tau is not None else None,
        "yhat_nowcast": (
            round(yhat_nowcast, 6) if yhat_nowcast is not None else None
        ),
        "yhat_path": round(yhat_path, 4) if yhat_path is not None else None,
        "y_check": y_check,
        "y_disagree": round(y_disagree, 6) if y_disagree is not None else None,
        "eod_trust": round(eod_trust, 4) if eod_trust is not None else None,
        "nowcast_as_of": item.get("nowcast_as_of"),
        "nowcast_vs": item.get("nowcast_vs"),
        "nowcast_K": _to_float(item.get("nowcast_K")),
        "nowcast_P": _to_float(item.get("nowcast_P")),
        "nowcast_x_prior": _to_float(item.get("nowcast_x_prior")),
        "heuristic": heuristic,
        "cluster_label": item.get("cluster_label"),
        "sector": sector,
        "model_id": item.get("return_model_source")
        or item.get("weight_source")
        or item.get("model_id"),
        "rank": item.get("rank") or item.get("rank_in_group"),
        "rank_key": item.get("rank_key"),
        "formula_terms_top": terms,
        "gap_pct": _to_float(item.get("gap_pct")),
        "open_price_for_tau_label": _to_float(
            item.get("open_price_for_tau_label") or item.get("open")
        ),
        "source": str(source or "scored"),
        "in_book": (
            bool(item.get("in_book"))
            if item.get("in_book") is not None
            else None
        ),
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }


def rows_for_book_review(rows: Optional[Sequence[dict]]) -> List[dict]:
    """复盘默认只看簿内行；无 ``in_book`` 标记的旧账本原样返回。

    校准拟合用全量 ``ledger.rows``（打分宇宙），与复盘截断刻意分轨。
    """
    out = [r for r in (rows or []) if isinstance(r, dict)]
    if not out:
        return []
    if not any(r.get("in_book") is not None for r in out):
        return out
    book_only = [r for r in out if r.get("in_book") is True]
    return book_only if book_only else out


def load_ledger(as_of: str) -> Dict[str, Any]:
    path = ledger_path(as_of)
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
        "as_of": date_key(as_of) or data.get("as_of"),
        "rows": rows,
        "meta": (data.get("meta") if isinstance(data, dict) else None) or {},
        "path": path,
        "updated_at": data.get("updated_at") if isinstance(data, dict) else None,
    }


def load_outcomes(as_of: str) -> Dict[str, Any]:
    path = outcomes_path(as_of)
    if not os.path.isfile(path):
        return {
            "success": True,
            "empty": True,
            "as_of": date_key(as_of),
            "by_code": {},
            "path": path,
        }
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"success": False, "error": str(e), "by_code": {}, "path": path}
    by_code = data.get("by_code") if isinstance(data, dict) else {}
    if not isinstance(by_code, dict):
        by_code = {}
    return {
        "success": True,
        "empty": not bool(by_code),
        "as_of": date_key(as_of),
        "by_code": by_code,
        "horizon_days": data.get("horizon_days") if isinstance(data, dict) else None,
        "path": path,
        "updated_at": data.get("updated_at") if isinstance(data, dict) else None,
    }


def upsert_ledger_rows(
    as_of: str,
    rows: Sequence[Dict[str, Any]],
    *,
    source: str = "upsert",
    meta: Optional[dict] = None,
) -> Dict[str, Any]:
    """按 code 覆盖写入当日账本。"""
    d = date_key(as_of)
    if not d:
        return {"success": False, "error": "as_of 无效", "n_rows": 0}
    os.makedirs(ledger_dir(), exist_ok=True)
    existing = load_ledger(d)
    by_code: Dict[str, Dict[str, Any]] = {}
    for r in existing.get("rows") or []:
        if isinstance(r, dict) and r.get("code"):
            by_code[str(r["code"])] = dict(r)
    n_in = 0
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = row_from_scored_item(raw, as_of=d, source=source)
        if row is None:
            continue
        by_code[str(row["code"])] = row
        n_in += 1
    ordered = sorted(
        by_code.values(),
        key=lambda r: (
            0 if r.get("rank") is not None else 1,
            int(r["rank"]) if isinstance(r.get("rank"), (int, float)) else 10**9,
            -float(r.get("yhat") or 0),
        ),
    )
    path = ledger_path(d)
    payload = {
        "success": True,
        "as_of": d,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": len(ordered),
        "rows": ordered,
        "meta": {
            **(existing.get("meta") or {}),
            **(meta or {}),
            "last_source": source,
        },
    }
    atomic_write_json(path, payload)
    return {
        "success": True,
        "as_of": d,
        "n_rows": len(ordered),
        "n_upserted": n_in,
        "path": path,
    }


def list_ledger_dates(*, limit: int = 30) -> List[str]:
    root = ledger_dir()
    if not os.path.isdir(root):
        return []
    dates = []
    for name in os.listdir(root):
        # 只收 YYYY-MM-DD.json；跳过 outcomes / τ·nowcast 影子等 sidecar
        if not _LEDGER_DATE_FILE_RE.match(name):
            continue
        dates.append(name[:-5])
    dates.sort(reverse=True)
    return dates[: max(1, int(limit))]


def list_ledger_entries(*, limit: int = 30) -> List[Dict[str, Any]]:
    """已冻结账本条目：日期 / 票数 / 是否已回填 / 是否未到期。"""
    from core.market.calendar import next_trading_day, resolve_session_date

    sess = resolve_session_date()
    out: List[Dict[str, Any]] = []
    for d in list_ledger_dates(limit=limit):
        led = load_ledger(d)
        rows = list(led.get("rows") or []) if led.get("success") else []
        oc = load_outcomes(d)
        by_code = oc.get("by_code") or {} if oc.get("success") else {}
        filled = sum(
            1
            for v in by_code.values()
            if isinstance(v, dict) and v.get("realized_h") is not None
        )
        # 会话日当天的账本：h 未到期，复盘默认勿选
        immature = bool(d and sess and d >= sess)
        need_h1 = None
        try:
            need_h1 = next_trading_day(d, n=1) if d else None
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            need_h1 = None
        pending_close = bool(
            need_h1 and sess and need_h1 >= sess and int(filled) == 0
        )
        meta = led.get("meta") if isinstance(led.get("meta"), dict) else {}
        out.append(
            {
                "as_of": d,
                "n_rows": len(rows),
                "has_outcomes": bool(by_code),
                "outcomes_filled": int(filled),
                "updated_at": led.get("updated_at"),
                "immature": immature,
                "pending_close": pending_close,
                "need_bar_date": need_h1,
                "feature_as_of": meta.get("feature_as_of"),
            }
        )
    return out


def delete_ledger(
    as_of: str,
    *,
    include_outcomes: bool = True,
) -> Dict[str, Any]:
    """删除指定日冻结账本（可选一并删 outcomes）。"""
    d = date_key(as_of)
    if not d:
        return {"success": False, "error": "as_of 无效", "as_of": as_of}
    removed: List[str] = []
    missing: List[str] = []
    paths = [ledger_path(d)]
    if include_outcomes:
        paths.append(outcomes_path(d))
    for path in paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(os.path.basename(path))
            else:
                missing.append(os.path.basename(path))
        except OSError as e:
            return {
                "success": False,
                "error": str(e),
                "as_of": d,
                "removed": removed,
                "missing": missing,
            }
    if not removed:
        return {
            "success": False,
            "error": "无该日账本文件",
            "as_of": d,
            "removed": removed,
            "missing": missing,
        }
    return {
        "success": True,
        "as_of": d,
        "removed": removed,
        "missing": missing,
        "include_outcomes": bool(include_outcomes),
    }


def delete_ledgers(
    dates: Sequence[str],
    *,
    include_outcomes: bool = True,
) -> Dict[str, Any]:
    """批量删除多日账本。"""
    results: List[Dict[str, Any]] = []
    ok = 0
    for raw in dates or []:
        one = delete_ledger(str(raw), include_outcomes=include_outcomes)
        results.append(one)
        if one.get("success"):
            ok += 1
    return {
        "success": ok > 0,
        "deleted": ok,
        "failed": len(results) - ok,
        "results": results,
    }

