"""分组 OLS 研究面板：日线 +（可选）PIT 财务探针。

``pit_fundamentals=True`` 时：
- 面板附带「末日 as_of」财务 metrics（供组 IC / 展示）；
- 逐决策日 PIT 仍由 ``collect_subscore_forward_panel`` 按日 resolve（大宇宙可改快照模式）。
``False`` 时：不注入财务，``lookahead_flags.fundamentals=none``。

拉日线默认 **刷新过期票**（约 36h 内缓存仍复用）；
``refresh_bars=False`` 时纯缓存优先，供改参快跑。
``force_latest_bars=True``：跳过 36h 复用，增量拉网合并到最新（当日首次分组自动开）。
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


def _load_bars_for_cluster(
    code: str,
    *,
    lookback: int,
    refresh_bars: bool,
    force_latest_bars: bool = False,
) -> tuple:
    """返回 (bars, src, fetched_remote).

    默认缓存优先；``refresh_bars`` 时仅对「超过约 36h / 条数不足」的票打远端；
    ``force_latest_bars`` 时跳过 36h 复用，``cache_max_age_hours<=0`` 增量拉到最新。
    """
    from core.data_service import bars_and_source_research as bars_and_source

    limit = lookback + 35
    min_bars = max(20, min(limit, 40))
    if force_latest_bars:
        bars, src = bars_and_source(
            code,
            limit=limit,
            cache_max_age_hours=0,
            incremental=True,
            offline_ok=False,
        )
        src_s = str(src or "empty")
        remote = bool(bars) and (
            "akshare" in src_s
            or src_s.startswith("empty")
            or not src_s.startswith("cache")
        )
        # skip_remote（末根已到今天）仍算完成强制路径，不计远端
        if src_s.startswith("cache"):
            remote = False
        if bars:
            return list(bars), src_s, remote
        return [], "empty", True

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

    # 过期或缺条：增量拉网合并（cache_max_age<=0 → 不短路，缺口合并落盘）
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


def _index_symbol_candidates(bench: str) -> List[str]:
    """基准名 → 可能落在本地 bars 缓存里的代码。"""
    key = str(bench or "").strip().lower()
    mapping = {
        "hs300": ["000300", "sh000300", "399300"],
        "csi300": ["000300", "sh000300"],
        "sh000300": ["000300", "sh000300"],
        "沪深300": ["000300", "sh000300"],
        "hsi": ["HSI", "hsi"],
        "恒生": ["HSI", "hsi"],
        "恒生指数": ["HSI", "hsi"],
    }
    out: List[str] = []
    for c in mapping.get(key, []) + [str(bench or "").strip()]:
        if c and c not in out:
            out.append(c)
    return out


def _load_index_bars_once(
    bench: str,
    *,
    limit: int,
    refresh_bars: bool,
    force_latest_bars: bool = False,
    progress_cb: ProgressCb = None,
    progress_n: int = 1,
    timeout_sec: float = 12.0,
) -> List[dict]:
    """加载一组指数日线；缓存优先时不阻塞打网；刷新时带超时+心跳。

    指数失败返回 []，分组仍可继续（相对强度等因子降级）。
    """
    from core.data_service import bars_and_source_research as bars_and_source
    from core.data_service import get_index_bars

    bench_s = str(bench or "").strip()
    if not bench_s:
        return []
    min_bars = max(15, min(limit, 30))
    do_net = bool(refresh_bars or force_latest_bars)
    age = 0.0 if force_latest_bars else (36.0 if refresh_bars else 24.0 * 14)

    # 1) 本地缓存（含指数代码若曾入库）；强制最新时仍先试盘，不够再打网
    if not force_latest_bars:
        for code in _index_symbol_candidates(bench_s):
            try:
                bars, _src = bars_and_source(
                    code,
                    limit=limit,
                    cache_max_age_hours=age,
                    incremental=False,
                    offline_ok=True,
                )
                if bars and len(bars) >= min_bars:
                    return list(bars)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
                logger.debug("指数本地缓存未命中 %s/%s", bench_s, code, exc_info=True)

    # 2) 缓存优先：缺指数也不打远端，避免 AkShare 挂死整任务
    if not do_net:
        if progress_cb:
            try:
                progress_cb(f"拉指数跳过（缓存优先·{bench_s}）", 0, progress_n)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
                pass
        return []

    # 3) 刷新：远端拉取，超时放弃（后台线程可能仍在跑，但主路径继续）
    if progress_cb:
        try:
            progress_cb(f"拉指数 {bench_s}…", 0, progress_n)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
            pass

    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(get_index_bars, bench_s, limit=limit)
    t0 = time.time()
    try:
        while True:
            finished, _ = wait([fut], timeout=1.2, return_when=FIRST_COMPLETED)
            if finished:
                try:
                    pack = fut.result(timeout=0.1)
                    if isinstance(pack, dict):
                        return list(pack.get("bars") or [])
                    if isinstance(pack, tuple):
                        return list(pack[0] or [])
                    return list(pack or [])
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
                    logger.warning("拉指数失败 %s", bench_s, exc_info=True)
                    return []
            elapsed = time.time() - t0
            if progress_cb:
                try:
                    progress_cb(
                        f"拉指数 {bench_s}… {int(elapsed)}s",
                        0,
                        progress_n,
                    )
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
                    pass
            if elapsed >= max(4.0, float(timeout_sec)):
                logger.warning(
                    "拉指数超时 %s after %.0fs，继续分组（无指数）",
                    bench_s,
                    elapsed,
                )
                if progress_cb:
                    try:
                        progress_cb(
                            f"拉指数超时跳过（{bench_s}）",
                            0,
                            progress_n,
                        )
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
                        pass
                return []
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)


def _load_one_panel(
    code: str,
    *,
    lookback: int,
    use_pit: bool,
    role: dict,
    fund_cfg: dict,
    index_bars: Optional[List[dict]],
    refresh_bars: bool = False,
    force_latest_bars: bool = False,
) -> Dict[str, Any]:
    from core.fundamentals_pit import resolve_fundamentals_for_score

    # 研究分组不打现价：腾讯接口慢/挂起时会把整批卡在 0/N
    sym_s = str(code or "").strip()
    bars, src, fetched_remote = _load_bars_for_cluster(
        code,
        lookback=lookback,
        refresh_bars=bool(refresh_bars),
        force_latest_bars=bool(force_latest_bars),
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
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
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
    force_latest_bars: bool = False,
) -> Dict[str, Any]:
    """构建分组 OLS 输入面板，并汇总财务 PIT 覆盖。

    refresh_bars=True（默认）：缓存未过期（约 36h）仍用本地；过期/缺条才限流拉网。
    refresh_bars=False：本地有足够日线即用，不打 AkShare（改参快跑）。
    force_latest_bars=True：增量强制对齐最新（覆盖 refresh 的 36h 复用）。
    """
    from core.fundamentals_pit import fundamentals_pit_summary
    from core.ports.market import default_benchmark, resolve_market_code
    from core.signal.config import load_signal_config

    use_pit = bool(pit_fundamentals)
    do_force = bool(force_latest_bars)
    do_refresh = bool(refresh_bars) or do_force
    roles = dict(code_roles or {})
    fund_cfg = (load_signal_config() or {}).get("fundamentals") or {}
    code_list = [str(c).strip() for c in codes if str(c).strip()]
    n = len(code_list)

    if progress_cb:
        try:
            if do_force:
                mode = "强制更新日线到最新"
            elif do_refresh:
                mode = "刷新过期票"
            else:
                mode = "缓存优先"
            progress_cb(f"拉日线 0/{n}（{mode}）", 0, n)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
            logger.warning("面板构建失败", exc_info=True)

    # 指数只拉一次；缓存优先不阻塞打网（避免卡在「拉日线 0/N」被 90s 回收）
    index_by_bench: Dict[str, List[dict]] = {}
    try:
        markets = {resolve_market_code(c)[0] for c in code_list[:5]}
        markets.add("CN")
        idx_timeout = 18.0 if do_refresh else 0.0
        for m in markets:
            if not m:
                continue
            bench = str(default_benchmark(m))
            if bench in index_by_bench:
                continue
            index_by_bench[bench] = _load_index_bars_once(
                bench,
                limit=lookback + 35,
                refresh_bars=do_refresh,
                force_latest_bars=do_force,
                progress_cb=progress_cb,
                progress_n=n,
                timeout_sec=idx_timeout,
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
        logger.warning("指数面板预取失败（继续个股）", exc_info=True)

    def _index_for(code: str) -> Optional[List[dict]]:
        try:
            market, _ = resolve_market_code(code)
            bench = str(default_benchmark(market))
            return index_by_bench.get(bench) or index_by_bench.get(
                str(default_benchmark("CN"))
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
            return index_by_bench.get(str(default_benchmark("CN")))

    # 强制刷新时降并发，避免 AkShare 打爆；纯缓存可多开
    if do_refresh:
        workers = max(1, min(int(max_workers or 4), 4, n or 1))
        batch_timeout = max(90.0, min(360.0, 1.5 * float(n or 1) + 60.0))
    else:
        workers = max(1, min(int(max_workers or 12), 24, n or 1))
        # Limit≤100：缓存路径给足时间，避免大批次未完成就被收尾
        batch_timeout = max(45.0, min(240.0, 0.9 * float(n or 1) + 40.0))
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
                force_latest_bars=do_force,
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
                        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
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
                        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
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
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
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
        # DC1 · 最小停牌过滤（零量 / 关键词）
        halt_audit = None
        try:
            from core.market_calendar import filter_halted_bars

            bars, halt_audit = filter_halted_bars(bars)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_panels.py", exc_info=True)
            halt_audit = None
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
                    "halt_audit": halt_audit,
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
                "halt_audit": halt_audit,
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
            "force_latest": do_force,
            "remote_count": int(remote_n),
            "total": int(n),
            "cache_count": max(0, int(n) - int(remote_n)),
            "note": (
                f"强制增量更新到最新（远端 {remote_n}/{n}）"
                if do_force
                else (
                    f"刷新过期：远端更新 {remote_n}/{n}，其余用本地缓存"
                    if do_refresh
                    else f"缓存优先：未强制拉网（本批远端回退 {remote_n}/{n}）"
                )
            ),
        },
    }
