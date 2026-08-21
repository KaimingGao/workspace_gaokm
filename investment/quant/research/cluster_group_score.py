"""分组 score：组因子系数 → 收益分 ŷ 组内排序（不写 signal_config）。"""


import logging

logger = logging.getLogger(__name__)
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
        logger.exception('unexpected error in _score_one')
        return {
            "success": False,
            "stock_code": code,
            "error": str(exc),
        }
    return {
        "success": True,
        "stock_code": code,
        "stock_name": (quote or {}).get("stock_name") or code,
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
    """每组用 return_model 算 ŷ → 组内降序。"""
    from core.signal.config import load_signal_config
    from core.signal.return_score import ReturnScoreModel

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    quotes_by_code = quotes_by_code or {}
    cfg_global = load_signal_config()

    group_rankings: List[Dict[str, Any]] = []
    pred_flat: List[Dict[str, Any]] = []

    for cl in clusters or []:
        label = str(cl.get("label") or f"G{(cl.get('cluster_id') or 0) + 1}")
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        ret_model = ReturnScoreModel.from_dict(cl.get("return_model"))
        entry: Dict[str, Any] = {
            "cluster_id": cl.get("cluster_id"),
            "label": label,
            "members": members,
            "member_count": len(members),
            "predicted_ranking": [],
            "has_return_model": ret_model is not None,
        }
        if ret_model is None:
            entry["skipped"] = True
            entry["reason"] = "no_return_model"
            group_rankings.append(entry)
            continue

        rows: List[Dict[str, Any]] = []
        for code in members:
            row = _score_one(
                code,
                bars_by_code.get(code) or [],
                horizon_days=horizon_days,
                quote=quotes_by_code.get(code),
                config=cfg_global,
            )
            if not row.get("success") or row.get("hard_reject"):
                rows.append(
                    {
                        **row,
                        "cluster_label": label,
                        "rank_in_group": None,
                        "score_mode": "group_predicted_score",
                    }
                )
                continue
            row["predicted_score"] = ret_model.predict(row.get("sub_scores") or {})
            rows.append(row)

        ok_rows = [
            r
            for r in rows
            if r.get("success")
            and not r.get("hard_reject")
            and r.get("predicted_score") is not None
        ]
        ok_rows.sort(
            key=lambda r: float(r.get("predicted_score") or 0.0), reverse=True
        )
        pred_ranked: List[Dict[str, Any]] = []
        for i, r in enumerate(ok_rows):
            item = {
                "stock_code": r.get("stock_code"),
                "stock_name": r.get("stock_name"),
                "score": r.get("predicted_score"),
                "predicted_score": r.get("predicted_score"),
                "rank_in_group": i + 1,
                "cluster_label": label,
                "cluster_id": cl.get("cluster_id"),
                "score_mode": "group_predicted_score",
            }
            pred_ranked.append(item)
            pred_flat.append(item)
        entry["predicted_ranking"] = pred_ranked

        for r in rows:
            if r.get("success") and not r.get("hard_reject"):
                continue
            pred_flat.append(
                {
                    "stock_code": r.get("stock_code"),
                    "stock_name": r.get("stock_name"),
                    "score": None,
                    "predicted_score": None,
                    "rank_in_group": None,
                    "cluster_label": label,
                    "cluster_id": cl.get("cluster_id"),
                    "score_mode": "group_predicted_score",
                    "error": r.get("error") or r.get("reject_reason"),
                }
            )
        entry["skipped"] = False
        group_rankings.append(entry)

    pred_global = sorted(
        [r for r in pred_flat if r.get("predicted_score") is not None],
        key=lambda r: float(r.get("predicted_score") or 0.0),
        reverse=True,
    )
    for i, r in enumerate(pred_global):
        r["predicted_global_rank"] = i + 1

    return {
        "success": True,
        "task": "cluster_group_score",
        "mode": "group_factor_coefs_within",
        "horizon_days": horizon_days,
        "groups": group_rankings,
        "predicted_flat": pred_flat,
        "predicted_global_ranking": pred_global,
        "n_groups_scored": sum(1 for g in group_rankings if not g.get("skipped")),
        "n_groups_with_predicted": sum(
            1 for g in group_rankings if g.get("predicted_ranking")
        ),
        "note": "组因子系数(return_model)→收益分 ŷ 组内序；研究探针，不写 signal_config。",
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
    by_label = {g.get("label"): g for g in (out.get("groups") or [])}
    for cl in report.get("clusters") or []:
        label = cl.get("label")
        g = by_label.get(label)
        if g:
            cl["group_ranking"] = g.get("predicted_ranking") or []
            cl["predicted_ranking"] = g.get("predicted_ranking") or []
            cl.pop("heuristic_ranking", None)
    note = str(report.get("note") or "")
    if "收益分" not in note:
        report["note"] = (note + " 已附分组收益分（因子系数→ŷ）组内排序。").strip()
    return report
