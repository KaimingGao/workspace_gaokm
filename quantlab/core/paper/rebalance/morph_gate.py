"""开盘形态硬闸（不进 ŷ）：高波动 + 弱开盘 → 禁止新开/加仓。

默认关闭。9:25 可用：昨收、近 20 日收益波动、今开（集合竞价）。
截面波动分位在当日候选池上算；缺分不拦（fail-open）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_EPS = 1e-12
_MIN_CS_N = 8


def morph_gate_enabled(cfg: Optional[Mapping[str, Any]]) -> bool:
    from core.t0.config import coerce_cfg_bool

    d = cfg if isinstance(cfg, Mapping) else {}
    return bool(coerce_cfg_bool(d.get("morph_gate_enabled"), False))


def morph_vol_pctl_from_cfg(cfg: Optional[Mapping[str, Any]], default: float = 0.9) -> float:
    d = cfg if isinstance(cfg, Mapping) else {}
    try:
        v = float(d.get("morph_vol_pctl", default))
    except (TypeError, ValueError):
        v = float(default)
    if v != v or v in (float("inf"), float("-inf")):
        v = float(default)
    return max(0.5, min(v, 0.99))


def morph_a1_max_from_cfg(cfg: Optional[Mapping[str, Any]], default: float = 0.0) -> float:
    """今开相对昨收（小数）。默认 0：须 a1 < 0（低开）才与高波动合取。"""
    d = cfg if isinstance(cfg, Mapping) else {}
    try:
        v = float(d.get("morph_a1_max", default))
    except (TypeError, ValueError):
        v = float(default)
    if v != v or v in (float("inf"), float("-inf")):
        v = float(default)
    return max(-0.2, min(v, 0.05))


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def vol20_ann_from_closes(closes: Sequence[float]) -> Optional[float]:
    """近最多 21 个收盘 → 约 20 段日收益的年化波动。"""
    xs: List[float] = []
    try:
        for c in closes or ():
            x = _f(c)
            if x is not None and x > 0:
                xs.append(x)
    except TypeError:
        return None
    if len(xs) < 11:
        return None
    seg = xs[-21:]
    rets: List[float] = []
    for i in range(1, len(seg)):
        prev = seg[i - 1]
        if prev <= _EPS:
            continue
        rets.append(seg[i] / prev - 1.0)
    if len(rets) < 10:
        return None
    try:
        import numpy as np

        return float(np.std(np.asarray(rets, dtype=float), ddof=1) * math.sqrt(252.0))
    except Exception:  # noqa: BLE001
        logger.debug("vol20_ann_from_closes numpy failed", exc_info=True)
        return None


def a1_from_open_prev_close(open_t: Optional[float], prev_close: Optional[float]) -> Optional[float]:
    o = _f(open_t)
    c = _f(prev_close)
    if o is None or c is None or c <= _EPS:
        return None
    if o <= 0:
        return None
    return o / c - 1.0


def _norm_day(raw: Any) -> str:
    """统一成 YYYY-MM-DD（也认 YYYYMMDD）；比不过则空串。"""
    s = str(raw or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return s[:10] if len(s) >= 10 else s


def _bar_date(b: Mapping[str, Any]) -> str:
    return _norm_day(b.get("date") or b.get("trade_date"))


def metrics_from_bars(
    bars: Sequence[Mapping[str, Any]],
    *,
    open_t: Optional[float] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """bars 按日升序；open_t 为决策日开盘。

    若提供 as_of，只用 date < as_of 的收盘作昨收/波动，避免误吃当日 K。
    缺 open_t 时 a1=None（fail-open），不回退昨 K 开盘。
    """
    if not bars:
        return {"a1": None, "vol20": None, "prev_close": None}
    day = _norm_day(as_of)
    closes: List[float] = []
    for b in bars:
        if not isinstance(b, Mapping):
            continue
        if len(day) >= 10:
            bd = _bar_date(b)
            if bd and bd >= day:
                continue
        c = _f(b.get("close"))
        if c is not None and c > 0:
            closes.append(c)
    if len(closes) < 2:
        return {"a1": None, "vol20": None, "prev_close": None}
    prev = closes[-1]
    return {
        "a1": a1_from_open_prev_close(_f(open_t), prev),
        "vol20": vol20_ann_from_closes(closes),
        "prev_close": prev,
    }


def _clear_morph_vol_p(items: Sequence[dict]) -> None:
    for it in items:
        if isinstance(it, dict):
            it.pop("morph_vol_p", None)


def stamp_morph_cs_on_items(
    items: Sequence[dict],
    *,
    metrics_by_code: Mapping[str, Mapping[str, Optional[float]]],
) -> None:
    """就地写入 morph_a1 / morph_vol20 / morph_vol_p（池内分位 0..1）。

    池有效 vol 样本 < 8 时清掉 morph_vol_p（fail-open，避免旧分位残留）。
    同 code 多行只取一次 vol 参与截面，再广播回所有行。
    """
    if not isinstance(metrics_by_code, Mapping):
        _clear_morph_vol_p(items)
        return
    codes: List[str] = []
    vols: List[float] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        m = metrics_by_code.get(code) if code else None
        if not isinstance(m, Mapping):
            m = {}
        a1 = _f(m.get("a1"))
        vol = _f(m.get("vol20"))
        if a1 is not None:
            it["morph_a1"] = round(a1, 6)
        else:
            it.pop("morph_a1", None)
        if vol is not None:
            it["morph_vol20"] = round(vol, 6)
        else:
            it.pop("morph_vol20", None)
        if code and vol is not None and code not in seen:
            seen.add(code)
            codes.append(code)
            vols.append(vol)
    if len(vols) < _MIN_CS_N:
        _clear_morph_vol_p(items)
        return
    try:
        import numpy as np

        arr = np.asarray(vols, dtype=float)
        if not np.isfinite(arr).all():
            _clear_morph_vol_p(items)
            return
        order = arr.argsort().argsort().astype(float)
        pctl = order / max(len(arr) - 1, 1)
        by_p = {c: float(p) for c, p in zip(codes, pctl)}
    except Exception:  # noqa: BLE001
        logger.debug("stamp_morph_cs_on_items percentile failed", exc_info=True)
        _clear_morph_vol_p(items)
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if code in by_p:
            it["morph_vol_p"] = round(by_p[code], 6)
        else:
            it.pop("morph_vol_p", None)


def morph_gate_skip_reason(
    item: Optional[dict],
    cfg: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """过闸返回中文原因；未启用/缺分/未触发/异常返回 None（fail-open）。"""
    try:
        if not morph_gate_enabled(cfg):
            return None
        if not isinstance(item, dict):
            return None
        a1 = _f(item.get("morph_a1"))
        vol_p = _f(item.get("morph_vol_p"))
        if a1 is None or vol_p is None:
            return None
        if not (0.0 <= vol_p <= 1.0 + 1e-9):
            return None
        vp_thr = morph_vol_pctl_from_cfg(cfg)
        a1_max = morph_a1_max_from_cfg(cfg)
        if vol_p >= vp_thr and a1 < a1_max:
            return (
                f"形态闸·高波动弱开盘(vol_p={vol_p:.2f}≥{vp_thr:g}, "
                f"a1={a1:.2%} < {a1_max:.2%})"
            )
        return None
    except Exception:  # noqa: BLE001
        logger.debug("morph_gate_skip_reason failed", exc_info=True)
        return None


def _row_prev_close(it: Mapping[str, Any]) -> Optional[float]:
    prev = _f(it.get("prev_close")) or _f(it.get("last_close"))
    if prev is not None:
        return prev
    factors = it.get("factors")
    if isinstance(factors, Mapping):
        return _f(factors.get("last_close")) or _f(factors.get("prev_close"))
    return None


def try_stamp_morph_from_opens(
    items: Sequence[dict],
    *,
    opens: Optional[Mapping[str, float]] = None,
    bars_by_code: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    as_of: Optional[str] = None,
) -> Tuple[int, int]:
    """用今开 + 日线戳 morph 字段。返回 (stamped_a1, stamped_vol_p)。"""
    opens_d = opens if isinstance(opens, Mapping) else {}
    bars_d = bars_by_code if isinstance(bars_by_code, Mapping) else {}
    day = str(as_of or "")[:10]
    metrics: Dict[str, Dict[str, Optional[float]]] = {}
    try:
        for it in items:
            if not isinstance(it, dict):
                continue
            code = str(it.get("stock_code") or "").strip()
            if not code:
                continue
            open_t = _f(opens_d.get(code))
            if open_t is None:
                open_t = _f(it.get("day_open")) or _f(it.get("open"))
            raw_bars = bars_d.get(code) or []
            bars = [b for b in raw_bars if isinstance(b, Mapping)]
            if bars:
                metrics[code] = metrics_from_bars(bars, open_t=open_t, as_of=day or None)
                continue
            prev = _row_prev_close(it)
            a1 = a1_from_open_prev_close(open_t, prev)
            vol = _f(it.get("morph_vol20"))
            metrics[code] = {"a1": a1, "vol20": vol, "prev_close": prev}
        stamp_morph_cs_on_items(items, metrics_by_code=metrics)
    except Exception:  # noqa: BLE001
        logger.debug("try_stamp_morph_from_opens failed", exc_info=True)
        _clear_morph_vol_p(items)
        return 0, 0
    n_a1 = sum(1 for it in items if isinstance(it, dict) and _f(it.get("morph_a1")) is not None)
    n_vp = sum(1 for it in items if isinstance(it, dict) and _f(it.get("morph_vol_p")) is not None)
    return n_a1, n_vp


__all__ = [
    "a1_from_open_prev_close",
    "metrics_from_bars",
    "morph_a1_max_from_cfg",
    "morph_gate_enabled",
    "morph_gate_skip_reason",
    "morph_vol_pctl_from_cfg",
    "stamp_morph_cs_on_items",
    "try_stamp_morph_from_opens",
    "vol20_ann_from_closes",
]
