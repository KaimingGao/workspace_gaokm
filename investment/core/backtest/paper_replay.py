"""纸面可实现回放：每个交易日 09:30 开盘走 rank_lots（y_fuse/y_on · 1000/2000 股）。

与 ``topk_research``（独立腿聚合）并列。现金地板默认 10 万、初始 50 万；T+1 仍生效。
历史回测手数大于 live（live 仍为 200/500）。
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ENGINE_ID = "paper_replay"
REPLAY_LOT_BASE = 1000
REPLAY_LOT_STRONG = 2000
REPLAY_INITIAL_CASH = 500_000.0
REPLAY_CASH_FLOOR = 100_000.0
# 历史回测 / 日报 / live rank_lots 缺省均为 1.2%。
REPLAY_RANK_ENTER = 0.012
REPLAY_RANK_STRONG = 0.012
REPLAY_FUSION_W_TRADE = 0.6
REPLAY_FUSION_W_NOWCAST = 0.4


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    """全交集日历。回测主路径用 ``_coverage_dates``；此处留给对照测试。"""
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _coverage_dates(
    stock_bars: Dict[str, List[dict]],
    *,
    min_coverage: float = 0.8,
) -> List[str]:
    """多数票有 K 的日期。全交集会被停牌/次新一张票卡死，lookback 拉长也不生效。"""
    n = max(1, len(stock_bars or {}))
    counts: Dict[str, int] = {}
    for bars in (stock_bars or {}).values():
        seen = set()
        for b in bars or []:
            if not isinstance(b, dict):
                continue
            d = str(b.get("date") or "").strip()[:10]
            if d and d not in seen:
                seen.add(d)
                counts[d] = counts.get(d, 0) + 1
    if n <= 3:
        thresh = n
    else:
        cov = min(1.0, max(0.5, float(min_coverage)))
        thresh = max(2, int(n * cov + 0.999999))
    dates = sorted(d for d, c in counts.items() if c >= thresh)
    return dates or sorted(counts)


def _replay_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    lookback: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    min_coverage: float = 0.8,
) -> Tuple[List[str], int]:
    """回测交易日：覆盖率日历的最后 lookback 根；前面垫特征窗，不占样本长度。"""
    cal = _coverage_dates(stock_bars, min_coverage=min_coverage)
    if not cal:
        return [], 0
    try:
        lb = int(lookback) if lookback is not None else 0
    except (TypeError, ValueError):
        lb = 0
    core = cal[-lb:] if lb > 0 else cal
    pad_n = max(int(min_history or 1), int(max_window or 1), 1)
    pre = [d for d in cal if d < core[0]]
    dates = pre[-pad_n:] + list(core)
    start_i = dates.index(core[0]) if core[0] in dates else 0
    if start_i < 1 and len(dates) > 1:
        start_i = 1
    return dates, start_i


def _fpx(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


DAY_LEG_TOP = 8


def _watching_name_map() -> Dict[str, str]:
    try:
        from core.watching.store import read_watching, watchlist_names_for

        uni = read_watching()
        wl = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        names = watchlist_names_for(uni)
        out: Dict[str, str] = {}
        for code, name in zip(wl, names or []):
            nm = str(name or "").strip()
            if nm and nm != code:
                out[code] = nm
        return out
    except Exception:  # noqa: BLE001
        logger.debug("watching name map failed in paper_replay", exc_info=True)
        return {}


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
        out[code] = {
            "stock_code": code,
            "stock_name": str(h.get("stock_name") or code),
            "shares": float(sh),
        }
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
) -> Tuple[List[dict], int]:
    """当日个股盈亏：隔夜段 open−昨收 + 当日段 close−open，贡献=盈亏/昨净值。

    ``ret_pct`` 是持有段，不是股票全日收盘涨跌：清仓只计隔夜，新开只计开→收。
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
        opn = _fpx((open_px or {}).get(code)) or _bar_px(
            date_maps, code, day, "open", "close"
        )
        close = _bar_px(date_maps, code, day, "close", "open")
        if close is None and opn is None:
            continue
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
        pnl = now_sh * (close_f - opn_f) + prev_sh * (opn_f - prev_c_f)
        contrib = (pnl / pe * 100.0) if pe > 0 else 0.0
        stock_ret = None
        if prev_sh > 0 and prev_c_f > 0:
            capital = prev_sh * prev_c_f
            added = now_sh - prev_sh
            if added > 0 and opn_f > 0:
                capital += added * opn_f
            if capital > 0:
                stock_ret = pnl / capital * 100.0
        elif now_sh > 0 and opn_f > 0:
            stock_ret = (close_f / opn_f - 1.0) * 100.0
        name = _leg_stock_name(code, st, en, name_by_code)
        rows.append(
            {
                "stock_code": code,
                "stock_name": name,
                "shares": round(now_sh, 0),
                "ret_pct": None if stock_ret is None else round(stock_ret, 3),
                "contrib_pct": round(contrib, 4),
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("contrib_pct") or 0.0)), reverse=True)
    n_more = max(0, len(rows) - DAY_LEG_TOP)
    return rows[:DAY_LEG_TOP], n_more


def replay_session_day(*, now: Optional[datetime] = None) -> Optional[str]:
    """交易日且已过 09:30 才覆盖当天；开盘前 / 非交易日返回 None。"""
    from core.market.calendar import is_trading_day, resolve_session_date

    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    if not session or not is_trading_day(session):
        return None
    if dt.strftime("%Y-%m-%d") == session:
        open_at = dt.replace(hour=9, minute=30, second=0, microsecond=0)
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
    if not isinstance(q, dict):
        return None
    return (
        _fpx(q.get("open"))
        or _fpx(q.get("open_raw"))
        or _fpx(q.get("price_raw"))
        or _fpx(q.get("price"))
        or _fpx(q.get("last"))
        or _fpx(q.get("close"))
    )


def overlay_session_day_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    now: Optional[datetime] = None,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    fetch_quotes: bool = True,
) -> Tuple[Dict[str, List[dict]], Optional[str], int]:
    """仓里还没有当日日 K 时，用现价开盘补一根，让 09:30 能跑到当天。

    特征仍只用昨收。成交用今开。真实收盘 / 隔夜 label 不写。
    仅当覆盖率日历末日恰好是上一交易日才补，避免测试夹具或过期仓跳到今天。
    """
    dates = _coverage_dates(stock_bars)
    session = replay_session_day(now=now)
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


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def mock_quote_from_bar(
    bar: Optional[dict],
    *,
    prev_bar: Optional[dict] = None,
    px_field: str = "open",
) -> dict:
    """日线 → 调仓/盯市用行情 dict（含 prev_close / change_raw，供涨跌停）。"""
    if not isinstance(bar, dict):
        return {}
    try:
        px = float(bar.get(px_field) or bar.get("close") or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            px = 0.0
    prev = None
    if isinstance(prev_bar, dict):
        try:
            prev = float(prev_bar.get("close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    if prev is None:
        try:
            prev = float(bar.get("pre_close") or bar.get("prev_close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    change = 0.0
    if prev and prev > 0 and px > 0:
        change = (px / prev - 1.0) * 100.0
    open_px = None
    try:
        open_px = float(bar.get("open") or 0) or None
    except (TypeError, ValueError):
        open_px = None
    return {
        "success": True,
        "price_raw": px,
        "price": px,
        "open_raw": open_px if open_px is not None else px,
        "open": open_px if open_px is not None else px,
        "prev_close": prev,
        "pre_close": prev,
        "yesterday_close": prev,
        "change_raw": round(change, 4),
        "change_pct": round(change, 4),
    }


def _make_batch_query(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
    *,
    px_field: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    date_i = {d: i for i, d in enumerate(dates)}
    i = date_i.get(str(day))

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for code in codes or []:
            c = str(code or "").strip()
            if not c:
                continue
            dm = date_maps.get(c) or {}
            bar = dm.get(str(day))
            prev_bar = None
            if i is not None and i > 0:
                prev_bar = dm.get(dates[i - 1])
            q = mock_quote_from_bar(bar, prev_bar=prev_bar, px_field=px_field)
            if q:
                q["stock_code"] = c
            out[c] = q
        return out

    return _batch


_DEBUG_SKIP_KEYS = ("T+1", "地板", "不可卖", "无有效报价", "不足一手", "cash_floor", "锁定")


def _is_debug_skip(sk: dict) -> bool:
    r = str((sk or {}).get("reason") or "")
    return any(k in r for k in _DEBUG_SKIP_KEYS)


def _infer_skip_side(sk: dict) -> str:
    s = str((sk or {}).get("side") or "").strip().lower()
    if s in ("buy", "sell"):
        return s
    r = str((sk or {}).get("reason") or "")
    if any(k in r for k in ("T+1", "不可卖", "不足一手", "锁定", "no_position")):
        return "sell"
    return "buy"


def _skip_to_sim(sk: dict, day: str) -> dict:
    side = _infer_skip_side(sk)
    return {
        "stock_code": sk.get("stock_code"),
        "stock_name": sk.get("stock_name"),
        "side": side,
        "shares": sk.get("shares"),
        "price": sk.get("price"),
        "amount": sk.get("amount"),
        "as_of": day,
        "signal_date": day,
        "status": "skipped",
        "action": "skip",
        "matrix_action": "skip",
        "y_fuse": sk.get("y_fuse"),
        "y_on": sk.get("y_on"),
        "y_tau": sk.get("y_tau")
        if sk.get("y_tau") is not None
        else sk.get("predicted_score_tau"),
        "predicted_score_tau": sk.get("predicted_score_tau")
        if sk.get("predicted_score_tau") is not None
        else sk.get("y_tau"),
        "ranking_score": sk.get("ranking_score"),
        "y_trade": sk.get("y_trade"),
        "y_nowcast": sk.get("y_nowcast"),
        "rank_i": sk.get("rank_i"),
        "rank_n": sk.get("rank_n"),
        "lot_kind": sk.get("lot_kind"),
        "reason": sk.get("reason") or "跳过",
    }


def _ledger_trades_to_sim(trades: Sequence[dict]) -> List[dict]:
    """账本买卖腿 → 前端成交表行（保留 side / 股数 / y_fuse，不是研究独立腿）。"""
    out: List[dict] = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            continue
        day = str(t.get("as_of") or t.get("ts") or "")[:10]
        yf = t.get("y_fuse")
        if yf is None:
            yf = t.get("y_trade") if t.get("y_trade") is not None else t.get("score")
        row: Dict[str, Any] = {
            "stock_code": t.get("stock_code"),
            "stock_name": t.get("stock_name"),
            "side": side,
            "shares": t.get("shares"),
            "price": t.get("price"),
            "amount": t.get("amount"),
            "fees": t.get("fees"),
            "as_of": day,
            "signal_date": day,
            "status": "filled",
            "action": t.get("action") or t.get("matrix_action"),
            "y_fuse": yf,
            "y_on": t.get("y_on"),
            "y_tau": t.get("y_tau")
            if t.get("y_tau") is not None
            else t.get("predicted_score_tau"),
            "predicted_score_tau": t.get("predicted_score_tau")
            if t.get("predicted_score_tau") is not None
            else t.get("y_tau"),
            "ranking_score": t.get("ranking_score"),
            "y_trade": t.get("y_trade"),
            "y_nowcast": t.get("y_nowcast"),
            "rank_i": t.get("rank_i"),
            "rank_n": t.get("rank_n"),
            "lot_kind": t.get("lot_kind"),
            "predicted_score": yf,
            "score": yf,
            "matrix_action": t.get("matrix_action") or t.get("action"),
            "note": t.get("note"),
            "reason": t.get("reason") or t.get("note"),
            "pnl_pct": t.get("pnl_pct"),
            "origin": t.get("origin"),
            "open_date": t.get("open_date"),
            "cost_price": t.get("cost_price"),
            "cum_cost": t.get("cum_cost"),
        }
        if side == "buy":
            row["entry_date"] = day
            row["entry_price"] = t.get("price")
            if row.get("cost_price") is None:
                row["cost_price"] = t.get("price")
            if not row.get("open_date"):
                row["open_date"] = day
        else:
            row["exit_date"] = day
            row["exit_price"] = t.get("price")
            row["return_pct"] = t.get("pnl_pct")
        out.append(row)
    return out


def _px(bar: Optional[dict], key: str) -> Optional[float]:
    if not isinstance(bar, dict):
        return None
    try:
        v = float(bar.get(key) or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _pct_ret(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return round((float(end) / float(start) - 1.0) * 100.0, 4)


def realized_yhat_windows(
    code: str,
    day: str,
    *,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    date_index: Optional[Dict[str, int]] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """事后对照：y_fuse↔close[T]/close[T−1]−1；y_τ↔close[T]/open[T]−1；y_on↔open[T+1]/close[T]−1。不进决策。"""
    d = str(day or "")[:10]
    c = str(code or "").strip()
    if not d or not c:
        return None, None, None
    if date_index is not None:
        i = date_index.get(d)
    else:
        try:
            i = list(dates).index(d)
        except ValueError:
            i = None
    if i is None or i < 0:
        return None, None, None
    dm = date_maps.get(c) or {}
    bar = dm.get(d)
    if isinstance(bar, dict) and bar.get("session_overlay"):
        return None, None, None
    prev = dm.get(dates[i - 1]) if i > 0 else None
    nxt = dm.get(dates[i + 1]) if i + 1 < len(dates) else None
    r_cc = _pct_ret(_px(prev, "close"), _px(bar, "close"))
    r_on = _pct_ret(_px(bar, "close"), _px(nxt, "open"))
    r_tau = _pct_ret(_px(bar, "open"), _px(bar, "close"))
    return r_cc, r_on, r_tau


def _default_paper(
    *,
    initial_cash: float,
    top_k: int,
    cost_model: str,
) -> dict:
    rules: Dict[str, Any] = {
        "max_positions": int(top_k),
        # 单票上限不随观察池放大而变小（否则 1/N 会把 1000 股手数挡掉）
        "position_pct": 0.25,
        "min_cash_pct": 0.0,
        "horizon_days": 1,
        "execution_mode": "next_open",
    }
    # 回放关闭做 T，避免卖腿读盘中分钟线打网
    rules["t0"] = {"enabled": False}
    cash = float(initial_cash)
    return {
        "cash": cash,
        "initial_cash": cash,
        "holdings": [],
        "trades": [],
        "snapshots": [],
        "operation_log": [],
        "strategy_id": "short_conservative",
        "cost_model": cost_model,
        "rules": rules,
    }


def _load_replay_cluster_models() -> Dict[str, Any]:
    """与历史 Top-K 同源：live 分组 β；失败则空（再试全局模型）。"""
    try:
        from core.signal.cluster.live import (
            cluster_yhat_shadow_compute_allowed,
            filter_primary_cluster_models_by_code,
            get_cluster_scoring_cfg,
            load_cluster_return_models_by_code,
        )

        cs = get_cluster_scoring_cfg()
        if cluster_yhat_shadow_compute_allowed(str(cs.get("mode") or "off")):
            raw = load_cluster_return_models_by_code()
            return filter_primary_cluster_models_by_code(raw)
    except Exception:  # noqa: BLE001
        logger.debug("load replay cluster models failed", exc_info=True)
    return {}


def _load_replay_global_model() -> Any:
    try:
        from core.signal.return_score_store import load_return_model

        model, meta = load_return_model(prefer_active=True)
        if meta.get("ok") and model is not None:
            return model
    except Exception:  # noqa: BLE001
        logger.debug("load replay global return model failed", exc_info=True)
    return None


def _attach_open_yhat_heads(
    entries: List[dict],
    *,
    quotes: Dict[str, dict],
    windows: Dict[str, List[dict]],
    cluster_models: Optional[Dict[str, Any]] = None,
    global_model: Any = None,
    on_model_doc: Any = None,
    tau_model_doc: Any = None,
    cfg: Optional[dict] = None,
) -> List[dict]:
    """heuristic 窗口分 → 分组 ŷ_EOD；09:30 PIT 挂 ŷ_τ / nowcast；开盘特征打 y_on。

    无 ŷ_τ 时 nowcast 退回 ŷ_EOD 先验，不写假 0。缺 nowcast 则 fuse 只用 y_trade。
    """
    from core.signal.return_score import (
        apply_predicted_scores,
        apply_predicted_scores_by_model,
    )

    items = list(entries)
    by_code = dict(cluster_models or {})
    if by_code:
        items = apply_predicted_scores_by_model(
            items,
            by_code,
            write_rank_score=False,
            default_model=global_model,
        )
    elif global_model is not None:
        items = apply_predicted_scores(items, global_model, write_rank_score=False)

    rem_doc = tau_model_doc
    if rem_doc is None:
        try:
            from core.research.tau_ridge import load_tau_model

            rem_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            rem_doc = None
            logger.debug("load_tau_model failed in replay yhat heads", exc_info=True)

    try:
        from core.signal.dual_score import (
            align_trade_score_fields,
            attach_dual_score_pit,
        )
    except Exception:  # noqa: BLE001
        align_trade_score_fields = None  # type: ignore[assignment]
        attach_dual_score_pit = None  # type: ignore[assignment]
        logger.debug("dual_score import failed", exc_info=True)

    try:
        from core.research.on_panel import build_on_features_from_quote_bars
        from core.research.on_ridge import predict_on_from_features
        from core.signal.dual_score.on import apply_on_score_fields
    except Exception:  # noqa: BLE001
        build_on_features_from_quote_bars = None  # type: ignore[assignment]
        predict_on_from_features = None  # type: ignore[assignment]
        apply_on_score_fields = None  # type: ignore[assignment]
        logger.debug("on-head imports failed", exc_info=True)

    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        pred = it.get("predicted_score")
        if pred is not None:
            it.setdefault("predicted_score_eod", pred)
            it.setdefault("y_trade", pred)
        if attach_dual_score_pit is not None and code:
            try:
                attach_dual_score_pit(
                    it,
                    quote=quotes.get(code) or {},
                    bars=windows.get(code) or [],
                    config=cfg,
                    rem_model_doc=rem_doc,
                    use_minute_tau=False,
                    fuse_intraday=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug("attach open nowcast failed for %s", code, exc_info=True)
        if align_trade_score_fields is not None:
            try:
                align_trade_score_fields(
                    it, write_score=False, refresh_window=False, config=cfg
                )
            except Exception:  # noqa: BLE001
                logger.debug("align_trade_score_fields failed", exc_info=True)
        blend = it.get("predicted_score_blend")
        if blend is not None:
            it["y_trade"] = blend
        elif it.get("y_trade") is None and pred is not None:
            it["y_trade"] = pred
        nc = it.get("predicted_score_nowcast")
        if nc is not None:
            it["y_nowcast"] = nc
        if (
            apply_on_score_fields is None
            or build_on_features_from_quote_bars is None
            or predict_on_from_features is None
            or not code
        ):
            continue
        quote = quotes.get(code) or {}
        window = windows.get(code) or []
        try:
            gap = None
            if quote:
                try:
                    gap = float(quote.get("change_raw"))
                except (TypeError, ValueError):
                    gap = None
            on_feats = build_on_features_from_quote_bars(
                quote,
                window,
                gap_pct=gap,
            )
            on_yhat = predict_on_from_features(on_feats, model_doc=on_model_doc)
            apply_on_score_fields(
                it,
                on_yhat=on_yhat,
                feats=on_feats,
                on_model_doc=on_model_doc,
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach y_on failed for %s", code, exc_info=True)
    return items


def _score_open_day(
    *,
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
    horizon_days: int,
    cfg: Optional[dict],
    cluster_models: Optional[Dict[str, Any]] = None,
    global_model: Any = None,
    on_model_doc: Any = None,
    tau_model_doc: Any = None,
) -> List[dict]:
    """9:30 信息集：窗口截至昨收，报价用今开（不把今日收盘喂进特征）。"""
    from core.signal.cross_section_batch import score_window_as_item

    if day_i <= 0:
        return []
    entries: List[dict] = []
    quotes: Dict[str, dict] = {}
    windows: Dict[str, List[dict]] = {}
    prev_i = day_i - 1
    today = dates[day_i]
    for code in stock_bars:
        window = _window_for_code(code, dates, date_maps, prev_i, max_window)
        if len(window) < 2:
            continue
        dm = date_maps.get(code) or {}
        today_bar = dm.get(today)
        prev_bar = dm.get(dates[prev_i])
        quote = mock_quote_from_bar(today_bar, prev_bar=prev_bar, px_field="open")
        if quote:
            quote["date"] = today
            quote["trade_date"] = today
        item = score_window_as_item(
            code,
            window,
            horizon_days=horizon_days,
            quote=quote,
            config=cfg,
        )
        if item:
            entries.append(item)
            quotes[str(code)] = quote or {}
            windows[str(code)] = window
    if not entries:
        return []
    return _attach_open_yhat_heads(
        entries,
        quotes=quotes,
        windows=windows,
        cluster_models=cluster_models,
        global_model=global_model,
        on_model_doc=on_model_doc,
        tau_model_doc=tau_model_doc,
        cfg=cfg,
    )


def _items_from_injected_ranking(rows: Sequence[dict]) -> List[dict]:
    """测试/日报注入行：ŷ 当作 y_fuse；有 y_on 则带上。"""
    out: List[dict] = []
    for it in rows or []:
        if not isinstance(it, dict) or not it.get("stock_code"):
            continue
        row = dict(it)
        yf = row.get("y_fuse")
        if yf is None:
            yf = row.get("y_fusion")
        if yf is None:
            yf = row.get("predicted_score")
        if yf is None:
            yf = row.get("predicted_score_eod")
        if yf is None:
            yf = row.get("y_trade")
        if yf is None:
            yf = row.get("score")
        if yf is not None:
            row["y_fuse"] = yf
            row.setdefault("y_trade", yf)
        out.append(row)
    return out


def backtest_paper_replay(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    lookback: Optional[int] = None,
    initial_cash: float = REPLAY_INITIAL_CASH,
    cost_model: str = "simple_cn",
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    yhat_horizon_days: int = 1,
    progress_cb: Optional[Callable[..., Any]] = None,
    cash_floor: Optional[float] = None,
    rank_enter: Optional[float] = None,
    rank_strong: Optional[float] = None,
    y_on_alpha: Optional[float] = None,
    fusion_w_trade: Optional[float] = None,
    fusion_w_nowcast: Optional[float] = None,
    include_session_day: bool = True,
    session_quotes: Optional[Dict[str, dict]] = None,
    session_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """策略调仓历史回测：每个交易日 9:30 开盘用 y_fuse/y_on 排序并按 1000/2000 股调仓。"""
    from core.backtest.engine import _trade_metrics
    from core.paper.rebalance.rank_lots import (
        DEFAULT_Y_ON_ALPHA,
        clamp_fusion_weight,
        clamp_y_on_alpha,
        coerce_rank_threshold,
        get_rank_lot_cfg,
        plan_rank_lot_day,
    )
    from core.paper.rebalance.watching_matrix import _apply_matrix_trades
    from core.paper.replay_ctx import paper_replay_context

    if not stock_bars or len(stock_bars) < 1:
        return {"success": False, "error": "stock_bars 为空", "params": {"engine": ENGINE_ID}}

    session_day = None
    n_overlay = 0
    if include_session_day:
        stock_bars, session_day, n_overlay = overlay_session_day_bars(
            stock_bars,
            now=session_now,
            quotes_by_code=session_quotes,
            fetch_quotes=session_quotes is None,
        )

    date_maps = {str(c): _bars_by_date(bars) for c, bars in stock_bars.items()}
    dates, trade_start = _replay_calendar(
        stock_bars,
        lookback=lookback,
        min_history=min_history,
        max_window=max_window,
    )
    if session_day and session_day not in dates:
        dates = sorted(dates + [session_day])
    n = len(dates)
    need = max(2, int(min_history) + 1)
    if n < need:
        return {
            "success": False,
            "error": f"可交易日不足 {n}<{need}",
            "params": {"engine": ENGINE_ID, "common_dates": n, "lookback": lookback},
        }

    from core.watching.store import WATCHING_MAX_SIZE

    universe_n = max(1, len(stock_bars))
    cap = max(universe_n, int(WATCHING_MAX_SIZE))
    if top_k is None:
        top_k = universe_n
    else:
        top_k = max(1, min(int(top_k or 1), cap))
    floor = float(REPLAY_CASH_FLOOR if cash_floor is None else cash_floor)
    alpha = clamp_y_on_alpha(
        DEFAULT_Y_ON_ALPHA if y_on_alpha is None else y_on_alpha
    )
    w_trade = clamp_fusion_weight(
        REPLAY_FUSION_W_TRADE if fusion_w_trade is None else fusion_w_trade,
        REPLAY_FUSION_W_TRADE,
    )
    w_nowcast = clamp_fusion_weight(
        REPLAY_FUSION_W_NOWCAST if fusion_w_nowcast is None else fusion_w_nowcast,
        REPLAY_FUSION_W_NOWCAST,
    )
    enter = coerce_rank_threshold(
        REPLAY_RANK_ENTER if rank_enter is None else rank_enter,
        REPLAY_RANK_ENTER,
    )
    strong = coerce_rank_threshold(
        REPLAY_RANK_STRONG if rank_strong is None else rank_strong,
        REPLAY_RANK_STRONG,
    )
    enter = max(0.0, min(1.0, float(enter)))
    strong = max(0.0, min(1.0, float(strong)))
    if strong < enter:
        strong = enter
    paper = _default_paper(
        initial_cash=initial_cash,
        top_k=top_k,
        cost_model=cost_model,
    )
    # 必须写 rank_lots：get_path_matrix_cfg 优先该键。只写旧键 path_matrix 时，
    # resolve_effective_execution 里 live 的 50 万地板会留下，落账把所有买入打成 cash_floor。
    replay_lots = {
        "enabled": True,
        "mode": "rank_lots",
        "rank_enter": enter,
        "rank_strong": strong,
        "cash_floor": floor,
        "holdings_mv_cap": 0.0,
        "y_on_alpha": alpha,
        "fusion_w_trade": w_trade,
        "fusion_w_nowcast": w_nowcast,
    }
    paper.setdefault("rules", {})["execution"] = {
        "rebalance_timing": {
            "rank_lots": dict(replay_lots),
            "path_matrix": dict(replay_lots),
        }
    }
    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    rl_cfg["cash_floor"] = floor
    rl_cfg["lot_base"] = REPLAY_LOT_BASE
    rl_cfg["lot_strong"] = REPLAY_LOT_STRONG
    rl_cfg["y_on_alpha"] = alpha
    rl_cfg["fusion_w_trade"] = w_trade
    rl_cfg["fusion_w_nowcast"] = w_nowcast
    rl_cfg["rank_enter"] = enter
    rl_cfg["rank_strong"] = strong
    rl_cfg["holdings_mv_cap"] = 0.0
    rl_cfg["t0_sell_blocks"] = {}

    cfg = None
    try:
        from core.signal.config import load_signal_config

        cfg = load_signal_config()
        cfg = dict(cfg)
        scoring = dict(cfg.get("scoring") or {})
        scoring["skip_minute_io"] = True
        cfg["scoring"] = scoring
    except Exception:  # noqa: BLE001
        logger.debug("load_signal_config failed in paper_replay", exc_info=True)

    cluster_models = _load_replay_cluster_models() if rankings_by_date is None else {}
    global_model = None
    on_model_doc = None
    tau_model_doc = None
    if rankings_by_date is None:
        if not cluster_models:
            global_model = _load_replay_global_model()
        try:
            from core.research.on_ridge import load_on_model

            on_model_doc = load_on_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_on_model failed in paper_replay", exc_info=True)
        try:
            from core.research.tau_ridge import load_tau_model

            tau_model_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_tau_model failed in paper_replay", exc_info=True)

    equity_curve: List[dict] = []
    day_returns: List[float] = []
    rebalance_logs: List[dict] = []
    debug_skips: List[dict] = []
    constraints: Dict[str, int] = {
        "t1_blocks": 0,
        "cash_floor_skips": 0,
        "rebalance_days": 0,
    }
    name_by_code = _watching_name_map()
    prev_equity = float(initial_cash)
    if lookback is not None:
        first_i = max(1, int(trade_start))
    else:
        first_i = max(1, int(min_history))
    if first_i >= n:
        first_i = max(1, n - 1)
    if dates:
        equity_curve.append(
            {
                "date": dates[first_i - 1],
                "equity": prev_equity,
                "equity_nav": 100.0,
                "return_pct": 0.0,
            }
        )

    for i in range(first_i, n):
        day = dates[i]
        if progress_cb is not None:
            try:
                progress_cb(f"{day} 09:30", i - first_i + 1, max(1, n - first_i))
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay progress_cb failed", exc_info=True)

        if rankings_by_date is not None:
            scored = _items_from_injected_ranking(rankings_by_date.get(day) or [])
            held_codes = [
                str(h.get("stock_code") or "")
                for h in (paper.get("holdings") or [])
            ]
            have = {str(it.get("stock_code") or "") for it in scored}
            for code in held_codes:
                if code and code not in have:
                    prev_rows = rankings_by_date.get(dates[i - 1]) or []
                    extra = _items_from_injected_ranking(
                        [r for r in prev_rows if str(r.get("stock_code")) == code]
                    )
                    scored.extend(extra or [{"stock_code": code}])
        else:
            scored = _score_open_day(
                stock_bars=stock_bars,
                dates=dates,
                date_maps=date_maps,
                day_i=i,
                max_window=max_window,
                horizon_days=max(1, int(yhat_horizon_days or 1)),
                cfg=cfg,
                cluster_models=cluster_models,
                global_model=global_model,
                on_model_doc=on_model_doc,
                tau_model_doc=tau_model_doc,
            )

        prices: Dict[str, float] = {}
        for code in list(stock_bars.keys()) + [
            str(h.get("stock_code") or "") for h in (paper.get("holdings") or [])
        ]:
            c = str(code or "").strip()
            bar = (date_maps.get(c) or {}).get(day)
            if not bar:
                continue
            try:
                px = float(bar.get("open") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if px > 0:
                prices[c] = px

        cash = float(paper.get("cash") or 0)
        start_snap = _holding_snap(paper.get("holdings") or [])
        with paper_replay_context(
            as_of=day,
            batch_query=_make_batch_query(date_maps, dates, day, px_field="open"),
        ):
            plan = plan_rank_lot_day(
                scored=scored,
                holdings=list(paper.get("holdings") or []),
                cash=cash,
                prices=prices,
                cfg=rl_cfg,
                as_of=day,
            )
            sell_trades = []
            buy_trades = []
            for leg in plan.get("sells") or []:
                px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                sell_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for leg in plan.get("buys") or []:
                px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                buy_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for sk in plan.get("skips") or []:
                if "地板" in str(sk.get("reason") or ""):
                    constraints["cash_floor_skips"] += 1
                if "T+1" in str(sk.get("reason") or ""):
                    constraints["t1_blocks"] += 1

            applied = {"sell_trades": [], "buy_trades": [], "apply_skips": []}
            if sell_trades or buy_trades:
                applied = _apply_matrix_trades(paper, sell_trades, buy_trades, as_of=day)
                constraints["rebalance_days"] += 1
            for sk in list(plan.get("skips") or []) + list(applied.get("apply_skips") or []):
                if not isinstance(sk, dict):
                    continue
                if not _is_debug_skip(sk):
                    continue
                debug_skips.append(_skip_to_sim(sk, day))

        equity = float(paper.get("cash") or 0)
        for h in paper.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            code = str(h.get("stock_code") or "")
            bar = (date_maps.get(code) or {}).get(day) or {}
            try:
                close_px = float(bar.get("close") or bar.get("open") or 0)
            except (TypeError, ValueError):
                close_px = 0.0
            sh = float(h.get("shares") or 0)
            if close_px > 0 and sh > 0:
                h["last_price"] = close_px
                equity += sh * close_px
        ret = (equity / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        day_returns.append(ret)
        cash_now = round(float(paper.get("cash") or 0), 2)
        nav = (
            round(100.0 * equity / float(initial_cash), 4)
            if initial_cash
            else None
        )
        n_holdings = len(paper.get("holdings") or [])
        end_snap = _holding_snap(paper.get("holdings") or [])
        legs, legs_more = day_stock_legs(
            start=start_snap,
            end=end_snap,
            date_maps=date_maps,
            prev_day=dates[i - 1],
            day=day,
            open_px=prices,
            prev_equity=prev_equity,
            name_by_code=name_by_code,
        )
        equity_curve.append(
            {
                "date": day,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "return_pct": round(ret, 4),
                "cash": cash_now,
                "n_holdings": n_holdings,
                "legs": legs,
                "legs_more": legs_more,
            }
        )
        rebalance_logs.append(
            {
                "signal_date": day,
                "exec_date": day,
                "ok": True,
                "n_buy": len(applied.get("buy_trades") or []),
                "n_sell": len(applied.get("sell_trades") or []),
                "n_hold": len(plan.get("holds") or []),
                "n_skip": len(plan.get("skips") or []),
                "n_apply_skip": len(applied.get("apply_skips") or []),
                "cash": cash_now,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "n_holdings": n_holdings,
                "return_pct": round(ret, 4),
            }
        )
        prev_equity = equity

    metrics = _trade_metrics(day_returns, holding_days=1)
    metrics = dict(metrics)
    ledger_trades = list(paper.get("trades") or [])
    cash_by_day = {
        str(p.get("date") or "")[:10]: p.get("cash")
        for p in equity_curve
        if isinstance(p, dict)
    }
    hold_by_day = {
        str(p.get("date") or "")[:10]: p.get("n_holdings")
        for p in equity_curve
        if isinstance(p, dict)
    }
    equity_by_day = {
        str(p.get("date") or "")[:10]: p.get("equity")
        for p in equity_curve
        if isinstance(p, dict)
    }
    date_index = {d: i for i, d in enumerate(dates)}

    def _stamp_day_context(row: dict) -> dict:
        day = str(row.get("as_of") or "")[:10]
        row["cash_after"] = cash_by_day.get(day)
        row["n_holdings"] = hold_by_day.get(day)
        row["equity_after"] = equity_by_day.get(day)
        r_cc, r_on, r_tau = realized_yhat_windows(
            str(row.get("stock_code") or ""),
            day,
            dates=dates,
            date_maps=date_maps,
            date_index=date_index,
        )
        row["realized_cc"] = r_cc
        row["realized_on"] = r_on
        row["realized_tau"] = r_tau
        return row

    sim_trades = [_stamp_day_context(r) for r in _ledger_trades_to_sim(ledger_trades)]
    sim_trades.extend(_stamp_day_context(dict(s)) for s in debug_skips)
    metrics["trade_count"] = len(ledger_trades)
    metrics["sim_trade_count"] = len(ledger_trades)
    metrics["skip_count"] = len(debug_skips)
    metrics["rebalance_days"] = constraints["rebalance_days"]
    final_eq = float(equity_curve[-1]["equity"]) if equity_curve else float(initial_cash)
    total_ret = (final_eq / float(initial_cash) - 1.0) * 100.0 if initial_cash else 0.0
    metrics["total_return_pct"] = round(total_ret, 2)

    note = (
        f"引擎={ENGINE_ID}：每个交易日 09:30 开盘算 y_fuse/y_on，"
        f"ranking=(1+y_fuse/100)×(1+α×y_on/100)−1 α={alpha:g}；"
        f"y_fuse 权 w_trade={w_trade:g} w_nowcast={w_nowcast:g}；ranking<0 清仓；"
        f"过 rank入场={rl_cfg.get('rank_enter')} 按分数买（开加上限=观察池 {top_k} 只，现金地板约束），"
        f"过 rank强={rl_cfg.get('rank_strong')} 买 {int(rl_cfg.get('lot_strong') or REPLAY_LOT_STRONG)} "
        f"否则 {int(rl_cfg.get('lot_base') or REPLAY_LOT_BASE)} 股；"
        f"现金地板={floor:.0f}；T+1；成本={cost_model}；≠ topk_research。"
    )
    if session_day:
        note += (
            f" 含当日 {session_day} 09:30（现价开盘 ×{n_overlay}；"
            "真实收盘/隔夜 label 待日K入库）。"
        )
    return {
        "success": True,
        "strategy": ENGINE_ID,
        "params": {
            "engine": ENGINE_ID,
            "top_k": top_k,
            "min_history": min_history,
            "max_window": max_window,
            "initial_cash": initial_cash,
            "cash_floor": floor,
            "rank_enter": rl_cfg.get("rank_enter"),
            "rank_strong": rl_cfg.get("rank_strong"),
            "y_on_alpha": alpha,
            "fusion_w_trade": w_trade,
            "fusion_w_nowcast": w_nowcast,
            "lot_base": int(rl_cfg.get("lot_base") or REPLAY_LOT_BASE),
            "lot_strong": int(rl_cfg.get("lot_strong") or REPLAY_LOT_STRONG),
            "cost_model": cost_model,
            "execution_mode": "open_930",
            "horizon_days": 1,
            "stock_count": len(stock_bars),
            "lookback": int(lookback) if lookback is not None else None,
            "common_dates": max(0, n - first_i),
            "trade_days": max(0, n - first_i),
            "end_date": dates[-1] if dates else None,
            "session_day": session_day,
            "session_overlay_n": n_overlay,
            "yhat_horizon_days": yhat_horizon_days,
            "rankings_injected": rankings_by_date is not None,
        },
        "metrics": metrics,
        "equity_curve": equity_curve,
        "constraints_hit": constraints,
        "rebalance_logs": rebalance_logs,
        "trades": ledger_trades,
        "sim_trades": sim_trades,
        "sim_trade_count": len(ledger_trades),
        "holdings_end": copy.deepcopy(paper.get("holdings") or []),
        "cash_end": round(float(paper.get("cash") or 0), 2),
        "paper": paper,
        "note": note,
    }


def rankings_from_topk_precomputed(
    precomputed_ranks: Dict[str, Any],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """把 Top-K ``precomputed_ranks`` 转成纸面调仓 ranking 行。"""
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for day, pack in (precomputed_ranks or {}).items():
        if not isinstance(pack, dict):
            continue
        picks = list(pack.get("picks") or [])[:top_k]
        items_by = pack.get("items_by_code") or {}
        rows: List[dict] = []
        for code, score in picks:
            c = str(code or "").strip()
            if not c:
                continue
            base = dict(items_by.get(c) or {})
            base["stock_code"] = c
            try:
                sc = float(score)
            except (TypeError, ValueError):
                sc = None
            if sc is not None:
                base.setdefault("predicted_score", sc)
                base.setdefault("predicted_score_eod", sc)
                base.setdefault("predicted_score_blend", sc)
                base.setdefault("score", sc)
            base.setdefault("score_scale", "predicted_yhat")
            base.setdefault("dual_score_window", "eod_next")
            base["rank_source"] = "topk_precomputed"
            rows.append(base)
        out[str(day)] = rows
    return out


def build_momentum_rankings(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """用昨收到今收涨跌幅做轻量目标簿（日报离线摘要用；≠生产 ŷ）。"""
    date_maps = {str(c): _bars_by_date(bars) for c, bars in (stock_bars or {}).items()}
    dates = _coverage_dates(stock_bars)
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for i, day in enumerate(dates):
        if i < 1:
            continue
        rows: List[dict] = []
        prev_d = dates[i - 1]
        for code, dm in date_maps.items():
            bar = dm.get(day)
            prev = dm.get(prev_d)
            if not bar or not prev:
                continue
            try:
                c0 = float(prev.get("close") or 0)
                c1 = float(bar.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if c0 <= 0 or c1 <= 0:
                continue
            ret = (c1 / c0 - 1.0) * 100.0
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": code,
                    "score": ret,
                    "predicted_score": ret,
                    "predicted_score_eod": ret,
                    "predicted_score_tau": ret,
                    "score_scale": "predicted_yhat",
                    "dual_score_window": "eod_next",
                    "rank_source": "momentum_close",
                }
            )
        rows.sort(key=lambda r: float(r.get("predicted_score") or 0), reverse=True)
        out[day] = rows[:top_k]
    return out


def summarize_paper_replay_for_daily(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    lookback: Optional[int] = None,
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    rank_source: Optional[str] = None,
) -> Dict[str, Any]:
    """日报用轻量摘要（与 ``summarize_portfolio_backtest`` 字段对齐子集）。

    优先用注入的 Top-K ŷ ranking；否则昨收→今收动量近似。
    """
    src = rank_source
    if rankings_by_date is None:
        rankings = build_momentum_rankings(stock_bars, top_k=top_k)
        src = src or "momentum_close"
    else:
        rankings = rankings_by_date
        src = src or "topk_precomputed"

    bt = backtest_paper_replay(
        stock_bars,
        top_k=top_k,
        rankings_by_date=rankings,
        cost_model="simple_cn",
        initial_cash=REPLAY_INITIAL_CASH,
        cash_floor=REPLAY_CASH_FLOOR,
        lookback=lookback,
    )
    if not bt.get("success"):
        return bt
    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    params = dict(bt.get("params") or {})
    params["rank_source"] = src
    if lookback is not None:
        params["lookback"] = int(lookback)
    note = str(bt.get("note") or "")
    if src == "topk_precomputed":
        note += " · 日报目标簿=与 Top-K 同序列 ŷ（precomputed_ranks）"
    else:
        note += " · 日报目标簿=昨收涨跌幅动量近似（≠ ŷ_EOD）"
    return {
        "success": True,
        "engine": ENGINE_ID,
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
        "equity_curve_tail": curve[-12:],
        "constraints_hit": bt.get("constraints_hit"),
        "metrics": metrics,
        "note": note,
    }
