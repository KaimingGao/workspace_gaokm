"""分池横截面：各组组权打分 → 组内 Top-N → 合并簿（禁止跨组总榜）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def rank_cluster_pools(
    codes: Optional[List[str]] = None,
    *,
    horizon_days: int = 3,
    top_n_per_group: Optional[int] = None,
    max_names: Optional[int] = None,
    min_score: Optional[float] = None,
    watching_path: Optional[str] = None,
    persist_book: bool = True,
) -> Dict[str, Any]:
    """live 分池排序：仅组内序，再 concat 合成候选。"""
    from core.signal.cluster_live import (
        get_cluster_scoring_cfg,
        load_active_cluster_weights,
        save_active_cluster_book,
    )
    from core.signal.config import get_rank_defaults, load_signal_config
    from core.signal.score_stock import score_stock
    from core.watching_store import read_watching, refresh_watchlist

    cs = get_cluster_scoring_cfg()
    cfg = load_signal_config()
    defaults = get_rank_defaults(cfg)
    if min_score is None:
        min_score = defaults["min_score"]
    top_n = int(top_n_per_group or cs["top_n_per_group"])
    max_n = int(max_names or cs["max_names"])
    horizon_days = max(1, min(int(horizon_days or 3), 3))

    active = load_active_cluster_weights()
    if not active or not active.get("code_map"):
        return {
            "success": False,
            "error": "无 live 分组映射（请先 promote）",
            "task": "rank_cluster_pools",
        }

    if codes is None:
        try:
            uni = read_watching(watching_path)
        except FileNotFoundError:
            return {"success": False, "error": "watching.json 不存在"}
        codes = list(uni.get("watchlist") or [])
        if not codes:
            refreshed = refresh_watchlist(uni, path=watching_path)
            codes = list(refreshed.get("watchlist") or [])

    codes = [str(c).strip() for c in (codes or []) if str(c).strip()][:50]
    if not codes:
        return {"success": False, "error": "候选池为空"}

    # 强制用组权打分（active 语义）
    by_label: Dict[str, List[dict]] = {}
    unmapped: List[dict] = []
    rejected: List[dict] = []

    for raw in codes:
        result = score_stock(
            raw,
            horizon_days=horizon_days,
            cluster_mode="active",
        )
        if not result.get("success"):
            rejected.append({"stock_code": raw, "reason": result.get("error")})
            continue
        item = result.get("signal_item") or {}
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": item.get("reject_reason"),
                }
            )
            continue
        label = item.get("cluster_label") or "_global_fallback"
        if item.get("weight_source", "").startswith("global"):
            unmapped.append(item)
            # 未映射不入分池簿（避免全局权冒充分组）
            continue
        sc = item.get("score")
        try:
            if sc is None or float(sc) < float(min_score):
                continue
        except (TypeError, ValueError):
            continue
        by_label.setdefault(str(label), []).append(item)

    groups_out: List[Dict[str, Any]] = []
    book: List[dict] = []
    for label in sorted(by_label.keys()):
        members = by_label[label]
        members.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        ranked = []
        for i, it in enumerate(members):
            row = {
                "stock_code": it.get("stock_code"),
                "stock_name": it.get("stock_name"),
                "score": it.get("score"),
                "rank_in_group": i + 1,
                "cluster_label": label,
                "cluster_id": it.get("cluster_id"),
                "weight_source": it.get("weight_source"),
                "score_global": it.get("score_global"),
                "delta_vs_global": it.get("delta_vs_global"),
            }
            ranked.append(row)
        groups_out.append(
            {
                "label": label,
                "scored_count": len(ranked),
                "ranking": ranked,
            }
        )
        for row in ranked[:top_n]:
            if len(book) >= max_n:
                break
            book.append(row)
        if len(book) >= max_n:
            break

    # 等权提示
    n = len(book)
    w_pct = round(100.0 / n, 4) if n else 0.0
    for b in book:
        b["weight_pct"] = w_pct

    path = None
    if persist_book:
        path = save_active_cluster_book(
            book,
            meta={
                "version": active.get("version"),
                "top_n_per_group": top_n,
                "horizon_days": horizon_days,
                "min_score": min_score,
            },
        )

    return {
        "success": True,
        "task": "rank_cluster_pools",
        "mode": "within_group_then_merge",
        "cross_group_rank": False,
        "cluster_version": active.get("version"),
        "top_n_per_group": top_n,
        "max_names": max_n,
        "min_score": min_score,
        "groups": groups_out,
        "book": book,
        "ranking": book,  # 兼容 rebalance 消费「排名列表」
        "unmapped_count": len(unmapped),
        "rejected": rejected[:20],
        "book_path": path,
        "note": (
            "分池：组权打分→组内 Top-N→合并；无跨组总榜。"
            "未映射票不进簿。不写 signal_config.weights。"
        ),
    }
