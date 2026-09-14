"""做 T 回测研究封装（供 QuantService / CLI）。"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from core.t0.backtest import (
    backtest_t0_on_bars,
    derive_t0_quality_metrics,
)
from core.t0.config import T0_TRADE_DAYS_SAMPLE_UI_LIMIT

logger = logging.getLogger(__name__)

# 持仓做T回测：只用纸面股票池；仓位/本金用虚拟假设（与调仓回测默认本金对齐）
T0_BT_DEFAULT_LOOKBACK = 10
T0_BT_VIRTUAL_SHARES = 1_000.0
T0_BT_VIRTUAL_CASH = 200_000.0

def _fill_t0_viz_stock_names(viz: Any, *, stock_code: str, stock_name: Optional[str]) -> None:
    """点位/贡献行补股票名，避免累计 PnL / 散点 tip 只剩代码。"""
    if not isinstance(viz, dict):
        return
    code = str(stock_code or "").strip()
    name = str(stock_name or "").strip()
    if not code and not name:
        return
    for key in ("stock_contrib", "cumulative_pnl", "y_tau_scatter"):
        for row in viz.get(key) or []:
            if not isinstance(row, dict):
                continue
            if code and not str(row.get("stock_code") or "").strip():
                row["stock_code"] = code
            if name and not str(row.get("stock_name") or "").strip():
                row["stock_name"] = name

# 仅「本地无分钟缓存」才打远端；东财偶发挂死，回测走 BaoStock 并设短超时。
_MINUTE_FETCH_TIMEOUT_SEC = 12.0
# A股全日约 48 根 5m；≥40 或末根≥14:55 视为齐窗（缺尾缓存会触发补拉）
_MINUTE_SESSION_MIN_BARS = 40
_MINUTE_SESSION_END_HM = (14, 55)
# 前端 fetch 约 300s abort（Safari 常报 Failed to fetch）；整批须含 τ 池在此前返回。
_HOLDINGS_DEADLINE_SEC = 240.0
_QUOTE_TIMEOUT_SEC = 4.0


def _minute_bar_hm(bar: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(bar, dict):
        return None
    ts = bar.get("datetime") or bar.get("time") or bar.get("date")
    s = str(ts or "").strip()
    if len(s) >= 16 and s[13:15].isdigit():
        try:
            return int(s[11:13]), int(s[14:16])
        except ValueError:
            return None
    if ":" in s:
        parts = s.replace("T", " ").split()[-1].split(":")
        if len(parts) >= 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None
    return None


def _minute_day_complete(minute_bars: Optional[List[dict]]) -> bool:
    """历史交易日 5m 是否齐到收盘窗（末根≥14:55 或根数≥40）。"""
    rows = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if len(rows) < 2:
        return False
    if len(rows) >= int(_MINUTE_SESSION_MIN_BARS):
        return True
    hm = _minute_bar_hm(rows[-1])
    if not hm:
        return False
    end_h, end_m = _MINUTE_SESSION_END_HM
    return hm[0] > end_h or (hm[0] == end_h and hm[1] >= end_m)


def _local_has_truncated_history(
    by_date: Dict[str, List[dict]],
    *,
    today: Optional[str] = None,
) -> bool:
    """本地缓存是否含「历史缺尾日」（当日盘中未齐不算）。"""
    day0 = str(today or "").strip()[:10]
    if not day0:
        from datetime import date as _date

        day0 = _date.today().isoformat()
    for d, ms in (by_date or {}).items():
        ds = str(d or "").strip()[:10]
        if not ds or ds >= day0:
            continue
        if not _minute_day_complete(ms if isinstance(ms, list) else []):
            return True
    return False


_T = TypeVar("_T")

# 回测响应 rules / execution.t0 与纸面 ExecutionSpec 对齐的字段
_BT_RULES_VIEW_KEYS = (
    "enabled",
    "t0_ratio",
    "fill_mode",
    "fill_mode_sell_then_buy",
    "fill_mode_buy_then_sell",
    "direction",
    "path_mode",
    "minute_period",
    "must_cover_same_day",
    "must_cover_same_day_sell_then_buy",
    "must_cover_same_day_buy_then_sell",
    "y_trade_enter",
    "y_trade_floor",
    "y_tau_enter",
    "y_tau_enter_sell_then_buy",
    "y_tau_enter_buy_then_sell",
    "y_enter_enabled",
    "y_enter_alt_enabled",
    "y_tau_enter_alt",
    "y_tc_enter",
    "y_τc_enter",
    "y_tc_enter_alt",
    "y_τc_enter_alt",
    "y_path_enter_alt",
    "y_t30_strong",
    "y_τ30_strong",
    "y_t30_enter",
    "y_τ30_enter",
    "y_t30_enter_alt",
    "y_τ30_enter_alt",
    "y_t60_strong",
    "y_τ60_strong",
    "y_t60_enter",
    "y_τ60_enter",
    "y_t60_enter_alt",
    "y_τ60_enter_alt",
    "y_tc_strong",
    "y_τc_strong",
    "y_on_allow",
    "y_on_risk",
    "t0_y_oc_target_scale",
    "t0_y_oc_l",
    "t0_y_oc_u",
    "y_use_path",
    "y_path_enter",
    "y_path_enter_sell_then_buy",
    "y_path_enter_buy_then_sell",
    "y_path_strong",
    "y_complexity_max",
    "y_cx_max",
    "y_tpd_max",
    "y_complexity_max_alt",
    "y_tpd_max_alt",
    "y_path_required",
    "t0_close_band_delta_pct",
    "t0_price_space_gate",
    "t0_price_space_max_dev_pct",
    "t0_price_space_prev_dev_pct",
    "t0_round_ratio",
    "t0_max_position_pct",
    "y_tau_exit_price_skip",
    "y_tau_exit_price_mult",
    "y_tau_exit_price_skip_buy_then_sell",
    "y_tau_exit_price_mult_buy_then_sell",
    "y_tau_exit_price_skip_sell_then_buy",
    "y_tau_exit_price_mult_sell_then_buy",
    "t0_pm_degrade",
    "t0_pm_degrade_sell_then_buy",
    "t0_pm_degrade_buy_then_sell",
    "t0_pm_chase_interval_min",
    "t0_pm_chase_interval_min_sell_then_buy",
    "t0_pm_chase_interval_min_buy_then_sell",
    "t0_stop_pct_buy_then_sell",
    "t0_stop_pct_sell_then_buy",
    "t0_stop_arm_bars",
    "t0_stop_on_close",
    "t0_slots_enabled",
    "t0_slots",
    "t0_slots_max_rounds",
    "y_score_source",
)


def _bt_rules_view(bt_rules: dict) -> Dict[str, Any]:
    """从生效 bt_rules 抽出 UI/表格需要的规则视图。"""
    src = bt_rules or {}
    out: Dict[str, Any] = {}
    for k in _BT_RULES_VIEW_KEYS:
        if k in src and src[k] is not None:
            out[k] = src[k]
    return out


def _execution_view_for_backtest(
    bt_rules: dict, exec_meta: Optional[dict]
) -> Dict[str, Any]:
    """回测结果里的 execution 视图，与 /api/paper/execution 的 t0 字段对齐。"""
    meta = exec_meta or {}
    coupling = meta.get("coupling") or {}
    t0 = _bt_rules_view(bt_rules)
    if "enabled" not in t0:
        t0["enabled"] = bool(bt_rules.get("enabled", True))
    return {
        "ok": True,
        "channel": meta.get("channel") or "backtest",
        "effective_hash": meta.get("effective_hash"),
        "t0_sources": meta.get("t0_sources") or {},
        "notes": meta.get("notes") or [],
        "summary": meta.get("summary"),
        "coupling": coupling,
        "t0": t0,
    }


def _rules_summary(bt_rules: dict) -> Dict[str, Any]:
    return _bt_rules_view(bt_rules)


def _call_with_timeout(fn: Callable[[], _T], timeout_sec: float) -> _T:
    """限时调用；超时立刻返回，不 ``shutdown(wait=True)`` 等挂死的远端线程。"""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=max(0.5, float(timeout_sec)))
    except FuturesTimeout as exc:
        raise TimeoutError(f"timeout ({timeout_sec}s)") from exc
    finally:
        pool.shutdown(wait=False)


def _local_minute_by_date(
    code: str, period: str
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """回测优先读本地分钟缓存（忽略 TTL）。历史 5m 不必等东财刷新。"""
    try:
        from skills.common.history import resolve_market_code
        from skills.common.minute_history import _load_stale_minute, group_minute_bars_by_date

        market, bare = resolve_market_code(code)
        if market != "CN" or not bare:
            raw = str(code or "").strip()
            if raw.isdigit() and len(raw) == 6:
                market, bare = "CN", raw
            else:
                return {}, {"ok": False, "error": f"仅支持 A 股分钟线: {code}"}
        packed = _load_stale_minute(market, bare, str(period or "5"))
        if not packed or not packed[0]:
            return {}, {"ok": False, "from_cache": False, "period": period}
        bars, meta = packed
        meta = dict(meta or {})
        meta["period"] = period
        return group_minute_bars_by_date(bars), meta
    except Exception as e:
        logger.debug("local minute cache failed for %s", code, exc_info=True)
        return {}, {"ok": False, "error": str(e), "period": period}


def _fetch_minute_by_date(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 90,
    timeout_sec: float = _MINUTE_FETCH_TIMEOUT_SEC,
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """返回 (minute_by_date, meta)；失败则 ({}, meta)。

    先用本地缓存（含过期）。无缓存、或历史日明显缺尾（如只到午前）时短超时补拉；
    跳过东财，避免 ak_lock 挂死把整次「做T回测」拖过前端 300s abort。
    """
    by_date, meta = _local_minute_by_date(code, period)
    truncated = bool(by_date) and _local_has_truncated_history(by_date)
    if by_date and not truncated:
        return by_date, meta
    try:
        from core.ports.market import fetch_minute_bars, group_minute_bars_by_date

        def _load():
            # 缺尾补拉与持仓涨跌 tip 同口径：use_cache=False，避免再读回残缺仓
            return fetch_minute_bars(
                code,
                period=period,
                lookback_days=lookback_days,
                use_cache=not truncated,
                skip_em=True,
                max_age_hours=0.01 if truncated else 48.0,
            )

        try:
            bars, remote_meta = _call_with_timeout(
                _load, max(1.0, float(timeout_sec or 5.0))
            )
        except TimeoutError:
            if by_date:
                out = dict(meta or {})
                out["truncated_days"] = True
                out["hint"] = "本地 5m 有缺尾日且补拉超时，仍用残缺缓存"
                return by_date, out
            return {}, {
                "ok": False,
                "error": f"minute fetch timeout ({timeout_sec}s)",
                "period": period,
                "hint": "无本地 5m 缓存；请先在研究页预热分钟线",
            }
        if not bars:
            if by_date:
                out = dict(meta or {})
                out["truncated_days"] = True
                out["hint"] = "本地 5m 有缺尾日且补拉无数据，仍用残缺缓存"
                out.setdefault("error", (remote_meta or {}).get("error") or "无分钟 K")
                return by_date, out
            out_meta = dict(remote_meta or {"ok": False})
            out_meta.setdefault("error", "无分钟 K")
            return {}, out_meta
        remote_by = group_minute_bars_by_date(bars)
        if not by_date:
            return remote_by, remote_meta
        # 合并：缺尾日优先用更长/更齐的远端；其余保留本地
        merged = {str(k): list(v) for k, v in by_date.items() if isinstance(v, list)}
        replaced = 0
        for d, ms in (remote_by or {}).items():
            key = str(d)
            rows = list(ms) if isinstance(ms, list) else []
            loc = merged.get(key) or []
            if (not loc) or len(rows) > len(loc) or (
                _minute_day_complete(rows) and not _minute_day_complete(loc)
            ):
                if loc and rows != loc:
                    replaced += 1
                merged[key] = rows
        out_meta = dict(remote_meta or {})
        out_meta["refreshed_truncated"] = True
        out_meta["replaced_days"] = replaced
        out_meta["from_cache"] = False
        out_meta["period"] = period
        return merged, out_meta
    except Exception as e:
        logger.exception("unexpected error in _fetch_minute_by_date")
        if by_date:
            out = dict(meta or {})
            out["truncated_days"] = True
            out["error"] = str(e)
            return by_date, out
        return {}, {"ok": False, "error": str(e), "period": period}


def _quote_for_backtest(code: str, *, timeout_sec: float = _QUOTE_TIMEOUT_SEC) -> Dict[str, Any]:
    """解析名称/代码；超时则退回原串，避免腾讯行情拖死整次回测。"""
    try:
        from core.data.facade import get_quote

        try:
            q = _call_with_timeout(
                lambda: get_quote(code), max(0.5, float(timeout_sec or 4.0))
            )
        except TimeoutError:
            logger.warning("t0 backtest quote timeout after %.1fs: %s", timeout_sec, code)
            return {"success": False, "error": f"quote timeout ({timeout_sec}s)"}
        return q if isinstance(q, dict) else {"success": False}
    except Exception:
        logger.debug("t0 backtest quote failed for %s", code, exc_info=True)
        return {"success": False}


def _align_daily_bars_to_minute(
    bars: List[dict],
    minute_by_date: Dict[str, List[dict]],
) -> Tuple[List[dict], Dict[str, Any]]:
    """日线窗口对齐到「有可用 5m」的交易日，避免缓存偏短时大量缺分钟跳过冲掉样本。

    优先保留齐窗日（末根≥14:55 / 根数≥40）；齐窗不足 2 日时回退到 ≥2 根。
    """
    if not bars or not minute_by_date:
        return list(bars or []), {"aligned": False}
    complete = {
        str(d)
        for d, ms in minute_by_date.items()
        if _minute_day_complete(ms if isinstance(ms, list) else [])
    }
    any_usable = {
        str(d)
        for d, ms in minute_by_date.items()
        if isinstance(ms, list) and len(ms) >= 2
    }
    usable = complete if len(complete) >= 2 else any_usable
    if not usable:
        return list(bars), {"aligned": False, "reason": "no_usable_minute_days"}
    aligned = [b for b in bars if str(b.get("date") or "") in usable]
    if not aligned:
        return list(bars), {"aligned": False, "reason": "no_overlap"}
    meta = {
        "aligned": len(aligned) < len(bars),
        "bars_before": len(bars),
        "bars_after": len(aligned),
        "minute_days": len(usable),
        "complete_minute_days": len(complete),
        "prefer_complete": usable is complete or usable == complete,
        "date_min": aligned[0].get("date"),
        "date_max": aligned[-1].get("date"),
    }
    return aligned, meta

def run_t0_backtest_for_code(
    code: str = "茅台",
    *,
    lookback: int = T0_BT_DEFAULT_LOOKBACK,
    initial_shares: float = 1000,
    initial_cost: Optional[float] = None,
    initial_cash: float = 0.0,
    rules: Optional[dict] = None,
    paper: Optional[dict] = None,
    use_minute: bool = True,
    compare_no_t0: bool = True,
    tau_pool_by_date: Optional[Dict[str, Any]] = None,
    stock_name: Optional[str] = None,
    skip_quote: bool = False,
) -> Dict[str, Any]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        scoring_model_role_context,
    )

    if current_scoring_model_role() != MODEL_ROLE_RESEARCH:
        with scoring_model_role_context(MODEL_ROLE_RESEARCH):
            return run_t0_backtest_for_code(
                code,
                lookback=lookback,
                initial_shares=initial_shares,
                initial_cost=initial_cost,
                initial_cash=initial_cash,
                rules=rules,
                paper=paper,
                use_minute=use_minute,
                compare_no_t0=compare_no_t0,
                tau_pool_by_date=tau_pool_by_date,
                stock_name=stock_name,
                skip_quote=skip_quote,
            )

    from core.data.facade import bars_and_source as fetch_daily_bars

    _ = use_minute  # 日线模拟已删除；强制分钟
    from core.t0.score_policy import (
        T0_BACKTEST_SCORE_WARMUP,
        active_book_codes_for_tau_pool,
        build_tau_pool_by_date,
        load_bars_by_code_for_tau_pool,
    )

    eval_lb = max(10, int(lookback or T0_BT_DEFAULT_LOOKBACK))
    warmup = int(T0_BACKTEST_SCORE_WARMUP)
    quote: Dict[str, Any]
    if skip_quote:
        quote = {
            "success": True,
            "stock_code": str(code).strip(),
            "stock_name": stock_name,
        }
    else:
        quote = _quote_for_backtest(code)
    sym = quote.get("stock_code") if quote.get("success") else code
    fetch_n = eval_lb + warmup + 5
    # 回测只读本地日线（含过期缓存）；缺缓存时快速失败，勿打东财拖死整批
    bars_all, src = fetch_daily_bars(
        str(sym), limit=fetch_n, offline_ok=True, offline_only=True
    )
    if not bars_all and str(sym) != str(code):
        bars_all, src = fetch_daily_bars(
            str(code), limit=fetch_n, offline_ok=True, offline_only=True
        )
    if not bars_all:
        return {"success": False, "error": f"无法获取 {code} 日线", "task": "t0_backtest"}

    from core.execution import resolve_t0_rules, strip_execution_meta

    req_rules = dict(rules or {})
    req_rules.setdefault("path_mode", "first_touch")
    resolved = resolve_t0_rules(
        paper=paper,
        rules=req_rules,
        channel="backtest",
        has_minute=True,
    )
    bt_rules = strip_execution_meta(resolved)
    bt_rules["path_mode"] = "first_touch"
    exec_meta = resolved.get("_execution_meta")

    period = str(bt_rules.get("minute_period") or "5")
    # 分钟只覆盖评估窗；warmup 日仅用于因子 hist_prior
    minute_span = min(max(eval_lb, 10), 120)
    minute_by_date, minute_meta = _fetch_minute_by_date(
        str(sym), period=period, lookback_days=minute_span
    )
    if not minute_by_date:
        err = (minute_meta or {}).get("error") or "无分钟 K"
        return {
            "success": False,
            "error": f"做T回测需 5 分钟 K（已删除日线模拟）：{err}",
            "task": "t0_backtest",
            "stock_code": str(sym),
            "minute_meta": minute_meta,
            "use_minute": False,
        }

    eval_slice = list(bars_all[-eval_lb:])
    bars_eval, align_meta = _align_daily_bars_to_minute(eval_slice, minute_by_date)
    if not bars_eval:
        bars_eval, align_meta = _align_daily_bars_to_minute(bars_all, minute_by_date)
        if align_meta.get("aligned") and len(bars_eval) > eval_lb:
            bars_eval = bars_eval[-eval_lb:]
    if not bars_eval:
        return {
            "success": False,
            "error": "评估窗内无可用分钟覆盖日",
            "task": "t0_backtest",
            "stock_code": str(sym),
            "minute_meta": minute_meta,
        }

    # ŷ_τ 截面：调用方未传时，用活跃簿宇宙（与刷簿同构）补 pool_gaps
    tau_pool = tau_pool_by_date if isinstance(tau_pool_by_date, dict) else None
    if tau_pool is None:
        pool_codes = active_book_codes_for_tau_pool(cap=120)
        if str(sym) not in pool_codes:
            pool_codes = [str(sym)] + list(pool_codes)
        bars_by_code = load_bars_by_code_for_tau_pool(pool_codes, limit=fetch_n)
        if str(sym) not in bars_by_code and bars_all:
            bars_by_code[str(sym)] = list(bars_all)
        tau_pool = build_tau_pool_by_date(bars_by_code)

    cost = float(
        initial_cost
        if initial_cost is not None
        else (bars_eval[0].get("close") or 0)
    )
    report = backtest_t0_on_bars(
        bars_eval,
        bars_history=bars_all,
        eval_lookback=eval_lb,
        initial_shares=initial_shares,
        initial_cost=cost,
        initial_cash=float(initial_cash or 0),
        rules=bt_rules,
        stock_code=str(sym),
        minute_by_date=minute_by_date,
        require_minute=True,
        tau_pool_by_date=tau_pool,
        stock_name=str(
            (quote.get("stock_name") if quote.get("success") else None) or stock_name or ""
        ),
    )
    try:
        from core.t0.costs import default_t0_research_cost_config, resolve_t0_cost_context

        _cm, _ = resolve_t0_cost_context(cost_config=default_t0_research_cost_config())
        report["cost_model"] = _cm
    except Exception:  # noqa: BLE001
        pass
    report["data_source"] = src
    report["stock_name"] = (
        (quote.get("stock_name") if quote.get("success") else None) or stock_name
    )
    if report.get("viz") and isinstance(report["viz"], dict):
        _fill_t0_viz_stock_names(
            report["viz"],
            stock_code=str(report.get("stock_code") or sym),
            stock_name=report.get("stock_name"),
        )
    report["use_minute"] = True
    report["minute_meta"] = {
        "period": minute_meta.get("period") or bt_rules.get("minute_period"),
        "data_source": minute_meta.get("data_source"),
        "from_cache": minute_meta.get("from_cache"),
        "bar_count": minute_meta.get("bar_count"),
        "date_min": minute_meta.get("date_min"),
        "date_max": minute_meta.get("date_max"),
        "covered_days": len(minute_by_date or {}),
        "error": minute_meta.get("error"),
        "align": align_meta,
    }
    if align_meta.get("aligned"):
        report["lookback_aligned_to_minute"] = True
        note = str(report.get("note") or "")
        clip_note = (
            f"日线已对齐分钟覆盖 {align_meta.get('date_min')}→{align_meta.get('date_max')}"
            f"（{align_meta.get('bars_before')}→{align_meta.get('bars_after')} 日）"
        )
        report["note"] = f"{note}；{clip_note}".strip("；") if note else clip_note
    if exec_meta:
        report["execution"] = _execution_view_for_backtest(bt_rules, exec_meta)
    report["rules"] = _rules_summary(bt_rules)
    if compare_no_t0 and report.get("success"):
        off_rules = dict(bt_rules)
        off_rules["enabled"] = False
        hold_only = backtest_t0_on_bars(
            bars_eval,
            bars_history=bars_all,
            eval_lookback=eval_lb,
            initial_shares=initial_shares,
            initial_cost=cost,
            initial_cash=float(initial_cash or 0),
            rules=off_rules,
            stock_code=str(sym),
            minute_by_date=None,
            require_minute=False,
        )
        if hold_only.get("success"):
            t0_pnl = float(report.get("t0_pnl_with_exposure") or report.get("t0_pnl_total") or 0)
            report["no_t0_compare"] = {
                "t0_pnl_with_exposure": t0_pnl,
                "t0_contribution": round(t0_pnl, 2),
                "t0_trade_days": report.get("t0_trade_days"),
                "note": "含T轨相对「不做T」：贡献≈做T含敞口净PnL（底仓涨跌另计在持仓市值）",
            }
    return report


def run_t0_backtest_for_holdings(
    holdings: List[dict],
    *,
    lookback: int = T0_BT_DEFAULT_LOOKBACK,
    rules: Optional[dict] = None,
    paper: Optional[dict] = None,
    cash: float = 0.0,
    use_minute: bool = True,
    virtual_shares: float = T0_BT_VIRTUAL_SHARES,
    virtual_cash: float = T0_BT_VIRTUAL_CASH,
) -> Dict[str, Any]:
    """对持仓列表逐票回测并汇总（研究用，不改账本）。

    股票池取自纸面持仓；仓位/本金默认虚拟假设（每票 ``virtual_shares``、
    账户本金 ``virtual_cash``，默认 1000 股 / 20 万）。累计收益比例 = 含敞口净 PnL / 本金。
    """
    if not holdings:
        return {
            "success": False,
            "error": "无持仓可回测",
            "task": "t0_backtest",
            "note": "请先在模拟页建仓，或指定 code",
        }

    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        scoring_model_role_context,
    )

    if current_scoring_model_role() != MODEL_ROLE_RESEARCH:
        with scoring_model_role_context(MODEL_ROLE_RESEARCH):
            return run_t0_backtest_for_holdings(
                holdings,
                lookback=lookback,
                rules=rules,
                paper=paper,
                cash=cash,
                use_minute=use_minute,
                virtual_shares=virtual_shares,
                virtual_cash=virtual_cash,
            )

    from core.execution import resolve_t0_rules, strip_execution_meta
    from core.t0.viz import merge_t0_viz_payloads, summarize_skip_reason_label

    _ = (use_minute, cash)
    req_rules = dict(rules or {})
    req_rules.setdefault("path_mode", "first_touch")
    resolved = resolve_t0_rules(
        paper=paper,
        rules=req_rules,
        channel="backtest",
        has_minute=True,
    )
    cfg = strip_execution_meta(resolved)
    cfg["path_mode"] = "first_touch"
    exec_meta = resolved.get("_execution_meta")

    v_shares = max(100.0, float(virtual_shares or T0_BT_VIRTUAL_SHARES))
    v_cash = max(0.0, float(virtual_cash if virtual_cash is not None else T0_BT_VIRTUAL_CASH))

    # 持仓 ∪ 活跃簿宇宙 → 共享 τ 截面（与刷簿同构，避免逐票缺 sector_gap_breadth）
    from core.t0.score_policy import (
        T0_BACKTEST_SCORE_WARMUP,
        active_book_codes_for_tau_pool,
        build_tau_pool_by_date,
        load_bars_by_code_for_tau_pool,
    )

    hold_codes = [
        str(h.get("stock_code") or "").strip()
        for h in holdings
        if str(h.get("stock_code") or "").strip()
    ]
    pool_codes = list(
        dict.fromkeys(hold_codes + active_book_codes_for_tau_pool(cap=120))
    )
    eval_lb = max(10, int(lookback or T0_BT_DEFAULT_LOOKBACK))
    fetch_n = eval_lb + int(T0_BACKTEST_SCORE_WARMUP) + 5
    per: List[Dict[str, Any]] = []
    t_deadline = time.time() + float(_HOLDINGS_DEADLINE_SEC)
    t_tau = time.time()
    tau_pool = build_tau_pool_by_date(
        load_bars_by_code_for_tau_pool(pool_codes, limit=fetch_n)
    )
    logger.info(
        "t0 holdings bt tau_pool n=%s elapsed=%.1fs remain=%.1fs holdings=%s",
        len(pool_codes),
        time.time() - t_tau,
        t_deadline - time.time(),
        len(holdings),
    )
    total_pnl = 0.0
    total_exposure = 0.0
    total_trades = 0
    total_covers = 0
    total_hold_mv = 0.0
    sell_then_buy_pnl = 0.0
    buy_then_sell_pnl = 0.0
    sell_then_buy_cover = 0
    buy_then_sell_cover = 0
    minute_path_days = 0
    missing_minute_days = 0

    for h in holdings:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        if time.time() >= t_deadline:
            one = {
                "success": False,
                "error": "回测截止：已达时间上限（请预热 5m 缓存或缩小回看窗）",
                "task": "t0_backtest",
                "stock_code": code,
                "stock_name": h.get("stock_name"),
            }
            per.append(one)
            continue
        # 只用纸面股票名单；股数/成本走虚拟仓（成本由日线开窗决定）
        t_one = time.time()
        one = run_t0_backtest_for_code(
            code,
            lookback=lookback,
            initial_shares=v_shares,
            initial_cost=None,
            initial_cash=v_cash,
            rules=cfg,
            paper=paper,
            use_minute=True,
            compare_no_t0=False,
            tau_pool_by_date=tau_pool,
            stock_name=h.get("stock_name"),
            skip_quote=True,
        )
        logger.debug(
            "t0 holdings bt %s ok=%s elapsed=%.1fs remain=%.1fs",
            code,
            bool(one.get("success")),
            time.time() - t_one,
            t_deadline - time.time(),
        )
        one["stock_name"] = one.get("stock_name") or h.get("stock_name")
        one["paper_shares"] = float(h.get("shares") or 0)
        one["virtual_shares"] = v_shares
        one["virtual_cash"] = v_cash
        if one.get("viz") and isinstance(one["viz"], dict):
            _fill_t0_viz_stock_names(
                one["viz"],
                stock_code=code,
                stock_name=one.get("stock_name"),
            )
        per.append(one)
        if one.get("success"):
            total_pnl += float(one.get("t0_pnl_total") or 0)
            total_exposure += float(one.get("exposure_pnl_total") or 0)
            total_trades += int(one.get("t0_trade_days") or 0)
            total_covers += int(one.get("t0_cover_days") or 0)
            total_hold_mv += float(one.get("hold_mv_start") or 0)
            sell_then_buy_pnl += float(one.get("sell_then_buy_pnl") or 0)
            buy_then_sell_pnl += float(one.get("buy_then_sell_pnl") or 0)
            sell_then_buy_cover += int(one.get("sell_then_buy_cover_days") or 0)
            buy_then_sell_cover += int(one.get("buy_then_sell_cover_days") or 0)
            minute_path_days += int(one.get("minute_path_days") or 0)
            missing_minute_days += int(one.get("missing_minute_days") or 0)

    ok = [x for x in per if x.get("success")]
    if not ok:
        failed = [x for x in per if not x.get("success")]
        err_bits = [str(x.get("error") or "失败") for x in failed[:3]]
        return {
            "success": False,
            "error": "持仓回测均未成功" + (f"：{'；'.join(err_bits)}" if err_bits else ""),
            "task": "t0_backtest",
            "from_holdings": True,
            "holding_count": len(holdings),
            "ok_count": 0,
            "results": per,
        }

    skip_days = sum(int(x.get("skip_days") or 0) for x in ok)
    signal_skip_days = sum(int(x.get("signal_skip_days") or 0) for x in ok)
    sell_then_buy_days = sum(int(x.get("sell_then_buy_days") or 0) for x in ok)
    buy_then_sell_days = sum(int(x.get("buy_then_sell_days") or 0) for x in ok)
    mixed_days = sum(int(x.get("mixed_days") or 0) for x in ok)
    uncover_days = sum(int(x.get("uncover_days") or 0) for x in ok)
    win_days = sum(int(x.get("t0_win_days") or 0) for x in ok)
    loss_days = sum(int(x.get("t0_loss_days") or 0) for x in ok)
    pnl_day_cnt = win_days + loss_days


    # 合并各票成交样本供 UI；最近按日 + 保留若干反T成交样本，避免「近一周全正T」误以为没有反T
    # 注意：trade_days_sample=[] 时勿用 `or days`，否则会把全日跳过行灌进样本
    trade_sample: List[Dict[str, Any]] = []
    skip_reason_counts: Dict[str, int] = {}
    for x in ok:
        code = x.get("stock_code")
        name = x.get("stock_name")
        tds = x.get("trade_days_sample")
        if not isinstance(tds, list):
            tds = []
        for d in tds:
            if d.get("skipped"):
                continue
            if (
                int(d.get("sold_qty") or 0) > 0
                or int(d.get("bought_qty") or 0) > 0
                or float(d.get("pnl") or 0) != 0
                or float(d.get("exposure_pnl") or 0) != 0
            ):
                row = dict(d)
                row["stock_code"] = code
                row["stock_name"] = name or row.get("stock_name")
                trade_sample.append(row)
        for d in x.get("days") or []:
            if not d.get("skipped"):
                continue
            reason = str(d.get("reason") or d.get("direction_reason") or "跳过").strip()
            reason_key = (reason[:69] + "…") if len(reason) > 72 else (reason or "跳过")
            label = summarize_skip_reason_label(reason_key)
            skip_reason_counts[label] = skip_reason_counts.get(label, 0) + 1
    trade_sample.sort(key=lambda r: str(r.get("date") or ""))
    ui_limit = T0_TRADE_DAYS_SAMPLE_UI_LIMIT
    recent = trade_sample[-ui_limit:]
    seen = {(r.get("stock_code"), r.get("date"), r.get("direction")) for r in recent}
    rev_extra: List[Dict[str, Any]] = []
    for r in reversed(trade_sample):
        # 补抽反T（sell_then_buy），避免近窗全正T时明细看不到反T
        if (r.get("direction") or r.get("direction_used")) != "sell_then_buy":
            continue
        key = (r.get("stock_code"), r.get("date"), r.get("direction"))
        if key in seen:
            continue
        rev_extra.append(r)
        seen.add(key)
        if len(rev_extra) >= 8:
            break
    trade_sample = sorted(recent + rev_extra, key=lambda r: str(r.get("date") or ""))[-ui_limit:]

    skip_reason_top = sorted(
        ({"reason": k, "count": v} for k, v in skip_reason_counts.items()),
        key=lambda r: (-int(r["count"]), str(r["reason"])),
    )[:8]

    out: Dict[str, Any] = {
        "success": bool(ok),
        "task": "t0_backtest",
        "from_holdings": True,
        "score_model_role": "research",
        "virtual_sizing": True,
        "virtual_shares": v_shares,
        "virtual_cash": v_cash,
        "holding_count": len(holdings),
        "ok_count": len(ok),
        "t0_pnl_total": round(total_pnl, 2),
        "exposure_pnl_total": round(total_exposure, 2),
        "t0_trade_days": total_trades,
        "t0_cover_days": total_covers,
        "skip_days": skip_days,
        "signal_skip_days": signal_skip_days,
        "sell_then_buy_days": sell_then_buy_days,
        "buy_then_sell_days": buy_then_sell_days,
        "mixed_days": mixed_days,
        "sell_then_buy_pnl": round(sell_then_buy_pnl, 2),
        "buy_then_sell_pnl": round(buy_then_sell_pnl, 2),
        "sell_then_buy_cover_days": sell_then_buy_cover,
        "buy_then_sell_cover_days": buy_then_sell_cover,
        "uncover_days": uncover_days,
        "minute_path_days": minute_path_days,
        "missing_minute_days": missing_minute_days,
        "hold_mv_start": round(total_hold_mv, 2),
        "t0_win_days": win_days,
        "t0_loss_days": loss_days,
        "t0_win_rate_pct": (
            round(win_days / pnl_day_cnt * 100.0, 2) if pnl_day_cnt else None
        ),
        "results": per,
        "days": trade_sample,
        "trade_days_sample": trade_sample,
        "skip_reason_top": skip_reason_top,
        "path_mode": "first_touch",
        "direction": cfg.get("direction"),
        "use_minute": True,
        "rules": _rules_summary(cfg),
        "scope_label": (
            f"纸面股票池 {len(ok)}/{len(holdings)} 只 · 虚拟每票{int(v_shares)}股"
            f" · 本金{int(v_cash / 10000)}万"
        ),
        "note": (
            "按纸面股票池回测；仓位/本金为虚拟假设"
            f"（每票 {int(v_shares)} 股 · 本金 {int(v_cash):,}）；"
            "仅 5m 第一触达（有分钟缓存时日线自动对齐覆盖窗口）；"
            f"direction={cfg.get('direction')} · path=first_touch；非实盘。"
            "主看累计收益比例（含敞口净PnL / 本金）。"
        ),
    }
    failed = [x for x in per if not x.get("success")]
    if failed:
        bits = [
            f"{x.get('stock_code') or '?'}:{(str(x.get('error') or '失败')[:40])}"
            for x in failed[:4]
        ]
        out["note"] = (
            str(out["note"])
            + f" 未计入 {len(failed)} 只（缺分钟等）："
            + "；".join(bits)
        )
        out["failed_count"] = len(failed)
    if exec_meta:
        out["execution"] = _execution_view_for_backtest(cfg, exec_meta)
    contrib = round(total_pnl + total_exposure, 2)
    out["no_t0_compare"] = {
        "t0_pnl_with_exposure": contrib,
        "t0_contribution": contrib,
        "t0_trade_days": total_trades,
        "note": "含T轨相对「不做T」：贡献≈做T含敞口净PnL合计",
    }
    out.update(derive_t0_quality_metrics(out))
    # 累计收益比例：含敞口净 PnL / 本金（账户级，不按票相乘）
    net = float(out.get("t0_pnl_with_exposure") or contrib)
    if v_cash > 1e-9:
        out["cumulative_return_pct"] = round(net / v_cash * 100.0, 4)
    elif total_hold_mv > 1e-9:
        out["cumulative_return_pct"] = round(net / total_hold_mv * 100.0, 4)
    else:
        out["cumulative_return_pct"] = None
    if total_hold_mv > 1e-9:
        out["pnl_vs_hold_mv_pct"] = round(net / total_hold_mv * 100.0, 4)
    else:
        out["pnl_vs_hold_mv_pct"] = out.get("cumulative_return_pct")
    # 相对「底仓+本金」的备选口径（本金账户级）
    capital = total_hold_mv + v_cash
    if capital > 1e-9:
        out["cumulative_return_vs_capital_pct"] = round(net / capital * 100.0, 4)
    else:
        out["cumulative_return_vs_capital_pct"] = None

    out["viz"] = merge_t0_viz_payloads([x.get("viz") for x in ok], rules=cfg)
    if len(ok) == 1:
        one = ok[0]
        out.update(
            {
                "stock_code": one.get("stock_code"),
                "stock_name": one.get("stock_name"),
                "days": one.get("trade_days_sample") or trade_sample,
                "trade_days_sample": one.get("trade_days_sample") or trade_sample,
                "rules": one.get("rules"),
                "uncover_days": one.get("uncover_days"),
                "hold_mv_start": one.get("hold_mv_start"),
                "sell_then_buy_pnl": one.get("sell_then_buy_pnl"),
                "buy_then_sell_pnl": one.get("buy_then_sell_pnl"),
                "sell_then_buy_cover_days": one.get("sell_then_buy_cover_days"),
                "buy_then_sell_cover_days": one.get("buy_then_sell_cover_days"),
                "t0_cover_days": one.get("t0_cover_days"),
                "minute_path_days": one.get("minute_path_days"),
                "missing_minute_days": one.get("missing_minute_days"),
                "minute_meta": one.get("minute_meta"),
                "use_minute": one.get("use_minute"),
            }
        )
        out.update(derive_t0_quality_metrics(out))
        out["viz"] = ok[0].get("viz") or out.get("viz")
        # 单票合并后重算累计收益比例（本金）
        net1 = float(out.get("t0_pnl_with_exposure") or out.get("t0_pnl_total") or 0)
        hmv1 = float(out.get("hold_mv_start") or 0)
        if v_cash > 1e-9:
            out["cumulative_return_pct"] = round(net1 / v_cash * 100.0, 4)
        elif hmv1 > 1e-9:
            out["cumulative_return_pct"] = round(net1 / hmv1 * 100.0, 4)
        if hmv1 > 1e-9:
            out["pnl_vs_hold_mv_pct"] = round(net1 / hmv1 * 100.0, 4)
        cap1 = hmv1 + v_cash
        out["cumulative_return_vs_capital_pct"] = (
            round(net1 / cap1 * 100.0, 4) if cap1 > 1e-9 else None
        )
        out["virtual_sizing"] = True
        out["virtual_shares"] = v_shares
        out["virtual_cash"] = v_cash
    from core.t0.viz import attach_summary_to_viz

    attach_summary_to_viz(out)
    return out
