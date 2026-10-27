"""L1 Feature Lake 物化：按 family 写穿（复用 assemble / collect）。"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.research.materialize.catalog import (
    FAMILY_CO,
    FAMILY_OO,
    FAMILY_OO_RANK,
    FAMILY_TC,
    normalize_families,
)
from core.research.materialize.consumer import MaterializeRequired
from core.research.portfolio_bars import load_portfolio_stock_bars

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[int, int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("materialize l1 progress_cb failed", exc_info=True)


def _step_progress(outer: ProgressCb, index: int, n_steps: int):
    """把一步内部的 done/total 折成整段里的位置。total 用 n*1000，避开宏步骤 2。"""
    from core.research.materialize.progress import emit_scaled

    def _cb(done: int = 0, total: int = 1, message: str = "") -> None:
        emit_scaled(outer, index, n_steps, done, total, message)

    return _cb


def _cancelled(cb: CancelCb) -> bool:
    if not cb:
        return False
    try:
        return bool(cb())
    except Exception:  # noqa: BLE001
        return False


def _stock_bar_items(
    codes: Sequence[str],
    *,
    lookback: int,
) -> Tuple[Dict[str, List[dict]], List[dict], List[Tuple[str, List[dict]]]]:
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        list(codes),
        lookback=int(lookback),
        fetch_fundamentals=False,
    )
    items: List[Tuple[str, List[dict]]] = []
    for code in list(stock_bars.keys()):
        bars = stock_bars.get(code) or []
        if isinstance(bars, list) and bars:
            items.append((str(code), list(bars)))
    return stock_bars, list(failures or []), items


def _items_from_bars(
    codes: Sequence[str],
    stock_bars: Dict[str, List[dict]],
) -> List[Tuple[str, List[dict]]]:
    items: List[Tuple[str, List[dict]]] = []
    for code in codes:
        bars = stock_bars.get(str(code)) or []
        if isinstance(bars, list) and bars:
            items.append((str(code), list(bars)))
    return items


def _materialize_oo(
    codes: Sequence[str],
    *,
    lookback: int,
    horizon_days: int,
    progress_cb: ProgressCb,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    failures: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from core.research.oo_ridge_compact import assemble_oo_panels

    t0 = time.perf_counter()
    if stock_bars is None:
        _emit(progress_cb, 0, 1, "L1 oo：加载日线…")
        _, failures, items = _stock_bar_items(codes, lookback=lookback)
    else:
        failures = list(failures or [])
        items = _items_from_bars(codes, stock_bars)
    if len(items) < 2:
        return {
            "family": FAMILY_OO,
            "ok": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "n_stocks": len(items),
            "failures": failures[:8],
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
    _emit(progress_cb, 0, 1, f"L1 oo：assemble {len(items)} 只…")

    def _pcb(done: int, total: int, message: str = "") -> None:
        _emit(
            progress_cb,
            done,
            total,
            message or f"L1 oo {int(done)}/{int(total)}",
        )

    summary: Dict[str, Any] = {}
    _x_mat, _y_mat, feat_names, _dates = assemble_oo_panels(
        items,
        horizon_days=int(horizon_days),
        progress_cb=_pcb,
        release_rows=True,
        stats_out=summary,
    )
    n_rows = int(summary.get("n_rows") or 0)
    n_features = int(summary.get("n_cols") or 0) or len(feat_names or [])
    n_dates = len(summary.get("dates") or [])
    return {
        "family": FAMILY_OO,
        "ok": True,
        "n_stocks": len(items),
        "n_rows": n_rows,
        "n_features": n_features,
        "n_dates": n_dates,
        "hits": int(summary.get("hits") or 0),
        "elapsed_s": round(time.perf_counter() - t0, 2),
    }


def _materialize_tc(
    codes: Sequence[str],
    *,
    lookback: int,
    progress_cb: ProgressCb,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    failures: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from core.research.panel_matrix import collect_tau_compact
    from core.yhat.keys import TAU_Z_FEATURES

    t0 = time.perf_counter()
    if stock_bars is None:
        _emit(progress_cb, 0, 1, "L1 tc：加载日线…")
        stock_bars, failures, _ = _stock_bar_items(codes, lookback=lookback)
    else:
        failures = list(failures or [])
    packs = [
        {"stock_code": c, "code": c, "bars": list(stock_bars.get(c) or [])}
        for c in codes
        if stock_bars.get(c)
    ]
    if len(packs) < 2:
        err = f"有效日线不足（失败 {len(failures)}）"
        return {
            "family": FAMILY_TC,
            "ok": False,
            "error": err,
            "n_stocks": len(packs),
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "tc_rank": {"family": "tc_rank", "ok": False, "error": err},
        }
    rank_packs = [
        {
            "stock_code": row.get("stock_code"),
            "code": row.get("code"),
            "bars": list(row.get("bars") or []),
        }
        for row in packs
    ]
    _emit(progress_cb, 0, 1, f"L1 tc：collect_tau_compact {len(packs)} 只…")

    def _pcb(done: int, total: int, message: str = "") -> None:
        _emit(
            progress_cb,
            done,
            total,
            message or f"L1 tc {int(done)}/{int(total)}",
        )

    tc_stats: Dict[str, Any] = {}
    _x, col_names, _ys, _dates, _metas, n_stocks = collect_tau_compact(
        packs,
        list(TAU_Z_FEATURES),
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc",
        cache_head="tc",
        progress_cb=_pcb,
        release_rows=True,
        stats_out=tc_stats,
    )
    rank_row = _materialize_tc_rank(rank_packs, progress_cb=progress_cb)
    return {
        "family": FAMILY_TC,
        "ok": bool(rank_row.get("ok")),
        "n_stocks": int(tc_stats.get("n_stocks") or n_stocks),
        "n_rows": int(tc_stats.get("n_rows") or 0),
        "n_features": int(tc_stats.get("n_cols") or 0) or len(col_names or []),
        "n_dates": int(tc_stats.get("n_dates") or 0),
        "hits": int(tc_stats.get("hits") or 0),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "x_shape": [
            int(tc_stats.get("n_rows") or 0),
            int(tc_stats.get("n_cols") or 0) or len(col_names or []),
        ],
        "tc_rank": rank_row,
    }


def _materialize_tc_rank(
    packs: Sequence[dict],
    *,
    progress_cb: ProgressCb,
) -> Dict[str, Any]:
    """开盘 tc_rank 与 tc 一起写。日线由调用方加载并复制，这里不再读一遍。"""
    from core.research.panel_matrix import collect_tau_compact
    from core.yhat.keys import TAU_Z_FEATURES

    packs = [row for row in (packs or []) if isinstance(row, dict)]
    if len(packs) < 2:
        return {"family": "tc_rank", "ok": False, "error": "有效日线不足"}
    _emit(progress_cb, 0, 1, f"L1 tc_rank：collect {len(packs)} 只…")

    def _pcb(done: int, total: int, message: str = "") -> None:
        _emit(
            progress_cb,
            done,
            total,
            message or f"L1 tc_rank {int(done)}/{int(total)}",
        )

    try:
        rank_stats: Dict[str, Any] = {}
        _x, _names, _ys, _dates, _metas, n_stocks = collect_tau_compact(
            packs,
            list(TAU_Z_FEATURES),
            tau_hm="open",
            include_alpha_tc=True,
            head="y_tc_rank",
            cache_head="tc_rank",
            progress_cb=_pcb,
            release_rows=True,
            stats_out=rank_stats,
        )
        n_rows = int(rank_stats.get("n_rows") or 0)
        n_stocks = int(rank_stats.get("n_stocks") or n_stocks)
    except Exception as exc:  # noqa: BLE001
        return {"family": "tc_rank", "ok": False, "error": str(exc)}
    return {
        "family": "tc_rank",
        "ok": n_rows > 0,
        "n_stocks": int(n_stocks),
        "n_rows": n_rows,
        "hits": int(rank_stats.get("hits") or 0),
        "error": None if n_rows > 0 else "empty tc_rank",
    }


def _materialize_co(
    codes: Sequence[str],
    *,
    lookback: int,
    progress_cb: ProgressCb,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    failures: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from core.research.panel_matrix import collect_co_compact
    from core.yhat.keys import CO_Z_FEATURES

    t0 = time.perf_counter()
    if stock_bars is None:
        _emit(progress_cb, 0, 1, "L1 co：加载日线…")
        stock_bars, failures, _ = _stock_bar_items(codes, lookback=lookback)
    else:
        failures = list(failures or [])
    packs = [
        {"stock_code": c, "code": c, "bars": list(stock_bars.get(c) or [])}
        for c in codes
        if stock_bars.get(c)
    ]
    if len(packs) < 2:
        return {
            "family": FAMILY_CO,
            "ok": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "n_stocks": len(packs),
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }

    def _pcb(done: int, total: int, message: str = "") -> None:
        _emit(progress_cb, done, total, message or f"L1 co：{done}/{total}")

    _emit(progress_cb, 0, 1, f"L1 co：collect_co_compact {len(packs)} 只…")
    co_stats: Dict[str, Any] = {}
    _x, col_names, _ys, _dates, _metas, n_stocks = collect_co_compact(
        packs,
        list(CO_Z_FEATURES),
        include_alpha_oo=True,
        head="y_co",
        progress_cb=_pcb,
        release_rows=True,
        stats_out=co_stats,
    )
    return {
        "family": FAMILY_CO,
        "ok": True,
        "n_stocks": int(co_stats.get("n_stocks") or n_stocks),
        "n_rows": int(co_stats.get("n_rows") or 0),
        "n_features": int(co_stats.get("n_cols") or 0) or len(col_names or []),
        "n_dates": int(co_stats.get("n_dates") or 0),
        "hits": int(co_stats.get("hits") or 0),
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "x_shape": [
            int(co_stats.get("n_rows") or 0),
            int(co_stats.get("n_cols") or 0) or len(col_names or []),
        ],
    }


def _materialize_oo_rank(
    codes: Sequence[str],
    *,
    lookback: int,
    horizon_days: int,
    progress_cb: ProgressCb,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    failures: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """oo_rank：默认同口径读 oo 的 L1；超额或无法复用 oo 指纹时才现算并写穿。"""
    from core.research.oo_rank_panel import build_oo_rank_day_panels

    t0 = time.perf_counter()
    if stock_bars is None:
        _emit(progress_cb, 0, 1, "L1 oo_rank：加载日线…")
        stock_bars, failures, _ = _stock_bar_items(codes, lookback=lookback)
    else:
        failures = list(failures or [])
    packs = [
        {"stock_code": c, "code": c, "bars": list(stock_bars.get(c) or [])}
        for c in codes
        if stock_bars.get(c)
    ]
    if len(packs) < 2:
        return {
            "family": FAMILY_OO_RANK,
            "ok": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "n_stocks": len(packs),
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
    _emit(progress_cb, 0, 1, f"L1 oo_rank：build panels {len(packs)} 只…")

    def _pcb(done: int, total: int, message: str = "") -> None:
        _emit(
            progress_cb,
            done,
            total,
            message or f"L1 oo_rank {int(done)}/{int(total)}",
        )

    try:
        panels = build_oo_rank_day_panels(
            packs,
            horizon_days=int(horizon_days),
            progress_cb=_pcb,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "family": FAMILY_OO_RANK,
            "ok": False,
            "error": str(exc),
            "elapsed_s": round(time.perf_counter() - t0, 2),
        }
    n = len(panels) if isinstance(panels, (list, dict)) else 0
    return {
        "family": FAMILY_OO_RANK,
        "ok": n > 0,
        "n_panels": n,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "error": None if n > 0 else "empty panels",
    }


def _eligible_minute_span(packs: Sequence[dict]) -> tuple:
    """有分钟线的股票数，以及这些分钟线覆盖的交易日数。"""
    days = set()
    eligible = 0
    for row in packs or []:
        if not isinstance(row, dict):
            continue
        bars = row.get("minute_bars")
        if not isinstance(bars, list) or not bars:
            continue
        eligible += 1
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            day = str(bar.get("date") or "")[:10]
            if len(day) >= 10:
                days.add(day)
    return eligible, len(days)


def _clock_reaches_lake(row: dict, *, n_eligible: int, n_days: int) -> bool:
    """单钟写入股票数和天数都达到 ``LAKE_COVER_FRAC``。"""
    from core.research.materialize.cover_policy import cover_reaches

    try:
        stocks = int(row.get("n_stocks") or 0)
        dates = int(row.get("n_dates") or 0)
    except (TypeError, ValueError):
        return False
    return cover_reaches(stocks, n_eligible, minimum=2) and cover_reaches(
        dates, n_days, minimum=1
    )


def _attach_minute_bars(packs: List[dict]) -> int:
    """给 packs 挂本地 5m；返回挂上分钟的票数。

    实现委托 ``panel_matrix.attach_minute_bars_slice``（测试可仍 patch 本函数）。
    """
    from core.research.panel_matrix import attach_minute_bars_slice

    return int(attach_minute_bars_slice(packs, 0, len(packs) if packs else 0))


def materialize_l1_tau_grid(
    codes: Sequence[str],
    *,
    lookback: int = 1000,
    clocks: Sequence[str] | None = None,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    """H1：按训练网格写 ``tc_HHMM`` L1（τc∪窗头列）。须本地 5m。"""
    from core.research.day_cube_gc import l2_invalidation_batch

    with l2_invalidation_batch():
        return _materialize_l1_tau_grid_body(
            codes,
            lookback=lookback,
            clocks=clocks,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )


def _materialize_l1_tau_grid_body(
    codes: Sequence[str],
    *,
    lookback: int = 1000,
    clocks: Sequence[str] | None = None,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    from core.research.materialize.catalog import suggested_tau_clocks
    from core.research.materialize.horizon_names import horizon_l1_feature_names
    from core.research.panel_matrix import collect_tau_grid_panels

    t0 = time.perf_counter()
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    clock_list = [str(c).strip() for c in (clocks or suggested_tau_clocks()) if str(c or "").strip()]
    clock_list = [c for c in clock_list if c.lower() not in ("open", "09:30", "0930")]
    report: Dict[str, Any] = {
        "ok": False,
        "n_codes": len(code_list),
        "clocks": clock_list,
        "by_clock": {},
        "minute_attached": 0,
        "elapsed_s": 0.0,
    }
    if len(code_list) < 2 or not clock_list:
        report["error"] = "codes < 2 or empty tau clocks"
        return report

    _emit(progress_cb, 0, 1, "L1 τ 网格：加载日线…")
    try:
        stock_bars, failures, _ = _stock_bar_items(code_list, lookback=lookback)
    except Exception as exc:  # noqa: BLE001
        logger.warning("l1 tau grid bars failed: %s", exc)
        logger.debug("l1 tau grid bars failed", exc_info=True)
        report["error"] = str(exc) or type(exc).__name__
        report["elapsed_s"] = round(time.perf_counter() - t0, 2)
        _emit(progress_cb, 1, 1, f"L1 τ 网格未完成 · {report['error']}")
        return report
    packs = [
        {"stock_code": c, "code": c, "bars": stock_bars.get(c) or []}
        for c in code_list
        if isinstance(stock_bars, dict) and stock_bars.get(c)
    ]
    if len(packs) < 2:
        report["error"] = f"有效日线不足（失败 {len(failures)}）"
        report["failures"] = list(failures or [])[:8]
        report["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return report

    # 仅探测首批是否够用；整池 5m 由 collect_tau_grid_panels 按批挂载
    from core.research.panel_matrix import _TAU_POOL_BATCH

    probe_n = min(len(packs), max(4, int(_TAU_POOL_BATCH)))
    probe = packs[:probe_n]
    try:
        attached_probe = _attach_minute_bars(probe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("l1 tau grid minutes failed: %s", exc)
        logger.debug("l1 tau grid minutes failed", exc_info=True)
        report["error"] = str(exc) or type(exc).__name__
        report["elapsed_s"] = round(time.perf_counter() - t0, 2)
        _emit(progress_cb, 1, 1, f"L1 τ 网格未完成 · {report['error']}")
        return report
    if attached_probe < 2:
        for row in probe:
            if isinstance(row, dict):
                row.pop("minute_bars", None)
        report["minute_attached"] = 0
        report["error"] = "本地 5m 不足（请先分钟增量/强更）"
        report["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return report
    eligible_probe, span_probe = _eligible_minute_span(probe)
    from core.research.materialize.cover_policy import (
        clamp_span_days,
        format_l1_clock_cover_error,
        scale_eligible_stocks,
    )

    # 有分钟的票数可按池比例外推；交易日跨度是共享日历，禁止 ×(池/探针)
    eligible = scale_eligible_stocks(
        eligible_probe, probe_n=probe_n, pool_n=len(packs)
    )
    span_days = int(span_probe or 0)
    for row in probe:
        if isinstance(row, dict):
            row.pop("minute_bars", None)

    feat_names = horizon_l1_feature_names()
    ok_n = 0
    if _cancelled(cancel_cb):
        report["error"] = "cancelled"
        report["ok"] = False
        report["minute_attached"] = 0
    else:
        try:
            summary = collect_tau_grid_panels(
                packs,
                feat_names,
                clock_list,
                include_alpha_tc=True,
                include_alpha_th=True,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        except MaterializeRequired:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("l1 tau grid collect failed: %s", exc)
            logger.debug("l1 tau grid collect failed", exc_info=True)
            report["error"] = str(exc) or type(exc).__name__
            report["elapsed_s"] = round(time.perf_counter() - t0, 2)
            _emit(progress_cb, 1, 1, f"L1 τ 网格未完成 · {report['error']}")
            return report
        attached = int(summary.get("minute_attached") or 0)
        report["minute_attached"] = attached
        if attached > 0:
            eligible = max(eligible, attached)
        if summary.get("cancelled"):
            report["error"] = "cancelled"
            report["ok"] = False
        max_written_dates = 0
        for hm in clock_list:
            raw = (summary.get("by_clock") or {}).get(hm) or {}
            try:
                max_written_dates = max(max_written_dates, int(raw.get("n_dates") or 0))
            except (TypeError, ValueError):
                pass
        span_days = clamp_span_days(span_days, max_written_dates)
        report["lake_eligible"] = int(eligible)
        report["lake_span_days"] = int(span_days)
        for hm in clock_list:
            row = dict((summary.get("by_clock") or {}).get(hm) or {})
            if not row:
                row = {"ok": False, "error": "sparse"}
            report["by_clock"][hm] = row
            if _clock_reaches_lake(row, n_eligible=eligible, n_days=span_days):
                row["ok"] = True
                ok_n += 1
            else:
                row["ok"] = False
                row["error"] = row.get("error") or format_l1_clock_cover_error(
                    hm,
                    n_stocks=int(row.get("n_stocks") or 0),
                    n_dates=int(row.get("n_dates") or 0),
                    n_eligible=eligible,
                    n_days=span_days,
                )

    if report.get("error") == "cancelled":
        report["ok"] = False
    else:
        report["ok"] = bool(clock_list) and ok_n == len(clock_list)
        if not report["ok"] and not report.get("error"):
            sample = ""
            for hm in clock_list:
                bit = str((report.get("by_clock") or {}).get(hm, {}).get("error") or "")
                if bit:
                    sample = bit
                    break
            report["error"] = (
                f"τ 网格 L1 覆盖不足 ok={ok_n}/{len(clock_list)}"
                + (f" · {sample}" if sample else "")
            )
    report["clocks_ok"] = ok_n
    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    if report.get("error") == "cancelled":
        tail = "L1 τ 网格已取消"
    elif not report["ok"]:
        tail = f"L1 τ 网格未完成 · {report['elapsed_s']}s"
    else:
        tail = f"L1 τ 网格完成 · {report['elapsed_s']}s"
    _emit(progress_cb, 1, 1, tail)
    return report


def _order_l1_families(families: Sequence[str]) -> List[str]:
    """含 oo 时把 oo_rank 放到队尾（P0 先齐，可选 rank 失败/取消不拖垮主族）。"""
    items = [str(f) for f in families]
    if FAMILY_OO not in items:
        return [f for f in items if f != FAMILY_OO_RANK] or items
    core = [f for f in items if f != FAMILY_OO_RANK]
    return core + [FAMILY_OO_RANK]


def materialize_l1(
    families: Sequence[str],
    codes: Sequence[str],
    *,
    lookback: int = 1000,
    horizon_days: int = 1,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    """按 family 物化 L1。返回 ``by_family`` 与汇总。"""
    from core.research.day_cube_gc import l2_invalidation_batch

    with l2_invalidation_batch():
        return _materialize_l1_body(
            families,
            codes,
            lookback=lookback,
            horizon_days=horizon_days,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )


def _materialize_l1_body(
    families: Sequence[str],
    codes: Sequence[str],
    *,
    lookback: int = 1000,
    horizon_days: int = 1,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    requested = normalize_families(families)
    implicit_rank = FAMILY_OO in requested and FAMILY_OO_RANK not in requested
    fams = _order_l1_families(requested)
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    p0_requested = [f for f in requested if f != FAMILY_OO_RANK]
    report: Dict[str, Any] = {
        "ok": True,
        "families": fams,
        "n_codes": len(code_list),
        "by_family": {},
        "elapsed_s": 0.0,
    }
    if len(code_list) < 2:
        report["ok"] = False
        report["error"] = "codes < 2"
        return report
    t0 = time.perf_counter()
    n = len(fams)
    shared_bars: Optional[Dict[str, List[dict]]] = None
    shared_failures: List[str] = []
    if any(
        fam in fams
        for fam in (FAMILY_OO, FAMILY_TC, FAMILY_CO, FAMILY_OO_RANK)
    ):
        _emit(progress_cb, 0, max(1, n), "L1：加载日线…")
        shared_bars, shared_failures, _ = _stock_bar_items(
            code_list, lookback=lookback
        )

    def _p0_ok() -> bool:
        if not p0_requested:
            return False
        return all(
            bool((report["by_family"].get(f) or {}).get("ok")) for f in p0_requested
        )

    for i, fam in enumerate(fams):
        if _cancelled(cancel_cb):
            # 显式未要 oo_rank：P0 已齐时取消只跳过可选 rank，不把整次 L1 打失败
            # （否则日线物化卡在 oo_rank 加载日线时一取消，oo/tc/co 成果也算失败）。
            if fam == FAMILY_OO_RANK and implicit_rank and _p0_ok():
                report["note"] = "cancelled during optional oo_rank"
                logger.info("L1 cancel during optional oo_rank; P0 kept")
                break
            report["ok"] = False
            report["error"] = "cancelled"
            break
        if (
            fam == FAMILY_OO_RANK
            and implicit_rank
            and not (report["by_family"].get(FAMILY_OO) or {}).get("ok")
        ):
            continue
        fam_cb = _step_progress(progress_cb, i, n)
        fam_cb(0, 1, f"L1 {fam}（{i + 1}/{n}）…")
        try:
            if fam == FAMILY_OO:
                row = _materialize_oo(
                    code_list,
                    lookback=lookback,
                    horizon_days=horizon_days,
                    progress_cb=fam_cb,
                    stock_bars=shared_bars,
                    failures=shared_failures,
                )
            elif fam == FAMILY_TC:
                row = _materialize_tc(
                    code_list,
                    lookback=lookback,
                    progress_cb=fam_cb,
                    stock_bars=shared_bars,
                    failures=shared_failures,
                )
            elif fam == FAMILY_CO:
                row = _materialize_co(
                    code_list,
                    lookback=lookback,
                    progress_cb=fam_cb,
                    stock_bars=shared_bars,
                    failures=shared_failures,
                )
            elif fam == FAMILY_OO_RANK:
                row = _materialize_oo_rank(
                    code_list,
                    lookback=lookback,
                    horizon_days=horizon_days,
                    progress_cb=fam_cb,
                    stock_bars=shared_bars,
                    failures=shared_failures,
                )
            else:
                row = {"family": fam, "ok": False, "error": "unknown family"}
        except MaterializeRequired:
            raise
        except Exception as exc:  # noqa: BLE001 — 一族失败仍留下报告，后面的族继续
            logger.exception("L1 %s failed", fam)
            row = {
                "family": fam,
                "ok": False,
                "error": str(exc) or type(exc).__name__,
            }
        report["by_family"][fam] = row
        if not row.get("ok"):
            if fam == FAMILY_OO_RANK and implicit_rank and _p0_ok():
                report["oo_rank_optional_failed"] = True
                logger.warning(
                    "optional oo_rank failed after P0 ok: %s",
                    row.get("error") or "unknown",
                )
            else:
                report["ok"] = False
        # 一族做完就丢掉面板/立方缓存，避免 800×多 family 叠在同一进程把 web 拖死
        try:
            from core.research.day_cube import clear_memory

            clear_memory()
        except Exception:  # noqa: BLE001
            logger.debug("clear day cube memory after %s failed", fam, exc_info=True)
        try:
            import gc

            gc.collect()
        except Exception:  # noqa: BLE001
            pass
    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    from core.research.materialize.progress import STEP_SCALE

    _emit(
        progress_cb,
        max(1, n) * STEP_SCALE,
        max(1, n) * STEP_SCALE,
        f"L1 完成 · {report['elapsed_s']}s",
    )
    return report
