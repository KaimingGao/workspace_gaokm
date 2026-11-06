"""可交易严筛：研究池入池主闸。"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.universe.policy import (
    load_tradable_policy,
    watching_subset_enforced,
)
from core.universe.st_name import is_st_name

logger = logging.getLogger(__name__)

# 再导出，便于 ``from core.universe.tradable_screen import load_tradable_policy``
__all__ = [
    "load_tradable_policy",
    "watching_subset_enforced",
    "screen_codes",
    "rebuild_tradable_research_universe",
    "normalize_stock_code",
    "is_index_code",
]


def normalize_stock_code(raw: Any) -> str:
    c = str(raw or "").strip().split(".")[0]
    digits = "".join(ch for ch in c if ch.isdigit())
    if len(digits) == 6:
        return digits
    return c


def is_index_code(code: str) -> bool:
    c = normalize_stock_code(code)
    return c.startswith("399") or c.startswith("000000") or c == "000000"


def _spot_field(row: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row.get(k) is not None:
            return row.get(k)
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            if value != value:
                return None
        except Exception:
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "None", "nan", "NaN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_spot_index(*, max_age_hours: float = 168.0) -> Tuple[Dict[str, Dict[str, Any]], Optional[float]]:
    """代码 → spot 行；展示/筛选用，默认容忍 7 天陈旧。"""
    try:
        from adapters.screen.engine import load_disk_spot
    except Exception:
        logger.debug("tradable_screen: import load_disk_spot failed", exc_info=True)
        return {}, None

    rows = load_disk_spot(max_age_hours=float(max_age_hours)) or []
    fetched_at: Optional[float] = None
    try:
        from core.paths import STORE_DIR
        import json

        path = os.path.join(STORE_DIR, "spot_a_em.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            fetched_at = float(payload.get("fetched_at") or 0) or None
            if not rows:
                raw = payload.get("rows")
                if isinstance(raw, list):
                    rows = raw
    except Exception:
        logger.debug("tradable_screen: spot meta read failed", exc_info=True)

    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = normalize_stock_code(_spot_field(row, "代码", "股票代码", "code"))
        if not code:
            continue
        out[code] = row
    return out, fetched_at


def _name_from_maps(code: str, spot_row: Optional[dict], name_by_code: Optional[Dict[str, str]]) -> str:
    if name_by_code and name_by_code.get(code):
        return str(name_by_code.get(code) or "").strip()
    if spot_row:
        nm = _spot_field(spot_row, "名称", "股票名称", "name")
        if nm:
            return str(nm).strip()
    try:
        from core.t0.intraday import _load_a_code_name_map

        m = _load_a_code_name_map(ignore_ttl=True) or {}
        return str(m.get(code) or "").strip()
    except Exception:
        logger.debug("tradable_screen: code name map failed", exc_info=True)
        return ""


def _bars_avg_amount(code: str, *, lookback: int, market: str = "CN") -> Optional[float]:
    """从日 K JSON/SQLite 取近窗均成交额；失败返回 None（回退 spot）。"""
    try:
        from core.bar_fields import bar_amount
        from core.store import bars_backend, daily_cache_path, get_store_dir
    except Exception:
        return None

    bars: Optional[List[dict]] = None
    try:
        if bars_backend() == "sqlite":
            from core import store_bars_sqlite as sq

            packed = sq.load_daily(market, code, get_store_dir()) or {}
            raw = packed.get("bars") if isinstance(packed, dict) else None
            if isinstance(raw, list):
                bars = raw
        else:
            path = daily_cache_path(market, code, get_store_dir())
            if path and os.path.isfile(path):
                import json

                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                raw = payload.get("bars") if isinstance(payload, dict) else None
                if isinstance(raw, list):
                    bars = raw
    except Exception:
        logger.debug("tradable_screen: bars amount failed code=%s", code, exc_info=True)
        return None

    if not bars:
        return None
    amts: List[float] = []
    for b in bars[-max(1, int(lookback or 20)) :]:
        if not isinstance(b, dict):
            continue
        amt = bar_amount(b)
        if amt and amt > 0:
            amts.append(float(amt))
    if not amts:
        return None
    return sum(amts) / len(amts)


def _bars_count(code: str, *, market: str = "CN") -> Optional[int]:
    try:
        from core.store import peek_daily_cache_meta

        meta = peek_daily_cache_meta(market, code) or {}
        n = meta.get("bar_count")
        if n is not None:
            return int(n)
    except Exception:
        logger.debug("tradable_screen: bar_count failed code=%s", code, exc_info=True)
    return None


def _suspended_name(name: str) -> bool:
    n = str(name or "")
    return "停牌" in n


def screen_codes(
    codes: Sequence[str],
    *,
    policy: Optional[dict] = None,
    spot_by_code: Optional[Dict[str, Dict[str, Any]]] = None,
    name_by_code: Optional[Dict[str, str]] = None,
    use_bars_amount: Optional[bool] = None,
) -> Dict[str, Any]:
    """对候选代码做可交易严筛。

    返回 ``{kept, rejected, stats, policy_version, policy}``。
    ``rejected`` 项：``{code, reason}``。
    """
    pol = dict(policy or load_tradable_policy())
    if spot_by_code is None:
        spot_by_code, _fetched = _load_spot_index()
    else:
        _fetched = None

    prefer_bars = bool(pol.get("prefer_bars_avg_amount", True))
    if use_bars_amount is not None:
        prefer_bars = bool(use_bars_amount)

    lookback = int(pol.get("amount_lookback_days") or 20)
    min_float = _to_float(pol.get("min_float_mv"))
    min_amt = _to_float(pol.get("min_amount"))
    min_bars = int(pol.get("min_listing_bars") or 0)
    min_px = _to_float(pol.get("min_price"))
    max_px = _to_float(pol.get("max_price"))

    kept: List[str] = []
    rejected: List[Dict[str, str]] = []
    reason_counts: Counter = Counter()
    seen = set()

    for raw in codes or []:
        code = normalize_stock_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)

        def _drop(reason: str) -> None:
            rejected.append({"code": code, "reason": reason})
            reason_counts[reason.split("（")[0].split(":")[0].strip()] += 1

        if pol.get("exclude_index", True) and is_index_code(code):
            _drop("指数代码")
            continue

        row = (spot_by_code or {}).get(code)
        if pol.get("require_spot_row", True) and not row:
            _drop("无现货行")
            continue

        name = _name_from_maps(code, row, name_by_code)
        if pol.get("require_name", True) and not name:
            _drop("缺名称")
            continue

        if pol.get("exclude_st", True) and is_st_name(name):
            _drop(f"ST/退市（{name}）")
            continue

        if pol.get("drop_suspended_keywords", True) and _suspended_name(name):
            _drop(f"停牌名（{name}）")
            continue

        if row is not None:
            float_mv = _to_float(
                _spot_field(row, "流通市值", "总市值", "market_cap", "float_mv")
            )
            if min_float is not None and min_float > 0:
                if float_mv is None:
                    _drop("缺流通市值")
                    continue
                if float_mv < min_float:
                    _drop(f"流通市值过低（{float_mv:.0f}）")
                    continue

            price = _to_float(_spot_field(row, "最新价", "price"))
            if min_px is not None and price is not None and price < min_px:
                _drop(f"价格过低（{price}）")
                continue
            if max_px is not None and price is not None and price > max_px:
                _drop(f"价格过高（{price}）")
                continue

        amount: Optional[float] = None
        if prefer_bars:
            amount = _bars_avg_amount(code, lookback=lookback)
        if amount is None and row is not None:
            amount = _to_float(_spot_field(row, "成交额", "amount"))
        if min_amt is not None and min_amt > 0:
            if amount is None:
                _drop("缺成交额")
                continue
            if amount < min_amt:
                _drop(f"成交额过低（{amount:.0f}）")
                continue

        if min_bars > 0:
            n_bars = _bars_count(code)
            # 无日 K meta 时不因次新规则误杀（仅有现货的候选仍可进）；
            # 若明确有 bar_count 且不足则剔。
            if n_bars is not None and n_bars < min_bars:
                _drop(f"日K过短（{n_bars}<{min_bars}）")
                continue

        kept.append(code)

    stats = {
        "input": len(seen),
        "kept": len(kept),
        "rejected": len(rejected),
        "by_reason": dict(reason_counts),
        "spot_fetched_at": _fetched,
        "policy_version": str(pol.get("version") or ""),
    }
    return {
        "ok": True,
        "kept": kept,
        "rejected": rejected,
        "stats": stats,
        "policy_version": str(pol.get("version") or ""),
        "policy": pol,
    }


def _candidate_codes_from_cached_daily(*, market: str = "CN") -> List[str]:
    try:
        from core.store import list_cached_symbols

        rows = list_cached_symbols(market=market) or []
    except Exception:
        logger.debug("tradable_screen: list_cached_symbols failed", exc_info=True)
        return []
    out: List[str] = []
    seen = set()
    for row in rows:
        if isinstance(row, dict):
            c = normalize_stock_code(row.get("code") or row.get("stock_code"))
        else:
            c = normalize_stock_code(row)
        if not c or c in seen or is_index_code(c):
            continue
        seen.add(c)
        out.append(c)
    out.sort()
    return out


def _candidate_codes_from_spot(spot_by_code: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(spot_by_code.keys())


def rebuild_tradable_research_universe(
    *,
    path: Optional[str] = None,
    market: str = "CN",
    max_codes: Optional[int] = None,
    include_spot_universe: bool = True,
    policy: Optional[dict] = None,
) -> Dict[str, Any]:
    """候选 = 日 K 仓 ∪（可选）现货全表 → 严筛 → 写入研究宇宙。"""
    from core.research_universe import RESEARCH_UNIVERSE_MAX_SIZE, save_research_universe

    pol = dict(policy or load_tradable_policy())
    spot_by_code, spot_fetched = _load_spot_index()
    cached = _candidate_codes_from_cached_daily(market=market)
    candidates: List[str] = list(cached)
    seen = set(cached)
    if include_spot_universe:
        for c in _candidate_codes_from_spot(spot_by_code):
            if c not in seen:
                seen.add(c)
                candidates.append(c)

    screened = screen_codes(candidates, policy=pol, spot_by_code=spot_by_code)
    kept_set = set(screened.get("kept") or [])

    def _rank_key(code: str) -> Tuple[float, float]:
        row = spot_by_code.get(code) or {}
        mv = _to_float(_spot_field(row, "流通市值", "总市值", "market_cap")) or 0.0
        amt = _to_float(_spot_field(row, "成交额", "amount")) or 0.0
        return (mv, amt)

    cap = RESEARCH_UNIVERSE_MAX_SIZE
    if max_codes is not None:
        try:
            cap = max(8, min(int(max_codes), RESEARCH_UNIVERSE_MAX_SIZE))
        except (TypeError, ValueError):
            cap = RESEARCH_UNIVERSE_MAX_SIZE

    # 优先保留「当前观察池 ∩ 已过筛」，避免硬顶 2000 按市值截断时把 watching 大批踢出
    prefer: List[str] = []
    try:
        from core.watching.store import read_watching

        for c in list((read_watching() or {}).get("watchlist") or []):
            code = normalize_stock_code(c)
            if code and code in kept_set and code not in prefer:
                prefer.append(code)
    except Exception:
        logger.debug("tradable rebuild: prefer watching failed", exc_info=True)

    rest = sorted((kept_set - set(prefer)), key=_rank_key, reverse=True)
    kept = (prefer + rest)[:cap]

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    stats = dict(screened.get("stats") or {})
    note = (
        f"可交易严筛宇宙 policy={pol.get('version')}；"
        f"候选 {stats.get('input')} → 保留 {len(kept)}；"
        f"筛于 {now}；分钟暖仓/live 仍只读 watching⊆研究池。"
    )
    saved = save_research_universe(
        {
            "version": 2,
            "codes": kept,
            "note": note,
            "policy_version": str(pol.get("version") or ""),
            "screened_at": now,
            "screen_stats": stats,
            "spot_fetched_at": spot_fetched,
        },
        path=path,
        apply_screen=False,  # 已筛过
    )
    saved["source"] = "tradable_rebuild"
    saved["screen"] = {
        "stats": stats,
        "rejected_sample": list(screened.get("rejected") or [])[:40],
        "rejected_count": len(screened.get("rejected") or []),
        "policy_version": screened.get("policy_version"),
        "candidate_cached": len(cached),
        "candidate_total": len(candidates),
    }
    return saved
