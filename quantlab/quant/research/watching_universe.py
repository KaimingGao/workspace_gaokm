"""观察池宇宙截断与合并（bars / 分钟预热等共用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def clamp_watching_limit(value: Any, default: int = 8) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return int(default)
    # 上限对齐观察池 WATCHING_MAX_SIZE（日K/5m / ŷ_* 拟合默认满池 WATCHING_MAX_SIZE）
    from core.watching.store import WATCHING_MAX_SIZE

    return max(3, min(n, int(WATCHING_MAX_SIZE)))


def merge_cluster_universe(
    watchlist: Sequence[Any],
    holdings: Sequence[Any],
    *,
    watching_limit: int = 8,
    universe_mode: str = "watching",
) -> Dict[str, Any]:
    """构建观察池宇宙。

    ``universe_mode=watching``：观察池前 N（``watching_limit``，钳制 3–WATCHING_MAX_SIZE，对齐观察池上限）。
    ``holdings``：仅纸面持仓。
    ``union``：观察池前 N ∪ 全部纸面持仓。
    ``watching_all``：全部观察池（旧行为，显式开启）。
    """
    limit = clamp_watching_limit(watching_limit, 8)
    mode = str(universe_mode or "watching").strip().lower()
    if mode in ("paper", "holding", "holdings_only"):
        mode = "holdings"
    if mode in ("watch", "watchlist", "watching_only"):
        mode = "watching"
    if mode in ("watching_full", "all_watching", "full"):
        mode = "watching_all"

    holdings_codes: List[str] = []
    seen_h: set = set()
    for h in holdings or []:
        if isinstance(h, dict):
            c = str(h.get("stock_code") or "").strip()
        else:
            c = str(h or "").strip()
        if not c or c in seen_h:
            continue
        seen_h.add(c)
        holdings_codes.append(c)

    # 去重保序
    watch_all: List[str] = []
    seen_w: set = set()
    for c in watchlist or []:
        code = str(c).strip()
        if not code or code in seen_w:
            continue
        seen_w.add(code)
        watch_all.append(code)

    if mode == "watching_all":
        watching_codes = list(watch_all)
        code_roles = {
            c: {
                "from_watching": True,
                "from_holdings": c in seen_h,
                "holdings_added": False,
            }
            for c in watching_codes
        }
        return {
            "codes": list(watching_codes),
            "watching_codes": list(watching_codes),
            "holdings_codes": list(holdings_codes),
            "holdings_added": [],
            "watching_limit": len(watching_codes),
            "universe_count": len(watching_codes),
            "universe_mode": "watching_all",
            "code_roles": code_roles,
        }

    if mode == "watching":
        watching_codes = watch_all[:limit]
        code_roles = {
            c: {
                "from_watching": True,
                "from_holdings": c in seen_h,
                "holdings_added": False,
            }
            for c in watching_codes
        }
        return {
            "codes": list(watching_codes),
            "watching_codes": list(watching_codes),
            "holdings_codes": list(holdings_codes),
            "holdings_added": [],
            "watching_limit": limit,
            "universe_count": len(watching_codes),
            "universe_mode": "watching",
            "code_roles": code_roles,
        }

    watching_codes = watch_all[:limit]

    if mode == "holdings":
        code_roles = {
            c: {
                "from_watching": False,
                "from_holdings": True,
                "holdings_added": True,
            }
            for c in holdings_codes
        }
        return {
            "codes": list(holdings_codes),
            "watching_codes": [],
            "holdings_codes": list(holdings_codes),
            "holdings_added": list(holdings_codes),
            "watching_limit": limit,
            "universe_count": len(holdings_codes),
            "universe_mode": "holdings",
            "code_roles": code_roles,
        }

    # union
    watch_set = set(watching_codes)
    holdings_added = [c for c in holdings_codes if c not in watch_set]
    codes: List[str] = []
    seen: set = set()
    code_roles: Dict[str, Dict[str, bool]] = {}
    for c in watching_codes + holdings_codes:
        if c not in code_roles:
            code_roles[c] = {
                "from_watching": False,
                "from_holdings": False,
                "holdings_added": False,
            }
        if c in watch_set:
            code_roles[c]["from_watching"] = True
        if c in seen_h:
            code_roles[c]["from_holdings"] = True
        if c not in watch_set and c in seen_h:
            code_roles[c]["holdings_added"] = True
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)
    return {
        "codes": codes,
        "watching_codes": watching_codes,
        "holdings_codes": holdings_codes,
        "holdings_added": holdings_added,
        "watching_limit": limit,
        "universe_count": len(codes),
        "universe_mode": "union",
        "code_roles": code_roles,
    }


# 兼容别名
merge_watching_universe = merge_cluster_universe
