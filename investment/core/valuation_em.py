"""单票估值补全（PE/PB/市值/股息）：东财 value_em + 分红股息率 + 本地短缓存。

财务 PIT 历史常只有 ROE/增速；估值/股息类因子依赖 pe/pb/market_cap/dividend_yield。
列表与研究分组共用，避免全表现货挂掉后估值因子长期「未算」。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

_FHPS_MEM: Optional[Dict[str, Any]] = None


def _cache_path(code: str) -> str:
    from core.paths import STORE_DIR

    return os.path.join(STORE_DIR, "valuation_em", f"{str(code).zfill(6)}.json")


def _fhps_map_path() -> str:
    from core.paths import STORE_DIR

    return os.path.join(STORE_DIR, "valuation_em", "_fhps_dividend_map.json")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def read_valuation_cache(
    code: str, *, max_age_hours: float = 36.0
) -> Optional[Dict[str, Optional[float]]]:
    path = _cache_path(code)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at") or 0)
        if fetched_at <= 0:
            return None
        age_h = (time.time() - fetched_at) / 3600.0
        if age_h > float(max_age_hours):
            return None
        out = {
            "pe": _to_float(payload.get("pe")),
            "pb": _to_float(payload.get("pb")),
            "pe_ttm": _to_float(payload.get("pe_ttm")),
            "market_cap": _to_float(payload.get("market_cap")),
            "dividend_yield": _to_float(payload.get("dividend_yield")),
        }
        if all(out.get(k) is None for k in ("pe", "pb", "pe_ttm", "market_cap")):
            return None
        return out
    except Exception:
        return None


def write_valuation_cache(code: str, vals: Dict[str, Any]) -> None:
    pe = _to_float(vals.get("pe"))
    pb = _to_float(vals.get("pb"))
    pe_ttm = _to_float(vals.get("pe_ttm"))
    mcap = _to_float(vals.get("market_cap"))
    dy = _to_float(vals.get("dividend_yield"))
    if pe is None and pb is None and pe_ttm is None and mcap is None:
        return
    path = _cache_path(code)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "code": str(code).zfill(6),
                    "pe": pe,
                    "pb": pb,
                    "pe_ttm": pe_ttm,
                    "market_cap": mcap,
                    "dividend_yield": dy,
                    "fetched_at": time.time(),
                },
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _read_fhps_map_disk(*, max_age_hours: float = 36.0) -> Optional[Dict[str, float]]:
    path = _fhps_map_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at") or 0)
        if fetched_at <= 0:
            return None
        age_h = (time.time() - fetched_at) / 3600.0
        if age_h > float(max_age_hours):
            return None
        raw = payload.get("by_code") or {}
        out: Dict[str, float] = {}
        for k, v in raw.items():
            fv = _to_float(v)
            if fv is not None:
                out[str(k).zfill(6)] = fv
        return out or None
    except Exception:
        return None


def _write_fhps_map_disk(by_code: Dict[str, float]) -> None:
    path = _fhps_map_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"fetched_at": time.time(), "by_code": by_code},
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _fetch_fhps_dividend_map() -> Dict[str, float]:
    """东财分红配送表：现金分红-股息率 → code 映射（一次拉全表，磁盘缓存）。"""
    global _FHPS_MEM
    if isinstance(_FHPS_MEM, dict) and _FHPS_MEM.get("by_code"):
        return dict(_FHPS_MEM["by_code"])
    disk = _read_fhps_map_disk()
    if disk:
        _FHPS_MEM = {"by_code": disk}
        return dict(disk)
    by_code: Dict[str, float] = {}
    try:
        from skills.common.ak_lock import import_akshare

        ak = import_akshare()
        df = ak.stock_fhps_em()
    except Exception:
        _FHPS_MEM = {"by_code": {}}
        return {}
    if df is None or getattr(df, "empty", True):
        _FHPS_MEM = {"by_code": {}}
        return {}
    code_col = "代码" if "代码" in df.columns else None
    dy_col = "现金分红-股息率" if "现金分红-股息率" in df.columns else None
    if not code_col or not dy_col:
        _FHPS_MEM = {"by_code": {}}
        return {}
    for _, row in df.iterrows():
        code = str(row.get(code_col) or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        dy = _to_float(row.get(dy_col))
        if dy is None or dy <= 0:
            continue
        # 同代码多行取较大股息率（近期方案）
        prev = by_code.get(code)
        if prev is None or dy > prev:
            by_code[code] = dy
    _write_fhps_map_disk(by_code)
    _FHPS_MEM = {"by_code": by_code}
    return dict(by_code)


def lookup_fhps_dividend_yield(code: str) -> Optional[float]:
    """查分红表股息率；无则 None。"""
    c = str(code or "").strip().zfill(6)
    if len(c) != 6:
        return None
    return _fetch_fhps_dividend_map().get(c)


def fetch_valuation_pack(code: str) -> Dict[str, Optional[float]]:
    """缓存优先；缺市值/估值时再拉 value_em；缺股息率再补分红表。"""
    cached = read_valuation_cache(code) or {}
    need_val = any(cached.get(k) is None for k in ("pe", "pb", "market_cap"))
    pack: Dict[str, Optional[float]] = dict(cached)
    if need_val or not cached:
        try:
            from skills.fundamentals.engine import fetch_cn_valuation_latest

            raw = fetch_cn_valuation_latest(str(code).zfill(6)) or {}
        except Exception:
            raw = {}
        pe = _to_float(raw.get("pe"))
        pe_ttm = _to_float(raw.get("pe_ttm"))
        if pe is None:
            pe = pe_ttm
        pb = _to_float(raw.get("pb"))
        mcap = _to_float(raw.get("total_mv"))
        if mcap is None:
            mcap = _to_float(raw.get("market_cap"))
        dy = _to_float(raw.get("dv_ttm"))
        if dy is None:
            dy = _to_float(raw.get("dividend_yield"))
        pack = {
            "pe": pe if pe is not None else cached.get("pe"),
            "pb": pb if pb is not None else cached.get("pb"),
            "pe_ttm": (pe_ttm if pe_ttm is not None else pe)
            if (pe_ttm is not None or pe is not None)
            else cached.get("pe_ttm"),
            "market_cap": mcap if mcap is not None else cached.get("market_cap"),
            "dividend_yield": dy if dy is not None else cached.get("dividend_yield"),
        }
    if pack.get("dividend_yield") is None:
        dy2 = lookup_fhps_dividend_yield(code)
        if dy2 is not None:
            pack["dividend_yield"] = dy2
    if any(pack.get(k) is not None for k in ("pe", "pb", "pe_ttm", "market_cap")):
        write_valuation_cache(code, pack)
    return pack


def enrich_fundamentals_metrics(
    code: str, metrics: Optional[dict]
) -> Optional[Dict[str, Any]]:
    """把 pe/pb/市值/股息补进财务 metrics（已有则不覆盖）。"""
    if not metrics and not code:
        return metrics
    out = dict(metrics or {})
    need = any(
        out.get(k) is None
        for k in ("pe", "pb", "pe_ttm", "market_cap", "dividend_yield")
    )
    if not need:
        return out
    pack = fetch_valuation_pack(code)
    if not pack:
        return out or None
    if out.get("pe") is None and pack.get("pe") is not None:
        out["pe"] = pack["pe"]
    if out.get("pe_ttm") is None and pack.get("pe_ttm") is not None:
        out["pe_ttm"] = pack["pe_ttm"]
    if out.get("pb") is None and pack.get("pb") is not None:
        out["pb"] = pack["pb"]
    if out.get("market_cap") is None and pack.get("market_cap") is not None:
        out["market_cap"] = pack["market_cap"]
    if out.get("dividend_yield") is None and pack.get("dividend_yield") is not None:
        out["dividend_yield"] = pack["dividend_yield"]
    return out or None
