"""OOS 失败组 label 提取（无循环依赖，供 rank / live / 证据包共用）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple


def oos_failed_cluster_labels(
    clusters: Optional[Sequence[Any]] = None,
    *,
    active: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """返回 OOS 门禁未过的组 label 列表（跳过 skipped）。"""
    if clusters is None:
        if active is None:
            from core.signal.cluster.live import load_active_cluster_weights

            art = load_active_cluster_weights()
        else:
            art = active
        clusters = list((art or {}).get("clusters") or [])
    failed: List[str] = []
    seen = set()
    for cl in clusters or []:
        if not isinstance(cl, dict):
            continue
        lab = str(cl.get("label") or cl.get("cluster_label") or "").strip()
        if not lab:
            continue
        gate = cl.get("oos_gate")
        failed_flag = False
        if isinstance(gate, dict) and gate:
            if gate.get("skipped"):
                continue
            if gate.get("passed") is False or gate.get("ok") is False:
                failed_flag = True
        elif "oos_passed" in cl and cl.get("oos_passed") is not None:
            failed_flag = not bool(cl.get("oos_passed"))
        if failed_flag and lab not in seen:
            seen.add(lab)
            failed.append(lab)
    return failed


def is_oos_failed_cluster_label(
    label: Optional[str],
    *,
    clusters: Optional[Sequence[Any]] = None,
    active: Optional[Dict[str, Any]] = None,
) -> bool:
    """单组 label 是否为 OOS 门禁失败（跳过 skipped）。"""
    lab = str(label or "").strip()
    if not lab:
        return False
    return lab in set(oos_failed_cluster_labels(clusters, active=active))


def codes_in_oos_failed_clusters(
    *,
    active: Optional[Dict[str, Any]] = None,
) -> set:
    """OOS 失败组内全部代码（来自 active code_map）；供 Top-K / 横截面剔榜。"""
    if active is None:
        from core.signal.cluster.live import load_active_cluster_weights

        art = load_active_cluster_weights()
    else:
        art = active
    failed = set(oos_failed_cluster_labels(active=art))
    if not failed:
        return set()
    cmap = (art or {}).get("code_map") or {}
    out: set = set()
    for code, meta in cmap.items():
        if not isinstance(meta, dict):
            continue
        lab = str(meta.get("cluster_label") or meta.get("label") or "").strip()
        if lab and lab in failed:
            key = str(code or "").strip()
            if key:
                out.add(key)
    return out


def oos_gate_stats(
    artifact: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """统计产物 OOS 门禁：evaluated / failed / fail_rate（跳过 skipped）。"""
    clusters = (
        list((artifact or {}).get("clusters") or [])
        if isinstance(artifact, dict)
        else []
    )
    evaluated = 0
    failed = 0
    skipped = 0
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        gate = cl.get("oos_gate")
        if isinstance(gate, dict) and gate:
            if gate.get("skipped"):
                skipped += 1
                continue
            evaluated += 1
            if gate.get("passed") is False or gate.get("ok") is False:
                failed += 1
        elif "oos_passed" in cl and cl.get("oos_passed") is not None:
            evaluated += 1
            if not bool(cl.get("oos_passed")):
                failed += 1
    rate = (float(failed) / float(evaluated)) if evaluated > 0 else None
    return {
        "cluster_count": len(clusters),
        "evaluated": evaluated,
        "failed": failed,
        "skipped": skipped,
        "fail_rate": None if rate is None else round(rate, 4),
        "failed_labels": oos_failed_cluster_labels(clusters),
    }


def compare_oos_vs_active(
    draft: Optional[Dict[str, Any]],
    active: Optional[Dict[str, Any]],
    *,
    max_oos_fail_rate: Optional[float] = None,
    allow_worse_than_active: bool = True,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """B1：draft OOS 闸。

    - **硬拦**：``fail_rate > max_oos_fail_rate``（绝对上限）
    - **相对 active**：默认仅软提示（避免棘轮）；
      ``allow_worse_than_active=False`` 时才硬拦「差于 active」
    """
    d_st = oos_gate_stats(draft)
    a_st = (
        oos_gate_stats(active)
        if active
        else {
            "evaluated": 0,
            "failed": 0,
            "fail_rate": None,
            "failed_labels": [],
        }
    )
    warnings: List[str] = []
    detail: Dict[str, Any] = {
        "draft": d_st,
        "active": a_st,
        "max_oos_fail_rate": max_oos_fail_rate,
        "allow_worse_than_active": bool(allow_worse_than_active),
        "warnings": warnings,
    }
    d_rate = d_st.get("fail_rate")
    if d_rate is None:
        return None, detail

    if max_oos_fail_rate is not None and d_rate > float(max_oos_fail_rate) + 1e-12:
        return (
            f"OOS 失败率过高：draft={d_rate:.0%} > max_oos_fail_rate={float(max_oos_fail_rate):.0%}"
            f"（失败组 {d_st.get('failed')}/{d_st.get('evaluated')}）",
            detail,
        )

    a_rate = a_st.get("fail_rate")
    if (
        a_rate is not None
        and int(d_st.get("evaluated") or 0) > 0
        and int(a_st.get("evaluated") or 0) > 0
        and d_rate > float(a_rate) + 1e-12
    ):
        msg = (
            f"OOS 失败率差于 active：draft={d_rate:.0%} > active={float(a_rate):.0%}"
        )
        if allow_worse_than_active:
            warnings.append(msg + "（软提示·不拦 promote；绝对上限仍生效）")
        else:
            return (
                msg + "（请改善分区、force promote，或打开 promote_allow_worse_oos_than_active）",
                detail,
            )
    return None, detail


def _cluster_r2(cl: dict) -> Optional[float]:
    for src in (
        cl.get("ols") if isinstance(cl.get("ols"), dict) else None,
        cl.get("return_model") if isinstance(cl.get("return_model"), dict) else None,
        cl,
    ):
        if not isinstance(src, dict):
            continue
        for k in ("r2", "pooled_r2", "train_r2"):
            v = src.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _cluster_ic(cl: dict) -> Optional[float]:
    for src in (
        cl.get("yhat_holdout") if isinstance(cl.get("yhat_holdout"), dict) else None,
        cl.get("holdout") if isinstance(cl.get("holdout"), dict) else None,
        cl.get("factor_ic_panel") if isinstance(cl.get("factor_ic_panel"), dict) else None,
        cl,
    ):
        if not isinstance(src, dict):
            continue
        for k in ("ic_mean", "ic", "holdout_ic"):
            v = src.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        pear = src.get("pearson")
        if isinstance(pear, dict) and pear.get("ic_mean") is not None:
            try:
                return float(pear["ic_mean"])
            except (TypeError, ValueError):
                pass
    return None


def _member_codes(cl: dict) -> List[str]:
    mem = cl.get("members") or cl.get("codes") or cl.get("stock_codes") or []
    out: List[str] = []
    for x in mem or []:
        if isinstance(x, dict):
            c = str(x.get("stock_code") or x.get("code") or "").strip()
        else:
            c = str(x or "").strip()
        if c:
            out.append(c)
    return out


def summarize_partition_quality(
    artifact: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """分区质量摘要：OOS + 组均 R²/IC（有则报）。"""
    oos = oos_gate_stats(artifact)
    clusters = (
        list((artifact or {}).get("clusters") or [])
        if isinstance(artifact, dict)
        else []
    )
    r2s: List[float] = []
    ics: List[float] = []
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        r2 = _cluster_r2(cl)
        if r2 is not None:
            r2s.append(r2)
        ic = _cluster_ic(cl)
        if ic is not None:
            ics.append(ic)
    mean_r2 = round(sum(r2s) / len(r2s), 4) if r2s else None
    mean_ic = round(sum(ics) / len(ics), 4) if ics else None
    art = artifact if isinstance(artifact, dict) else {}
    ksel = art.get("k_selection") if isinstance(art.get("k_selection"), dict) else {}
    greedy = ksel.get("greedy_refine") or art.get("greedy_refine")
    return {
        "oos": oos,
        "n_clusters": len(clusters),
        "mean_r2": mean_r2,
        "mean_ic": mean_ic,
        "n_r2": len(r2s),
        "n_ic": len(ics),
        "partition_loss": ksel.get("partition_loss") or art.get("partition_loss"),
        "greedy_refine": (
            {
                "ok": (greedy or {}).get("ok"),
                "mode": (greedy or {}).get("mode"),
                "n_swaps": (greedy or {}).get("n_swaps"),
                "improved": (greedy or {}).get("improved"),
                "reason": (greedy or {}).get("reason"),
            }
            if isinstance(greedy, dict)
            else None
        ),
        "horizon_days": art.get("horizon_days"),
        "version": art.get("version"),
    }


def focus_code_assignments(
    artifact: Optional[Dict[str, Any]],
    focus_codes: Sequence[str],
) -> Dict[str, Any]:
    """焦点票在分区中的组归属与 OOS。"""
    clusters = (
        list((artifact or {}).get("clusters") or [])
        if isinstance(artifact, dict)
        else []
    )
    out: Dict[str, Any] = {}
    for raw in focus_codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        hit = None
        for cl in clusters:
            if not isinstance(cl, dict):
                continue
            mem = _member_codes(cl)
            in_mem = code in mem or code.zfill(6) in mem
            if not in_mem:
                in_mem = any(
                    str(m).endswith(code) or str(m).endswith(code.zfill(6))
                    for m in mem
                )
            if not in_mem:
                continue
            gate = cl.get("oos_gate") if isinstance(cl.get("oos_gate"), dict) else {}
            hit = {
                "label": cl.get("label") or cl.get("cluster_label"),
                "n_members": len(mem),
                "oos_passed": gate.get("passed"),
                "oos_reason": gate.get("reason"),
                "r2": _cluster_r2(cl),
                "ic": _cluster_ic(cl),
            }
            break
        out[code] = hit
    return out


def compare_partition_vs_active(
    draft: Optional[Dict[str, Any]],
    active: Optional[Dict[str, Any]] = None,
    *,
    focus_codes: Optional[Sequence[str]] = None,
    max_oos_fail_rate: Optional[float] = None,
    allow_worse_than_active: bool = True,
) -> Dict[str, Any]:
    """B2/B3：draft vs active 晋升预检对照表（不写盘）。"""
    if active is None:
        try:
            from core.signal.cluster.live import load_active_cluster_weights

            active = load_active_cluster_weights()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_oos_labels.py", exc_info=True)
            active = None

    oos_err, oos_detail = compare_oos_vs_active(
        draft,
        active,
        max_oos_fail_rate=max_oos_fail_rate,
        allow_worse_than_active=allow_worse_than_active,
    )
    d_q = summarize_partition_quality(draft)
    a_q = summarize_partition_quality(active)
    focus = list(focus_codes or [])
    if not focus:
        focus = ["000938", "603019"]

    delta: Dict[str, Any] = {
        "oos_fail_rate": None,
        "mean_r2": None,
        "mean_ic": None,
    }
    try:
        if (
            d_q["oos"].get("fail_rate") is not None
            and a_q["oos"].get("fail_rate") is not None
        ):
            delta["oos_fail_rate"] = round(
                float(d_q["oos"]["fail_rate"]) - float(a_q["oos"]["fail_rate"]), 4
            )
    except (TypeError, ValueError):
        pass
    if d_q.get("mean_r2") is not None and a_q.get("mean_r2") is not None:
        delta["mean_r2"] = round(float(d_q["mean_r2"]) - float(a_q["mean_r2"]), 4)
    if d_q.get("mean_ic") is not None and a_q.get("mean_ic") is not None:
        delta["mean_ic"] = round(float(d_q["mean_ic"]) - float(a_q["mean_ic"]), 4)

    blockers: List[str] = []
    if oos_err:
        blockers.append(str(oos_err))
    warnings: List[str] = []
    for w in list((oos_detail or {}).get("warnings") or []):
        if w:
            warnings.append(str(w))
    if delta.get("mean_r2") is not None and float(delta["mean_r2"]) < -0.02:
        warnings.append(f"mean_r2 低于 active（Δ={delta['mean_r2']}）")
    if delta.get("mean_ic") is not None and float(delta["mean_ic"]) < -0.02:
        warnings.append(f"mean_ic 低于 active（Δ={delta['mean_ic']}）")

    # 硬清单扩展：α 衰减告警（研究台，不替代 OOS 闸）
    checklist: List[Dict[str, Any]] = []
    checklist.append(
        {
            "id": "oos_gate",
            "ok": oos_err is None,
            "label": "分组 OOS 过门",
            "detail": str(oos_err) if oos_err else "通过",
        }
    )
    try:
        from core.risk.portfolio_health import build_portfolio_health

        health = build_portfolio_health(
            book=[],
            book_constraints=None,
            rolling_ic={
                "mean_ic": (d_q or {}).get("mean_ic"),
            },
        )
        decay = bool((health.get("alpha") or {}).get("decay_alert"))
        checklist.append(
            {
                "id": "alpha_decay",
                "ok": not decay,
                "label": "α 衰减告警未触发",
                "detail": (health.get("alerts") or [{}])[0].get("message")
                if decay
                else "无衰减告警",
            }
        )
        if decay:
            warnings.append("组合健康度：α 衰减告警（建议暂缓 promote）")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_oos_labels.py", exc_info=True)
        checklist.append(
            {
                "id": "portfolio_health",
                "ok": True,
                "label": "组合健康度",
                "detail": "跳过（健康度不可用）",
            }
        )

    return {
        "success": True,
        "promote_ready": oos_err is None,
        "blockers": blockers,
        "warnings": warnings,
        "checklist": checklist,
        "oos_compare": oos_detail,
        "draft": d_q,
        "active": a_q,
        "delta": delta,
        "focus_draft": focus_code_assignments(draft, focus),
        "focus_active": focus_code_assignments(active, focus),
        "note": (
            "B3 对照：promote_ready 看绝对 OOS 上限（max_oos_fail_rate）；"
            "checklist 含衰减/行业集中度软项；相对 active 默认软提示。"
        ),
    }
