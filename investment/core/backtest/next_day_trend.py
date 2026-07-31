"""观察池日频 +1 趋势预判（收盘 → 次日收盘方向）。

决策日 t 仅用当时可见事实打分；标签为 r_{t+1}=(C_{t+1}/C_t)-1。
研究探针：不写 signal_config、不改 stance、不做半日。

盘中：若入库日线落后于会话日且有现价，用暂估今日 bar 决策 → 预判下一交易日，
避免「昨收预判今日」与表格现价/涨跌并排造成假对照。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.backtest.pool_ic import (
    _bars_by_date,
    _forward_return_pct,
    _pearson,
)

BIAS_UP = "up"
BIAS_DOWN = "down"
BIAS_FLAT = "flat"

# 与 stance 粗对齐的启发式阈值（可测、固定）
SCORE_UP = 60.0
SCORE_DOWN = 45.0


def score_to_bias(score: float, *, up: float = SCORE_UP, down: float = SCORE_DOWN) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return BIAS_FLAT
    if s >= up:
        return BIAS_UP
    if s <= down:
        return BIAS_DOWN
    return BIAS_FLAT


def return_to_label(ret_pct: float, *, flat_band_pct: float = 0.5) -> str:
    band = max(0.0, float(flat_band_pct or 0.0))
    if ret_pct > band:
        return BIAS_UP
    if ret_pct < -band:
        return BIAS_DOWN
    return BIAS_FLAT


def bias_confidence(score: float, bias: str) -> float:
    """启发式置信：距阈值越远越高，压到 [0.35, 0.85]。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.4
    if bias == BIAS_UP:
        raw = 0.5 + (s - SCORE_UP) / 40.0
    elif bias == BIAS_DOWN:
        raw = 0.5 + (SCORE_DOWN - s) / 40.0
    else:
        dist = min(abs(s - SCORE_UP), abs(s - SCORE_DOWN))
        raw = 0.45 + (10.0 - min(dist, 10.0)) / 40.0
    return round(max(0.35, min(0.85, raw)), 3)


def _momentum_label(ret_pct: Optional[float], *, flat_band_pct: float) -> Optional[str]:
    if ret_pct is None:
        return None
    return return_to_label(ret_pct, flat_band_pct=flat_band_pct)


def _quote_px(quote: dict, key: str) -> Optional[float]:
    raw = quote.get(key)
    if raw is None:
        return None
    try:
        s = str(raw).replace("元", "").replace("HK$", "").replace("$", "").replace(",", "")
        return float(s)
    except (TypeError, ValueError):
        return None


def provisional_bar_from_quote(
    quote: Optional[dict],
    session_date: str,
) -> Optional[dict]:
    """用盘中行情压成当日暂估 bar（close=现价）；供「决策今日→预判明日」。"""
    if not quote or not session_date:
        return None
    price = quote.get("price_raw")
    if price is None:
        price = quote.get("price")
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return None
    if price_f <= 0:
        return None
    open_p = _quote_px(quote, "open") or price_f
    high_p = _quote_px(quote, "high") or max(open_p, price_f)
    low_p = _quote_px(quote, "low") or min(open_p, price_f)
    vol = 0.0
    try:
        vol_raw = quote.get("volume")
        if vol_raw is not None:
            vol = float(
                str(vol_raw).replace("万", "e4").replace("亿", "e8").replace(",", "")
            )
    except (TypeError, ValueError):
        vol = 0.0
    return {
        "date": str(session_date)[:10],
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": price_f,
        "volume": vol,
    }


def _live_change_pct(quote: Optional[dict]) -> Optional[float]:
    if not quote:
        return None
    for key in ("change_raw", "change_percent"):
        if quote.get(key) is None:
            continue
        try:
            return float(quote.get(key))
        except (TypeError, ValueError):
            continue
    return None


def compute_next_day_trend_report(
    stock_bars: Dict[str, List[dict]],
    *,
    flat_band_pct: float = 0.5,
    lookback_eval_days: int = 60,
    min_history: int = 12,
    max_window: int = 30,
    pit_fundamentals: bool = True,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    score_up: float = SCORE_UP,
    score_down: float = SCORE_DOWN,
    live_quotes: Optional[Dict[str, dict]] = None,
    session_date: Optional[str] = None,
) -> Dict[str, Any]:
    """对观察池日线做 horizon=1 方向评估 + 最新一日 bias。

    历史评估只用已入库日线（无未来）。
    最新预判：日线落后于会话日且有现价时，暂估今日 → 预判下一交易日。
    """
    from core.backtest.engine import _mock_quote_from_bars
    from core.market_calendar import (
        filter_trading_dates,
        is_trading_day,
        next_trading_day,
        resolve_session_date,
    )
    from core.signal.cross_section_batch import score_window_as_item

    if not stock_bars:
        return {
            "success": False,
            "ok": False,
            "error": "无标的日线",
            "mode": "watchlist_daily_plus1",
            "horizon_days": 1,
        }

    flat_band_pct = max(0.0, min(float(flat_band_pct or 0.5), 5.0))
    lookback_eval_days = max(10, min(int(lookback_eval_days or 60), 250))
    min_history = max(5, int(min_history or 12))

    date_maps = {c: _bars_by_date(b) for c, b in stock_bars.items()}
    all_dates: set = set()
    for dm in date_maps.values():
        all_dates |= set(dm.keys())
    dates = sorted(all_dates)
    try:
        dates = filter_trading_dates(dates)
        calendar_tag = "cn_lite"
    except Exception:
        calendar_tag = "none"

    n = len(dates)
    if n < min_history + 2:
        return {
            "success": False,
            "ok": False,
            "error": f"交易日不足（{n}<{min_history + 2}）",
            "mode": "watchlist_daily_plus1",
            "horizon_days": 1,
            "calendar": calendar_tag,
        }

    sess = str(session_date or "").strip()[:10] or resolve_session_date()
    quotes = live_quotes or {}

    fund_cache: Dict[Tuple[str, str], Optional[dict]] = {}

    def _fund_for(code: str, decision_date: str) -> Optional[dict]:
        if not pit_fundamentals:
            return (fundamentals_by_code or {}).get(code)
        key = (code, decision_date)
        if key in fund_cache:
            return fund_cache[key]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(
                code, as_of=decision_date, live_fallback=False
            )
            metrics = resolved.get("metrics") if resolved.get("ok") else None
            if not metrics and fundamentals_by_code:
                metrics = fundamentals_by_code.get(code)
            fund_cache[key] = metrics
            return metrics
        except Exception:
            metrics = (fundamentals_by_code or {}).get(code)
            fund_cache[key] = metrics
            return metrics

    def _score_window(
        code: str,
        window: List[dict],
        decision_date: str,
        *,
        quote: Optional[dict] = None,
    ) -> Optional[dict]:
        if len(window) < 2:
            return None
        q = quote if quote is not None else _mock_quote_from_bars(window, len(window) - 1)
        fund = _fund_for(code, decision_date)
        return score_window_as_item(
            code,
            window,
            horizon_days=1,
            quote=q,
            fundamentals=fund,
        )

    def _score_at(code: str, dm: Dict[str, dict], date_idx: int) -> Optional[dict]:
        decision_date = dates[date_idx]
        if decision_date not in dm:
            return None
        start = max(0, date_idx - max_window + 1)
        window = [dm[d] for d in dates[start : date_idx + 1] if d in dm]
        return _score_window(code, window, decision_date)

    # —— 历史评估（有 t+1 的日子；只用入库日线）——
    scores: List[float] = []
    rets: List[float] = []
    hits = 0
    dir_correct = 0
    dir_total = 0
    mom_hits = 0
    mom_n = 0
    labeled = 0
    daily_rows: List[dict] = []

    last_eval_i = n - 2
    first_i = max(min_history - 1, last_eval_i - lookback_eval_days + 1)

    for i in range(first_i, last_eval_i + 1):
        decision_date = dates[i]
        day_hits = 0
        day_n = 0
        for code, dm in date_maps.items():
            item = _score_at(code, dm, i)
            if not item:
                continue
            fr = _forward_return_pct(dm, dates, i, 1)
            if fr is None:
                continue
            sc = float(item.get("score") or 0)
            pred = score_to_bias(sc, up=score_up, down=score_down)
            actual = return_to_label(fr, flat_band_pct=flat_band_pct)
            labeled += 1
            scores.append(sc)
            rets.append(fr)
            day_n += 1
            if pred == actual:
                hits += 1
                day_hits += 1
            if pred != BIAS_FLAT and actual != BIAS_FLAT:
                dir_total += 1
                if pred == actual:
                    dir_correct += 1
            prev_fr = _forward_return_pct(dm, dates, i - 1, 1) if i >= 1 else None
            mom = _momentum_label(prev_fr, flat_band_pct=flat_band_pct)
            if mom is not None:
                mom_n += 1
                if mom == actual:
                    mom_hits += 1
        if day_n > 0:
            daily_rows.append(
                {
                    "date": decision_date,
                    "n": day_n,
                    "hit_rate": round(day_hits / day_n, 4),
                }
            )

    ic = _pearson(scores, rets) if len(scores) >= 3 else None
    hit_rate = round(hits / labeled, 4) if labeled else None
    dir_hit = round(dir_correct / dir_total, 4) if dir_total else None
    mom_rate = round(mom_hits / mom_n, 4) if mom_n else None
    random_baseline = round(1.0 / 3.0, 4)

    eval_block = {
        "sample_count": labeled,
        "day_count": len(daily_rows),
        "hit_rate": hit_rate,
        "directional_hit_rate": dir_hit,
        "directional_n": dir_total,
        "momentum_baseline_hit_rate": mom_rate,
        "random_baseline_hit_rate": random_baseline,
        "ic": round(ic, 4) if ic is not None else None,
        "flat_band_pct": flat_band_pct,
        "score_up": score_up,
        "score_down": score_down,
        "lookback_eval_days": lookback_eval_days,
        "daily_tail": daily_rows[-40:],
        "beats_momentum": (
            hit_rate is not None and mom_rate is not None and hit_rate > mom_rate
        ),
        "beats_random": (hit_rate is not None and hit_rate > random_baseline),
    }

    # —— 最新预判（会话对齐；可盘中暂估）——
    latest_rows: List[dict] = []
    for code in sorted(date_maps.keys()):
        dm = date_maps[code]
        own_dates = [d for d in dates if d in dm]
        if len(own_dates) < 2:
            continue
        last_bar_date = own_dates[-1]
        live = quotes.get(code) or {}
        decision_date = last_bar_date
        phase = "bar_for_next"
        intraday = False
        window: List[dict]
        score_quote: Optional[dict] = None

        if sess and last_bar_date < sess and is_trading_day(sess):
            qbar = provisional_bar_from_quote(live, sess)
            if qbar:
                hist = own_dates[-(max_window - 1) :] if max_window > 1 else []
                window = [dm[d] for d in hist if d in dm] + [qbar]
                decision_date = sess
                phase = "session_for_tomorrow"
                intraday = True
                chg = _live_change_pct(live)
                score_quote = {
                    "change_raw": chg if chg is not None else 0.0,
                    "price_raw": qbar["close"],
                }
            else:
                phase = "prior_close_for_today"
                start_own = max(0, len(own_dates) - max_window)
                window = [dm[d] for d in own_dates[start_own:]]
                decision_date = last_bar_date
        else:
            start_own = max(0, len(own_dates) - max_window)
            window = [dm[d] for d in own_dates[start_own:]]
            decision_date = last_bar_date
            phase = "bar_for_next"

        item = _score_window(code, window, decision_date, quote=score_quote)
        if not item:
            continue
        sc = float(item.get("score") or 0)
        bias = score_to_bias(sc, up=score_up, down=score_down)
        conf = bias_confidence(sc, bias)
        target = next_trading_day(decision_date) or ""
        row: Dict[str, Any] = {
            "code": code,
            "score": round(sc, 2),
            "bias": bias,
            "conf": conf,
            "as_of": decision_date,
            "target_date": target,
            "decision_phase": phase,
            "intraday_provisional": intraday,
            "bars_as_of": last_bar_date,
            "note": (
                "盘中暂估今日 bar → 预判下一交易日；非投资建议"
                if intraday
                else (
                    "日线未含会话日且无现价：仍为昨收→预判今日；勿与现价误作前瞻对错"
                    if phase == "prior_close_for_today"
                    else "收盘决策 → 次日收盘方向；非投资建议"
                )
            ),
        }

        if decision_date in dm and decision_date in dates:
            try:
                as_of_i = dates.index(decision_date)
            except ValueError:
                as_of_i = -1
            if as_of_i >= 0:
                fr_realized = _forward_return_pct(dm, dates, as_of_i, 1)
                if fr_realized is not None:
                    row["realized_next_pct"] = round(fr_realized, 4)
                    row["realized_label"] = return_to_label(
                        fr_realized, flat_band_pct=flat_band_pct
                    )
                    row["hit"] = row["realized_label"] == bias
                else:
                    row["realized_next_pct"] = None
        else:
            row["realized_next_pct"] = None

        if phase == "prior_close_for_today":
            live_chg = _live_change_pct(live)
            if live_chg is not None:
                row["live_chg_pct"] = round(live_chg, 4)
                row["live_label"] = return_to_label(
                    live_chg, flat_band_pct=flat_band_pct
                )
                row["live_vs_pred"] = (
                    "match" if row["live_label"] == bias else "miss"
                )

        latest_rows.append(row)

    latest_rows.sort(key=lambda r: float(r.get("score") or 0), reverse=True)

    report_as_of = sess
    report_target = next_trading_day(sess) if sess else ""
    report_phase = "session_for_tomorrow"
    if latest_rows:
        report_as_of = str(latest_rows[0].get("as_of") or report_as_of)
        report_target = str(latest_rows[0].get("target_date") or report_target)
        report_phase = str(latest_rows[0].get("decision_phase") or report_phase)

    ok = labeled >= 10 or bool(latest_rows)
    return {
        "success": True,
        "ok": ok,
        "mode": "watchlist_daily_plus1",
        "horizon_days": 1,
        "calendar": calendar_tag,
        "pit_fundamentals": bool(pit_fundamentals),
        "stock_count": len(stock_bars),
        "eval": eval_block,
        "latest": latest_rows,
        "session_date": sess,
        "as_of": report_as_of,
        "target_date": report_target,
        "decision_phase": report_phase,
        "note": (
            "观察池日频+1：决策日收盘（或盘中暂估）→ 下一交易日收盘方向。"
            "历史命中≠明日保证；勿用「昨收预判今日」对照今日现价评判前瞻能力。"
            "研究探针，不写 signal_config。"
        ),
    }
