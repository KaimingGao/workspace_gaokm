"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.fundamentals_bridge import fetch_fundamentals_batch

# 日报/轻量回测：大宇宙超过此数则只读缓存、优先离线日线，并截断候选
DAILY_PORTFOLIO_MAX_NAMES = 40
_CACHE_WORKERS = 12
_REMOTE_ITEM_TIMEOUT = 25.0

logger = logging.getLogger(__name__)


def should_fetch_backtest_fundamentals(config: Optional[dict] = None) -> bool:
    cfg = config or load_signal_config()
    fund_cfg = cfg.get("fundamentals") or {}
    if not fund_cfg.get("enabled", True):
        return False
    return bool(fund_cfg.get("use_in_backtest", True))


def _resolve_symbol(raw: str) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) == 6:
        return digits
    from core.data_service import get_quote

    quote = get_quote(str(raw))
    return str(quote.get("stock_code") if quote.get("success") else raw)


def _fetch_bars_for_raw(
    raw: str,
    *,
    limit: int,
    offline_ok: bool,
    offline_only: bool = False,
) -> Tuple[str, str, List[dict]]:
    from core.data_service import bars_and_source

    code = str(raw or "").strip()
    sym = _resolve_symbol(code)
    bars, _ = bars_and_source(
        code,
        limit=limit,
        offline_ok=bool(offline_ok),
        offline_only=bool(offline_only),
    )
    if not bars and str(sym) != code:
        bars, _ = bars_and_source(
            str(sym),
            limit=limit,
            offline_ok=bool(offline_ok),
            offline_only=bool(offline_only),
        )
    return code, str(sym), list(bars or [])


def _bars_from_pack(pack: object) -> List[dict]:
    if isinstance(pack, dict):
        return list(pack.get("bars") or [])
    if isinstance(pack, tuple) and pack:
        return list(pack[0] or [])
    return []


def _load_bars_parallel(
    raw_list: List[str],
    *,
    limit: int,
    need: int,
    offline_ok: bool,
) -> Tuple[Dict[str, Tuple[str, List[dict]]], int]:
    """先并发读本地缓存；不够的再经进程池补远端（避开同进程 AkShare 锁）。"""
    by_raw: Dict[str, Tuple[str, List[dict]]] = {}
    n = len(raw_list)
    misses = list(raw_list)
    if offline_ok and n:
        workers = min(_CACHE_WORKERS, max(1, n))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [
                pool.submit(
                    _fetch_bars_for_raw,
                    raw,
                    limit=limit,
                    offline_ok=True,
                    offline_only=True,
                )
                for raw in raw_list
            ]
            for fut in as_completed(futs):
                try:
                    raw, sym, bars = fut.result()
                except Exception:
                    logger.warning("portfolio cache bar load failed", exc_info=True)
                    continue
                by_raw[str(raw)] = (str(sym), list(bars or []))
        for raw in raw_list:
            by_raw.setdefault(raw, (_resolve_symbol(raw), []))
        misses = [raw for raw in raw_list if len(by_raw[raw][1]) < need]

    remote_n = 0
    if not misses:
        return by_raw, remote_n

    from core.data_service import get_bars
    from core.ports.market import batch_map

    packs = batch_map(
        get_bars,
        misses,
        limit=limit,
        offline_ok=False,
        timeout=_REMOTE_ITEM_TIMEOUT,
    )
    for raw, pack in zip(misses, packs):
        bars = _bars_from_pack(pack)
        if not bars:
            by_raw.setdefault(raw, (_resolve_symbol(raw), []))
            continue
        remote_n += 1
        prev = by_raw.get(raw)
        sym = prev[0] if prev else _resolve_symbol(raw)
        by_raw[raw] = (str(sym), list(bars))
    for raw in raw_list:
        by_raw.setdefault(raw, (_resolve_symbol(raw), []))
    return by_raw, remote_n


def load_portfolio_stock_bars(
    candidates: List[str],
    *,
    lookback: int = 120,
    fetch_fundamentals: Optional[bool] = None,
    min_bars: Optional[int] = None,
    offline_ok: bool = True,
    fundamentals_live: Optional[bool] = None,
    max_names: Optional[int] = None,
) -> Tuple[Dict[str, List[dict]], List[str], Dict[str, dict]]:
    """拉取组合回测用日线；可选批量基本面（快照，供 value/quality）。

    min_bars：过短序列直接丢弃（默认约 lookback 的一半，且不少于 16），
    避免单票把共同交易日交集压垮。

    大宇宙（≥40）默认 ``fundamentals_live=False``：只读本地财务快照，
    避免日报在 100 票上串行打远端挂死。

    日线：线程池扫本地缓存，缺票再 ``batch_map(get_bars)`` 进程池补远端。
    """
    raw_list = [str(c).strip() for c in (candidates or []) if str(c).strip()]
    truncated = False
    if max_names is not None and max_names > 0 and len(raw_list) > int(max_names):
        raw_list = raw_list[: int(max_names)]
        truncated = True

    stock_bars: Dict[str, List[dict]] = {}
    failures: List[str] = []
    sym_by_raw: Dict[str, str] = {}
    need = int(min_bars) if min_bars is not None else max(16, min(40, int(lookback or 120) // 2))
    limit = int(lookback or 120) + 35

    by_raw, remote_n = _load_bars_parallel(
        raw_list,
        limit=limit,
        need=need,
        offline_ok=bool(offline_ok),
    )

    for raw in raw_list:
        sym, bars = by_raw.get(raw, (_resolve_symbol(raw), []))
        if bars and len(bars) >= need:
            stock_bars[str(sym)] = bars
            sym_by_raw[str(raw)] = str(sym)
            sym_by_raw[str(sym)] = str(sym)
        elif bars:
            failures.append(f"{raw}(日线{len(bars)}<{need})")
        else:
            failures.append(str(raw))

    logger.info(
        "portfolio bars loaded=%d fail=%d remote=%d universe=%d truncated=%s",
        len(stock_bars),
        len(failures),
        remote_n,
        len(raw_list),
        truncated,
    )

    fundamentals_by_code: Dict[str, dict] = {}
    use_fund = (
        should_fetch_backtest_fundamentals()
        if fetch_fundamentals is None
        else bool(fetch_fundamentals)
    )
    live_fund = fundamentals_live
    if live_fund is None:
        # 大宇宙默认不打远端基本面
        live_fund = len(stock_bars) < DAILY_PORTFOLIO_MAX_NAMES
    if use_fund and stock_bars:
        batch = fetch_fundamentals_batch(
            list(stock_bars.keys()),
            live=bool(live_fund),
        )
        fundamentals_by_code.update(batch)

    if truncated:
        failures.append(f"universe_truncated_to_{len(raw_list)}")

    return stock_bars, failures, fundamentals_by_code
