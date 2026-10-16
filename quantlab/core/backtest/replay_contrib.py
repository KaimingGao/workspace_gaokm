"""Replay day contribution, marks, and session overlay."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest.replay_calendar import _bars_by_date, _coverage_dates
from core.backtest.replay_fill import REPLAY_FILL_CLOCK, clamp_replay_fill_clock

logger = logging.getLogger(__name__)

def _fpx(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n if n > 0 and n == n else None
    s = str(v).replace("元", "").replace(",", "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except (TypeError, ValueError):
        return None
    return n if n > 0 and n == n else None


DAY_LEG_TOP = 8


def _watching_name_map() -> Dict[str, str]:
    """观察池代码→名称：落盘/static + a_code_name；绝不打行情（回测热路径）。"""
    try:
        from core.watching.store import read_watching, watchlist_names_for

        uni = read_watching() or {}
        wl = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        # allow_live=False：空名不逐只 get_quote，避免回测卡在「逐日调仓」进度 0
        names = watchlist_names_for(uni if isinstance(uni, dict) else {}, allow_live=False)
        out: Dict[str, str] = {}
        for code, name in zip(wl, names or []):
            nm = str(name or "").strip()
            if nm and nm != code:
                out[code] = nm
        need = [c for c in wl if c and c not in out]
        if need:
            try:
                from core.t0.intraday import resolve_stock_name
            except Exception:  # noqa: BLE001
                resolve_stock_name = None  # type: ignore[assignment]
            if resolve_stock_name is not None:
                for code in need:
                    try:
                        nm = str(resolve_stock_name(code) or "").strip()
                    except Exception:  # noqa: BLE001
                        nm = ""
                    if nm and nm != code:
                        out[code] = nm
        return out
    except Exception:  # noqa: BLE001
        logger.debug("watching name map failed in paper_replay", exc_info=True)
        return {}


def _usable_stock_name(code: str, *cands: Any) -> str:
    c = str(code or "").strip()
    for raw in cands:
        nm = str(raw or "").strip()
        if nm and nm != c and not nm.isdigit():
            return nm
    return ""


def _stamp_stock_names(
    rows: Sequence[dict],
    name_by_code: Optional[Dict[str, str]] = None,
) -> None:
    """把观察池中文名写到打分/成交行；缺名时不要用代码冒充。"""
    names = name_by_code or {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "").strip()
        if not code:
            continue
        nm = _usable_stock_name(code, names.get(code), row.get("stock_name"))
        if not nm:
            try:
                from core.t0.intraday import resolve_stock_name

                nm = resolve_stock_name(
                    code,
                    fallback=str(row.get("stock_name") or ""),
                    name_by_code=names,
                )
            except Exception:  # noqa: BLE001
                logger.debug("resolve replay stock name failed %s", code, exc_info=True)
                nm = ""
        if nm and nm != code:
            row["stock_name"] = nm


def _leg_stock_name(
    code: str,
    start: dict,
    end: dict,
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    mapped = str((name_by_code or {}).get(code) or "").strip()
    raw = str(end.get("stock_name") or start.get("stock_name") or "").strip()
    if mapped and mapped != code:
        return mapped
    if raw and raw != code:
        return raw
    return mapped or raw or code


def _holding_snap(holdings: Sequence[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        sh = _fpx(h.get("shares"))
        if not code or sh is None:
            continue
        row: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": str(h.get("stock_name") or code),
            "shares": float(sh),
        }
        mark = None
        for key in ("last_price", "mark_price", "price"):
            mark = _fpx(h.get(key))
            if mark is not None and mark > 0:
                row["mark_px"] = float(mark)
                break
        out[code] = row
    return out


def _bar_px(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    day: str,
    *fields: str,
) -> Optional[float]:
    bar = (date_maps.get(code) or {}).get(day) or {}
    for f in fields:
        v = _fpx(bar.get(f))
        if v is not None:
            return float(v)
    return None


def _resolve_mark_px(
    *,
    code: str,
    day: str,
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day_i: int,
    holding: Optional[dict] = None,
) -> Optional[float]:
    """收盘盯市：当日 close/open → 向前最近有 K 的交易日 → holding.last_price。

    停牌/缺行情时不能返回空，否则净值会把底仓静默记成 0。
    """
    px = _bar_px(date_maps, code, day, "close", "open")
    if px is not None and px > 0:
        return float(px)
    try:
        i0 = int(day_i)
    except (TypeError, ValueError):
        i0 = -1
    for j in range(i0 - 1, -1, -1):
        if j >= len(dates):
            continue
        px = _bar_px(date_maps, code, dates[j], "close", "open")
        if px is not None and px > 0:
            return float(px)
    if isinstance(holding, dict):
        for key in ("last_price", "mark_price", "price"):
            px = _fpx(holding.get(key))
            if px is not None and px > 0:
                return float(px)
    return None


def day_stock_legs(
    *,
    start: Dict[str, dict],
    end: Dict[str, dict],
    date_maps: Dict[str, Dict[str, dict]],
    prev_day: str,
    day: str,
    open_px: Dict[str, float],
    prev_equity: float,
    name_by_code: Optional[Dict[str, str]] = None,
    top: Optional[int] = DAY_LEG_TOP,
) -> Tuple[List[dict], int]:
    """当日个股盈亏：隔夜段 open−昨收 + 当日段 close−open，贡献=盈亏/昨净值。

    ``ret_pct`` 是持有段，不是股票全日收盘涨跌：清仓只计隔夜，新开只计开→收。
    停牌/缺当日 K：沿用昨收（或 snap ``mark_px``）盯市，盈亏记 0，避免漏出分票。
    ``top<=0`` 返回全日全部票（分票贡献汇总用）；净值悬停仍截 ``DAY_LEG_TOP``。
    """
    codes = sorted(set(start) | set(end))
    rows: List[dict] = []
    pe = float(prev_equity or 0.0)
    for code in codes:
        st = start.get(code) or {}
        en = end.get(code) or {}
        prev_sh = float(st.get("shares") or 0.0)
        now_sh = float(en.get("shares") or 0.0)
        prev_c = _bar_px(date_maps, code, prev_day, "close", "open")
        if prev_c is None:
            prev_c = _fpx(st.get("mark_px")) or _fpx(en.get("mark_px"))
        opn = _fpx((open_px or {}).get(code)) or _bar_px(
            date_maps, code, day, "open", "close"
        )
        close = _bar_px(date_maps, code, day, "close", "open")
        stale_mark = False
        if close is None and opn is None:
            carry = prev_c or _fpx(en.get("mark_px")) or _fpx(st.get("mark_px"))
            if carry is None or (prev_sh <= 0 and now_sh <= 0):
                continue
            # 停牌/缺 K：沿用昨收盯市，盈亏 0；净值与分票仍保留该腿。
            opn = close = float(carry)
            stale_mark = True
        if opn is None:
            opn = close
        if prev_c is None:
            prev_c = opn
        if close is None:
            close = opn
        if opn is None or prev_c is None or close is None:
            continue
        opn_f = float(opn)
        prev_c_f = float(prev_c)
        close_f = float(close)
        overnight_pnl = prev_sh * (opn_f - prev_c_f)
        intraday_pnl = now_sh * (close_f - opn_f)
        pnl = overnight_pnl + intraday_pnl
        contrib = (pnl / pe * 100.0) if pe > 0 else 0.0
        capital = 0.0
        stock_ret = None
        if prev_sh > 0 and prev_c_f > 0:
            capital = prev_sh * prev_c_f
            added = now_sh - prev_sh
            if added > 0 and opn_f > 0:
                capital += added * opn_f
            if capital > 0:
                stock_ret = pnl / capital * 100.0
        elif now_sh > 0 and opn_f > 0:
            capital = now_sh * opn_f
            stock_ret = (close_f / opn_f - 1.0) * 100.0
        name = _leg_stock_name(code, st, en, name_by_code)
        row: Dict[str, Any] = {
            "stock_code": code,
            "stock_name": name,
            "shares": round(now_sh, 0),
            "shares_prev": round(prev_sh, 0),
            "pnl": round(pnl, 2),
            "overnight_pnl": round(overnight_pnl, 2),
            "intraday_pnl": round(intraday_pnl, 2),
            "capital": round(capital, 2) if capital > 0 else 0.0,
            "ret_pct": None if stock_ret is None else round(stock_ret, 3),
            "contrib_pct": round(contrib, 4),
            "prev_close": round(prev_c_f, 4),
            "open": round(opn_f, 4),
            "close": round(close_f, 4),
        }
        if stale_mark:
            row["stale_mark"] = True
        rows.append(row)
    rows.sort(key=lambda r: abs(float(r.get("contrib_pct") or 0.0)), reverse=True)
    try:
        limit = DAY_LEG_TOP if top is None else int(top)
    except (TypeError, ValueError):
        limit = DAY_LEG_TOP
    if limit <= 0:
        return rows, 0
    n_more = max(0, len(rows) - limit)
    return rows[:limit], n_more


def _stock_contrib_acc(code: str, name: str) -> Dict[str, Any]:
    return {
        "stock_code": code,
        "stock_name": name,
        "pnl": 0.0,
        "overnight_pnl": 0.0,
        "intraday_pnl": 0.0,
        "contrib_sum_pct": 0.0,
        "hold_days": 0,
        "shares_end": 0.0,
        "shares_max": 0.0,
        "capital_sum": 0.0,
        "first_date": None,
        "last_date": None,
        "start_price": None,
        "end_price": None,
        "buy_count": 0,
        "sell_count": 0,
        "buy_shares": 0.0,
        "sell_shares": 0.0,
    }


def accumulate_day_contrib(
    acc: Dict[str, dict],
    rows: Sequence[dict],
    *,
    day: str,
) -> None:
    """把当日全日个股腿累进分票贡献槽（调用方须传未截断 rows）。"""
    dt = str(day or "")[:10]
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        slot = acc.get(code)
        if slot is None:
            slot = _stock_contrib_acc(code, str(r.get("stock_name") or code))
            acc[code] = slot
        else:
            nm = str(r.get("stock_name") or "").strip()
            if nm and nm != code:
                slot["stock_name"] = nm
        slot["pnl"] += float(r.get("pnl") or 0.0)
        slot["overnight_pnl"] += float(r.get("overnight_pnl") or 0.0)
        slot["intraday_pnl"] += float(r.get("intraday_pnl") or 0.0)
        slot["contrib_sum_pct"] += float(r.get("contrib_pct") or 0.0)
        prev_sh = float(r.get("shares_prev") or 0.0)
        now_sh = float(r.get("shares") or 0.0)
        if prev_sh > 0 or now_sh > 0:
            slot["hold_days"] += 1
            if not slot["first_date"]:
                slot["first_date"] = dt
                if prev_sh > 0:
                    slot["start_price"] = r.get("prev_close") or r.get("open")
                else:
                    slot["start_price"] = r.get("open")
            slot["last_date"] = dt
            if now_sh > 0:
                slot["end_price"] = r.get("close")
            else:
                slot["end_price"] = r.get("open")
        slot["shares_end"] = now_sh
        slot["shares_max"] = max(float(slot["shares_max"] or 0), prev_sh, now_sh)
        cap = float(r.get("capital") or 0.0)
        if cap > 0:
            slot["capital_sum"] += cap


def finalize_stock_contrib(
    acc: Dict[str, dict],
    *,
    initial_cash: float,
    trades: Sequence[dict] = (),
    name_by_code: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """窗口级分票贡献：盈亏=持仓盯市合计；贡献%=盈亏/回测本金。"""
    cash = float(initial_cash or 0.0)
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        code = str(t.get("stock_code") or "").strip()
        if not code or code not in acc:
            continue
        side = str(t.get("side") or "").strip().lower()
        try:
            sh = float(t.get("shares") or 0.0)
        except (TypeError, ValueError):
            sh = 0.0
        if side == "buy":
            acc[code]["buy_count"] += 1
            acc[code]["buy_shares"] += sh
        elif side == "sell":
            acc[code]["sell_count"] += 1
            acc[code]["sell_shares"] += sh
    out: List[dict] = []
    for slot in acc.values():
        pnl = round(float(slot["pnl"]), 2)
        hold_days = int(slot["hold_days"] or 0)
        avg_cap = (
            float(slot["capital_sum"]) / hold_days
            if hold_days and float(slot["capital_sum"] or 0) > 0
            else 0.0
        )
        ret = round(pnl / avg_cap * 100.0, 4) if avg_cap > 0 else None
        contrib = round(pnl / cash * 100.0, 4) if cash > 0 else 0.0
        row: Dict[str, Any] = {
            "stock_code": slot["stock_code"],
            "stock_name": slot["stock_name"],
            "pnl": pnl,
            "overnight_pnl": round(float(slot["overnight_pnl"]), 2),
            "intraday_pnl": round(float(slot["intraday_pnl"]), 2),
            "contrib_pct": contrib,
            "contrib_sum_pct": round(float(slot["contrib_sum_pct"]), 4),
            "return_pct": ret,
            "hold_days": hold_days,
            "buy_count": int(slot["buy_count"] or 0),
            "sell_count": int(slot["sell_count"] or 0),
            "shares_end": int(round(float(slot["shares_end"] or 0))),
            "shares_max": int(round(float(slot["shares_max"] or 0))),
            "first_date": slot["first_date"],
            "last_date": slot["last_date"],
        }
        try:
            sp = float(slot["start_price"]) if slot.get("start_price") is not None else None
        except (TypeError, ValueError):
            sp = None
        try:
            ep = float(slot["end_price"]) if slot.get("end_price") is not None else None
        except (TypeError, ValueError):
            ep = None
        if sp is not None and sp > 0:
            row["start_price"] = round(sp, 4)
        if ep is not None and ep > 0:
            row["end_price"] = round(ep, 4)
        out.append(row)
    _stamp_stock_names(out, name_by_code)
    out.sort(key=lambda r: -abs(float(r.get("pnl") or 0)))
    return out


def replay_session_day(
    *,
    now: Optional[datetime] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Optional[str]:
    """交易日且已过调仓钟才覆盖当天；钟前 / 非交易日返回 None。"""
    from core.market.calendar import is_trading_day, resolve_session_date

    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    if not session or not is_trading_day(session):
        return None
    if dt.strftime("%Y-%m-%d") == session:
        clock = clamp_replay_fill_clock(fill_clock)
        hh, mm = int(clock[:2]), int(clock[3:5])
        open_at = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt < open_at:
            return None
    return session


def _lookup_quote(quotes: Dict[str, Any], code: str) -> dict:
    if not quotes:
        return {}
    if code in quotes and isinstance(quotes.get(code), dict):
        return quotes[code]
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    for k, v in quotes.items():
        if not isinstance(v, dict):
            continue
        kd = "".join(ch for ch in str(k) if ch.isdigit())
        if digits and kd == digits:
            return v
    return {}


def _quote_open_px(q: Any) -> Optional[float]:
    """只要今开。现价/昨收不能冒充开盘，否则盘中回测会把 last 喂进 09:30 ŷ。"""
    if not isinstance(q, dict):
        return None
    return _fpx(q.get("open")) or _fpx(q.get("open_raw"))


def overlay_session_day_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    now: Optional[datetime] = None,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    fetch_quotes: bool = True,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Tuple[Dict[str, List[dict]], Optional[str], int]:
    """仓里还没有当日日 K 时，用今开补一根，让调仓钟能跑到当天。

    只要报价 open / open_raw（可带「元」）；现价 last 不能冒充开盘。
    特征仍只用昨收。成交优先 5m；无分钟时 09:30 用今开。真实收盘 / 隔夜 label 不写。
    仅当覆盖率日历末日恰好是上一交易日才补，避免测试夹具或过期仓跳到今天。
    """
    dates = _coverage_dates(stock_bars)
    session = replay_session_day(now=now, fill_clock=fill_clock)
    if not session or not dates:
        return stock_bars, None, 0
    last = dates[-1]
    if last >= session:
        return stock_bars, None, 0
    from core.market.calendar import prev_trading_day

    if last != prev_trading_day(session):
        return stock_bars, None, 0

    missing = [
        str(code)
        for code, bars in stock_bars.items()
        if session not in _bars_by_date(bars)
    ]
    if not missing:
        return stock_bars, session, 0

    quotes = dict(quotes_by_code or {})
    if fetch_quotes:
        still = [c for c in missing if not _quote_open_px(_lookup_quote(quotes, c))]
        if still:
            try:
                from core.data.facade import batch_get_quotes

                got = batch_get_quotes(still) or {}
                quotes.update(got)
            except Exception:  # noqa: BLE001
                logger.debug("session overlay quotes failed", exc_info=True)

    n = 0
    out = {str(c): list(bars or []) for c, bars in stock_bars.items()}
    for code in missing:
        open_px = _quote_open_px(_lookup_quote(quotes, code))
        if open_px is None:
            continue
        out[code].append(
            {
                "date": session,
                "open": open_px,
                "high": open_px,
                "low": open_px,
                "close": open_px,
                "volume": 0,
                "session_overlay": True,
            }
        )
        n += 1
    if n < 1:
        return stock_bars, None, 0
    return out, session, n
