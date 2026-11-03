"""开盘形态硬闸（不进 ŷ）：禁止新开/加仓（已持仓不清仓）。

默认总开关关。门槛写死（不暴露 UI），三腿 OR：
  1) 异常低开：a1 ≤ −8%
  2) 涨停开盘：今开相对昨收已到该板块涨停阈值（主板 9.5% / 创业科创 19.5% / ST 4.5%）
  3) 高波弱开：候选池 vol20 分位 ≥ 0.9 且 a1 < 0（低开）
9:25 可用：昨收、近 20 日收益波动、今开、代码。缺分 fail-open。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_EPS = 1e-12
_MIN_CS_N = 8
# |a1| 超此视为脏数：三腿都不拦（fail-open），避免坏昨收/开盘误杀或误放
_A1_ABS_MAX = 0.5

# 产品写死门槛（cfg / UI 不再调）
MORPH_VOL_PCTL = 0.9
MORPH_A1_MAX = 0.0  # 高波腿：须 a1 < 0
MORPH_GAP_MAX = 0.08  # 异常低开：a1 ≤ −8%
# 跳过原因固定前缀。盯盘认行用这个，不靠句子中部碰巧出现「形态闸」。
MORPH_GATE_REASON_PREFIX = "形态闸"


def morph_gate_enabled(cfg: Optional[Mapping[str, Any]]) -> bool:
    from core.t0.config import coerce_cfg_bool

    try:
        d = cfg if isinstance(cfg, Mapping) else {}
        return bool(coerce_cfg_bool(d.get("morph_gate_enabled"), False))
    except Exception:  # noqa: BLE001
        logger.debug("morph_gate_enabled coerce failed", exc_info=True)
        return False


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


def _std_sample(xs: Sequence[float]) -> Optional[float]:
    """样本标准差（ddof=1）；失败返回 None。"""
    n = len(xs)
    if n < 2:
        return None
    try:
        import numpy as np

        arr = np.asarray(xs, dtype=float)
        if arr.size < 2 or not np.isfinite(arr).all():
            return None
        return float(np.std(arr, ddof=1))
    except Exception:  # noqa: BLE001
        logger.debug("_std_sample numpy failed; pure-python fallback", exc_info=True)
    try:
        mean = sum(xs) / float(n)
        var = sum((x - mean) ** 2 for x in xs) / float(n - 1)
        if var < 0 or var != var or var in (float("inf"), float("-inf")):
            return None
        return math.sqrt(var)
    except Exception:  # noqa: BLE001
        return None


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
        r = seg[i] / prev - 1.0
        if r != r or r in (float("inf"), float("-inf")):
            continue
        rets.append(r)
    if len(rets) < 10:
        return None
    sd = _std_sample(rets)
    if sd is None:
        return None
    try:
        out = float(sd * math.sqrt(252.0))
    except Exception:  # noqa: BLE001
        return None
    if out != out or out in (float("inf"), float("-inf")) or out < 0:
        return None
    return out


def a1_from_open_prev_close(open_t: Optional[float], prev_close: Optional[float]) -> Optional[float]:
    o = _f(open_t)
    c = _f(prev_close)
    if o is None or c is None or c <= _EPS:
        return None
    if o <= 0:
        return None
    try:
        a1 = o / c - 1.0
    except Exception:  # noqa: BLE001
        return None
    if a1 != a1 or a1 in (float("inf"), float("-inf")):
        return None
    return a1


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
    """bars 可乱序；open_t 为决策日开盘。

    若提供 as_of，只用 date < as_of 的收盘作昨收/波动，避免误吃当日 K。
    缺 open_t 时 a1=None（fail-open），不回退昨 K 开盘。
    """
    empty = {"a1": None, "vol20": None, "prev_close": None}
    if not bars:
        return empty
    day = _norm_day(as_of)
    dated: List[Tuple[str, float]] = []
    undated: List[float] = []
    try:
        for b in bars:
            if not isinstance(b, Mapping):
                continue
            c = _f(b.get("close"))
            if c is None or c <= 0:
                continue
            bd = _bar_date(b)
            if bd:
                if len(day) >= 10 and bd >= day:
                    continue
                dated.append((bd, c))
            elif not day:
                # 无 as_of 才收无日期行，避免滤空后误用当日
                undated.append(c)
    except TypeError:
        return empty
    if dated:
        dated.sort(key=lambda x: x[0])
        closes = [c for _, c in dated]
    else:
        closes = undated
    if len(closes) < 2:
        return empty
    prev = closes[-1]
    return {
        "a1": a1_from_open_prev_close(_f(open_t), prev),
        "vol20": vol20_ann_from_closes(closes),
        "prev_close": prev,
    }


def _clear_morph_vol_p(items: Optional[Sequence[dict]]) -> None:
    for it in items or ():
        if isinstance(it, dict):
            it.pop("morph_vol_p", None)


def _clear_all_morph_fields(items: Optional[Sequence[dict]]) -> None:
    for it in items or ():
        if isinstance(it, dict):
            it.pop("morph_vol_p", None)
            it.pop("morph_a1", None)
            it.pop("morph_vol20", None)


def stamp_morph_cs_on_items(
    items: Optional[Sequence[dict]],
    *,
    metrics_by_code: Mapping[str, Mapping[str, Optional[float]]],
) -> None:
    """就地写入 morph_a1 / morph_vol20 / morph_vol_p（池内分位 0..1）。

    池有效 vol 样本 < 8 时清掉 morph_vol_p（fail-open，避免旧分位残留）。
    同 code 多行只取一次 vol 参与截面，再广播回所有行。
    """
    rows = items or ()
    if not isinstance(metrics_by_code, Mapping):
        _clear_morph_vol_p(rows)
        return
    try:
        codes: List[str] = []
        vols: List[float] = []
        seen: set[str] = set()
        for it in rows:
            if not isinstance(it, dict):
                continue
            code = _norm_code(it.get("stock_code"))
            m = metrics_by_code.get(code) if code else None
            if m is None and code:
                # 兼容调用方 metrics 未补零
                m = metrics_by_code.get(str(it.get("stock_code") or "").strip())
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
            _clear_morph_vol_p(rows)
            return
        try:
            import numpy as np

            arr = np.asarray(vols, dtype=float)
            if arr.size != len(vols) or not np.isfinite(arr).all():
                _clear_morph_vol_p(rows)
                return
            order = arr.argsort().argsort().astype(float)
            denom = max(len(arr) - 1, 1)
            pctl = order / float(denom)
            by_p = {c: float(p) for c, p in zip(codes, pctl)}
        except Exception:  # noqa: BLE001
            # 纯 Python 分位回退
            logger.debug("stamp percentile numpy failed; pure-python", exc_info=True)
            order_idx = sorted(range(len(vols)), key=lambda i: vols[i])
            rank = [0.0] * len(vols)
            for r, i in enumerate(order_idx):
                rank[i] = float(r)
            denom = max(len(vols) - 1, 1)
            by_p = {c: rank[i] / denom for i, c in enumerate(codes)}
        for it in rows:
            if not isinstance(it, dict):
                continue
            code = _norm_code(it.get("stock_code"))
            if code in by_p:
                p = by_p[code]
                if p != p or p < -1e-9 or p > 1.0 + 1e-9:
                    it.pop("morph_vol_p", None)
                else:
                    it["morph_vol_p"] = round(max(0.0, min(1.0, p)), 6)
            else:
                it.pop("morph_vol_p", None)
    except Exception:  # noqa: BLE001
        logger.debug("stamp_morph_cs_on_items failed", exc_info=True)
        _clear_morph_vol_p(rows)


def is_morph_gate_reason(reason: Any) -> bool:
    """跳过文案是否为形态闸。只认前缀，句子中部提到不算。"""
    return str(reason or "").strip().startswith(MORPH_GATE_REASON_PREFIX)


def row_is_morph_gate(row: Optional[Mapping[str, Any]]) -> bool:
    """盯盘行是否为形态闸跳过。显式标记或原因前缀均可，缺一不漏。"""
    if not isinstance(row, Mapping):
        return False
    if row.get("morph_gate") is True:
        return True
    return is_morph_gate_reason(row.get("reason"))


def morph_gate_skip_reason(
    item: Optional[dict],
    cfg: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """过闸返回中文原因；未启用/缺分/未触发/异常返回 None（fail-open）。

    先判异常低开（a1≤−8%）；再判涨停开盘（按代码板块）；再判高波弱开（vol_p≥0.9 且 a1<0）。
    """
    try:
        if not morph_gate_enabled(cfg):
            return None
        if not isinstance(item, dict):
            return None
        a1 = _f(item.get("morph_a1"))
        if a1 is None:
            return None
        # 脏跳空：不当成可交易信号
        if abs(a1) > _A1_ABS_MAX + 1e-15:
            return None
        gap_max = MORPH_GAP_MAX
        if a1 <= -gap_max + 1e-15:
            return f"形态闸·异常低开(a1={a1:.2%} ≤ −{gap_max:.2%})"
        limit_skip = _limit_up_open_reason(item, a1)
        if limit_skip:
            return limit_skip
        vol_p = _f(item.get("morph_vol_p"))
        if vol_p is None or not (0.0 <= vol_p <= 1.0 + 1e-9):
            return None
        vp_thr = MORPH_VOL_PCTL
        a1_max = MORPH_A1_MAX
        if vol_p >= vp_thr and a1 < a1_max:
            return (
                f"形态闸·高波弱开(vol_p={vol_p:.2f}≥{vp_thr:g}, "
                f"a1={a1:.2%} < {a1_max:.2%})"
            )
        return None
    except Exception:  # noqa: BLE001
        logger.debug("morph_gate_skip_reason failed", exc_info=True)
        return None


def _limit_up_open_reason(item: Mapping[str, Any], a1: float) -> Optional[str]:
    """今开已到涨停价则返回原因。无代码且无名称时不知道板块，fail-open。"""
    code = str(item.get("stock_code") or "").strip()
    name = item.get("stock_name") or item.get("name")
    if not code and not str(name or "").strip():
        return None
    try:
        from core.backtest.matching import limit_up_threshold_for_code

        thr = float(
            limit_up_threshold_for_code(
                code or None,
                stock_name=str(name) if name else None,
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("limit_up_threshold_for_code failed", exc_info=True)
        return None
    if thr != thr or thr <= 0:
        return None
    gap_pct = float(a1) * 100.0
    if gap_pct + 1e-9 < thr:
        return None
    return f"形态闸·涨停开盘(a1={a1:.2%} ≥ {thr / 100.0:.2%})"


def _row_prev_close(it: Mapping[str, Any]) -> Optional[float]:
    prev = _f(it.get("prev_close")) or _f(it.get("last_close"))
    if prev is not None:
        return prev
    factors = it.get("factors")
    if isinstance(factors, Mapping):
        return _f(factors.get("last_close")) or _f(factors.get("prev_close"))
    return None


def _row_open(it: Mapping[str, Any]) -> Optional[float]:
    return (
        _f(it.get("day_open"))
        or _f(it.get("open"))
        or _f(it.get("open_price"))
        or _f(it.get("today_open"))
    )


def _norm_code(raw: Any) -> str:
    """A 股代码归一：纯数字 ≤6 位左补零；其余原样 strip。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.isdigit() and len(s) <= 6:
        return s.zfill(6)
    return s


def _opens_str_map(opens: Optional[Mapping[Any, Any]]) -> Dict[str, float]:
    """opens 键统一成 str（数字码补零）；非有限价丢掉。"""
    out: Dict[str, float] = {}
    if not isinstance(opens, Mapping):
        return out
    try:
        for k, v in opens.items():
            code = _norm_code(k)
            px = _f(v)
            if code and px is not None and px > 0:
                out[code] = px
    except Exception:  # noqa: BLE001
        logger.debug("_opens_str_map failed", exc_info=True)
    return out


def _bars_str_map(
    bars_by_code: Optional[Mapping[Any, Any]],
) -> Dict[str, Sequence[Mapping[str, Any]]]:
    """bars_by_code 键统一成 str（数字码补零）；非序列丢掉。"""
    out: Dict[str, Sequence[Mapping[str, Any]]] = {}
    if not isinstance(bars_by_code, Mapping):
        return out
    try:
        for k, v in bars_by_code.items():
            code = _norm_code(k)
            if not code or not isinstance(v, (list, tuple)):
                continue
            out[code] = v  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        logger.debug("_bars_str_map failed", exc_info=True)
    return out


def _merge_metrics(
    dst: Dict[str, Dict[str, Optional[float]]],
    code: str,
    m: Mapping[str, Optional[float]],
) -> None:
    prev_m = dst.get(code)
    if not isinstance(prev_m, dict):
        dst[code] = {
            "a1": m.get("a1"),
            "vol20": m.get("vol20"),
            "prev_close": m.get("prev_close"),
        }
        return
    if prev_m.get("a1") is None and m.get("a1") is not None:
        prev_m["a1"] = m.get("a1")
    if prev_m.get("vol20") is None and m.get("vol20") is not None:
        prev_m["vol20"] = m.get("vol20")
    if prev_m.get("prev_close") is None and m.get("prev_close") is not None:
        prev_m["prev_close"] = m.get("prev_close")


def try_stamp_morph_from_opens(
    items: Optional[Sequence[dict]],
    *,
    opens: Optional[Mapping[str, float]] = None,
    bars_by_code: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    as_of: Optional[str] = None,
) -> Tuple[int, int]:
    """用今开 + 日线戳 morph 字段。返回 (stamped_a1, stamped_vol_p)。"""
    rows = items or ()
    opens_d = _opens_str_map(opens)
    bars_d = _bars_str_map(bars_by_code)
    day = _norm_day(as_of)
    metrics: Dict[str, Dict[str, Optional[float]]] = {}
    try:
        for it in rows:
            if not isinstance(it, dict):
                continue
            code = _norm_code(it.get("stock_code"))
            if not code:
                continue
            open_t = opens_d.get(code)
            if open_t is None:
                open_t = _row_open(it)
            raw_bars = bars_d.get(code) or []
            bars = [b for b in raw_bars if isinstance(b, Mapping)]
            if bars:
                m = metrics_from_bars(bars, open_t=open_t, as_of=day or None)
                # bars 被 as_of 滤空 / 过短：回退行内昨收，避免整票 fail-open
                if m.get("prev_close") is None:
                    prev = _row_prev_close(it)
                    a1 = a1_from_open_prev_close(open_t, prev)
                    vol = _f(it.get("morph_vol20"))
                    m = {"a1": a1, "vol20": vol, "prev_close": prev}
                _merge_metrics(metrics, code, m)
                continue
            prev = _row_prev_close(it)
            a1 = a1_from_open_prev_close(open_t, prev)
            vol = _f(it.get("morph_vol20"))
            _merge_metrics(
                metrics,
                code,
                {"a1": a1, "vol20": vol, "prev_close": prev},
            )
        stamp_morph_cs_on_items(rows, metrics_by_code=metrics)
    except Exception:  # noqa: BLE001
        logger.debug("try_stamp_morph_from_opens failed", exc_info=True)
        _clear_all_morph_fields(rows)
        return 0, 0
    n_a1 = sum(1 for it in rows if isinstance(it, dict) and _f(it.get("morph_a1")) is not None)
    n_vp = sum(1 for it in rows if isinstance(it, dict) and _f(it.get("morph_vol_p")) is not None)
    return n_a1, n_vp


__all__ = [
    "MORPH_A1_MAX",
    "MORPH_GAP_MAX",
    "MORPH_GATE_REASON_PREFIX",
    "MORPH_VOL_PCTL",
    "is_morph_gate_reason",
    "row_is_morph_gate",
    "a1_from_open_prev_close",
    "metrics_from_bars",
    "morph_gate_enabled",
    "morph_gate_skip_reason",
    "stamp_morph_cs_on_items",
    "try_stamp_morph_from_opens",
    "vol20_ann_from_closes",
]
