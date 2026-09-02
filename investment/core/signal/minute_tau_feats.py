"""分钟 τ 小包特征：开盘→τ 路径摘要（PIT，仅用 ≤τ 的 5m）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 单票可算（无截面）
MINUTE_TAU_PACK_KEYS = (
    "ret_open_to_tau",
    "ret_prev_to_tau",
    "range_pct",
    "loc_hl",
    "up_extent",
    "down_extent",
    "path_sign",
    "pullback_from_high",
    "bounce_from_low",
    "ret_last_15m",
    "realized_vol",
    "vol_last3_vs_avg",
    "tau_elapsed_min",
)

# 截面附着后可写
MINUTE_TAU_CS_KEYS = (
    "sector_ret_to_tau",
    "ret_vs_sector",
)

MINUTE_TAU_ALL_KEYS = MINUTE_TAU_PACK_KEYS + MINUTE_TAU_CS_KEYS

MINUTE_TAU_FEAT_LABELS = {
    "ret_open_to_tau": "开盘→τ 收益 %",
    "ret_prev_to_tau": "昨收→τ 收益 %",
    "range_pct": "前缀振幅 %",
    "loc_hl": "HL 位置 0–1",
    "up_extent": "相对开盘上探 %",
    "down_extent": "相对开盘下探 %",
    "path_sign": "路径符号(+先低后高)",
    "pullback_from_high": "自高回撤 %",
    "bounce_from_low": "自低反弹 %",
    "ret_last_15m": "近15m 收益 %",
    "realized_vol": "前缀已实现波动 %",
    "vol_last3_vs_avg": "近3根量/均量",
    "tau_elapsed_min": "τ距开盘分钟",
    "sector_ret_to_tau": "板块中位开→τ %",
    "ret_vs_sector": "开→τ 相对板块 %",
}


def _bar_hm(b: dict) -> str:
    t = str(b.get("time") or b.get("datetime") or b.get("date") or "")
    for part in t.replace("T", " ").split(" "):
        if ":" in part:
            return part[:5].replace(":", "")
    return ""


def _bar_date(b: dict) -> str:
    d = str(b.get("date") or "")[:10]
    if len(d) == 10:
        return d
    t = str(b.get("datetime") or "")
    return t[:10] if len(t) >= 10 else ""


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _day_bars_upto_tau(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
) -> List[dict]:
    day = str(trade_date or "")[:10]
    target = str(tau_hm or "10:30").replace(":", "")
    if not day or not target:
        return []
    out: List[dict] = []
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        if _bar_date(b) != day and day not in str(b.get("datetime") or ""):
            continue
        hm = _bar_hm(b)
        if not hm or hm > target:
            continue
        c = _f(b.get("close") if b.get("close") is not None else b.get("price"))
        if c is None or c <= 0:
            continue
        out.append(b)
    out.sort(key=lambda x: _bar_hm(x))
    return out


def extract_minute_tau_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
) -> Dict[str, float]:
    """从当日 ≤τ 分钟线提取小包特征；不足 2 根返回空 dict。"""
    bars = _day_bars_upto_tau(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm
    )
    if len(bars) < 2:
        return {}

    o = _f(open_px)
    if o is None or o <= 0:
        o = _f(bars[0].get("open")) or _f(bars[0].get("close"))
    if o is None or o <= 0:
        return {}

    highs: List[Tuple[str, float]] = []
    lows: List[Tuple[str, float]] = []
    closes: List[float] = []
    vols: List[float] = []
    for b in bars:
        hm = _bar_hm(b)
        h = _f(b.get("high"))
        l = _f(b.get("low"))
        c = _f(b.get("close") if b.get("close") is not None else b.get("price"))
        v = _f(b.get("volume")) or 0.0
        if c is None or c <= 0:
            continue
        if h is None or h <= 0:
            h = c
        if l is None or l <= 0:
            l = c
        highs.append((hm, float(h)))
        lows.append((hm, float(l)))
        closes.append(float(c))
        vols.append(max(0.0, float(v)))

    if len(closes) < 2:
        return {}

    px = closes[-1]
    hi = max(v for _, v in highs)
    lo = min(v for _, v in lows)
    # 极值首次出现时刻（并列取更早）
    t_hi = min((hm for hm, v in highs if v >= hi - 1e-12), default="")
    t_lo = min((hm for hm, v in lows if v <= lo + 1e-12), default="")

    out: Dict[str, float] = {}
    out["ret_open_to_tau"] = round((px / o - 1.0) * 100.0, 6)
    pc = _f(prev_close)
    if pc is not None and pc > 0:
        out["ret_prev_to_tau"] = round((px / pc - 1.0) * 100.0, 6)

    rng = hi - lo
    out["range_pct"] = round((rng / o) * 100.0, 6)
    out["up_extent"] = round(((hi - o) / o) * 100.0, 6)
    out["down_extent"] = round(((o - lo) / o) * 100.0, 6)
    if rng > 1e-12:
        out["loc_hl"] = round((px - lo) / rng, 6)
    else:
        out["loc_hl"] = 0.5

    if t_lo and t_hi:
        out["path_sign"] = 1.0 if t_lo < t_hi else -1.0
    if hi > 1e-12:
        out["pullback_from_high"] = round(((hi - px) / hi) * 100.0, 6)
    if lo > 1e-12:
        out["bounce_from_low"] = round(((px - lo) / lo) * 100.0, 6)

    # 近 15m ≈ 3 根 5m
    n15 = min(3, len(closes))
    if n15 >= 2 and closes[-n15] > 0:
        out["ret_last_15m"] = round(
            (closes[-1] / closes[-n15] - 1.0) * 100.0, 6
        )

    rets: List[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] / closes[i - 1] - 1.0) * 100.0)
    if len(rets) >= 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        out["realized_vol"] = round(math.sqrt(max(0.0, var)), 6)

    if len(vols) >= 3:
        last3 = sum(vols[-3:]) / 3.0
        avg = sum(vols) / len(vols)
        if avg > 1e-12:
            out["vol_last3_vs_avg"] = round(last3 / avg, 6)

    return out


def attach_ret_vs_sector(feats: Dict[str, Any]) -> None:
    """有 ret_open_to_tau 与 sector_ret_to_tau 时写 ret_vs_sector。"""
    if not isinstance(feats, dict):
        return
    a = _f(feats.get("ret_open_to_tau"))
    b = _f(feats.get("sector_ret_to_tau"))
    if a is None or b is None:
        return
    feats["ret_vs_sector"] = round(float(a) - float(b), 6)


def sector_ret_median(peer_rets: Sequence[Any]) -> Optional[float]:
    """同伴 ``ret_open_to_tau`` 中位数（与 ``tau_panel.attach_cross_section_breadth`` 同口径）。"""
    vals: List[float] = []
    for v in peer_rets or []:
        x = _f(v)
        if x is not None:
            vals.append(float(x))
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    mid = n // 2
    if n % 2:
        return round(vals[mid], 6)
    return round(0.5 * (vals[mid - 1] + vals[mid]), 6)


def apply_sector_ret_cs(
    feats: Optional[Dict[str, Any]],
    sector_ret_to_tau: Optional[float],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """写入截面开→τ 中位，并派生 ``ret_vs_sector``。

    默认已有非空 ``sector_ret_to_tau`` 不覆盖；``overwrite=True`` 时重写并重算
    ``ret_vs_sector``（画像/前缀因果打分对齐训练截面）。
    """
    out = dict(feats or {})
    sret = _f(sector_ret_to_tau)
    if sret is not None and (overwrite or out.get("sector_ret_to_tau") is None):
        out["sector_ret_to_tau"] = round(float(sret), 6)
    if overwrite:
        out.pop("ret_vs_sector", None)
    attach_ret_vs_sector(out)
    return out


# (trade_date, tau_hm) → 池中位；做 T 回测同日多票复用
_SECTOR_RET_CACHE: Dict[Tuple[str, str], Optional[float]] = {}
# 同伴分钟线进程缓存：回测 20 日×4 槽会反复 load_minute_cache，未缓存时单票可卡 ~1 分钟
_PEER_MINUTE_BARS: Dict[str, List[dict]] = {}
_PEER_CODES_MEMO: Optional[List[str]] = None
_SECTOR_RET_DEFAULT_CAP = 24


def clear_sector_ret_cache() -> None:
    global _PEER_CODES_MEMO
    _SECTOR_RET_CACHE.clear()
    _PEER_MINUTE_BARS.clear()
    _PEER_CODES_MEMO = None


def resolve_sector_ret_to_tau(
    trade_date: str,
    tau_hm: str = "10:30",
    *,
    codes: Optional[Sequence[str]] = None,
    known_rets: Optional[Sequence[Any]] = None,
    cap: int = _SECTOR_RET_DEFAULT_CAP,
    use_cache: bool = True,
) -> Optional[float]:
    """解析 ``sector_ret_to_tau``：优先已知同伴收益，否则读活跃簿/观察池/分钟仓宇宙。

    与训练 ``attach_cross_section_breadth`` 一致：对 (date, τ) 下多票 ``ret_open_to_tau`` 取中位。
    """
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return None

    if known_rets is not None:
        return sector_ret_median(known_rets)

    cache_key = (day, hm)
    if use_cache and cache_key in _SECTOR_RET_CACHE:
        return _SECTOR_RET_CACHE[cache_key]

    peer_codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not peer_codes:
        peer_codes = _peer_codes_for_sector_ret(cap=max(8, int(cap or _SECTOR_RET_DEFAULT_CAP)))
    if not peer_codes:
        if use_cache:
            _SECTOR_RET_CACHE[cache_key] = None
        return None

    rets: List[float] = []
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_minute_cache
    except Exception:
        if use_cache:
            _SECTOR_RET_CACHE[cache_key] = None
        return None

    for raw in peer_codes[: max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))]:
        code_key = str(raw or "").strip()
        if not code_key:
            continue
        bars = _PEER_MINUTE_BARS.get(code_key)
        if bars is None:
            try:
                mkt, pure = resolve_market_code(code_key)
                packed = load_minute_cache(
                    mkt or "CN",
                    pure or code_key,
                    period="5",
                    min_bars=2,
                    ignore_age=True,
                )
            except Exception:
                _PEER_MINUTE_BARS[code_key] = []
                continue
            if not packed:
                _PEER_MINUTE_BARS[code_key] = []
                continue
            bars = list(packed[0] or [])
            _PEER_MINUTE_BARS[code_key] = bars
        if len(bars) < 2:
            continue
        pack = extract_minute_tau_pack(
            bars,
            trade_date=day,
            tau_hm=hm,
        )
        rot = _f(pack.get("ret_open_to_tau"))
        if rot is not None:
            rets.append(float(rot))

    med = sector_ret_median(rets)
    if use_cache:
        _SECTOR_RET_CACHE[cache_key] = med
    return med


def _peer_codes_for_sector_ret(*, cap: int = _SECTOR_RET_DEFAULT_CAP) -> List[str]:
    """同伴宇宙：活跃簿 → 观察池 → 本地分钟仓代码。"""
    global _PEER_CODES_MEMO
    n = max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))
    if _PEER_CODES_MEMO is not None:
        return list(_PEER_CODES_MEMO[:n])
    try:
        from core.t0.score_policy import active_book_codes_for_tau_pool

        codes = active_book_codes_for_tau_pool(cap=n)
        if codes:
            _PEER_CODES_MEMO = list(codes)
            return list(_PEER_CODES_MEMO[:n])
    except Exception:
        pass
    try:
        from core.paths import WATCHING_PATH
        import json
        import os

        if os.path.isfile(WATCHING_PATH):
            with open(WATCHING_PATH, encoding="utf-8") as f:
                doc = json.load(f) or {}
            wl = doc.get("watchlist") if isinstance(doc, dict) else None
            out = []
            seen = set()
            if isinstance(wl, list):
                for x in wl:
                    c = str(x or "").strip()
                    if isinstance(x, dict):
                        c = str(x.get("stock_code") or x.get("code") or "").strip()
                    if not c or c in seen:
                        continue
                    seen.add(c)
                    out.append(c)
                    if len(out) >= n:
                        _PEER_CODES_MEMO = out
                        return list(out)
            if out:
                _PEER_CODES_MEMO = out
                return list(out)
    except Exception:
        pass
    try:
        from core.store import get_store_dir
        import sqlite3
        import os

        db = os.path.join(get_store_dir(), "bars.db")
        if not os.path.isfile(db):
            return []
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT code FROM minute_cache_meta ORDER BY code LIMIT ?",
                (n,),
            ).fetchall()
        finally:
            conn.close()
        out = [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]
        _PEER_CODES_MEMO = out
        return list(out)
    except Exception:
        return []

def merge_minute_tau_pack_into_feats(
    feats: Optional[Dict[str, Any]],
    *,
    code: str = "",
    trade_date: str,
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
    tau_hm: str = "10:30",
    minute_bars: Optional[Sequence[dict]] = None,
    load_cache_if_missing: bool = True,
    max_age_hours: float = 36.0,
) -> Tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
    """把 ≤τ 分钟小包并入 feats（已有非空键不覆盖）。

    返回 ``(feats, as_of_tau_override, y_spec_override)``。
    ``minute_bars`` 优先；缺则可选读本地 5m 缓存（不拉网）。
    """
    out = dict(feats or {})
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return out, None, None
    if out.get("ret_open_to_tau") is not None:
        return out, None, None

    bars = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if not bars and load_cache_if_missing and code:
        try:
            from core.ports.market import resolve_market_code
            from core.store import load_minute_cache

            mkt, pure = resolve_market_code(str(code))
            packed = load_minute_cache(
                mkt or "CN",
                pure or str(code),
                period="5",
                min_bars=1,
                max_age_hours=float(max_age_hours),
            )
            if packed:
                bars = list(packed[0] or [])
        except Exception:
            bars = []

    if len(bars) < 2:
        return out, None, None

    pack = extract_minute_tau_pack(
        bars,
        trade_date=day,
        tau_hm=hm,
        open_px=open_px,
        prev_close=prev_close,
    )
    if pack.get("ret_open_to_tau") is None:
        return out, None, None

    for k, v in pack.items():
        if v is None or v == "":
            continue
        if out.get(k) is None:
            out[k] = v
    attach_ret_vs_sector(out)
    as_of = f"{day}T{hm}:00+08:00"
    y_spec = {
        "tau": hm,
        "formula": f"close[T]/price[{hm}]-1",
        "note": "分钟小包；无网拉；模型缺特征时 z≈0",
        "minute_pack": sorted(pack.keys()),
    }
    return out, as_of, y_spec

