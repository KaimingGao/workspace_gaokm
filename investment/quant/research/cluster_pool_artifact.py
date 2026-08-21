"""分池研究产物：code→cluster→因子系数(return_model) 映射 + 纸面调仓预演。

真源为组 OLS 收益分系数；weights 仅由 |β| 派生（兼容旧消费者）。
不写 signal_config。
"""

import logging

logger = logging.getLogger(__name__)
import copy
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.numbers import now_iso_utc
from core.research.oos_slim import slim_oos_gate
from core.signal.factor_coefs import (
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
            "weights": weights,
            "weights_derived_from_beta": bool(
                rm and has_factor_coefficients(rm) and weights
            ),
            "oos_gate": gate_slim,
            "oos_passed": oos_passed,
            "singleton": bool(cl.get("singleton")),
        }
        clusters_out.append(entry)
        for code in members:
            code_map[code] = {
                "cluster_id": cl.get("cluster_id"),
                "cluster_label": label,
                "return_model": rm,
                "weights": weights,
                "oos_passed": entry["oos_passed"],
            }

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


def _book_to_ranking(book: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "stock_code": str(p.get("stock_code") or ""),
            "stock_name": p.get("stock_name"),
            "score": float(p.get("score") or 0.0),
            "hard_reject": False,
            "cluster_label": p.get("cluster_label"),
        }
        for p in (book or [])
        if str(p.get("stock_code") or "").strip()
    ]


def _trade_lists(result: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    sells = list(result.get("sell_trades") or result.get("sells") or [])
    buys = list(result.get("buy_trades") or result.get("buys") or [])
    if not sells and not buys and isinstance(result.get("trades"), list):
        sells = [t for t in result["trades"] if t.get("side") == "sell"]
        buys = [t for t in result["trades"] if t.get("side") == "buy"]
    return sells, buys


def preview_paper_pool_rebalance(
    book: Sequence[Dict[str, Any]],
    *,
    paper_path: Optional[str] = None,
    top_k: Optional[int] = None,
    dry_run: bool = True,
    confirm: bool = False,
    artifact: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """用分池候选簿作排名，对纸面账户调仓。

    - 默认预演：deepcopy，**不写盘**
    - ``confirm=True``：写 ``paper.json``，记 ``last_cluster_pool``；**永不**写 signal_config
    - ``dry_run=False`` 且未 confirm：拒绝（防误触）
    """
    from core.paper import (
        append_operation_log,
        append_snapshot,
        append_trade_legs_to_operation_log,
        capture_mark_snapshot,
        load_paper,
        mark_to_market,
        save_paper,
    )
    from core.paths import PAPER_PATH

    write = bool(confirm)
    if not dry_run and not write:
        return {
            "success": False,
            "ok": False,
            "dry_run": False,
            "confirmed": False,
            "error": "未 confirm 时禁止写账；请先预演，或传 confirm=true",
            "task": "cluster_paper_preview",
        }

    path = paper_path or PAPER_PATH
    if not path or not os.path.isfile(path):
        return {
            "success": False,
            "ok": False,
            "dry_run": not write,
            "confirmed": False,
            "error": "纸面账户未初始化",
            "task": "cluster_paper_preview",
            "intent": intent_preview_vs_holdings(book, []),
        }

    paper = load_paper(path)
    intent = intent_preview_vs_holdings(book, paper.get("holdings") or [])
    ranking = _book_to_ranking(book)
    if not ranking:
        return {
            "success": False,
            "ok": False,
            "dry_run": not write,
            "confirmed": False,
            "error": "候选簿为空",
            "task": "cluster_paper_preview",
            "intent": intent,
        }

    from core.paper_rebalance import simulate_cross_section_rebalance

    target = paper if write else copy.deepcopy(paper)
    k = top_k if top_k is not None else len(ranking)
    k = max(1, min(int(k), 80))

    if write:
        capture_mark_snapshot(target)

    try:
        result = simulate_cross_section_rebalance(
            target,
            ranking,
            top_k=k,
            # 分池簿本身已是每组 Top-N 合成；勿再被纸面 max_positions(常=5) 砍掉
            respect_max_positions=False,
        )
    except Exception as exc:
        logger.exception('unexpected error in preview_paper_pool_rebalance')
        return {
            "success": False,
            "ok": False,
            "dry_run": not write,
            "confirmed": False,
            "error": str(exc),
            "task": "cluster_paper_preview",
            "intent": intent,
        }

    sells, buys = _trade_lists(result)
    summary = None
    if write:
        summary = mark_to_market(target)
        append_snapshot(target, summary)
        art = artifact if isinstance(artifact, dict) else {}
        target["last_cluster_pool"] = {
            "applied_at": now_iso_utc(),
            "schema_version": art.get("schema_version") or SCHEMA_VERSION,
            "artifact_created_at": art.get("created_at"),
            "n_clusters": art.get("n_clusters"),
            "book_codes": [r["stock_code"] for r in ranking],
            "sell_count": len(sells),
            "buy_count": len(buys),
            "signal_config_touched": False,
            "note": "分池候选簿纸面落账；组权未写入 signal_config",
        }
        append_trade_legs_to_operation_log(
            target,
            sells,
            buys,
            origin="cluster",
            source="research_hub",
        )
        append_operation_log(
            target,
            "cluster_pool_rebalance",
            detail=(
                f"分池调仓：卖 {len(sells)} · 买 {len(buys)} · "
                f"目标 {len(ranking)} 只（不写 signal_config）"
            ),
            meta={
                "book_codes": [r["stock_code"] for r in ranking],
                "sell_count": len(sells),
                "buy_count": len(buys),
                "artifact_created_at": art.get("created_at"),
                "signal_config_touched": False,
                "origin": "cluster",
                "source": "research_hub",
            },
        )
        save_paper(target, path)

    return {
        "success": True,
        "ok": True,
        "dry_run": not write,
        "confirmed": write,
        "task": "cluster_paper_apply" if write else "cluster_paper_preview",
        "top_k": k,
        "intent": intent,
        "sell_count": len(sells),
        "buy_count": len(buys),
        "sell_trades": sells[:20],
        "buy_trades": buys[:20],
        "buys_blocked": bool(result.get("buys_blocked")),
        "risk_gate": result.get("risk_gate"),
        "risk_budget_skips": result.get("risk_budget_skips") or [],
        "sentiment_prior": result.get("sentiment_prior"),
        "cash_impact": result.get("cash_impact"),
        "ops_report": result.get("ops_report"),
        "rebalance_report": result.get("rebalance_report")
        or [
            {
                "stock_code": t.get("stock_code"),
                "stock_name": t.get("stock_name"),
                "decision": (
                    "买入"
                    if t.get("side") == "buy"
                    else (
                        "减仓"
                        if t.get("sentiment_prior") and "缩仓" in str(t.get("note") or "")
                        else "卖出"
                    )
                ),
                "reason": t.get("note") or "",
                "shares": t.get("shares"),
                "score": t.get("score"),
                "sentiment_prior": bool(t.get("sentiment_prior")),
            }
            for t in (sells + buys)[:40]
        ],
        "cash_after": target.get("cash"),
        "holdings_after": [
            {
                "stock_code": h.get("stock_code"),
                "stock_name": h.get("stock_name"),
                "shares": h.get("shares"),
            }
            for h in (target.get("holdings") or [])[:30]
        ],
        "summary": summary,
        "last_cluster_pool": target.get("last_cluster_pool") if write else None,
        "note": (

                "分池候选簿已写入纸面账户；组权未写入 signal_config。"
                if write
                else "分池候选簿 → 纸面调仓预演（deepcopy，未写 paper.json）。"

        ),
        "apply_note": (
            "已落账 · 仅 paper.json"
            if write
            else "预演不写账；确认落账请传 confirm=true（仍不写 signal_config）。"
        ),
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
