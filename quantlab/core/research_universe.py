"""研究宇宙：可交易严筛后的门票池；观察池须为其子集。

- 观察池 ``watching``：默认 300、硬顶 ``WATCHING_MAX_SIZE``=1600；分钟暖仓、live、ŷ_* 拟合
- 模型拟合上限 ``MODEL_FIT_MAX_SIZE``（默认 1600）：请求钳制，不垫本名单
- 研究宇宙：``tradable_screen`` 严筛后写入；核心入口 ``rebuild_tradable_research_universe``
- Web 单按钮：``POST .../rebuild-and-reconcile``（重建并裁观察池 ⊆ 研究池）
- 观察池扩容/加票须 ⊆ 本名单（``QUANTLAB_WATCHING_SUBSET_OF_RESEARCH``，默认开）
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
        "version": 2,
        "codes": [],
        "max_size": RESEARCH_UNIVERSE_MAX_SIZE,
        "policy_version": "",
        "screened_at": "",
        "screen_stats": {},
        "note": (
            "可交易严筛研究宇宙（观察池须为其子集）。"
            "非空时 alpha_oo 等研究脚本优先用此名单；空则回退观察池。"
            "ŷ_* 枢纽拟合只读 watching。分钟暖仓 / live 只读 watching。"
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
    payload: dict,
    *,
    path: Optional[str] = None,
    apply_screen: bool = True,
) -> Dict[str, Any]:
    """写入研究宇宙。默认 ``apply_screen=True`` 过可交易严筛。

    已筛过的重建路径传 ``apply_screen=False``。测试可关筛。
    """
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

    screen_info: Optional[Dict[str, Any]] = None
    if apply_screen and uniq:
        try:
            from core.universe.tradable_screen import screen_codes

            screened = screen_codes(uniq)
            screen_info = {
                "stats": screened.get("stats"),
                "rejected_count": len(screened.get("rejected") or []),
                "rejected_sample": list(screened.get("rejected") or [])[:40],
                "policy_version": screened.get("policy_version"),
            }
            uniq = list(screened.get("kept") or [])
            merged["policy_version"] = str(screened.get("policy_version") or "")
            merged["screen_stats"] = screened.get("stats") or {}
            if not merged.get("screened_at"):
                from datetime import datetime, timezone

                merged["screened_at"] = (
                    datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                )
            if not str(merged.get("note") or "").startswith("可交易严筛"):
                merged["note"] = (
                    f"可交易严筛写入 policy={merged.get('policy_version')}；"
                    f"保留 {len(uniq)} / 候选 {screen_info['stats'].get('input') if screen_info.get('stats') else len(codes)}。"
                )
        except Exception:
            logger.exception("save_research_universe: screen failed; refusing dirty write")
            raise

    cap = max(8, min(int(merged.get("max_size") or RESEARCH_UNIVERSE_MAX_SIZE), RESEARCH_UNIVERSE_MAX_SIZE))
    merged["codes"] = uniq[:cap]
    merged["max_size"] = cap
    merged["count"] = len(merged["codes"])
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    atomic_write_json(p, merged)
    out: Dict[str, Any] = {"ok": True, "path": p, "universe": merged}
    if screen_info is not None:
        out["screen"] = screen_info
    return out


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
    from core.watching.store import watching_pool_limit

    lim = watching_pool_limit(watching_limit)

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
        "watching_pool_size": len(pool),
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

    from core.universe.policy import watching_subset_enforced

    subset_ok = len(only_watching) == 0
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
            "only_watching_sample": only_watching[:40],
        },
        "watching_subset_ok": subset_ok,
        "watching_subset_enforced": watching_subset_enforced(),
        "policy_version": uni.get("policy_version") or "",
        "screen_stats": uni.get("screen_stats") or {},
        "screened_at": uni.get("screened_at") or "",
        "coverage": coverage,
        "note": uni.get("note") or "",
    }


def research_code_set(*, path: Optional[str] = None) -> set:
    """当前研究宇宙代码集合。"""
    uni = load_research_universe(path=path)
    return {
        str(c).strip()
        for c in (uni.get("codes") or [])
        if str(c).strip()
    }
