"""分组 live 启用证据包与公开状态（从 cluster_live 拆出）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _summarize_cluster_oos(clusters: Sequence[Any]) -> Dict[str, Any]:
    """统计 active/draft clusters 的 OOS：优先 oos_gate，回退 oos_passed。"""
    from core.signal.weight_oos_gate import OOS_PRODUCT_SEMANTICS

    oos_pass = 0
    oos_fail = 0
    oos_skip = 0
    oos_unknown = 0
    for cl in clusters or []:
        if not isinstance(cl, dict):
            continue
        gate = cl.get("oos_gate")
        if isinstance(gate, dict) and gate:
            if gate.get("skipped"):
                oos_skip += 1
            elif gate.get("ok") and gate.get("passed"):
                oos_pass += 1
            elif gate.get("ok") is False or gate.get("passed") is False:
                oos_fail += 1
            else:
                oos_unknown += 1
            continue
        # 旧产物只有布尔 oos_passed
        if "oos_passed" in cl and cl.get("oos_passed") is not None:
            if bool(cl.get("oos_passed")):
                oos_pass += 1
            else:
                oos_fail += 1
            continue
        oos_unknown += 1
    return {
        "pass_count": oos_pass,
        "fail_count": oos_fail,
        "skip_count": oos_skip,
        "unknown_count": oos_unknown,
        "n_clusters": oos_pass + oos_fail + oos_skip + oos_unknown,
        "note": (
            "来自 active 映射 clusters.oos_gate（缺省回退 oos_passed）；无记录不拦启用。 "
            + OOS_PRODUCT_SEMANTICS
        ),
    }


def build_cluster_enable_evidence(
    *,
    include_audit: bool = False,
    health: Optional[Dict[str, Any]] = None,
    audit: Optional[Dict[str, Any]] = None,
    include_rolling_ic: bool = False,
) -> Dict[str, Any]:
    """
    EP1：对照→启用证据包（JSON）。

    门禁：健康 allow_active；若有 OOS 记录且全部失败则禁止启用。
    默认不采全局/组双分样本（系统主路径无全局 return_model）。
    ``include_rolling_ic`` 默认关：状态/UI 路径避免拉行情卡死；启用门禁显式打开。
    """
    from core.signal.cluster_live import (
        assess_cluster_live_health,
        cluster_score_audit_sample,
        ensure_active_cluster_oos_gates,
        get_cluster_scoring_cfg,
        load_active_cluster_book,
        load_active_cluster_weights,
    )

    cs = get_cluster_scoring_cfg()
    active = load_active_cluster_weights()
    book_doc = load_active_cluster_book() or {}
    book_rows = list(book_doc.get("book") or [])
    meta = book_doc.get("meta") if isinstance(book_doc.get("meta"), dict) else {}
    h = health if isinstance(health, dict) else assess_cluster_live_health()

    top_n = meta.get("top_n_per_group") or cs.get("top_n_per_group")
    max_names = meta.get("max_names") or cs.get("max_names")
    from core.signal.score_display import json_safe_number

    raw_min = meta.get("min_score")
    min_score = json_safe_number(raw_min)
    if min_score is None and raw_min is None and not meta.get("min_score_disabled"):
        min_score = json_safe_number(cs.get("min_score"))
    selection_mode = meta.get("mode") or "cluster_score_global_rank"
    name_count = len(book_rows)

    # 缺 oos_gate 时补算并落盘，避免证据包长期「未知」
    try:
        ensure_active_cluster_oos_gates(persist=True, force=False)
        active = load_active_cluster_weights() or active
    except Exception:
        pass

    oos_summary = _summarize_cluster_oos((active or {}).get("clusters") or [])

    # 换手估计：纸面持仓 vs 分池簿（只数，无报价）
    book_codes = {
        str(r.get("stock_code") or "").strip()
        for r in book_rows
        if r.get("stock_code")
    }
    held_codes: set = set()
    try:
        from core.paper import load_paper

        paper = load_paper()
        for hh in (paper or {}).get("holdings") or []:
            c = str(hh.get("stock_code") or "").strip()
            if c and float(hh.get("shares") or 0) > 0:
                held_codes.add(c)
    except Exception:
        held_codes = set()
    would_sell = sorted(held_codes - book_codes)
    would_buy = sorted(book_codes - held_codes)
    turnover_est = {
        "held_count": len(held_codes),
        "book_count": len(book_codes),
        "would_sell_count": len(would_sell),
        "would_buy_count": len(would_buy),
        "would_sell": would_sell[:12],
        "would_buy": would_buy[:12],
        "note": "相对当前纸面 vs 合并簿只数估计，非金额换手",
    }

    # 行业集中度简表（簿内）
    exposure_summary: Dict[str, Any] = {"sectors": [], "top_sector": None}
    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        smap = load_sector_map()
        sec_cnt: Dict[str, int] = {}
        for c in book_codes:
            sec = _sector_for(c, smap)
            sec_cnt[sec] = int(sec_cnt.get(sec) or 0) + 1
        total = sum(sec_cnt.values()) or 1
        sectors = [
            {
                "name": k,
                "count": v,
                "weight_pct": round(v / total * 100.0, 1),
            }
            for k, v in sorted(sec_cnt.items(), key=lambda x: -x[1])
        ]
        exposure_summary = {
            "sectors": sectors[:8],
            "top_sector": sectors[0] if sectors else None,
            "note": "按簿内只数占比（非市值）",
        }
    except Exception:
        pass

    if include_audit and audit is None and (cs.get("mode") in ("shadow", "active")):
        try:
            audit = cluster_score_audit_sample(limit=6)
        except Exception as exc:
            audit = {"success": False, "rows": [], "error": str(exc)}
    # 主路径无全局ŷ；双分样本仅遗留调试（默认不采）
    audit_rows = list((audit or {}).get("rows") or [])[:6] if include_audit else []

    blockers: List[str] = []
    warnings: List[str] = []
    if not active:
        blockers.append("无 active 映射")
    if not h.get("allow_active"):
        blockers.extend(list(h.get("alerts") or []) or ["健康检查未通过"])
    if h.get("refit_suggested"):
        warnings.append("建议重拟合：研究枢纽跑分组→①对照（映射临期或样本偏少）")

    # Y1.4：可配 OOS 失败率门禁（默认 1.0 = 仅全败才拦，兼容旧行为）
    oos_n = int(oos_summary.get("pass_count") or 0) + int(
        oos_summary.get("fail_count") or 0
    )
    fail_n = int(oos_summary.get("fail_count") or 0)
    max_fail_rate = float(
        cs.get("max_oos_fail_rate") if cs.get("max_oos_fail_rate") is not None else 0.5
    )
    if oos_n > 0:
        fail_rate = fail_n / float(oos_n)
        oos_summary = dict(oos_summary)
        oos_summary["fail_rate"] = round(fail_rate, 3)
        oos_summary["max_oos_fail_rate"] = max_fail_rate
        if fail_rate > max_fail_rate + 1e-12:
            blockers.append(
                f"组 OOS 失败率 {fail_rate:.0%} > 容忍 {max_fail_rate:.0%}（失败 {fail_n}/{oos_n}）"
            )
        elif fail_n > 0 and int(oos_summary.get("pass_count") or 0) == 0:
            # 兼容：全败且 max=1.0 时仍拦
            if max_fail_rate >= 1.0 - 1e-12:
                blockers.append(f"组 OOS 全部失败（{fail_n}）")
    if name_count <= 0:
        warnings.append("分池簿为空 · 启用后请刷新簿再分池调仓")

    # Y1.1 / Y1.3：滚动 ŷ IC（仅启用门禁显式打开；状态 GET 默认跳过以免行情挂死）
    rolling_ic_pack: Dict[str, Any] = {"ok": False, "rolling_ic": None, "skipped": True}
    mode_now = str(cs.get("mode") or "off")
    if include_rolling_ic and mode_now == "active":
        rolling_ic_pack = {"ok": False, "rolling_ic": None}
        try:
            from core.strategy_monitor import estimate_rolling_yhat_ic_for_codes

            ic_codes = list(book_codes)[:6] or list(held_codes)[:6]
            if ic_codes:
                rolling_ic_pack = estimate_rolling_yhat_ic_for_codes(ic_codes, limit=4)
        except Exception as exc:
            rolling_ic_pack = {"ok": False, "rolling_ic": None, "error": str(exc)}
        yhat_ic = rolling_ic_pack.get("rolling_ic")
        min_yhat_ic = float(cs.get("min_yhat_rolling_ic") or 0.0)
        if yhat_ic is not None:
            try:
                yic = float(yhat_ic)
                if yic < min_yhat_ic:
                    msg = f"滚动 ŷ IC={yic:.3f} < 阈值 {min_yhat_ic}"
                    if cs.get("block_active_on_yhat_ic"):
                        blockers.append(msg)
                    else:
                        warnings.append(msg + " · 建议重拟合后再启用")
            except (TypeError, ValueError):
                pass
    elif mode_now == "active":
        rolling_ic_pack["note"] = "状态路径跳过 ŷ IC（启用门禁再算）"
    else:
        rolling_ic_pack["note"] = "shadow/off 跳过 ŷ IC（启用 active 时再算）"

    # Y3.3：行业 map 覆盖率
    try:
        from core.strategy_monitor import sector_coverage_report

        sec_cov = sector_coverage_report(list(book_codes) or list(held_codes))
        exposure_summary["sector_map_coverage"] = sec_cov.get("coverage")
        exposure_summary["sector_map"] = {
            "mapped": sec_cov.get("mapped"),
            "total": sec_cov.get("total"),
            "coverage": sec_cov.get("coverage"),
        }
        min_sec = float(cs.get("min_sector_map_coverage") or 0.5)
        cov_v = sec_cov.get("coverage")
        if cov_v is not None and float(cov_v) < min_sec and int(sec_cov.get("total") or 0) > 0:
            warnings.append(
                f"行业 map 覆盖 {float(cov_v):.0%} < {min_sec:.0%} · 限额精度偏弱（不硬拦）"
            )
    except Exception:
        pass

    gate_ok = len(blockers) == 0
    out: Dict[str, Any] = {
        "success": True,
        "task": "cluster_enable_evidence",
        "ok": gate_ok,
        "gate": {"ok": gate_ok, "blockers": blockers, "warnings": warnings},
        "top_n_per_group": top_n,  # 兼容旧字段；建簿已改为全局排序
        "selection_mode": selection_mode,
        "min_score": min_score,
        "max_names": max_names,
        "name_count": name_count,
        "book_codes": sorted(book_codes),
        "cluster_version": (active or {}).get("version"),
        "mode": cs.get("mode") or "off",
        "oos_summary": oos_summary,
        "turnover_est": turnover_est,
        "exposure_summary": exposure_summary,
        "rolling_yhat_ic": rolling_ic_pack,
        "health": {
            "coverage": h.get("coverage"),
            "age_days": h.get("age_days"),
            "stale": h.get("stale"),
            "allow_active": h.get("allow_active"),
            "refit_suggested": h.get("refit_suggested"),
            "fitted_as_of": h.get("fitted_as_of"),
            "sample_count_min": h.get("sample_count_min"),
        },
        "note": "启用前证据包 · 健康门禁 + OOS/簿长/换手估计/行业简表/ŷ IC（组ŷ；无全局模型对照）",
    }
    if include_audit:
        out["score_audit_sample"] = {
            "rows": audit_rows,
            "count": len(audit_rows),
            "error": (audit or {}).get("error"),
        }
    return out


def cluster_status_public(
    *,
    include_audit: bool = False,
    audit_rotate: bool = False,
    audit_offset: Optional[int] = None,
    light: bool = False,
    run_auto_demote: bool = False,
) -> Dict[str, Any]:
    """供 API/UI 的状态摘要（含落地下一步）。

    ``include_audit`` 默认关闭：系统主路径只有组 return_model，无全局ŷ对照。
    ``light``：交易执行状态条等只读 mode/簿长，跳过证据包与自动降级（避免行情挂死）。
    ``run_auto_demote``：默认关；GET 状态不做副作用。日更请走 ``prepare_cluster_for_daily``。
    """
    from core.paths import (
        CLUSTER_BOOK_ACTIVE_PATH,
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        CLUSTER_WEIGHTS_DRAFT_PATH,
    )
    from core.signal.cluster_live import (
        assess_cluster_live_health,
        cluster_score_audit_sample,
        get_cluster_scoring_cfg,
        load_active_cluster_book,
        load_active_cluster_weights,
        load_cluster_draft,
        maybe_auto_demote_stale,
    )
    from core.signal.config import load_signal_config
    from core.signal.score_display import json_safe_number

    # 跨进程改写 signal_config 后，服务进程缓存可能仍是旧 mode
    try:
        load_signal_config(reload=True)
    except Exception:
        pass

    cs = get_cluster_scoring_cfg()
    auto_demote = None
    # 默认不在 GET status 上自动降级（副作用 + 可能拉行情卡死 UI）
    if (
        run_auto_demote
        and not light
        and cs.get("mode") == "active"
        and cs.get("auto_demote_on_stale")
    ):
        try:
            auto_demote = maybe_auto_demote_stale()
            if auto_demote.get("demoted"):
                cs = get_cluster_scoring_cfg()
        except Exception:
            auto_demote = None

    active = load_active_cluster_weights()
    # 状态/落地 UI 不拉滚动 IC；IC 门禁在 set_mode(active) / 日更路径算
    health = assess_cluster_live_health(compute_ic=False)
    draft = load_cluster_draft()
    book = load_active_cluster_book()
    import core.signal.cluster_live as cluster_live_mod

    paper_land = cluster_live_mod._paper_cluster_landed(active)
    mode = cs.get("mode") or "off"
    has_draft = bool(draft and draft.get("code_map"))
    has_active = bool(active)

    book_meta = (book or {}).get("meta") if isinstance((book or {}).get("meta"), dict) else {}
    book_min_score = json_safe_number(book_meta.get("min_score"))
    book_rows = list((book or {}).get("book") or [])
    book_codes = [
        str(r.get("stock_code") or "").strip()
        for r in book_rows
        if isinstance(r, dict) and str(r.get("stock_code") or "").strip()
    ]

    audit = None
    if include_audit and not light and mode in ("shadow", "active") and has_active:
        try:
            audit = cluster_score_audit_sample(
                limit=8,
                offset=audit_offset,
                rotate=bool(audit_rotate),
            )
        except Exception as exc:
            audit = {"success": False, "rows": [], "error": str(exc)}

    if light:
        evidence = {
            "success": True,
            "ok": bool(health.get("allow_active")),
            "gate": {
                "ok": bool(health.get("allow_active")),
                "blockers": [],
                "warnings": [],
            },
            "skipped": True,
            "note": "light 状态跳过证据包",
        }
    else:
        evidence = build_cluster_enable_evidence(
            include_audit=False,
            health=health,
            audit=None,
            include_rolling_ic=False,
        )
    allow_active = bool(health.get("allow_active")) and bool(
        (evidence.get("gate") or {}).get("ok")
    )
    if not has_draft and not has_active:
        next_step = "run_cluster"
        next_label = "先跑分组"
    elif mode == "off" or not has_active:
        next_step = "apply_shadow"
        next_label = "① 对照"
    elif mode == "shadow":
        next_step = "enable_active" if allow_active else "fix_health"
        next_label = "② 启用" if allow_active else "修复健康/证据包后再启用"
    elif health.get("suggest_demote") or not allow_active:
        # 仍挂在 active 但门禁已破（未开自动降级时）
        next_step = "demote_shadow"
        next_label = "降为对照（健康未过）"
    else:
        # mode=active：研究侧权责结束；调仓走侧栏交易执行页
        next_step = "go_follow"
        next_label = "已启用 · 侧栏进交易执行"

    if auto_demote and auto_demote.get("demoted"):
        next_step = "fix_health"
        next_label = "已自动降为对照 · 请跑分组重估后再启用"
        note = str(auto_demote.get("note") or "已自动降为 shadow")
        alerts = list(health.get("alerts") or [])
        if note and note not in alerts:
            health = dict(health)
            health["alerts"] = [note] + alerts
            health["auto_demoted"] = True

    return {
        "success": True,
        "cluster_scoring": cs,
        "active": {
            "exists": has_active,
            "path": CLUSTER_WEIGHTS_ACTIVE_PATH,
            "version": (active or {}).get("version"),
            "promoted_at": (active or {}).get("promoted_at"),
            "n_mapped_codes": (active or {}).get("n_mapped_codes"),
            "n_clusters": (active or {}).get("n_clusters"),
        },
        "draft": {
            "exists": has_draft,
            "path": CLUSTER_WEIGHTS_DRAFT_PATH,
            "saved_at": (draft or {}).get("saved_at"),
            "n_mapped_codes": (draft or {}).get("n_mapped_codes"),
            "n_clusters": (draft or {}).get("n_clusters"),
            "clusters": [
                {
                    "label": c.get("label"),
                    "member_count": c.get("member_count"),
                    "members": list(c.get("members") or [])[:12],
                }
                for c in ((draft or {}).get("clusters") or [])
                if isinstance(c, dict)
            ],
            "holdings_assignment": (draft or {}).get("holdings_assignment"),
        },
        "book": {
            "exists": bool(book),
            "path": CLUSTER_BOOK_ACTIVE_PATH,
            "updated_at": (book or {}).get("updated_at"),
            "name_count": len(book_rows),
            "codes": book_codes,
            "selection_mode": book_meta.get("mode") or "cluster_score_global_rank",
            "min_score": book_min_score,
            "top_n_per_group": book_meta.get("top_n_per_group") or cs.get("top_n_per_group"),
            "max_names": book_meta.get("max_names") or cs.get("max_names"),
        },
        "health": health,
        "landing": {
            "next_step": next_step,
            "next_label": next_label,
            "can_apply": has_draft or has_active,
            "can_activate": allow_active and has_active,
            # 研究枢纽不调仓；就绪后跳转 /follow（健康破线时不算就绪）
            "ready_for_follow": mode == "active" and has_active and allow_active,
            "can_paper": False,
            "paper_applied": bool(paper_land.get("applied")),
            "paper_applied_at": paper_land.get("applied_at"),
            "paper_cluster_version": paper_land.get("cluster_version"),
        },
        "enable_evidence": evidence,
        "audit_sample": audit,
        "auto_demote": (
            {
                "demoted": bool((auto_demote or {}).get("demoted")),
                "note": (auto_demote or {}).get("note"),
            }
            if auto_demote
            else None
        ),
        "light": bool(light),
        "note": "组权在 live 产物；调仓仅交易执行页；signal_config 仅开关 mode",
    }


def _paper_cluster_landed(active: Optional[dict]) -> Dict[str, Any]:
    """纸面是否已按当前 active 映射完成过分池调仓。"""
    if not active:
        return {"applied": False}
    try:
        from core.paper import load_paper

        paper = load_paper()
    except Exception:
        return {"applied": False}
    last = paper.get("last_cluster_pool") if isinstance(paper, dict) else None
    if not isinstance(last, dict):
        return {"applied": False}
    av = active.get("version")
    pv = last.get("cluster_version")
    if av is None or pv is None:
        return {
            "applied": False,
            "applied_at": last.get("applied_at"),
            "cluster_version": pv,
        }
    try:
        matched = int(av) == int(pv)
    except (TypeError, ValueError):
        matched = str(av) == str(pv)
    return {
        "applied": matched,
        "applied_at": last.get("applied_at"),
        "cluster_version": pv,
    }
