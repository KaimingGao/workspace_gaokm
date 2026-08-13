"""分池选股：各组 ŷ（return_model）打分 → 全局按 score 排序 → min_score 过滤 + max_names 截断。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def rank_cluster_pools(
    codes: Optional[List[str]] = None,
    *,
    horizon_days: Optional[int] = None,
    top_n_per_group: Optional[int] = None,
    max_names: Optional[int] = None,
    min_score: Optional[float] = None,
    watching_path: Optional[str] = None,
    persist_book: bool = True,
) -> Dict[str, Any]:
    """live 分池排序：组收益分后跨组按分数排序截断。

    ``top_n_per_group`` 已废弃（保留入参兼容旧 API），不再做组内 Top-N。
    """
    from core.signal.cluster_live import (
        get_cluster_scoring_cfg,
        load_active_cluster_weights,
        oos_failed_cluster_labels,
        save_active_cluster_book,
    )
    from core.signal.config import get_rank_defaults, get_scoring_horizon_days, load_signal_config
    from core.signal.score_display import json_safe_number, selection_min_score
    from core.signal.score_stock import score_stock
    from core.watching_store import read_watching, refresh_watchlist

    cs = get_cluster_scoring_cfg()
    cfg = load_signal_config()
    defaults = get_rank_defaults(cfg)
    # 收益分默认：scoring.min_predicted_score（缺省 +1：ŷ<1% 不入簿）
    floor_disabled = False
    if min_score is None:
        floor = selection_min_score()
        if floor is None:
            min_score = float("-inf")
            floor_disabled = True
        else:
            min_score = float(floor)
    else:
        try:
            min_score = float(min_score)
            floor_disabled = not math.isfinite(min_score)
            if floor_disabled:
                min_score = float("-inf")
        except (TypeError, ValueError):
            min_score = float("-inf")
            floor_disabled = True
    max_n = max(1, min(int(max_names or cs.get("max_names") or 40), 80))
    if horizon_days is None:
        horizon_days = get_scoring_horizon_days(cfg)
    horizon_days = max(1, min(int(horizon_days or 1), 10))
    # 兼容旧调用方：仍回传配置值，但不参与建簿
    top_n_cfg = int(
        top_n_per_group
        if top_n_per_group is not None
        else (cs.get("top_n_per_group") or 10)
    )
    exclude_oos = bool(cs.get("exclude_oos_failed_groups", True))

    active = load_active_cluster_weights()
    if not active or not active.get("code_map"):
        return {
            "success": False,
            "error": "无 live 分组映射（请先 promote）",
            "task": "rank_cluster_pools",
        }

    oos_blocked_labels = set(
        oos_failed_cluster_labels(active=active) if exclude_oos else []
    )

    if codes is None:
        # 优先 live 映射码（与分组成员一致）；否则回退观察池
        mapped_codes = [
            str(c).strip()
            for c in (active.get("code_map") or {}).keys()
            if str(c).strip()
        ]
        if mapped_codes:
            codes = mapped_codes
        else:
            try:
                uni = read_watching(watching_path)
            except FileNotFoundError:
                return {"success": False, "error": "watching.json 不存在"}
            codes = list(uni.get("watchlist") or [])
            if not codes:
                refreshed = refresh_watchlist(uni, path=watching_path)
                codes = list(refreshed.get("watchlist") or [])

    codes = [str(c).strip() for c in (codes or []) if str(c).strip()][:80]
    if not codes:
        return {"success": False, "error": "候选池为空"}

    by_label: Dict[str, List[dict]] = {}
    unmapped: List[dict] = []
    rejected: List[dict] = []
    scored_extra: List[dict] = []  # hard_reject / 未映射 / OOS 阻断，供展示
    mapped_rows: List[dict] = []
    below_min = 0
    oos_blocked_count = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _score_one(raw: str) -> Dict[str, Any]:
        # 与纸面持仓打分一致：跳过基本面；刷簿跳过舆情（避免 N×12s）
        return score_stock(
            raw,
            horizon_days=horizon_days,
            cluster_mode="active",
            skip_fundamentals=True,
            skip_sentiment=True,
        )

    workers = max(1, min(8, len(codes)))
    scored_by_code: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_score_one, raw): raw for raw in codes}
        for fut in as_completed(futs):
            raw = futs[fut]
            try:
                scored_by_code[raw] = fut.result()
            except Exception as e:
                scored_by_code[raw] = {
                    "success": False,
                    "stock_code": raw,
                    "error": str(e),
                }

    for raw in codes:
        result = scored_by_code.get(raw) or {"success": False, "error": "no_result"}
        if not result.get("success"):
            rejected.append({"stock_code": raw, "reason": result.get("error")})
            continue
        item = result.get("signal_item") or {}
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": item.get("reject_reason"),
                    "score": item.get("score"),
                }
            )
            scored_extra.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "score": item.get("score"),
                    "hard_reject": True,
                    "reject_reason": item.get("reject_reason"),
                    "cluster_label": item.get("cluster_label"),
                    "weight_source": item.get("weight_source"),
                    "score_global": item.get("score_global"),
                    "delta_vs_global": item.get("delta_vs_global"),
                    "below_min_score": True,
                }
            )
            continue
        name_u = str(item.get("stock_name") or "").upper()
        if "ST" in name_u or "退" in str(item.get("stock_name") or ""):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": f"ST/退市名过滤（{item.get('stock_name') or item.get('stock_code')}）",
                    "score": item.get("score"),
                }
            )
            continue
        label = item.get("cluster_label") or "_global_fallback"
        if item.get("weight_source", "").startswith("global"):
            unmapped.append(item)
            # 未映射不入选股簿（避免全局权冒充分组）；分数仍可供展示
            scored_extra.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "score": item.get("score"),
                    "cluster_label": None,
                    "weight_source": item.get("weight_source") or "global_fallback",
                    "score_global": item.get("score_global") or item.get("score"),
                    "delta_vs_global": item.get("delta_vs_global"),
                    "below_min_score": False,
                    "unmapped": True,
                }
            )
            continue
        # P0.2：OOS 失败组不进簿（ŷ 仍进 scored_all 供对照）
        if exclude_oos and str(label) in oos_blocked_labels:
            oos_blocked_count += 1
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": f"组 {label} OOS 门禁未过·已排除入簿",
                    "score": item.get("score"),
                    "cluster_label": str(label),
                }
            )
            scored_extra.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "score": item.get("score"),
                    "predicted_score": item.get("predicted_score"),
                    "heuristic_score": item.get("heuristic_score"),
                    "cluster_label": str(label),
                    "cluster_id": item.get("cluster_id"),
                    "weight_source": item.get("weight_source"),
                    "score_global": item.get("score_global"),
                    "delta_vs_global": item.get("delta_vs_global"),
                    "below_min_score": True,
                    "oos_blocked": True,
                    "return_model_source": item.get("return_model_source"),
                    "score_formula_terms": item.get("score_formula_terms"),
                    "sector": item.get("sector"),
                }
            )
            continue
        sc = item.get("predicted_score")
        if sc is None:
            sc = item.get("score")
        try:
            sc_f = float(sc) if sc is not None else None
        except (TypeError, ValueError):
            sc_f = None
        if sc_f is None:
            continue
        below = sc_f < float(min_score)
        if below:
            below_min += 1
        row = {
            "stock_code": item.get("stock_code"),
            "stock_name": item.get("stock_name"),
            "score": sc_f,
            "predicted_score": item.get("predicted_score"),
            "heuristic_score": item.get("heuristic_score"),
            "cluster_label": str(label),
            "cluster_id": item.get("cluster_id"),
            "weight_source": item.get("weight_source"),
            "score_global": item.get("score_global"),
            "delta_vs_global": item.get("delta_vs_global"),
            "below_min_score": below,
            "return_model_source": item.get("return_model_source"),
            "score_formula_terms": item.get("score_formula_terms"),
            "sector": item.get("sector"),
            "score_rem": item.get("score_rem"),
            "predicted_score_rem": item.get("predicted_score_rem"),
            "gap_pct": item.get("gap_pct"),
            "event_prior": item.get("event_prior"),
        }
        mapped_rows.append(row)
        by_label.setdefault(str(label), []).append(row)

    # 组内榜仅供对照展示
    groups_out: List[Dict[str, Any]] = []
    for label in sorted(by_label.keys()):
        members = sorted(
            by_label[label],
            key=lambda x: float(x.get("score") or 0.0),
            reverse=True,
        )
        ranked = []
        for i, it in enumerate(members):
            ranked.append({**it, "rank_in_group": i + 1})
        groups_out.append(
            {
                "label": label,
                "scored_count": len(ranked),
                "eligible_count": sum(
                    1 for r in ranked if not r.get("below_min_score")
                ),
                "ranking": ranked,
            }
        )

    # 选股簿：全局按组权分降序 → min_score 过滤 → max_names 截断
    eligible = [
        dict(r) for r in mapped_rows if not r.get("below_min_score")
    ]
    eligible.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    book = eligible[:max_n]
    for i, b in enumerate(book):
        b["rank"] = i + 1

    scored_all: List[dict] = list(scored_extra) + list(mapped_rows)
    scored_all.sort(
        key=lambda x: (
            0 if x.get("score") is not None else 1,
            -(float(x["score"]) if x.get("score") is not None else 0.0),
        )
    )

    n = len(book)
    w_pct = round(100.0 / n, 4) if n else 0.0
    for b in book:
        b["weight_pct"] = w_pct

    min_score_out = json_safe_number(min_score)
    path = None
    if persist_book:
        path = save_active_cluster_book(
            book,
            meta={
                "version": active.get("version"),
                "horizon_days": horizon_days,
                "min_score": min_score_out,
                "min_score_disabled": floor_disabled or min_score_out is None,
                "max_names": max_n,
                "mode": "cluster_score_global_rank",
                # 兼容旧 status 读取
                "top_n_per_group": top_n_cfg,
                "exclude_oos_failed_groups": exclude_oos,
                "oos_blocked_labels": sorted(oos_blocked_labels),
                "oos_blocked_count": oos_blocked_count,
            },
            scored_all=scored_all,
        )

    return {
        "success": True,
        "task": "rank_cluster_pools",
        "mode": "cluster_score_global_rank",
        "cross_group_rank": True,
        "cluster_version": active.get("version"),
        "top_n_per_group": top_n_cfg,  # 兼容字段；建簿已不再使用
        "max_names": max_n,
        "min_score": min_score_out,
        "min_score_disabled": floor_disabled or min_score_out is None,
        "horizon_days": horizon_days,
        "exclude_oos_failed_groups": exclude_oos,
        "oos_blocked_labels": sorted(oos_blocked_labels),
        "oos_blocked_count": oos_blocked_count,
        "groups": groups_out,
        "book": book,
        "ranking": book,
        "scored_all": scored_all,
        "unmapped_count": len(unmapped),
        "below_min_score_count": below_min,
        "rejected": rejected[:20],
        "book_path": path,
        "note": (
            "分池：组ŷ→剔ST→剔OOS失败组→ŷ≥min_predicted_score（h=1 现网约 +0.35）→全局降序→max_names 截断。"
            "不再做组内 Top-N。低于门槛 / 未映射 / 硬拒绝 / OOS阻断仍进 scored_all 供展示。"
            "不写 signal_config.weights。"
        ),
    }
