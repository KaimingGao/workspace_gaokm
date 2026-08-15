"""倒 U / 分段因子的 raw + 分档基函数（试点）。

替代启发式 0–100 子分进 OLS：让 β 更贴近原始经济量。
默认不替换生产特征集；由 ``scoring.feature_encoding=raw_basis`` 或影子对照启用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# 替换关系：启发式名 → raw/分档名
HEURISTIC_TO_RAW: Dict[str, Tuple[str, ...]] = {
    "momentum": ("mom3_pct", "mom5_pct", "mom_overheat"),
    "volatility": ("atr_pct_raw", "atr_pct_sq", "vol_elevated"),
    "value": ("pe_raw", "value_fair", "value_expensive"),
}

RAW_BASIS_FACTOR_NAMES: Tuple[str, ...] = tuple(
    name for names in HEURISTIC_TO_RAW.values() for name in names
)

# 非 0–100 分制：拟合时豁免 min_std=5
RAW_BASIS_MIN_STD_EXEMPT: Tuple[str, ...] = RAW_BASIS_FACTOR_NAMES


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def extract_momentum_basis(bars: Optional[Sequence[dict]]) -> Dict[str, Optional[float]]:
    from core.signal.factors.momentum import pct_change

    b = list(bars or [])
    mom3 = pct_change(b, min(3, max(0, len(b) - 1))) if len(b) > 1 else None
    mom5 = pct_change(b, min(5, max(0, len(b) - 1))) if len(b) > 5 else mom3
    overheat = None
    if mom3 is not None:
        overheat = 1.0 if float(mom3) > 6.0 else 0.0
    return {
        "mom3_pct": round(mom3, 4) if mom3 is not None else None,
        "mom5_pct": round(mom5, 4) if mom5 is not None else None,
        "mom_overheat": overheat,
    }


def extract_volatility_basis(bars: Optional[Sequence[dict]]) -> Dict[str, Optional[float]]:
    from core.signal.factors.volatility import atr_pct

    atr = atr_pct(list(bars or []), window=5)
    if atr is None:
        return {
            "atr_pct_raw": None,
            "atr_pct_sq": None,
            "vol_elevated": None,
            "omit_sub_score": True,
        }
    a = float(atr)
    return {
        "atr_pct_raw": round(a, 4),
        "atr_pct_sq": round(a * a, 4),
        "vol_elevated": 1.0 if a > 4.5 else 0.0,
    }


def extract_value_basis(
    *,
    fundamentals: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    from core.signal.factors.value import _pick_pe

    pe = _pick_pe(fundamentals)
    if pe is None:
        return {
            "pe_raw": None,
            "value_fair": None,
            "value_expensive": None,
            "omit_sub_score": True,
        }
    p = float(pe)
    return {
        "pe_raw": round(p, 4),
        "value_fair": 1.0 if 8.0 <= p <= 25.0 else 0.0,
        "value_expensive": 1.0 if p > 40.0 else 0.0,
    }


def apply_feature_encoding(
    factor_names: Sequence[str],
    *,
    encoding: Optional[str] = None,
) -> Tuple[str, ...]:
    """heuristic：原样；raw_basis：用 raw+分档替换 momentum/volatility/value。"""
    enc = str(encoding or "heuristic").strip().lower() or "heuristic"
    names = [str(n).strip() for n in factor_names if str(n).strip()]
    if enc in ("", "heuristic", "sub_score", "legacy"):
        return tuple(names)
    if enc not in ("raw_basis", "raw", "basis"):
        return tuple(names)
    out: List[str] = []
    seen = set()
    replaced = set()
    for n in names:
        if n in HEURISTIC_TO_RAW:
            replaced.add(n)
            for r in HEURISTIC_TO_RAW[n]:
                if r not in seen:
                    seen.add(r)
                    out.append(r)
        elif n in RAW_BASIS_FACTOR_NAMES:
            if n not in seen:
                seen.add(n)
                out.append(n)
        else:
            if n not in seen:
                seen.add(n)
                out.append(n)
    # 若名单里本无三启发式，仍确保试点三组进模（研究对照）
    if not replaced:
        for _h, raws in HEURISTIC_TO_RAW.items():
            for r in raws:
                if r not in seen:
                    seen.add(r)
                    out.append(r)
    return tuple(out)


def get_feature_encoding(config: Optional[dict] = None) -> str:
    try:
        if config is None:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        scoring = (config or {}).get("scoring") or {}
        enc = str(scoring.get("feature_encoding") or "heuristic").strip().lower()
        return enc or "heuristic"
    except Exception:
        return "heuristic"


# --- registry compute wrappers ---


def score_mom3_pct(bars, **_kw) -> Tuple[float, dict]:
    d = extract_momentum_basis(bars)
    v = d.get("mom3_pct")
    if v is None:
        return 0.0, {"omit_sub_score": True, **d}
    return float(v), {"omit_sub_score": False, **d}


def score_mom5_pct(bars, **_kw) -> Tuple[float, dict]:
    d = extract_momentum_basis(bars)
    v = d.get("mom5_pct")
    if v is None:
        return 0.0, {"omit_sub_score": True, **d}
    return float(v), {"omit_sub_score": False, **d}


def score_mom_overheat(bars, **_kw) -> Tuple[float, dict]:
    d = extract_momentum_basis(bars)
    v = d.get("mom_overheat")
    if v is None:
        return 0.0, {"omit_sub_score": True, **d}
    return float(v), {"omit_sub_score": False, **d}


def score_atr_pct_raw(bars, **_kw) -> Tuple[float, dict]:
    d = extract_volatility_basis(bars)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["atr_pct_raw"]), d


def score_atr_pct_sq(bars, **_kw) -> Tuple[float, dict]:
    d = extract_volatility_basis(bars)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["atr_pct_sq"]), d


def score_vol_elevated(bars, **_kw) -> Tuple[float, dict]:
    d = extract_volatility_basis(bars)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["vol_elevated"]), d


def score_pe_raw(bars, *, fundamentals=None, **_kw) -> Tuple[float, dict]:
    _ = bars
    d = extract_value_basis(fundamentals=fundamentals)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["pe_raw"]), d


def score_value_fair(bars, *, fundamentals=None, **_kw) -> Tuple[float, dict]:
    _ = bars
    d = extract_value_basis(fundamentals=fundamentals)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["value_fair"]), d


def score_value_expensive(bars, *, fundamentals=None, **_kw) -> Tuple[float, dict]:
    _ = bars
    d = extract_value_basis(fundamentals=fundamentals)
    if d.get("omit_sub_score"):
        return 0.0, d
    return float(d["value_expensive"]), d
