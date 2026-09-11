"""分池研究产物：code→cluster→因子系数(return_model) 映射 + 纸面调仓预演。

真源为组 OLS 收益分系数；weights 仅由 |β| 派生（兼容旧消费者）。
不写 signal_config。
"""

import logging

logger = logging.getLogger(__name__)
import os
from typing import Any, Dict, List, Optional, Sequence

from core.numbers import now_iso_utc
from core.research.oos_slim import slim_oos_gate
from core.signal.factors.meta.coefs import (
    display_weights_from_return_model,
    has_factor_coefficients,
)

SCHEMA_VERSION = 1


def _cluster_weights(cluster: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """优先 |β| 派生；无 return_model 时回退旧 weight_suggest。"""
    rm = cluster.get("return_model")
    derived = display_weights_from_return_model(rm if isinstance(rm, dict) else None)
    if derived:
        return derived
    sug = cluster.get("weight_suggest") or {}
    if not sug.get("success"):
        return None
    raw = sug.get("suggested_weights") or {}
    if not isinstance(raw, dict) or not raw:
        return None
    out: Dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = round(float(v), 6)
        except (TypeError, ValueError):
            continue
    return out or None


def _slim_oos_gate(gate: Any) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias; implementation in ``core.research.oos_slim``."""
    return slim_oos_gate(gate)


def build_pool_artifact(report: Dict[str, Any]) -> Dict[str, Any]:
    """从 β 分组报告抽出可归档映射产物（真源=return_model）。"""
    clusters_out: List[Dict[str, Any]] = []
    code_map: Dict[str, Dict[str, Any]] = {}

    for cl in report.get("clusters") or []:
        rm = cl.get("return_model") if isinstance(cl.get("return_model"), dict) else None
        rm_research = (
            cl.get("return_model_research")
            if isinstance(cl.get("return_model_research"), dict)
            else None
        )
        weights = _cluster_weights(cl)
        label = str(cl.get("label") or f"G{(cl.get('cluster_id') or 0) + 1}")
        members = [str(m).strip() for m in (cl.get("members") or []) if str(m).strip()]
        gate = cl.get("oos_gate") or {}
        gate_slim = _slim_oos_gate(gate)
        oos_passed = bool(
            gate.get("ok") and gate.get("passed") and not gate.get("skipped")
        )
        entry = {
            "cluster_id": cl.get("cluster_id"),
            "label": label,
            "members": members,
            "member_count": len(members),
            "return_model": rm,
            "return_model_research": rm_research,
            "weights": weights,
            "weights_derived_from_beta": bool(
                rm and has_factor_coefficients(rm) and weights
            ),
            "oos_gate": gate_slim,
            "oos_passed": oos_passed,
            "fit_tier": str(cl.get("fit_tier") or "").strip().upper() or None,
            "fit_tier_label": cl.get("fit_tier_label"),
            "fit_tier_reason": cl.get("fit_tier_reason"),
            "singleton": bool(cl.get("singleton")),
        }
        if entry["fit_tier"] not in ("A", "B", "C"):
            entry.pop("fit_tier", None)
            entry.pop("fit_tier_label", None)
            entry.pop("fit_tier_reason", None)
        clusters_out.append(entry)
        cmap_entry = {
            "cluster_id": cl.get("cluster_id"),
            "cluster_label": label,
            "return_model": rm,
            "return_model_research": rm_research,
            "weights": weights,
            "oos_passed": entry["oos_passed"],
        }
        if entry.get("fit_tier"):
            cmap_entry["fit_tier"] = entry["fit_tier"]
        for code in members:
            code_map[code] = dict(cmap_entry)

    pm = report.get("pool_merge") or {}
    book_wrap = (pm.get("book") or {}) if isinstance(pm, dict) else {}
    book = list(book_wrap.get("book") or []) if book_wrap.get("success") else []
    bt = pm.get("backtest") if isinstance(pm, dict) else None
    bt_summary = None
    if isinstance(bt, dict) and bt.get("success"):
        bt_summary = {
            "metrics": bt.get("metrics"),
            "compare": bt.get("compare"),
            "top_n_per_group": bt.get("top_n_per_group"),
            "top_k_global": bt.get("top_k_global"),
            "rebalance_count": bt.get("rebalance_count"),
        }

    for p in book:
        code = str(p.get("stock_code") or "").strip()
        if not code or code in code_map:
            continue
        code_map[code] = {
            "cluster_id": p.get("cluster_id"),
            "cluster_label": p.get("cluster_label"),
            "return_model": None,
            "weights": None,
            "oos_passed": None,
            "from_book_only": True,
        }

    n_with_coefs = sum(
        1
        for m in code_map.values()
        if isinstance(m, dict) and has_factor_coefficients(m.get("return_model"))
    )

    return {
        "success": True,
        "task": "cluster_pool_artifact",
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso_utc(),
        "promote_ready": False,
        "n_clusters": len(clusters_out),
        "n_mapped_codes": len(code_map),
        "n_codes_with_coefs": n_with_coefs,
        "lookback": report.get("lookback"),
        "horizon_days": report.get("horizon_days"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "y_spec": report.get("y_spec"),
        "sample_fingerprint": report.get("sample_fingerprint"),
        "respect_regime": report.get("respect_regime"),
        "collinearity_policy": report.get("collinearity_policy"),
        "select_ridge": report.get("select_ridge"),
        "stock_count": report.get("stock_count"),
        "clusters": clusters_out,
        "code_map": code_map,
        "pool_book": book,
        "pool_book_meta": {
            "top_n_per_group": book_wrap.get("top_n_per_group"),
            "name_count": book_wrap.get("name_count"),
            "vs_global_top": book_wrap.get("vs_global_top"),
        },
        "backtest_summary": bt_summary,
        "preferred_cluster_label": (report.get("preferred_cluster") or {}).get("label"),
        "apply_note": (
            "研究归档：code→cluster→因子系数(return_model)；weights 由 |β| 派生。"
            "不写 signal_config；纸面调仓须单独预演；promote_ready 恒否。"
        ),
        "note": "人审产物；因子系数=组 OLS β→收益分；不进 live 须 promote。",
        "track": "B0-B5",
    }


def build_research_scoring_artifact(artifact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """同一分组：把打分真源换成研究套 β。缺研究系数则该组不打分（不回退 live）。"""
    import copy

    from core.signal.factors.meta.coefs import has_factor_coefficients

    if not isinstance(artifact, dict):
        return None
    doc = copy.deepcopy(artifact)
    n = 0
    by_label: Dict[str, Any] = {}
    for cl in doc.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        rm_r = cl.get("return_model_research")
        if has_factor_coefficients(rm_r if isinstance(rm_r, dict) else None):
            cl["return_model"] = rm_r
            n += 1
            lab = str(cl.get("label") or "")
            if lab:
                by_label[lab] = rm_r
        else:
            cl["return_model"] = None
        cl.pop("return_model_research", None)
    for meta in (doc.get("code_map") or {}).values():
        if not isinstance(meta, dict):
            continue
        rm_r = meta.get("return_model_research")
        lab = str(meta.get("cluster_label") or meta.get("label") or "")
        if not has_factor_coefficients(rm_r if isinstance(rm_r, dict) else None):
            rm_r = by_label.get(lab)
        if has_factor_coefficients(rm_r if isinstance(rm_r, dict) else None):
            meta["return_model"] = rm_r
            n += 1
        else:
            meta["return_model"] = None
        meta.pop("return_model_research", None)
    if n < 1:
        return None
    doc["model_role"] = "research"
    doc["note"] = "研究套组 β（Holdout）；回测加载；不回退 live。"
    return doc


def assign_holdings_to_clusters(
    code_map: Dict[str, Any],
    holdings: Sequence[Dict[str, Any]],
    *,
    universe_codes: Optional[Sequence[str]] = None,
    skipped: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """把纸面持仓票映射到 β 分组（研究枢纽定位②）。

    - 在 code_map 中 → 已入组（weights 可为空）
    - skipped 含 β离群 → unmapped，reason=β离群未入簇
    - 在宇宙 / skipped 但未入簇 → unmapped，reason=数据不足未入簇
    - 不在宇宙 → unmapped，reason=未纳入本次宇宙
    """
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    assigned: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []
    cmap = code_map or {}
    uni_set = {
        str(c).strip() for c in (universe_codes or []) if str(c).strip()
    }
    skip_reason: Dict[str, str] = {}
    for row in skipped or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("code") or row.get("stock_code") or "").strip()
        if not c:
            continue
        skip_reason[c] = str(row.get("reason") or "数据不足未入簇")

    for h in holdings or []:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        name = h.get("stock_name") or code
        meta = cmap.get(code) if isinstance(cmap.get(code), dict) else None
        row = {
            "stock_code": code,
            "stock_name": name,
            "shares": h.get("shares"),
        }
        if meta and meta.get("cluster_label"):
            label = str(meta.get("cluster_label") or meta.get("label") or "?")
            row.update(
                {
                    "cluster_label": label,
                    "cluster_id": meta.get("cluster_id"),
                    "mapped": True,
                    "oos_passed": meta.get("oos_passed"),
                    "has_weights": bool(meta.get("weights")),
                }
            )
            by_group.setdefault(label, []).append(row)
            assigned.append(row)
            continue

        # 未入簇
        row["cluster_label"] = None
        row["mapped"] = False
        reason_raw = skip_reason.get(code) or ""
        if "β离群" in reason_raw or "离群" in reason_raw:
            row["reason"] = reason_raw if "β离群" in reason_raw else "β离群未入簇"
        elif code in skip_reason:
            row["reason"] = f"数据不足未入簇（{skip_reason[code]}）"
        elif uni_set and code in uni_set:
            row["reason"] = "数据不足未入簇"
        elif uni_set:
            row["reason"] = "未纳入本次宇宙"
        else:
            row["reason"] = "不在本次 β 分组宇宙"
        unmapped.append(row)

    groups_out = [
        {
            "label": lab,
            "holdings": rows,
            "count": len(rows),
        }
        for lab, rows in sorted(by_group.items(), key=lambda kv: kv[0])
    ]
    n_hold = len(assigned) + len(unmapped)
    n_beta = sum(
        1 for u in unmapped if "β离群" in str(u.get("reason") or "")
    )
    n_data = sum(
        1
        for u in unmapped
        if str(u.get("reason") or "").startswith("数据不足")
    )
    n_out = len(unmapped) - n_data - n_beta
    note_bits = [
        f"纸面持仓 {n_hold} 只",
        f"已入组 {len(assigned)}",
    ]
    if n_beta:
        note_bits.append(f"β离群未入簇 {n_beta}")
    if n_data:
        note_bits.append(f"数据不足未入簇 {n_data}")
    if n_out:
        note_bits.append(f"未纳入宇宙 {n_out}")
    return {
        "success": True,
        "task": "holdings_cluster_assign",
        "holding_count": n_hold,
        "mapped_count": len(assigned),
        "unmapped_count": len(unmapped),
        "unmapped_beta_outlier_count": n_beta,
        "unmapped_data_count": n_data,
        "unmapped_universe_count": n_out,
        "groups": groups_out,
        "unmapped": unmapped,
        "note": " · ".join(note_bits),
    }


def intent_preview_vs_holdings(
    book: Sequence[Dict[str, Any]],
    holdings: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """相对当前纸面持仓的结构意图（不拉行情、不改账）。"""
    book_codes: List[str] = []
    seen = set()
    for p in book or []:
        code = str(p.get("stock_code") or "").strip()
        if code and code not in seen:
            seen.add(code)
            book_codes.append(code)
    hold_codes = [
        str(h.get("stock_code") or "").strip()
        for h in (holdings or [])
        if str(h.get("stock_code") or "").strip()
    ]
    hold_set = set(hold_codes)
    book_set = set(book_codes)
    return {
        "success": True,
        "book_codes": book_codes,
        "hold_codes": hold_codes,
        "would_keep": sorted(hold_set & book_set),
        "would_sell": sorted(hold_set - book_set),
        "would_buy": sorted(book_set - hold_set),
        "note": "结构对照：目标为分池候选簿；未模拟成交价与风控。",
    }


def preview_paper_pool_rebalance(
    book: Sequence[Dict[str, Any]],
    *,
    paper_path: Optional[str] = None,
    top_k: Optional[int] = None,
    dry_run: bool = True,
    confirm: bool = False,
    artifact: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """分池簿→纸面调仓已停用；请改用 Follow 观察池 rank_lots。"""
    _ = book, paper_path, top_k, dry_run, confirm, artifact
    return {
        "success": False,
        "ok": False,
        "deprecated": True,
        "dry_run": bool(dry_run) and not bool(confirm),
        "confirmed": False,
        "error": "分池簿纸面调仓已停用；请到交易执行页用观察池 rank_lots 预演/确认",
        "task": "cluster_paper_preview",
        "redirect": "/follow",
    }


def attach_cluster_pool_artifact(
    report: Dict[str, Any],
    *,
    run_artifact: bool = True,
    include_intent: bool = True,
) -> Dict[str, Any]:
    """挂映射产物；可选附带相对纸面持仓的结构意图（不模拟成交）。"""
    if not run_artifact or not report.get("success"):
        report["pool_artifact"] = {
            "success": False,
            "skipped": True,
            "reason": "gate_disabled" if not run_artifact else "report_failed",
        }
        return report

    pm = report.get("pool_merge") or {}
    if not pm.get("success"):
        report["pool_artifact"] = {
            "success": False,
            "skipped": True,
            "reason": "pool_merge_missing",
        }
        return report

    art = build_pool_artifact(report)
    holdings: List[dict] = []
    if include_intent:
        try:
            from core.paper import load_paper
            from core.paths import PAPER_PATH

            if os.path.isfile(PAPER_PATH):
                holdings = load_paper(PAPER_PATH).get("holdings") or []
            art["intent_preview"] = intent_preview_vs_holdings(
                art.get("pool_book") or [], holdings
            )
        except Exception as exc:
            logger.exception('unexpected error in attach_cluster_pool_artifact')
            art["intent_preview"] = {
                "success": False,
                "error": str(exc),
            }

    # 枢纽定位②：纸面持仓 → 分组
    try:
        uni_codes = report.get("universe_codes") or []
        if not uni_codes:
            # 回退：聚类成员 + skipped
            uni_codes = []
            for cl in report.get("clusters") or []:
                uni_codes.extend(
                    str(m).strip()
                    for m in (cl.get("members") or [])
                    if str(m).strip()
                )
            for sk in report.get("skipped") or []:
                if isinstance(sk, dict):
                    c = str(sk.get("code") or "").strip()
                    if c:
                        uni_codes.append(c)
        assign = assign_holdings_to_clusters(
            art.get("code_map") or {},
            holdings,
            universe_codes=uni_codes,
            skipped=report.get("skipped") or [],
        )
        art["holdings_assignment"] = assign
        report["holdings_assignment"] = assign
    except Exception as exc:
        logger.exception('unexpected error in attach_cluster_pool_artifact')
        art["holdings_assignment"] = {"success": False, "error": str(exc)}
        report["holdings_assignment"] = art["holdings_assignment"]

    report["pool_artifact"] = art
    note = str(report.get("note") or "")
    if "映射产物" not in note:
        report["note"] = note + " 已附分组映射 / 持仓入组。"
    return report
