"""回测日打分：优先从 L1 Feature Lake 灌特征，miss 再 score_window。

暖路径：``prefetch_l1_coverage`` 把整票 panel 留在进程内，日循环按日切片，
避免每票每日重开 npz；coverage 已确认无该日时直接 miss，不切窗。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 进程内：family → code → set(dates) 覆盖索引（paper_replay 预热）
_COVERAGE: Dict[str, Dict[str, Set[str]]] = {}
# 进程内：family → code → L1 pack（含 X/dates/names 与 _date_i）
_L1_PACKS: Dict[str, Dict[str, Dict[str, Any]]] = {}


def clear_coverage_cache() -> None:
    _COVERAGE.clear()
    _L1_PACKS.clear()


def _index_pack(pack: Dict[str, Any]) -> Dict[str, Any]:
    """为 pack 挂 ``_date_i``，便于 O(1) 按日取行。"""
    dates = list(pack.get("dates") or [])
    pack["_date_i"] = {str(d)[:10]: i for i, d in enumerate(dates)}
    return pack


def prefetch_l1_coverage(
    codes: Sequence[str],
    *,
    families: Optional[Sequence[str]] = None,
    progress_cb: Optional[Any] = None,
    cancel_cb: Optional[Any] = None,
) -> Dict[str, int]:
    """预热 L1 按股日期索引 + 整票 panel 内存缓存。

    返回 ``{family: n_codes_loaded}``。
    ``progress_cb(cur, tot, msg)`` 按票推进，避免 Job 槽 current=0 被误杀。
    """
    from core.research.feature_lake import enabled, load_stock_panel
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC

    if not enabled():
        return {}
    fams = list(families) if families else [FAMILY_OO, FAMILY_TC, FAMILY_CO]
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    n_codes = max(1, len(code_list))
    stats: Dict[str, int] = {}

    def _cancelled() -> bool:
        if not cancel_cb:
            return False
        try:
            return bool(cancel_cb())
        except Exception:  # noqa: BLE001
            return False

    def _emit(done: int, msg: str) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(int(done), n_codes, msg)
        except Exception:  # noqa: BLE001
            logger.debug("l1 prefetch progress_cb failed", exc_info=True)

    for fi, fam in enumerate(fams):
        if _cancelled():
            break
        bucket = _COVERAGE.setdefault(str(fam), {})
        panels = _L1_PACKS.setdefault(str(fam), {})
        n = 0
        for i, code in enumerate(code_list):
            if _cancelled():
                break
            pack = load_stock_panel(fam, code, fingerprint=None)
            if not pack:
                bucket.pop(code, None)
                panels.pop(code, None)
            else:
                indexed = _index_pack(dict(pack))
                panels[code] = indexed
                bucket[code] = set(indexed.get("_date_i") or {})
                n += 1
            if progress_cb and (i % 25 == 0 or i + 1 == n_codes):
                _emit(
                    i + 1,
                    f"预热 Feature Lake {fam} {i + 1}/{n_codes}"
                    + (f"（family {fi + 1}/{len(fams)}）" if len(fams) > 1 else ""),
                )
        stats[str(fam)] = n
    logger.info("l1 coverage prefetch codes=%s stats=%s", len(code_list), stats)
    return stats


def _coverage_has(family: str, code: str, day: str) -> Optional[bool]:
    """None=无索引；True/False=有索引时的命中。"""
    bucket = _COVERAGE.get(str(family))
    if not bucket:
        return None
    days = bucket.get(str(code))
    if days is None:
        return False
    return str(day)[:10] in days


def _feats_from_pack(pack: Dict[str, Any], day: str) -> Optional[Dict[str, float]]:
    day_s = str(day or "")[:10]
    idx_map = pack.get("_date_i")
    if not isinstance(idx_map, dict):
        indexed = _index_pack(pack)
        idx_map = indexed.get("_date_i") or {}
    idx = idx_map.get(day_s)
    if idx is None:
        return None
    names = list(pack.get("names") or [])
    X = pack.get("X")
    if X is None:
        return None
    row = np.asarray(X, dtype=np.float64)[int(idx)]
    feats: Dict[str, float] = {}
    for j, name in enumerate(names):
        if j >= row.shape[0]:
            break
        v = float(row[j])
        if np.isfinite(v):
            feats[str(name)] = v
    return feats or None


def _day_feats(family: str, code: str, day: str) -> Optional[Dict[str, float]]:
    """优先进程缓存；未预热则单票读盘。"""
    panels = _L1_PACKS.get(str(family)) or {}
    pack = panels.get(str(code))
    if pack is not None:
        return _feats_from_pack(pack, day)
    try:
        from core.research.feature_lake import load_day_rows

        return load_day_rows(family, [code], day).get(code)
    except Exception:  # noqa: BLE001
        return None


def _lake_row_maps(
    codes: Sequence[str],
    day: str,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    """一日内批量取 oo/tc/co 特征（吃进程缓存）。"""
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC

    day_s = str(day or "")[:10]
    oo_m: Dict[str, Dict[str, float]] = {}
    tc_m: Dict[str, Dict[str, float]] = {}
    co_m: Dict[str, Dict[str, float]] = {}
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        oo = _day_feats(FAMILY_OO, code, day_s)
        if oo:
            oo_m[code] = oo
        tc = _day_feats(FAMILY_TC, code, day_s)
        if tc:
            tc_m[code] = tc
        co = _day_feats(FAMILY_CO, code, day_s)
        if co:
            co_m[code] = co
    return oo_m, tc_m, co_m


def _clock_hm_display(tau_hm: str) -> str:
    """``0950`` / ``09:50`` → ``09:50``；open → ``09:30``。"""
    from core.research.lake_bridge import normalize_tau_clock

    clock = normalize_tau_clock(tau_hm)
    if clock == "open":
        return "09:30"
    if len(clock) == 4 and clock.isdigit():
        return f"{clock[:2]}:{clock[2:]}"
    s = str(tau_hm or "").strip()
    return s[:5] if len(s) >= 5 else "09:30"


def load_tau_feats_map(
    codes: Sequence[str],
    day: str,
    *,
    tau_hm: str,
) -> Dict[str, Dict[str, float]]:
    """按钟批量取 τ L1 行（开盘=``tc``，分钟=``tc_HHMM``）。"""
    from core.research.lake_bridge import family_for_tc

    fam = family_for_tc(tau_hm=tau_hm)
    day_s = str(day or "")[:10]
    out: Dict[str, Dict[str, float]] = {}
    if len(day_s) < 10:
        return out
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        feats = _day_feats(fam, code, day_s)
        if feats:
            out[code] = feats
    return out


def try_prefix_live_from_lake(
    open_snap: Optional[dict],
    *,
    code: str,
    day: str,
    tau_hm: str,
    feats: Optional[Dict[str, float]] = None,
    tau_pool_day: Optional[dict] = None,
) -> Optional[dict]:
    """用 ``tc_HHMM`` L1 行灌 features_tau 并重算 ŷ_τc；缺行 / 缺开→τ 则 None。

    调用方应已在 ``temporary_pool_feature_rows`` 内（同伴 μ/σ 与本钟特征对齐）。
    """
    from core.research.feature_lake import enabled
    from core.research.lake_bridge import FAMILY_TC, family_for_tc

    if not enabled():
        return None
    code_s = str(code or "").strip()
    day_s = str(day or "")[:10]
    if not code_s or len(day_s) < 10:
        return None
    fam = family_for_tc(tau_hm=tau_hm)
    if fam == FAMILY_TC:
        # 开盘族不走前缀路径（09:30 已用开盘 Z）
        return None
    row = feats if isinstance(feats, dict) else _day_feats(fam, code_s, day_s)
    if not row or row.get("ret_open_to_tau") is None:
        return None
    live: Dict[str, Any] = dict(open_snap) if isinstance(open_snap, dict) else {}
    live["stock_code"] = code_s
    live["date"] = day_s
    live["as_of"] = day_s
    live["trade_date"] = day_s
    live["features_tau"] = dict(row)
    if "gap_pct" in row:
        live["gap_pct"] = row.get("gap_pct")
    try:
        from core.t0.score_policy import tau_pool_day_score_kwargs

        xs = tau_pool_day_score_kwargs(tau_pool_day, code_s) or {}
        if xs.get("pool_gaps"):
            live["_pool_gaps"] = xs.get("pool_gaps")
        ft = live["features_tau"]
        for k in ("sector_gap_breadth", "sector_gap_median", "sector_ret_to_tau"):
            if xs.get(k) is not None and ft.get(k) is None:
                ft[k] = xs.get(k)
    except Exception:  # noqa: BLE001
        logger.debug("prefix lake pool kwargs failed", exc_info=True)
    hm = _clock_hm_display(tau_hm)
    try:
        from core.t0.score_policy import _refresh_tau_oc_from_feats

        _refresh_tau_oc_from_feats(live, hm=hm, trade_date=day_s, force=True)
    except Exception:  # noqa: BLE001
        logger.debug("prefix lake refresh y_τc failed", exc_info=True)
        return None
    if live.get("y_τc") is None and live.get("predicted_score_τc") is None:
        return None
    live["_score_source"] = "prefix_causal"
    live["_score_prefix_hm"] = hm
    live["_score_prefix_bars"] = 0
    live["_score_source_detail"] = "feature_lake"
    return live


def assemble_item_from_lake_rows(
    code: str,
    day: str,
    *,
    oo: Optional[Dict[str, float]] = None,
    tc: Optional[Dict[str, float]] = None,
    co: Optional[Dict[str, float]] = None,
    quote: Optional[dict] = None,
    window: Optional[Sequence[dict]] = None,
) -> Optional[dict]:
    """用已取到的 L1 行组装轻量 signal_item；三族皆空则 None。"""
    day_s = str(day or "")[:10]
    code_s = str(code or "").strip()
    if len(day_s) < 10 or not code_s:
        return None
    if not oo and not tc and not co:
        return None
    item: Dict[str, Any] = {
        "stock_code": code_s,
        "as_of": day_s,
        "date": day_s,
        "trade_date": day_s,
        "success": True,
        "_score_source": "feature_lake",
    }
    if oo:
        item["sub_scores"] = dict(oo)
        try:
            from core.signal.factors.z_oo import stamp_z_oo_aliases

            stamp_z_oo_aliases(item["sub_scores"])
        except Exception:  # noqa: BLE001
            pass
    if tc:
        item["features_tau"] = dict(tc)
        if "gap_pct" in tc:
            item["gap_pct"] = tc.get("gap_pct")
    if co:
        item["features_co"] = dict(co)
        item["features_on"] = dict(co)
    if isinstance(quote, dict) and quote:
        item["quote"] = quote
        for k in ("price_raw", "price", "open", "open_raw", "prev_close", "change_pct"):
            if k in quote and quote[k] is not None:
                item.setdefault(k, quote[k])
    if window:
        item["_hist_bars"] = list(window)
    return item


def try_score_item_from_lake(
    code: str,
    day: str,
    *,
    quote: Optional[dict] = None,
    window: Optional[Sequence[dict]] = None,
) -> Optional[dict]:
    """若 L1 有该日 oo/tc/co 行，组装轻量 signal_item；否则 None。

    仍附带 quote / 末窗价量，供涨跌停与 yhat 头使用。
    """
    day_s = str(day or "")[:10]
    code_s = str(code or "").strip()
    if len(day_s) < 10 or not code_s:
        return None
    try:
        from core.research.feature_lake import enabled
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
    except Exception:  # noqa: BLE001
        return None
    if not enabled():
        return None
    # 预热索引：三族皆明确无该日 → 直接 miss，避免三次开盘 npz
    flags = [
        _coverage_has(FAMILY_OO, code_s, day_s),
        _coverage_has(FAMILY_TC, code_s, day_s),
        _coverage_has(FAMILY_CO, code_s, day_s),
    ]
    if all(f is False for f in flags):
        return None
    oo = _day_feats(FAMILY_OO, code_s, day_s) if flags[0] is not False else None
    tc = _day_feats(FAMILY_TC, code_s, day_s) if flags[1] is not False else None
    co = _day_feats(FAMILY_CO, code_s, day_s) if flags[2] is not False else None
    return assemble_item_from_lake_rows(
        code_s, day_s, oo=oo, tc=tc, co=co, quote=quote, window=window
    )


def score_open_day_entries(
    *,
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
    horizon_days: int,
    cfg: Optional[dict],
    required_factor_keys: Optional[List[str]],
    window_for_code,
    mock_quote_from_bar,
) -> tuple:
    """返回 ``(entries, quotes, windows, lake_hits, lake_miss)``。

    coverage 已确认无该日 → 不切窗、不读盘；暖缓存命中 → 跳过 ``score_window_as_item``
    与昨收窗（开盘回测 y_hl 已下线；缺口/ŷ 头吃 quote + L1 特征）。
    """
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
    from core.signal.cross_section_batch import score_window_as_item

    entries: List[dict] = []
    quotes: Dict[str, dict] = {}
    windows: Dict[str, List[dict]] = {}
    hits = 0
    miss = 0
    if day_i <= 0:
        return entries, quotes, windows, hits, miss
    prev_i = day_i - 1
    today = dates[day_i]
    req_keys = list(required_factor_keys or []) or None
    codes = [str(c).strip() for c in stock_bars if str(c or "").strip()]

    # 一日批量切片（吃 prefetch 内存），避免 per-code 反复探盘
    oo_m, tc_m, co_m = _lake_row_maps(codes, today)

    for code in codes:
        dm = date_maps.get(code) or {}
        today_bar = dm.get(today)
        prev_bar = dm.get(dates[prev_i])
        if today_bar is None and prev_bar is None:
            continue
        quote = mock_quote_from_bar(today_bar, prev_bar=prev_bar, px_field="open")
        if quote:
            quote["date"] = today
            quote["trade_date"] = today

        flags = [
            _coverage_has(FAMILY_OO, code, today),
            _coverage_has(FAMILY_TC, code, today),
            _coverage_has(FAMILY_CO, code, today),
        ]
        # 预热后三族皆无该日 → 仍可能走切窗（L1 未写穿时的回退）
        definite_miss = bool(flags) and all(f is False for f in flags)
        oo = oo_m.get(code) if flags[0] is not False else None
        tc = tc_m.get(code) if flags[1] is not False else None
        co = co_m.get(code) if flags[2] is not False else None
        # 须有 oo 行才跳过切窗：ŷ_oo / oo_rank 依赖 sub_scores。
        # 仅 tc/co 有湖日（oo 湖常慢一天）若当命中，会留下无 sub_scores 的条目，
        # 末日记明细 y_oo / y_oo_rank 全空而 y_τc / τc_rank 仍在。
        if not definite_miss and oo:
            item = assemble_item_from_lake_rows(
                code, today, oo=oo, tc=tc, co=co, quote=quote, window=None
            )
            if item is not None:
                hits += 1
                entries.append(item)
                quotes[str(code)] = quote or {}
                windows[str(code)] = []
                continue

        # miss / 缺 oo：切窗 + score_window_as_item
        miss += 1
        window = window_for_code(code, dates, date_maps, prev_i, max_window)
        if len(window) < 2:
            continue
        item = score_window_as_item(
            code,
            window,
            horizon_days=horizon_days,
            quote=quote,
            config=cfg,
            required_factor_keys=req_keys,
        )
        if item:
            entries.append(item)
            quotes[str(code)] = quote or {}
            windows[str(code)] = window
    return entries, quotes, windows, hits, miss
