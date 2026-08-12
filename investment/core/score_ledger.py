"""打分账本：按决策日 as_of 冻结 ŷ，供「昨日复盘」对账。

写入：集群书刷新 / 日报 / 手动冻结。
回填：次日或 h 日后用日线算 realized，再生成方向复盘报告。
"""

from __future__ import annotations
from core.numbers import date_key

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.io_atomic import atomic_write_json

_YHAT_EPS = 0.05  # |ŷ| < ε → 无方向


def default_as_of() -> str:
    """默认识别为「上一交易日」（相对当前会话日）。"""
    from core.market_calendar import prev_trading_day, resolve_session_date

    sess = resolve_session_date()
    prev = prev_trading_day(sess, n=1)
    return prev or sess


def _codes_from_book_doc(book_doc: Optional[dict]) -> List[str]:
    if not isinstance(book_doc, dict):
        return []
    codes: List[str] = []
    seen = set()
    for key in ("book", "scored_all"):
        for item in book_doc.get(key) or []:
            if not isinstance(item, dict):
                continue
            c = str(item.get("stock_code") or item.get("code") or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            codes.append(c)
    return codes


def _last_bar_date_for_code(code: str) -> Optional[str]:
    """本地日线末根日期（只读缓存，不拉网）。"""
    raw = str(code or "").strip()
    if not raw:
        return None
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_daily_cache

        market, c = resolve_market_code(raw)
        if not market or not c:
            return None
        cached = load_daily_cache(market, c, min_bars=1, max_age_hours=72.0)
        if not cached:
            return None
        bars, _meta = cached
        if not bars:
            return None
        return date_key(bars[-1].get("date") or bars[-1].get("time"))
    except Exception:
        return None


def infer_feature_as_of(
    *,
    book_doc: Optional[dict] = None,
    codes: Optional[Sequence[str]] = None,
    sample: int = 16,
) -> Optional[str]:
    """从分池簿标的本地日线推断因子截止日（多数末根日期）。"""
    from collections import Counter

    pool = list(codes or []) or _codes_from_book_doc(book_doc)
    if not pool:
        return None
    take = max(1, min(int(sample or 16), 40, len(pool)))
    lasts: List[str] = []
    for code in pool[:take]:
        d = _last_bar_date_for_code(code)
        if d:
            lasts.append(d)
    if not lasts:
        return None
    mode, n = Counter(lasts).most_common(1)[0]
    # 至少一半样本同意；否则取最早末根（偏保守，避免超前标决策日）
    if n * 2 >= len(lasts):
        return mode
    return min(lasts)


def resolve_freeze_as_of(
    as_of: Optional[str] = None,
    *,
    book_doc: Optional[dict] = None,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """冻结决策日：对齐因子截止，禁止「会话日标签 + 昨收因子」。

    规则：
    - 无显式 as_of → 优先本地日线推断的 feature_as_of，否则上一交易日
    - 显式 as_of 若晚于 feature_as_of → 下调到 feature_as_of
    - 无日线证据时，禁止把 as_of 标成「今天会话日」（除非已是上一交易日）
    """
    from core.market_calendar import resolve_session_date

    sess = resolve_session_date()
    prev = default_as_of()
    feature = infer_feature_as_of(book_doc=book_doc, codes=codes)
    requested = date_key(as_of) if as_of else None
    notes: List[str] = []

    if requested:
        target = requested
    elif feature:
        target = feature
        notes.append(f"按因子截止日 {feature} 冻结")
    else:
        target = prev
        notes.append(f"无日线证据，默认上一交易日 {prev}")

    remapped = False
    if feature and target and target > feature:
        notes.append(f"请求 {target} 晚于因子截止 {feature}，已下调")
        target = feature
        remapped = True
    if not feature and target == sess and sess != prev:
        notes.append(f"无今日日线证据，会话日 {sess} 下调为 {prev}")
        target = prev
        remapped = True

    return {
        "as_of": target,
        "session_date": sess,
        "prev_trading_day": prev,
        "feature_as_of": feature,
        "requested_as_of": requested,
        "remapped": remapped,
        "note": "；".join(notes) if notes else None,
    }


def ledger_dir() -> str:
    from core.paths import SCORE_LEDGER_DIR

    return SCORE_LEDGER_DIR


def ledger_path(as_of: str) -> str:
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.json")


def outcomes_path(as_of: str) -> str:
    d = date_key(as_of)
    return os.path.join(ledger_dir(), f"{d}.outcomes.json")


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
        yhat = _to_float(item.get("score"))
    if yhat is None:
        return None
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
        except Exception:
            sector = None
    return {
        "as_of": date_key(as_of),
        "code": code.zfill(6) if code.isdigit() else code,
        "name": item.get("stock_name") or item.get("name"),
        "yhat": round(yhat, 6),
        "heuristic": _to_float(item.get("heuristic_score")),
        "cluster_label": item.get("cluster_label"),
        "sector": sector,
        "model_id": item.get("return_model_source")
        or item.get("weight_source")
        or item.get("model_id"),
        "rank": item.get("rank") or item.get("rank_in_group"),
        "formula_terms_top": terms,
        "source": str(source or "scored"),
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }


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
        with open(path, "r", encoding="utf-8") as f:
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
        with open(path, "r", encoding="utf-8") as f:
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


def freeze_from_cluster_book(
    *,
    as_of: Optional[str] = None,
    book_doc: Optional[dict] = None,
) -> Dict[str, Any]:
    """从 active 集群书冻结 ŷ；决策日对齐因子截止（见 resolve_freeze_as_of）。"""
    from core.signal.cluster_live import load_active_cluster_book

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
    out = upsert_ledger_rows(
        d,
        book,
        source="cluster_book",
        meta={
            "cluster_version": meta.get("version") or doc.get("version"),
            "book_updated_at": doc.get("updated_at"),
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
    out = upsert_ledger_rows(
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


def _realized_from_bars(
    bars: Sequence[dict],
    as_of: str,
    horizon_days: int,
) -> Optional[float]:
    """(close[as_of+h] / close[as_of] - 1) * 100。"""
    from core.market_calendar import next_trading_day

    d0 = date_key(as_of)
    if not d0 or not bars:
        return None
    by_date = {}
    for b in bars:
        if not isinstance(b, dict):
            continue
        k = date_key(b.get("date") or b.get("time") or b.get("datetime"))
        if k:
            by_date[k] = b
    if d0 not in by_date:
        return None
    d1 = next_trading_day(d0, n=max(1, int(horizon_days or 1)))
    if not d1 or d1 not in by_date:
        return None
    c0 = _to_float(by_date[d0].get("close"))
    c1 = _to_float(by_date[d1].get("close"))
    if c0 is None or c1 is None or c0 <= 0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def _sign_hit(yhat: Optional[float], realized: Optional[float]) -> Optional[bool]:
    if yhat is None or realized is None:
        return None
    if abs(float(yhat)) < _YHAT_EPS:
        return None  # 无方向
    if abs(float(realized)) < 1e-12:
        return None
    return (float(yhat) > 0) == (float(realized) > 0)


def fill_outcomes(
    as_of: str,
    *,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """用本地日线回填 realized / sign_hit。"""
    from core.data_service import bars_and_source

    ledger = load_ledger(as_of)
    if not ledger.get("success"):
        return ledger
    rows = list(ledger.get("rows") or [])
    if not rows:
        return {
            "success": False,
            "error": "无账本行，请先冻结打分",
            "as_of": date_key(as_of),
            "filled": 0,
        }
    h = max(1, min(int(horizon_days or 3), 10))
    by_code: Dict[str, Dict[str, Any]] = {}
    filled = 0
    missing = 0
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        bars, _ = bars_and_source(code, limit=max(40, h + 25), offline_ok=True)
        realized = _realized_from_bars(bars or [], r.get("as_of") or as_of, h)
        yhat = _to_float(r.get("yhat"))
        hit = _sign_hit(yhat, realized)
        abs_err = None
        if yhat is not None and realized is not None:
            abs_err = round(abs(float(yhat) - float(realized)), 4)
        dominant = None
        terms = r.get("formula_terms_top") or []
        if terms:
            dominant = terms[0].get("key")
        by_code[code] = {
            "code": code,
            "realized_h": round(realized, 4) if realized is not None else None,
            "sign_hit": hit,
            "abs_err": abs_err,
            "dominant_factor": dominant,
            "no_direction": bool(yhat is not None and abs(float(yhat)) < _YHAT_EPS),
        }
        if realized is not None:
            filled += 1
        else:
            missing += 1
    path = outcomes_path(as_of)
    os.makedirs(ledger_dir(), exist_ok=True)
    payload = {
        "success": True,
        "as_of": date_key(as_of),
        "horizon_days": h,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "filled": filled,
        "missing": missing,
        "by_code": by_code,
    }
    atomic_write_json(path, payload)
    return {
        "success": True,
        "as_of": date_key(as_of),
        "horizon_days": h,
        "filled": filled,
        "missing": missing,
        "path": path,
    }


def _blame_tag(
    *,
    sign_hit: Optional[bool],
    dominant: Optional[str],
    factor_ic: Optional[float],
    no_direction: bool,
    has_terms: bool,
    has_realized: bool,
) -> str:
    if no_direction:
        return "no_direction"
    if not has_realized:
        return "data_thin"
    if sign_hit is True:
        return "hit"
    if sign_hit is not False:
        return "data_thin"
    if not has_terms or not dominant:
        return "data_thin"
    if factor_ic is not None and factor_ic < 0:
        return "factor_fade"
    if factor_ic is not None and factor_ic >= 0:
        return "idiosyncratic"
    return "model_tilt"


def _factor_cn(name: Optional[str]) -> str:
    key = str(name or "").strip()
    if not key:
        return ""
    try:
        from core.signal.factor_registry import factor_label

        return factor_label(key) or key
    except Exception:
        return key


def _day_factor_ic_proxy(
    rows: Sequence[dict],
    outcomes: Dict[str, dict],
    factor_key: str,
) -> Optional[float]:
    """用账本 terms 的 z 与 realized 做简易截面相关（样本少时仅作提示）。"""
    xs: List[float] = []
    ys: List[float] = []
    for r in rows:
        code = str(r.get("code") or "")
        oc = outcomes.get(code) or {}
        realized = _to_float(oc.get("realized_h"))
        if realized is None:
            continue
        z = None
        for t in r.get("formula_terms_top") or []:
            if str(t.get("key")) == factor_key:
                z = _to_float(t.get("z"))
                if z is None:
                    z = _to_float(t.get("contrib"))
                break
        if z is None:
            continue
        xs.append(z)
        ys.append(realized)
    if len(xs) < 3:
        return None
    try:
        from core.backtest.pool_ic import _pearson

        return _pearson(xs, ys)
    except Exception:
        return None


def build_score_review(
    as_of: Optional[str] = None,
    *,
    horizon_days: int = 3,
    autofill: bool = True,
) -> Dict[str, Any]:
    """方向复盘报告：命中率 + 错票 + 简易归因标签。"""
    d = date_key(as_of) or default_as_of()
    h = max(1, min(int(horizon_days or 3), 10))
    ledger = load_ledger(d)
    if ledger.get("empty"):
        return {
            "success": True,
            "empty": True,
            "as_of": d,
            "horizon_days": h,
            "error": None,
            "note": "无该日账本。请先跑分组落书、生成日报，或点「冻结今日打分」。",
            "summary": {},
            "wrong_rows": [],
            "scored_rows": [],
            "factor_blame": [],
            "industry_blame": [],
            "cluster_blame": [],
        }
    outcomes = load_outcomes(d)
    need_fill = (
        autofill
        and (
            outcomes.get("empty")
            or int(outcomes.get("horizon_days") or 0) != h
            or not outcomes.get("by_code")
        )
    )
    if need_fill:
        fill_outcomes(d, horizon_days=h)
        outcomes = load_outcomes(d)
    by_code = outcomes.get("by_code") or {}
    rows = list(ledger.get("rows") or [])

    n = 0
    hits = 0
    wrong_long = 0
    wrong_short = 0
    no_dir = 0
    thin = 0
    wrong_rows: List[Dict[str, Any]] = []
    scored_rows: List[Dict[str, Any]] = []
    tag_counts: Dict[str, int] = {}
    factor_wrong: Dict[str, int] = {}
    industry_wrong: Dict[str, int] = {}
    cluster_wrong: Dict[str, int] = {}
    factor_ic_cache: Dict[str, Optional[float]] = {}

    for r in rows:
        code = str(r.get("code") or "")
        oc = by_code.get(code) or {}
        yhat = _to_float(r.get("yhat"))
        realized = _to_float(oc.get("realized_h"))
        hit = oc.get("sign_hit")
        if hit is None and yhat is not None and realized is not None:
            hit = _sign_hit(yhat, realized)
        no_direction = bool(oc.get("no_direction")) or (
            yhat is not None and abs(float(yhat)) < _YHAT_EPS
        )
        dominant = oc.get("dominant_factor")
        if not dominant:
            terms = r.get("formula_terms_top") or []
            if terms:
                dominant = terms[0].get("key")
        fic = None
        if dominant:
            if dominant not in factor_ic_cache:
                factor_ic_cache[dominant] = _day_factor_ic_proxy(rows, by_code, str(dominant))
            fic = factor_ic_cache.get(str(dominant))
        tag = _blame_tag(
            sign_hit=hit if isinstance(hit, bool) else None,
            dominant=str(dominant) if dominant else None,
            factor_ic=fic,
            no_direction=no_direction,
            has_terms=bool(r.get("formula_terms_top")),
            has_realized=realized is not None,
        )
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if no_direction:
            no_dir += 1
            continue
        if realized is None:
            thin += 1
            continue
        n += 1
        scored_rows.append(
            {
                "code": code,
                "name": r.get("name"),
                "yhat": yhat,
                "realized_h": realized,
                "hit": hit if isinstance(hit, bool) else None,
                "abs_err": oc.get("abs_err"),
                "sector": r.get("sector"),
                "cluster_label": r.get("cluster_label"),
                "dominant_factor": dominant,
                "dominant_factor_label": _factor_cn(dominant) if dominant else None,
                "tag": tag,
            }
        )
        if hit is True:
            hits += 1
            continue
        if hit is False:
            if float(yhat or 0) > 0:
                wrong_long += 1
            else:
                wrong_short += 1
            if dominant:
                factor_wrong[str(dominant)] = factor_wrong.get(str(dominant), 0) + 1
            sec = str(r.get("sector") or "").strip() or "其他"
            industry_wrong[sec] = industry_wrong.get(sec, 0) + 1
            clab = str(r.get("cluster_label") or "").strip() or "—"
            cluster_wrong[clab] = cluster_wrong.get(clab, 0) + 1
            wrong_rows.append(
                {
                    "code": code,
                    "name": r.get("name"),
                    "yhat": yhat,
                    "realized_h": realized,
                    "abs_err": oc.get("abs_err"),
                    "cluster_label": r.get("cluster_label"),
                    "sector": r.get("sector") or sec,
                    "dominant_factor": dominant,
                    "dominant_factor_label": _factor_cn(dominant) if dominant else None,
                    "factor_ic_day": round(fic, 4) if fic is not None else None,
                    "tag": tag,
                    "formula_terms_top": (r.get("formula_terms_top") or [])[:3],
                }
            )

    wrong_rows.sort(key=lambda x: -float(x.get("abs_err") or 0))
    hit_rate = round(hits / n, 4) if n else None
    factor_blame = []
    for fac, cnt in sorted(factor_wrong.items(), key=lambda kv: -kv[1])[:8]:
        fic = factor_ic_cache.get(fac)
        if fic is None:
            fic = _day_factor_ic_proxy(rows, by_code, fac)
        factor_blame.append(
            {
                "factor": fac,
                "factor_label": _factor_cn(fac),
                "wrong_count": cnt,
                "factor_ic_day": round(fic, 4) if fic is not None else None,
            }
        )
    industry_blame = [
        {"sector": sec, "wrong_count": cnt}
        for sec, cnt in sorted(industry_wrong.items(), key=lambda kv: -kv[1])[:8]
    ]
    cluster_blame = [
        {"cluster_label": lab, "wrong_count": cnt}
        for lab, cnt in sorted(cluster_wrong.items(), key=lambda kv: -kv[1])[:8]
    ]

    blame_line = "样本不足"
    if n:
        if hit_rate is not None and hit_rate >= 0.55:
            blame_line = f"方向命中 {hit_rate:.0%}（{hits}/{n}）"
        elif factor_blame:
            top = factor_blame[0]
            ic = top.get("factor_ic_day")
            ic_s = f"，当日因子相关≈{ic}" if ic is not None else ""
            fac_cn = top.get("factor_label") or _factor_cn(top.get("factor"))
            blame_line = (
                f"方向命中 {hit_rate:.0%}（{hits}/{n}）；"
                f"错票常挂在 {fac_cn}（{top['wrong_count']} 次）{ic_s}"
            )
            if industry_blame:
                blame_line += f"；行业 {industry_blame[0]['sector']}×{industry_blame[0]['wrong_count']}"
        else:
            blame_line = f"方向命中 {hit_rate:.0%}（{hits}/{n}）"
    elif thin > 0:
        blame_line = (
            f"薄样本 {thin}/{len(rows)}：本地日线未覆盖 as_of+{h}，尚无实现收益"
        )

    # h>1 全部薄样本时自动降到 h=1，避免 UI 只显示「命中 —」
    if n == 0 and thin > 0 and h > 1:
        fallback = build_score_review(d, horizon_days=1, autofill=autofill)
        fb_n = int((fallback.get("summary") or {}).get("n_scored") or 0)
        if fallback.get("success") and not fallback.get("empty") and fb_n > 0:
            fallback["horizon_fallback_from"] = h
            fallback["note"] = (
                f"h={h} 实现收益未齐（日线未覆盖 as_of+{h}，薄样本 {thin}）。"
                f"已自动改用 h=1。"
                + (" " + str(fallback.get("note") or "")).rstrip()
            )
            return fallback

    return {
        "success": True,
        "empty": False,
        "as_of": d,
        "horizon_days": h,
        "ledger_path": ledger.get("path"),
        "outcomes_path": outcomes.get("path"),
        "n_ledger": len(rows),
        "summary": {
            "n_scored": n,
            "hit_rate": hit_rate,
            "hits": hits,
            "wrong": len(wrong_rows),
            "wrong_long": wrong_long,
            "wrong_short": wrong_short,
            "no_direction": no_dir,
            "data_thin": thin,
            "tag_counts": tag_counts,
            "blame_line": blame_line,
        },
        "wrong_rows": wrong_rows[:80],
        "scored_rows": scored_rows[:200],
        "factor_blame": factor_blame,
        "industry_blame": industry_blame,
        "cluster_blame": cluster_blame,
        "suggested_horizon": 1 if (n == 0 and thin > 0 and h > 1) else None,
        "note": (
            (
                f"h={h} 尚无实现收益（薄样本 {thin}/{len(rows)}）。"
                "请刷新日线、改小 Horizon，或选更早决策日后再「回填收益」。"
                if (n == 0 and thin > 0)
                else ""
            )
            + "方向复盘：sign(ŷ) vs sign(r_h)；|ŷ|<0.05% 视为无方向。"
            "标签：factor_fade=主导因子当日截面相关为负；"
            "idiosyncratic=因子未坏但个股反；model_tilt=分解不足时的模型偏置兜底。"
        ).strip(),
        "refit_hint": (
            "错票偏多时建议到上方「跑分组」重估组 β（不自动改权）。"
            if (hit_rate is not None and hit_rate < 0.5 and n >= 5)
            else None
        ),
    }


def list_ledger_dates(*, limit: int = 30) -> List[str]:
    root = ledger_dir()
    if not os.path.isdir(root):
        return []
    dates = []
    for name in os.listdir(root):
        if name.endswith(".json") and not name.endswith(".outcomes.json"):
            dates.append(name[:-5])
    dates.sort(reverse=True)
    return dates[: max(1, int(limit))]


def list_ledger_entries(*, limit: int = 30) -> List[Dict[str, Any]]:
    """已冻结账本条目：日期 / 票数 / 是否已回填 / 是否未到期。"""
    from core.market_calendar import resolve_session_date

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
        meta = led.get("meta") if isinstance(led.get("meta"), dict) else {}
        out.append(
            {
                "as_of": d,
                "n_rows": len(rows),
                "has_outcomes": bool(by_code),
                "outcomes_filled": int(filled),
                "updated_at": led.get("updated_at"),
                "immature": immature,
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


def code_yhat_series(
    code: str,
    *,
    limit: int = 40,
) -> Dict[str, Any]:
    """单票跨决策日 ŷ 时间线（读已冻结账本）。"""
    raw = str(code or "").strip()
    if not raw:
        return {"success": False, "error": "code 无效", "points": []}
    key = raw.zfill(6) if raw.isdigit() else raw
    dates = list_ledger_dates(limit=max(5, min(int(limit or 40), 90)))
    points: List[Dict[str, Any]] = []
    name = None
    for d in reversed(dates):  # 时间正序
        led = load_ledger(d)
        if not led.get("success") or led.get("empty"):
            continue
        for r in led.get("rows") or []:
            if str(r.get("code") or "") != key:
                continue
            y = _to_float(r.get("yhat"))
            if y is None:
                break
            if name is None and r.get("name"):
                name = r.get("name")
            oc = (load_outcomes(d).get("by_code") or {}).get(key) or {}
            points.append(
                {
                    "date": d,
                    "yhat": y,
                    "realized_h": _to_float(oc.get("realized_h")),
                    "sign_hit": oc.get("sign_hit"),
                    "cluster_label": r.get("cluster_label"),
                }
            )
            break
    return {
        "success": True,
        "code": key,
        "name": name,
        "n": len(points),
        "points": points,
        "note": "来自 score_ledger 冻结行；无账本日不出现。",
    }


def stock_panel_series(
    code: str,
    *,
    lookback: int = 10,
    yhat_limit: int = 90,
) -> Dict[str, Any]:
    """复盘单票三面板：收盘价 / 日涨跌% / 冻结 ŷ%（按日期对齐，ŷ 稀疏不插值）。"""
    raw = str(code or "").strip()
    if not raw:
        return {"success": False, "error": "code 无效", "points": []}
    key = raw.zfill(6) if raw.isdigit() else raw
    lb = max(5, min(int(lookback or 10), 120))
    # 多取 1 根用于首日涨跌计算，展示仍截到 lookback 日
    fetch_n = min(lb + 1, 120)
    yhat_lim = max(5, min(int(yhat_limit or 90), 120))

    try:
        from core.data_service import bars_and_source

        bars, src = bars_and_source(key, limit=fetch_n)
    except Exception as exc:
        return {
            "success": False,
            "error": f"日线加载失败：{exc}",
            "code": key,
            "points": [],
        }

    yhat_pack = code_yhat_series(key, limit=yhat_lim)
    yhat_by_date: Dict[str, float] = {}
    name = yhat_pack.get("name")
    for p in yhat_pack.get("points") or []:
        d = date_key(p.get("date"))
        y = _to_float(p.get("yhat"))
        if d and y is not None:
            yhat_by_date[d] = y

    raw_points: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None

    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = date_key(b.get("date") or b.get("time") or b.get("datetime"))
        px = _to_float(b.get("close"))
        if not d or px is None or px <= 0:
            continue
        if name is None and b.get("stock_name"):
            name = b.get("stock_name")
        chg = None
        if prev_close is not None and prev_close > 0:
            chg = round((px / prev_close - 1.0) * 100.0, 2)
        prev_close = px
        y = yhat_by_date.get(d)
        raw_points.append(
            {
                "date": d,
                "close": round(px, 4),
                "change_pct": chg,
                "yhat": round(y, 4) if y is not None else None,
            }
        )

    points = raw_points[-lb:] if len(raw_points) > lb else raw_points
    close_points: List[Dict[str, Any]] = []
    change_points: List[Dict[str, Any]] = []
    yhat_points: List[Dict[str, Any]] = []
    for row in points:
        close_points.append({"date": row["date"], "value": row["close"]})
        if row.get("change_pct") is not None:
            change_points.append({"date": row["date"], "value": row["change_pct"]})
        if row.get("yhat") is not None:
            yhat_points.append({"date": row["date"], "value": row["yhat"]})

    return {
        "success": True,
        "code": key,
        "name": name,
        "lookback": lb,
        "data_source": src,
        "points": points,
        "close_points": close_points,
        "change_points": change_points,
        "yhat_points": yhat_points,
        "n_close": len(close_points),
        "n_change": len(change_points),
        "n_yhat": len(yhat_points),
        "note": "ŷ 仅来自已冻结账本；无账本日断点，不插值。需先落书或点「冻结今日打分」。近 "
        f"{lb} 个交易日。",
    }


def hit_rate_series(
    *,
    horizon_days: int = 3,
    limit: int = 20,
    autofill: bool = False,
) -> Dict[str, Any]:
    """跨决策日方向命中率序列（复盘 sparkline）。"""
    h = max(1, min(int(horizon_days or 3), 10))
    dates = list_ledger_dates(limit=max(5, min(int(limit or 20), 60)))
    series: List[Dict[str, Any]] = []
    for d in reversed(dates):
        # 轻量：不跑全量 build_score_review 文案，只算命中
        led = load_ledger(d)
        if not led.get("success") or led.get("empty"):
            continue
        outcomes = load_outcomes(d)
        if autofill and (
            outcomes.get("empty")
            or int(outcomes.get("horizon_days") or 0) != h
            or not outcomes.get("by_code")
        ):
            try:
                fill_outcomes(d, horizon_days=h)
                outcomes = load_outcomes(d)
            except Exception:
                pass
        by_code = outcomes.get("by_code") or {}
        n = 0
        hits = 0
        for r in led.get("rows") or []:
            code = str(r.get("code") or "")
            yhat = _to_float(r.get("yhat"))
            if yhat is None or abs(float(yhat)) < _YHAT_EPS:
                continue
            oc = by_code.get(code) or {}
            realized = _to_float(oc.get("realized_h"))
            if realized is None:
                continue
            hit = oc.get("sign_hit")
            if hit is None:
                hit = _sign_hit(yhat, realized)
            if not isinstance(hit, bool):
                continue
            n += 1
            if hit:
                hits += 1
        if n <= 0:
            continue
        series.append(
            {
                "date": d,
                "hit_rate": round(hits / n, 4),
                "hits": hits,
                "n_scored": n,
            }
        )
    return {
        "success": True,
        "horizon_days": h,
        "n": len(series),
        "points": series,
        "note": "仅含已回填 realized 的决策日；autofill=false 时不拉日线。",
    }


def run_score_ledger_daily(
    *,
    as_of: Optional[str] = None,
    horizon_days: Optional[int] = None,
    fill_lookback: int = 5,
) -> Dict[str, Any]:
    """日更钩子：按因子截止冻结账本 + 回填已到期 outcomes。

    不抛异常给调度层；失败写进返回字段。
    """
    from core.market_calendar import prev_trading_day, resolve_session_date

    sess = resolve_session_date()
    requested = date_key(as_of)
    h = horizon_days
    if h is None:
        try:
            from core.signal.config import load_signal_config

            scoring = (load_signal_config() or {}).get("scoring") or {}
            h = int(scoring.get("horizon_days") or 3)
        except Exception:
            h = 3
    h = max(1, min(int(h or 3), 10))

    freeze_out: Dict[str, Any]
    try:
        # 默认不传 as_of，由 resolve_freeze_as_of 对齐因子截止
        freeze_out = freeze_from_cluster_book(as_of=requested)
        if not freeze_out.get("success") or int(freeze_out.get("n_rows") or 0) <= 0:
            pass
    except Exception as exc:
        freeze_out = {
            "success": False,
            "error": str(exc),
            "as_of": requested or default_as_of(),
            "n_rows": 0,
        }

    freeze_day = date_key((freeze_out or {}).get("as_of")) or default_as_of()

    fills: List[Dict[str, Any]] = []
    look = max(1, min(int(fill_lookback or 5), 20))
    for i in range(1, look + 1):
        target = prev_trading_day(sess, n=h + i - 1) or None
        if not target:
            continue
        led = load_ledger(target)
        if led.get("empty") or not led.get("success"):
            continue
        try:
            fo = fill_outcomes(target, horizon_days=h)
            fills.append(
                {
                    "as_of": target,
                    "success": bool(fo.get("success")),
                    "filled": fo.get("filled"),
                    "missing": fo.get("missing"),
                    "error": fo.get("error"),
                }
            )
        except Exception as exc:
            fills.append({"as_of": target, "success": False, "error": str(exc)})

    return {
        "success": True,
        "as_of": freeze_day,
        "session_date": sess,
        "horizon_days": h,
        "freeze": freeze_out,
        "fills": fills,
        "filled_days": sum(1 for f in fills if f.get("success")),
        "note": "日更：按因子截止冻结 ŷ 账本；回填到期决策日 realized（不改权）。",
    }
