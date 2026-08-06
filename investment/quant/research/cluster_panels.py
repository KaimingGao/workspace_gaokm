"""分组 OLS 研究面板：日线 +（可选）PIT 财务探针。

``pit_fundamentals=True`` 时：
- 面板附带「末日 as_of」财务 metrics（供组 IC / 展示）；
- 逐决策日 PIT 仍由 ``collect_subscore_forward_panel`` 按日 resolve（大宇宙可改快照模式）。
``False`` 时：不注入财务，``lookahead_flags.fundamentals=none``。

拉日线默认 **刷新过期票**（约 36h 内缓存仍复用）；
``refresh_bars=False`` 时纯缓存优先，供改参快跑。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeout,
    wait,
)
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str, int, int], None]]


def _last_bar_date(bars: Sequence[dict]) -> Optional[str]:
    if not bars:
        return None
    d = str((bars[-1] or {}).get("date") or "")[:10]
    return d or None


def _load_bars_for_cluster(code: str, *, lookback: int, refresh_bars: bool) -> tuple:
    """返回 (bars, src, fetched_remote).

    默认缓存优先；``refresh_bars`` 时仅对「超过约 36h / 条数不足」的票打远端，限流由上层 workers 控制。
    """
    from core.data_service import bars_and_source

    limit = lookback + 35
    min_bars = max(20, min(limit, 40))
    if not refresh_bars:
        bars, src = bars_and_source(
            code,
            limit=limit,
            cache_max_age_hours=24 * 14,
            incremental=False,
            offline_ok=True,
        )
        return list(bars or []), str(src or "empty"), False

    # 先吃较新的本地缓存
    bars, src = bars_and_source(
        code,
        limit=limit,
        cache_max_age_hours=36,
        incremental=False,
        offline_ok=False,
    )
    if bars and len(bars) >= min_bars:
        return list(bars), str(src or "cache"), False

    # 过期或缺条：增量拉网合并
    bars2, src2 = bars_and_source(
        code,
        limit=limit,
        cache_max_age_hours=0,
        incremental=True,
        offline_ok=False,
    )
    if bars2:
        return list(bars2), str(src2 or "akshare"), True
    # 远端失败则退回任意本地
    if bars:
        return list(bars), str(src or "cache:stale"), False
    return [], "empty", True


def _load_one_panel(
    code: str,
    *,
    lookback: int,
    use_pit: bool,
    role: dict,
    fund_cfg: dict,
    index_bars: Optional[List[dict]],
    refresh_bars: bool = False,
) -> Dict[str, Any]:
    from core.fundamentals_pit import resolve_fundamentals_for_score

    # 研究分组不打现价：腾讯接口慢/挂起时会把整批卡在 0/N
    sym_s = str(code or "").strip()
    bars, src, fetched_remote = _load_bars_for_cluster(
        code, lookback=lookback, refresh_bars=bool(refresh_bars)
    )
    out: Dict[str, Any] = {
        "input_code": code,
        "code": sym_s,
        "quote": None,
        "role": role,
        "bars": bars or [],
        "data_source": src,
        "fetched_remote": bool(fetched_remote),
        "fund_resolve": None,
    }
    if not bars:
        out["fundamentals"] = None
        out["fundamentals_mode"] = "empty_bars"
        out["index_bars"] = None
        out["fundamentals_as_of"] = None
        return out

    fund_metrics = None
    fund_mode = "none"
    as_of = _last_bar_date(bars)
    if use_pit and as_of:
        try:
            resolved = resolve_fundamentals_for_score(
                sym_s,
                as_of=as_of,
                fund_cfg=fund_cfg,
                live_fallback=False,
            )
            out["fund_resolve"] = resolved
            if resolved.get("ok") and resolved.get("metrics"):
                fund_metrics = dict(resolved["metrics"])
                try:
                    from core.valuation_em import enrich_fundamentals_metrics

                    fund_metrics = (
                        enrich_fundamentals_metrics(sym_s, fund_metrics) or fund_metrics
                    )
                except Exception:
                    logger.warning("面板加载失败", exc_info=True)
                fund_mode = "pit_as_of"
            else:
                fund_mode = str(resolved.get("mode") or "as_of_missing")
        except Exception as e:
            out["fund_resolve"] = {
                "ok": False,
                "metrics": None,
                "fundamentals_pit": True,
                "mode": "error",
                "error": str(e)[:120],
            }
            fund_mode = "error"
    elif use_pit:
        fund_mode = "as_of_missing"

    out.update(
        {
            "bars": bars,
            "index_bars": index_bars,
            "fundamentals": fund_metrics,
            "fundamentals_mode": fund_mode,
            "fundamentals_as_of": as_of if use_pit else None,
        }
    )
    return out


def _empty_row(code: str, role: dict, error: str) -> Dict[str, Any]:
    return {
        "input_code": code,
        "code": code,
        "quote": None,
        "role": role,
        "bars": [],
        "fundamentals": None,
        "fundamentals_mode": "error",
        "index_bars": None,
        "fundamentals_as_of": None,
        "data_source": None,
        "fund_resolve": {"ok": False, "error": str(error)[:120]},
    }


def build_cluster_ols_panels(
    codes: Sequence[str],
    *,
    lookback: int = 80,
    pit_fundamentals: bool = True,
    code_roles: Optional[Dict[str, Any]] = None,
    progress_cb: ProgressCb = None,
    max_workers: int = 12,
    refresh_bars: bool = True,
) -> Dict[str, Any]:
    """构建分组 OLS 输入面板，并汇总财务 PIT 覆盖。

    refresh_bars=True（默认）：缓存未过期（约 36h）仍用本地；过期/缺条才限流拉网。
    refresh_bars=False：本地有足够日线即用，不打 AkShare（改参快跑）。
    """
    from core.fundamentals_pit import fundamentals_pit_summary
    from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code
    from core.signal.config import load_signal_config

    use_pit = bool(pit_fundamentals)
    do_refresh = bool(refresh_bars)
    roles = dict(code_roles or {})
    fund_cfg = (load_signal_config() or {}).get("fundamentals") or {}
    code_list = [str(c).strip() for c in codes if str(c).strip()]
    n = len(code_list)

    if progress_cb:
        try:
            mode = "强制刷新过期票" if do_refresh else "缓存优先"
            progress_cb(f"拉日线 0/{n}（{mode}）", 0, n)
        except Exception:
            logger.warning("面板构建失败", exc_info=True)

    # 指数只拉一次，避免每票抢锁打远端
    index_by_bench: Dict[str, List[dict]] = {}
    try:
        markets = {resolve_market_code(c)[0] for c in code_list[:5]}
        markets.add("CN")
        for m in markets:
            if not m:
                continue
            bench = default_benchmark(m)
            if bench in index_by_bench:
                continue
            bars, _ = fetch_index_bars(bench, limit=lookback + 35)
            index_by_bench[str(bench)] = list(bars or [])
    except Exception:
        logger.warning("面板渲染失败", exc_info=True)

    def _index_for(code: str) -> Optional[List[dict]]:
        try:
            market, _ = resolve_market_code(code)
            bench = str(default_benchmark(market))
            return index_by_bench.get(bench) or index_by_bench.get(
                str(default_benchmark("CN"))
            )
        except Exception:
            return index_by_bench.get(str(default_benchmark("CN")))

    # 强制刷新时降并发，避免 AkShare 打爆；纯缓存可多开
    if do_refresh:
        workers = max(1, min(int(max_workers or 4), 4, n or 1))
        batch_timeout = max(90.0, min(240.0, 1.5 * float(n or 1) + 60.0))
    else:
        workers = max(1, min(int(max_workers or 12), 24, n or 1))
        batch_timeout = max(45.0, min(120.0, 0.8 * float(n or 1) + 30.0))
    raw_rows: List[Optional[Dict[str, Any]]] = [None] * n
    done = 0
    remote_n = 0
    remote_lock = threading.Lock()
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                _load_one_panel,
                code,
                lookback=lookback,
                use_pit=use_pit,
                role=roles.get(code) or {},
                fund_cfg=fund_cfg,
                index_bars=_index_for(code),
                refresh_bars=do_refresh,
            ): i
            for i, code in enumerate(code_list)
        }
        pending = set(futs.keys())
        try:
            while pending:
                finished, pending = wait(
                    pending, timeout=1.5, return_when=FIRST_COMPLETED
                )
                if not finished:
                    if progress_cb:
                        try:
                            elapsed = int(time.time() - t0)
                            progress_cb(
                                f"拉日线 {done}/{n}（远端 {remote_n} · {elapsed}s）",
                                done,
                                n,
                            )
                        except Exception:
                            logger.warning("面板子项加载失败", exc_info=True)
                    if time.time() - t0 >= batch_timeout:
                        raise FuturesTimeout()
                    continue
                for fut in finished:
                    i = futs[fut]
                    try:
                        raw_rows[i] = fut.result(timeout=0.1)
                    except Exception as e:
                        raw_rows[i] = _empty_row(
                            code_list[i], roles.get(code_list[i]) or {}, str(e)
                        )
                    row = raw_rows[i] or {}
                    if row.get("fetched_remote"):
                        with remote_lock:
                            remote_n += 1
                    done += 1
                    if progress_cb and (done == n or done % 5 == 0 or done <= 3):
                        try:
                            progress_cb(
                                f"拉日线 {done}/{n}（远端 {remote_n}）",
                                done,
                                n,
                            )
                        except Exception:
                            logger.warning("面板子项处理失败", exc_info=True)
        except FuturesTimeout:
            for fut, i in futs.items():
                if raw_rows[i] is not None:
                    continue
                if fut.done():
                    try:
                        raw_rows[i] = fut.result(timeout=0)
                    except Exception as e:
                        raw_rows[i] = _empty_row(
                            code_list[i], roles.get(code_list[i]) or {}, str(e)
                        )
                else:
                    fut.cancel()
                    raw_rows[i] = _empty_row(
                        code_list[i], roles.get(code_list[i]) or {}, "拉日线超时"
                    )
                if (raw_rows[i] or {}).get("fetched_remote"):
                    remote_n += 1
                done += 1
            if progress_cb:
                try:
                    progress_cb(f"拉日线超时收尾 {done}/{n}（远端 {remote_n}）", done, n)
                except Exception:
                    logger.warning("面板汇总失败", exc_info=True)

    panels: List[Dict[str, Any]] = []
    bars_by_code: Dict[str, Any] = {}
    quotes_by_code: Dict[str, Any] = {}
    fund_resolves: List[Dict[str, Any]] = []
    resolved_codes: List[str] = []
    resolved_seen: set = set()
    watching_resolved: List[str] = []
    holdings_resolved: List[str] = []
    holdings_added_resolved: List[str] = []

    for row in raw_rows:
        if not row:
            continue
        sym_s = str(row.get("code") or "")
        role = row.get("role") or {}
        quote = row.get("quote")
        if quote and quote.get("success"):
            quotes_by_code[sym_s] = quote
        if sym_s and sym_s not in resolved_seen:
            resolved_seen.add(sym_s)
            resolved_codes.append(sym_s)
        if role.get("from_watching") and sym_s not in watching_resolved:
            watching_resolved.append(sym_s)
        if role.get("from_holdings") and sym_s not in holdings_resolved:
            holdings_resolved.append(sym_s)
        if role.get("holdings_added") and sym_s not in holdings_added_resolved:
            holdings_added_resolved.append(sym_s)

        bars = list(row.get("bars") or [])
        fr = row.get("fund_resolve")
        if isinstance(fr, dict):
            fund_resolves.append(fr)

        if not bars:
            panels.append(
                {
                    "code": sym_s,
                    "bars": [],
                    "fundamentals": None,
                    "fundamentals_mode": row.get("fundamentals_mode") or "empty_bars",
                }
            )
            continue

        if sym_s:
            bars_by_code[sym_s] = bars
        panels.append(
            {
                "code": sym_s,
                "bars": bars,
                "index_bars": row.get("index_bars"),
                "fundamentals": row.get("fundamentals"),
                "fundamentals_mode": row.get("fundamentals_mode"),
                "fundamentals_as_of": row.get("fundamentals_as_of"),
                "data_source": row.get("data_source"),
                "role": role,
            }
        )

    if use_pit:
        pit_summary = fundamentals_pit_summary(fund_resolves)
        if pit_summary.get("resolved_ok", 0) > 0:
            fund_label = "pit_as_of"
            note = (
                f"财务按决策日 as_of 解析；末日探针 ok="
                f"{pit_summary.get('resolved_ok')}/{pit_summary.get('sample_count')}；"
                f"逐日拟合走 collect_subscore_forward_panel PIT。"
            )
        else:
            fund_label = "pit_as_of_missing"
            note = (
                "已启用 PIT，但末日探针无可用财务点（拒未来快照）；"
                "财务因子按缺失/中性处理。"
            )
    else:
        pit_summary = {
            "fundamentals_pit": False,
            "sample_count": 0,
            "note": "未启用 PIT 财务。",
        }
        fund_label = "none"
        note = "非 PIT · 未注入财务面板 · 勿当实盘证据。"

    lookahead_flags = {
        "pit_fundamentals": use_pit,
        "fundamentals": fund_label,
        "pit_summary": pit_summary,
        "note": note,
    }

    return {
        "panels": panels,
        "bars_by_code": bars_by_code,
        "quotes_by_code": quotes_by_code,
        "resolved_codes": resolved_codes,
        "watching_resolved": watching_resolved,
        "holdings_resolved": holdings_resolved,
        "holdings_added_resolved": holdings_added_resolved,
        "fund_resolves": fund_resolves,
        "lookahead_flags": lookahead_flags,
        "pit_fundamentals": use_pit,
        "bars_refresh": {
            "requested": do_refresh,
            "remote_count": int(remote_n),
            "total": int(n),
            "cache_count": max(0, int(n) - int(remote_n)),
            "note": (
                f"强制刷新：远端更新 {remote_n}/{n}，其余用本地缓存"
                if do_refresh
                else f"缓存优先：未强制拉网（本批远端回退 {remote_n}/{n}）"
            ),
        },
    }
