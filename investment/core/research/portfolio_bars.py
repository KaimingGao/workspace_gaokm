"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.fundamentals_bridge import fetch_fundamentals_batch

# 日报/轻量回测：大宇宙超过此数则只读缓存、优先离线日线，并截断候选
DAILY_PORTFOLIO_MAX_NAMES = 40


def should_fetch_backtest_fundamentals(config: Optional[dict] = None) -> bool:
    cfg = config or load_signal_config()
    fund_cfg = cfg.get("fundamentals") or {}
    if not fund_cfg.get("enabled", True):
        return False
    return bool(fund_cfg.get("use_in_backtest", True))


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
    """
    from core.data_service import bars_and_source, get_quote

    raw_list = [str(c).strip() for c in (candidates or []) if str(c).strip()]
    truncated = False
    if max_names is not None and max_names > 0 and len(raw_list) > int(max_names):
        raw_list = raw_list[: int(max_names)]
        truncated = True

    stock_bars: Dict[str, List[dict]] = {}
    failures: List[str] = []
    sym_by_raw: Dict[str, str] = {}
    need = int(min_bars) if min_bars is not None else max(16, min(40, int(lookback or 120) // 2))

    for raw in raw_list:
        # 六位代码跳过 get_quote 远端解析，减少日报卡顿面
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) == 6:
            sym = digits
        else:
            quote = get_quote(str(raw))
            sym = quote.get("stock_code") if quote.get("success") else str(raw)
        bars, _ = bars_and_source(raw, limit=lookback + 35, offline_ok=bool(offline_ok))
        if not bars and sym != raw:
            bars, _ = bars_and_source(str(sym), limit=lookback + 35, offline_ok=bool(offline_ok))
        if bars and len(bars) >= need:
            stock_bars[str(sym)] = bars
            sym_by_raw[str(raw)] = str(sym)
            sym_by_raw[str(sym)] = str(sym)
        elif bars:
            failures.append(f"{raw}(日线{len(bars)}<{need})")
        else:
            failures.append(str(raw))

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
