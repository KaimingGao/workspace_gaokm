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

