"""研究侧多权打分：按 code_map 逐票 overlay 组权 → 仅组内排序（不进 live）。

与统一全局权排名对照；**不做跨组总榜**（分数不可跨组直接比）。
"""

from __future__ import annotations

import json
import os
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
        return {"success": False, "stock_code": code, "error": "无日线"}
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
        return {"success": False, "stock_code": code, "error": str(exc)}
    return {
        "success": True,
        "stock_code": code,
        "stock_name": (quote or {}).get("stock_name") or code,
        "score": scored.get("score"),
        "hard_reject": bool(scored.get("hard_reject")),
        "reject_reason": scored.get("reject_reason"),
    }


def score_by_code_map(
    code_map: Dict[str, Any],
    bars_by_code: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    quotes_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """对 code_map 中每只票用其组权打分，并按 cluster 组内排序。

    同时用全局权打分得到 ``score_global`` / ``delta_vs_global`` 对照。
    """
    from core.signal.config import load_signal_config, signal_config_overlay

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    quotes_by_code = quotes_by_code or {}
    cfg_global = load_signal_config()

    # 按组聚合成员
    by_label: Dict[str, List[str]] = {}
    meta_by_code: Dict[str, Dict[str, Any]] = {}
    for raw_code, meta in (code_map or {}).items():
        code = str(raw_code).strip()
        if not code or not isinstance(meta, dict):
            continue
        weights = meta.get("weights")
        if not isinstance(weights, dict) or not weights:
            continue
        label = str(meta.get("cluster_label") or meta.get("label") or "?")
        by_label.setdefault(label, []).append(code)
        meta_by_code[code] = {
            "cluster_id": meta.get("cluster_id"),
            "cluster_label": label,
            "weights": {str(k): float(v) for k, v in weights.items()
                        if _is_num(v)},
            "oos_passed": meta.get("oos_passed"),
        }

    if not meta_by_code:
        return {
            "success": False,
            "error": "code_map 无可用组权",
            "task": "cluster_multi_score",
        }

    # 全局权基线
    global_scores: Dict[str, float] = {}
    for code in meta_by_code:
        row = _score_one(
            code,
            bars_by_code.get(code) or [],
            horizon_days=horizon_days,
            quote=quotes_by_code.get(code),
            config=cfg_global,
        )
        if row.get("success") and not row.get("hard_reject"):
            try:
                global_scores[code] = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                pass

    groups: List[Dict[str, Any]] = []
    flat: List[Dict[str, Any]] = []

    for label in sorted(by_label.keys()):
        codes = by_label[label]
        w0 = meta_by_code[codes[0]]["weights"]
        rows: List[Dict[str, Any]] = []
        for code in codes:
            w = meta_by_code[code]["weights"]
            with signal_config_overlay({"weights": w}):
                cfg = load_signal_config()
                row = _score_one(
                    code,
                    bars_by_code.get(code) or [],
                    horizon_days=horizon_days,
                    quote=quotes_by_code.get(code),
                    config=cfg,
                )
            if not row.get("success") or row.get("hard_reject"):
                continue
            try:
                sc = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                continue
            gsc = global_scores.get(code)
            delta = round(sc - gsc, 3) if gsc is not None else None
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": row.get("stock_name") or code,
                    "score": round(sc, 3),
                    "score_global": round(gsc, 3) if gsc is not None else None,
                    "delta_vs_global": delta,
                    "cluster_label": label,
                    "cluster_id": meta_by_code[code].get("cluster_id"),
                    "score_mode": "code_map_weights",
                }
            )

        rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        for i, r in enumerate(rows):
            r["rank_in_group"] = i + 1
            flat.append(r)
        groups.append(
            {
                "label": label,
                "cluster_id": meta_by_code[codes[0]].get("cluster_id"),
                "member_count": len(codes),
                "scored_count": len(rows),
                "ranking": rows,
                "weights_top": dict(
                    sorted(
                        ((k, round(float(v), 3)) for k, v in w0.items()),
                        key=lambda kv: kv[1],
                        reverse=True,
                    )[:5]
                ),
            }
        )

    return {
        "success": True,
        "task": "cluster_multi_score",
        "mode": "per_code_map_within_group",
        "horizon_days": horizon_days,
        "mapped_count": len(meta_by_code),
        "scored_count": len(flat),
        "groups": groups,
        "flat": flat,
        "cross_group_rank": False,
        "note": (
            "按 code_map 组权逐票打分，仅组内排序；"
            "不做跨组总榜（分数不可跨组直接比）。"
            "研究探针，不写 signal_config，不进 live。"
        ),
    }


def _is_num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def attach_cluster_multi_score(
    report: Dict[str, Any],
    bars_by_code: Dict[str, List[dict]],
    *,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    horizon_days: int = 3,
    run_multi_score: bool = True,
    persist_artifact: bool = True,
) -> Dict[str, Any]:
    """挂 multi_score；可选把 pool_artifact 落到 reports 供复打。"""
    if not run_multi_score or not report.get("success"):
        report["multi_score"] = {
            "success": False,
            "skipped": True,
            "reason": "gate_disabled" if not run_multi_score else "report_failed",
        }
        return report

    art = report.get("pool_artifact") or {}
    code_map = art.get("code_map") if art.get("success") else None
    if not code_map:
        report["multi_score"] = {
            "success": False,
            "skipped": True,
            "reason": "code_map_missing",
        }
        return report

    out = score_by_code_map(
        code_map,
        bars_by_code,
        horizon_days=horizon_days,
        quotes_by_code=quotes_by_code,
    )
    report["multi_score"] = out

    if persist_artifact and art.get("success"):
        path = persist_pool_artifact(art)
        if path:
            report["pool_artifact"]["persisted_path"] = path
            out["artifact_path"] = path

    note = str(report.get("note") or "")
    if "多权打分" not in note:
        report["note"] = note + " 已附 code_map 多权打分（仅组内序）。"
    return report


def persist_pool_artifact(artifact: Dict[str, Any]) -> Optional[str]:
    """写入 ``data/reports/last_cluster_pool_artifact.json`` 供复打。"""
    try:
        from core.paths import QUANT_REPORTS_DIR

        os.makedirs(QUANT_REPORTS_DIR, exist_ok=True)
        path = os.path.join(QUANT_REPORTS_DIR, "last_cluster_pool_artifact.json")
        slim = {
            "success": True,
            "schema_version": artifact.get("schema_version"),
            "created_at": artifact.get("created_at"),
            "n_clusters": artifact.get("n_clusters"),
            "n_mapped_codes": artifact.get("n_mapped_codes"),
            "code_map": artifact.get("code_map"),
            "clusters": artifact.get("clusters"),
            "pool_book": artifact.get("pool_book"),
            "promote_ready": False,
            "note": "last β-分组映射产物；供多权复打，不进 live",
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def load_persisted_pool_artifact() -> Optional[Dict[str, Any]]:
    try:
        from core.paths import QUANT_REPORTS_DIR

        path = os.path.join(QUANT_REPORTS_DIR, "last_cluster_pool_artifact.json")
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("code_map"):
            return data
    except Exception:
        return None
    return None


def run_multi_score_from_artifact(
    *,
    artifact: Optional[Dict[str, Any]] = None,
    lookback: int = 80,
    horizon_days: int = 3,
    watching_limit: int = 20,
) -> Dict[str, Any]:
    """用归档/传入的 code_map 对研究池（或 map 内代码）复打分。"""
    from core.data_service import bars_and_source_research as bars_and_source, get_quote
    from core.watching_store import read_watching

    art = artifact if isinstance(artifact, dict) else None
    if not art or not art.get("code_map"):
        art = load_persisted_pool_artifact()
    if not art or not art.get("code_map"):
        return {
            "success": False,
            "error": "无可用 code_map（请先跑 β 分组或上传产物）",
            "task": "cluster_multi_score",
        }

    code_map = art["code_map"]
    # 优先打 map 内代码；可与 watching 交集
    map_codes = [str(c) for c in code_map.keys() if str(c).strip()]
    uni = read_watching()
    watch = [str(c) for c in (uni.get("watchlist") or [])]
    # 保持 map 顺序，watching 靠前的优先拉数
    ordered: List[str] = []
    seen = set()
    for c in watch + map_codes:
        if c in code_map and c not in seen:
            seen.add(c)
            ordered.append(c)
    limit = max(1, min(int(watching_limit or 20), 40))
    ordered = ordered[:limit]

    bars_by_code: Dict[str, List[dict]] = {}
    quotes_by_code: Dict[str, dict] = {}
    use_map: Dict[str, Any] = {}
    for code in ordered:
        meta = code_map.get(code)
        if not meta:
            continue
        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        sym_s = str(sym)
        if quote.get("success"):
            quotes_by_code[sym_s] = quote
        bars, _ = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, _ = bars_and_source(sym_s, limit=lookback + 35)
        if not bars:
            continue
        bars_by_code[sym_s] = bars
        use_map[sym_s] = meta

    out = score_by_code_map(
        use_map,
        bars_by_code,
        horizon_days=horizon_days,
        quotes_by_code=quotes_by_code,
    )
    out["artifact_created_at"] = art.get("created_at")
    out["lookback"] = lookback
    out["source"] = "request_artifact" if artifact else "persisted_artifact"
    return out
