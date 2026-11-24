"""会话日开盘截面特征构建（物化写盘用）：昨收日线 + 行情今开。

风格因子与训练面板同一套 PIT：X 只用 date < 会话日的日线，quote 是今开 / 昨收。
今收不进 X。相对强弱不传指数，按今开缺口分档，与 ``collect_oo_panel`` 一致。

仅供 ``materialize.session_open.materialize_session_open_l2`` 正式落官方 open DayCube。
调仓 / 持仓出分 **不得** 直接消费本模块拼截面；缺 L2 → ``materialize_required``。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[int, int, str], None]]


def _emit_open_pit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("open_pit progress failed", exc_info=True)

_OPEN_PIT_MAX_BARS = 80
_OPEN_PIT_MIN_HIST = 61
# 这三包的 raw_* 已由 inject_raw_aoo / inject_raw_atc 写入，再算会盖住开盘列。
_STYLE_PACKS = frozenset({"alpha_oo", "alpha_tc", "alpha_th"})
# 训练面板里这些列缺 PIT 财务就是 NaN，不能用来否决整行。
# 否决的话开盘截面会变成 items=0（今开其实已经齐了）。
_STYLE_MAY_OMIT = frozenset(
    {"value", "quality", "size", "earnings_yield", "growth", "dividend"}
)
_style_miss_logged = False


def _fpx(*vals: Any) -> Optional[float]:
    for v in vals:
        if v is None or v == "":
            continue
        try:
            n = float(str(v).replace("元", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if n == n and n > 0:
            return float(n)
    return None


def _hist_through_prev(
    bars: Sequence[dict],
    *,
    cycle_day: str,
) -> List[dict]:
    """只用 date < 会话日的完整日线（今开由行情另接）。"""
    day = str(cycle_day or "")[:10]
    out: List[dict] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "")[:10]
        if len(d) >= 10 and day and d >= day:
            continue
        out.append(b)
    return out


def _quote_usable_open(quote: Optional[dict]) -> bool:
    try:
        from adapters.market.quote_api import StockAPI

        return bool(StockAPI.quote_has_day_open(quote if isinstance(quote, dict) else None))
    except Exception:  # noqa: BLE001
        return False


def _batch_quotes(codes: Sequence[str]) -> Dict[str, dict]:
    """拉开盘截面行情。先丢掉相关内存缓存，避免持仓盯市写入的「空今开」毒死 open-pit。

    交易执行页 / ``mark_to_market`` 会先只拉持仓票；竞价未定开时 ``open=""`` 仍
    ``success=True`` 并进短缓存。随后 open-pit 整池 ``batch_query`` 对持仓命中缓存，
    其它票现场拉到今开 → 恰似「只有昨持仓缺今开」。缺今开的票再失效并重拉一轮。
    """
    uniq = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if not uniq:
        return {}
    try:
        from adapters.market.quote_api import StockAPI

        StockAPI.invalidate_codes(uniq)
    except Exception:  # noqa: BLE001
        logger.debug("open_pit quote cache invalidate skipped", exc_info=True)
    try:
        from core.paper.rebalance.match import _batch_query_quotes

        out = dict(_batch_query_quotes(uniq) or {})
    except Exception:  # noqa: BLE001
        logger.warning("open_pit batch quotes failed", exc_info=True)
        return {}
    missing = [c for c in uniq if not _quote_usable_open(out.get(c))]
    if not missing:
        return out
    try:
        from adapters.market.quote_api import StockAPI

        StockAPI.invalidate_codes(missing)
    except Exception:  # noqa: BLE001
        logger.debug("open_pit missing-open invalidate skipped", exc_info=True)
    try:
        from core.paper.rebalance.match import _batch_query_quotes

        retry = dict(_batch_query_quotes(missing) or {})
        if retry:
            out.update(retry)
    except Exception:  # noqa: BLE001
        logger.warning("open_pit missing-open retry failed", exc_info=True)
    return out


def _load_hist_bars(
    codes: Sequence[str],
    *,
    min_bars: int = _OPEN_PIT_MIN_HIST,
    max_bars: int = _OPEN_PIT_MAX_BARS,
) -> Dict[str, List[dict]]:
    pairs = [("CN", str(c).strip()) for c in codes if str(c or "").strip()]
    if not pairs:
        return {}
    try:
        from core.store import load_daily_cache_many

        packed = load_daily_cache_many(
            pairs,
            min_bars=max(1, int(min_bars)),
            ignore_age=True,
            max_bars=int(max_bars),
        )
    except Exception:  # noqa: BLE001
        logger.warning("open_pit load_daily_cache_many failed", exc_info=True)
        return {}
    out: Dict[str, List[dict]] = {}
    for (_mkt, code), (bars, _meta) in (packed or {}).items():
        key = str(code or "").strip()
        if key and isinstance(bars, list) and bars:
            out[key] = list(bars)
    return out


def _open_and_prev(
    *,
    cycle_day: str,
    quote: Optional[dict],
    hist: Sequence[dict],
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    from core.signal.session_pit import resolve_open_t

    got = resolve_open_t(quote, hist, trade_day=cycle_day)
    ot = got.get("open")
    pc = got.get("prev_close")
    src = got.get("source")
    if ot is None:
        ot = _fpx(
            (quote or {}).get("open_raw"),
            (quote or {}).get("open"),
            (quote or {}).get("open_price"),
        )
        if ot is not None:
            src = src or "quote_open"
    if pc is None and hist:
        pc = _fpx(hist[-1].get("close"))
    return (
        float(ot) if ot is not None else None,
        float(pc) if pc is not None else None,
        str(src) if src else None,
    )


def _finite_feat_map(
    src: Dict[str, Any],
    *,
    prefixes: Tuple[str, ...],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (src or {}).items():
        sk = str(k)
        if not any(sk.startswith(p) for p in prefixes):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if math.isfinite(fv):
            out[sk] = fv
    return out


def _gap_pct(open_t: float, prev_close: Optional[float]) -> Optional[float]:
    if prev_close is None:
        return None
    try:
        pc = float(prev_close)
        ot = float(open_t)
    except (TypeError, ValueError):
        return None
    if pc <= 0 or ot != ot or pc != pc:
        return None
    return round((ot / pc - 1.0) * 100.0, 6)


def _attach_open_tau_z_feats(
    feats: Dict[str, Any],
    *,
    hist: Sequence[dict],
    open_t: float,
    prev_close: Optional[float],
    cycle_day: str,
) -> Optional[float]:
    """写入开盘 ŷ_τc / τc_rank 面板裸名（gap/theme 等）。返回 gap_pct 供截面二次补齐。"""
    from core.research.tau_panel import (
        attach_tau_lag_features,
        gap_atr_from_hist,
        yclose_loc_from_prev,
    )
    from core.signal.factors.z_tc import stamp_z_tc_aliases

    gap = _gap_pct(open_t, prev_close)
    if gap is not None:
        feats["gap_pct"] = gap
    try:
        atr = gap_atr_from_hist(gap, hist)
        if atr is not None:
            feats["gap_atr"] = atr
    except Exception:  # noqa: BLE001
        logger.debug("open_pit gap_atr failed", exc_info=True)
    try:
        yloc = yclose_loc_from_prev(hist[-1] if hist else None, float(open_t))
        if yloc is not None:
            feats["yclose_loc"] = yloc
    except Exception:  # noqa: BLE001
        logger.debug("open_pit yclose_loc failed", exc_info=True)
    try:
        lagged = attach_tau_lag_features(
            feats, hist_bars=hist, asof_date=str(cycle_day or "")[:10]
        )
        if isinstance(lagged, dict):
            feats.update(lagged)
    except Exception:  # noqa: BLE001
        logger.debug("open_pit tau lag failed", exc_info=True)
    # 截面项先占位；build 后按全池 gap 重写 theme / breadth / gap_vs_sector
    feats.setdefault("theme_day", 0.0)
    feats.setdefault("sector_gap_breadth", 0.0)
    feats.setdefault("gap_vs_sector", 0.0)
    stamp_z_tc_aliases(feats)
    return gap


def _enrich_open_pit_tau_cross_section(items: Sequence[dict]) -> None:
    """全池 gap → theme_day / sector_gap_breadth / gap_vs_sector（与开盘 τ 面板同构）。"""
    from core.research.tau_panel import _finite_median, gap_vs_sector_value
    from core.research.tau_theme import resolve_theme_day
    from core.signal.factors.z_tc import stamp_z_tc_aliases

    gaps: List[float] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        tau = it.get("features_tau")
        if not isinstance(tau, dict):
            continue
        g = tau.get("z_τc_gap_pct")
        if g is None:
            g = tau.get("gap_pct")
        try:
            fv = float(g) if g is not None and g != "" else None
        except (TypeError, ValueError):
            fv = None
        if fv is not None and fv == fv:
            gaps.append(fv)
            it["_open_pit_gap_pct"] = fv
    if not gaps:
        return
    med = _finite_median(gaps)
    try:
        breadth = float(sum(1 for g in gaps if abs(float(g)) >= 2.0)) / float(len(gaps))
    except (TypeError, ValueError, ZeroDivisionError):
        breadth = 0.0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        tau = it.get("features_tau")
        if not isinstance(tau, dict):
            continue
        gap = it.pop("_open_pit_gap_pct", None)
        if gap is None:
            continue
        try:
            th = resolve_theme_day(
                gap_pct=gap,
                sector_breadth=breadth,
                pool_gaps=gaps,
                gap_trigger_pct=2.0,
            )
        except Exception:  # noqa: BLE001
            th = 1.0 if abs(float(gap)) >= 2.0 else 0.0
        tau["theme_day"] = float(th)
        tau["sector_gap_breadth"] = float(breadth)
        try:
            tau["gap_vs_sector"] = gap_vs_sector_value(gap, med)
        except Exception:  # noqa: BLE001
            tau["gap_vs_sector"] = 0.0
        stamp_z_tc_aliases(tau)


def _open_style_hist(hist: Sequence[dict]) -> List[dict]:
    """与 ``collect_oo_panel`` 抬窗后的 X 等长：只要 T−1 及更早，不含会话日。

    抬窗必须看见 ``alpha_oo``（全量注册名）。改用 ``_style_factor_names()``
    会把窗口从 61 根缩回 29 根，风格分就和官方立方对不上。
    """
    from core.research.oo_panel import OO_PANEL_MAX_WINDOW, OO_PANEL_MIN_HISTORY
    from core.signal.factors.alpha_oo import (
        ALPHA_OO_PANEL_WINDOW,
        bump_window_for_alpha_oo,
    )
    from core.signal.factors.meta.registry import registered_factor_names

    _min_h, max_window = bump_window_for_alpha_oo(
        registered_factor_names(),
        min_history=OO_PANEL_MIN_HISTORY,
        max_window=OO_PANEL_MAX_WINDOW,
    )
    n_hist = max(1, int(max_window) - 1)
    # 抬窗失败时用短窗会让整池风格分偏离官方立方，宁可不进截面。
    if int(max_window) < int(ALPHA_OO_PANEL_WINDOW):
        logger.error(
            "open_pit style window not bumped: %s < %s",
            max_window,
            ALPHA_OO_PANEL_WINDOW,
        )
        return []
    rows = [b for b in hist if isinstance(b, dict)]
    if len(rows) < n_hist:
        return []
    return rows[-n_hist:]


def _open_style_quote(open_t: float, prev_close: Optional[float]) -> Dict[str, Any]:
    """与 ``pit_oo_window_quote`` 同口径：change_raw 是今开相对昨收的百分点。"""
    quote: Dict[str, Any] = {
        "open": float(open_t),
        "open_raw": float(open_t),
        "price_raw": float(open_t),
        "change_raw": 0.0,
    }
    if prev_close is None:
        return quote
    try:
        pc = float(prev_close)
    except (TypeError, ValueError):
        return quote
    if pc > 0 and pc == pc:
        quote["prev_close"] = pc
        quote["change_raw"] = round((float(open_t) / pc - 1.0) * 100.0, 4)
    return quote


def _bars_before_day(bars: Sequence[dict], cycle_day: str) -> List[dict]:
    """丢掉 date ≥ 会话日的 bar。今收 / 今日指数不进开盘 X。"""
    day = str(cycle_day or "")[:10]
    out: List[dict] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or "")[:10]
        if len(d) >= 10 and day and d >= day:
            continue
        out.append(b)
    return out


def _load_open_style_index(cycle_day: str) -> List[dict]:
    """基准指数日线，只要 date < 会话日。只读进程缓存和本地日线，不打新浪。

    开盘补齐时现拉指数会占住 AkShare 锁，整池停在「拉今开」。没有指数时特异动量缺测。
    """
    bars: List[dict] = []
    try:
        from core.signal.live_features import peek_cached_index_bars

        pack = peek_cached_index_bars(market="CN", allow_live_origin=True)
        if isinstance(pack, dict):
            bars = [b for b in (pack.get("bars") or []) if isinstance(b, dict)]
    except Exception:  # noqa: BLE001
        logger.debug("open_pit index cache peek failed", exc_info=True)
        bars = []
    if len(bars) < 21:
        try:
            from core.data.facade import get_bars
            from core.ports.market import default_benchmark

            bench = str(default_benchmark("CN") or "sh000300").strip() or "sh000300"
            pack = (
                get_bars(
                    bench,
                    limit=120,
                    offline_only=True,
                    reject_quote_fallback=True,
                )
                or {}
            )
            offline = [b for b in (pack.get("bars") or []) if isinstance(b, dict)]
            if len(offline) > len(bars):
                bars = offline
        except Exception:  # noqa: BLE001
            logger.debug("open_pit offline index bars failed", exc_info=True)
    kept = _bars_before_day(bars, cycle_day)
    if len(kept) < 21:
        logger.info(
            "open_pit index thin day=%s n=%s; idio_momentum left missing",
            str(cycle_day or "")[:10],
            len(kept),
        )
    return kept


def _open_style_fundamentals(code: str, cycle_day: str) -> Optional[dict]:
    """本地 PIT 财务。缺公告日返回 None。不调用估值接口。

    ``resolve_fundamentals_for_score`` 会按票打估值网。1600 只串行时补齐会停在
    「拉今开」。这里只读已落盘的财务和估值缓存。
    """
    day = str(cycle_day or "")[:10]
    try:
        from core.data.policy import DEFAULT_ANN_MISSING_POLICY
        from core.fundamentals_pit import load_fundamentals_panel, select_point_as_of

        panel = load_fundamentals_panel(code)
        point, meta = select_point_as_of(panel.get("history") or [], day)
    except Exception:  # noqa: BLE001
        logger.debug("open_pit fundamentals failed for %s", code, exc_info=True)
        return None
    if not isinstance(point, dict) or not point.get("metrics"):
        return None
    ann_miss = bool((meta or {}).get("ann_missing") or point.get("ann_missing"))
    if ann_miss and DEFAULT_ANN_MISSING_POLICY in ("zero_weight", "omit", "hard_reject"):
        return None
    metrics = dict(point.get("metrics") or {})
    try:
        from core.valuation_em import merge_cached_valuation

        merged = merge_cached_valuation(code, metrics)
        if isinstance(merged, dict):
            metrics = merged
    except Exception:  # noqa: BLE001
        logger.debug("open_pit cached valuation skipped for %s", code, exc_info=True)
    return metrics or None


def _style_factor_names() -> Tuple[str, ...]:
    """与训练面板同一套裸名：去掉 alpha 包、aoo 重叠列、开盘常数列、无真源因子。"""
    from core.signal.factors.alpha_oo import OPEN_OO_DROP_Z, Z_OO_DROP_WHEN_AOO_CORE
    from core.signal.factors.meta.registry import registered_factor_names

    skip = set(_STYLE_PACKS) | set(Z_OO_DROP_WHEN_AOO_CORE) | set(OPEN_OO_DROP_Z)
    try:
        from core.signal.factors.meta.health import unsourced_factor_names

        skip |= set(unsourced_factor_names())
    except Exception:  # noqa: BLE001
        logger.debug("open_pit unsourced factor list skipped", exc_info=True)
    return tuple(n for n in registered_factor_names() if n not in skip)


def _style_required_names(
    names: Sequence[str],
    *,
    index_bars: Optional[Sequence[dict]],
) -> Tuple[str, ...]:
    """必须有限值的裸名。财务列允许缺；没有指数时特异动量也允许缺。"""
    skip = set(_STYLE_MAY_OMIT)
    if not index_bars:
        skip.add("idio_momentum")
    return tuple(n for n in names if n not in skip)


def _style_complete(scores: Dict[str, float], names: Sequence[str]) -> bool:
    """要的裸名必须全有限值。缺一列进截面会被当成 z=0，名次会偏。"""
    if not names:
        return False
    return all(n in scores for n in names)


def _relative_strength_like_panel(
    window: Sequence[dict],
    quote: dict,
) -> Optional[float]:
    """与训练面板一致：不传指数，相对强弱用今开缺口分档。

    ``collect_oo_panel`` 不带指数，这列在拟合里是 30/45/65/80。
    早盘若改用 20 日超额（28/42/58/72/85），现网权重会吃到没见过的输入。
    """
    from core.signal.factors.meta.registry import compute_factor

    try:
        score, meta = compute_factor(
            "relative_strength",
            list(window),
            quote=quote,
            index_bars=None,
        )
    except Exception:  # noqa: BLE001
        logger.debug("open_pit relative_strength failed", exc_info=True)
        return None
    if isinstance(meta, dict) and meta.get("omit_sub_score"):
        return None
    try:
        val = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val):
        return None
    return val


def _open_style_scores(
    hist: Sequence[dict],
    open_t: float,
    prev_close: Optional[float],
    *,
    index_bars: Optional[Sequence[dict]] = None,
    fundamentals: Optional[dict] = None,
) -> Dict[str, float]:
    """注册风格因子的 0–100 裸分。跳过 alpha 包，避免改写 raw_aoo_* / raw_atc_*。

    价量列（以及有指数时的特异动量）必须齐。财务列缺测留空，与训练面板的 NaN 一样，
    不把整行扔掉。窗不够或昨收缺失时返回空。指数只给特异动量；相对强弱跟训练面板，
    用今开缺口分档。
    """
    from core.research.oo_panel import _research_sub_scores

    try:
        ot = float(open_t)
        pc = float(prev_close) if prev_close is not None else float("nan")
    except (TypeError, ValueError):
        return {}
    if not (math.isfinite(ot) and ot > 0 and math.isfinite(pc) and pc > 0):
        return {}
    window = _open_style_hist(hist)
    names = _style_factor_names()
    required = _style_required_names(names, index_bars=index_bars)
    if not window or not required:
        return {}
    quote = _open_style_quote(ot, pc)
    # 相对强弱不进带指数的那一遍，避免先写成 20 日超额再覆盖。
    indexed = tuple(n for n in names if n != "relative_strength")
    try:
        row = _research_sub_scores(
            window,
            quote=quote,
            index_bars=list(index_bars) if index_bars else None,
            fundamentals=fundamentals if isinstance(fundamentals, dict) else None,
            factor_names=indexed,
        )
    except Exception:  # noqa: BLE001
        logger.info("open_pit style factors failed", exc_info=True)
        return {}
    out: Dict[str, float] = {}
    for key, val in (row or {}).items():
        sk = str(key)
        if sk not in names or sk.startswith("raw_"):
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        fv = float(val)
        if math.isfinite(fv):
            out[sk] = fv
    if "relative_strength" in names:
        rs = _relative_strength_like_panel(window, quote)
        if rs is None:
            out.pop("relative_strength", None)
        else:
            out["relative_strength"] = rs
    if not _style_complete(out, required):
        global _style_miss_logged
        if not _style_miss_logged:
            _style_miss_logged = True
            missing = [n for n in required if n not in out]
            logger.info(
                "open_pit style incomplete missing=%s have=%s",
                ",".join(missing[:12]),
                len(out),
            )
        return {}
    return out


def _feature_item(
    code: str,
    *,
    cycle_day: str,
    hist: Sequence[dict],
    open_t: float,
    prev_close: Optional[float],
    quote: Optional[dict],
    open_source: Optional[str],
    index_bars: Optional[Sequence[dict]] = None,
    fundamentals: Optional[dict] = None,
) -> Tuple[Optional[dict], str]:
    """返回 ``(item, "")``。失败时 item 为空，reason 为 ``hist_short`` / ``style_short``。"""
    from core.signal.factors.alpha_oo import inject_raw_aoo
    from core.signal.factors.alpha_tc import inject_raw_atc

    if len(hist) < _OPEN_PIT_MIN_HIST:
        return None, "hist_short"
    try:
        ot = float(open_t)
    except (TypeError, ValueError):
        return None, "hist_short"
    if not (math.isfinite(ot) and ot > 0):
        return None, "hist_short"
    feats: Dict[str, Any] = {}
    inject_raw_aoo(
        feats,
        hist,
        open_t=ot,
        prev_close=prev_close,
        quote=quote,
    )
    inject_raw_atc(
        feats,
        hist,
        open_t=ot,
        prev_close=prev_close,
        price_tau=ot,
        minute_bars=None,
        tau_hm="open",
        quote=quote,
    )
    try:
        _attach_open_tau_z_feats(
            feats,
            hist=hist,
            open_t=ot,
            prev_close=prev_close,
            cycle_day=cycle_day,
        )
    except Exception:  # noqa: BLE001
        logger.debug("open_pit tau z feats failed for %s", code, exc_info=True)
    style = _open_style_scores(
        hist,
        ot,
        prev_close,
        index_bars=index_bars,
        fundamentals=fundamentals,
    )
    if not style:
        return None, "style_short"
    oo = _finite_feat_map(feats, prefixes=("raw_aoo_",))
    for key, val in style.items():
        # 裸风格分留给 stamp_z_oo_aliases 写成 z_oo_*；不盖已注入的 raw_aoo_*。
        if key not in oo:
            oo[key] = val
    tau = _finite_feat_map(feats, prefixes=("raw_atc_", "z_τc_", "z_tc_", "z_τh_"))
    eod_as_of = ""
    if hist:
        eod_as_of = str(hist[-1].get("date") or "")[:10]
    item: Dict[str, Any] = {
        "stock_code": code,
        "as_of": cycle_day,
        "date": cycle_day,
        "trade_date": cycle_day,
        "trade_day": cycle_day,
        "success": True,
        "_score_source": "session_open_build",
        "lake_score_day": cycle_day,
        "day_open": float(ot),
        "open_t": float(ot),
    }
    if open_source:
        item["open_t_source"] = open_source
    if len(eod_as_of) >= 10:
        item["eod_feature_as_of"] = eod_as_of
    item["sub_scores"] = oo
    if tau:
        item["features_tau"] = tau
    return item, ""


def build_open_pit_feature_items(
    codes: Sequence[str],
    *,
    cycle_day: str,
    universe_codes: Optional[Sequence[str]] = None,
    quotes: Optional[Dict[str, dict]] = None,
    progress_cb: ProgressCb = None,
) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
    """全池抽开盘特征。覆盖不足时返回空列表，``meta.ok`` 为假，调用方拒单。"""
    from core.research.materialize.cover_policy import LAKE_COVER_FRAC, cover_need

    day = str(cycle_day or "")[:10]
    pool = [str(c or "").strip() for c in (universe_codes or codes) if str(c or "").strip()]
    focus = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if len(day) < 10 or len(pool) < 2:
        return [], [], {"ok": False, "reason": "bad_day_or_pool", "cycle_day": day}

    n_pool = len(pool)
    _emit_open_pit(progress_cb, 0, n_pool, f"补齐 {day} 开盘 L2：拉今开 {n_pool}…")
    qmap = dict(quotes) if isinstance(quotes, dict) else _batch_quotes(pool)
    _emit_open_pit(progress_cb, 0, n_pool, f"补齐 {day} 开盘 L2：读日线 {n_pool}…")
    bars_by = _load_hist_bars(pool)
    index_bars = _load_open_style_index(day)
    items: List[dict] = []
    rejected: List[dict] = []
    n_open = 0
    n_style_gap = 0
    n_hist_gap = 0
    for i, code in enumerate(pool):
        if i == 0 or i % 80 == 0:
            _emit_open_pit(
                progress_cb,
                i,
                n_pool,
                f"补齐 {day} 开盘 L2：风格 {i}/{n_pool}…",
            )
        hist = _hist_through_prev(bars_by.get(code) or [], cycle_day=day)
        quote = qmap.get(code) if isinstance(qmap.get(code), dict) else None
        ot, pc, src = _open_and_prev(cycle_day=day, quote=quote, hist=hist)
        if ot is None:
            rejected.append({"stock_code": code, "reason": f"缺今开 {day}"})
            continue
        n_open += 1
        item, why = _feature_item(
            code,
            cycle_day=day,
            hist=hist,
            open_t=ot,
            prev_close=pc,
            quote=quote,
            open_source=src,
            index_bars=index_bars,
            fundamentals=_open_style_fundamentals(code, day),
        )
        if item is None:
            if why == "style_short":
                n_style_gap += 1
                rejected.append({"stock_code": code, "reason": f"风格因子不足 {day}"})
            elif why == "hist_short":
                n_hist_gap += 1
                rejected.append({"stock_code": code, "reason": f"日线不足 {day}"})
            else:
                rejected.append({"stock_code": code, "reason": f"open-pit 特征不足 {day}"})
            continue
        items.append(item)

    need = cover_need(len(pool), frac=LAKE_COVER_FRAC, minimum=2)
    # 今开够、但风格齐的行不够：不能用薄向量或两只票的 μ/σ 成交。
    ok = n_open >= need and len(items) >= need
    meta: Dict[str, Any] = {
        "ok": ok,
        "cycle_day": day,
        "n_universe": len(pool),
        "n_open": n_open,
        "n_items": len(items),
        "n_style_gap": n_style_gap,
        "n_hist_gap": n_hist_gap,
        "n_index": len(index_bars),
        "need_open": need,
        "cover_frac": LAKE_COVER_FRAC,
        "quotes": qmap,
    }
    if not ok:
        meta["reason"] = "style_cover_short" if n_open >= need else "open_cover_short"
        logger.info(
            "open_pit cover short day=%s reason=%s open=%s/%s need=%s items=%s style_gap=%s",
            day,
            meta["reason"],
            n_open,
            len(pool),
            need,
            len(items),
            n_style_gap,
        )
        return [], rejected, meta

    # 全池 gap → theme_day / breadth / gap_vs_sector（τc_rank tip 依赖这些 z_τc_*）
    try:
        _enrich_open_pit_tau_cross_section(items)
    except Exception:  # noqa: BLE001
        logger.debug("open_pit tau cross-section enrich failed", exc_info=True)

    want = set(focus) if focus else set(pool)
    scored = [it for it in items if it.get("stock_code") in want] if want else list(items)
    # 截面 μ/σ 必须用全池 items；调用方 rescore 前要拿齐 pool 行
    meta["pool_items"] = items
    have = {str(it.get("stock_code") or "") for it in scored}
    for code in focus:
        if code not in have and code not in {r.get("stock_code") for r in rejected}:
            rejected.append({"stock_code": code, "reason": f"open-pit 缺 {day}"})
    return scored, rejected, meta
