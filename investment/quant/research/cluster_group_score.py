"""分组 score：每组用本组权重打分，仅组内排序（研究探针，不写盘）。

与全池统一 ``config.weights`` 排名对照；不自动进 live scorer。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _score_one(
    code: str,
    bars: Sequence[dict],
    *,
    horizon_days: int,
    quote: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    from core.signal.scorer import score_bars

    if not bars:
        return {
            "success": False,
            "stock_code": code,
            "error": "无日线",
        }
    try:
        scored = score_bars(
            list(bars),
            horizon_days=horizon_days,
            quote=quote,
            fundamentals=None,
            config=config,
            sentiment=None,
        )
    except Exception as exc:
        return {
            "success": False,
            "stock_code": code,
            "error": str(exc),
        }
    return {
        "success": True,
        "stock_code": code,
        "stock_name": (quote or {}).get("stock_name") or code,
        "score": scored.get("score"),
        "hard_reject": bool(scored.get("hard_reject")),
        "reject_reason": scored.get("reject_reason"),
        "sub_scores": scored.get("sub_scores") or {},
    }


def compute_cluster_group_scores(
    clusters: Sequence[Dict[str, Any]],
    bars_by_code: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    quotes_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """对每个有建议权的组：overlay 组权 → 组员打分 → 组内降序排名。

    同时用当前全局权对同一批代码打分，便于对照「统一排名」。
    """
    from core.signal.config import load_signal_config, signal_config_overlay

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    quotes_by_code = quotes_by_code or {}
    cfg_global = load_signal_config()
    global_weights = dict(cfg_global.get("weights") or {})

    # 全局权：同一宇宙打分（统一排名基线）
    all_codes: List[str] = []
    seen = set()
    for cl in clusters or []:
        for c in cl.get("members") or []:
            code = str(c).strip()
            if code and code not in seen:
                seen.add(code)
                all_codes.append(code)

    global_rows: List[Dict[str, Any]] = []
    for code in all_codes:
        row = _score_one(
            code,
            bars_by_code.get(code) or [],
            horizon_days=horizon_days,
            quote=quotes_by_code.get(code),
            config=cfg_global,
        )
        if row.get("success") and not row.get("hard_reject"):
            global_rows.append(row)
    global_rows.sort(
        key=lambda r: float(r.get("score") or 0.0),
        reverse=True,
    )
    global_rank = {
        str(r["stock_code"]): i + 1 for i, r in enumerate(global_rows)
    }

    group_rankings: List[Dict[str, Any]] = []
    flat: List[Dict[str, Any]] = []

    for cl in clusters or []:
        label = str(cl.get("label") or f"G{(cl.get('cluster_id') or 0) + 1}")
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        sug = cl.get("weight_suggest") or {}
        weights = sug.get("suggested_weights") if sug.get("success") else None
        entry: Dict[str, Any] = {
            "cluster_id": cl.get("cluster_id"),
            "label": label,
            "members": members,
            "member_count": len(members),
            "ranking": [],
        }
        if not weights:
            entry["skipped"] = True
            entry["reason"] = "no_group_weights"
            group_rankings.append(entry)
            continue

        with signal_config_overlay({"weights": weights}):
            cfg = load_signal_config()
            rows: List[Dict[str, Any]] = []
            for code in members:
                row = _score_one(
                    code,
                    bars_by_code.get(code) or [],
                    horizon_days=horizon_days,
                    quote=quotes_by_code.get(code),
                    config=cfg,
                )
                if not row.get("success"):
                    rows.append(
                        {
                            **row,
                            "cluster_label": label,
                            "rank_in_group": None,
                            "global_rank": global_rank.get(code),
                            "score_mode": "group_weights",
                        }
                    )
                    continue
                if row.get("hard_reject"):
                    rows.append(
                        {
                            **row,
                            "cluster_label": label,
                            "rank_in_group": None,
                            "global_rank": global_rank.get(code),
                            "score_mode": "group_weights",
                        }
                    )
                    continue
                rows.append(row)

            ok_rows = [r for r in rows if r.get("success") and not r.get("hard_reject")]
            ok_rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
            ranked: List[Dict[str, Any]] = []
            for i, r in enumerate(ok_rows):
                item = {
                    "stock_code": r.get("stock_code"),
                    "stock_name": r.get("stock_name"),
                    "score": r.get("score"),
                    "rank_in_group": i + 1,
                    "global_rank": global_rank.get(str(r.get("stock_code"))),
                    "cluster_label": label,
                    "cluster_id": cl.get("cluster_id"),
                    "score_mode": "group_weights",
                }
                ranked.append(item)
                flat.append(item)
            # 失败的也挂上
            for r in rows:
                if r.get("success") and not r.get("hard_reject"):
                    continue
                flat.append(
                    {
                        "stock_code": r.get("stock_code"),
                        "stock_name": r.get("stock_name"),
                        "score": None,
                        "rank_in_group": None,
                        "global_rank": global_rank.get(str(r.get("stock_code"))),
                        "cluster_label": label,
                        "cluster_id": cl.get("cluster_id"),
                        "score_mode": "group_weights",
                        "error": r.get("error") or r.get("reject_reason"),
                    }
                )
            entry["ranking"] = ranked
            entry["skipped"] = False
            entry["weights_top"] = dict(
                sorted(
                    ((k, round(float(v), 3)) for k, v in weights.items()),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:5]
            )
        group_rankings.append(entry)

    global_ranking = [
        {
            "stock_code": r.get("stock_code"),
            "stock_name": r.get("stock_name"),
            "score": r.get("score"),
            "global_rank": i + 1,
            "score_mode": "global_weights",
        }
        for i, r in enumerate(global_rows)
    ]

    return {
        "success": True,
        "task": "cluster_group_score",
        "mode": "group_weights_within",
        "horizon_days": horizon_days,
        "global_weights_sample": dict(
            sorted(
                ((k, round(float(v), 3)) for k, v in global_weights.items()),
                key=lambda kv: kv[1],
                reverse=True,
            )[:5]
        ),
        "groups": group_rankings,
        "flat": flat,
        "global_ranking": global_ranking,
        "note": (
            "每组用该组建议权对组员打分并组内排序；"
            "global_ranking 为同一批票用当前全局权的统一排名对照。"
            "研究探针，不写 signal_config，不进 live。"
        ),
    }


def attach_cluster_group_scores(
    report: Dict[str, Any],
    bars_by_code: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    run_group_score: bool = True,
) -> Dict[str, Any]:
    """把分组 score 结果挂到 β 分组报告上。"""
    if not run_group_score or not report.get("success"):
        report["group_scores"] = {
            "success": False,
            "skipped": True,
            "reason": "gate_disabled" if not run_group_score else "report_failed",
        }
        return report
    out = compute_cluster_group_scores(
        report.get("clusters") or [],
        bars_by_code,
        horizon_days=horizon_days,
        quotes_by_code=quotes_by_code,
    )
    report["group_scores"] = out
    # 同步到各 cluster 节点，方便 UI
    by_label = {g.get("label"): g for g in (out.get("groups") or [])}
    for cl in report.get("clusters") or []:
        label = cl.get("label")
        g = by_label.get(label)
        if g:
            cl["group_ranking"] = g.get("ranking") or []
    note = str(report.get("note") or "")
    if "分组 score" not in note:
        report["note"] = note + " 已附分组 score / 组内排序（对照全局权统一排名）。"
    return report
