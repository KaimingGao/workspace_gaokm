"""PaperService · 买卖 / 图表 / T0。"""


import logging

logger = logging.getLogger(__name__)
import copy
import os
from typing import Any, Dict, List, Optional, Tuple

from core.paper.ledger import _now_iso
from core.paper import (
    append_operation_log,
    append_snapshot,
    append_trade_legs_to_operation_log,
    capture_mark_snapshot,
    load_paper,
    mark_to_market,
    paper_write_lock,
    save_paper,
)


def _default_t0_auto() -> Dict[str, Any]:
    return {
        "enabled": False,
        "schedule": "after_close",
        "last_run": None,
    }


def _normalize_t0_auto(raw: Optional[dict]) -> Dict[str, Any]:
    base = _default_t0_auto()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    sched = str(raw.get("schedule") or "").strip()
    if sched in ("after_close", "with_paper_daily"):
        out["schedule"] = sched
    lr = raw.get("last_run")
    if isinstance(lr, dict):
        out["last_run"] = {
            "ts": lr.get("ts"),
            "ok": lr.get("ok"),
            "trade_count": lr.get("trade_count"),
            "pnl_total": lr.get("pnl_total"),
            "skip_count": lr.get("skip_count"),
            "source": lr.get("source"),
            "note": lr.get("note"),
            "error": lr.get("error"),
            "session_date": lr.get("session_date"),
            "rules": lr.get("rules") if isinstance(lr.get("rules"), dict) else None,
            "results": lr.get("results") if isinstance(lr.get("results"), list) else None,
        }
        if "skip_count" in lr:
            out["last_run"]["skip_count"] = lr.get("skip_count")
    return out


_T0_LEDGER_ROW_KEYS = (
    "stock_code",
    "stock_name",
    "date",
    "skipped",
    "reason",
    "signal_skip",
    "skip_category",
    "success",
    "error",
    "direction_used",
    "direction",
    "direction_score",
    "direction_reason",
    "pnl",
    "exposure_pnl",
    "sold_qty",
    "bought_qty",
    "covered_qty",
    "uncovered_qty",
    "sold_back_qty",
    "touch_sell_at",
    "touch_buy_at",
    "touch_cover_at",
    "path_mode",
    "minute_path",
    "intraday_path",
    "cover_policy",
    "must_cover_same_day",
    "exit_reason",
    "t0_slots_enabled",
    "t0_slot",
    "t0_slot_hm",
    "t0_slot_focus",
    "range_mode",
    "prefix_bars",
    "open",
    "high",
    "low",
    "close",
    "price_space",
    "price_space_scale",
    "price_space_mode",
    "close_band",
    "day_return_pct",
    "leg1_notional",
)

_T0_SCORE_BULK_KEYS = frozenset(
    {
        "score_formula_terms",
        "factor_coefficients",
        "y_spec_tau",
        "dual_score_weights",
        "dual_score_fusion",
    }
)

_T0_TRACE_KEYS = (
    "idx",
    "time",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "buy_fill",
    "sell_fill",
    "leg1_fill",
    "leg2_fill",
)

_T0_SLOT_ROW_KEYS = (
    "id",
    "hm",
    "t0_slot",
    "t0_slot_hm",
    "skipped",
    "pending",
    "reason",
    "direction",
    "direction_used",
    "trades",
    "sold_qty",
    "covered_qty",
    "uncovered_qty",
    "bought_qty",
    "sold_back_qty",
    "pnl",
    "exposure_pnl",
    "exit_reason",
    "close_band",
)

_T0_LEG_KEYS = (
    "side",
    "price",
    "shares",
    "at",
    "ts",
    "t0_slot",
    "t0_slot_hm",
    "note",
    "leg_kind",
)

_T0_DISPLAY_KEYS = (
    "t0_slots_enabled",
    "t0_slot_results",
    "close_band",
    "close_band_scan",
    "forward_trace",
    "price_space",
    "price_space_scale",
    "price_space_mode",
    "open",
    "high",
    "low",
    "close",
    "prefix_bars",
    "range_mode",
    "day_return_pct",
    "leg1_notional",
)


def _slim_t0_scores(raw: Any) -> Optional[dict]:
    """成交表只要 ŷ 标量；丢掉公式拆项 / 系数表。"""
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in _T0_SCORE_BULK_KEYS or str(k).startswith("formula_terms"):
            continue
        out[k] = v
    return out or None


def _compact_t0_legs(trades: Any) -> List[dict]:
    out: List[dict] = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        leg = {k: t[k] for k in _T0_LEG_KEYS if k in t}
        if leg:
            out.append(leg)
    return out


def _compact_forward_trace(trace: Any) -> List[dict]:
    out: List[dict] = []
    for row in trace or []:
        if not isinstance(row, dict):
            continue
        slim = {k: row[k] for k in _T0_TRACE_KEYS if k in row}
        if slim:
            out.append(slim)
    return out


def _compact_t0_slot_row(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    row = {k: raw[k] for k in _T0_SLOT_ROW_KEYS if k in raw}
    if raw.get("direction_used") and "direction" not in row:
        row["direction"] = raw.get("direction_used")
    scores = _slim_t0_scores(raw.get("scores"))
    if scores:
        row["scores"] = scores
    feats = _slim_t0_scores(raw.get("direction_features"))
    if feats:
        row["direction_features"] = feats
    raw_legs = raw.get("trade_legs")
    if not isinstance(raw_legs, list):
        raw_legs = raw.get("trades") if isinstance(raw.get("trades"), list) else None
    legs = _compact_t0_legs(raw_legs)
    if legs:
        row["trade_legs"] = legs
    if isinstance(row.get("trades"), list):
        row["trades"] = len(row["trades"])
    return row or None


def _compact_t0_result_rows(results: Optional[list]) -> List[dict]:
    """落账 last_run 存档：保留成交表所需字段，去掉冗余 bulk。"""
    from core.t0.viz import classify_t0_skip_reason

    out: List[dict] = []
    for raw in results or []:
        if not isinstance(raw, dict):
            continue
        row = {k: raw[k] for k in _T0_LEDGER_ROW_KEYS if k in raw}
        if raw.get("direction_used") and "direction" not in row:
            row["direction"] = raw.get("direction_used")
        if row.get("skipped") and not row.get("skip_category"):
            row["skip_category"] = classify_t0_skip_reason(str(raw.get("reason") or ""))
        scores = _slim_t0_scores(raw.get("scores"))
        if scores:
            row["scores"] = scores
        feats = _slim_t0_scores(raw.get("direction_features"))
        if feats:
            row["direction_features"] = feats
        legs = _compact_t0_legs(raw.get("trades"))
        if legs:
            row["trades"] = legs
        slots = [
            s
            for s in (_compact_t0_slot_row(r) for r in (raw.get("t0_slot_results") or []))
            if s
        ]
        if slots:
            row["t0_slot_results"] = slots
        scan = raw.get("close_band_scan")
        if isinstance(scan, list) and scan:
            row["close_band_scan"] = [r for r in scan if isinstance(r, dict)]
        trace = _compact_forward_trace(raw.get("forward_trace"))
        if trace:
            row["forward_trace"] = trace
        out.append(row)
    return out


def _ledger_row_missing_px(row: Optional[dict]) -> bool:
    if not isinstance(row, dict):
        return True
    if row.get("close_band_scan") or row.get("forward_trace") or row.get("t0_slot_results"):
        return False
    return True


def _intraday_ledger_snap(state: dict, code: str) -> Optional[dict]:
    stocks = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    st = stocks.get(code) if isinstance(stocks.get(code), dict) else {}
    snap = st.get("day_snapshot") if isinstance(st.get("day_snapshot"), dict) else None
    if isinstance(snap, dict) and not _ledger_row_missing_px(snap):
        return snap
    for raw in state.get("results") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("stock_code") or "") != code:
            continue
        if not _ledger_row_missing_px(raw):
            return raw
    return snap if isinstance(snap, dict) else None


def _leg_clock(at: Any) -> str:
    s = str(at or "").strip()
    for part in s.replace("T", " ").split():
        if ":" in part and part[0].isdigit():
            return part[:5]
    return s[:5]


def _stamp_slot_ids_on_legs(legs: List[dict], snap_legs: List[dict]) -> List[dict]:
    """只给已落账腿补槽位标记，不改成交价。"""
    unused = [dict(s) for s in snap_legs or [] if isinstance(s, dict)]
    out: List[dict] = []
    for raw in legs or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if row.get("t0_slot") and row.get("t0_slot_hm"):
            out.append(row)
            continue
        clock = _leg_clock(row.get("at"))
        side = str(row.get("side") or "")
        try:
            shares = int(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0
        hit_i = None
        for i, s in enumerate(unused):
            if str(s.get("side") or "") != side:
                continue
            try:
                if int(s.get("shares") or 0) != shares:
                    continue
            except (TypeError, ValueError):
                continue
            if clock and _leg_clock(s.get("at")) != clock:
                continue
            hit_i = i
            break
        if hit_i is not None:
            s = unused.pop(hit_i)
            for k in ("t0_slot", "t0_slot_hm", "note", "leg_kind"):
                if not row.get(k) and s.get(k):
                    row[k] = s.get(k)
        out.append(row)
    return out


def _stamp_book_ts_from_paper(last_run: Optional[dict], paper: Optional[dict]) -> bool:
    """旧 last_run 缺记账 ``ts`` 时从 paper.trades 补回（审计用；过程列走 5m 槽钟）。"""
    if not isinstance(last_run, dict) or not isinstance(paper, dict):
        return False
    rows = last_run.get("results")
    if not isinstance(rows, list) or not rows:
        return False
    pool = [t for t in (paper.get("trades") or []) if isinstance(t, dict) and t.get("ts")]
    if not pool:
        return False
    unused = list(pool)
    changed = False
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("stock_code") or "").strip()
        legs = raw.get("trades")
        if not isinstance(legs, list):
            continue
        patched: List[dict] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            row = dict(leg)
            if row.get("ts"):
                patched.append(row)
                continue
            side = str(row.get("side") or "")
            try:
                shares = int(row.get("shares") or 0)
            except (TypeError, ValueError):
                shares = 0
            try:
                px = round(float(row.get("price") or 0), 4)
            except (TypeError, ValueError):
                px = 0.0
            hit_i = None
            for i, t in enumerate(unused):
                t_code = str(t.get("stock_code") or "").strip()
                if code and t_code and t_code != code:
                    continue
                if str(t.get("side") or "") != side:
                    continue
                try:
                    if int(t.get("shares") or 0) != shares:
                        continue
                except (TypeError, ValueError):
                    continue
                try:
                    if round(float(t.get("price") or 0), 4) != px:
                        continue
                except (TypeError, ValueError):
                    continue
                hit_i = i
                break
            if hit_i is not None:
                t = unused.pop(hit_i)
                row["ts"] = t.get("ts")
                changed = True
            patched.append(row)
        raw["trades"] = patched
    return changed


def _hydrate_t0_last_run_display(last_run: Optional[dict]) -> bool:
    """旧 last_run 缺 OHLC/Ĉ 时，用当日盘中 day_snapshot 补成交表字段。"""
    if not isinstance(last_run, dict):
        return False
    rows = last_run.get("results")
    if not isinstance(rows, list) or not rows:
        return False
    if not any(_ledger_row_missing_px(r) for r in rows if isinstance(r, dict)):
        return False
    try:
        from core.t0.intraday import load_intraday_state
    except Exception:  # noqa: BLE001
        logger.debug("hydrate last_run: load_intraday_state import failed", exc_info=True)
        return False
    state = load_intraday_state()
    if not isinstance(state, dict) or not state:
        return False
    sess = str(last_run.get("session_date") or "")[:10]
    st_sess = str(state.get("session_date") or "")[:10]
    if sess and st_sess and sess != st_sess:
        return False
    changed = False
    patched: List[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if not _ledger_row_missing_px(raw):
            patched.append(raw)
            continue
        code = str(raw.get("stock_code") or "").strip()
        snap = _intraday_ledger_snap(state, code) if code else None
        if not isinstance(snap, dict):
            patched.append(raw)
            continue
        merged = dict(raw)
        for k in _T0_DISPLAY_KEYS:
            if merged.get(k) in (None, [], {}) and snap.get(k) not in (None, [], {}):
                merged[k] = snap.get(k)
        existing_legs = (
            list(merged.get("trades") or [])
            if isinstance(merged.get("trades"), list)
            else []
        )
        snap_legs = _compact_t0_legs(snap.get("trades"))
        if existing_legs:
            merged["trades"] = _stamp_slot_ids_on_legs(existing_legs, snap_legs)
        elif snap_legs:
            merged["trades"] = snap_legs
        slim = _compact_t0_result_rows([merged])
        patched.append(slim[0] if slim else merged)
        changed = True
    if changed:
        last_run["results"] = patched
    return changed


def _t0_rules_from_result(result: dict) -> Dict[str, Any]:
    exe = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    t0 = exe.get("t0") if isinstance(exe.get("t0"), dict) else {}
    return dict(t0)


def _t0_session_date_from_result(result: dict) -> Optional[str]:
    for row in result.get("results") or []:
        if isinstance(row, dict) and row.get("date"):
            return str(row["date"])[:10]
    return None


def _t0_auto_from_paper(paper: dict) -> Dict[str, Any]:
    rules = paper.get("rules") if isinstance(paper.get("rules"), dict) else {}
    return _normalize_t0_auto((rules or {}).get("t0_auto"))


def _results_with_trades_only(results: Optional[list]) -> List[dict]:
    out: List[dict] = []
    for raw in results or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("skipped"):
            continue
        if not (raw.get("trades") or []):
            continue
        out.append(raw)
    return out


def _results_from_flat_trades(
    trades: List[dict], *, session_date: Optional[str] = None
) -> List[dict]:
    """top-level trades 缺少 per-stock results 时，合成落账明细行（盘中兜底）。"""
    by_code: Dict[str, Dict[str, Any]] = {}
    sess = str(session_date or "")[:10] if session_date else ""
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        code = str(t.get("stock_code") or "").strip()
        if not code:
            continue
        row = by_code.get(code)
        if row is None:
            row = {
                "stock_code": code,
                "date": sess or None,
                "skipped": False,
                "success": True,
                "trades": [],
            }
            by_code[code] = row
        row["trades"].append(t)
    for row in by_code.values():
        legs = row["trades"]
        sells = [x for x in legs if str(x.get("side") or "").endswith("sell")]
        buys = [x for x in legs if str(x.get("side") or "").endswith("buy")]
        if sells and not buys:
            row["direction_used"] = "sell_then_buy"
            row["sold_qty"] = int(sells[0].get("shares") or 0)
        elif buys and not sells:
            row["direction_used"] = "buy_then_sell"
            row["bought_qty"] = int(buys[0].get("shares") or 0)
        elif buys and sells:
            row["direction_used"] = "buy_then_sell"
            row["bought_qty"] = int(buys[0].get("shares") or 0)
            row["sold_back_qty"] = int(sells[-1].get("shares") or 0)
        if buys and sells and str(row.get("direction_used") or "") == "sell_then_buy":
            row["covered_qty"] = sum(int(x.get("shares") or 0) for x in buys)
    return list(by_code.values())


def _merge_t0_result_rows(
    prev: Optional[list],
    new_rows: List[dict],
    *,
    session_date: Optional[str],
    prev_session: Optional[str],
) -> List[dict]:
    """同日合并 per-stock 快照；新 session 则替换。"""
    if session_date and prev_session and str(session_date) != str(prev_session):
        base: Dict[str, dict] = {}
    else:
        base = {
            str(r.get("stock_code") or ""): r
            for r in (prev or [])
            if isinstance(r, dict) and r.get("stock_code")
        }
    for row in new_rows or []:
        code = str(row.get("stock_code") or "")
        if code:
            base[code] = row
    return list(base.values())


def _summarize_t0_trade_results(rows: Optional[list]) -> Tuple[int, float]:
    legs = 0
    pnl = 0.0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        legs += len(r.get("trades") or [])
        if r.get("pnl") is not None:
            pnl += float(r.get("pnl") or 0)
    return legs, round(pnl, 2)


def _write_t0_auto_last_run(paper: dict, *, result: dict, source: str) -> None:
    """写入 last_run；仅在有买/卖腿时落明细（results 只含成交票）。"""
    legs_in = list(result.get("trades") or [])
    if not legs_in:
        return
    rules = dict(paper.get("rules") or {})
    cfg = _normalize_t0_auto(rules.get("t0_auto"))
    import time

    sess = _t0_session_date_from_result(result) or result.get("session_date")
    prev_lr = cfg.get("last_run") if isinstance(cfg.get("last_run"), dict) else {}
    prev_sess = prev_lr.get("session_date")
    trade_rows = _compact_t0_result_rows(
        _results_with_trades_only(result.get("results"))
    )
    if not trade_rows and legs_in:
        trade_rows = _compact_t0_result_rows(
            _results_from_flat_trades(legs_in, session_date=sess)
        )
    merged = _merge_t0_result_rows(
        prev_lr.get("results"),
        trade_rows,
        session_date=sess,
        prev_session=prev_sess,
    )
    if not merged:
        return
    trade_count, pnl_total = _summarize_t0_trade_results(merged)

    cfg["last_run"] = {
        "ts": time.time(),
        "ok": bool(result.get("success", result.get("ok", True))),
        "trade_count": trade_count,
        "pnl_total": pnl_total,
        "source": source,
        "note": result.get("note"),
        "error": result.get("error"),
        "session_date": sess,
        "rules": _t0_rules_from_result(result),
        "results": merged,
    }
    rules["t0_auto"] = cfg
    paper["rules"] = rules


def _trade_session_date(trade: dict) -> str:
    for key in ("at", "ts", "date"):
        raw = str(trade.get(key) or "").strip()
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    return ""


def _norm_log_ts(ts: Any) -> str:
    s = str(ts or "").strip().replace("T", " ")
    return s[:19] if len(s) >= 19 else s


def _mark_t0_operation_logs_voided(
    paper: dict,
    *,
    code: str,
    session_date: Optional[str],
    matched: List[dict],
) -> int:
    """冲正后把「交易记录」里对应做 T 流水一并标 voided，避免仍显示成交。"""
    from core.paper.ledger import _now_iso

    code = str(code or "").strip()
    sess = str(session_date or "")[:10]
    if not code or not isinstance(paper.get("operation_log"), list):
        return 0
    now = _now_iso()
    n = 0
    marked_ts: set = set()
    for e in paper["operation_log"]:
        if not isinstance(e, dict) or e.get("voided"):
            continue
        et = str(e.get("type") or "")
        if et not in ("buy", "sell"):
            continue
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        if str(meta.get("origin") or "") != "t0":
            continue
        if str(meta.get("stock_code") or "").strip() != code:
            continue
        e_sess = _trade_session_date(e) or str(e.get("ts") or "")[:10]
        if sess and e_sess and e_sess != sess:
            continue
        e["voided"] = True
        e["voided_at"] = now
        marked_ts.add(_norm_log_ts(e.get("ts")))
        n += 1
    if not marked_ts:
        for t in matched or []:
            marked_ts.add(_norm_log_ts((t or {}).get("ts")))
    for e in paper["operation_log"]:
        if not isinstance(e, dict) or e.get("voided"):
            continue
        if str(e.get("type") or "") != "t0_batch":
            continue
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        if str(meta.get("origin") or "") != "t0":
            continue
        if _norm_log_ts(e.get("ts")) not in marked_ts:
            continue
        e["voided"] = True
        e["voided_at"] = now
        n += 1
    return n


def _void_t0_trades_for_code(
    paper: dict,
    code: str,
    *,
    session_date: Optional[str],
) -> Dict[str, Any]:
    """冲正某票当日做 T 腿：反转现金、批次，并标记 trades.voided。"""
    from core.market.calendar import prev_trading_day
    from core.paper.ledger import _now_iso
    from core.paper.tplus1 import (
        consume_lots_bought_on,
        ensure_lots,
        restore_sellable_lot,
    )
    from core.t0.costs import t0_leg_cash_delta

    code = str(code or "").strip()
    sess = str(session_date or "")[:10]
    if not code:
        return {"voided": 0, "cash_delta": 0.0}

    trades = list(paper.get("trades") or [])
    matched: List[dict] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        if t.get("voided"):
            continue
        if str(t.get("stock_code") or "").strip() != code:
            continue
        side = str(t.get("side") or "").strip().lower()
        if not side.startswith("t0_"):
            continue
        t_sess = _trade_session_date(t)
        if sess and t_sess and t_sess != sess:
            continue
        if sess and not t_sess:
            # 无日期时：仅当 last_run 会话日与今日接近时仍匹配同票未作废腿
            continue
        matched.append(t)

    if not matched and sess:
        # 兜底：同票未作废 t0 腿且 ts 前缀匹配会话（部分旧记录 at 缺日期）
        for t in trades:
            if not isinstance(t, dict) or t.get("voided"):
                continue
            if str(t.get("stock_code") or "").strip() != code:
                continue
            side = str(t.get("side") or "").strip().lower()
            if not side.startswith("t0_"):
                continue
            ts = str(t.get("ts") or "")
            if ts.startswith(sess):
                matched.append(t)

    if not matched:
        return {"voided": 0, "cash_delta": 0.0, "stock_code": code}

    def _leg_sort_key(t: dict) -> str:
        return str(t.get("ts") or t.get("at") or "")

    matched.sort(key=_leg_sort_key)

    holdings = list(paper.get("holdings") or [])
    holding = next((h for h in holdings if str(h.get("stock_code") or "") == code), None)
    if holding is None:
        holding = {
            "stock_code": code,
            "stock_name": matched[0].get("stock_name") or code,
            "shares": 0.0,
            "cost": float(matched[0].get("price") or 0) or 0.0,
            "lots": [],
        }
        holdings.append(holding)
        paper["holdings"] = holdings

    ensure_lots(holding, as_of=sess or None)
    cash_delta = 0.0
    now = _now_iso()
    prev_day = prev_trading_day(sess) if sess else ""
    if not prev_day:
        prev_day = sess or str(now)[:10]

    # 逆序冲正：后发生的腿先撤（按 ts/at 排序后反转）
    for t in reversed(matched):
        leg_cash = float(t0_leg_cash_delta(t) or 0)
        # 原腿对现金的影响取反
        cash_delta += -leg_cash
        qty = float(t.get("shares") or 0)
        side = str(t.get("side") or "").strip().lower()
        if qty > 1e-9 and side.endswith("sell"):
            restore_sellable_lot(
                holding,
                qty,
                bought_date=prev_day,
                ts=str(t.get("ts") or now),
            )
        elif qty > 1e-9 and side.endswith("buy"):
            consume_lots_bought_on(holding, qty, bought_date=sess or prev_day)
        t["voided"] = True
        t["voided_at"] = now
        note = str(t.get("note") or "").strip()
        tag = " · 已删除冲正"
        if tag not in note:
            t["note"] = (note + tag).strip(" ·")

    _mark_t0_operation_logs_voided(
        paper, code=code, session_date=sess, matched=matched
    )

    paper["cash"] = round(float(paper.get("cash") or 0) + cash_delta, 2)
    # 清零空仓
    paper["holdings"] = [
        h for h in (paper.get("holdings") or []) if float(h.get("shares") or 0) > 1e-9
    ]
    # holding 可能已从列表剔除；仅当仍持仓时刷新 t0 元数据
    still = next(
        (h for h in (paper.get("holdings") or []) if str(h.get("stock_code") or "") == code),
        None,
    )
    if still is not None:
        from core.paper.tplus1 import sellable_shares as t1_sellable

        still["t0"] = {
            **dict(still.get("t0") or {}),
            "sellable_shares": t1_sellable(still, as_of=sess or None),
            "last_date": sess or (still.get("t0") or {}).get("last_date"),
        }
    return {
        "voided": len(matched),
        "cash_delta": round(cash_delta, 2),
        "stock_code": code,
        "shares_end": float((still or holding).get("shares") or 0),
    }


def _clear_intraday_for_codes(
    codes: Optional[List[str]] = None,
    *,
    clear_all: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """删除盘中盯盘状态，便于 Worker 重新监视。不改账本。

    默认跳过已落账腿（legs_written>0）的票，避免清状态后重复落第一腿；
    force=True 时一并清除。
    """
    try:
        from core.t0.intraday import load_intraday_state, save_intraday_state
    except Exception:  # noqa: BLE001
        logger.debug("import intraday for clear failed", exc_info=True)
        return {"cleared": 0, "stock_codes": [], "skipped_with_legs": []}
    state = load_intraday_state()
    stocks = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    if not stocks:
        return {"cleared": 0, "stock_codes": [], "skipped_with_legs": []}

    if clear_all:
        targets = [str(c) for c in stocks.keys()]
    else:
        targets = [str(c or "").strip() for c in (codes or []) if str(c or "").strip()]

    removed: List[str] = []
    skipped_with_legs: List[str] = []
    for code in targets:
        if code not in stocks:
            continue
        st = stocks.get(code) if isinstance(stocks.get(code), dict) else {}
        legs = int((st or {}).get("legs_written") or 0)
        if legs > 0 and not force:
            skipped_with_legs.append(code)
            continue
        del stocks[code]
        removed.append(code)

    if removed:
        state["stocks"] = stocks
        try:
            save_intraday_state(state)
        except Exception:  # noqa: BLE001
            logger.debug("save intraday after clear failed", exc_info=True)
            return {"cleared": 0, "stock_codes": [], "skipped_with_legs": skipped_with_legs}
    return {
        "cleared": len(removed),
        "stock_codes": removed,
        "skipped_with_legs": skipped_with_legs,
    }


def _rebalance_report_from_legs(
    *,
    ranking: List[dict],
    sell_trades: List[dict],
    buy_trades: List[dict],
    holdings_before: List[dict],
    holdings_after: List[dict],
    risk_budget_skips: Optional[List[dict]] = None,
    score_rows: Optional[List[dict]] = None,
) -> List[dict]:
    """把分池/横截面腿转成交易执行页调仓报告行。

    ``ranking`` = 目标簿（选股结果）；``score_rows`` = 展示用全量打分
    （含低于 min_score 被踢出簿的票）。省略时回退 ranking。
    """
    sell_by = {str(t.get("stock_code")): t for t in sell_trades or []}
    buy_by = {str(t.get("stock_code")): t for t in buy_trades or []}
    skip_by = {
        str(s.get("stock_code")): s
        for s in (risk_budget_skips or [])
        if s.get("stock_code")
    }
    # 展示分：优先全量 scored；目标簿仅用于「是否在簿」判断
    display_rows = list(score_rows or []) or list(ranking or [])
    score_by = {
        str(r.get("stock_code")): r.get("score")
        for r in display_rows
        if r.get("stock_code")
    }
    # 卖出腿上若已带分，补进 lookup（持仓不在映射/簿时）
    for t in sell_trades or []:
        code = str(t.get("stock_code") or "")
        if code and code not in score_by and t.get("score") is not None:
            score_by[code] = t.get("score")
    hard_by = {
        str(r.get("stock_code")): str(r.get("reject_reason") or "硬拒绝")
        for r in display_rows
        if r.get("stock_code") and r.get("hard_reject")
    }
    for t in sell_trades or []:
        code = str(t.get("stock_code") or "")
        note = str(t.get("note") or "")
        if code and code not in hard_by and (
            t.get("hard_reject") or "追高" in note or "硬拒绝" in note
        ):
            hard_by[code] = note or "硬拒绝"
    book_codes = {
        str(r.get("stock_code"))
        for r in ranking or []
        if r.get("stock_code")
    }
    row_by = {
        str(r.get("stock_code")): r
        for r in display_rows
        if r.get("stock_code")
    }
    name_by: Dict[str, str] = {}
    for src in (holdings_before or []) + display_rows + (sell_trades or []) + (
        buy_trades or []
    ) + list(skip_by.values()):
        code = str(src.get("stock_code") or "")
        if code:
            raw_nm = str(src.get("stock_name") or "").strip()
            if raw_nm and raw_nm != code:
                name_by[code] = raw_nm
    old_shares = {
        str(h.get("stock_code")): float(h.get("shares") or 0)
        for h in holdings_before or []
        if h.get("stock_code")
    }
    new_shares = {
        str(h.get("stock_code")): float(h.get("shares") or 0)
        for h in holdings_after or []
        if h.get("stock_code")
    }
    codes = sorted(
        set(old_shares) | set(new_shares) | set(sell_by) | set(buy_by) | set(skip_by)
    )
    rows: List[dict] = []
    for code in codes:
        o = old_shares.get(code, 0.0)
        n = new_shares.get(code, 0.0)
        decision = "持有"
        reason = ""
        if code in sell_by:
            st = sell_by[code]
            note = str(st.get("note") or "")
            prior_trim = bool(st.get("sentiment_prior")) or ("舆情先验" in note)
            if prior_trim and n > 1e-9:
                decision = "减仓"
            else:
                decision = "卖出"
            reason = note or "分池调仓卖出"
        elif code in buy_by:
            decision = "买入"
            reason = buy_by[code].get("note") or "分池调仓买入"
        elif code in skip_by and abs(n - o) < 1e-9:
            decision = "跳过"
            reason = skip_by[code].get("reason") or "风险预算跳过"
        elif abs(n - o) < 1e-9:
            decision = "持有"
            if code in book_codes:
                reason = "仍在目标簿内"
            elif code in hard_by:
                reason = hard_by[code]
            elif score_by.get(code) is not None:
                reason = "未进目标簿 · 滞回持有"
            else:
                reason = "未纳入本轮打分"
        rank_row = dict(row_by.get(code) or {})
        for src in (skip_by.get(code), sell_by.get(code), buy_by.get(code)):
            if not isinstance(src, dict):
                continue
            for k in (
                "predicted_score",
                "predicted_score_eod",
                "predicted_score_tau",
                "predicted_score_path",
                "predicted_score_on",
                "predicted_score_nowcast",
                "predicted_score_blend",
                "y_trade",
                "y_path",
                "y_on",
                "y_nowcast",
                "y_nc",
                "y_tau",
                "y_fuse",
                "ranking_score",
                "score",
                "score_rem",
            ):
                if rank_row.get(k) is None and src.get(k) is not None:
                    rank_row[k] = src.get(k)
        label = rank_row.get("cluster_label")
        # 字段名与 paper_cycle / 交易执行页 renderRebalanceReport 对齐
        delta = n - o
        if not reason:
            if abs(delta) < 1e-9:
                if code in book_codes:
                    reason = "仍在目标簿内"
                elif score_by.get(code) is not None:
                    reason = "未进目标簿 · 滞回持有"
                else:
                    reason = "未纳入本轮打分"
            elif delta > 0:
                reason = "分池调仓买入"
            else:
                reason = "分池调仓卖出"
        below = bool(rank_row.get("below_min_score"))
        if below and decision == "卖出" and "min_score" not in str(reason):
            reason = (reason or "分池调仓卖出") + " · 低于 min_score"
        st_row = sell_by.get(code) or {}
        sk_row = skip_by.get(code) or {}
        prior_flag = bool(st_row.get("sentiment_prior") or sk_row.get("sentiment_prior")) or (
            "舆情先验" in str(reason or "")
            or "sentiment_prior" in str(reason or "")
        )
        hard_flag = bool(rank_row.get("hard_reject") or code in hard_by)
        oos_failed = bool(rank_row.get("oos_failed"))
        if not oos_failed:
            rms = str(rank_row.get("return_model_source") or "")
            oos_failed = rms.startswith("oos_failed") or str(
                rank_row.get("score_scale") or ""
            ) == "heuristic_0_100"
        row_out = {
            "stock_code": code,
            "stock_name": name_by.get(code) or code,
            "score": score_by.get(code),
            "decision": decision,
            "reason": reason,
            "old_shares": int(o),
            "new_shares": int(n),
            "shares_change": int(delta),
            "shares_before": o,
            "shares_after": n,
            "cluster_label": label,
            "in_book": code in book_codes,
            "oos_failed": oos_failed,
            "weight_source": rank_row.get("weight_source")
            or (
                f"cluster:{label}"
                if label and decision != "卖出"
                else None
            ),
            "score_global": rank_row.get("score_global"),
            "score_cluster": rank_row.get("score_cluster")
            if rank_row.get("score_cluster") is not None
            else rank_row.get("score"),
            "below_min_score": below,
            "hard_reject": hard_flag,
            "reject_reason": rank_row.get("reject_reason") or hard_by.get(code),
            "sentiment_prior": bool(prior_flag)
            if (code in sell_by or code in skip_by)
            else False,
        }
        # tip：从打分行透传 book_fields
        try:
            from core.signal.service import get_default_signal_service

            src = dict(rank_row) if isinstance(rank_row, dict) else {}
            if src.get("predicted_score") is None and src.get("predicted_score_blend") is None:
                sc = score_by.get(code)
                if sc is not None:
                    src.setdefault("score", sc)
                    src.setdefault("predicted_score", sc)
            row_out.update(get_default_signal_service().book_fields(src))
            for k in (
                "predicted_score",
                "score_formula",
                "score_formula_terms",
                "reasons",
                "factor_coefficients",
                "return_model_source",
                "cluster_mode",
                "cluster_version",
            ):
                if src.get(k) is not None and row_out.get(k) is None:
                    row_out[k] = src.get(k)
            # book_fields 可能带回 return_model_source / score_scale；再对齐 oos 旗标
            if not row_out.get("oos_failed"):
                rms = str(row_out.get("return_model_source") or src.get("return_model_source") or "")
                row_out["oos_failed"] = rms.startswith("oos_failed") or str(
                    row_out.get("score_scale") or src.get("score_scale") or ""
                ) == "heuristic_0_100"
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_trades.py", exc_info=True)
            pass
        rows.append(row_out)
    def _sort_key(row: dict) -> tuple:
        # 预演调仓：按分数降序；同分时买卖优先于持有
        sc_raw = row.get("score")
        try:
            sc = float(sc_raw) if sc_raw is not None and sc_raw != "" else None
        except (TypeError, ValueError):
            sc = None
        # None 排最后
        sc_rank = -(sc if sc is not None else -1.0)
        missing = 0 if sc is not None else 1
        dec = str(row.get("decision") or "")
        if "卖" in dec or "买" in dec or "减" in dec or "加" in dec:
            action = 0
        elif "跳过" in dec:
            action = 1
        else:
            action = 2
        return (missing, sc_rank, action)

    rows.sort(key=_sort_key)
    try:
        from core.t0.intraday import stamp_resolved_stock_names

        stamp_resolved_stock_names(rows, name_by_code=name_by)
    except Exception:  # noqa: BLE001
        logger.debug("stamp rebalance report names skipped", exc_info=True)
    return rows


def _paper_mutation_token(paper: dict) -> tuple:
    """轻量指纹：确认落账前检测账本是否被并发改写。"""
    holdings = paper.get("holdings") or []
    return (
        round(float(paper.get("cash") or 0), 4),
        tuple(
            sorted(
                (
                    str(h.get("stock_code") or ""),
                    round(float(h.get("shares") or 0), 4),
                )
                for h in holdings
                if isinstance(h, dict)
            )
        ),
        len(paper.get("trades") or []),
        len(paper.get("operation_log") or []),
    )


class PaperTradesMixin:
    def rebalance(
        self,
        *,
        top_k: Optional[int] = None,
        dry_run: bool = False,
        strategy: Optional[str] = None,
        offline_only: bool = True,
    ) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")

        _ = strategy

        from core.paper.rebalance.watching_matrix import (
            simulate_watching_matrix_preview,
        )

        paper_ro = load_paper(self.path)
        token0 = _paper_mutation_token(paper_ro)
        work = copy.deepcopy(paper_ro)
        holdings_before = copy.deepcopy(work.get("holdings") or [])
        if not dry_run:
            capture_mark_snapshot(work)
        result = simulate_watching_matrix_preview(
            work, top_k=top_k, dry_run=dry_run, offline_only=bool(offline_only)
        )
        if not (result.get("success") or result.get("ok")):
            return {**result, "matrix_mode": True}

        from core.paper.open_fill import apply_next_open_commit

        work, result = apply_next_open_commit(
            paper_ro,
            work,
            result,
            dry_run=dry_run,
            source="matrix",
        )

        sell_trades = list(result.get("sell_trades") or [])
        buy_trades = list(result.get("buy_trades") or [])
        report = list(result.get("rebalance_report") or [])
        if not report:
            report = _rebalance_report_from_legs(
                ranking=[],
                sell_trades=sell_trades,
                buy_trades=buy_trades,
                holdings_before=holdings_before,
                holdings_after=work.get("holdings") or [],
                risk_budget_skips=result.get("risk_budget_skips"),
            )
        summary = result.get("summary") or mark_to_market(work)
        try:
            from core.paper.rebalance import attach_change_pct_to_rebalance_report

            attach_change_pct_to_rebalance_report(report, summary=summary)
        except Exception:  # noqa: BLE001
            logger.debug("attach_change_pct matrix failed", exc_info=True)

        base_out = {
            **result,
            "success": True,
            "ok": True,
            "mode": "watching_matrix",
            "dry_run": dry_run,
            "matrix_mode": True,
            "cluster_mode": False,
            "sell_trades": sell_trades,
            "buy_trades": buy_trades,
            "rebalance_report": report,
            "summary": summary,
            "confirm_supported": True,
        }
        if result.get("fill_action"):
            base_out["fill_action"] = result.get("fill_action")
            base_out["fill_phase"] = result.get("fill_phase")
            base_out["execution_mode"] = result.get("execution_mode")
        if result.get("pending_orders") is not None:
            base_out["pending_orders"] = result.get("pending_orders")
        if result.get("staged"):
            base_out["staged"] = True
        if result.get("note"):
            base_out["note"] = result.get("note")

        if dry_run:
            if not base_out.get("note"):
                base_out["note"] = "矩阵预演 · 未写 paper.json"
            return base_out

        fill_action = str(result.get("fill_action") or "immediate")
        append_snapshot(work, summary)
        deferred = fill_action in ("staged", "kept_pending")
        if not deferred:
            append_trade_legs_to_operation_log(
                work,
                sell_trades,
                buy_trades,
                origin="matrix",
                source="follow",
            )
        verb = (
            "挂开盘单"
            if fill_action == "staged"
            else (
                "保留挂单"
                if fill_action == "kept_pending"
                else (
                    "开盘成交"
                    if fill_action.startswith("open_fill")
                    else "调仓"
                )
            )
        )
        append_operation_log(
            work,
            "watching_matrix_rebalance",
            detail=(
                f"矩阵 live {verb} · "
                f"卖 {len(sell_trades)} · 买 {len(buy_trades)}"
            ),
            meta={
                "mode": "watching_matrix",
                "matrix_mode": True,
                "buy_count": len(buy_trades),
                "sell_count": len(sell_trades),
                "fill_action": fill_action,
                "origin": "matrix",
                "source": "follow",
                "path_matrix": result.get("path_matrix"),
            },
        )

        from core.io_atomic import atomic_write_json
        from core.paper import trim_paper_lists

        with paper_write_lock(self.path):
            current = load_paper(self.path)
            if _paper_mutation_token(current) != token0:
                return {
                    "success": False,
                    "ok": False,
                    "mode": "watching_matrix",
                    "dry_run": False,
                    "matrix_mode": True,
                    "error": "账本已变更，请重新预演后再确认调仓",
                    "confirm_supported": True,
                }
            work.pop("watchlist", None)
            trim_paper_lists(work)
            atomic_write_json(self.path, work)
        try:
            from core.paper.rebalance.auto_worker import (
                mark_run_session,
                resolve_session,
                should_record_follow_run,
            )

            if should_record_follow_run(fill_action):
                sess = resolve_session()
                mark_run_session(
                    sess,
                    fill_action=fill_action,
                    buy_count=len(buy_trades),
                    sell_count=len(sell_trades),
                    note=str(base_out.get("note") or ""),
                    source="follow",
                )
                try:
                    from core.paper.rebalance.desk import persist_desk_from_result

                    persist_desk_from_result(
                        base_out, sess, source="follow", filled=True
                    )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "persist rebalance desk after follow commit failed",
                        exc_info=True,
                    )
        except Exception:  # noqa: BLE001
            logger.debug("mark auto-rebalance session after follow commit failed", exc_info=True)
        return base_out


    def buy(self, *, stock_code: str, amount: Optional[float] = None, shares: Optional[float] = None) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.paper import manual_buy

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            from core.paper.open_fill import require_open_fill

            blocked = require_open_fill(paper, action="手动买入")
            if blocked:
                raise ValueError(blocked)
            capture_mark_snapshot(paper)
            trade = manual_buy(paper, stock_code, amount=amount, shares=shares)
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            from core.paper.costs import fee_fields_from_trade

            fee_meta = fee_fields_from_trade(trade)
            append_operation_log(
                paper, "buy",
                detail=f"买入 {trade.get('stock_name') or trade.get('stock_code')} {trade.get('shares')}股 @ {trade.get('price')}",
                meta={
                    "stock_code": trade.get("stock_code"),
                    "stock_name": trade.get("stock_name"),
                    "shares": trade.get("shares"),
                    "price": trade.get("price"),
                    "amount": trade.get("amount") or trade.get("actual_cost"),
                    "origin": trade.get("origin") or "manual",
                    **fee_meta,
                },
            )
            save_paper(paper, self.path)
        out = self.status()
        out["trade"] = trade
        out["message"] = (
            f"已加仓 {trade.get('stock_name') or trade.get('stock_code')} "
            f"{trade.get('shares')}股 · {trade.get('amount')}元"
        )
        return out

    def sell(
        self,
        *,
        codes: Optional[list] = None,
        stock_code: Optional[str] = None,
        shares: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.paper import manual_sell
        from core.paper.exec import manual_sell_intents
        from core.paper.open_fill import (
            PHASE_CLOSED,
            get_rebalance_timing,
            is_next_open_mode,
            merge_pending_orders,
            paper_fill_phase,
            pending_from_trades,
            stage_pending,
        )

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            timing = get_rebalance_timing(paper)
            phase = paper_fill_phase(timing=timing)
            # 收盘后 / 非交易日：挂次日开盘卖出，不改仓
            if is_next_open_mode(paper) and phase == PHASE_CLOSED:
                intents = manual_sell_intents(
                    paper, codes=codes, stock_code=stock_code, shares=shares
                )
                incoming = pending_from_trades(
                    intents, [], source="manual_sell"
                )
                merged = merge_pending_orders(paper.get("pending_orders"), incoming)
                stage_pending(paper, merged)
                n_legs = len(incoming.get("legs") or [])
                target = str(merged.get("target_fill_date") or "")[:10]
                append_operation_log(
                    paper,
                    "sell",
                    detail=(
                        f"收盘后挂开盘卖出 {n_legs} 笔"
                        + (f"（目标 {target}）" if target else "")
                    ),
                    meta={
                        "staged": True,
                        "source": "manual_sell",
                        "codes": [t.get("stock_code") for t in intents],
                        "target_fill_date": target or None,
                        "legs": n_legs,
                    },
                )
                save_paper(paper, self.path)
                out = self.status()
                out["trades"] = []
                out["staged"] = True
                out["fill_action"] = "staged"
                out["pending_orders"] = merged
                out["message"] = (
                    f"已挂 {n_legs} 笔次日开盘卖出"
                    + (f"（目标 {target}）" if target else "")
                    + " · 持仓未改，开盘窗按开盘价尝试成交（涨停/跌停/停牌可能跳过）"
                )
                return out

            from core.paper.open_fill import require_open_fill

            blocked = require_open_fill(paper, action="手动卖出")
            if blocked:
                raise ValueError(blocked)
            capture_mark_snapshot(paper)
            trades = manual_sell(paper, codes=codes, stock_code=stock_code, shares=shares)
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            from core.paper.costs import fee_fields_from_trade, pnl_fields_from_trade

            for t in trades:
                fee_meta = fee_fields_from_trade(t)
                pnl_meta = pnl_fields_from_trade(t)
                append_operation_log(
                    paper, "sell",
                    detail=f"卖出 {t.get('stock_name') or t.get('stock_code')} {t.get('shares')}股 @ {t.get('price')}",
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "manual",
                        **fee_meta,
                        **pnl_meta,
                    },
                )
            save_paper(paper, self.path)
        out = self.status()
        out["trades"] = trades
        out["message"] = f"已卖出 {len(trades)} 笔"
        return out

    def holding_chart(self, stock_code: str, *, lookback: int = 60) -> Dict[str, Any]:
        """单只持仓：近 lookback 日收盘价 + 相对成本浮盈%（非整账净值）。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("请指定股票代码")

        from core.data.facade import bars_and_source

        paper = load_paper(self.path)
        holding = next(
            (
                h
                for h in (paper.get("holdings") or [])
                if str(h.get("stock_code") or "").strip() == code
            ),
            None,
        )
        if not holding:
            raise ValueError(f"持仓中没有 {code}")

        cost = float(holding.get("cost") or 0)
        shares = float(holding.get("shares") or 0)
        name = holding.get("stock_name") or code
        bars, src = bars_and_source(code, limit=max(10, min(int(lookback), 120)))
        points = []
        for b in bars or []:
            close = b.get("close")
            try:
                px = float(close)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            pnl_pct = round((px / cost - 1.0) * 100.0, 2) if cost > 0 else None
            points.append(
                {
                    "date": str(b.get("date") or ""),
                    "close": round(px, 4),
                    "pnl_pct": pnl_pct,
                    "market_value": round(px * shares, 2) if shares else None,
                }
            )
        return {
            "ok": True,
            "mode": "stock",
            "stock_code": code,
            "stock_name": name,
            "cost": cost,
            "shares": shares,
            "data_source": src,
            "points": points,
            "point_count": len(points),
        }

    def simulate_t0(
        self,
        *,
        rules: Optional[dict] = None,
        dry_run: bool = False,
        log_source: str = "paper_t0",
        skip_open_fill_gate: bool = False,
    ) -> Dict[str, Any]:
        """纸面底仓做 T（非实盘）。

        dry_run=True：只预演，不写账本；**不加写锁**（避免拉分钟线期间阻塞调仓/落账）。
        confirm 写账仍全程加写锁。
        仅 5m 第一触达；缺分钟线的票跳过（已删除日线模拟）。
        """
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        if dry_run:
            return self._simulate_t0_impl(
                rules=rules,
                dry_run=True,
                log_source=log_source,
                skip_open_fill_gate=skip_open_fill_gate,
            )
        with paper_write_lock(self.path):
            return self._simulate_t0_impl(
                rules=rules,
                dry_run=False,
                log_source=log_source,
                skip_open_fill_gate=skip_open_fill_gate,
            )

    def _simulate_t0_impl(
        self,
        *,
        rules: Optional[dict] = None,
        dry_run: bool = False,
        log_source: str = "paper_t0",
        skip_open_fill_gate: bool = False,
    ) -> Dict[str, Any]:
        """simulate_t0 的加锁实现：拉行情 → 做T模拟 → 写账本。"""
        from core.data.facade import bars_and_source
        from core.t0.rules import atr_pct_from_bars, simulate_t0_on_holdings

        paper = load_paper(self.path)

        if not dry_run and not skip_open_fill_gate:
            from core.paper.open_fill import require_open_fill

            blocked = require_open_fill(paper, action="做 T")
            if blocked:
                return {
                    "ok": False,
                    "success": False,
                    "dry_run": False,
                    "error": blocked,
                    "note": blocked,
                }
        holdings = paper.get("holdings") or []
        if not holdings:
            return {
                "ok": True,
                "success": True,
                "dry_run": dry_run,
                "trades": [],
                "pnl_total": 0.0,
                "results": [],
                "note": "无持仓，跳过做T",
            }

        run_rules = dict(rules or {})
        from core.execution import (
            execution_public_view,
            resolve_effective_execution,
            strip_execution_meta,
        )

        # 预判是否会用分钟（与下方拉取一致）；无持仓分钟时仍先按 paper channel 解析
        bundle = resolve_effective_execution(
            strategy=paper.get("strategy_id"),
            paper=paper,
            request_override=run_rules if run_rules else None,
            channel="paper",
            has_minute=None,
        )
        eff_t0 = strip_execution_meta(bundle["t0"])
        period = str(eff_t0.get("minute_period") or "5")

        # 与 Worker 一致：按当前交易会话对齐日线/分钟，禁止用未滚日的 bars[-1] 当今日
        _now = None
        try:
            from core.market.calendar import resolve_session_date
            from core.signal.session_pit import asof_session_final, shanghai_now
            from core.t0.intraday import session_in_market

            _now = shanghai_now()
            session_date = resolve_session_date(now=_now)
            in_market, _ = session_in_market(now=_now)
            session_closed = bool(
                session_date and asof_session_final(session_date, now=_now)
            )
        except Exception:  # noqa: BLE001
            logger.debug("session resolve for simulate_t0 failed", exc_info=True)
            session_date = None
            in_market = False
            session_closed = True
            _now = None
        # 盘中整单：defer 收盘回补；收盘后/休市：允许 eod（与 Worker force_session_close 对齐）
        defer_eod = bool(in_market) and not session_closed

        def _load_one_holding(h: dict) -> Optional[tuple]:
            code = str(h.get("stock_code") or "")
            if not code:
                return None
            bars, _src = bars_and_source(code, limit=40)
            if not bars:
                return None
            by_day: Dict[str, Any] = {}
            try:
                from core.ports.market import fetch_minute_bars, group_minute_bars_by_date

                if dry_run:
                    # 预演：先读本地仓；会话日缺 K / 明显落后盘面（如只到 10:00）则现场补拉，
                    # 与持仓涨跌 tip、按钮文案「不足可现场拉」一致。
                    from core.ports.market import resolve_market_code
                    from skills.common.minute_history import load_minute_cache

                    market, bare = resolve_market_code(code)
                    cached = (
                        load_minute_cache(
                            market, bare, period, min_bars=2, max_age_hours=168.0
                        )
                        if market and bare
                        else None
                    )
                    mbars = list(cached[0]) if cached else []
                    by_day = group_minute_bars_by_date(mbars) if mbars else {}
                    day_key = str(session_date or "")[:10]
                    day_bars = list(by_day.get(day_key) or []) if day_key else []
                    need_fetch = not bool(day_bars)
                    if not need_fetch and day_key:
                        try:
                            from core.signal.tail_anomaly_view import (
                                _session_day_needs_refresh,
                            )

                            need_fetch = bool(
                                _session_day_needs_refresh(
                                    day_bars,
                                    session_asof=day_key,
                                    now=_now,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "preview minute freshness check failed %s",
                                code,
                                exc_info=True,
                            )
                    if need_fetch:
                        fresh, _mmeta = fetch_minute_bars(
                            code,
                            period=period,
                            lookback_days=10,
                            use_cache=False,
                            max_age_hours=0.01,
                        )
                        if fresh:
                            by_day = group_minute_bars_by_date(fresh)
                else:
                    mbars, _mmeta = fetch_minute_bars(
                        code,
                        period=period,
                        lookback_days=10,
                        use_cache=True,
                    )
                    by_day = group_minute_bars_by_date(mbars) if mbars else {}
            except Exception:  # noqa: BLE001
                logger.debug("minute hydrate failed %s", code, exc_info=True)

            from core.t0.intraday import align_session_day_context

            aligned = align_session_day_context(
                session_date=session_date,
                daily_bars=list(bars),
                minute_by_day=by_day,
            )
            if aligned.get("pending") or not aligned.get("ok"):
                if not dry_run:
                    # 实盘：不回退昨收整段回放
                    return None
                # 预演：会话未开盘时可用最近完整日（分钟仍同日，禁止跨日）
                bar = dict(bars[-1])
                if len(bars) >= 2 and not bar.get("prev_close"):
                    prev_c = float(bars[-2].get("close") or 0)
                    if prev_c > 0:
                        bar["prev_close"] = prev_c
                hist = list(bars[:-1])
                day_key = str(bar.get("date") or "")[:10]
                minute_day = list(by_day.get(day_key) or []) or None
                atr = atr_pct_from_bars(hist, 14) if hist else None
                return code, bar, hist, atr, minute_day
            bar = dict(aligned.get("bar") or {})
            hist = list(aligned.get("hist") or [])
            minute_day = list(aligned.get("minute_bars") or []) or None
            atr = atr_pct_from_bars(hist, 14) if hist else None
            return code, bar, hist, atr, minute_day

        bars_by_code: Dict[str, Any] = {}
        atr_by_code: Dict[str, float] = {}
        hist_bars_by_code: Dict[str, Any] = {}
        minute_bars_by_code: Dict[str, Any] = {}
        loaded: List[tuple] = []
        if dry_run and len(holdings) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            workers = min(4, len(holdings))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_load_one_holding, h) for h in holdings]
                for fut in as_completed(futs):
                    try:
                        row = fut.result()
                        if row:
                            loaded.append(row)
                    except Exception:  # noqa: BLE001
                        logger.debug("holding hydrate failed", exc_info=True)
        else:
            for h in holdings:
                row = _load_one_holding(h)
                if row:
                    loaded.append(row)
        for code, bar, hist, atr, minute_day in loaded:
            bars_by_code[code] = bar
            hist_bars_by_code[code] = hist
            if atr is not None:
                atr_by_code[code] = atr
            if minute_day:
                minute_bars_by_code[code] = minute_day

        stance_by_code: Dict[str, Any] = {}
        coup_mode = str((bundle.get("coupling") or {}).get("t0_vs_stance") or "independent")
        if coup_mode != "independent" and holdings:
            try:
                from core.paper import run_signal_scan
                from core.stance import compute_buy_stance

                codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
                pool = run_signal_scan(paper, stock_codes=codes)
                for item in pool or []:
                    c = str(item.get("stock_code") or "")
                    if not c:
                        continue
                    st = compute_buy_stance(
                        quote={"success": True},
                        signal_item=item,
                    )
                    stance_by_code[c] = st.get("stance_code")
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_trades.py", exc_info=True)
                stance_by_code = {}

        if not dry_run:
            capture_mark_snapshot(paper)

        scores_by_code = None
        if str(eff_t0.get("direction") or "") == "dual_y":
            try:
                from core.t0.score_policy import (
                    load_scores_map_for_codes,
                    resolve_score_as_of,
                )

                codes = [str(h.get("stock_code") or "") for h in holdings if h.get("stock_code")]
                # ledger 决策日 = hist 末日（T−1）；禁止用当日 bar.date
                as_of = None
                for code in codes:
                    as_of = resolve_score_as_of(
                        hist_bars=hist_bars_by_code.get(code),
                        day_bar=bars_by_code.get(code),
                    )
                    if as_of:
                        break
                y_src = str(eff_t0.get("y_score_source") or "compute")
                scores_by_code = load_scores_map_for_codes(
                    codes,
                    as_of=as_of or None,
                    source=y_src,
                    hist_bars_by_code=hist_bars_by_code or None,
                    day_bars_by_code=bars_by_code or None,
                    allow_fallback=(y_src != "compute"),
                    rules=eff_t0,
                )
            except Exception:  # noqa: BLE001
                logger.debug("dual_y scores hydrate failed", exc_info=True)
                scores_by_code = None

        # 盘中已落账腿的票禁止整单回放，避免在已变簿上再开一轮
        skip_codes: set = set()
        try:
            from core.market.calendar import resolve_session_date
            from core.signal.session_pit import shanghai_now
            from core.t0.intraday import load_intraday_state

            sess = resolve_session_date(now=shanghai_now())
            ist = load_intraday_state()
            if str(ist.get("session_date") or "") == str(sess or ""):
                stocks_st = ist.get("stocks") if isinstance(ist.get("stocks"), dict) else {}
                for c, st in stocks_st.items():
                    if isinstance(st, dict) and int(st.get("legs_written") or 0) > 0:
                        skip_codes.add(str(c))
        except Exception:  # noqa: BLE001
            logger.debug("load open intraday legs for simulate_t0 failed", exc_info=True)

        result = simulate_t0_on_holdings(
            paper,
            bars_by_code=bars_by_code,
            rules=eff_t0,
            dry_run=dry_run,
            atr_by_code=atr_by_code,
            hist_bars_by_code=hist_bars_by_code,
            minute_bars_by_code=minute_bars_by_code or None,
            stance_by_code=stance_by_code or None,
            coupling=bundle.get("coupling"),
            scores_by_code=scores_by_code or None,
            log_source=str(log_source or "paper_t0"),
            skip_codes=skip_codes or None,
            defer_eod=defer_eod,
        )
        result["execution"] = execution_public_view(bundle)
        if dry_run:
            miss = sum(
                1
                for c in (bars_by_code or {})
                if c and c not in (minute_bars_by_code or {})
            )
            if miss:
                result["minute_cache_miss"] = miss
                note = str(result.get("note") or "").strip()
                hint = f"预演补拉后仍缺 5m · {miss} 只已跳过"
                result["note"] = f"{note} · {hint}" if note else hint
            return {"ok": True, **result}

        _write_t0_auto_last_run(paper, result=result, source=str(log_source or "paper_t0"))
        try:
            from core.t0.intraday import sync_intraday_state_from_full_run

            sync_intraday_state_from_full_run(result.get("results"))
        except Exception:  # noqa: BLE001
            logger.debug("sync intraday state after simulate_t0 failed", exc_info=True)

        summary = mark_to_market(paper)
        append_snapshot(paper, summary)
        save_paper(paper, self.path)
        return {"ok": True, **result, "summary": summary}

    def t0_auto_status(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        paper = load_paper(self.path)
        cfg = _t0_auto_from_paper(paper)
        lr = cfg.get("last_run") if isinstance(cfg.get("last_run"), dict) else None
        need_persist = _hydrate_t0_last_run_display(lr) or _stamp_book_ts_from_paper(
            lr, paper
        )
        if need_persist:
            try:
                with paper_write_lock(self.path):
                    paper = load_paper(self.path)
                    rules = dict(paper.get("rules") or {})
                    auto = _normalize_t0_auto(rules.get("t0_auto"))
                    h = _hydrate_t0_last_run_display(auto.get("last_run"))
                    s = _stamp_book_ts_from_paper(auto.get("last_run"), paper)
                    if h or s:
                        rules["t0_auto"] = auto
                        paper["rules"] = rules
                        save_paper(paper, self.path)
                    cfg = auto
            except Exception:  # noqa: BLE001
                logger.debug("persist hydrated t0 last_run failed", exc_info=True)
        return {"ok": True, "t0_auto": cfg}

    def delete_t0_records(
        self,
        *,
        stock_codes: Optional[List[str]] = None,
        reverse_ledger: bool = True,
    ) -> Dict[str, Any]:
        """删除落账明细；默认冲正对应 t0_* 成交腿（现金/批次）并清盘中状态。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        codes = [
            str(c or "").strip()
            for c in (stock_codes or [])
            if str(c or "").strip()
        ]
        if not codes:
            raise ValueError("请指定要删除的股票代码")
        code_set = set(codes)

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            rules = dict(paper.get("rules") or {})
            cfg = _normalize_t0_auto(rules.get("t0_auto"))
            lr = cfg.get("last_run") if isinstance(cfg.get("last_run"), dict) else None
            if not lr:
                raise ValueError("尚无落账明细可删")
            sess = str(lr.get("session_date") or "")[:10] or None
            prev_rows = [
                r
                for r in (lr.get("results") or [])
                if isinstance(r, dict) and r.get("stock_code")
            ]
            kept = [r for r in prev_rows if str(r.get("stock_code") or "") not in code_set]
            removed_rows = [
                r for r in prev_rows if str(r.get("stock_code") or "") in code_set
            ]
            if not removed_rows:
                raise ValueError("落账明细中未找到指定股票")

            void_stats: List[Dict[str, Any]] = []
            cash_delta = 0.0
            voided_legs = 0
            if reverse_ledger:
                for row in removed_rows:
                    code = str(row.get("stock_code") or "").strip()
                    row_sess = str(row.get("date") or sess or "")[:10] or sess
                    st = _void_t0_trades_for_code(
                        paper, code, session_date=row_sess
                    )
                    void_stats.append(st)
                    cash_delta += float(st.get("cash_delta") or 0)
                    voided_legs += int(st.get("voided") or 0)

            trade_count, pnl_total = _summarize_t0_trade_results(kept)
            if kept:
                cfg["last_run"] = {
                    **dict(lr),
                    "trade_count": trade_count,
                    "pnl_total": pnl_total,
                    "results": kept,
                    "ts": lr.get("ts"),
                }
            else:
                cfg["last_run"] = None
            rules["t0_auto"] = cfg
            paper["rules"] = rules
            paper["updated_at"] = _now_iso()

            cleared_info = _clear_intraday_for_codes(
                [str(r.get("stock_code") or "") for r in removed_rows],
                force=True,
            )
            cleared = int(cleared_info.get("cleared") or 0)
            append_operation_log(
                paper,
                "t0_void",
                detail=(
                    f"删除做T落账 · {len(removed_rows)} 票"
                    + (f" · 冲正 {voided_legs} 腿" if reverse_ledger else " · 仅明细")
                ),
                meta={
                    "origin": "t0",
                    "stock_codes": [str(r.get("stock_code")) for r in removed_rows],
                    "session_date": sess,
                    "reverse_ledger": bool(reverse_ledger),
                    "voided_legs": voided_legs,
                    "cash_delta": round(cash_delta, 2),
                    "intraday_cleared": cleared,
                },
            )
            summary = mark_to_market(paper)
            if reverse_ledger:
                append_snapshot(paper, summary)
            save_paper(paper, self.path)

        return {
            "ok": True,
            "removed": len(removed_rows),
            "stock_codes": [str(r.get("stock_code")) for r in removed_rows],
            "voided_legs": voided_legs,
            "cash_delta": round(cash_delta, 2),
            "reverse_ledger": bool(reverse_ledger),
            "intraday_cleared": cleared,
            "void_stats": void_stats,
            "t0_auto": _t0_auto_from_paper(load_paper(self.path)),
            "summary": summary,
            "message": (
                f"已删除 {len(removed_rows)} 票落账"
                + (f"并冲正 {voided_legs} 腿" if reverse_ledger else "")
            ),
        }

    def clear_t0_intraday(
        self,
        *,
        stock_codes: Optional[List[str]] = None,
        clear_all: bool = False,
        force: bool = False,
    ) -> Dict[str, Any]:
        """清理今日盯盘状态（仅盘中状态文件，不冲正账本）。"""
        codes = [
            str(c or "").strip()
            for c in (stock_codes or [])
            if str(c or "").strip()
        ]
        if not clear_all and not codes:
            raise ValueError("请指定股票代码，或 clear_all=true")
        info = _clear_intraday_for_codes(
            codes if not clear_all else None,
            clear_all=bool(clear_all),
            force=bool(force),
        )
        desk = {}
        try:
            from core.t0.intraday import build_intraday_desk_status

            desk = build_intraday_desk_status()
        except Exception:  # noqa: BLE001
            logger.debug("build desk after clear failed", exc_info=True)
        skipped = list(info.get("skipped_with_legs") or [])
        cleared_n = int(info.get("cleared") or 0)
        msg = f"已清理盯盘 {cleared_n} 票"
        if skipped:
            msg += f" · 跳过已落账 {len(skipped)} 票（需 force 或先删落账）"
        return {
            "ok": True,
            **info,
            "force": bool(force),
            "clear_all": bool(clear_all),
            "intraday": desk,
            "message": msg,
        }

    @staticmethod
    def rebalance_worker_status() -> Dict[str, Any]:
        from core.paper.rebalance.auto_worker import rebalance_auto_worker
        from core.paper.rebalance.desk import build_rebalance_desk_status

        worker = rebalance_auto_worker.status()
        try:
            desk = build_rebalance_desk_status(worker=worker)
        except Exception:  # noqa: BLE001
            logger.debug("build_rebalance_desk_status failed", exc_info=True)
            desk = {"rows": [], "universe_count": 0, "note": "盯盘状态读取失败"}
        return {"ok": True, "worker": worker, "desk": desk}

    @staticmethod
    def set_rebalance_worker(enabled: bool) -> Dict[str, Any]:
        from core.paper.rebalance.auto_worker import rebalance_auto_worker

        return {"ok": True, **rebalance_auto_worker.set_enabled(bool(enabled))}

    @staticmethod
    def t0_worker_status() -> Dict[str, Any]:
        from core.t0.auto_worker import t0_auto_worker
        from core.t0.intraday import build_intraday_desk_status

        try:
            desk = build_intraday_desk_status()
        except Exception:  # noqa: BLE001
            logger.debug("build_intraday_desk_status failed", exc_info=True)
            desk = {"rows": [], "universe_count": 0, "note": "盘中状态读取失败"}
        return {"ok": True, "worker": t0_auto_worker.status(), "intraday": desk}

    def set_t0_worker(self, enabled: bool) -> Dict[str, Any]:
        from core.t0.auto_worker import t0_auto_worker

        on = bool(enabled)
        out = t0_auto_worker.set_enabled(on)
        if os.path.isfile(self.path):
            saved = self.save_t0_auto({"enabled": on})
            out["t0_auto"] = saved.get("t0_auto")
        return {"ok": True, **out}

    def save_t0_auto(self, patch: Optional[dict] = None) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        patch = patch or {}
        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            rules = dict(paper.get("rules") or {})
            cfg = _normalize_t0_auto(rules.get("t0_auto"))
            if "enabled" in patch:
                cfg["enabled"] = bool(patch.get("enabled"))
            sched = str(patch.get("schedule") or "").strip()
            if sched in ("after_close", "with_paper_daily"):
                cfg["schedule"] = sched
            rules["t0_auto"] = cfg
            paper["rules"] = rules
            paper["updated_at"] = _now_iso()
            save_paper(paper, self.path)
            saved = dict(cfg)
        return {"ok": True, "t0_auto": saved, "message": "已保存自动做T配置"}

    def run_t0_auto(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        skip_open_fill_gate: bool = False,
    ) -> Dict[str, Any]:
        """例行/立即自动做 T。force=True 时忽略 enabled 开关（仍受 open_fill 等门禁）。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        paper = load_paper(self.path)
        cfg = _t0_auto_from_paper(paper)
        if not cfg.get("enabled") and not force and not dry_run:
            return {
                "ok": True,
                "success": True,
                "skipped": True,
                "reason": "自动做T未启用",
                "t0_auto": cfg,
            }
        src = "paper_t0_auto" if not dry_run else "paper_t0_preview"
        out = self.simulate_t0(
            dry_run=bool(dry_run),
            log_source=src if not dry_run else "paper_t0",
            skip_open_fill_gate=bool(skip_open_fill_gate),
        )
        out["t0_auto"] = _t0_auto_from_paper(load_paper(self.path)) if not dry_run else cfg
        if dry_run:
            out["skipped"] = False
        return out

    def run_t0_intraday_tick(
        self,
        *,
        log_source: str = "paper_t0_auto",
        skip_open_fill_gate: bool = True,
        force_session_close: bool = False,
    ) -> Dict[str, Any]:
        """5m 盯盘增量落账：用截至当前的分钟线回放，新触达腿即时写账。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.data.facade import bars_and_source
        from core.execution import execution_public_view, resolve_effective_execution, strip_execution_meta
        from core.paper.tplus1 import sellable_shares as t1_sellable
        from core.t0.config import T0_INTRADAY_MINUTE_CACHE_HOURS, T0_INTRADAY_MINUTE_LOOKBACK_DAYS
        from core.t0.intraday import (
            accept_intraday_dual_y_scores,
            align_session_day_context,
            latest_minute_bar_ts,
            load_intraday_state,
            past_morning_close,
            run_intraday_session_tick,
            should_process_intraday_stock,
            unlock_retryable_skipped,
        )
        from core.t0.rules import atr_pct_from_bars

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            if not skip_open_fill_gate:
                from core.paper.open_fill import require_open_fill

                blocked = require_open_fill(paper, action="做 T")
                if blocked:
                    return {"ok": False, "success": False, "error": blocked, "note": blocked}

            holdings = paper.get("holdings") or []
            if not holdings:
                return {
                    "ok": True,
                    "success": True,
                    "skipped": True,
                    "reason": "无持仓",
                    "trade_count": 0,
                }

            from core.market.calendar import resolve_session_date
            from core.signal.session_pit import shanghai_now

            sess = resolve_session_date(now=shanghai_now())
            intraday_state = load_intraday_state()
            state_aligned = str(intraday_state.get("session_date") or "") == str(sess or "")
            if not state_aligned:
                intraday_state = {"session_date": sess, "stocks": {}, "results": []}
            stock_states = (
                dict(intraday_state.get("stocks") or {})
                if isinstance(intraday_state.get("stocks"), dict)
                else {}
            )
            unlocked_any = False
            state_dirty = not state_aligned
            for _code, _st in list(stock_states.items()):
                _u = unlock_retryable_skipped(_st if isinstance(_st, dict) else {})
                if _u is not None:
                    stock_states[_code] = _u
                    unlocked_any = True
            if unlocked_any:
                from core.t0.intraday import save_intraday_state

                save_intraday_state(
                    {
                        **intraday_state,
                        "session_date": sess,
                        "stocks": stock_states,
                        "results": list(intraday_state.get("results") or []),
                    }
                )

            bundle = resolve_effective_execution(
                strategy=paper.get("strategy_id"),
                paper=paper,
                request_override=None,
                channel="paper",
                has_minute=True,
            )
            eff_t0 = strip_execution_meta(bundle["t0"])
            period = str(eff_t0.get("minute_period") or "5")
            coup_mode = str((bundle.get("coupling") or {}).get("t0_vs_stance") or "independent")

            stance_by_code: Dict[str, Any] = {}
            if coup_mode != "independent":
                try:
                    from core.paper import run_signal_scan
                    from core.stance import compute_buy_stance

                    codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
                    pool = run_signal_scan(paper, stock_codes=codes)
                    for item in pool or []:
                        c = str(item.get("stock_code") or "")
                        if not c:
                            continue
                        st = compute_buy_stance(quote={"success": True}, signal_item=item)
                        stance_by_code[c] = st.get("stance_code")
                except Exception:  # noqa: BLE001
                    logger.debug("intraday stance hydrate failed", exc_info=True)

            holdings_ctx: List[dict] = []
            hist_by_code: Dict[str, list] = {}
            y_src = str(eff_t0.get("y_score_source") or "compute")
            force_dual_y_gate = bool(force_session_close) or past_morning_close()
            state_dirty = unlocked_any
            for h in holdings:
                code = str(h.get("stock_code") or "")
                if not code:
                    continue
                st0 = stock_states.get(code) if isinstance(stock_states.get(code), dict) else {}
                if not force_session_close and str(st0.get("phase") or "") in ("done", "skipped"):
                    continue
                bars, _src = bars_and_source(code, limit=40)
                if not bars:
                    continue
                minute_by_day: Dict[str, list] = {}
                try:
                    from core.ports.market import fetch_minute_bars, group_minute_bars_by_date

                    mbars, _meta = fetch_minute_bars(
                        code,
                        period=period,
                        lookback_days=T0_INTRADAY_MINUTE_LOOKBACK_DAYS,
                        use_cache=True,
                        max_age_hours=T0_INTRADAY_MINUTE_CACHE_HOURS,
                    )
                    minute_by_day = group_minute_bars_by_date(mbars) if mbars else {}
                except Exception:  # noqa: BLE001
                    logger.debug("intraday minute fetch failed for %s", code, exc_info=True)
                    minute_by_day = {}

                aligned = align_session_day_context(
                    session_date=str(sess or "")[:10] or None,
                    daily_bars=list(bars),
                    minute_by_day=minute_by_day,
                )
                if aligned.get("pending") or not aligned.get("ok"):
                    # 上午：日线未齐则继续等；午盘规则已在上方终锁无腿票
                    if code not in stock_states or not isinstance(stock_states.get(code), dict):
                        stock_states[code] = {
                            "phase": "idle",
                            "legs_written": 0,
                            "stock_name": str(h.get("stock_name") or ""),
                        }
                        state_dirty = True
                    elif not stock_states[code].get("stock_name") and h.get("stock_name"):
                        stock_states[code]["stock_name"] = str(h.get("stock_name") or "")
                        state_dirty = True
                    continue

                bar = dict(aligned.get("bar") or {})
                hist_by_code[code] = list(aligned.get("hist") or [])
                minute_bars = list(aligned.get("minute_bars") or [])

                latest_ts = latest_minute_bar_ts(minute_bars)
                if not should_process_intraday_stock(
                    st0,
                    latest_ts,
                    force_session_close=force_session_close,
                ):
                    continue

                atr = atr_pct_from_bars(hist_by_code.get(code) or [], 14)
                scores = None
                if str(eff_t0.get("direction") or "") == "dual_y":
                    try:
                        from core.t0.score_policy import (
                            load_scores_map_for_codes,
                            resolve_score_as_of,
                        )

                        hist_c = hist_by_code.get(code) or []
                        as_of_score = resolve_score_as_of(
                            hist_bars=hist_c, day_bar=bar
                        )
                        raw_scores = load_scores_map_for_codes(
                            [code],
                            as_of=as_of_score or None,
                            source=y_src,
                            hist_bars_by_code={code: hist_c},
                            day_bars_by_code={code: bar},
                            allow_fallback=(y_src != "compute"),
                            rules=eff_t0,
                        ).get(code)
                        scores = accept_intraday_dual_y_scores(raw_scores, source=y_src)
                    except Exception:  # noqa: BLE001
                        scores = None

                as_of = str(sess or bar.get("date") or "")[:10]
                sellable = t1_sellable(h, as_of=as_of or None)
                holdings_ctx.append(
                    {
                        "code": code,
                        "holding": h,
                        "bar": bar,
                        "minute_bars": minute_bars,
                        "cfg": dict(eff_t0),
                        "sellable": sellable,
                        "atr_pct": atr,
                        "hist_bars": hist_by_code.get(code),
                        "scores": scores,
                        "stance_code": stance_by_code.get(code),
                        "coupling_mode": coup_mode,
                        "force_dual_y_gate": force_dual_y_gate,
                        "force_session_close": bool(force_session_close),
                    }
                )

            if state_dirty:
                from core.t0.intraday import save_intraday_state

                save_intraday_state(
                    {
                        **intraday_state,
                        "session_date": sess,
                        "stocks": stock_states,
                        "results": list(intraday_state.get("results") or []),
                    }
                )

            if not holdings_ctx:
                return {
                    "ok": True,
                    "success": True,
                    "skipped": True,
                    "reason": "无新 5m K 线",
                    "trade_count": 0,
                }

            capture_mark_snapshot(paper)
            tick_out = run_intraday_session_tick(
                paper,
                holdings_ctx=holdings_ctx,
                log_source=str(log_source or "paper_t0_auto"),
            )
            new_trades = tick_out.get("new_trades") or []
            pnl_total = 0.0
            for snap in tick_out.get("results") or []:
                if isinstance(snap, dict) and snap.get("pnl") is not None and (snap.get("trades") or []):
                    pnl_total += float(snap.get("pnl") or 0)

            if new_trades:
                traded_codes = {
                    str(t.get("stock_code") or "")
                    for t in new_trades
                    if t.get("stock_code")
                }
                snap_rows = [
                    r
                    for r in (tick_out.get("results") or [])
                    if isinstance(r, dict)
                    and str(r.get("stock_code") or "") in traded_codes
                ]
                _write_t0_auto_last_run(
                    paper,
                    result={
                        "success": True,
                        "trades": new_trades,
                        "session_date": tick_out.get("session_date"),
                        "results": snap_rows,
                        "execution": execution_public_view(bundle),
                    },
                    source=str(log_source or "paper_t0_auto"),
                )

            paper["updated_at"] = _now_iso()
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            save_paper(paper, self.path)

        return {
            "ok": True,
            "success": True,
            "skipped": not bool(new_trades),
            "trade_count": len(new_trades),
            "pnl_total": round(pnl_total, 2),
            "skip_count": tick_out.get("skip_count"),
            "session_date": tick_out.get("session_date"),
            "t0_auto": _t0_auto_from_paper(load_paper(self.path)),
            "note": "5m 盯盘落账" if new_trades else "5m 盯盘 · 无新成交",
        }

