"""研究宇宙：日线截面研究用宽名单；与观察池（分钟暖仓 / live）分离。

- 观察池 ``watching``：≤WATCHING_MAX_SIZE（默认 500），分钟暖仓、live 打分、ŷ_* 拟合（实际只数=观察池）
- 模型拟合上限 ``MODEL_FIT_MAX_SIZE``（默认 1000）：请求钳制，不从研究宇宙垫票
- 研究宇宙 ``research_universe``：可更大，仅日线研究（LambdaRank / Alpha158 OOS 等）
  非空时日线研究优先用本名单；空则回退观察池。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from core.io_atomic import atomic_write_json
from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

RESEARCH_UNIVERSE_PATH = os.path.join(DATA_DIR, "research_universe.json")
# 日线研究上限；远大于观察池，但仍防失控
RESEARCH_UNIVERSE_MAX_SIZE = 2000


def default_research_universe() -> Dict[str, Any]:
    return {
        "version": 1,
        "codes": [],
        "max_size": RESEARCH_UNIVERSE_MAX_SIZE,
        "note": (
            "日线研究宇宙（可宽于观察池）。非空时 oo_rank / Alpha158 等日线拟合优先用此名单；"
            "空则回退观察池。分钟暖仓与 live 仍只读 watching，勿把宽名单塞进观察池。"
        ),
    }


def load_research_universe(*, path: Optional[str] = None) -> Dict[str, Any]:
    p = path or RESEARCH_UNIVERSE_PATH
    out = default_research_universe()
    if not os.path.isfile(p):
        return out
    try:
        import json

        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return out
    if isinstance(data, dict):
        out.update(data)
    codes = [str(c).strip() for c in (out.get("codes") or []) if str(c).strip()]
    # 去重保序
    seen = set()
    uniq: List[str] = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    cap = max(8, min(int(out.get("max_size") or RESEARCH_UNIVERSE_MAX_SIZE), RESEARCH_UNIVERSE_MAX_SIZE))
    out["codes"] = uniq[:cap]
    out["max_size"] = cap
    out["count"] = len(out["codes"])
    return out


def save_research_universe(
    payload: dict, *, path: Optional[str] = None
) -> Dict[str, Any]:
    p = path or RESEARCH_UNIVERSE_PATH
    merged = default_research_universe()
    if isinstance(payload, dict):
        merged.update(payload)
    codes = [str(c).strip() for c in (merged.get("codes") or []) if str(c).strip()]
    seen = set()
    uniq: List[str] = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    cap = max(8, min(int(merged.get("max_size") or RESEARCH_UNIVERSE_MAX_SIZE), RESEARCH_UNIVERSE_MAX_SIZE))
    merged["codes"] = uniq[:cap]
    merged["max_size"] = cap
    merged["count"] = len(merged["codes"])
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(p, merged)
    return {"ok": True, "path": p, "universe": merged}


def resolve_research_codes(
    *,
    limit: Optional[int] = None,
    fallback_watching: bool = True,
    watching_codes: Optional[Sequence[str]] = None,
    universe: Optional[dict] = None,
) -> Dict[str, Any]:
    """解析日线研究用股票列表。

    优先 ``research_universe.codes``；空且 ``fallback_watching`` 时用观察池（钳制≤WATCHING_MAX_SIZE）。
    永不驱动分钟暖仓。
    """
    from core.watching.store import WATCHING_MAX_SIZE

    uni = universe if isinstance(universe, dict) else load_research_universe()
    codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    source = "research_universe"
    if not codes and fallback_watching:
        if watching_codes is None:
            try:
                from core.watching.store import read_watching

                watching_codes = list((read_watching() or {}).get("watchlist") or [])
            except Exception:  # noqa: BLE001
                logger.debug("resolve_research_codes: read_watching failed", exc_info=True)
                watching_codes = []
        raw_watch = [str(c).strip() for c in (watching_codes or []) if str(c).strip()]
        seen_w: set = set()
        codes = []
        for c in raw_watch:
            if c in seen_w:
                continue
            seen_w.add(c)
            codes.append(c)
        source = "watching_fallback"
        hard_cap = int(WATCHING_MAX_SIZE)
    else:
        hard_cap = int(uni.get("max_size") or RESEARCH_UNIVERSE_MAX_SIZE)
        hard_cap = max(8, min(hard_cap, RESEARCH_UNIVERSE_MAX_SIZE))

    if limit is not None:
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = hard_cap
        if lim > 0:
            hard_cap = min(hard_cap, lim)

    codes = codes[: max(0, hard_cap)]
    return {
        "ok": True,
        "codes": codes,
        "count": len(codes),
        "source": source,
        "cap": hard_cap,
        "universe": uni,
        "minute_warmup": False,
        "note": (
            "日线研究宇宙；分钟暖仓请用观察池"
            if source == "research_universe"
            else f"研究宇宙为空，已回退观察池（≤{WATCHING_MAX_SIZE}）"
        ),
    }


def resolve_model_fit_codes(
    *,
    watching_limit: Optional[int] = None,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """ŷ_* 拟合用股票列表：显式 codes，否则观察池（不垫 research_universe）。

    上限 ``MODEL_FIT_MAX_SIZE``；实际只数 = min(请求, 上限, 观察池长度)。
    """
    from core.watching.store import MODEL_FIT_MAX_SIZE

    try:
        lim = int(watching_limit) if watching_limit is not None else int(MODEL_FIT_MAX_SIZE)
    except (TypeError, ValueError):
        lim = int(MODEL_FIT_MAX_SIZE)
    lim = max(3, min(lim, int(MODEL_FIT_MAX_SIZE)))

    if codes is not None:
        seen: set = set()
        out: List[str] = []
        for c in codes:
            s = str(c).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        out = out[:lim]
        return {
            "ok": True,
            "codes": out,
            "count": len(out),
            "source": "explicit",
            "cap": lim,
            "minute_warmup": False,
            "note": "显式 codes 拟合宇宙",
        }

    pool = _watching_codes()
    out = pool[:lim]
    return {
        "ok": True,
        "codes": out,
        "count": len(out),
        "source": "watching",
        "cap": lim,
        "minute_warmup": False,
        "note": f"观察池拟合宇宙（≤{lim}）",
    }


def _watching_codes() -> List[str]:
    try:
        from core.watching.store import read_watching

        raw = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001
        logger.debug("research_universe: read_watching failed", exc_info=True)
        return []
    seen = set()
    out: List[str] = []
    for c in raw:
        code = str(c).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def build_research_universe_board(
    *,
    include_coverage: bool = False,
    coverage_cap: int = 400,
) -> Dict[str, Any]:
    """看板载荷：名单 KPI + 与观察池交并 + 可选日线覆盖摘要。"""
    from core.watching.store import WATCHING_MAX_SIZE

    uni = load_research_universe()
    resolved = resolve_research_codes(universe=uni)
    ru_codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    watch = _watching_codes()
    ru_set = set(ru_codes)
    watch_set = set(watch)
    overlap = sorted(ru_set & watch_set)
    only_research = [c for c in ru_codes if c not in watch_set]
    only_watching = [c for c in watch if c not in ru_set]

    coverage: Optional[Dict[str, Any]] = None
    if include_coverage:
        sample = list(resolved.get("codes") or [])[
            : max(1, min(int(coverage_cap or 400), 800))
        ]
        try:
            from core.data.coverage import build_data_coverage

            cov = build_data_coverage(codes=sample, include_paper=False) or {}
            coverage = {
                "sampled": len(sample),
                "total_resolved": int(resolved.get("count") or 0),
                "good": int((cov.get("levels") or {}).get("good") or 0),
                "thin": int((cov.get("levels") or {}).get("thin") or 0),
                "empty": int((cov.get("levels") or {}).get("empty") or 0),
                "stale": int(cov.get("stale_count") or 0),
                "missing": int(cov.get("missing") or 0),
                "coverage": cov.get("coverage"),
                "truncated": len(sample) < int(resolved.get("count") or 0),
            }
        except Exception:  # noqa: BLE001
            logger.debug("research_universe coverage failed", exc_info=True)
            coverage = {"ok": False, "error": "coverage_failed"}

    return {
        "ok": True,
        "universe": uni,
        "resolved": {
            "count": resolved.get("count"),
            "source": resolved.get("source"),
            "cap": resolved.get("cap"),
            "note": resolved.get("note"),
            "minute_warmup": False,
            "codes": list(resolved.get("codes") or []),
        },
        "watching": {
            "count": len(watch),
            "max_size": int(WATCHING_MAX_SIZE),
        },
        "overlap": {
            "count": len(overlap),
            "only_research": len(only_research),
            "only_watching": len(only_watching),
            "overlap_sample": overlap[:40],
            "only_research_sample": only_research[:40],
        },
        "coverage": coverage,
        "note": uni.get("note") or "",
    }


def sync_research_universe_from_watching(
    *, path: Optional[str] = None
) -> Dict[str, Any]:
    """用当前观察池覆盖写入研究宇宙（仍不触发分钟暖仓）。"""
    watch = _watching_codes()
    return save_research_universe({"codes": watch}, path=path)


def _cached_daily_codes(*, market: str = "CN") -> List[str]:
    """本地日 K 仓代码（研究宽宇宙来源；可 ~1200）。"""
    try:
        from core.store import list_cached_symbols

        rows = list_cached_symbols(market=market) or []
    except Exception:  # noqa: BLE001
        logger.debug("research_universe: list_cached_symbols failed", exc_info=True)
        return []
    seen = set()
    out: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            c = str(row.get("code") or row.get("stock_code") or "").strip()
        else:
            c = str(row or "").strip()
        if not c:
            continue
        c = c.split(".")[0]
        # 跳过指数类（如 399300）——研究截面默认个股
        if c.startswith("399") or c.startswith("000000"):
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    out.sort()
    return out


def sync_research_universe_from_cached_daily(
    *,
    path: Optional[str] = None,
    market: str = "CN",
    max_codes: Optional[int] = None,
) -> Dict[str, Any]:
    """用本地日 K 缓存仓覆盖写入研究宇宙（宽名单，默认可至 2000）。

    不改观察池、不触发分钟暖仓。这是「1200 票研究宇宙」的主入口。
    """
    codes = _cached_daily_codes(market=market)
    if max_codes is not None:
        try:
            cap = max(8, int(max_codes))
        except (TypeError, ValueError):
            cap = RESEARCH_UNIVERSE_MAX_SIZE
        codes = codes[: min(cap, RESEARCH_UNIVERSE_MAX_SIZE)]
    saved = save_research_universe(
        {
            "codes": codes,
            "note": (
                f"由本地日 K 仓灌入（market={market}，n={len(codes)}）；"
                "仅日线研究；分钟暖仓 / live 仍只读 watching。"
            ),
        },
        path=path,
    )
    saved["source"] = "cached_daily"
    saved["cached_count"] = len(codes)
    return saved
